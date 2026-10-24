# SpacetimeDB inspector

A local, read-only window into the `tandem-dev` database, as its owner. All 32
tables are private; browsers normally only see them through per-person views.
This shows what's actually stored.

```bash
cd stdb-inspector
npm install
npm run inspect        # http://127.0.0.1:4100 (bound to localhost only)
```

**Owner token.** Read on the server from `~/.config/spacetime/cli.toml` (your
`spacetime login`), or from `STDB_OWNER_TOKEN`. It never reaches the browser:
the page only calls this app's `/api` routes. Don't deploy this app anywhere.
It's an owner-only debugging tool.

**Settings:** `STDB_URL` (default `https://maincloud.spacetimedb.com`) and `STDB_DB` (default `tandem-dev`).

**Three modes:**
- **Learn**
  - **Replay this room, call by call:** scrub through every reducer call that shaped a room. Every write in one call shares a timestamp, so calls are rebuilt from the rows. Each step shows which reducer most likely ran and who called it, every row it created or changed, and the SpacetimeDB idea at work (transactions, revisions, leases, views).
  - **Map of the module:** reducers grouped by who calls them, the tables, and the views; hover to see what touches what. Which tables each reducer and view touches is *derived from `spacetimedb/src`*, so it stays accurate as the module changes.
  - **Explain panel:** click any underlined name (table, reducer, view or concept) for what it is, who writes it, which view exposes it to whom, and the code.
- **Sandbox:** a real room on the shared database with three simulated people (ava, ben, cleo).
  - **Actions:** buttons call real reducers as them: join, confirm settings, set a budget, describe the trip, talk to their agent, confirm, answer questions.
  - **After each action:** you see exactly which rows that one transaction wrote, and what each person's browser actually receives, because their real views are queried with their own token.
  - **Privacy test:** "ben tries to read the raw limits table" shows SpacetimeDB refusing.
  - **AI worker:** "run the AI worker once" runs one sweep of the real worker for the room (onboarding only: Claude calls, no paid searches).
  - **Tokens:** the sandbox people's tokens stay in `.sandbox/` (git-ignored).
- **Inspect:** the raw views below.

**Inspect views:**
- **Story:** every row tied to the room, in time order, with the table it lives in and who can read it: everyone in the room, one person, or only the worker and owner.
- **People:** each traveller's settings, onboarding, confirmed private limits and private chat.
- **Jobs:** the work queue, with leases, attempts, revisions and outcomes.
- **Agent chat:** what the agents said, and the facts they cited.
- **Search results:** every flight, stay, transfer and idea found, and whether its price is complete.
- **Plans & events:** published plans, private impacts and room events.
- **Raw tables:** any table, column by column. The table list is read from the module schema, so new tables appear automatically.
- **"Looking at":** switch to *What X's browser can read* to check privacy. It applies the same rules as the module's views.

**How it reads:** SQL over HTTP. Each room snapshot is cached and refreshed in
the background, because Maincloud takes 0.5–5 s per query. Large stored model
outputs (`operation_record.result_json`) are left out of snapshots; the raw
table endpoint `/api/tables/<name>` still returns them.
