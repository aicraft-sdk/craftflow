# Craftflow Router Protocol (Shared)

> Host-agnostic orchestration protocol. Both `skills/craftflow-router/SKILL.md` (Claude
> Code) and `skills/cursor-router/SKILL.md` (Cursor) `Read()` this file for the sections
> below instead of inlining them — see
> `docs/plans/2026-08-19-craftflow-hooks-as-bridge-design.md` (backlog item 8, Layer B)
> and `docs/plans/2026-08-19-router-protocol-mapping.md` for the classification this
> extraction follows.
>
> **Purity boundary:** this file must contain no host-specific tool-invocation syntax —
> no literal `Task()`/`TaskCreate()` call syntax, no literal Cursor-only field names. Only
> host-specific binding docs (`craftflow-router/SKILL.md`, `cursor-router/SKILL.md`) may
> contain that.
>
> **Migration status (Phase 3, Claude side):** this is a first-draft extraction covering
> the plan's two most clearly-delineated, cleanly-separable `shared`-classified sections —
> Intent Routing and the dispatch prompt scaffold. Several other sections the mapping table
> classifies `shared` (`## 0. Resolve Project Root`, `### Parent workflow creation`'s
> artifact schema, `## 13. Memory Finalization`'s two-tier concept, `### Worktree
> Isolation`'s project-root-reuse text, `JUST_GO:`) were deliberately left inline in
> `craftflow-router/SKILL.md` for this pass — see that file's own inline notes at each
> section, and the Phase 3 completion report, for why: `craftflow_hook_unit_tests.py`
> anchors dense, exact-position, and in one case exact-match-paragraph assertions directly
> inside those sections, and a clean shared/host-specific split was not achievable in this
> pass without disproportionate regression risk relative to a follow-up, more carefully
> scoped sub-phase. This file will grow as those follow-ups land.

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

(Host-specific additions: Cursor's router applies 3 additional risk keywords — `concurrent`, `race`, `rollback` — beyond the shared fast-path keyword table, and runs an additional Cursor-only "Pending-answer precedence check" before this routing table, per its own binding doc. Neither addition belongs here — see `cursor-router/SKILL.md` § 1.)

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
- `## Planning Review Findings` only for `re-plan`.
- `## Original User Request` only for `plan-gap-reviewer`.
- `## Approved Context Files` only for `plan-gap-reviewer`.
- `## Previous Agent Findings` only for integration-verifier and only after review/hunt phases.

(Host-specific additions: Cursor's dispatch prompt additionally injects a `## Worktree` block and a "you are an isolated subagent, read your own brief" preamble, both necessary only because `Task`'s `generalPurpose` subagent doesn't natively load an agent's system prompt the way Claude Code's registered subagent types do. Claude Code needs neither — see each binding doc's own dispatch section.)

### Prompt assembly rule

- Every routed prompt must be self-contained from the workflow artifact, approved files, and the current task contract.
- Do not rely on prior chat turns or completed-phase narrative when the same fact already exists in the workflow artifact, plan, design, or research files.
- Include only the current-phase objective, live blockers, approved decisions, and directly relevant evidence. Omit unrelated completed-phase detail.
