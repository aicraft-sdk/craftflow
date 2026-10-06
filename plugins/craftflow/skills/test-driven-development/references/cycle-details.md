<!-- Moved verbatim from the test-driven-development SKILL.md preload (PR 2 split). Contents: Red-green-refactor details, bug-fix example, output format, final rule. -->

## Red-Green-Refactor

```
    ┌─────────┐       ┌─────────┐       ┌───────────┐
    │   RED   │──────>│  GREEN  │──────>│ REFACTOR  │
    │ (Fail)  │       │ (Pass)  │       │ (Clean)   │
    └─────────┘       └─────────┘       └───────────┘
         ^                                    │
         │                                    │
         └────────────────────────────────────┘
                    Next Feature
```

### Vertical Slicing (CRITICAL)

```
WRONG (horizontal — all tests then all code):
  RED:   test1, test2, test3, test4, test5
  GREEN: impl1, impl2, impl3, impl4, impl5

RIGHT (vertical — one feature at a time):
  RED->GREEN: test1->impl1
  RED->GREEN: test2->impl2
  RED->GREEN: test3->impl3
```

**DO NOT write all tests first, then all implementation.** This produces bad tests:
- Tests written in bulk test _imagined_ behavior, not _actual_ behavior
- You end up testing the _shape_ of things rather than user-facing behavior
- Tests become insensitive to real changes — pass when behavior breaks, fail when behavior is fine
- You outrun your headlights, committing to test structure before understanding the implementation

**Correct approach:** One test → one implementation → repeat. Each test responds to what you learned from the previous cycle.

### RED - Write Failing Test

Write one minimal test showing what should happen.

**Good:**
```typescript
test('retries failed operations 3 times', async () => {
  let attempts = 0;
  const operation = () => {
    attempts++;
    if (attempts < 3) throw new Error('fail');
    return 'success';
  };

  const result = await retryOperation(operation);

  expect(result).toBe('success');
  expect(attempts).toBe(3);
});
```
Clear name, tests real behavior, one thing

**Bad:**
```typescript
test('retry works', async () => {
  const mock = jest.fn()
    .mockRejectedValueOnce(new Error())
    .mockRejectedValueOnce(new Error())
    .mockResolvedValueOnce('success');
  await retryOperation(mock);
  expect(mock).toHaveBeenCalledTimes(3);
});
```
Vague name, tests mock not code

**Requirements:**
- One behavior
- Clear name
- Real code (no mocks unless unavoidable)

### Verify RED - Watch It Fail

**MANDATORY. Never skip.**

```bash
CI=true npm test path/to/test.test.ts
```

Confirm:
- Test fails (not errors)
- Failure message is expected
- Fails because feature missing (not typos)

**Test passes?** You're testing existing behavior. Fix test.

**Test errors?** Fix error, re-run until it fails correctly.

### GREEN - Minimal Code

Write simplest code to pass the test.

**Good:**
```typescript
async function retryOperation<T>(fn: () => Promise<T>): Promise<T> {
  for (let i = 0; i < 3; i++) {
    try {
      return await fn();
    } catch (e) {
      if (i === 2) throw e;
    }
  }
  throw new Error('unreachable');
}
```
Just enough to pass

**Bad:**
```typescript
async function retryOperation<T>(
  fn: () => Promise<T>,
  options?: {
    maxRetries?: number;
    backoff?: 'linear' | 'exponential';
    onRetry?: (attempt: number) => void;
  }
): Promise<T> {
  // YAGNI - You Ain't Gonna Need It
}
```
Over-engineered

Don't add features, refactor other code, or "improve" beyond the test. Don't hard-code test values - implement general logic that works for ALL inputs.

### Verify GREEN - Watch It Pass

**MANDATORY.**

```bash
CI=true npm test path/to/test.test.ts
```

Confirm:
- Test passes
- Other tests still pass
- Output pristine (no errors, warnings)

**Test fails?** Fix code, not test.

**Other tests fail?** Fix now.

### REFACTOR - Clean Up

After green only:
- Remove duplication
- Improve names
- Extract helpers

Keep tests green. Don't add behavior.

### Repeat

Next failing test for next feature.

## Example: Bug Fix

**Bug:** Empty email accepted

**RED**
```typescript
test('rejects empty email', async () => {
  const result = await submitForm({ email: '' });
  expect(result.error).toBe('Email required');
});
```

**Verify RED**
```bash
$ npm test
FAIL: expected 'Email required', got undefined
```

**GREEN**
```typescript
function submitForm(data: FormData) {
  if (!data.email?.trim()) {
    return { error: 'Email required' };
  }
  // ...
}
```

**Verify GREEN**
```bash
$ npm test
PASS
```

**REFACTOR**
Extract validation for multiple fields if needed.

## Output Format

```markdown
## TDD Cycle

### Requirements
[What functionality is being built]

### RED Phase
- Test: [test name]
- Command: `npm test -- --grep "test name"`
- Result: exit 1 (FAIL as expected)
- Failure reason: [function not defined / expected X got Y]

### GREEN Phase
- Implementation: [summary]
- File: [path:line]
- Command: `npm test -- --grep "test name"`
- Result: exit 0 (PASS)

### REFACTOR Phase
- Changes: [what was improved]
- Command: `npm test`
- Result: exit 0 (all tests pass)
```

## Final Rule

```
Production code → test exists and failed first
Otherwise → not TDD
```

No exceptions without your human partner's permission.
