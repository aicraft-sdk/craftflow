# Changelog

All notable changes to the craftflow plugin are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Releases are produced automatically by `.github/workflows/publish-craftflow-plugin.yml`.
Do not hand-edit released sections.

## [1.8.0] - 2026-09-23

### Features

- add jev-setup CLI and guided setup skill
- wire jev prompt hint audit telemetry and advise injection (fail-open)
- add pure roster/state/question/gating/telemetry builders for jev hint
- add stdlib jev client with 4s deadline and file cache
- add deterministic keyword-table port for jev agreement telemetry
- register inert UserPromptSubmit jev hint hook + event contract row
- add jev.json config loader (off by default, fail-open normalize)

### Fixes

- actionable diagnostics, top-level try/except, subprocess timeout in jev live-canary driver
- guard jev report mean/p95 latency against sum-overflow inf
- guard aggregate() raw_line.strip() against MemoryError
- catch MemoryError in _sanitize_json_value's utf-8 encode probe
- catch MemoryError in jev report _read_lines()
- structurally close jev report crash-bug class (OverflowError + UnicodeEncodeError)
- guard jev report sanitizer against nested NaN/Infinity in disagreement fields
- guard jev report aggregate against unhashable feature and non-finite disagreement fields
- close prompt-injection breakout in jev routing-hint block
- whitelist workflow.choice against WORKFLOWS before injection
- consolidate jev client call() into single fail-open handler; stop stale status leak
- guard jev client pre-loop setup against uncaught TypeError
- catch MemoryError/RecursionError in jev client call()
- widen jev client exception handling for mid-response body-read failures

### Documentation

- README + hook inventory docs for optional Jev routing hint
- document jev hint precedence (ERROR keywords always win) in router protocol

### Tests

- jev live canary manifest + bootstrap proof scenarios
- add regression tests for jev routing-hint injection breakout
- add regression test for workflow.choice whitelist
- add run_active wiring tests for jev prompt hint (audit/advise/fail-open)
- add pure roster/state/question/gating/telemetry builder tests for jev hint
- add jev client regression tests for clock failure, hostile log, and stale-status leak
- add regression tests for jev client pre-loop TypeError gap
- add resp.read()/json.loads() exception-gap regression tests
- add mocked-urllib jev client tests (retry budget, secret hygiene, cache)
- add jev heuristic port with markdown parity tests

## [1.7.0] - 2026-09-17

### Features

- append precompact narrative digest on SessionStart(source=compact)
- add hooklib.read_precompact_snapshot() read-side helper
- wire narrative digest into PreCompact snapshot main()
- add bounded narrative-digest extraction to precompact snapshot
- add dual-shape section-entry extraction for precompact digest

### Tests

- import craftflow_sessionstart_context module for direct testing
- add drift-guard for combined PreCompact subprocess timeout budget
- add end-to-end PreCompact hook test for narrative digest

## [1.6.1] - 2026-09-17

### Fixes

- widen _is_protected_skill_promotion_path's except clause for consistency
- guard unguarded memory_finalize_permit_path().resolve() in bash_guard's redirect loop (ADR 0036 site 18)
- close final 3 detector-completeness gaps in all-or-nothing-loop test
- harden all-or-nothing-loop prevention test against 4 blind spots
- close 7 all-or-nothing violation-detection loops + add structural prevention test
- stop one unresolvable workflow JSON from unprotecting its siblings
- stop one unresolvable redirect path from unprotecting its siblings
- stop one unresolvable memory path from unprotecting its siblings
- fail closed on an unresolvable cwd in the Bash destructive guard
- REM-FIX cycle 9 -- close whitespace-tolerance bypass in python alias-detection
- stop over-restricting aliased-but-unused python imports
- REM-FIX cycle 7 — close os.system/subprocess/shutil write-mechanism bypass in unresolvable-cwd detector
- deny Bash writes when the payload cwd cannot be resolved
- resolve import bindings in stdin-read prevention test
- REM-FIX cycle 6 (final) - close remaining unguarded stdin decode crash sites
- REM-FIX cycle 5 - guard load_input() stdin decode crash
- REM-FIX cycle 4 - close _load_blocks() JSON type validation gap
- comprehensive close-out of memory-protect-restore error handling (REM-FIX cycle 3)
- guard restore_file() read_text() against non-UTF-8 crash
- degrade memory-protect-restore instead of crashing on unresolvable PostToolUse target
- deny Edit/Write targets whose path cannot be resolved

### Refactoring

- localize the confinement cwd-resolve guard and fix its log label

### Documentation

- record ADR 0036 guard resolve-crash hardening

## [1.6.0] - 2026-09-16

### Features

- add side-effect-free project_root to the live-workflow lookup chain
- collect workspace members in ai-first-setup provisioning interview
- wire membership-gated workspace-tier memory grant into Edit/Write guard
- add resolve_workspace_memory_paths with symlink-escape hardening
- add continue-past-non-member discover_workspace_root() and workspace_memory_writable()
- add is_workspace_member() multi-segment-capable ownership predicate
- add workspace-config reader and multi-segment-capable member-path validator to hooklib

### Fixes

- decouple project_tier fallback from root_state in protected-path helpers
- anchor memory-write permit check to trusted cwd, not CLAUDE_PROJECT_DIR
- anchor memory-file and workflow-JSON protection to trusted payload cwd
- skip foreign-workflow wf_uuid/phase log lookup when cwd unresolvable
- anchor worktree_path resolution to trusted payload cwd
- close skill-ledger literal drift risk, correct docstring inversion
- anchor skill-ledger and skill-promotion protection to trusted payload cwd
- anchor reliability-gates predicate to trusted payload cwd
- anchor reliability-gates protection to trusted payload cwd
- sweep remaining python3 -c string-interpolation bug in ai-first-setup Step 5
- pass workspace_root/CRAFTFLOW_INSTALL as argv, not interpolated python -c strings
- anchor memory-finalize permit check to cwd, not CLAUDE_PROJECT_DIR
- is_workspace_member() must not log for the ordinary members-key-absent case (M-6)

### Documentation

- record ADR 0035 cwd-identity confinement fix
- record ADR 0033 workspace membership allowlist
- cross-reference workspace membership provisioning and lock router read-side divergence
- document workspace membership boundary and read-side exemption
- land agent-teams-ai borrowable-ideas research

### Tests

- cover B24 symlinked-requesting-path case; document no-outer-catch design choice
- lock NUL-byte and duplicate/self-entry handling for is_workspace_member

### Chores

- land pending workspace changes (metrics dashboard, event-log host tagging, demo guide)

### Other

- docs+test(craftflow): close 2 re-review/re-hunt MEDIUM nits on is_workspace_member() logging discipline

## [1.5.0] - 2026-09-15

### Features

- sync code-generation ladder with upstream ponytail

### Documentation

- add next-steps tracker and craftflow-plugin guardrails summary
- fix stale agent count and script-name references

## [1.4.0] - 2026-08-28

### Features

- add workspace-tier discovery to router memory load (both hosts)
- add workspace-setup skill for workspace-tier memory provisioning
- add craftflow_workspace_init.py for workspace-tier memory provisioning

### Fixes

- close command-injection risk and stale-doc gaps in workspace-setup skill

### Refactoring

- hoist argparse import to top-level in craftflow_workspace_init.py

## [1.3.2] - 2026-08-27

### Fixes

- dispatch housekeeping agents on haiku
- drop duplicate prose report from write-agent output

## [1.3.1] - 2026-08-26

### Fixes

- cap+archive project-tier ## Last Updated / ## Completed

## [1.3.0] - 2026-08-25

### Features

- wire plan-bakeoff-judge into SKILL.md contract/dispatch tables
- PLAN bake-off fan-out, judge dispatch, degraded-candidate tolerance
- PLAN scout-then-qualify branch, stem derivation, N parsing, artifact defaults
- planner supports router-supplied Target Plan File override
- add plan-bakeoff-judge agent and contract overlay

### Fixes

- correct plan-bakeoff-judge field list and step 5b cross-reference

### Documentation

- regenerate architecture graph for plan-bakeoff-judge agent

## [1.2.1] - 2026-08-23

### Fixes

- wire ai-craft repo-root marketplace.json into the version-consistency surface

## [1.2.0] - 2026-08-23

### Features

- version-first staleness check in craftflow:update + release ADR

## [1.1.0] - 2026-08-23

### Features

- heads-up permission-prompt note in cursor-router dispatch template
- dispatch-only CI publish workflow and release live-harness manifest
- version bump writers, CHANGELOG generation, and CLI
- conventional-commit bump classifier (pure core)
- standalone version-consistency gate extracted from harness audit

### Chores

- add version-surface baseline (README line + CHANGELOG)

## [1.0.0] - 2026-06-14

### Added

- Initial tracked release. This section is a baseline marker for the automated
  release pipeline; it does not enumerate the plugin's pre-automation history.
  See `git log -- tools/craftflow-plugin/` for the full record.
