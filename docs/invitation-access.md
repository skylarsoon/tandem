# Invitation-only accounts

The landing page and demo stay public. Each single-use invitation creates one email/password account. The profile, companion, memberships, private conversations, and approvals belong to a stable account identity, so signing in on another device restores the same data. App reads and human mutations enforce access in SpacetimeDB, including direct calls; the browser gate is presentation. Workers retain separate authorization.

## Traveller steps

1. Open the private invitation link from an administrator.
2. Enter your email and a unique password of 14–128 characters, confirm it, and select **Create account**. This atomically consumes the invitation and creates the account.
3. Complete your profile, then open your trips. On any other device, visit `/account` and sign in with the same email and password. No new invitation is needed.
4. Use **Account → Change password** to change your password. Other sessions are signed out. **Sign out** revokes only the current session.

Existing guests who used Tandem before accounts must open `/account?register=1` **in their original browser**, where their old profile/trips are accessible. Registration upgrades that signed browser identity in place without a new invitation. Afterward they can sign in anywhere. An email alone cannot recover an anonymous browser's former data; if its browser identity was lost, contact the project owner rather than creating a new account and expecting an automatic merge.

## Issue, suspend, and restore

Admins create/revoke invitations and suspend/restore access through `/admin`. Database-owner CLI alternative:

```sh
python3 scripts/app-invitations.py create --label "Traveller name" --out /private/tmp/tandem-invitation.txt
python3 scripts/app-invitations.py revoke --hash HASH_FROM_OUTPUT
```

An unused invitation expires after 1–168 hours (default seven days). Send it privately. Its secret travels in a URL fragment, is removed from the address bar, and is stored only as a SHA-256 digest in the database. Preview fetches cannot redeem it. Account creation, invitation consumption, session creation, and device binding commit together. Competing redemptions cannot create two accounts. A lost-response retry is restricted to the original signed device.

An accepted account lasts until suspended/revoked. Revocation hides existing data and blocks writes and voice access without deleting trips. Administrators can restore the existing access grant; a new invitation cannot bypass suspension. Room join codes do not grant app access.

## Password recovery

There is no email sender, email verification, or self-service email reset in this version. Treat an email as a login identifier, not verified ownership. The project owner must verify the person independently before resetting an existing account:

```sh
node scripts/traveller-password.mjs --email person@example.com --out /private/tmp/tandem-password-reset.txt
```

The private mode-0600 file contains a temporary password. Deliver it securely, never commit it or log it. The reset invalidates all sessions and requires a new password at the next sign-in. It preserves the account's profile/trips and does not grant admin privileges. Admin resets use `scripts/admin-account.mjs` instead.

## Authentication and deployment

The server verifies scrypt passwords with random salts and constant-time comparison. Random session secrets live in HttpOnly, Secure, SameSite=Strict cookies; the database stores only their digests. Sessions last eight hours, with scheduled expiry retracting idle private subscriptions. Password changes invalidate other sessions; resets invalidate all. One-minute app handoffs bind transport identities to the authenticated account. Switching accounts does not transfer the previous user's data.

The existing server-only `TANDEM_ADMIN_GATEWAY_TOKEN` authenticates account procedures, which separately validate traveller sessions and cannot grant admin roles. Never expose this token to client code. Same-origin checks protect HTTP mutations. Persistent per-email, client-hash, and global rate limits run before expensive password work. No email, participant token, or user-supplied owner identifier alone grants account access.

Publish the additive module before deploying the frontend, with `--delete-data=never` on shared databases. Retain `TANDEM_APP_ORIGIN`, the server gateway token, and the public database URL/name. Existing anonymous guests retain access until they upgrade; new invitations require account registration.

## Verification

- `spacetimedb/scripts/test.sh` covers registration races, cross-device ownership, direct private reads, session revocation, account switching, admin isolation, and the existing app contracts.
- `cd web && npm test && npm run build` checks HTTP credential boundaries and the production build.
- Against a disposable local database, `npx playwright test e2e/invitations.spec.ts` exercises real signup, mobile sign-in, shared trip/profile changes, logout, password change, invitation reuse, and revocation.
