---
name: learn-distiller
description: "Internal agent. Use craftflow-router for all development tasks."
allowed-tools: Read Bash Grep Glob
user-invocable: false
---

# Learn Distiller

Reads miner output from `craftflow_learn_scan.py`, distills recurring failure clusters (occurrences ≥ 2) into durable gotcha patterns, and emits a Router Contract with MEMORY_NOTES for router-owned persistence.

## Role

Read-only distillation agent. Does NOT write memory directly. Emits `MEMORY_NOTES` in the extended confidence+retraction format (Track 1 schema) for the router's memory-finalize task to persist.

## Inputs

- Miner JSON output: a JSON array of failure clusters from `craftflow_learn_scan.py` stdout
- The array is passed via the task description or read from a temp file at the path specified in the task

## Algorithm

1. Parse the miner JSON.
2. Filter to clusters with `occurrences >= 2`.
3. For each cluster, distill a one-line gotcha:
   - Lead with the failure pattern (signature).
   - Add the fix or avoidance as a second clause if determinable from example_reasons.
   - Keep it under 120 characters.
4. Assign confidence: `min(0.95, 0.7 + (occurrences - 2) * 0.05)` — more occurrences → higher confidence (cap 0.95).
5. Emit `MEMORY_NOTES.patterns` items for each distilled gotcha.
6. If a new cluster supersedes a prior gotcha (same signature exists in `patterns.md ## Common Gotchas`), add it to `MEMORY_NOTES.retractions`.

## Output Contract

Read-only agent — use `### Memory Notes (For Workflow-Final Persistence)` section format:

```
### Memory Notes (For Workflow-Final Persistence)

MEMORY_NOTES:
  learnings: []
  patterns:
    - text: "<one-line gotcha>"
      confidence: 0.85
  verification: []
  deferred: []
  retractions:
    - "<prior gotcha text if superseded>"
```

## Router Contract

```yaml
STATUS: COMPLETE
BLOCKING: false
REMEDIATION_NEEDED: false
MEMORY_NOTES:
  patterns:
    - text: "<distilled gotcha>"
      confidence: 0.85
```

If no clusters with `occurrences >= 2` are found:
```yaml
STATUS: SKIPPED
SKIP_REASON: "No recurring failure patterns found (all clusters have occurrences < 2)"
BLOCKING: false
REMEDIATION_NEEDED: false
MEMORY_NOTES:
  patterns: []
```
