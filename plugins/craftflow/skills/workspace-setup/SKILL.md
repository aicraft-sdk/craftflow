---
name: workspace-setup
description: "Use when the user asks to 'set up craftflow workspace memory', 'provision workspace-tier memory', 'add a north star for this workspace', 'set up cross-repo memory', or 'initialize .craftflow/state/workspace'."
allowed-tools: Read Write Bash Edit Grep Glob
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

**Provisioning the memory tier alone does not make it writable.** This skill never writes
`.craftflow-workspace.json`, so a workspace provisioned by this skill alone has no `members`
and no `memory_writable` key — the workspace-tier memory stays read-only for every nested
project (fail-closed by design, not a bug; live-verified as finding E1 in
`docs/plans/2026-09-15-design-membership-ownership-chec-plan.md`). To make the memory tier
shared-writable, the workspace owner must also run `craftflow:ai-first-setup` Step 4 item 10
and explicitly confirm a `members` list plus `memory_writable: true` in
`{workspace_root}/.craftflow-workspace.json`.

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

Before asking anything, check whether a North Star already exists for this workspace:
`Read()` `{workspace_root}/.craftflow/state/workspace/activeContext.md` if it exists, and
check whether its `## North Star` section already has a non-empty body (any non-blank line
between that heading and the next `##` heading). If it does, tell the user explicitly:
"A North Star already exists for this workspace and will NOT be overwritten by this run —
`craftflow_workspace_init.py` never clobbers existing section content on re-run." Do this
before Step 1's question so the user isn't misled into thinking a new answer will replace
the existing one.

Ask the user:

1. **North Star** (required): "What is the north-star goal for this workspace — the 1-2
   sentence outcome every repo under it is working toward?" Do not proceed to Step 2 without
   a non-empty answer. (If an existing North Star was found above, still collect this answer
   for the Step 2 preview and Step 3 invocation, but the warning already told the user it
   will not overwrite what's already there.)
2. **Shared cross-project conventions/gotchas** (optional, may be empty): "Are there any
   conventions or recurring gotchas that apply across every repo in this workspace, not just
   one repo?" Record as free text; may be left empty — do not press the user for an answer.

---

## Step 2 — Propose, never silently write

Construct the exact content that will result from this run for each of the three target
files. Two different mechanisms populate them, and the preview must reflect both
accurately — do not imply the script alone produces all of it:

- `activeContext.md`: `craftflow_workspace_init.py` (Step 3's `Bash(...)` call) writes
  `## Current Focus`, `## North Star` (populated with the Step 1 north-star text, unless an
  existing non-empty North Star was found in Step 1 — in that case the script leaves it
  untouched), `## Recent Changes`, `## Next Steps`, `## Decisions`, `## Learnings`,
  `## References`, `## Blockers`, `## Session Settings`, `## Last Updated`
- `patterns.md`: the script writes `## User Standards`, `## Common Gotchas` (empty
  skeleton — the script has no flag for conventions text), `## Project SKILL_HINTS`,
  `## Last Updated`. Any Step 1 conventions/gotchas text is then appended into
  `## Common Gotchas` by *this skill's own* separate `Edit()` in Step 3, immediately after
  the script succeeds — not by the script itself.
- `progress.md`: the script writes `## Current Workflow`, `## Tasks`, `## Completed`,
  `## Verification`, `## Last Updated`

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

After confirmation, invoke `craftflow_workspace_init.py`. **Never interpolate the Step 1 north-star
text directly into a Bash command as a double-quoted argument** — it is free-text the user
typed, and a north-star answer containing `"`, a backtick, `$(...)`, or a newline could
break out of the quoted argument or inject a command. Instead:

1. Write the exact Step 1 north-star text (verbatim, byte-for-byte, no escaping applied by
   you) to a private temp file using `Write()` — never via a shell heredoc or echo, since
   that would reintroduce the same interpolation risk:

   ```
   Write(file_path="/tmp/craftflow-workspace-north-star-<random>.txt", content="<verbatim Step 1 north-star text>")
   ```

2. Invoke the script with `--north-star-file` pointing at that path instead of
   `--north-star`:

   ```bash
   python3 "$CRAFTFLOW_INSTALL/scripts/craftflow_workspace_init.py" \
     --workspace-root "<workspace_root>" \
     --north-star-file "/tmp/craftflow-workspace-north-star-<random>.txt"
   ```

   (`$CRAFTFLOW_INSTALL` resolved the same way as Step 0 — the installed plugin path, not a
   path relative to this skill's own plugin-cache layout. If Step 0 already resolved and
   exported `$CRAFTFLOW_INSTALL` in this session, reuse it rather than re-resolving.
   `workspace_root` itself is safe to interpolate directly here: it is always a filesystem
   path resolved in Step 0 via `git rev-parse --show-toplevel` or the JSON `project_root`
   from `craftflow_resolve_workspace_root.py` — never raw keyboard-typed free text — so it
   cannot carry shell metacharacters in practice.)

3. After the script exits (success or failure), delete the temp file
   (`rm -f "/tmp/craftflow-workspace-north-star-<random>.txt"`) — do not leave the raw
   north-star text lying around in `/tmp`.

The script only accepts `--workspace-root` and one of `--north-star`/`--north-star-file`; it
has no flag for shared conventions text. If the user gave conventions/gotchas text in
Step 1, append it into `patterns.md`'s `## Common Gotchas` section via a targeted `Edit()`
immediately after the script's own write succeeds — never before, and never if the script
exits non-zero. After that `Edit()`, `Read()` `patterns.md`'s `## Common Gotchas` section
again and confirm the new conventions text is actually present verbatim. If it is not
present, do not silently continue — report the failure to the user explicitly (the
conventions text was lost) alongside the rest of the Step 4 report, rather than claiming
the run fully succeeded.

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
