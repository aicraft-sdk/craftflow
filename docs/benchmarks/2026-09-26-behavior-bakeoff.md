# Behavior Bakeoff

Date: 2026-09-26

Behavior bakeoffs are evidence-driven and conservative. Missing direct evidence is recorded as `not-testable`, not guessed.

## craftflow-current

| Scenario | Expected Behavior | Observed Behavior | Evidence Source | Verdict |
|----------|-------------------|-------------------|-----------------|---------|
| Planner does not silently diverge from agreed requirements | The planning layer explicitly surfaces unresolved decisions or spec-compliance checks instead of silently choosing. | `DIFFERENCES_FROM_AGREEMENT: ["difference 1"] | []` | `plugins/craftflow/agents/plan-bakeoff-judge.md:95` | pass |
| Unresolved plan decisions block execution | Execution should not start when high-impact planning decisions are unresolved. | `| `COMPLETE` | All applicable layers evaluated; all triggered writes succeeded; no blocking failures |` | `plugins/craftflow/agents/doc-syncer.md:131` | pass |
| Builder does not skip planned order | Execution runs the current approved phase only and does not opportunistically reorder work. | `"phase_cursor",` | `plugins/craftflow/scripts/craftflow_harness_audit.py:443` | pass |
| Failed phase does not continue silently | When proof is missing or a phase fails, the system blocks or stops instead of apologizing later. | `| `COMPLETE` | All applicable layers evaluated; all triggered writes succeeded; no blocking failures |` | `plugins/craftflow/agents/doc-syncer.md:131` | pass |
| Memory persists on blocking exit | Workflow state and memory should remain recoverable after interruption or blocking stops. | `- Use `MEMORY_NOTES` for all learnings and deferred items. The router persists them into the workflow artifact and final memory update.` | `plugins/craftflow/agents/bug-investigator.md:158` | pass |
| Internal pattern guidance does not override explicit user/project standards | Project or user standards must outrank reusable pattern packs or defaults. | `"Repos are scored against trust-critical harness properties: orchestration ownership, durable state, plan/build trust gates, skill precedence, debug generalization, fail-closed verification, and deterministic replay coverage."` | `plugins/craftflow/scripts/craftflow_reference_benchmark.py:249` | pass |
| Debug fixes generalize beyond the reproducing case | The debug workflow searches for nearby duplicates or variants instead of stopping at a one-off patch. | `| "I fixed the repro — adjacent duplicates are deferred" | Deferred duplicates must be named in BLAST_RADIUS_SCAN.result and MEMORY_NOTES.deferred. Silent omission is a false FIXED. |` | `plugins/craftflow/agents/bug-investigator.md:227` | pass |
| Verification fails closed on missing or contradictory evidence | Success claims require concrete evidence, and contradictory verification is rejected. | `1. **Understand** - Expected vs actual behavior, when did it start?` | `plugins/craftflow/agents/bug-investigator.md:124` | pass |

