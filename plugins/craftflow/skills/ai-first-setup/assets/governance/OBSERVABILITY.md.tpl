# Observability

## Logging

- Use the **project logger** (see `AGENTS.md` §5 for the designated logger module); avoid `console.log` / `console.error` in production code.
- Log at appropriate levels: `debug` for trace-level detail (disabled in production by default), `info` for key lifecycle events, `warn` for recoverable issues, `error` for failures requiring attention.

## Redaction

- Use the project's redaction utility wherever log context may include PII, tokens, session identifiers, or other sensitive data.
- Never log raw: passwords, API keys, auth tokens, email addresses, IP addresses, or session IDs in clear text.
- Apply redaction at the point of log creation, not retrospectively.

## Correlation

- Propagate request or correlation IDs per existing middleware and logging conventions in each service or module.
- Include correlation IDs in error responses where the project's API contract permits it.

## Metrics and tracing

- Follow organizational standards and per-service documentation when adding metrics or traces.
- Do not introduce new observability dependencies without approval (supply chain rule).

## Alerting

- On-call runbook: {{ON_CALL_URL}}
- Follow existing alert policies for the project; do not silence alerts without a documented reason.
