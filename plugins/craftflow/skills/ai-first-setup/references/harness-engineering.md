# Harness Engineering — Expert Knowledge Base

> **What this file is.** A self-contained reference on *harness engineering* for AI coding agents
> (Codex, Claude Code, Cursor, and similar). Paste it as context to make an agent an expert on the
> topic. Synthesized from the primary sources (OpenAI + Anthropic engineering posts) and the
> *Learn Harness Engineering* curriculum.
>
> **How to use this file (two modes):**
> 1. **Answer mode** — When asked a question about harness design, ground your answer in the
>    principles, failure catalog, and artifact schemas below. Prefer concrete, evidence-based
>    guidance over generic "best practices."
> 2. **Evaluate mode** — When asked to review/assess a harness, repo, `AGENTS.md`, or agent setup,
>    apply the **Evaluation Rubric** (§7). Score each subsystem, name specific gaps, and give the
>    single highest-leverage fix. Do not award credit for documents that exist but aren't enforced.
>
> **Governing principle to never lose:** reliability is *environment engineering*, not prompt
> engineering. If your advice could be summarized as "write a better prompt," it is probably wrong
> for this domain.

---

## 1. Core thesis

The strongest model still fails on real engineering tasks if there is no proper environment around
it. This is not a model problem; it is a harness problem. A harness does **not** make the model
smarter — it establishes a closed-loop *working system* that makes the model's output **reliable**.

Division of labor:
- **The model** decides *what* code to write.
- **The harness** governs *when*, *where*, and *how* it writes it, and *whether it is allowed to
  declare the work done*.

The empirically observed effect (reported by both OpenAI for Codex and Anthropic for Claude): the
*same model* on the *same task* shifts from "unreliable, produces broken output" to "reliable,
produces working output" purely by adding a harness (e.g. planner + generator + evaluator). The
change is qualitative, not marginal.

The questions harness engineering actually cares about:
- Which designs raise task-completion rates?
- Which designs reduce rework and incorrect "completions"?
- Which mechanisms keep long-running, multi-session tasks progressing?
- Which structures keep the system maintainable after many agent runs?

---

## 2. The five subsystems

A complete harness has five subsystems. A gap in any one is a predictable failure source.

| Subsystem | Its one job | Typical artifacts |
|---|---|---|
| **Instructions** | Tell the agent what to do, in what order, and what to read first. **Progressive disclosure**: a map, not an encyclopedia. | `AGENTS.md` / `CLAUDE.md` / `.cursor/rules`, `feature_list.json`, `docs/` |
| **State** | Track what's done, in progress, and next. **Persisted to disk** so the next session resumes exactly where the last ended. | `progress.md`, `feature_list.json`, `git log`, `session-handoff.md` |
| **Verification** | Only a passing test suite counts as evidence. The agent cannot declare victory without a runnable proof. | tests, lint, type-check, smoke runs, e2e pipeline |
| **Scope** | Constrain to one feature at a time with an explicit Definition of Done. No overreach, no half-finishing several things. | `feature_list.json` with acceptance criteria |
| **Session Lifecycle** | Initialize at the start; clean up at the end; leave a clean restart path. | `init.sh`, `clean-state-checklist.md`, `session-handoff.md` |

Memorize the contrast:
- The MODEL decides what code to write.
- The HARNESS governs when, where, and how — and what counts as "done" vs. "broken."

---

## 3. The agent session lifecycle

The session must follow a structured lifecycle, not a free-for-all. The harness governs every
transition; the model only chooses what code to write at each step.

```
START   1. Read the instruction file (AGENTS.md / CLAUDE.md / rules)
        2. Run init.sh (install, verify, health check)
        3. Read progress.md (what happened last time)
        4. Read feature_list.json (what's done, what's next)
        5. Check git log (recent changes)

SELECT  6. Pick EXACTLY ONE unfinished feature
        7. Work only on that feature

EXECUTE 8. Implement
        9. Run verification (tests, lint, type-check)
       10. If verification fails -> fix and re-run
       11. If verification passes -> record evidence

WRAP UP 12. Update progress.md
       13. Update feature_list.json (status + evidence)
       14. Record what is still broken or unverified
       15. Commit (only when safe to resume)
       16. Leave a clean restart path for the next session
```

Without the harness, step 9 degrades into "the agent says it looks fine." With the harness, step 9
is "tests pass, lint clean, types check."

---

## 4. Failure-mode catalog (diagnostic knowledge)

Each entry is a recurring way capable agents fail, and the harness mechanism that addresses it. Use
this in **Answer mode** to diagnose "why is my agent doing X," and in **Evaluate mode** to check
whether a harness defends against each failure.

| # | Failure mode | Root cause | Harness fix |
|---|---|---|---|
| 1 | Strong model fails on real tasks | Gap between benchmark ability and real-repo execution | Build the surrounding environment (all 5 subsystems) |
| 2 | "What harness?" — no shared definition | Treating it as prompt tweaking | Adopt the 5-subsystem model |
| 3 | Agent can't find/needs context that isn't in the repo | Knowledge lives in heads/chat, not in the repo | **Repo is the single source of truth**: if the agent can't see it, it doesn't exist |
| 4 | One giant instruction file is ignored or misapplied | Context overload; everything at once | **Progressive disclosure**: short entry file that links out on demand |
| 5 | Long tasks lose continuity across sessions | No persistent state; each session starts blind | Persist progress to disk; resume from `progress.md` |
| 6 | Agent starts work on a broken environment | No setup/verification phase | **Initialization phase** (`init.sh`): verify env health before any work |
| 7 | Agent overreaches and under-finishes | No scope boundary; "helpful" sprawl | One feature at a time + explicit Definition of Done |
| 8 | Scope drifts; agent reinterprets the task | Boundaries only in prose, easy to ignore | **Feature list as a primitive**: machine-readable scope it can't hand-wave |
| 9 | Agent declares victory too early | Confidence ≠ correctness; no proof required | Verification gate; "done" requires recorded evidence |
| 10 | Unit tests pass but the feature is broken | Verifying parts, not the whole | **End-to-end / full-pipeline run** is the real verification |
| 11 | Can't tell what the agent did or why it broke | No runtime visibility | **Observability inside the harness** (logs, traces, runnable repro) |
| 12 | Next session starts from a mess | Previous session left dirty state | **Clean-state handoff**: every session ends resumable |

---

## 5. The canonical artifacts (what "good" looks like at the file level)

Minimal harness = 4 files: an instruction file, `init.sh`, `feature_list.json`, `progress.md`.
Add `session-handoff.md` and `clean-state-checklist.md` as the project grows.

### Instruction file (`AGENTS.md`, with `CLAUDE.md` / `.cursor/rules` pointing to it)
- Short. A map, not a wall of text. Detail lives in `docs/`, linked on demand.
- Must contain: what to read before starting (in order); working rules (one feature at a time, no
  scope expansion, don't rewrite the feature list to hide work); the **Definition of Done**;
  canonical verification commands; the end-of-session procedure; and a "stop and ask a human" list
  (schema changes, auth/secrets, irreversible deletes, unrunnable verification).
- Tool-agnostic: one source of truth; each tool's entry point links to it rather than duplicating.

### `init.sh`
- Fail-fast (`set -euo pipefail`). Runs: env health check (tool versions, git), install, type-check,
  lint, tests. Dies on any failure so the agent does **not** build on a broken baseline.
- The agent runs it at the start of every session.

### `feature_list.json`
- Machine-readable scope. Each feature has: `id`, `title`, `description`, `status`
  (`todo|in_progress|done|blocked`), `acceptance_criteria`, `verification` (typed commands +
  expected result), and an **`evidence`** field.
- Hard rule: a feature cannot be `done` with an empty `evidence` field.

### `progress.md`
- Cross-session log, newest entry on top. Each entry: starting point, what was done, **what was
  verified (with the exact commands + results)**, what's broken/unverified, and the concrete next
  step. Concrete enough that a fresh agent knows exactly where to resume.

### `session-handoff.md`
- One-screen, high-signal note overwritten each handoff. The single "resume here" line, the active
  feature + branch + last safe commit, environment state, done-vs-not for that feature, and
  **"landmines"** (non-obvious context that would cost the next session an hour to rediscover).

### `clean-state-checklist.md`
- End-of-session gate covering: verification actually ran (with evidence), scope stayed honest, the
  tree is resumable, state is written down, commit is safe-to-resume. Final gate: *could a fresh
  agent with no memory resume cleanly from here?*

---

## 6. Decision heuristics (use in Answer mode)

- **"Should I add X to the harness?"** Only if it closes a real, observed failure mode (§4). Don't
  add ceremony that no agent reads or that no check enforces.
- **"My agent keeps doing too much."** Scope problem (#7/#8). Tighten `feature_list.json`, enforce
  one feature per session, make the Definition of Done explicit.
- **"My agent says done but it's broken."** Verification problem (#9/#10). Require recorded evidence;
  add an end-to-end run, not just unit tests.
- **"My agent forgets context between sessions."** State problem (#5/#12). Strengthen `progress.md`
  and add `session-handoff.md`; ensure clean-state handoff.
- **"One giant CLAUDE.md isn't working."** Instructions problem (#4). Split via progressive
  disclosure; keep the entry file short and link to `docs/`.
- **Prompt vs. harness.** If the fix is "phrase the request better," it's a prompt. If it's "change
  the environment, state, verification, scope, or lifecycle," it's a harness. This domain is the
  second kind.
- **What to measure.** Not how many docs you wrote — the *measured difference* between weak and
  strong harness: completion rate, rework rate, incorrect-completion rate, multi-session continuity.

---

## 7. Evaluation Rubric (use in Evaluate mode)

When asked to assess a harness/repo/setup, score each subsystem **0–2** and report findings.
**0 = absent, 1 = present but weak/unenforced, 2 = present and enforced with evidence.** Max 10.

Critical principle: **a document only counts if it is enforced.** An `AGENTS.md` that says "run
tests" but no gate that requires evidence scores like an unenforced rule, not a working control.

### Instructions (0–2)
- [ ] An entry instruction file exists and is short (progressive disclosure, not a monolith).
- [ ] It states read-order, working rules, and a concrete Definition of Done.
- [ ] It is tool-agnostic / single-source (no diverging duplicates per tool).
- Red flags: one giant file; no "what to read first"; rules with no enforcement.

### State (0–2)
- [ ] Progress is persisted to disk (`progress.md` or equivalent), newest-first, concrete.
- [ ] State records *verified* facts (commands + results), not vague summaries.
- [ ] A fresh agent could resume from the written state alone.
- Red flags: state lives only in chat history; entries say "made progress" with no specifics.

### Verification (0–2)
- [ ] Verification commands are defined and runnable (`init.sh` / scripts).
- [ ] "Done" requires recorded evidence; there is a gate, not just a suggestion.
- [ ] End-to-end / full-pipeline run exists, not only unit tests.
- Red flags: "done" based on the agent's say-so; only unit tests; no e2e; empty evidence fields.

### Scope (0–2)
- [ ] Machine-readable feature list with statuses and acceptance criteria.
- [ ] One-feature-at-a-time is enforced, not just encouraged.
- [ ] Explicit rule against rewriting the list to hide unfinished work.
- Red flags: scope only in prose; agent free to pick up arbitrary work; statuses don't match reality.

### Session Lifecycle (0–2)
- [ ] Initialization phase that verifies env health before work (`init.sh`).
- [ ] End-of-session clean-state procedure / checklist.
- [ ] Handoff mechanism for long or cross-tool sessions.
- Red flags: agent starts on an unverified env; sessions end mid-edit; no resumable commit.

### Scoring guide
- **9–10** Strong harness. Reliable multi-session, cross-tool operation likely.
- **6–8** Workable but with a clear weak subsystem — name it and give the one highest-leverage fix.
- **3–5** Mostly prompt-driven with harness decoration; reliability still depends on the human.
- **0–2** No harness; failures from §4 are expected.

Always end an evaluation with: (a) the weakest subsystem, (b) the single highest-leverage fix, and
(c) which specific §4 failure mode that fix prevents.

---

## 8. Anti-patterns (call these out)

- **Prompt-as-harness.** Believing a longer/cleverer prompt replaces environment, state, and
  verification. It does not.
- **Doc theater.** Many polished docs no agent reads and no gate enforces. Volume ≠ control.
- **The monolith.** One enormous instruction file; the agent drowns and skips it.
- **Trust-based "done."** Accepting the agent's confidence instead of a runnable proof.
- **Unit-test mirage.** Green unit tests on a feature that fails end to end.
- **Amnesiac sessions.** No persisted state, so every session re-learns or re-does work.
- **Dirty handoff.** Ending mid-edit with no resumable commit and no handoff note.
- **Status laundering.** Marking features `done` (or rewriting the list) to look finished.
- **Per-tool drift.** Separate, diverging instruction files for Claude Code vs. Cursor vs. Codex
  instead of one source of truth they all point to.

---

## 9. Glossary

- **Harness** — the engineered environment (instructions, state, verification, scope, lifecycle)
  around a model that makes its output reliable.
- **Progressive disclosure** — give the agent a short map that links to detail on demand, instead of
  dumping everything up front.
- **System of record** — the repo itself; if the agent can't see it in the repo, it effectively
  doesn't exist.
- **Definition of Done (DoD)** — explicit, verifiable criteria that must all pass before a feature
  is `done`.
- **Evidence** — recorded command + result proving verification ran and passed.
- **Clean state / resumable** — the repo is left so a fresh agent can pick up with no prior memory.
- **Overreach** — the agent expanding beyond the one assigned feature.
- **Premature victory** — declaring done without runnable proof.

---

## 10. Canonical sources

Primary:
- OpenAI — *Harness engineering: leveraging Codex in an agent-first world* — https://openai.com/index/harness-engineering/
- Anthropic — *Effective harnesses for long-running agents* — https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- Anthropic — *Harness design for long-running application development* — https://www.anthropic.com/engineering/harness-design-long-running-apps

Supplementary:
- LangChain — *The Anatomy of an Agent Harness*
- Thoughtworks / Martin Fowler — *Harness Engineering*
- HumanLayer — *Skill Issue: Harness Engineering for Coding Agents*
- Course: https://walkinglabs.github.io/learn-harness-engineering/ — Repo: https://github.com/walkinglabs/learn-harness-engineering

---

## TL;DR (load-bearing summary)

1. Reliability is environment engineering, not prompt engineering.
2. Five subsystems: instructions, state, verification, scope, session lifecycle. A gap in any one is
   a predictable failure.
3. One feature at a time. State on disk. Only passing tests (with recorded evidence) count as done.
4. Every session starts with init and ends in a clean, resumable state.
5. To evaluate a harness: score the five subsystems, credit only *enforced* mechanisms, and name the
   single highest-leverage fix tied to a specific failure mode.
