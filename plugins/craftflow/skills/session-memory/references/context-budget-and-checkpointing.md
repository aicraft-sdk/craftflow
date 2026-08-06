# Context Budget And Checkpointing

## Purpose

CRAFTFLOW works best when durable artifacts carry the load and the conversation stays focused.
This reference adapts the best context-budget ideas from the comparison set to CRAFTFLOW's
memory-first workflow.

## Universal Rules

1. Load durable context before trusting chat history.
2. Prefer the current plan, current research files, and current memory sections over long
   historical rereads.
3. Prefer file paths, headings, and contract fields over inlining large bodies.
4. Keep SKILL.md files lean; read `references/` only when needed.
5. Checkpoint into workflow artifacts, plan/research docs, and memory notes before the
   session gets fragile.

## Read-Depth Guidance

| Situation | Preferred depth |
|-----------|-----------------|
| current task needs one decision | read the relevant memory section only |
| plan verification | read the current plan phase and cited files |
| research synthesis | read the saved research file, not the whole browser transcript |
| long task with many touched areas | re-open the durable artifact instead of relying on recall |

## Degradation Tiers

These tiers are self-assessed (qualitative) — they still matter when `tokentracker` isn't
installed, or for agents without direct token visibility. When a measured context % *is*
available (via `craftflow status`'s `ctx:NN%` segment, `--json`'s `context_usage`, or
`--verbose`'s "context used" line — all best-effort, sourced from `tokentracker context
--json`), treat it as quantitative corroboration of the tier, not a replacement for it.

| Tier | Signal | Behavior | Quantitative corroboration (when measured ctx% is available) |
|------|--------|----------|----------------------------------------------------------|
| clear | context feels crisp | normal operation | ctx% below 70% |
| warming | many file reads / growing chat | tighten reads and favor references | ctx% at or above 70% |
| degrading | vague thinking, skipped checks, repeated rereads | checkpoint immediately | ctx% at or above 90% |
| fragile | contradictory recall or missing thread | stop expanding scope; restore from durable artifacts | ctx% at or above 90%, treat as fragile regardless of subjective signals |

## Early Warning Signs

- success language gets vaguer
- protocol steps start getting skipped
- "I think" replaces specific evidence
- the same files are being reread because the durable summary is missing
- agent outputs or plan details start blending together

## Checkpoint Triggers

Checkpoint now when you notice:

- extended debugging or repeated failed hypotheses
- long planning or research sessions
- large multi-file refactors
- many tool calls in one session
- multiple long artifact reads
- approaching a risky handoff or compaction boundary

## What To Checkpoint

- workflow state -> workflow artifact
- detailed design/research -> `docs/plans/*` or `docs/research/*`
- durable summary -> `MEMORY_NOTES`
- verification truth -> `progress.md ## Verification` via router finalization

## Safe User Warning

If you need to warn about rising context pressure, say it plainly:

`Context is getting heavy. I’m checkpointing the durable state so we don’t lose the thread.`

That is better than silently continuing with fuzzy recall.
