# Harness Self-Checks (Arch Lint, Clean-State Check, Feature Backlog)

Cross-cutting wiring for 3 advisory, non-blocking harness capabilities. Each is a plain
`Bash()` call to a pure-stdlib Python script under `{plugin_root}/scripts/` — none use
`Task()`/`TaskCreate()`, so `capabilities.task_tools_available == false` fallback mode is a
non-issue for all 3: every call below runs identically whether or not Task tools are
available this session.

## Arch Lint

**When:** immediately before creating any `code-reviewer` `TaskCreate` — standard BUILD
(`build-workflow.md` § `### BUILD task graph`), fast-path BUILD escalation's re-review spawn
(`build-workflow.md` § `### Fast Path Escalation`), and REVIEW (`review-workflow.md` §
`### REVIEW task graph`). Skipped entirely on a clean (non-escalated) fast-path BUILD, since
`code-reviewer` never runs there (see `build-workflow.md`'s fast-path "Agents skipped" list).

```bash
python3 {plugin_root}/scripts/craftflow_arch_lint.py --project-root . --format json
```

Parse stdout `{"findings": [...], "rule_count": N, "skipped": [...]}`.
- `findings == []` → do nothing; omit any new prompt section.
- `findings != []` → append a `## Arch Lint Findings` section to the upcoming code-reviewer
  dispatch prompt's `## Project Patterns` scaffold field (per
  `skills/_shared/router-protocol.md` § "Dispatch Prompt Scaffold"), one `WHAT`/`WHY`/`FIX`
  block per finding. This is advisory context only — never a required field, never a new
  Contract Override row, never a change to `code-reviewer`'s verdict-extraction rules in
  `SKILL.md` § 8.

Non-zero exit or unparseable stdout: capture the script's stderr content (where the real
diagnostic lives, not just the exit code) into `error`, and append
`{"event":"arch_lint_failed","error":"{stderr_content}","ts":"{iso_now}"}` to
`.craftflow/state/workflows/{workflow_uuid}.events.jsonl` (same convention as
`build-workflow.md`'s `fast_path_escalated` event). Continue dispatch WITHOUT the findings
section — never block or retry.

## Clean-State Check

**When:** BUILD — inside `SKILL.md`'s `### Worktree Isolation (BUILD Default)` step 4,
immediately before sub-step 4a (the pre-merge safety guard) when `worktree_mode ==
"auto_created"`, OR immediately before creating the BUILD Memory Update task when no worktree
exists. DEBUG — immediately before creating the DEBUG Memory Update task
(`debug-workflow.md`). Not run for PLAN or REVIEW — neither produces a code diff to scan.

```bash
python3 {plugin_root}/scripts/craftflow_clean_state_check.py --project-root . --format json
```

Parse stdout `{"findings": [...], "skipped": [...]}`.
- `findings == []` and `skipped == []` → nothing to add.
- `findings != []` → add one `[Deferred]: clean-state-check: {pattern} at {file}:{line}` line
  per finding directly into the SAME workflow-scoped `activeContext.md` write that this
  memory-finalize task already performs (via the `deferred` `MEMORY_NOTES` routing in
  `SKILL.md` § 13) — never a separate write, never a blocking gate.
- `skipped != []` → fold skipped entries into that SAME deferred-note write, one
  `[Deferred]: clean-state-check: could not scan {file} ({error})` line per skipped entry.
  This is the one signal that distinguishes "clean" from "couldn't tell" — never silently
  discard it just because `findings` is empty.

Non-zero exit or unparseable stdout: log `{"event":"clean_state_check_failed","error":"..."}`
and proceed to memory-finalize without a deferred note — never block or retry.

## Feature Backlog

**Register** — PLAN memory-finalize (`plan-workflow.md` § `### PLAN task graph`, the
"CRAFTFLOW Memory Update: Index plan in memory" task), only when the planner returned
`STATUS: PLAN_CREATED` or `STATUS: DECISION_RFC_CREATED` this round (a plan file actually
exists):

```bash
python3 {plugin_root}/scripts/craftflow_feature_backlog.py --state-dir .craftflow/state \
  --register '{"id":"{plan_file_stem}","title":"{planner SUMMARY}","plan_file":"{plan_file}"}'
```

**Activate** — BUILD preparation (`build-workflow.md` § `### BUILD preparation` step 2, right
after the plan file is read), when the workflow artifact's `plan_file` matches a
`not_started` backlog entry's `plan_file`:

```bash
python3 {plugin_root}/scripts/craftflow_feature_backlog.py --state-dir .craftflow/state \
  --activate {matching_id}
```

**Complete** — BUILD memory-finalize (`build-workflow.md`, both the standard and fast-path
"CRAFTFLOW Memory Update: Persist workflow learnings" tasks), only when `integration-verifier`
returned `PASS` on the final phase (the workflow is actually finishing, not stopping partial or
blocked):

```bash
python3 {plugin_root}/scripts/craftflow_feature_backlog.py --state-dir .craftflow/state \
  --complete {matching_id}
```

**VCR surfacing** — at the SAME PLAN and BUILD memory-finalize points above, immediately after
any register/complete call:

```bash
python3 {plugin_root}/scripts/craftflow_feature_backlog.py --state-dir .craftflow/state --report json
```

Parse stdout `{"features":[...], "vcr":{"passing":N,"activated":N,"ratio":float|null,"display":"N/M"|"N/A (no activated features yet)"}}`
and set `activeContext.md ## Current Focus`'s trailing line to
`VCR: {display}` (append/replace only that single trailing line — never touch the rest of
`## Current Focus`'s existing content).

Any non-zero exit or unparseable stdout from any of the 4 subcommands above: log
`{"event":"feature_backlog_update_failed","subcommand":"...","error":"..."}` and continue the
workflow without that update — never block or retry. Exit 1 from `--complete`/`--activate` on
an unknown id is expected/designed script behavior (see its docstring), not a crash — still
non-blocking for the workflow.

**On-demand report:** a maintainer may run
`python3 {plugin_root}/scripts/craftflow_feature_backlog.py --state-dir .craftflow/state --report text`
directly at any time; this is never part of an automatic router chain.
