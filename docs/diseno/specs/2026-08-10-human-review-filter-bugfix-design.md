# Human Review Filter Bugfix Design

**Date:** 2026-08-10

**Repository:** `C:\Users\OMAR\Desktop\tesis\New project`

## Scope

Repair the Human Review queue filter crash and add an explicit, keyboard-accessible path from each queue row to the existing audited case-detail workflow. The backend, OpenAPI contract, date semantics, decision commands, migrations, and B2B.3 remain unchanged.

## Root cause

Both multiselect handlers read `event.currentTarget.selectedOptions` inside a functional `setForm` updater. React may execute that updater after the handler returns, when `currentTarget` has been cleared. Selecting status and then case type reproduced `TypeError: Cannot read properties of null (reading 'selectedOptions')` before the combined request was sent.

## Approved design

- Read and type the selected option values synchronously in each event handler.
- Pass only the captured array into `setForm(previous => ...)`; never retain or dereference the event from the updater.
- Preserve the installed repeated-parameter API contract: `status`, `case_type`, and public `document_id`.
- Pass queue loading state to the filter form and disable only the Apply submit while a list request is active.
- Add an `Acciones` table column containing a normal same-tab link labeled `Revisar caso` to `/human-review/cases/{id}`.
- Preserve local safe API error rendering and the existing `DecisionComposer` workflow.
- Keep `created_from` and `created_to` empty when no filter exists; add regression evidence without changing their implementation.

## Verification

TDD must first reproduce the deferred-event failure in real React through Playwright. Verification then covers single and multiple status/type filters, combined filters, reset, empty submission, exact query parameters, local structured errors, initial empty dates, loading disablement, navigation to case detail, visible DecisionComposer, and preview/cancel without scientific writes. Finish with the complete frontend suite, TypeScript, production build, live desktop/mobile QA, OpenAPI 8/8, migration integrity, and cleanup.

