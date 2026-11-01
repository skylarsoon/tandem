# Live planning activity

The conversation and discussion views share a **Behind the plan** panel. Its summary stays outside transcript scrolling. Expand it to see Apify searches, provider/actor/run references, usable result counts, agent status, planning stages, and a timestamped activity history. It follows room subscriptions and never starts searches from the browser.

`RESEARCH_ACTIVITY` events come from the worker's actual Apify gateway transitions: queued, starting, running (provider status checks), collecting, completed, cached, or failed. A request-scoped observer binds them to the current job, generation, attempt, round, and input/membership/permission revisions through the existing lease-checked event reducer. The room research router's dynamically created gateways inherit the observer. Search inputs, owner identities, dataset contents, private reasons, prompts, provider exception text, and credentials are excluded. Recognized failures are translated into fixed user-facing messages.

Recent dataset reuse is labeled explicitly and does not start a new actor. A usable result count is a normalizer output count, not a claim that all options satisfy the group. Poll-deadline failure means the worker stopped monitoring; it does not claim the remote actor was cancelled. Queues are distinct from running searches. Disconnects, superseded attempts, stopped workflows, and updates older than 45 seconds never display as active work. No progress percentage or completion ETA is invented. Earlier inputs/jobs remain available in history.

Existing workflow and agent activity records supply planning and conversational progress. If there are no execution events, the UI says so rather than claiming a worker is busy. This is an activity record, not a global service-uptime monitor. Long model calls without new events will appear as awaiting updates. Historical actor runs from before this feature cannot be reconstructed from local logs in the UI.

## Verification

- Worker unit/workflow suites exercise ordering, resumable cache reuse, privacy-safe errors, context cleanup, and reporting outages.
- Frontend activity tests cover revision isolation, simultaneous queues, terminal states, stale updates and disconnected clients.
- `web/e2e/activity.spec.ts` uses a disposable local database and real event subscriptions to check both conversation views, mobile layout and offline behavior. Its provider events are test fixtures; it performs no paid actor calls.

Deploy the frontend and reload the existing bounded worker to enable new run events. No schema migration, new API key, additional actor run, or higher provider budget is required.
