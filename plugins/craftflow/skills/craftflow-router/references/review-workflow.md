### REVIEW preparation

1. REVIEW is advisory only.
2. Never create REM-FIX or implementation tasks directly from a REVIEW workflow.
3. If the final review verdict is `CHANGES_REQUESTED`, the router may offer `Start BUILD to fix (Recommended)` as a follow-up user choice.

### REVIEW task graph

```text
TaskCreate({
  subject: "CRAFTFLOW code-reviewer: Review {target}",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:review-audit\nplan:N/A\nscope:N/A\nreason:Advisory review\n\nRun a scoped code review.",
  activeForm: "Reviewing code"
}) -> reviewer_task_id

TaskCreate({
  subject: "CRAFTFLOW Memory Update: Persist review learnings",
  description: "wf:{workflow_uuid}\nkind:memory\norigin:router\nphase:memory-finalize\nplan:N/A\nscope:N/A\nreason:Persist captured Memory Notes\n\nROUTER ONLY: execute inline. Read the workflow artifact and THIS task description payload, persist to .craftflow/state/*.md,\nBefore persisting each MEMORY_NOTES field, resolve its destination file and section from SKILL.md Section 13's routing table, then obtain the FULL destination file content via:\n  python3 {plugin_root}/scripts/craftflow_state_query.py <destination_file_path> --mode full\n(never a raw Read -- the destination files are exactly the .craftflow/state/**\nfiles the state-read-compaction guard may deny once oversized; --mode full is\nthis script's byte-identical full-content path) and pipe that output into:\n  python3 {plugin_root}/scripts/craftflow_memory_merge.py\nwith a JSON payload of {"file_text": "<full destination file content>", "section": "<target section, e.g. Common Gotchas>", "notes": [...], "retractions": [], "max_bullets": <cap per routing table, e.g. 60 for patterns -> project/patterns.md ## Common Gotchas; omit for learnings -> workflows/{workflow_uuid}/activeContext.md ## Learnings and verification -> workflows/{workflow_uuid}/progress.md ## Verification, which are workflow-scoped and need no cap>}\non stdin; use the FULL stdout as the replacement file content -- section-anchored mode returns the whole file with only the target section's body replaced, not just a section body.\nOmit max_bullets entirely (do not pass it) if the destination file's memory contract sections are known to still be structurally corrupted; do not silently evict existing content when a section's heading structure is broken (see Phase 3 of this plan for the corrupted-file repair).\nWhen max_bullets is set (patterns.md -> project/patterns.md ## Common Gotchas), also pass \"archive\": {\"dir_rel\": \".craftflow/state/project/archive\", \"section_slug\": \"<kebab-case section name>\", \"month\": \"<current UTC YYYY-MM>\"}. The script's stdout becomes a JSON envelope {\"file_text\": ..., \"archived_bullets\": [...], \"archive_path\": ...} instead of plain text -- when archived_bullets is non-empty, write archive_path FIRST (create .craftflow/state/project/archive/ if missing, append if the monthly archive file already exists), verify the write succeeded, and only THEN write file_text back to the destination file (mirrors craftflow_memory_repair.py's backup-before-any-destructive-write ordering -- never write the live (trimmed) file before the archive file exists).\nConfidence <0.7 notes are dropped. Retractions remove matching bullets. New bullets get a (conf: x) suffix.\nthen remove the matching [craftflow-internal] memory_task_id line from activeContext.md ## References. Never spawn Agent() for this task.",
  activeForm: "Persisting review learnings"
}) -> memory_task_id
TaskUpdate({ taskId: memory_task_id, addBlockedBy: [reviewer_task_id] })
```
