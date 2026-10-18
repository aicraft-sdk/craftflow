# craftflow Hooks

This directory now serves two different purposes:

1. **Plugin runtime hooks** via `hooks.json`
   - `PreToolUse` — protected writes guard (Edit, Write, and Bash matchers) — the Bash matcher inspects write-shaped commands (redirects, `tee`, python `open()`/`write_text()`/`os.system`/`subprocess`/`shutil`/`os.rename` writes) for protected-path targets, closing a prior bypass where Bash-only agents could overwrite memory files undetected; destructive-command guard (Bash matcher — denies destructive commands whose resolved target is in-cwd (e.g. `rm -rf packages/agent-cli`, `rm -rf .`) as well as ones that escape the session's own cwd/worktree, e.g. a worktree relative-path escape; covers `rm`, `rmdir`, `mv`, `shred`, `truncate`, `dd`, `chmod`, `find -exec`/`-execdir`/`-ok`/`-okdir`/`-delete`, `git clean`/`reset --hard`/`push --force`, with fail-closed handling of dynamic ($/backtick) targets; see `docs/incidents/2026-07-25-phase3-verifier-rm-attempt.md`); catastrophic-command guard (Bash matcher — `craftflow_safe_shell_guard.py`, an absolute denylist for `rm -rf /`, `mkfs`, and fork-bomb patterns regardless of path, plus an opt-in recursive-grep-against-a-broad-path advisory; concept ported from `xai-org/grok-build`, see this plugin's `NOTICE`); policy guards (ADR-0073, `config/policy.json`, Claude Code only) — an `EnterPlanMode` matcher (`craftflow_pretooluse_guard.py`) denies the native plan tool unless `native_plan_mode` is `allow`; the Bash guard (`craftflow_pretooluse_bash_guard.py`) denies `git add|stage|commit` of plan artifacts (`docs/plans/**` except `.gitkeep`, `.craftflow/state/workflows/*.json`, the live artifact's `plan_file`/`design_file`) in craftflow-managed projects and their linked worktrees unless `plan_commit` is `allow`, and denies `git merge <workflow branch>` into a dirty main tree (default-true `worktree_merge.require_clean_tree`); an invalid policy never widens any of them, and Cursor payloads (`CURSOR_PLUGIN_ROOT` set by the adapter) skip the Bash denies (DD-12; the early return keys on that variable in the hook process's own environment, so a Claude Code user who exports it gets today's behavior, never a stricter one; an untracked, non-ignored `.craftflow/` makes the merge guard's `git status --porcelain` non-empty and so denies); the plan-commit guard unwraps `command|env|time|nice|exec|sudo`, subshell groups and `sh|bash|zsh -c` / `eval` strings, honors `-C`, `--git-dir`, `--work-tree` and `GIT_DIR`/`GIT_WORK_TREE`, also guards `git update-index --add|--cacheinfo` and `git pull . <wf-branch>`, allows `--dry-run`, and shares one 5 s probe budget per run. Budget exhaustion fails OPEN (logged as `plan_commit_probe_error`, `reason: budget`) for the `plan_commit` commit probes only; the merge guard fails CLOSED on a genuine probe error (a `git status` that errors or times out denies), but its status probe gets its own fresh 2 s deadline, so a spent parse or commit-probe budget never turns a clean tree into a deny. The policy pass is bounded (16 KB of command text, 2000 command units, a 4 KB tracked cwd, the 5 s deadline, linear parsing, branch-name tokens over 256 characters ignored). Per-run work is bounded too: at most 50 DISTINCT git-write invocations (repeats of the same one are skipped), 20 commit probes, one cached policy load, one cached workflow-file scan, and ONE merge-status probe deadline (2 s) with a cached result per repository; the loop also stops at the 5 s deadline. A commit probe that cannot run because the time or count budget is spent is "cannot verify" and DENIES (`policy_guard_skipped`, reason `probe`); an ordinary probe error (git missing, exit 128) still fails open with its log line. When the pass is skipped or exhausted (`size`, `units`, `path`, `time`, `invocations`, `unparseable` for an unbalanced quote or no parsable unit, `unmaskable` for text that already contains U+E000-E007) AND the command has `git` at a COMMAND position (start of a segment, after `; && || | & ( { !`, `then`/`do`/`else`, or a wrapper such as `env`/`sudo`/`time`) followed by add/stage/commit/update-index (or merge/pull together with a workflow branch name) in a managed project, the command is DENIED. That judgement is made on the RAW command text (quotes, comments and heredoc bodies included), never on lexer output, because the hand-rolled lexer must not be able to hide a command from the fail-closed rule; the cost is a documented false deny when a skipped or exhausted command quotes `git commit` at a line start. The `size` path (over 16 KB) does not run the lexer at all. For `size` alone, a command whose only git write is `commit` (and that is at most 256 KB) is verified by two bounded probes, `git diff --cached` and the worktree `git diff` (so `-a`, `--only`, `-i` and pathspec commits are covered), and denied only when a protected path shows up in either or a probe fails; above 256 KB it is denied on raw text alone. The lexing pre-pass is linear, memoised, capped at 16 unresolved heredocs and 1.5 s, and a heredoc body is blanked only when inert: a body fed to `sh bash zsh dash ksh eval source . xargs python node perl ruby php` stays in the lexed text (so its commands are inspected), and an unquoted-delimiter body containing `$(` or a backtick, an unterminated heredoc and more than 16 heredocs fail closed (`heredoc`). `<<` is not a heredoc inside `((...))`, `[[...]]` or a `let` line. `unparseable` (unbalanced or mis-synced quoting) denies with "unverifiable quoting; use `git commit -F file` or split". More than 50 distinct git writes or 20 commit probes in one command deny with "too many distinct git writes in one command; split it". The denial (`craftflow-policy: plan_commit — ... split it into smaller commands`, logged as `policy_guard_skipped` with `denied: true`); `plan_commit: allow` and Cursor payloads turn this off like every plan_commit deny, and commands without those git words stay allowed. An internal exception in the policy pass is logged (`policy_guard_error`, command capped at 300 chars), writes ONE stderr warning line, and falls through to the destructive-command guards: a bug here fails OPEN for `plan_commit` / merge checks rather than blocking every Bash call; allow paths never print anything. Protected-path comparison is always case-folded, so on a case-sensitive filesystem a path like `DOCS/Plans/x.md` is a false positive; the escape hatch is `plan_commit: allow` in `config/policy.json`. Followed: static `export GIT_DIR=` / `GIT_WORK_TREE=` and `declare -x` (like `env GIT_DIR=`; `unset` clears them), `if` / `elif` / `while` / `until` / `!` heads (a `cd` there persists as in bash), and quoted or backslash-escaped operators (`-m ";"`, `-m ")"` stay arguments), `#` comments (an apostrophe in a comment no longer hides later lines; a mid-word `#` is not a comment), heredoc bodies (removed before lexing, only when the terminator line exists, so `1<<2` cannot swallow the rest), `\<newline>` continuations and `export -n`. Accepted gaps (fail open unless noted): `case` statements, function bodies, `g=git; $g commit` and other indirect command names, arrays, more than 8 nested wrapper levels (logged as `wrapper iteration cap`), `<<` heredoc forms with a numeric delimiter, `git add .` / `git add -A` of an untracked, NON-ignored plan (the pathspec names no plan and nothing is staged yet), `bash -c "git commit"` once the pass is exhausted (the raw rule needs `git` at a command position), and an `add && commit` over 16 KB or past the caps, which false-denies by design (split it). The Bash guard is a hand-rolled lexer that kept opening holes across five fix cycles; the recommended follow-up is to extract it into its own module with a differential test against real bash, outside this phase because it would change the conductor import closure. The pre-existing redirect-target check now computes its protected-path set once per hook run and denies (fail closed, logged) beyond 2000 redirect targets in one command. The single stderr warning line is likely invisible on a hook exit 0, so the `policy_guard_error` log event is the reliable signal; no hook-visible output is added to allow paths. Not followed (logged as `plan_commit_dynamic_allowed`, fail open): dynamic command names and `-C`/`cd`/`--git-dir`/`--work-tree` values, `cd -` / `popd` / `cd "$PWD"`, dynamic or value-less `export GIT_DIR`, bare `GIT_DIR=...` assignment statements, `case` statements, `| sh`, here-documents and here-strings, `python3 -c` style strings, command substitution that names git, and `xargs` / `find -exec` pathspecs read from elsewhere; state-read compaction guard (Read matcher — denies a Read of an oversized `.craftflow/state/**` file and redirects the agent to `craftflow_state_query.py`, never mutating the target file; block mode, `stateReadCompaction`); optional Jev pre-execution risk classification (`craftflow_jev_risk_gate.py`, Bash|Write|Edit matchers) for a narrow allowlist of consequential tool calls (`rm -rf`, force-push, DB-drop/migration-down, credential/secret file writes), audit-only advisory (never blocks), inert unless `config/jev.json` sets `features.riskGate` to `"audit"` or `"advise"` AND `TYPESAFE_API_KEY` is set
   - `SessionStart` — workflow resume context; hook import self-check; optional Jev auto-detect (`craftflow_jev_session_check.py`) consent-ask + session-scoped canary gating; context-nudge state reset on `compact` (`craftflow_context_nudge.py --reset`)
   - `PostToolUse` — workflow artifact integrity audit and memory placeholder restore (defensive, fires on Edit/Write)
   - `TaskCompleted` — task metadata validation (enforced: block mode)
   - `PostCompact` — compaction event capture
   - `SubagentStop` — agent contract presence audit and memory placeholder restore
   - `PreCompact` — workflow state snapshot before compaction
   - `Stop` — workflow state snapshot and memory placeholder restore on session stop; opt-in stop-verify gate (`craftflow_stop_verify.py` — inert unless `config/stop-verify.json` sets `{"enabled": true, "command": "..."}`; concept ported from `xai-org/grok-build`, see this plugin's `NOTICE`)
   - `Stop` (second entry) — opt-in stop gate (`craftflow_stop_gate.py` — inert unless `config/stop-gate.json` or `~/.claude/craftflow/stop-gate.json` sets `mode` to `audit` or `on`; `audit` only logs; `on` blocks the stop with a constant continue reason only when the user has armed it with `craftflow_stop_gate_arm.py` (SPEC-0020); never pushes, opens PRs, merges or commits; fail open; see README "Stop gate")
   - `StopFailure` — API error logging (async)
   - `InstructionsLoaded` — instruction file load audit (async)
   - `UserPromptSubmit` — optional Jev routing/skill hint (`craftflow_jev_prompt_hint.py`; inert unless `config/jev.json` sets `enabled:true` AND `TYPESAFE_API_KEY` is set; when enabled it sends the submitted prompt text (capped at `maxStateChars`), the project folder name and the active workflow type off-machine to TypeSafe AI; advisory `additionalContext` only, never a decision); context-size nudge (`craftflow_context_nudge.py`; audit-only by default via `config/hook-mode.json` `contextNudge`, local only, reads the transcript tail, never blocks; optional durable user override `~/.claude/craftflow/context-nudge.json`; advisories end with a ready-to-paste `/compact` command)
2. **Optional git pre-commit helper** via `pre-commit`

**Not the same file as Cursor's hooks.json.** A separate `hooks.json` lives at the plugin root
(`../hooks.json`, sibling of this `hooks/` directory) — that one is for Cursor, uses Cursor's own
native hook step names (`preToolUse`, `postToolUse`, `sessionStart`, `afterFileEdit`,
`subagentStop`, `preCompact`, `stop`), and is a *template*: its commands reference a
`${CURSOR_PLUGIN_ROOT}` placeholder. Cursor DOES resolve this placeholder itself, but only for
hooks it loads via its own native "claude-plugin" hook source — i.e. Claude Code plugin manifests
it auto-imports directly, gated behind its `thirdPartyExtensibilityEnabled` setting — NOT for a
project-local `.cursor/hooks.json` like the one `../install-cursor.sh` writes, which gets no
placeholder substitution at all. `../install-cursor.sh` resolves the placeholder to a real
absolute path itself and writes/merges the result into the current project's own
`.cursor/hooks.json` (confirmed live: Cursor reads a project-local `.cursor/hooks.json` per open
workspace folder and additively merges it with the machine's global `~/.cursor/hooks.json` — never
an override). This step is per-project; re-run `install-cursor.sh` inside every project you want
craftflow's write-guard enforced in, not just once globally. Whether the native
`thirdPartyExtensibilityEnabled` auto-import path already covers this without any per-project step
is an open question, not yet confirmed either way. See `../skills/cursor-router/SKILL.md`'s
"Hook-based write-guard enforcement" note for the full story, including why this silently didn't
work before 2026-08-18.

## Optional: Cursor CLI Permission Allowlist (`.cursor/cli.json`)

`../install-cursor.sh` also ships a separate, **opt-in** step (unrelated to the write-guard
`hooks.json` step above) that pre-populates the `cursor-agent` CLI's per-project permission
allowlist so common read-only/safe commands (`grep`, `rg`, `ls`, `cat`, `pwd`, `cd`, and
`git status`/`diff`/`log`/`show`/`add`/`commit`) don't trigger an
approval prompt on every craftflow session in Cursor. It writes/merges
`../cli-permissions.json`'s `permissions.allow`/`permissions.deny` arrays into the **project-local**
`.cursor/cli.json` — never the machine's global `~/.cursor/cli-config.json` — using the same
additive-merge-never-overwrite pattern as the `hooks.json` step (dedup by exact string, existing
entries preserved, degrades gracefully with a warning if `python3` is unavailable).

Destructive/high-blast-radius commands (`git push`, `git reset --hard`, force-push, `rm`, `mv`,
`npm`/`pnpm install`) are deliberately excluded and will still prompt. `git:checkout`, `git:stash`,
`git:branch`, and `find` are also deliberately **excluded** from the allowlist (not present, not
unscoped) — see `../cli-permissions.json`'s own `_comment` field for the reasoning: each git verb,
if allowed unscoped, would also permit a destructive/discarding trailing-subcommand form
(`git checkout -- <file>` / `git checkout .` discarding uncommitted changes; `git stash drop` /
`git stash clear` permanently discarding stashed changes; `git branch -D` force-deleting a branch
and bypassing git's own unmerged-commit safety check); an unscoped `find` would likewise permit
`find ... -exec <cmd> {} \;` / `-execdir` / `-ok` / `-okdir` (arbitrary command execution with zero
approval prompt) and `find ... -delete` (mass deletion) — this project's own Claude-side Bash
destructive-command guard (`bashDestructiveTraversal` above, enforced in
`craftflow_hook_unit_tests.py`) already treats exactly these `find` flags as destructive, so an
unscoped Cursor-side allow would silently reopen a risk class this plugin already guards against
on the Claude Code side. None of these four have a scoped-glob substitute available, since
Cursor's exact command:args glob syntax could not be confirmed. All four keep prompting
for approval.

Default is **skip** — this step only runs when opted in via `CRAFTFLOW_CURSOR_PERMISSIONS=1`
(non-interactive/scripted use) or an interactive `y` answer at the TTY prompt install-cursor.sh
shows when run directly (not curl-piped) with no flag set.

## Composable Trust Gate (not wired to a hook event)

`craftflow_hook_trust.py` is a standalone CLI (`check` / `update` subcommands),
not registered in `hooks.json`. It fail-closed refuses to vouch for a
repo-local hook script that isn't listed in `.craftflow/hook-trust.json`'s
sha256 manifest, or whose content no longer matches its manifested hash.
Intended for a future caller (e.g. repo-conductor's sandbox spawner) to
invoke before executing a less-trusted repo-local hook script. Concept
ported from `xai-org/grok-build`'s `trust.rs`; see this plugin's `NOTICE`.

## Plugin Runtime Hooks

When CRAFTFLOW is installed as a Claude Code plugin, Claude Code reads `hooks/hooks.json`
from the plugin bundle and runs the referenced scripts from `${CLAUDE_PLUGIN_ROOT}/scripts`.

The shipped runtime hooks are intentionally minimal. Most hooks operate in audit mode; `memoryWrites`, `protectedWrites`, `taskMetadata`, `bashDestructiveTraversal`, `safeShellGuard`, and `stateReadCompaction` are enforced in block mode (config: `config/hook-mode.json`; each toggle enum-validates its value and fails closed — block — on a missing or malformed config, via the shared `resolve_toggle_decision()` helper in `craftflow_hooklib.py`):
- protect and enforce direct memory markdown writes on Edit/Write (block mode, `memoryWrites`)
- protect the same memory files, the `.memory-finalize` permit, and workflow JSON artifacts against Bash-write bypasses — redirects, `tee`, and python-script writes (block mode, `protectedWrites`)
- deny destructive Bash commands (in-cwd or cwd/worktree-escaping) across a widened command vocabulary (block mode, `bashDestructiveTraversal`)
- deny an absolute denylist of catastrophic Bash commands — `rm -rf /`, `mkfs`, fork bombs (block mode, `safeShellGuard`; concept ported from `xai-org/grok-build`, see `NOTICE`)
- deny a Read of an oversized `.craftflow/state/**` file and redirect the agent to `craftflow_state_query.py` (never mutating the target file's bytes; block mode, `stateReadCompaction`)
- inject workflow resume context
- self-check that every sibling hook script still imports cleanly under python3, warning (not blocking) on failure
- optionally run Jev auto-detect consent-ask + session-canary via `SessionStart` — inert unless `TYPESAFE_API_KEY` is set and `config/jev.json` has neither `enabled:true` (manual path) nor `consent.status="declined"`; when active, asks the user once per session to opt in, then gates activation to a short-budget canary call (off by default)
- audit workflow artifact integrity after writes
- validate and enforce CRAFTFLOW task metadata on completion (block mode)
- restore memory placeholders after Edit/Write and on SubagentStop and Stop
- snapshot workflow state before compaction and on session stop; optionally block session completion until a configured verify command passes (opt-in, off by default; concept ported from `xai-org/grok-build`, see `NOTICE`)
- log API failures and instruction file loads for telemetry
- optionally ask TypeSafe AI's Jev classifier for a routing/skill hint via the `UserPromptSubmit` hook before each prompt (opt-in, off by default; sends prompt text off-machine only when enabled; never blocks)

## Internal Publication Audit

The plugin also ships an internal drift check:

```bash
python3 plugins/craftflow/scripts/craftflow_harness_audit.py
```

It validates the publication-critical contract:
- plugin manifest version matches `README.md` and `CHANGELOG.md`
- marketplace metadata matches the shipped plugin version
- plugin hooks and MCP names referenced by docs/router actually exist
- workflow replay fixtures and checker are present
- key router headings still exist for invariant coverage
- router-consumed task metadata and agent contract fields are still present

## Skill Candidate Ledger (router-invoked, not a registered hook)

Phase 1 of `docs/plans/2026-07-29-craftflow-skill-distillation-plan.md` ships
a standalone recurrence miner. As of Phase 3, all three of `craftflow-router/references/build-workflow.md`
(the Skill-Distill Gate's `--query` check), `craftflow-router/references/memory-finalize.md`
(the unconditional `--observe` step in Memory Finalization) and `cursor-router/SKILL.md` (§ 5a
Skill-Distill Gate) invoke it directly via Bash — it is still not registered
as a `hooks.json` entry, but it is no longer merely a standalone CLI:

```bash
python3 plugins/craftflow/scripts/craftflow_skill_ledger.py --observe <wf-id>
python3 plugins/craftflow/scripts/craftflow_skill_ledger.py --query
python3 plugins/craftflow/scripts/craftflow_skill_ledger.py --prune
python3 plugins/craftflow/scripts/craftflow_skill_ledger.py --backtest
```

It reads `.craftflow/state/workflows/*.json`/`*.events.jsonl`, mines
candidate skill-worthy signatures, and upserts them into
`.craftflow/state/project/skill-candidates.json`. The recurrence gate
(`distinct_workflows >= 2`) was empirically calibrated against this
project's real workflow corpus — see
`docs/2026-07-29-craftflow-skill-distillation-threshold-calibration-decision.md`.

Phase 4 adds anti-rot maintenance to `--prune`, also wired unconditionally into
`references/memory-finalize.md → ## 13. Memory Finalization` immediately after `--observe` (both run on every workflow,
independent of `SKILL_DISTILL: skip`): stale `candidate` entries (>90 days, no new evidence) are
removed outright; `rejected` entries are left untouched as permanent tombstones; and `promoted`
entries are never removed or status-changed, but are checked for rot (missing/blank
`promoted_skill` name, a missing canonical `SKILL.md`, a missing `craftflow-referenced-paths`
entry, or an elapsed/unparseable `craftflow-review-after`) and flagged with
`needs_review`/`needs_review_reason` in place. A corrupt-but-existing ledger file now fails closed
(`LedgerCorruptError`) rather than being silently treated as absent and overwritten — see
`docs/2026-07-30-craftflow-skill-distill-anti-rot-decision.md`.

## Skill Authoring + Promotion (staging-only; wired into the router in Phase 3)

Phase 2 of the same plan adds a `skill-author` agent
(`plugins/craftflow/agents/skill-author.md`, loading the
`craftflow:skill-distillation` skill) that reads one gate-eligible ledger
candidate, applies the anti-slop rubric
(`skills/skill-distillation/references/rubric.md`), and — only if it
survives — stages a proposal. Phase 3 wires this into both routers:
`craftflow-router/references/post-agent-validation.md`'s Skill-Distill Approval Flow pointer to the shared protocol (`AskUserQuestion`
with Approve / Approve + register in SKILL_HINTS / Reject / Defer) and
`cursor-router/SKILL.md` § 5a (a synchronous plain-text confirmation
exchange, since Cursor has no `AskUserQuestion` equivalent). Neither script
is registered in `hooks.json` — both are invoked directly via Bash by the
router during the `skill-distill` phase.

The agent never writes proposal files directly; staging goes through a
trusted script:

```bash
python3 plugins/craftflow/scripts/craftflow_skill_propose.py \
  --candidate-id <id> \
  --skill-md-file <scratch-path-to-SKILL.md-draft> \
  --proposal-md-file <scratch-path-to-PROPOSAL.md-draft> \
  --state-dir .craftflow/state
```

which atomically writes
`.craftflow/state/project/skill-proposals/<candidate-id>/{SKILL.md,PROPOSAL.md}`
and flips the ledger candidate's `status` to `proposed`. Promotion out of
staging (on explicit user approval only) goes through a separate script:

```bash
python3 plugins/craftflow/scripts/craftflow_skill_promote.py --approve <candidate-id>
```

which writes the canonical `.claude/skills/<name>/SKILL.md` (synced to
`.cursor/skills/<name>`) and marks the ledger entry `promoted`. Both scripts'
write-path safety (approve/reject symmetry, a ledger-file-absent bypass,
cross-candidate name collisions including a case-folding variant, and
malformed-ledger-entry degradation) went through 5 REM-FIX hardening rounds
as part of Phase 3 router wiring — see
`docs/2026-07-30-craftflow-skill-distill-router-wiring-decision.md`.

`craftflow_pretooluse_guard.py` now unconditionally protects
`.claude/skills/`, `.cursor/skills/`, the skill-candidate ledger, and
`.craftflow/state/project/skill-proposals/` against any raw Edit/Write/Bash
write — only the two scripts above are authorized writers. This protection
went through 5 REM-FIX hardening rounds; a final review found 7 further
undetected bypasses in the underlying command-name-enumeration approach
(also affecting the pre-existing memory-file guard). These gaps are
accepted and disclosed, not fixed, pending a future effect-based
write-detection redesign — see
`docs/2026-07-30-craftflow-guard-write-detection-limitations-decision.md`.

`craftflow_pretooluse_guard.py` also unconditionally protects
`.craftflow/state/project/reliability-gates.json` (the reliability-gates
ledger — see `docs/ai/decisions/0018-craftflow-reliability-gates-ledger.md`)
against raw Edit/Write/Bash writes, mirroring the skill-candidate ledger's
own treatment above: it is a single script-owned JSON file, not a markdown
memory file eligible for the `.memory-finalize` permit, so only
`craftflow_reliability_gates.py` is an authorized writer.

## Workspace-Root File Allowlist (`.craftflow-workspace.json`)

When a session is launched at a multi-repo workspace root (a directory that is not itself a
git repo but contains several independently git-initialized nested repos), `## 0.` step 1a of
`craftflow-router/SKILL.md` narrows `PROJECT_ROOT` down to exactly one owning nested repo. From
that point on, the confinement union the guards enforce — `{cwd} ∪ {worktree_path}` — only ever
covers that one nested repo, so workspace-root-level sibling files that live outside any nested
repo (e.g. `CONTRACTS.md` sitting alongside the project folders) become permanently unwritable
via Edit/Write/Bash-redirect for the rest of the session.

`.craftflow-workspace.json`, placed at the workspace root itself (a sibling of the nested repos,
not inside any of them), is a human-authored escape hatch for that gap: a `writable_paths` array
naming exact, direct workspace-root-child filenames to treat as writable in addition to
`{cwd} ∪ {worktree_path}`. It is read once per workflow, in `## 0.` step 1a
(`craftflow_resolve_workspace_root.py`'s `read_workspace_writable_paths()`), and the validated,
resolved list is persisted into the workflow artifact as `workspace_writable_paths` so the guard
hooks can read it the same way they already read `worktree_path`.

```json
{ "writable_paths": ["CONTRACTS.md", "PLATFORM_CONTEXT.md"] }
```

Matching is **exact-equality only** — never prefix, descendant, or directory matching — enforced
via `resolve_confinement()`'s new `extra_exact_paths` parameter in `craftflow_hooklib.py`. An
entry is dropped (never silently honored) if it isn't a direct child of the workspace root (no
path separators, no `..`, no absolute paths) or if it resolves inside a nested repo. This keeps
the mechanism structurally incapable of becoming a directory/subtree grant into a sibling repo's
contents — only the pre-existing single confined repo/worktree ever gains full-tree write access.
Missing or malformed config, or an unvalidated entry, degrades to `[]` (no grant), never a crash
and never a wildcard. See `docs/2026-08-13-craftflow-workspace-root-allowlist-decision.md`.

### Workspace Membership (`members`)

`writable_paths` (above) grants access to individual **files** at the workspace root.
`members` is a separate allowlist that grants a nested **project** write access to the shared
workspace-tier memory at `{workspace_root}/.craftflow/state/workspace/*.md`
(`discover_workspace_root()` / `is_workspace_member()` / `resolve_workspace_memory_paths()` in
`craftflow_hooklib.py`). Directory proximity alone (sitting `<=3` levels under a workspace root)
is never sufficient — an unrelated nested project must never be able to write another project's
shared memory purely because of where it happens to sit on disk.

```json
{ "writable_paths": ["CONTRACTS.md"], "members": ["ai-craft", "team/nested-repo"], "memory_writable": true }
```

- **Owner-authored, never inferred.** `members` is populated only via `craftflow:ai-first-setup`
  Step 4 item 10's propose-and-confirm interview — the same human-authored-only, propose-never-
  write doctrine as `writable_paths`. No project may add itself to another workspace's `members`,
  and Craftflow never infers membership from directory structure.
- **Entry contract (multi-segment-capable).** Unlike a `writable_paths` entry (bare direct-child
  filename only), a `members` entry may be a bare direct-child basename (`"ai-craft"`) **or** a
  multi-segment path relative to `workspace_root` (`"team/nested-repo"`) — no `.`/`..` segment
  anywhere, no leading `/` or `~`, no backslash. It is resolved and compared to the requesting
  project's own resolved path by **exact path equality or ancestor-containment only — never a
  prefix or glob match**.
- **Fail-closed list.** Every one of the following denies the grant: the config file is missing,
  unreadable, or malformed JSON; the top level isn't a dict; `members` is absent (the ordinary
  shape for a workspace that hasn't opted into membership yet — e.g. every config
  `craftflow:workspace-setup` alone produces); `members` is present but not a list; the list is
  empty; an individual entry is the wrong type, syntactically unsafe, fails to resolve, resolves
  to `workspace_root` itself, or resolves outside `workspace_root`; a NUL-bearing entry is dropped
  on its own without discarding its valid siblings. An empty or absent `members` list means **no**
  nested project can write the workspace-tier memory — fail-closed by design, not a bug.
- **Per-candidate monotone restriction (not a whole-walk guarantee).** At any single candidate
  ancestor, the membership check is strictly subtractive relative to *that candidate*: failing it
  can only remove the grant that candidate alone would have issued, never add one. This is **not**
  a whole-walk subtractive guarantee — because the walk continues past a failed candidate to a
  farther one (see M-5 below), `discover_workspace_root()` can resolve a different, farther
  ancestor than an ungated walk would have reached for the same requester. That farther ancestor's
  own grant is always one its own owner explicitly authorized via its own `members` list — never
  an unauthorized grant.
- **E1 finding — `workspace-setup`-only workspaces are write-inert.** `craftflow:workspace-setup`
  deliberately never writes `.craftflow-workspace.json` (it provisions the memory-tier files
  only), so a workspace provisioned that way has no `members` and no `memory_writable` key —
  `workspace_memory_writable()` already denies it. Until `craftflow:ai-first-setup` Step 4 item 10
  runs and the workspace owner explicitly confirms `members`/`memory_writable`, the workspace-tier
  memory stays read-only for every nested project, regardless of `workspace-setup` having run.
- **Continue-past-non-member walk semantics (M-5).** A candidate ancestor that carries a marker
  but fails the membership check is treated identically to "no marker found at this level" — the
  walk continues to farther ancestors within the existing 3-level cap, it does not abort. Example:
  given nested `Z ⊃ Y ⊃ X` where `Y` carries an unrelated, stray marker that does not list `X` as
  a member but `Z` does, `discover_workspace_root()` starting from `X` resolves `Z`, not `Y` —
  the stray marker at `Y` is skipped exactly as if it weren't there, so a legitimate, farther
  membership grant is still reachable.
- **M-8 — the read side is deliberately not membership-gated.** Both routers'
  memory-*load* walk (`craftflow-router/references/memory-load-workspace.md` step 5a, `cursor-router/SKILL.md` step 4a)
  reads an ancestor's workspace-tier memory files with no membership check at all — this is the
  write-side mechanism documented above, not the read side. This is a deliberate, disclosed
  choice, not drift: fixing it fail-closed would break every existing workspace-memory user who
  adopted the read-side walk before `members` existed (shipped 2026-08-28). The residual risk
  (a non-member nested project can still *read* an ancestor's shared workspace memory) is tracked
  as a follow-up, not silently accepted as settled — see the membership-allowlist ADR.

## Optional Git Pre-Commit Hook

This is separate from Claude Code plugin hooks. Install it only if you want
git commits blocked when tests fail:

```bash
cp plugins/craftflow/hooks/pre-commit .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

It blocks `git commit` if your test suite fails. No test runner configured?
Hook exits 0 and passes through.
