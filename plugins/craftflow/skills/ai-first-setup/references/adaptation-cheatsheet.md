# Adaptation Cheatsheet

Per-shape variations for the AI-first setup playbook. Apply these rules during Step 1 (detect) and Step 2 (propose).

---

## Shape: single-app

**Signals:** No `nx.json`, no `pnpm-workspace.yaml`, no `lerna.json`; single `package.json` at root.

| Decision | Value |
|---|---|
| `scanRoots` | `["."]` |
| CI ai-checks variant | `ai-checks.generic.yml.tpl` |
| Nx generators | SKIP entirely |
| AI Context Pack location | Repo root (`AI.md`, `TESTS.md` at root) |
| Subfolder AGENTS.md | Only if polyglot (one per non-root language subtree) |

**Notes:**
- `AI.md` doubles as the single-project Context Pack entry; link it from root `AGENTS.md` §8
- `scanRoots: ["."]` means `ai-contract-pack-lint.js` looks for `AGENTS.md` in the root directory itself; since the root `AGENTS.md` is the project-level doc, `AI.md` and `TESTS.md` must exist at the root

---

## Shape: Nx-monorepo

**Signals:** `nx.json` present.

| Decision | Value |
|---|---|
| `scanRoots` | From `nx.json` workspaceLayout (`appsDir` + `libsDir`), or default `["platform", "libs"]` |
| CI ai-checks variant | `ai-checks.nx.yml.tpl` |
| Nx generators | Include if user explicitly requests scaffolding |
| AI Context Pack location | Per first-level project under each scanRoot |
| Subfolder AGENTS.md | One per first-level project under each scanRoot |

**Notes:**
- `ai-checks.nx.yml.tpl` uses `nx affected -t lint` — keep Nx-specific steps as-is
- `designPathPatterns` in `.agents-md-validator.json` should be configured to match UI project naming conventions (e.g., prefix `ui-` for `platform/ui-*`)

---

## Shape: generic-monorepo

**Signals:** `pnpm-workspace.yaml` or `lerna.json` present without `nx.json`.

| Decision | Value |
|---|---|
| `scanRoots` | Derived from workspace globs (e.g., `packages/*` → `["packages"]`) |
| CI ai-checks variant | `ai-checks.generic.yml.tpl` |
| Nx generators | SKIP entirely |
| AI Context Pack location | Per first-level directory under each scanRoot that has `AGENTS.md` |

**Notes:**
- Parse `pnpm-workspace.yaml` packages array; strip glob wildcards to get root dir names
- `BUILD_TEST_COMMAND` in CI template: adjust for pnpm or yarn based on detected package manager

---

## Shape: go-module

**Signals:** `go.mod` present at root; no `package.json`.

| Decision | Value |
|---|---|
| `scanRoots` | `["."]` |
| CI ai-checks variant | `ai-checks.generic.yml.tpl` (substitute `go build ./... && go test -race ./...`) |
| Nx generators | SKIP entirely |
| AI Context Pack location | Repo root (`AI.md`, `TESTS.md` at root) |
| Validators | `agents-md-lint.sh` + `ai-contract-pack-lint.sh` (shell; no Node required) |
| Pre-commit hook | `.git/hooks/pre-commit` from `assets/hooks/pre-commit-nonjs` |
| Husky | SKIP; no `package.json` to patch |
| CI lint step | `golangci-lint run` |

**Notes:**
- Drop `actions/setup-node` from both named CI workflows; use `agents-md-validate.sh.yml.tpl` and `docs-coverage.sh.yml.tpl` verbatim.
- `BUILD_TEST_COMMAND` = `go build ./... && go test -race ./...`.

---

## Shape: rust-crate

**Signals:** `Cargo.toml` present at root; no `package.json`.

| Decision | Value |
|---|---|
| `scanRoots` | `["."]` |
| CI ai-checks variant | `ai-checks.generic.yml.tpl` (substitute `cargo build && cargo test`) |
| Nx generators | SKIP entirely |
| AI Context Pack location | Repo root (`AI.md`, `TESTS.md` at root) |
| Validators | `agents-md-lint.sh` + `ai-contract-pack-lint.sh` (shell; no Node required) |
| Pre-commit hook | `.git/hooks/pre-commit` from `assets/hooks/pre-commit-nonjs` |
| Husky | SKIP |
| CI lint step | `cargo clippy` |

**Notes:**
- Drop `actions/setup-node` from both named CI workflows; use `agents-md-validate.sh.yml.tpl` and `docs-coverage.sh.yml.tpl` verbatim.
- `BUILD_TEST_COMMAND` = `cargo build && cargo test`.

---

## Shape: python

**Signals:** `pyproject.toml` (Poetry/PDM/Hatch) or `requirements.txt` present at root; no `package.json`.

| Decision | Value |
|---|---|
| `scanRoots` | `["."]` |
| CI ai-checks variant | `ai-checks.generic.yml.tpl` (substitute Poetry or pip command) |
| Nx generators | SKIP entirely |
| AI Context Pack location | Repo root (`AI.md`, `TESTS.md` at root) |
| Validators | `agents-md-lint.sh` + `ai-contract-pack-lint.sh` (shell; no Node required) |
| Pre-commit hook | `.git/hooks/pre-commit` from `assets/hooks/pre-commit-nonjs` |
| Husky | SKIP |
| CI lint step | `ruff check .` |

**Notes:**
- Drop `actions/setup-node`; use `agents-md-validate.sh.yml.tpl` and `docs-coverage.sh.yml.tpl` verbatim.
- `BUILD_TEST_COMMAND` = `poetry install && poetry run pytest` (Poetry) or `pip install -r requirements.txt && pytest` (pip).
- `python3` must be pre-installed in CI (ubuntu-latest includes it); no extra setup step needed for the shell validators.

---

## Shape: polyglot

**Signals:** Root uses language X; one or more subfolders use language Y (e.g., `swift`, `python`, `go`, `rust`).

| Decision | Value |
|---|---|
| Subfolder AGENTS.md | One per non-root language subtree, overrides only |
| Root AGENTS.md languages | List ALL languages (e.g., `TypeScript + Swift`) |

**Notes:**
- Subfolder `AGENTS.md` contains only the language-specific build/test commands and framework notes
- Must not repeat root section titles: "Project / Scope Identification", "Non-Negotiable Constraints", "Build, Run & Test", "Security & Safety", "Agent Operating Rules"
- Typical content: `## Local Overrides` with build command, test command, and `Package.swift` or `Makefile` reference

---

## No-Nx adaptation rules

When `nx.json` is absent, apply these substitutions throughout all templates:

| xfuse original | Generic replacement |
|---|---|
| `nx <target> <project>` | `{{BUILD_CMD}}` / `{{TEST_CMD}}` |
| `nx affected -t lint` | `npm run lint` (or detected package manager equivalent) |
| `yarn nx ...` | `npm ci && npm run build && npm test` (or detected) |
| `nx e2e <project>` | `npm run e2e` (if e2e script exists) or omit |
| `nx generators` | Omit this section entirely |

---

## Package manager / language substitutions

| Detected | Install | Build | Test | Lint |
|---|---|---|---|---|
| `npm` | `npm ci` | `npm run build` | `npm test` | `npm run lint` |
| `yarn` | `yarn install --frozen-lockfile` | `yarn build` | `yarn test` | `yarn lint` |
| `pnpm` | `pnpm install --frozen-lockfile` | `pnpm build` | `pnpm test` | `pnpm lint` |
| `bun` | `bun install` | `bun run build` | `bun test` | `bun lint` |
| **Go** | *(none)* | `go build ./...` | `go test -race ./...` | `golangci-lint run` |
| **Rust** | *(none)* | `cargo build` | `cargo test` | `cargo clippy` |
| **Python (Poetry)** | `poetry install` | *(none)* | `poetry run pytest` | `ruff check .` |
| **Python (pip)** | `pip install -r requirements.txt` | *(none)* | `pytest` | `ruff check .` |

Use the detected language/package manager consistently in: CI templates, pre-commit hook instructions, and Step 5 verification commands.

For non-JS repos, `BUILD_TEST_COMMAND` in `ai-checks.generic.yml.tpl` is replaced with the combined build + test command for the detected language (e.g. `go build ./... && go test -race ./...`). The `setup-node` step is dropped entirely — use `agents-md-validate.sh.yml.tpl` and `docs-coverage.sh.yml.tpl` instead.

---

## DESIGN.md eligibility rules

| Project type | Emit `docs/ai/DESIGN.md` | Emit per-project `DESIGN.md` |
|---|---|---|
| React / Next / Vite / Vue / Svelte | Yes (standard: Figma + Storybook structure) | Yes (links to project Figma, Storybook) |
| Ink TUI | Yes (text-only: ASCII layout reference) | No |
| Node CLI (no UI framework) | Yes (text-only: interface description) | No |
| Pure backend / microservice | Yes (text-only: describes no visual layer) | No |

`docs/ai/DESIGN.md` is always emitted at the governance level. The `{{FIGMA_URL}}` placeholder is left for the user to fill in when applicable; for TUI/CLI, replace the Figma section with "ASCII layout reference: describe key screens using ASCII art or table layout".

---

## gitignore policy

Always leave existing entries untouched. Rules:

| Entry | Action |
|---|---|
| `CLAUDE.md` | Leave ignored; do NOT remove |
| `.claude/` | Leave ignored; do NOT remove |
| `AGENTS.md`, `AI.md`, `TESTS.md`, `docs/ai/**` | These must be TRACKED — do NOT add them to `.gitignore` |
| `tools/scripts/**` | Must be tracked — do NOT add to `.gitignore` |

---

## Severity ramp schedule (recommended)

| Stage | `aiContractPack.severity` | Trigger |
|---|---|---|
| Initial setup | `"warn"` | Default; validators pass even if AI Context Pack is incomplete |
| After pilot (1 real spec + 1 ADR merged) | `"error"` | Flip manually in `.agents-md-validator.json` |
