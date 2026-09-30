---
name: component-registry
description: "Use before writing any UI component, token file, layout pattern, or multi-step UI flow: search the ai-craft registry via the ai-craft-registry MCP tools, reuse and cite matching entries, and list promotion candidates after the build."
allowed-tools: Read Grep Glob
---

# Component Registry

## Overview

The ai-craft registry holds shadcn-style entries: token sets, components, patterns, UI flows, and agent flows.
Read-only MCP tools: `registry_search`, `registry_get`, `registry_list` (Claude Code names: `mcp__ai-craft-registry__registry_*`).
Promotion of new entries is CLI-only (`promote`); follow `craftflow:registry-promote` for when and how, offered at finish by `finishing-a-development-branch`.

## The Iron Law

```
NO NEW UI COMPONENT BEFORE A REGISTRY SEARCH
```

## Protocol

1. Call `registry_search({ query, kind, framework: 'react' })` with 2-4 domain words from the request. Pass `framework: 'react'` only for `component`, `pattern` or `ui-flow`; omit `framework` for `token-set` and `agent-flow` (their framework is `none`, so combining them with `framework: 'react'` returns no hits).
2. If a hit's `whenToUse` matches, call `registry_get` for it AND for its `x-craft.tokens` token set.
3. Check `x-craft.framework` and `x-craft.styling` against the target `package.json` and DESIGN.md. Adapt token names only through a wrapper; never edit registry token names.
4. Copy the entry's files to its `target` paths (or the project's component dir), keep the spec, and run it.
5. Cite the entries used in the build summary under `Registry entries used:`.
6. After the build, list new components under `Promotion candidates:` (name, files, suggested kind); promote per `craftflow:registry-promote`.

## Degraded mode

If the MCP tools are not listed, or return the `registry.json not found` error, state once in the summary: "ai-craft registry unavailable: <reason>" and continue without it. Never silently skip. Never edit `~/.claude.json` to register the server; registration is user-owned.

## Do not

- Modify registry entries from a consumer repo.
- Hand-write a component that a `verified` hit already provides.
- Drop the entry's spec when copying it.

## Rationalization table

| Excuse | Reality |
|--------|---------|
| "I know what the component looks like" | The registry entry carries tokens, a11y, and a spec you will not reproduce from memory. Search first. |
| "The registry is probably empty for this" | Guessing is not searching. One `registry_search` call costs less than a duplicate component. |
| "I'll cite it later" | Later never happens. Record `Registry entries used:` in the summary now. |
