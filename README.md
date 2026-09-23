# funnel-growth-agent

## Creative → landing workflow

```bash
uv run funnel-growth demo --live --open
```

The workspace keeps **chat on the left and the landing preview on the right**. Research,
video evidence, changes and technical activity expand inside the conversation. The composer
stays available while you inspect results; saved drafts live in the header menu. Its visual system and interaction rules are documented in [DESIGN.md](DESIGN.md),
with the editable [Figma reference](https://www.figma.com/design/v8YCkFn11l7u8lGx0MPGjE).

Start with a baseline and describe your goal, select creatives, or combine both. A prompt works
without creatives or a weekly report. Choose **Light**, **Medium** (default), or **Heavy**.
Light refines copy while preserving the current design; Medium changes supported layouts,
section order/visibility and media; Heavy presents three compositions before generating the
selected page. The measured objective stays **landing-to-CTA conversion**.

Ask a question to discuss the current page without generating a revision. A clear request such
as “simplify the hero” starts the existing validated workflow at the selected change level.
Ambiguous requests receive a clarification. Claude uses the saved landing, evidence, agreed
brief and recent conversation; publishing always requires its explicit button. Conversations
persist across restarts, duplicate sends are deduplicated, and failed replies can be retried
without invalidating a ready landing.

Each completed operation keeps its own evidence snapshot. Expand **Research**, **Video
understanding**, **What changed**, or **Activity** beneath its result message. Screenshots and
players load on expansion, and live updates preserve open cards, playback, and unsent text.
**Edit page** opens the section editor in chat. Desktop supports a resizable divider and
fullscreen preview; mobile uses **Chat / Preview** tabs. Older drafts display their saved
activity with a clear label rather than invented past messages.

Choose a version from `recraft-pricing-performance`, search the weekly report by
creative/ad-set/campaign name, and optionally select one ad, several ads, or a whole ad set. All reported creatives are selectable,
including low-volume ads; observed metrics, missing assets and weak evidence are shown.
A group produces **one shared landing**, combining its themes through a main message and
supporting features. The creative review is advisory: different feature promises or destination
URLs do not block generation. Previously blocked drafts can resume with **Generate landing**.

For demonstrations, drop your own creatives into **Creatives**: JPEG, PNG or
WebP images (up to 20 MiB), and MP4, MOV or WebM videos (up to 100 MiB). Uploads can be used
alone, without a weekly report, or combined with report creatives. They have no measured
performance data. Upload validation remains local and responsive. During analysis, Gemini reads
the original video's visual sequence **and audio** using the Files API, sampled at 2 fps.
Videos over two minutes use consecutive 120-second intervals with two-second overlaps;
observations retain original-video timestamps. Temporary provider files are released afterward.

The **Video understanding** card shows the concept, narrative, separate narration/on-screen
text, and up to 12 seekable storyboard thumbnails. A representative nonblank frame replaces
the initial upload thumbnail when available. Native-analysis failure attempts sampled frames
and is labeled **Visual-only analysis — narration unavailable**. A total outage still preserves
the clip and storyboard, with interpretation explicitly unavailable. **Reanalyze video** retries
editable drafts. Cache identity includes video bytes, model, sampling policy and analysis version;
legacy first-frame evidence refreshes on the next generation without rewriting prior revisions.
Uploads persist in Git-ignored `data/uploads/`; selected assets are copied into each saved draft.
Files are limited to 40 megapixels per frame and previews are scaled to at most 2048 pixels.
The same video path handles selected report creatives with local files. Whole-catalog YouTube
analysis is deferred. Uploaded advertising claims remain evidence, not verified Recraft capabilities.

Heavy uses a versioned `PageBlueprint` (`landing_rebuild`) and the lab’s `growth-blocks-v1`
renderer: hero, gallery, features, workflow, before/after, verified proof, FAQ and final CTA.
Results-first, workflow-first and proof-first directions use distinct compositions and clean-light,
warm-editorial or dark-showcase treatments with bundled fonts. Previews reuse baseline assets
and labeled placeholders; final image generation waits for selection. Light edits retain an
earlier Heavy composition. The inline page editor changes block content, ordering, visibility and image briefs.

The shared renderer must be installed in the pricing lab before making a Heavy draft. GitHub
publishing also checks the target deployment branch for `growth-blocks-v1`; deploy that shared
renderer separately before publishing a generated Heavy version. Generated-version commits
do not include shared runtime changes. Offer, quiz answer identities, checkout and attribution
contracts remain protected. Custom React-only baselines remain unsupported.

Competitor evidence records section order, hero composition, typography, spacing, proof and
CTA placement, plus mobile differences. Proposals explain observed pattern → fit → Recraft
adaptation with source links. The renderer adapts pinned MIT HyperUI/Tailark patterns; its
`THIRD_PARTY_GROWTH_BLOCKS.md` preserves notices. No Shadcnblocks, paid templates, Next.js
or Firecrawl dependency is introduced; conversion lift still needs a measured experiment.

Asset briefs support role, target aspect ratio, composition, intended message, shared art
direction and `video:<creativeId>:<seconds>` references. New image plans default to Nano Banana 2
(`gemini-3.1-flash-image`), with reference-image support. Nano Banana Pro remains available through
an explicit `model: nano_banana_pro` image plan; saved plans retain their explicit model selection.
`GEMINI_MODEL` configures analysis and judging separately from image generation.
The Glam backend still rejects reference images. Candidate judging includes brief relevance and
simulated desktop/phone placements. Heavy pages label generated illustrations separately from
real product evidence. Composition and reference content participate in media cache keys.

Each draft saves its baseline, report snapshot, original ad assets, Gemini analysis, competitor
and YouTube context, and revision history. Generate a page, compare it to the baseline at
desktop/mobile widths, then refine it with instructions or page-element edits. The element
inspector exposes supported copy, CTA, hero layout, section order/visibility, and image briefs.
Copy-first, visual-first, and proof-early presets appear only when compatible with the baseline.
Structured edits merge into the previous proposal without another language-model proposal;
freeform instructions request a new cumulative proposal. Both paths validate and build a new
revision, preserving other changes and reusable media. Each revision
builds in an isolated pricing-lab snapshot. Failed operations retain prior previews and saved
inputs for retry. Drafts survive server restarts in `data/drafts/` (Git-ignored).
Model responses are validated and corrected automatically before they become proposals.
Baseline edit constraints are checked before generating images. Retrying a rendering or build
failure reuses the saved valid proposal and cached media; an invalid proposal is returned to
the model with the failed changes to correct.

The **YouTube library** in the research panel searches a saved Recraft channel index by title
or topic, with filters for videos, Shorts and recorded livestreams. The bundled demo snapshot
contains 84 public uploads (63 videos, 20 Shorts, 1 livestream), indexed on September 22, 2026.
Older drafts show the current library while retaining their original research snapshot.
Catalog lookup works offline; opening YouTube or downloading an uncached clip needs a connection.

```bash
# Refresh all channel pages into FUNNEL_GROWTH_DATA_DIR/youtube_catalog.json.
uv run funnel-growth index-youtube

# Update the bundled snapshot when preparing a demo for another machine.
uv run funnel-growth index-youtube --output src/funnel_growth_agent/youtube_catalog.json
```

The local data-directory snapshot takes precedence over the bundled file. Refreshes preserve
curated topic notes, deduplicate uploads and keep the previous file on failure. Unexpectedly
smaller results or missing tabs require `--allow-smaller` after review. The index contains
metadata, not transcripts or visual analysis; automatic topics come from titles, and missing
durations remain unknown. Video files are downloaded on demand into `data/media_cache/youtube/`.

The activity inspector shows actual creative analyses, research observations, model requests,
tool inputs/results, image candidates, judging, and build checks. Events stream over SSE with
durable sequence numbers; reconnecting resumes after the last event. Saved drafts and older
event histories remain readable. Cached work, unavailable sources, and failures are labeled;
elapsed time describes work in progress, not a simulated completion percentage.

**Publish** uses the configured GitHub auto-deployment or a separate Vercel
deployment, verifies public access and the reviewed page, then registers the version in the
local pricing lab. The default landing stays unchanged. **＋ New** refreshes the catalog and starts with empty
selections; any published version can be the next baseline. Historical drafts and previews
remain in the header's saved-draft menu.

Prerequisites: the existing API keys and media tools below, installed pricing-lab dependencies,
and a local lab dev server for browsing baselines before generation. Set
`PRICING_LAB_DIR=../recraft-pricing-performance` for the checkout used here. The library default
and `.env.example` still use `../recraft-pricing-lab`; adjust that path to your actual checkout.
Preview generation does not require Vercel.

To use an existing GitHub → Vercel integration, sign in with `gh auth login` and set
`GITHUB_PUBLISH_REPO`, `GITHUB_PUBLISH_BRANCH`, and `GITHUB_PUBLIC_ORIGIN` (see `.env.example`).
**Prepare publish** copies the latest deployment branch into an isolated checkout,
adds the saved version, gives its new paywall a distinct identity, registers its mobile layout
in the lab's tests, and runs tests, lint and a production build. Review this refreshed preview,
then **Publish** makes a normal fast-forward push containing only that version,
its catalog/test registration, and public verification markers. The existing GitHub integration
deploys it at `GITHUB_PUBLIC_ORIGIN/pm/<version>`. Existing versions and `default_version` are
preserved. Upstream changes require another preview; deployment retries reuse the saved commit.
The demo does not treat a sign-in-protected branch preview as a public page.
Production previews use the repository's defaults, not the developer checkout's staging
settings. If Vercel has public frontend overrides, mirror them as `GITHUB_PUBLISH_VITE_*`
in this demo's environment. **Refresh publishing preview** can rebuild an already submitted
commit for review and verification without changing its content or pushing a duplicate.

Alternatively, install/sign in to the Vercel CLI and link the destination project with
`vercel link` in the pricing lab, or set `VERCEL_PROJECT_ID` and `VERCEL_ORG_ID`. The project must
allow unauthenticated preview deployments; protected deployments are reported as failures,
not public successes. The publisher uses [Vercel's prebuilt deployment flow](https://vercel.com/docs/cli/deploy)
and [Build Output API](https://vercel.com/docs/build-output-api/configuration), without `--prod`.
No deployment occurs until Publish is clicked.

Supported baselines include Kittl heroes, carousel heroes (including `main/v1`), quiz heroes,
and variants derived from them. The fixed Brand Studio React page is listed for preview only
because its copy/media do not live in editable landing sections. Existing funnel behavior,
pricing, choice IDs and existing versions' analytics identities are preserved. Built local previews block
tracking scripts, API connections and forms so reviewing a draft cannot send tracking events or checkout
requests. The public build retains the lab's configured frontend environment.

### Competitor research

Research defaults to **Kittl, Canva, Zeely, Runway, and Luma AI**, with **GB** as the Ads Library
country. The saved `ResearchConfig` accepts `competitors`, `country`, `adLibraryUrls`,
`landingUrls`, and `refresh`; the setup panel exposes these controls. Research samples up to
three matching active ads per competitor and chooses one relevant destination after removing
duplicate tracking variants. It records the ad ID and Library link, advertised destination,
resolved landing, observed redirects, screenshots, and one public primary-CTA destination.
Desktop/mobile hero captures and a full-page capture support the landing read.

Optional `META_AD_LIBRARY_TOKEN` credentials enable the Ads Library API. This is separate
from growth-loop's owned-account `META_ADS_TOKEN`. Public gstack browsing and pasted Library
links provide another discovery path; login walls, access restrictions, or missing ad evidence
remain visible. Advertiser matching uses the observed name and official-domain destination;
it does not claim a Meta verification badge. Directly supplied landing URLs remain **reference
evidence**, without an invented relationship to an ad. No competitor performance is inferred.

Successful research is cached for **24 hours**; **Refresh research** bypasses it. Each draft
keeps its selected evidence, and refreshed screenshot files do not overwrite earlier captures.
Research validates public HTTPS destinations, DNS answers, and HTTP redirect hops. It stops
at authentication/payment boundaries and never submits forms. These navigation checks do
not fully isolate arbitrary page JavaScript or subresource requests.

### Who converts

The audience panel filters the saved report to the selected owned creatives. It keeps
**Meta-attributed purchases**, **PostHog payment events**, and **warehouse payers** separate.
Older reports work without audience data; unavailable metrics stay unavailable, uploads have
no measured outcomes, and sparse or zero conversions do not establish a winning audience.

The sibling growth-loop pipeline optionally adds the versioned `creative_audience` report
block: observed creative-to-funnel/version outcomes, independent `age_gender`, `country`, and
`platform_placement_device` Meta breakdowns, plus coverage, freshness, and attribution details.
To populate it, ingest the same period used by the report, then rebuild that report:

```bash
cd ../growth-loop
uv run growth-loop ingest-audience-api --since 2026-09-14 --until 2026-09-20
uv run growth-loop report --date 2026-09-21 --week
```

These example dates are explicit; choose your actual reporting window. Owned-account
Marketing API credentials and access are configured in growth-loop. Missing permissions or
unsupported breakdowns are reported per source. Breakdown views are not additive, and
warehouse conversions are never apportioned into Meta demographic buckets. Configured
targeting and the model's inferred creative intent are not measured audience behavior.
Production Meta CAPI instrumentation is a separate follow-up; this workflow does not send
conversion events or change campaign targeting.

The read-only proposal CLI and recorded replay console below remain available. `R`/`A`
keyboard controls apply to the replay console; live mode uses the workflow buttons.

Proposes and applies landing-page experiments for the Recraft pricing lab.

The agent reads the latest growth-loop report, the current landing page, the top-performing
ad creatives and a couple of reference landings, then asks Claude for one **landing redesign**
— a single hypothesis expressed across the whole first screen: hero copy, hero layout, hero
clip, every CTA, section order and generated showcase tiles. Applying it produces the media,
copies the base funnel into a new variant with only the allowed changes, validates it twice
and publishes. **Nothing is written to the pricing lab until `apply` runs.**

## Start here

```bash
uv run funnel-growth demo --live --open
```

One command opens the creative-to-landing workflow described above.

The recorded console retains `R` run · `A` apply · `D` deploy · `F` fullscreen.

Needs the lab's dev server up for the left pane:

```bash
cd ../recraft-pricing-performance && npm run dev
```

Generation produces the media and builds a complete preview. Expect several minutes for a
first draft; cached assets speed up later revisions. The draft's activity and history are saved
as work progresses.

The CLI below exposes the steps individually for scripting and focused runs.

## Setup

```bash
uv sync --extra dev
cp .env.example .env            # set API keys and the actual PRICING_LAB_DIR
brew install ffmpeg webp        # ffmpeg, ffprobe, cwebp
```

Requires Python 3.11+ with [uv](https://docs.astral.sh/uv/), `node` on `PATH` (yt-dlp needs it
for YouTube), and two sibling checkouts:

```
all/
├── funnel-growth-agent/            # this repo
├── growth-loop/                    # reports + downloaded ad creatives
└── recraft-pricing-performance/    # funnels/site.yaml and funnels/<version>/
```

The lab also needs `npm install`, because `apply` runs `npm run funnel:validate` there.

Keys: Anthropic is required for `propose`; Gemini describes creatives, reads landings and
generates and judges the showcase tiles. Optional: a glam.ai key routes tiles through glam
instead, and the gstack `browse` binary enables `research_landing` (without it the tool
returns an error the model reads and moves on).

## Configuration

Edit `.env`. Relative paths resolve against the directory you run from.

| Variable | Default | Purpose |
| --- | --- | --- |
| `GROWTH_LOOP_DIR` | `../growth-loop` | Where reports and ad creatives are read from |
| `PRICING_LAB_DIR` | `../recraft-pricing-lab` | Set `../recraft-pricing-performance` for this checkout; baseline and published variants |
| `ANTHROPIC_API_KEY` | — | Required for `propose` |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Model for the proposal loop |
| `GEMINI_API_KEY` | — | Creative analysis, landing reads, tile generation and judging |
| `GEMINI_MODEL` | `gemini-3.6-flash` | Leave unset unless you know the name is current |
| `GLAM_API_KEY` | — | Optional; tiles via glam.ai instead of Gemini (no reference images) |
| `TILE_VARIANTS` | `3` | Candidates per tile before the judge picks (1–4) |
| `GSTACK_BROWSE` | Auto-discovered | gstack browser; searches `.claude/skills` and `.agents/skills`, then `PATH` |
| `META_AD_LIBRARY_TOKEN` | — | Optional competitor Ads Library access, separate from owned-account Meta credentials |
| `META_AD_LIBRARY_API_VERSION` | `v23.0` | Graph API version used for competitor ad discovery |
| `BASE_VERSION` | `default_version` from `site.yaml` | Leave unset to follow the lab |
| `MAX_REPORT_AGE_HOURS` | `48` | Older reports are rejected as stale |
| `MIN_LANDING_PEOPLE` | `100` | Below this, metrics count as insufficient data |
| `MIN_CREATIVE_SPEND` / `MIN_CREATIVE_CLICKS` | `5` / `10` | Ranking thresholds |
| `FUNNEL_GROWTH_DATA_DIR` | `./data` | Run memory and every cache |
| `PROD_LANDING_BASE` | — | Public base for deployed variants, e.g. `https://www.recraft.ai/pm`; unset prints the Vercel host |

## Commands

```bash
uv run funnel-growth status                      # base version, variants, latest run
uv run funnel-growth propose                     # → a run id like 2026-09-20T2328-v8-001
uv run funnel-growth apply <run_id>              # publish the variant into the lab
uv run funnel-growth deploy [<variant>]          # open the PR that ships it to production
uv run funnel-growth show <run_id> --open        # one page: evidence, diffs, tiles, scores
                                                 #   (or `apply <run_id> --page` to do both)
uv run funnel-growth evaluate <run_id>           # once the variant has traffic
```

**`propose`** reads the freshest weekly report, ranks comparable-traffic creatives, describes
them with Gemini (cached in `data/creative_analysis/`), then runs a read-only Claude tool loop
capped at 12 calls:

| Tool | Returns |
| --- | --- |
| `get_landing_cta_metrics` | The latest landing→CTA slice |
| `get_current_landing` | Every section with id, copy, hero layout, clip, showcase groups |
| `get_previous_runs` | Earlier hypotheses, changes, evaluations, learnings |
| `get_top_creatives` / `get_creative_analysis` | Ranked ads and Gemini's read: promise, text, palette, motifs |
| `get_media_sources` | Curated Recraft YouTube clips and ranked creatives with a local video |
| `get_showcase_style` | Gemini's read of the real tiles, with `tile:<group>:<i>` ids |
| `research_landing(url)` | Desktop and phone screenshots of a public landing, summarised |

The result appends to `data/memory/runs.jsonl` — either a full proposal or `no_experiment`
with a reason. A proposal's `changes` covers up to four groups: `copy` (headline, subhead,
ctaLabel, reassurance), `layout` (hero copy-first/video-first, stickyCta), `composition`
(section order after the hero) and `media` (hero clip plus showcase tile briefs). Media is a
plan — nothing is downloaded or generated yet. Add `--refresh-creatives` to ignore the
Gemini cache.

**`apply`** creates the next free variant (`v8_a1`, `v8_a2`, …):

- Copies the base funnel into a temp dir on the same volume.
- Produces media into `funnels/<variant>/assets/`: downloads the clip with yt-dlp (or reads
  the ad mp4), trims and encodes at 1280 and 720 px as mp4 and webm with a poster, then writes
  each chosen tile at 1600 px plus an 800 px thumb. Cached by content key, so a retry
  re-encodes nothing and calls no API.
- Patches section order, copy, hero layout, hero video and showcase images.
- Refuses if the base landing changed since the proposal, or if the plan names a clip,
  creative or tile the model was never offered.
- Runs a hard-diff allowlist and `npm run funnel:validate` twice, then publishes atomically
  and appends to `published_versions`. `default_version` is never changed.

Prints `http://localhost:5173/pm/<variant>` on success — restart `npm run dev` to see a new
version folder. A run is applied once; propose again for another variant.

**`deploy`** ships an applied variant. `apply` does not touch git: it only writes
into the local lab checkout, and production is whatever the lab repo's `main` branch holds
(Vercel deploys every merge to `main`; Recraft proxies `/pm` to it). So publishing means
getting `funnels/<variant>` onto `main`:

- Fetches `origin` and builds a branch `agent/<variant>` off `origin/main` in a throwaway git
  worktree, so a stale or dirty local checkout never leaks into the commit.
- Copies `funnels/<variant>` (without its git-ignored `content/`) and adds exactly two lines
  to `site.yaml`: the variant under `published_versions` and its description under
  `versions`. `default_version` is never written, so nothing is served differently until
  traffic is pointed at the new URL.
- Runs `npm run funnel:validate` on the branch, commits, pushes and opens the pull request
  with `gh`, then waits for Vercel's preview build and prints its URL.
- **Merging stays a human click on GitHub.** The command waits for it (an hour by default),
  then follows the production deployment through GitHub's deployments API and prints the
  live URL. `--no-wait` stops once the pull request is open.

Needs `gh auth login` with push access to the lab repo. Set `PROD_LANDING_BASE` (for example
`https://www.recraft.ai/pm`) to print the public URL instead of the Vercel deployment host.

**`evaluate`** compares the variant's landing→CTA rate against the baseline stored at propose
time, once it appears in a new report. Directional only, written back to memory as a learning.

## Recorded console

```bash
uv run funnel-growth demo --open
```

The landing on the left, the agent on the right. The right pane shows the tool trace, last
week's evidence, the ranked creatives, the current landing, then the proposal with its copy
diff and tile briefs. **A** animates the safety checklist, each tile arrives with its
candidates, the judge's five scores fill in, the pick turns lime, and the iframe switches to
`/pm/<variant>`.

**D** replays the deployment phase when the recording includes it. The separate
`deploy` CLI opens a pull request, waits for a person to merge it, and verifies the
production deployment; it never merges automatically.

Preflight prints at start: API keys present, dev server reachable, base landing on disk.
Apply is available when the selected recording contains an apply phase. CLI runs can be
recorded or synthesized into `data/demo/` for replay — see Advanced.

## Advanced

Hidden from `--help` on purpose; all of them still run when typed.

**Tile review.** `apply` generates showcase tiles itself, so this is optional — it only warms
the cache apply reads, letting you reject bad tiles before they reach the lab:

```bash
uv run funnel-growth tiles <run_id> --open        # generate, open the contact sheet
uv run funnel-growth tiles <run_id> --variants 1  # cheap path, one candidate per tile
uv run funnel-growth tiles <run_id> --refresh     # throw candidates away and regenerate
```

Renders `TILE_VARIANTS` candidates per tile at 2K with the reference images attached; a Gemini
vision judge scores each (house style, subject clarity, cleanliness, crop safety, palette) and
picks one. The sheet at `data/media_cache/sheets/<run_id>.html` shows every candidate and
score. Exits 1 if the proposal has no showcase tiles.

**Replay — the stage path.** Fixed pacing, no model, no network, no encoding during the talk:

```bash
uv run funnel-growth demo record                  # real run, headless, recorded
uv run funnel-growth demo synthesize <run_id>     # recording from a past run, no API calls
uv run funnel-growth demo --open                  # replay the newest recording at :8765
uv run funnel-growth demo --recording <id> --speed 2
```

A recording only has an Apply phase if that run was actually applied — `synthesize` on a
`proposed` run stops after the proposal and the Apply button stays dark. The server serves
only files the recording names.

For a clean demo, point the data dir somewhere fresh so memory reads "0 prior experiments":

```bash
mkdir -p data-demo/creative_analysis && cp data/creative_analysis/*.json data-demo/creative_analysis/
export FUNNEL_GROWTH_DATA_DIR=$PWD/data-demo
```

Warm the caches beforehand: failed-operation retries and later revisions reuse matching cached media.
Do not `git stash` or `git checkout` in the lab while a variant is in use.

**Scoring.** `uv run funnel-growth quality-gate` lists report snapshots for scoring `propose`
across distinct weeks.

## Development

```bash
uv run pytest          # no network; local storyboard tests require ffmpeg and ffprobe
uv run ruff check .
uv run ruff format .
```

Workspace JavaScript regression checks use Node when available.
Two integration tests run against the real pricing-lab landing when the checkout is present,
still with faked tools.

## Project layout

```
src/funnel_growth_agent/
├── cli.py            # Typer entry point
├── config.py         # .env, sibling paths, base version resolution
├── agent.py          # Claude tool loop (read-only, 12-call cap)
├── proposal.py       # Builds and saves the proposal envelope
├── apply.py          # Transactional variant creation
├── landing*.py       # Landing summary, hard-diff allowlist, YAML patching
├── media.py          # yt-dlp / ffmpeg / cwebp, image clients, candidates + judge
├── tile_*.py         # House-style tile prompts and contact sheet
├── gemini.py         # Creative descriptions, cached on disk
├── research.py       # Public landing observations, screenshots, cache and navigation checks
├── competitor_research.py # Ads Library discovery and ad-to-landing provenance
├── workflow.py       # Persistent drafts, revisions, operation events and publication
├── workflow_editing.py # Capability-driven element changes and compatible presets
├── workflow_audience.py # Selected creative outcomes and optional audience evidence
├── demo/             # Live workspace + SSE server, legacy console, recorder and replay
├── instructions.md   # System prompt          └── playbook.md  # CRO playbook
data/                 # runs.jsonl, creative_analysis/, media_cache/, demo/, research/, tmp/
```

Implementation and acceptance details: [Goal-driven redesigns](docs/goal-driven-redesigns.md)
and [Chat workspace](docs/chat-workspace.md).

## Troubleshooting

- **"Cannot resolve base version"**: `PRICING_LAB_DIR` does not point at a checkout with
  `funnels/site.yaml`. Fix the path or set `BASE_VERSION`.
- **Gemini 402 / depleted prepaid credits**: restore the configured project’s credits, then use
  **Reanalyze video**. The clip, storyboard and last successful landing preview stay available.
- **Heavy renderer missing on deployment branch**: install the shared renderer and
  `funnels/growth-capabilities.json` on that branch separately, then prepare publishing again.
- **Gemini 404 "no longer available"**: `GEMINI_MODEL` names a retired model. Unset it to take
  the default, then `propose --refresh-creatives` — a failed call is cached as a title-only
  fallback and would otherwise be reused silently.
- **Stale report error**: the newest `report_data.json` is older than `MAX_REPORT_AGE_HOURS`.
- **"stale base hash"** on apply: the base landing changed after the proposal. Propose again.
- **"is applied as v8_a3; a run is applied once"**: propose again for another variant.
- **`funnel:validate` failed**: the lab needs `npm install`.
- **"media: no image backend"**: set `GEMINI_API_KEY` or `GLAM_API_KEY`.
- **"media: yt-dlp ... failed"**: check `node` is on `PATH`. Nothing in the lab changed.
- **"is not in the catalog"** / **"not among this proposal's ranked creatives"**: the model
  named something it was never offered. Propose again.
- **Variant 404s at `/pm/<variant>`**: restart `npm run dev`; Vite globs folders at startup.
- **Apply button dark in the console**: that recording has no apply phase. Use `--live`, or
  synthesize from a run that was applied.
- **"`<run_id>` has no showcase tiles"**: that proposal's `media.showcase` is empty, so there
  is nothing for `tiles` to render. Nothing is wrong — `apply` handles it.
