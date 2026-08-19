# {{PROJECT_NAME}} — AI Context

## Purpose

{{PROJECT_DESCRIPTION}}

## Public Interface

**Interface type**: {{INTERFACE_TYPE}}
<!-- INTERFACE_TYPE: HTTP REST | gRPC | CLI subcommands | exported module API | event consumer -->

<!-- List the main entry points. Examples: -->
<!-- HTTP: `GET /api/v1/resource`, `POST /api/v1/resource/:id` -->
<!-- CLI: `project-name <command> [options]` — see `--help` for full command list -->
<!-- Module: `import { functionName } from '{{PROJECT_NAME}}'` -->

{{INTERFACE_DETAILS}}

## Events

<!-- List async events emitted or consumed. Remove this section if the project has no event bus. -->

| Event | Direction | Payload |
|---|---|---|
| `{{EVENT_NAME}}` | emitted / consumed | {{EVENT_PAYLOAD}} |

## Errors

<!-- How do errors surface? HTTP status codes, CLI exit codes, exception types? -->

{{ERROR_HANDLING}}

## Dependencies

<!-- Key runtime dependencies and what they are for. -->

| Dependency | Purpose |
|---|---|
| `{{DEP_NAME}}` | {{DEP_PURPOSE}} |

## SLOs

<!-- Performance or availability expectations. -->

- Latency: {{LATENCY_TARGET}}
- Availability: {{AVAILABILITY_TARGET}}
- Data access: {{DATA_ACCESS_PATTERN}}

## Vocabulary

<!-- Project-specific terms not in the root GLOSSARY.md. -->

| Term | Meaning |
|---|---|
| `{{TERM}}` | {{DEFINITION}} |

## Gotchas

<!-- Known foot-guns, version requirements, environment quirks. -->

- {{GOTCHA_1}}
- {{GOTCHA_2}}
