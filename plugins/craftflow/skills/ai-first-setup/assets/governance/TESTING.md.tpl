# Testing contract

## TDD

- **RED → GREEN → REFACTOR** for new behavior.
- Write or update a failing test that expresses the acceptance criterion, then implement the minimum to pass, then refactor.

## Layers

- **Unit** — Pure logic, services, utilities; fast, no I/O or mocked I/O.
- **Integration** — Module + real adjacent deps (DB, cache) where the project already uses them; follow existing patterns in the target area.
- **Contract / API** — Controllers or clients against documented contracts where applicable.
- **E2E** — Reserved for critical user journeys. Follow each project's `TESTS.md` for commands and scope.

## Commands

- Default: `{{TEST_CMD}}` from repo root (see [AGENTS.md](../../AGENTS.md)).
- Watch mode (local dev only): `{{TEST_WATCH_CMD}}`
- Coverage: `{{TEST_COVERAGE_CMD}}`

## Hygiene

- No `.only` in committed tests (pre-commit enforces via AGENTS.md linter or a separate check).
- Flaky tests: fix or quarantine with owner and ticket reference; do not merge sustained flakes.

## Coverage

- Aim for meaningful coverage of new code paths; follow team thresholds per project type when defined in CI.

## AIDLC

- Skills under the AIDLC set define the detailed TDD contract when using AIDLC orchestration.
- See [docs/ai/PROMPTS.md](./PROMPTS.md) for how to invoke the test-driven-development skill.
