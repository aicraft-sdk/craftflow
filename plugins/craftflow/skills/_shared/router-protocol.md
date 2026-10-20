# Craftflow Router Protocol (Shared)

> Host-agnostic orchestration protocol. Both `skills/craftflow-router/SKILL.md` (Claude
> Code) and `skills/cursor-router/SKILL.md` (Cursor) `Read()` this file for the sections
> below instead of inlining them. Migration history: `docs/router-protocol-history.md`
> (repo-relative to `tools/craftflow-plugin/`).
>
> **Purity boundary:** this file must contain no host-specific tool-invocation syntax —
> no literal `Task()`/`TaskCreate()` call syntax, no literal Cursor-only field names. Only
> host-specific binding docs (`craftflow-router/SKILL.md`, `cursor-router/SKILL.md`) may
> contain that.

## Memory File Required Sections

| File | Required Sections |
|------|-------------------|
| `activeContext.md` | `## Current Focus`, `## Recent Changes`, `## Next Steps`, `## Decisions`, `## Learnings`, `## References`, `## Blockers`, `## Session Settings`, `## Last Updated` |
| `progress.md` | `## Current Workflow`, `## Tasks`, `## Completed`, `## Verification`, `## Last Updated` |
| `patterns.md` | `## User Standards`, `## Common Gotchas`, `## Project SKILL_HINTS`, `## Last Updated` |

(Host-specific note: this table is byte-identical in both hosts' binding docs — confirmed by
direct comparison, not just by the mapping table's classification. If a required section is
missing, both hosts create it before proceeding; Claude Code's auto-heal rule (insert before
`## Last Updated`, verify via `Read()` after `Edit()`) and JUST_GO rule are meaningfully more
detailed than Cursor's own versions of those same concepts and are NOT moved here — see each
binding doc's own section for its real behavior.)

## Agent Verdict Headings

Primary signal (Claude Code only — see host-specific note below):
- Line 1: `CONTRACT {"s":"...","b":...,"cr":...}`

Fallback heading on line 2:
- `## Review: Approve|Changes Requested`
- `## Error Handling Audit: CLEAN|ISSUES_FOUND`
- `## Verification: PASS|FAIL`
- `## Fix Verification: LOAD_BEARING|NOT_LOAD_BEARING|SIBLING_FOUND`
- `## Planning Review: Pass|Findings`

(Host-specific note: these fallback heading strings are byte-identical across hosts —
confirmed by direct comparison, not just by the mapping table's classification — because
both hosts dispatch the same underlying `craftflow:*` agent `.md` files, which emit this
exact text regardless of which router dispatched them; Cursor's own "Verdict by agent"
table literally reuses `## Review: Approve`, `## Error Handling Audit: CLEAN`, and `##
Verification: PASS` as its own pass conditions. The `CONTRACT {"s":...}` envelope line-1
primary signal is Claude-Code-only — no Cursor equivalent was found; Cursor's own
extraction mechanics go straight to the `### Router Contract (MACHINE-READABLE)` YAML
block for both read-only and write agents, with no distinct envelope-then-fallback-heading
two-path split. The verdict-extraction algorithm itself (5-line scan, `SELF_REMEDIATED`
task-state detection, scenario reconciliation) and the full per-agent required-fields
tables are NOT moved here — confirmed host-specific-in-depth: Cursor's own "Verdict by
agent" table covers fewer agent types than Claude's required-fields table, e.g. no
doubt-verifier/fix-verify rows exist there. See each binding doc's own section for its
real extraction mechanics.)

## Resolve Project Root

[EASY TO MISS: `## 0.` sits between `## 1.` and `## 2.` intentionally — a literal "0"
heading, not a full renumber of `## 2.`-`## 7.`, matching the approved design's own "§0"
terminology and minimizing blast radius on an already 1000+-line file.]

1. At the start of every workflow (PLAN/DEBUG/REVIEW/BUILD), before any
   `.craftflow/state/...` path is touched, resolve the project root:
   ```bash
   PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
   TOPLEVEL_EXIT=$?
   ```
   [EASY TO MISS: Use `git rev-parse --show-toplevel` for the absolute path — never a bare
   `~/.claude/worktrees/` because `~` may not expand correctly in all shell contexts, and a
   relative path resolves against cwd, not the project root.]

   - **If `TOPLEVEL_EXIT == 0`**: `PROJECT_ROOT` is set — this is the unchanged single-repo
     path. `PROJECT_ROOT` is now set for the remainder of this session — every later step in
     this document (memory load, workflow-artifact creation, resume, and — for BUILD only —
     worktree creation) consumes this same value. Skip step 1a.
   - **If `TOPLEVEL_EXIT != 0`**: cwd itself is not a git repo. This happens when a session is
     launched at a multi-repo workspace root (a directory that is not itself a git repo but
     contains several independently git-initialized nested repos, e.g. `ai-infra/` containing
     `ai-platform-core/`, `genai-platform-dev/`, etc.). Run **step 1a** below before deciding
     whether to proceed.

   **1a. Multi-repo workspace root resolution** (only reached when `TOPLEVEL_EXIT != 0`): read
   `skills/_shared/router-protocol-multirepo.md` and follow it.

(Host-specific note: both hosts run this exact algorithm. Execution order differs by host (sequencing only, not content): Claude Code resolves once at session start, before `## 1.`; Cursor resolves inline at the start of `## 4a. Worktree Isolation`. See each binding doc.)

## Jev Auto-Detect Consent Request Contract

When `<craftflow_jev_consent_request>` appears in `additionalContext` (injected by the optional SessionStart consent-ask hook):
- The assistant MUST call `AskUserQuestion` with the embedded question, options, and privacy note BEFORE its first substantive reply this turn.
- Then run the embedded `--record-consent {granted|declined}` command matching the user's answer.
- Never re-ask in the same session (structural idempotency enforced by the hook via an `already_asked_consent` flag).
- Granting consent activates Jev starting at the next automatic session check (next `SessionStart` firing), not immediately within the current turn/session — this latency is disclosed to the user at ask-time.

See `docs/ai/decisions/0038-craftflow-jev-auto-detect-consent.md` for the full design.

(Host-specific note: this tag and its contract are Claude-Code-only, as the optional SessionStart Jev auto-detect hook is not registered in Cursor's hooks.json.)

## Intent Routing

Route using the first matching signal:

| Priority | Signal | Keywords | Workflow | Chain |
|----------|--------|----------|----------|-------|
| 1 | ERROR | error, bug, fix, broken, crash, fail, debug, troubleshoot, issue | DEBUG | bug-investigator -> code-reviewer -> integration-verifier |
| 2 | PLAN | plan, design, architect, roadmap, strategy, spec, brainstorm | PLAN | brainstorming -> planner -> bounded fresh review loop |
| 3 | REVIEW | review, audit, analyze, assess, "is this good" | REVIEW | code-reviewer |
| 4 | DEFAULT | Everything else | BUILD | fast path: builder -> verifier -> memory (default); full chain: builder -> [code-reviewer \|\| silent-failure-hunter] -> verifier -> memory (when risk keywords match) |

Rules:
- NEVER use Claude Code's native plan mode (EnterPlanMode). Craftflow owns planning. All "plan", "design", "architect", "brainstorm" requests route to the Craftflow PLAN workflow — not to the built-in plan mode tool. EnterPlanMode bypasses Craftflow orchestration, memory, workflow artifacts, and verification entirely.
- ERROR always wins over BUILD.
- REVIEW is advisory only. Never let REVIEW create code-changing tasks.
- BUILD uses fast path (builder → verifier → memory) by default when no risk keywords are detected in the request. Full chain (builder → reviewer → hunter → verifier → memory) is used when risk keywords match. See each binding doc's own fast-path detection reference for keyword rules.
- Before execution, output one line: `-> {WORKFLOW} workflow (signals: {matched keywords})`
- Optional routing hint: when the turn context contains a `<craftflow_routing_hint source="jev">` block, priority-1 ERROR keywords still win unconditionally. Otherwise, a `workflow:` line in that block is consulted BEFORE the keyword table (the hook only emits lines already at/above the configured confidence threshold). A `risk_full_chain` value ≥ 0.5 counts as one additional risk signal for the fast-path decision and never removes a keyword-matched signal. No block, or no `workflow:` line → the keyword table applies unchanged. Announce as `-> {WORKFLOW} workflow (signals: {matched keywords}; jev: {workflow} {confidence})` when the hint was consulted.

(Host-specific additions: Cursor's router applies 3 additional risk keywords — `concurrent`, `race`, `rollback` — beyond the shared fast-path keyword table, and runs an additional Cursor-only "Pending-answer precedence check" before this routing table, per its own binding doc. Claude Code's router may additionally receive the optional routing-hint block above from the opt-in `UserPromptSubmit` hook (`config/jev.json`, off by default; Claude Code only, no Cursor equivalent) — see `craftflow-router/SKILL.md` § 1. None of these additions belongs here — see `cursor-router/SKILL.md` § 1 and `craftflow-router/SKILL.md` § 1.)

## Dispatch Prompt Scaffold

### Prompt scaffold for every agent

```text
## Task Context
- Task ID: {task_id}
- Parent Workflow ID: {workflow_uuid}
- Task Phase: {phase}
- Plan File: {plan_file or 'None'}
- Workflow Scope: wf:{workflow_uuid}
- Workflow Artifact: $PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}.json
- Effort Directive: {low|medium|high — from fast-path.md dispatch table; append one-line steering per §4 Effort Steering Directives}

## User Request
{request}

## Requirements
{clarified requirements or 'See plan/design files'}

## Memory Summary
{brief activeContext summary}

## Project Patterns
{User Standards + Common Gotchas, trimmed if needed}

## Domain Context
{If UBIQUITOUS_LANGUAGE.md, DOMAIN_GLOSSARY.md, docs/domain/*.md, or project-context.md exist, include content. Otherwise omit section.}

## SKILL_HINTS
{router-detected skill list or "None"}
```

Optional sections:
- `## Pre-Answered Requirements` for BUILD when router already gathered decisions.
- `## Intent Contract` when a plan or design already defined goal, constraints, acceptance criteria, and named scenarios.
- `## Research Files` only when at least one research file exists.
- `## Research Quality` only when at least one research result exists.
- `## Design File` only for planner.
- `## Target Plan File` for `planner`/`plan-bakeoff-judge` dispatches where the router assigns a
  non-default target path (every PLAN scout/candidate/judge dispatch under the bake-off mechanism;
  omitted for a direct/manual planner invocation that self-derives its own filename per
  `planner.md` Process step 14).
- `## Candidates` only for `plan-bakeoff-judge`, assembled from `results.bakeoff[]`.
- `## Planning Review Findings` only for `re-plan`.
- `## Original User Request` only for `plan-gap-reviewer`.
- `## Approved Context Files` only for `plan-gap-reviewer`.
- `## Previous Agent Findings` only for integration-verifier and only after review/hunt phases.

(Host-specific additions: Cursor's dispatch prompt additionally injects a `## Worktree` block and a "you are an isolated subagent, read your own brief" preamble, both necessary only because `Task`'s `generalPurpose` subagent doesn't natively load an agent's system prompt the way Claude Code's registered subagent types do. Claude Code needs neither — see each binding doc's own dispatch section. Cursor's template also omits `## Domain Context`, `## SKILL_HINTS` and `Effort Directive`, so it stays fully inline in `cursor-router/SKILL.md` § 5.)

### Prompt assembly rule

- Every routed prompt must be self-contained from the workflow artifact, approved files, and the current task contract.
- Do not rely on prior chat turns or completed-phase narrative when the same fact already exists in the workflow artifact, plan, design, or research files.
- Include only the current-phase objective, live blockers, approved decisions, and directly relevant evidence. Omit unrelated completed-phase detail.

## Findings handoff

The previous-agent and verifier findings handoff blocks live in `skills/_shared/verifier-handoff.md`.

## Skill-Distill approval

The approval flow lives in `skills/_shared/skill-distill-approval.md`.

## Explicit Dispatcher (Phase-to-Agent Table)

| Task Phase / Kind | Agent |
|-------------------|-------|
| `build-implement` | `craftflow:component-builder` |
| `debug-investigate` | `craftflow:bug-investigator` |
| `build-review`, `debug-review`, `review-audit`, `re-review` | `craftflow:code-reviewer` |
| `build-hunt`, `re-hunt` | `craftflow:silent-failure-hunter` |
| `build-verify`, `debug-verify`, `re-verify` | `craftflow:integration-verifier` |
| `doubt-verify` | `craftflow:doubt-verifier` |
| `fix-verify` | `craftflow:doubt-verifier` |
| `plan-create`, `re-plan` | `craftflow:planner` |
| `plan-bakeoff-candidate-opus`, `plan-bakeoff-candidate-sonnet`, `plan-bakeoff-candidate-haiku`, `plan-bakeoff-candidate-fable` | `craftflow:planner` (Mechanism A: router passes `model` as a literal runtime dispatch parameter on each call — see workflow `activeContext.md ## Decisions`, Phase 0) |
| `plan-bakeoff-judge` | `craftflow:plan-bakeoff-judge` |
| `plan-review-gap-1`, `plan-review-gap-2` | `craftflow:plan-gap-reviewer` |
| `research-web` | `craftflow:web-researcher` |
| `research-github` | `craftflow:github-researcher` |
| `kind:remfix` + `origin:bug-investigator` | `craftflow:bug-investigator` |
| `build-doc-sync` | `craftflow:doc-syncer` |
| `learn-distill` | `craftflow:learn-distiller` |
| `skill-distill` | `craftflow:skill-author` |
| `kind:remfix` + `origin:code-reviewer|silent-failure-hunter|integration-verifier|router` | `craftflow:component-builder` |

(Host-specific: this table states WHICH agent role handles each phase — the routing intent
itself, shared across hosts. HOW each host turns an agent role into something invocable is
host-specific and stays in each binding doc: Claude Code resolves these via `Task()`/
`TaskCreate()` against registered subagent types using the `craftflow:` name directly;
Cursor's `Task` tool cannot select custom subagent types by name at all, so
`cursor-router/SKILL.md`'s `## Agent File Paths` maps each role to a literal
`.md` file path instead. See each binding doc's own section for its resolution mechanics.
`craftflow-router/SKILL.md` also keeps its own short trailing note on `skill-author` not
being a `kind:remfix` origin inline, since it cross-references that file's own `## 6.`
section numbering.)
