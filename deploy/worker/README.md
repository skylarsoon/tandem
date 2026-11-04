# Cloud worker migration

Status: deployed to Google Cloud on November 3, 2026 (America/New_York). Real profile, intake, provider searches, shared discussion, and two-person proposal approval verified. The Mac launch agent is disabled.

## Google Cloud deployment

- Project: `traveladvisor-393606` (TravelAdvisor, explicitly approved).
- VM: `tandem-worker`, zone `us-central1-a`, `e2-small` (2 GiB RAM).
- Network: `tandem-worker`; subnet: `tandem-worker-us-central1`.
- Ubuntu 24.04, 30 GB standard persistent boot disk, retained on VM deletion.
- No attached Google service account or public application port. SSH is restricted
  to the operator's IP by `tandem-worker-ssh`; update that rule if the IP changes.
- Automatic VM restart and systemd `tandem-worker.service` enabled.
- Estimated baseline about $17/month before credits, discounts, traffic and tax;
  credits are linked to the billing account confirmed by the owner.

The earlier Oracle attempt failed with out-of-host-capacity; it created networking
but no VM or boot volume. Render and Railway were not deployed.

## Install while disabled

Use Ubuntu with Python 3.12 and a dedicated unprivileged `tandem` user.
Install the optimizer and worker[live] packages in /opt/tandem/venv from the
reviewed source revision. Use deploy/worker/constraints.txt when installing the reviewed Linux x86_64 wheels.
Keep source root-owned and /var/lib/tandem owned by tandem (0700).
Install worker credentials in /etc/tandem/worker.env (root:tandem, 0640),
using the existing model, actor build and backend to preserve budget scope.
Do not put credentials or private state in Git, cloud-init, command arguments,
logs or a public artifact. Copy tandem-worker.service to the systemd unit
folder. It starts with provider calls disabled.

## Handoff

1. Confirm no active jobs, inspect local process ownership, and disable/bootout
   com.tandem.worker on the Mac so it cannot automatically return.
2. Wait until its PID exits completely, including draining pending work.
3. Run export_state.py with source directory, a new private destination,
   worker env-file and the stopped source-pid. It refuses a running PID.
4. Transfer all three SQLite files and migration.json over verified SSH into
   /var/lib/tandem/state. Preserve restricted permissions and verify_state.
   Never initialize an empty provider ledger during a migration or recovery.
5. Create /etc/tandem/activation.env with TANDEM_CLOUD_ENABLED=1, then enable
   and start the systemd service. Keep the Mac service disabled.
6. Verify real profile, intake and planning jobs, actual search results,
   subscriptions in the deployed UI, and usage counters. Restart the cloud
   worker and confirm state and counters persist.

Limits: 500 Claude calls and 40 Apify starts per UTC day, $1 per actor run.
Transfer exact-date exceptions unchanged; do not renew expired exceptions.
The November 1, 2026 exception does not apply to later dates.

Rollback must first stop the cloud process completely and transfer its latest
state back. Never restart the Mac with the pre-migration snapshot after the
cloud worker has spent provider calls. Missing or damaged cloud state requires
reconciling usage before enabling either host.

Dockerfile.worker remains available for container packaging; production currently runs the systemd VM service.

## Operational checks

On the VM, use `sudo systemctl status tandem-worker` and
`sudo journalctl -u tandem-worker --since "10 minutes ago"`. The service logs
allowlisted outcomes; do not print worker.env or raw private databases.
The ready message reports a three-second poll interval and no HTTP listener.

The private environment is /etc/tandem/worker.env; persistent state is
/var/lib/tandem/state. Install changes with install-vm.sh only while the service
is stopped and after taking a consistent backup. Do not reset the state directory.

Check current job status in SpacetimeDB and confirm an actual provider result in
the app; an active systemd process alone is not proof that planning completed.


## Verification record — November 4, 2026 UTC

- Cloud service restart preserved migrated state and limits; the service returned
  ready. The Mac launch agent remained disabled. VM reboot was not tested.
- A fresh profile summary and private intake jobs completed in the deployed app.
- Trip 37 returned real flight, accommodation, activity and restaurant results.
  Shared agent messages and private follow-up questions appeared in the UI.
  Its accommodation proposal remained exploratory because fees were unconfirmed.
- After restart, job 4487 refreshed trip 34 and published proposal version 3.
  Both dedicated walkthrough accounts accepted its exact flight/stay terms;
  the production UI showed “2 of 2 accepted” and “Agreed · everyone approved”.
  No booking or purchase occurred.
- Fresh flight-only trip 38 reached the daily Apify limit and did not complete.
  At the last usage check the UTC day had 40 Apify starts and 160 Claude calls.
  The frontend showed the search allowance limit; no cap increase was applied.
  This is not a claim that every fresh trip can complete within the remaining
  shared allowance.

The test also observed that a free-text ten-hour travel-duration preference did
not appear in the extracted intake summary. Confirm extracted constraints before
planning; that intake coverage issue is separate from the hosting migration.
