---
name: craftflow-router
description: |
  THE ONLY ENTRY POINT FOR CRAFTFLOW. Activate this skill for build, debug, review, and plan requests.

  Use when the user asks to implement, fix, review, plan, test, refactor, or continue code work.

  Trigger keywords: build, implement, create, write, add, review, audit, debug, fix, error, bug, broken, plan, design, architect, spec, brainstorm, test, refactor, optimize, update, change, research, craftflow, craftflow.

  CRITICAL: Route and execute immediately. Do not stop at describing capabilities.
---

# craftflow Router

**Runtime contract only.** v10 restores trust-first orchestration: route intent, hydrate workflow state, write workflow artifacts, execute the task graph, validate agent output, and fail closed on ambiguity, skipped work, or missing persistence.

Mandatory reference read: before routing (## 1.) or dispatching any agent, read
`tools/craftflow-plugin/plugins/craftflow/skills/_shared/router-protocol.md` once per
session if not already read. It holds the host-agnostic Intent Routing table and the
dispatch prompt scaffold, both `Read()` from there rather than inlined below. A missing or unreadable shared doc is a hard-stop
condition, same as any other required reference read in this file — do not silently
proceed with routing/dispatch decisions from stale in-context memory of its content.

## Reference Read Map

<!-- readmap:begin -->
Generated from `config/router-readmap.json` by `scripts/craftflow_router_readset.py --print-table`; edit the map, not this table. It lists when each runtime file is read today. The sections below carry the rules.

| Class | Read when | File | Workflows | Variant | Happy path |
|---|---|---|---|---|---|
| kernel | skill load | `SKILL.md` | ALL | - | yes |
| kernel | read before routing | `_shared/router-protocol.md` | ALL | - | yes |
| start | workflow start | `references/workflow-artifact-and-hook-policy.md` | ALL | - | yes |
| start | BUILD preparation | `references/build-workflow.md` | BUILD | - | yes |
| start | fast-path routing during BUILD preparation | `references/fast-path.md` | BUILD | - | yes |
| start | PLAN preparation | `references/plan-workflow.md` | PLAN | - | yes |
| start | DEBUG preparation | `references/debug-workflow.md` | DEBUG | - | yes |
| start | REVIEW preparation | `references/review-workflow.md` | REVIEW | - | yes |
| stage | before code-reviewer dispatch / clean-state check | `references/harness-self-checks.md` | BUILD, REVIEW, DEBUG | - | yes |
| stage | remediation or research is triggered | `references/remediation-and-research.md` | ALL | - | no |
| stage | phase exit | `references/context-boundary.md` | BUILD, PLAN | - | yes |
| stage | first agent dispatch effort lookup | `references/fast-path.md` | PLAN, DEBUG, REVIEW | - | yes |
| stage | final-phase verifier PASS (BUILD, worktree_mode == auto_created); BUILD resume with auto_created or any worktree pending_gate (incl. policy_invalid) | `references/worktree.md` | BUILD | - | yes |
| stage | memory task runnable | `references/memory-finalize.md` | ALL | - | yes |
| stage | task_tools_available == false or native Task* failure | `references/task-tool-fallback.md` | ALL | - | no |
| start | before workflow creation | `references/workflow-start.md` | ALL | - | yes |
| stage | resume or live scope marker | `references/resume.md` | ALL | - | no |
| stage | agent returns | `references/post-agent-validation.md` | ALL | - | yes |
| stage | integration-verifier PASS | `references/verify-dispatch.md` | BUILD, DEBUG | - | yes |
| stage | workspace marker found, or a state-read-compaction deny during Memory Load | `references/memory-load-workspace.md` | ALL | - | no |
<!-- readmap:end -->

## 1. Intent Routing

**Shared with Cursor — canonical text lives in
`tools/craftflow-plugin/plugins/craftflow/skills/_shared/router-protocol.md` §
"Intent Routing" (full priority/keyword/chain table, routing rules, announce-line convention).** See `references/fast-path.md`
for the risk-keyword detection table used to choose between BUILD's fast path and full
chain. An optional Jev hint block (Claude Code only; produced by the opt-in
`UserPromptSubmit` hook gated by `config/jev.json`, off by default), when present, is
consulted per the shared doc's hint-precedence rule (ERROR keywords always win).

**Jev auto-detect consent contract:** When a consent-request block appears in `additionalContext` during the optional auto-detect SessionStart hook, execute the embedded `AskUserQuestion` contract before your first substantive reply and run the matching `--record-consent` command. See `router-protocol.md` "## Jev Auto-Detect Consent Request Contract" for the full contract and tag format.

## 0. Resolve Project Root

**Shared with Cursor — canonical text lives in
`tools/craftflow-plugin/plugins/craftflow/skills/_shared/router-protocol.md` §
"Resolve Project Root" (single-repo/multi-repo resolution: the `git rev-parse --show-toplevel`
check, the `1a.` multi-repo branch with its dedicated resolver script, and the
`DETERMINISTIC`/`AMBIGUOUS`/`NO_REPO_FOUND` outcome handling).** Runs once per session,
before `## 1.` routing and before any `.craftflow/state/...` path is touched. `PROJECT_ROOT`
resolved here is reused verbatim by every later step in this document (memory load,
workflow-artifact creation, resume, and — for BUILD only — worktree creation) — never
re-run `git rev-parse --show-toplevel` a second time later in a session.

## 0a. Task Tool Capability Detection

Probe task tools once per session via `references/workflow-start.md`, before `TaskList()` or a workflow write.

When `capabilities.task_tools_available == false`, or a router-owned native Task* call fails mid-session, read `references/task-tool-fallback.md` now and apply it at every `TaskCreate` / `TaskList` / `TaskGet` / `TaskUpdate` call site.

## 2. Memory Load And Template Validation

Always run this before routing or resuming. Memory is organized in three tiers:
- **project/** — long-lived cross-workflow state (architecture decisions, durable patterns, ongoing blockers). Always load first.
- **workflows/{wf-id}/** — per-workflow isolated state (current focus, active phase, in-flight tasks). Load only when a `workflow_uuid` is already known (resume path).
- **workspace/** — long-lived, cross-repo state shared by every project under a configured workspace root. Lowest precedence; loaded only when a workspace marker is discovered.

```text
1. Bash("mkdir -p \"$PROJECT_ROOT/.craftflow/state/project\"")
2-5. (default path) ONE Bash call replaces the per-file Reads of project/{activeContext,patterns,progress}.md,
    project/constitution.md, workspace/*, and workflows/{workflow_uuid}/*, after steps 1, 5a and 6a below
    have resolved which tiers apply:
      Bash("python3 {plugin_root}/scripts/craftflow_state_query.py --mode digest --project-root \"$PROJECT_ROOT\" [--workspace-root \"{workspace_root}\"] [--workflow-uuid {workflow_uuid}]")
    stdout is ONE fixed-size digest (independent of file size, no state-read-compaction deny/retry): per
    `### {tier}/{file} [OK|EMPTY|MISSING|UNREADABLE]` block, `MISSING_SECTIONS:` lines (drives auto-heal),
    the constitution's MUST constraints (active for this session when present), a root-flat tier only when
    project/ is empty (step 7), and ready-made `## Memory Summary` / `## Project Patterns` text for the
    dispatch scaffold. Read a file in full only when the digest is insufficient, via
    `craftflow_state_query.py <path> --mode full`, or raw Read for files under the compaction threshold.
    **Re-read rule:** when the digest shows `(omitted for size` or `(+N more)` for a section the task depends on, or the task depends on prior Decisions/Learnings, re-read that file with `craftflow_state_query.py <path> --mode full` before acting.
5a. Workspace tier (walk up max 3 levels above PROJECT_ROOT): if an ancestor holds
    `.craftflow-workspace.json` or `.craftflow/state/workspace/`, read `references/memory-load-workspace.md`.
6. If workflow_uuid is known (resume path):
   a. Bash("mkdir -p \"$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}\"")
   b-d. pass `--workflow-uuid {workflow_uuid}` to the digest call (covers workflows/{workflow_uuid}/
        activeContext.md, patterns.md, progress.md).
   Merge: workflow-scoped values override project-scoped for current-focus
   fields (## Current Focus, ## Next Steps, ## Tasks) only.
7. Fallback: If project/ files are missing or empty, the digest also includes the root-flat
   files ($PROJECT_ROOT/.craftflow/state/activeContext.md etc.); merge content into project/
   before proceeding. Root-flat files are the backward-compat layer.
```

Do not parallelize step 1 with reads.

Memory Summary / Project Patterns for the dispatch scaffold: take them from the digest's
`## Memory Summary` and `## Project Patterns` blocks (do not re-Read the files for this).

Workspace marker, or `state-read-compaction` deny in Memory Load (`SKILL.md → ## 2. Memory Load And Template Validation`): `references/memory-load-workspace.md`.

If a project/ memory file is missing:
- Create it using the `craftflow:session-memory` template.
- Read it before continuing.

Required sections — shared with Cursor — canonical table lives in
`tools/craftflow-plugin/plugins/craftflow/skills/_shared/router-protocol.md` §
"Memory File Required Sections". `Read()` that file now if you have not already this
session.

Auto-heal rule:
- Insert missing sections before `## Last Updated`.
- After every `Edit(...)`, immediately `Read(...)` and verify the new section exists.

JUST_GO:
- Read `$PROJECT_ROOT/.craftflow/state/project/activeContext.md ## Session Settings`.
- If `AUTO_PROCEED: true`, set `JUST_GO=true`.
- While `JUST_GO=true`, auto-default all non-REVERT AskUserQuestion gates to the recommended option and log the choice in `## Decisions`.
- Exception: the Skill-Distill Approval Flow's `AskUserQuestion` (§ 8) is carved out as a de
  facto REVERT-class gate under JUST_GO — see its own "JUST_GO carve-out" for the fail-closed
  default (`Defer`, never `Approve`).

v10 trust rule:
- `JUST_GO` never overrides explicit user/project standards, open plan decisions, or failure-stop gates — including any gate that explicitly documents its own exception, such as the multi-repo AMBIGUOUS resolution gate in Worktree Isolation.
- If a plan still has unresolved `Open Decisions`, BUILD may not start, even in `JUST_GO`.

## 2a. Workflow Artifact And Hook Policy

Core law:
- Durable router state lives under `$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}.json`
- Companion event log lives under `$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}.events.jsonl`
- Router-owned gates still include `plan_trust_gate`, `phase_exit_gate`, `failure_stop_gate`, `memory_sync_gate`, and `skill_precedence_gate`

Mandatory reference read:
- Before workflow creation, artifact mutation, hook policy changes, or resume logic that depends on artifact fields, immediately read `references/workflow-artifact-and-hook-policy.md`.
- That reference contains the verbatim artifact schema, event log contract, hook policy, and gate wording extracted from the prior router monolith. Treat it as load-bearing orchestration law, not optional background.

**3. Task Metadata Contract** and **Parent workflow creation** (§ 6): before any `TaskCreate()`: `references/workflow-start.md`.

## 4. Resume And Hydration

On resume, or when grep of `project/activeContext.md ## Decisions` finds a live `[SCOPE-DECISION-PENDING:` or unconsumed `[jev-auto-decided:` marker, read `references/resume.md`.

## 5. Workflow Preparation

### Shared preparation

Before creating a new workflow:
- Read `$PROJECT_ROOT/.craftflow/state/project/activeContext.md ## References` to discover `Plan`, `Design`, and prior `Research` files.
- Read `$PROJECT_ROOT/.craftflow/state/project/activeContext.md ## Decisions` for prior planner/build clarifications.
- Read `$PROJECT_ROOT/.craftflow/state/project/progress.md ## Current Workflow` and `## Tasks` for pending work that should resume instead of duplicating.
- Read the latest `$PROJECT_ROOT/.craftflow/state/workflows/*.json` artifact if one exists for the current conversation.

**Script directory (DD-9):** `CRAFTFLOW_SCRIPTS` = the router skill's Base directory + `/../../scripts`; fall back to `$CRAFTFLOW_INSTALL/scripts` only when that directory lacks the script you need. The Skill tool can serve a different cached version than `installed_plugins.json` names.

**Intent Readiness Gate (MANDATORY before PLAN or BUILD):**
Before dispatching to planner or builder, verify the intent contract meets three conditions:
1. **Context-bounded:** The full intent (goal + constraints + acceptance criteria) fits within the agent's prompt scaffold without truncation. If the intent requires loading more than 5 source files to be understood, decompose first (switch to PLAN).
2. **Contradiction-free:** No acceptance criterion contradicts a stated constraint or non-goal. If contradictions exist, halt and persist `pending_gate="intent_contradiction"`.
3. **Sufficiently specific:** Every acceptance criterion maps to at least one verifiable scenario. If a criterion is unverifiable ("make it better" without a metric), halt and ask for specificity.

Router-owned interface fields:
- `plan_mode`: `direct` | `execution_plan` | `decision_rfc`
- `verification_rigor`: `standard` | `critical_path`
- `checkpoint_type`: `none` | `human_verify` | `decision` | `human_action`
- `proof_status`: `passed` | `gaps_found` | `human_needed`

### BUILD preparation

- Before any BUILD-specific readiness decision or child-task creation, immediately read `references/build-workflow.md`.
- Use the `### BUILD preparation` and `### BUILD task graph` blocks in that file as the canonical BUILD law.
- Before fast-path routing (performed during BUILD preparation), read `references/fast-path.md` for the canonical keyword table, agent dispatch table, gate table, and escalation protocol.
- Before any `code-reviewer` dispatch (BUILD or REVIEW), read `references/harness-self-checks.md` for the arch-lint wiring; the detail lives there, not here, to keep this file from growing further.

4. Final-phase `integration-verifier` PASS, before memory-finalize, `worktree_mode == "auto_created"`:
   read `references/worktree.md`, run its finalize call.

### DEBUG preparation

- Before any DEBUG-specific readiness decision or child-task creation, immediately read `references/debug-workflow.md`.
- Use the `### DEBUG preparation` and `### DEBUG task graph` blocks in that file as the canonical DEBUG law.

### REVIEW preparation

- Before any REVIEW-specific readiness decision or child-task creation, immediately read `references/review-workflow.md`.
- Use the `### REVIEW preparation` and `### REVIEW task graph` blocks in that file as the canonical REVIEW law.

### PLAN preparation

- Before any PLAN-specific readiness decision or child-task creation, immediately read `references/plan-workflow.md`.
- Use the `### PLAN preparation` and `### PLAN task graph` blocks in that file as the canonical PLAN law.
- If planner clarification, review-loop findings, or plan remediation rules trigger later in the workflow, also read `references/remediation-and-research.md` before continuing.

## 6. Workflow Task Graphs

### BUILD task graph

- See `references/build-workflow.md` and apply its `### BUILD task graph` block verbatim before creating BUILD child tasks.

### DEBUG task graph

- See `references/debug-workflow.md` and apply its `### DEBUG task graph` block verbatim before creating DEBUG child tasks.

### REVIEW task graph

- See `references/review-workflow.md` and apply its `### REVIEW task graph` block verbatim before creating REVIEW child tasks.

### PLAN task graph

- See `references/plan-workflow.md` and apply its `### PLAN task graph` block verbatim before creating PLAN child tasks.

### Research tasks

- When a workflow explicitly triggers research task creation, immediately read `references/remediation-and-research.md`.
- Use the `## 10. Research Orchestration`, `## Research Quality`, and `## Research Files` blocks there before creating or consuming research tasks.

### Marker rules

- BUILD writes `[BUILD-START: wf:{workflow_uuid}]`
- DEBUG writes `[DEBUG-RESET: wf:{workflow_uuid}]`
- PLAN writes `[PLAN-START: wf:{workflow_uuid}]`

## 7. Dispatcher And Agent Prompt Contract

### Explicit dispatcher

**Shared with Cursor — canonical text lives in
`tools/craftflow-plugin/plugins/craftflow/skills/_shared/router-protocol.md` §
"Explicit Dispatcher (Phase-to-Agent Table)" (full phase→agent mapping).** Claude Code resolves each row via `Task()`/`TaskCreate()`
against a registered subagent type using the `craftflow:` name directly — no host-specific
resolution step needed here, unlike Cursor's `## Agent File Paths` literal-path mapping.

`skill-author` is deliberately NOT a `kind:remfix` origin. A `skill-distill` task returning
`STATUS: FAIL` (or never returning) is not a code defect for `component-builder` to fix — it
is a proposal-authoring/availability issue. See `## 6.` and `references/build-workflow.md → #### Skill-Distill Gate`
for the exact non-blocking handling: no REM-FIX is created, the chain is never stalled
waiting on this agent.

### Prompt scaffold for every agent

**Shared with Cursor — canonical text lives in
`tools/craftflow-plugin/plugins/craftflow/skills/_shared/router-protocol.md` § "Dispatch
Prompt Scaffold" (full field list: `## Task
Context` including the `$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}.json`
Workflow Artifact line, `## User Request`, `## Requirements`, `## Memory Summary`, `##
Project Patterns`, `## Domain Context`, `## SKILL_HINTS`), and the optional-sections
list.** Claude Code needs no host-specific additions to this scaffold — dispatch via
`Task()`/`TaskCreate()` on a registered subagent type already loads that agent's own
system prompt, so unlike Cursor's `generalPurpose`-only dispatch, no extra preamble or
`## Worktree` block is required here.

### Prompt assembly rule

**Shared with Cursor — canonical text lives in the same
`tools/craftflow-plugin/plugins/craftflow/skills/_shared/router-protocol.md` § "Dispatch
Prompt Scaffold" section referenced immediately above** (the Prompt assembly rule is the
final subsection there): every routed prompt must be self-contained from the workflow
artifact, approved files, and the current task contract, relying on none of prior chat
turns or completed-phase narrative when the same fact already lives in those sources.

### Effort Dispatch Rule

Before dispatching each agent, look up the phase in the `references/fast-path.md → #### Agent Dispatch Table → Effort` column. Append the corresponding steering directive (from `#### Effort Steering Directives`) as the last line of the `## Task Context` section in the agent scaffold.

- fast-path `build-implement` → `low`
- standard/escalated `build-implement` → `medium`
- `build-review`, `build-hunt` → `medium`
- `build-verify`, all re-verify → `high`
- `planner`, `plan-gap-reviewer`, `doubt-verifier`, `bug-investigator` → `high`
- `build-doc-sync`, `memory-finalize`, `learn-distill`, `skill-distill` → `low`
- All research tasks → `medium`

Record the assigned effort in `telemetry.effort.{agent}` in the workflow artifact and in the `agent_started` event log entry.

### Deterministic skill hints

- Router is the only authority allowed to load internal CRAFTFLOW skills.
- Agents may not self-activate `frontend-patterns`, `architecture-patterns`, or `debugging-patterns`.
- Include `craftflow:frontend-patterns` only when the request, changed files, plan, or design clearly targets UI/frontend work.
- Include `craftflow:component-registry` whenever `craftflow:frontend-patterns` is included (registry search-before-build; the skill degrades explicitly when the `ai-craft-registry` MCP server is not registered).
- Include `craftflow:architecture-patterns` only for multi-component, API, schema, auth, or integration-heavy work.
- Include `craftflow:research` only when planner or investigator receives `## Research Files`.
- Include project/domain skills only from `patterns.md ## Project SKILL_HINTS`.
- A `skill:` line inside a `<craftflow_routing_hint source="jev">` block is a level-4/5 candidate only (never above explicit user prompt, repo standards, or approved plan/design); include it in SKILL_HINTS only if it names an installed skill, and never let it load an internal CRAFTFLOW skill the rules above would not load.
- Skill precedence is strict:
  1. explicit user prompt
  2. project `CLAUDE.md` / repo standards / user standards
  3. approved plan and design docs
  4. domain-specific external skills
  5. internal CRAFTFLOW skills
  6. model heuristics

### Previous Agent Findings handoff

Shared with Cursor — canonical text lives in
`tools/craftflow-plugin/plugins/craftflow/skills/_shared/router-protocol.md` §
"Previous Agent Findings Handoff". DEBUG skips hunter findings.

**Doubt-Verify, Fix-Verify (BUILD, DEBUG):** after `integration-verifier` PASS, read `references/verify-dispatch.md`.

### Task metrics and timing telemetry

Timing telemetry is measurement only. It must never bypass gates, phase exit, or remediation rules.

**Router-owned timestamp instrumentation:**
- When dispatching an agent task, append `"agent_started"` to the event log with `"ts": "{iso_now}"` before invoking the agent.
- When capturing agent output, append `"agent_completed"` to the event log with `"ts": "{iso_now}"`.
- `{iso_now}` is UTC ISO-8601; omit the field if no clock.

**Legacy path (Claude Code task metrics):**
- If Claude Code exposes task duration metrics via `TaskGet()`, use those as the primary source.
- If unavailable, use the event log delta above.
- Keep `task_metrics_available="unknown"` until one of these paths succeeds; set to `"event_log"` when using delta computation.

## 8. Post-Agent Validation

After any agent returns, read `references/post-agent-validation.md` (once per context; re-read after /clear or compaction) and apply it. Stand-ins:

- `SCENARIOS_TOTAL`
- `SCENARIOS_PASSED`
- `SCENARIOS_FAILED`
- `REMEDIATION_REASON: ...`
- `REPLAN_REASON: ...`
- `DESIGN_FILE`
- telemetry:

| Agent | Override |
|---|---|
| any | rows in that file |

**Intent Interview Gate, brainstorming handoff (PLAN only):** `references/plan-workflow.md`.

## 9. Remediation And Workflow Rules

- When remediation, scope resolution, review-to-build escalation, planner clarification, investigation continuation, or the verifier REVERT gate is in play, immediately read `references/remediation-and-research.md`.
- Use the `## 9. Remediation And Workflow Rules` block there as canonical router law.

## 10. Research Orchestration

- See `references/remediation-and-research.md` and apply its `## 10. Research Orchestration`, `## Research Quality`, and `## Research Files` blocks whenever research is triggered or consumed.

## Research Quality

- See `references/remediation-and-research.md` and apply its `## Research Quality` block whenever research quality must be summarized or persisted.

## Research Files

- See `references/remediation-and-research.md` and apply its `## Research Files` block whenever research file paths are handed to planner or investigator.

## 11. Re-Review Loop

- See `references/remediation-and-research.md` and apply its `## 11. Re-Review Loop` block whenever a `kind:remfix` task completes.

## 12. Chain Execution Loop

```text
1. TaskList()
2. Select tasks in the active `wf:` where:
   - status is pending or in_progress
   - blockedBy is empty or all blockers are completed
3. If the runnable task kind is memory:
   - execute inline in the main context
   - persist workflow artifact results + Memory Notes from the task description
   - append `memory_finalized` to `$PROJECT_ROOT/.craftflow/state/workflows/{wf}.events.jsonl`
   - clean up the matching [craftflow-internal] or legacy [cc10x-internal] memory_task_id entry
   - mark the memory task completed
   - mark the parent workflow task completed
   - continue
4. Otherwise, map each runnable task through the dispatcher table.
4a. If `build_mode == "fast_path"` in the workflow artifact for this BUILD:
   - If `build_mode` is null (legacy artifact or non-BUILD workflow): treat as `"standard"` — skip this step, continue to step 5.
   - code-reviewer and silent-failure-hunter tasks were NOT created — skip step 5 parallel dispatch check for them
   - When integration-verifier completes with FAIL:
     - Read `references/build-workflow.md → ### Fast Path Escalation` block and execute it
     - Do not advance phase cursor
5. If `code-reviewer` and `silent-failure-hunter` are both ready in BUILD (build_mode == "standard" only):
   - mark both in_progress first
   - invoke them in the same message
   - If parallel invocation fails or is unavailable (API error, rate limit): fall back to sequential execution (reviewer first, then hunter). Never block a workflow because parallelism is unavailable. Log `event=parallel_fallback` in the workflow event log.
6. After each agent returns:
   - capture memory payload immediately
   - validate output (§ 8)
   - persist task-state side effects
   - if BUILD review and hunt are both complete for the current phase, write one router-owned merged findings summary into the existing workflow results before verifier handoff
   - apply workflow rules
   - for BUILD, run `phase_exit_gate`; if the current phase is not complete, persist `phase_status={partial|blocked}` and stop
   - never advance to the next phase or workflow step on apology prose alone
   - if two agents in the same phase return contradictory verdicts (e.g., reviewer approves but verifier fails on the same evidence), treat the stricter verdict as authoritative and do not average or reconcile the signals. Log the contradiction in `status_history`.
   - doc-syncer `STATUS=SKIPPED` is a passing state; advance to Memory Update immediately
   - doc-syncer STATUS=PARTIAL: soft pass; advance to Memory Update; persist doc_sync_partial=true in workflow artifact results.doc_syncer for user review
   - On fast path (`build_mode == "fast_path"` or `"fast_path_escalated"`): doc-syncer task was never created; when verifier PASS, advance directly to Memory Update (no doc-sync step)
7. Repeat until all tasks in the active `wf:` are completed.
```

`SKILL.md → ## 12. Chain Execution Loop` 5a (research): `references/remediation-and-research.md`; 5b (bake-off): `references/plan-workflow.md`. In task-tool fallback mode, step 1 and agent-completion steps 1-3: `references/task-tool-fallback.md`. Memory task (step 3): `references/memory-finalize.md`.

## 14. Hard Rules

- Router must run in the main Claude Code session, never inside a sub-agent.
- Router is the only orchestration state owner. Agents may propose remediation or next actions, but only the router creates, blocks, unblocks, reuses, or completes orchestration tasks.
- Never stop after one agent if the workflow chain has more runnable tasks.
- Never rely on prose when `wf:`, `kind:`, `origin:`, `phase:`, or `scope:` can answer the question.
- Never use an unscoped task lookup in critical paths.
- Never treat stored task IDs as durable truth across workflows.
- Never spawn Memory Update as a sub-agent.
- Never create `CRAFTFLOW TODO:` tasks. Non-blocking discoveries go into `**Deferred:**` memory notes.
- Never let REVIEW create implementation tasks without an explicit router/user transition into BUILD.
- Never `git add` or `git commit` a plan artifact (`docs/plans/**`, any `PLAN_FILE`/`design_file` the planner produces, or a workflow's own `.craftflow/state/workflows/*.json`); enforced by policy `plan_commit` in craftflow projects (see Policy table). Recording `- Plan: {plan_file}` or `Plan saved: {plan_file}` in `activeContext.md` (a memory pointer to a local path) is unaffected by this rule and still required. If any dispatched agent stages or commits a plan file anyway, the router must `git restore --staged`/`git reset` that path before continuing — never let it ride along in the next commit.
- Never report a workflow outcome (pass, fixed, complete) to the user without first confirming the verification evidence that supports that claim. "I believe it works" is not evidence. [EASY TO MISS: "I ran the tests and they passed" without showing command output, exit codes, or scenario evidence is also not evidence. Require concrete proof artifacts, not agent assertions.]
- Never let a remediation loop run more than `policy.remediation_cycles_max` (= 3) cycles without a human checkpoint. Drift accumulates silently in long chains.
- Only parallelize agents whose file-write surfaces do not overlap. Reviewer and hunter are read-only and safe to parallelize. Two write agents on overlapping files must be serialized. [EASY TO MISS: Each parallel agent must have a distinct phase value and unique task description. Identical prompts cause agents to duplicate work or silently clobber each other's output.]
- Agents must never inherit raw conversation context. They receive only the structured scaffold from the dispatcher. Leaking conversation history into agent prompts causes scope pollution and non-reproducible behavior.
- Maintain professional objectivity in all routing decisions. Do not rationalize a failing workflow as "close enough" or downgrade critical findings to avoid remediation. The router exists to enforce quality, not to please.
- `DIFF_DRIVEN_DOCS: skip` in Session Settings disables doc-syncer for projects that manage documentation separately; when present, skip `build-doc-sync` task creation and block Memory Update on `verifier_task_id` directly.
- `SKILL_DISTILL: skip` in Session Settings disables the `skill-distill` phase entirely; when present, never run the ledger gate check, never create the `skill-distill` task, and block Memory Update on whatever task it would otherwise have depended on (`learn-distill` if gated in, otherwise `doc_sync_task_id`/`verifier_task_id` per the normal chain). This toggle does NOT disable the unconditional `--observe` step in `references/memory-finalize.md → ## 13. Memory Finalization` — that call still runs every workflow regardless of this setting (cheap, deterministic, preserves future calibration value).
- Agents must never reference or read internal skill files from other agents or skills (e.g., component-builder must never read code-review-patterns/SKILL.md). Cross-agent knowledge flows exclusively through router-mediated scaffolds and workflow artifacts.
- Never use EnterPlanMode. Claude Code's native plan mode is incompatible with Craftflow. Planning requests go through the Craftflow PLAN workflow (brainstorming → planner → bounded fresh review → memory finalization), which provides orchestration state, workflow artifacts, intent contracts, and verification. Native plan mode provides none of these.

### Policy table

Printed by `craftflow_policy.py --print-table`; each value is enforced by the named script and the prose location keeps the rule text.

| key | value | enforced by | prose location |
|---|---|---|---|
| `native_plan_mode` | `never` | `craftflow_pretooluse_guard.py` | § 14 EnterPlanMode rule |
| `plan_commit` | `never` | `craftflow_pretooluse_bash_guard.py` | § 14 plan-artifact rule |
| `memory_write` | `finalize-only` | `craftflow_pretooluse_guard.py` | `references/memory-finalize.md → ## 13. Memory Finalization` |
| `worktree_merge` | `require_clean_tree=true, lock_max_attempts=9, lock_sleep_seconds=5, copy_fallback_on_up_to_date=true` | `craftflow_pretooluse_bash_guard.py` (clean tree) | worktree.md |
| `remediation_cycles_max` | `3` | `craftflow_gate_check.py` | § 14 remediation loop rule |
| `doubt_cycles_max` | `3` | `craftflow_gate_check.py` | verify-dispatch.md Doubt-Verify |
| `fix_verify_cycles_max` | `3` | `craftflow_gate_check.py` | verify-dispatch.md Fix-Verify |
| `plan_review_passes_max` | `2` | `craftflow_gate_check.py` | `references/plan-workflow.md` |

### Loop caps

Hard stops. Operative text: the last column.

| gate | max | evaluation point | input | at-cap action | operative prose |
|---|---|---|---|---|---|
| `remediation_cycles_max` | 3 | before creating a REM-FIX task | `circuit_breaker` | ask the user | `remediation-and-research.md` "Before creating a new remediation task" |
| `doubt_cycles_max` | 3 | after doubt-verifier returns | cycle N | advance, no REM-FIX | verify-dispatch.md Doubt-Verify "After doubt-verifier returns" |
| `fix_verify_cycles_max` | 3 | after the fix-verify doubt-verifier returns | cycle N | advance, no REM-FIX | verify-dispatch.md Fix-Verify "After doubt-verifier returns" |
| `plan_review_passes_max` | 2 | after a plan-gap-reviewer pass | pass N, verdict | pass 2 FINDINGS: clarification | `remediation-and-research.md` plan-gap-reviewer blocks |

**`loop_cap_check_failed`.** When `craftflow_gate_check.py` exits non-zero, prints anything but one JSON line with a valid `action`, or returns `human_checkpoint` with a `reason` other than `cap-reached`: persist `pending_gate: "loop_cap_check_failed"`, apply no verdict, create no task, increment no further counter, and ask: "Loop-cap check for `{gate}` gave no usable decision (`{reason or "exit " + code}`), so I have not acted on the `{verdict}` verdict for cycle/pass {N}. Reply `retry` to run the check again, `advance` to apply the hard-stop handling, or `proceed` to apply the verdict as written." For remediation, `advance` is not offered and `proceed` reads "create the REM-FIX as written". On the reply, clear `pending_gate` and apply the chosen block. On resume with this `pending_gate`, re-ask the same line and take no other action.
