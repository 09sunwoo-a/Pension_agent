---
name: aidlc-inception-mockup
description: Use when the user wants to turn an idea into a plan + a non-functional UI mockup and a report using the inception-only AIDLC workflow (no runnable code). Drives Requirements → mockup → report with a forward-only feedback loop.
---

# AIDLC Inception-Only Mockup

This skill drives the inception-only AIDLC fork. It loads the controller and runs the workflow to a non-functional HTML mockup + final report.

## How to run

1. Load `aidlc-rules/aws-aidlc-rules/core-workflow.md` and follow it exactly.
2. Workspace Detection auto-detects brownfield (existing code → Reverse Engineering runs).
3. Inception proceeds through ONE batched question gate per stage (`common/batched-questions.md`): ask everything genuinely needed; never proceed on blocking ambiguity.
4. ALWAYS terminal stages: UI Mockup (`common/ui-mockup.md`) → Report (`common/final-report.md`).
5. Mockup feedback is handled forward-only via `common/feedback-loop.md`.

## Guarantees

- The mockup is non-functional (dead buttons, mocked data, single `aidlc-docs/mockup/index.html`).
- Screens are stamped (`data-screen-id`/`data-story`/`data-requirement`, `aidlc-mockup-stamp` marker; canonical FR-NNN/NFR-NNN/US-NNN).
- Full intent/dialogue provenance is preserved in `audit.md` and distilled into the report.
- Domain-agnostic: no product-domain bias baked in (the customer adds steering separately).
- All written artifacts are in English.
