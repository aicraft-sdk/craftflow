### PLAN preparation

0. Workflow setup (runs immediately after `workflow_uuid`/`WF_INFO` are minted — this already
   happens in `SKILL.md § 6`'s Parent workflow creation, reused verbatim, no new script — and
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
2. The post-judge step (Phase 4 Step 6), `upstream_task_id = judge_task_id`.
3. The all-candidates-failed fallback (Phase 4 Step 4), `upstream_task_id` = the fresh fallback
   planner dispatch's task id.

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

TaskCreate({
  subject: "CRAFTFLOW Memory Update: Index plan in memory",
  description: "wf:{workflow_uuid}\nkind:memory\norigin:router\nphase:memory-finalize\nplan:N/A\nscope:N/A\nreason:Persist captured Memory Notes\n\nROUTER ONLY: execute inline. Read the workflow artifact and THIS task description payload, persist to .craftflow/state/*.md,\nBefore persisting each MEMORY_NOTES field, resolve its destination file and section from SKILL.md Section 13's routing table, then obtain the FULL destination file content via:\n  python3 {plugin_root}/scripts/craftflow_state_query.py <destination_file_path> --mode full\n(never a raw Read -- the destination files are exactly the .craftflow/state/**\nfiles the state-read-compaction guard may deny once oversized; --mode full is\nthis script's byte-identical full-content path) and pipe that output into:\n  python3 {plugin_root}/scripts/craftflow_memory_merge.py\nwith a JSON payload of {\"file_text\": \"<full destination file content>\", \"section\": \"<target section, e.g. Common Gotchas>\", \"notes\": [...], \"retractions\": [], \"max_bullets\": <cap per routing table, e.g. 60 for patterns -> project/patterns.md ## Common Gotchas; omit for learnings -> workflows/{workflow_uuid}/activeContext.md ## Learnings and verification -> workflows/{workflow_uuid}/progress.md ## Verification, which are workflow-scoped and need no cap>}\non stdin; use the FULL stdout as the replacement file content -- section-anchored mode returns the whole file with only the target section's body replaced, not just a section body.\nOmit max_bullets entirely (do not pass it) if the destination file's memory contract sections are known to still be structurally corrupted; do not silently evict existing content when a section's heading structure is broken (see Phase 3 of this plan for the corrupted-file repair).\nWhen max_bullets is set (patterns.md -> project/patterns.md ## Common Gotchas), also pass \"archive\": {\"dir_rel\": \".craftflow/state/project/archive\", \"section_slug\": \"<kebab-case section name>\", \"month\": \"<current UTC YYYY-MM>\"}. The script's stdout becomes a JSON envelope {\"file_text\": ..., \"archived_bullets\": [...], \"archive_path\": ...} instead of plain text -- when archived_bullets is non-empty, write archive_path FIRST (create .craftflow/state/project/archive/ if missing, append if the monthly archive file already exists), verify the write succeeded, and only THEN write file_text back to the destination file (mirrors craftflow_memory_repair.py's backup-before-any-destructive-write ordering -- never write the live (trimmed) file before the archive file exists).\nConfidence <0.7 notes are dropped. Retractions remove matching bullets. New bullets get a (conf: x) suffix.\nthen remove the matching [craftflow-internal] memory_task_id line from activeContext.md ## References. Never spawn Agent() for this task.",
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
```

(Phase 4 Step 1b extends this same subsection with the `plan-bakeoff-candidate-*` and
`plan-bakeoff-judge` rules, and the `## Candidates` rule for the judge.)

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
    and Phase 4 Step 3/4).
  # Proceed to Phase 4's fan-out logic. Block B is constructed later, in Phase 4 Step 6 (judge
  # succeeds) or Phase 4 Step 4 (all candidates fail) — never here.
```

**Why the Block A/Block B split is a genuine, disclosed behavior change from "pre-create all 5
atomically" (not a cosmetic single-edge deferral):** in the `task_tools_available == false` world,
`SKILL.md § 6`'s fallback rule enforces ordering purely via `phase_status`/`normalized_phases`
insertion order, with `blockedBy`-equivalent constraints fixed at the moment an entry is first
appended — there is no mechanism to redirect a phase's ordering after the fact the way a real
`addBlockedBy` edge can be re-bound later. Pre-creating `plan-review-gap-1`'s entry early (even
without its edge) would let it appear "runnable" before the scout/candidates/judge phases exist.
Deferring Block B's entire construction removes this race outright: the tail phases simply do not
exist in the task graph or in `phase_status`/`normalized_phases` until the exact moment their true
upstream dependency is already known, so they are always created already-correctly-ordered. (This
also removes the smaller, latent equivalent race in the real-`Task()` world, where `§12`'s
`TaskList()`-driven runnable selection has no documented guarantee against observing
`plan-review-gap-1` unblocked in the window between creation and a later `addBlockedBy` call.)
