# Code quality

## Size and shape

- Files: prefer **under 400** lines; **max 500** ([AGENTS.md](../../AGENTS.md)).
- Functions: **~30–40** lines; extract helpers when logic branches grow.

## TypeScript (if applicable)

- Avoid `any`; prefer precise types and shared DTOs or interfaces.
- Prefer explicit error handling over silent catches.
- Use `unknown` instead of `any` when the type is genuinely unknown at compile time.

## Imports and module boundaries

- Use designated workspace libraries or shared modules instead of deep relative imports across major boundaries.
- Do not bypass the project's module boundary conventions.

## Framework-specific (adapt to your stack)

- Do not use framework-internal APIs or deprecated patterns without explicit approval.
- Follow the project's existing patterns for dependency injection, configuration, and error propagation.

## Observability

- See [OBSERVABILITY.md](./OBSERVABILITY.md) for logging and redaction.

## Linting and formatting

- Run linter before commit: `{{LINT_CMD}}`
- Formatting: `{{FORMAT_CMD}}`
- CI enforces zero lint errors; warnings are reviewed per PR.
