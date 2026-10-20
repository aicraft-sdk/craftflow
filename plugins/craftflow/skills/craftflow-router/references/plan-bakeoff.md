# PLAN bake-off

**Why the Block A/Block B split is a genuine, disclosed behavior change from "pre-create all 5
atomically" (not a cosmetic single-edge deferral):** in the `task_tools_available == false` world,
`SKILL.md § 6`'s fallback rule (`references/task-tool-fallback.md`) enforces ordering purely via `phase_status`/`normalized_phases`
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
reviewer+hunter / research-web+research-github precedent (`SKILL.md § 12` step 5; `references/remediation-and-research.md` step 5a).

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
contract is validated today (`SKILL.md § 8` override row, now in `references/post-agent-validation.md`, unchanged). Additionally verify
PLAN_FILE Glob-matches its OWN assigned target path exactly (not any other candidate's path, not
the canonical path).

EXCEPTION to the default "malformed contract = hard stop" rule (`SKILL.md § 8`, `references/post-agent-validation.md`), scoped ONLY to
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
Validate judge's contract per the `SKILL.md § 8` override row in `references/post-agent-validation.md` (added for `plan-bakeoff-judge`).
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
generalized Task*-tool-fallback rule in `SKILL.md § 6` (`references/task-tool-fallback.md`) without further modification — when
`capabilities.task_tools_available == false`, skip each call and append the corresponding
`phase_status`/`normalized_phases` entry (`plan-bakeoff-candidate-{model}` / `plan-bakeoff-judge`
/ the Block B tail phases) directly. **What the existing rule does NOT, by itself, cover — and
what the Block A/Block B split above supplies instead — is WHEN Block B's entries get appended**:
the fallback rule only says a skipped `TaskCreate()` becomes a `phase_status` append at the point
the (skipped) call would have happened; it says nothing about deferring that point to after a
branch resolves. That deferral is this mechanism's own structural change (`### PLAN task graph`
Block B), layered on top of the pre-existing, unmodified fallback mechanism, not something the
fallback rule already provided. Dispatch itself falls through to the `Agent()`-tool path per
`SKILL.md § 7`'s existing Task*-tool-fallback rule (`references/task-tool-fallback.md`), substituting the same `'n/a — task-tool
fallback active (capabilities.task_tools_available=false)'` `Task ID:` placeholder every other
agent already uses.

## Chain loop step 5b (PLAN)

```text
5b. If N-1 `plan-bakeoff-candidate-*` tasks are all runnable in the same round (PLAN workflow,
    qualifying bake-off only):
   - Mark all in_progress first
   - Dispatch all of them in the same message (see `references/plan-workflow.md → ### PLAN
     dispatch-time prompt assembly` for the exact per-candidate model/target-file construction
     formula and `### PLAN bake-off fan-out` for the actual per-candidate dispatch loop)
   - If parallel invocation fails (rate limit, API error): fall back to sequential dispatch, one
     candidate at a time. Log `event=parallel_fallback` in the event log.
   - Wait for ALL dispatched candidates to return (validly or failed) before creating the
     `plan-bakeoff-judge` task. A candidate returning an invalid contract does not block the
     others — see `### PLAN bake-off fan-out`'s degraded-bake-off tolerance rule (a scoped
     exception to the Chain Execution Loop's own step 6 hard-stop-on-malformed-output default in `SKILL.md`, applying only
     to `phase:plan-bakeoff-candidate-*`).
```
