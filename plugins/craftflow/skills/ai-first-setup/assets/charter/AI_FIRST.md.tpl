# AI-First Charter

Non-negotiable principles for AI-assisted work in this repository.

1. **Spec before code** — No implementation without a linked feature spec under `docs/ai/specs/` ({{TICKET_PREFIX}}-ticket id in front matter, or a slug when no ticketing system is used).
2. **Plan before build** — The AIDLC router produces `docs/plans/*.md`; follow the plan phases and gates.
3. **TDD** — RED → GREEN → REFACTOR; see [docs/ai/TESTING.md](docs/ai/TESTING.md).
4. **Smallest step** — One phase at a time; agent output must include a valid CONTRACT envelope where the workflow requires it.
5. **Evidence before claim** — Paste verifier / test command output in the PR; do not assert "passing" without proof.
6. **Shared libraries only** — Use project-designated shared utilities for cross-cutting concerns (logging, config, testing helpers); no ad-hoc duplicates.
7. **Use designated build commands** — Run `{{BUILD_CMD}}` / `{{TEST_CMD}}` as documented in `AGENTS.md`; do not invoke the underlying tools (tsc, vitest, jest) directly unless the documented command does so.
8. **Size limits** — Prefer &lt;400 lines per file (max 500); functions ~30–40 lines.
9. **Redact PII** — Use the project logger's redaction mechanism on every log path that may carry user or session data; never log raw tokens, secrets, or personally identifiable information.
10. **Design system compliance** — For UI projects: use the designated token library and component system; do not hard-code colors, spacing, or typography outside the token system. For CLI/TUI projects: follow the ASCII layout reference in [docs/ai/DESIGN.md](docs/ai/DESIGN.md).

For day-to-day flow, see [docs/ai/WAY_OF_WORK.md](docs/ai/WAY_OF_WORK.md).
