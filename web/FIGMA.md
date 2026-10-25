# Tandem design integration

[Product Screens in Figma](https://www.figma.com/design/f7KSvYAJ1jZpjO6hGqw1Pi/Cal-Hacks-13.0?node-id=3-876)
is the visual reference for this frontend. The app renders real participant-scoped
data through its existing connection and action handlers; the design's example
travellers, dates, prices, and approval counts are not production data.

| Design | Frontend implementation | Adaptation |
| --- | --- | --- |
| Home / Your plans (`22:125`) | `app/page.tsx`, `components/design.tsx` | Welcome, room list, existing create/join forms; forms collapse after selecting a room. |
| Profile / Calibrate with Pip (`27:125`) | `app/profile/page.tsx`, existing `components/profile.tsx` | Companion sidebar around the existing question, summary, confirmation, and correction flow. |
| Planning / Shared progress (`27:127`) | `components/planning.tsx`, existing `components/workflow.tsx` | Shared companion artwork, live recorded workflow, private agent and settings panels. |
| Review / Your decision (`27:128`) | `Proposal` in `app/page.tsx` | Styled real proposal terms and existing exact-version responses, feedback, and withdrawal. |
| Shared navigation and controls | `app/layout.tsx`, `components/design.tsx`, `app/globals.css` | Cream/lavender/peach tokens, Manrope and IBM Plex Mono, responsive cards, focus states, and 48px controls. |

`public/artwork/companions.png` and `public/artwork/pip.png` are the original
Figma assets, stored locally. The home group artwork uses a cover fit; planning
and private companion slots use contain, matching their Figma slots. Fonts remain
bundled through the existing Fontsource dependencies.

## Behavior boundaries

Backend, worker, dispatcher, contracts, shared libraries, generated bindings,
dependencies, connection lifecycle, subscriptions, reducer payloads, privacy
filters, and proposal version/digest checks are unchanged.

The full conversational trip-description, parsed shared-brief confirmation, and
conversational intake screens are not wired by this visual update. The existing
room goal, invite-code, participation, constraint, and room-settings controls
remain available. No illustrative totals, agent activity, or agreement state is
substituted for backend records. Static companion illustrations do not imply
live 3D, voice, or simultaneous agent execution.

## Validation

- `npm run typecheck` and `npm run build` pass.
- All 23 existing frontend tests pass with `npm test`.
- Browser checks cover home, room, profile, and production setup layouts at
  1440px and 390px, including image loading, fonts, and horizontal overflow.
- An isolated test fixture outside the repository verifies the rendered create,
  join, participation, constraint, private-question, and exact-terms response
  controls still produce their original request payloads.
- Connection/reducer/question/proposal calls were compared against the original
  source. Full live-database integration was not run; it requires the team's
  configured database and worker.

For local setup and the existing live integration suite, see [README.md](README.md)
and the repository's `LOCAL_DEV.md`.
