---
name: intent-interview
description: "Internal skill. Use craftflow-router for all development tasks."
allowed-tools: AskUserQuestion Read Bash
user-invocable: false
---

# Intent Interview (Pre-Planning Confidence Gate)

## Purpose

Extract the intent contract before the planner commits to an artifact.

This skill runs before brainstorming/planning when the router detects that the
task description lacks the three pillars required for a reliable plan:
1. **Success definition** — what done looks like, concretely
2. **Constraints** — what must not change, what is out of scope
3. **Scope boundary** — where this ends and the next thing begins

Without these three, the planner will either invent them (hidden assumptions)
or return NEEDS_CLARIFICATION mid-plan (wasted cycles).

## When the Router Dispatches This Skill

The router invokes `Skill(skill="craftflow:intent-interview")` in PLAN preparation when:
- The user request is ≤3 sentences with no acceptance criteria
- The request contains scope-ambiguous terms: "improve", "refactor", "clean up", "make it better", "add stuff", "fix things"
- No plan file or design file exists for the request
- The task touches ≥2 systems or modules (integration risk, unclear boundary)

**Auto-skip conditions (do not invoke this skill):**
- `AUTO_PROCEED: true` in `activeContext.md ## Session Settings` (JUST_GO mode)
- A saved plan file is already referenced in `activeContext.md ## References`
- The request is a single concrete change with an explicit file, function, or behavior named

## Protocol

One question at a time. Never ask two questions in the same turn. Wait for the
answer before asking the next question.

Question order:
1. **Success** — "What does done look like? How will we know this worked?"
2. **Constraints** — "What must not change, break, or be out of scope here?"
3. **Boundary** — "Where does this end? What's explicitly not included?"

Stop when you can predict the user's next 3 answers.

The stop condition is behaviorally checkable: if you can already answer
"What would the user say if I asked about success?", "...about constraints?",
and "...about scope?", the interview is complete — ask no more questions.

## Stop Condition Check (MANDATORY before each question)

Before formulating the next question, ask yourself:
> "If I were to ask the remaining questions, what would the user most likely answer?"
> Can I predict all of those answers with ≥80% confidence from what I've heard so far?

If yes: stop asking and produce the Intent Contract (see Output below).
If no: ask the next unanswered pillar question.

Maximum questions: 5. If all three pillars are still unclear after 5 questions,
produce the contract with `confidence: low` and surface the remaining ambiguities
as `open_decisions` for the planner.

## What This Skill Does NOT Do

- Does not ask about implementation approach (that is the planner's job)
- Does not ask about technology choices (planner)
- Does not ask more than one question per turn
- Does not paraphrase the user's request back to them as a question
- Does not ask questions already answered in the user's original request
- Does not run in JUST_GO / AUTO_PROCEED mode

## Output

After the interview is complete (stop condition met or 5 questions asked),
produce the Intent Contract in this exact format so the router can pass it
to the planner:

```
## Intent Contract (From Interview)
- goal: [one sentence — what success looks like]
- non_goals: [list — what is explicitly out of scope]
- constraints: [list — what must not change or break]
- acceptance_criteria:
  - [testable criterion 1]
  - [testable criterion 2]
- open_decisions:
  - [any pillar that remained unclear after interview — planner must resolve]
- confidence: high | medium | low
```

`confidence: high` — all three pillars answered, stop condition met naturally.
`confidence: medium` — all three pillars answered, but via inference on ≤2 questions.
`confidence: low` — ≥1 pillar still unclear after 5 questions; open_decisions non-empty.

The router passes this contract to the planner under `## Intent Contract (Pre-Interview)`.
The planner treats it as user-approved input for the Requirements Snapshot and may
not deviate from it without returning NEEDS_CLARIFICATION.
