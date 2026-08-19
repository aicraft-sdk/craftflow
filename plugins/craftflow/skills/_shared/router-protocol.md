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
> **Migration status (Phase 3, Claude side):** Phase 3 extracted the plan's two most
> clearly-delineated, cleanly-separable `shared`-classified sections — Intent Routing and
> the dispatch prompt scaffold. Phase 3b added `## 0. Resolve Project Root` (below, as
> "Resolve Project Root") — the resolution algorithm itself is identical in substance
> across hosts (Cursor's own text already said so directly before this extraction:
> "Resolve exactly as Claude Code's `craftflow-router/SKILL.md` does"). Phase 3c added
> the Skill-Distill Approval Flow. Phase 3d added the Explicit Dispatcher
> (Phase-to-Agent) table, as pure data. Phase 3e attempted `## 12. Chain Execution Loop`
> and made a deliberate no-op call — its policy logic is threaded sentence-by-sentence
> through literal `TaskList()` mechanics in the real text, not cleanly separable without
> either violating the purity boundary or the verbatim-move rule; it remains inline in
> `craftflow-router/SKILL.md` pending a future, deliberately-paraphrased treatment. Phase
> 3f added "Previous Agent Findings Handoff" — pure prose/template content with no
> host-specific tool syntax. Phase 3g (this pass) added "Verifier Findings Handoff" — same
> kind of pure prose, explicitly cross-referenced by Cursor's own text as sharing the same
> fast-path-omission rule. Several other sections the mapping table classifies
> `shared` (`### Parent workflow creation`'s artifact schema, `## 13. Memory
> Finalization`'s two-tier concept, `### Worktree Isolation`'s project-root-reuse text,
> `JUST_GO:`) remain deliberately left inline in `craftflow-router/SKILL.md` — see that
> file's own inline notes at each section, and the Phase 3/3b completion reports, for why:
> `craftflow_hook_unit_tests.py` anchors dense, exact-position, and in one case
> exact-match-paragraph assertions directly inside those sections, and a clean
> shared/host-specific split was not achievable without disproportionate regression risk
> relative to a follow-up, more carefully scoped sub-phase. This file will grow as those
> follow-ups land.

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

   **1a. Multi-repo workspace root resolution** (only reached when `TOPLEVEL_EXIT != 0`):
   ```bash
   CRAFTFLOW_INSTALL=$(python3 -c "
   import json, pathlib
   reg = json.loads(pathlib.Path.home().joinpath('.claude/plugins/installed_plugins.json').read_text())
   print(reg['plugins']['craftflow@craftflow'][0]['installPath'])
   ")
   CRAFTFLOW_INSTALL_EXIT=$?
   RESOLVE_RESULT=$(python3 "$CRAFTFLOW_INSTALL/scripts/craftflow_resolve_workspace_root.py" \
     --cwd "$(pwd)" \
     --request "USER_REQUEST_SHELL_ESCAPED")
   RESOLVE_EXIT=$?
   ```
   (A non-zero `CRAFTFLOW_INSTALL_EXIT` here is not handled as a separate branch — it surfaces
   downstream as a non-zero `RESOLVE_EXIT` from the next command, an empty/unusable
   `$CRAFTFLOW_INSTALL` path, which the existing `RESOLVE_EXIT != 0` handling below already
   catches.)
   Replace `USER_REQUEST_SHELL_ESCAPED` with the actual user request, properly shell-quoted
   (same convention as **Parent workflow creation** step 1). The script never mutates git or
   filesystem state — it only reads cwd's immediate child directories and runs non-mutating
   `git rev-parse --show-toplevel` calls.

   - **If `RESOLVE_EXIT != 0`** (the script itself could not complete the scan, e.g. cwd
     unreadable): do not parse `$RESOLVE_RESULT`. Treat identically to `NO_REPO_FOUND` below.
   - **Otherwise**, parse the outcome:
     ```bash
     RESOLVE_OUTCOME=$(printf '%s' "$RESOLVE_RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['outcome'])")
     ```
     Also capture the workspace-root file allowlist `$RESOLVE_RESULT` may carry — safe and inert
     for every outcome reachable in this branch (`DETERMINISTIC`, `AMBIGUOUS`, or `NO_REPO_FOUND`
     reached via a successful `RESOLVE_EXIT == 0` scan), since the resolver script's
     `resolve()` never includes these keys for `NO_REPO_FOUND` at all — `.get(..., [])` degrades to
     an empty list in that case, exactly as intended. This capture is explicitly UNREACHABLE when
     `RESOLVE_EXIT != 0` (the branch immediately above this one, which never parses
     `$RESOLVE_RESULT` at all):
     ```bash
     WORKSPACE_WRITABLE_PATHS_JSON=$(printf '%s' "$RESOLVE_RESULT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps(d.get('workspace_writable_paths', [])))")
     WORKSPACE_WRITABLE_PATHS_DROPPED_JSON=$(printf '%s' "$RESOLVE_RESULT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps(d.get('workspace_writable_paths_dropped', [])))")
     ```
     `WORKSPACE_WRITABLE_PATHS_JSON` is held in-session — it is written into the workflow artifact
     at **Parent workflow creation** (`## 6.`) below, the same ordering constraint the
     `project_root_resolution_fallback` reason already works around (the artifact doesn't exist
     yet at this point in `## 0.`). If `WORKSPACE_WRITABLE_PATHS_DROPPED_JSON` is non-empty (`!=
     '[]'`), fold it into `## 6.`'s own initial event-log write alongside `workflow_started`, the
     same conditional pattern as `project_root_resolution_fallback` below —
     `{"event":"workspace_writable_paths_entries_dropped","dropped":<value>}`.
     **Caveat — the two in-session values are NOT equivalent in what is lost if they never reach
     `## 6.`:** `project_root_resolution_fallback` is a diagnostic string only — its own note above
     already documents that nothing functional is lost if it never gets durably recorded.
     `WORKSPACE_WRITABLE_PATHS_JSON` is a functional payload — nothing else re-echoes or reuses it
     between this capture and `## 6.`'s write, so if it is lost in-session (e.g. the session ends
     before `## 6.` runs), the workspace-root allowlist is silently disabled for that workflow
     (the artifact's `workspace_writable_paths` stays at its `[]` default). The downstream guard
     in `craftflow_hooklib.py`/`craftflow_pretooluse_guard.py` still fails closed regardless — an
     empty allowlist only denies writes to the workspace-root files it would otherwise have
     permitted, it never grants anything extra.
     - **`DETERMINISTIC`** (exactly one candidate nested repo exists, or the request text
       uniquely names one among several):
       ```bash
       PROJECT_ROOT=$(printf '%s' "$RESOLVE_RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['project_root'])")
       ```
       `PROJECT_ROOT` is now set for the remainder of this session — every later step in this
       document (memory load, workflow-artifact creation, resume, and — for BUILD only —
       worktree creation) consumes this same value.
     - **`AMBIGUOUS`** (2+ candidate nested repos exist and the request text does not uniquely
       name one): ask the user once via `AskUserQuestion`, with one option per path in the
       `candidates` array from `$RESOLVE_RESULT` (optionally enrich each option's label with
       that repo's `git -C <candidate> log -1 --format=%s` first line, if available). Set
       `PROJECT_ROOT` to the chosen candidate's absolute path. `PROJECT_ROOT` is now set for
       the remainder of this session — every later step in this document (memory load,
       workflow-artifact creation, resume, and — for BUILD only — worktree creation) consumes
       this same value.
       [EASY TO MISS: this `AskUserQuestion` gate is NEVER auto-defaulted under `JUST_GO=true`
       (§ 2 `JUST_GO` rule) — cross-repo routing has no safe "recommended" default the way an
       ordinary implementation-choice gate does, so it is treated the same as an unresolved
       plan **Open Decision**: always stop and ask, even in `JUST_GO` mode.]
     - **`NO_REPO_FOUND`** (or `RESOLVE_EXIT != 0` above): no git-repo children exist under
       cwd at all (or the resolver script itself could not complete the scan). Set
       `PROJECT_ROOT=$(pwd)` as the fallback — this raw-cwd value still resolves correctly for
       the intended purpose; the single-repo case was already handled above, and this branch
       only covers a workspace root with no nested git repos, or a scan failure, where cwd is
       the only available candidate.
       [EASY TO MISS: `## 0.` runs BEFORE `workflow_uuid` is minted (minted later, at
       **Parent workflow creation** in `## 6.`), so no per-workflow event log
       (`$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}.events.jsonl`) can exist yet
       at this point — it is filename-keyed on `workflow_uuid`, which literally cannot exist
       yet. Do not append an event to "the event log" here; there is nothing to append to.
       Instead, keep `reason:"NO_REPO_FOUND"` (resolver returned that outcome) or
       `reason:"RESOLVE_SCRIPT_ERROR"` (`RESOLVE_EXIT != 0`) in-session, and fold it into
       `## 6.`'s own initial `status_history`/event-log write once the workflow artifact is
       created — e.g.
       `{"event":"project_root_resolution_fallback","reason":"NO_REPO_FOUND"|"RESOLVE_SCRIPT_ERROR"}`
       alongside `workflow_started`. This is necessarily a best-effort, undurable signal at
       this early stage: `PROJECT_ROOT` is already correctly resolved via the raw-cwd fallback
       regardless of whether the reason is ever durably recorded, so nothing functional is lost
       if a workflow artifact never ends up being created (e.g. the session ends before `## 6.`
       runs).] This is an event, not a `pending_gate` — there is nothing to resume or retry.
       The router continues immediately with this fallback `PROJECT_ROOT` value.

(Host-specific note: both hosts run this exact algorithm; `cursor-router/SKILL.md`
previously stated "Resolve exactly as Claude Code's `craftflow-router/SKILL.md` does" —
see its own binding doc for exactly when in its execution order this step runs, which
differs by host (Claude Code: once at session start, before `## 1.`; Cursor: inline at the
start of `## 4a. Worktree Isolation`), a sequencing detail, not a content difference.)

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

## Previous Agent Findings Handoff

When invoking `integration-verifier`, pass:

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

DEBUG skips hunter findings.

(Host-specific note: Cursor's binding doc's own "Previous Agent Findings handoff
(integration-verifier only)" section states it uses the same sub-block format and the same
fast-path-omission rule as this one — see `cursor-router/SKILL.md` § 5 for its own dispatch
mechanics around when this block is attached to a `Task` call.)

## Verifier Findings Handoff

Before invoking `integration-verifier` in BUILD:
- Read `results.reviewer` and `results.hunter` from the workflow artifact.
- Build `## Previous Agent Findings` exactly in the format verifier expects.
- Never invoke verifier without that section when review/hunt already ran.
- **fast-path exception:** When `build_mode == "fast_path"`, omit `## Previous Agent Findings` from the verifier prompt entirely — no reviewer or hunter ran. The verifier must run independent scenario coverage. When `build_mode == "fast_path_escalated"` (after escalation), the merged findings handoff IS required using the standard format above.

(Host-specific note: Cursor's binding doc explicitly cross-references this as using "the
same fast-path-omission logic already used for the Claude Code router" — see
`cursor-router/SKILL.md` § 5 for its own dispatch mechanics around when this block is
attached to a `Task` call.)

## Skill-Distill Approval Flow

After `craftflow:skill-author` (the `skill-distill` task) returns `STATUS: COMPLETE`
(meaning it staged a real proposal under `.craftflow/state/project/skill-proposals/{candidate_id}/`),
the router must surface it to the user via `AskUserQuestion` before doing anything else —
before doc-sync, before Memory Update, before any other pending task in the chain. Read
`PROPOSAL.md` from the staged proposal directory to build the evidence-trail summary shown
to the user. Present exactly these four options:

- **Approve** → run:
  ```bash
  python3 {plugin_root}/scripts/craftflow_skill_promote.py --approve {candidate_id} --project-root {project_root} --ledger {state_root}/project/skill-candidates.json --proposals-dir {state_root}/project/skill-proposals
  ```
  On exit 0, the canonical skill now exists at `.claude/skills/{name}/SKILL.md` (synced to
  `.cursor/skills/{name}`) and the ledger candidate is marked `promoted`. On non-zero exit,
  do not advance the workflow — surface the script's stderr to the user and stop.
- **Approve + register in SKILL_HINTS** → run the identical promote command above, PLUS emit
  a `MEMORY_NOTES` entry (routed through the normal memory-finalize persistence path, never a
  direct edit) adding the new skill's id to `patterns.md ## Project SKILL_HINTS` so future
  craftflow-dispatched subagents pick it up automatically.
- **Reject** → the router does NOT call `craftflow_skill_promote.py` for this option. It runs:
  ```bash
  python3 {plugin_root}/scripts/craftflow_skill_ledger.py --reject {candidate_id} --reason "{user-stated or inferred reason}" --ledger {state_root}/project/skill-candidates.json
  ```
  This tombstones the candidate (`status: rejected`, permanent unless `distinct_workflows`
  at least doubles from the value recorded at rejection time — enforced by the ledger's own
  revival logic in `--observe`, not by the router).
- **Defer** → take no script action. Leave the candidate as `status: proposed`/`candidate`
  as-is; it remains gate-eligible and will be re-surfaced the next time `skill-distill` gates
  in for a future workflow.

**Non-matching reply:** If the user's reply does not clearly match one of these four options
(exact label or an unambiguous restatement), re-issue the same `AskUserQuestion` with the same
four options. Never guess, never fall through to a default on an ambiguous reply — only the
JUST_GO carve-out below is permitted to auto-select without an explicit human answer.

**JUST_GO carve-out:** This `AskUserQuestion` gate is treated as a de facto REVERT-class gate
for JUST_GO purposes, overriding the general "auto-default all non-REVERT `AskUserQuestion`
gates to the recommended option" rule in `## 2. Memory Load And Template Validation` — there is
no textual "recommended option" signal for this gate, and the highest-consequence misread
(auto-Approve) would promote an LLM-authored skill into `.claude/skills` with zero human
review. Under `JUST_GO=true` (`AUTO_PROCEED: true`):
- **Never** auto-select **Approve** or **Approve + register in SKILL_HINTS** — both require an
  explicit human answer regardless of `AUTO_PROCEED`.
- Auto-select **Reject** only when a router-derivable reason exists (e.g., the proposal fails
  an objective, router-checkable rubric condition, or the candidate signature duplicates an
  already-`promoted`/`rejected` entry). Log the derived reason in `## Decisions`.
- Otherwise (no router-derivable reject reason), the fail-closed default is **Defer**. Log the
  auto-Defer in `## Decisions`.

If `skill-distill` returns `STATUS: SKIPPED`, this is a passing state — advance directly to
Memory Update, no `AskUserQuestion`. If it returns `STATUS: FAIL`, or does not return at all
(stuck/timeout): `skill-author` is NOT a `kind:remfix` origin (see `## 7. Dispatcher And Agent
Prompt Contract`) — do not create a REM-FIX task and do not block the remaining chain tail
(doc-sync/Memory Update). Append a `skill_distill_failed` event to the workflow event log with
the failure reason if available, leave the candidate's ledger status unchanged (still
`candidate`, flagged for manual retry the next time this gate fires), and advance directly to
Memory Update.

(Host-specific additions: `cursor-router/SKILL.md`'s `## 5a. Skill-Distill Gate` mirrors this
flow in intent — same four options, same two scripts, same JUST_GO-never-auto-Approve
carve-out — but presents them via plain-text chat plus `cursor-wf.json`'s
`pending_skill_approval` field instead of `AskUserQuestion`, since Cursor has no equivalent
tool. See that file's own section for its host-specific mechanics.)

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
