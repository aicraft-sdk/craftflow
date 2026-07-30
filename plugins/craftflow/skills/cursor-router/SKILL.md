---
name: cursor-router
description: |
  Craftflow router for Cursor AI agent mode. Each workflow phase is dispatched via a
  real, isolated Cursor `Task` tool call (subagent_type: generalPurpose) — not inline
  role-play in the router's own turn. code-reviewer and silent-failure-hunter are
  dispatched in parallel via two Task calls in the same message when both are ready.
  There is still no real task-tracking system in Cursor — no TaskCreate, TaskUpdate,
  TaskList, or TaskGet — so phase-state tracking remains self-managed via cursor-wf.json.
---

# Craftflow Router — Cursor Task-Dispatch Mode

**You are running in Cursor AI agent mode.** The Craftflow orchestration system has
detected this via `CRAFTFLOW_PLATFORM: cursor` injected by the MDC rule.

This file replaces the Claude Code router for Cursor sessions. The router's own turn
uses Cursor's tool set (`Read`, `Edit`, `Write`, `Bash`, `Grep`, `Glob`) for orchestration
bookkeeping — memory load, workflow-artifact writes, progress blocks, validation — and
uses the `Task` tool to dispatch each phase's actual work to a genuinely isolated child
agent. There is still no real task-tracking system in Cursor: no `TaskCreate`,
`TaskUpdate`, `TaskList`, or `TaskGet`. Phase-state tracking remains self-managed via
`cursor-wf.json`, exactly as before.

**Core contract:** Each workflow phase is executed by a real, isolated Cursor `Task`
call — `subagent_type: generalPurpose`, dispatched in foreground/blocking mode so the
router receives that phase's result before advancing. code-reviewer and
silent-failure-hunter, when both are ready in the same round, are dispatched via two
`Task` calls in the same message for genuine parallel execution. The dispatched
subagent starts with a fresh, clean context — it has not read this file or any prior
phase's transcript — so every dispatch prompt must be fully self-contained (see § 5).

**Why this changed:** Cursor 2.4 (Jan 22, 2026) introduced a real `Task` tool for
subagent dispatch. A CLI-specific bug meant it was not actually usable in headless
`cursor-agent -p` mode (the exact invocation craftflow uses) until Cursor's own fix
landed (~March 2026). This session verified, live, against the real `cursor-agent -p
--mode plan --trust` invocation craftflow uses, that `Task` now works correctly: it is
present in the tool list, two ad-hoc `Task` calls in the same message produced two
independent, correctly-isolated results with no cross-visibility between their prompts,
and `generalPurpose` is the only dispatchable ad-hoc `subagent_type` (custom names under
`.cursor/agents/*.md` are not selectable via `Task`, even though they work for the
interactive `/name` chat shortcut). A future reader should not assume this section is
speculative and revert the file to inline role-play — it is based on live, corroborated
verification, not documentation alone.

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
| skill-author | `tools/craftflow-plugin/plugins/craftflow/agents/skill-author.md` |

## 1. Intent Routing

**Pending-answer precedence check — run this before anything below, on EVERY incoming
message** (mirrors § 2 Memory Load's "Run this before routing" note): read
`.craftflow/state/cursor-wf.json` first, before evaluating any keyword below.

- If `pending_skill_approval` is non-null, do NOT run Intent Routing on this message.
  Treat the incoming message as the answer to that pending Skill-Distill Gate question
  and process it per § 5a Step 4.4 (match one of the 4 options, or re-ask verbatim if it
  doesn't clearly match). Skip Intent Routing entirely for this turn.
- If `pending_gate` is non-null (set by § 6 "When a phase fails" while awaiting a
  retry/skip/stop answer — the same kind of pending-question state, used analogously),
  treat the incoming message as the answer to that question instead and process it per
  § 6's own options ((a) retry, (b) skip, (c) stop), re-asking verbatim on a
  non-matching reply. Skip Intent Routing entirely for this turn.
- This precedence rule applies uniformly whether the pending state was set moments ago
  in this same live session or is being read back after a session restart — there is no
  separate "same session" vs "resumed session" code path. A router turn that receives a
  reply while `pending_skill_approval` (or `pending_gate`) is still non-null must never
  fall through to Intent Routing below and start a new workflow from that reply's text.
- Only when both `pending_skill_approval` and `pending_gate` are null does Intent
  Routing below apply to the incoming message.

Route using the first matching signal:

| Priority | Signal | Keywords | Workflow | Chain |
|----------|--------|----------|----------|-------|
| 1 | ERROR | error, bug, fix, broken, crash, fail, debug, troubleshoot, issue | DEBUG | bug-investigator → code-reviewer → integration-verifier |
| 2 | PLAN | plan, design, architect, roadmap, strategy, spec, brainstorm | PLAN | brainstorming (inline) → planner (dispatched) → plan-gap-reviewer (dispatched, 1-2 passes) |
| 3 | REVIEW | review, audit, analyze, assess, "is this good" | REVIEW | code-reviewer |
| 4 | DEFAULT | Everything else | BUILD | component-builder → code-reviewer → silent-failure-hunter → integration-verifier |

Rules:
- ERROR always wins over BUILD.
- REVIEW is advisory only. Never let REVIEW create implementation tasks.
- BUILD uses a risk scan: if no risk keywords match, fast path (builder → verifier only).
  Risk keywords (case-insensitive, matches request text):

  | Group | Keywords |
  |-------|----------|
  | Security | auth, authz, oauth, jwt, password, credential, secret, cert, ssl, tls, encrypt, decrypt, permission, role, session, access control |
  | Database / schema | migration, schema change, alter table, drop table, seed, remove column, drop column, export data, data export |
  | Payment | payment, billing, stripe, checkout, subscription, invoice |
  | Explicit risk markers | critical path, production data, irreversible, truncate, delete all, purge |

  **Cursor-only additional risk keywords (not in the canonical Claude Code list):**
  `concurrent`, `race`, `rollback` — a deliberate Cursor-specific superset carried
  forward from this file's own routing history. These 3 terms are NOT part of the
  4 canonical groups above and are NOT present in Claude Code's canonical table
  (`craftflow-router/references/fast-path.md`). Do not fold them into the table
  above or propagate them to `fast-path.md`/`craftflow-router/SKILL.md` — this
  asymmetry (Cursor scans 3 more risk-trigger terms than Claude Code) is intentional
  and approved (user decision, wf-20260722-123920-c6b6741d / wf-20260722-125603-78a7da0e).
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

If cursor-wf.json's `pending_skill_approval` field is non-null, a Skill-Distill Gate
approval question (§ 5a) was left unanswered when the prior session ended — re-ask it
per § 5a's "Resume behavior" before continuing any further workflow progress. This is
the session-restart instance of § 1's general "Pending-answer precedence check" — that
same check also applies to a live reply arriving within the current session, not only
to session resume.

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
  "worktree_mode": null,
  "worktree_path": null,
  "worktree_branch": null,
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
  "worktree_mode": null,
  "worktree_path": null,
  "worktree_branch": null,
  "status_history": [{"event": "workflow_started", "ts": "{iso_timestamp}", "phase": "{type}"}],
  "created_at": "{iso_timestamp}",
  "updated_at": "{iso_timestamp}"
}
```

## 4a. Worktree Isolation (BUILD Default)

Every new BUILD workflow attempts to isolate file writes in a dedicated git worktree —
the same mechanism Claude Code's `craftflow-router/SKILL.md` uses (see its own "### Worktree
Isolation (BUILD Default)" section under `references/build-workflow.md`), adapted here for
Cursor's `Task`-dispatch model: instead of a `## Worktree` block appended to a
`TaskCreate()` description, the block is added to the per-phase dispatch prompt built in
§ 5.

1. **Creation.** At BUILD start, before any `Task` dispatch, resolve the project root and
   create the worktree via the Shell tool:
   ```bash
   PROJECT_ROOT=$(git rev-parse --show-toplevel)
   SHORT_ID="{last 8 hex chars of the workflow UUID}"
   git worktree add "$PROJECT_ROOT/.claude/worktrees/wf-${SHORT_ID}" -b "worktree-wf-${SHORT_ID}"
   ```
   `SHORT_ID` is the same trailing 8-hex suffix already present in `wf_id`
   (`wf-{UTC_timestamp}-{8_hex_chars}`, see § 4 Step 4a) — the same convention Claude Code
   uses. Both platforms' worktrees live under `.claude/worktrees/` and cannot collide, since
   each workflow's UUID suffix is distinct.

2. **On success:** record in BOTH `cursor-wf.json` (§ 4b) and the main workflow artifact
   (§ 4c):
   - `worktree_mode: "auto_created"`
   - `worktree_path`: absolute, e.g. `{PROJECT_ROOT}/.claude/worktrees/wf-{SHORT_ID}`
   - `worktree_branch`: `worktree-wf-{SHORT_ID}`

3. **On failure** (any git error — shallow clone, detached HEAD, path conflict): set
   `worktree_mode: null` in both artifacts, log a note/event describing the failure, and
   continue in the main tree. Never block the workflow over worktree failure — same
   fallback philosophy as Claude Code's.

4. **Every dispatch prompt gains a `## Worktree` section** (§ 5's template), populated ONLY
   when `worktree_mode == "auto_created"`:
   ```
   ## Worktree
   WORKTREE_PATH: {worktree_path}
   All file reads, edits, and writes for this phase must be rooted at WORKTREE_PATH.
   Do not modify files outside WORKTREE_PATH.
   ```
   This is not optional decoration: a dispatched subagent starts with a clean context and
   receives nothing except what is in this prompt — omitting this section would silently
   leave the phase operating in the main tree instead of the isolated worktree, with no way
   for the subagent to discover the worktree existed at all.

5. **Merge + cleanup**, after `integration-verifier`'s dispatched subagent returns PASS on
   the final phase and BEFORE § 8 Memory Finalization:
   - `git merge worktree-wf-{SHORT_ID}` (run from the project root)
   - Remove the worktree: `git worktree remove {worktree_path} --force`
   - Delete the branch: `git branch -d worktree-wf-{SHORT_ID}`
   - Update `worktree_mode` to `"merged_and_removed"` in both artifacts
   - **Known gotcha** (documented in `.craftflow/state/project/patterns.md`'s Common
     Gotchas for worktree builders): if the worktree only has uncommitted changes (its
     branch points at the same commit as `main`), `git merge` reports "Already up to date"
     despite real changes existing on disk. In that case, copy the changed files directly
     into the main tree instead of relying on `git merge`, then still remove the worktree
     and delete the branch as normal.
   - On a genuine merge conflict (not the "Already up to date" case): stop, ask the user to
     resolve, and do not proceed to Memory Finalization.

**Empirical basis:** confirmed this session via real, live `cursor-agent -p --force --trust`
calls that cursor-agent's Shell tool can genuinely run `git worktree add <path> -b <branch>`
and `git worktree remove <path> --force` successfully (exit 0). Cursor's own native
worktree feature (the `/worktree` slash command, the Agents Window's per-tab worktrees,
`/in-cloud` background agents) is a DIFFERENT, session-level mechanism that does NOT apply
to `Task`-dispatched subagents — confirmed via official docs (cursor.com/docs/subagents
says nothing about worktree isolation for Task-dispatched subagents). This file's
cursor-router therefore creates and manages its own real git worktree via the plain Shell
tool, exactly like Claude Code's own router does via Bash — there is no shortcut via a
native Cursor feature.

## 4b. PLAN Workflow — Brainstorming (Inline) + Dispatched Planner/Reviewer Loop

This file is fully self-contained for PLAN, exactly as § 4a is for BUILD's worktree
mechanics. Do not go looking for more detail elsewhere — in particular, see the warning
at the end of this section.

**Step 1 — brainstorming runs INLINE, not dispatched.** Read
`tools/craftflow-plugin/plugins/craftflow/skills/brainstorming/SKILL.md` directly (this
is the same Skill() override as § 9: read the file, follow it inline) and run it in the
router's own turn. This is a deliberate exception to § 10's "always dispatch via Task"
rule: brainstorming's own SKILL.md calls `AskUserQuestion` to clarify scope and lock in
design tradeoffs with the real user — a Task-dispatched `generalPurpose` subagent has no
channel back to the actual user, so this phase cannot be delegated. Brainstorming ends
when a design doc is saved (typically `docs/plans/{date}-{slug}-design.md`).

**Step 2 — planner is dispatched via a REAL `Task` call.** The instant brainstorming's
design doc is saved, stop working inline. Build the § 5 dispatch prompt for
`planner` (agent file: `tools/craftflow-plugin/plugins/craftflow/agents/planner.md`),
referencing the design doc path in the `## Requirements` field, and invoke `Task` exactly
as § 5 describes for every other phase. Do not write the plan file yourself in the
router's own turn — that is `planner`'s job, running in its own isolated subagent
context.

**Step 3 — plan-gap-reviewer is dispatched via a REAL `Task` call.** Same mechanism:
build the § 5 dispatch prompt (agent file:
`tools/craftflow-plugin/plugins/craftflow/agents/plan-gap-reviewer.md`), referencing the
saved plan file path, and invoke `Task`. This phase's entire value is a *fresh, unbiased*
read of the plan — dispatching it as an isolated subagent with no prior context is not
optional decoration, it is the anti-anchoring mechanism itself. Running plan-gap-review
inline in the same turn that wrote the plan defeats its purpose.

**Bounded review loop:** if plan-gap-reviewer's Router Contract shows
`REPLAN_NEEDED=true` or `BLOCKING_FINDINGS_COUNT>0`, re-dispatch `planner` via `Task`
ONE more time (2 planner passes max), appending the reviewer's findings to the
`## Requirements` field, then re-dispatch `plan-gap-reviewer`. If still blocked after 2
total plan-gap-reviewer passes, stop and follow § 6 "When a phase fails" (ask the user;
do not loop indefinitely).

**Do NOT consult Claude Code's own `craftflow-router/SKILL.md` or its
`references/plan-workflow.md` / `references/remediation-and-research.md` to figure out
PLAN mechanics.** Those files describe Claude Code's `TaskCreate({...}) -> planner_task_id`
task-tracking system — a completely different mechanism from Cursor's real `Task` tool.
Their syntax superficially resembles the already-overridden-as-skippable
`TaskUpdate`/`TaskList`/`TaskGet` calls in § 9, but `TaskCreate` there is NOT one of
those overrides and must never be read as "this phase can just be done inline." If you
find yourself about to write a plan, or judge a plan, directly in the router's own turn
instead of via a real `Task()` call — stop. That is the exact regression this file was
rewritten to eliminate (see the file's own history note near the top). This file's own
§ 5 dispatch loop and the three steps above are the complete specification for PLAN.

## 5. Cursor Task-Dispatch Execution Loop

This is the core of Cursor Task-Dispatch Mode. Replace all `Agent()` and
`TaskCreate()` calls with this loop.

### Why `subagent_type: generalPurpose` always

Cursor's `Task` tool only accepts its own built-in `subagent_type` values (e.g.
`generalPurpose`, `planner`, `web-researcher`). Custom subagent names defined under
`.cursor/agents/*.md` are **not** selectable via `Task`'s `subagent_type` parameter —
confirmed directly this session (dispatching a custom-named agent by name failed with
"not in the Task tool's allowed subagent_type enum"), even though those same custom
names ARE selectable for the interactive `/name` chat shortcut. This means the router
can never say "dispatch the `component-builder` subagent" — it must always dispatch
`generalPurpose` and put the entire actual brief, including which craftflow agent role
to play, directly in the Task prompt itself.

### Read-only phases are prompt-level only, not tool-enforced

`generalPurpose` has full tool access (Write, StrReplace, EditNotebook, Delete are all
present) regardless of the role it is asked to play. Unlike Claude Code — where a
subagent's own declared tool list enforces read-only behavior at the SDK level — Cursor
has no equivalent mechanism. When dispatching `code-reviewer`, `silent-failure-hunter`,
or `integration-verifier` (all three declare themselves READ-ONLY / "do NOT edit any
files" in their own agent files), the read-only guarantee is prompt-level only: carry
that agent file's own "read-only, never edit" instruction into the dispatch prompt
verbatim and trust it, exactly as you would trust any other markdown-instruction agent
that has no tool restriction.

### Dispatch prompt template

Construct this prompt for every phase before calling `Task`. Fill in `{agent-name}` and
the scaffold fields (reusing the same field names the router already assembles in § 2
Memory Load / § 3 Workflow Preparation / § 4 Workflow Artifact Creation):

```
You are acting as the `{agent-name}` craftflow agent, running as an isolated Cursor
subagent with a fresh, clean context — you have NOT read any other file in this
conversation yet.

## Step 1: Read your operating brief
Read `tools/craftflow-plugin/plugins/craftflow/agents/{agent-name}.md` in full and
follow it as the complete operating brief for this phase.

## Step 2: Overrides (apply these before following the file above)
You are running inside Cursor, not Claude Code. The following tools referenced in
`{agent-name}.md` do NOT exist for you: `TaskUpdate`, `TaskCreate`, `TaskList`,
`TaskGet`, `Agent()`, `Skill()`.
- Wherever the agent file says "you MUST call TaskUpdate" or "writing text is not
  sufficient" — that does not apply to you. Emitting the
  `### Router Contract (MACHINE-READABLE)` block in your OWN final message is both
  necessary and sufficient. It is your ONLY channel back to the parent router.
- Wherever the agent file says `Skill(skill="craftflow:X")` — instead, read
  `tools/craftflow-plugin/plugins/craftflow/skills/X/SKILL.md` and follow its
  instructions inline first, before continuing with the phase's work.

## Task Context
- Task ID: {task_id_or_none}
- Parent Workflow ID: {parent_workflow_id}
- Task Phase: {task_phase}
- Plan File: {plan_file or 'None'}
- Workflow Scope: wf:{parent_workflow_id}
- Workflow Artifact: .craftflow/state/workflows/{wf_id}.json

{## Worktree — include ONLY when `worktree_mode == "auto_created"` (see § 4a Worktree
Isolation). Omit this section entirely when `worktree_mode` is null (fallback mode). When
included, use exactly this shape:
## Worktree
WORKTREE_PATH: {worktree_path}
All file reads, edits, and writes for this phase must be rooted at WORKTREE_PATH.
Do not modify files outside WORKTREE_PATH.
}

## User Request
{original_user_request}

## Requirements
{phase-specific requirements, plan reference, files/surfaces in scope}

## Memory Summary
{condensed activeContext.md / patterns.md / progress.md relevant to this phase}

## Project Patterns
{relevant entries from patterns.md ## Common Gotchas / ## Architecture Patterns}

{## Previous Agent Findings — integration-verifier ONLY, and ONLY when code-reviewer
and/or silent-failure-hunter ran this round (standard chain). Omit this section
entirely on the fast path (no reviewer/hunter ran this round) or for any other
agent-name. See "Previous Agent Findings handoff" below for the exact format.}

## Step 3: Do the work
Execute the phase's actual work per your operating brief (Step 1) and these overrides
(Step 2).

Your final message MUST end with the `### Router Contract (MACHINE-READABLE)` fenced
YAML block exactly as `{agent-name}.md` specifies. This is the only way the router will
receive your result.
```

### Previous Agent Findings handoff (integration-verifier only)

`integration-verifier.md`'s own "Claim extraction (MANDATORY)" step depends on receiving
a `## Previous Agent Findings` section — this is load-bearing, not decoration. When
building the integration-verifier dispatch prompt AND code-reviewer and/or
silent-failure-hunter ran this round (standard BUILD/DEBUG chain), append this section
to the template above, in the same sub-block format
`craftflow-router/SKILL.md`'s own "Previous Agent Findings handoff" section uses:

```text
## Previous Agent Findings

### Code Reviewer
**Verdict:** {Approve|Changes Requested}
**Critical Issues:**
{reviewer critical issues or "None"}

### Silent Failure Hunter
**Critical Issues:**
{hunter critical issues or "None / not in this workflow"}
```

- **Fast path (no reviewer/hunter ran this round):** omit the `## Previous Agent
  Findings` section entirely — same fast-path-omission logic already used for the
  Claude Code router (`craftflow-router/SKILL.md` §12).
- **DEBUG chain:** Silent Failure Hunter is not in the chain — omit its sub-block (or
  state "not in this workflow" per the format above); Code Reviewer's sub-block still
  applies.
- Pull `{reviewer critical issues}` / `{hunter critical issues}` from the Router
  Contract YAML each agent returned this round (their `Critical Issues` /
  `CRITICAL_ISSUES` fields), not from re-reading their full output.

### Execution loop

```
For each phase in workflow chain:
  1. Emit PHASE-START progress block (see § 7)
  2. Build the dispatch prompt for this phase (template above).
     If this phase is integration-verifier AND code-reviewer/silent-failure-hunter ran
     this round (standard chain): include the `## Previous Agent Findings` section per
     "Previous Agent Findings handoff (integration-verifier only)" above.
     If this is the fast path (no reviewer/hunter ran this round): omit that section.
  3. If this phase is code-reviewer or silent-failure-hunter AND the other one is
     ALSO ready to run in this same round:
       Invoke Task TWICE in the SAME message — once per agent, each with its own
       fully-built dispatch prompt (subagent_type: generalPurpose, foreground) — and
       wait for BOTH results before proceeding.
     Otherwise:
       Invoke Task ONCE (subagent_type: generalPurpose, foreground/blocking) with
       this phase's dispatch prompt, and wait for its result.
  4. Capture the Router Contract YAML from each dispatched subagent's final message
  5. Validate the contract(s) (see § 6)
  6. Update cursor-wf.json: set phase status to "completed", "failed", or "skipped".
     "skipped" is a third valid phase-status value (see § 6 "When a phase fails" →
     Resolution → (b) Skip) written when the user answers a failed-phase gate question
     with "skip" — it is written by that resolution step, not by this loop iteration
     itself, but this loop's Post-Agent Validation (step 5, below) is what routed the
     phase into "failed" in the first place.
     Update ".craftflow/state/cursor-wf.json" → set status["{phase}"] = "completed"
     and cursor = cursor + 1 (once per phase, twice in one step for a parallel pair)
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
**PLAN:** brainstorming (inline, see § 4b) → planner (dispatched) → plan-gap-reviewer (dispatched, 1-2 passes, see § 4b's bounded review loop)

### Simplified execution model (v1 differences from Claude Code)

In v1 Cursor Task-Dispatch Mode, the following Claude Code features are NOT supported:
- Doubt-verifier cycle (deferred to v2)
- Research orchestration (deferred to v2)
- Doc-syncer phase (deferred to v2)
- Learn-distill phase (deferred to v2) — Claude Code's `learn-distill` gate
  (`craftflow-router/references/fast-path.md`'s Learn-Distill Gate section) has no
  Cursor equivalent yet. Same deferral status as doc-syncer above; not added here because
  neither `learn-distiller` nor its agent file appear anywhere in this file's § "Agent File
  Paths" table.
- Effort steering (deferred to v2) — the canonical Claude Code Task Context scaffold
  includes an `Effort Directive` field driven by `references/fast-path.md`'s Effort
  Dispatch Rule (per-phase low/medium/high steering). Cursor has no equivalent Effort
  Dispatch Rule wired yet, so the `## Task Context` block in § 5 intentionally omits
  this field rather than fabricate a value with no steering logic behind it. All
  dispatched phases run without effort steering until this is implemented.

Worktree isolation is now SUPPORTED (see § 4a) — it is no longer on this deferred list.
Confirmed empirically this session: a real `cursor-agent -p --force --trust` Shell-tool
call genuinely runs `git worktree add`/`git worktree remove` successfully (exit 0).
Cursor's own native worktree feature (`/worktree`, the Agents Window's per-tab worktrees,
`/in-cloud` background agents) is a separate, session-level mechanism that does not apply
to `Task`-dispatched subagents, so this file self-manages its own worktree lifecycle
directly via the plain Shell tool instead.

Skill-distill phase is now SUPPORTED (see § 5a) — it is no longer on this deferred list.
`skill-author` has an Agent File Paths table entry above, and § 5a documents a
synchronous, plain-text confirmation exchange in the router's own turn as the Cursor
substitute for Claude Code's `AskUserQuestion` gate (Cursor has no equivalent tool).
Learn-distill and doc-syncer remain deferred above; § 5a's gate does not depend on
either.

These features are deferred — not removed. When they are implemented, they will be
added to this file without touching Claude Code files.

## 5a. Skill-Distill Gate (Sequential BUILD-Tail Step)

This section wires the post-implementation skill-distillation feature's `skill-distill`
phase into Cursor's task-dispatch model, replacing the "deferred to v2" status this file
previously carried. It mirrors Claude Code's `references/build-workflow.md` "#### Skill-
Distill Gate" and `craftflow-router/SKILL.md` "### Skill-Distill Approval Flow" in intent,
using this file's own established mechanisms instead of `TaskCreate`/`AskUserQuestion`
(neither exists in Cursor): a real `Task` dispatch (§ 5's dispatch loop) for the
gate-check body, and a router-own-turn plain-text question/answer exchange for the
approval decision — a dispatched `generalPurpose` subagent has no channel back to the
real user, the same reasoning § 4b gives for why brainstorming runs inline instead of
dispatched.

Learn-distill and doc-syncer remain deferred to v2 in this file (§ 5's Simplified
Execution Model). This gate does not depend on either and does not wire them.

### When this runs

After the BUILD (or DEBUG) chain's final phase — `integration-verifier` — completes and
passes § 6 Post-Agent Validation, and BEFORE § 8 Memory Finalization. If a worktree was
created (§ 4a), complete § 4a Step 5 (merge + cleanup) first; this gate still runs before
Memory Finalization, after any worktree merge.

**Session Settings opt-out:** Before Step 1, read `activeContext.md ## Session
Settings`. If `SKILL_DISTILL: skip` is present (this setting is shared with Claude Code
via the common `.craftflow/state/` memory tier), skip this entire gate — do not run the
ledger query, do not dispatch `skill-author` — and proceed directly to § 8 Memory
Finalization.

### Step 1 — Gate check (router's own turn, no dispatch)

Run via the Shell tool:
```bash
python3 tools/craftflow-plugin/plugins/craftflow/scripts/craftflow_skill_ledger.py \
  --query --ledger .craftflow/state/project/skill-candidates.json
```
Parse the returned `candidates` array. A candidate is gate-eligible when BOTH:
- `distinct_workflows >= 2`
- `status == "candidate"` (not `"proposed"`, `"promoted"`, or `"rejected"`)

- **Zero eligible candidates** → skip this entire gate. Proceed directly to § 8 Memory
  Finalization. Do not dispatch `skill-author`; do not touch `cursor-wf.json`'s
  `pending_skill_approval` field.
- **One or more eligible** → pick the one with the highest `distinct_workflows` (ties
  broken by earliest `first_seen`) and continue to Step 2.

### Step 2 — Dispatch `skill-author` via a real `Task` call

Build the § 5 dispatch prompt exactly per the standard template, with:
- `{agent-name}` = `skill-author`
- Agent file: `tools/craftflow-plugin/plugins/craftflow/agents/skill-author.md` (see this
  file's own Agent File Paths table)
- Task Phase: `skill-distill`
- `## Requirements`: `Candidate id: {candidate_id}. Read this candidate from the ledger
  at .craftflow/state/project/skill-candidates.json, apply the anti-slop rubric, and
  stage a proposal or emit STATUS: SKIPPED per your own agent contract.`

Invoke `Task` ONCE (`subagent_type: generalPurpose`, foreground/blocking) — the same
single-agent invocation shape § 5's execution loop uses for any non-parallel phase. Wait
for its result and validate the returned Router Contract per § 6's contract-extraction
rules (a missing or malformed contract is a BLOCKED case, same as any other phase).

### Step 3 — Handle the result

- `STATUS: SKIPPED` (requires non-empty `SKIP_REASON`) → passing state. Advance directly
  to § 8 Memory Finalization. No question is asked.
- `STATUS: FAIL`, or the `Task` call itself fails/times out (§ 6's "tool-call-level
  failure" case) → do NOT block the workflow tail. A failed proposal attempt is not a
  code defect — `skill-author` output is a proposal-authoring/availability signal, not a
  correctness signal for the current BUILD/DEBUG phase. Note the failure in this
  workflow's memory notes, leave the candidate's ledger status untouched (it will be
  retried the next time this gate fires on a future workflow), and proceed directly to
  § 8 Memory Finalization.
- `STATUS: COMPLETE` (requires non-empty `PROPOSAL_PATH` and `CANDIDATE_ID`) → continue
  to Step 4. This is the only outcome that pauses the workflow for a human answer.

### Step 4 — Synchronous confirmation-based approval (router's own turn)

Cursor has no `AskUserQuestion` tool. This step runs in the router's OWN main-session
turn — never inside a dispatched `Task` subagent, which has no channel back to the real
user (see § 4b's identical reasoning for why brainstorming is not dispatched).

1. Read `PROPOSAL.md` from the staged proposal directory
   (`.craftflow/state/project/skill-proposals/{candidate_id}/PROPOSAL.md`) to build the
   evidence-trail summary.
2. Before asking, persist the pending state to `cursor-wf.json` — the same file this
   file already uses to self-track in-progress phase state ad hoc (see § 6 "When a phase
   fails", which writes `pending_gate` into `cursor-wf.json` the same way):
   ```json
   "pending_skill_approval": {
     "candidate_id": "{candidate_id}",
     "proposal_path": ".craftflow/state/project/skill-proposals/{candidate_id}/"
   }
   ```
   This is what lets a resumed/interrupted session pick the flow back up correctly — see
   "Resume behavior" below.
3. In your OWN response text (not a `Task` dispatch), present the evidence-trail summary
   and ask the user to choose exactly one of these four options, worded plainly since
   there is no structured-choice tool:
   ```
   Skill-Distill proposal ready for candidate {candidate_id}:
   {evidence-trail summary from PROPOSAL.md}

   Choose one:
   1) Approve — promote this skill now
   2) Approve + register in SKILL_HINTS — promote AND add to patterns.md ## Project SKILL_HINTS
   3) Reject — discard this proposal permanently (revivable only if recurrence doubles)
   4) Defer — leave it staged, ask again next time this gate fires
   ```
   End your turn here and wait for the user's next message as the answer — the Cursor
   substitute for `AskUserQuestion`: a plain synchronous question in chat, with the reply
   arriving as the next user turn.
4. **Non-matching reply:** if the user's next message does not clearly match one of the
   four options (exact label, number, or unambiguous restatement), re-ask the same
   question verbatim. Never guess, never silently fall through to a default — same rule
   Claude Code's `AskUserQuestion` gate uses.
5. On a clear answer, take the matching action and then clear `pending_skill_approval`
   back to `null` in `cursor-wf.json`:
   - **Approve** → run:
     ```bash
     python3 tools/craftflow-plugin/plugins/craftflow/scripts/craftflow_skill_promote.py \
       --approve {candidate_id} --project-root "$(git rev-parse --show-toplevel)" \
       --ledger .craftflow/state/project/skill-candidates.json \
       --proposals-dir .craftflow/state/project/skill-proposals
     ```
     Exit 0 → the skill now exists at `.claude/skills/{name}/SKILL.md` (synced to
     `.cursor/skills/{name}`); the ledger entry is marked `promoted`. Non-zero exit → do
     NOT clear `pending_skill_approval`; surface the script's stderr to the user and
     stop.
   - **Approve + register in SKILL_HINTS** → run the identical promote command above,
     PLUS add the new skill's id to `.craftflow/state/project/patterns.md ## Project
     SKILL_HINTS` as part of § 8 Memory Finalization's normal persistence step (never a
     direct ad hoc edit outside that flow).
   - **Reject** → do NOT run the promote script. Run:
     ```bash
     python3 tools/craftflow-plugin/plugins/craftflow/scripts/craftflow_skill_ledger.py \
       --reject {candidate_id} --reason "{user-stated or inferred reason}" \
       --ledger .craftflow/state/project/skill-candidates.json
     ```
   - **Defer** → no script action. Leave the candidate's ledger `status` unchanged
     (`proposed`/`candidate`); it remains gate-eligible and resurfaces next time this
     gate fires.
6. Proceed to § 8 Memory Finalization only after the approval decision is resolved.
   Approve, Approve+SKILL_HINTS, Reject, and Defer all resolve it — Defer resolves it by
   explicitly choosing to leave the candidate as-is; it does not mean "skip the
   decision."

### Fail-closed default (JUST_GO-equivalent)

This file's JUST_GO-equivalent is `AUTO_PROCEED: true` in `activeContext.md ## Session
Settings` (§ 2 Memory Load's JUST_GO rule). Same fail-closed default as Claude Code's
`AskUserQuestion` gate:
- **Never** auto-select **Approve** or **Approve + register in SKILL_HINTS** under
  `AUTO_PROCEED: true` — both require an explicit human answer regardless of this
  setting.
- Auto-select **Reject** only when a router-derivable reason exists (the candidate
  signature duplicates an already-`promoted`/`rejected` ledger entry, or another
  objective, router-checkable rubric condition). Log the derived reason in
  `activeContext.md ## Decisions`.
- Otherwise, the fail-closed default is **Defer** — never silently Approve. Log the
  auto-Defer in `activeContext.md ## Decisions`.

### Resume behavior

If `cursor-wf.json` has a non-null `pending_skill_approval` when a session resumes (§ 3
Workflow Preparation's resume check), re-surface the exact same evidence-trail question
from Step 4.3 before continuing any further workflow progress. Do not silently drop the
pending approval and do not silently re-dispatch `skill-author` again for the same
`candidate_id` while an approval decision is still pending.

## 6. Post-Agent Validation

After each dispatched phase's subagent returns its final message, validate its output before proceeding.

### Contract extraction

Look for the `### Router Contract (MACHINE-READABLE)` fenced YAML block in the
dispatched subagent's final message.

Two structurally different failure shapes can occur now that each phase is a real,
isolated `Task` call (rather than inline role-play where output was guaranteed to
exist):

- **Tool-call-level failure:** the `Task` call itself errors or times out — no subagent
  final message is produced at all, so there is no output to search for a contract
  block. This can happen before any subagent output exists.
- **Output-level failure:** a final message exists, but the `### Router Contract
  (MACHINE-READABLE)` block is either entirely absent from it, or present but its YAML
  body is unparseable.

**All three cases funnel to the same BLOCKED path:** treat the phase as invalid. Stop
the workflow, emit a BLOCKED progress block, and ask the user for direction. Do not
silently retry the `Task` call more than once before surfacing BLOCKED to the user.

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
| skill-author | `STATUS=COMPLETE` (non-empty `PROPOSAL_PATH` + `CANDIDATE_ID`) or `STATUS=SKIPPED` (non-empty `SKIP_REASON`, a passing state) — see § 5a for the full gate + approval flow |

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

### Non-matching reply (pending_gate answers)

If the user's next message does not clearly match one of Step 3's three options (exact
label, letter, or unambiguous restatement — e.g. "retry", "(a)", "try it again" all match
retry; "skip it" matches skip; "abort"/"halt" match stop), re-ask the same Step 3
question verbatim. Never guess, never silently fall through to a default — the same rule
§ 5a Step 4.4 uses for `pending_skill_approval` replies.

### Resolution (Step 5 — mirrors § 5a Step 4.5's `pending_skill_approval` resolution)

On a clear answer, take the matching action below. In EVERY one of the three cases,
clear `pending_gate` back to `null` in cursor-wf.json as part of taking that action — a
non-null `pending_gate` must never persist past the answer that resolves it, otherwise
the router would re-ask the same question on every subsequent turn forever.

- **(a) Retry** → clear `pending_gate` to `null`. Do NOT advance `cursor` — re-invoke
  `Task` for the SAME phase, at the same cursor position, rebuilding the identical § 5
  dispatch prompt (same `## Task Context`, `## Requirements`, `## Memory Summary`, etc.)
  that was used for the failed attempt. Feed the retry's result back through this same
  § 6 Post-Agent Validation: pass → `status["{phase}"] = "completed"`, `cursor = cursor +
  1`, resume the § 5 execution loop from the next phase; fail again → repeat this entire
  "When a phase fails" procedure from Step 1 (re-emit BLOCKED, re-set `pending_gate`,
  re-ask Step 3 — there is no retry-attempt cap in v1, the user is asked again each time).

- **(b) Skip** → clear `pending_gate` to `null`. Set `status["{phase}"] = "skipped"` — a
  third valid phase-status value alongside `"completed"`/`"failed"` (§ 5's execution loop
  Step 6 recognizes and writes this value the same way it writes `"completed"`/
  `"failed"`). Advance `cursor = cursor + 1` past the skipped phase and resume the § 5
  execution loop from the next phase.
  - **Downstream impact when the skipped phase is `code-reviewer` or
    `silent-failure-hunter`:** `integration-verifier`'s "Previous Agent Findings
    handoff" (§ 5) still applies, but the skipped agent's sub-block must read
    `**Verdict:** Skipped (user chose skip after failure)` / `**Critical Issues:**
    Skipped — phase not run` instead of a real verdict or "None". This is NOT the same
    case as the fast-path omission (no reviewer/hunter scheduled this round) — silently
    omitting the section here would read as "no findings" when the truth is "never
    checked." Always include the section, with an explicit `Skipped` marker, whenever a
    scheduled reviewer/hunter phase was skipped this round, so `integration-verifier`
    and the user can see the coverage gap instead of a false-confidence "clean" read.

- **(c) Stop** → clear `pending_gate` to `null`. Halt the remaining phase chain
  entirely: do not dispatch any further phases and do not advance `cursor` past the
  failed phase. § 8 Memory Finalization **still runs** on this partially-completed
  workflow — this is an explicit, intentional choice: write whatever `MEMORY_NOTES` were
  collected from phases that did complete before the stop, and record in
  `activeContext.md ## Blockers` (or the workflow-scoped `activeContext.md` if a
  `workflow_uuid` is known) that the workflow was stopped early by user direction at
  phase `{phase}`, so partial progress and the stop reason are never silently lost. Do
  not skip Memory Finalization merely because the chain never reached
  `integration-verifier`.

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

Before starting the steps below: if `worktree_mode == "auto_created"`, complete § 4a Step 5
(merge + cleanup) first. Do not begin memory finalization on an unmerged worktree.

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

These overrides are baked directly into the dispatch prompt template in § 5 Step 2 —
the router no longer reads agent files itself and applies overrides "as it goes" while
role-playing a phase inline. Instead, the router builds a fully self-contained `Task`
prompt BEFORE dispatch, and that prompt already tells the dispatched subagent about
every override below. This section exists for future maintainers of the § 5 dispatch
template: it documents WHY each override line exists, so the reasoning behind the
template isn't lost if the template itself is ever edited. The mechanism moved from
"the router encounters this inline while reading an agent file" to "the router bakes
this into the Task prompt before dispatch" — the override content itself is unchanged.

Some instructions inside an agent .md file were written for the Claude Code sub-agent
context and do not apply to a Cursor-dispatched subagent. Apply these overrides every
time the dispatch prompt is built from a file containing the following patterns:

### TaskUpdate override
When an agent file contains `TaskUpdate(...)` or says:
  "CRITICAL: You MUST call the TaskUpdate tool directly"
  "Writing a text message claiming completion is NOT sufficient"
→ **Skip it.** In Cursor Task-Dispatch Mode, phase completion is determined by the Router
  Contract YAML block you capture in § 5 Step 4. Emitting that YAML block IS the
  completion mechanism — no tool call is needed or possible.

### Skill() override
When an agent file contains `Skill(skill="craftflow:X")` or says
  "invoke each skill via Skill(skill='{name}')":
→ **Replace with:** `Read("tools/craftflow-plugin/plugins/craftflow/skills/X/SKILL.md")`
  and follow that skill file's instructions inline immediately before continuing.

### "Text is insufficient" override
When an agent file says "TaskUpdate is NOT sufficient", "writing text is insufficient",
or any variant of "tool call must execute" — that instruction applies only in the
Claude Code sub-agent context where TaskUpdate is a real tool.
→ **In Cursor Task-Dispatch Mode:** emitting the Router Contract YAML block is both
  necessary and sufficient. No additional tool call is needed.

### Tool-not-available override
When an agent file contains `TaskList()`, `TaskGet()`, or `Agent(...)` calls:
→ **Skip/ignore them.** These tools do not exist in Cursor. Proceed with the next
  instruction in the agent file.

## 10. Hard Rules (Cursor)

- NEVER call TaskCreate, TaskUpdate, TaskList, or TaskGet — no real task-tracking
  system exists in Cursor; continue self-tracking phase state via `cursor-wf.json` +
  the workflow artifact.
- ALWAYS dispatch each phase via a real `Task` call (foreground/blocking,
  `subagent_type: generalPurpose`) — never execute a phase's actual work inline in the
  router's own turn anymore.
- ALWAYS dispatch code-reviewer + silent-failure-hunter via two `Task` calls in the
  SAME message when both are ready, for genuine parallel execution.
- NEVER reference a custom `.cursor/agents/*.md` name as a Task `subagent_type` — it
  will not resolve; always use `generalPurpose` with a fully self-contained prompt.
- NEVER skip the progress block emission — it is the user's only visibility into phase progress
- NEVER advance to the next phase if post-agent validation fails
- NEVER report a workflow complete without memory finalization
- NEVER modify agent .md files, existing SKILL.md files, or hooks/hooks.json
- NEVER consult Claude Code's own `craftflow-router/SKILL.md` or its `references/*.md`
  files to determine HOW to execute a phase (PLAN included — see § 4b). This file is
  fully self-contained for every workflow type. Those files' `TaskCreate()`/`TaskUpdate()`
  call syntax describes a different tracking mechanism and must never be treated as
  instructions to follow literally here — the only real dispatch mechanism in Cursor is
  the `Task` tool per § 5.
- ALWAYS write cursor-wf.json after each phase completes
- ALWAYS write the main workflow artifact for hook and resume compatibility
- NEVER auto-select Approve or Approve + register in SKILL_HINTS for the § 5a
  Skill-Distill Gate under `AUTO_PROCEED: true` — both require an explicit human
  answer regardless of that setting; the fail-closed default is Defer.
- `SKILL_DISTILL: skip` in `activeContext.md ## Session Settings` disables the § 5a
  Skill-Distill Gate entirely — never run the ledger query, never dispatch
  `skill-author`, when present.
- If context window grows too large (150K+ tokens), warn in progress block:
  "⚠️ Context is large — consider breaking this request into smaller phases"
  Then ask the user whether to continue or stop. Note: since each phase's actual work
  now happens inside an isolated dispatched subagent's own context — not the router's —
  the router's own turn only accumulates each phase's final returned message, not its
  full working transcript, so this risk is reduced compared to inline execution. It is
  not eliminated: many phases' final messages plus the router's own bookkeeping can
  still add up across a long workflow.
