# CRAFTFLOW Benchmark Report

Date: 2026-08-10

## Method

This is a measurable signal benchmark, not proof of absolute superiority.
Repos are scored against trust-critical harness properties: orchestration ownership, durable state, plan/build trust gates, skill precedence, debug generalization, fail-closed verification, and deterministic replay coverage.

## Current vs Stable v7

- Stable baseline: `craftflow-v7.7.0-stable` @ `unknown`
- Current repo HEAD: `fc086077cf38`
- Files changed since `v7.7.0`: `0`
- Critical harness files changed since `v7.7.0`: `0`
- Critical changed file sample:

## Scoreboard

| Repo | HEAD | Score |
|------|------|-------|
| craftflow-current | `fc086077cf38` | 33/33 |

## CRAFTFLOW Current Signal Coverage

- `router_orchestrator`: PASS (Dedicated router/orchestrator entry point)
- `specialist_agents`: PASS (Named specialist agents/subagents)
- `skills_layer`: PASS (Reusable local skill layer)
- `plugin_hooks`: PASS (Hook-based runtime guardrails)
- `workflow_artifacts`: PASS (Durable workflow artifact model)
- `versioned_state`: PASS (Versioned state root or namespace)
- `stable_workflow_identity`: PASS (Stable workflow UUID/ULID-like identity)
- `plan_trust_gate`: PASS (Explicit plan trust / open-decision gate)
- `phase_gating`: PASS (Sequential phase cursor / phase-exit gating)
- `failure_stop`: PASS (Explicit stop-on-failure behavior)
- `skill_precedence`: PASS (User/project standards outrank internal patterns)
- `debug_generalization`: PASS (Blast-radius / generalized debug remediation)
- `fail_closed_verification`: PASS (Fail-closed verification and evidence contracts)
- `replay_harness`: PASS (Replay fixtures / deterministic harness checks)
- `test_suite`: PASS (Dedicated tests directory)

## Gaps vs References

- Higher score than: `None`
- Tied with: `None`
- Higher-scoring references: `None`

## Interpretation

- Current CRAFTFLOW leads on the specific trust-harness signals it was designed to optimize.
- That does not prove it is universally better than every reference repo.
- It does prove the repo now has stronger explicit machinery around plan trust, phase gating, workflow identity, and replay validation than stable v7 and most pulled references.

## Next Actions

- Add scenario-based golden fixtures for skipped-phase execution and false completion after failed work.
- Expand the benchmark from structural signals to behavior replays where possible for top reference repos.
- Re-run this report after any router/agent contract edit.

