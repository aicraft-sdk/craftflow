---
name: status
description: |
  Read-only status of the current (or a chosen) Craftflow workflow — which phases
  and agents are done vs pending, blockers, proof status, and last event.
  Never writes anything; safe to call at any point.

  Use this skill when: checking workflow status, what phase am I in, what's running,
  what's done, what's pending, workflow progress, feature status, wf status,
  where am I in the build, what's the current phase, show me progress.

  Triggers: status, craftflow status, workflow status, where am I, what phase,
  progress, what's done, what's pending, wf status, feature status, what's running,
  show status, show progress, current workflow.

  NOTE: This skill does NOT go through craftflow-router. It is an inspection
  tool — read-only. Running it from a separate terminal is the truly
  non-interrupting path (works while the agent is mid-task); invoked in-session
  it runs when the agent yields between turns.
allowed-tools: Read, Bash, Glob
---

# craftflow Status

Non-mutating workflow status report. Reads `.craftflow/state/` as-is.

**Does NOT go through craftflow-router.** This is an inspection tool, not a
development task — an explicitly allowed exception to the always-route rule.

---

## Step 1 — Resolve Script Path

The first rule that yields an existing file wins.

**(a) Skill-relative (both hosts).** `SKILL_FILE` is this skill's own SKILL.md: in Claude Code, `<Base directory for this skill>/SKILL.md`; in Cursor (context contains `CRAFTFLOW_PLATFORM: cursor`), `~/.cursor/skills/status/SKILL.md`. Run:

```bash
python3 -c "import pathlib,sys; p=pathlib.Path(sys.argv[1]).expanduser().resolve().parents[2]/'scripts'/'craftflow_status_report.py'; print(p if p.is_file() else '')" "<SKILL_FILE>"
```

A non-empty output is `SCRIPT`. `resolve()` follows the Cursor symlink into the plugin checkout, and in Claude Code it points at the same plugin copy the skill was loaded from.

**Cursor workspace copy.** In Cursor only, if (a) printed nothing, take `WS_ROOT` from `git rev-parse --show-toplevel` (the current directory if that fails; your shell may be in a subdirectory). Wherever `<WS_ROOT>` appears below, write the quoted shell expression `"$(git rev-parse --show-toplevel || pwd)"` instead of pasting the printed path, and never leave it unquoted (a workspace path can contain spaces or `$(...)`). Run the same command once more with `SKILL_FILE` = `<WS_ROOT>/tools/craftflow-plugin/plugins/craftflow/skills/status/SKILL.md` (a Conductor-provisioned workspace carries this copy and the scripts it needs). A non-empty output is `SCRIPT`. When `SCRIPT` came from this workspace copy, add `--project "<WS_ROOT>"` to every command below, so the report reads only this workspace's state.

**(b) Claude Code only, and only if (a) printed nothing.** Read `~/.claude/plugins/installed_plugins.json`, take `craftflow@craftflow` → `installPath`, then run:

```bash
test -f "<installPath>/scripts/craftflow_status_report.py"
```

Use it only if that exits 0. Never use an `installPath` script without that check (an `installPath` without the script is unusable).

**(c) Otherwise** print exactly the following and stop. Never fall back to reading `.craftflow/state` by hand.

```
craftflow_status_report.py not found next to this skill. craftflow:status needs the whole craftflow plugin (skill + scripts/). Claude Code: update the craftflow plugin. Cursor: run install-cursor.sh from a local craftflow checkout (npx skills add copies only the skill folder and is not supported for status).
```

---

## Step 2 — Map User Intent to Flags

Parse the user's words for these signals:

| User says…                                   | Flag(s) to add          |
|----------------------------------------------|-------------------------|
| "verbose", "details", "full", "deep"         | `--verbose`             |
| "all", "all workflows", "list all"           | `--all`                 |
| "feature X", "for X", "on the X feature"    | `--feature "X"`         |
| "worktree X", "branch X"                    | `--worktree X`          |
| explicit wf-ID (`wf-auth-refactor-…`)        | `--wf <ID>`             |
| "statusline", "% segment", "hud segment"    | `--statusline`          |
| "specs", "spec index", "living specs", "what specs exist" | `--specs` (add `--spec-dir DIR` to override the project's declared living-spec directory) |
| nothing specific / "current"                 | (no flags)              |

Default (no flags) shows the most recently active workflow.

**`--feature` / `--worktree` now match on feature slugs** embedded in the new
`wf-{slug}-{date}-{hex}` id format, so `--feature auth-refactor` finds a
workflow whose id is `wf-auth-refactor-20260706-d4e5f6a7`.

`--statusline` emits the single-line statusline segment used by the wrapper
(`⚡ auth-refactor 60% · 🟢 phase_2 (3/5)`) — useful for ad-hoc checking
without the full report. Prints nothing and exits 0 when no workflow is active.

`--specs` prints a read-only portfolio index of the project's living specs
(id · title · status · packages) — the missing roll-up `--all` doesn't cover,
since `--all` only lists per-workflow rows. It resolves the spec directory
from a `living-spec: {dir: ...}` block under `## Doc Targets` in the
project's `CLAUDE.md`; pass `--spec-dir DIR` to override that lookup. If no
`living-spec` target is declared and no `--spec-dir` is given, it errors
with a clear message rather than guessing. Never writes anything.

---

## Step 3 — Run the Report

```bash
python3 "$SCRIPT" [FLAGS]
```

Examples:

```bash
python3 "$SCRIPT"                                       # current/active workflow
python3 "$SCRIPT" --verbose                             # + agent chain, event timeline, narrative
python3 "$SCRIPT" --all                                 # summary table of all workflows
python3 "$SCRIPT" --feature "auth-refactor"             # find by feature slug or goal text
python3 "$SCRIPT" --worktree auth-refactor-d4e5f6a7    # find by worktree slug suffix
python3 "$SCRIPT" --wf wf-auth-refactor-20260706-140000-d4e5f6a7   # explicit ID
python3 "$SCRIPT" --statusline                          # one-line ⚡ progress segment only
python3 "$SCRIPT" --specs                               # living-spec portfolio index
python3 "$SCRIPT" --specs --spec-dir docs/ai/specs      # override the spec directory
```

---

## Step 4 — Present the Output

- Display the output exactly as returned — it is already formatted.
- If the report shows `⚠️ BLOCKED` with a pending gate, highlight the block reason.
- If `--all` returns an empty table, note that no Craftflow workflows have run yet.
- If Step 1 printed the not-found message, show it and stop.
- **Do NOT call craftflow-router. Do NOT create tasks. Do NOT modify files.**

---

## Terminal Usage — for the truly non-interrupting path

Run from any second terminal while an agent is working (the script reads shared
on-disk state and never writes anything):

```bash
# Resolve the script path once (copy/paste the output):
python3 -c "
import json, pathlib
try:
    reg = json.loads(pathlib.Path('~/.claude/plugins/installed_plugins.json').expanduser().read_text())
    ip = reg['plugins']['craftflow@craftflow'][0]['installPath']
    print(pathlib.Path(ip) / 'scripts' / 'craftflow_status_report.py')
except Exception as e:
    print(f'# Not found: {e}')
"

# Cursor-only install (no Claude Code): resolve via the linked skill instead:
python3 -c "import pathlib; print(pathlib.Path('~/.cursor/skills/status/SKILL.md').expanduser().resolve().parents[2]/'scripts'/'craftflow_status_report.py')"

# Add a shell alias (replace PATH with the output above):
alias cfstatus='python3 /path/to/craftflow_status_report.py'

# Then use it from anywhere:
cfstatus                             # current workflow
cfstatus --all                       # all workflows
cfstatus --verbose                   # full detail
cfstatus --feature "auth-refactor"   # by feature slug or goal text
cfstatus --statusline                # one-line ⚡ segment (same as hud wrapper)
cfstatus --project /path/to/project  # if run outside the project tree
cfstatus --specs                     # living-spec portfolio index (id · title · status · packages)
cfstatus --specs --spec-dir docs/ai/specs  # override the spec directory
```

The `--project` flag makes it work from any cwd (e.g. inside a worktree or
a completely different directory) — auto-detection walks up from cwd first.
