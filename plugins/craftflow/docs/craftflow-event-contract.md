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
| `SessionStart` | session boot, resume, or post-compact | `startup`, `startup\|resume\|compact` |
| `PostToolUse` | after a tool call executes | `Edit\|Write`, `WebFetch`, unmatched |
| `TaskCompleted` | a `TaskUpdate`/`TaskCreate`-tracked task finishes | unmatched |
| `PostCompact` | context compaction finishes | unmatched |
| `SubagentStop` | a dispatched subagent's turn ends | unmatched |
| `PreCompact` | context compaction is about to run | unmatched |
| `Stop` | the main agent turn ends | unmatched |
| `StopFailure` | the main agent turn ends abnormally | unmatched |
| `InstructionsLoaded` | project/global instruction files are loaded | unmatched |
| `UserPromptSubmit` | the user submits a prompt, before the model sees it (opt-in Jev hint; inert by default) | unmatched |

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

## Response shape (`json_print()`)

Hook scripts write one JSON object to stdout via `craftflow_hooklib.json_print()`.
Four call sites build it:

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

## Keeping this doc honest

`scripts/verify-craftflow-event-contract.mjs` (`pnpm run verify:event-contract`) asserts
every canonical event in the table above has a real match in
`hooks/hooks.json`, and that `.cursor/hooks.json`'s adapter invocations only ever pass
`--event` values drawn from the same set. It also asserts every `### Log event:` heading
in the section above has a `scripts/*.py` caller of `log_event("<name>", ...)`. Run it
after editing this doc or either `hooks.json` file.
