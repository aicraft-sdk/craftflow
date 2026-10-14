# Router policy map

Maintainer ledger for the router context cut (SPEC-0034, ADR-0073). Not loaded at runtime.
`craftflow_rule_inventory.py --diff` parses the five tables below and fails closed when a header differs.
Every removed baseline rule occurrence must be accounted for here, or still be present, under the same heading path
list label, enclosing list items and preceding paragraph (an occurrence's `parent_ctx`), in a runtime file that every workflow and variant that read it at `BASELINE_SHA` still reads, at least
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
  heading element; it cannot drop a path element (use `section-move`). The new heading must keep at least 60% of the old
  heading's words and its guarded tokens (below) must match both ways, so `Hard Rules` -> `Soft Suggestions (...)` is
  rejected.
- `section-move` maps `file.md#a > b > c` to `file.md#x > y` (headings joined with ` > `, lower-cased, backticks and
  quotes removed). Every baseline occurrence in the source file whose heading path starts with the source path is
  accounted for by the unit with the same id, label, parent context and remaining path under the destination path in the
  destination file; the depth may change. One signed row moves a whole section, including its `[EASY TO MISS]` units. Reach is
  preserved as for every kind: a section moved into a file read later (a conditional stage file) is `unmapped_removed`
  unless an Applicability row narrows it with `@reach`.
- `group` is many-to-many: several old ids, several new ids (comma separated). Each new id must relate to at least one
  old id and each old id to at least one new id, by shared tokens of at least 60% of either unit. Retention is then
  checked per id: the union of an old id's related new units keeps at least 60% of its tokens and each new unit is at
  least 60% drawn from its related old units, so a fragment of a long rule is not a relation. Negation tokens must match
  both ways (below). `[EASY TO MISS]` units contained in a listed unit are added to their side automatically.
- Every other kind names replacement ids that exist in the current inventory and must relate them to the old rule: the
  new units together keep at least 60% of the old unit's tokens, each new unit is at least 60% old tokens (a verbatim move,
  a dedupe and a split of one rule pass) and the guarded tokens match both ways: any of `never`, `not`, `no`, `only`,
  `must`, `always`, `cannot`, `unless`, `without`, `nor`, `neither`, an `n't` form (`don't`, `isn't`, ...), the modal,
  obligation and sequencing words `shall`, `required`, `mandatory`, `optional`, `may`, `should`, `before`, `after`,
  `until`, `skip`, `run`, `allow`, `deny`, `stop`, `advance` (and their `-s` forms), `fail closed` / `fail open`, any
  numeral and the operators `>=`, `<=`, `>`, `<`, `==`, `!=` present on only one side, or with fewer occurrences kept than
  the old text had, is no relation (a token inside a code span counts like any other token). A `dedupe`, `split` or
  `literal-to-key` row, and each old id of a `group` row, must also keep at least 50% of the old text's adjacent word
  pairs (bigrams) in the new units, which catches a subject/object swap built from distinct phrases. A table row is compared on its body only, never on the header text that is folded into
  its id. A `pointer` row may instead have a new unit that names the old unit's heading, the file the rule now lives in and
  a heading of that file whose section holds at least 60% of the old unit's tokens (and its negation tokens) or a unit
  that relates by the rules above; an unrelated heading of the named file does not qualify.

An occurrence also carries its context: the ids of the list items that enclose it (any trailing punctuation) and of the
paragraph that directly precedes its list, so a child that ends up under the other branch of an `If ... failed` /
`If ... succeeded` pair is a lost occurrence even when its text is unchanged. A row that rewrites a parent item or intro
paragraph 1:1 carries its children with it; a many-to-many rewrite of a parent needs the children moved by a
`section-move` or listed in the same row.

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
rows may not narrow the same occurrence. It must quote the rule's own scope words: at least two whole words, found on
word boundaries in the full normalized text of every rule the row names. A section row whose quote is missing from any
rule it covers is rejected; give each such rule its own id row instead. Coverage cells are `WORKFLOW` or `WORKFLOW:variant` tokens separated by commas; a narrowed token may add
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

## Structural limit: what the mechanical checks cannot see

The mechanical checks (retention >= 60%, two-way negation on an 11-word list, reach, read-map pinning, lock,
section-content for pointers) catch gross loss and the most common polarity flips. They **cannot** see semantic
inversions expressed lexically: token-order inversions (`before`<->`after`, subject/object swaps, a negation moved to
another clause), numeric changes (`3`->`30`, `>=`->`>`), obligation-word substitutions outside the guarded list
(`required`->`optional`, `MANDATORY`->`advisory`, `fail closed`->`fail open`, `skip`<->`run`), heading-text rewording
(`Hard Rules`->`Soft Suggestions`), up to 40% added exception text in a group reflow, and pointer sections that merely
contain the rule's tokens. The guarded-token set, the bigram check and the heading check narrow this gap but do not close
it. **The signed reviewer is the control for meaning.** Every Rewrites/Applicability/Widening/Policy/Rationale row must be
read by the signer against the old and new text; the Evidence column should cite the doubt-verifier output that reviewed
the row.

Also not covered: an override sentence ("the following rules are obsolete") near a rule is reported only as
`added_normative_near_rule`, and under a different heading it is invisible; and the read map is self-attested (nothing
checks it against what SKILL.md tells the router to `Read()`; P2's read-set measurer should derive it from the kernel's
Read Map table). `[rebaseline]` is free text and a direct push to main skips the PR-only gate, so branch protection with
required code-owner review is the real gate (a repository setting). `.github/CODEOWNERS` also names the inventory
scripts, the corpus loader and util modules they import, their tests, the CI workflow, the fixture runner and the read
map.

Documented limits (P0 REM-FIX 7, advisory A-1..A-5; none is mechanically closed): the applicability quote rule accepts
weak quotes (`e.g` is two words, `"in the"` passes per rule); a 1:1 rewrite row X to X lets X sit under any heading and a
section-move row maps a whole subtree, so the signer owns both; only a multi-line HTML comment counts as a removal; a `---`
thematic break between an intro and its list drops the intro, reordering un-numbered siblings with the same anchor is
invisible, and case or `**` changes are normalized away; and frontmatter edge cases (a ` ---` closing line, whitespace
collapse in quoted values, `name: |`) cannot be checked against Claude Code's own parser.

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

## Pending human signature

Draft rows that are written but deliberately kept OUT of the tables above: `--diff` counts an unsigned row in
`rows_missing_review` and fails, and humans sign (see the top of this file). A signer pastes the row into the named table
with `Name (YYYY-MM-DD)` in the `Reviewed by` cell, in the same change that signs it.

P4 (`memory_write`, ADR-0073 BC-4, document-only: the guard reads and logs the value, behavior is unchanged), for
`## Policy-mapped deletions`. The baseline id `2b1275f157fe` is the SKILL.md § 13 sentence "the `craftflow_pretooluse_guard.py`
hook blocks direct writes to protected memory files"; the prose is not deleted.

```
| 2b1275f157fe | memory_write | craftflow_pretooluse_guard.py::_handle_edit_write | craftflow_hook_unit_tests.py::test_memory_write_permit_unchanged_with_policy_invalid | document-only | false | <Name (YYYY-MM-DD)> | |
```
