# Backend action items for conversational planning

The frontend now has a trip overview and a shared/private conversation timeline.
It uses existing SpacetimeDB views/reducers. No production backend or worker
changes are part of this revamp. The worker modifications already present in the
working tree before this revamp have not been changed further.

## Current frontend contract

| User action | Existing API |
| --- | --- |
| Organizer's initial trip idea | `set_trip_description(roomId, description)` |
| All later chat messages, including corrections | `submit_private_message(roomId, body)` |
| Confirm proposed shared brief | `confirm_trip_brief(roomId, revision)` |
| Confirm departure airport when proposed | `confirm_origin(roomId, patchId)` |
| Confirm other standalone proposed facts | `confirm_constraint_patch(patchId)` |
| Dismiss a suggestion | `reject_constraint_patch(patchId)` |
| Confirm onboarding details and participation settings | `confirm_intake(roomId, acceptedPatchIds, participation flags)` |
| Answer an actionable question | `answer_question(questionId, version, answerJson)` |
| Accept/decline/defer a proposal | `respond_to_proposal(proposalId, version, termsDigest, response, note)` |
| Withdraw acceptance | `withdraw_consent(proposalId, note)` |

`MISSING_FACT` answers now include the exact offered `actionJson.nudge.yes/no`
constraint. Ordinary binary questions use `{answer: boolean}`; grant questions
use `{granted: boolean}`. The frontend preserves question versions and proposal
digests and disables historical actions.

The initial description is a **shared** database record, so the composer explicitly
labels the first idea as shared. Subsequent messages are private. A private-first
bootstrap needs backend support; do not publish private user text as a brief.

## Required backend work

1. **Interpret chat throughout the workflow.** The current intake worker returns
   `NOT_HANDLED` after confirmed onboarding. Handle messages during research,
   negotiation, review and after agreement. Process messages in order per owner
   and room, with durable receipts so retries cannot duplicate questions or effects.
   Include messages written before a shared brief exists.

2. **Propose changes from chat instead of silently applying them.** Extract additions,
   replacements and removals, distinguish preferences from requirements, and attach
   source-message references. Treat questions/comments as conversation rather than
   inputs. Confirmations must apply only the displayed, still-current proposal.
   Identify the prior fact/rule when replacing it; do not accumulate contradictory
   generic rules. Relaxing a requirement needs explicit owner confirmation.

3. **Resolve corrections and free-form answers.** A chat correction currently uses
   the private-message API with human-readable context (`Regarding …`). Correlate
   it with the appropriate pending question/proposal, or expose a typed reference
   in the message contract if text context is insufficient. Supersede obsolete
   cards and deliver replacements. The frontend blocks the original after sending
   a correction; it waits for authoritative updated records. Implement owner reply
   correlation rather than assuming sending chat marks a question answered.

4. **Make readiness conditional on the actual trip.** Remove mandatory flight,
   airport, and flight-budget assumptions across validation, onboarding, research,
   solving and publication. Ask for a trip budget only when relevant; distinguish
   unknown from an explicit absence of a cap. Support driving/no booked stay and
   camping where the corresponding research/evidence pipeline exists. Report
   unsupported capabilities clearly instead of forcing unrelated flight inputs.
   Preserve soft preferences throughout solver preparation and scoring.

5. **Validate proposed facts before confirmation.** Check actual airport existence
   only for relevant transport modes; validate calendar dates, ordering and future
   travel dates. Missing evidence must not become a zero cost or an assumed fit.
   A confirmed brief and current shared settings must agree.

6. **Replan from confirmed revisions.** Confirming a material change should update
   the correct input/permission revision, supersede affected questions/proposals,
   and queue eligible work automatically. Preserve unaffected commitments or ask
   explicitly to revise them. Keep consent tied to exact proposal terms; chat and
   agent permissions never imply final approval or purchase authority.

7. **Expose processing status and useful revision summaries.** Add durable message
   states such as received/processing/answered/needs-confirmation/failed, with a
   response reference. The frontend currently knows only reducer success and must
   say “message sent,” not “change applied.” Emit audience-safe change summaries,
   revision identifiers and explicit waiting reasons. Current events often provide
   only “A participant updated their requirements”; exact before/after history
   cannot be reconstructed from those. Publish activity for conversational jobs,
   not just PLAN jobs.

8. **Preserve privacy in the new conversation flow.** Return private interpretation,
   personal caps/reasons and personal impacts only through owner views. Shared
   change events must remain neutral. Existing participation settings are global;
   confirm whether per-trip permissions are intended before changing their scope.

## Integration acceptance scenarios

- Describe a driving/camping trip without being asked for an airport or flight cap.
- Supply only information needed for the current decision; skip irrelevant questions.
- Add or correct a budget/date/preference while research is running. Confirm the
  exact change and see one new revision and appropriate replanning.
- Replace or remove a requirement without leaving an older rule active.
- Reply conversationally to a pending question and see the question resolved once.
- Correct a confirmed shared brief as organizer; participants cannot change it.
- Reload/reconnect while a message is processing without losing or repeating effects.
- Verify private details remain private in a two-participant room.
- Approve exact terms, then change an affected input; the old approval cannot carry
  over silently. No flow creates bookings or purchases.

The fixture browser tests validate frontend behavior, not these missing backend
capabilities. Run these acceptance scenarios against the integrated backend before
claiming end-to-end conversational planning is complete.
