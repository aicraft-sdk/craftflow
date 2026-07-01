# Memory File Contracts

## Contents

- Purpose
- Required files and sections
- Stable anchors
- Templates
- Auto-heal rule

## Purpose

CRAFTFLOW memory files are edit anchors, not casual notes. The router and audits rely on stable
headings and file purposes.

## Required Files And Sections

| File | Required sections |
|------|-------------------|
| `activeContext.md` | `## Current Focus`, `## Recent Changes`, `## Next Steps`, `## Decisions`, `## Learnings`, `## References`, `## Blockers`, `## Session Settings`, `## Last Updated` |
| `patterns.md` | `## User Standards`, `## Common Gotchas`, `## Project SKILL_HINTS`, `## Last Updated` |
| `progress.md` | `## Current Workflow`, `## Tasks`, `## Completed`, `## Verification`, `## Last Updated` |

## Stable Anchors

Use these when editing memory:

| Anchor | File | Stability |
|--------|------|-----------|
| `## Recent Changes` | `activeContext.md` | guaranteed |
| `## Learnings` | `activeContext.md` | guaranteed |
| `## References` | `activeContext.md` | guaranteed |
| `## Common Gotchas` | `patterns.md` | guaranteed |
| `## Project SKILL_HINTS` | `patterns.md` | guaranteed |
| `## Completed` | `progress.md` | guaranteed |
| `## Verification` | `progress.md` | guaranteed |
| `## Last Updated` | all files | guaranteed fallback |

Do not anchor on:

- checkbox text
- table headers
- optional prose blocks
- temporary debug notes

## Templates

### activeContext.md

```md
# Active Context
<!-- CRAFTFLOW: Do not rename headings. Used as Edit anchors. -->

## Current Focus
[Active work]

## Recent Changes
- [Change]

## Next Steps
1. [Step]

## Decisions
- [Decision]: [Choice] - [Why]

## Learnings
- [Insight]

## References
- Plan: `docs/plans/...` (or N/A)
- Design: `docs/plans/...` (or N/A)
- Research: `docs/research/...` (or N/A)
- [craftflow-internal] memory_task_id: [task id] wf:[workflow id]

## Blockers
- [None]

## Session Settings
# AUTO_PROCEED: false

## Last Updated
[timestamp]
```

**Note:** Bullets in `## Learnings` and `## Common Gotchas` may carry an optional `(conf: x)` suffix
written by the memory finalizer (e.g., `- Node 22 required (conf: 0.9)`). The parser tolerates
absence of the suffix (back-compat). Do not rename these headings.

### patterns.md

```md
# Project Patterns
<!-- CRAFTFLOW MEMORY CONTRACT: Do not rename headings. Used as Edit anchors. -->

## User Standards
- [Non-negotiable project rule]

## Common Gotchas
- [Gotcha]: [Solution]

## Project SKILL_HINTS
- [full-skill-id]

## Last Updated
[timestamp]
```

**Note:** Bullets in `## Learnings` and `## Common Gotchas` may carry an optional `(conf: x)` suffix
written by the memory finalizer (e.g., `- Node 22 required (conf: 0.9)`). The parser tolerates
absence of the suffix (back-compat). Do not rename these headings.

### progress.md

```md
# Progress Tracking
<!-- CRAFTFLOW: Do not rename headings. Used as Edit anchors. -->

## Current Workflow
[PLAN | BUILD | REVIEW | DEBUG]

## Tasks
- [ ] Task 1
- [x] Task 2 - evidence

## Completed
- [x] Item - evidence

## Verification
- `command` -> exit 0

## Last Updated
[timestamp]
```

## Auto-Heal Rule

If a canonical section is missing:

- insert it just above `## Last Updated`
- verify the insertion with `Read(...)`
- retry the intended edit only after the contract shape is restored

Never rename canonical headings to make one edit easier. The headings are part of the
durable CRAFTFLOW protocol.
