# Runtime Complexity Benchmark

Date: 2026-06-24

> Structural proxies for runtime cost: context load, chain depth, gates, context management, parallelism.
> ai-craft/craftflow also includes real telemetry from workflow event logs.

## 1. Context Load (estimated tokens injected per turn)

| Repo | Agent files | Skill files | Total bytes | Est. tokens | Largest file |
|------|-------------|-------------|-------------|-------------|--------------|
| craftflow | 11 | 16 | 151,098 | 37,774 | planner.md (22,734B) |
| cc10x | 10 | 24 | 170,494 | 42,623 | bug-investigator.md (30,419B) |
| superpowers | 0 | 14 | 30,520 | 7,630 | SKILL.md (21,647B) |

## 2. Orchestration Chain Depth (agents per workflow type)

| Repo | BUILD | DEBUG | PLAN | REVIEW |
|------|-------|-------|------|--------|
| craftflow | 5 | 3 | 2 | 1 |
| cc10x | 5 | 3 | 2 | 1 |
| superpowers | 2 | 1 | 1 | 1 |

## 3. Enforcement Gates

| Gate | craftflow | cc10x | superpowers |
|------|------|------|------|
| `plan_trust_gate` | ✓ | ✓ | ✓ |
| `phase_exit_gate` | ✓ | ✓ | — |
| `failure_stop_gate` | ✓ | ✓ | ✓ |
| `scope_decision_gate` | ✓ | ✓ | ✓ |
| `memory_sync_gate` | ✓ | ✓ | ✓ |
| `skill_precedence_gate` | ✓ | ✓ | — |
| `doubt_verify_gate` | ✓ | ✓ | ✓ |
| `review_loop` | ✓ | ✓ | ✓ |
| `hunt_loop` | ✓ | ✓ | ✓ |

**Gate scores:**

- craftflow: **9/9**
- cc10x: **9/9**
- superpowers: **7/9**

## 4. Context Management

| Signal | craftflow | cc10x | superpowers |
|--------|--------|--------|--------|
| `compact_hook` | ✓ | ✓ | ✓ |
| `state_persist` | ✓ | ✓ | ✓ |
| `context_eviction` | ✓ | ✓ | ✓ |
| `summarization` | ✓ | — | — |
| `context_resume` | ✓ | ✓ | ✓ |
| `token_tracking` | ✓ | ✓ | ✓ |

**Context management scores:**

- craftflow: **6/6**
- cc10x: **5/6**
- superpowers: **5/6**

## 5. Parallelism

| Signal | craftflow | cc10x | superpowers |
|--------|--------|--------|--------|
| `parallel_agents` | ✓ | ✓ | ✓ |
| `worktree_isolation` | ✓ | ✓ | ✓ |
| `subagent_dispatch` | ✓ | ✓ | ✓ |
| `concurrent_phases` | — | ✓ | — |

**Parallelism scores:**

- craftflow: **3/4**
- cc10x: **4/4**
- superpowers: **3/4**

## 6. Real Telemetry — craftflow (42 workflows)

**Workflow distribution:** BUILD=36, DEBUG=2, PLAN=4

**Remediation loop rates** (re_review / re_hunt / re_verify):

- `re_review`: mean=0.19, median=0, max=2, triggered in 6/42 runs (14.3%)
- `re_hunt`: mean=0.19, median=0, max=2, triggered in 6/42 runs (14.3%)
- `re_verify`: mean=0.02, median=0, max=1, triggered in 1/42 runs (2.4%)

**Events per workflow** (proxy for agent turn count): mean=9.57, median=8, max=35
