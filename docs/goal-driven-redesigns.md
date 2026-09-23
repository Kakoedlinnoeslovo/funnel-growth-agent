# Goal-driven redesigns: implementation and acceptance

The workspace now accepts a baseline plus a goal, creatives, or both. `goalPrompt` and
`changeLevel` are persisted on the draft and every generated revision. Landing-to-CTA
conversion stays the metric. No site has been published as part of this implementation.

## Contracts

- `POST /api/drafts`: `baseVersion`, `baseHash`, optional `goalPrompt`, `creativeIds` or
  `adsetId`, and `changeLevel` (`light`, `medium`, `heavy`; default `medium`). A report token
  is needed only when selecting report creatives.
- `POST /api/drafts/<id>/select_direction`: `directionSetId`, `directionId`. Selection is
  bound to the persisted set and reviewed revision. The `awaiting_direction` state survives
  restart. Stale selections fail. Direction previews use valid temporary version identities
  inside an isolated lab copy.
- `POST /api/drafts/<id>/revise`: existing instruction/element payload plus `changeLevel`.
  A blueprint element edit uses `changes.page`. Blueprint content is validated data, never
  generated React or executable page code.
- `POST /api/drafts/<id>/reanalyze`: refreshes creative evidence in editable drafts; saved
  revision evidence and the last successful preview remain intact.
- Video assets support HTTP byte ranges for click-to-seek playback without restarting at zero.

`blueprint.py` composes and validates pages, enforces change levels and summarizes actual
changes. `workflow_design.py` prepares three recipe-bound directions. All proposals still
pass the model correction loop and transactional file allowlist. A Heavy revision must
change the hero treatment and recompose at least two body sections. A Light revision
cannot change layout, theme, media or art direction.

`video_analysis.py` handles Files API upload/processing/cleanup, 2 fps audiovisual analysis,
120-second intervals with two-second overlaps, original timestamps, visual-only fallback,
and storyboard extraction. Processing and individual API calls have bounded timeouts.
The native and sampled-frame results distinguish on-screen text from speech. A missing
provider or total provider outage preserves an explicitly unconfirmed visual preview.

## Shared renderer delivery

Installed locally in the sibling `recraft-pricing-performance` checkout:

- `src/funnel/sections/GrowthBlocks.tsx`, `growth-blocks.css`, renderer registry and schemas.
- `src/funnel/steps/LandingPage.tsx` theme integration.
- `funnels/growth-capabilities.json`, containing version, blocks, themes and input schema.
- Bundled Instrument Sans with OFL notice; warm treatment uses the existing local Recraft
  display font. MIT template pins and notices live in `THIRD_PARTY_GROWTH_BLOCKS.md`.

Merge/deploy these shared changes independently before publishing generated Heavy versions.
Publishing preparation checks the deployment checkout's capability version and refuses an
incompatible branch. It does not copy renderer source into generated-version commits.
Existing saved drafts retain their original source snapshots; start a new draft after installing
this renderer to access Heavy mode from an older baseline snapshot.

## Acceptance performed

- Python suite: **352 tests**, plus Ruff and JavaScript syntax checks.
- Pricing lab: schema generation, validation, lint, **356 tests**, and production build.
- Browser: prompt-only Heavy request, three real React previews, selection, Light revision
  preserving the Heavy composition, block inspector, desktop/390px layouts, and original CTA
  navigation into the existing quiz. No horizontal overflow or broken images observed in
  the inspected directions. External product/advertising requests were mocked in the isolated
  acceptance build; the normal preview CSP also blocks live checkout and tracking connections.
- Original uploaded 41.284-second video: six actual storyboard thumbnails, retained playback,
  truthful unavailable interpretation, and click-to-seek confirmed at **16.474 seconds**.
  This was a provider-unavailable UI check, not a successful semantic analysis.
- Automated video cases cover blank openings, later content, narration/text separation,
  silent clips, changing subtitles/fast cuts, long-video interval coverage, out-of-range
  timestamps, processing timeout, provider outages, byte changes, legacy refresh and immutable
  prior revision evidence. Existing upload tests cover portrait/rotated/short clips.

The real Gemini acceptance request was attempted on the original uploaded video. It returned
**402 RESOURCE_EXHAUSTED: prepaid credits depleted**. Consequently, its AI-generated narrative,
spoken statements and cited moments could not be checked against playback. Restore credits,
use **Reanalyze video**, and compare the concept and hook/demo/payoff/CTA timestamps with
playback before calling this manual acceptance complete. Live final-image quality also depends
on an available image provider; browser acceptance used existing assets and scripted proposals.

Screenshots are saved locally under `data/acceptance/screenshots/` (Git-ignored). The most useful
are `growth-results-mobile.png`, `growth-editor-desktop.png`, and `growth-video-evidence.png`.
