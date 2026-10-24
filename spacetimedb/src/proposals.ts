import { t } from 'spacetimedb/server';
import type { Identity } from 'spacetimedb';
import spacetimedb from './schema';
import {
  type Ctx,
  type RoomRow,
  RESPONSES,
  STAGES,
  closeJobThreads,
  containsPrivateText,
  digest,
  emitEvent,
  fail,
  insideWindow,
  oneOf,
  parseJson,
  refResolves,
  requireHuman,
  requireLease,
  requireMember,
  requireMembershipById,
  requireRoom,
  requireWorker,
  runProcedure,
  setMilestone,
  withReceipt,
} from './guards';
import { messageOwnAgent, recomputeAgreement, scheduleReviewReminders } from './effects';
import { findIntake } from './onboarding';

const ImpactInput = t.object('ImpactInput', { memberId: t.u64(), impactJson: t.string() });

type CandidateFacts = {
  category?: unknown;
  owner_member_id?: unknown;
  total_cents?: unknown;
  arrival_epoch?: unknown;
  observed_at?: unknown;
  valid_until?: unknown;
  baggage_included?: unknown;
  cost_basis?: unknown;
  missing_critical_fields?: unknown;
  capacity?: unknown;
  arrival_local?: unknown;
  stops?: unknown;
};

// A complete total is the only price people can approve.
const COMPLETE_BASIS: Record<string, string> = {
  FLIGHT: 'COMPLETE_FLIGHT_TOTAL',
  STAY: 'COMPLETE_STAY_TOTAL',
  TRANSFER: 'COMPLETE_TRANSFER_TOTAL',
};

function constraintValue(ctx: Ctx, roomId: bigint, owner: Identity, key: string, hardOnly: boolean): unknown {
  for (const c of ctx.db.participantConstraint.byRoomOwnerKey.filter([roomId, owner, key])) {
    if (hardOnly && c.hardness !== 'HARD') return undefined;
    return JSON.parse(c.valueJson);
  }
  return undefined;
}

// Independent server-side check of a trip selection (policy minimax_v1), so a
// buggy or compromised worker can't publish a plan that breaks someone's
// confirmed hard limits. A trip is one FLIGHT per active member plus, when the
// room's settings list them in `components`, one shared STAY and TRANSFER whose
// cost is split equally. Every price must be a complete total (cost_basis
// COMPLETE_<CATEGORY>_TOTAL, no missing_critical_fields) and current; flights
// carry owner_member_id, arrival_epoch and baggage_included; shared items carry
// capacity. Hard limits checked: flight_budget_total, trip_budget_total (own
// flight + equal share), baggage_required, seats/guests, arrival window. Room
// preferences that owners can relax by consent (bedrooms, rating) stay in the worker.
function validateTripSelection(ctx: Ctx, r: RoomRow, refs: string[]): void {
  const active = [...ctx.db.membership.byRoomMember.filter(r.id)].filter(m => m.active);
  const nowSeconds = Number(ctx.timestamp.microsSinceUnixEpoch / 1000n) / 1000;
  const settings = JSON.parse(r.settingsJson) as { arrival_overlap_seconds?: number; components?: string[] };
  const required = new Set((settings.components ?? ['FLIGHT']).filter(c => c !== 'FLIGHT'));
  const flights = new Map<bigint, number>();
  const shared = new Map<string, number>();
  const arrivals: number[] = [];
  for (const ref of refs) {
    let row;
    for (const c of ctx.db.candidate.byRoomRef.filter([r.id, ref])) row = c;
    if (!row) fail('INVALID_EVIDENCE_REF', `candidate ${ref} is not recorded in this room`);
    const facts = JSON.parse(row.factsJson) as CandidateFacts;
    const category = typeof facts.category === 'string' ? facts.category : 'FLIGHT';
    if (category !== row.category) fail('INVALID_PROPOSAL', `candidate ${ref} has inconsistent category`);
    if (typeof facts.total_cents !== 'number') fail('INVALID_PROPOSAL', `candidate ${ref} has no known total price`);
    const missing = Array.isArray(facts.missing_critical_fields) ? facts.missing_critical_fields : [];
    if (facts.cost_basis !== COMPLETE_BASIS[category] || missing.length > 0) {
      fail('INVALID_PROPOSAL', `candidate ${ref} has incomplete cost evidence`);
    }
    if (
      typeof facts.observed_at !== 'number' ||
      typeof facts.valid_until !== 'number' ||
      facts.observed_at > nowSeconds ||
      facts.valid_until <= nowSeconds
    ) {
      fail('INVALID_PROPOSAL', `candidate ${ref} evidence is not current`);
    }
    if (category !== 'FLIGHT') {
      if (!required.has(category) || shared.has(category)) fail('INVALID_COVERAGE', `unexpected ${category} ${ref}`);
      if (typeof facts.capacity !== 'number' || facts.capacity < active.length) {
        fail('INVALID_PROPOSAL', `candidate ${ref} doesn't fit everyone`);
      }
      shared.set(category, facts.total_cents);
      continue;
    }
    const member = active.find(m => Number(m.id) === facts.owner_member_id);
    if (!member || flights.has(member.id)) fail('INVALID_COVERAGE', `candidate ${ref} has no unique active owner`);
    flights.set(member.id, facts.total_cents);
    if (!ctx.db.participationPreferences.owner.find(member.member)?.confirmed) {
      fail('INVALID_PROPOSAL', 'every traveller must confirm participation preferences');
    }
    const cap = constraintValue(ctx, r.id, member.member, 'flight_budget_total', true);
    if (typeof cap === 'number' && facts.total_cents > cap) fail('INVALID_PROPOSAL', `candidate ${ref} exceeds a hard limit`);
    if (constraintValue(ctx, r.id, member.member, 'baggage_required', false) === true && facts.baggage_included !== true) {
      fail('INVALID_PROPOSAL', `candidate ${ref} does not confirm required baggage`);
    }
    if (typeof facts.arrival_epoch !== 'number') fail('INVALID_PROPOSAL', `candidate ${ref} has no arrival time`);
    const window = constraintValue(ctx, r.id, member.member, 'arrival_window', true);
    if (window !== undefined && !insideWindow(facts.arrival_local, window)) {
      fail('INVALID_PROPOSAL', `candidate ${ref} arrives outside a traveller's arrival window`);
    }
    const maxStops = constraintValue(ctx, r.id, member.member, 'max_stops', true);
    if (typeof maxStops === 'number' && (typeof facts.stops !== 'number' || facts.stops > maxStops)) {
      fail('INVALID_PROPOSAL', `candidate ${ref} has more stops than a traveller allows`);
    }
    arrivals.push(facts.arrival_epoch);
  }
  if (flights.size !== active.length) fail('INVALID_COVERAGE', 'every active member needs exactly one selected flight');
  if (shared.size !== required.size) fail('INVALID_COVERAGE', 'every shared part of the trip needs exactly one option');
  // Equal split, compared in party-size-scaled cents so shares stay exact.
  const sharedTotal = [...shared.values()].reduce((a, b) => a + b, 0);
  for (const m of active) {
    const cap = constraintValue(ctx, r.id, m.member, 'trip_budget_total', true);
    if (typeof cap === 'number' && active.length * flights.get(m.id)! + sharedTotal > active.length * cap) {
      fail('INVALID_PROPOSAL', 'a traveller’s share of the trip exceeds a hard limit');
    }
  }
  const overlap = settings.arrival_overlap_seconds ?? 7200;
  if (arrivals.length > 0 && Math.max(...arrivals) - Math.min(...arrivals) > overlap) {
    fail('INVALID_PROPOSAL', 'arrivals are further apart than the room allows');
  }
}

// Atomic publication: immutable proposal + private impacts + PENDING consent
// rows + milestone + job SUCCEEDED, all in one transaction. The terms digest is
// computed here from the exact termsJson bytes; send canonical JSON.
export const publishProposal = spacetimedb.procedure(
  {
    jobId: t.u64(),
    attempt: t.u32(),
    requestKey: t.string(),
    stage: t.string(),
    termsJson: t.string(),
    sharedSummaryJson: t.string(),
    validationJson: t.string(),
    evidenceRefs: t.array(t.string()),
    affectedMemberIds: t.array(t.u64()),
    impacts: t.array(ImpactInput),
    // Trip candidate refs to validate server-side (empty for stages without candidates).
    selectedCandidateRefs: t.array(t.string()),
  },
  t.string(),
  (ctx, args) =>
    runProcedure(() =>
      ctx.withTx(tx => {
        requireWorker(tx);
        return withReceipt(tx, 'publish_proposal', args.requestKey, args, () => {
          const j = requireLease(tx, args.jobId, args.attempt);
          if (j.kind !== 'PLAN') fail('INVALID_STATE', 'only PLAN jobs publish proposals');
          const r = requireRoom(tx, j.roomId);
          oneOf(args.stage, STAGES, 'stage');
          parseJson(args.termsJson, 'termsJson');
          parseJson(args.sharedSummaryJson, 'sharedSummaryJson');
          parseJson(args.validationJson, 'validationJson');
          for (const ref of args.evidenceRefs) {
            if (!refResolves(tx, r.id, ref)) fail('INVALID_EVIDENCE_REF', `evidence ${ref} is not recorded in this room`);
          }
          // A trip that was described and onboarded waits for everyone's confirmed onboarding.
          if (tx.db.tripBrief.roomId.find(r.id)) {
            for (const m of tx.db.membership.byRoomMember.filter(r.id)) {
              if (m.active && findIntake(tx, r.id, m.member)?.state !== 'CONFIRMED') {
                fail('INTAKE_INCOMPLETE', 'every traveller must finish onboarding first');
              }
            }
          }
          if (args.selectedCandidateRefs.length > 0) validateTripSelection(tx, r, args.selectedCandidateRefs);
          if (containsPrivateText(tx, r.id, args.termsJson) || containsPrivateText(tx, r.id, args.sharedSummaryJson)) {
            fail('PRIVATE_CONTENT_DETECTED', 'shared proposal text contains a participant’s private reason');
          }

          let affected: Identity[];
          if (args.affectedMemberIds.length > 0) {
            affected = args.affectedMemberIds.map(id => requireMembershipById(tx, r.id, id).member);
          } else {
            affected = [...tx.db.membership.byRoomMember.filter(r.id)].filter(m => m.active).map(m => m.member);
          }
          if (affected.length === 0) fail('INVALID_STATE', 'no active members to review the proposal');

          let version = 0;
          for (const p of [...tx.db.proposal.byRoomStage.filter([r.id, args.stage])]) {
            version = Math.max(version, p.version);
            if (p.state === 'AWAITING_REVIEW' || p.state === 'AGREED') {
              tx.db.proposal.id.update({ ...p, state: 'SUPERSEDED' });
              for (const c of [...tx.db.consent.byProposalMember.filter(p.id)]) {
                tx.db.consent.id.update({ ...c, response: 'STALE', updatedAt: tx.timestamp });
              }
            }
          }

          const termsDigest = digest(args.termsJson);
          const p = tx.db.proposal.insert({
            id: 0n,
            roomId: r.id,
            stage: args.stage,
            version: version + 1,
            jobId: j.id,
            inputRevision: j.inputRevision,
            membershipRevision: j.membershipRevision,
            permissionRevision: j.permissionRevision,
            termsJson: args.termsJson,
            termsDigest,
            sharedSummaryJson: args.sharedSummaryJson,
            validationJson: args.validationJson,
            evidenceRefs: args.evidenceRefs,
            affectedMembers: affected,
            state: 'AWAITING_REVIEW',
            createdAt: tx.timestamp,
          });
          for (const impact of args.impacts) {
            parseJson(impact.impactJson, 'impactJson');
            const m = requireMembershipById(tx, r.id, impact.memberId);
            tx.db.participantImpact.insert({
              id: 0n,
              proposalId: p.id,
              roomId: r.id,
              owner: m.member,
              impactJson: impact.impactJson,
            });
          }
          for (const member of affected) {
            tx.db.consent.insert({
              id: 0n,
              proposalId: p.id,
              roomId: r.id,
              member,
              termsDigest,
              response: 'PENDING',
              note: undefined,
              updatedAt: tx.timestamp,
            });
          }
          scheduleReviewReminders(tx, p.id, affected);
          setMilestone(tx, r.id, args.stage, 'IN_REVIEW', p.id);
          closeJobThreads(tx, j.id, 'RESOLVED');
          tx.db.job.id.update({
            ...j,
            status: 'SUCCEEDED',
            outcome: 'PUBLISHED',
            outcomeJson: JSON.stringify({ proposal_id: Number(p.id) }),
            leaseOwner: undefined,
            leaseExpiresAt: undefined,
            updatedAt: tx.timestamp,
          });
          tx.db.room.id.update({ ...r, status: 'AWAITING_REVIEW' });
          emitEvent(tx, r.id, 'PROPOSAL_PUBLISHED', { proposal_id: Number(p.id), version: p.version, stage: p.stage });
          return { proposal_id: Number(p.id), version: p.version, terms_digest: termsDigest };
        }) as object;
      })
    )
);

const RESPONSE_TO_CONSENT: Record<string, string> = {
  ACCEPT: 'ACCEPTED',
  NOT_COMFORTABLE: 'DECLINED',
  SUGGEST_ALTERNATIVE: 'DECLINED',
  ASK_MY_AGENT: 'PENDING',
  DECIDE_LATER: 'PENDING',
};

// Human-only. Approval binds to the exact version, digest and current room
// revisions; anything stale is rejected rather than applied to new terms.
export const respondToProposal = spacetimedb.reducer(
  {
    proposalId: t.u64(),
    version: t.u32(),
    termsDigest: t.string(),
    response: t.string(),
    note: t.option(t.string()),
  },
  (ctx, args) => {
    requireHuman(ctx);
    const p = ctx.db.proposal.id.find(args.proposalId);
    if (!p) fail('NOT_FOUND', `proposal ${args.proposalId}`);
    requireMember(ctx, p.roomId);
    if (!p.affectedMembers.some(m => m.isEqual(ctx.sender))) fail('NOT_AUTHORIZED', 'you are not asked to review this proposal');
    if (p.state === 'SUPERSEDED') fail('STALE_SNAPSHOT', `proposal ${p.id} was superseded`);
    if (p.state !== 'AWAITING_REVIEW') fail('INVALID_STATE', `proposal ${p.id} is ${p.state}; use withdraw_consent`);
    if (p.version !== args.version || p.termsDigest !== args.termsDigest) {
      fail('STALE_SNAPSHOT', 'version or terms digest does not match the current proposal');
    }
    const r = requireRoom(ctx, p.roomId);
    if (
      r.inputRevision !== p.inputRevision ||
      r.membershipRevision !== p.membershipRevision ||
      r.permissionRevision !== p.permissionRevision
    ) {
      fail('STALE_SNAPSHOT', 'the room changed after this proposal was published');
    }
    oneOf(args.response, RESPONSES, 'response');

    for (const c of ctx.db.consent.byProposalMember.filter([p.id, ctx.sender])) {
      ctx.db.consent.id.update({
        ...c,
        response: RESPONSE_TO_CONSENT[args.response],
        note: args.note,
        updatedAt: ctx.timestamp,
      });
    }
    if (args.response === 'NOT_COMFORTABLE' || args.response === 'ASK_MY_AGENT' || args.response === 'SUGGEST_ALTERNATIVE') {
      messageOwnAgent(ctx, p.roomId, `[${args.response} on proposal ${p.id} v${p.version}] ${args.note ?? ''}`.trim());
    }
    recomputeAgreement(ctx, p.id);
  }
);

export const withdrawConsent = spacetimedb.reducer(
  { proposalId: t.u64(), note: t.option(t.string()) },
  (ctx, { proposalId, note }) => {
    requireHuman(ctx);
    const p = ctx.db.proposal.id.find(proposalId);
    if (!p) fail('NOT_FOUND', `proposal ${proposalId}`);
    requireMember(ctx, p.roomId);
    if (p.state !== 'AWAITING_REVIEW' && p.state !== 'AGREED') fail('INVALID_STATE', `proposal ${p.id} is ${p.state}`);
    let withdrew = false;
    for (const c of ctx.db.consent.byProposalMember.filter([p.id, ctx.sender])) {
      if (c.response !== 'ACCEPTED') fail('INVALID_STATE', 'there is no acceptance to withdraw');
      ctx.db.consent.id.update({ ...c, response: 'WITHDRAWN', note, updatedAt: ctx.timestamp });
      withdrew = true;
    }
    if (!withdrew) fail('NOT_AUTHORIZED', 'you are not asked to review this proposal');
    messageOwnAgent(ctx, p.roomId, `[WITHDRAWN from proposal ${p.id} v${p.version}] ${note ?? ''}`.trim());
    recomputeAgreement(ctx, p.id);
  }
);
