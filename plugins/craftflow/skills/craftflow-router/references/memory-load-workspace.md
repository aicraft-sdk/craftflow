# Memory load: workspace tier

Read this when a workspace marker is found in the step 5a walk (`SKILL.md → ## 2. Memory Load And Template Validation`), or when a `Read(...)` in the Memory Load step is denied with `state-read-compaction`: the first section is the workspace tier, the last the self-heal.

5a. Workspace-tier discovery (capped upward walk, max 3 levels above PROJECT_ROOT, to
    avoid runaway scans): starting at PROJECT_ROOT's parent, check each ancestor for
    EITHER a `.craftflow-workspace.json` file OR a `.craftflow/state/workspace/`
    directory. Stop at the first match, or after 3 levels, or at the filesystem root,
    whichever comes first. Never select `$HOME` or `/` itself as a matched workspace
    root even if a marker is somehow present there (defense in depth alongside
    `craftflow_workspace_init.py`'s own refusal list).
    - No marker found: skip silently -- zero behavior change (existing production
      reality for every non-workspace session today).
    - Marker found at {workspace_root}: pass it as `--workspace-root` to the digest call
      (covers workspace/activeContext.md, patterns.md, progress.md).
      Missing/malformed file: auto-heal via craftflow:session-memory template (same rule
      as project/'s own auto-heal), never a hard stop.
      Unreadable (permission error): skip with a logged note, never a hard stop.
    - workspace_root == PROJECT_ROOT (the current project IS itself the configured
      workspace root): do not double-load; treat workspace tier as absent for this
      session to avoid merging a tier with itself.
    Merge precedence (lowest to highest): workspace/ < project/ < workflows/{wf-id}/ --
    for ## Current Focus / ## Next Steps / ## Tasks, the highest-precedence tier present
    wins; ## Decisions / ## User Standards / ## Architecture Patterns always come from
    project/ (unchanged rule) with workspace/'s own such sections available as
    additional read-only context, never overriding project/'s.
    Note: this **load** walk is deliberately NOT membership-gated, unlike
    `discover_workspace_root()`'s write-grant walk in `craftflow_hooklib.py`. The
    divergence is intentional (back-compat with existing workspace-memory users) and
    ADR-recorded -- do not "re-sync" the two without reading that ADR first.

## State-read compaction self-heal

State-read compaction self-heal: if any `Read(...)` in the Memory Load step is denied with a
`state-read-compaction` reason (the target `.craftflow/state/**` file is oversized), do not
treat the denial as a hard failure. Instead, run the exact `craftflow_state_query.py ...
--mode summary` command named in the deny message via `Bash(...)` and treat its stdout as the
loaded memory content for that file.

