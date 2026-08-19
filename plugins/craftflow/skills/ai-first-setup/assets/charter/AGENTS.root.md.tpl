# {{PROJECT_NAME}} - AGENTS.md

> **Note**: For documentation change history, use `git log` on this file.

## 1. Project / Scope Identification

- Scope: root
- Project name: {{PROJECT_NAME}}
- Languages: {{LANGUAGES}}
- Frameworks + versions: {{FRAMEWORKS}}
- Runtime: {{RUNTIME}}
- Package manager: {{PACKAGE_MANAGER}}
- Build system: {{BUILD_SYSTEM}}
- Commands: `{{BUILD_CMD}}` / `{{TEST_CMD}}` / `{{DEV_CMD}}`

## 2. High-Level Intent

{{PROJECT_DESCRIPTION}}

## 3. Non-Negotiable Constraints

- AI-first charter: [AI_FIRST.md](AI_FIRST.md) (spec-first, TDD, evidence in PRs)
- Mandatory patterns:
  - Use designated shared utilities for logging, config, and testing (see §5)
  - Follow project skills and rules installed via the AI resources CLI (see `project-config.json`)
  - File length limits: max 500 lines, prefer under 400 lines
  - Functions max 30–40 lines
- Forbidden patterns:
  - Do not commit secrets, tokens, or private keys — use environment variables
  - Do not suppress linter or type errors without a comment explaining the exception
  - Do not broaden types (`any`, unsafe casts) without explicit approval in the PR

## 4. Build, Run & Test

- Install: `{{INSTALL_CMD}}`
- Build: `{{BUILD_CMD}}`
- Test: `{{TEST_CMD}}`
- Dev: `{{DEV_CMD}}`

## 5. Security & Safety

- Secrets handling: environment variables and secret stores; never commit secrets
- Data redaction: use the project logger's redaction mechanism on every log path that may carry user or session data
- Restricted areas: see [docs/ai/SECURITY.md](docs/ai/SECURITY.md) for the full AI-specific checklist

## 6. Code & Change Expectations

- Tests required for: all new features and bug fixes — see [docs/ai/TESTING.md](docs/ai/TESTING.md)
- PR / commit rules:
  - Conventional commits format: `<type>(<scope>): <description>`
  - Scope: project name or area
  - PR titles should reference the relevant ticket ({{TICKET_PREFIX}}-id) when using a ticketing system
  - Pre-commit: validates AGENTS.md format and AI Context Pack completeness

## 7. Progressive Documentation Index

- AI-first charter: `AI_FIRST.md`
- Way of work: `docs/ai/WAY_OF_WORK.md`
- Design reference: `docs/ai/DESIGN.md`
- Security: `docs/ai/SECURITY.md`
- Testing: `docs/ai/TESTING.md`
- Quality: `docs/ai/QUALITY.md`
- Observability: `docs/ai/OBSERVABILITY.md`
- Prompts: `docs/ai/PROMPTS.md`
- Glossary: `docs/ai/GLOSSARY.md`
- Release: `docs/ai/RELEASE.md`
- Feature specs: `docs/ai/specs/`
- RFCs: `docs/ai/rfcs/`
- ADRs: `docs/ai/decisions/`
- Plans: `docs/plans/`
- Research: `docs/research/`
- Session lifecycle: `init.sh`, `clean-state-checklist.md`, `session-handoff.md`
- State log: `progress.md`
- Scope tracker: `feature_list.json`

## 8. Session Lifecycle & Read-Order

Every agent session follows this startup sequence (harness START):
1. Run `./init.sh` — verifies environment health before any work
2. Read `progress.md` — what happened last session (newest entry on top)
3. Read `feature_list.json` — what is done, in progress, and next
4. Check `git log --oneline -10` — recent changes
5. Pick **exactly one** `todo` or `in_progress` feature from `feature_list.json`; work only on that feature

**Definition of Done:** A feature is done when:
- All `acceptance_criteria` are met
- `verification.commands` run and produce `expected_result`
- `evidence` field in `feature_list.json` is non-empty (records the command + result)
- `progress.md` has an entry for this feature with the verification output

End every session with `clean-state-checklist.md` and overwrite `session-handoff.md`.

## 9. Agent Operating Rules

- This file is authoritative.
- Prefer existing patterns over introducing new ones.
- Nearest AGENTS.md wins (subfolder overrides root).
- Follow skills and rules from the AI resources CLI (synced on install; see `project-config.json`).

### AI Context Pack

- Purpose and interface: `AI.md`
- Test commands and layout: `TESTS.md`
