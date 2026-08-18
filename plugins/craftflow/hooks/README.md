# craftflow Hooks

This directory now serves two different purposes:

1. **Plugin runtime hooks** via `hooks.json`
   - `PreToolUse` — protected writes guard (Edit, Write, and now Bash matchers; Read events are not intercepted) — the Bash matcher inspects write-shaped commands (redirects, `tee`, python `open()`/`write_text()`/`os.system`/`subprocess`/`shutil`/`os.rename` writes) for protected-path targets, closing a prior bypass where Bash-only agents could overwrite memory files undetected; destructive-command guard (Bash matcher — denies destructive commands whose resolved target is in-cwd (e.g. `rm -rf packages/agent-cli`, `rm -rf .`) as well as ones that escape the session's own cwd/worktree, e.g. a worktree relative-path escape; covers `rm`, `rmdir`, `mv`, `shred`, `truncate`, `dd`, `chmod`, `find -exec`/`-execdir`/`-ok`/`-okdir`/`-delete`, `git clean`/`reset --hard`/`push --force`, with fail-closed handling of dynamic ($/backtick) targets; see `docs/incidents/2026-07-25-phase3-verifier-rm-attempt.md`); catastrophic-command guard (Bash matcher — `craftflow_safe_shell_guard.py`, an absolute denylist for `rm -rf /`, `mkfs`, and fork-bomb patterns regardless of path, plus an opt-in recursive-grep-against-a-broad-path advisory; concept ported from `xai-org/grok-build`, see this plugin's `NOTICE`)
   - `SessionStart` — workflow resume context; hook import self-check
   - `PostToolUse` — workflow artifact integrity audit and memory placeholder restore (defensive, fires on Edit/Write)
   - `TaskCompleted` — task metadata validation (enforced: block mode)
   - `PostCompact` — compaction event capture
   - `SubagentStop` — agent contract presence audit and memory placeholder restore
   - `PreCompact` — workflow state snapshot before compaction
   - `Stop` — workflow state snapshot and memory placeholder restore on session stop; opt-in stop-verify gate (`craftflow_stop_verify.py` — inert unless `config/stop-verify.json` sets `{"enabled": true, "command": "..."}`; concept ported from `xai-org/grok-build`, see this plugin's `NOTICE`)
   - `StopFailure` — API error logging (async)
   - `InstructionsLoaded` — instruction file load audit (async)
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

The shipped runtime hooks are intentionally minimal. Most hooks operate in audit mode; `memoryWrites`, `protectedWrites`, `taskMetadata`, `bashDestructiveTraversal`, and `safeShellGuard` are enforced in block mode (config: `config/hook-mode.json`; each toggle enum-validates its value and fails closed — block — on a missing or malformed config, via the shared `resolve_toggle_decision()` helper in `craftflow_hooklib.py`):
- protect and enforce direct memory markdown writes on Edit/Write (block mode, `memoryWrites`)
- protect the same memory files, the `.memory-finalize` permit, and workflow JSON artifacts against Bash-write bypasses — redirects, `tee`, and python-script writes (block mode, `protectedWrites`)
- deny destructive Bash commands (in-cwd or cwd/worktree-escaping) across a widened command vocabulary (block mode, `bashDestructiveTraversal`)
- deny an absolute denylist of catastrophic Bash commands — `rm -rf /`, `mkfs`, fork bombs (block mode, `safeShellGuard`; concept ported from `xai-org/grok-build`, see `NOTICE`)
- inject workflow resume context
- self-check that every sibling hook script still imports cleanly under python3, warning (not blocking) on failure
- audit workflow artifact integrity after writes
- validate and enforce CRAFTFLOW task metadata on completion (block mode)
- restore memory placeholders after Edit/Write and on SubagentStop and Stop
- snapshot workflow state before compaction and on session stop; optionally block session completion until a configured verify command passes (opt-in, off by default; concept ported from `xai-org/grok-build`, see `NOTICE`)
- log API failures and instruction file loads for telemetry

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
a standalone recurrence miner. As of Phase 3, both `craftflow-router/SKILL.md`
(the Skill-Distill Approval Flow's Gate check and the unconditional
`--observe` step in Memory Finalization) and `cursor-router/SKILL.md` (§ 5a
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
`SKILL.md § 13. Memory Finalization` immediately after `--observe` (both run on every workflow,
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
`craftflow-router/SKILL.md`'s Skill-Distill Approval Flow (`AskUserQuestion`
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

## Optional Git Pre-Commit Hook

This is separate from Claude Code plugin hooks. Install it only if you want
git commits blocked when tests fail:

```bash
cp plugins/craftflow/hooks/pre-commit .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

It blocks `git commit` if your test suite fails. No test runner configured?
Hook exits 0 and passes through.
