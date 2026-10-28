### ORIENT preparation

ORIENT answers a request to understand existing code, inline and read-only. It creates no workflow artifact, no `TaskCreate`, no phase graph, no agents and no Memory Update, and it never falls into BUILD.

If an artifact was pre-created before routing resolved (`workflow_type: pending`), set its `workflow_type` to `ORIENT` and its `phase_cursor` to `orient`, record the close in `status_history`, and create nothing else. That is the only reason `ORIENT` and `orient` appear in the artifact enums.

### ORIENT procedure

1. Map the modules and files for the named subject: `Glob` or `Grep` to locate, `Read` only the slices needed to explain.
2. Trace one layer up: callers and dependents of the focal symbols through the `LSP` tool (`incomingCalls`, `findReferences`) when a language server is configured, otherwise `Grep`. Locate the exact line and column with `Grep` before any `LSP` call.
3. Explain in the project's own vocabulary: its names, terms and domain glossary as they appear in the code, not generic abstractions.

### ORIENT exit

Stop at understanding. Do not propose or apply edits, and do not write any file. If a change is clearly implied, end with one line offering to route it (BUILD, DEBUG or PLAN); the offer mints nothing. A follow-up change request re-routes from scratch under the routing table, with a fresh announce line and a fresh artifact.

### ORIENT vs REVIEW

REVIEW judges quality ("is this good", "audit it"). ORIENT only explains structure and flow ("help me understand"). The request's primary deliverable decides; only a genuine tie goes to REVIEW, the lower priority number.

### ORIENT phases

The only ORIENT phase token is `orient`. No task is ever created with it, and the token is not in the task-metadata `phase:` enum on purpose; it exists only as the `phase_cursor` of a pre-created artifact closed by the preparation rule.
