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
  refreshMilestone,
  withReceipt,
} from './guards';
import { commitments, messageOwnAgent, recomputeAgreement, scheduleReviewReminders, supersedeProposal } from './effects';
import { findIntake } from './onboarding';
import { bindingRules, checkCandidateRules, checkNeeds, costLimitsHold } from './room_rules';

const ImpactInput = t.object('ImpactInput', { memberId: t.u64(), impactJson: t.string() });
// burdenMicros: the 0..1 compromise burden in millionths (0..1_000_000).
const BurdenInput = t.object('BurdenInput', { memberId: t.u64(), burdenMicros: t.u32() });
const MAX_BURDEN_MICROS = 1_000_000;

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
  start_epoch?: unknown;
  end_epoch?: unknown;
};

// A complete total is the only price people can approve for bookings.
// Activities and dinners only ever have a per-person estimate, labelled as such.
const PRICE_BASIS: Record<string, string> = {
  FLIGHT: 'COMPLETE_FLIGHT_TOTAL',
  STAY: 'COMPLETE_STAY_TOTAL',
  TRANSFER: 'COMPLETE_TRANSFER_TOTAL',
  ACTIVITY: 'ESTIMATE_PER_PERSON',
  DINNER: 'ESTIMATE_PER_PERSON',
};

// What a proposal decides, and so which checks it must pass:
// FLIGHT_BUNDLE          one flight per active member, arrivals together
// SINGLE_SHARED_BOOKING  one STAY or TRANSFER the room asked for, fitting everyone
// ACTIVITY_DECISION      include one ACTIVITY or DINNER (no candidate = skip it)
// COMPLETE_PLAN          the whole trip at once: flights plus every shared part
// GENERAL_AGREEMENT      no candidates (goals, dates)
const SCOPES = [
  'FLIGHT_BUNDLE',
  'SINGLE_SHARED_BOOKING',
  'ACTIVITY_DECISION',
  'COMPLETE_PLAN',
  'GENERAL_AGREEMENT',
] as const;

type Member = ReturnType<typeof activeMembers>[number];
function activeMembers(ctx: Ctx, roomId: bigint) {
  return [...ctx.db.membership.byRoomMember.filter(roomId)].filter(m => m.active);
}

// Costs a set of selections adds to each person, for the whole-trip budget:
// their own flight, an equal share of shared bookings, per-person activities.
type Costs = { flights: Map<bigint, number>; shared: number; perPerson: number };
const noCosts = (): Costs => ({ flights: new Map(), shared: 0, perPerson: 0 });

// A recorded candidate with current, complete-enough price evidence.
function loadCandidate(ctx: Ctx, r: RoomRow, ref: string): { category: string; facts: CandidateFacts; total: number } {
  let row;
  for (const c of ctx.db.candidate.byRoomRef.filter([r.id, ref])) row = c;
  if (!row) fail('INVALID_EVIDENCE_REF', `candidate ${ref} is not recorded in this room`);
  const facts = JSON.parse(row.factsJson) as CandidateFacts;
  const category = typeof facts.category === 'string' ? facts.category : 'FLIGHT';
  if (category !== row.category) fail('INVALID_PROPOSAL', `candidate ${ref} has inconsistent category`);
  if (typeof facts.total_cents !== 'number') fail('INVALID_PROPOSAL', `candidate ${ref} has no known total price`);
  const missing = Array.isArray(facts.missing_critical_fields) ? facts.missing_critical_fields : [];
  if (facts.cost_basis !== PRICE_BASIS[category] || missing.length > 0) {
    fail('INVALID_PROPOSAL', `candidate ${ref} has incomplete cost evidence`);
  }
  const nowSeconds = Number(ctx.timestamp.microsSinceUnixEpoch / 1000n) / 1000;
  if (
    typeof facts.observed_at !== 'number' ||
    typeof facts.valid_until !== 'number' ||
    facts.observed_at > nowSeconds ||
    facts.valid_until <= nowSeconds
  ) {
    fail('INVALID_PROPOSAL', `candidate ${ref} evidence is not current`);
  }
  return { category, facts, total: facts.total_cents };
}

function roomSettings(r: RoomRow) {
  const settings = JSON.parse(r.settingsJson) as { arrival_overlap_seconds?: number; components?: string[] };
  return { overlap: settings.arrival_overlap_seconds ?? 7200, components: settings.components ?? ['FLIGHT'] };
}

// One flight per active member, each within its owner's hard limits, all
// landing within the room's arrival overlap.
function checkFlights(ctx: Ctx, r: RoomRow, active: Member[], refs: string[], costs: Costs): void {
  const arrivals: number[] = [];
  for (const ref of refs) {
    const { facts, total } = loadCandidate(ctx, r, ref);
    const member = active.find(m => Number(m.id) === facts.owner_member_id);
    if (!member || costs.flights.has(member.id)) fail('INVALID_COVERAGE', `candidate ${ref} has no unique active owner`);
    costs.flights.set(member.id, total);
    if (!ctx.db.participationPreferences.owner.find(member.member)?.confirmed) {
      fail('INVALID_PROPOSAL', 'every traveller must confirm participation preferences');
    }
    if (typeof facts.arrival_epoch !== 'number') fail('INVALID_PROPOSAL', `candidate ${ref} has no arrival time`);
    // The traveller's own rules on their own flight (fare, bag, landing time, stops...).
    checkCandidateRules(bindingRules(ctx, r.id), [member.member], 'FLIGHT', facts as Record<string, unknown>, ref);
    arrivals.push(facts.arrival_epoch);
  }
  if (costs.flights.size !== active.length) fail('INVALID_COVERAGE', 'every active member needs exactly one selected flight');
  if (arrivals.length > 0 && Math.max(...arrivals) - Math.min(...arrivals) > roomSettings(r).overlap) {
    fail('INVALID_PROPOSAL', 'arrivals are further apart than the room allows');
  }
}

// A shared booking the room asked for, with room for everyone; split equally.
function checkShared(ctx: Ctx, r: RoomRow, active: Member[], ref: string, costs: Costs): string {
  const { category, facts, total } = loadCandidate(ctx, r, ref);
  if (category !== 'STAY' && category !== 'TRANSFER') fail('INVALID_COVERAGE', `${ref} is not a shared booking`);
  if (!roomSettings(r).components.includes(category)) fail('INVALID_COVERAGE', `this room isn't planning a ${category}`);
  if (typeof facts.capacity !== 'number' || facts.capacity < active.length) {
    fail('INVALID_PROPOSAL', `candidate ${ref} doesn't fit everyone`);
  }
  // Everyone's rules on the shared pick, and what it must hold for them.
  const people = active.map(m => m.member);
  checkCandidateRules(bindingRules(ctx, r.id), people, category, facts as Record<string, unknown>, ref);
  checkNeeds(ctx, r.id, people, JSON.parse(r.settingsJson), category, facts as Record<string, unknown>, ref);
  costs.shared += total;
  return category;
}

// Each person's whole-trip cap: own flight + per-person items + equal share of
// shared bookings, compared as n × (own) + shared ≤ n × cap (exact in cents).
// Checked for this proposal alone, then together with the room's commitments,
// so the error says which one breaks it.
function checkTripBudgets(ctx: Ctx, r: RoomRow, active: Member[], own: Costs, committed?: Costs): void {
  const n = active.length;
  const rules = bindingRules(ctx, r.id);
  const over = (c: Costs[]) =>
    active.some(m => {
      const mine = c.reduce((sum, x) => sum + (x.flights.get(m.id) ?? 0) + x.perPerson, 0);
      const shared = c.reduce((sum, x) => sum + x.shared, 0);
      return !costLimitsHold(rules, m.member, n, mine, shared);
    });
  if (over([own])) fail('INVALID_PROPOSAL', 'a traveller’s share of the trip exceeds a hard limit');
  if (committed && over([own, committed])) {
    fail('CONFLICTS_WITH_COMMITMENT', 'with what the group already agreed, a traveller’s share of the trip exceeds a hard limit');
  }
}

type Commitment = ReturnType<typeof commitments>[number];

// What the room's other agreed decisions already cost each person. A complete
// plan already contains its parts, so it isn't added on top of them.
function committedCosts(ctx: Ctx, r: RoomRow, others: Commitment[]): Costs {
  const costs = noCosts();
  for (const c of others) {
    if (c.scope === 'COMPLETE_PLAN' || c.scope === 'GENERAL_AGREEMENT' || c.scope === 'LEGACY') continue;
    for (const ref of c.selectedRefs) {
      let row;
      for (const x of ctx.db.candidate.byRoomRef.filter([r.id, ref])) row = x;
      if (!row) continue;
      const facts = JSON.parse(row.factsJson) as CandidateFacts;
      if (typeof facts.total_cents !== 'number') continue;
      if (row.category === 'FLIGHT') {
        const owner = activeMembers(ctx, r.id).find(m => Number(m.id) === facts.owner_member_id);
        if (owner) costs.flights.set(owner.id, (costs.flights.get(owner.id) ?? 0) + facts.total_cents);
      } else if (row.category === 'STAY' || row.category === 'TRANSFER') costs.shared += facts.total_cents;
      else costs.perPerson += facts.total_cents;
    }
  }
  return costs;
}

function categoryOf(ctx: Ctx, roomId: bigint, ref: string): string | undefined {
  for (const c of ctx.db.candidate.byRoomRef.filter([roomId, ref])) return c.category;
  return undefined;
}

const overlaps = (a: CandidateFacts, b: CandidateFacts) =>
  typeof a.start_epoch === 'number' && typeof a.end_epoch === 'number' &&
  typeof b.start_epoch === 'number' && typeof b.end_epoch === 'number' &&
  a.start_epoch < b.end_epoch && b.start_epoch < a.end_epoch;

// Independent server-side check of a proposal for its scope, so a buggy or
// compromised worker can't publish something that breaks a confirmed hard
// limit or contradicts what the group already agreed. Room preferences owners
// can relax by consent (bedrooms, rating) stay in the worker.
function validateScope(ctx: Ctx, r: RoomRow, scope: string, decisionId: string, refs: string[]): void {
  const active = activeMembers(ctx, r.id);
  const others = commitments(ctx, r.id).filter(c => c.decisionId !== decisionId);
  const own = noCosts();
  const conflict = (detail: string) => fail('CONFLICTS_WITH_COMMITMENT', detail);

  if (scope === 'GENERAL_AGREEMENT') {
    if (refs.length > 0) fail('INVALID_COVERAGE', 'a general agreement selects no candidates');
    return;
  }
  if (scope === 'COMPLETE_PLAN') {
    // The whole trip in one proposal: flights plus exactly one of each shared
    // part the room's `components` lists. It stands on its own.
    const flights = refs.filter(ref => categoryOf(ctx, r.id, ref) === 'FLIGHT');
    const sharedRefs = refs.filter(ref => !flights.includes(ref));
    checkFlights(ctx, r, active, flights, own);
    const seen = new Set<string>();
    for (const ref of sharedRefs) {
      const category = checkShared(ctx, r, active, ref, own);
      if (seen.has(category)) fail('INVALID_COVERAGE', `unexpected second ${category} ${ref}`);
      seen.add(category);
    }
    const required = roomSettings(r).components.filter(c => c !== 'FLIGHT');
    if (required.some(c => !seen.has(c))) fail('INVALID_COVERAGE', 'every shared part of the trip needs exactly one option');
    checkTripBudgets(ctx, r, active, own);
    return;
  }
  if (scope === 'FLIGHT_BUNDLE') {
    if (refs.some(ref => categoryOf(ctx, r.id, ref) !== 'FLIGHT')) fail('INVALID_COVERAGE', 'a flight bundle selects only flights');
    if (others.some(c => c.scope === 'FLIGHT_BUNDLE')) conflict('flights are already agreed as another decision; revise that one');
    checkFlights(ctx, r, active, refs, own);
  } else if (scope === 'SINGLE_SHARED_BOOKING') {
    if (refs.length !== 1) fail('INVALID_COVERAGE', 'a shared booking selects exactly one candidate');
    const category = checkShared(ctx, r, active, refs[0], own);
    if (others.some(c => c.scope === 'SINGLE_SHARED_BOOKING' && c.selectedRefs.some(x => categoryOf(ctx, r.id, x) === category))) {
      conflict(`a ${category} is already agreed as another decision; revise that one`);
    }
  } else if (scope === 'ACTIVITY_DECISION') {
    if (refs.length > 1) fail('INVALID_COVERAGE', 'an activity decision includes at most one candidate');
    if (refs.length === 0) return; // skipping it costs nothing and can't conflict
    const { category, facts, total } = loadCandidate(ctx, r, refs[0]);
    if (category !== 'ACTIVITY' && category !== 'DINNER') fail('INVALID_COVERAGE', `${refs[0]} is not an activity`);
    if (typeof facts.capacity === 'number' && facts.capacity < active.length) {
      fail('INVALID_PROPOSAL', `candidate ${refs[0]} doesn't fit everyone`);
    }
    checkCandidateRules(bindingRules(ctx, r.id), active.map(m => m.member), category, facts as Record<string, unknown>, refs[0]);
    for (const c of others) {
      if (c.scope !== 'ACTIVITY_DECISION') continue;
      for (const ref of c.selectedRefs) {
        if (ref === refs[0]) conflict(`${ref} is already agreed as another decision`);
        let row;
        for (const x of ctx.db.candidate.byRoomRef.filter([r.id, ref])) row = x;
        if (row && overlaps(facts, JSON.parse(row.factsJson) as CandidateFacts)) {
          conflict(`${refs[0]} overlaps ${ref}, which the group already agreed`);
        }
      }
    }
    own.perPerson += total;
  } else {
    fail('INVALID_ARGUMENT', `unknown scope ${scope}`);
  }
  checkTripBudgets(ctx, r, active, own, committedCosts(ctx, r, others));
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
    // The decision this is a version of (e.g. "flights", "stay", "activity_snorkeling"):
    // it supersedes only earlier versions of the same decision.
    decisionId: t.string(),
    // What it decides, which picks the server-side checks (see SCOPES).
    scope: t.string(),
    termsJson: t.string(),
    sharedSummaryJson: t.string(),
    validationJson: t.string(),
    evidenceRefs: t.array(t.string()),
    affectedMemberIds: t.array(t.u64()),
    impacts: t.array(ImpactInput),
    // Per member, what agreeing would cost them (empty = nothing to record).
    burdens: t.array(BurdenInput),
    // Candidate refs this proposal selects, validated server-side for its scope.
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
          oneOf(args.scope, SCOPES, 'scope');
          if (!/^[a-z0-9][a-z0-9_:.-]{0,127}$/.test(args.decisionId)) {
            fail('INVALID_ARGUMENT', 'decisionId must be 1-128 lowercase letters, digits or _:.-');
          }
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
          validateScope(tx, r, args.scope, args.decisionId, args.selectedCandidateRefs);
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

          // Only earlier versions of this same decision are replaced; other
          // decisions, pending or agreed, are untouched.
          let version = 0;
          const replaced = new Set<string>();
          for (const d of [...tx.db.proposalDecision.byRoomDecision.filter([r.id, args.decisionId])]) {
            const old = tx.db.proposal.id.find(d.proposalId);
            if (!old) continue;
            version = Math.max(version, old.version);
            if (old.state === 'AWAITING_REVIEW' || old.state === 'AGREED') {
              supersedeProposal(tx, old);
              replaced.add(old.stage);
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
          tx.db.proposalDecision.insert({
            proposalId: p.id,
            roomId: r.id,
            decisionId: args.decisionId,
            scope: args.scope,
            selectedRefs: args.selectedCandidateRefs,
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
          const seen = new Set<bigint>();
          for (const b of args.burdens) {
            if (b.burdenMicros > MAX_BURDEN_MICROS) fail('INVALID_ARGUMENT', 'burden must be between 0 and 1');
            const m = requireMembershipById(tx, r.id, b.memberId);
            if (!affected.some(a => a.isEqual(m.member))) fail('INVALID_ARGUMENT', 'burden for someone not reviewing this proposal');
            if (seen.has(b.memberId)) fail('INVALID_ARGUMENT', `duplicate burden for member ${b.memberId}`);
            seen.add(b.memberId);
            tx.db.proposalBurden.insert({
              id: 0n,
              proposalId: p.id,
              roomId: r.id,
              memberId: b.memberId,
              burdenMicros: b.burdenMicros,
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
          for (const stage of replaced) refreshMilestone(tx, r.id, stage);
          refreshMilestone(tx, r.id, args.stage);
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
          emitEvent(tx, r.id, 'PROPOSAL_PUBLISHED', {
            proposal_id: Number(p.id),
            version: p.version,
            stage: p.stage,
            decision_id: args.decisionId,
          });
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
