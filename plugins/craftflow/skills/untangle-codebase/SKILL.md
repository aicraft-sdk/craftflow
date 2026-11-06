---
name: untangle-codebase
description: "Use when a codebase is tangled (hidden links, needless links, surplus): read-only diagnosis, fixes go to PLAN."
allowed-tools: Read Grep Glob Bash
---

# Untangle Codebase

Method adapted from caldoai's untangle-codebase skill (https://github.com/caldoai/skills, MIT); the wording here is craftflow's own.

## When To Use

Use when existing code is hard to change safely: a small edit breaks something far away, the same fact lives in several places, or special cases keep piling up.

Not for these jobs:

- Shallow modules alone (thin wrappers, pass-through layers): use `codebase-deepening`.
- One failing behavior: that is DEBUG.
- Greenfield design: use `architecture-patterns`.

**Diagnosis is read-only. This skill never edits code: every fix goes to PLAN, then BUILD.**

## The Iron Law

```
NO FIX PROPOSED BEFORE THE PROBLEM LIST IS WRITTEN
```

A fix chosen before the evidence is in tends to move the tangle somewhere else. List the problems with proof first, then design.

## What Counts As Tangle

Tangle is code whose real connections do not match its visible structure. Four categories cover it.

| Category | Probe question | Typical evidence |
|----------|----------------|------------------|
| **Hidden link** | Do two places have to change together while nothing visible connects them? | Copied constants, a format written in one file and parsed in another, implicit call ordering, files that co-change in git with no import between them |
| **Needless link** | Is this connected code that actually changes for different reasons? | Imports that cross concerns, shared mutable state, god modules that everything touches |
| **Surplus** | Would deleting this lose nothing? | Wrappers, unused options or extension points, dead code. Prove it with the Deletion Test in `skills/codebase-deepening/SKILL.md`; do not restate it here |
| **Special case** | What rule is the model missing that forces this branch? | Every special case is an unmet requirement: name the rule, then the branch can be replaced by it |

## Flow

1. **Ask two opt-in questions.** Remove comments that merely restate the code? Delete or repair tests that assert nothing? The default for both is no; do nothing extra unless the user says yes.
2. **List the problems, with no edits.**
   - Skeleton first: Glob the source tree and Grep exported signatures.
   - Import graph: Grep the imports to see what is visibly connected.
   - Co-change history: run `git log --name-only --format=` over the scope and look for file pairs that change together without an import link. Those are hidden-link candidates.
   - Start with the areas that many other files depend on.
   - If the harness offers a read-only explore subagent, it may summarize one directory each. Never use an agent that can write.
   - Record each finding as: category | `file:line` evidence | why it costs | badge. Badges match `codebase-deepening`: **Strong**, **Worth-exploring**, **Speculative**.
3. **Redesign.** Group findings by root cause and prefer one change that removes several. Turn each special case into a named requirement plus a change to the model that absorbs it.
4. **Propose.** Present the fix set and a decision log (Decision | Options | Chosen | Why | Findings closed). List any opt-in "yes" answers from step 1 in the proposal; they go to the planner as extra scope for the fix set and are never acted on during diagnosis. The user picks. Then hand off to the planner and BUILD (builder, then reviewer alongside silent-failure-hunter, then verifier).
5. **Re-scan after BUILD lands.** Rerun step 2 on the touched area. Repeat steps 3 to 5 until no Strong finding remains. Behavior must be shown unchanged by the existing tests passing.

## Stop And Report

- Nothing worth fixing: say so and stop. A clean result is a valid result.
- No tests cover the behavior you would touch: stop with `cannot verify behavior preservation` and propose characterization tests as the first phase of the plan.
- A finding needs domain judgment you do not have: badge it Worth-exploring and ask the user.

## Output Format

```markdown
## Untangle Report: <scope>

### Findings
| # | Category | Evidence (file:line) | Cost | Badge |
|---|----------|----------------------|------|-------|

### Root causes
- <cause> closes findings #, #

### Proposed fix set
1. <change> (closes #, #)

### Decision log
| Decision | Options | Chosen | Why | Findings closed |
|----------|---------|--------|-----|-----------------|

### Handoff
Pick a fix set; the planner takes it from there. No files were edited.
```

## Red Flags - STOP

- Editing any file during diagnosis.
- Proposing fixes before the problem list exists.
- Patching a special case instead of naming the requirement behind it.
- A fix that only moves the complexity to another file.
- Skipping the re-scan after BUILD.

## Rationalization Prevention

| Excuse | Reality |
|--------|---------|
| "It is a one-line fix, I will just make it" | Diagnosis is read-only. The line goes in the plan like any other change. |
| "This special case is a one-off" | Special cases repeat. Name the missing rule; if you cannot, ask. |
| "Moving it to a helper untangles it" | If the callers still depend on the same hidden detail, you only relocated the tangle. |
| "The tests probably still pass" | Run them. Behavior preservation is shown by passing tests, not assumed. |

## Handoff to PLAN and BUILD (gates stay on)

This skill ends with a ranked findings list, a proposed fix set and a decision log. It does **not** change code. Untangling is a behavior-preserving change and gets craftflow's full bar:

- The user's chosen fix set becomes the planner's input. After the plan comes the BUILD workflow: builder, then reviewer alongside silent-failure-hunter, then verifier, doc-sync and memory.
- Each fix is checked against reality: existing tests pass, callers are updated, typecheck is clean, and the re-scan shows the finding closed.
- Never let this skill turn into an ungated rewrite path. Finding is read-only; fixing is gated.
