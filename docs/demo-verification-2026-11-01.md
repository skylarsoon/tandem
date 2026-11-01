# Production trip walkthrough — 1 November 2026

Published demo: `/media/tandem-demo.mp4` (5:26, 1440 × 900, H.264/AAC).

## Observed outcome

Two dedicated demonstration accounts completed production trip 36, “San Diego, every step together,” for 20–22 November 2026. Proposal IDs 11, 12, 13, and 14 are AGREED, each with two accepted reviewers. Final PLAN job 4454 finished with ALL_DECIDED.

- Flights: Alex SFO→SAN, $141 round trip, nonstop; Jamie PDX→SAN, $269 round trip, one connection each way. Recorded prices include taxes; no checked bags.
- Stay: “Private, beachy, cozy, welcome to California!”, $246.92 total shared accommodation.
- Activity: San Diego Harbor Cruise, $37 per person planning estimate.
- Dinner: El Pueblo Mexican Food & Bar - Carmel Valley, $15 per person planning estimate.

Nothing was purchased or reserved. Source prices were observed during the run and are not a future availability guarantee.

## Recording provenance

Actual browser interactions and production provider/worker events. No injected messages, results, or approvals. The video includes onboarding, creating and joining the trip, confirming inputs, search criteria/results, owner permission questions, group agent evaluation, a grounded private cost answer during dinner planning, and separate approvals for every stage.

Waiting and troubleshooting pauses were cut. Navigation was sped up and some approvals slowed for readability. A live invitation code was concealed. Local macOS Samantha narration was added with English captions and a transcript. This take does not contain captured Cartesia agent voices and does not independently verify live STT/TTS.

The run was not an uninterrupted success: it exposed stale evidence, later-stage research selection, and final completion-label issues. Fixes were applied and the same trip resumed. Accepted provider runs and prior decisions were preserved. One organizer-authorized retry used the real retry_planning API after an old WAITING_FOR_INTAKE outcome. No database results or approvals were manufactured.

## Validation and operations

Worker planning-answer and workflow suites: 29 tests passed before the final answer refinement; 7 planning-answer tests and 21 conversation/intake tests passed after that refinement. Backend retry/privacy suite: 10 tests passed, plus backend type checking. Frontend completion/activity/presentation/workflow: 24 tests passed, plus TypeScript checking and production build.

Completion derives from every catalog-defined requested decision ID, including repeated activities, and excludes superseded or pending decisions. The final live UI displayed “All trip decisions agreed.”

Usage observed at completion: 49 Apify starts and 238 Claude calls for the UTC day. Approved temporary ceiling: 52 Apify starts until midnight UTC, then the default 40; Claude stays at 500 and each actor retains its $1 maximum. The worker runs on the owner's Mac and still requires it to be awake and connected.
