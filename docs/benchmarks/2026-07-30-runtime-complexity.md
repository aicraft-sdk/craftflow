# Runtime Complexity Benchmark

Date: 2026-07-30

> Structural proxies for runtime cost: context load, chain depth, gates, context management, parallelism.
> ai-craft/craftflow also includes real telemetry from workflow event logs.

## 1. Context Load (always-on tokens & per-workflow cost)

| Repo | Agent files | Skill files | Always-on tokens | Est. tokens / workflow | Largest file |
|------|-------------|-------------|------------------|------------------------|--------------|
| craftflow | 13 | 27 | 2,886 | 17,316 (6 events (real)) | planner.md (25,527B) |
| cc10x | 10 | 24 | 2,545 | 12,725 (5 agents (BUILD proxy)) | bug-investigator.md (30,419B) |
| superpowers | 0 | 14 | 2,879 | 5,758 (2 agents (BUILD proxy)) | SKILL.md (21,647B) |
| agent-skills | 4 | 24 | 2,595 | 5,190 (2 agents (BUILD proxy)) | web-performance-auditor.md (12,278B) |

> Agent/skill bodies are lazy-loaded; only frontmatter is always-on. Per-workflow = always-on tokens × median events (craftflow: real telemetry; others: BUILD chain depth).

## 2. Orchestration Chain Depth (agents per workflow type)

| Repo | BUILD | DEBUG | PLAN | REVIEW |
|------|-------|-------|------|--------|
| craftflow | 5 | 3 | 2 | 1 |
| cc10x | 5 | 3 | 2 | 1 |
| superpowers | 2 | 1 | 1 | 1 |
| agent-skills | 2 | 1 | 0 | 1 |

## 3. Enforcement Gates

| Gate | craftflow | cc10x | superpowers | agent-skills |
|------|------|------|------|------|
| `plan_trust_gate` | ✓ | ✓ | ✓ | ✓ |
| `phase_exit_gate` | ✓ | ✓ | — | ✓ |
| `failure_stop_gate` | ✓ | ✓ | ✓ | — |
| `scope_decision_gate` | ✓ | ✓ | ✓ | — |
| `memory_sync_gate` | ✓ | ✓ | ✓ | — |
| `skill_precedence_gate` | ✓ | ✓ | — | — |
| `doubt_verify_gate` | ✓ | ✓ | ✓ | — |
| `review_loop` | ✓ | ✓ | ✓ | ✓ |
| `hunt_loop` | ✓ | ✓ | ✓ | — |

**Gate scores:**

- craftflow: **9/9**
- cc10x: **9/9**
- superpowers: **7/9**
- agent-skills: **3/9**

## 4. Context Management

| Signal | craftflow | cc10x | superpowers | agent-skills |
|--------|--------|--------|--------|--------|
| `compact_hook` | ✓ | ✓ | ✓ | — |
| `state_persist` | ✓ | ✓ | ✓ | — |
| `context_eviction` | ✓ | ✓ | ✓ | ✓ |
| `summarization` | ✓ | — | — | ✓ |
| `context_resume` | ✓ | ✓ | ✓ | ✓ |
| `token_tracking` | ✓ | ✓ | ✓ | ✓ |

**Context management scores:**

- craftflow: **6/6**
- cc10x: **5/6**
- superpowers: **5/6**
- agent-skills: **4/6**

## 5. Parallelism

| Signal | craftflow | cc10x | superpowers | agent-skills |
|--------|--------|--------|--------|--------|
| `parallel_agents` | ✓ | ✓ | ✓ | ✓ |
| `worktree_isolation` | ✓ | ✓ | ✓ | ✓ |
| `subagent_dispatch` | ✓ | ✓ | ✓ | ✓ |
| `concurrent_phases` | ✓ | ✓ | — | ✓ |

**Parallelism scores:**

- craftflow: **4/4**
- cc10x: **4/4**
- superpowers: **3/4**
- agent-skills: **4/4**

## 6. Real Telemetry — craftflow (147 workflows)

**Workflow distribution:** BUILD=115, DEBUG=9, PLAN=19, REVIEW=2, unknown=2

**Remediation loop rates** (re_review / re_hunt / re_verify):

- `re_review`: mean=0.33, median=0, max=9, triggered in 21/147 runs (14.3%)
- `re_hunt`: mean=0.33, median=0, max=9, triggered in 21/147 runs (14.3%)
- `re_verify`: mean=0.16, median=0, max=3, triggered in 16/147 runs (10.9%)

**Events per workflow** (proxy for agent turn count): mean=9.26, median=6, max=92
