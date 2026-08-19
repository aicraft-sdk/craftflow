---
name: ai-first-setup
description: "Use when the user asks to 'set up an AI-first project', 'apply the AI-first playbook', 'scaffold AGENTS.md and docs/ai', 'make this repo AI-first', 'bootstrap AI Context Pack', or 'wire up agents-md-lint'."
allowed-tools: Read Write Edit Bash Grep Glob
---

## Mission

Apply the AI-first playbook to the current working directory. This skill installs the full governance surface: an `AI_FIRST.md` charter, a root `AGENTS.md`, governance docs under `docs/ai/`, an AI Context Pack per project, config files for the AGENTS.md validator, validator scripts, CI workflows, optional Husky pre-commit hooks, and harness lifecycle/state/scope files. The result is a repo where every AI agent has authoritative, structured context and every pull request carries mandatory evidence.

---

## Harness Subsystem Map

This skill scaffolds a complete 5-subsystem harness. Every artifact maps to a subsystem:

| Subsystem | Artifact(s) |
|---|---|
| **Instructions** | `AI_FIRST.md`, `AGENTS.md` (root + per-project), `docs/ai/*` governance docs |
| **State** | `progress.md` (repo-root state log), `session-handoff.md` |
| **Verification** | `tools/scripts/agents-md-lint.*`, `tools/scripts/ai-contract-pack-lint.*`, `.github/workflows/*.yml`, `.husky/pre-commit` / `.git/hooks/pre-commit`, `.github/pull_request_template.md` |
| **Scope** | `feature_list.json` (machine-readable, evidence-gated) |
| **Session Lifecycle** | `init.sh` (START), `clean-state-checklist.md` + `session-handoff.md` (END) |

**Governing principle:** reliability is environment engineering, not prompt engineering. A gap in any subsystem is a predictable failure source.

---

## Step 0 — Workspace scope interview

Before any scaffolding, resolve the intended **workspace root** and surface known shared root-level files. Do this before Step 1.

1. **Ask the user:** "Is this a single project, or a workspace/monorepo containing multiple projects (e.g. Nx monorepo, multiple apps/packages)?"
2. **Resolve `workspace_root`** using the identical method Craftflow BUILD workflows use (mirror `craftflow-router/SKILL.md`'s `## 0. Resolve Project Root` step 1a exactly — this skill scaffolds into arbitrary third-party target repos, so the resolver script must be located via the installed plugin path, not a path relative to this skill's own plugin-cache layout):
   - Run `git rev-parse --show-toplevel` at the current working directory.
     - Succeeds → that toplevel IS `workspace_root` (covers the common case, including an Nx-monorepo that is itself one git repo — Step 1's shape detection handles the internal project layout within it).
     - Fails (cwd is not itself inside a git repo — e.g. a parent folder containing multiple independent repos as immediate children) → resolve the installed plugin path first, then invoke the resolver script from there:
       ```bash
       CRAFTFLOW_INSTALL=$(python3 -c "
       import json, pathlib
       reg = json.loads(pathlib.Path.home().joinpath('.claude/plugins/installed_plugins.json').read_text())
       print(reg['plugins']['craftflow@craftflow'][0]['installPath'])
       ")
       CRAFTFLOW_INSTALL_EXIT=$?
       RESOLVE_RESULT=$(python3 "$CRAFTFLOW_INSTALL/scripts/craftflow_resolve_workspace_root.py" \
         --cwd <cwd> \
         --request "<user's setup request text>")
       RESOLVE_EXIT=$?
       ```
       (A non-zero `CRAFTFLOW_INSTALL_EXIT` is not handled as a separate branch — it surfaces downstream as a non-zero `RESOLVE_EXIT` from the next command, an empty/unusable `$CRAFTFLOW_INSTALL` path, which the `RESOLVE_EXIT != 0` handling below already catches.)
       - **If `RESOLVE_EXIT != 0`** (the script itself could not complete, including an unresolved `CRAFTFLOW_INSTALL`): do not parse `$RESOLVE_RESULT`. Treat identically to `NO_REPO_FOUND` — treat cwd itself as `workspace_root`; note this (script exit != 0 → treated as NO_REPO_FOUND) in the Step 6 report.
       - **Otherwise**, parse `outcome` from `$RESOLVE_RESULT` and branch:
         - `DETERMINISTIC` → `project_root` from the JSON is `workspace_root`.
         - `AMBIGUOUS` → present `candidates` to the user and ask which one is `workspace_root` before continuing.
         - `NO_REPO_FOUND` → treat cwd itself as `workspace_root` (no git repo found among children); note this in the Step 6 report.
3. **Ask (or detect via a repo scan of `workspace_root`) about shared root-level files:** "Are there known shared, root-level files outside any individual app/package directory that agents will likely need to edit later (for example a root `CONTRACTS.md`, a shared lint/CI config, a monorepo-wide doc)?" Each entry must be a **bare filename that is a direct child of `workspace_root`** — no `/` or `\` path separators, no `.`/`..`, no leading `/` or `~`, and it must not name (or resolve through a symlink into) a nested git repo's directory; if the user offers a nested path, ask them to name the file itself instead, since a nested path will be silently dropped later (see Step 4 item 10's entry contract). A repo scan is: list files directly under `workspace_root` that are not inside any single project/package directory and are not already part of this skill's own emitted set (Step 2). Record the confirmed list as `shared_root_files` (may be empty).

Carry `workspace_root` and `shared_root_files` forward into Step 2 and Step 4.

---

## Step 1 — Detect repo shape

Read the following files to classify the repository and derive the decision variables that control which templates are emitted:

**JS detection (first pass):**
- `package.json` — languages, frameworks, runtime, package manager, scripts, devDependencies (detect: `react`, `next`, `vite`, `vue`, `svelte`, `ink`, `husky`)
- `nx.json` — present → Nx-monorepo shape; absent → single-app or generic-monorepo
- `pnpm-workspace.yaml` or `lerna.json` — present → generic-monorepo shape; also inspect workspace globs for `scanRoots`

**Non-JS detection (second pass — run when no `package.json` is found):**
- `go.mod` → **go-module** shape
- `Cargo.toml` → **rust-crate** shape
- `pyproject.toml` or `requirements.txt` → **python** shape
- Multiple primary language roots → **polyglot** (emit subfolder AGENTS.md overrides)

**Existing setup detection:**
- `AGENTS.md` — already present? Record sections found; do not overwrite blindly
- `AI_FIRST.md` — already present? Note for user
- `docs/ai/` — already present? List existing files

Classify the repo into one of seven shapes:

| Shape | Signals | Key decisions derived |
|---|---|---|
| **single-app** | No `nx.json`, no workspace file; single root `package.json` | `scanRoots: ["."]`; use `ai-checks.generic.yml.tpl`; no Nx generators |
| **Nx-monorepo** | `nx.json` present | `scanRoots` from `nx.json` workspaceLayout or default `["platform","libs"]`; use `ai-checks.nx.yml.tpl` |
| **generic-monorepo** | `pnpm-workspace.yaml` or `lerna.json` without `nx.json` | `scanRoots` from workspace globs; use `ai-checks.generic.yml.tpl` |
| **polyglot** | Root language is X, subfolder(s) use Y | Emit a subfolder `AGENTS.md` override for each non-root language subtree |
| **go-module** | `go.mod` present; no `package.json` | `scanRoots: ["."]`; shell validators; shell CI; shell pre-commit hook; no Husky |
| **rust-crate** | `Cargo.toml` present; no `package.json` | `scanRoots: ["."]`; shell validators; shell CI; shell pre-commit hook; no Husky |
| **python** | `pyproject.toml` or `requirements.txt`; no `package.json` | `scanRoots: ["."]`; shell validators; shell CI; shell pre-commit hook; no Husky |

Additional decisions to derive:

- **UI eligibility for DESIGN.md**: `true` if any of `react`, `next`, `vite`, `vue`, `svelte` appear in `dependencies` or `devDependencies`; `ink` is a TUI — emit `docs/ai/DESIGN.md` as a text-only (ASCII layout reference) adapted version, NOT a Figma/Storybook DESIGN.md; explicit user opt-in overrides the heuristic for any edge case
- **Husky (JS repos only)**: check for `.husky/` directory or `"prepare": "husky"` in `package.json` — already wired means pre-commit snippet is appended; not present means emit the full setup instructions; **skip entirely for non-JS repos**
- **BUILD_TEST_COMMAND**: derive from primary language using this table:

  | Primary language | `BUILD_TEST_COMMAND` | Lint command |
  |---|---|---|
  | Go | `go build ./... && go test -race ./...` | `golangci-lint run` |
  | Rust | `cargo build && cargo test` | `cargo clippy` |
  | Python (Poetry) | `poetry install && poetry run pytest` | `ruff check .` |
  | Python (pip) | `pip install -r requirements.txt && pytest` | `ruff check .` |
  | JS (npm) | `npm ci && npm run build && npm test` | (existing logic) |
  | JS (yarn/pnpm/bun) | (adjust per detected manager) | — |

---

## Step 1.5 — Upgrade detection

Before building the proposal, read and classify each existing file. Files that don't exist are **missing**; files that exist are checked against structural markers below.

| Status label | Meaning | Action in Step 4 |
|---|---|---|
| **missing** | File does not exist on disk | Write it from template |
| **present-needs-update** | File exists but fails one or more structural marker checks (see below) | Patch or overwrite — see per-file rules in Step 4 |
| **present-unchanged** | File exists and passes all structural marker checks | Skip — list in Step 6 report as "already current" |

### Structural marker checks

**`AGENTS.md`** — Read the file and verify ALL four markers. If any one is missing, classify as **present-needs-update**:

1. A line starting with one or more `#` characters contains the phrase `Session Lifecycle` (case-insensitive; e.g., `## 8. Session Lifecycle & Read-Order`)
2. References `init.sh` anywhere in the body
3. References `progress.md` anywhere in the body
4. References `feature_list.json` anywhere in the body

If `AGENTS.md` is **present-needs-update** AND other v0.1.0 artifacts exist (`docs/ai/` present, harness files absent), surface this upgrade callout at the top of the Step 2 proposal table:

> **Upgrade detected — v0.1.0 → v0.2.0:** This repo has an existing AI-first setup that predates the harness lifecycle model. `AGENTS.md` will be **patched** (Session Lifecycle block injected — existing content preserved). The N harness files listed as `missing` below will be written fresh (N = count of harness files classified `missing` in this pass).

**Harness files** (`init.sh`, `progress.md`, `session-handoff.md`, `clean-state-checklist.md`, `feature_list.json`) — Check existence only. Any repo set up before v0.2.0 will not have these; they will be **missing** and written normally. If present (user-created), read for unfilled `{{...}}` placeholders → **present-needs-update** if any found, **present-unchanged** otherwise.

**All other files** (`AI_FIRST.md`, `docs/ai/*.md`, `AI.md`, `TESTS.md`, config, validators, CI) — If present, always **present-unchanged** (skip). These are large governance docs; the user must explicitly request regeneration.

Surface the Status column in the Step 2 proposal table so the user can see what will and will not be changed before approving.

---

## Step 2 — Propose the file list

Build the complete list of files to be written, adapted to the detected repo shape. Present it to the user in a table with **four columns**: **Path**, **Source template**, **Notes/Decisions**, **Status** (from Step 1.5).

Include every file from the following set, applying the exclusion rules below:

### Always emitted (all shapes)

| Path | Template | Notes/Decisions |
|---|---|---|
| `AI_FIRST.md` | `assets/charter/AI_FIRST.md.tpl` | |
| `AGENTS.md` | `assets/charter/AGENTS.root.md.tpl` | |
| `docs/ai/WAY_OF_WORK.md` | `assets/governance/WAY_OF_WORK.md.tpl` | |
| `docs/ai/SECURITY.md` | `assets/governance/SECURITY.md.tpl` | |
| `docs/ai/TESTING.md` | `assets/governance/TESTING.md.tpl` | |
| `docs/ai/QUALITY.md` | `assets/governance/QUALITY.md.tpl` | |
| `docs/ai/OBSERVABILITY.md` | `assets/governance/OBSERVABILITY.md.tpl` | |
| `docs/ai/PROMPTS.md` | `assets/governance/PROMPTS.md.tpl` | |
| `docs/ai/GLOSSARY.md` | `assets/governance/GLOSSARY.md.tpl` | |
| `docs/ai/RELEASE.md` | `assets/governance/RELEASE.md.tpl` | |
| `docs/ai/DESIGN.md` | `assets/governance/DESIGN.md.tpl` | |
| `docs/ai/specs/template.md` | `assets/governance/specs/template.md` | |
| `docs/ai/rfcs/template.md` | `assets/governance/rfcs/template.md` | |
| `docs/ai/decisions/0001-template.md` | `assets/governance/decisions/0001-template.md` | |
| `docs/plans/.gitkeep` | (empty file) | |
| `docs/research/.gitkeep` | (empty file) | |
| `AI.md` (single-app) or `platform/<name>/AI.md` (monorepo) | `assets/context-pack/AI.service.md.tpl` or `AI.frontend.md.tpl` | |
| `TESTS.md` (single-app) or per-project | `assets/context-pack/TESTS.md.tpl` | |
| `project-config.json` | `assets/config/project-config.json.tpl` | |
| `.agents-md-validator.json` | `assets/config/agents-md-validator.json.tpl` | |
| `.github/pull_request_template.md` | `assets/config/pull_request_template.md.tpl` | |
| `init.sh` | `assets/lifecycle/init.sh.tpl` | Session Lifecycle START; fail-fast env health |
| `progress.md` | `assets/state/progress.md.tpl` | State log |
| `session-handoff.md` | `assets/lifecycle/session-handoff.md.tpl` | Session Lifecycle END; one-screen resume note |
| `clean-state-checklist.md` | `assets/lifecycle/clean-state-checklist.md.tpl` | Session Lifecycle END; end-of-session gate |
| `feature_list.json` | `assets/scope/feature_list.json.tpl` | Scope; machine-readable, evidence-gated |
| `{workspace_root}/.craftflow-workspace.json` | (generated — see Step 4 item 10, no template) | Always proposed to the human for confirmation (never auto-written); pre-populated from Step 0's `shared_root_files` |

### Shape-conditional — validators, CI, and hooks

| Condition | Emit | Skip |
|---|---|---|
| **JS repo** (has `package.json`) | `tools/scripts/agents-md-lint.js` (verbatim) | `agents-md-lint.sh` |
| **JS repo** | `tools/scripts/ai-contract-pack-lint.js` (verbatim) | `ai-contract-pack-lint.sh` |
| **JS repo** | `.github/workflows/agents-md-validate.yml` (`agents-md-validate.yml.tpl`) | `agents-md-validate.sh.yml.tpl` |
| **JS repo** | `.github/workflows/docs-coverage.yml` (`docs-coverage.yml.tpl`) | `docs-coverage.sh.yml.tpl` |
| **JS repo** — Husky not yet wired | Husky `package.json` patches + `.husky/pre-commit` | `.git/hooks/pre-commit` |
| **Non-JS repo** (go-module / rust-crate / python) | `tools/scripts/agents-md-lint.sh` (verbatim) | `agents-md-lint.js` |
| **Non-JS repo** | `tools/scripts/ai-contract-pack-lint.sh` (verbatim) | `ai-contract-pack-lint.js` |
| **Non-JS repo** | `.github/workflows/agents-md-validate.yml` (`agents-md-validate.sh.yml.tpl`) | `agents-md-validate.yml.tpl` (Node variant) |
| **Non-JS repo** | `.github/workflows/docs-coverage.yml` (`docs-coverage.sh.yml.tpl`) | `docs-coverage.yml.tpl` (Node variant) |
| **Non-JS repo** | `.git/hooks/pre-commit` (`assets/hooks/pre-commit-nonjs`) | Husky, `package.json` patches |
| **Nx-monorepo** | `assets/ci/ai-checks.nx.yml.tpl` as `.github/workflows/ai-checks.yml` | `ai-checks.generic.yml.tpl` |
| **Non-Nx** (single-app, generic, polyglot, go-module, rust-crate, python) | `assets/ci/ai-checks.generic.yml.tpl` as `.github/workflows/ai-checks.yml` (substitute `BUILD_TEST_COMMAND`) | `ai-checks.nx.yml.tpl`; Nx generators |
| **Polyglot** | Additional `<subfolder>/AGENTS.md` (overrides only) | — |

Flag the following as manual follow-ups in the proposal (do not block on them):

- TODO placeholders: governance docs contain `{{...}}` tokens the user must fill in (on-call URL, ticket prefix, team names)
- Severity ramp: `aiContractPack.severity` defaults to `"warn"`; ramp to `"error"` after the first real spec and ADR are added
- Husky initialization (JS repos only): run `npm install` (or the detected package manager's install command) after writing `package.json` patches to activate the pre-commit hook
- Non-JS pre-commit hook: `.git/hooks/pre-commit` is not committed to the repo; document it in `AGENTS.md` so new contributors know to re-run the skill or copy the hook after cloning

---

## Step 3 — Approval gate

Present the complete file list to the user before writing any file. Wait for an explicit "go ahead", "yes", "proceed", "confirm", or equivalent positive signal before Step 4. Do not infer approval from context or prior messages in the conversation. If the user asks to modify the list, update it and re-present before proceeding.

---

## Step 4 — Apply (dependency-ordered write sequence)

**Upgrade guard:** Before writing any file, check its Status from Step 1.5. Only write files marked **missing** or **present-needs-update**. Files marked **present-unchanged** are listed in the Step 6 report as "skipped (already present)" but are NOT written.

Write eligible files in this exact order so that cross-references resolve correctly:

1. **Governance docs** — `docs/ai/*.md` and their subdirectory templates (`specs/`, `rfcs/`, `decisions/`); create `docs/plans/.gitkeep` and `docs/research/.gitkeep`
2. **Charter** — `AI_FIRST.md`, then `AGENTS.md`; ordered by status:
   - **`AI_FIRST.md`:** write from template if missing; skip if present-unchanged.
   - **`AGENTS.md` — missing:** write full template; fill `{{PLACEHOLDER}}` tokens from Step 1 detection; verify ≤ 8 KB.
   - **`AGENTS.md` — present-needs-update:** do NOT overwrite. Apply a targeted patch:
     1. Read the current file.
     2. Find the heading containing "Agent Operating" or "Operating Rules" — this is the anchor. If no such heading exists in the file, do NOT silently append. Stop the patch, emit a WARNING in the Step 6 report: "AGENTS.md anchor not found — Session Lifecycle block was NOT inserted. Manually add it before your operating rules section, or re-run with `--force-overwrite`." Mark `AGENTS.md` as `patch-failed` in the Step 2 status column and skip remaining sub-steps for this file.
     3. Insert the Session Lifecycle block immediately before that anchor heading; renumber the anchor and all later sections by +1:
        ````
        ## N. Session Lifecycle & Read-Order
        Every agent session follows this startup sequence (harness START):
        1. Run `./init.sh` — verifies environment health before any work
        2. Read `progress.md` — what happened last session (newest entry on top)
        3. Read `feature_list.json` — what is done, in progress, and next
        4. Check `git log --oneline -10` — recent changes
        5. Pick **exactly one** `todo` or `in_progress` feature; work only on that feature

        Definition of Done: feature is done when acceptance_criteria met, verification commands pass, evidence non-empty, `progress.md` updated.
        End every session with `clean-state-checklist.md` and overwrite `session-handoff.md`.
        ````
     4. Find the section whose heading contains both "Documentation" AND "Index". Append these entries if not already listed: `init.sh`, `progress.md`, `feature_list.json`, `session-handoff.md`, `clean-state-checklist.md`. If no such heading exists, create a new section immediately after the Session Lifecycle block you just inserted:
        ````
        ## N. Documentation Index
        - `init.sh` — env health check (Session Lifecycle START)
        - `progress.md` — state log
        - `feature_list.json` — scope registry
        - `session-handoff.md` — session resume note
        - `clean-state-checklist.md` — end-of-session gate
        ````
        Renumber the anchor and all later sections again (+1 for the new Documentation Index section). Note the creation in the Step 6 report.
     5. Count bytes in the patched file. If the result would exceed 8 KB, do NOT write. Emit a WARNING in the Step 6 report: "AGENTS.md would be [N bytes] after patching — exceeds 8 KB cap. Patch aborted. Trim the file to under 7.7 KB then re-run." Mark `AGENTS.md` as `patch-failed`.
   - **`AGENTS.md` — present-unchanged:** skip.
3. **AI Context Pack** — `AI.md`, `TESTS.md`, and (if UI-eligible) `DESIGN.md`; for monorepos, write per-project context packs into the appropriate subfolder
4. **Config** — `project-config.json`, `.agents-md-validator.json`, `.github/pull_request_template.md`; set `scanRoots` to the derived value
5. **Validator scripts** — copy verbatim based on repo shape:
   - **JS repo**: `tools/scripts/agents-md-lint.js`, `tools/scripts/ai-contract-pack-lint.js`
   - **Non-JS repo**: `tools/scripts/agents-md-lint.sh`, `tools/scripts/ai-contract-pack-lint.sh`; make both executable (`chmod +x`)
6. **CI** — write the appropriate `ai-checks.yml` variant plus the two named workflows:
   - **JS repo**: `agents-md-validate.yml` (Node variant) + `docs-coverage.yml` (Node variant)
   - **Non-JS repo**: `agents-md-validate.yml` (shell variant from `agents-md-validate.sh.yml.tpl`) + `docs-coverage.yml` (shell variant from `docs-coverage.sh.yml.tpl`)
7. **Pre-commit hook** — branch on repo shape:
   - **JS repo, Husky not yet wired**: add `"husky"` to `devDependencies`, add `"prepare": "husky"` to `scripts` in `package.json`; write `.husky/pre-commit` from `assets/husky/pre-commit-snippet`; instruct user to run `npm install` to initialize
   - **JS repo, Husky already wired**: append validator calls to existing `.husky/pre-commit`; do not overwrite
   - **Non-JS repo**: write `assets/hooks/pre-commit-nonjs` to `.git/hooks/pre-commit`; run `chmod +x .git/hooks/pre-commit`; do NOT patch `package.json`
   - **Explicit opt-out** (user requested no pre-commit hook): skip this sub-step entirely
8. **Harness lifecycle + state + scope files** — write all five new files:
   - `init.sh` from `assets/lifecycle/init.sh.tpl`; run `chmod +x init.sh`; substitute `{{BUILD_TEST_COMMAND}}`, `{{LINT_CMD}}`, `{{TYPE_CHECK_CMD}}`, `{{INSTALL_CMD}}`, `{{TOOL_VERSION_CHECKS}}` from Step 1 detection
   - `progress.md` from `assets/state/progress.md.tpl`; substitute `{{PROJECT_NAME}}`
   - `session-handoff.md` from `assets/lifecycle/session-handoff.md.tpl`; substitute `{{PROJECT_NAME}}`
   - `clean-state-checklist.md` from `assets/lifecycle/clean-state-checklist.md.tpl`; substitute `{{PROJECT_NAME}}`, `{{BUILD_TEST_COMMAND}}`
   - `feature_list.json` from `assets/scope/feature_list.json.tpl`
9. **`.gitignore` audit** — confirm `CLAUDE.md` and `.claude/` remain ignored; do not add new ignores for `AGENTS.md`, `AI.md`, `TESTS.md`, `docs/ai/**`, or `tools/scripts/**`
10. **Workspace-root allowlist — `{workspace_root}/.craftflow-workspace.json`** — This file is a security-relevant, **human-authored-only** artifact: its `writable_paths` widens BUILD-phase agent write access outside worktree confinement, and `docs/2026-08-13-craftflow-workspace-root-allowlist-decision.md` ("Alternatives Considered") explicitly rejects auto-generating or scaffolding it — Craftflow only ever *reads* this file, never writes or infers its contents unprompted. This skill must never call `Write()` on it without explicit, in-the-moment human confirmation for this specific file. Always **propose**, never silently write, regardless of repo shape (this is independent of Steps 1–9; `workspace_root` may differ from the project root written into in Steps 1–9, per Step 0):
    - Construct the proposed content (same rules as before — only the delivery mechanism changes):
      - If `{workspace_root}/.craftflow-workspace.json` does not exist: propose `{"writable_paths": [<shared_root_files from Step 0, or [] if none were named>]}`.
      - If it already exists: read it (read-only), then propose an updated `writable_paths` list that appends any newly-identified `shared_root_files` entries not already present (dedup by exact string match; validated per the entry contract below); never propose removing existing entries; never propose overwriting the whole file wholesale.
    - **Print the proposed content** to the user in a copy-pasteable fenced code block, labeled with the exact target path, e.g.:
      ```
      Proposed content for {workspace_root}/.craftflow-workspace.json:
      { "writable_paths": ["CONTRACTS.md"] }
      ```
    - **Ask the human directly:** "Should I create/update this file for you, or would you rather save it yourself?" The Step 3 approval-gate signal for the rest of the file list does NOT cover this file — it requires its own explicit, in-the-moment confirmation because it is a security-relevant, human-authored artifact. Only call `Write()` on `{workspace_root}/.craftflow-workspace.json` after the human gives that confirmation. If the human declines, defers, or does not respond with explicit confirmation, do NOT write the file — record it as a pending item in Step 6's Manual follow-ups instead.
    - **Entry contract** (unchanged — must match `read_workspace_writable_paths()` in `craftflow_resolve_workspace_root.py` exactly, or the entry is silently dropped by that validator — two separate checks apply, both must pass):
      1. The entry, resolved against `workspace_root` (following any symlink), must land as a **literal direct child of `workspace_root`** — i.e. its resolved parent directory must be `workspace_root` itself. An entry whose resolved path escapes `workspace_root` entirely (e.g. a symlink pointing anywhere outside it) is dropped with reason `resolves_outside_workspace_root`, regardless of whether the escape target happens to be a nested repo.
      2. Additionally, the resolved entry must not equal, or be nested inside, any nested git repo's directory under `workspace_root` — dropped with reason `resolves_inside_nested_repo` if it does.
      Syntactically, each entry is also the bare filename of a direct child only — no path separators (`/` or `\`), no `.` or `..`, no leading `/` or `~` (no absolute paths, no home-dir expansion). When unsure, use the shortest exact filename rather than a path.

### Built-in rules enforced during Apply

- **Upgrade guard**: never overwrite a file marked **present-unchanged** without explicit user request
- Root `AGENTS.md` must not exceed 8 KB after write; check byte length before writing
- Root `AGENTS.md` must contain these five sections: "Project / Scope", "Non-Negotiable", "Build", "Security", "Agent Operating"
- Root `AGENTS.md` prose must not contain the literals `/src/`, `/lib/`, or `/components/`; use descriptive text such as "source files" or "shared components" instead
- Subfolder `AGENTS.md` files contain overrides only — they must not repeat the root section titles "Project / Scope Identification", "Non-Negotiable Constraints", "Build, Run & Test", "Security & Safety", or "Agent Operating Rules"
- `DESIGN.md` at the `docs/ai/` level is always emitted (even for TUI/CLI projects) as a text-only governance document; per-project `DESIGN.md` inside the AI Context Pack is only emitted for UI-eligible projects
- Nx generator scaffolding (tools/plugin generators) is omitted for non-Nx repos

---

## Step 5 — Verify

Run the following commands after writing all files. Report the exit code and full output for each:

**JS repo:**

```bash
# 1. AGENTS.md validator (Node)
node tools/scripts/agents-md-lint.js

# 2. AI Context Pack validator (Node)
node tools/scripts/ai-contract-pack-lint.js
```

**Non-JS repo (Go / Rust / Python):**

```bash
# 1. AGENTS.md validator (shell)
bash tools/scripts/agents-md-lint.sh

# 2. AI Context Pack validator (shell)
bash tools/scripts/ai-contract-pack-lint.sh
```

**All shapes:**

```bash
# 3. Project build and test (use detected commands)
{{BUILD_TEST_COMMAND}}

# 4. Confirm gitignore policy is intact
git check-ignore CLAUDE.md
git check-ignore .claude/foo

# 5. Confirm newly written files are not ignored
git status --short AGENTS.md AI.md TESTS.md docs/ai/WAY_OF_WORK.md

# 6. Verify harness lifecycle files exist and init.sh is executable
ls init.sh progress.md session-handoff.md clean-state-checklist.md feature_list.json
test -x init.sh && echo "init.sh is executable"

# 7. Validate feature_list.json is valid JSON
python3 -m json.tool feature_list.json > /dev/null && echo "feature_list.json is valid JSON"

# 8. Confirm no 'done' feature has empty evidence (evidence gate)
python3 -c "
import json, sys
data = json.load(open('feature_list.json'))
bad = [f['id'] for f in data.get('features', []) if f.get('status') == 'done' and not f.get('evidence', '').strip()]
if bad: sys.exit('ERROR: done features with empty evidence: ' + str(bad))
print('evidence gate: OK')
"

# 9. Verify {workspace_root}/.craftflow-workspace.json — only applicable if the human
# already confirmed the Step 4 item 10 proposal and the file was written. If the human
# deferred (declined or did not confirm), this file will not exist yet — that is expected,
# not an error; skip the check and note it as a manual follow-up in Step 6 instead.
python3 -c "
import json, os, sys
path = '{workspace_root}/.craftflow-workspace.json'
if not os.path.exists(path):
    print('workspace allowlist: SKIPPED (not yet created — human deferred the Step 4 item 10 proposal; see Step 6 Manual follow-ups)')
    sys.exit(0)
data = json.load(open(path))
paths = data.get('writable_paths', [])
if not isinstance(paths, list):
    sys.exit('ERROR: writable_paths is not a list in ' + path)
print('workspace allowlist: OK (' + str(len(paths)) + ' entries)')
"
```

A passing run shows: `AGENTS.md lint passed`, `AI contract pack lint passed`, build exit 0, test exit 0, `CLAUDE.md` reported ignored, the new files appearing as untracked (not ignored), `init.sh is executable`, `feature_list.json is valid JSON`, and either `workspace allowlist: OK` (the human confirmed the Step 4 item 10 proposal and the file was written) or `workspace allowlist: SKIPPED` (the human deferred — expected, not a failure; carry it into Step 6 Manual follow-ups). If the resolver script from Step 0 reported any `workspace_writable_paths_dropped` entries (or if re-running the resolver script here surfaces any), surface them verbatim in the Step 6 report under Manual follow-ups — a dropped entry means a `shared_root_files` answer was silently rejected and the user must re-supply it as a bare filename.

---

## Step 6 — Report

Emit a summary with the following four sections: **Files created**, **Files skipped (already present)**, **Manual follow-ups**, and **Craftflow workspace guards**.

**Files created** — one line per file with path and brief purpose (only files that were actually written).

**Files skipped (already present)** — one line per file that was found on disk and left untouched. If none, omit this section.

**Manual follow-ups** — list these items in order of priority:

1. TODO placeholders: open each governance doc under `docs/ai/` and replace every `{{PLACEHOLDER}}` token with project-specific values (on-call URL, ticket prefix, team name, Figma URL if UI)
2. Severity ramp: after merging the first real feature spec (`docs/ai/specs/`) and the first ADR (`docs/ai/decisions/`), flip `aiContractPack.severity` from `"warn"` to `"error"` in `.agents-md-validator.json`
3. Husky initialization (JS repos only): if Husky was newly added, run the package manager install command so the pre-commit hook activates before the next commit
4. Non-JS pre-commit hook: `.git/hooks/pre-commit` is not committed to the repo; new contributors must re-run the skill or manually copy it from `assets/hooks/pre-commit-nonjs` after cloning
5. **Workspace-root allowlist (if deferred in Step 4 item 10):** `{workspace_root}/.craftflow-workspace.json` was proposed but not written because the human had not yet confirmed it. Create or update the file yourself using the exact content printed during Apply (Step 4 item 10), or re-run this skill and confirm the write when prompted. Until this file exists with the intended `writable_paths` entries, BUILD-phase Craftflow agents cannot write to those shared workspace-root files.

**Craftflow workspace guards (read before your next session)** — if this repo is operated under Craftflow orchestration (`.craftflow/` present, or this setup was invoked via `craftflow:craftflow-router`):
- `.craftflow/state/{activeContext,patterns,progress}.md` are router-protected memory files — permit-gated, router-owned finalization only. Never hand-edit them directly.
- `{workspace_root}/.craftflow-workspace.json`'s `writable_paths` (Step 4 item 10) grants BUILD-phase agents write access to shared root-level files outside their isolated worktree. Add an entry there when a new shared file needs it.
- Full mechanics: `tools/craftflow-plugin/plugins/craftflow/hooks/README.md` (near the worktree-confinement / `writable_paths` section) and `docs/2026-08-13-craftflow-workspace-root-allowlist-decision.md`.

---

## Step 7 — Harness Self-Evaluation

After completing the report, score the produced setup against the 5-subsystem rubric. Print the result table:

| Subsystem | Score (0–2) | Evidence | Gaps |
|---|---|---|---|
| Instructions | | | |
| State | | | |
| Verification | | | |
| Scope | | | |
| Session Lifecycle | | | |
| **Total** | **/10** | | |

**Scoring rules:**
- **2** = present AND enforced with evidence (a gate, not just a document)
- **1** = present but unenforced (document exists, no gate)
- **0** = absent

**End the evaluation with:**
1. Weakest subsystem (lowest score)
2. Single highest-leverage fix
3. The failure mode that fix prevents

**Target score: 9–10/10.** If the total is below 8, surface the gaps in the Step 6 Manual Follow-ups before ending.

---

## Reference Disclosure

This skill reads asset files using skill-relative paths (the same convention used throughout Steps 2, 4, and 8 — e.g. `assets/charter/AI_FIRST.md.tpl`, with no home-directory or installed-skill-path prefix). Load them as needed during Steps 2–7:

### references/
- `references/playbook-summary.md` — condensed sections 0–11 of the AI-first setup playbook; decisions and ordering rationale
- `references/adaptation-cheatsheet.md` — per-shape variations and no-Nx adaptation rules
- `references/agents-md-validation.md` — required sections, 8 KB cap, forbidden pattern list, subfolder-override contract
- `references/ai-context-pack.md` — structure and rules for `AI.md`, `TESTS.md`, and optional `DESIGN.md` per project
- `references/enforcement-and-husky.md` — how validator scripts work, Husky wiring, pre-commit snippet
- `references/ci-workflow-templates.md` — GitHub Actions YAML reference for all three CI files
- `references/harness-engineering.md` — 5-subsystem harness model, failure catalog, evaluation rubric; load for Step 7 self-evaluation
- `references/harness-invocation-prompts.md` — copy-paste prompts for Evaluate/Answer/Diagnose modes

### assets/charter/
- `assets/charter/AI_FIRST.md.tpl`
- `assets/charter/AGENTS.root.md.tpl`

### assets/governance/
- `assets/governance/WAY_OF_WORK.md.tpl`
- `assets/governance/DESIGN.md.tpl`
- `assets/governance/SECURITY.md.tpl`
- `assets/governance/TESTING.md.tpl`
- `assets/governance/QUALITY.md.tpl`
- `assets/governance/OBSERVABILITY.md.tpl`
- `assets/governance/PROMPTS.md.tpl`
- `assets/governance/GLOSSARY.md.tpl`
- `assets/governance/RELEASE.md.tpl`
- `assets/governance/specs/template.md`
- `assets/governance/rfcs/template.md`
- `assets/governance/decisions/0001-template.md`

### assets/context-pack/
- `assets/context-pack/AGENTS.project.md.tpl`
- `assets/context-pack/AI.service.md.tpl`
- `assets/context-pack/AI.frontend.md.tpl`
- `assets/context-pack/TESTS.md.tpl`
- `assets/context-pack/DESIGN.md.tpl`

### assets/config/
- `assets/config/project-config.json.tpl`
- `assets/config/agents-md-validator.json.tpl`
- `assets/config/pull_request_template.md.tpl`

### assets/tools/
- `assets/tools/agents-md-lint.js`
- `assets/tools/ai-contract-pack-lint.js`
- `assets/tools/agents-md-lint.sh`
- `assets/tools/ai-contract-pack-lint.sh`

### assets/ci/
- `assets/ci/agents-md-validate.yml.tpl`
- `assets/ci/docs-coverage.yml.tpl`
- `assets/ci/agents-md-validate.sh.yml.tpl`
- `assets/ci/docs-coverage.sh.yml.tpl`
- `assets/ci/ai-checks.nx.yml.tpl`
- `assets/ci/ai-checks.generic.yml.tpl`

### assets/husky/
- `assets/husky/pre-commit-snippet`

### assets/hooks/
- `assets/hooks/pre-commit-nonjs`

### assets/lifecycle/
- `assets/lifecycle/init.sh.tpl`
- `assets/lifecycle/session-handoff.md.tpl`
- `assets/lifecycle/clean-state-checklist.md.tpl`

### assets/state/
- `assets/state/progress.md.tpl`

### assets/scope/
- `assets/scope/feature_list.json.tpl`
