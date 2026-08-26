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
2. The post-judge step (`### PLAN post-judge validation` below), `upstream_task_id = judge_task_id`.
3. The all-candidates-failed fallback (`### PLAN bake-off fan-out` below), `upstream_task_id` =
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

TaskCreate({
  subject: "CRAFTFLOW Memory Update: Index plan in memory",
  description: "wf:{workflow_uuid}\nkind:memory\norigin:router\nphase:memory-finalize\nplan:N/A\nscope:N/A\nreason:Persist captured Memory Notes\n\nROUTER ONLY: execute inline. Read the workflow artifact and THIS task description payload, persist to .craftflow/state/*.md,\nBefore persisting each MEMORY_NOTES field, resolve its destination file and section from SKILL.md Section 13's routing table, then obtain the FULL destination file content via:\n  python3 {plugin_root}/scripts/craftflow_state_query.py <destination_file_path> --mode full\n(never a raw Read -- the destination files are exactly the .craftflow/state/**\nfiles the state-read-compaction guard may deny once oversized; --mode full is\nthis script's byte-identical full-content path) and pipe that output into:\n  python3 {plugin_root}/scripts/craftflow_memory_merge.py\nwith a JSON payload of {\"file_text\": \"<full destination file content>\", \"section\": \"<target section, e.g. Common Gotchas>\", \"notes\": [...], \"retractions\": [], \"max_bullets\": <cap per routing table, e.g. 60 for patterns -> project/patterns.md ## Common Gotchas; omit for learnings -> workflows/{workflow_uuid}/activeContext.md ## Learnings and verification -> workflows/{workflow_uuid}/progress.md ## Verification, which are workflow-scoped and need no cap>, \"unit\": <omit for \"bullets\" (default, existing behavior, unchanged) | \"entries\" for paragraph-shaped project-tier sections whose entries are NOT \"- \" bullet lines -- project/activeContext.md ## Last Updated, project/progress.md ## Last Updated, project/patterns.md ## Last Updated (each entry is a dated free-text paragraph, prepended newest-first, blank-line-separated); entries[0] is the newest, so max_bullets caps the N most-recent entries and oldest-first eviction trims from the END of the list, opposite of bullets-mode ordering>}\non stdin; use the FULL stdout as the replacement file content -- section-anchored mode returns the whole file with only the target section's body replaced, not just a section body.\nOmit max_bullets entirely (do not pass it) if the destination file's memory contract sections are known to still be structurally corrupted; do not silently evict existing content when a section's heading structure is broken (see Phase 3 of this plan for the corrupted-file repair).\nWhen max_bullets is set (patterns.md -> project/patterns.md ## Common Gotchas), also pass \"archive\": {\"dir_rel\": \".craftflow/state/project/archive\", \"section_slug\": \"<kebab-case section name>\", \"month\": \"<current UTC YYYY-MM>\"}. The script's stdout becomes a JSON envelope {\"file_text\": ..., \"archived_bullets\": [...], \"archive_path\": ...} instead of plain text -- when archived_bullets is non-empty, write archive_path FIRST (create .craftflow/state/project/archive/ if missing, append if the monthly archive file already exists), verify the write succeeded, and only THEN write file_text back to the destination file (mirrors craftflow_memory_repair.py's backup-before-any-destructive-write ordering -- never write the live (trimmed) file before the archive file exists).\nConfidence <0.7 notes are dropped. Retractions remove matching bullets. New bullets get a (conf: x) suffix. In \"entries\" unit mode, notes are prepended as new raw-text entries (no bullet/confidence formatting, no dedupe/supersede against existing entries) and retractions are not supported (ignored with a warning if present).\nthen remove the matching [craftflow-internal] memory_task_id line from activeContext.md ## References. Never spawn Agent() for this task.",
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
  `SKILL.md § 12` step 5/5a "structured agent results"): one line per valid candidate —
  "- {model}: {plan_file} (CONFIDENCE={confidence}, PLAN_MODE={plan_mode},
  RISKS_IDENTIFIED={risks_identified}, ALTERNATIVES={alternatives}, DRAWBACKS={drawbacks})"
```

Both `## Target Plan File` and `## Candidates` are built at the same dispatch-prompt-assembly
point as `plan-create`'s `## Target Plan File` above — never embedded in any `TaskCreate()`
`description` string. See `### PLAN bake-off fan-out` and `### PLAN judge dispatch` below for the
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
    and `### PLAN bake-off fan-out` below).
  # Proceed to `### PLAN bake-off fan-out` below. Block B is constructed later, in
  # `### PLAN post-judge validation` (judge succeeds) or `### PLAN bake-off fan-out`'s
  # all-candidates-failed branch — never here.
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

### PLAN bake-off fan-out

Runs immediately after `### PLAN qualify branch`'s `ELSE` arm persists `bakeoff_triggered`,
`bakeoff_n`, and `bakeoff_models`.

**N-1 parallel candidate dispatch:**
```text
FOR each model in bakeoff_models (the fixed rotation slice from `### PLAN qualify branch` — no
    separate availability filtering occurs before this loop; an unavailable model is handled
    identically to any other dispatch-time tool-level failure, see below):
  TaskCreate({
    subject: f"CRAFTFLOW planner: Bake-off candidate ({model})",
    description: f"wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:plan-bakeoff-candidate-{model}\nplan:N/A\nscope:N/A\nreason:Parallel bake-off candidate ({model}) — Target Plan File assembled at dispatch time, see ### PLAN dispatch-time prompt assembly, never embedded here\n\nChoose the correct plan mode and verification rigor exactly as the scout did. Create an independent planning artifact — do NOT read the scout's or any other candidate's output.",
    activeForm: f"Creating bake-off candidate ({model})"
  }) -> candidate_task_id[model]
  Persist task_ids.plan_bakeoff_candidates[model] = candidate_task_id[model].
  # No addBlockedBy — all N-1 are parallel, no ordering among themselves.

Mark ALL candidate tasks in_progress FIRST, then dispatch every one of them (via Mechanism A,
passing the literal `model` override) in the SAME message — identical mechanics to the existing
reviewer+hunter / research-web+research-github precedent (`SKILL.md § 12` step 5/5a).

If the parallel invocation itself fails or is unavailable (API error, rate limit): fall back to
sequential dispatch, one candidate at a time. Log event=parallel_fallback. Never block the
workflow over parallelism unavailability (identical wording to the existing precedent). A model
that is simply unavailable in this environment (e.g. an unrecognized `model` value) surfaces as
this exact same category of tool-level dispatch failure — retried once sequentially, then handled
by the degraded-bake-off tolerance rule below.
```

**Per-candidate validation and degraded-bake-off tolerance** (a scoped exception to the default
hard-stop rule):
```text
For EACH candidate that returns (scout included), validate its contract exactly as any planner
contract is validated today (`SKILL.md § 8` override row, unchanged). Additionally verify
PLAN_FILE Glob-matches its OWN assigned target path exactly (not any other candidate's path, not
the canonical path).

EXCEPTION to the default "malformed contract = hard stop" rule (`SKILL.md § 8`), scoped ONLY to
phase:plan-bakeoff-candidate-* tasks: an invalid contract, a tool-level dispatch failure that
survives the sequential retry (this is also where an unavailable-model dispatch failure lands —
above), or a PLAN_FILE that doesn't match the assigned target does NOT hard-stop the workflow.
Mark that candidate `bakeoff_candidate_failed`, persist to `bakeoff_candidate_failures` with the
reason, exclude it from `results.bakeoff[]`, and continue.
```

**ROUTER-OWNED CLEANUP.** The instant a candidate is marked `bakeoff_candidate_failed`, the router
(never the judge, never the failed candidate's own agent) reconciles that candidate's OWN assigned
target file. A failed candidate may still have successfully written a file before its contract
came back malformed or missing. The judge is only ever given, and is prompt-restricted to delete
only, `results.bakeoff[]`'s paths (`### PLAN dispatch-time prompt assembly`) — which excludes
failed candidates by construction — so without this step a failed candidate's leftover file would
never be deleted by anyone, and `### PLAN post-judge validation`'s Glob-zero-matches check would
then incorrectly hard-stop an otherwise-successful degraded bake-off (the design's own Error
Handling explicitly requires proceeding with ≥1 valid candidate — a degraded bake-off "is still
strictly no worse than today's single-planner path"). Immediately after marking the candidate
failed:
```text
Glob(f"docs/plans/{plan_file_stem}-candidate-{model}.md")  # this candidate's OWN assigned path
  only — the exact same string already used for its dispatch target above, never re-derived
  differently
If it matches: Bash(f"rm 'docs/plans/{plan_file_stem}-candidate-{model}.md'")  # exact literal
  path, never a wildcard/glob-delete — mirrors the judge's own "restricted to only the candidate
  paths it was explicitly given" posture (`### PLAN judge dispatch` below)
```

Once every dispatched candidate has either returned validly or been marked failed (and, for any
failed candidate, its own leftover file already reconciled per the cleanup above):
```text
  valid_count = len(results.bakeoff)  # includes the scout if it's still valid
  IF valid_count >= 1: proceed to `### PLAN judge dispatch` below with only the valid candidates.
  IF valid_count == 0: abandon the bake-off. Dispatch ONE fresh planner task, model: inherit,
    NO Target Plan File override this time (self-derives the canonical path directly — this is
    the one case where plan-create is genuinely re-run from scratch). Treat its output as the
    final plan_file exactly as the non-qualify branch does. bakeoff_triggered stays true (for
    telemetry honesty — a bake-off WAS attempted) but bakeoff_all_failed = true is also persisted.
    Once this fresh planner task returns validly: construct Block B (`### PLAN task graph`) NOW,
    with upstream_task_id = this fresh planner task's own task id — this is the THIRD and last of
    the three Block B call sites (alongside non-qualify and post-judge success below).
```

### PLAN judge dispatch

```text
TaskCreate({
  subject: "CRAFTFLOW plan-bakeoff-judge: Synthesize final plan",
  description: f"wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:plan-bakeoff-judge\nplan:N/A\nscope:N/A\nreason:Synthesize {valid_count} bake-off candidates into one final plan — Target Plan File and Candidates assembled at dispatch time, see ### PLAN dispatch-time prompt assembly, never embedded here\n\nRead every candidate file. Compare against the original user request and design file. Produce exactly one synthesized plan at the Target Plan File path, then delete every candidate file.",
  activeForm: "Synthesizing bake-off candidates"
}) -> judge_task_id
Persist task_ids.plan_bakeoff_judge = judge_task_id.
TaskUpdate({ taskId: judge_task_id, addBlockedBy: [every valid candidate's task_id] })
```

### PLAN post-judge validation

```text
Validate judge's contract per the `SKILL.md § 8` override row (added for `plan-bakeoff-judge`).
Glob(f"docs/plans/{plan_file_stem}-candidate-*.md") — MUST return zero matches. (This check now
  holds unconditionally: every failed candidate's own leftover file was already deleted at
  fail-time by `### PLAN bake-off fan-out`'s router-owned cleanup above, so this Glob only needs to
  catch a judge-side deletion mistake among the valid candidates it was actually given — not a
  failed candidate's leftover, which can no longer exist by this point.) If any match survives,
  treat the judge's STATUS as invalid regardless of its own claim (same posture as the existing
  "APPROVE + critical issues becomes CHANGES_REQUESTED" override pattern) — hard stop, re-run
  inline verification.
On success: persist plan_file = judge's PLAN_FILE (== the canonical path).
# Construct Block B NOW (`### PLAN task graph`) — this is the FIRST point plan-review-gap-1/
# re-plan/plan-review-gap-2/memory-finalize are created on the qualify path;
# upstream_task_id = judge_task_id.
<invoke Block B, upstream_task_id = judge_task_id>
# Continue into the EXISTING, unmodified plan-review-gap-1 flow — identical downstream handling
# to the non-qualify branch from this point forward.
```

**Task*-tool-fallback note (cross-reference, not new logic):** every individual
`TaskCreate()`/`TaskUpdate({ addBlockedBy })` call site in `### PLAN bake-off fan-out`,
`### PLAN judge dispatch`, and Block B (`### PLAN task graph`) above is covered by the existing
generalized Task*-tool-fallback rule in `SKILL.md § 6` without further modification — when
`capabilities.task_tools_available == false`, skip each call and append the corresponding
`phase_status`/`normalized_phases` entry (`plan-bakeoff-candidate-{model}` / `plan-bakeoff-judge`
/ the Block B tail phases) directly. **What the existing rule does NOT, by itself, cover — and
what the Block A/Block B split above supplies instead — is WHEN Block B's entries get appended**:
the fallback rule only says a skipped `TaskCreate()` becomes a `phase_status` append at the point
the (skipped) call would have happened; it says nothing about deferring that point to after a
branch resolves. That deferral is this mechanism's own structural change (`### PLAN task graph`
Block B), layered on top of the pre-existing, unmodified fallback mechanism, not something the
fallback rule already provided. Dispatch itself falls through to the `Agent()`-tool path per
`SKILL.md § 7`'s existing Task*-tool-fallback rule, substituting the same `'n/a — task-tool
fallback active (capabilities.task_tools_available=false)'` `Task ID:` placeholder every other
agent already uses.
