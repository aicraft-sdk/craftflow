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

Read the plugin registry to find the installed script:

```
Read(file_path="~/.claude/plugins/installed_plugins.json")
```

Extract the `craftflow@craftflow` entry → `installPath`. The status script is at:

```
SCRIPT="<installPath>/scripts/craftflow_status_report.py"
```

If the registry is absent or the entry is missing, the script lives relative to
this SKILL.md at:

```
SCRIPT="$(dirname <this-skill-file>)/../../scripts/craftflow_status_report.py"
```

Resolve that path with `python3 -c "import pathlib; print(pathlib.Path('...').resolve())"`.

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
| nothing specific / "current"                 | (no flags)              |

Default (no flags) shows the most recently active workflow.

**`--feature` / `--worktree` now match on feature slugs** embedded in the new
`wf-{slug}-{date}-{hex}` id format, so `--feature auth-refactor` finds a
workflow whose id is `wf-auth-refactor-20260706-d4e5f6a7`.

`--statusline` emits the single-line statusline segment used by the wrapper
(`⚡ auth-refactor 60% · 🟢 phase_2 (3/5)`) — useful for ad-hoc checking
without the full report. Prints nothing and exits 0 when no workflow is active.

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
```

---

## Step 4 — Present the Output

- Display the output exactly as returned — it is already formatted.
- If the report shows `⚠️ BLOCKED` with a pending gate, highlight the block reason.
- If `--all` returns an empty table, note that no Craftflow workflows have run yet.
- If the script is not found, tell the user and show the terminal alias from below.
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

# Add a shell alias (replace PATH with the output above):
alias cfstatus='python3 /path/to/craftflow_status_report.py'

# Then use it from anywhere:
cfstatus                             # current workflow
cfstatus --all                       # all workflows
cfstatus --verbose                   # full detail
cfstatus --feature "auth-refactor"   # by feature slug or goal text
cfstatus --statusline                # one-line ⚡ segment (same as hud wrapper)
cfstatus --project /path/to/project  # if run outside the project tree
```

The `--project` flag makes it work from any cwd (e.g. inside a worktree or
a completely different directory) — auto-detection walks up from cwd first.
