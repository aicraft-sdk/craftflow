---
name: skill-distillation
description: >-
  Use when authoring a staged skill proposal from a gate-eligible
  skill-candidate ledger entry. Applies the anti-slop rubric (dedup check,
  already-documented check, executable-artifact check) and writes a proposal
  to staging only — never directly to .claude/skills/ or .cursor/skills/.
allowed-tools: Read, Edit, Write, Bash, Grep, Glob
user-invocable: false
---

# skill-distillation

## Overview

Craftflow's project-skill distillation feature exists to turn recurring, hard-won
lessons (things that showed up across two or more independent workflows) into
durable, project-local skills — without producing skill slop. Writing nothing is
the default outcome. Just as diff-driven-docs treats `IMPACT_LEVEL: none` as a
passing skip, skill-distillation treats `STATUS: SKIPPED` as a passing state, not
a failure: most candidates that reach this skill will, and should, be skipped.

This skill is loaded by the `skill-author` agent, dispatched by the router's
`skill-distill` phase after a candidate crosses the recurrence gate in
`craftflow_skill_ledger.py` (Phase 1). It never runs unconditionally — the gate
already filtered to `distinct_workflows >= 2` before this skill is ever invoked.

## Trigger Classifier

Run this before opening any rubric or dedup logic:

| Condition | Action |
|---|---|
| No candidate id was passed in the task, or the referenced ledger entry does not exist | `STATUS: SKIPPED`, `SKIP_REASON: "no candidate provided or candidate id not found in ledger"` |
| Candidate exists but `status` is already `proposed`, `promoted`, or `rejected` | `STATUS: SKIPPED`, `SKIP_REASON: "candidate status is already <status>; nothing to author"` |
| Candidate exists, `status: candidate`, and `distinct_workflows >= 2` | Proceed to Dedup Check below |

The recurrence gate (`distinct_workflows >= 2`) and the occurrence-counting
discipline (distinct `workflow_uuid`s, not raw events) are Phase 1's job,
already enforced by `craftflow_skill_ledger.py`'s `gate_eligible()` before a
candidate ever reaches this skill. Do not re-derive or re-check either — trust
the ledger's `status` and `distinct_workflows` fields as given.

## Dedup Check (before the rubric)

`Glob` these three locations and compare each result's `description:` frontmatter
against the candidate's surface/signature for semantic overlap (not just exact
string match — a close paraphrase of the same lesson counts as overlap):

1. `.claude/skills/*/SKILL.md`
2. `.cursor/skills/*/SKILL.md`
3. `tools/craftflow-plugin/plugins/craftflow/skills/*/SKILL.md` (this plugin's own skills — compare frontmatter `description:` only; never open or read the body of another skill's `SKILL.md` per the router's Hard Rule that agents must never read internal skill files belonging to other agents/skills)

Any of these three directories may not exist yet in a given project (e.g. no
skill has ever been promoted). Treat a missing directory as zero results, not an
error — `Glob` returning nothing is the expected common case, not a failure.

- **No overlap found:** this is a candidate NEW skill. Proceed to the rubric.
- **Overlap found:** this is an UPDATE to an existing skill, not a new file.
  Proceed to the rubric anyway (the rubric still applies to updates), but when
  writing the proposal, write a `SKILL.patch`-style update proposal against the
  existing file instead of a brand-new `SKILL.md`.

## The Rubric

Apply `references/rubric.md` in order, rules 1 through 4. Rules 1 (occurrence
counting) and 2 (gate threshold) are already satisfied by construction by the
time a candidate reaches this skill — the rubric documents this so the reasoning
is auditable, but does not re-check them. Rules 3 (already-documented) and 4
(no executable artifact) are real checks performed here, in order, and either
one failing is a `STATUS: SKIPPED` outcome.

**Any rubric rule fails → stop immediately.** Emit `STATUS: SKIPPED` with a
`SKIP_REASON` naming which rule (3 or 4) failed and why, quoting the specific
overlap or the specific evidence-shape gap. Do not continue to staging.

## Staging Write (the only write this skill ever performs)

**This skill, and the `skill-author` agent that loads it, never write to
`.claude/skills/` or `.cursor/skills/` under any circumstance.** Those are the
canonical, promoted-skill locations, and only `craftflow_skill_promote.py` —
run later, only on explicit user approval — is authorized to write there.

The only write target for a passing candidate is:

```
.craftflow/state/project/skill-proposals/<candidate-id>/SKILL.md       (new skill)
.craftflow/state/project/skill-proposals/<candidate-id>/SKILL.patch     (update, if dedup found overlap)
.craftflow/state/project/skill-proposals/<candidate-id>/PROPOSAL.md     (evidence trail + rationale, always)
```

`PROPOSAL.md` must state: which candidate id, which workflows contributed
evidence, why it passed rules 3 and 4 (what was checked, what was found), and
whether it is a NEW skill or an UPDATE to an existing one (naming the existing
file if so).

## Skip Contract

`STATUS: SKIPPED` is a passing state, exactly like diff-driven-docs' `IMPACT_LEVEL: none`.
A `skill-distill` phase that skips on most runs is the system working as
designed, not a gap. Never treat a run of consecutive skips as evidence that
something is broken — recurrence-gated distillation is rare by construction
(the plan's own backtest calibration found only 2-5 gate-eligible candidates
across ~140 real workflow logs in this repo).

## Router Integration

Loaded by the `skill-author` agent, dispatched by the router's `skill-distill`
phase (after `build-doc-sync`, before `memory-finalize`). The agent emits a
`### Router Contract (MACHINE-READABLE)` YAML block the router validates before
advancing to memory finalization and, on `STATUS: COMPLETE`, surfaces the
proposal to the user via `AskUserQuestion` for approval.

**Opt-out:** `SKILL_DISTILL: skip` in a project's `## Session Settings` disables
this phase entirely, mirroring `DIFF_DRIVEN_DOCS: skip`.

## Rationalization Table

| Common excuse | Counter |
|---|---|
| "The lesson is close enough to an existing gotcha, propose it anyway" | Close paraphrase counts as overlap under rule 3. Skip it — re-encoding is pure duplication. |
| "Prose advice is still useful, write the skill anyway" | Rule 4 requires an executable artifact. Advice without a command, `file:line`, or concrete code pattern is not verifiable and does not become a skill. |
| "I'll just write straight to .claude/skills/, it saves a step" | Never. Staging-only is a hard rule, not a convenience default. Promotion requires explicit user approval and runs through `craftflow_skill_promote.py` only. |
| "No SKILL.md exists yet in .claude/skills/, so dedup doesn't apply" | Also compare against `.cursor/skills/*/SKILL.md` and this plugin's own `skills/*/SKILL.md` frontmatter — dedup spans all three locations. |
| "Skipping most of the time feels like doing nothing" | Skip-by-default is the anti-slop design. A low promotion rate is the system working, not failing. |
