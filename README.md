# funnel-growth-agent

## Creative → landing workflow

```bash
uv run funnel-growth demo --live --open
```

The live demo now starts with an explicit baseline and creative selection. Choose a version
from `recraft-pricing-lab`, search the latest weekly report by creative/ad-set/campaign name,
and select one ad, several ads, or a whole ad set. All reported creatives are selectable,
including low-volume ads; observed metrics, missing assets and weak evidence are shown.
A group produces **one shared landing**, combining its themes through a main message and
supporting features. The creative review is advisory: different feature promises or destination
URLs do not block generation. Previously blocked drafts can resume with **Generate this draft**.

For demonstrations, drop your own creatives into **Upload your own creative**: JPEG, PNG or
WebP images (up to 20 MiB), and MP4, MOV or WebM videos (up to 100 MiB). Uploads can be used
alone, without a weekly report, or combined with report creatives. They have no measured
performance data. FFmpeg/FFprobe validate each file and extract a video's first decoded frame
as its image reference. Analysis distinguishes visible text from inferred CTA direction; a
blank opening frame or absent CTA uses supported Recraft context without blocking generation.
The original video remains available, and CTA wording is editable in the generated draft.
Uploads persist in Git-ignored `data/uploads/`; selected assets are copied into each saved draft.
Files are limited to 40 megapixels per frame and previews are scaled to at most 2048 pixels.
The demo continues to generate Recraft pages using existing baselines, claims and destinations.

Each draft saves its baseline, report snapshot, original ad assets, Gemini analysis, competitor
and YouTube context, and revision history. Generate a page, compare it to the baseline at
desktop/mobile widths, then refine it with instructions or quick copy/CTA edits. Each revision
builds in an isolated pricing-lab snapshot. Failed operations retain prior previews and saved
inputs for retry. Drafts survive server restarts in `data/drafts/` (Git-ignored).
Model responses are validated and corrected automatically before they become proposals.
Baseline edit constraints are checked before generating images. Retrying a rendering or build
failure reuses the saved valid proposal and cached media; an invalid proposal is returned to
the model with the failed changes to correct.

**Publish public version** uses the configured GitHub auto-deployment or a separate Vercel
deployment, verifies public access and the reviewed page, then registers the version in the
local pricing lab. The default landing stays unchanged. **Create another version** refreshes the catalog and starts with empty
selections; any published version can be the next baseline. Historical drafts and previews
remain in the sidebar.

Prerequisites: the existing API keys and media tools below, installed pricing-lab dependencies,
and a local lab dev server for browsing baselines before generation. Set
`PRICING_LAB_DIR=../recraft-pricing-lab` in an existing `.env` if it still uses the old checkout
name. Preview generation does not require Vercel.

To use an existing GitHub → Vercel integration, sign in with `gh auth login` and set
`GITHUB_PUBLISH_REPO`, `GITHUB_PUBLISH_BRANCH`, and `GITHUB_PUBLIC_ORIGIN` (see `.env.example`).
**Prepare publishing preview** copies the latest deployment branch into an isolated checkout,
adds the saved version, gives its new paywall a distinct identity, registers its mobile layout
in the lab's tests, and runs tests, lint and a production build. Review this refreshed preview,
then **Publish public version** makes a normal fast-forward push containing only that version,
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
cd ../recraft-pricing-lab && npm run dev
```

Generation produces the media and builds a complete preview. Expect several minutes for a
first draft; cached assets speed up later revisions. The draft's activity and history are saved
as work progresses.

The CLI below does the same steps one at a time, which is what you want for real work rather
than a demo.

## Setup

```bash
uv sync --extra dev
cp .env.example .env            # fill ANTHROPIC_API_KEY and GEMINI_API_KEY
brew install ffmpeg webp        # ffmpeg, ffprobe, cwebp
```

Requires Python 3.11+ with [uv](https://docs.astral.sh/uv/), `node` on `PATH` (yt-dlp needs it
for YouTube), and two sibling checkouts:

```
all/
├── funnel-growth-agent/            # this repo
├── growth-loop/                    # reports + downloaded ad creatives
└── recraft-pricing-lab/            # funnels/site.yaml and funnels/<version>/
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
| `PRICING_LAB_DIR` | `../recraft-pricing-lab` | Baselines and locally registered published variants |
| `ANTHROPIC_API_KEY` | — | Required for `propose` |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Model for the proposal loop |
| `GEMINI_API_KEY` | — | Creative analysis, landing reads, tile generation and judging |
| `GEMINI_MODEL` | `gemini-3.6-flash` | Leave unset unless you know the name is current |
| `GLAM_API_KEY` | — | Optional; tiles via glam.ai instead of Gemini (no reference images) |
| `TILE_VARIANTS` | `3` | Candidates per tile before the judge picks (1–4) |
| `GSTACK_BROWSE` | `~/.claude/skills/gstack/browse/dist/browse` | Browser CLI for `research_landing` |
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

**`deploy`** ships an applied variant. Nothing the agent does touches git: `apply` only writes
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

Warm the caches beforehand: apply once, and a re-apply of the same run makes no network calls.
Do not `git stash` or `git checkout` in the lab while a variant is in use.

**Scoring.** `uv run funnel-growth quality-gate` lists report snapshots for scoring `propose`
across distinct weeks.

## Development

```bash
uv run pytest          # no network, no ffmpeg: media tools, judge and browsers are faked
uv run ruff check .
uv run ruff format .
```

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
├── demo/             # Console: recorder, synthesize, replay, server, console.html
├── instructions.md   # System prompt          └── playbook.md  # CRO playbook
data/                 # runs.jsonl, creative_analysis/, media_cache/, demo/, research/, tmp/
```

## Troubleshooting

- **"Cannot resolve base version"**: `PRICING_LAB_DIR` does not point at a checkout with
  `funnels/site.yaml`. Fix the path or set `BASE_VERSION`.
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
