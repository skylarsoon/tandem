# Admin accounts

`/admin` supports up to three active administrators. Each uses an email and password, independently of participant browser identities. The database owner provisions or resets an account; there is no public admin signup and admins cannot promote other admins.

Permissions:
- Create and revoke single-use app invitations.
- Suspend and restore existing participant access, preserving their trips.
- Read job counts and recent state/attempt/timestamp metadata, plus admin audit events.

There is no access to private conversations, profile answers, trip constraints, job payloads, provider keys, or worker controls. Job state is a database snapshot, not a worker uptime monitor. Lists show the latest 100 invitations and access grants, and 30 jobs/audit events.

## Open the app

After the mandatory password change, **Open app** takes an admin directly to `/app`. Visiting `/app` or `/profile` while signed in also automatically grants participant access. No invitation is created or consumed.

A same-origin, cookie-authenticated request creates a single-use, one-minute handoff. Redeeming it binds the signed browser transport identity to the account's stable traveller identity. The first link preserves the existing profile and trips in place. A later login on another device resolves to that same traveller; answers, companion appearance, room memberships, private conversations, and approvals share the same owner.

Existing admin-derived grants are reused when unambiguous. Multiple populated older profiles cause an explicit reconciliation error rather than an automatic merge. An older invitation-only browser identity without an account association must first be linked by signing in from that browser; an email address alone cannot prove ownership of anonymous data.

Each device link is bound to the admin session, not just an email. Sign-out revokes that session's participant access; password changes revoke other sessions; resets and disabled accounts revoke all sessions. Eight-hour expiry is scheduled in the database to retract idle subscriptions. Human writes additionally check the expiry timestamp. Admin roles remain separate from traveller permissions. Switching accounts in one browser never attaches the previous account's profile to the next account.

This version provides cross-device login for provisioned admin accounts. Invitation-only guests still have browser-based access; they do not yet have email/password registration or account recovery.

## Provisioning

Build and publish the additive database module with data deletion disabled first. Authenticate the CLI as the database owner. These commands save sensitive files with mode 0600 outside the repository; do not commit or log them.

```sh
python3 scripts/admin-gateway.py --out /private/tmp/tandem-admin-gateway.json
node scripts/admin-account.mjs --email person@example.com --out /private/tmp/tandem-admin-account.txt
```

Set the gateway JSON's `token` as the **server-only** Vercel environment variable `TANDEM_ADMIN_GATEWAY_TOKEN`. Set `TANDEM_APP_ORIGIN=https://tandem-calhacks.vercel.app` and retain the existing `NEXT_PUBLIC_STDB_URL` and `NEXT_PUBLIC_STDB_DB`. Redeploy the frontend. Never use a worker or database-owner credential as the gateway.

Give the account file privately to its owner. The temporary password must be changed at first sign-in. Use the account command again with a new output file to reset a forgotten password. `--disable` disables the account and invalidates its sessions. The account command does not send email.

## Security model

Node verifies passwords with scrypt (N=131072, r=8, p=1), a random 32-byte salt, and constant-time comparison. The database stores only salt and hash. Random 256-bit session tokens are kept in HttpOnly, Secure, SameSite=Strict cookies in production; only SHA-256 session digests are stored in the database. Sessions expire after eight hours. Password changes invalidate other sessions; owner resets/disabling invalidate all existing sessions.

Every administrative database procedure requires a separately registered gateway identity. Every privileged action also checks the session expiry, account enablement, account credential version, and first-login password change. No user-provided email, cookie claim, or participant token alone grants admin permissions. Mutations require the configured origin. Responses are never cached. No private record tables are public.

Login attempt limits persist in SpacetimeDB across server instances: 10 per account, 20 per client-address hash, and 120 globally per 15 minutes. Unknown accounts use the same generic error and password computation. Raw client IP addresses are not stored. These limits can temporarily prevent legitimate sign-in during sustained abuse. Admin writes are audited. No MFA or self-service email recovery is provided.

## Verification

- `spacetimedb/scripts/test.sh test_admin.py test_invitations.py`
- `cd web && npm test && npm run build`
- Exercise `/admin` against an isolated database: wrong password, mandatory reset, invitation creation/redemption/revocation, suspension/restoration, health privacy, sign-out, and a second session invalidated by password change.
