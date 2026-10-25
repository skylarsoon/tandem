# Tandem companions

Four transparent, prerendered clay sprout characters generated with the built-in
imagegen tool. These are raster character assets, not rigged 3D meshes.

- `lavender.png`, `peach.png`, `mint.png`, `yellow.png`: original 1254px RGBA assets.
- Matching `.webp` files: 512px web derivatives, quality 85, alpha preserved;
  approximately 95 KB for the whole family. These are the files the UI loads.

## Prompts

Base generation:

> Use case: stylized-concept. Asset type: transparent web app mascot. Create ONE full-body friendly soft clay 3D sprout creature, matching the user's reference: chubby rounded egg-shaped body, two small stubby feet, two rounded flipper arms, two rounded leaf sprouts on its head, tiny glossy dark brown bead eyes, small curved smiling mouth. Color: pastel lavender throughout body and leaves. Front view, slightly three-quarter, entire creature fits centered within a square with 12 percent transparent padding. Arms relaxed outward. Soft studio illumination from upper left, subtle clay texture, premium warm gentle toy aesthetic. True transparent background, no scene, no pedestal, no text, no accessories, no ground shadow. Clearly separated limbs, beautiful clean silhouette. This is one reusable character asset, not a sheet.

Each color edit used the lavender PNG as its reference:

> Use case: precise-object-edit. Create the {color} variant of this exact sprout mascot for a matching web app character family. Change only the lavender clay body, arms, feet and leaves to soft pastel {palette}. Preserve exact character shape, smile, brown eyes, size, position, camera, lighting, texture and transparent background. No text, no scene, no ground shadow.

Substitutions: peach / warm coral peach; mint / sage mint green;
yellow / butter yellow. All calls used `transparent_background: true`.

## Behavior

`AgentAvatar` provides greeting bounce, idle sway, thinking tilt, searching sway,
checking nod, writing motion, and a waiting cue. The mediator uses mint with a
dashed halo and star badge. All motion respects reduced-motion settings and
pauses when hidden or outside the viewport. Message and picker thumbnails stay
static. Missing assets fall back to a flower while the adjacent agent name stays
visible. Colors are cosmetic; they do not imply agent personalities or abilities.

## Deployment

Deploy the updated SpacetimeDB module before the web client/dispatcher, since
their generated bindings include `my_agent_appearance`, `set_agent_appearance`,
and `room_members.agent_variant`. The private `agent_appearance` table is
additive; existing identities get deterministic defaults without a backfill.
Publish without deleting existing data. Restart the worker for intake activity
updates. Appearance follows the existing browser identity; cross-device identity
linking is outside this feature.

Useful checks: `npm test`, `npm run typecheck`, `npm run build` in `web/`;
`npx playwright test e2e/agents.spec.ts e2e/planning.spec.ts` in `web/`;
backend `test_appearance.py`, `test_rooms_and_access.py`, `test_live.py`;
worker `tests/unit/test_intake_activity.py` and existing intake/progress tests.
