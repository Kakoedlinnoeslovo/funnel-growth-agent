# Growth workspace

The workspace lets an operator turn selected creative evidence into one reviewable landing
version. Keep the creative, the resulting page, and the actions that produced it easy to inspect.

[Editable Figma reference](https://www.figma.com/design/v8YCkFn11l7u8lGx0MPGjE).
The shipped UI lives in `src/funnel_growth_agent/demo/workflow.html`, `workflow.css`, and
`workflow.js`. The CSS is the source of truth for current tokens; Figma is a design reference,
not a required runtime dependency or an automatic code-generation pipeline.

Reference patterns: [Flowbite blocks](https://flowbite.com/blocks/) for controls and timelines,
and [shadcn blocks](https://ui.shadcn.com/blocks) for landing composition. The implementation
uses native HTML/CSS/JavaScript and a small capability-checked preset library; it adds no
React migration or paid template dependency.

## Visual system

Use a light, editorial workspace: pale green paper, white surfaces, dark ink, subtle borders,
and lime primary actions. Creative assets and landing previews carry the strongest imagery.
Avoid decorative gradients, oversized status banners, and repeated card shells inside cards.

| Token | Value | Use |
| --- | --- | --- |
| `--paper` | `#f5f6f2` | Workspace background |
| `--surface` | `#ffffff` | Panels and controls |
| `--ink` | `#20231e` | Primary text |
| `--muted` | `#63695f` | Secondary text |
| `--line` | `#e0e4d9` | Borders and dividers |
| `--lime` | `#c6f135` | Main action and selected creative |
| `--soft` | `#eff2e8` | Quiet inset surfaces |
| `--success` | `#31734a` | Ready/completed states |
| `--warning` | `#815614` | Limited evidence |
| `--danger` | `#a33d34` | Failed operations |

Instrument Sans is bundled locally as a variable WOFF2 font, with its OFL license alongside
the file; system sans-serif fonts provide fallback. Body text is 14px with 1.55–1.65 line height, section headings
14–18px, primary headings 26–27px, supporting text 12px, and compact labels 10–11px. Use weight
600 for emphasis and tabular numbers for counts. Controls have 8px corners; main panels use
12px corners, 1px borders, and roughly 18–24px spacing. Reserve shadows for raised surfaces.

## Layout and interaction

- **One workspace:** chat on the left, results on the right, from baseline selection through
  publication. No separate setup page or permanent inspector. Saved drafts use a header menu.
- **Conversation:** initially 400px wide, resizable from 320–560px while preserving preview
  space. One scrolling conversation and an anchored composer; avoid nested scrolling cards.
  The composer includes the selected change level and optional initial creative/reference inputs.
- **Evidence:** operation results have compact, closed Research, Video understanding, What
  changed and Activity cards. Expand them in place. Research can expand individual sources;
  keep disclosures to at most two levels. Long lists use Show more. Long assistant replies have
  a Read full reply control; full content remains available.
- **Results:** Draft/Baseline/Compare, Desktop/Mobile, revision selection and fullscreen live
  together. Heavy directions occupy this same results area while awaiting a choice. The last
  successful preview stays available during work and after failures.
- **Editing:** Edit page opens a section editor card in chat, preserving the existing copy,
  layout, composition, media and preset capabilities. Older revisions are preview-only; select
  Latest before sending a new message so the edited page is unambiguous.
- **Responsive:** below 900px, Chat/Preview tabs replace the split. Neither tab resets its
  scroll position or composer. A new-result indicator replaces automatic tab switching.
- **Accessibility:** labeled native disclosures and controls, visible focus rings, keyboard
  resizing, keyboard menu dismissal and reduced-motion support. Preserve DOM identity for
  expanded evidence and video players during streamed updates.

## Activity and evidence contract

`Workflow` persists each event with a sequence number, operation ID, revision, stage, kind,
timestamp, and payload. `GET /api/drafts/{id}/events` streams these events using SSE; the
cursor/Last-Event-ID resumes delivery without replaying completed cards. The snapshot endpoint
remains available, and legacy saved events receive stable sequence numbers on read.

Show the current stage and elapsed time in one operation card. Detailed execution steps,
real inputs/results and diagnostics remain in its Activity disclosure. Store terminal results
and evidence per operation; refreshed research must not rewrite older cards. Group repeated
provider failures into a concise explanation naming affected sources. Never animate invented reasoning or
completion percentages. Cached observations and unavailable sources need visible labels;
successful generation can still contain unavailable research evidence.

Competitor evidence distinguishes an observed ad destination from a supplied reference URL.
Keep the Library link, ad identity, advertised/resolved URL, CTA boundary, capture time, and
screenshots together. A failed analysis should retain successful observations. Audience data
must distinguish inferred intent, Meta attribution, PostHog events, and warehouse customers;
missing or sparse outcomes cannot support a claim that an audience wins.

## Conversation contract

`POST /api/drafts/{id}/messages` accepts text, requestId, expectedRevision and changeLevel.
The configured Claude model returns an answer, a clarification, or a concrete edit instruction.
Only the edit decision hands off to the existing validated generation worker. Chat cannot
publish or bypass landing constraints. Keep conversation status separate from landing status.
Persist user/assistant messages, agreed brief, operation references and idempotency inputs.
Use resumable SSE to update individual entries. A pending reply interrupted by restart becomes
retryable; it does not invalidate an existing preview. Label legacy saved activity explicitly.

## Revision boundaries

Structured edits merge into the current proposal and rebuild without another proposal-model
request. Freeform instructions request changes relative to the saved baseline; unspecified
earlier changes and media are retained during the merge. Both create isolated revisions with
validation and reusable media. Keep the last
ready preview available during work and after failure. A stale editor revision is rejected.

Publication uses the reviewed artifact and the existing explicit publishing action. New
designs do not change the default public landing. Figma references, competitor pages, and
the research browser are evidence sources; they do not bypass the landing edit contract.
