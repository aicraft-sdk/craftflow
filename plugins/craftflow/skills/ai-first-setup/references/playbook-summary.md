# Playbook Summary (AI-First Setup)

Condensed from the xfuse AI-first project setup playbook. Stack-neutral. Use as the ordering and decision rationale reference during execution.

---

## §0 — Prerequisites

Before running this playbook, confirm:
- Git repo initialized
- Node.js ≥ 18 available (scripts require `fs`, `path`, `child_process`)
- Package manager resolved (`npm`, `yarn`, `pnpm`, or `bun`)
- GitHub Actions available (or substitute CI provider)

---

## §1 — Governance docs (`docs/ai/`)

Create `docs/ai/` with these files:

| File | Purpose |
|---|---|
| `WAY_OF_WORK.md` | Hybrid human+AI delivery flow: intake → plan → build → review → release |
| `DESIGN.md` | Design system contract (Figma/Storybook for UI; ASCII layout reference for TUI/CLI) |
| `SECURITY.md` | Secrets handling, auth boundaries, AI-specific checklist |
| `TESTING.md` | TDD contract: RED→GREEN→REFACTOR; test layers; commands |
| `QUALITY.md` | Size limits, type safety, import boundaries |
| `OBSERVABILITY.md` | Logging patterns, redaction, correlation IDs |
| `PROMPTS.md` | AIDLC usage, skills/CLI, practical flow |
| `GLOSSARY.md` | Project-specific terms, AIDLC concepts |
| `RELEASE.md` | Conventional commits, PR title conventions, changelog policy |

Also create subdirectories with starter templates:
- `docs/ai/specs/template.md`
- `docs/ai/rfcs/template.md`
- `docs/ai/decisions/0001-template.md`
- `docs/plans/.gitkeep`
- `docs/research/.gitkeep`

---

## §2 — Charter (`AI_FIRST.md`)

Ten non-negotiable principles. Generic version:
1. Spec before code (feature spec in `docs/ai/specs/` before implementation)
2. Plan before build (AIDLC router produces `docs/plans/*.md`)
3. TDD (RED → GREEN → REFACTOR)
4. Smallest step (one phase at a time; valid CONTRACT envelope where required)
5. Evidence before claim (paste test/verifier output in PR)
6. Use shared libraries for cross-cutting concerns (logging, config, testing utilities)
7. Use the project's designated build system commands (not ad-hoc scripts)
8. Size limits (prefer < 400 lines per file; max 500; functions ~30–40 lines)
9. Redact PII in logs on every path that may carry user or session data
10. Design system compliance (Figma → code for UI; skip for CLI/TUI projects)

---

## §3 — Root `AGENTS.md`

Eight sections required by the validator:
1. Project / Scope — name, languages, runtime, package manager, build system, commands
2. High-Level Intent — one paragraph describing the system
3. Non-Negotiable — link to `AI_FIRST.md`; forbidden patterns; size limits
4. Build / Run / Test — exact build, test, and dev commands
5. Security — secrets policy, auth boundaries, data redaction
6. Code & Change Expectations — test requirements, PR/commit rules
7. Progressive Documentation Index — links to all `docs/ai/*`, `AI.md`, `TESTS.md`, plans, research
8. Agent Operating Rules — file is authoritative; nearest AGENTS.md wins; AI Context Pack link

**Hard constraints:**
- ≤ 8 KB (enforced by `agents-md-lint.js`)
- No `/src/`, `/lib/`, `/components/` literals
- Subfolder AGENTS.md: overrides only, never repeating root section titles

---

## §4 — AI Context Pack (per project or repo root)

For each project (or for the repo root in single-app shape):

| File | Required | Condition |
|---|---|---|
| `AI.md` | Yes | Always |
| `TESTS.md` | Yes | Always |
| `DESIGN.md` | Conditional | UI projects only (react/next/vite/vue/svelte) |

`AI.md` covers: purpose, public interface, events/errors, dependencies, SLOs, vocabulary, gotchas.
`TESTS.md` covers: test commands, layout, layers, coverage expectations.
`DESIGN.md` covers: Figma link, Storybook entry, component notes (UI) — or ASCII layout reference (TUI).

---

## §5 — Config files

| File | Purpose |
|---|---|
| `project-config.json` | Project identity, tech stack, `resources.sets: ["aidlc"]` |
| `.agents-md-validator.json` | Validator config: `maxSizeBytes`, `requiredSections`, `forbiddenPatterns`, `aiContractPack` |
| `.github/pull_request_template.md` | Mandatory PR sections: Spec, Plan, Verification |

---

## §6 — Validator scripts

Copy verbatim from `assets/tools/`:
- `tools/scripts/agents-md-lint.js` — validates AGENTS.md: size, required sections, forbidden patterns, subfolder-override contract
- `tools/scripts/ai-contract-pack-lint.js` — validates AI Context Pack completeness per scanRoot

Both are zero-dependency Node scripts. Do not modify them.

---

## §7 — Nx generators (SKIP for non-Nx repos)

The playbook defines Nx workspace generators for scaffolding new projects. This section is skipped entirely when `nx.json` is not present. The adaptation rule is built into the playbook itself.

---

## §8 — AIDLC anchors

Write minimal memory seeds to `.cursor/aidlc/v10/`:
- `activeContext.md` — `AUTO_PROCEED: false`, empty Current Focus
- `patterns.md` — empty User Standards, Code Conventions, Common Gotchas
- `progress.md` — `Current Workflow: BUILD`, empty Tasks

If `.cursor/` appears in `.gitignore`, add narrow negation `!.cursor/aidlc/` before writing.
`CLAUDE.md` and `.claude/` are NEVER un-ignored.

---

## §9 — Husky pre-commit

Add `husky` to `devDependencies` and `"prepare": "husky"` to `scripts` in `package.json`.
Write `.husky/pre-commit` with:
```sh
node tools/scripts/agents-md-lint.js
node tools/scripts/ai-contract-pack-lint.js
```
Run the package manager install command to initialize Husky.

---

## §10 — CI workflows

| File | Trigger | Purpose |
|---|---|---|
| `agents-md-validate.yml` | PR + push to main | Runs `agents-md-lint.js` |
| `docs-coverage.yml` | PR + push to main | Runs `ai-contract-pack-lint.js` |
| `ai-checks.yml` | PR + workflow_dispatch | Build + lint (Nx variant or generic) |

---

## §11 — Verification order

1. `node tools/scripts/agents-md-lint.js` → exit 0
2. `node tools/scripts/ai-contract-pack-lint.js` → exit 0
3. Project build command → exit 0
4. Project test command → exit 0
5. `git check-ignore CLAUDE.md` → reports ignored
6. `git status` on new files → shows untracked (not ignored)
