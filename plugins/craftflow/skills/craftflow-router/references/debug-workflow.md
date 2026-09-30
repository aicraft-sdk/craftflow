### DEBUG preparation

1. If the user explicitly asks for research or the bug clearly depends on external post-2024 behavior, allow a research round before the first investigator run.
2. Immediately write `[DEBUG-RESET: wf:{workflow_uuid}]` once the workflow id exists.
3. Preserve failed attempt counting semantics: the investigator counts `[DEBUG-N]:` entries after the most recent reset marker.

### DEBUG task graph

```text
TaskCreate({
  subject: "CRAFTFLOW bug-investigator: Investigate {error}",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:debug-investigate\nplan:N/A\nscope:N/A\nreason:Find root cause\n\nFind the root cause and apply the fix.",
  activeForm: "Investigating bug"
}) -> investigator_task_id

TaskCreate({
  subject: "CRAFTFLOW code-reviewer: Review fix",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:debug-review\nplan:N/A\nscope:N/A\nreason:Review the fix\n\nReview the debug fix quality.",
  activeForm: "Reviewing fix"
}) -> reviewer_task_id
TaskUpdate({ taskId: reviewer_task_id, addBlockedBy: [investigator_task_id] })

TaskCreate({
  subject: "CRAFTFLOW integration-verifier: Verify fix",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:debug-verify\nplan:N/A\nscope:N/A\nreason:Verify the fix\n\nVerify the fix works end-to-end.",
  activeForm: "Verifying fix"
}) -> verifier_task_id
TaskUpdate({ taskId: verifier_task_id, addBlockedBy: [reviewer_task_id] })
```

DEBUG has no doc-sync step, so `chain_tail_task_id` starts as `verifier_task_id` directly (unlike BUILD, where it starts as `doc_sync_task_id`). Apply the SAME Learn-Distill Gate and Skill-Distill Gate documented in `build-workflow.md` (identical gate checks, identical `phase:learn-distill` / `phase:skill-distill` `TaskCreate` shape, identical `chain_tail_task_id` update rule) before creating Memory Update:

- `references/fast-path.md`'s own Learn-Distill Gate section already states `learn-distill` is dispatched "at the end of BUILD (standard and fast-path) and DEBUG workflows" — DEBUG was already in scope for that gate by design, it simply had no `TaskCreate` to back it, exactly like BUILD's dead-wiring gap.
- The Skill-Distill Gate's eligibility check (`craftflow_skill_ledger.py --query`) is workflow-type agnostic — it reads a project-wide ledger, not anything DEBUG-specific — so there is no reason to exclude DEBUG from it once BUILD has it.

**Clean-State Check (advisory):** before creating the Memory Update task below, run the check per `references/harness-self-checks.md § Clean-State Check` and fold any findings AND any skipped (unscannable) entries into this task's own deferred memory notes.

```text
TaskCreate({
  subject: "CRAFTFLOW Memory Update: Persist debug learnings",
  description: "wf:{workflow_uuid}\nkind:memory\norigin:router\nphase:memory-finalize\nplan:N/A\nscope:N/A\nreason:Persist captured Memory Notes\n\nROUTER ONLY: execute inline. Read the workflow artifact and THIS task description payload, persist to .craftflow/state/*.md.\nBefore persisting each MEMORY_NOTES field, resolve its destination file and section from SKILL.md Section 13's routing table, then write it with the apply mode (PREFERRED; REQUIRED whenever the payload carries \"archive\"):\n  python3 {plugin_root}/scripts/craftflow_memory_merge.py --apply <destination_file_path> < <payload_file>\nwith the JSON payload below minus \"file_text\" as <payload_file>, a temp file inside the repo/worktree that is gitignored (e.g. under docs/plans/) and that you delete afterwards -- the primary route, fed via < file; do NOT use a heredoc for this call, because the safe-shell guard only allows a strict python heredoc shape and a heredoc feeding --apply is refused (a file under the session scratchpad is an optional alternative only when the session scratchpad is available; the grant may fail closed in worktree/subdirectory sessions, so never rely on it); --apply reads the destination itself, writes any archived entries to the archive file first (fsync), then atomically replaces the destination, requires the .memory-finalize permit, exits 0 on success, and exits 1 leaving the destination untouched if the permit is missing or the merged text starts with { or has no ## heading. Do not write --apply's stdout anywhere.\nLegacy stdin/stdout mode (only when the payload has NO \"archive\" field, e.g. the no-workflow_uuid fallback where no permit exists): obtain the FULL destination file content via:\n  python3 {plugin_root}/scripts/craftflow_state_query.py <destination_file_path> --mode full\n(never a raw Read -- the destination files are exactly the .craftflow/state/**\nfiles the state-read-compaction guard may deny once oversized; --mode full is\nthis script's byte-identical full-content path) and pipe that output into:\n  python3 {plugin_root}/scripts/craftflow_memory_merge.py\nwith a JSON payload of {"file_text": "<full destination file content>", "section": "<target section, e.g. Common Gotchas>", "notes": [...], "retractions": [], "max_bullets": <cap per routing table, e.g. 60 for patterns -> project/patterns.md ## Common Gotchas; omit for learnings -> workflows/{workflow_uuid}/activeContext.md ## Learnings and verification -> workflows/{workflow_uuid}/progress.md ## Verification, which are workflow-scoped and need no cap>, "unit": <omit for "bullets" (default, existing behavior, unchanged) | "entries" for paragraph-shaped project-tier sections whose entries are NOT "- " bullet lines -- project/activeContext.md ## Last Updated, project/progress.md ## Last Updated, project/patterns.md ## Last Updated (each entry is a dated free-text paragraph, prepended newest-first, blank-line-separated); entries[0] is the newest, so max_bullets caps the N most-recent entries and oldest-first eviction trims from the END of the list, opposite of bullets-mode ordering>}\non stdin; use the FULL stdout as the replacement file content ONLY in this no-archive legacy mode -- section-anchored mode returns the whole file with only the target section's body replaced, not just a section body.\nOmit max_bullets entirely (do not pass it) if the destination file's memory contract sections are known to still be structurally corrupted; do not silently evict existing content when a section's heading structure is broken (see craftflow_memory_repair.py for the corrupted-file repair).\nWhen max_bullets is set (patterns.md -> project/patterns.md ## Common Gotchas), also pass \"archive\": {\"dir_rel\": \".craftflow/state/project/archive\", \"section_slug\": \"<kebab-case section name>\", \"month\": \"<current UTC YYYY-MM>\"}, and use --apply, which performs the ordering itself. Without --apply, its stdout is a JSON envelope -- never write it raw to any memory file (shape: {\"file_text\": ..., \"archived_bullets\": [...], \"archive_path\": ...}); --apply's contract is: when archived_bullets is non-empty, write archive_path FIRST (create .craftflow/state/project/archive/ if missing, append if the monthly archive file already exists), verify the write succeeded, and only THEN write file_text back to the destination file (mirrors craftflow_memory_repair.py's backup-before-any-destructive-write ordering -- never write the live (trimmed) file before the archive file exists).\nConfidence <0.7 notes are dropped. Retractions remove matching bullets. New bullets get a (conf: x) suffix. In "entries" unit mode, notes are prepended as new raw-text entries (no bullet/confidence formatting, no dedupe/supersede against existing entries) and retractions are not supported (ignored with a warning if present).\nthen remove the matching [craftflow-internal] memory_task_id line from activeContext.md ## References. Never spawn Agent() for this task.",
  activeForm: "Persisting debug learnings"
}) -> memory_task_id
TaskUpdate({ taskId: memory_task_id, addBlockedBy: [chain_tail_task_id] })
```
