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
| f41d16afa416 | plan_commit | craftflow_pretooluse_bash_guard.py::plan_pathspec_violations | test_policy_guards.py::plan_commit_staged_probe_denies | deleted | true | David Gracia (2026-10-15) | live `claude -p --plugin-dir` probe 2026-10-15 (haiku, scratch managed repo): `git add -f docs/plans/live-plan.md` denied with the plan_commit=never message, nothing staged, `plan_commit_denied` logged; control `git add control.txt` staged |

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
| 24a07e419821 | 6819ae575d1e | literal-to-key | P6 (BC-6, DD-13): the doubt cap is read from `policy.doubt_cycles_max` | Same sentence; `Max 3 cycles` becomes ``Max `policy.doubt_cycles_max` (= 3) cycles``. The value is pinned to `policy.json` by `prose_policy_values_match`; the sentence carries the unchanged cycle-counter text and the following reliability-gates paragraph (id b06c4c87a1a2) is its child. | David Gracia (2026-10-16) | |
| 2f4f92a0e475 | 37de4260b437 | literal-to-key | P6 (BC-6, DD-13): the :981 hard stop names the key | Same bullet; `on cycle 3` becomes ``on cycle `policy.doubt_cycles_max` (= 3)`` and `(3-cycle hard stop)` becomes ``(`policy.doubt_cycles_max` (= 3)-cycle hard stop)``. Deviation from the plan's listed text: the plan drops the `(= 3)` from the second form, but the literal-to-key numeral guard counts the two baseline `3`s, so both forms keep it (and both stay pinned to `policy.json` by `prose_policy_values_match`). Verdict, action and every other word are unchanged. | David Gracia (2026-10-16) | |
| 05d3056bc1b3 | c92db17d1160 | literal-to-key | P6 (BC-6, DD-13): the fix-verify cap is read from `policy.fix_verify_cycles_max` | Same paragraph; `Max 3 cycles` becomes ``Max `policy.fix_verify_cycles_max` (= 3) cycles``. | David Gracia (2026-10-16) | |
| d03c4122f72e | d8b2376cfc55 | literal-to-key | P6 (BC-6, DD-13): the remediation hard rule names `policy.remediation_cycles_max` | Same bullet; `more than 3 cycles` becomes ``more than `policy.remediation_cycles_max` (= 3) cycles``. | David Gracia (2026-10-16) | |
| 3427a4c99edb | febb68703aec | literal-to-key | P6 (BC-6, DD-13): the breaker trip threshold names `policy.remediation_cycles_max` | Same bullet; `remfix_count >= 3` becomes ``remfix_count >= policy.remediation_cycles_max` (= 3)``. The increment and the `broken = true` trip stay router-owned prose. | David Gracia (2026-10-16) | |
| 717929bbb2a0 | bcf8947ee763 | literal-to-key | P6 (BC-6, DD-13): the artifact policy describes the same threshold | Same list item; `remfix_count reaches 3` becomes ``remfix_count reaches `policy.remediation_cycles_max` (= 3)``. | David Gracia (2026-10-16) | |
| 4408e14e2a8f | 2f7f9ded6677 | xref | P8 (cross-reference fix 4, B3): the worktree gate summary moves from `workflow-artifact-and-hook-policy.md` to `references/worktree.md`, and its opening clause named `SKILL.md` "### Worktree Isolation (BUILD Default)" step 4, which is now a pointer | Same summary, pointer retargeted to the code that now holds step 4: "canonical logic lives in `skills/craftflow-router/SKILL.md` → "### Worktree Isolation (BUILD Default)" step 4" becomes "canonical logic, formerly `SKILL.md` step 4, lives in `scripts/craftflow_worktree_finalize.py`; the outcome table above is the router contract". Deviation from the plan's text: the plan drops "step 4" altogether, but the `xref` guard counts the numeral 4, so the clause keeps "formerly ... step 4". The six gate bullets under it are unchanged except as the next row says. | <Name (YYYY-MM-DD)> | |
| dedce6112f4a | 45441147cba7 | xref | P8 (cross-reference fix 4): the `worktree_merge_locked` bullet of the moved summary said "see step 4c", and step 4c (the holder and local-error mapping) is now `holder_of` and the `locked` / `locked_local_error` messages in the finalize script | Same bullet; `(distinct conditions — see step 4c)` becomes `(distinct conditions — see the holder in the finalize `message`)`. The three conditions, the wait / fix / confirm-dead advice and the resume step are unchanged. | <Name (YYYY-MM-DD)> | |
| 2f1ebf989f9e | 2f1ebf989f9e | pointer | P8 (BC-7): the `worktree_merge_conflict` bullet of the worktree gate summary moves verbatim from `workflow-artifact-and-hook-policy.md` to `references/worktree.md` | Verbatim move under the same opening item (the `xref` row above retargets that item). Reach narrows from every workflow at start to BUILD at the final-phase exit through the `then resume` Applicability row, because the bullet is read only when a worktree gate fires. | <Name (YYYY-MM-DD)> | |
| 478233fab3f1 | 478233fab3f1 | pointer | P8 (BC-7): the `worktree_copy_fallback_failed` bullet of the worktree gate summary moves verbatim from `workflow-artifact-and-hook-policy.md` to `references/worktree.md` | Verbatim move under the same opening item (the `xref` row above retargets that item). Reach narrows from every workflow at start to BUILD at the final-phase exit through the `then resume` Applicability row, because the bullet is read only when a worktree gate fires. | <Name (YYYY-MM-DD)> | |
| d0f16424449f | d0f16424449f | pointer | P8 (BC-7): the `worktree_merge_unrecognized_failure` bullet of the worktree gate summary moves verbatim from `workflow-artifact-and-hook-policy.md` to `references/worktree.md` | Verbatim move under the same opening item (the `xref` row above retargets that item). Reach narrows from every workflow at start to BUILD at the final-phase exit through the `then resume` Applicability row, because the bullet is read only when a worktree gate fires. | <Name (YYYY-MM-DD)> | |
| eb6b64868d9c | eb6b64868d9c | pointer | P8 (BC-7): the `worktree_dirty_main_tree` bullet of the worktree gate summary moves verbatim from `workflow-artifact-and-hook-policy.md` to `references/worktree.md` | Verbatim move under the same opening item (the `xref` row above retargets that item). Reach narrows from every workflow at start to BUILD at the final-phase exit through the `then resume` Applicability row, because the bullet is read only when a worktree gate fires. | <Name (YYYY-MM-DD)> | |
| ef01c95cc3b1 | ef01c95cc3b1 | pointer | P8 (BC-7): the `worktree_cleanup_failed` bullet of the worktree gate summary moves verbatim from `workflow-artifact-and-hook-policy.md` to `references/worktree.md` | Verbatim move under the same opening item (the `xref` row above retargets that item). Reach narrows from every workflow at start to BUILD at the final-phase exit through the `then resume` Applicability row, because the bullet is read only when a worktree gate fires. | <Name (YYYY-MM-DD)> | |
| 4e3390cfd563 | ba7fc7e9b2c0 | xref | P8: the Clean-State Check "When" paragraph of `harness-self-checks.md` named "`SKILL.md`'s `### Worktree Isolation (BUILD Default)` step 4, immediately before sub-step 4a", and step 4 is now a pointer to `references/worktree.md` | Same condition (BUILD, `worktree_mode == "auto_created"`, or immediately before the BUILD Memory Update task when no worktree exists; DEBUG; not PLAN or REVIEW) and the same position (immediately before the pre-merge safety step, which is now the finalize call): `inside SKILL.md's step 4` becomes `inside references/worktree.md (formerly SKILL.md's ... step 4)` and `sub-step 4a` becomes `the finalize call (the old sub-step 4a, the pre-merge safety guard)`. The fenced command under it is unchanged and travels with the paragraph. | <Name (YYYY-MM-DD)> | |
| 25eae49eb760 | 6bca1959f9ba | xref | P8 (BC-7, plan P8 Files): the Safety paragraph moves from `SKILL.md` to `references/worktree.md` and re-enters the finalize call, not "step 4a" | Verbatim, except the last clause: `re-enter this step from 4a` becomes `re-enter this step by calling finalize again (the old step 4a)` (step 4a is now the top of the idempotent finalize script, so calling it again is the same act), and `in ## 4. Resume And Hydration` becomes `in SKILL.md's ## 4. Resume And Hydration` so the heading still resolves from the new file (test_router_xrefs). Deviation from the plan's text `re-run the finalize call`: the `xref` guard rejects a row that adds the sequencing word `run` and drops the numeral `4a`. The create-idempotency sentences, the null `worktree_path` fallback and the list of gates that need no bespoke resume branch are unchanged. | <Name (YYYY-MM-DD)> | |

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
| 4408e14e2a8f | BUILD, PLAN, DEBUG, REVIEW | BUILD@happy_path_stage | worktree merge-safety guard | <Name (YYYY-MM-DD)> | |
| dedce6112f4a | BUILD, PLAN, DEBUG, REVIEW | BUILD@happy_path_stage | another build workflow | <Name (YYYY-MM-DD)> | |
| 2f1ebf989f9e, 478233fab3f1, d0f16424449f, eb6b64868d9c, ef01c95cc3b1 | BUILD, PLAN, DEBUG, REVIEW | BUILD@happy_path_stage | then resume | <Name (YYYY-MM-DD)> | |

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
| skills/craftflow-router/references/worktree.md | none | BUILD@happy_path_stage | P8 (BC-7): the new stage file that holds the finalize call, the outcome-to-action table, the Safety paragraph and the worktree gate summary; step 4 of `SKILL.md` was read by every workflow at skill load, this file is read by BUILD only, at the final-phase exit (or on a resume with a worktree gate) | <Name (YYYY-MM-DD)> | |

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

P7 (`craftflow_worktree_finalize.py`, ADR-0073 BC-7; wired into the router prose by P8). The script ports SKILL.md step 4a-4e
(`SKILL.md:392-727@BASELINE_SHA`) in the prose order. P8 replaced that prose with a pointer to `references/worktree.md`; the group row
`SKILL.md:392-727@BASELINE_SHA` -> `worktree_merge` | `craftflow_worktree_finalize.py::main` | `test_worktree_finalize.py` | `deleted` waits
under "Pending human signature" for a person's signature and live evidence. The table below is the branch-to-test mapping the P8
doubt-verifier checks the prose against. Every test name is in `tests/fixtures/test_worktree_finalize.py`.

| Step-4 branch (baseline prose) | Finalize code (`scripts/craftflow_worktree_finalize.py`) | Test |
|---|---|---|
| 4a guard: `worktree_mode != "auto_created"` skips the step | `finalize` -> `skipped_not_auto_created` | `skipped_when_mode_null` |
| 4a guard: empty `worktree_path` -> `{"event":"worktree_path_missing"}`, skip | `finalize` -> `legacy_event` + `worktree_path_missing` | `path_missing_event_skip` |
| 4a guard: empty `worktree_branch` -> `{"event":"worktree_branch_missing"}`, skip | `finalize` -> `legacy_event` + `worktree_branch_missing` | `branch_missing_event_skip` |
| 4a: `PROJECT_ROOT` = three `dirname`s of `worktree_path`, never `git rev-parse --show-toplevel` | `worktree_shape` normalises the path (`os.path.normpath`: `<wt>/`, `.claude/./worktrees`, `<wt>/.` and `//` all derive the true root) and enforces `<root>/.claude/worktrees/<name>`, absolute, no `..`; `merge_root` = that root, and its realpath must equal `--project-root`. A non-empty path that fails is a GATE, not a skip: outcome `worktree_merge_unrecognized_failure` (message "Worktree finalize refused: worktree_path (`...`) is not `<project root>/.claude/worktrees/<name>`"), `pending_gate` persisted, `details.root_mismatch`, nothing touched (Deviation 10) | `root_mismatch_fails_closed`, `worktree_path_must_be_absolute_and_in_root`, `worktree_path_spellings_derive_the_same_root`, `no_root_mismatch_key_when_roots_agree` |
| (new) the policy is read after the 4a guards (mode, path, branch, root), before 4b: an invalid policy gates only a run that would use it | `finalize` ordering (Deviation 11) | `policy_is_read_after_the_4a_guards`, `policy_invalid_touches_nothing` |
| 4b: `mkdir` lock, metadata `{workflow_uuid, worktree_path, acquired_at}` | `acquire_lock` | `lock_metadata_has_three_fields_while_held` |
| 4b: attempts 9, sleep 5 (now `policy.worktree_merge.*`) | `policy_value(policy, "worktree_merge.lock_max_attempts")`, `lock_sleep_seconds` | `policy_values_are_read` |
| 4b: reclaim on `STALE_WORKTREE_GONE*` / `STALE_INACTIVE*` / `SELF_RECLAIM*`, no attempt consumed | `acquire_lock` (`RECLAIM`, `continue`) | `corrupt_metadata_reclaims`, `self_lock_worktree_gone_reclaims`, `reclaim_does_not_consume_an_attempt` |
| 4b: TOCTOU, delete only if `METADATA_BEFORE == METADATA_AFTER` and non-empty | `acquire_lock` (`read_metadata` before and after) | `metadata_changed_between_reads_no_reclaim` |
| 4b: a same-workflow lock with a live worktree is not reclaimed | the staleness script decides; finalize waits | `self_lock_live_worktree_waits` |
| 4b: any other decision (CONTENDED*, LOCK_READ_ERROR*, GIT_WORKTREE_LIST_ERROR*, empty, garbled) waits | `acquire_lock` | `lock_contended_live_stranger`, `unrecognized_decision_holder_unknown` |
| 4c: holder `CONTENDED <uuid>` -> the uuid | `holder_of` | `lock_contended_live_stranger` |
| 4c: holder `CONTENDED_UNKNOWN_HOLDER` or an unrecognized decision -> `unknown` | `holder_of` | `contended_unknown_holder_is_unknown`, `unrecognized_decision_holder_unknown` |
| 4c: holder `LOCK_READ_ERROR <class>` -> the class and the local-error message | `holder_of` + `MESSAGES["locked_local_error"]` | `lock_read_error_holder_is_exception_class` |
| 4c: holder `GIT_WORKTREE_LIST_ERROR <class>` -> same treatment | `holder_of` | `git_worktree_list_error_template` |
| 4c: nothing touched, stop before memory-finalize | `conclude(..., gate="worktree_merge_locked")` | `no_side_effects_on_gate_outcomes` |
| 4d: `git status --porcelain` non-empty -> release lock, `worktree_dirty_main_tree` | `merge_and_clean` | `dirty_main_tree_releases_lock` |
| 4d: `git status` itself fails -> treated as dirty (same outcome; the message says the status failed, not that the tree is dirty) | `merge_and_clean` (`main_status_failed`) | `status_failure_is_dirty` |
| 4e: `MERGE_EXIT == 0` and "Already up to date" -> copy-fallback script | `merge_and_clean` (`COPY_FALLBACK`) | `up_to_date_copy_fallback_ok_cleans_up` |
| 4e: copy-fallback exit != 0 -> release lock, `worktree_copy_fallback_failed`, worktree and branch kept | `merge_and_clean` | `copy_fallback_failure_partial_apply_gate` |
| 4e: `MERGE_EXIT == 0` otherwise -> a real merge | `merge_and_clean` | `real_merge_cleans_up`, `real_merge_divergent_creates_merge_commit` |
| 4e: `MERGE_EXIT != 0` and "CONFLICT" -> release lock, `worktree_merge_conflict`, worktree and branch kept | `merge_and_clean` | `conflict_gate_keeps_worktree` |
| 4e: `MERGE_EXIT != 0` otherwise -> `worktree_merge_unrecognized_failure`, copy-fallback not run | `merge_and_clean` | `unrecognized_merge_failure` |
| (new, Deviation 12) before the remove, a worktree with uncommitted or untracked changes that copy-fallback did not carry over is NOT removed: `worktree_cleanup_failed` (`cleanup_dirty`; `cleanup_status_failed` when its status cannot be read), worktree and branch untouched | `merge_and_clean` (`applied`, `git status --porcelain --untracked-files=all --ignore-submodules=none` in the worktree, so `status.showUntrackedFiles=no` cannot hide work; Deviation 12) | `dirty_worktree_is_never_discarded`, `untracked_work_survives_show_untracked_files_no`, `e32_hyphenated_not_special_cased` |
| cleanup: `git worktree remove {path} --force` fails -> `worktree_cleanup_failed`, branch kept (argv `remove --force -- {path}`) | `merge_and_clean` | `worktree_remove_failure`, `git_worktree_remove_uses_double_dash` |
| cleanup: `git branch -d {branch}` fails -> `worktree_cleanup_failed`, `worktree_mode` unchanged | `merge_and_clean` | `branch_delete_refusal` |
| cleanup: both succeed -> `worktree_mode = merged_and_removed`, THEN release the lock (artifact first, BC-7 item 12 / SKILL.md:743-748); a release error is not fatal but is loud: `details.lock_release_failed` plus a message WARNING | `Ctx.conclude(lock_dir=...)`, `release_lock` | `real_merge_cleans_up`, `artifact_persisted_before_lock_release`, `release_failure_is_loud` |
| (new, Deviation 13) the lock is released at most once per run and only while it is still this workflow's (metadata `workflow_uuid`); a crash after the gate/success release cannot delete another workflow's lock | `release_lock` (`Ctx.released`, `Ctx.lock_created`) | `lock_released_once_foreign_lock_survives`, `release_requires_ownership`, `exception_propagation_releases_lock` |
| (new, Deviation 14) artifact write failure is exit 4 + `details.artifact_write_error` + a message WARNING; the outcome and `worktree_mode` in the response stay what really happened; the temp file is removed | `Ctx.conclude`, `update_artifact` | `artifact_write_failure_success_path`, `artifact_write_failure_gate_path`, `artifact_write_failure_cleans_tmp` |
| (new, Deviation 15) lock metadata cannot be written -> the lock is dropped and the run gates `worktree_merge_locked` with holder `LOCK_WRITE_ERROR <class>` (a local error) | `acquire_lock`, `_write_meta` | `lock_metadata_write_failure_fails_closed` |
| (new, Deviation 16) `git merge` killed by the 600 s timeout, not launched (exit 127) or killed by a signal (exit < 0) -> `worktree_merge_unrecognized_failure` with the "merge state is UNKNOWN, inspect" message instead of "Nothing has been merged" | `merge_and_clean` (`merge_state_unknown`) | `merge_timeout_says_state_unknown`, `merge_killed_by_signal_says_state_unknown` |
| (new, Deviation 17) every git call runs with `LC_ALL=C LANGUAGE=C`: "Already up to date" and "CONFLICT" are matched as English text | `_git` (`C_LOCALE`) | `git_runs_in_the_c_locale` |
| (new) paths with spaces; a reclaim whose `rmtree` fails terminates | n/a | `paths_with_spaces`, `reclaim_loop_terminates_when_rmtree_fails` |
| Safety: a resume with a worktree `pending_gate` re-enters from 4a | the call is idempotent from the top | `resume_after_locked_reenters`, `resume_after_cleanup_failure_completes`, `rerun_after_success_skipped` |
| (new, Deviation 19) resume after an unrecorded finish: the branch ref is gone AND the worktree is neither on disk nor listed -> `merged_and_removed` with `details.recovered`, nothing in the main tree touched, a worktree gate cleared, lock released; branch present and worktree gone -> merge as usual, skip copy-fallback / status / remove, then `git branch -d` | `worktree_gone`, `merge_and_clean` | `resume_after_exit_4_recovers_as_already_merged`, `resume_after_branch_delete_failure_completes_without_traceback` |
| (new, Deviation 19, P8 step 0) a worktree whose directory is gone from disk but which git still lists (`prunable`): merge as usual, skip copy-fallback and the worktree status check (nothing on disk to lose), run `git worktree remove --force -- {path}`, then `git branch -d`; a `git branch -d` failure for a worktree that is not on disk says "no worktree is registered or present" (`cleanup_branch_gone`), never "the worktree was removed" | `merge_and_clean` (`on_disk`) | `prunable_missing_worktree_is_completed`, `message_does_not_claim_a_removal_that_did_not_happen` |
| (new, Deviation 20) `skipped_not_auto_created` clears a worktree `pending_gate` a prior run left behind, only when `worktree_mode` is `merged_and_removed` | `finalize` (`clear_stale`) | `skip_clears_a_stale_worktree_gate_only_when_merged` |
| (new, BC-7 item 12) success clears only a worktree `pending_gate` | `Ctx.conclude` (`GATES`) | `success_clears_only_worktree_pending_gate` |
| (new, BC-7 items 1-2) exit codes 0 / 2 usage / 3 corrupt artifact / 4 artifact write failed; invalid policy -> nothing touched | `read_artifact`, `load_policy` | `artifact_corrupt_exit_3`, `policy_invalid_touches_nothing` |
| (new, PP-4) one JSON line, 12-value enum, `pending_gate` iff a gate outcome | `Ctx.conclude` | `exactly_one_json_line_enum`, `every_outcome_is_exercised` |

Deliberate differences from the baseline prose, each visible in the tests (none changes which branch a given git/lock state takes):

1. `policy_invalid` and `artifact_unreadable` are new outcomes (BC-7 items 1 and 2); the prose had no policy and no artifact read.
2. The conflict gate message is new wording ("`git merge {branch}` stopped with merge conflicts ..."): the baseline only says "ask user to resolve before memory-finalize" and gives no text. The `$PROJECT_ROOT` placeholder in the other messages is kept verbatim.
3. A `worktree_branch` that starts with `-` is treated as `worktree_branch_missing` (git would read it as an option). The prose guard only covered null or empty.
4. A reclaim whose lock directory cannot be removed falls through to the wait path instead of looping (`rm -rf` could fail forever in the bash loop).
5. `GIT_WORKTREE_LIST_ERROR NonZeroExit(rc=N): <stderr>` is recorded whole as the holder, so the message names the real local cause (the staleness script's own contract); the launch-failure form records the class name, as the prose says.
6. `git merge` runs with `GIT_MERGE_AUTOEDIT=no` so a real merge never waits on an editor; every git call uses `git -C <MERGE_ROOT>`.
7. The conflict gate is excluded from the PP-4 byte-equal `git status --porcelain` clause: the `git merge` attempt itself leaves its conflict state in the main tree, as step 4e always did, for the user to resolve. The branch ref and the worktree listing are still compared. The partial-apply copy-fallback gate is excluded by BC-7.
8. An unexpected exception inside the merge section releases the held lock and propagates (traceback, non-zero exit, no JSON line); the router reads that as `worktree_merge_unrecognized_failure` (Difference 6).
9. The post-attempt sleep still follows the last failed attempt (the bash loop slept 5 s after each attempt, including the last).
10. `worktree_path` must be absolute, free of `..` and, once normalised, exactly `<root>/.claude/worktrees/<name>`, and that root must equal `--project-root` (realpath). An EMPTY or null path (not a whitespace-only one, which is non-empty and gates) stays the prose-faithful `worktree_path_missing` skip (with the legacy event). A NON-EMPTY path that fails the shape is a gate: outcome `worktree_merge_unrecognized_failure` (the pinned 12-value enum has no new value; this is the smallest change), `pending_gate` persisted, nothing touched, because committed work may be stranded in a worktree this script refuses to trust; no legacy event is appended. Normalised spellings (`<wt>/`, `<wt>/.`, `.claude/./worktrees`) are accepted and derive the real root where three bare `dirname`s would not (previously these skipped or mis-derived).
11. The policy is loaded after the 4a guards (mode, path, branch, root) instead of first. The prose has no policy and its first guard is the mode check, so a non-`auto_created` workflow (or a missing path or branch) skips exactly as before and an invalid `config/policy.json` gates only a run that would read it. This supersedes the first draft's "policy before the mode check" order. **P8: the equivalence reviewer checks this order against the prose guards.**
12. DATA-LOSS GUARD (the prose silently discards): a worktree that still has uncommitted or untracked changes after the merge, and whose edits copy-fallback did not carry over, is not removed by `git worktree remove --force`. The run gates `worktree_cleanup_failed` (worktree and branch untouched) with a message saying uncommitted changes remain. The worktree check runs `git status --porcelain --untracked-files=all --ignore-submodules=none`, so a user's `status.showUntrackedFiles=no` or submodule ignore setting cannot hide work. A worktree that copy-fallback fully applied is still removed, as in the prose. Ignored files are not looked at (`.gitignore`d files are omitted by status) and are still discarded with the worktree: a known, deferred gap. The step-4d MAIN-tree check keeps the bare `git status --porcelain` (as the prose has it), so it honours `status.showUntrackedFiles`; that check is advisory (the merge itself refuses to overwrite untracked files).
13. The merge lock is released at most once per run and only while its metadata `workflow_uuid` is this workflow (or the metadata is unreadable and this run created the directory). The prose had no such guard because bash had no second release.
14. The artifact is written BEFORE the lock is released (success and gate paths), matching BC-7 item 12 / SKILL.md:743-748. A failed artifact write is exit code `4` (next to 0 normal, 2 usage, 3 unreadable artifact), keeps the real `outcome`, and ends the message with a WARNING; the router must not read exit 4 as success.
15. A lock whose metadata cannot be written is dropped and reported as `worktree_merge_locked` with the local-error message (`LOCK_WRITE_ERROR <class>`), where the prose ignored a failed `cat >`.
16. A `git merge` that did not finish (launch failure or the 600 s timeout, exit 127, or killed by a signal, exit < 0) says the merge state is unknown; the prose's "nothing has been merged" would be false after a timeout.
17. Every git call runs under `LC_ALL=C LANGUAGE=C` so the text matches ("Already up to date", "CONFLICT") hold in any user locale; `git worktree remove` takes `--` before the path.
18. A lock-release failure is surfaced loudly (`details.lock_release_failed` and a message WARNING that names `.claude/worktrees/.merge.lock`), where the prose said a release error is not fatal and said nothing more.
19. Resume recovery: if `worktree_mode` is still `auto_created` but the branch ref is gone and the worktree is neither on disk nor in `git worktree list` (an exit-4 artifact write failure, or a lost write, after the cleanup ran), the prose would re-run the merge and report a false "nothing has been merged". The script records `merged_and_removed` (`details.recovered`, a message saying so) without touching the main tree. Residual risk: a branch deleted by hand without being merged is recorded as merged too; the worktree is gone either way, so nothing is lost by recording it. If the branch is still there but the worktree is gone (a failed `git branch -d`), the merge runs as usual and the copy-fallback, worktree status and `worktree remove` steps are skipped, so a persistent delete failure gates `worktree_cleanup_failed` instead of a copy-fallback traceback. P8 step 0 extends this to a worktree whose directory was deleted by hand but is still registered (`prunable`): `worktree_gone` is false there, but nothing is on disk, so copy-fallback and the worktree status check are skipped and `git worktree remove --force -- <path>` (which accepts a prunable entry) deregisters it before `git branch -d`; the prose would have died in copy-fallback or the status check until `git worktree prune`. A branch-delete failure when no worktree is on disk uses its own message (`cleanup_branch_gone`) instead of claiming the worktree was removed. A whitespace-only `worktree_path` is a non-empty path finalize cannot trust: it gates like any other bad path (Deviation 10), where the first draft skipped it as `worktree_path_missing`.
20. `skipped_not_auto_created` clears a stale worktree `pending_gate` (one of the 7 finalize gates) when `worktree_mode` is `merged_and_removed`, so a gate left behind by a failed earlier write cannot block the workflow; any other mode or a foreign gate is left alone.
21. Known limits kept as documented risk: (a) `release_lock` with unreadable lock metadata and `lock_created=True` deletes the directory it created; in the narrow window between another workflow reclaiming and writing metadata, that could be a foreign lock. Tightening needs a lock token in the directory name and is not trivial, so it is left. (b) E32 (hyphenated "up-to-date"), ignored files, and non-English installed-locale proof stay deferred.
22. **P8 outcome table (done):** the planned P8 `references/worktree.md` outcome table in `tests/fixtures/router-cut-plan.json` had no row for exit code 4 (the artifact must be repaired before resuming) or for `details.lock_release_failed`, and its `worktree_merge_unrecognized_failure` row did not cover the refused `worktree_path`. P8 writes the real `worktree.md` with those cases (an "Exit code 4" paragraph, the `lock_release_failed` warning in the `merged_and_removed` row and under the table, the refused-path clause in the unrecognized-failure row, the Difference 6 row last), updates the cut-plan P8 row texts to the real files, regenerates `router-projection.json` and appends a `projection_revisions` entry with `decided_by: "pending-David"`.

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

P5 (`plan_commit` and `native_plan_mode`, ADR-0073 BC-2 and BC-3, Claude Code only per DD-12), for `## Policy-mapped deletions`.
Three draft rows:

- `f41d16afa416` is the SKILL.md § 13 "Never `git add` or `git commit` a plan artifact" bullet. P5 shortened it to one sentence
  ("enforced by policy `plan_commit` in craftflow projects (see Policy table)") and kept the `- Plan:` pointer and the
  `git restore --staged` sentences as defense in depth, so the baseline occurrence is gone: `deleted`. The pointer to the
  Policy table has no target until the phase that renders that table lands.
- `a01e2c640767` (SKILL.md) and `c403449266e4` (`router-protocol.md`) are the two "Never use EnterPlanMode" rules. P5 enforces them
  but deletes neither: `pending-prose-deletion`, `live_verified: false`. The live `claude -p` probe was not run in P5; P13 (LV-3 /
  HV-4) must prove the deny fires, or these rows become `prose_retained`.

Accepted gaps of the `plan_commit` guard (all fail open, today's behavior): `git update-index --index-info` (reads stdin),
plumbing that writes the index or commits without `add`/`commit`/`update-index` (`git hash-object -w` + `commit-tree`,
`git stash`), a `--pathspec-from-file` list without `--force` (the staged probe still catches it at commit), a
`--git-dir` that points into a linked worktree's admin dir (repo lookup falls back to the current directory), and dynamic
command names, `-C`/`cd`/`--git-dir`/`--work-tree` values or `sh -c` strings, `cd -` / `popd` / `cd "$PWD"`, dynamic or value-less
`export GIT_DIR`, bare `GIT_DIR=...` assignments, `case` statements, `| sh`,
here-documents and here-strings, `python3 -c`-style interpreter strings, command substitution that names git, `xargs` / `find -exec`
pathspecs supplied elsewhere, and an unwrapper that gives up past its depth or iteration cap (each logged as
`plan_commit_dynamic_allowed` with its reason). The pass is bounded (16 KB of command text, 2000 units, a 4 KB cwd, 5 s); when it
is skipped or exhausted (also: 50 distinct git-write invocations, a spent commit-probe budget, an unparseable or unmaskable
command), a command with `git` at a command position followed by add/stage/commit/update-index (or merge/pull with a workflow
branch name) is DENIED in a managed project (fail closed, `policy_guard_skipped` with `denied: true`; judged on the RAW text, so a quoted
`git commit` at a line start is a documented false deny), and any other command is allowed. The size path (over 16 KB) never runs the
lexer; a size-exhausted commit-only command is checked with the staged and worktree diff probes. Still accepted gaps: `case` statements,
function bodies, indirect command names (`g=git; $g commit`), arrays, more than 8 nested wrapper levels, numeric-delimiter `<<` forms,
`git add .` of an untracked non-ignored plan, `bash -c "git commit"` once exhausted, and an `add && commit` over 16 KB or past the caps
(false deny, split it). Recommended follow-up phase: extract the lexer into its own module and add a differential test against real bash.

```
| a01e2c640767 | native_plan_mode | craftflow_pretooluse_guard.py::_handle_enter_plan_mode | test_policy_guards.py::enterplanmode_denied_by_default | pending-prose-deletion | false | <Name (YYYY-MM-DD)> | |
| c403449266e4 | native_plan_mode | craftflow_pretooluse_guard.py::_handle_enter_plan_mode | test_policy_guards.py::enterplanmode_denied_by_default | pending-prose-deletion | false | <Name (YYYY-MM-DD)> | |
```

P6 (`craftflow_gate_check.py`, ADR-0073 BC-6, DD-13). Two things need a human signature before CI's inventory diff can pass:

1. The six `literal-to-key` Rewrites rows already sit in `## Rewrites` with `<Name (YYYY-MM-DD)>` in `Reviewed by`; `--diff` reports
   `rows_missing_review: 6` until a person signs them (and nothing else: `unmapped_removed: 0`, `row_errors: []`). One row deviates
   from the plan's listed text and says so: `(3-cycle hard stop)` keeps its `(= 3)` pin, because the `literal-to-key` numeral guard counts
   the two baseline `3`s in the :981 bullet (the plan's form drops one). `plan-workflow.md:66` ("Maximum fresh-review passes: 2.") is **not**
   rewritten: `router-normative-allowlist.json` names that unit and both `baseline_matches_ref` and the attack-8 scratch emit need it
   present, so the pin is a new bullet below it (no row needed).
2. `8b95f8c74a2b` is `remediation-and-research.md:264`, "If count >= 2, run the cycle-cap gate before continuing". P6 deliberately does
   not wire it: its `2` is a trigger on completed remediation tasks, not a cap value, and "cycle-cap gate" is defined nowhere else, so
   mapping it would invent semantics (A9). Draft row for `## Policy-mapped deletions` (nothing is deleted; the unit is still present, so
   the diff does not need it):

```
| 8b95f8c74a2b | remediation_cycles_max | craftflow_gate_check.py::run | test_router_policy_prose.py::rr_264_stays_verbatim_and_unwired | prose_retained | false | <Name (YYYY-MM-DD)> | |
```

After signing, regenerate `tests/fixtures/router-projection.json` (`craftflow_router_readset.py --project tests/fixtures/router-cut-plan.json --json`),
because `reach_feasibility` counts the signed rows, and record a `projection_revisions` entry in `router-budget.json` (as P5 did).

P8 (`craftflow_worktree_finalize.py` wired into `references/worktree.md`, ADR-0073 BC-7). Needs a human signature and live evidence before CI's
inventory diff can pass. The draft group row for `## Policy-mapped deletions` below is deliberately kept OUT of the table. Signed, it accounts
for the 66 baseline ids of `SKILL.md:392-727@0439c33932463d257b881ce50f136ab7661c0a39` (Worktree Isolation step 4 and its Safety paragraph) that no
Rewrites row covers; the row lists the ids, not the `file:start-end@sha` range the plan names, because a range needs a git checkout under
`--plugin-root` and the scratch-copy tests (`test_rule_inventory_doubt*.py`) run `--diff` on a copy without `.git`. Measured with this
tree: `unmapped_removed` is 77 occurrences / 66 ids with the row absent (plus `rows_missing_review: 13`); with the row and the thirteen rows
below signed it is 0 (`row_errors: []`, `unreachable: 0`, `policy_mapped` 78, `present` 1241, `reworded` 14, which sum to the 1333 baseline
occurrences). `live_verified` must be true for a `deleted` row; the live probe is a real `claude -p --plugin-dir` BUILD run in a scratch
managed repo that ends in a `craftflow_worktree_finalize.py` call with a `merged_and_removed` outcome and the worktree and branch gone
(the P5 probe is the model).

```
| 0600a1be9270, 07b5f4c83b70, 0e697b746505, 0fb2fe390de2, 15f8b2298a92, 1b2655482484, 20b540d96d08, 20d7a9a9ada3, 25eae49eb760, 2874d10c541b, 29f7203fbde2, 2f5588bfa26e, 30307ca86ee9, 32c8865918fb, 3564eb52d73f, 365c86e93611, 414d7bc58559, 424f3676b24e, 496209c69131, 515990f3adc2, 55d87f6608d3, 5606d64a58fc, 57064d296c16, 5b24bac42e89, 5d44678a8c8c, 5d53194f38fd, 624cffe01286, 641a4fc0f499, 6591cde71bf0, 7071aadfae53, 74f9c3d59bc0, 7f03b57f75bc, 89733d670d8f, 8a5090e28835, 8c26c8f91c97, 8f164dc0f842, 91b0890f6ef9, 91bc31aa66e7, 920f2387bdda, 9471d6d61266, a6518b56c748, abffb1ebc21c, acdc11f60141, ae44666e6085, bcc88e42886d, c0a247ea8d50, c0ad81c9c468, c577b86dd699, c7b29414f19b, c9a8490b1c02, cc35d7127a3d, ccfa946a5e08, cfdc024fec13, d5ccd76cfe50, d85016555964, daff4ec339d4, dffb60671b88, e071721c49ab, e0d893afd420, e80c088f885b, ee29e4350412, f3dd2498e41e, f7f6ac7b0bb1, fbe89d3379ca, fea3c7a096bd, ff598b22e81f | worktree_merge | craftflow_worktree_finalize.py::main | test_worktree_finalize.py | deleted | false | <Name (YYYY-MM-DD)> | |
```

Thirteen rows already sit in their tables with `<Name (YYYY-MM-DD)>` in `Reviewed by` (`rows_missing_review: 13` until a person signs them):
four `xref` rows and five `pointer` rows in `## Rewrites` (the moved worktree gate summary, its "see step 4c" bullet, the Safety
paragraph and the Clean-State Check "When" paragraph of `harness-self-checks.md`), three rows in `## Applicability` (the summary and its bullets narrowed from every workflow at start to BUILD at the
final-phase exit) and one row in `## Read-map widening` (`worktree.md`, baseline coverage `none`).
