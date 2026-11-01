# Tandem product experience redesign

Reference: supplied “Tandem User Flows” document (October 31, 2026). Its anonymous-browser arrival and missing-intake labels describe an older revision. Keep the current invitation-only account system, cross-device identity, private intake, real event streams, and exact-version approvals.

## Design direction
Warm editorial travel workspace: cream paper, forest green, lavender companion moments, peach/mint/butter identities, Manrope 400–700 and IBM Plex Mono metadata. Keep the existing Pip artwork. Clear type hierarchy, generous reading space, 44px touch controls, visible keyboard focus, reduced-motion support. Avoid indefinite fake “thinking” animation.

## Journey and screen responsibilities
1. Landing → invitation/account sign-in. Explain private preferences and human approval. Existing marketing interactions remain explicitly illustrative.
2. Account creation/password management: two-column welcome and focused form; preserve invitation and cookie/session security.
3. Profile: companion alongside one question at a time; nine-step progress, save/skip/back, optional sections, summary request/correction/confirmation and saved-answer review.
4. Trips home: real trip cards, useful empty state, prominent create/join dialog. Prefill known display name; isolate form errors; preserve keyboard and focus.
5. Companion setup: real saved appearance, then enter room.
6. Room: compact trip context/navigation, phase indicator, private conversation, always-available provider activity, explicit action for unanswered private questions.
7. Agent discussion: a dedicated shared workspace; only actual participants, original source text retained, voice session stays mounted when changing tabs.
8. Plan: dedicated review view, decisions grouped by stage, shared candidate details, outbound AND return dates, correctly named shared stay, private allocation, anonymous approval counts, exact-version consent and withdrawal. Superseded plans retained as history, not actionable.
9. My limits: current confirmed private rules with original quote and strength; set aside/keep/restore generic rules through existing reducers; legacy chat-derived limits edited through chat. Recorded compromises only visible to their owner.
10. Connection, empty, pending, failed, stale and recovery states: never imply provider work from a spinner alone, clear private data on disconnect, preserve unsent drafts and error feedback.

## Validation
Typecheck/build; existing contract tests; browser tests for private data scoping, proposal approval versions, rule controls, active activity through tab switches, accessible create/join, profile forms. Desktop, short laptop, and mobile screenshots of real components using explicitly isolated fixtures; read-only live account checks where available. No synthetic activity in production.

## Verification completed — November 1, 2026
- Production Next.js build and TypeScript compilation passed.
- 103 unit/contract tests passed. Password-hashing tests were rerun sequentially after concurrent build CPU contention caused timeouts.
- 16 browser scenarios passed across planning, profile ranking, new product journeys, and voice privacy. Provider requests were intercepted in voice tests; no live speech quality claim is made.
- Read-only checks against the existing Alex/Jamie live walkthrough verified actual saved profile, shared messages, provider activity, round-trip flight dates, shared accommodation, and agreement rendering.
- Desktop 1440×1000, short laptop 1280×720, and mobile 390×844 checked. Mobile has no horizontal overflow; conversation and activity use bounded scrolling.
- No backend schema or worker changes. The homepage video remains the original product walkthrough and is labelled accordingly.
