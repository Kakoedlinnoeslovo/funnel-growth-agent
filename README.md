# funnel-growth-agent

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

One command, everything in the browser. The console opens with the live landing on the left
and the agent on the right. Press **R** to run a real proposal, **A** to apply it.

Keys: `R` run · `A` apply · `F` fullscreen. `?autostart=1&autoapply=1` runs both unattended.

Needs the lab's dev server up for the left pane:

```bash
cd ../recraft-pricing-performance && npm run dev
```

Nothing needs to be run first. Apply produces the hero clip and generates any showcase tiles
itself, so **R** then **A** is the whole demo. Expect 1–3 minutes for propose and 1–2 for
apply. The run is recorded as it goes, so it can be replayed later without touching a model.

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
| `PRICING_LAB_DIR` | `../recraft-pricing-performance` | Funnel YAML that `apply` writes into |
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

## Commands

```bash
uv run funnel-growth status                      # base version, variants, latest run
uv run funnel-growth propose                     # → a run id like 2026-09-20T2328-v8-001
uv run funnel-growth apply <run_id>              # publish the variant into the lab
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

**`evaluate`** compares the variant's landing→CTA rate against the baseline stored at propose
time, once it appears in a new report. Directional only, written back to memory as a learning.

## Demo console

```bash
uv run funnel-growth demo --live --open
```

The landing on the left, the agent on the right. The right pane shows the tool trace, last
week's evidence, the ranked creatives, the current landing, then the proposal with its copy
diff and tile briefs. **A** animates the safety checklist, each tile arrives with its
candidates, the judge's five scores fill in, the pick turns lime, and the iframe switches to
`/pm/<variant>`.

Preflight prints at start: API keys present, dev server reachable, base landing on disk.
Apply is always available in live mode, and the run is written to `data/demo/` as it goes so
it can be replayed later — see Advanced.

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
