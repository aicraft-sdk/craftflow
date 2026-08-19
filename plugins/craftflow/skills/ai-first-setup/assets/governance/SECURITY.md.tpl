# Security (AI-generated code)

## Secrets

- Never commit secrets, tokens, or private keys. Use environment variables and secret stores as documented in the project deployment guide.
- Do not log secrets or raw auth tokens.

## AuthN / AuthZ

- Respect service boundaries: each layer of the system enforces auth as documented in architecture docs.
- Validate and sanitize inputs at system boundaries (HTTP endpoints, CLI argument parsing, message consumers).

## AI-specific checklist

Before merging AI-written changes:

- [ ] No new dependencies without justification and review (supply chain).
- [ ] No linter or type suppression (`// eslint-disable`, `@ts-ignore`, `// @ts-expect-error`) without a comment and associated ticket reference.
- [ ] No broadening of types (`any`, unsafe casts) without explicit approval.
- [ ] Logging uses the project logger and **redaction** for sensitive fields (see [OBSERVABILITY.md](./OBSERVABILITY.md)).
- [ ] PII and session identifiers never appear in clear text in logs or error messages returned to callers or users.

## Dependency and supply chain

- Follow organizational policy for dependency audits (`npm audit` / `yarn audit` / `pnpm audit`) and license review.
- Pin dependency ranges where the project policy requires it.

## References

- Root constraints: [AGENTS.md](../../AGENTS.md) §3, §5.
- Architecture: see project architecture documentation if maintained.
