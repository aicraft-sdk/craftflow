# AI Context Pack Guide

An AI Context Pack is a set of files that give AI agents authoritative, project-specific context for a single project (or the repo root for single-app repos). It is distinct from the root `AGENTS.md`, which provides repo-wide operating rules.

---

## Components

| File | Required | Condition |
|---|---|---|
| `AI.md` | Yes | All projects |
| `TESTS.md` | Yes | All projects |
| `DESIGN.md` | Conditional | UI projects only (react/next/vite/vue/svelte); TUI/CLI use text-only variant at `docs/ai/DESIGN.md` |

---

## `AI.md` — Project context doc

Purpose: give AI agents the key facts about a project's public interface, data model, dependencies, and gotchas.

### Required sections

1. **Purpose** — one paragraph; what the project does and why it exists
2. **Public Interface** — `{{INTERFACE_TYPE}}`: HTTP REST / gRPC / CLI subcommands / exported module API; list the main entry points
3. **Events** — async events emitted or consumed (omit section if none)
4. **Errors** — error categories and how they surface (HTTP status codes, CLI exit codes, exception types)
5. **Dependencies** — key runtime dependencies and what they are for; note any with restricted version ranges
6. **SLOs** — performance or availability expectations (read-only? stateless? latency targets?)
7. **Vocabulary** — project-specific terms not covered in the repo-level glossary
8. **Gotchas** — known foot-guns: version requirements, ESM/CJS interop, env var format, platform-specific behavior

### Placeholder tokens

Use `{{PROJECT_NAME}}`, `{{INTERFACE_TYPE}}`, `{{TECH_STACK}}` for values to be filled in by the user.

---

## `TESTS.md` — Test context doc

Purpose: give AI agents the exact commands and conventions for running tests, so they never guess.

### Required content

- **Test commands**: copy-paste ready commands for unit, integration, e2e (as applicable)
- **Test layout**: where test files live relative to the project root
- **Test layers**: which layers exist (unit, integration, e2e, contract) and what they cover
- **PR gates**: which test targets must pass before merge
- **Coverage expectations**: thresholds if defined; otherwise state "follow team convention"
- **Fixtures**: where test fixtures live; how to add new ones

---

## `DESIGN.md` (per-project, UI only)

Purpose: give AI agents the design system contract for a specific UI project.

### Required content

- **Figma link**: `{{FIGMA_URL}}` — the Figma file for this specific project
- **Storybook**: URL or run command for the local Storybook instance
- **Token source**: where design tokens come from (shared library name)
- **Component notes**: any project-specific component conventions or overrides
- **Figma → React workflow**: reference the project's approved scaffold path

For TUI/CLI projects: the per-project `DESIGN.md` is not emitted. The `docs/ai/DESIGN.md` governance doc contains the project-level ASCII layout reference.

---

## Location conventions

| Repo shape | AI.md location | TESTS.md location |
|---|---|---|
| Single-app | Repo root: `AI.md` | Repo root: `TESTS.md` |
| Nx-monorepo (project) | `platform/<name>/AI.md` or `libs/<name>/AI.md` | `platform/<name>/TESTS.md` |
| Generic-monorepo (package) | `packages/<name>/AI.md` | `packages/<name>/TESTS.md` |

The root `AGENTS.md` §8 (Agent Operating Rules) must link to the AI Context Pack files.

---

## Validator behavior (`ai-contract-pack-lint.js`)

The linter scans each directory in `aiContractPack.scanRoots` for first-level subdirectories that contain `AGENTS.md`. For each one found (that is not in the exemptions list), it requires:

- `AI.md` — always
- `TESTS.md` — always
- `DESIGN.md` — only if the directory matches a `designPathPatterns` rule

**For single-app repos**: `scanRoots: ["."]` causes the linter to look for `AGENTS.md` in the root (which exists). It then checks for `AI.md` and `TESTS.md` at the root. Do not add `designPathPatterns` for TUI/CLI projects.

**Severity**: defaults to `"warn"` (exit 0 even with missing files). Ramp to `"error"` after the first real Context Pack is complete.

---

## Linking from AGENTS.md

Include this sub-section in AGENTS.md §8 (Agent Operating Rules):

```markdown
### AI Context Pack
- Purpose and interface: `AI.md`
- Test commands and layout: `TESTS.md`
```

For monorepo projects with per-project AGENTS.md files, the subfolder AGENTS.md links to the sibling `AI.md` and `TESTS.md` in the same directory.
