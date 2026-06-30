---
name: handoff-package
description: "Use when handing unfinished work to a coworker, a different tool, or a fresh session — produce a portable, secrets-redacted handoff doc that references artifacts by path, not by pasted content."
allowed-tools: Read Grep Glob Bash
---

# Handoff Package

## Overview

A handoff package is a single portable Markdown doc that lets a DIFFERENT picker-upper
continue half-done work: a coworker, a different CLI/tool, or a clean session with no craftflow
router present.

craftflow's own resume is structural — state lives in `.craftflow/state/` and the router rehydrates it.
But that artifact is craftflow-internal and tool-bound: it assumes the same router runs in
the same tool. The handoff package is the PORTABLE, human-readable export for when the next
reader is NOT this craftflow session.

**The package references artifacts by path; it is not a transcript and not a copy.**

## When This Fires Vs. session-memory

| Audience | Use |
|----------|-----|
| A resumed craftflow router in THIS tool | `session-memory` — durable `.craftflow/state/` state, no export needed |
| A native compaction mid-session | `session-memory` compaction rubric — keeps state alive in-tool |
| A coworker, a DIFFERENT tool, or a no-craftflow session | this skill — a portable export |

They pair but serve different audiences. `session-memory` keeps craftflow's own resume alive;
this skill builds the bridge OUT to a reader who cannot read `.craftflow/state/` orchestration state.

## The Iron Law

```
THE HANDOFF DOC MUST:
1. WRITE to the OS temp dir, never into the repo
2. REFERENCE artifacts by path/URL, never paste their contents
3. REDACT secrets and PII before writing a single line
```

If you write it into the repo, it becomes accidental committed noise.
If you paste artifact contents, it goes stale and bloats the read.
If you skip redaction, you leak a token into a file that gets emailed around.

## Where It Goes (Repo-EXTERNAL)

Write the doc to the OS temp dir, NOT into the working tree. A handoff is transient
coordination, not a committed artifact.

```bash
HANDOFF="${TMPDIR:-/tmp}/handoff-$(date +%Y%m%d-%H%M%S).md"
```

Write the doc to `$HANDOFF`, then PRINT the absolute path so the next reader can find it.
Do not create it under the repo root, `.craftflow/state/`, or `docs/`.

## Reference By Path, Not Duplication

The next reader has the repo and the artifacts. They need a map, not a copy.

Reference these BY PATH/URL — never paste their bodies:

- the durable workflow artifact: `.craftflow/state/workflows/{wf}.json` (craftflow-internal; name it so a
  resumed craftflow session can rehydrate, but the portable reader treats it as opaque)
- the craftflow memory index: `.craftflow/state/project/activeContext.md`, `patterns.md`, `progress.md`
- plan / design / research docs: `docs/plans/*`, `docs/research/*`
- the diff package or branch/PR URL
- the source files in play

Prefer behavioral descriptions over `file:line` where the code may still drift —
durability over precision. "the dedupe key is built in the work-item save path" survives a
refactor; `store.py:456` does not. Use a line number only when it is the sole stable locator.

## Redact Before You Write

Never write tokens, API keys, connection-strings, credentials, or PII into the handoff doc.
This aligns with `session-memory`'s redact-before-persist rule for outward artifacts — and a
temp-dir handoff that gets shared is exactly an outward artifact.

- Replace any secret with a stable placeholder: `<redacted:secret>`, `<redacted:pii>`.
- Keep the surrounding constraint intact ("auth uses `<redacted:secret>` from the env").
- When unsure whether a value is sensitive, redact it. The next reader has the real source.

## Contents

Keep it tight. A reader should reconstruct the current state and the exact next move without
re-reading any transcript.

| Section | Holds |
|---------|-------|
| Goal | the one-line objective of the work, verbatim user intent if stated |
| Current state | where things stand right now, in one short paragraph |
| What's done | reference the ledger / artifacts by path; do not re-narrate the work |
| What's next | the EXACT next step — concrete enough to start cold |
| Open blockers / failures | unresolved errors VERBATIM, failing commands, dead ends tried |
| Suggested skills / tools | which skills the next agent should invoke to continue |

The "suggested skills/tools" pointer is load-bearing: tell the next agent how to continue
(e.g. invoke `craftflow-router` for multi-step work if craftflow is present; otherwise name the
domain skills — debugging, planning, review — that fit the remaining work). If craftflow is not
in the next tool, say so and point at the plan/research docs instead of the router.

## Template

```markdown
# Handoff: <short title>

## Goal
<one line; verbatim user intent if it was stated>

## Current State
<one short paragraph — where things stand>

## What's Done
- <done item> — see <path/URL>
- durable state: .craftflow/state/workflows/{wf}.json (craftflow-internal)

## What's Next
1. <exact next step, concrete enough to start cold>

## Open Blockers / Failures
- <error VERBATIM> from `<command>`
- tried: <dead end>, did not work because <reason>

## Suggested Skills / Tools
- If craftflow is present: invoke `craftflow-router` to continue <build/debug/review/plan>.
- Otherwise: <domain skill names>; read <plan/research doc path> first.

## Artifacts (by path)
- plan: docs/plans/<file>
- diff/PR: <branch or URL>
- key source: <path> — <behavioral description, not line number>
```

## Red Flags

Stop and correct course if you catch yourself:

- writing the handoff doc into the repo, `.craftflow/state/`, or `docs/`
- pasting the contents of a plan, diff, or source file instead of referencing it
- writing a token, key, connection-string, or PII into the doc
- narrating the whole session instead of stating state + next move
- omitting the suggested-skills pointer, leaving the next agent without a way in
- pinning everything to `file:line` where the code is still changing

## Verification Checklist

- [ ] Doc written to `$TMPDIR`/`/tmp`, not the repo; absolute path printed
- [ ] Artifacts referenced by path/URL, not pasted
- [ ] Secrets and PII redacted with stable placeholders
- [ ] Goal, current state, what's done, what's next, blockers all present
- [ ] Exact next step is concrete enough to start cold
- [ ] Suggested skills/tools pointer included for the next agent

Cannot check these boxes? The handoff is not portable yet.
