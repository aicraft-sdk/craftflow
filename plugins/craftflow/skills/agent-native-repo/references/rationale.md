# Why the setup looks like this

Each choice traces to a page in the AICraft wiki (`aicraft-sdk/aicraft-wiki`, under `wiki/`). Cite these when the user asks why.

| Choice | Reason | Wiki page |
|---|---|---|
| One canonical root `AGENTS.md`, nested per directory, default-no on what loads | Agents re-onboard from zero every run; stale or duplicated context compounds. Canon vs non-canon must be explicit. | `concepts/engineering/agent-native-monorepo-basis` |
| Every rule traces to a past failure; repeat mistakes become lint rules or tests | The harness is a living artifact tightened after each failure (the ratchet). | `concepts/harness/harness-as-living-artifact`, `concepts/claude-code/claude-code-mistake-pattern-linting` |
| Quality lives in deterministic gates; CI is binding | Generation outscales review; constraints, not reading diffs, decide what ships. | `concepts/harness/quality-as-agent-constraints`, `concepts/engineering/constraint-driven-quality-gates-agentic-code` |
| Fast checks after every edit; block cheatable escapes | Slow checks stall agents, noisy ones waste context, cheatable ones hide problems. | `concepts/harness/agentic-development-loop-linting` |
| Guard rejects skipped tests and fewer assertions | Agents optimise the signals they see; deterministic layers first, a thin LLM judge on top. | `concepts/engineering/verification-pyramid-agent-code-correctness` |
| Clean-context reviewer agent on every PR | Cognition: a review agent with no shared context finds about 2 bugs per PR, about 58% severe. | `concepts/multi-agent/multi-agent-what-works-cognition` |
| Stop hook blocks "done" while tests fail | Verification-gated completion instead of a single pass. | `concepts/harness/self-improving-loop-claude-code-setup` |
| Acceptance criteria and evidence before "done" | Agents need onboarding, permissions and a machine-verifiable definition of done. | `concepts/engineering/software-factory-trap`, `concepts/engineering/validation-first-agent-development-workflow` |
| One task, one writer, one worktree | Parallel writes conflict on implicit decisions; parallel intelligence with single-threaded writes works. | `concepts/multi-agent/multi-agent-what-works-cognition`, `guides/graph-of-loops-claude-code-repo-stack` |
| Work state in a tracker, not chat or markdown lists | Work state moves into external systems that outlive a session. | `concepts/multi-agent/agentic-coding-six-emerging-architectures` |
| No shared append-only log files | File memory loses concurrent writes, keeps contradictions and grows without decay. | `concepts/memory/filesystem-memory-failure-modes` |
| Claims expire; a sweeper releases them | Fleets need lifecycle tracking and recovery sweeps for stuck work. | `concepts/harness/swarm-management-fleet-coordination`, `concepts/harness/heartbeat-pattern` |
| Read-only explorers fan out; the main session writes | Keeps write authority single and blast radius small. | `concepts/claude-code/claude-code-subagent-explore-only-guard` |
