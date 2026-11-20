# Prose audit

Files scanned: 144. Advisory: file findings as cleanup tasks.

## Duplicated passages (33)

- agents/bug-investigator.md, agents/component-builder.md, agents/doc-syncer.md, agents/github-researcher.md, agents/plan-bakeoff-judge.md, agents/planner.md, agents/skill-author.md, agents/web-researcher.md: "if the `taskupdate` tool call is unavailable or fails (tool not found, permission error, or any error distinct from a normal task-not-found response — and note the task-not-found carve-out does not ap"
- skills/failure-digest/SKILL.md, skills/retro/SKILL.md, skills/status/SKILL.md: "**(b) claude code only, and only if (a) printed nothing.** read `~/.claude/plugins/installed_plugins.json`, take `craftflow@craftflow` → `installpath`, then run:"
- skills/architecture-patterns/SKILL.md, skills/code-review-patterns/SKILL.md, skills/debugging-patterns/SKILL.md: "**critical:** always get linehint from localsearchcode first. never guess line numbers."
- agents/bug-investigator.md, agents/component-builder.md, agents/plan-bakeoff-judge.md: "**critical:** now execute the `taskupdate` tool to mark `{task_id}` as completed. do not just write completed."
- agents/code-reviewer.md, agents/integration-verifier.md, agents/silent-failure-hunter.md: "**single final response rule (critical — this is why output reaches the router):** the router receives only your last response turn, not intermediate messages. therefore:"
- skills/failure-digest/SKILL.md, skills/retro/SKILL.md, skills/status/SKILL.md: "a non-empty output is `script`. `resolve()` follows the cursor symlink into the plugin checkout, and in claude code it points at the same plugin copy the skill was loaded from."
- agents/bug-investigator.md, agents/component-builder.md, agents/plan-bakeoff-judge.md: "do not edit `.craftflow/state/*.md` directly. emit structured `memory_notes`; the router/workflow finalizer persists memory."
- skills/failure-digest/SKILL.md, skills/status/SKILL.md: "**(c) otherwise** print exactly the following and stop. never fall back to reading `.craftflow/state` by hand."
- agents/plan-bakeoff-judge.md, agents/planner.md: "**after providing your final output**, you must call the `taskupdate` **tool** directly: `taskupdate({ taskid: "{task_id}", status: "completed" })` where `{task_id}` is from your task context prompt. "
- agents/component-builder.md, skills/test-driven-development/references/discipline-and-rationalizations.md: "**after tdd cycle complete**, verify no orphaned processes: `pgrep -f "vitest|jest" || echo "clean"`"
- skills/debugging-patterns/SKILL.md, skills/systematic-debugging/SKILL.md: "**before proposing fixes, add diagnostic instrumentation:** ``` for each component boundary:"
- skills/craftflow-router/references/build-graph-fast.md, skills/craftflow-router/references/build-graph-standard.md: "**clean-state check (advisory):** when `worktree_mode != "auto_created"` (no worktree exists — `references/worktree.md` covers the worktree-exists case) and `integration-verifier` returned pass on the"
- skills/debugging-patterns/SKILL.md, skills/systematic-debugging/SKILL.md: "**core principle:** always find root cause before attempting fixes. symptom fixes are failure."
- skills/craftflow-router/references/build-graph-fast.md, skills/craftflow-router/references/build-graph-standard.md: "**feature backlog (advisory):** before creating the memory update task below, when `integration-verifier` returned pass on the final phase, run the complete + vcr calls per `references/harness-self-ch"
- agents/github-researcher.md, agents/web-researcher.md: "**invoked by:** router directly (in parallel with its sibling agent). never invoked standalone."
- agents/code-reviewer.md, agents/silent-failure-hunter.md: "**mode:** read-only. you do not have edit tool. output `### memory notes (for workflow-final persistence)` section. router persists via task-enforced workflow."
- agents/component-builder.md, skills/test-driven-development/references/discipline-and-rationalizations.md: "**problem:** test runners (vitest, jest) default to watch mode, leaving processes hanging indefinitely."
- skills/debugging-patterns/SKILL.md, skills/systematic-debugging/SKILL.md: "**when system has multiple components (ci → build → signing, api → service → database):**"
- skills/ai-first-setup/SKILL.md, skills/workspace-setup/SKILL.md: "`ambiguous` → present `candidates` to the user and ask which one is `workspace_root` before continuing."
- agents/github-researcher.md, agents/web-researcher.md: "`sources_attempted` must list every backend you tried, even if it failed. `sources_used` must list only the backends that produced usable findings."
- agents/doc-syncer.md, agents/skill-author.md: "after emitting the router contract, call `taskupdate({ taskid: "{task_id}", status: "completed" })` where `{task_id}` is from the task context in your prompt."
- agents/github-researcher.md, agents/web-researcher.md: "all sources unavailable -> still save a file with the outage note and return `unavailable`."
- skills/_shared/router-protocol.md, skills/cursor-router/SKILL.md: "before execution, output one line: `-> {workflow} workflow (signals: {matched keywords})`"
- agents/bug-investigator.md, agents/component-builder.md: "do not create standalone report files. findings go in output + router contract only."
- skills/debugging-patterns/SKILL.md, skills/systematic-debugging/SKILL.md: "if systematic investigation reveals issue is truly environmental, timing-dependent, or external:"
- skills/verification-before-completion/SKILL.md, skills/verification-before-completion/references/core-gate-full.md: "if you haven't run the verification command in this message, you cannot claim it passes."
- skills/_shared/route-qa.md, skills/craftflow-router/references/qa-workflow.md: "not yet available in this release. announce `-> qa workflow (signals: …; pending)`, then route the request by the pre-harvest order error → debug, plan → plan, review → review, otherwise build, and co"
- skills/_shared/route-triage.md, skills/craftflow-router/references/triage-workflow.md: "not yet available in this release. announce `-> triage workflow (signals: …; pending)`, then route the request by the pre-harvest order error → debug, plan → plan, review → review, otherwise build, an"
- skills/debugging-patterns/SKILL.md, skills/systematic-debugging/SKILL.md: "random fixes waste time and create new bugs. quick patches mask underlying issues."
- agents/planner.md, skills/test-driven-development/references/integration-and-live-proof.md: "read `plugins/craftflow/skills/planning-patterns/references/live-verification-strategy.md`"
- agents/integration-verifier.md, skills/test-driven-development/references/integration-and-live-proof.md: "read `plugins/craftflow/skills/verification-before-completion/references/live-production-testing.md`"
- skills/craftflow-router/references/build-graph-standard.md, skills/craftflow-router/references/verify-dispatch.md: "set `chain_tail_task_id = learn_distill_task_id`. after `learn-distiller` completes, append `learn_distilled` to the event log."
- skills/failure-digest/SKILL.md, skills/status/SKILL.md: "use it only if that exits 0. never use an `installpath` script without that check (an `installpath` without the script is unusable)."

## Sentences that restate a default (15)

- `agents/code-reviewer.md:190`: 1. Use as many turns as needed for tool calls (Read, Grep, Bash) — output ZERO analysis text during these turns.
- `agents/integration-verifier.md:207`: 1. Use as many turns as needed for tool calls (Bash tests, Read, Grep) — output ZERO analysis text during these turns.
- `agents/silent-failure-hunter.md:140`: 1. Use as many turns as needed for tool calls (Grep, Read, Bash) — output ZERO analysis text during these turns.
- `agents/web-researcher.md:34`: mcp__brightdata__search_engine(query="{topic} best practices 2024", engine="google")
- `agents/web-researcher.md:39`: WebSearch(query="{topic} {reason} tutorial gotchas best practices")
- `agents/web-researcher.md:40`: WebFetch(url="{most_promising_url_from_search}", prompt="Extract key patterns, gotchas, best practices, and version-specific warnings for {topic}. Focus on {reason}.")
- `agents/web-researcher.md:48`: - Start broad: `{topic} best practices {year}`. Narrow only if results exceed 20 relevant hits.
- `skills/ai-first-setup/SKILL.md:459`: This skill reads asset files using skill-relative paths (the same convention used throughout Steps 2, 4, and 8 — e.g. `assets/charter/AI_FIRST.md.tpl`, with no home-directory or installed-skill-path p
- `skills/ai-first-setup/assets/governance/rfcs/template.md:21`: <!-- Describe the change in detail. Include code examples, diagrams, or API changes as needed. -->
- `skills/ai-first-setup/references/harness-engineering.md:11`: >    guidance over generic "best practices."
- `skills/ai-first-setup/references/harness-invocation-prompts.md:25`: evidence-based guidance over generic best practices. If the real fix reduces to "write a better
- `skills/architecture-patterns/SKILL.md:296`: - Choosing patterns because "it's best practice"
- `skills/code-review-patterns/SKILL.md:294`: | "Code looks clean" | Clean code can still be wrong. Verify functionality. |
- `skills/requesting-code-review/code-reviewer.md:12`: design patterns, and best practices. Your job is to review completed work
- `skills/skill-distillation/references/rubric.md:74`: pure prose ("be careful with X", "watch out for Y")?
