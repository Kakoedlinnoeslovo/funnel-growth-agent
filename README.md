# funnel-growth-agent

Proposes and applies landing-page CTA experiments for the Recraft pricing lab.

The agent reads the latest growth-loop report, looks at the current landing page
and the top-performing ad creatives, and asks Claude to propose one hero-copy
experiment (or to decline). A separate `apply` step copies the base funnel into a
new variant with only the allowed text fields changed. Nothing is written to the
pricing lab until you explicitly run `apply`.

## Prerequisites

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- Two sibling repos checked out next to this one (paths are configurable):
  - `../growth-loop` — produces `data/output/reports/*/report_data.json`
  - `../recraft-pricing-performance` — holds `funnels/site.yaml` and `funnels/<version>/`
- Node in the pricing-lab repo, because `apply` runs `npm run funnel:validate` there
- An Anthropic API key (required for `propose`)
- A Gemini API key (optional; without it creatives are described from ad copy only)

Expected layout:

```
all/
├── funnel-growth-agent/            # this repo
├── growth-loop/
└── recraft-pricing-performance/
```

## Setup

```bash
cd funnel-growth-agent
uv sync --extra dev
cp .env.example .env
```

Edit `.env`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `GROWTH_LOOP_DIR` | `../growth-loop` | Where weekly/daily reports are read from |
| `PRICING_LAB_DIR` | `../recraft-pricing-performance` | Funnel YAML that `apply` writes into |
| `ANTHROPIC_API_KEY` | — | Required for `propose` |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Model used for the proposal loop |
| `GEMINI_API_KEY` | — | Optional; enables visual creative analysis |
| `GEMINI_MODEL` | `gemini-3.6-flash` | Model used to describe creatives |
| `BASE_VERSION` | `default_version` from `funnels/site.yaml` | Override the base funnel version |
| `MAX_REPORT_AGE_HOURS` | `48` | Reports older than this are rejected as stale |
| `MIN_LANDING_PEOPLE` | `100` | Below this, metrics count as insufficient data |
| `MIN_CREATIVE_SPEND` | `5` | Minimum spend for a creative to be ranked |
| `MIN_CREATIVE_CLICKS` | `10` | Minimum clicks for a creative to be ranked |
| `FUNNEL_GROWTH_DATA_DIR` | `./data` | Where run memory and creative cache live |

## Run

All commands go through the `funnel-growth` CLI:

```bash
uv run funnel-growth --help
```

### 1. Check state

```bash
uv run funnel-growth status
```

Shows the base version, existing variants, the latest proposal, and the latest
applied variant with its evaluation.

### 2. Propose an experiment

```bash
uv run funnel-growth propose
uv run funnel-growth propose --refresh-creatives   # ignore the Gemini cache
```

Reads the freshest weekly report, ranks comparable-traffic creatives, describes
them with Gemini (cached in `data/creative_analysis/`), then runs a read-only
Claude tool loop capped at 8 calls. The result is appended to
`data/memory/runs.jsonl` with a run id like `2026-09-20T2104-v7-001`. No funnel
files are modified.

The output is either a full proposal (problem, baseline, hypothesis, changes,
alternative ideas) or `no_experiment` with a reason.

### 3. Apply a proposal

```bash
uv run funnel-growth apply <run_id>
```

Creates the next free variant directory (`v7_a1`, `v7_a2`, …) in the pricing lab:

- Copies the base funnel into a temp directory and patches only headline,
  subhead, CTA label, and reassurance in the hero, video-cta, and final-cta sections.
- Refuses if the base landing page changed since the proposal was made.
- Runs a hard-diff allowlist and `npm run funnel:validate` twice, then publishes atomically.
- Appends the variant to `published_versions` in `site.yaml`. `default_version` is never changed.

On success it prints a preview URL of the form `http://localhost:5173/pm/<variant>`.

### 4. Evaluate after traffic

```bash
uv run funnel-growth evaluate <run_id>
```

Once the variant appears in a new growth-loop report, compares its landing→CTA
rate against the proxy baseline stored at propose time. The result is
directional only (`directionally_better`, `directionally_worse`, `flat`, or
`insufficient_data`) and is written back to run memory as a learning.

### Quality gate

```bash
uv run funnel-growth quality-gate
uv run funnel-growth quality-gate --snapshots path/to/snapshots.json
```

Lists report snapshots for scoring `propose` across distinct weeks. Without an
Anthropic key it only lists snapshots.

## Development

```bash
uv run pytest          # 42 tests, no network needed
uv run ruff check .
uv run ruff format .
```

Tests use fixtures from `tests/conftest.py` and never call Claude or Gemini.

## Project layout

```
src/funnel_growth_agent/
├── cli.py            # Typer entry point (propose / status / apply / evaluate / quality-gate)
├── config.py         # .env loading, sibling repo paths, base version resolution
├── agent.py          # Claude tool loop (read-only tools, 8-call cap)
├── proposal.py       # Builds and saves the proposal envelope
├── apply.py          # Transactional variant creation with allowlist + validation
├── evaluate.py       # Directional post-traffic evaluation
├── metrics.py        # Reads landing→CTA slices from growth-loop reports
├── creatives.py      # Ranks comparable-traffic creatives
├── gemini.py         # Describes creatives with Gemini, cached on disk
├── memory.py         # JSONL run memory
├── models.py         # Pydantic schemas
├── instructions.md   # System prompt for the proposal agent
└── playbook.md       # CRO playbook appended to the system prompt
data/
├── memory/runs.jsonl          # run history (gitignored)
└── creative_analysis/*.json   # Gemini cache (gitignored)
```

## Troubleshooting

- **"Cannot resolve base version"**: `PRICING_LAB_DIR` does not point at a checkout
  with `funnels/site.yaml`. Fix the path or set `BASE_VERSION` explicitly.
- **Stale report error**: the newest `report_data.json` is older than
  `MAX_REPORT_AGE_HOURS`. Re-run growth-loop or raise the limit.
- **"stale base hash"** on apply: the base landing YAML changed after the proposal.
  Run `propose` again.
- **`funnel:validate` failed**: the pricing lab needs `npm install` and a working
  `funnel:validate` script.
