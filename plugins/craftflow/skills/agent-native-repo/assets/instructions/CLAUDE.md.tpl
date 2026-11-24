@AGENTS.md

## Claude Code only

- `AGENTS.md` above is the source of truth. Do not copy its rules here; add Claude-specific notes only.
- `.claude/settings.json` runs lint and typecheck after edits and the test suite before you stop. Never disable or bypass these hooks.
- Fan out subagents for read-only exploration; only the main session edits files for the claimed task.
