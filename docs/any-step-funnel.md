# Any-step funnel workspace

The agreed scope is preserved in [any-step-funnel-plan.md](any-step-funnel-plan.md).
The existing landing-workspace PR remains open: https://github.com/Kakoedlinnoeslovo/funnel-growth-agent/pull/5.

## Local setup

Three isolated checkouts hold this implementation:

| Repository | Worktree | Branch |
| --- | --- | --- |
| funnel-growth-agent | `.worktrees/any-step-funnel` | `codex/any-step-funnel` |
| recraft-pricing-performance | `.worktrees/any-step-renderer` | `codex/any-step-funnel` |
| growth-loop | `.worktrees/any-step-analytics` | `codex/any-step-funnel` |

All paths above are relative to the original funnel-growth-agent checkout. The renderer and
analytics worktrees include the existing local prerequisites from their original checkouts;
the originals have not been switched or edited. Install dependencies normally in each checkout.
For this local session the renderer reuses its original installed dependencies via a symlink.

Set `PRICING_LAB_DIR` to the renderer worktree, `GROWTH_LOOP_DIR` to the original growth-loop
checkout containing reports and PostHog configuration, and `GROWTH_LOOP_METRICS_DIR` to the
analytics worktree. Set `FUNNEL_GROWTH_DATA_DIR` to a separate local data directory.
Provider credentials are read from configuration and never copied into preview snapshots.
The metrics subprocess receives only the existing PostHog environment configuration, and uses
the selected metrics checkout's Python source. It performs read-only Query API calls.

Start the renderer with `npm run dev -- --host 127.0.0.1 --port 5187`. Start the workspace with
`uv run funnel-growth demo --live --preview-base http://127.0.0.1:5187/pm --port 8817 --open`.
When using an existing environment from another checkout, set `PYTHONPATH=src` so editable
package installation paths cannot silently run the other checkout.

## User flow

1. Paste a known page URL, or choose a library funnel. Shared workspace/paywall routes offer
   the matching steps. Tracking parameters are removed for URL deduplication; other parameters
   remain part of a saved library URL.
2. Select a step in manifest order. The navigator shows accepted edits and saved visitor counts.
   Chat and unsent text are scoped to that step. One global revision prevents stale changes.
3. Refresh exact PostHog evidence or research the selected step. Paywall research uses public
   pricing references; other steps use the goal and selected component to rank observations.
   Missing/failed observations remain labeled. Editing does not depend on analytics availability.
4. Ask a question, request Light/Medium/Heavy changes, or edit supported copy directly.
   Heavy builds three compatible directions. A manual edit does not invoke a proposal model.
5. Repeat for another step. Each revision copies the last accepted whole funnel before applying
   the selected change. A failed revision retains the last ready preview.
6. Review the combined funnel and choose **Publish new funnel**. GitHub publication first builds
   against the deployment branch for review. It checks shared renderer capabilities before any
   push; deploy that dependency separately before publishing a design requiring it.

Unknown public URLs are captured through gstack at desktop/mobile sizes. Supported source
images, provenance and captures are saved with a schema-validated `StepDesign` replica. No
captured HTML, scripts, third-party handlers or executable generated code are installed.
The library copy editor operates on inert data. Attaching a replica transfers composition and
media, populates it with target copy, and retains exactly one native interaction where required.
The replica is an approximation within the supported containers, typography and block types;
content behind interactions is not inferred. Capture/model failures are persisted and retryable.

## Contracts and API additions

- `GET /api/catalog`: each baseline retains its legacy landing `hash` and adds `funnelHash`
  and ordered `steps`; also returns saved library pages.
- `POST /api/drafts`: supplying `stepId` opts into schema v5 whole-funnel drafts and requires
  `baseHash=funnelHash`. Omitting it retains legacy landing-only behavior.
- `POST /api/resolve-url`, `/api/import-url`; `GET /api/library`; `POST /api/library/<id>`.
- `POST /api/drafts/<id>/select_step`, `/refresh_metrics`, `/replace_step` require the selected
  `stepId` and global `expectedRevision`. Messages and step revisions carry the same binding.
- Step revisions accept an instruction, a whitelisted `copyPatch`, or a typed `design`.
  Existing IDs, routes, answer identities, native gates, catalog prices and checkout handlers
  remain outside the editable surface. Other step files are checked byte-for-byte.
- Renderer YAML gains optional top-level `design`. Its strict schema rejects unknown fields,
  external media URLs, duplicate block IDs and missing/duplicated mandatory interactions.
  All existing components retain their controllers. Quiz and paywall adapters remove old page
  chrome; studio adapters retain functional toolbars. Without `design`, existing rendering runs.
- Preview servers inject the preview marker into served HTML only. Production query parameters
  cannot enable it. Preview checkout, analytics initialization, automatic loaders, success handoff,
  forms and external anchor navigation are disabled. Prices come from the actual public product
  catalog or a captured copy; unavailable catalogs show an error rather than invented prices.

## Exact evidence

```bash
growth-loop funnel-metrics --funnel-id recraft-quiz-v8 --version v8 --node-id plan --role paywall --json
```

The default cohort is seven complete UTC entry days, followed by a complete 24-hour conversion
window. It uses the first eligible selected-step view per distinct tracked visitor, then ordered
completion/checkout/payment events. A payment-success event can occur on the success node;
when checkout and payment both include a Stripe session ID, they must match. Known preview,
development and test visitors are excluded. Daily queries reject truncation instead of returning
partial rates. Zero denominators return null rates.

Evidence includes dates, cohort, freshness, counts, exclusions and limitations. Payment-success
telemetry is not confirmed revenue. Failed refreshes preserve a saved exact snapshot and label
it stale. Weekly mixed-version/Meta evidence is not presented as selected-step conversion.
Each accepted revision snapshots its evidence and competitor observations.

## Verification

Automated coverage includes cumulative A→B edits and publication, restart recovery, failed builds,
stale requests, protected prices/answer IDs, mandatory native slots, Heavy choices, import
failure/deduplication, concurrent YAML reads, exact analytics ordering and preview guards.
The renderer schema generation, validation, TypeScript build and tests are run separately.

Live acceptance must distinguish local verification from external-provider and production work.
The development session verified a real v8 paywall URL, real PostHog aggregates and a locally
built manual paywall revision. External model generation requires approval of the transfer of
funnel content and private analytics aggregates. The shared renderer capability must be shipped
on the deployment branch before the new designs can be published there.
