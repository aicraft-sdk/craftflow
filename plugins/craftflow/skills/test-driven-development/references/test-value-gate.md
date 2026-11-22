# Test-value gate

Adapted from [testectomy](https://github.com/felipebasurto/testectomy) (MIT, see `NOTICE`). A test that restates the
implementation is a second, worse copy of the logic: it stays green when the real bug ships and costs every run time
and tokens. The gate decides **which** test to write. TDD still decides **when**: the test you keep is still written
first and still watched to fail.

## The four questions

Before adding or changing any test, answer all four. A missing answer means don't add it yet.

1. **Which rule does it guard?** Name the observable behavior, invariant or contract, by its `FR-###`/`SC-###` id
   when the plan has one. A test with no rule needs the rule first.
2. **What credible regression makes it fail?** One sentence: "fails if the coupon makes the total negative".
3. **Why doesn't existing coverage catch it?** Each contract has one owner test at the strongest boundary. Another
   layer needs its own failure mode. Prefer a new row in an existing table test over a near-duplicate.
4. **Does it need a production seam** (export, flag, wrapper, injection hook) that no production caller needs? Then
   test at the real boundary instead.

Two requests produce junk by reflex:

- Asked to remove or move something: don't add a test that the removed code stays gone. Bringing it back would be a
  decision, not a regression.
- Asked to fix a flaky test: don't add a sleep, a retry or a raised timeout. Find the race and await the real signal.

A bug-fix regression test must fail on the pre-fix code for the intended reason. One that never demonstrably failed
proves the mock, not the fix.

Record the four answers for every new or changed test in the builder's `TEST_GATE` field:
`test | rule id | regression | keep-bar reason`.

## Keep bar

A test earns its place when at least one holds. Write which one in the `TEST_GATE` row.

1. **External boundary**: a provider, API, DB, file format, wire protocol or OS behavior you don't control. Fake
   only at the boundary, with a fake that behaves like the provider (times out, errors, succeeds).
2. **Costly or irreversible failure**: money, auth and permissions, data deletion, migrations, security, privacy.
3. **Hard to verify by reading**: concurrency, ordering, time zones and clocks, floating point, caching, retries.
4. **Large input space**: parsers, serializers, formatters, renderers, validators. Use table, expect or property tests.
5. **A regression that actually happened**: link the incident or issue, if it can plausibly come back.
6. **Public contract**: a library API, CLI output, plugin SDK, protocol, config format, event names others read.

Everything else is checked by reading the diff against the rule, or by the type system.

## Junk patterns

Ask what the test depends on. A test worth keeping depends only on intended behavior: it changes when that behavior
changes and fails only when it regresses. A match rejects the test unless the keep bar names the contract it guards.

### It depends on the past

A removed feature, flag, endpoint or old module path is asserted absent, or an old name asserted not to resolve.

### It depends on nothing

No assertion; it tests the platform or the framework; it asserts what the type system already guarantees; it mocks
the unit under test and asserts what the mock returns; a negative control that passes for an unrelated reason.

### It depends on the implementation

The expected value is computed by the code under test; it restates constants, defaults, fixtures or config; it asserts
private calls, internal call order or which helper ran when a boundary test covers the outcome; it asserts a mock was
called with arguments the code built, when no rule is about that interaction; an unreviewed snapshot updated with `-u`.

### It duplicates another test

The same contract asserted again at another layer, or per provider, with no new failure mode.

### It passes by hiding a flake

These guard real behavior, so the fix is to repair them, not delete them.

| Hides the flake by | Fix |
|---|---|
| A fixed sleep or timer | Await the operation or the signal its handler resolves; control the clock with fake timers |
| Polling state until a deadline | Have the code resolve a signal when the event happens, and await it |
| Retrying on failure | Find the race and await what the test depends on |
| A raised timeout | Await the real event, or move slow shared setup to suite-level setup |
| Live network, or requests answered silently | Fake at the network boundary and make any unexpected request fail the test |
| Being skipped on every run | Fix and re-enable it, or delete it |

## When code and intent disagree

If the plan, spec, README or issue states the intent clearly and the code contradicts it, never write the test to
match the code. Write it from intent as a strict expected failure (`pytest.mark.xfail(strict=True)`, `test.fails`,
or a skip that names the rule id) and report it as a bug. When the task itself is the fix, fix the code instead.

## Behavior coverage and the oracle

Line coverage says a line ran, not that its expected output is pinned. Behavior coverage asks a different question:
for each named behavior, is the expected output asserted for its meaningful inputs? The hard part is not writing the
test, it is knowing what the test should assert: the oracle. After
[Loop-Driven Development](https://x.com/nfcampos/status/2064746971944956299).

When a plan rule has `check: test`, the plan names its oracle, the source of the expected values. Prefer the
strongest one available:

| Oracle | Use when | Weakness |
|---|---|---|
| A reference implementation, queried and frozen | A real system defines correct output (Postgres for a SQL parser, a spreadsheet for formulas, a real API) | Needs a refresh script; the reference is never called from the test run |
| A mined test suite | The domain has a public conformance suite (test262, CommonMark spec cases, SQLLogicTest) | Port only cases at the external boundary, not one project's internals |
| The spec or plan rule, read by a person | No reference exists | Same agent interprets and grades: state the expected value in the rule |
| A reviewer's judgment | Nothing above applies | Judgment, not measurement: say so in the `TEST_GATE` row |

A frozen oracle answer is checked in as a fixture with the script that produced it. The test compares against the
fixture: exact equality, or a stated tolerance (float error, edit distance, pixel diff) where equality can't hold.

A phase is done when every behavior the plan names has at least one pinned case and its oracle is recorded, not just
when every test passes: an empty suite also passes. A named behavior with no case is a gap to report.

## Coverage

Coverage is a reported number, not the goal. Never add a test only to raise it: a junk test that lifts coverage is
still junk. A project that wants a hard floor opts in with `coverage-thresholds.json`.
