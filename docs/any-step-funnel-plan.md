# Any-step funnel improvement demo

## Summary

Build this workflow:

**Paste a URL → open or replicate the page → select a funnel step → describe a goal → review improvements → publish a new funnel version.**

Use `funnel-growth-agent` for the workspace, `recraft-pricing-performance` for funnel rendering and publishing, and `growth-loop` for PostHog analytics.

## Workspace and user flow

- Make the workspace dark: charcoal background, raised dark panels, light text, subtle borders and blue primary actions. Remove the Recraft header logo. Keep Instrument Sans and the existing chat/preview layout.
- Replace “Starting page” with a URL field plus a library picker. Known URLs open their existing funnel and select the matching step.
- Add a collapsible step navigator between chat and preview. Show every step in manifest order, its name, available conversion evidence and whether it has draft changes. On mobile, use a drawer.
- Scope chat, goals, research, editing and comparison to the selected step. Preserve each step’s unsent message and editor state when switching.
- Retain Light, Medium and Heavy changes. Heavy produces three directions compatible with the selected step’s behavior.
- Let users improve several steps sequentially, review the combined funnel and choose **Publish new funnel**. The existing default funnel remains unchanged.

The dark theme applies to the workspace; funnel previews retain their own designs.

## Importing and editing pages

- Match URLs against configured application origins, version routes and saved library URLs. Preserve routing parameters while removing known tracking parameters. For shared routes such as workspace/modal paywalls, offer the matching steps.
- For an unknown public URL, use gstack to capture desktop/mobile appearance, content and assets. Save an editable structured replica with source provenance and a source-versus-replica comparison.
- Keep the initial replica in the library with inactive controls. **Replace step** adapts it to a selected step in an existing funnel; it does not infer an entire funnel from one URL.
- Add an optional versioned `StepDesign` document to renderer step definitions. Support validated responsive containers, text, media, cards, buttons and native interaction slots. Captured scripts and arbitrary generated code are excluded.
- Give every existing step type an adapter. Reuse its native controller for quiz answers, upload validation, workspace interactions, progression and checkout. Existing rendering remains the fallback when no design is supplied.
- Preserve step IDs, routes, answer identities, navigation and event semantics. Paywall designs use the target funnel’s actual product catalog, prices, billing periods and checkout handler. Imported prices cannot replace the real offer.
- Keep mandatory interactions present exactly once. Unresolved bindings remain editable but cannot become a ready funnel revision.
- Provide isolated previews for every step, including explicitly opened modals, frozen loaders and synthetic success states. Previewing must not initiate payments, track conversions or redirect away.

## Backend, analytics and research

- Extend the catalog with step identities, routes, capabilities and whole-funnel hashes. Add URL resolution/import APIs and persist imported pages in the local library.
- Extend draft creation, messages and revision requests with an explicit target step. Keep one global revision history: every ready revision contains all accepted step changes. Retain stale-revision checks, durable retries and the last successful preview.
- Load existing drafts as landing-only drafts; preserve existing API callers and CLI workflows.
- Add a read-only `growth-loop funnel-metrics` command using the existing PostHog credentials. Return aggregate evidence for an exact funnel, version and selected step; persist each response with the relevant revision.
- Default to seven fully observed UTC entry days and a 24-hour conversion window. Deduplicate tracked visitors and enforce event order. Paywall evidence measures **view → checkout → payment-success event**; other steps default to completion after viewing.
- Show counts, dates, cohort, freshness and limitations. Payment-success telemetry is distinct from confirmed revenue. Failed refreshes use a saved exact snapshot first; existing mixed-version or Meta-only reports remain explicitly labeled fallback evidence.
- Continue editing when analytics are unavailable. Support free-text goals alongside the selected measurable objective.
- Make competitor research step-aware: pricing/paywall references for payment goals, onboarding references for quiz progression. Keep citations and observed patterns attached to proposals without inventing competitor conversion performance.

## Publishing and verification

Reuse the existing whole-funnel build and publication pipeline. Allow changes only to selected step documents and declared assets, plus required new-version registration and analytics identities. Verify the reviewed artifact includes every accepted edit.

Ship shared renderer capabilities before enabling publication of designs that require them; incompatible deployment branches receive a clear capability error.

Acceptance checks:

- Known landing/paywall URLs, ambiguous modal routes, duplicate imports and failed public-page captures.
- Editable imported replica attached to a quiz or paywall with native behavior preserved.
- Edit step A, then B; both survive restart and publication. A failed B revision preserves the prior ready funnel.
- Prices, checkout payloads, answer identities and unrelated step content remain protected.
- Correct analytics ordering, version filtering, duplicate handling, conversion windows, zero/missing data and refresh failures.
- Legacy drafts continue working; desktop/mobile dark UI remains readable and keyboard accessible.
- Full Python checks and renderer validation, tests, lint and build pass.
- Browser demo completes: paste a paywall URL, inspect PostHog evidence, improve it, improve another step, review the complete funnel and verify a newly published version through the explicit publish action.

## Implementation handoff

- Current-work PR: https://github.com/Kakoedlinnoeslovo/funnel-growth-agent/pull/5
- Starting commit: `fb1240c84bcbb5b4046acb51f4cc53f3f9e42055`
- Implementation branch: `codex/any-step-funnel`
- Worktree: `/Users/roman/Desktop/all/funnel-growth-agent/.worktrees/any-step-funnel`
- The PR targets `main` and remains open. This branch starts from its final commit.
- The agreed implementation plan above is preserved unchanged. No any-step or dark-theme implementation has started.
