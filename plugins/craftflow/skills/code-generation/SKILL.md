---
name: code-generation
description: "Internal skill. Use craftflow-router for all development tasks."
allowed-tools: Read Grep Glob Write Edit LSP
user-invocable: false
---

# Code Generation

**Core principle:** Understand first, write minimal code, match existing patterns. Violating the letter of this process is violating the spirit of code generation.

## The Iron Law

```
NO CODE BEFORE UNDERSTANDING FUNCTIONALITY AND PROJECT PATTERNS
```

If you haven't answered the Universal Questions, you cannot write code.

## Universal Questions (Answer Before Writing)

1. **Functionality?** What must the code DO?
2. **Users?** Who uses it, in what flow?
3. **Inputs?** What data, what formats?
4. **Outputs?** Returns and side effects?
5. **Edge cases?** What can go wrong; how is it handled?
6. **Patterns?** How does the codebase do similar things?
7. **Read the files?** Never propose changes to code you haven't opened.
8. **Simpler approach?** If yes, present both, recommend the simpler.

## Minimal implementation (stop at the first rung that holds)

Needs to exist at all (YAGNI)? > already in this codebase (reuse)? > stdlib? > native platform feature? > installed dependency (never add one for a few lines)? > one line? > only then the minimum code. Prefer editing existing files. Match naming, imports, exports, types, error and logging patterns. Handle empty/invalid input, errors, boundaries.

**Minimal diffs:** only change what is necessary; no drive-by refactors. Before editing, grep every caller of the function you touch and fix the shared function once, not each path.

## Red Flags - STOP and go back to the Universal Questions

Code before answering them; unrequested features; ignoring project patterns; happy path only; abstraction for one use case (rule of three); unrequested config options; magic numbers; comments instead of clear code; multiple valid approaches not presented.

## Read on demand (one reference per trigger)

- UI/API/business-logic/DB follow-up questions, LSP lookups, pattern study, full minimal-implementation ladder, `cf:shortcut:` convention, clarity and edge-case rules: `references/process-and-context.md`
- When choosing between approaches or deciding whether to abstract, and before completing (run the checklist): `references/options-abstraction-checklist.md`
- Writing the implementation report or needing example function/component/error-handling shapes: `references/output-and-patterns.md`
