# Invitation-only access

The landing page and demo stay public. App and profile reads and human mutations require an active `app_access` grant in SpacetimeDB. The gate in the browser is only presentation; direct HTTP reducer calls and SQL views enforce the same restriction. Registered worker authorization is separate.

## Issue and revoke

Publish the updated module before deploying the frontend. Keep the existing database: the two new tables are additive. Do not use `--delete-data=always` on the shared database.

Using the SpacetimeDB CLI signed in as the database owner:

```sh
python3 scripts/app-invitations.py create --label "Traveller name" --out /private/tmp/tandem-invitation.txt
python3 scripts/app-invitations.py revoke --hash HASH_FROM_OUTPUT
```

The link defaults to a seven-day expiry (`--hours 1` through `168`). Send the private link to the intended traveller yourself. Do not commit it or place it in a public page. The database stores only the SHA-256 hash. The browser accepts the secret via a URL fragment, removes it from the address bar, and requires an explicit acceptance, so link preview fetches do not consume it. Redemption and granting are one transaction; a competing second identity cannot claim the same link. Same-identity retries are idempotent. Revocation hides existing data and blocks further human changes without deleting profiles or trips.

## Identity and recovery

This release builds on Tandem's signed SpacetimeDB browser identity token; it does not introduce email/password accounts. Access is remembered in the browser that accepts the invitation. Clearing site data or using another device creates a different identity: a new invitation grants access to that identity, **not recovery of the former identity's trips**. There is no account recovery or cross-device identity linking in this release. Existing browser identities retain their data, but need an invitation when the gate is deployed. The database owner's identity retains administrative access. Room join codes alone never grant app access.

The invitation expires if not redeemed; an accepted grant lasts until revoked. A new invitation can restore a revoked browser identity. Revoking its old invitation afterward does not revoke a newer grant. Server-side voice authorization also checks the grant before handling a Cartesia key. Private data never becomes public when access is denied.

## Verification

`spacetimedb/scripts/test.sh test_invitations.py test_profile.py test_appearance.py test_intake.py`

Tests hit reducers and views directly, including concurrent redemption, unauthorized issuance, revocation, private-table visibility, expiry bounds, and restoration without data loss. Other contract tests admit their test identities explicitly in the fixture; production remains closed by default.
