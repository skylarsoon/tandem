# Design: one generic language for limits and decisions

Status: proposal for Ronit to approve, then for Elena and Skylar to review. Date: 2026-10-24.

## Why

Tandem stores what people need as **named keys**, such as `max_stops = 1`, `own_bedroom = true` or `trip_budget_total = 80000`. It stores what the group is deciding as a **room setting**, such as `components: [FLIGHT, STAY]`.

Every part of the system then re-interprets those names in its own hand-written code:
- the database's publish checks (`proposals.ts`);
- the worker's solver and shortlist (`solver.py`, `shortlist.py`);
- explanations (`explain.py`);
- onboarding (`intake_policy.py`);
- Elena's adapters, which hard-code three keys and refuse the rest;
- the bridge.

Today 12 named limits are read by about 20 files across the database, the worker and the frontend. Adding one new kind of limit ("has a pool", "vegetarian", "lands before 6 pm"), or one new kind of decision (a dinner, an activity), means editing many of those files.

Elena's optimizer already speaks a universal language: *rules* (`attribute, operator, value, hard/soft, owner`) and *decisions* (choose one candidate from each group). The gap between our named keys and that language is the only reason translators exist. **This design closes the gap at the source.** Limits and decisions are stored in the universal language from the moment they're captured, so nothing downstream needs to know what a "flight" or "max stops" is.

## Goals

1. A new kind of limit needs **no code**. It works as soon as candidates carry the attribute it refers to.
2. A new kind of decision needs **no code**. It's a decision definition the organizer's brief proposes and the organizer confirms.
3. **One meaning, enforced everywhere.** The database's publish check, the worker's filtering and the optimizer all evaluate the same rule the same way.
4. **Explanations come from the rule itself and the person's own words** ("your 'no layovers' ruled out 6 flights"), never from per-limit text.
5. **Privacy and consent are unchanged.** Rules are private to their owner, proposed by agents with the person's quote, and confirmed by the person.

## Non-goals

- Changing Elena's optimizer. It's used unchanged.
- Making search providers generic. Each Apify actor stays an adapter that turns a search into candidates. That's a data source, not a translation of limits.
- Building weighted fairness now. Soft rules carry an `importance` field from day one, but onboarding asks for it later.

## The language

There are four concepts, and every current limit and decision is expressible in them (see "Every current limit, expressed" below).

### 1. Candidate attributes (the shared vocabulary)

Every candidate already carries facts: `price_cents`, `arrival_epoch`, `arrival_local`, `stops`, `baggage_included`, `capacity`, `bedrooms`, `rating`, and so on. An **attribute catalog** (`contracts/attributes.json`) lists each category's attributes with their type and unit:

```json
{ "FLIGHT": { "stops": {"type": "int"}, "arrival_local": {"type": "time"}, "price_cents": {"type": "money"} },
  "STAY":   { "bedrooms": {"type": "int"}, "rating": {"type": "number", "range": [0, 5]} } }
```

The search normalizers already produce these facts, so the catalog is generated from what they emit, not hand-written per limit. It's the one shared vocabulary, and it's data.

### 2. Rules: what a person needs

```json
{ "id": "r-…", "owner": "<member>", "category": "FLIGHT", "attribute": "stops", "operator": "lte", "value": 0,
  "kind": "hard", "importance": null, "source_quote": "no layovers please", "rationale_private": "…" }
```

- **Operators:** `eq, neq, lt, lte, gt, gte, in, not_in, between`. `between` covers time windows that may wrap midnight.
- **`kind` and `importance`:**
  - `hard` excludes candidates;
  - `soft` (importance 1–5) only scores them;
  - `tolerance` gives a soft rule partial credit, as in Elena's scorer.
- **Scope is implied by the decision.** A rule on a category the person picks for themselves (their FLIGHT) applies to that person's choice. A rule on a shared choice (the STAY) applies to the group's choice.
- **Trip-wide rules** use an aggregate instead of a candidate attribute: `{"aggregate": "my_cost", "operator": "lte", "value": 80000}`. Here `my_cost` is the sum of what this person pays across every decision (see "Who pays" in §3).
- **Every rule cites its source:** the message it came from and the person's exact words, checked by the database as today.
- **A rule on an attribute no candidate carries can't be enforced.** It's stored as a private note, and the agent says honestly that it can't check that yet. That replaces `trip_note`.

### 3. Decisions: what the group is choosing

```json
{ "decisions": [
    { "id": "flights",  "category": "FLIGHT",   "chooses": "one_per_person", "pays": "each" },
    { "id": "stay",     "category": "STAY",     "chooses": "one_for_group",  "pays": "split" },
    { "id": "transfer", "category": "TRANSFER", "chooses": "one_for_group",  "pays": "split" } ],
  "group_rules": [ { "spread": "arrival_epoch", "across": "flights", "operator": "lte", "value": 7200 } ] }
```

- **`chooses`:** `one_per_person` or `one_for_group`.
- **`pays` (who pays):** `each` (your own pick), `split` (shared equally) or `per_person` (an activity's per-head price).
- **`group_rules`:** limits on the group's combined choice, such as arrival spread.
- **Where it comes from:** the organizer's trip brief proposes this, and confirming the brief stores it. "Plan a dinner too" is one more entry, not new code. It replaces `components`, `arrival_overlap_seconds`, and the scopes and decision ids from the decisions work.

### 4. Capacity needs: shared choices that must fit everyone

Some rules aren't about one person's pick. They're about whether a shared pick fits everyone:
- "Seats for all of us": each person needs 1 seat.
- "Own room": I need 1 bedroom alone; others need 1/`people_per_bedroom` each.

These are stored as a person's **need**: `{"owner", "category": "STAY", "attribute": "bedrooms", "needs": 1}`. The compiler turns the sum of everyone's needs into an ordinary rule on the shared choice (`bedrooms ≥ 3`). So the optimizer still sees only plain rules, and it needs no change.

### Search inputs (not rules)

Where you fly from, and the trip's place and dates, decide **what to search for**, not which results to keep. They're stored as **search facts**: `{"owner", "origin": "SFO"}` and the brief's `destination` and dates.

Research may also **push rules down** into an actor's own filters as a speed-up, for example `stops ≤ 0` becomes memo23's `maxStops: 0`. That uses each actor's list of the filters it supports (data). Results are filtered by the rules again regardless.

## Every current limit, expressed

| Today | In the language |
|---|---|
| `flight_budget_total` | rule `FLIGHT.price_cents lte X` (hard, or soft with tolerance up to the authorized range) |
| `trip_budget_total` | rule `aggregate my_cost lte X` |
| `no_budget_limit` | no money rule, plus the money topic marked answered (an onboarding coverage fact, not a rule) |
| `baggage_required` | rule `FLIGHT.baggage_included eq true` (unknown stays "needs evidence", as today) |
| `arrival_window` | rule `FLIGHT.arrival_local between [earliest, latest]` |
| `max_stops` | rule `FLIGHT.stops lte N` |
| `own_bedroom` | need `STAY.bedrooms: 1` |
| `stay_min_rating` | rule `STAY.rating gte X` |
| `origin_airport` | search fact `origin` |
| `dates_ok` | onboarding coverage fact |
| `trip_note` | a rule on an attribute nobody can check yet, kept as a private note |
| `kept_<key>` / "set aside for this trip" | the rule's state: `active` / `set_aside` / `kept`, so it's asked once |
| room `components`, `arrival_overlap_seconds` | the decisions and group rules |

## What changes, by part

**Database module (Ronit)**
- New tables:
  - `participant_rule`: rows shaped like §2, private;
  - `participant_need`;
  - `search_fact`;
  - `trip_decisions` on the brief.
  These are additive; existing tables stay.
- `set_constraint` stays as a compatibility entry point: it converts the 12 old keys into rules with a fixed table, so Skylar's current form flow keeps working. That table is the only key-specific code left, and it only serves old clients.
- **Publish checks become one evaluator.** Load the people's rules, needs and the decisions, then evaluate the chosen candidates' facts. It's a TypeScript mirror of Elena's `constraint_evaluator`, with the same operators and the same unknown-means-not-OK rule. It replaces the hand-written per-scope checks in `proposals.ts`. The commitment and decision rules from the decisions work stay; they're about state, not meaning.
- Views: `my_rules`, plus `room_decisions` (members).

**Onboarding (worker, Ronit)**
- **Extraction produces rules instead of keys.** Claude gets the attribute catalog and the person's message, and proposes rules with the quote. The existing deterministic checks still apply, and gain one more:
  - the quote must appear in the message;
  - numbers must appear in the quote;
  - the attribute must exist in the catalog.
- Coverage topics (origin, dates, money, settings) stay as onboarding's own concern. "Money covered" means a money rule (either one) or an explicit "no limit".
- **Just-in-time nudges come from the rule language.** "This plan needs everyone to share rooms; OK?" is generated from the need that blocks it.

**Planning (worker)**
- **One generic compiler** turns `(decisions, group rules, rules, needs, candidates)` into Elena's request. It contains no flight, stay or trip words. This replaces both the bridge and `Brief`.
- **The shortlist** evaluates each person's hard rules with the same evaluator. Its diversity and anchor slots read the group rule (spread over `arrival_epoch`) instead of knowing about flights.
- **Explanations** quote the rule and the person's words. Counts ("ruled out 6 of 20") come from the evaluator's per-rule results, which is also what the transparent optimizer testing shows.
- **Fairness: `TripSolver` is retired.** The generic compiler plus Elena's optimizer replace it once the transparent tests agree. It stays available as a reference during the switch.

**Elena**
- No optimizer change.
- `adapter.py` and `multi_adapter.py` become unnecessary for Tandem. Her own tests keep them.
- She reviews the compiler's output on real rooms.

**Skylar**
- The form flow keeps working through `set_constraint`.
- A future form can write rules directly: pick an attribute from the catalog, an operator and a value.
- The frontend bindings get regenerated.

## Order of work

1. **Language and evaluator.** The attribute catalog, the rule/need/decision contracts, and one evaluator in Python and TypeScript tested against the same cases (a shared JSON test file), so both sides provably agree.
2. **Database.** New tables and reducers, the legacy-key conversion, and publish checks through the evaluator. All 85 module tests and the decision tests must still pass.
3. **Worker planning.** The generic compiler, with the shortlist and explanations on rules. Every current scenario (friction suite, onboarding runs, integration) must give the same outcomes as today, and any deliberate difference is listed.
4. **Onboarding.** Extraction to rules; nudges and set-aside on rules; a re-run of the live persona simulation.
5. **Transparent optimizer testing.** Each scenario prints its rules, which candidates each rule removed, the combinations examined, and the winner, all straight from the evaluator.

Each step is merged only when the existing suites are green.

## Risks and how they're handled

- **Claude picks the wrong attribute.** The catalog is small, the quote and number checks still apply, and the person confirms every rule in their summary. An unknown attribute becomes a private note, never a silent rule.
- **The two evaluators drift apart.** They share one JSON test file that both suites must pass.
- **Old rooms on Maincloud.** The new tables are additive. Old rooms keep their keys, which convert on the next change.
- **Scope creep.** This doesn't make search providers or the UI generic. It does that for limits and decisions only.

## Decisions to confirm

1. Retire `TripSolver` once the transparent tests agree (rather than keeping two choosers).
2. Keep `set_constraint` with a fixed old-key conversion for Skylar's form flow until he moves to rules.
3. Weighted fairness stays off until onboarding asks for importance (as decided today).
