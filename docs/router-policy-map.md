# Router policy map

Maintainer ledger for the router context cut (SPEC-0034, ADR-0073). Not loaded at runtime.
`craftflow_rule_inventory.py --diff` parses the five tables below and fails closed when a header differs.
Every removed baseline rule occurrence must be accounted for here, or still be present, under the same heading path
and list label, in a runtime file that every workflow and variant that read it at `BASELINE_SHA` still reads, at least
as early (kernel and start files at workflow start, then happy-path stage files, then conditional stage files such as
remediation and research).

Reviewer values: every row in every table is signed by a person as `Firstname Lastname (YYYY-MM-DD)`, for example
`David Gracia (2026-10-09)`. A bare first name, an empty cell or an agent id is not a signature and counts toward
`rows_missing_review`, which fails the diff.

## Policy-mapped deletions

Status is one of `deleted`, `pending-prose-deletion`, `prose_retained`, `document-only`. Only `deleted` rows
account for a removed occurrence. The script and the test named in a row must exist.

| Baseline ID or group (file:start-end@sha) | Policy key | Enforcing script::function | Test | Status | live_verified | Reviewed by |
|---|---|---|---|---|---|---|

## Rewrites

Kind is one of `pointer`, `dedupe`, `trigger`, `literal-to-key`, `xref`, `split`, `heading`. A `heading` row maps
an old heading text (first cell) to the new one (second cell). Every other row names replacement ids that exist in
the current inventory and must relate them to the old rule: the new units together keep at least 60% of the old
unit's tokens and each new unit is at least 60% old tokens (a verbatim move, a dedupe and a split of one rule pass), or
the row kind is `pointer` and a new unit names the old unit's heading (the rule now lives behind that reference).
A `dedupe` row is only valid when a kept copy (under any heading, in any file) is read by every workflow and
variant that read the removed copy, at least as early.

| Baseline ID(s) or group | New IDs | Kind (pointer/dedupe/trigger/literal-to-key/xref/split/heading) | Justification | Equivalence note | Reviewed by |
|---|---|---|---|---|---|

## Applicability

A row narrows where a rule must still be readable, and only for the occurrences it names: a `file#heading` section
row narrows the occurrences under that heading in that file, an id row narrows every occurrence of that id, and two
rows may not narrow the same occurrence. It must quote the rule's own scope words, and the quote must appear in the
first 100 normalized characters of a named rule (its inventory excerpt); a quote found nowhere in the named rule is
rejected. Coverage cells are `WORKFLOW` or `WORKFLOW:variant` tokens separated by commas; a narrowed token may add
`@happy_path_stage` or `@conditional_stage` to also accept a later reach for that pair (`@kernel` and `@start` are the
default, the baseline reach).

| Baseline ID or section (file#heading@sha) | Baseline coverage | Narrowed coverage | Quoted scope words | Reviewed by |
|---|---|---|---|---|

## Rationale

Non-normative units moved verbatim to `router-rationale.md`. A normative unit can never be listed here.
Only a unit whose every occurrence is in one of the eight files read at workflow start (the kernel and
start-class files, the L3 basis) may be listed: a stage-class rule is never moved out of the runtime corpus this way.

| Baseline ID | Destination | Justification | Reviewed by |
|---|---|---|---|

## Read-map widening

`--diff` compares the live `config/router-readmap.json` with the frozen `tests/fixtures/router-readmap.baseline.json`.
Any file whose live coverage reaches a workflow, variant or earlier reach that the baseline map did not give it is
`readmap_widened`, and fails, unless a row here names that file, states its real baseline coverage (`none` for a file
the baseline map does not list) and the widened coverage that is being allowed (`@kernel` by default; a row may cap the
reach with `@happy_path_stage` or `@conditional_stage`). Without this check a rule moved into a file that the live map
then marks as read everywhere would look reachable.

| File | Baseline coverage | Widened coverage | Justification | Reviewed by |
|---|---|---|---|---|

## Rebaseline

The baseline inventory (`tests/fixtures/router-rule-inventory.baseline.json`), its frozen read map
(`tests/fixtures/router-readmap.baseline.json`) and the commit they describe are pinned by
`tests/fixtures/router-baseline.lock.json`:
`{"baseline_sha": <full 40-hex commit>, "baseline_sha256": <sha256 of the baseline file>, "readmap_baseline_sha256": <sha256 of the frozen read map>}`.
`--diff` and `test_rule_inventory.py::baseline_matches_ref` both fail, never skip, when the baseline's `ref`, either
file's sha256 or the commit's presence in the clone (CI needs `fetch-depth: 0`) differs from the lock. A baseline
re-emitted from an edited tree, or with `ref` null, therefore cannot be diffed against itself.

Change the lock only by a deliberate rebaseline, after a change to unit extraction, normalization or the classifier,
and only against the original commit, never against a tree that has lost rules:

```
# 1. re-derive the allowlist texts when normalization changed (see the header of build_normative_allowlist.py)
python3 tests/fixtures/build_normative_allowlist.py
# 2. re-emit the baseline at the FULL sha (--force needs an explicit --ref and refuses ref null)
python3 scripts/craftflow_rule_inventory.py --emit --force --ref <baseline_sha> \
  --readmap-baseline tests/fixtures/router-readmap.baseline.json \
  --allowlist tests/fixtures/router-normative-allowlist.json --out tests/fixtures/router-rule-inventory.baseline.json
# 3. re-derive the start-file non-normative report and update the L3 numbers in ADR-0073
# 4. write the lock from the regenerated files
shasum -a 256 tests/fixtures/router-rule-inventory.baseline.json tests/fixtures/router-readmap.baseline.json
```

Commit the baseline, the allowlist, the report and the lock together, and have a reviewer confirm that the diff of the
baseline against the previous one shows only the intended shape change.

## Worktree finalize equivalence

Empty until the worktree-finalize phase (P7/P8) records its step-by-step equivalence.
