# Way of work (AI-first delivery)

Hybrid flow: **human intent in markdown specs** + **Craftflow orchestration** (router → planner → builder → reviewer / silent-failure-hunter → integration-verifier → memory).

## 1. Intake

- Ticket ({{TICKET_PREFIX}}-…) is the anchor for branch name, commits, and PR title. If no ticketing system is used, use a descriptive slug.
- Author or update a feature spec: `docs/ai/specs/<ticket-or-slug>.md` using `docs/ai/specs/template.md`.

## 2. Plan

- Router produces `docs/plans/<slug>.md` when planning is required.
- **Open decisions** in the plan must be empty or explicitly approved before implementation (craftflow's `plan_trust_gate`).

## 3. Build

- Implement in small phases; follow TDD ([TESTING.md](./TESTING.md)).
- Use `{{BUILD_SYSTEM}} targets` for the affected project(s): `{{BUILD_CMD}}`, `{{TEST_CMD}}`.
- Link the spec and plan in the PR description.

## 4. Review & verify

- Complete craftflow's review + verification chain for the workflow.
- Paste evidence (commands + outcomes) in the PR body; no "done" without proof.

## 5. Release

- Conventional commits with mandatory scope; PR title includes {{TICKET_PREFIX}}- reference when applicable ([RELEASE.md](./RELEASE.md)).
- Update changelog when required by repo policy.

## Session settings (Craftflow)

- Memory lives under `.craftflow/state/project/` (`activeContext.md`, `patterns.md`, `progress.md`), shared by Claude Code and any connected editor integration.
- Default: `AUTO_PROCEED: false` in `activeContext.md` — explicit user approval between risky steps unless changed deliberately.

## Branch naming (suggested)

- `feat/{{TICKET_PREFIX}}-1234-short-slug`
- `fix/{{TICKET_PREFIX}}-1234-short-slug`
- `chore/{{TICKET_PREFIX}}-1234-short-slug`
- Without ticketing: `feat/short-description-of-change`

## Responsibilities

| Artifact        | Owner (default)      |
|----------------|----------------------|
| Feature spec   | Author / PM          |
| Plan           | craftflow planner     |
| Code + tests   | Builder (AI) + human review |
| Security notes | Author + reviewer    |
