<!-- Moved verbatim from the verification-before-completion SKILL.md preload (PR 2 split). Contents: Claims table, key patterns, rationalizations. -->

## Common Failures

| Claim | Requires | Not Sufficient |
|-------|----------|----------------|
| Tests pass | Test command output: 0 failures | Previous run, "should pass" |
| Linter clean | Linter output: 0 errors | Partial check, extrapolation |
| Build succeeds | Build command: exit 0 | Linter passing, logs look good |
| Bug fixed | Test original symptom: passes | Code changed, assumed fixed |
| Regression test works | Red-green cycle verified | Test passes once |
| Agent completed | VCS diff shows changes | Agent reports "success" |
| Requirements met | Line-by-line checklist | Tests passing |

## Proof type must match the claim

A passing test proves what the test asserts, not the claim built on it. Pick the proof by the kind of claim, then
check the evidence is that kind. Evidence of a weaker kind leaves the claim UNVERIFIED, however green it is.
After [The Software Factory Trap](https://dhasandev.substack.com/p/the-software-factory-trap).

| Claim kind | Proof that settles it | Looks like proof, but isn't |
|---|---|---|
| A user can complete a flow (sign up, check out, export) | End-to-end run of that journey: browser, CLI session or API sequence | Unit tests of each step, mocked services |
| An API or contract is correct | Contract test against the spec or a real client | Unit tests that mock the API boundary |
| An integration works (DB, provider, queue, file format) | Run against the real dependency or a faithful local one | A fake you wrote that returns what the code expects |
| A bug is fixed | The original reproduction now passes, and fails with the fix reverted | A new test that never failed on the old code |
| No regression | The suite that guards that behavior, run fresh | A different suite, or a subset |
| Performance or load holds | A measurement against a stated number | "Looks fast", a single local run |
| Secure or authorized | A test that the unauthorized path is refused | The authorized path working |
| A refactor changed no behavior | The behavior suite passes before and after, unchanged | Type check and lint |

When the plan names a claim, the plan's verification strategy names its proof kind. A claim whose kind has no proof
yet is reported as a gap, not upgraded to PASS.

## Rationalization Prevention

| Excuse | Reality |
|--------|---------|
| "Should work now" | RUN the verification |
| "I'm confident" | Confidence ≠ evidence |
| "Just this once" | No exceptions |
| "Linter passed" | Linter ≠ compiler |
| "Agent said success" | Verify independently |
| "I'm tired" | Exhaustion ≠ excuse |
| "Partial check is enough" | Partial proves nothing |
| "Different words so rule doesn't apply" | Spirit over letter |
| "I already tested it manually" | Manual ≠ automated evidence |
| "The code looks correct" | Looking ≠ running |

## Key Patterns

**Tests:**
```
✅ [Run test command] [See: 34/34 pass] "All tests pass"
❌ "Should pass now" / "Looks correct"
```

**Regression tests (TDD Red-Green):**
```
✅ Write → Run (pass) → Revert fix → Run (MUST FAIL) → Restore → Run (pass)
❌ "I've written a regression test" (without red-green verification)
```

**Build:**
```
✅ [Run build] [See: exit 0] "Build passes"
❌ "Linter passed" (linter doesn't check compilation)
```

**Requirements:**
```
✅ Re-read plan → Create checklist → Verify each → Report gaps or completion
❌ "Tests pass, phase complete"
```

**Agent delegation:**
```
✅ Agent reports success → Check VCS diff → Verify changes → Report actual state
❌ Trust agent report
```

## Why This Matters

False completion destroys trust, ships broken code, and creates rework. Verification exists to stop that. No fresh evidence, no completion claim.

## When To Apply

**ALWAYS before:**

- ANY variation of success/completion claims
- ANY expression of satisfaction
- ANY positive statement about work state
- Committing, PR creation, task completion
- Moving to next task
- Delegating to agents

**Rule applies to:**

- Exact phrases
- Paraphrases and synonyms
- Implications of success
- ANY communication suggesting completion/correctness
