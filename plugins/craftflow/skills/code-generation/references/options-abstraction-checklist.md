<!-- Moved verbatim from the code-generation SKILL.md preload (PR 2 split). Contents: Full red flags, rationalizations, options, abstraction, checklist. -->

## Red Flags - STOP and Reconsider

If you find yourself:

- Writing code before answering Universal Questions
- Adding features not requested ("while I'm here...")
- Ignoring project patterns ("my way is better")
- Not handling edge cases ("happy path only")
- Creating abstractions for one use case
- Adding configuration options not requested
- Using magic numbers or hardcoded thresholds instead of named constants or derived formulas
- Writing comments instead of clear code
- Multiple valid approaches exist but not presenting options

**STOP. Go back to Universal Questions.**

## Rationalization Prevention

| Excuse | Reality |
|--------|---------|
| "This might be useful later" | YAGNI. Build what's needed now. |
| "My pattern is better" | Match existing patterns. Consistency > preference. |
| "Edge cases are unlikely" | Edge cases cause production bugs. Handle them. |
| "I'll add docs later" | Code should be self-documenting. Write clear code now. |
| "It's just a quick prototype" | Prototypes become production. Write it right. |
| "I know a better way" | The codebase has patterns. Follow them. |
| "I understand enough to start" | Partial understanding produces wrong code. Read the full spec, pattern, or reference before writing. |

## When to Present Multiple Options

**Present 2-3 approaches with tradeoffs if:**
- Multiple design patterns could work (e.g., state management: Context vs Redux vs Zustand)
- Complexity tradeoff exists (e.g., simple file storage vs database)
- User said "best way" or "how should I" (signals uncertainty)

**Proceed with single approach if:**
- One approach is clearly simpler AND meets requirements
- Project patterns already established (follow existing pattern)
- User request is specific (no ambiguity)

**When multiple valid approaches exist:** Prefer the simplest option that matches project patterns. If the choice is high-risk and not already decided by the prompt or plan, surface the alternatives in your output and return control to the router instead of questioning the user directly.

## When to Abstract

Abstraction has a cost. Only introduce it when concrete evidence justifies it:

| Signal | Action |
|--------|--------|
| Pattern seen in 1 example only | Do not extract. This is overfitting to a single case. |
| Same logic in 1 place | Do not abstract. Inline is fine. |
| Same logic in 2 places | Note the duplication. Do not abstract yet. |
| Same logic in 3+ places | Extract. The pattern is proven. |
| 1-2 line change | Inline edit. No helper function needed. |
| Parameter variations only | Extract function with parameters. |
| Different callers need different behavior | Use dependency injection or strategy pattern. |

**Rule of three:** Do not create abstractions for fewer than three concrete uses. Premature abstraction is harder to undo than duplication.

## Code Quality Checklist

Before completing:

- [ ] Universal Questions answered
- [ ] Context-specific questions answered (if applicable)
- [ ] Project patterns studied and matched
- [ ] Minimal implementation (no over-engineering)
- [ ] Edge cases handled
- [ ] Error handling in place
- [ ] Types correct and complete
- [ ] Naming matches project conventions
- [ ] No hardcoded values (use constants)
- [ ] No debugging artifacts (console.log, TODO)
- [ ] No commented-out code
