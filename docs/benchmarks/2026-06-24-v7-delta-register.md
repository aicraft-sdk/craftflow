# V7 Delta Register

Baseline: `v7.7.0`

- Critical files reviewed: `20`
- Supporting changed files: `0`
- Non-critical changed files: `0`

| Path | Category | Verdict | Delta Summary |
|------|----------|---------|---------------|
| `plugins/craftflow/skills/craftflow-router/SKILL.md` | router | better | stable workflow identity independent of task ids; sequential phase gating; explicit instruction precedence enforcement; state root is the shared memory namespace |
| `plugins/craftflow/agents/planner.md` | planner | better | open decisions are first-class; differences from agreement are explicit; planner blocks on high-impact ambiguity |
| `plugins/craftflow/agents/component-builder.md` | builder | better | phase-level completion reporting; phase exit evidence gate; sequential execution contract |
| `plugins/craftflow/agents/bug-investigator.md` | debugger | better | blast-radius scan is mandatory; variant coverage is explicit; debug memory reads are versioned |
| `plugins/craftflow/agents/integration-verifier.md` | verifier | better | verifier emits explicit machine-readable contract envelope; verifier treats upstream approvals as inputs, not proof; verifier obeys router-owned internal skill precedence |
| `plugins/craftflow/agents/code-reviewer.md` | reviewer | better | review output is structured for router handling; review can signal revert-worthy state; review integrates with scoped remediation |
| `plugins/craftflow/agents/silent-failure-hunter.md` | hunter | better | hunter reads only state memory; hunter obeys router-owned internal skill precedence; hunter must state scan coverage and blind spots |
| `plugins/craftflow/scripts/craftflow_hooklib.py` | hooks | better | hook state root is versioned; hooks prefer stable workflow identity; hook paths centralize v10 state |
| `plugins/craftflow/scripts/craftflow_pretooluse_guard.py` | hooks | better | memory protection is scoped to versioned state |
| `plugins/craftflow/scripts/craftflow_posttooluse_artifact_guard.py` | hooks | better | artifact guard expects stable ids; artifact guard validates phase-aware state; artifact guard validates v10 schema |
| `plugins/craftflow/scripts/craftflow_sessionstart_context.py` | hooks | better | session context is version-aware; resume context includes phase cursor |
| `plugins/craftflow/scripts/craftflow_task_completed_guard.py` | hooks | better | task metadata enforcement remains explicit |
| `plugins/craftflow/scripts/craftflow_harness_audit.py` | harness | better | harness covers workflow identity fixture; harness covers blocked phase fixture; harness validates planner trust fields |
| `plugins/craftflow/scripts/craftflow_workflow_replay_check.py` | harness | better | replay fixtures require stable workflow identity; replay validates debug generalization; replay validates phase gating |
| `plugins/craftflow/skills/frontend-patterns/SKILL.md` | internal-skill | better | frontend patterns are advisory instead of authoritative; user/project standards outrank style guidance |
| `plugins/craftflow/skills/debugging-patterns/SKILL.md` | internal-skill | better | debugging patterns are advisory instead of authoritative; debugging guidance includes nearby duplicate scan |
| `plugins/craftflow/skills/architecture-patterns/SKILL.md` | internal-skill | better | architecture patterns are advisory instead of authoritative; approved design outranks architecture heuristics |
| `docs/router-invariants.md` | docs | unclear | No clear trust-contract improvement detected automatically. |
| `README.md` | docs | unclear | No clear trust-contract improvement detected automatically. |
| `CHANGELOG.md` | docs | unclear | No clear trust-contract improvement detected automatically. |

## Backlog Seed

- `docs/router-invariants.md`: unclear — review manually and decide whether to improve or keep as-is.
- `README.md`: unclear — review manually and decide whether to improve or keep as-is.
- `CHANGELOG.md`: unclear — review manually and decide whether to improve or keep as-is.

## Evidence Notes

### `plugins/craftflow/skills/craftflow-router/SKILL.md`
- Current: line 39 matches `workflow_uuid` -> `- **workflows/{wf-id}/** — per-workflow isolated state (current focus, active phase, in-flight tasks). Load only when a `workflow_uuid` is already known (resume path).`
- `v7.7.0`: no matching baseline evidence

### `plugins/craftflow/agents/planner.md`
- Current: line 218 matches `OPEN_DECISIONS` -> `| "I'll clarify the details during build" | Details left for build become hidden assumptions. Builder stops on ambiguity — surface it in OPEN_DECISIONS or return NEEDS_CLARIFICATION. |`
- `v7.7.0`: no matching baseline evidence

### `plugins/craftflow/agents/component-builder.md`
- Current: line 90 matches `PHASE_STATUS` -> `If any of these are missing from a non-trivial approved phase, stop and return `STATUS: FAIL` with `PHASE_STATUS: blocked`. Do not invent a hidden phase contract.`
- `v7.7.0`: no matching baseline evidence

### `plugins/craftflow/agents/bug-investigator.md`
- Current: line 215 matches `BLAST_RADIUS_SCAN` -> `| "I fixed the repro — adjacent duplicates are deferred" | Deferred duplicates must be named in BLAST_RADIUS_SCAN.result and MEMORY_NOTES.deferred. Silent omission is a false FIXED. |`
- `v7.7.0`: no matching baseline evidence

### `plugins/craftflow/agents/integration-verifier.md`
- Current: line 93 matches `CONTRACT {"s":"PASS","b":false,"cr":0}` -> ``CONTRACT {"s":"PASS","b":false,"cr":0}``
- `v7.7.0`: no matching baseline evidence

### `plugins/craftflow/agents/code-reviewer.md`
- Current: line 237 matches `REMEDIATION_NEEDED` -> `- REMEDIATION_NEEDED: [true if BUILD/DEBUG should create a REM-FIX]`
- `v7.7.0`: no matching baseline evidence

### `plugins/craftflow/agents/silent-failure-hunter.md`
- Current: line 25 matches `.craftflow/state` -> `- Memory files (`.craftflow/state/*.md`) are managed by the router, not this agent.`
- `v7.7.0`: no matching baseline evidence

### `plugins/craftflow/scripts/craftflow_hooklib.py`
- Current: line 10 matches `STATE_VERSION = "v10"` -> `STATE_VERSION = "v10"`
- `v7.7.0`: no matching baseline evidence

### `plugins/craftflow/scripts/craftflow_pretooluse_guard.py`
- Current: line 21 matches `state_root()` -> `paths = {state_root() / name for name in PROTECTED_MEMORY_FILES}`
- `v7.7.0`: no matching baseline evidence

### `plugins/craftflow/scripts/craftflow_posttooluse_artifact_guard.py`
- Current: line 15 matches `workflow_uuid` -> `"workflow_uuid",`
- `v7.7.0`: no matching baseline evidence

### `plugins/craftflow/scripts/craftflow_sessionstart_context.py`
- Current: line 27 matches `CRAFTFLOW v10 workflow context` -> `f"CRAFTFLOW v10 workflow context ({source}): "`
- `v7.7.0`: no matching baseline evidence

### `plugins/craftflow/scripts/craftflow_task_completed_guard.py`
- Current: line 14 matches `REQUIRED_METADATA` -> `REQUIRED_METADATA = (`
- `v7.7.0`: no matching baseline evidence

### `plugins/craftflow/scripts/craftflow_harness_audit.py`
- Current: line 204 matches `workflow-identity-v10.json` -> `"workflow-identity-v10.json",`
- `v7.7.0`: no matching baseline evidence

### `plugins/craftflow/scripts/craftflow_workflow_replay_check.py`
- Current: line 46 matches `workflow_uuid` -> `"workflow_uuid",`
- `v7.7.0`: no matching baseline evidence

### `plugins/craftflow/skills/frontend-patterns/SKILL.md`
- Current: line 15 matches `advisory in v10` -> `This skill is advisory in v10. Explicit user instructions, `CLAUDE.md`, repo standards, and approved plans override every suggestion here.`
- `v7.7.0`: no matching baseline evidence

### `plugins/craftflow/skills/debugging-patterns/SKILL.md`
- Current: line 15 matches `advisory in v10` -> `This skill is advisory in v10. It deepens investigation quality. It does not authorize local-only patches, guesswork, or "fix the line that crashed" thinking.`
- `v7.7.0`: no matching baseline evidence

### `plugins/craftflow/skills/architecture-patterns/SKILL.md`
- Current: line 16 matches `advisory in v10` -> `This skill is advisory in v10. It frames decisions and tradeoffs; it does not outrank explicit user requirements, repo standards, or an approved plan/design doc.`
- `v7.7.0`: no matching baseline evidence

### `docs/router-invariants.md`
- Current: no automatic evidence match
- `v7.7.0`: no matching baseline evidence

### `README.md`
- Current: no automatic evidence match
- `v7.7.0`: no matching baseline evidence

### `CHANGELOG.md`
- Current: no automatic evidence match
- `v7.7.0`: no matching baseline evidence

