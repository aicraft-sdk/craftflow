---
name: agent-native-repo
description: "Use to make any repo agent-native for many people's agents: CI gates, task claims, no overlap."
allowed-tools: Read Write Edit Bash Grep Glob Skill
---

## When To Use

The user asks to make a repository "agent-native", "agent-only", "ready for many agents", or "safe for Cursor, Claude Code and Codex at once"; to stop agents overlapping work; or to enforce quality on agent PRs whatever tool wrote them. Works on new and existing repos. Not for a single developer's local setup: that is `craftflow:ai-first-setup`, which this skill builds on.

## Mission

Make the repository itself the harness, so quality and coordination do not depend on which tool or person runs the agent:

| Layer | What gets installed | Enforced by |
|---|---|---|
| Instructions | One canonical root `AGENTS.md` + Agent Session Protocol; `CLAUDE.md` = `@AGENTS.md` import; one Cursor pointer rule | Code owners on harness files |
| Quality gates | `tools/agent_guards.py diff-check`: no deleted/skipped tests, fewer assertions, new suppressions, or unlabelled harness edits; Claude hooks for fast local feedback; independent AI review | CI (`agent-guards`, `ai-review`), branch protection |
| Coordination | One task = one claim = one branch = one PR. `task-ref` fails a PR with no task or a task another open PR holds; `claim-sweeper` releases stale claims | CI, scheduled workflow |
| Memory | Canon (`AGENTS.md`, `docs/`) vs work state (tracker) vs handoffs (PR body). No shared append-only log files | Protocol + review |

CI is the contract: it is the only gate every tool's agents pass through. Local hooks are accelerators. Read `references/rationale.md` when the user asks why.

---

## Step 0 — Interview and detect

Detect first, then ask only what detection could not answer, in one message:

1. `git rev-parse --show-toplevel` → `repo_root`. `git remote get-url origin` → GitHub or not. Default branch: `git symbolic-ref refs/remotes/origin/HEAD`.
2. Mode: **existing** if any of `AGENTS.md`, `CLAUDE.md`, `.cursor/rules/`, `.github/workflows/` exist, else **new**.
3. Commands: read `package.json` scripts / `Makefile` / `pyproject.toml` / `go.mod` for `TEST_CMD` and a fast check (`FAST_CHECK_CMD`: lint + typecheck, under ~30 s). Ask if unclear; never guess a command you could not see.
4. Ask:
   - Tracker: **GitHub Issues** (default; claim = assignee + `in-progress` label + draft PR) or **beads** (`bd update <id> --claim` is atomic; every contributor needs `bd`).
   - Human code owners for harness files (GitHub handles or a team).
   - Independent AI review in CI: yes (default, needs the `ANTHROPIC_API_KEY` secret) or no, and the reviewer model (prefer a different model from the one most authors use).
   - Claim TTL in hours (default 48).

Not a GitHub repo → install instructions, hooks and the guard tool; skip the workflows, CODEOWNERS and templates, and say which CI system needs equivalent jobs.

## Step 1 — Baseline governance

- No root `AGENTS.md` or no `docs/ai/` → invoke `craftflow:ai-first-setup` first and let it finish (it scaffolds `AGENTS.md`, `docs/ai/`, the context pack and `agents-md-lint`). Then continue here.
- Already present → do not re-run it; run its validator if installed (`tools/scripts/agents-md-lint.*`) and note the result.

## Step 2 — Instructions (never overwrite)

Existing files are merged, never replaced. For each file: absent → create from the template; present → show the user a diff of the proposed merge and apply only what they accept.

1. Root `AGENTS.md`: insert `assets/instructions/agents-protocol.md.tpl` as its `## Agent Session Protocol` section. Fill `{{CLAIM_STEPS}}` from `references/claim-steps.md` for the chosen tracker, `{{CLAIM_TTL_HOURS}}`, and `{{NON_CANON_DIR}}` (`.specs/` unless the repo already has a plans dir).
   - A session protocol from `ai-first-setup` that tells agents to append to `progress.md` or overwrite `session-handoff.md` gets those lines replaced: with parallel agents they are lost-write hotspots. The files can stay for humans.
   - Over ~300 lines → list sections that describe rather than instruct and propose moving them to `docs/` or a nested `AGENTS.md`. Delete nothing without approval.
2. `CLAUDE.md` from `assets/instructions/CLAUDE.md.tpl`. If one exists, ensure line 1 is `@AGENTS.md` and propose moving rules duplicated from AGENTS.md out of it.
3. `.cursor/rules/agents.mdc` from `assets/instructions/cursor-agents.mdc.tpl`. Existing `alwaysApply` rules that copy AGENTS.md content → propose deleting the copies; keep glob-scoped rules.

## Step 3 — Guards and hooks

1. Copy `assets/tools/agent_guards.py` → `tools/agent_guards.py`, and `assets/tools/claude-stop-gate.sh` → `tools/claude-stop-gate.sh` (fill `{{TEST_CMD}}`).
2. `assets/config/guards.json.tpl` → `.agents/guards.json`. Tune `test_name_globs`, `test_dirs` and `harness_globs` to this repo (add its lint configs; drop globs for tools it does not use).
3. `assets/claude/settings.json.tpl` → `.claude/settings.json` (fill `{{FAST_CHECK_CMD}}`). Existing file → merge the `hooks` keys only.
4. Self-test: `python3 tools/agent_guards.py diff-check --base <default-branch>` must run (exit 0 or 2, never 1). On the setup change itself, expect only `harness-change-unlabelled` findings.

## Step 4 — GitHub workflows and templates

1. `.github/workflows/agent-guards.yml` from `assets/github/workflows/agent-guards.yml.tpl` (always).
2. `.github/workflows/claim-sweeper.yml` from `claim-sweeper.yml.tpl` when the tracker is GitHub Issues; `{{SWEEP_CRON}}` = every 6 hours at a random minute (e.g. `23 */6 * * *`).
3. `.github/workflows/ai-review.yml` from `ai-review.yml.tpl` when review was chosen; fill `{{REVIEW_MODEL}}` and tell the user to add the `ANTHROPIC_API_KEY` secret.
4. `.github/pull_request_template.md`, `.github/ISSUE_TEMPLATE/agent-task.md` (fill `{{TEST_CMD}}`), and `CODEOWNERS` (fill `{{HUMAN_OWNERS}}`; existing file → append the harness block).
5. Leave existing workflows alone. If one already does the same job, say so and ask before adding a second.

## Step 5 — Tracker setup

- GitHub Issues: create the labels `ready`, `in-progress` and `harness-change` with `gh api repos/{owner}/{repo}/labels -f name=<label>`, skipping ones that exist. Ask first unless the user already agreed in Step 0.
- beads: run `bd init` if `.beads/` is absent, and check `.beads/` is not in a cloud-synced folder (iCloud/Dropbox corrupt its database).

## Step 6 — Branch protection (ask, never assume)

Branch protection changes the rules for everyone on the repo. Show the exact commands from `references/branch-protection.md` with the repo's names filled in, and run them only after an explicit yes.

## Step 7 — Verify and report

Run and record real exit codes:

- `python3 tools/agent_guards.py diff-check --base <default-branch>`
- the AGENTS.md validator, if installed
- a YAML parse of every workflow written

Report a table: file · created / merged / skipped · why. Then list what is left for the user: add the `harness-change` label to the setup PR (it edits harness files by design), add `ANTHROPIC_API_KEY`, apply branch protection, and seed the first `ready` tasks. Commit on a branch and open a PR only if the user asked; never push to the default branch.

## Rules

- Never overwrite an existing file; merge with a shown diff.
- Never weaken an existing check, hook or workflow to make the new ones pass.
- No secrets in any file; the API key is a repository secret.
- Do not invent commands. A placeholder you could not fill stays visible and goes in the report.
