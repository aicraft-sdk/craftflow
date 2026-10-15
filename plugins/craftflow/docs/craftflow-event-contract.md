# Craftflow Event Contract

> Item 8 (`todo.md`), Phase 0 (Layer A) of
> `docs/plans/2026-08-19-craftflow-hooks-as-bridge-design.md`.
>
> This is a **formalization of behavior that already exists in code** — not a new
> mechanism. It documents the shape every `craftflow_*.py` hook script already reads
> from stdin and writes to stdout, and the shape `craftflow_cursor_adapter.py` already
> bridges Cursor's native hook payload onto so the same 46 scripts run unchanged on both
> hosts. Zero behavior change accompanies this doc; the `TypedDict`s in
> `scripts/craftflow_hooklib.py` are additive type annotations extracted from the
> functions listed below.

## Why this exists

Craftflow's real extension surface is a small set of typed internal events
(`PreToolUse`, `SessionStart`, ...). Claude Code's `hooks.json` + Python scripts are the
*native* binding of that surface; Cursor's `.cursor/hooks.json` +
`craftflow_cursor_adapter.py` are a second binding that normalizes onto the same shape.
Before this doc, that contract was implicit — reverse-engineerable only by reading
`craftflow_hooklib.py` and `craftflow_cursor_adapter.py` source. This doc makes it
explicit so a future third host's author has one place to read instead of two files to
grep.

## Canonical hook events

The 11 event names below are Claude Code's native hook event vocabulary
(`tools/craftflow-plugin/plugins/craftflow/hooks/hooks.json`). Every hook script keys off
`hook_event_name` (or the underlying function names below) matching one of these.

| `hook_event_name` | Fires on | Wired via matcher(s) in `hooks.json` |
|---|---|---|
| `PreToolUse` | before a tool call executes | `Edit\|Write`, `Read`, `WebFetch`, `Bash` |
| `SessionStart` | session boot, resume, or post-compact | `startup`, `startup\|resume\|compact`, `compact` |
| `PostToolUse` | after a tool call executes | `Edit\|Write`, `WebFetch`, unmatched |
| `TaskCompleted` | a `TaskUpdate`/`TaskCreate`-tracked task finishes | unmatched |
| `PostCompact` | context compaction finishes | unmatched |
| `SubagentStop` | a dispatched subagent's turn ends | unmatched |
| `PreCompact` | context compaction is about to run | unmatched |
| `Stop` | the main agent turn ends | unmatched |
| `StopFailure` | the main agent turn ends abnormally | unmatched |
| `InstructionsLoaded` | project/global instruction files are loaded | unmatched |
| `UserPromptSubmit` | the user submits a prompt, before the model sees it (opt-in Jev hint; inert by default; context-size nudge, audit by default) | unmatched |

`UserPromptSubmit` is intentionally absent from `HookEventName` in `scripts/craftflow_hooklib.py`
(which keeps its 10 members): `craftflow_jev_prompt_hint.py` does not use the `TypedDict`s or any
event-typed helper, and `craftflow_hooklib.py` is frozen for that feature. `pnpm run
verify:event-contract` reads this table, not the Literal, so this table is the registered-event
vocabulary and the Literal is the typed subset.

## Request shape (`load_input()`)

Every hook script calls `craftflow_hooklib.load_input()`, which reads one JSON object
from stdin (or returns `{}` on missing/invalid input — see Error Handling below).

```python
class HookRequest(TypedDict):
    hook_event_name: HookEventName   # always present

class HookRequestOptional(TypedDict, total=False):
    tool_name: str                   # PreToolUse / PostToolUse only
    tool_input: Dict[str, Any]       # PreToolUse / PostToolUse only
    session_id: str
    cwd: str                         # Claude Code native
    workspace_roots: List[str]       # Cursor native, per ADR-0003
```

`hook_event_name`, `tool_name`, and `tool_input` are the fields every hook script
actually reads. `cwd` and `workspace_roots` are host-native fields passed through
unvalidated — no current script depends on them, they are documented here so a future
binding knows they exist and are host-specific, not part of the required core.
`prompt` (UserPromptSubmit only, Claude Code native) is read solely by
`craftflow_jev_prompt_hint.py` and is not added to the `TypedDict`s — it is passed through
unvalidated like `cwd`.
`transcript_path` (Claude Code native) is read by `craftflow_context_nudge.py`; passed through unvalidated beyond the `.jsonl`/regular-file guard.

## Response shape (`json_print()`)

Hook scripts write one JSON object to stdout via `craftflow_hooklib.json_print()`.
Five call sites build it:

```python
class HookSpecificOutput(TypedDict, total=False):
    hookEventName: str
    permissionDecision: Literal["deny"]
    permissionDecisionReason: str
    additionalContext: str

class HookResponse(TypedDict, total=False):
    hookSpecificOutput: HookSpecificOutput
```

| Helper | `hookEventName` | Sets | Used for |
|---|---|---|---|
| `pretool_deny(reason)` | `"PreToolUse"` | `permissionDecision: "deny"`, `permissionDecisionReason` | blocking a tool call before it runs |
| `posttool_context(message)` | `"PostToolUse"` | `additionalContext` | non-blocking nudge after a tool already ran (no `permissionDecision` — too late to deny) |
| `session_context(message)` | `"SessionStart"` | `additionalContext` | injecting context at session boot/resume/compact |
| `craftflow_jev_prompt_hint.py` (inline `json_print`) | `"UserPromptSubmit"` | `additionalContext` | advisory routing/skill hint injected before the model sees the prompt (never a decision) |
| `craftflow_context_nudge.py` (inline `json_print`) | `"UserPromptSubmit"` | `additionalContext` + top-level `systemMessage` | one-line context-size advisory ending with a ready-to-paste `/compact` command, once per threshold crossing, only when `contextNudge` is `on` (never a decision) |

`craftflow_context_nudge.py` additionally sets a top-level `systemMessage` (user-visible copy of the advisory); it is not part of the `HookResponse` TypedDict above.

A script that emits no output (prints nothing) is the implicit "allow, no comment"
response — this is not a distinct shape, it's simply skipping the optional write.

## Per-host binding

| Concern | Claude Code (native) | Cursor (via `craftflow_cursor_adapter.py`) |
|---|---|---|
| Registration file | `hooks/hooks.json` | `.cursor/hooks.json` (project) / `~/.cursor/hooks.json` (merged, per ADR-0003) |
| Request shape on the wire | `HookRequest` directly, as documented above | `{"event", "toolName", "toolInput", ...}` — remapped by `normalize_input()` to `HookRequest` shape before the target script runs as a subprocess |
| Event-name mapping | 1:1 — `hooks.json` matcher keys are the `hook_event_name` values | Not always 1:1: e.g. Cursor's `afterFileEdit` and `postToolUse` hooks both invoke their target with `--event PostToolUse`; Cursor's `subagentStop` hook invokes `craftflow_task_completed_guard.py` with `--event TaskCompleted` and `craftflow_subagent_stop_audit.py` with `--event SubagentStop` — the adapter's `--event` flag is the source of truth, not the Cursor-side hook key name |
| Response shape on the wire | `HookResponse` directly, read natively by Claude Code | Target script's `HookResponse` stdout is read by `translate_output()`: `permissionDecision == "deny"` → Cursor exit code 2; anything else → exit 0, with `additionalContext` (if present) passed through on stdout |
| Target script | invoked directly (`python3 .../craftflow_*.py`) | invoked as a subprocess of `craftflow_cursor_adapter.py .../craftflow_*.py --tool ... --event ...` — the same script binary, unmodified |

**A third host** implementing this contract needs: (1) a way to invoke a Python
subprocess with the `HookRequest` shape on stdin (or, like the Cursor adapter, a small
translation shim if its native payload differs), and (2) a way to interpret the
`HookResponse` shape on stdout into that host's own allow/deny/context-injection
primitives. No new craftflow-side script is required — all 46 existing
`craftflow_*.py` scripts already only depend on this contract, not on which host
invoked them.

## Error handling

- **Missing/empty stdin, or non-JSON stdin:** `load_input()` returns `{}`. Every caller
  reads via `.get(...)`, so this degrades to "no fields present," not a crash.
- **Valid JSON but not a JSON object** (e.g. a bare list): `load_input()` coerces to
  `{}` — the same missing-input default (see `craftflow_hooklib.py::load_input()`'s
  inline comment for the incident this guards).
- **Cursor payload missing `toolName`/`toolInput`:** `normalize_input()` defaults
  `tool_name` to `""` and `tool_input` to `{}` rather than omitting the keys.
- **Target script crashes (non-zero exit) under the Cursor adapter:** `translate_output()`
  fails open (Cursor still gets exit 0) but logs the crash via `log_event()` first, so a
  crash is never silently indistinguishable from "nothing happened."

## Non-goals of this doc

- This is not a JSON Schema and no runtime validation is added against it — see
  `docs/plans/2026-08-19-craftflow-hooks-as-bridge-design.md`'s
  `[NEEDS CLARIFICATION-2]` resolution for why `TypedDict` + prose was chosen over a
  standalone schema artifact.
- This does not change `hooks.json`'s or `.cursor/hooks.json`'s on-disk format — see
  the same plan's `[NEEDS CLARIFICATION-3]` resolution.
- This does not cover the router *orchestration protocol* (`SKILL.md` content) — that is
  Layer B of the same plan, starting at its Phase 1.

## Hook-emitted log events (craftflow-hook-events.log)

Records in `.craftflow/state/craftflow-hook-events.log` are JSON lines written by
`craftflow_hooklib.log_event(name, payload)`. The `event` field equals `name` unless the
payload itself carries an `event` key, which overwrites it (the existing SubagentStop
audit record is logged under `event: "subagent_stop"` for that reason). Payloads for the
events below must never contain an `event` key.

### Log event: `agent_usage`

- Emitter: `scripts/craftflow_subagent_stop_audit.py` (`_log_agent_usage`), run after the
  existing SubagentStop events so they are written first.
- Trigger: a craftflow SubagentStop when the `agentUsageTelemetry` mode is not `off`.
- Fail-open: any failure logs a reduced record (`schema`, `agent_type`, `agent_id`,
  `error`) and never blocks the hook.
- `schema`: integer, currently `1`.
- `agent_type`, `agent_id`: strings copied from the SubagentStop payload.
- `session_id`: string or `null`.
- `stop_hook_active`: boolean.
- `mode`: `audit`, or `audit-unrecognized-config-value` when the configured value is
  neither `audit` nor `off`.
- `contract_source`: `hook_last_message` (shape classified from `last_assistant_message`).
- `contract_shape`: `yaml_block`, `envelope`, `heading_other`, `none`, or `null` when the
  classifier itself failed.
- `contract_valid`: boolean for `yaml_block`, otherwise `null`.
- `contract_errors`: up to 3 strings, each at most 120 characters (`classifier_error`,
  `validator_unavailable`, `classify_error` are the internal markers).
- `models`: object keyed by model id. Each value holds `input_tokens`, `output_tokens`,
  `cache_read_input_tokens`, `cache_creation_input_tokens`, `cache_write_5m`,
  `cache_write_1h`, `messages`, `fast_messages`.
- `primary_model`: model id with the most output tokens (first seen wins ties), or `null`.
- `turns`: number of distinct API messages.
- `duration_s`: seconds between first and last transcript timestamp, or `null`.
- `effort`: most common `effort` value in the transcript, or `null`.
- `attribution_agent`: first `attributionAgent` seen, or `null`.
- `workflow_id`, `dispatch_phase`: parsed from the first user message (dispatch prompt),
  or `null`; `is_remfix`: boolean.
- `unkeyed_messages`, `corrupt_lines`, `invalid_usage_values`: integer counters of
  transcript rows that were skipped or coerced to 0.
- `partial`: boolean, true when the parse stopped early.
- `error`: `null` on success, otherwise one of `no_transcript_path`, `transcript_missing`,
  `transcript_not_regular_jsonl`, `transcript_too_large`, `time_budget_exceeded`, or
  `unexpected:<Type>`.
- Dedupe rule: assistant records sharing a `message.id` are one API message (streamed
  chunks), so per-field maximum is taken across duplicates before summing per model.
- Cursor: `agent_transcript_path` is absent, so `error: no_transcript_path`.

Shape (abridged):

```json
{
  "event": "agent_usage",
  "schema": 1,
  "agent_type": "craftflow:component-builder",
  "mode": "audit",
  "contract_shape": "yaml_block",
  "models": {
    "claude-sonnet-5-5": {
      "input_tokens": 120,
      "output_tokens": 900,
      "messages": 4
    }
  },
  "primary_model": "claude-sonnet-5-5",
  "error": null
}
```

### Log event: `context_nudge`

- Emitter: `scripts/craftflow_context_nudge.py` (`hook_main` for `UserPromptSubmit`, `reset_main`
  for `SessionStart(compact)` via `--reset`, `boundary_main` for the router's `--boundary` call).
- Trigger: a decision only (DD-8): a `UserPromptSubmit` that reaches a measurement or skip, a
  post-compact reset, or a phase-boundary check. Prompts below threshold with no state change
  are not logged.
- Fail-open: any failure logs `outcome: "error"` with an `error` value and never blocks.
- No `event` key in the payload (ADR-0048 DD-2).
- `schema`: integer, currently `1`.
- `source`: `hook`, `reset`, or `boundary`.
- `mode`: `audit`, `on`, or `audit-unrecognized-config-value`; reflects the user override when one applies.
- `session_id`: string or `null`.
- `level`: `none`, `warn`, `critical`, or `null`.
- `tokens`: measured context tokens, or `null`.
- `token_source`: `assistant`, `compact_boundary`, or `null`.
- `threshold`: the configured threshold that applied, or `null`.
- `last_level`: previously nudged level (hook only).
- `had_state`: boolean, whether a state file existed (reset only).
- `outcome`: `nudged`, `would_nudge`, `rearmed`, `skipped`, `reset`, `advised`, `would_advise`,
  `already_advised`, `below_threshold`, `no_session_state`, or `error`.
- `error`: `null`, `no_transcript_path`, `transcript_missing`, `transcript_not_regular_jsonl`,
  `no_usage`, `post_tokens_missing`, `config_invalid`, `bad_session_id`, `state_write_failed`,
  `workflow_missing`, `workflow_unreadable`, `bad_wf`, `bad_args`, `remeasure_failed`, or
  `unexpected:<Type>`.
- Boundary only: `wf`, `phase`, `session_source`, `relay`, `checkpoint_path`.
- `override`, `override_error`, `override_keys` (all rows): result of reading the durable user override
  `~/.claude/craftflow/context-nudge.json` (or the file named by `CRAFTFLOW_CONTEXT_NUDGE_USER_CONFIG`).
  `override` is `absent`, `applied`, `partial` or `error`; `override_error` is `null` or one of `corrupt`,
  `unreadable`, `too_large`, `home_unresolved`, `invalid_value`, `unknown_key`, `inconsistent_thresholds`;
  `override_keys` lists the keys that took effect.
- `compact_source`, `compact_reason`, `compact_wf` (hook `nudged`/`would_nudge` rows and boundary rows;
  `null` elsewhere): `compact_source` is `workflow` when the advisory's `/compact` line names a workflow,
  else `generic`. `compact_reason` is one of `session_match`, `single_candidate`, `mention_mtime_agree`,
  `ambiguous`, `no_mention`, `no_live_candidate`, `lookup_error` (hook lookup), or `explicit_wf`,
  `workflow_missing`, `workflow_unreadable`, `bad_wf` (boundary, from `--wf`). `compact_wf` is the bound
  workflow id or `null`. A workflow whose artifact `session_id` differs from the current session belongs to
  another session and is never bound; the line stays generic.

```json
{
  "schema": 1,
  "source": "hook",
  "mode": "on",
  "session_id": "6f1c0a52",
  "level": "warn",
  "tokens": 123456,
  "token_source": "assistant",
  "threshold": 120000,
  "last_level": "none",
  "outcome": "nudged",
  "error": null,
  "override": "absent",
  "override_error": null,
  "override_keys": [],
  "compact_source": "generic",
  "compact_reason": "no_mention",
  "compact_wf": null
}
```

### Log event: `plugin_stop_gate`

- Emitter: `scripts/craftflow_stop_gate.py` (`main`), after the shadow row is appended to
  `.craftflow/state/stop-gate/events.jsonl`. Never emitted in mode `off`.
- Trigger: one per `Stop` evaluated in mode `audit` or `on`.
- Fail-open: the hook always exits 0.
- No `event` key in the payload.
- Decision payload: `decision` (`needs_human`, `would_continue`, `would_commit`), `wf` (string or
  `null`), `mode`, `rule_hits` (list of `H##_...` codes), `row_kind` (`stop`, `relay_followup` or `error`), `acted`
  (boolean; true only when the hook printed a continue block, SPEC-0020). Armed-continue details live in the
  row (`act_blockers`, `arm_status`), not in this event.
- Config payload (same event name): `config_error` (`corrupt`, `unreadable`, `too_large`,
  `home_unresolved`, `not_object`) and `source: "user"`, when the user settings file is unusable.
- Related event `plugin_stop_gate_error` (payload `exc`: exception class name only) is logged when a hook
  failure could not be recorded as an `error` row (hook inert, or the row write itself failed), or, with
  `acted_undelivered: true`, when a continue block was recorded but writing it to stdout failed (the host never
  saw it).
- Related workflow event `session_rebound` (written by the router to the workflow `events.jsonl`, not a hook
  log event; fields `from`, `to` session ids): the router re-stamped the artifact `session_id` on resume. The
  stop gate treats a rebound newer than the last human line as blocker A03.

```json
{"decision": "needs_human", "wf": null, "mode": "audit", "rule_hits": ["H01_no_bound_workflow"], "row_kind": "stop"}
```

#### Stop-gate row (`.craftflow/state/stop-gate/events.jsonl`)

One JSON object per line with exactly these keys (`ROW_KEYS` in `scripts/craftflow_stop_gate_core.py`;
unknown keys are dropped, strings are cut to 200 chars, never message text):

`schema` (2; the report accepts 1 and 2), `row_kind` (`stop`, `relay_followup`, `error`), `ts`, `session_id`, `transcript_path`,
`mode`, `mode_tag`, `stop_hook_active`, `permission_mode`, `wf`, `binding_reason`, `phase_cursor`,
`cursor_case`, `cursor_resolution`, `rule_hits`, `loop_guards`, `commit_blockers`, `jev_text_source`,
`last_human_ts`, `heuristic_kind`, `heuristic_verdict`, `jev_status`, `jev_kind`, `jev_kind_conf`,
`jev_needs_human`, `jev_latency_ms`, `jev_usage`, `verdict`, `verdict_source`, `act_eligible`, `would_action`, `notify`, `notify_status`, `turn_seconds`,
`turn_seconds_lower_bound`, `message_chars`, `tail_sha` (short hash), `hook_ms`, `settings_tags`, and the
schema-2 keys `act_blockers` (A01..A17 codes), `acted` (true only when a continue block was printed),
`arm_status`, `stop_reason`, `continues_since_human`. `act_eligible` is true only for a clean Jev
`would_continue` verdict.

List-valued keys: `rule_hits`, `loop_guards`, `commit_blockers`, `settings_tags`, `act_blockers`. An `error` row carries
only `ts`, `session_id`, `mode`, `hook_ms` and `settings_tags: ["hook_error:<ExceptionClassName>"]`.

### Log event: `policy_loaded`

- Emitter: `scripts/craftflow_workflow_id.py` (`init_artifact`), best effort, after the workflow artifact is written.
- Trigger: once per minted workflow (`--init-artifact`). The same fact is also the second line of the workflow
  `events.jsonl` (`policy_loaded`, right after `workflow_started`) and the artifact's `policy` field.
- Fail-open: any failure to load the policy or write the line is swallowed; the mint never fails because of it.
- Payload: `wf` (workflow id), `policy_sha256` (sha256 of the bytes that were validated, `"defaults"` only when the file is
  missing, `"unreadable"` when no bytes were validated: the file could not be read (a directory, a FIFO or device, a symlink
  loop, permissions), was over the size limit, or the loader itself failed), `policy_status` (`ok`, `defaults`, `invalid` or `error`) and,
  for `invalid` or `error` only, `policy_errors` (at most 3 short strings). Informational only; no gate reads it.

```json
{"event": "policy_loaded", "wf": "wf-example-20261014-120000-0a1b2c3d", "policy_sha256": "defaults", "policy_status": "defaults"}
```

### Log event: `policy_invalid`

- Emitter: `scripts/craftflow_policy.py` (`load_policy`, via `_log_invalid`), at most once per process.
- Trigger: `config/policy.json` exists but is unreadable, over 64 KiB, not UTF-8, not JSON, not an object, has an unknown
  key or violates `scripts/schemas/policy.json`. The loader then returns the built-in defaults with status `invalid`
  and every gated decision takes its most restrictive branch (`memory_write` is document-only and keeps today's behavior).
- Fail-open: the line is written only inside an existing `.craftflow` project and any write failure is swallowed;
  loading a policy never prints to stdout.
- Also emitted, once per process and under the same project-only rule, by the `craftflow_hooklib.py` fallback when
  `craftflow_policy.py` cannot be imported (`path` is `craftflow_policy.py`; `errors` is one string, `import-error: <ExceptionType>: <message>`, at most 120 ASCII characters).
- Payload: `path` (the policy file) and `errors` (up to 5 validation messages).

```json
{"event": "policy_invalid", "path": "/plugin/config/policy.json", "errors": ["policy: file is over the 65536 byte limit"]}
```

The permit record the pretooluse guard already logs for a finalize-permitted memory write
(`reason: "memory-write-permitted-by-finalize-token"`) now also carries `memory_write`, the effective policy value
(`finalize-only`), and `policy_status` (`ok`, `defaults` or `invalid`). Both are read and logged only; the permit decision never depends on it.

### Log event: `native_plan_mode_denied`

- Emitter: `scripts/craftflow_pretooluse_guard.py` (`_handle_enter_plan_mode`), Claude Code only (`hooks/hooks.json` registers the
  `EnterPlanMode` matcher; the Cursor template does not).
- Trigger: an `EnterPlanMode` call while policy `native_plan_mode` is `never`, or the policy is `invalid`. The hook then prints a deny
  decision (reason prefix `craftflow-policy: native_plan_mode=never —` or `craftflow-policy: policy-invalid —`). `allow` logs nothing.
- Fail-open: a failed log write is swallowed; the deny is still printed.
- Payload: `native_plan_mode` (effective value), `policy_status` (`ok`, `defaults` or `invalid`) and `cwd`.

```json
{"event": "native_plan_mode_denied", "native_plan_mode": "never", "policy_status": "ok", "cwd": "/repo"}
```

### Log event: `plan_commit_denied`

- Emitter: `scripts/craftflow_pretooluse_bash_guard.py` (`_plan_commit_check`), Claude Code only (DD-12: Cursor payloads return before parsing).
- Trigger: a `git add|stage|commit` that names (or, for `commit`, stages) a plan artifact (`docs/plans/**` except `.gitkeep`,
  `.craftflow/state/workflows/*.json`, or the live artifact's `plan_file` / `design_file`) in a craftflow-managed project, including its
  linked worktrees, while policy `plan_commit` is `never` or `invalid`.
- Payload: `policy_key` (`plan_commit`), `policy_value`, `policy_status`, `paths` (top-relative protected paths) and `command`.

```json
{"event": "plan_commit_denied", "policy_key": "plan_commit", "policy_value": "never", "policy_status": "ok", "paths": ["docs/plans/x.md"], "command": "git add -f docs/plans/x.md"}
```

### Log event: `plan_commit_probe_error`

- Emitter: `scripts/craftflow_pretooluse_bash_guard.py` (`_plan_commit_check`).
- Trigger: the bounded (2 s) `git diff --name-only` probe behind a `git commit` failed, timed out, or git is missing. The commit is
  allowed (fail open, today's behavior); an explicit protected pathspec still denies.
- Payload: `command`, `probe` (`--cached` or `worktree`) and `reason` (`error`, or `budget` when the shared 5 s per-run probe budget
  was already spent, so chained commits cannot outlive the hook timeout). The merge status probe has its own fresh 2 s deadline
  (it does not draw on this budget) and, unlike this guard, denies on a genuine probe error.

### Log event: `plan_commit_dynamic_allowed`

- Emitter: `scripts/craftflow_pretooluse_bash_guard.py` (`_plan_commit_check`).
- Trigger: a plan-commit candidate with a dynamic pathspec (`$VAR`, `$(...)`), a dynamic `-C` / `cd` / `--git-dir` / `--work-tree`
  target, a dynamic command name (`$G add ...`), a dynamic `sh -c` string that names git, a bare `export GIT_DIR=...` /
  `GIT_WORK_TREE=...` statement, `xargs` / `find -exec` supplying pathspecs elsewhere, an unwrapper that gave up (`unwrap depth
  exceeded`, `wrapper iteration cap`), or an opaque construct naming git add/commit/merge (`pipe to a shell`, `here-document or
  here-string`, `interpreter string`, `command substitution`). It cannot be resolved statically, so it is allowed here and the
  staged-index probe catches it at commit.
- Payload: `command` (capped at 300 characters) and `reason`.

### Log event: `policy_guard_error`

- Emitter: `scripts/craftflow_pretooluse_bash_guard.py` (`main`).
- Trigger: the policy-guard step (`plan_commit`, merge clean tree) raised. The hook logs it and falls through to the existing
  destructive-command guards, so a bug here can never skip them or exit non-zero.
- Also writes one stderr warning line (the only time the policy pass prints anything besides a deny). Fail-open on an internal bug
  is deliberate: the alternative is blocking every Bash call.
- Payload: `error` (repr of the exception) and `command` (capped at 300 characters).

### Log event: `policy_guard_skipped`

- Emitter: `scripts/craftflow_pretooluse_bash_guard.py` (`_policy_guards`).
- Trigger: the policy pass was skipped or exhausted: `reason: "size"` (over 16 KB of command text), `"units"` (over 2000 command
  units), `"path"` (a tracked cwd over 4 KB) or `"time"` (the shared 5 s deadline passed while parsing). Logged only in a managed project.
- Outcome: when the text names `git` followed by add/stage/commit/update-index (or merge/pull together with a workflow branch name),
  the command is DENIED (`denied: true`, reason prefix `craftflow-policy: plan_commit —`, "split it into smaller commands"); `plan_commit: allow`,
  Cursor payloads and unmanaged projects are not denied. Any other command is allowed (`denied: false`).
- Payload: `reason`, `denied`, `chars` and `command` (capped at 300 characters).

### Log event: `worktree_merge_dirty_denied`

- Emitter: `scripts/craftflow_pretooluse_bash_guard.py` (`_merge_check`), Claude Code only (DD-12).
- Trigger: `git merge <b>` or `git pull . <b>` (also `refs/heads/<b>`, `heads/<b>`, `<b>~N`, `<b>^`, `<b>@{...}`) where `<b>` is the `worktree_branch` of an artifact in `<main_root>/.craftflow/state/workflows/*.json` and the
  main tree has uncommitted changes (`git status --porcelain` non-empty) or the status probe errored, while `worktree_merge.require_clean_tree`
  is true (the default until the policy ships the key). Other branches and unmanaged projects are untouched.
- Payload: `policy_key`, `policy_status`, `branch`, `status_error` (true when the probe itself failed) and `command`.

```json
{"event": "worktree_merge_dirty_denied", "policy_key": "worktree_merge.require_clean_tree", "policy_status": "ok", "branch": "wf-x-abc", "status_error": false, "command": "git merge wf-x-abc"}
```

## Keeping this doc honest

`scripts/verify-craftflow-event-contract.mjs` (`pnpm run verify:event-contract`) asserts
every canonical event in the table above has a real match in
`hooks/hooks.json`, and that `.cursor/hooks.json`'s adapter invocations only ever pass
`--event` values drawn from the same set. It also asserts every `### Log event:` heading
in the section above has a `scripts/*.py` caller of `log_event("<name>", ...)`. Run it
after editing this doc or either `hooks.json` file.
