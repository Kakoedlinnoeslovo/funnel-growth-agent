# funnel-growth-agent

Proposes and applies landing-page experiments for the Recraft pricing lab.

The agent reads the latest growth-loop report, looks at the current landing page,
the top-performing ad creatives, one or two reference landings, and the ready video
clips it may use, then asks Claude to propose one **landing redesign** (or to decline).
A redesign is one hypothesis expressed across the whole first screen: hero copy, hero
layout, hero clip, every CTA on the page, section order, and generated showcase tiles.
A separate `apply` step produces the media, copies the base funnel into a new variant
with only the allowed changes, validates it twice, and publishes it. Nothing is written
to the pricing lab until you explicitly run `apply`.

## Quick start

The whole loop, in order. Each step is explained in detail under "Commands".

```bash
# 0. One-time setup
cd funnel-growth-agent
uv sync --extra dev
cp .env.example .env            # then fill ANTHROPIC_API_KEY and GEMINI_API_KEY
brew install ffmpeg webp        # ffmpeg, ffprobe, cwebp (yt-dlp is a project dependency)

# 1. Start the lab's dev server in another terminal (it serves /pm/<version>)
cd ../recraft-pricing-performance && npm install && npm run dev

# 2. Back here: see the base version and existing variants
uv run funnel-growth status

# 3. Ask Claude for one redesign (reads reports, creatives, the landing, competitors)
uv run funnel-growth propose
#    -> prints the proposal and a run id like 2026-09-20T2328-v8-001

# 4. Generate the showcase tiles and look at them before touching the lab
uv run funnel-growth tiles <run_id> --open
#    -> opens a contact sheet: every candidate, the judge's scores, the chosen one
#    Not happy? `propose` again, or `tiles <run_id> --refresh` for new candidates.

# 5. Publish the variant into the lab (hero clip, tiles, copy, order)
uv run funnel-growth apply <run_id>
#    -> prints http://localhost:5173/pm/v8_a3 (restart `npm run dev` if it 404s)

# 6. Later, when growth-loop has traffic for the variant
uv run funnel-growth evaluate <run_id>
```

Typical timings on a laptop: `propose` 1–3 minutes, `tiles` 1–2 minutes for three tiles with
three candidates each, `apply` about a minute the first time (clip download and encode) and
seconds on a retry, because every media artefact is cached.

### Running a clean demo

Point the data dir somewhere fresh so memory says "0 prior experiments" and nothing from
earlier runs leaks in. Copy the Gemini creative cache over to save calls:

```bash
mkdir -p data-demo/creative_analysis && cp data/creative_analysis/*.json data-demo/creative_analysis/
export FUNNEL_GROWTH_DATA_DIR=$PWD/data-demo
uv run funnel-growth propose
uv run funnel-growth tiles <run_id> --open
uv run funnel-growth apply <run_id>
```

`data-demo/` is gitignored. Warm the caches before stage time: apply once, then a re-apply of
the same run (or a run with the same clip and tiles) makes no network calls. Do not `git stash`
or `git checkout` in the lab while a variant is in use; the variant folder and the `site.yaml`
line must stay in place.

## Prerequisites

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- Two sibling repos checked out next to this one (paths are configurable):
  - `../growth-loop` — produces `data/output/reports/*/report_data.json` and keeps the
    downloaded ad creatives (jpg and mp4) in `data/output/creative/live/`
  - `../recraft-pricing-performance` — holds `funnels/site.yaml` and `funnels/<version>/`
- Node in the pricing-lab repo, because `apply` runs `npm run funnel:validate` there and
  `npm run dev` serves the preview
- `ffmpeg`, `ffprobe` and `cwebp` on `PATH` (`brew install ffmpeg webp`); `yt-dlp` comes with
  the project and needs `node` on `PATH` for YouTube's challenge solver
- An Anthropic API key (required for `propose`)
- A Gemini API key: describes creatives, reads reference landings and the real showcase tiles,
  generates showcase tiles (Nano Banana) and judges the candidates
- Optional: a glam.ai key routes tile generation through glam instead (text prompts only)
- Optional: the gstack `browse` binary for `research_landing`; without it the tool returns an
  error the model can read and moves on

Expected layout:

```
all/
├── funnel-growth-agent/            # this repo
├── growth-loop/
└── recraft-pricing-performance/
```

## Configuration

Edit `.env`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `GROWTH_LOOP_DIR` | `../growth-loop` | Where weekly/daily reports and ad creatives are read from |
| `PRICING_LAB_DIR` | `../recraft-pricing-performance` | Funnel YAML that `apply` writes into |
| `ANTHROPIC_API_KEY` | — | Required for `propose` |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Model used for the proposal loop |
| `GEMINI_API_KEY` | — | Creative analysis, landing reads, Nano Banana tiles, tile judge |
| `GEMINI_MODEL` | `gemini-3.6-flash` | Model used to describe creatives, screenshots and judge tiles |
| `GLAM_API_KEY` | — | Optional; routes showcase tiles through glam.ai instead of Gemini (no reference images) |
| `TILE_VARIANTS` | `3` | Candidates generated per showcase tile before the judge picks one (1–4) |
| `GSTACK_BROWSE` | `~/.claude/skills/gstack/browse/dist/browse` | Browser CLI used by `research_landing` |
| `BASE_VERSION` | `default_version` from `funnels/site.yaml` | Override the base funnel version |
| `MAX_REPORT_AGE_HOURS` | `48` | Reports older than this are rejected as stale |
| `MIN_LANDING_PEOPLE` | `100` | Below this, metrics count as insufficient data |
| `MIN_CREATIVE_SPEND` | `5` | Minimum spend for a creative to be ranked |
| `MIN_CREATIVE_CLICKS` | `10` | Minimum clicks for a creative to be ranked |
| `FUNNEL_GROWTH_DATA_DIR` | `./data` | Run memory, creative cache, media cache, research cache, temp dir |

Relative paths resolve against the directory you run from, so use absolute paths when running
from a git worktree or another checkout.

## Commands

All commands go through the `funnel-growth` CLI:

```bash
uv run funnel-growth --help
```

### `status`

```bash
uv run funnel-growth status
```

Shows the base version, existing variants, the latest proposal, and the latest
applied variant with its evaluation.

### `propose`

```bash
uv run funnel-growth propose
uv run funnel-growth propose --refresh-creatives   # ignore the Gemini creative cache
```

Reads the freshest weekly report, ranks comparable-traffic creatives, describes
them with Gemini (cached in `data/creative_analysis/`), then runs a read-only
Claude tool loop capped at 12 calls. The tools it can call:

| Tool | What it returns |
| --- | --- |
| `get_landing_cta_metrics` | The latest landing→CTA slice; v6 numbers are a proxy |
| `get_current_landing` | Every section with its id, copy, hero layout, clip, showcase groups |
| `get_previous_runs` | Earlier hypotheses, changes, evaluations and learnings |
| `get_top_creatives` / `get_creative_analysis` | Ranked ads and Gemini's read of each: promise, visible text, palette, reusable motifs |
| `get_media_sources` | Curated Recraft YouTube clips (`youtube_catalog.json`) and ranked creatives with a local ad video |
| `get_showcase_style` | Gemini's read of the real showcase tiles per group, with the `tile:<group>:<i>` ids |
| `research_landing(url)` | Desktop and phone screenshots of a public landing, read into a pattern summary |

The result is appended to `data/memory/runs.jsonl` with a run id like
`2026-09-20T2328-v8-001`. No funnel files are modified.

The output is either a full proposal (problem, baseline, hypothesis, changes, alternative
ideas) or `no_experiment` with a reason. A `landing_redesign` proposal's `changes` has up
to four groups:

| Group | What it may change |
| --- | --- |
| `copy` | headline, subhead, ctaLabel, reassurance on the hero, video-cta and final-cta; headline and ctaLabel on every inline-cta |
| `layout` | hero `layout` (copy-first / video-first) and `stickyCta` |
| `composition` | order of the sections after the hero; omit at most two of logo-strip, showcase, style-switcher, inline-cta, video-cta |
| `media` | `heroVideo` (a YouTube id from the catalog or a ranked creative's ad video, with start and duration) and `showcase` tiles: per group slot a brief, medium, palette, background and references (`tile:<group>:<i>` real tiles, `creative:<id>` a winning ad, `previous` the prior slot's output) |

Media is a plan, not files: nothing is downloaded or generated at propose time.

### `tiles`

```bash
uv run funnel-growth tiles <run_id> --open           # generate into the cache, open the contact sheet
uv run funnel-growth tiles <run_id> --variants 1     # cheap path, one candidate per tile
uv run funnel-growth tiles <run_id> --refresh        # throw away candidates and regenerate
```

For each tile the house-style builder (`tile_prompt.py`) turns the brief into a narrative
prompt, Nano Banana Pro renders `TILE_VARIANTS` candidates at 2K 16:9 in parallel with the
reference images attached, and a Gemini vision judge scores each candidate (house style,
subject clarity, cleanliness, crop safety, palette) and picks one. The chosen tile feeds the
next slot's `previous` reference. The sheet at `data/media_cache/sheets/<run_id>.html` shows
every candidate, the scores, the references and the prompt. Nothing in the lab changes;
`apply` reuses the cache, so this is the place to iterate on tile quality.

### `apply`

```bash
uv run funnel-growth apply <run_id>
```

Creates the next free variant directory (`v8_a1`, `v8_a2`, …) in the pricing lab:

- Copies the base funnel into a temp directory on the same volume.
- Produces the media into `funnels/<variant>/assets/`: downloads the clip with yt-dlp (or
  reads the ad mp4 from growth-loop), trims and encodes it at 1280 and 720 px as mp4 and
  webm with a first-frame poster (the lab's own recipe), generates and judges showcase tiles as
  in `tiles`, and writes each chosen tile at 1600 px plus an 800 px thumb under
  `assets/thumbs/showcase/`. Everything is cached under `data/media_cache/` by content key, so a
  retry re-encodes nothing and calls no API.
- Patches the landing: section order, copy, hero layout, hero video, showcase images.
- Refuses if the base landing page changed since the proposal was made, or if the plan names
  a clip, creative or tile the model was never offered.
- Runs a hard-diff allowlist (identity-aware per section; new files only under
  `assets/video`, `assets/showcase` and `assets/thumbs/showcase`, at most 6 MB each) and
  `npm run funnel:validate` twice, then publishes atomically.
- Appends the variant to `published_versions` in `site.yaml`. `default_version` is never
  changed. The base version and `funnels/_shared` are hashed before and after.

Progress is printed as it happens (`• media: encoded 1280 mp4`, `• media: judge picked #1`).
On success it prints a preview URL of the form `http://localhost:5173/pm/<variant>`. A running
`npm run dev` needs a restart to see a new version folder. A run is applied once; propose
again for another variant.

### `show`

```bash
uv run funnel-growth show <run_id> --open
uv run funnel-growth apply <run_id> --page      # apply, then write the page too
```

One self-contained HTML page for a run at `data/media_cache/pages/<run_id>.html`: baseline,
the winning creatives, problem and hypothesis, every change as before → after against the
base landing, the section order, the hero clip with its poster once produced, and each
showcase tile with its brief, palette, references, candidates and judge scores. Reads memory
and caches only: no model calls, no lab changes. Works for proposed and applied runs and for
`no_experiment`.

### `demo`

```bash
uv run funnel-growth demo synthesize <run_id>   # recording from an existing run, no API calls
uv run funnel-growth demo --open                # replay the newest recording at 127.0.0.1:8765
uv run funnel-growth demo record                # real propose + apply, recorded for replay
uv run funnel-growth demo --live --open         # run for real inside the console
```

The console shows the live landing (`npm run dev` in the lab) in an iframe on the left and the
agent's run as cards on the right: the tool trace, last week's evidence, the ranked creatives,
the current landing, research and style reads, then the proposal with its copy diff and tile
briefs. **Apply** animates the safety checklist, then every showcase tile arrives with its
candidates, the five judge scores fill in, the pick turns lime, and the iframe switches to
`/pm/<variant>`. Keys: `R` run, `A` apply, `F` fullscreen; `?autostart=1&autoapply=1` for
rehearsal, the reset button to loop.

Replay is the stage path: a recording (`data/media_cache/../demo/<run_id>.events.jsonl` under
the data dir) plays with fixed pacing, so no model, no network and no encoding happens during
the talk. `synthesize` builds one from any run already in memory using the caches on disk;
`record` captures a real run as it happens. `--live` runs the real thing with the same page.
The server only serves files the recording itself names (creative jpgs, reference tiles,
candidates), never arbitrary paths. Preflight is printed at start: dev server reachable,
variant folder present, every referenced file on disk.

### `evaluate`

```bash
uv run funnel-growth evaluate <run_id>
```

Once the variant appears in a new growth-loop report, compares its landing→CTA
rate against the proxy baseline stored at propose time. The result is
directional only (`directionally_better`, `directionally_worse`, `flat`, or
`insufficient_data`) and is written back to run memory as a learning.

### `quality-gate`

```bash
uv run funnel-growth quality-gate
uv run funnel-growth quality-gate --snapshots path/to/snapshots.json
```

Lists report snapshots for scoring `propose` across distinct weeks. Without an
Anthropic key it only lists snapshots.

## Development

```bash
uv run pytest          # no network, no ffmpeg: media tools, judge and browsers are faked
uv run ruff check .
uv run ruff format .
```

Tests use fixtures from `tests/conftest.py` and fakes from `tests/redesign_helpers.py`
and never call Claude, Gemini, glam.ai, yt-dlp or ffmpeg. Two integration tests run against
the real pricing-lab landing when the checkout is present, still with faked tools.

## Project layout

```
src/funnel_growth_agent/
├── cli.py               # Typer entry point (status / propose / tiles / show / apply / evaluate / demo / quality-gate)
├── config.py            # .env loading, sibling repo paths, base version resolution
├── agent.py             # Claude tool loop (read-only tools, 12-call cap)
├── proposal.py          # Builds and saves the proposal envelope
├── apply.py             # Transactional variant creation: media, patch, allowlist, validate
├── landing.py           # Structured landing summary and section id resolution
├── landing_diff.py      # Hard-diff allowlist (identity-aware section diff, file rules)
├── landing_patch.py     # Writes copy, layout, composition and media into landing.yaml
├── media.py             # yt-dlp / ffmpeg / cwebp pipeline, image clients, candidates + judge
├── tile_prompt.py       # House-style prompt builder for showcase tiles (pure)
├── tile_sheet.py        # Contact sheet HTML for `funnel-growth tiles`
├── page_style.py        # Shared dark palette and CSS for every HTML page
├── proposal_page.py     # `funnel-growth show`: one page per run
├── events.py            # Structured event kinds emitted by propose and apply
├── demo/                # Console: events.py (recorder), synthesize.py, replay.py, server.py, console.html
├── showcase_style.py    # get_showcase_style: Gemini read of the real tiles, cached
├── research.py          # research_landing: browse screenshots + Gemini pattern read
├── sources.py           # Ready clip sources offered to the model
├── youtube_catalog.json # Curated Recraft YouTube clips the hero may use
├── evaluate.py          # Directional post-traffic evaluation
├── metrics.py           # Reads landing→CTA slices from growth-loop reports
├── creatives.py         # Ranks comparable-traffic creatives
├── gemini.py            # Describes creatives with Gemini, cached on disk
├── memory.py            # JSONL run memory
├── models.py            # Pydantic schemas (hero_copy and landing_redesign changes)
├── instructions.md      # System prompt for the proposal agent
└── playbook.md          # CRO playbook appended to the system prompt
data/
├── memory/runs.jsonl          # run history (gitignored)
├── creative_analysis/*.json   # Gemini cache (gitignored)
├── media_cache/               # youtube/ sources, encoded/ clips, tiles/<stem>.* candidates + judge, sheets/, pages/ (gitignored)
├── demo/<run_id>.events.jsonl # console recordings (gitignored)
├── research/                  # reference-landing and showcase-style reads (gitignored)
└── tmp/                       # apply's temp copies, same volume as the lab (gitignored)
```

## Troubleshooting

- **"Cannot resolve base version"**: `PRICING_LAB_DIR` does not point at a checkout
  with `funnels/site.yaml`. Fix the path or set `BASE_VERSION` explicitly.
- **Stale report error**: the newest `report_data.json` is older than
  `MAX_REPORT_AGE_HOURS`. Re-run growth-loop or raise the limit.
- **"stale base hash"** on apply: the base landing YAML changed after the proposal.
  Run `propose` again.
- **"is applied as v8_a3; a run is applied once"**: propose again for another variant.
- **`funnel:validate` failed**: the pricing lab needs `npm install` and a working
  `funnel:validate` script.
- **"media: no image backend"**: the proposal asks for showcase tiles. Set `GEMINI_API_KEY`
  (or `GLAM_API_KEY`) or propose again.
- **"media: yt-dlp ... failed"**: the clip could not be downloaded. Nothing in the lab
  changed; the proposal stays `proposed`. Check `node` is on `PATH` and retry; warm the cache
  before a demo by applying once.
- **"is not in the catalog"** / **"is not among this proposal's ranked creatives"**: the model
  named a clip, creative or tile it was never offered. Propose again.
- **The variant 404s at `/pm/<variant>`**: restart `npm run dev`; Vite globs version folders at
  startup.
- **A tile looks wrong**: open the sheet in `data/media_cache/sheets/`, then `tiles <run_id>
  --refresh` for new candidates, or propose again with a different brief.
