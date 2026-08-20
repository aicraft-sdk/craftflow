# Craftflow Architecture (generated)

<!-- GENERATED FILE -- do not hand-edit. Run `node scripts/gen-craftflow-graph.mjs`
     from the repo root to regenerate, or `pnpm run gen:craftflow-graph`.
     `pnpm run verify:craftflow-graph` checks this file isn't stale. -->

Introspected from `hooks/hooks.json` and `agents/*.md` / `skills/*/SKILL.md`
frontmatter — not hand-maintained, so it can't silently drift from the actual
registrations the way a hand-written summary table can.

## Hook wiring

Every registered hook event, its matcher (tool-name pattern, or unmatched `*`
for events that aren't tool-scoped), and the script it invokes.

```mermaid
flowchart LR
  event_PreToolUse["PreToolUse"]
  event_SessionStart["SessionStart"]
  event_PostToolUse["PostToolUse"]
  event_TaskCompleted["TaskCompleted"]
  event_PostCompact["PostCompact"]
  event_SubagentStop["SubagentStop"]
  event_PreCompact["PreCompact"]
  event_Stop["Stop"]
  event_StopFailure["StopFailure"]
  event_InstructionsLoaded["InstructionsLoaded"]
  script_craftflow_pretooluse_guard_py(["craftflow_pretooluse_guard.py"])
  script_craftflow_sdd_cache_pre_py(["craftflow_sdd_cache_pre.py"])
  script_craftflow_pretooluse_bash_guard_py(["craftflow_pretooluse_bash_guard.py"])
  script_craftflow_safe_shell_guard_py(["craftflow_safe_shell_guard.py"])
  script_craftflow_context_migration_py(["craftflow_context_migration.py"])
  script_craftflow_sessionstart_context_py(["craftflow_sessionstart_context.py"])
  script_craftflow_hook_selfcheck_py(["craftflow_hook_selfcheck.py"])
  script_craftflow_posttooluse_artifact_guard_py(["craftflow_posttooluse_artifact_guard.py"])
  script_craftflow_memory_protect_restore_py(["craftflow_memory_protect_restore.py"])
  script_craftflow_sdd_cache_post_py(["craftflow_sdd_cache_post.py"])
  script_craftflow_posttooluse_repeat_guard_py(["craftflow_posttooluse_repeat_guard.py"])
  script_craftflow_task_completed_guard_py(["craftflow_task_completed_guard.py"])
  script_craftflow_postcompact_context_py(["craftflow_postcompact_context.py"])
  script_craftflow_subagent_stop_audit_py(["craftflow_subagent_stop_audit.py"])
  script_craftflow_precompact_state_py(["craftflow_precompact_state.py"])
  script_craftflow_stop_persist_py(["craftflow_stop_persist.py"])
  script_craftflow_stop_verify_py(["craftflow_stop_verify.py"])
  script_craftflow_stop_failure_log_py(["craftflow_stop_failure_log.py"])
  script_craftflow_instructions_loaded_audit_py(["craftflow_instructions_loaded_audit.py"])
  event_PreToolUse -->|"Edit / Write"| script_craftflow_pretooluse_guard_py
  event_PreToolUse -->|"Read"| script_craftflow_pretooluse_guard_py
  event_PreToolUse -->|"WebFetch"| script_craftflow_sdd_cache_pre_py
  event_PreToolUse -->|"Bash"| script_craftflow_pretooluse_guard_py
  event_PreToolUse -->|"Bash"| script_craftflow_pretooluse_bash_guard_py
  event_PreToolUse -->|"Bash"| script_craftflow_safe_shell_guard_py
  event_SessionStart -->|"startup"| script_craftflow_context_migration_py
  event_SessionStart -->|"startup / resume / compact"| script_craftflow_sessionstart_context_py
  event_SessionStart -->|"startup / resume / compact"| script_craftflow_hook_selfcheck_py
  event_PostToolUse -->|"Edit / Write"| script_craftflow_posttooluse_artifact_guard_py
  event_PostToolUse -->|"Edit / Write"| script_craftflow_memory_protect_restore_py
  event_PostToolUse -->|"WebFetch"| script_craftflow_sdd_cache_post_py
  event_PostToolUse --> script_craftflow_posttooluse_repeat_guard_py
  event_TaskCompleted --> script_craftflow_task_completed_guard_py
  event_PostCompact --> script_craftflow_postcompact_context_py
  event_SubagentStop --> script_craftflow_subagent_stop_audit_py
  event_SubagentStop --> script_craftflow_memory_protect_restore_py
  event_PreCompact --> script_craftflow_precompact_state_py
  event_Stop --> script_craftflow_stop_persist_py
  event_Stop --> script_craftflow_memory_protect_restore_py
  event_Stop --> script_craftflow_stop_verify_py
  event_StopFailure --> script_craftflow_stop_failure_log_py
  event_InstructionsLoaded --> script_craftflow_instructions_loaded_audit_py
```

23 hook registrations across 10 event types, 19 distinct scripts.

## Agent -> declared skills

Skills each agent's frontmatter (`skills:`) declares pre-loading. Agents not
shown here declare none in frontmatter (they can still invoke any skill via
the `Skill` tool at runtime — this graph reflects declared, not possible,
wiring): `doubt-verifier`, `github-researcher`, `learn-distiller`, `plan-gap-reviewer`, `web-researcher`.

```mermaid
flowchart LR
  agent_bug_investigator["bug-investigator"]
  agent_code_reviewer["code-reviewer"]
  agent_component_builder["component-builder"]
  agent_doc_syncer["doc-syncer"]
  agent_integration_verifier["integration-verifier"]
  agent_planner["planner"]
  agent_silent_failure_hunter["silent-failure-hunter"]
  agent_skill_author["skill-author"]
  skill_craftflow_session_memory(["craftflow:session-memory"])
  skill_craftflow_debugging_patterns(["craftflow:debugging-patterns"])
  skill_craftflow_test_driven_development(["craftflow:test-driven-development"])
  skill_craftflow_verification_before_completion(["craftflow:verification-before-completion"])
  skill_craftflow_code_review_patterns(["craftflow:code-review-patterns"])
  skill_craftflow_code_generation(["craftflow:code-generation"])
  skill_craftflow_diff_driven_docs(["craftflow:diff-driven-docs"])
  skill_craftflow_planning_patterns(["craftflow:planning-patterns"])
  skill_craftflow_skill_distillation(["craftflow:skill-distillation"])
  agent_bug_investigator --> skill_craftflow_session_memory
  agent_bug_investigator --> skill_craftflow_debugging_patterns
  agent_bug_investigator --> skill_craftflow_test_driven_development
  agent_bug_investigator --> skill_craftflow_verification_before_completion
  agent_code_reviewer --> skill_craftflow_code_review_patterns
  agent_code_reviewer --> skill_craftflow_verification_before_completion
  agent_component_builder --> skill_craftflow_session_memory
  agent_component_builder --> skill_craftflow_test_driven_development
  agent_component_builder --> skill_craftflow_code_generation
  agent_component_builder --> skill_craftflow_verification_before_completion
  agent_doc_syncer --> skill_craftflow_diff_driven_docs
  agent_doc_syncer --> skill_craftflow_verification_before_completion
  agent_integration_verifier --> skill_craftflow_verification_before_completion
  agent_planner --> skill_craftflow_session_memory
  agent_planner --> skill_craftflow_planning_patterns
  agent_silent_failure_hunter --> skill_craftflow_code_review_patterns
  agent_skill_author --> skill_craftflow_skill_distillation
  agent_skill_author --> skill_craftflow_verification_before_completion
```

## Agent -> declared tools

| Agent | Declared tools |
|-------|----------------|
| `bug-investigator` | `Read`, `Edit`, `Write`, `Bash`, `Grep`, `Glob`, `Skill`, `LSP`, `WebFetch`, `TaskUpdate` |
| `code-reviewer` | `Read`, `Bash`, `Grep`, `Glob`, `Skill`, `LSP`, `WebFetch` |
| `component-builder` | `Read`, `Edit`, `Write`, `Bash`, `Grep`, `Glob`, `Skill`, `LSP`, `WebFetch`, `TaskUpdate` |
| `doc-syncer` | `Read`, `Edit`, `Write`, `Bash`, `Grep`, `Glob` |
| `doubt-verifier` | `Read`, `Bash`, `Grep`, `Glob`, `LSP` |
| `github-researcher` | `Read`, `Write`, `Edit`, `Bash`, `WebFetch`, `WebSearch`, `TaskUpdate` |
| `integration-verifier` | `Read`, `Bash`, `Grep`, `Glob`, `Skill`, `LSP`, `WebFetch` |
| `learn-distiller` | `Read`, `Bash`, `Grep`, `Glob` |
| `plan-gap-reviewer` | `Read`, `Grep`, `Glob`, `LSP` |
| `planner` | `Read`, `Edit`, `Write`, `Bash`, `Grep`, `Glob`, `Skill`, `LSP`, `WebFetch`, `TaskUpdate` |
| `silent-failure-hunter` | `Read`, `Bash`, `Grep`, `Glob`, `Skill`, `LSP`, `WebFetch` |
| `skill-author` | `Read`, `Edit`, `Write`, `Bash`, `Grep`, `Glob` |
| `web-researcher` | `Read`, `Write`, `Edit`, `Bash`, `WebFetch`, `WebSearch`, `TaskUpdate` |

## Inventory

- 13 agents (`agents/*.md`)
- 28 skills (`skills/*/SKILL.md`)
- 19 hook scripts wired in `hooks/hooks.json`
