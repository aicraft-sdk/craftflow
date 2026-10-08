# Router policy map

Maintainer ledger for the router context cut (SPEC-0034, ADR-0073). Not loaded at runtime.
`craftflow_rule_inventory.py --diff` parses the four tables below and fails closed when a header differs.
Every removed baseline rule occurrence must be accounted for here, or still be present in a runtime file
that every workflow and variant that read it at `BASELINE_SHA` still reads.

Reviewer values: `doubt-verifier@wf:<id>`, `plan-gap-reviewer@wf:<id>` or `David`.

## Policy-mapped deletions

Status is one of `deleted`, `pending-prose-deletion`, `prose_retained`, `document-only`. Only `deleted` rows
account for a removed occurrence. The script and the test named in a row must exist.

| Baseline ID or group (file:start-end@sha) | Policy key | Enforcing script::function | Test | Status | live_verified |
|---|---|---|---|---|---|

## Rewrites

Kind is one of `pointer`, `dedupe`, `trigger`, `literal-to-key`, `xref`, `split`, `heading`. A `heading` row maps
an old heading text (first cell) to the new one (second cell). Every other row names replacement ids that exist in
the current inventory. A `dedupe` row is only valid when a kept copy (under any heading, in any file) is read by every workflow and
variant that read the removed copy.

| Baseline ID(s) or group | New IDs | Kind (pointer/dedupe/trigger/literal-to-key/xref/split/heading) | Justification | Equivalence note | Reviewed by |
|---|---|---|---|---|---|

## Applicability

A row narrows where a rule must still be readable, and only for the occurrences it names: a `file#heading` section
row narrows the occurrences under that heading in that file, an id row narrows every occurrence of that id, and two
rows may not narrow the same occurrence. It must quote the rule's own scope words, and the quote must appear in the
first 100 normalized characters of a named rule (its inventory excerpt); a quote found nowhere in the named rule is
rejected.

| Baseline ID or section (file#heading@sha) | Baseline coverage | Narrowed coverage | Quoted scope words | Reviewed by |
|---|---|---|---|---|

## Rationale

Non-normative units moved verbatim to `router-rationale.md`. A normative unit can never be listed here.
Only a unit whose every occurrence is in one of the eight files read at workflow start (the kernel and
start-class files, the L3 basis) may be listed: a stage-class rule is never moved out of the runtime corpus this way.

| Baseline ID | Destination | Justification |
|---|---|---|

## Worktree finalize equivalence

Empty until the worktree-finalize phase (P7/P8) records its step-by-step equivalence.
