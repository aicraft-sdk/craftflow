# Craftflow Plugin — Critical Guardrails

Hard rules only. One bullet, no exceptions. For rationale and full context, see
`README.md`, `AI_FIRST.md`, and `docs/ai/decisions/`.

- Never bypass `craftflow-router`. It is the only entry point — no direct Edit/Write/Bash
  for code changes without routing through it first.
- TDD is mandatory: RED (failing test) → GREEN (minimal passing code) → REFACTOR. No
  production code without a failing test first.
- File size: prefer < 400 lines per file, max 500; functions ~30–40 lines.
- Never hand-edit `CHANGELOG.md`'s released sections or any version field in this plugin.
  CI auto-releases on every push to `main` touching `tools/craftflow-plugin/**` — manual
  edits are overwritten or fail the consistency gate.
- Never skip TDD, review, or verifier phases "just this once" — no fresh evidence, no
  completion claim.
- Never edit `.craftflow/state/*.md` memory files directly from a write agent — emit
  structured `MEMORY_NOTES`; only the router/workflow finalizer persists memory.
- No secrets, tokens, or private keys committed — use environment variables.
- No `any` / `@ts-ignore` without an inline justification comment.
- Spec before code — no implementation without a linked feature spec or approved plan.
