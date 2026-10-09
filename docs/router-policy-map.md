# Router policy map

Maintainer ledger for the router context cut (SPEC-0034, ADR-0073). Not loaded at runtime.
`craftflow_rule_inventory.py --diff` parses the five tables below and fails closed when a header differs.
Every removed baseline rule occurrence must be accounted for here, or still be present, under the same heading path
and list label, in a runtime file that every workflow and variant that read it at `BASELINE_SHA` still reads, at least
as early (kernel and start files at workflow start, then happy-path stage files, then conditional stage files such as
remediation and research).

Reviewer values (policy-map v2): every row in every table is signed by a person as `Name (YYYY-MM-DD)`, for example
`David Gracia (2026-10-09)`; accented, hyphenated and multi-token names are fine. A bare first name, an empty cell or an
agent id is not a signature and counts toward `rows_missing_review`, which fails the diff. Humans sign. Agent reviews
(doubt-verifier, plan-gap-reviewer, code-reviewer) are attached as **Evidence**: every table ends with an optional
`Evidence` column holding the path of the review output, which is never the signature. The signature pattern is a shape
check and not the control: the control is human PR review of this file, `router-baseline.lock.json` and the baseline
fixtures (see Rebaseline: CODEOWNERS and the `[rebaseline]` CI gate).

## Policy-mapped deletions

Status is one of `deleted`, `pending-prose-deletion`, `prose_retained`, `document-only`. Only `deleted` rows
account for a removed occurrence. The script and the test named in a row must exist.

| Baseline ID or group (file:start-end@sha) | Policy key | Enforcing script::function | Test | Status | live_verified | Reviewed by | Evidence |
|---|---|---|---|---|---|---|---|

## Rewrites

Kind is one of `pointer`, `dedupe`, `trigger`, `literal-to-key`, `xref`, `split`, `heading`, `group`, `section-move`.

- `heading` maps `file.md#old heading` (first cell) to `file.md#new heading` (second cell). The rename is scoped to that
  file, the old heading must exist in the baseline file and the new heading must exist in the current file. It renames one
  heading element; it cannot drop a path element (use `section-move`).
- `section-move` maps `file.md#a > b > c` to `file.md#x > y` (headings joined with ` > `, lower-cased, backticks and
  quotes removed). Every baseline occurrence in the source file whose heading path starts with the source path is
  accounted for by the unit with the same id, label and remaining path under the destination path in the destination
  file; the depth may change. One signed row moves a whole section, including its `[EASY TO MISS]` units. Reach is
  preserved as for every kind: a section moved into a file read later (a conditional stage file) is `unmapped_removed`
  unless an Applicability row narrows it with `@reach`.
- `group` is many-to-many: several old ids, several new ids (comma separated). Each new id must relate to at least one
  old id and each old id to at least one new id, by shared tokens of at least 60% of either unit and without losing a
  negation token. `[EASY TO MISS]` units contained in a listed unit are added to their side automatically.
- Every other kind names replacement ids that exist in the current inventory and must relate them to the old rule: the
  new units together keep at least 60% of the old unit's tokens, each new unit is at least 60% old tokens (a verbatim move,
  a dedupe and a split of one rule pass) and no negation token (`never`, `not`, `no`, `only`, `must`, `always`) of the old
  unit is missing from the new text. A table row is compared on its body only, never on the header text that is folded into
  its id. A `pointer` row may instead have a new unit that names the old unit's heading, the file the rule now lives in and
  an existing heading of that file.

Reach is checked on every kind except `heading`: each replacement id (for `group`, each id related to the old one) must be
read by every workflow and variant that read the removed occurrence, at least as early. A `dedupe` kept copy, a `pointer`
unit and a split half are all held to it. Losing reach is expressible only with an Applicability row that carries `@reach`.
A `pointer` row may therefore sit at a lower reach than the rule it points to only if the pointer unit itself is read at
the original reach.

| Baseline ID(s) or group | New IDs | Kind (pointer/dedupe/trigger/literal-to-key/xref/split/heading/group/section-move) | Justification | Equivalence note | Reviewed by | Evidence |
|---|---|---|---|---|---|---|

## Applicability

A row narrows where a rule must still be readable, and only for the occurrences it names: a `file#heading` section
row narrows the occurrences under that heading in that file, an id row narrows every occurrence of that id, and two
rows may not narrow the same occurrence. It must quote the rule's own scope words, and the quote must appear in the
first 100 normalized characters of a named rule (its inventory excerpt); a quote found nowhere in the named rule is
rejected. Coverage cells are `WORKFLOW` or `WORKFLOW:variant` tokens separated by commas; a narrowed token may add
`@happy_path_stage` or `@conditional_stage` to also accept a later reach for that pair (`@kernel` and `@start` are the
default, the baseline reach).

| Baseline ID or section (file#heading@sha) | Baseline coverage | Narrowed coverage | Quoted scope words | Reviewed by | Evidence |
|---|---|---|---|---|---|

## Rationale

Non-normative units moved verbatim to `router-rationale.md`. A normative unit can never be listed here.
Only a unit whose every occurrence is in one of the eight files read at workflow start (the kernel and
start-class files, the L3 basis) may be listed: a stage-class rule is never moved out of the runtime corpus this way.

| Baseline ID | Destination | Justification | Reviewed by | Evidence |
|---|---|---|---|---|

## Read-map widening

`--diff` compares the live `config/router-readmap.json` with the frozen `tests/fixtures/router-readmap.baseline.json`.
Any file whose live coverage reaches a workflow, variant or earlier reach that the baseline map did not give it is
`readmap_widened`, and fails, unless a row here names that file, states its real baseline coverage (`none` for a file
the baseline map does not list) and the widened coverage that is being allowed (`@kernel` by default; a row may cap the
reach with `@happy_path_stage` or `@conditional_stage`). Without this check a rule moved into a file that the live map
then marks as read everywhere would look reachable.

| File | Baseline coverage | Widened coverage | Justification | Reviewed by | Evidence |
|---|---|---|---|---|---|

## Rebaseline

The baseline inventory (`tests/fixtures/router-rule-inventory.baseline.json`), its frozen read map
(`tests/fixtures/router-readmap.baseline.json`) and the commit they describe are pinned by
`tests/fixtures/router-baseline.lock.json`:
`{"baseline_sha": <full 40-hex commit>, "baseline_sha256": <sha256 of the baseline file>, "readmap_baseline_sha256": <sha256 of the frozen read map>}`.
`--diff` and `test_rule_inventory.py::baseline_matches_ref` both fail, never skip, when the baseline's `ref` or either
file's sha256 differs from the lock, or when `git cat-file -e <baseline_sha>^{commit}` fails in the clone (message
"fetch-depth 0 required"; CI checks out with `fetch-depth: 0`). A baseline re-emitted from an edited tree, or with `ref`
null, therefore cannot be diffed against itself.

Human gate (R-M2, H-M3): the lock can be edited in the same PR that edits the rules, so it is protected by review and by
CI, not by the script. `.github/CODEOWNERS` names the owner for the lock, the baseline and the frozen read map. The
`craftflow-ci` job runs the `--diff` exit-criterion command and then fails when
`tests/fixtures/router-baseline.lock.json` differs from `origin/main`, unless the pull request title or body contains
`[rebaseline]` (push events skip the title check but still run `--diff`). Tag a PR `[rebaseline]` only for the deliberate
procedure below.

policy-map v2 migration: the tables gained a trailing `Evidence` column and the Rewrites kinds `group` and
`section-move`. The parser fails closed on the old headers (`header differs from the required header`). To migrate a map
written against v1, append ` Evidence |` to each header line and one `---|` to each separator row, and add
`group/section-move` to the Kind header text; rows need no new cell (a row may omit the trailing Evidence cell). Unit ids
and norms are unchanged, so this is not a rebaseline.

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
