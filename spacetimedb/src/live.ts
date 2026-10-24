import { t } from 'spacetimedb/server';
import spacetimedb, { reviewReminder } from './schema';
import { type Ctx, AGENT_ACTIVITIES, emitEvent, fail, isWorker, oneOf, requireLease } from './guards';

// ---------- Presence ----------

// Counts open connections per human so several tabs don't flicker offline.
// Service identities (worker, dispatcher) are not people and are skipped.
function trackPresence(ctx: Ctx, delta: 1 | -1): void {
  if (isWorker(ctx)) return;
  const row = ctx.db.presence.identity.find(ctx.sender);
  const connections = Math.max(0, (row?.connections ?? 0) + delta);
  const fields = { connections, online: connections > 0, lastSeen: ctx.timestamp };
  if (row) ctx.db.presence.identity.update({ ...row, ...fields });
  else ctx.db.presence.insert({ identity: ctx.sender, ...fields });
}

export const onConnect = spacetimedb.clientConnected(ctx => trackPresence(ctx, 1));
export const onDisconnect = spacetimedb.clientDisconnected(ctx => trackPresence(ctx, -1));

// ---------- Agent activity ----------

// The worker reports what an agent is doing ("SEARCHING", "WRITING", …) so
// every screen in the room can animate it live. Only valid under the job's
// lease; the room view shows IDLE once the job is no longer running.
export const setAgentActivity = spacetimedb.reducer(
  { jobId: t.u64(), attempt: t.u32(), agentId: t.u64(), activity: t.string(), threadId: t.option(t.u64()) },
  (ctx, args) => {
    const j = requireLease(ctx, args.jobId, args.attempt);
    oneOf(args.activity, AGENT_ACTIVITIES, 'activity');
    const a = ctx.db.agent.id.find(args.agentId);
    if (!a || a.roomId !== j.roomId) fail('NOT_AUTHORIZED', `agent ${args.agentId} is not in room ${j.roomId}`);
    const fields = {
      roomId: j.roomId,
      jobId: j.id,
      activity: args.activity,
      threadId: args.threadId,
      updatedAt: ctx.timestamp,
    };
    const existing = ctx.db.agentStatus.agentId.find(a.id);
    if (existing) ctx.db.agentStatus.agentId.update({ ...existing, ...fields });
    else ctx.db.agentStatus.insert({ agentId: a.id, ...fields });
  }
);

// ---------- Review reminders ----------

// Owner-only: how long reviewers wait before a private nudge (default 10 min).
export const setReminderDelay = spacetimedb.reducer({ seconds: t.u32() }, (ctx, { seconds }) => {
  const cfg = ctx.db.config.id.find(0);
  if (!cfg || !cfg.owner.isEqual(ctx.sender)) fail('NOT_AUTHORIZED', 'only the database owner can change reminders');
  if (seconds < 1) fail('INVALID_ARGUMENT', 'seconds must be at least 1');
  ctx.db.config.id.update({ ...cfg, reminderDelaySeconds: seconds });
});

// Fires once per reviewer. Sends a private nudge only if the proposal is still
// open, their response is still pending, and they haven't asked for time.
export const sendReviewReminder = spacetimedb.reducer(
  { onSchedule: reviewReminder },
  { arg: reviewReminder.rowType },
  (ctx, { arg }) => {
    const p = ctx.db.proposal.id.find(arg.proposalId);
    if (!p || p.state !== 'AWAITING_REVIEW') return;
    const prefs = ctx.db.participationPreferences.owner.find(arg.member);
    if (!prefs || !prefs.confirmed || prefs.giveMeTime) return;
    for (const c of ctx.db.consent.byProposalMember.filter([p.id, arg.member])) {
      if (c.response !== 'PENDING') return;
    }
    emitEvent(ctx, p.roomId, 'REVIEW_REMINDER', { proposal_id: Number(p.id), version: p.version, stage: p.stage }, arg.member);
  }
);
