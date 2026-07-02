# Runtime Complexity Benchmark

Date: 2026-07-02

> Structural proxies for runtime cost: context load, chain depth, gates, context management, parallelism.
> ai-craft/craftflow also includes real telemetry from workflow event logs.

## 1. Context Load (always-on tokens & per-workflow cost)

| Repo | Agent files | Skill files | Always-on tokens | Est. tokens / workflow | Largest file |
|------|-------------|-------------|------------------|------------------------|--------------|
| craftflow | 12 | 24 | 2,326 | 13,956 (6 events (real)) | planner.md (22,734B) |
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

## 6. Real Telemetry — craftflow (49 workflows)

**Workflow distribution:** BUILD=41, DEBUG=2, PLAN=6

**Remediation loop rates** (re_review / re_hunt / re_verify):

- `re_review`: mean=0.18, median=0, max=2, triggered in 7/49 runs (14.3%)
- `re_hunt`: mean=0.18, median=0, max=2, triggered in 7/49 runs (14.3%)
- `re_verify`: mean=0.02, median=0, max=1, triggered in 1/49 runs (2.0%)

**Events per workflow** (proxy for agent turn count): mean=8.64, median=6, max=35
