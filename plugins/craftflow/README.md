# Craftflow

Router-first AI development orchestration for Claude Code and Cursor AI.

Every build, debug, review, and plan task routes through a single entry point that dispatches the right agent chain, tracks workflow state, and enforces quality gates before marking anything complete.

---

## What it does

- **Routes all dev tasks** — one router (`craftflow-router`) classifies intent and dispatches to the right agent chain automatically
- **Agent chain** — 14 specialized agents: planner, component-builder, bug-investigator, code-reviewer, silent-failure-hunter, integration-verifier, and more
- **29 skills** — planning patterns, TDD, code generation, debugging, diff-driven docs, workflow status, and others
- **Hook system** — Python lifecycle hooks for memory protection, write guards, URL caching, and session continuity
- **Shared state** — `.craftflow/state/` is readable by both Claude Code and Cursor
- **Feature-named workflows** — workflow folders, files, and worktrees are named after the feature (`wf-auth-refactor-20260706-d4e5f6a7`) so you can identify them at a glance
- **Live statusline progress** — a `⚡ feature-name 60% · 🟢 phase_2` segment appended to claude-hud, updates every ~300ms without interrupting the running agent
- **Cursor support** — each workflow phase dispatched via a real, isolated Cursor `Task` call (`subagent_type: generalPurpose`), with code-reviewer and silent-failure-hunter dispatched in parallel; progress blocks appear in Cursor chat at each phase transition
- **Reliability-gates ledger** — `craftflow_reliability_gates.py` tracks proven invariants
  (append-only, fail-closed evidence log) across workflows
- **Skill-distillation pipeline** — `craftflow_skill_ledger.py` mines recurring workflow patterns
  into candidate skills, staged via `craftflow_skill_propose.py` and promoted via
  `craftflow_skill_promote.py`
- **Safe-shell / stop-verify / hook-trust guards** — `craftflow_safe_shell_guard.py` blocks
  catastrophic shell command patterns pre-execution, `craftflow_stop_verify.py` is an opt-in
  end-of-session verification gate (inert by default), and `craftflow_hook_trust.py` is a standalone
  hash-manifest trust gate for repo-local hook scripts (not itself wired into `hooks.json`)

### Default agent models

Each agent pins its model in its `model:` frontmatter line:

| Agent | Default model | Why |
|-------|---------------|-----|
| `planner` | opus | plan quality drives every later phase |
| `plan-gap-reviewer` | opus | fresh, anti-anchoring review of a saved plan |
| `plan-bakeoff-judge` | opus | compares and synthesizes competing plans |
| `bug-investigator` | opus | root-cause proof before any fix |
| `doubt-verifier` | opus | adversarial verification of claims |
| `component-builder` | sonnet | TDD execution of an approved phase |
| `code-reviewer` | sonnet | diff review |
| `silent-failure-hunter` | sonnet | error-handling review |
| `integration-verifier` | sonnet | end-to-end verification |
| `web-researcher` | sonnet | research lookups with a Router Contract |
| `github-researcher` | sonnet | repository research with a Router Contract |
| `doc-syncer` | sonnet | documentation sync from the current diff |
| `learn-distiller` | sonnet | distills workflow learnings into notes |
| `skill-author` | sonnet | drafts skill stubs |

Precedence: a per-dispatch `model` parameter > the frontmatter pin > `CLAUDE_CODE_SUBAGENT_MODEL` on Claude Code >= 2.1.251 (older versions ranked the environment variable first) > the session model. Cursor cannot select custom subagent types, so it inherits the session model for every agent. Rationale and evidence: ADR-0049, craftflow default agent model pins, in the ai-craft repository.

## Workflow types

| Signal | Workflow | Agent chain |
|--------|----------|-------------|
| build, implement, create | BUILD | component-builder → code-reviewer → silent-failure-hunter → integration-verifier |
| error, bug, fix, crash | DEBUG | bug-investigator → code-reviewer → integration-verifier |
| plan, design, spec | PLAN | planner → plan-gap-reviewer |
| review, audit | REVIEW | code-reviewer (advisory) |

---

## Quality layer

Craftflow enforces a quality contract from spec through verification. These conventions are active on every workflow:

### Spec conventions (AI_FIRST rules 11–14)

| Rule | Convention |
|------|-----------|
| `FR-###` / `SC-###` | Stable functional-requirement and success-criteria identifiers in `docs/ai/specs/`. Plans and verifier scenarios reference these IDs for traceability. |
| `[NEEDS CLARIFICATION]` | Any unresolved spec or plan item is tagged. `plan-gap-reviewer` blocks advancement until all markers are resolved. |
| `[P]` parallel markers | Steps within a plan phase are marked `[P]` when they can run concurrently. Each phase also declares a delivery strategy: `mvp_first`, `incremental`, or `parallel_team`. |
| Tech-agnostic AC | Success criteria must describe user-observable outcomes, not implementation metrics. "User sees results in 3 s" is valid; "API response time < 200 ms" is not — restate it in user terms. |

### Gap classification

When verification fails, every FAIL scenario is classified before remediation begins:

| Type | Meaning |
|------|---------|
| `Missing` | Required work is entirely absent from the implementation |
| `Partial` | Exists but incompletely satisfies the acceptance criterion |
| `Contradicts` | Code conflicts with the spec, plan, or a MUST constraint in the constitution |
| `Unrequested` | Code implements behavior not present in the accepted plan (scope creep) |

Severity: `CRITICAL` / `HIGH` / `MEDIUM` / `LOW`

Classification is written by `integration-verifier` (step 3.5, `### Gap Classification` block) and by `silent-failure-hunter` (Unrequested gap detection). `craftflow_contract_validate.py` machine-validates the `GAP_CLASSIFICATION` field in every agent contract.

### Constitution

Project immutable principles live at `.craftflow/state/project/constitution.md`. The PLAN workflow reads this file before brainstorming and halts if the user's intent violates a MUST constraint. SHOULD violations are logged as advisories but do not block. Amendment requires explicit user approval and a version bump.

---

## Feature-named workflow folders

Every new workflow gets an id that embeds a feature slug:

```
wf-{slug}-{YYYYMMDD-HHMMSS}-{8hex}
e.g.  wf-auth-refactor-20260706-140312-d4e5f6a7
```

The slug comes from the current git branch name (if it's a genuine feature branch — not `main`/`master`/`develop`) or from the user request text. The timestamp + 8-hex suffix keeps ids unique, so two concurrent workflows for the same feature never collide.

Worktrees follow the same pattern: `.claude/worktrees/{slug}-{hex}` and branch `wf-{slug}-{hex}`, so they're identifiable AND traceable back to their workflow.

Old on-disk ids (pre-slug format) are fully backward-compatible — nothing changes for existing workflows.

---

## Live % progress in the statusline

When Craftflow is active, the statusline shows a live progress segment:

```
⚡ auth-refactor 60% · 🟢 phase_2 (3/5)
```

Updated every ~300ms alongside claude-hud, derived from the workflow's `.craftflow/state/` data with no agent interruption. The segment disappears when no workflow is active.

The progress % uses a 4-tier fallback: explicit phase list → phase-status map → coarse stage estimate (e.g. `fast_path_selected`→15%, `phase_exit_gate_passed`→80%) → 0%.

**Setup** — wire the wrapper once in `~/.claude/settings.json`:
```json
{
  "statusLine": {
    "type": "command",
    "command": "bash /path/to/craftflow-plugin/plugins/craftflow/scripts/craftflow_statusline.sh"
  }
}
```

**Revert** to plain claude-hud by restoring the original command (preserved as a comment in `craftflow_statusline.sh`).

---

## Check workflow status — non-interrupting

While a Craftflow agent is running, a second terminal can read live status from the
shared `.craftflow/state/` directory at any time:

```bash
# Resolve the script path (run once):
python3 -c "
import json, pathlib
try:
    reg = json.loads(pathlib.Path('~/.claude/plugins/installed_plugins.json').expanduser().read_text())
    ip  = reg['plugins']['craftflow@craftflow'][0]['installPath']
    print(pathlib.Path(ip) / 'scripts' / 'craftflow_status_report.py')
except Exception as e:
    print(f'# {e}')
"

# Add a shell alias (replace PATH with the output above):
alias cfstatus='python3 /path/to/craftflow_status_report.py'

# Examples:
cfstatus                              # current / last-active workflow
cfstatus --all                        # one-line summary of every workflow
cfstatus --verbose                    # phases + agent chain + event timeline + narrative
cfstatus --feature "auth-refactor"    # find by feature slug, goal text, or request
cfstatus --worktree auth-refactor-d4e5f6a7  # find by worktree branch/slug suffix
cfstatus --statusline                 # single-line % segment (used by the wrapper)
cfstatus --project /path/to/project  # explicit root (auto-detected otherwise)
cfstatus --json                       # machine-readable JSON for tooling
```

You can also invoke it in-session (between agent turns) with:
```
craftflow status
```

---

## Install — Claude Code

```bash
claude plugin marketplace add aicraft-sdk/craftflow
claude plugin install craftflow
```

Then add to `~/.claude/CLAUDE.md`:

```markdown
[Craftflow]|entry: craftflow:craftflow-router
```

## Install — Cursor AI

If you have a local checkout of this plugin, run the script directly — it wires up the MDC rules **and** symlinks the `cursor-router` skill into `~/.cursor/skills/cursor-router` automatically (idempotent; backs up any stale content it finds there):

```bash
bash tools/craftflow-plugin/plugins/craftflow/install-cursor.sh
```

Without a local checkout (curl-piped), the script can only install the MDC rules — it has no local plugin directory to link the skill from, and prints a fallback note. In that case, install the skill separately first:

```bash
# 1. Install the cursor-router skill
npx skills add aicraft-sdk/craftflow --skill cursor-router

# 2. Install MDC rules (auto-activates Craftflow on every dev request)
curl -fsSL https://raw.githubusercontent.com/aicraft-sdk/craftflow/main/plugins/craftflow/install-cursor.sh | bash
```

Craftflow will activate automatically on every dev request via `alwaysApply: true`.

When run from a local checkout, `install-cursor.sh` also offers an **optional, opt-in** step to
pre-populate this project's Cursor CLI permission allowlist (`.cursor/cli.json` — not the global
`~/.cursor/cli-config.json`) with common read-only/safe commands (`grep`, `git status`/`diff`/
`log`, etc.) so they stop prompting for approval every session. It's off by default; enable it
with `CRAFTFLOW_CURSOR_PERMISSIONS=1` or by answering `y` at the interactive prompt. See
`plugins/craftflow/hooks/README.md`'s "Optional: Cursor CLI Permission Allowlist" section for
details.

---

## How it works

### Claude Code

```
User request
  → craftflow-router (Skill)
    → dispatches Agent(agentType="craftflow:component-builder", ...)
    → dispatches Agent(agentType="craftflow:code-reviewer", ...)
    → dispatches Agent(agentType="craftflow:integration-verifier", ...)
    → writes .craftflow/state/workflows/{wf}.json
    → updates .craftflow/state/project/activeContext.md
```

### Cursor AI

```
User request
  → craftflow-router.mdc (auto-injected)
    → Read("skills/cursor-router/SKILL.md")
    → dispatches each phase via Task(subagent_type: generalPurpose, prompt: <agent role + overrides>)
    → dispatches code-reviewer + silent-failure-hunter in parallel (two Task calls, same message)
    → writes .craftflow/state/cursor-wf.json
    → updates .craftflow/state/project/activeContext.md
```

There is no real task-tracking system in Cursor (no `TaskCreate`/`TaskUpdate`/`TaskList`/`TaskGet`) — phase-state tracking is self-managed via `cursor-wf.json`.

Progress blocks appear inline in Cursor chat at each phase transition:

```
╔══ CRAFTFLOW BUILD ════════════════════════════════╗
║ Phase 2 / 4 · code-reviewer                       ║
╠═══════════════════════════════════════════════════╣
║ ✅ component-builder      DONE                    ║
║ ⏳ code-reviewer          RUNNING...              ║
║ ○  silent-failure-hunter  WAITING                 ║
║ ○  integration-verifier   WAITING                 ║
╚═══════════════════════════════════════════════════╝
```

---

## State

Workflow state lives at `.craftflow/state/` in the project root:

| Path | Purpose |
|------|---------|
| `project/activeContext.md` | Current focus, decisions, learnings — persists across sessions |
| `project/patterns.md` | Durable code patterns and gotchas |
| `project/progress.md` | Completed workflows and verification evidence |
| `workflows/{wf-id}.json` | Per-workflow artifact (plan, phase status, evidence). New format: `wf-{slug}-{date}-{hex}.json` |
| `precompact-state.json` | Per-turn pointer to the current active workflow (written by the `Stop` hook) |

---

## Optional: Jev routing hint (TypeSafe AI)

An opt-in hook system can ask TypeSafe AI's Jev classifier for a routing/skill hint before
each prompt. It is **`enabled: false` by default** in the committed `config/jev.json` —
off by default means zero behavior change, zero network calls, and zero cost until the user
explicitly opts in.

**Two onboarding paths exist:**

1. **Automatic (default, if `TYPESAFE_API_KEY` is set):** When you submit a prompt and haven't
yet consented to Jev, an `AskUserQuestion` gate appears asking "Enable the optional Jev
(TypeSafe AI) routing hint?" Once you grant consent, a session-scoped canary call runs and
Jev activates for that session (and will re-check at every `SessionStart` firing thereafter).
The assistant invokes `craftflow_jev_setup.py --record-consent granted` to record your
choice durably. This auto-detect flow requires zero manual setup.

2. **Manual override (`jev-setup` skill or direct CLI):** Run the `jev-setup` skill at any
time, or the commands directly:

```bash
python3 scripts/craftflow_jev_setup.py --check    # canary call + prints the privacy note; writes nothing
python3 scripts/craftflow_jev_setup.py --enable    # re-runs --check, then flips enabled:true on success
python3 scripts/craftflow_jev_setup.py --disable   # flips enabled:false
```

Once `--enable`d (manual path active), auto-detect is a no-op — the manual path takes
precedence and Jev stays on across all sessions until you run `--disable`.

**Privacy** (verbatim from `craftflow_jev_setup.py`'s printed privacy note):

> PRIVACY: when enabled, each prompt you submit in Claude Code (first `maxStateChars` chars), the project folder name,
> and the active craftflow workflow type are sent to api.typesafe.ai (TypeSafe AI) for classification.
> Nothing else is sent; no prompt text is stored locally; telemetry rows contain only answers/latency/usage.
> Disable at any time: `python3 scripts/craftflow_jev_setup.py --disable`

**Modes:** each feature (`features.routingHint`, `features.skillHint`, `features.remediationScope`) is independently one
of `off` / `audit` / `advise`. `audit` logs telemetry only and injects nothing. `advise`
additionally injects a `<craftflow_routing_hint source="jev">` block via
`hookSpecificOutput.additionalContext` when the answer's confidence is at/above the
configured threshold.

**Thresholds:** `thresholds.routing` (default `0.85`), `thresholds.skill` (default
`0.7`), and `thresholds.remediationScope` (default `0.85`) gate `advise`-mode injection per feature; below threshold, nothing is injected even
in `advise` mode.

**Timeout:** `timeoutSeconds` caps the client's total deadline (default `2.5`, max `4.0`).
At `4.0` there is no retry budget left inside the 5s hook timeout — DD-4's retry only fires
when at least 1.0s remains after a 0.3s backoff, so a `timeoutSeconds` of `4.0` consumes the
whole budget on the first attempt.

**Promotion:** `python3 scripts/craftflow_jev_report.py` summarizes agreement rate, latency,
and token stats per feature from the telemetry log, ending in a DD-11 `PROMOTE`/`HOLD`
verdict (`PROMOTE` requires `n >= 100` and per-feature minimum agreement — `0.80` routing,
`0.60` skill, `0.80` remediationScope, `0.80` risk_gate). Only a `PROMOTE` verdict justifies manually flipping a feature from `audit` to
`advise` in `config/jev.json`.

**Config reset on update:** a plugin update resets `config/jev.json` to the shipped
defaults (`enabled: false`); re-run `--enable` after updating if the hint was previously
turned on.

**Telemetry:** rows are appended to `.craftflow/state/jev/events.jsonl` — never prompt text,
never the API key.

**Model:** pinned via `config/jev.json`'s `model` key (default `jev-latest`); README
recommends pinning to `jev-1.12` for stable calibration once any feature reaches `advise`.

**Never blocks:** this hook only ever adds advisory `additionalContext` or does nothing —
it never emits a routing decision and never denies a prompt. Priority-1 ERROR keywords in
the router's Intent Routing table always win over any Jev hint (see `router-protocol.md`
§ Intent Routing).

---

## Optional: context-size nudge

`craftflow_context_nudge.py` measures the session's context size from the transcript tail (local only, never blocks) and warns once per threshold crossing. It runs on `UserPromptSubmit`, resets on `SessionStart(compact)`, and is also called by the router at phase boundaries.

- Default is `audit`: decisions are logged, nothing is shown. To enable durably, create `~/.claude/craftflow/context-nudge.json` with `{"contextNudge": "on"}`: it survives plugin updates (editing `config/hook-mode.json` also works but is reset by every update).
- The same file may also set the thresholds keys (`warnTokens`, `criticalTokens`, `assumedWindow`), layered per key over the plugin defaults; invalid keys are ignored and logged.
- `CRAFTFLOW_CONTEXT_NUDGE_USER_CONFIG` points at another file (tests and diagnostics).
- Advisories end with a ready-to-paste `/compact` command naming the active workflow when it can be identified unambiguously from this session, else a generic one. Session binding is best-effort: the router does not yet stamp `session_id` into workflow artifacts, so the session filter is inert today and a parallel session's live workflow that this transcript mentions can be bound. The line names the workflow, so check it before pasting. Router-side stamping is a follow-up; once it lands, other sessions' workflows are excluded exactly.
- Switching to `on` mid-session takes effect at the next threshold crossing or after `/compact`.
- Thresholds live in `config/context-nudge.json` (`warnTokens`, `criticalTokens`, `assumedWindow`). Defaults assume a 200k window; on a 1M window raise `assumedWindow` and both thresholds.
- Phase-boundary `/compact` prompt: after each BUILD phase exit (and at PLAN hand-off) the router runs the check (`skills/craftflow-router/references/context-boundary.md`). A `warn` is informational; a `critical` makes the router persist the workflow artifact and pause so you can run `/compact` and say "continue".
- Every decision is logged as the `context_nudge` event (see `docs/craftflow-event-contract.md`); per-session state is kept under `.craftflow/state/context-nudge/`.

## Optional: Stop gate (shadow, plus armed continue)

`craftflow_stop_gate.py` is an opt-in `Stop` hook that classifies each end-of-turn stop and logs what it *would* do (continue to the next approved phase, local commit, or wait for you). Slice 1 (SPEC-0018, ADR-0055) is shadow only. Slice 2 (SPEC-0019, ADR-0056) adds exactly one action: when mode is `on` AND you have armed it (below), it blocks the stop with a constant reason so the agent continues to the next already-approved phase. It never pushes, opens a PR, merges, commits or picks backlog work. It always exits 0 (fail open: any error means no output) and does nothing unless `hook_event_name` is `Stop`.

Modes (`mode` key, shipped default `off` in `config/stop-gate.json`):

- `off`: with no user settings file, exits before importing heavy modules and writes nothing.
- `audit`: evaluates hard rules H00-H19 and appends one row per stop to `.craftflow/state/stop-gate/events.jsonl`, plus one `plugin_stop_gate` log event. Rows never hold message text.
- `on`: audits exactly like `audit` and acts only when armed (mode tag `act_<arm_status>`, for example `act_not_armed` or `act_armed`). Without a valid arm it never blocks.

Enable durably with `~/.claude/craftflow/stop-gate.json`, e.g. `{"mode": "audit"}`. `CRAFTFLOW_STOP_GATE_USER_CONFIG` points at another file (tests, diagnostics). Invalid or unknown keys are ignored and tagged in the row's `settings_tags`.

| Key | Values | Default | Notes |
|---|---|---|---|
| `mode` | `off`, `audit`, `on` | `off` | `on` acts only when armed |
| `notify` | `off`, `desktop`, `push` | `off` | `push` needs the consent file (below) |
| `notifyMinTurnSeconds` | 0-86400 | 300 | minimum turn length before a notification |
| `jevKindThreshold` | 0.5-1.0 | 0.9 | minimum Jev kind confidence |
| `jevNeedsHumanMax` | 0.0-0.5 | 0.2 | maximum Jev needs-human score |
| `jevTimeoutSeconds` | 0.5-2.5 | 2.0 | Jev call budget |
| `tailChars` | 200-4000 | 1500 | tail length used for text rules and Jev |
| `maxAutoContinuesPerSession` | 0-50 | 5 | continue budget between human lines (H17; ACT uses the smaller of this and the arm value; 0 disables ACT) |
| `intCursorMeaning` | `finished_count`, `one_based_current` | `finished_count` | plugin file only; ignored in the user file |
| `jevText` | `true` | absent | consent file only |

**Consent file.** `~/.claude/craftflow/stop-gate.json` read from the passwd home (not `$HOME`, not the env seam), without following symlinks, owned by you and at most 64 KiB, is the only place `jevText: true` and `notify: "push"` are honoured. The same path serves as the user settings file; a seam or `HOME`-redirected copy can set `mode`, `notify: "desktop"` and thresholds (local logging and banners only) but is ignored for `jevText` (tag `jev_text_seam_ignored`) and `push` (tag `notify_push_seam_ignored`, falls back to `desktop`). Accepted risk: an agent with file-write access can edit this file, so treat it as your consent, not a security boundary against the agent.

**Arming continue ACT (SPEC-0019).** `on` never acts by itself. Run the arm CLI in your own terminal:

```bash
python3 scripts/craftflow_stop_gate_arm.py status                       # read-only; caveat human_turn_unchecked
python3 scripts/craftflow_stop_gate_arm.py arm --workflow WF [--hours H]  # TTY only; --workflow required; 1-24 h, default 8
python3 scripts/craftflow_stop_gate_arm.py disarm                       # works without a TTY
```

- `arm` needs a real TTY, a valid workflow id (`--workflow` is required), runs the report with `--scope jev`, refuses (exit 2) unless the GO criteria are met, and asks you to type the project folder name. The arm is per project (realpath of `CLAUDE_PROJECT_DIR` or the git toplevel) and expires after 1-24 h.
- The arm is written only into the passwd-home consent file (key `actContinue`, atomic, mode 0600, symlinks refused, other keys preserved). The shipped plugin and tests never write your real consent file; an arm in the env seam or a `HOME`-redirected file is ignored (tag `act_arm_seam_ignored`).
- GO stamp shape: `go.{at, stats, schemas, scope, jevKindThreshold, jevNeedsHumanMax, met}`. The arm also records `maxAutoContinuesPerSession`; ACT uses `min(arm value, settings value)` and refuses if you later loosen the Jev thresholds.
- Arm first, THEN send your prompt: the consent file ctime must not be newer than your last genuine human line (`arm_after_last_human`). `status` cannot see the transcript, so it reports the caveat `human_turn_unchecked`.
- The only autonomous action is continue: the hook prints `{"decision":"block","reason":<template>}` and exits 0. The reason is built in code from this constant and validated ids only (no message, Jev or artifact text ever enters it):

> craftflow stop-gate: auto-continue %d/%d (armed by the user). The approved plan of workflow %s continues with phase %s. Run only phase %s under the craftflow router BUILD rules. Do not push, open a pull request, merge, or start any other work. If this phase needs the user, stop and say why.

- Block output happens only when ALL hold: mode `on`; no hard rule hit; no act blocker; a Jev `would_continue` verdict marked `act_eligible`. The relay (needs a human) and continue (needs `would_continue`) are mutually exclusive. The session record is written before the block is printed and printed only if that write succeeded.
- A negative human reply after an auto-continue disarms the session (A08); the reply text stays in memory only.

**Hard rules H16-H19** (every mode, after H15): H16 `no_progress`; H17 `continue_budget` (applies only when `maxAutoContinuesPerSession >= 1`); H18 `checkpoint_phase` (the next or just-completed phase has a `checkpoint_type` other than `none`); H19 `stop_reason_not_end_turn` (a string `stop_reason` other than `end_turn`).

**Act blockers A01-A17** (logged on every stop in `act_blockers`, consulted only for ACT): A01 mode not `on`; A02 not armed; A03 binding not exact (`binding_reason == session_match`, artifact `session_id` equals the payload `session_id`, and no newer `session_rebound`); A04 `checkpoint_type` missing; A05 not a Jev verdict; A06 human turn unknown; A07 stop-verify enabled; A08 disarmed after a negative reply; A09 `CRAFTFLOW_JEV_ENDPOINT` set (defence in depth: the Jev client no longer reads that variable); A10 no session id; A11 tail already acted on; A12 phase or workflow id unrenderable; A13 session record unreliable; A14 settings not from the user layer; A15 budget zero or spent; A16 `stop_reason` not an explicit string (H19 needs an explicit `end_turn`); A17 tail truncated (1 MiB) after an act.

**Limits and accepted residuals (friction, not security).** The TTY check and typed confirmation are friction; an agent shell can bypass a TTY (for example with `script`), `events.jsonl` is repo-writable so seeded rows could fake a GO, and the consent file is agent-writable. Accepted residuals:

- the session record is agent-writable (HIGH-3), so a hostile agent could reset the counter; A13 only catches unreliable records it can see;
- a human line can be forged in the transcript (HIGH-2); only a future-timestamp cap applies;
- an artifact `worktree_path` can steer the git facts (MEDIUM-4);
- residual self-arm at the next human turn (R2/F8): an agent that arms itself before your next prompt is stopped only by the ctime rule, the 24 h maximum and project scoping;
- negative-reply detection reads a tail window, and a 1 MiB truncated transcript trips the truncation blockers (A17), which fail closed;
- the push relay is skipped under `stop_hook_active`, so a push user is not notified when an auto-continue chain ends (relay at chain end, A9, is deferred);
- the repository-set endpoint issue is fixed: the Jev client no longer reads `CRAFTFLOW_JEV_ENDPOINT`, and A09 stays only as defence in depth. The sole override is the user-level file `~/.claude/craftflow/jev-endpoint.json` (`{"endpoint": "<url>"}`), resolved from the passwd home (never `$HOME`). It must be a regular file of at most 4096 bytes, the URL must be http or https with a loopback host (127.0.0.1, localhost, ::1), and redirects are not followed. Invalid values fall back to the default endpoint and are logged (`endpoint_file_unreadable`, `endpoint_home_unresolved`, `endpoint_override_ignored`). The file is a test seam, not for production use;
- after `/clear` or in a new session the binding is `no_live_candidate` until the router restamps `session_id`, so ACT waits (safe direction);
- ACT is inert until shadow data exists: GO cannot be met with zero rows, so `arm` refuses.

**Privacy.** When `jevText` is true and Jev is active, the last up to `tailChars` characters of the assistant's final message, after credential masking, plus the workflow type and whether a next phase exists, are sent to api.typesafe.ai for classification. Nothing is stored locally; decision records hold only labels, scores, lengths and a short hash.

**Push notifications.** `notify: "push"` takes effect only when the user-layer setting says `"push"` AND the consent file also sets `notify: "push"` (stricter than a single layer). The relay costs one extra short model turn (a `decision: block` asking for one `PushNotification` call). It fires at most once per tail sha, at most once per genuine human turn, never when `stop_hook_active` is true, and is refused without a `session_id`. `desktop` uses local `osascript`/`notify-send` and costs no model turn. Notification text is built from allowlisted fields only.

**Error rows.** If the hook fails while active it appends a row with `row_kind: "error"` and `settings_tags: ["hook_error:<ExceptionClassName>"]` (class name only, never the message). If that cannot be written, a `plugin_stop_gate_error` log event is emitted instead.

**Report CLI.** Labels each row by your next genuine reply in the same transcript and prints one JSON object (verdict counts, rule hits, Jev and heuristic precision/coverage, threshold sweep, latency, summed Jev usage, `go_criteria`):

```bash
python3 scripts/craftflow_stop_gate_report.py [--events FILE] [--transcripts-root DIR]
python3 scripts/craftflow_stop_gate_report.py --replay --transcripts-root DIR   # counts only, offline
```

It is read-only and never prints message text. ACT stays **NO-GO** until the RD-3 go criteria all hold: at least 50 labeled `would_continue` rows, at least 5 sessions, precision >= 0.95, zero negated replies, Jev failure rate <= 5%, hook p90 <= 1500 ms, `label_coverage` >= 0.8 (labeled rows over all rows) and `jev_calls_min` >= 20 Jev calls.

**Tests.** `python3 tests/fixtures/test_craftflow_stop_gate.py` includes load-sensitive timing tests (hook latency budgets); run them on a quiet host and re-run before treating a timing failure as a regression.

---

## Architecture graph

`docs/generated/architecture.md` is a generated (not hand-maintained) view of
this plugin's actual hook/agent/skill wiring — hook event → matcher → script,
agent → declared skills, agent → declared tools — introspected straight from
`hooks/hooks.json` and `agents/*.md` / `skills/*/SKILL.md` frontmatter.
Regenerate with `pnpm run gen:craftflow-graph` after touching any of those;
`pnpm run verify:craftflow-graph` checks it isn't stale.

---

## License

MIT — see [LICENSE](LICENSE)
