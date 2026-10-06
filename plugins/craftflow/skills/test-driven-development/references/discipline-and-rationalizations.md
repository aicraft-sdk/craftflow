<!-- Moved verbatim from the test-driven-development SKILL.md preload (PR 2 split). Contents: Overview, source-of-truth, when to use, iron law, process discipline, why order matters, red flags, rationalizations, checklist. -->

## Overview

Write the test first. Watch it fail. Write minimal code to pass.

**Core principle:** If you didn't watch the test fail, you don't know if it tests the right thing.

**Violating the letter of the rules is violating the spirit of the rules.**

## Tests Are the Source of Truth (CRITICAL)

The test encodes the required behavior. When a test fails — in GREEN, in REFACTOR, on a regression run, or anywhere in the full suite, including tests you did not write this session — the default assumption is **the code is wrong, not the test.** Fix the code.

**Never make a failing test pass by:**
- Loosening or deleting the assertion (`toBe(true)`, removing an `expect(...)`)
- Adding `.skip`, `.only`, or an early `return` that bypasses the check
- Changing the expected value to match whatever the buggy code currently outputs
- Wrapping the assertion in a try/catch that swallows the failure
- Deleting the test

**The only valid reason to edit a test is that the test itself is provably wrong** — it encodes a stale requirement, has a typo, or asserts on the wrong fixture. That is rare, and it is not a silent edit:
1. State plainly, in output, which test is being changed and why.
2. Name the correct expected behavior per the spec/plan — not "what the code currently does."
3. If the correctness of the requirement is not yours to decide, stop and ask instead of rewriting it.

This is not a new carve-out from the rules below — it's the same rule the RED and GREEN sections already apply locally ("Test passes? Fix test." at RED, "Test fails? Fix code, not test." at GREEN), stated as the general principle so it also covers pre-existing and regression tests encountered outside the current RED/GREEN cycle. `craftflow:integration-verifier` scans diffs for exactly this pattern (weakened, skipped, or trivialized assertions) and reports it as a CRITICAL finding — don't produce work that gate is designed to catch.

## When to Use

**Always:**
- New features
- Bug fixes
- Refactoring
- Behavior changes

**Exceptions (ask your human partner):**
- Throwaway prototypes
- Generated code
- Configuration files

Thinking "skip TDD just this once"? Stop. That's rationalization.

## The Iron Law

```
NO PRODUCTION CODE WITHOUT A FAILING TEST FIRST
```

Write code before the test? Delete it. Start over.

**No exceptions:**
- Don't keep it as "reference"
- Don't "adapt" it while writing tests
- Don't look at it
- Delete means delete

Implement fresh from tests. Period.

## Test Process Discipline (CRITICAL)

**Problem:** Test runners (Vitest, Jest) default to watch mode, leaving processes hanging indefinitely.

**Mandatory Rules:**
1. **Always use run mode** — Never invoke watch mode:
   - Vitest: `npx vitest run` (NOT `npx vitest`)
   - Jest: `CI=true npx jest` or `npx jest --watchAll=false`
   - npm scripts: `CI=true npm test` or `npm test -- --run`
2. **Prefer CI=true prefix** for all test commands: `CI=true npm test`
3. **After TDD cycle complete**, verify no orphaned processes:
   `pgrep -f "vitest|jest" || echo "Clean"`
4. **Kill if found**: `pkill -f "vitest" 2>/dev/null || true`

## Why Order Matters

**"I'll write tests after to verify it works"**

Tests written after code pass immediately. Passing immediately proves nothing:
- Might test wrong thing
- Might test implementation, not behavior
- Might miss edge cases you forgot
- You never saw it catch the bug

Test-first forces you to see the test fail, proving it actually tests something.

**"I already manually tested all the edge cases"**

Manual testing is ad-hoc. You think you tested everything but:
- No record of what you tested
- Can't re-run when code changes
- Easy to forget cases under pressure
- "It worked when I tried it" ≠ comprehensive

Automated tests are systematic. They run the same way every time.

**"Deleting X hours of work is wasteful"**

Sunk cost fallacy. The time is already gone. Your choice now:
- Delete and rewrite with TDD (X more hours, high confidence)
- Keep it and add tests after (30 min, low confidence, likely bugs)

The "waste" is keeping code you can't trust. Working code without real tests is technical debt.

## Red Flags - STOP and Start Over

If you catch yourself:

- Code before test
- Test after implementation
- Test passes immediately
- Can't explain why test failed
- Tests added "later"
- Rationalizing "just this once"
- "I already manually tested it"
- "Tests after achieve the same purpose"
- "It's about spirit not ritual"
- "Keep as reference" or "adapt existing code"
- "Already spent X hours, deleting is wasteful"
- "TDD is dogmatic, I'm being pragmatic"
- "This is different because..."

**All of these mean: Delete code. Start over with TDD.**

## Rationalization Prevention

| Excuse | Reality |
|--------|---------|
| "Too simple to test" | Simple code breaks. Test takes 30 seconds. |
| "I'll test after" | Tests passing immediately prove nothing. |
| "Tests after achieve same goals" | Tests-after = "what does this do?" Tests-first = "what should this do?" |
| "Already manually tested" | Ad-hoc ≠ systematic. No record, can't re-run. |
| "Deleting X hours is wasteful" | Sunk cost fallacy. Keeping unverified code is technical debt. |
| "Keep as reference, write tests first" | You'll adapt it. That's testing after. Delete means delete. |
| "Need to explore first" | Fine. Throw away exploration, start with TDD. |
| "Test hard = design unclear" | Listen to test. Hard to test = hard to use. |
| "TDD will slow me down" | TDD faster than debugging. Pragmatic = test-first. |
| "Manual test faster" | Manual doesn't prove edge cases. You'll re-test every change. |
| "Existing code has no tests" | You're improving it. Add tests for existing code. |

## Verification Checklist

Before marking work complete:

- [ ] Every new function/method has a test
- [ ] Watched each test fail before implementing
- [ ] Each test failed for expected reason (feature missing, not typo)
- [ ] Wrote minimal code to pass each test
- [ ] All tests pass
- [ ] Output pristine (no errors, warnings)
- [ ] Tests use real code (mocks only if unavoidable)
- [ ] Edge cases and errors covered
- [ ] No hanging test processes (pgrep -f "vitest|jest" returns empty)

Can't check all boxes? You skipped TDD. Start over.
