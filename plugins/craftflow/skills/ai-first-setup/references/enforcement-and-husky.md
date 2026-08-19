# Enforcement and Husky

How validator scripts work, how to wire pre-commit hooks (Husky for JS, shell hook for non-JS), and the postinstall no-op guard.

---

## Validator scripts — choosing the right variant

The skill ships **two equivalent validator pairs**. Choose by repo shape:

| Repo shape | Validators to emit | Pre-commit hook |
|---|---|---|
| JS / TS (has `package.json`) | `agents-md-lint.js` + `ai-contract-pack-lint.js` | Husky (existing) |
| Go / Rust / Python / non-JS | `agents-md-lint.sh` + `ai-contract-pack-lint.sh` | `.git/hooks/pre-commit` shell hook |

The `.sh` variants have a parity caveat: `designPathPatterns` and `exemptions` from `.agents-md-validator.json` are not enforced. Use the Node variants when those features are needed (e.g., on a JS repo where a non-JS module lives in a subfolder).

---

## Validator scripts overview

### Node variants (JS repos)

Both scripts are zero-dependency Node.js scripts. They use only built-in modules (`fs`, `path`, `child_process`). Copy them verbatim from `assets/tools/` into `tools/scripts/` in the target repo.

### `agents-md-lint.js`

Validates every `AGENTS.md` file found by recursively walking the repo (skipping `.git`, `node_modules`, `.next`, `dist`, `build`, `.cursor`, and gitignored paths).

| Check | Root file | Subfolder file |
|---|---|---|
| File exists | Must exist | n/a (only validates if found) |
| Size ≤ 8 KB | Yes | Yes |
| Required sections | Yes (all 5) | No |
| Forbidden patterns | Yes | Yes |
| No root section redefinition | No | Yes |

Exit 0 on pass. Exit 1 on first violation (fail-fast with error message to stderr).

### `ai-contract-pack-lint.js`

Scans each directory in `aiContractPack.scanRoots`. For each first-level subdirectory that contains `AGENTS.md` (and is not in `exemptions`), checks for required Context Pack files.

> **Single-app note:** `scanRoots: ["."]` causes the linter to scan the immediate subdirectories of the repo root (e.g. `mac/`, `src/`, `.github/`), NOT the root itself. A root-level `AGENTS.md` at `<repo>/AGENTS.md` is NOT checked by this script. For single-app repos, the `agents-md-lint.js` script validates the root `AGENTS.md` independently (it walks all `AGENTS.md` files). The contract pack linter's role is to verify subdirectories (like `mac/`) have their own context packs when required. Always write `.agents-md-validator.json` — do not rely on DEFAULT_CONFIG fallbacks.

| Severity | `"warn"` | `"error"` |
|---|---|---|
| Exit code | Always 0 | 1 if any files missing |
| Output | Issues printed to stderr | Issues printed to stderr |

Configuration is read from `.agents-md-validator.json` at the repo root.

### Shell variants (non-JS repos)

**`agents-md-lint.sh`** (`assets/tools/agents-md-lint.sh`) — POSIX-safe bash replication of `agents-md-lint.js`. Same checks: root exists, ≤ 8 KB, 5 required sections, no forbidden literals (`/src/`, `/lib/`, `/components/`), subfolder files may not repeat root section titles. Uses `find`, `grep`, and `wc -c`; calls `git check-ignore --quiet` to skip ignored paths. Requires bash 3.2+ (macOS default) and standard POSIX tools.

**`ai-contract-pack-lint.sh`** (`assets/tools/ai-contract-pack-lint.sh`) — Replicates the core contract-pack check: reads `severity` and `scanRoots` from `.agents-md-validator.json` (via `python3` + sed fallback), scans first-level project dirs under each scanRoot, requires `AI.md` + `TESTS.md` per dir that contains `AGENTS.md`. Uses `python3` for JSON parsing when available; sed/awk fallback otherwise. `severity=warn` → exit 0; `severity=error` → exit 1 if issues found.

Copy them from `assets/tools/` into `tools/scripts/` and make them executable:

```bash
chmod +x tools/scripts/agents-md-lint.sh
chmod +x tools/scripts/ai-contract-pack-lint.sh
```

---

## Husky wiring (JS repos only)

### New installation (Husky not yet present)

1. Add `husky` to `devDependencies` in `package.json`
2. Add `"prepare": "husky"` to the `scripts` section in `package.json`
3. Write `.husky/pre-commit` from `assets/husky/pre-commit-snippet`
4. Run the package manager install command to initialize Husky: `npm install` / `yarn install` / `pnpm install`

### Pre-existing Husky installation

Append the validator calls to the existing `.husky/pre-commit` file:

```sh
node tools/scripts/agents-md-lint.js
node tools/scripts/ai-contract-pack-lint.js
```

Do not overwrite the existing pre-commit file; append to it.

### `.husky/pre-commit` content

```sh
#!/usr/bin/env sh
. "$(dirname "$0")/_/husky.sh"

node tools/scripts/agents-md-lint.js
node tools/scripts/ai-contract-pack-lint.js
```

Husky v9+ does not require the `. "$(dirname "$0")/_/husky.sh"` line; if the project uses Husky v9+, the script can be a plain shell script. Check the installed Husky version.

---

## Pre-commit hook for non-JS repos

For Go/Rust/Python repos (no `package.json`, no Husky), emit a plain shell hook:

```bash
# Write the hook
cp assets/hooks/pre-commit-nonjs .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

### `.git/hooks/pre-commit` content

```sh
#!/usr/bin/env sh
# AI-first pre-commit hook for non-JS repos (no Husky dependency).
bash tools/scripts/agents-md-lint.sh
bash tools/scripts/ai-contract-pack-lint.sh
```

This hook is **not committed to the repo** (`.git/` is never tracked). New contributors must re-run the skill or manually copy the hook after cloning. Document this in the project `CONTRIBUTING.md` or `AGENTS.md` if needed.

No `package.json` changes are needed for non-JS repos — do not add Husky, `prepare`, or `postinstall` script entries.

---

## `postinstall-ai-resources.sh` — generic no-op stub (JS repos only)

`postinstall-ai-resources.sh` is a **generic stub** — it simply prints a message and exits 0. It is wired into `package.json` as the `postinstall` script so that future providers can be plugged in without changing the wiring.

```bash
#!/usr/bin/env bash
echo "AI resources postinstall: no provider configured — skipping."
exit 0
```

This script is **only written for JS repos** (where `package.json` is patched). Non-JS repos have no `postinstall` hook.

### Wiring in `package.json` (JS repos only)

```json
{
  "scripts": {
    "prepare": "husky",
    "postinstall": "tools/scripts/postinstall-ai-resources.sh"
  }
}
```

Make the script executable after writing it:

```bash
chmod +x tools/scripts/postinstall-ai-resources.sh
```

---

## CI enforcement

The CI workflows run the same scripts as the pre-commit hook, ensuring violations are caught even if the hook is bypassed locally.

| Repo shape | `agents-md-validate` CI | `docs-coverage` CI |
|---|---|---|
| JS repo | `agents-md-validate.yml.tpl` (uses `setup-node` + `node …`) | `docs-coverage.yml.tpl` |
| Non-JS repo | `agents-md-validate.sh.yml.tpl` (no `setup-node`; uses `bash …`) | `docs-coverage.sh.yml.tpl` |

See `references/ci-workflow-templates.md` for the full workflow YAML of all four templates.

---

## Manual invocation

**JS repos:**

```bash
node tools/scripts/agents-md-lint.js
node tools/scripts/ai-contract-pack-lint.js
```

**Non-JS repos:**

```bash
bash tools/scripts/agents-md-lint.sh
bash tools/scripts/ai-contract-pack-lint.sh
```

Both variants use the current working directory as the repo root — always run from the repo root.
