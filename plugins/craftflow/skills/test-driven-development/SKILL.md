---
name: test-driven-development
description: "Internal skill. Use craftflow-router for all development tasks."
allowed-tools: Read Grep Glob Bash Write Edit
user-invocable: false
---

# Test-Driven Development (TDD)

Write the test first. Watch it fail. Write minimal code to pass. **If you didn't watch the test fail, you don't know if it tests the right thing.** Violating the letter of the rules is violating the spirit of the rules.

## The Iron Law

```
NO PRODUCTION CODE WITHOUT A FAILING TEST FIRST
```

Code before the test? Delete it and start over; no keeping it as "reference". Always for features, bug fixes, refactoring, behavior changes. Exceptions (ask your human partner): throwaway prototypes, generated code, config files.

## Tests Are the Source of Truth (CRITICAL)

When a test fails, assume **the code is wrong, not the test.** Never pass a test by loosening/deleting an assertion, adding `.skip`/`.only` or an early `return`, changing the expected value to match buggy output, swallowing the failure in try/catch, or deleting the test. Only edit a test that is provably wrong (stale requirement, typo, wrong fixture), and then state in output which test and why, and name the correct behavior per spec/plan. If the requirement isn't yours to decide, stop and ask. `craftflow:integration-verifier` reports weakened tests as CRITICAL.

## Cycle

One vertical slice at a time: RED test1 -> GREEN impl1, then test2 -> impl2. Never all tests first.
- **RED:** one minimal test, one behavior, clear name, real code. Run it (`CI=true`, run mode); MANDATORY: it must fail, for the expected reason. Passes? Fix the test. Errors? Fix and re-run.
- **GREEN:** simplest code to pass; no extra features; don't hard-code test values. Run it: test passes, other tests pass, output pristine. Fails? Fix code, not test.
- **REFACTOR:** only after green; keep tests green; add no behavior.

## Test process discipline

Never watch mode: `npx vitest run`, `CI=true npx jest`, `CI=true npm test`. After the cycle: `pgrep -f "vitest|jest" || echo "Clean"`; kill leftovers with `pkill -f "vitest" 2>/dev/null || true`.

## Red Flags - delete code, start over with TDD

Code before test; test after implementation; test passes immediately; can't explain the failure; "just this once"; "already manually tested"; "keep as reference"; "TDD is dogmatic"; "this is different because...".

## Read on demand (one reference per trigger)

- Full RED/GREEN/REFACTOR examples, vertical-slicing rationale, bug-fix walkthrough, output format, final rule: `references/cycle-details.md`
- Why-order-matters arguments, full red flags, rationalization table, completion checklist: `references/discipline-and-rationalizations.md`
- Before claiming the phase complete: coverage >= 80% (branches/functions/lines/statements) or add the missing tests; also test prioritization, stuck-problems table, design for testability, behavioral focus, cross-agent test contracts: `references/coverage-and-design.md`
- Naming, AAA structure, near-miss negatives, smells: `references/testing-patterns.md`
- Factories, mocks, env/time handling: `references/test-data-and-mocks.md`
- Real APIs, seeded data, browser flows, stress proof: `references/integration-and-live-proof.md`
