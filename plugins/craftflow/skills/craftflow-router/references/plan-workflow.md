### PLAN preparation

0. Workflow setup (runs immediately after `workflow_uuid`/`WF_INFO` are minted — this already
   happens in `SKILL.md § 6`'s Parent workflow creation (`references/workflow-start.md`), reused verbatim, no new script — and
   BEFORE Constitution Check below):
   - Derive `plan_file_stem` (collision-guarded):
     ```text
     date_part = iso_timestamp[0:10]  # "YYYY-MM-DD"
     plan_file_stem = f"{date_part}-{WF_INFO['slug']}"
     Glob(pattern=f"docs/plans/{plan_file_stem}-*.md")
     If any match: plan_file_stem = f"{plan_file_stem}-{WF_INFO['short_hex']}"
     Persist plan_file_stem in the workflow artifact.
     ```
   - Parse `bakeoff_n_requested` from `user_request`: scan `user_request` (case-insensitive) for
     an explicit candidate count near bake-off language — pattern:
     `\b(\d+)\s*(candidates?|models?|plans?)\b` co-occurring with any of
     `bake[- ]?off|compare models|parallel plan`. If found, clamp to `[2,4]`; if absent, default
     `2`. Persist as `bakeoff_n_requested` (the user's raw request before any qualify/skip
     decision — this is independent of whether the bake-off ever actually triggers, since qualify
     is decided later by the scout's own contract, not by this scan. This scan ONLY determines
     "if a bake-off happens, how many candidates" — it is explicitly NOT the qualify/no-qualify
     decision itself, per the design's Q&A: "No second, separate risk-classification heuristic
     exists anywhere in this design." N-parsing and qualify-detection are two independent,
     non-overlapping concerns — do not conflate them.
   - Constitution Check (MANDATORY — runs before brainstorming or planner):
     - Read `.craftflow/state/project/constitution.md` if it exists (it was loaded in § 2 Memory Load step 5 — reference the loaded copy; no second read needed).
     - If absent, skip this step gracefully.
     - Scan the user's intent for MUST constraint violations (MUST-1 through MUST-7). If a violation is detected, surface the specific principle to the user and halt before brainstorming begins. SHOULD violations are advisory — log in `approved_decisions` but do not block.
1. Restore design enrichment:
   - Read `- Design:` from `activeContext.md ## References`.
   - If a design path exists, verify it with `Glob(...)` and hold it as fallback.
2. Mandatory brainstorming (ALWAYS runs for PLAN workflows):
   - ALWAYS run `Skill(skill="craftflow:brainstorming")` in the main context before planner. Brainstorming is how the user explores and clarifies intent — skipping it means the planner works from assumptions instead of understanding.
   - If a valid design file exists from step 1: brainstorming uses it as a foundation (the skill's Spec File Workflow reads and expands the existing design rather than starting from scratch).
   - If no design file exists: brainstorming starts from the user's request and explores the idea space.
   - Brainstorming may ask the user questions and may save a `*-design.md` file. After it completes, parse `### Brainstorming Handoff (MACHINE-READABLE)` and capture `DESIGN_FILE`.
   - If `DESIGN_FILE` is present, persist it into the workflow artifact `design_file` field and pass it under `## Design File`.
   - If no handoff is present, fall back to the pre-existing memory design reference from step 1.
   - Brainstorming should ask only unresolved, high-impact questions and stop as soon as the intent contract is complete.
3. Optional research before planning:
   - Ask whether to run web + GitHub research for external/unfamiliar technology when it would materially improve the plan.
4. Planner receives `## Research Files` only when research files actually exist.
5. Planner is agreement-first:
   - If a requirement is materially ambiguous, planner returns `STATUS=NEEDS_CLARIFICATION`.
   - Planner never treats its own defaults as approved implementation.
6. Planner must choose one `plan_mode`:
   - `direct` for trivial low-risk work
   - `execution_plan` for standard implementation work
   - `decision_rfc` for architecture or multi-option work
7. Planner must choose one `verification_rigor`:
   - `standard` by default (covers most work; keeps verification proportional to risk)
   - `critical_path` for security, money, state-machine, concurrency, or irreversible-migration work (failure in these domains is irreversible or high-blast-radius; justifies extended scenario coverage)
   - After the planner (scout) returns its `PLAN_MODE`/`VERIFICATION_RIGOR`, evaluate the qualify
     condition: `PLAN_MODE == 'decision_rfc' OR VERIFICATION_RIGOR == 'critical_path'`. This is
     the ONLY qualify signal — never a separate keyword/heuristic scan of the request text. See
     `### PLAN qualify branch` below for the branch logic this drives.
8. PLAN fresh-review loop:
   - Every PLAN workflow creates `plan-create` (Block A) immediately. The review-DAG tail
     (`plan-review-gap-1 -> re-plan -> plan-review-gap-2 -> memory-finalize`, Block B) is
     constructed exactly once, at exactly one of the three call sites documented in
     `### PLAN task graph` below — never pre-created atomically with `plan-create`. See
     `### PLAN task graph` for the authoritative construction and timing rules.
   - Every saved plan artifact enters that DAG, including `direct`, `execution_plan`, and `decision_rfc`.
   - If pass 1 succeeds, the router prunes the unused `re-plan` and pass 2 branch explicitly.
   - If pass 1 finds blocking issues, the router keeps the pre-created `re-plan` and pass 2 branch alive.
   - Maximum fresh-review passes: 2.
   - Cap: `policy.plan_review_passes_max` (= 2).
   - Planner remains the only plan writer.
   - The existing inline `plan-review-gate` inside planner remains the final fail-closed boundary on each planner pass.

### PLAN task graph

**Block A** — only the first `TaskCreate()` (`plan-create`, the scout). Unchanged in content and
position. No other task is created here:

```text
TaskCreate({
  subject: "CRAFTFLOW planner: Create plan for {feature}",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:plan-create\nplan:N/A\nscope:N/A\nreason:Create implementation plan\n\nChoose the correct plan mode (`direct`, `execution_plan`, or `decision_rfc`) and verification rigor (`standard` or `critical_path`). Create the corresponding planning artifact.",
  activeForm: "Creating plan"
}) -> planner_task_id
```

**Block B** — a new, reusable "PLAN review-DAG tail" construction containing the remaining 4
`TaskCreate()` calls (`plan-review-gap-1`, `re-plan`, `plan-review-gap-2`, `memory-finalize`),
byte-identical in shape to today's chain, taking one parameter: `upstream_task_id`.
`plan-review-gap-1`'s `addBlockedBy` binds to `[upstream_task_id]` instead of the literal
`planner_task_id`. **Block B is never invoked at pre-creation time.** It is invoked exactly once,
at exactly one of three call sites documented in this plan (never zero, never more than one per
workflow run):
1. The non-qualify branch (`### PLAN qualify branch` below), `upstream_task_id = planner_task_id`.
2. The post-judge step (`### PLAN post-judge validation` in `references/plan-bakeoff.md`), `upstream_task_id = judge_task_id`.
3. The all-candidates-failed fallback (`### PLAN bake-off fan-out` in `references/plan-bakeoff.md`), `upstream_task_id` =
   the fresh fallback planner dispatch's task id.

Each invocation also persists `task_ids.plan_bakeoff_judge`/the relevant candidate task id (see
the artifact schema in `workflow-artifact-and-hook-policy.md`) so `upstream_task_id` is durably
recoverable, not just an in-session variable.

```text
TaskCreate({
  subject: "CRAFTFLOW plan-gap-reviewer: Fresh review pass 1",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:plan-review-gap-1\nplan:N/A\nscope:N/A\nreason:Fresh anti-anchoring review of saved plan (pass 1)\n\nWait for the planner to save a plan artifact, then review it against the original user request and any approved design/research files.",
  activeForm: "Fresh-reviewing plan"
}) -> planning_review_pass1_task_id
TaskUpdate({ taskId: planning_review_pass1_task_id, addBlockedBy: [upstream_task_id] })

TaskCreate({
  subject: "CRAFTFLOW planner: Revise plan after fresh review",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:re-plan\nplan:N/A\nscope:N/A\nreason:Revise plan if fresh review finds blocking issues\n\nOnly run if pass 1 finds blocking issues. Revise the existing saved plan using structured planning review findings.",
  activeForm: "Revising plan"
}) -> planner_replan_task_id
TaskUpdate({ taskId: planner_replan_task_id, addBlockedBy: [planning_review_pass1_task_id] })

TaskCreate({
  subject: "CRAFTFLOW plan-gap-reviewer: Fresh review pass 2",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:plan-review-gap-2\nplan:N/A\nscope:N/A\nreason:Fresh anti-anchoring review of saved plan (pass 2)\n\nOnly run if the re-plan task produces a revised saved plan after pass 1 findings.",
  activeForm: "Fresh-reviewing revised plan"
}) -> planning_review_pass2_task_id
TaskUpdate({ taskId: planning_review_pass2_task_id, addBlockedBy: [planner_replan_task_id] })

**Feature Backlog (advisory):** before creating the Memory Update task below, when this round returned `STATUS: PLAN_CREATED`/`DECISION_RFC_CREATED`, run the register + VCR calls per `references/harness-self-checks.md § Feature Backlog`.

TaskCreate({
  subject: "CRAFTFLOW Memory Update: Index plan in memory",
  description: "wf:{workflow_uuid}\nkind:memory\norigin:router\nphase:memory-finalize\nplan:N/A\nscope:N/A\nreason:Persist captured Memory Notes. ROUTER ONLY: execute inline. Follow references/memory-finalize.md § Memory Update task instructions (read it now if not in context). Never spawn Agent() for this task.",
  activeForm: "Indexing plan in memory"
}) -> memory_task_id
TaskUpdate({ taskId: memory_task_id, addBlockedBy: [upstream_task_id, planning_review_pass1_task_id, planner_replan_task_id, planning_review_pass2_task_id] })
```

### PLAN dispatch-time prompt assembly

Documents the derivation formula the router applies at the moment it actually invokes
`Task()`/`Agent()` for a runnable phase (§ 12 Chain Execution Loop step 4 / § 7 Prompt scaffold) —
never at `TaskCreate()` time. Do NOT embed these fields in any `TaskCreate()` `description`
string; assemble them fresh from the persisted workflow-artifact fields at actual dispatch time,
matching how `## Design File`/`## Research Files` are already assembled today:

```text
phase == "plan-create":
  assemble ## Target Plan File: docs/plans/{plan_file_stem}-candidate-inherit.md
  (always — every plan-create dispatch, qualifying or not, per Durable Decision 1)

phase matches "plan-bakeoff-candidate-{model}":
  assemble ## Target Plan File: docs/plans/{plan_file_stem}-candidate-{model}.md
  ({model} is read directly from the task's own phase: metadata suffix — no separate
  per-task filename lookup needed; plan_file_stem is read from the workflow artifact)

phase == "plan-bakeoff-judge":
  assemble ## Target Plan File: docs/plans/{plan_file_stem}-plan.md
  assemble ## Candidates from results.bakeoff[] (already persisted per candidate return, per
  `SKILL.md § 12` step 5 and 5a, `references/post-agent-validation.md` "structured agent results"): one line per valid candidate —
  "- {model}: {plan_file} (CONFIDENCE={confidence}, PLAN_MODE={plan_mode},
  RISKS_IDENTIFIED={risks_identified}, ALTERNATIVES={alternatives}, DRAWBACKS={drawbacks})"
```

Both `## Target Plan File` and `## Candidates` are built at the same dispatch-prompt-assembly
point as `plan-create`'s `## Target Plan File` above — never embedded in any `TaskCreate()`
`description` string. See `### PLAN bake-off fan-out` and `### PLAN judge dispatch` in `references/plan-bakeoff.md` for the
task-creation blocks these dispatch-time rules pair with.

### PLAN qualify branch

Placed immediately after the scout's contract (Block A's `plan-create` dispatch) is validated
(existing contract validation unchanged) and BEFORE any further DAG advancement:

```text
IF NOT (PLAN_MODE == 'decision_rfc' OR VERIFICATION_RIGOR == 'critical_path'):
  # Non-qualify: rename, zero added cost
  Bash(f"mv 'docs/plans/{plan_file_stem}-candidate-inherit.md' 'docs/plans/{plan_file_stem}-plan.md'")
  Verify via Glob(f"docs/plans/{plan_file_stem}-plan.md") — must match exactly once, and the
    candidate-suffixed path must no longer match.
  Persist plan_file = f"docs/plans/{plan_file_stem}-plan.md"; bakeoff_triggered = false
  # Construct Block B NOW — this is the FIRST point plan-review-gap-1/re-plan/
  # plan-review-gap-2/memory-finalize are created; upstream_task_id = planner_task_id.
  <invoke Block B, upstream_task_id = planner_task_id>
  # Continue into the EXISTING, unmodified plan-review-gap-1 flow.
ELSE:
  Persist bakeoff_triggered = true, bakeoff_n = min(bakeoff_n_requested, 4),
    bakeoff_models = the first (bakeoff_n - 1) entries of [opus, sonnet, haiku, fable] (fixed
    rotation slice — no pre-dispatch availability filtering occurs here; an unavailable model
    surfaces at actual dispatch time via the same tool-level-failure path as any other candidate
    dispatch error — see the merged Edge-case 4 in the design's Critical-Path Verification Design
    and `### PLAN bake-off fan-out` in `references/plan-bakeoff.md`).
  # Proceed to `### PLAN bake-off fan-out` (`references/plan-bakeoff.md`). Block B is constructed later, in
  # `### PLAN post-judge validation` (judge succeeds) or `### PLAN bake-off fan-out`'s
  # all-candidates-failed branch — never here.
```

**Bake-off body** (the Block A/Block B rationale, `### PLAN bake-off fan-out`, `### PLAN judge dispatch`, `### PLAN post-judge validation` and the Task*-tool-fallback note): read `references/plan-bakeoff.md` only when `PLAN_MODE == 'decision_rfc' OR VERIFICATION_RIGOR == 'critical_path'`; a plan that does not qualify never reads it.


### Intent Interview Gate (PLAN only)

Before brainstorming, check whether the task description is under-specified:

**Dispatch `Skill(skill="craftflow:intent-interview")` when ALL of:**
- `AUTO_PROCEED` is NOT `true` in `activeContext.md ## Session Settings`
- No saved plan file exists for this request in `activeContext.md ## References`
- At least ONE of:
  - Request is ≤3 sentences with no success definition
  - Request contains scope-ambiguous terms: "improve", "refactor", "clean up", "make it better", "fix things"
  - Request touches ≥2 systems or modules with no stated boundary

**Skip the interview (run brainstorming directly) when ANY of:**
- `AUTO_PROCEED: true` (JUST_GO mode — auto-skip)
- A saved plan file is already referenced
- Request explicitly names the file, function, or behavior to change

After `Skill(skill="craftflow:intent-interview")` completes, read the `## Intent Contract (From Interview)` block it emits. Persist it into:
- Workflow artifact `intent` field
- Planner prompt under `## Intent Contract (Pre-Interview)` (planner treats it as user-approved input)


### Inline brainstorming handoff

After `Skill(skill="craftflow:brainstorming")`, parse the fenced YAML block under
`### Brainstorming Handoff (MACHINE-READABLE)`.

Required field:
- `DESIGN_FILE`

If present:
- persist it into workflow artifact `design_file`
- pass it to planner as `## Design File`
- do not require `activeContext.md` to be updated first


