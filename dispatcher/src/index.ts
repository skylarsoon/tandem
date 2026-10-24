// Tandem dispatcher: subscribes to the worker-only `worker_pending_jobs` view
// and wakes the Python worker whenever a job becomes claimable. It never claims
// jobs or writes to the database; the worker claims through `claim_job`, and
// its own startup scan plus polling recover anything a wake-up misses.
//
// Environment:
//   STDB_URL         SpacetimeDB host (default http://127.0.0.1:3000)
//   STDB_DB          database name (default tandem-dev)
//   STDB_TOKEN       long-lived token of an identity registered with add_worker
//   WORKER_WAKE_TOKEN shared private secret for the worker endpoint
//   WORKER_WAKE_URL  optional; POST {"job_id", "room_id", "kind"} here per job

import { DbConnection } from './module_bindings';

const STDB_URL = process.env.STDB_URL ?? 'http://127.0.0.1:3000';
const STDB_DB = process.env.STDB_DB ?? 'tandem-dev';
// Local development shares the root private env file with the Python worker.
const STDB_TOKEN = process.env.STDB_TOKEN ?? process.env.STDB_WORKER_TOKEN;
const WORKER_WAKE_URL = process.env.WORKER_WAKE_URL;
const WORKER_WAKE_TOKEN = process.env.WORKER_WAKE_TOKEN;

type PendingJob = { id: bigint; roomId: bigint; kind: string };

async function wake(job: PendingJob): Promise<void> {
  const body = { job_id: Number(job.id), room_id: Number(job.roomId), kind: job.kind };
  if (!WORKER_WAKE_URL) {
    console.log(`[dispatcher] pending job ${body.job_id} (${body.kind}); no WORKER_WAKE_URL set`);
    return;
  }
  try {
    const res = await fetch(WORKER_WAKE_URL, {
      method: 'POST',
      headers: { 'content-type': 'application/json', ...(WORKER_WAKE_TOKEN ? { authorization: `Bearer ${WORKER_WAKE_TOKEN}` } : {}) },
      signal: AbortSignal.timeout(5000),
      body: JSON.stringify(body),
    });
    console.log(`[dispatcher] woke worker for job ${body.job_id} (${body.kind}): HTTP ${res.status}`);
  } catch (err) {
    // The worker's reconciliation scan picks the job up later.
    console.warn(`[dispatcher] wake for job ${body.job_id} failed: ${(err as Error).message}`);
  }
}

if (!STDB_TOKEN) {
  // First run: mint an identity and print what to register, then exit.
  DbConnection.builder()
    .withUri(STDB_URL)
    .withDatabaseName(STDB_DB)
    .onConnect((conn, identity, token) => {
      console.log('No STDB_TOKEN set. A new identity was created for this dispatcher.');
      console.log(`  identity: 0x${identity.toHexString()}`);
      console.log(`  token:    ${token}`);
      console.log('Register it as the database owner, then rerun with STDB_TOKEN set:');
      console.log(`  spacetime call ${STDB_DB} add_worker '{"__identity__":"0x${identity.toHexString()}"}' '"dispatcher"'`);
      conn.disconnect();
      process.exit(1);
    })
    .build();
} else {
  const connection = DbConnection.builder()
    .withUri(STDB_URL)
    .withDatabaseName(STDB_DB)
    .withToken(STDB_TOKEN)
    .withAutomaticReconnect()
    .onConnect((conn, identity) => {
      console.log(`[dispatcher] connected to ${STDB_URL}/${STDB_DB} as 0x${identity.toHexString()}`);
      conn
        .subscriptionBuilder()
        .onApplied(() => console.log(`[dispatcher] watching ${conn.db.workerPendingJobs.count()} pending job(s)`))
        .onError(ctx => console.error(`[dispatcher] subscription error: ${ctx.event?.message ?? 'unknown'}`))
        .subscribe(['SELECT * FROM worker_pending_jobs']);
    })
    .onConnectError((_ctx, err) => console.error(`[dispatcher] connection error: ${err.message}`))
    .onDisconnect(() => console.warn('[dispatcher] disconnected; reconnecting'))
    .build();

  // Registered once. Fires for rows in each initial snapshot (including after a
  // reconnect) and whenever a job becomes PENDING again, e.g. after its lease expires.
  connection.db.workerPendingJobs.onInsert((_ctx, job) => void wake(job));
}
