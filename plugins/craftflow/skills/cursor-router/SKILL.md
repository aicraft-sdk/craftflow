---
name: cursor-router
description: |
  Craftflow router for Cursor AI agent mode. Inline sequential execution only —
  no TaskCreate, no Agent(), no Skill() tools. All phases execute in one Cursor turn
  by Read()-ing agent .md files and following their instructions inline.
---

# Craftflow Router — Cursor Inline Mode

**You are running in Cursor AI agent mode.** The Craftflow orchestration system has
detected this via `CRAFTFLOW_PLATFORM: cursor` injected by the MDC rule.

This file replaces the Claude Code router for Cursor sessions. Everything here is
designed for Cursor's tool set: `Read`, `Edit`, `Write`, `Bash`, `Grep`, `Glob`.
There are no `TaskCreate`, `TaskUpdate`, `TaskList`, `Agent()`, or `Skill()` tools.

**Core contract:** All workflow phases run sequentially in a single Cursor agent turn.
Each phase is executed by Read()-ing the agent's .md file and following its instructions
inline in this context.

## Agent File Paths

All agent .md files live under:
`tools/craftflow-plugin/plugins/craftflow/agents/`

| Agent role | File path |
|-----------|-----------|
| component-builder | `tools/craftflow-plugin/plugins/craftflow/agents/component-builder.md` |
| bug-investigator | `tools/craftflow-plugin/plugins/craftflow/agents/bug-investigator.md` |
| code-reviewer | `tools/craftflow-plugin/plugins/craftflow/agents/code-reviewer.md` |
| silent-failure-hunter | `tools/craftflow-plugin/plugins/craftflow/agents/silent-failure-hunter.md` |
| integration-verifier | `tools/craftflow-plugin/plugins/craftflow/agents/integration-verifier.md` |
| planner | `tools/craftflow-plugin/plugins/craftflow/agents/planner.md` |
| plan-gap-reviewer | `tools/craftflow-plugin/plugins/craftflow/agents/plan-gap-reviewer.md` |

## 1. Intent Routing

Route using the first matching signal:

| Priority | Signal | Keywords | Workflow | Chain |
|----------|--------|----------|----------|-------|
| 1 | ERROR | error, bug, fix, broken, crash, fail, debug, troubleshoot, issue | DEBUG | bug-investigator → code-reviewer → integration-verifier |
| 2 | PLAN | plan, design, architect, roadmap, strategy, spec, brainstorm | PLAN | planner → plan-gap-reviewer (1-2 passes) |
| 3 | REVIEW | review, audit, analyze, assess, "is this good" | REVIEW | code-reviewer |
| 4 | DEFAULT | Everything else | BUILD | component-builder → code-reviewer → silent-failure-hunter → integration-verifier |

Rules:
- ERROR always wins over BUILD.
- REVIEW is advisory only. Never let REVIEW create implementation tasks.
- BUILD uses a risk scan: if no risk keywords match, fast path (builder → verifier only).
  Risk keywords: auth, payment, migration, schema, security, concurrent, race, rollback.
- Before execution, output one line: `-> {WORKFLOW} workflow (signals: {matched keywords})`

## 2. Memory Load

Run this before routing. Memory lives at `.craftflow/state/`.

```
1. Bash("mkdir -p .craftflow/state/project")
2. Read(".craftflow/state/project/activeContext.md")
3. Read(".craftflow/state/project/patterns.md")
4. Read(".craftflow/state/project/progress.md")
5. If resuming a known workflow, also read:
   Read(".craftflow/state/cursor-wf.json")
6. Fallback: if project/ files are missing, read root-flat files:
   Read(".craftflow/state/activeContext.md")
   Read(".craftflow/state/patterns.md")
   Read(".craftflow/state/progress.md")
```

Required sections per file:

| File | Required Sections |
|------|-------------------|
| `activeContext.md` | `## Current Focus`, `## Recent Changes`, `## Next Steps`, `## Decisions`, `## Learnings`, `## References`, `## Blockers`, `## Session Settings`, `## Last Updated` |
| `progress.md` | `## Current Workflow`, `## Tasks`, `## Completed`, `## Verification`, `## Last Updated` |
| `patterns.md` | `## User Standards`, `## Common Gotchas`, `## Project SKILL_HINTS`, `## Last Updated` |

If a required section is missing, create it before proceeding.

JUST_GO rule: Read `activeContext.md ## Session Settings`. If `AUTO_PROCEED: true`, skip
all optional clarification gates and auto-select recommended defaults.

## 3. Workflow Preparation

Before starting any workflow:
- Read `activeContext.md ## References` to find plan, design, and research files.
- Read `activeContext.md ## Decisions` for prior clarifications.
- Read `progress.md ## Current Workflow` for pending work that should resume.
- Check `.craftflow/state/cursor-wf.json` for an active in-progress workflow.

Resume check: If cursor-wf.json exists with `"status"` entries that are not all
`"completed"`, you have an in-progress workflow. Resume it by reading the phase list
and cursor position, then continue from the first non-completed phase.

Intent Readiness Gate (PLAN and BUILD only):
Before executing, verify:
1. The goal fits in the current context without truncation (if not, decompose first — switch to PLAN).
2. No acceptance criterion contradicts a stated constraint.
3. Every acceptance criterion maps to a verifiable scenario.

If criteria fail, halt and ask the user for clarification before proceeding.

## 4. Workflow Artifact Creation

On new workflow start:

**Step 4a:** Generate a workflow UUID:
```
wf_id = "wf-" + UTC_timestamp + "-" + 8_hex_chars
```
Example: `wf-20260630-140000-a8b3c4d5`

**Step 4b:** Write the lightweight cursor workflow file:
```bash
# .craftflow/state/cursor-wf.json
```
```json
{
  "wf": "{wf_id}",
  "type": "{WORKFLOW_TYPE}",
  "phases": ["{agent1}", "{agent2}", "..."],
  "cursor": 0,
  "status": {"{agent1}": "pending", "{agent2}": "pending"},
  "plan_file": "{plan_file_or_null}",
  "created_at": "{iso_timestamp}",
  "updated_at": "{iso_timestamp}"
}
```

**Step 4c:** Write the main workflow artifact (same format as Claude Code — required for
hook compatibility and session resume via craftflow_sessionstart_context.py):
```
Write(
  file_path=".craftflow/state/workflows/{wf_id}.json",
  content="{...standard workflow artifact JSON with workflow_uuid, workflow_type, etc.}"
)
```

Use the minimal artifact schema:
```json
{
  "workflow_uuid": "{wf_id}",
  "workflow_id": "{wf_id}",
  "workflow_type": "{WORKFLOW_TYPE}",
  "state_root": ".craftflow/state",
  "user_request": "{request}",
  "plan_file": null,
  "phase_status": {},
  "phase_cursor": null,
  "pending_gate": null,
  "status_history": [{"event": "workflow_started", "ts": "{iso_timestamp}", "phase": "{type}"}],
  "created_at": "{iso_timestamp}",
  "updated_at": "{iso_timestamp}"
}
```

## 5. Cursor Inline Execution Loop

This is the core of Cursor Inline Mode. Replace all `Agent()` and `TaskCreate()` calls
with this loop:

```
For each phase in workflow chain:
  1. Emit PHASE-START progress block (see § 7)
  2. Read the agent .md file (path from § Agent File Paths table above)
  3. Execute the agent instructions inline in this same context
     - You ARE the agent. Follow its instructions as written.
     - Use only Cursor tools: Read, Edit, Write, Bash, Grep, Glob
     - Do not spawn sub-agents or call Skill()
  4. Capture the Router Contract YAML from agent output
  5. Validate the contract (see § 6)
  6. Update cursor-wf.json: set phase status to "completed" (or "failed")
     Update ".craftflow/state/cursor-wf.json" → set status["{phase}"] = "completed"
     and cursor = cursor + 1
  7. Emit PHASE-COMPLETE progress block (see § 7)
  8. If validation failed: stop, emit BLOCKED progress block, ask user for direction
  9. Proceed to next phase
End loop → proceed to § 8 Memory Finalization
```

### Phase chains by workflow type

**BUILD (fast path):** component-builder → integration-verifier
**BUILD (standard — risk keywords matched):** component-builder → code-reviewer → silent-failure-hunter → integration-verifier
**DEBUG:** bug-investigator → code-reviewer → integration-verifier
**REVIEW:** code-reviewer (advisory output only)
**PLAN:** planner → plan-gap-reviewer

### Simplified execution model (v1 differences from Claude Code)

In v1 Cursor Inline Mode, the following Claude Code features are NOT supported:
- Parallel agent execution (code-reviewer + silent-failure-hunter run sequentially)
- Worktree isolation (git worktree commands skipped — work in main tree)
- Doubt-verifier cycle (deferred to v2)
- Research orchestration (deferred to v2)
- Doc-syncer phase (deferred to v2)

These features are deferred — not removed. When they are implemented, they will be
added to this file without touching Claude Code files.

## 6. Post-Agent Validation

After each agent phase completes inline, validate its output before proceeding.

### Contract extraction

Look for the `### Router Contract (MACHINE-READABLE)` fenced YAML block in the agent output.

If absent: treat the phase as invalid. Stop workflow, emit BLOCKED block, ask user.

### Verdict by agent

| Agent | Pass condition |
|-------|---------------|
| component-builder | `STATUS=PASS`, `PHASE_EXIT_READY=true`, `PROOF_STATUS=passed`, non-empty `SCENARIOS` |
| bug-investigator | `STATUS=FIXED`, non-empty `SCENARIOS`, at least one `Regression:` scenario |
| code-reviewer | `## Review: Approve` (no critical issues) |
| silent-failure-hunter | `## Error Handling Audit: CLEAN` (no critical issues) |
| integration-verifier | `## Verification: PASS` (scenario totals reconcile with evidence) |
| planner | `STATUS=PLAN_CREATED` or `STATUS=DECISION_RFC_CREATED`, `GATE_PASSED=true`, `OPEN_DECISIONS=[]` |
| plan-gap-reviewer | `BLOCKING_FINDINGS_COUNT=0`, `REPLAN_NEEDED=false` |

### Override rules

- code-reviewer `Approve` + critical issues = block (treat as `Changes Requested`)
- integration-verifier `PASS` + critical issues = block
- planner `PLAN_CREATED` requires non-empty `SCENARIOS` and non-empty `PLAN_FILE`

### When a phase fails

1. Emit BLOCKED progress block showing which phase failed and why
2. Write failure details to cursor-wf.json:
   `status["{phase}"] = "failed"`, `pending_gate = "phase_{phase}_failed"`
3. Ask the user: "Phase {phase} failed. Options: (a) retry this phase, (b) skip and continue, (c) stop workflow"
4. Do NOT auto-remediate without user direction in v1

## 7. Progress Blocks

Emit these as plain text in Cursor chat. Use Unicode box-drawing characters.

### Phase-start block

Emit before executing each agent phase:

```
╔══ CRAFTFLOW {TYPE} ════════════════════════════════╗
║ Phase {N} / {TOTAL} · {phase-name}                 ║
╠════════════════════════════════════════════════════╣
║ {status_symbol} {phase1}    {status_label}         ║
║ {status_symbol} {phase2}    {status_label}         ║
║ ...                                                 ║
╚════════════════════════════════════════════════════╝
```

Status symbols: `✅` = DONE, `⏳` = RUNNING..., `○` = WAITING, `❌` = FAILED

### Phase-complete block

Emit after validating each agent phase:

Same format as phase-start, but the current phase shows its result:
- Approved: `✅ {phase}    APPROVE ({N} critical)`
- Pass: `✅ {phase}    PASS ({N}/{M} scenarios)`
- Done: `✅ {phase}    DONE`
- Clean: `✅ {phase}    CLEAN` (use for silent-failure-hunter when `## Error Handling Audit: CLEAN` verdict confirmed)
- Failed: `❌ {phase}    BLOCKED — {brief reason}`

### Workflow-complete block

Emit after all phases complete successfully:

```
╔══ CRAFTFLOW {TYPE} · COMPLETE ═════════════════════╗
║ ✅ {phase1}    {result}                             ║
║ ✅ {phase2}    {result}                             ║
║ ...                                                 ║
╠════════════════════════════════════════════════════╣
║ Workflow: {wf_id}                                  ║
║ Plan: {plan_file or "N/A"}                         ║
╚════════════════════════════════════════════════════╝
```

## 8. Memory Finalization

After all phases complete, write memory. This is identical to Claude Code memory finalization.

```
1. Collect MEMORY_NOTES from each agent's Router Contract YAML
2. Write to .craftflow/state/workflows/{wf_id}/activeContext.md (learnings)
3. Write to .craftflow/state/project/patterns.md ## Common Gotchas (durable patterns)
4. Write to .craftflow/state/workflows/{wf_id}/progress.md ## Verification (evidence)
5. Update .craftflow/state/project/activeContext.md ## Recent Changes
6. Update .craftflow/state/project/progress.md ## Completed (one-line summary)
7. Write final status to .craftflow/state/workflows/{wf_id}.json
```

State write order matters: project/ writes are the last step. Never write project/
memory before workflow/ memory — incomplete workflow memory would pollute project state.

## 9. Agent Execution Overrides (Cursor)

When you Read() an agent .md file and execute its instructions inline, some instructions
were written for the Claude Code sub-agent context and do not apply here. Apply these
overrides every time you encounter the following patterns inside an agent file:

### TaskUpdate override
When an agent file contains `TaskUpdate(...)` or says:
  "CRITICAL: You MUST call the TaskUpdate tool directly"
  "Writing a text message claiming completion is NOT sufficient"
→ **Skip it.** In Cursor Inline Mode, phase completion is determined by the Router Contract
  YAML block you capture in § 5 Step 4. Emitting that YAML block IS the completion
  mechanism — no tool call is needed or possible.

### Skill() override
When an agent file contains `Skill(skill="craftflow:X")` or says
  "invoke each skill via Skill(skill='{name}')":
→ **Replace with:** `Read("tools/craftflow-plugin/plugins/craftflow/skills/X/SKILL.md")`
  and follow that skill file's instructions inline immediately before continuing.

### "Text is insufficient" override
When an agent file says "TaskUpdate is NOT sufficient", "writing text is insufficient",
or any variant of "tool call must execute" — that instruction applies only in the
Claude Code sub-agent context where TaskUpdate is a real tool.
→ **In Cursor Inline Mode:** emitting the Router Contract YAML block is both necessary
  and sufficient. No additional tool call is needed.

### Tool-not-available override
When an agent file contains `TaskList()`, `TaskGet()`, or `Agent(...)` calls:
→ **Skip/ignore them.** These tools do not exist in Cursor. Proceed with the next
  instruction in the agent file.

## 10. Hard Rules (Cursor)

- NEVER call TaskCreate, TaskUpdate, TaskList, Agent(), or Skill() — they do not exist in Cursor
- NEVER spawn sub-agents or background agents
- NEVER skip the progress block emission — it is the user's only visibility into phase progress
- NEVER advance to the next phase if post-agent validation fails
- NEVER report a workflow complete without memory finalization
- NEVER modify agent .md files, existing SKILL.md files, or hooks/hooks.json
- ALWAYS write cursor-wf.json after each phase completes
- ALWAYS write the main workflow artifact for hook and resume compatibility
- If context window grows too large (150K+ tokens), warn in progress block:
  "⚠️ Context is large — consider breaking this request into smaller phases"
  Then ask the user whether to continue or stop
