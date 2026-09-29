---
name: registry-promote
description: "Use when a built, reviewed component, pattern, ui-flow, token-set or agent-flow looks reusable and should be added to the shared ai-craft registry: decide whether to promote, author the entry, run the promote CLI from any repo, then validate and build."
allowed-tools: Read Grep Glob Bash
---

# Registry Promote

## Overview

Creates entries in the shared ai-craft registry (`<checkout>/packages/registry`). Complements the consume-only `craftflow:component-registry` skill. Promotion is CLI-only and always produces a `draft` entry; a human-reviewed follow-up makes it `verified`.

## The Iron Law

```
NO PROMOTE WITHOUT A REGISTRY SEARCH AND A PASSING SPEC
```

## When to promote

- The component/pattern/ui-flow/token-set is built, reviewed, and has a passing `*.spec.ts(x)`.
- It uses theme tokens (`var(--...)`), no hardcoded hex, and is plausibly reusable across projects.
- `registry_search` / `registry_list` found no matching entry. If one exists, extend it instead of duplicating.
- Not for one-offs or project-specific code.

## Protocol

1. Search first (`registry_search`); stop if a match exists. Ask the user to confirm the files and slug (kebab-case).
2. Locate the CLI: `CRAFT_REGISTRY_ROOT` in `claude mcp get ai-craft-registry` is `<checkout>/packages/registry`; otherwise ask the user for the ai-craft checkout. If `<checkout>/dist/packages/registry/cli.js` is missing, run `pnpm exec nx run registry:build-cli` in the checkout.
3. Run from any repo (`--from` repeatable, include the spec file):
   `node <checkout>/dist/packages/registry/cli.js promote --root <checkout>/packages/registry --kind <token-set|component|pattern|ui-flow|agent-flow> --name <slug> --from <file> [--from <file>...] --source <repo name> [--workflow <wf id>] [--tokens craft-dark]`
   Required: `--kind --name --from --source`. `--workflow` defaults to `manual` in `promotedFrom`. Use `--tokens` when any file uses `var(--`.
4. Exit codes: `0` created (prints `created <path>` plus draft warnings); `1` manifest/validation failure (nothing written); `2` entry name already exists (report the path, stop); `3` usage error (missing flag, unknown kind, non-kebab name, duplicate `--from` basename, file not found).
5. Complete the draft in the checkout: fill `usage.md` (no `TODO`), set `x-craft.whenToUse` (at least 40 chars), `tags`, and `description`. Layout: `registry/<kind>/<slug>/{manifest.json, usage.md, <the files passed via --from, including the spec>}`. `x-craft` = kind, framework, styling, tokens, tags, whenToUse, sourceProject, promotedFrom, status. `usage.md` is embedded as docs and never listed in `files[]`.
6. In the checkout run `pnpm registry validate --root packages/registry`, then `pnpm registry build --root packages/registry`, then `pnpm registry build --root packages/registry --check`, plus the registry tests (`npx vitest run` inside `packages/registry`, or `pnpm exec nx run registry:build-registry`, which runs tests then a report-aware build).
7. Commit on a branch in the ai-craft checkout. Never push without user consent.

## Validation rules

| Rule | Requirement |
|------|-------------|
| V1 | manifest parses against the schema |
| V2 | `name` equals directory slug; unique across kind dirs |
| V3 | `kind` equals parent dir; `type`, `framework`, `styling` match the kind |
| V4 | `usage.md` present, non-empty, no `TODO` |
| V5 | at least one `*.spec.ts` / `*.spec.tsx` in the entry |
| V6 | each `files[].path` exists, is not `usage.md`; `registry:file` needs `target` |
| V7 | a file with `var(--` needs non-null `x-craft.tokens` (token-set exempt) |
| V8 | `tokens` names another existing token-set and is in `registryDependencies`; a token-set has `tokens: null` |
| V9 | `whenToUse` at least 40 characters |

`promote` validates with `--allow-draft` (V4-TODO, V5, V9 become warnings), so a fresh draft passes. `validate --allow-draft` does the same. `build` is strict; with `--test-report`, an entry whose spec fails or is missing is downgraded to draft in generated output.

## Do not

- Edit registry files from a consumer repo, or hand-edit `registry.json` / `r/*.json`.
- Edit `~/.claude.json`; MCP registration is user-owned.
- Overwrite an existing entry, or push/merge without user consent.
- Promote code with hardcoded hex colors or no spec.

## Rationalization table

| Excuse | Reality |
|--------|---------|
| "It is probably new, skip the search" | Duplicates fragment the registry. Search costs one call. |
| "promote passed, so it is done" | promote only makes a draft. `build` fails until `usage.md` and `whenToUse` are complete. |
| "I'll fix the spec later" | V5 blocks `build`; without a passing spec the entry ships as draft. |
| "Push it so others get it" | Pushing needs explicit user consent. Commit on a branch and ask. |
