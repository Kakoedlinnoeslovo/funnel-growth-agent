# Chat workspace: implementation and acceptance

The live workspace now has two permanent areas: a resizable conversation on the left and
landing results on the right. Setup uses the same layout. Research, video evidence, change
explanations, technical activity and the page editor expand inside chat; they do not replace
the preview or open another inspector.

## Interaction

- Questions discuss the saved page and evidence. Clear edit requests enter the existing
  validated generation/revision workflow. Ambiguity gets a clarification. Publishing remains
  a dedicated explicit action.
- The composer keeps Light / Medium / Heavy nearby and preserves unsent text during work.
  Ordinary answers are concise; longer answers have a Read full reply disclosure. A pending
  submission uses a stable request ID, including retries after a lost HTTP response.
- Each operation has one progress/result card. Research expands to individual sources;
  repeated provider errors are grouped and raw diagnostics live in Activity. Video players,
  storyboard images and research captures mount on expansion. Input thumbnails stay compact.
- New evidence is saved per operation. Old cards retain their source observations, analyses
  and revision association. Legacy drafts show explicitly labeled saved activity; no past
  dialogue is fabricated.
- The preview includes baseline comparison, desktop/mobile sizing, revision selection and
  fullscreen. Select Latest before sending new instructions while inspecting a historical
  revision. Edit page opens the existing section editor in chat.
- Below 900px, Chat / Preview tabs preserve the composer and their scrolling surfaces. New
  results use an indicator rather than switching tabs. The desktop divider supports arrows,
  Home and End; Escape dismisses the saved-draft menu and restores focus.

## API and state

`POST /api/drafts/{id}/messages` accepts:

```json
{
  "text": "Simplify the hero while keeping the editable-vector promise.",
  "requestId": "client-generated-unique-id",
  "expectedRevision": 2,
  "changeLevel": "light"
}
```

The endpoint responds with HTTP 202 and a message ID/status. Duplicate request IDs acknowledge
the same turn; changed payloads with a reused ID are rejected. To retry the latest failed
assistant reply, resend the original payload with `retryFailed: true`. Stale revisions are
rejected before contacting the provider. A newer conversation requires a new message instead
of rewriting an older failed turn.

Draft snapshots now include `conversation` (messages, agreed brief, status), `operationResults`
(immutable evidence/result snapshots), and optional `legacyEvidence`. Messages have stable IDs,
roles, timestamps, terminal status and optional operation references. Existing generation,
research, reanalysis, structured editing and publication endpoints remain compatible.

Conversation uses the configured Anthropic model with a validated answer/clarify/edit result.
An edit transfers the existing job lock to the normal worker, retaining all landing, change-level
and publishing checks. A failed or interrupted reply changes conversation status only; it does
not invalidate a ready landing. Interrupted edits retain a terminal operation card and the
existing operation retry path. SSE uses the same durable sequence cursor; snapshots recover
completed replies and results after reconnects.

## Acceptance

- **367 Python tests passed**, including 15 conversation cases covering routing, agreed context,
  change levels, idempotency, conflicts, stale revisions, provider failure/retry, restart,
  publication restrictions, evidence history, HTTP and SSE. Ruff and JavaScript syntax checks
  also passed.
- Browser acceptance used gstack `/browse` with isolated test drafts, scripted model responses,
  real pricing-lab builds, mocked external requests and the preview CSP. A question left revision
  3 unchanged; a Light request produced revision 4. A prompt-only Heavy request produced three
  directions, selection built revision 1, and the inline editor produced revision 2.
- Research expanded inline, grouped two matching provider failures into one notice, and kept
  the existing preview element and URL. The original uploaded video sought to **16.474 seconds**
  of **41.284 seconds**; a new chat reply preserved the player, seek position and open disclosure.
  Video interpretation in this fixture remains explicitly unavailable, as previously recorded.
- At **1440px, 1024px and 390px**, inspected layouts had no horizontal overflow. Comparison,
  mobile preview, fullscreen, keyboard resizing, menu focus restoration, revision selection,
  and preserving typed text while building/switching tabs were checked.
- One real request to the configured Claude provider returned **answer** with no edit instruction
  for a question asking to discuss improvements before changing anything. Generation acceptance
  used scripted proposals; this does not claim new live Gemini video/image acceptance.

Screenshots are saved under `data/acceptance/screenshots/`, including
`growth-chat-ready-desktop.png`, `growth-chat-inline-research.png`,
`growth-chat-inline-video.png`, `growth-chat-inline-editor.png`, `growth-chat-mobile.png`, and
`growth-chat-mobile-preview.png`. Research rows labeled “Research fixture” are synthetic
acceptance data, not new competitor findings. Nothing was published.
