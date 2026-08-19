# {{PROJECT_NAME}} — Tests

## Commands

```bash
# Run all tests
{{TEST_CMD}}

# Run in watch mode (local dev only, never in CI)
{{TEST_WATCH_CMD}}

# Run with coverage
{{TEST_COVERAGE_CMD}}

# Run a specific test file
{{TEST_FILE_CMD}}
```

## Test layout

```
{{TEST_LAYOUT}}
```

<!-- Example:
tests/
  unit/           # pure logic, no I/O
  integration/    # module-level with real deps
  fixtures/       # shared test data
  security/       # security-focused cases
-->

## Layers

| Layer | Scope | Notes |
|---|---|---|
| Unit | Pure logic, utilities | No I/O; fast |
| Integration | Module + adjacent deps | Follow existing patterns |
| E2E | Critical user journeys | {{E2E_NOTES}} |

## PR gates

The following test targets must pass before merge:
- `{{TEST_CMD}}` — exit 0
- `{{BUILD_CMD}}` — exit 0

## Fixtures

- Test fixtures live under `{{FIXTURES_PATH}}`
- To add a fixture: {{FIXTURE_INSTRUCTIONS}}

## Coverage expectations

- {{COVERAGE_NOTES}}
