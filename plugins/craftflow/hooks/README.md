# craftflow Hooks

This directory now serves two different purposes:

1. **Plugin runtime hooks** via `hooks.json`
   - `PreToolUse` — protected writes guard (Edit, Write, and now Bash matchers; Read events are not intercepted) — the Bash matcher inspects write-shaped commands (redirects, `tee`, python `open()`/`write_text()`/`os.system`/`subprocess`/`shutil`/`os.rename` writes) for protected-path targets, closing a prior bypass where Bash-only agents could overwrite memory files undetected; destructive-command guard (Bash matcher — denies destructive commands whose resolved target is in-cwd (e.g. `rm -rf packages/agent-cli`, `rm -rf .`) as well as ones that escape the session's own cwd/worktree, e.g. a worktree relative-path escape; covers `rm`, `rmdir`, `mv`, `shred`, `truncate`, `dd`, `chmod`, `find -exec`/`-execdir`/`-ok`/`-okdir`/`-delete`, `git clean`/`reset --hard`/`push --force`, with fail-closed handling of dynamic ($/backtick) targets; see `docs/incidents/2026-07-25-phase3-verifier-rm-attempt.md`)
   - `SessionStart` — workflow resume context; hook import self-check
   - `PostToolUse` — workflow artifact integrity audit and memory placeholder restore (defensive, fires on Edit/Write)
   - `TaskCompleted` — task metadata validation (enforced: block mode)
   - `PostCompact` — compaction event capture
   - `SubagentStop` — agent contract presence audit and memory placeholder restore
   - `PreCompact` — workflow state snapshot before compaction
   - `Stop` — workflow state snapshot and memory placeholder restore on session stop
   - `StopFailure` — API error logging (async)
   - `InstructionsLoaded` — instruction file load audit (async)
2. **Optional git pre-commit helper** via `pre-commit`

## Plugin Runtime Hooks

When CRAFTFLOW is installed as a Claude Code plugin, Claude Code reads `hooks/hooks.json`
from the plugin bundle and runs the referenced scripts from `${CLAUDE_PLUGIN_ROOT}/scripts`.

The shipped runtime hooks are intentionally minimal. Most hooks operate in audit mode; `memoryWrites`, `protectedWrites`, `taskMetadata`, and `bashDestructiveTraversal` are enforced in block mode (config: `config/hook-mode.json`; each toggle enum-validates its value and fails closed — block — on a missing or malformed config, via the shared `resolve_toggle_decision()` helper in `craftflow_hooklib.py`):
- protect and enforce direct memory markdown writes on Edit/Write (block mode, `memoryWrites`)
- protect the same memory files, the `.memory-finalize` permit, and workflow JSON artifacts against Bash-write bypasses — redirects, `tee`, and python-script writes (block mode, `protectedWrites`)
- deny destructive Bash commands (in-cwd or cwd/worktree-escaping) across a widened command vocabulary (block mode, `bashDestructiveTraversal`)
- inject workflow resume context
- self-check that every sibling hook script still imports cleanly under python3, warning (not blocking) on failure
- audit workflow artifact integrity after writes
- validate and enforce CRAFTFLOW task metadata on completion (block mode)
- restore memory placeholders after Edit/Write and on SubagentStop and Stop
- snapshot workflow state before compaction and on session stop
- log API failures and instruction file loads for telemetry

## Internal Publication Audit

The plugin also ships an internal drift check:

```bash
python3 plugins/craftflow/scripts/craftflow_harness_audit.py
```

It validates the publication-critical contract:
- plugin manifest version matches `README.md` and `CHANGELOG.md`
- marketplace metadata matches the shipped plugin version
- plugin hooks and MCP names referenced by docs/router actually exist
- workflow replay fixtures and checker are present
- key router headings still exist for invariant coverage
- router-consumed task metadata and agent contract fields are still present

## Optional Git Pre-Commit Hook

This is separate from Claude Code plugin hooks. Install it only if you want
git commits blocked when tests fail:

```bash
cp plugins/craftflow/hooks/pre-commit .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

It blocks `git commit` if your test suite fails. No test runner configured?
Hook exits 0 and passes through.
