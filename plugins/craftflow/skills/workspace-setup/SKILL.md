---
name: workspace-setup
description: "Use when the user asks to 'set up craftflow workspace memory', 'provision workspace-tier memory', 'add a north star for this workspace', 'set up cross-repo memory', or 'initialize .craftflow/state/workspace'."
allowed-tools: Read Bash Edit Grep Glob
---

## Mission

Provisions the **workspace-tier memory** at `{workspace_root}/.craftflow/state/workspace/*.md`
(`activeContext.md`, `patterns.md`, `progress.md`) — the durable north-star and cross-project
conventions layer that spans every repo under a workspace root.

**This skill is NOT `craftflow:ai-first-setup`.** Disambiguation:

- `craftflow:ai-first-setup` scaffolds a single project's AI-first governance surface
  (`AGENTS.md`, `docs/ai/`, harness lifecycle files) AND owns
  `{workspace_root}/.craftflow-workspace.json` — the write-allowlist that widens BUILD-phase
  agent write access outside worktree confinement (its own Step 0 / Step 4 item 10).
- `craftflow:workspace-setup` (this skill) only provisions the 3-file workspace **memory**
  tier. It never reads, writes, or infers `.craftflow-workspace.json` — that file remains
  `ai-first-setup`'s exclusive, human-confirmed-only artifact.

If the user's request is about widening write access to a shared root-level file (the
allowlist), route to `craftflow:ai-first-setup` Step 4 item 10 instead of this skill.

---

## Step 0 — Resolve `workspace_root`

Resolve `workspace_root` using the identical method `craftflow:ai-first-setup` Step 0 uses
(mirror `craftflow-router/SKILL.md` § "## 0. Resolve Project Root" step 1a exactly — this
skill may be invoked from a directory that is itself not a git repo, e.g. a parent folder
containing multiple independent repos as immediate children — do not re-implement this
resolution a third time):

1. Run `git rev-parse --show-toplevel` at the current working directory.
   - Succeeds → that toplevel IS `workspace_root` (covers the common case, including an
     Nx-monorepo that is itself one git repo).
   - Fails (cwd is not itself inside a git repo) → resolve the installed plugin path first,
     then invoke the resolver script from there:
     ```bash
     CRAFTFLOW_INSTALL=$(python3 -c "
     import json, pathlib
     reg = json.loads(pathlib.Path.home().joinpath('.claude/plugins/installed_plugins.json').read_text())
     print(reg['plugins']['craftflow@craftflow'][0]['installPath'])
     ")
     RESOLVE_RESULT=$(python3 "$CRAFTFLOW_INSTALL/scripts/craftflow_resolve_workspace_root.py" \
       --cwd <cwd> \
       --request "<user's setup request text>")
     RESOLVE_EXIT=$?
     ```
     - `RESOLVE_EXIT != 0` (the script itself could not complete, including an unresolved
       `CRAFTFLOW_INSTALL`): do not parse `$RESOLVE_RESULT`. Treat identically to
       `NO_REPO_FOUND` — treat cwd itself as `workspace_root`; note this in the Step 4
       report.
     - Otherwise, parse `outcome` from `$RESOLVE_RESULT` and branch:
       - `DETERMINISTIC` → `project_root` from the JSON is `workspace_root`.
       - `AMBIGUOUS` → present `candidates` to the user and ask which one is
         `workspace_root` before continuing.
       - `NO_REPO_FOUND` → treat cwd itself as `workspace_root`; note this in the Step 4
         report.

---

## Step 1 — Interview

Ask the user:

1. **North Star** (required): "What is the north-star goal for this workspace — the 1-2
   sentence outcome every repo under it is working toward?" Do not proceed to Step 2 without
   a non-empty answer.
2. **Shared cross-project conventions/gotchas** (optional, may be empty): "Are there any
   conventions or recurring gotchas that apply across every repo in this workspace, not just
   one repo?" Record as free text; may be left empty — do not press the user for an answer.

---

## Step 2 — Propose, never silently write

Construct the exact content `craftflow_workspace_init.py` will produce for each of the three
target files (mirrors that script's own template shape):

- `activeContext.md`: `## Current Focus`, `## North Star` (populated with the Step 1
  north-star text), `## Recent Changes`, `## Next Steps`, `## Decisions`, `## Learnings`,
  `## References`, `## Blockers`, `## Session Settings`, `## Last Updated`
- `patterns.md`: `## User Standards`, `## Common Gotchas` (populated with any Step 1
  conventions text), `## Project SKILL_HINTS`, `## Last Updated`
- `progress.md`: `## Current Workflow`, `## Tasks`, `## Completed`, `## Verification`,
  `## Last Updated`

If a target file already exists, `Read()` it first and show only the sections that will be
newly added — the underlying script auto-heals missing required sections and never clobbers
or rewrites existing content on re-run; mirror that framing in the preview so the user knows
nothing they already have will be lost.

Print the proposed content in a fenced code block, labeled with the exact target path, e.g.:

```
Proposed content for {workspace_root}/.craftflow/state/workspace/activeContext.md:

# Workspace Active Context

## Current Focus

## North Star
<north-star text>

## Recent Changes
...
```

Mirrors `craftflow:ai-first-setup` § Step 3 — Approval gate: wait for an explicit "go
ahead", "yes", "proceed", "confirm", or equivalent positive signal before Step 3 below. Do
not infer approval from context or prior messages in the conversation. If the user asks to
modify the content, update it and re-present before proceeding. Never call `Bash(...)` to
run the provisioning script before this explicit confirmation is given.

---

## Step 3 — Apply

After confirmation, invoke the Phase 1 script:

```bash
python3 "$CRAFTFLOW_INSTALL/scripts/craftflow_workspace_init.py" \
  --workspace-root "<workspace_root>" \
  --north-star "<north-star text>"
```

(`$CRAFTFLOW_INSTALL` resolved the same way as Step 0 — the installed plugin path, not a
path relative to this skill's own plugin-cache layout. If Step 0 already resolved and
exported `$CRAFTFLOW_INSTALL` in this session, reuse it rather than re-resolving.)

The script only accepts `--workspace-root` and `--north-star`; it has no flag for shared
conventions text. If the user gave conventions/gotchas text in Step 1, append it into
`patterns.md`'s `## Common Gotchas` section via a targeted `Edit()` immediately after the
script's own write succeeds — never before, and never if the script exits non-zero.

---

## Step 4 — Report

Confirm the three file paths created/updated:

- `{workspace_root}/.craftflow/state/workspace/activeContext.md`
- `{workspace_root}/.craftflow/state/workspace/patterns.md`
- `{workspace_root}/.craftflow/state/workspace/progress.md`

If the script exits non-zero (refusal-list hit — `$HOME` or `/` as `workspace_root` — or an
unwritable target), surface the exact stderr diagnostic to the user rather than a generic
failure message. Do not retry silently and do not fall back to a different `workspace_root`
without asking first.

Note any Step 0 fallback (`NO_REPO_FOUND` treated as cwd, or a non-zero `RESOLVE_EXIT`
treated identically) in this report so the user can confirm the resolved `workspace_root`
was correct.
