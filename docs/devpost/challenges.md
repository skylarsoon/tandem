# Challenges we ran into

Running log for the Devpost submission. Newest entries at the bottom of each section.

## Designing agents that actually talk

**Our agents were "negotiating" by filling in forms.** The first protocol had agents exchanging typed commands (`OBJECT`, `COUNTERPROPOSE`) with no language, which made "agents negotiating" look like decoration. We redesigned every message to pair a typed act (what it does) with a natural-language utterance (what the agent says), threaded so agents can reply to each other directly. The typed act stays authoritative: text can never create an option, a concession or an approval.

**Making agents speak without leaking secrets.** If an agent writes its group message with its owner's private budget and reasons in context, a model can leak them ("Alice can't go over $500"). We split each agent into two separate model calls: a private *decision* context that sees the owner's limits and reasons, and a *room voice* context that only sees facts the group is allowed to know. The voice can't leak what it never saw.

**Our "behavioral" pitch had no implementation.** Onboarding asked people how they handle disagreement and saying no, but nothing downstream used the answers: two people with the same budget got identical agents. We turned the answers into explicit, user-confirmed settings (*speak up for me*, *ask me first*, *give me time*) that change what the agent is allowed to do, without the app ever guessing someone's personality.

## Making SpacetimeDB the coordination layer

**Reducers can't return values.** Our worker needed to claim a job and get back a lease, and publish a plan and get back its id. Reducers only report success or failure, so we moved those calls to procedures. Then we found that throwing an error inside a procedure crashes it with an HTTP 500, so every procedure returns a JSON result with an error code instead of throwing.

**There's no Python SDK.** The planning worker is Python, so it talks to SpacetimeDB over raw HTTP. A throwaway spike against a local server mapped out the encoding before we committed to a contract: top-level argument names are camelCase while nested fields and query results are snake_case, identities need a special `{"__identity__": ...}` wrapper, and optional values need `{"some": ...}`. We hid all of it behind a small client so the rest of the team never has to think about it.

**Module HTTP endpoints don't know who's calling.** SpacetimeDB modules can expose custom HTTP routes, which looked like a cleaner API for the worker, until our spike showed the caller identity is always empty there. We kept all worker traffic on authenticated reducer and procedure calls.

**Privacy has to live in the database, not the UI.** SpacetimeDB views must be public, so "private" means each view filters by who is asking. Views can also only read through indexes, so every private table needed the right indexes. We wrote tests where one person tries to read another's budget, private chat or approval card through every path. The database returns nothing.

**Event tables would have leaked activity across rooms.** We wanted live "agent is thinking…" animations and reached for SpacetimeDB's event tables, but they're public and views can't filter them, so anyone could watch every room's agents. We used a small status table behind a per-room view instead: the same instant updates without the leak.

**A crashed worker left avatars "thinking" forever.** If the worker died mid-negotiation, its agents' last status stuck around. The view now derives activity from the job itself: once the job stops running, every agent shows idle automatically.

## Correctness under crashes and retries

**"Exactly once" isn't possible across external APIs.** A worker can crash after Claude or Apify accepted a request but before we recorded it. We gave every effect a stable request key with a stored receipt, so a retry returns the original result instead of repeating it, plus a durable ledger for model and search calls with budgets that survive crashes. We document the one window we can't close (a crash before the provider's run ID is saved) instead of pretending it doesn't exist.

**A replacement worker was rejected for doing the right thing.** When a crashed worker's job was picked up by a new attempt, replaying the same logical message failed as a "different request", because our duplicate check included the attempt number. The attempt is a fence, not part of what the operation *is*, so we excluded it from the fingerprint and let any registered worker replay a worker's receipt.

**People answer faster than the worker can pause.** An owner can answer a question in the gap between the agent asking it and the worker parking the job, which would have left the job waiting forever. Parking checks, in the same transaction, whether the answers are already in, and if so the job goes straight back to the queue.

## Integrating two halves built in parallel

**The worker and the backend were built against different assumptions.** Skylar built the worker against his own in-memory fake while we built the real SpacetimeDB module. Each passed its own tests, but they had never talked. They disagreed on:
- ID formats
- what a "yes" to a permission question does (continue the current plan vs. start a fresh one)
- whether reminders are opt-in
- whether the backend re-checks a plan before publishing

We wrote an adapter, aligned the rules (picking the stricter option each time), and ran the worker's own contract tests against the real database.

**Python and JavaScript disagree about JSON.** The worker fingerprints each message's content in Python and the backend verifies it in TypeScript. Python escapes non-ASCII characters by default, JavaScript doesn't, so any message with an accent, em dash or emoji would fail verification. We wrote a byte-exact canonical JSON encoder in the module and test it with deliberately awkward text: `Café option — lands 13:05 ✈️ "quoted" \ back\slash`.

**Two fingerprints for the same plan.** After connecting everything, every publish failed with "invalid validation report": the validator stamped its report with a fingerprint of the full planning snapshot, while our adapter fingerprinted only the revision numbers. We made the adapter use exactly the validator's fingerprint, and the end-to-end runs went green.

**Duplicate-request keys collided across rooms.** Our receipts are global across the database, while the worker's test double kept them per room, so tests reusing a key like `"x"` in different rooms collided. Production uses random keys, but we scoped claim keys by room in the adapter so both behave the same.

**Spec drift between documents.** Our PRD and architecture evolved separately and started contradicting each other (when to show who hasn't approved, what happens to earlier agreements when plans change). We made the architecture the source of truth for building, listed every conflict explicitly, and settled them as a team instead of letting each person pick silently.

**Live flight prices weren't complete enough to approve.** When we connected the real Google Flights scraper, every result came back as an observed fare without mandatory fees, so the honest answer was "we can't put this in front of people as an approvable total." Instead of quietly treating missing fees as zero, both the worker's validator and the database's publish check reject incomplete prices, and a live run ended as *exploratory only*, not as a fake agreement.

The fix came from reading the fine print. Google states its fares already include VAT/GST and airport taxes; only extras like bags and card fees are excluded. The same scraper can return each seller's own price and bag fees. We now:
- treat the **airline's own booking price** as the complete total
- add a "with one checked bag" variant priced from the airline's listed bag fee
- have the solver skip fares known to lack a bag for anyone who needs one
- keep agency-only prices marked incomplete, and list card fees as a disclosed limitation

**Our tests broke the moment we logged in to the cloud.** SpacetimeDB remembers which identity owns each database *name*, even across restarts of a throwaway in-memory server. After we logged in to Maincloud with a new identity, our test scripts were refused permission to republish their old test databases. Each test run now publishes to a uniquely named database, which also makes runs fully isolated.

**The scraper's documentation and its real output disagreed.** The docs describe booking options as `booking-option-{n}-…`, and we assumed numbering started at 1. One real run showed it starts at 0, and option 0 is usually the airline's cheapest basic fare. We'd been skipping exactly the fare people want (United Basic Economy $73, shown instead as $118), and reading a phantom empty option at the end. We saved those real rows as a test fixture, so the parser is now checked against what the actor actually returns.

**Real fares broke our made-up budgets.** Our first four-city Hawaii run ended in "no feasible plan". It was right: the cheapest real SFO→Honolulu fare was $292 against a $250 cap, and Seattle's cheapest was $432 against $350. The system searched again, still found nothing, and said so instead of quietly stretching a limit. With realistic budgets, it found the only plan where everyone lands within two hours, which required the New Yorker to take a $629 flight instead of a cheaper one landing at midnight.

**Live latency and output length.** Asking the scraper for each seller's prices roughly doubled search time, so four travellers' searches no longer fit the two-minute research deadline, and we raised it. Claude's structured decision for a four-person trip also needed more than our 512-token output cap. The worker rejected the cut-off answer rather than guessing, which is what we want, but it ended the run until we raised the limit.

## Fairness and social friction

**The cheapest plan dumped the whole compromise on one person.** Minimizing total cost happily picked a plan where three friends flew cheap and the fourth paid $90 extra so everyone could land together. We changed the policy to *minimax*: first make the largest extra cost anyone carries as small as possible, then minimize the total. Now a $50-each compromise for two people beats $90 for one, and each person privately sees exactly what they pay over their own cheapest option and why.

**"No" had to be information, not a dead end.** When an agent needed someone to pay more to land with the group, their *Ask me first* setting meant asking them. But a "no" just ended the run. Now a declined concession removes that fare and re-solves, so the trade-off can move to the others: in our test, three friends each take a later flight for $150 more instead of one person paying $125. If nothing works without that fare, the system says how far apart the closest arrivals are.

**Explaining "no plan" without outing anyone.** "No plan fits" helps nobody, but "Alice can't afford it" is worse. Every failure now produces a private note for each blocked person, with their own numbers ("the cheapest fare found for you is $290, above your $250 limit"), and a neutral room event that counts causes without names or amounts ("1 traveller: every fare found is above a private limit").

**Whose requirement is it?** With shared stays, one person's request (their own bedroom, a minimum rating) can push someone else over budget. The solver now checks whether dropping one requirement would bring every over-limit trip back within limits. If so, only the people who set that requirement are asked privately whether to set it aside for this trip, and the person over budget is told the requirement is being discussed. In the test, two own-room requests made the only 3-bedroom stay put a friend $80 over their limit. Both agreed to share, and the plan fit everyone.

**Shared costs aren't whole cents.** Splitting a $1,320 stay and transfer four ways is fine, but $1,321 isn't, and a CP-SAT model needs integers. We work in party-size-scaled cents (4 × own flight + shared total), which is exact and preserves every comparison, and round only for display.

**Our own privacy test caught a leak.** Each traveller's private "you pay $125 more" impact was passed to their agent through the agent's task. LangGraph subgraphs inherit the parent's checkpointer, so that task, including the private numbers, was being written to the checkpoint database. A test that scans every checkpoint for private markers failed. We now pass private data to the agent through a closure, which is never checkpointed.

## Planning the whole trip, not just flights

**Only some prices can be approved.** We evaluated scrapers for stays, transfers, car rentals, activities and restaurants against one rule: can people approve this exact price? Airbnb returns a total including taxes. We accept it only when base plus taxes equals the total to the cent, in every row. Airport transfers are fixed per vehicle. Car rentals didn't say whether taxes were included, so we left them out. Activity "from" prices and restaurant price bands go into the plan as clearly labelled ideas, never as terms people approve.

**Sequential scraping didn't scale.** Each search waited for its run to finish before starting the next, so four flights plus four trip searches would take minutes. The gateway now starts every run first, then polls them together, with the same crash-safe ledger, spend budget and deadline.

**Starting everything at once hit the account's limit.** Our first full-trip live run started four flight searches and a stay search in parallel, then the transfer search was refused: Apify allows five concurrent runs per account. Worse, our crash-safety rule treated the refused start as *ambiguous* ("maybe it ran and we lost the reply"), which blocks retries so we never pay twice. A refusal is different: the API answered, so nothing ran. The gateway now starts runs only while there's a free slot (four, leaving one for teammates), starts the next as each finishes, and records a definite refusal as "not started" so it can be retried, returning its reserved budget.

**Reasoning ate the answer.** Our agents' structured decisions are tiny JSON objects, yet live runs kept failing as "truncated". The model's hidden reasoning counts against the output limit, and a four-traveller trip with a shared stay sometimes needed over 1,500 tokens of it before writing a 170-character answer. We raised the budget to 4,096 and documented that it includes reasoning.

**A vague objection burned the whole research budget.** In one live run, an agent kept answering "could you check an alternative?" without saying what was wrong. The coordinator dutifully re-searched until it ran out of rounds and ended with "round budget exhausted": no plan, no explanation. Now a concern that no search can fix (anything other than an over-limit price or a missing bag) becomes one specific private question to that agent's owner. If they say yes, the plan goes ahead; if they say no, it stops with their decision.

**Live data takes different paths.** Two runs with the same config went different ways. In one, Airbnb's top results had no three-bedroom place, so Alice was asked privately whether she'd share. In the other, a three-bedroom place appeared and nobody was asked. Our tests cover each branch with fixtures, so the live behaviour isn't luck.

## Onboarding without a form

**Forms feel like paperwork; chat invites guessing.** We wanted onboarding to feel like talking to someone who's paying attention: one trip-aware opener ("Honolulu, Feb 12–16 with Alice, Bob and Carol. Where would you fly from, and is there anything that's a must-have or a deal-breaker?"), then only what's still missing. But a chatty agent will happily turn "I'm flexible" into a rule. So everything the agent records must quote the person's exact words, and the database checks the quote against their message. A number has to appear in what they said. A budget without "for everything" or "for the flight" becomes a question, not a guess. Nothing becomes a limit until they confirm it.

**Ask now, or ask later?** Asking about rooming, bags and landing times up front made onboarding long. Never asking meant silently assuming. We split it: onboarding covers only what planning can't start without (where you fly from, money, how your agent should act). The rest is asked *just in time*, when a real plan depends on it ("The best-value place has 3 bedrooms for 6 of you. OK to share a room?"). The answer is recorded as the person's own limit.

**Our first live simulation caught four real bugs.** We had Claude play six people with private backstories (a nurse off night shifts, a friend who just got laid off and says "whatever works", someone with a CPAP who won't say why they need their own room) against the real worker:
- **Drifting questions.** The agent's own questions wandered into small talk about beach vibes and used every question allowed. Questions now use our wording for exactly the open essentials, which also halved the number of model calls.
- **A dropped budget.** "Around $900, could stretch to $1,000" was thrown away because the quoted span didn't contain the $1,000. The person repeated it four times in a loop.
- **A preference recorded as a rule.** "A few bedrooms would be nice, but I'm flexible" became "needs own room".
- **Internal notes sent to people.** The agent once sent its own note ("Maya did not say whether the dates work; ask her") to Maya herself.

Each is now a regression test. The rerun took 155 seconds instead of about 9 minutes: 2–4 questions per person, nothing assumed, nothing leaked.

**A permission that expired too soon.** When someone agreed to share a room so a friend's budget would work, we stored it as a grant tied to the current planning revision. The next person's answer bumped the revision, the grant silently expired, and she was asked again. Setting a requirement aside is really a change to your own requirements, so it's now recorded as your constraint ("kept" if you say no), and you're asked once per trip.

**One answer cancelled everyone else's questions.** Just-in-time answers restarted planning, which cancelled the other open questions, so five people were asked in five rounds. Answers to the waiting job's own questions are now recorded without a restart. The job resumes once everyone has answered and re-plans with all of it.

**Real names broke agent replies.** With test names like `p1`, a thread's participant list happened to be in sorted order. With "dana" and "jordan", which sort before "mediator", it wasn't, so every reply was rejected. Participant lists are now compared as sets.

**Fairness that remembers, without trusting the optimizer.** The optimizer wanted a running total of what each person has given up across decisions, so whoever compromised on flights gets priority on the stay. The risk was counting a burden that nobody actually approved, or counting it twice. So the optimizer doesn't write the ledger. It hands its scores to the database along with the plan, and the database moves them into the ledger itself, in the same transaction where the last person's "yes" makes the plan agreed.
- A retry can't count twice.
- A withdrawn "yes" takes the burden back out.
- A new version of a decision replaces the old one's burden instead of adding to it.

Burdens are stored as millionths, not floats, so a person's total stays exact however many decisions it adds up.

**One agreement per stage wasn't enough.** Publishing a plan replaced every earlier plan in the same stage, so agreeing on hiking would have silently cancelled the snorkeling everyone had already approved. Each proposal is now a version of one named decision, and only versions of the same decision replace each other. Agreed decisions become commitments, and the database checks every new proposal against them:
- a stay that fits a budget on its own but breaks it on top of the agreed flights is refused as a conflict, not as a bad plan;
- so is an activity that overlaps one already agreed.

Agreeing one part also queues planning of the next.

**Too many flights to try every combination.** The fairness optimizer tries every combination of one flight per traveller: five people with twenty flights each is 3.2 million plans. So a shortlist cuts each person to five meaningfully different flights:
- the cheapest;
- the one that lands closest to everyone else's;
- the most distinct alternatives by time and price.

The risk was a heuristic quietly turning into a verdict. "No plan among these five" isn't "no plan exists", so an infeasible shortlist is redone with flights it hadn't tried before Tandem searches again, and the "no plan" message counts how many options it checked. Someone with no usable flight at all is told why, privately, instead of being planned around.

**Every new limit meant code in six places.** "Max one stop" was a named key that the database, the solver, the shortlist, the explanations, onboarding and the optimizer's adapter each interpreted in their own code. Now a limit is data: a category, an attribute, an operator and a value, stored with the person's own words. The database and the worker each have one evaluator, and both must give identical answers on a shared test file. A test fails if the compiler that feeds the optimizer ever names a category, and on its first run it caught two room settings we'd hard-coded.

**Cheapest isn't fair.** Making the optimizer's steps visible showed something the tests had missed. With fairness switched off, it saved the group $10 by making one person pay $90 extra, where the old solver spreads $50 across two people.

Our first idea for measuring compromise (extra cost as a share of your budget) wouldn't have fixed it: $90 against a $500 budget is too small to outweigh the price. What works is measuring the extra against **your own cheapest option**. c paying $190 instead of $100 is a 0.9 burden, and a and b paying $150 instead of $100 is 0.5 each. With that, the optimizer's own formula (5 × the worst, 3 × the total, then cost) shares the compromise. It now agrees with the old solver on every scenario, so it became the default, and each plan's burdens go to the fairness ledger once everyone agrees.

**Adding an argument broke every caller at once.** SpacetimeDB rejects a call that is missing an argument ("missing reducer argument `burdens`"), so adding one to `publish_proposal` broke the simulator, the worker and the tests together. Every caller is now updated in the same commit, and the module and the worker have to be deployed together.

**Preferences were quietly turning into rules.** Preparing an end-to-end test with four personas, we followed each sentence through the pipeline and found three ways a preference got bent:
- Bella's "around $800 for flights, but I'd pay more for comfort" became "never above $800";
- Sam's "nonstop if possible" became a search filter, so connecting flights were never even searched;
- "a bag would be nice" was thrown away for not being firm enough.

Now only firm statements become limits. A hedged one is kept as a preference: it's shown back as "If possible: …" and earns partial credit up to the top the person named (or twice the target if they named none). It never filters a search.

**A worker that could make four calls, ever.** A long-running worker shared one budget counter that never reset, with a default of four Claude calls and one search. A real room's onboarding ran out partway through. The service now has a daily ceiling, and each job gets its own limit.

**The scraper's stop count.** A single-leg San Francisco to Honolulu flight came back as "1 stop". The rows also list every flight leg, so stops are now counted from the legs, and the stated number is only a fallback.

**Options that silently disappear.** Pinning the optimizer's behaviour in a test showed that an option missing a fact a preference needs (say, no stop count) is dropped from the ranking, even if it's the cheapest. If every option lacks the fact, nothing gets planned at all. So preferences will reach the optimizer as precomputed scores where unknown counts as unmet, never as missing.

## Honest limits

**The backend can't judge wording.** A database can check that a message cites real facts, comes from someone allowed to speak, and doesn't quote a private reason word for word. It can't tell whether "Alice needs money for her apartment" is a reworded leak. We kept semantic checks in the worker (a constrained voice and validated sentences), made the backend enforce everything mechanical, and wrote down which layer is responsible for what.
