# Repository survey: deterministic guardrails and agent instructions in /home/user/craftflow

All sources are files in the repository at HEAD `cd1f57a` (`chore(craftflow): release v1.37.0`). Paths are relative to `/home/user/craftflow`. Counts come from `git grep` / `wc -l` over tracked files on 2026-11-16.

## 1. Languages, package managers, project layout

### Takeaway
This is not an application monorepo. It is a single Claude Code / Cursor plugin ("craftflow", v1.37.0) made of Markdown prompts (skills, agents) plus stdlib-only Python hook and validator scripts, with no package manager manifest at all. It is the published subtree of a larger monorepo (`tools/craftflow-plugin/` in "ai-craft"), and several scripts and docs still point at monorepo paths that do not exist here.

### Cited Findings
- 560 tracked files: 209 `.py`, 183 `.md`, 107 `.json`, 30 `.tpl`, 9 `.jsonl`, 6 `.sh`, 2 `.js`, 2 `.mdc`, 2 `.pyc` (tracked bytecode under `plugins/craftflow/scripts/__pycache__`). 504 of the 560 are under `plugins/craftflow/`. — `git ls-files`
- Top level: `.claude-plugin/marketplace.json`, `AGENT_CRITICAL_GUARDRAILS.md`, `CHANGELOG.md`, `NOTICE`, `README.md`, `docs/` (router policy map, rationale, protocol history, benchmarks), `knowledge/`, `plugins/craftflow/`. — [README.md](README.md) L213-225
- Plugin layout: `agents/` (15 `.md`), `skills/` (35 skill directories with a `SKILL.md`, plus `_shared`), `scripts/` (112 `.py` + 1 `.sh` + `schemas/`), `hooks/` (Claude Code bindings), `hooks.json` (Cursor bindings), `rules/` (2 Cursor `.mdc`), `config/`, `contracts/`, `templates/`, `tests/fixtures`, `tests/live`. — `plugins/craftflow/`; README L215-224 describes it as "15 agent definitions", "35 skills", "80 Python and shell scripts" (the README script count is lower than the 112 `.py` files `ls` shows today; agent and skill counts match).
- No `package.json`, `pyproject.toml`, `requirements*.txt`, `setup.cfg`, lockfile, `.toml`, `.cfg`, `.ini`, `.yml`/`.yaml` or `.gitignore` is tracked anywhere. — `git ls-files | grep` returned nothing
- Scripts declare "Python 3.9, standard library only". — [craftflow_docs_rot.py](plugins/craftflow/scripts/craftflow_docs_rot.py) L1-12; [craftflow_release_gate.py](plugins/craftflow/scripts/craftflow_release_gate.py) L1-12
- Subtree evidence: `ROOT = Path(__file__).resolve().parents[5]` and `PLUGIN = ROOT / "tools/craftflow-plugin/plugins/craftflow"` followed by an `assert PLUGIN == ...parents[1]` — [craftflow_release_gate.py](plugins/craftflow/scripts/craftflow_release_gate.py) L29-31; same constants in [craftflow_docs_rot.py](plugins/craftflow/scripts/craftflow_docs_rot.py). README: "Merging to `main` with changes under `tools/craftflow-plugin/**` runs the release gate ... tags the release and publishes it here." — [README.md](README.md) L228
- Git history: 58 commits; conventional-commit subjects all scoped `(craftflow)` (19 fix, 13 feat, 12 test, 9 chore, 2 docs, 1 refactor, 1 ci); 3 commits by `github-actions[bot]` (release commits). Recent pattern is `test(...) (red)` commit, then `feat`, then `fix` rounds, then `chore: release`. — `git log`

### Inferences
- In this checkout the release gate's path assertion (L31) cannot hold, because the repo root is not nested under `tools/craftflow-plugin/`; the gate and `docs_rot` are designed to run only in the source monorepo.
- Advice that assumes ESLint/TypeScript/Nx or an apps/packages monorepo does not apply to this repository itself. The relevant "lint" surface here is Python scripts and Markdown prompt files. (The plugin does ship templates aimed at JS/Nx consumer repos; see section 3.)

### Gaps
- The source monorepo (`ai-craft`) is not present, so its `scripts/*.mjs`, `docs/ai/`, `AI_FIRST.md`, and `.github/workflows` could not be read.

## 2. Linters, formatters, type checkers, test frameworks

### Takeaway
No off-the-shelf linter, formatter or type checker is configured. Enforcement is entirely custom: a large home-grown test suite, a fail-closed release gate, a docs-rot ratchet with a shrink-only baseline, a router "rule inventory" diff, and an advisory architecture-boundary linter driven by a JSON rule registry.

### Cited Findings
- No Ruff/flake8/pylint/mypy/black/ESLint/Prettier/markdownlint/shellcheck config file is tracked. — `git ls-files` (see section 1)
- Yet the Python carries 320 `# noqa` comments using Ruff/flake8-style codes (E402, BLE001, E731, F401, PLC0415, D401). — `git grep -F '# noqa'` (details in section 6)
- Test framework is custom, not pytest/unittest: each `tests/fixtures/test_*.py` is a standalone script that prints `Results: N passed, M failed` and exits 1 on failure. — [test_gate_check.py](plugins/craftflow/tests/fixtures/test_gate_check.py) L9-10 and final lines; runner [run_fixture_suite.sh](plugins/craftflow/tests/fixtures/run_fixture_suite.sh) L1-13
- 77 `test_*.py` fixture files in `plugins/craftflow/tests/fixtures/` (143 directory entries including JSON replay fixtures); `plugins/craftflow/scripts/craftflow_hook_unit_tests.py` is a single 29,924-line file with 943 `test_` functions. — `ls`, `wc -l`, `grep -c`
- The fixture runner has a pinned known-failure list (`CRAFTFLOW_CI_KNOWN_ENV_FAILURES`, "`<test file> | <expected failed count> | <reason>`"); a listed file that passes, or fails with a different count, fails the job. With `CI=true` a fixture that prints a `SKIP: ... CI must not skip` line fails. — [run_fixture_suite.sh](plugins/craftflow/tests/fixtures/run_fixture_suite.sh) L4-13
- Release gate steps, fixed order: `version_consistency`, `hook_unit_tests`, `fixture_suite_a`, `fixture_suite_b`, `rule_inventory_diff`, `workflow_replay_check` (+ route replay), `graph_check`, `event_contract`, `decisions`, `docs_rot`, `plugin_validate` (`claude plugin validate`). Last line must be exactly `RELEASE GATE: OK`. — [craftflow_release_gate.py](plugins/craftflow/scripts/craftflow_release_gate.py) L48-80; [hooks/README.md](plugins/craftflow/hooks/README.md) L119-140
- Three of those steps call Node scripts at the monorepo root (`scripts/gen-craftflow-graph.mjs`, `scripts/verify-craftflow-event-contract.mjs`, `scripts/verify-decision-format.mjs`) that are not in this repository. — [craftflow_release_gate.py](plugins/craftflow/scripts/craftflow_release_gate.py) L74-76
- Docs-rot ratchet: checks D1..D10 (agent lists vs files, dispatched agents, Cursor parity, skill count, hook scripts exist and are documented, version strings, read-map coverage, agent skill references, install script links, workflow tables). NEW or STALE ids exit 1; "The baseline may only shrink"; unresolvable base ref "fails closed". — [craftflow_docs_rot.py](plugins/craftflow/scripts/craftflow_docs_rot.py) L2-11; [craftflow_docs_rot_checks.py](plugins/craftflow/scripts/craftflow_docs_rot_checks.py) L112-231
- The docs-rot baseline currently grandfathers 18 violations (3 `rot:D3:missing:*`, 15 `rot:D5:undocumented:*`). — [docs-rot-baseline.json](plugins/craftflow/tests/fixtures/docs-rot-baseline.json)
- Router rule inventory: splits the router prompt corpus into units with stable ids and "accounts for every removed occurrence against the policy map", exit 1 on any gap. — [craftflow_rule_inventory.py](plugins/craftflow/scripts/craftflow_rule_inventory.py) L2-15; policy map is [docs/router-policy-map.md](docs/router-policy-map.md) (1,099 lines)
- Version consistency gate across all version-bearing files. — [craftflow_version_consistency_check.py](plugins/craftflow/scripts/craftflow_version_consistency_check.py) L1-6
- Architecture/boundary check: `craftflow_arch_lint.py` is an "Advisory architecture-boundary linter" reading `.craftflow/state/project/arch-rules.json`, reporting WHAT/WHY/FIX per finding. "Never fails a workflow"; exit 0 even with findings. It self-seeds one rule, `no-tools-import-packages` (forbids `@ai-craft/` imports inside `tools/**/*.ts|tsx|js`). Detection is a regex on literal import/require strings in JS/TS only. — [craftflow_arch_lint.py](plugins/craftflow/scripts/craftflow_arch_lint.py) L4-25, L43-55, L67-75
- Clean-state check: advisory scan of a workflow's added diff lines for `console.log(`, `debugger`, un-ticketed `TODO`, and new disable comments (`eslint-disable|@ts-|prettier-ignore|noqa|type: ignore|pylint: disable`). "Never blocks a workflow." — [craftflow_clean_state_check.py](plugins/craftflow/scripts/craftflow_clean_state_check.py) L4-13, L30-44
- Agent output contract: JSON schema `contracts/agent_contract.schema.json` validated by `craftflow_contract_validate.py`; hook mode for `contractValidation` is `audit`. — [craftflow_contract_validate.py](plugins/craftflow/scripts/craftflow_contract_validate.py) L2-16; [config/hook-mode.json](plugins/craftflow/config/hook-mode.json)
- Coverage thresholds exist only as a copy-in template for consumer projects (statements 80, branches 70, functions 80, lines 80), checked by the `component-builder` agent, not by a tool here. — [templates/coverage-thresholds.json](plugins/craftflow/templates/coverage-thresholds.json)
- Code-smell and "tangle" rules (Hidden link, Needless link, Surplus, special-case branch) are prose heuristics in skills, added in commit `a2b823e`; the review skill states each smell is "never a hard violation. Skip anything tooling already enforces." — [code-review-patterns/SKILL.md](plugins/craftflow/skills/code-review-patterns/SKILL.md) L160, L177-183; [code-generation/SKILL.md](plugins/craftflow/skills/code-generation/SKILL.md) L37-42

### Inferences
- The repository already practises the ratchet pattern (shrink-only baseline, pinned known-failure counts, fail-closed on unverifiable base) for docs and prompt content, but not for Python code style or suppressions.
- The only two code-content linters (`arch_lint`, `clean_state_check`) are advisory by design and target consumer JS/TS projects; neither gates this repo's own Python.

### Gaps
- Whether Ruff (or another linter) runs against this code in the source monorepo is not determinable here; the `noqa` codes suggest a linter is or was used, but no config is in this repository.

## 3. Agent-facing files and what they instruct

### Takeaway
There is no `AGENTS.md`, `CLAUDE.md`, `.claude/` or `.cursor/` directory in this repository. The repo-level agent guidance is one 20-line file, `AGENT_CRITICAL_GUARDRAILS.md`, several of whose bullets are mechanically checkable but have no check in this repo, and which cites files that are absent here. The bulk of agent instruction is the plugin's own product: about 14,000 lines of skill, agent and rule Markdown.

### Cited Findings
- `AGENT_CRITICAL_GUARDRAILS.md`: 20 lines, 1,284 bytes, 9 bullets. It points to `AI_FIRST.md` and `docs/ai/decisions/` for rationale; neither exists in this repo. — [AGENT_CRITICAL_GUARDRAILS.md](AGENT_CRITICAL_GUARDRAILS.md) L3-4; `ls` confirms absence
- Its bullets (L6-20): never bypass `craftflow-router`; TDD RED→GREEN→REFACTOR mandatory; "File size: prefer < 400 lines per file, max 500; functions ~30–40 lines"; never hand-edit released `CHANGELOG.md` sections or version fields; never skip TDD/review/verifier phases; never edit `.craftflow/state/*.md` memory directly; no secrets committed; "No `any` / `@ts-ignore` without an inline justification comment"; spec before code.
- Status of each bullet against mechanisms found in this repo:
  - Memory-file edits: enforced by hooks in block mode (`protectedWrites`, `memoryWrites`: `block`). — [config/hook-mode.json](plugins/craftflow/config/hook-mode.json); [hooks/hooks.json](plugins/craftflow/hooks/hooks.json)
  - Version/CHANGELOG edits: enforced by the version consistency gate in the release gate. — [craftflow_release_gate.py](plugins/craftflow/scripts/craftflow_release_gate.py) L60
  - File size max 500 lines: no check found. 61 of 209 tracked `.py` files exceed 500 lines (largest: `craftflow_hook_unit_tests.py` 29,924; `craftflow_pretooluse_guard.py` 3,461; `craftflow_pretooluse_bash_guard.py` 3,213; `craftflow_hooklib.py` 1,948). Markdown over 500 lines: `skills/cursor-router/SKILL.md` 1,206, `docs/router-policy-map.md` 1,099, `CHANGELOG.md` 1,097, `skills/ai-first-setup/SKILL.md` 530. — `wc -l`
  - Function length ~30–40 lines: no check found.
  - `any` / `@ts-ignore` justification: no check found; the repo has no TypeScript (2 `.js` files, both templates under `skills/ai-first-setup/assets/tools/`).
  - No secrets: no secret scanner config found in this repo.
  - TDD-first, spec-before-code, router-first: prose plus workflow-level hooks (task metadata guard in block mode); no commit-level check found here.
- The guardrails file says CI triggers on `tools/craftflow-plugin/**`, a path that does not exist in this repo. — [AGENT_CRITICAL_GUARDRAILS.md](AGENT_CRITICAL_GUARDRAILS.md) L11-13
- Cursor rules shipped by the plugin: `rules/craftflow-router.mdc` (58 lines, `alwaysApply: true`; router first, trigger keywords, exact-phrase opt-out) and `rules/craftflow-state.mdc` (79 lines; state directory conventions). — [craftflow-router.mdc](plugins/craftflow/rules/craftflow-router.mdc) L1-58; [craftflow-state.mdc](plugins/craftflow/rules/craftflow-state.mdc) L1-25
- Largest instruction files (lines): `skills/cursor-router/SKILL.md` 1,206; `skills/ai-first-setup/SKILL.md` 530; `skills/planning-patterns/SKILL.md` 494; `skills/craftflow-router/SKILL.md` 492; `skills/update/SKILL.md` 485; `skills/debugging-patterns/SKILL.md` 470; `agents/planner.md` 449. The 2 rules + 15 agents + 35 `SKILL.md` + 2 READMEs + guardrails + 3 root docs total 14,254 lines. — `wc -l`
- Users are told to add the router to `~/.claude/CLAUDE.md` themselves. — [README.md](README.md) L149; [plugins/craftflow/README.md](plugins/craftflow/README.md) L217
- Runtime hooks (Claude Code), from `hooks/hooks.json`: PreToolUse on Edit|Write, Read, EnterPlanMode, WebFetch, Bash (three guards: protected-path/policy guard, destructive-command guard, catastrophic "safe shell" denylist) and an optional risk gate; SessionStart (context, hook import self-check); PostToolUse (artifact integrity, memory restore, repeat-call advisory); TaskCompleted; SubagentStop; Pre/PostCompact; Stop (persist, optional stop-verify command, optional stop gate); StopFailure; InstructionsLoaded audit; UserPromptSubmit. — [hooks/hooks.json](plugins/craftflow/hooks/hooks.json); summary in [hooks/README.md](plugins/craftflow/hooks/README.md) L5-17
- Hook modes: `block` for `protectedWrites`, `memoryWrites`, `taskMetadata`, `stateReadCompaction`, `bashDestructiveTraversal`; `audit` for the other ten (including `artifactIntegrity`, `contractValidation`, `subagentStopAudit`). — [config/hook-mode.json](plugins/craftflow/config/hook-mode.json)
- Loop caps and plan/merge policy are in a schema-validated policy file that "mirror[s] router prose (ADR-0073)": `remediation_cycles_max` 3, `doubt_cycles_max` 3, `fix_verify_cycles_max` 3, `plan_review_passes_max` 2, `native_plan_mode: never`, `plan_commit: never`. An invalid policy gives defaults and callers "take their most restrictive branch". — [config/policy.json](plugins/craftflow/config/policy.json); [craftflow_policy.py](plugins/craftflow/scripts/craftflow_policy.py) L2-11; [craftflow_gate_check.py](plugins/craftflow/scripts/craftflow_gate_check.py) L2-18
- Stop-verify hook (run a configured build/test command before the session may stop) is "Opt-in, OFF BY DEFAULT"; stop gate `mode` is `off`. — [craftflow_stop_verify.py](plugins/craftflow/scripts/craftflow_stop_verify.py) L8-14; [config/stop-gate.json](plugins/craftflow/config/stop-gate.json)
- The hooks README notes the Bash guard is "a hand-rolled lexer that kept opening holes across five fix cycles" and recommends extracting it with "a differential test against real bash". — [hooks/README.md](plugins/craftflow/hooks/README.md) L6
- The plugin ships an `ai-first-setup` skill that scaffolds, for other repos, `AGENTS.md` templates, governance docs, and validators: `agents-md-lint.js` (232 lines) / `.sh` (116), `ai-contract-pack-lint.js` (124) / `.sh` (120), CI workflow templates, a Husky snippet and a non-JS pre-commit hook. — `plugins/craftflow/skills/ai-first-setup/assets/`
- The agents-md validator rules: root `AGENTS.md` must exist, ≤ 8,192 bytes, five required sections (Project / Scope, Non-Negotiable, Build, Security, Agent Operating), forbidden path literals `/src/`, `/lib/`, `/components/`; subfolder files may only override. — [agents-md-validation.md](plugins/craftflow/skills/ai-first-setup/references/agents-md-validation.md) L9-58; [agents-md-validator.json.tpl](plugins/craftflow/skills/ai-first-setup/assets/config/agents-md-validator.json.tpl)

### Inferences
- The repo ships an `AGENTS.md` linter as a product for other repositories but does not apply it to itself: it has no `AGENTS.md`, and its own guardrails file is not covered by any validator found here.
- The clearest prose-only, mechanically checkable rules are the file-size cap (already violated by 61 Python files), function length, and the suppression-justification rule.

### Gaps
- `AI_FIRST.md`, `docs/ai/decisions/`, `docs/ai/QUALITY.md`, `docs/plans/` and `docs/incidents/` are referenced repeatedly but are not in this repository, so their content is unknown.

## 4. Pre-commit hooks and CI workflows

### Takeaway
No CI workflow and no installed pre-commit hook exist in this repository. CI is described in prose and code comments as living in the source monorepo. The only pre-commit artifact is an optional 18-line script that runs the consumer project's tests.

### Cited Findings
- No `.github/` directory, no `.husky/`, no `lefthook.yml`, no `.pre-commit-config.yaml`; `.git/hooks` contains only samples and `core.hooksPath` is unset. — `ls`, `git config core.hooksPath`
- `plugins/craftflow/hooks/pre-commit` (18 lines): detects vitest/jest/npm test via `package.json`, or pytest via `pyproject.toml`/`setup.py`, and blocks the commit on test failure. It is installed manually by copy (`cp ... .git/hooks/pre-commit`). In this repo neither manifest exists, so it would exit 0 without running anything. — [hooks/pre-commit](plugins/craftflow/hooks/pre-commit) L1-18
- CI referenced but not present: "Used by .github/workflows/craftflow-ci.yml" — [run_fixture_suite.sh](plugins/craftflow/tests/fixtures/run_fixture_suite.sh) L2; "the `release-gate` job in `craftflow-ci.yml` runs the same gate on pull requests" — [hooks/README.md](plugins/craftflow/hooks/README.md) L121-123
- Releases: automatic on merge to `main`; bump derived from conventional-commit type (`feat` minor; `fix`/`perf`/`revert` patch; `!`/`BREAKING CHANGE` major). — [README.md](README.md) L226-239
- CI workflow templates for consumer repos (`agents-md-validate.yml.tpl`, `ai-checks.generic.yml.tpl`, `ai-checks.nx.yml.tpl`, `docs-coverage.yml.tpl` and `.sh` variants) ship under the `ai-first-setup` skill. — `plugins/craftflow/skills/ai-first-setup/assets/ci/`

### Inferences
- A contributor or agent working only in this checkout gets no automatic check at commit or push time; every gate described runs upstream.

### Gaps
- The content of `craftflow-ci.yml` and the publish workflow could not be inspected.

## 5. Mechanisms for recording lessons learned or agent failures

### Takeaway
The plugin has a fairly complete failure-to-lesson pipeline for projects that use it (event logs, a deterministic failure miner, a per-workflow retro whose rubric explicitly routes mechanical defects to a deterministic check, a memory file for gotchas, a skill-candidate ledger, a reliability-gates ledger). None of that state is stored in this repository, and the conversion from a retro proposal to an actual rule is manual.

### Cited Findings
- `craftflow_learn_scan.py`: "Deterministic failure-pattern miner" over `.craftflow/state/workflows/*.events.jsonl`, clustering failure signals by normalized signature with occurrence counts. — [craftflow_learn_scan.py](plugins/craftflow/scripts/craftflow_learn_scan.py) L2-21
- `learn-distiller` agent: distills clusters with occurrences ≥ 2 "into durable gotcha patterns" emitted as `MEMORY_NOTES`. — [agents/learn-distiller.md](plugins/craftflow/agents/learn-distiller.md) L11
- `failure-digest` skill: read-only ranking of recurring failure signatures; gotchas land in `patterns.md ## Common Gotchas`. — [skills/failure-digest/SKILL.md](plugins/craftflow/skills/failure-digest/SKILL.md) L3, L16
- `retro` skill + `craftflow_retro.py`: read-only extraction of 11 friction signals from one workflow, mapped to at most 5 ranked, evidence-cited proposals. Rubric row 2: repeated remediation cycles sharing "one mechanical defect shape" → category "deterministic check", destination `harness-self-checks.md`, a `scripts/craftflow_arch_lint.py` rule, or a `hooks/hooks.json` hook. Row 3: judgment issues → "reviewer standard" in `code-review-patterns/SKILL.md` or `docs/ai/QUALITY.md`. Rule: "Mechanical violations become checks; judgment issues become reviewer prose rules. Never propose adding steering-file bloat." — [skills/retro/SKILL.md](plugins/craftflow/skills/retro/SKILL.md) L9-22, L95-108; [craftflow_retro.py](plugins/craftflow/scripts/craftflow_retro.py) L2-13; [craftflow_retro_signals.py](plugins/craftflow/scripts/craftflow_retro_signals.py) L2-7
- Retro "Never writes anything"; implementing a proposal is a separate routed development task. — [skills/retro/SKILL.md](plugins/craftflow/skills/retro/SKILL.md) L11, L22
- Session memory: project tier `.craftflow/state/project/{activeContext,patterns,progress}.md`; "learned gotchas" and "project conventions" go to `patterns.md`; only the router finalizer writes. — [skills/session-memory/SKILL.md](plugins/craftflow/skills/session-memory/SKILL.md) L53, L68, L160-161
- Skill-candidate ledger with anti-rot pruning (stale candidates > 90 days removed) and a staged `skill-distillation` skill. — [craftflow_skill_ledger.py](plugins/craftflow/scripts/craftflow_skill_ledger.py) L2-24; [skills/skill-distillation/SKILL.md](plugins/craftflow/skills/skill-distillation/SKILL.md) L3
- Reliability-gates ledger: "Append-only, durable ledger of proven invariants", 3 seeded gates, manual promotion `experimental → soak → blocking`. — [craftflow_reliability_gates.py](plugins/craftflow/scripts/craftflow_reliability_gates.py) L4-24
- Repeat-call guard: PostToolUse advisory when the agent repeats an identical tool call; advisory only. — [craftflow_posttooluse_repeat_guard.py](plugins/craftflow/scripts/craftflow_posttooluse_repeat_guard.py) L2-17
- An incident write-up is cited as the origin of the destructive-command guard (`docs/incidents/2026-07-25-phase3-verifier-rm-attempt.md`), but that file is not in this repo. — [hooks/README.md](plugins/craftflow/hooks/README.md) L6
- No `.craftflow/` directory, memory file, lessons file or incident log is tracked in this repository. — `git ls-files`
- An agents-md lint exists only as a template for consumer repos (section 3).

### Inferences
- The retro rubric already encodes the principle "mechanical failure → deterministic check", and names `arch_lint` rules and hooks as destinations. What is missing in the repo is (a) an enforcing (non-advisory) target for such rules, since `arch_lint` never fails, and (b) any generic linter for the plugin's own Python.

### Gaps
- No evidence in this repository shows how often retro proposals have been turned into checks; that history would be in the source monorepo's state and decision records.

## 6. Visible suppressions

### Takeaway
Suppressions are almost entirely Python `# noqa` (320 across 92 files), dominated by import-order (E402) and broad-exception (BLE001) codes, with no linter config in the repo to give them meaning. There are no real ESLint or TypeScript suppressions.

### Cited Findings
- `# noqa`: 320 occurrences in 92 `.py` files. By code: E402 about 156, BLE001 about 105, E731 37, F401 6, PLC0415 5, D401 3. Top files: `craftflow_context_nudge.py` 29, `craftflow_stop_gate_core.py` 23, `test_router_budget.py` 20, `craftflow_stop_gate.py` 20, `craftflow_hook_unit_tests.py` 14. — `git grep -F '# noqa' -- '*.py'`
- Many BLE001 suppressions carry an inline reason, e.g. `except Exception:  # noqa: BLE001 - classifier failure must not drop the usage record`. — [craftflow_subagent_stop_audit.py](plugins/craftflow/scripts/craftflow_subagent_stop_audit.py) L26
- `except Exception` appears 409 times in tracked `.py` (308 outside `craftflow_hook_unit_tests.py` and `tests/`); zero bare `except:`. — `git grep -E 'except Exception'`
- `# type: ignore`: 15 occurrences in 8 files. `# pragma: no cover`: 7. `nosec`: 0. `shellcheck disable`: 0. — `git grep`
- `eslint-disable` (3), `@ts-ignore` (1) and `pylint: disable` (4) appear only as a detection regex and as test-fixture strings for the clean-state check, not as live suppressions. — [craftflow_clean_state_check.py](plugins/craftflow/scripts/craftflow_clean_state_check.py) L42-44; [test_craftflow_clean_state_check.py](plugins/craftflow/tests/fixtures/test_craftflow_clean_state_check.py) L493-519
- The clean-state check flags newly added disable comments in a workflow diff, advisory only. — [craftflow_clean_state_check.py](plugins/craftflow/scripts/craftflow_clean_state_check.py) L4-8

### Inferences
- The E402 volume matches the visible pattern of `sys.path.insert(0, ...)` before sibling imports (e.g. `test_gate_check.py` L27), a consequence of having no package structure.
- The BLE001 volume matches the hooks' stated fail-open design ("Fail-open: never raises").

### Gaps
- The per-code counts for `noqa` are approximate (trailing-whitespace variants were merged by hand); the 320 total and 92-file count are exact.
- I did not measure how many `noqa` comments have a written justification.
