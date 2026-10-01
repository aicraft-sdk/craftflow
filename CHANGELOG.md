# Changelog

All notable changes to the craftflow plugin are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Releases are produced automatically by `.github/workflows/publish-craftflow-plugin.yml`.
Do not hand-edit released sections.

## [1.24.1] - 2026-10-01

### Fixes

- renumber stop-gate spec/ADR to 0020/0057 and tighten jev seam tests
- harden jev endpoint file read, redirects and URL validation
- remove CRAFTFLOW_JEV_ENDPOINT env override; use user-level loopback-only endpoint file

### Documentation

- document jev endpoint file seam and close env override residual

### Tests

- migrate jev tests and live harnesses off CRAFTFLOW_JEV_ENDPOINT to isolated-home endpoint file

## [1.24.0] - 2026-10-01

### Features

- stop-gate continue ACT wiring
- stop-gate report jev scope and arm CLI
- stop-gate ACT decision core
- router stamps session_id into workflow artifacts

### Fixes

- harden stop-gate ACT hook
- harden stop-gate arm CLI status and writes
- harden stop-gate ACT core
- harden session_id stamping and resume rebind
- stop-gate fast path fails open on unreadable stdin

### Performance

- stop-gate off mode exits before heavy imports

### Documentation

- stop-gate continue ACT docs and disclosures

### Tests

- stop-gate continue ACT live proof

## [1.23.0] - 2026-10-01

### Features

- stop-gate calibration report and offline replay
- stop-gate desktop notification and consent-gated push relay
- stop-gate optional Jev text classification (separate consent, fail-open)
- stop-gate hook in shadow mode (off by default)
- stop-gate taxonomy heuristic, verdict combiner, notify/relay/session math
- stop-gate pure core: settings, artifact facts, hard rules

### Fixes

- stop-gate review remediation (error rows, report join, relay cap, git fail-closed)

### Documentation

- stop-gate shadow mode docs and event contract

### Tests

- live proof and timing for stop-gate shadow mode

## [1.22.0] - 2026-09-30

### Features

- wire /compact line and user override into nudge hook, reset and boundary
- code-generated /compact line bound to the session's active workflow
- context-nudge user override loader and precedence (pure core)

### Fixes

- nudge compact binding liveness, fifo/symlink safety, escape parity, document best-effort session binding
- scope compact binding to the owning session and harden lookup/timing driver

### Documentation

- renumber context-nudge ADR to 0054 (0052/0053 taken on main)
- document /compact line and durable user override for the context nudge

### Tests

- live proof for /compact line + user override; ADR-0052 stays proposed

## [1.21.0] - 2026-09-30

### Features

- allow Edit/Write to the exact session scratchpad directory
- add memory_merge --apply (archive-first, atomic, permit-checked) and memory-file validator

### Fixes

- make --apply archive slug/month validation strict
- restrict heredoc body stripping to a strict whole-command python shape
- close $'..' quote-desync and consumer-shadowing bypasses in heredoc stripping
- treat quoted python heredoc bodies as data in the safe-shell guard
- treat read-only python open() of protected files as a read, soften heuristic-only escalation wording
- anchor guard identity to project root when cwd drifts into .craftflow/state

### Documentation

- prefer memory_merge --apply in memory-finalize instructions and fix contradictory stdout guidance

### Tests

- add doc-contract tests for the memory-finalize --apply instructions

## [1.20.1] - 2026-09-30

### Fixes

- correct registry skill search example and promote exit-code docs

## [1.20.0] - 2026-09-29

### Features

- add component-registry hint to cursor-router

## [1.19.0] - 2026-09-29

### Features

- router phase-boundary context check + context_nudge event contract and docs
- context-nudge --boundary phase checkpoint + relay contract
- context-size nudge UserPromptSubmit hook + compact reset (audit by default)
- context-nudge pure core (classify/decide/config/state)
- last_turn_context_tokens transcript helper for context-size nudge

### Tests

- live proof + timing for context-size nudge; ADR-0051 accepted

## [1.18.0] - 2026-09-29

### Features

- add registry-promote skill

### Documentation

- fix registry-promote layout wording and --kind requirement

## [1.17.0] - 2026-09-29

### Features

- component-registry skill, router hint, finishing Promote option

### Fixes

- let component-builder call ai-craft-registry MCP tools

### Chores

- regenerate architecture graph for component-builder tools
- regenerate architecture graph (adds component-registry; absorbs pre-existing drift)

## [1.16.0] - 2026-09-29

### Features

- pin default agent models (opus x5, sonnet x9)
- model report CLI with transcript backfill and phase-2 rubric
- add model usage report core and estimate price table
- log per-dispatch agent_usage telemetry from SubagentStop
- add pure transcript usage summarizer and contract classifier for subagent telemetry

### Documentation

- document agent_usage log event and verify emitter sync

## [1.15.1] - 2026-09-29

### Reverts

- return github-researcher and web-researcher to model inherit (ADR-0047)

## [1.15.0] - 2026-09-29

### Features

- add trust-boundary, test-integrity, and irreversibility guardrails; add failure-digest skill
- pilot haiku model routing on github-researcher and web-researcher (ADR-0043)

### Documentation

- document risk_gate threshold and hook in READMEs (ADR-0044)

## [1.14.0] - 2026-09-28

### Features

- aggregate risk_gate telemetry in craftflow_jev_report.py
- register PreToolUse risk-gate hook in hooks/hooks.json
- wire craftflow_jev_risk_gate.py main() fail-open hook I/O
- add risk_gate pure builders (state/questions/decide/telemetry)
- add craftflow_jev_risk_gate.py allowlist matcher
- add classify_risk_gate() deterministic baseline
- surface features.riskGate in jev setup --status
- ship riskGate:off in committed jev.json default
- add riskGate feature key to jev config normalizer

### Fixes

- log failed risk-gate telemetry append via log_event (REM-FIX HIGH)
- restore sequential redaction chain, move safe-cut to raw-input pre-pass (REM-FIX cycle 9)
- position-tracked redaction closes multi-match cumulative-shrink leak (REM-FIX cycle 8)
- couple risk_gate's backstop and max_chars cuts, correct timing comment (REM-FIX cycle 7)
- bound _SECRET_FLAG separator and derive margin from full match span
- widen redact_action input window to close 100k-boundary credential leak
- bound redact_action's input size in build_state (REM-FIX cycle 3)
- move risk_gate length cap to post-redaction, close straddling-boundary leak

### Refactoring

- eliminate fixed-cut-point-before-matching architecture in risk_gate redaction

### Tests

- add live risk_gate audit round-trip driver + canary scenario (Phase 6, driver-only)

## [1.13.0] - 2026-09-26

### Features

- add before/after impact benchmark for 3 harness capabilities
- wire feature-backlog register/activate/complete/VCR into PLAN and BUILD
- add feature-backlog register/activate/complete lifecycle script
- wire clean-state-check into BUILD pre-merge and DEBUG memory-finalize
- add clean-state-check script (console.log/debugger + diff plumbing)
- wire arch-lint into BUILD/REVIEW code-reviewer dispatch
- add advisory arch-rules linter (craftflow_arch_lint.py)
- nest planner Router Contract into verdict/rationale
- nest component-builder Router Contract into verdict/rationale
- add schema-validated verdict/rationale contract validator (pilot)
- add per-agent verdict/rationale schema tables for component-builder, planner

### Fixes

- raise specific error for backlog entry missing status field
- reject empty/null id on --register, correct exit-code docstring
- normalize persisted feature-backlog file to 0644 permissions
- reject multiple subcommand flags instead of silently collapsing
- dispatch on is-not-None instead of truthiness, reject empty ids
- guard _compute_vcr and text-report loop against malformed list entries
- fail-closed on corrupted feature-backlog instead of silent data loss
- surface special/broken-symlink untracked files, wrap missing git binary as clean GitError
- flush block-comment and line-comment scanners at every file boundary, anchor TODO-ticket regex
- surface nested git repos as skipped, not silently dropped
- tolerate colon-separated TODO ticket style
- flush unterminated block comment at end of scan
- reset block-comment scan state at file boundaries
- harden clean-state-check detection (untracked dirs, debugger ASI, block comments, TODO ticket anchor, py suppression, root-proof test)

### Documentation

- document clean-state-check skipped field, fold into deferred notes
- add harness-self-checks reference file, Arch Lint section
- add before/after impact report for contract schema-validation pilot
- refresh worldclass benchmark snapshot post schema-validation-pilot
- wire pilot contract validator into router post-agent validation

### Tests

- cover feature-backlog VCR N/A case, malformed-JSON recovery, unknown-id exits
- cover clean-state-check TODO/commented-block/eslint-disable cases

## [1.12.0] - 2026-09-26

### Features

- add dual-gate (agreement OR accuracy) promotion to craftflow_jev_report.py

### Fixes

- guard jev replay driver against append-mode data contamination

### Refactoring

- extract _manifest_by_call_id into shared craftflow_jev_report_lib.py

## [1.11.0] - 2026-09-25

### Features

- add jev-on-vs-jev-off A/B comparison report
- add isolated jev replay driver for A/B benchmark corpus
- add jev corpus builder for real-request A/B benchmark

### Fixes

- make default --events path self-documenting when missing in A/B report
- error loudly on missing manifest, disambiguate 0/0 accuracy and invalid-latency from real zeros in A/B report
- resync replay cursor on hook failure, reject partial-parse telemetry rows
- error loudly on missing workflows-dir/invalid limit, add corpus-build summary output

### Tests

- live smoke proof for jev A/B benchmark replay path

## [1.10.0] - 2026-09-25

### Features

- show remediationScope feature+threshold in jev-setup --status
- wire jev-assisted scope call into the 1a-SCOPE router gate (JUST_GO + circuit-breaker guarded)
- report agreement/PROMOTE-HOLD verdict for the remfix_scope jev feature
- add main() CLI shell for jev remfix-scope gate script, fail-closed on persist failure
- add telemetry_row + events.jsonl append (bool-returning) for jev remfix-scope gate
- add pure builders (build_state/build_questions/decide) for jev remfix-scope gate
- add doc-sourced remfix_scope heuristic baseline for jev agreement telemetry
- ship remediationScope:off by default, extend disabled-by-default guard
- add remediationScope feature+threshold to jev config schema

### Fixes

- isolate jev_remfix_scope_roundtrip.py diagnostic log writes to tempdir
- check for an existing matching REM-FIX before creating one in the interrupted jev-auto-decide resume branch
- correlate interrupted jev-auto-decide marker by consumed:true tag, not workflow-wide remfix existence
- avoid duplicate REM-FIX on jev auto-decide, harden structural test, add interrupted-auto-decide resume rule
- escalated-path 1a-SCOPE delegates to Scope resolution instead of duplicating the ask
- catch SystemExit from argparse in jev remfix-scope main(), close audit-mode coverage gap
- pass encoding=utf-8 to remfix_scope drift-guard read_text (matches sibling reads in same file)

### Documentation

- document remediationScope as a shipped Jev feature in README
- document remfix_scope PROMOTE threshold in jev README (Phase 4)

### Tests

- live proof scenario for jev remfix-scope audit round-trip
- structural guard -- jev remfix-scope call site stays isolated to 1a-SCOPE, single file

## [1.9.0] - 2026-09-24

### Features

- jev prompt hint consults the session-scoped auto-detect cache
- add jev auto-detect SessionStart hook (consent ask + session canary)
- add jev-setup --record-consent, --enable/--disable now stamp consent
- add jev session-scoped + cross-session status cache module
- add consent sub-object to jev config schema
- additive budget/failure-reason kwargs on jev client.call()

### Fixes

- gate jev session-cache OR-gate on consent==granted, not declined
- re-check consent live in jev session_is_active OR-gate
- catch and log jev canary notify-delivery failures distinctly
- detect and log jev consent-rollback write that silently no-ops
- roll back jev consent-ask flag when session_context() delivery fails
- close jev consent-ask silent-suppression + timeout margin + cache-merge gaps
- replace assert with checked ValueError for jev consent-status guard
- make jev config writes atomic + validate consent_status at write site
- bound jev session-cache _file_lock() acquisition with a deadline
- serialize jev session-cache read-decide-write per file
- harden jev session cache per silent-failure-hunter findings

### Documentation

- document jev auto-detect consent flow across router/README/skills
- correct comment on canary notify no-rollback rationale

### Tests

- live proof scenario for jev auto-detect consent + session canary
- fix 2 more jev prompt-hint fixtures short-circuited by consent gate

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
