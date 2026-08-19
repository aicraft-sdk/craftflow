# Glossary

Terms used across specs, plans, and AI context packs.

| Term | Meaning |
|------|---------|
| **{{PROJECT_NAME}}** | This repository / project. {{PROJECT_DESCRIPTION}} |
| **{{DOMAIN}}** | The business or technical domain this project serves. |
| **Craftflow** | The AI development lifecycle orchestrator used in this repo: router, planner, builder, reviewers, verifier, memory files. Formerly referred to elsewhere as "AIDLC" — that term is retired in favor of the actual tool name. |
| **AI Context Pack** | Per-project `AI.md`, `TESTS.md`, optional `DESIGN.md` + `AGENTS.md` links. |
| **{{TICKET_PREFIX}}-** | Ticket prefix for branch names and PR titles (replace with your ticketing system prefix, or remove if unused). |
| **WAY_OF_WORK** | The hybrid human+AI delivery flow documented in `docs/ai/WAY_OF_WORK.md`. |
| **Spec** | A feature specification document under `docs/ai/specs/` that links to the ticket and defines acceptance criteria. |
| **Plan** | A craftflow-generated execution plan under `docs/plans/` that phases and gates the implementation. |
| **ADR** | Architecture Decision Record — a short document under `docs/ai/decisions/` recording a significant technical decision and its rationale. |
| **RFC** | Request for Comment — a proposal document under `docs/ai/rfcs/` for changes that need team review before implementation. |
| **scanRoots** | Directories the AI Context Pack linter scans for first-level project directories (see `.agents-md-validator.json`). |

<!-- Add project-specific terms below: -->
