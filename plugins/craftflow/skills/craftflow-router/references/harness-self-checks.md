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

*(added in Phase 2 — see below)*

## Feature Backlog

*(added in Phase 3 — see below)*
