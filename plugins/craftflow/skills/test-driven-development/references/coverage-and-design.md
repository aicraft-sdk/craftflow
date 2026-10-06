<!-- Moved verbatim from the test-driven-development SKILL.md preload (PR 2 split). Contents: Good tests, integration, coverage, stuck, testability, behavioral focus, test contracts. -->

## Good Tests

| Quality | Good | Bad |
|---------|------|-----|
| **Minimal** | One thing. "and" in name? Split it. | `test('validates email and domain and whitespace')` |
| **Clear** | Name describes behavior | `test('test1')` |
| **Shows intent** | Demonstrates desired API | Obscures what code should do |

For deeper test structure, near-miss negative tests, behavior-vs-internals, and
smell checks, read `references/testing-patterns.md`.

For factories, mocks, and env/time handling, read
`references/test-data-and-mocks.md`.

## Integration And Live Proof

When the accepted plan or risk profile goes beyond local behavior, read
`references/integration-and-live-proof.md`.

Unit tests are not enough when the task depends on:
- real API calls
- seeded or resettable data
- browser or worker orchestration
- cross-service side effects
- load or stress behavior

In those cases, keep TDD for the inner loop and escalate verification depth for
the outer proof.

## Coverage Threshold (Project Default)

Target: **80%+ code coverage** across:
- Branches: 80%
- Functions: 80%
- Lines: 80%
- Statements: 80%

**Verify with:** `npm run test:coverage` or equivalent.

**Below threshold?** Add missing tests before claiming completion.

### Test Prioritization

80% coverage means deliberate choices about what to test first. Focus effort on:
- Critical user-facing paths (auth, payments, data integrity)
- Complex logic with multiple branches
- Code that has broken before (regression-prone areas)

Do NOT skip tests because code "looks simple" — simple code breaks too. The 80% target is a floor, not a ceiling.

## When Stuck

| Problem | Solution |
|---------|----------|
| Don't know how to test | Write wished-for API. Write assertion first. Ask your human partner. |
| Test too complicated | Design too complicated. Simplify interface. |
| Must mock everything | Code too coupled. Use dependency injection. |
| Test setup huge | Extract helpers. Still complex? Simplify design. |

## Design for Testability (When Tests Are Hard)

If tests are hard to write, the interface needs work:

1. **Accept dependencies, don't create them**
   - Testable: `function processOrder(order, paymentGateway) {}`
   - Hard to test: `function processOrder(order) { const gw = new StripeGateway(); }`

2. **Return results, don't produce side effects**
   - Testable: `function calculateDiscount(cart): Discount {}`
   - Hard to test: `function applyDiscount(cart): void { cart.total -= discount; }`

3. **Small surface area** — fewer methods = fewer tests needed, fewer params = simpler setup

### Behavioral Focus

Test how objects collaborate, not what they contain. If a test inspects `.state`, `.length`, or private fields, it is testing structure — and will break when internals change without behavior changing.

| Test target | Correct | Wrong |
|-------------|---------|-------|
| Function output | `expect(calculate(input)).toBe(result)` | `expect(calculator.internalCache).toContain(...)` |
| Component behavior | `expect(screen.getByText('Saved')).toBeTruthy()` | `expect(component.state.saved).toBe(true)` |
| Service interaction | `expect(response.status).toBe(201)` | `expect(service.callCount).toBe(1)` |

This is already implied by the "Testing implementation" smell in the Test Smells table. Make it the default lens: every assertion should answer "what did the user/caller observe?" not "what happened inside?"

### Test Contracts Across Agents

When Craftflow routes work across multiple agents (planner writes test specs, builder implements, reviewer verifies), the test file IS the contract:

- **Planner** defines expected behavior as test names and assertions in the plan
- **Builder** writes tests first, implements to green — the test file proves the contract is met
- **Reviewer** re-runs the same tests — pass means contract fulfilled, fail means contract broken

Do not duplicate the contract in prose. If the test file expresses the requirement, the test file is the requirement.
