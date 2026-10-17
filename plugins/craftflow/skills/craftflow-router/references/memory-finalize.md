# Memory finalization

## 13. Memory Finalization

The memory task executes inline only. Never spawn it as a sub-agent.
For PLAN and BUILD memory-finalize specifically, also run the feature-backlog register/activate/complete/VCR calls per `references/harness-self-checks.md § Feature Backlog` — the detail lives there, not here.

Memory is written to two tiers. Route each `MEMORY_NOTES` field as follows:

| MEMORY_NOTES field | Write destination | Rationale |
|--------------------|-------------------|-----------|
| `learnings` | `$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}/activeContext.md ## Learnings` | Workflow-specific causal insights |
| `patterns` | `$PROJECT_ROOT/.craftflow/state/project/patterns.md ## Common Gotchas` | Durable conventions that apply to all future workflows |
| `verification` | `$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}/progress.md ## Verification` | Proof evidence scoped to this build/debug/review run |
| `deferred` | `$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}/activeContext.md` as `[Deferred]: ...` | Non-blocking follow-ups scoped to this workflow |

Cross-workflow promotion rule: If a `learnings` item is a project-wide constraint
(not specific to the current task), also copy it to
`$PROJECT_ROOT/.craftflow/state/project/activeContext.md ## Learnings`.
Use judgment: workflow-local observations stay in
`$PROJECT_ROOT/.craftflow/state/workflows/{wf}/`; durable project truths belong in
`$PROJECT_ROOT/.craftflow/state/project/`.

Skill-candidate observe step (required before any markdown persistence below):
- Before writing any `.md` memory file, run the deterministic ledger observe call for
  every workflow (BUILD, DEBUG, REVIEW, and PLAN alike — this is cheap, deterministic
  Python and runs unconditionally, independent of whether `skill-distill` itself was
  gated in for this workflow):
  ```bash
  python3 {plugin_root}/scripts/craftflow_skill_ledger.py --observe {workflow_uuid} --state-dir .craftflow/state
  ```
- This mines this workflow's own artifact/event log into
  `.craftflow/state/project/skill-candidates.json`, feeding future `skill-distill` gate
  checks on later workflows even when this workflow's own candidate count never crossed
  the recurrence threshold.
- A non-zero exit from this call is non-blocking for the workflow itself (log a
  `skill_candidates_observed` event with the error and continue to memory persistence) —
  the ledger is best-effort telemetry, never a gate on workflow completion.
- **Explicit resolution:** this `--observe` call runs unconditionally even when the
  `SKILL_DISTILL: skip` Session Setting disables the `skill-distill` phase (gate check +
  `skill-author` dispatch, per `SKILL.md → ## 14. Hard Rules`) — that toggle never disables this observe
  step, which stays cheap, deterministic, and preserves future calibration value if the
  toggle is later removed.

Skill-candidate prune step (required before any markdown persistence below, runs immediately
after the observe step above, same unconditional cadence — every workflow, BUILD, DEBUG, REVIEW,
and PLAN alike, independent of whether `skill-distill` itself was gated in):
- Immediately after the observe call above, run the ledger's anti-rot pass:
  ```bash
  python3 {plugin_root}/scripts/craftflow_skill_ledger.py --prune --state-dir .craftflow/state --project-root .
  ```
- This purges stale `candidate` entries (>90 days, no new evidence) outright, leaves `rejected`
  entries untouched (permanent tombstone), and NEVER deletes or changes `status` for `promoted`
  entries. Instead it checks each `promoted` entry for rot: whether its canonical
  `.claude/skills/{name}/SKILL.md` still exists, whether every path listed in that file's
  `craftflow-referenced-paths` frontmatter still exists on disk, and (if present) whether its
  `craftflow-review-after` date has not yet elapsed, and whether the entry's `promoted_skill`
  name itself is present. Any rot found sets `needs_review: true` / `needs_review_reason`
  (`missing_promoted_skill_name`, `stale_path`, `review_after_elapsed`, or
  `unparseable_review_after`) on the ledger entry in place — never a status transition, never a
  delete.
- A non-zero exit from this call is non-blocking for the workflow itself (log a
  `skill_candidates_pruned` event with the error and continue to memory persistence) — same
  best-effort-telemetry posture as the observe step; never a gate on workflow completion.
- **Explicit resolution:** this `--prune` call runs unconditionally even when the
  `SKILL_DISTILL: skip` Session Setting disables the `skill-distill` phase, for the same reason
  the observe step is exempt — anti-rot maintenance of the ledger is independent of whether this
  workflow itself dispatches `skill-author`.

Memory finalization permit (required before any `.md` memory write):
- The `craftflow_pretooluse_guard.py` hook blocks direct writes to protected memory files.
- Before writing the first memory file, create the permit token:
  ```
  Bash("printf '%s' '{workflow_uuid}' > \"$PROJECT_ROOT/.craftflow/state/.memory-finalize\"")
  ```
- After all memory files are written, clear the permit:
  ```
  Bash("rm -f \"$PROJECT_ROOT/.craftflow/state/.memory-finalize\"")
  ```
- Permit create and clear must each be their own **standalone Bash** call. Folding the `printf ... > .memory-finalize` into a compound command (`&&`, `;`, a second line) is denied by the bash guard (`bash-write-protected-path`).
- **Batch alternative (preferred for 2+ `--apply` calls):** write one JSON file `{"project_root","workflow_uuid","applies":[{"target","payload"}...]}` (payloads = the usual `--apply` payloads) and run `python3 "$CRAFTFLOW_INSTALL/scripts/craftflow_memory_merge.py" --finalize <batch.json>`. The script creates the permit, applies every item, and clears the permit in a `finally` block, so no separate permit Bash calls are needed. Exit 1 means at least one item failed (others still applied); the permit is already cleared.
- If workflow_uuid is unavailable (fallback path), omit the permit steps — the guard will audit-log but not block in that case.

The memory task also:
- Replaces `$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}/progress.md ## Tasks` with the active workflow snapshot.
- Keeps only the most recent 10 items in `$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}/progress.md ## Completed`.
- Updates `$PROJECT_ROOT/.craftflow/state/project/progress.md ## Completed` with a one-line summary of the finished workflow, via `craftflow_memory_merge.py` bullets-mode (section: `Completed`, `max_bullets: 20`, `archive: {"dir_rel": ".craftflow/state/project/archive", "section_slug": "completed", "month": "<current UTC YYYY-MM>"}`) — the same cap+archive mechanism already used for `patterns.md ## Common Gotchas`, reusing the existing `.craftflow/state/project/archive/completed-2026-08.md` archive file/slug established by a prior one-time rotation. Never omit `max_bullets`/`archive` for this destination; omitting it is exactly the gap that let this section regrow unbounded once already (a prior one-time manual rotation on 2026-08-19 archived it once, but no ongoing per-workflow cap was ever wired in afterward, so it silently regrew from ~25KB back to ~77KB by 2026-08-25 — six days, roughly 15 workflows).
- Caps project-tier `## Last Updated` in ALL THREE project memory files the same way, using `craftflow_memory_merge.py`'s `"unit": "entries"` mode (paragraph-shaped, not bullet-shaped — see the memory-finalize instruction sites for the full field contract): `max_bullets: 20` each, archived to `.craftflow/state/project/archive/` with per-file slugs `activecontext-last-updated`, `progress-last-updated`, `patterns-last-updated` respectively (distinct slugs required — all three files share the identical `## Last Updated` heading, so a shared slug would collide in the archive directory).
- Removes the matching `[craftflow-internal]` or legacy `[cc10x-internal]` `memory_task_id` line from `$PROJECT_ROOT/.craftflow/state/project/activeContext.md ## References`.
- If any artifact or memory write fails, stop immediately (clear the permit first). Never advance the workflow after a failed persistence write.
- **Known deferred gap (do not silently "fix" without a dedicated PLAN):** `activeContext.md ## Current Focus`, `activeContext.md ## Recent Changes`, and `progress.md ## Current Workflow` (project-tier) exhibit the same unbounded-append anti-pattern as `## Last Updated`/`## Completed` above and are NOT yet capped — `## Current Focus` in particular is typically the single largest contributor to `activeContext.md`'s size, well past `## Last Updated`. Fixing these requires a product decision (replace-in-place per their own single-value template contract vs. cap+archive like a history log), not just wiring the existing mechanism, and is out of scope here. `progress.md ## Current Workflow` already has a precedent archive file from one prior one-time manual rotation (`.craftflow/state/project/archive/current-workflow-history-2026-08.md`, slug `current-workflow-history`) that a future fix should resume, not replace.

Fallback: If `workflow_uuid` is unavailable, write to root-flat files
(`$PROJECT_ROOT/.craftflow/state/activeContext.md`, `$PROJECT_ROOT/.craftflow/state/patterns.md`,
`$PROJECT_ROOT/.craftflow/state/progress.md`) as in prior versions.

For PLAN:
- Ensure `- Plan: {plan_file}` remains correct in `$PROJECT_ROOT/.craftflow/state/project/activeContext.md ## References`.
- Ensure `- Design: {design_file}` remains correct in `$PROJECT_ROOT/.craftflow/state/project/activeContext.md ## References` when a design exists.
- If a plan exists, record `Plan saved: {plan_file}` in `$PROJECT_ROOT/.craftflow/state/project/activeContext.md ## Recent Changes`.
- If a plan exists, set `$PROJECT_ROOT/.craftflow/state/project/activeContext.md ## Next Steps` to `1. Execute plan: {plan_file}` unless the workflow ended in clarification-needed state.

For DEBUG:
- Preserve the latest `[DEBUG-RESET: wf:{workflow_uuid}]` section in `$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}/activeContext.md ## Recent Changes` and summarize the final result beneath it.

## Memory-phase timing computation

- At memory-finalize, for each agent that ran in this workflow, compute `completed_ts - started_ts` in seconds and write to `telemetry.agent_wall_clock_seconds.{agent}`.
- Compute `workflow_wall_clock_seconds` as the delta between the `workflow_started` event `ts` and the `memory_finalized` event `ts`.
- ISO timestamps are always UTC. Use `new Date().toISOString()` or equivalent. If the runtime does not expose a clock, omit the field rather than writing 0.

## Memory Update task

### Memory Update task instructions

Single copy of the Memory Update `TaskCreate` description body, shared by the five Memory Update task sites (BUILD standard, BUILD fast path, PLAN, DEBUG, REVIEW). A clause that differs per site, such as the `plan:` value, stays at its site as a parameter:

  description: "wf:{workflow_uuid}\nkind:memory\norigin:router\nphase:memory-finalize\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:Persist captured Memory Notes\n\nROUTER ONLY: execute inline. Read the workflow artifact and THIS task description payload, persist to .craftflow/state/*.md.\nBefore persisting each MEMORY_NOTES field, resolve its destination file and section from this file's Memory Finalization routing table, then write it with the apply mode (PREFERRED; REQUIRED whenever the payload carries \"archive\"):\n  python3 {plugin_root}/scripts/craftflow_memory_merge.py --apply <destination_file_path> < <payload_file>\nwith the JSON payload below minus \"file_text\" as <payload_file>, a temp file inside the repo/worktree that is gitignored (e.g. under docs/plans/) and that you delete afterwards -- the primary route, fed via < file; do NOT use a heredoc for this call, because the safe-shell guard only allows a strict python heredoc shape and a heredoc feeding --apply is refused (a file under the session scratchpad is an optional alternative only when the session scratchpad is available; the grant may fail closed in worktree/subdirectory sessions, so never rely on it); --apply reads the destination itself, writes any archived entries to the archive file first (fsync), then atomically replaces the destination, requires the .memory-finalize permit, exits 0 on success, and exits 1 leaving the destination untouched if the permit is missing or the merged text starts with { or has no ## heading. Do not write --apply's stdout anywhere.\nLegacy stdin/stdout mode (only when the payload has NO \"archive\" field, e.g. the no-workflow_uuid fallback where no permit exists): obtain the FULL destination file content via:\n  python3 {plugin_root}/scripts/craftflow_state_query.py <destination_file_path> --mode full\n(never a raw Read -- the destination files are exactly the .craftflow/state/**\nfiles the state-read-compaction guard may deny once oversized; --mode full is\nthis script's byte-identical full-content path) and pipe that output into:\n  python3 {plugin_root}/scripts/craftflow_memory_merge.py\nwith a JSON payload of {"file_text": "<full destination file content>", "section": "<target section, e.g. Common Gotchas>", "notes": [...], "retractions": [], "max_bullets": <cap per routing table, e.g. 60 for patterns -> project/patterns.md ## Common Gotchas; omit for learnings -> workflows/{workflow_uuid}/activeContext.md ## Learnings and verification -> workflows/{workflow_uuid}/progress.md ## Verification, which are workflow-scoped and need no cap>, "unit": <omit for "bullets" (default, existing behavior, unchanged) | "entries" for paragraph-shaped project-tier sections whose entries are NOT "- " bullet lines -- project/activeContext.md ## Last Updated, project/progress.md ## Last Updated, project/patterns.md ## Last Updated (each entry is a dated free-text paragraph, prepended newest-first, blank-line-separated); entries[0] is the newest, so max_bullets caps the N most-recent entries and oldest-first eviction trims from the END of the list, opposite of bullets-mode ordering>}\non stdin; use the FULL stdout as the replacement file content ONLY in this no-archive legacy mode -- section-anchored mode returns the whole file with only the target section's body replaced, not just a section body.\nOmit max_bullets entirely (do not pass it) if the destination file's memory contract sections are known to still be structurally corrupted; do not silently evict existing content when a section's heading structure is broken (see craftflow_memory_repair.py for the corrupted-file repair).\nWhen max_bullets is set (patterns.md -> project/patterns.md ## Common Gotchas), also pass \"archive\": {\"dir_rel\": \".craftflow/state/project/archive\", \"section_slug\": \"<kebab-case section name>\", \"month\": \"<current UTC YYYY-MM>\"}, and use --apply, which performs the ordering itself. Without --apply, its stdout is a JSON envelope -- never write it raw to any memory file (shape: {\"file_text\": ..., \"archived_bullets\": [...], \"archive_path\": ...}); --apply's contract is: when archived_bullets is non-empty, write archive_path FIRST (create .craftflow/state/project/archive/ if missing, append if the monthly archive file already exists), verify the write succeeded, and only THEN write file_text back to the destination file (mirrors craftflow_memory_repair.py's backup-before-any-destructive-write ordering -- never write the live (trimmed) file before the archive file exists).\nConfidence <0.7 notes are dropped. Retractions remove matching bullets. New bullets get a (conf: x) suffix. In "entries" unit mode, notes are prepended as new raw-text entries (no bullet/confidence formatting, no dedupe/supersede against existing entries) and retractions are not supported (ignored with a warning if present).\nthen remove the matching [craftflow-internal] memory_task_id line from activeContext.md ## References. Never spawn Agent() for this task.",
