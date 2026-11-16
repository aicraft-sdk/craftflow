# Tooling and mechanisms for deterministic guardrails on AI coding agents (state as of 2026-11-16)

**Read this first: how far to trust these notes.**
This session's network allowlist blocked direct fetches of almost every documentation host: cursor.com, developers.openai.com, geminicli.com, docs.github.com, opencode.ai, ampcode.com, eslint.org, biomejs.dev and ast-grep.github.io all failed to resolve. Only code.claude.com could be fetched and read in full. Each finding therefore carries one of three evidence grades:

- **[PRIMARY-READ]**: I read the official page text this session (Claude Code docs only).
- **[SNIPPET]**: from a web-search result summary of the linked page. The link is the page the snippet came from; I did not read the full page. Exact field names and defaults should be re-checked before being quoted in a report.
- **[SECONDARY]**: blog, forum or third-party guide, via search snippet.

Anything I know only from training memory and could not confirm this session is in a "Gaps" list and labelled "unverified recall". Config examples marked "illustrative" are assembled by me from documented option shapes and are not copied from docs.

---

## 1. Agent-level hooks: Claude Code, and equivalents in Cursor, Codex CLI, Gemini CLI, Copilot, Amp, OpenCode

### Takeaway
By late 2026 every major coding agent has a pre-tool hook that can deny a tool call and return a reason to the model; Claude Code's is the most extensive (33 events, 5 handler types) and the only one I could verify in full. Most hook systems fail open when the hook crashes or times out, pre-tool hooks do not cover every path to the filesystem, and vendors themselves say hooks are guardrails and not security boundaries, so CI must remain the final authority.

### Cited Findings

#### Claude Code (docs fetched 2026-11-16; pages reference versions up to v2.1.314)

- [PRIMARY-READ] Hooks are "user-defined shell commands" run at lifecycle points, giving "deterministic control: certain actions always happen rather than relying on the LLM to choose to run them." — [Hooks guide](https://code.claude.com/docs/en/hooks-guide)
- [PRIMARY-READ] The reference lists 33 events. Per tool call: `PreToolUse`, `PermissionRequest`, `PermissionDenied`, `PostToolUse`, `PostToolUseFailure`, `PostToolBatch`. Per turn: `UserPromptSubmit`, `UserPromptExpansion`, `Stop`, `StopFailure`. Per session: `SessionStart`, `SessionEnd`, `Setup`. Others include `SubagentStart/Stop`, `TaskCreated/Completed`, `ConfigChange`, `FileChanged`, `InstructionsLoaded`, `PreCompact/PostCompact`, `WorktreeCreate/Remove`. — [Hooks reference](https://code.claude.com/docs/en/hooks)
- [PRIMARY-READ] Five handler types: `command` (shell, JSON on stdin), `http` (POST), `mcp_tool`, `prompt` (single-turn model evaluation), `agent` (subagent with tools; experimental). — [Hooks reference](https://code.claude.com/docs/en/hooks)
- [PRIMARY-READ] Exit-code semantics for command hooks:
  - Exit 0: success; JSON on stdout is applied; stderr is not shown to Claude.
  - Exit 2: blocking error; stderr is the reason fed back; JSON on stdout cannot override the block.
  - Any other code: non-blocking error, the action proceeds. "Exit 1 is not a block; use `exit 2` for policy enforcement."
  — [Hooks reference](https://code.claude.com/docs/en/hooks)
- [PRIMARY-READ] What exit 2 does per event:
  - `PreToolUse`: blocks the tool call.
  - `PostToolUse` / `PostToolUseFailure`: stderr is shown to Claude, but the tool has already run.
  - `Stop` / `SubagentStop`: prevents stopping and continues the conversation.
  - `UserPromptSubmit`: blocks the prompt.
  - `ConfigChange`: blocks the change (except `policy_settings`).
  - `TaskCompleted`: prevents completion.
  - `PostToolBatch`: stops the agentic loop.
  - `SessionStart`: cannot block; stderr goes to the user only.
  — [Hooks reference](https://code.claude.com/docs/en/hooks)
- [PRIMARY-READ] JSON output alternative (exit 0, single JSON object on stdout):
  - `hookSpecificOutput.permissionDecision` = `allow` / `deny` / `ask` / `defer`, plus `permissionDecisionReason`, for `PreToolUse`.
  - Top-level `decision: "block"` + `reason` for `PostToolUse`, `Stop` and others.
  - `additionalContext` is injected as a system reminder.
  - `continue: false` stops Claude entirely.
  - `additionalContext`, `systemMessage` and plain stdout are capped at 10,000 characters each; overflow is saved to a file with a 2,000-character preview.
  — [Hooks reference](https://code.claude.com/docs/en/hooks)
- [PRIMARY-READ] The docs advise writing `additionalContext` "as factual statements, not imperative system commands, to avoid prompt-injection defenses." — [Hooks reference](https://code.claude.com/docs/en/hooks)
- [PRIMARY-READ] Matchers: plain strings or `|`-separated lists match tool names exactly; any other character makes the matcher an unanchored JavaScript regex (`Edit.*` also matches `NotebookEdit`; use `^Edit$`). An `if` field takes one permission-rule expression, e.g. `"if": "Bash(git *)"`, and is "best-effort, so use permissions for hard enforcement." — [Hooks reference](https://code.claude.com/docs/en/hooks)
- [PRIMARY-READ] Timeouts: `command` / `http` / `mcp_tool` default to 600 s (30 s on `UserPromptSubmit`); `prompt` 30 s; `agent` 60 s. — [Hooks reference](https://code.claude.com/docs/en/hooks)
- [PRIMARY-READ] **Fail-open by default.** On `PreToolUse`, a crashed, missing or timed-out command hook lets the call proceed unless `onFailure: "block"` is set (requires v2.1.295+). `onFailure` has no effect on `Stop`, `SubagentStop`, `TaskCompleted`, `TeammateIdle` or async hooks. — [Hooks reference](https://code.claude.com/docs/en/hooks)
- [PRIMARY-READ] Config locations: `~/.claude/settings.json`, `.claude/settings.json` (committed), `.claude/settings.local.json`, managed policy settings, plugin `hooks/hooks.json`, and skill or subagent frontmatter. Entries merge across levels. — [Hooks reference](https://code.claude.com/docs/en/hooks)
- [PRIMARY-READ] Enterprise controls: `allowManagedHooksOnly` blocks user, project, local and plugin hooks; `disableAllHooks` in user or project settings cannot disable managed hooks; `ConfigChange` hooks can block settings changes. — [Hooks reference](https://code.claude.com/docs/en/hooks)
- [PRIMARY-READ] A hook `permissionDecision: "deny"` "blocks the tool even in `bypassPermissions` mode or with `--dangerously-skip-permissions`"; `PreToolUse` hooks fire before any permission-mode check. A hook `allow` cannot override deny rules from settings. — [Hooks guide](https://code.claude.com/docs/en/hooks-guide)
- [PRIMARY-READ] Limits: `PostToolUse` hooks "can't undo actions since the tool has already executed"; `Stop` hooks "fire whenever Claude finishes responding, not only at task completion" and not on user interrupts; command hooks cannot trigger slash commands or tool calls. — [Hooks guide](https://code.claude.com/docs/en/hooks-guide)
- [PRIMARY-READ] Stop-hook loop protection: Claude Code overrides a Stop hook after it blocks eight times in a row with no tool call in between (cap adjustable via `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`). Scripts should check `stop_hook_active` and exit 0 when it is `true`. — [Hooks guide](https://code.claude.com/docs/en/hooks-guide)
- [PRIMARY-READ] Prompt hooks return `{"ok": true|false, "reason": ...}`. On `Stop`, the reason is fed back so Claude keeps working. On `PreToolUse` and `PostToolUse` the turn ends by default unless `continueOnBlock: true` is set. Agent hooks can run up to 50 tool-use turns and are flagged experimental: "For production workflows, prefer command hooks." — [Hooks guide](https://code.claude.com/docs/en/hooks-guide)

Official example, protected-file hook (copied verbatim from the guide):

```bash
#!/bin/bash
# protect-files.sh
INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
FILE_PATH="${FILE_PATH//\\//}"
PROTECTED_PATTERNS=(".env" "package-lock.json" ".git/")
for pattern in "${PROTECTED_PATTERNS[@]}"; do
  if [[ "$FILE_PATH" == *"$pattern"* ]]; then
    echo "Blocked: $FILE_PATH matches protected pattern '$pattern'" >&2
    exit 2
  fi
done
exit 0
```

```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "Edit|Write",
        "hooks": [ { "type": "command",
                     "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/protect-files.sh" } ] }
    ]
  }
}
```
— [Hooks guide](https://code.claude.com/docs/en/hooks-guide)

- [PRIMARY-READ] The guide states the hole in that example: "Claude can still change the protected files with shell commands." It recommends adding `Edit(/.env)`-style deny rules, then adds "Even with those rules, Claude can change `.env` in other ways" and points to sandboxing. — [Hooks guide](https://code.claude.com/docs/en/hooks-guide)
- [PRIMARY-READ] Official format-after-edit example (`PostToolUse`, matcher `Edit|Write`): `"command": "f=$(jq -r '.tool_input.file_path') && npx prettier --write --ignore-unknown \"$f\""`. — [Hooks guide](https://code.claude.com/docs/en/hooks-guide)

#### Cursor

- [SNIPPET] Config lives in `~/.cursor/hooks.json` (user) and `<project>/.cursor/hooks.json` (project), with a top-level `"version": 1` and a `"hooks"` object. Listed events include `beforeShellExecution`, `afterShellExecution`, `beforeReadFile`, `afterFileEdit`, `preToolUse`, `postToolUse`, `postToolUseFailure`, `subagentStart`, `subagentStop`. — [Cursor hooks docs](https://cursor.com/docs/agent/hooks)
- [SECONDARY] A hook answers with a permission of `allow`, `deny` or `ask`, or exits with code 2 to block. — [flaviocopes glossary](https://flaviocopes.com/ai-glossary/hooks/)
- [SECONDARY] Cursor's docs list other exit codes as "Hook failed, action proceeds (fail-open by default)"; a per-script `failClosed` option (default false) makes crashes, timeouts, non-zero exits and empty output block instead. — [startdebugging.net, Oct 2026](https://startdebugging.net/2026/10/fail-closed-vs-fail-open-hooks-for-coding-agent-guardrails/); comparison at [developersdigest](https://www.developersdigest.tech/blog/coding-agent-hooks-fail-open-vs-fail-closed-2026)
- [SECONDARY] User-reported reliability problems:
  - On Windows the stdin JSON carried a UTF-8 BOM that broke `JSON.parse`, so guard hooks silently allowed commands. — [forum thread](https://forum.cursor.com/t/on-windows-cursor-s-hook-stdin-json-payload-includes-a-utf-8-bom-that-breaks-standard-json-parse-causing-security-guards-to-silently-degrade-to-allowing-commands-across-all-agent-channels/166794)
  - "Hooks not firing. Cannot have guardrails." — [forum thread](https://forum.cursor.com/t/hooks-not-firing-cannot-have-guardrails/168407)
  - Fail-closed `beforeShellExecution` hard-locking the agent shell. — [forum thread](https://forum.cursor.com/t/agent-shell-hard-locks-fail-closed-beforeshellexecution-mainthreadshellexec-not-initialized/165215)
- [SNIPPET] Cursor documents loading third-party (Claude Code format) hooks. — [Cursor third-party hooks](https://cursor.com/docs/agent/third-party-hooks)

#### OpenAI Codex CLI

- [SNIPPET] Events: `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PreCompact`, `PostCompact`, `UserPromptSubmit`, `SubagentStop`, `Stop` (turn scope), plus `SessionStart` and `SubagentStart`. Config has three tiers: event, matcher group, handlers. — [Codex hooks docs](https://developers.openai.com/codex/hooks)
- [SECONDARY] Project file is `<repo>/.codex/hooks.json`; from v0.124 hooks can also be defined inline in `config.toml`. A post describes hooks as GA by v0.133 (May 2026). — [migration guide](https://codex.danielvaughan.com/2026/04/24/codex-cli-hooks-migration-guide-hooks-json-to-inline-config-toml/); [post-GA guide](https://codex.danielvaughan.com/2026/05/25/codex-cli-hooks-after-ga-event-model-trust-verification-production-patterns/)
- [SECONDARY, conflicting] An older docs mirror says `PreToolUse` / `PostToolUse` only fired for Bash and that hooks were disabled on Windows. A later post says `apply_patch` interception was added (PR #18391). — [wikidocs mirror](https://wikidocs.net/365662); contradicted by [migration guide](https://codex.danielvaughan.com/2026/04/24/codex-cli-hooks-migration-guide-hooks-json-to-inline-config-toml/)
- [SECONDARY] "PreToolUse is a guardrail, not a full security boundary," because it does not intercept every shell path or every non-shell, non-MCP tool. — [knightli.com, June 2026](https://knightli.com/en/2026/06/11/codex-hooks-advanced-usage/)

#### Gemini CLI

- [SNIPPET] `BeforeTool` exit 2 blocks the tool, with stderr as the reason sent to the agent, and the turn continues. `AfterTool` exit 2 hides the tool result and substitutes stderr. Exit 0 with `{"decision": "deny"}` is the preferred style. `continue: false` kills the whole agent loop. — [Gemini CLI hooks reference](https://geminicli.com/docs/hooks/reference/)
- [SNIPPET] Hooks are configured in `settings.json` and merged across layers: project `.gemini/settings.json`, user `~/.gemini/settings.json`, system `/etc/gemini-cli/settings.json`, with extension hooks lowest. The matcher is compared with the tool name. — [Gemini CLI hooks](https://geminicli.com/docs/hooks/)

#### GitHub Copilot (coding agent, CLI, VS Code)

- [SNIPPET] `preToolUse` returns `permissionDecision` of `allow`, `deny` or `ask`; a reason is required when denying and is shown to the agent. — [Copilot CLI hooks reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-hooks-reference)
- [SNIPPET] VS Code (hooks marked Preview): repository-shared hooks live in `.github/hooks/*.json`; a command `preToolUse` error denies the tool call, but a timeout falls through to normal permissions. — [VS Code agent hooks](https://code.visualstudio.com/docs/copilot/customization/hooks)
- [SECONDARY] The coding agent reads `.github/hooks/hooks.json` from the default branch; only `preToolUse` can block and other events are observational. — [Enkrypt README](https://glama.ai/mcp/servers/@enkryptai/secure-mcp-gateway/blob/ec939917726c2b4bec5a82902185927ccb3cf208/hooks/copilot/README.md)

#### OpenCode

- [SECONDARY] No JSON hook config; guardrails are JS/TS plugins. In `tool.execute.before`, throwing an `Error` blocks the tool and the message is returned to the model; mutating `input.args` does not block. — [OpenCode plugins docs mirror](https://mintlify.com/anomalyco/opencode/plugins); [learnopencode hooks](https://learnopencode.com/en/5-advanced/12c-hooks)
- [SNIPPET] Known gap: `tool.execute.before` blocks primary-agent tool calls but does not intercept calls from subagents spawned via the task tool. — [opencode issue #5894](https://github.com/anomalyco/opencode/issues/5894)

#### Amp

- [SNIPPET] `amp.permissions` is an ordered rule list; the first rule matching the tool and its arguments decides allow / reject / ask / delegate. A delegate program receives tool parameters as JSON on stdin and decides by exit code: 0 allow, 1 ask, 2 reject with stderr forwarded to the model. — [Amp permissions](https://ampcode.com/permissions); [Tool-level permissions](https://ampcode.com/news/tool-level-permissions)
- [SNIPPET] The plugin API exposes `amp.on('tool.call', async (event, ctx) => …)`; plugins live in `.amp/plugins/` or `~/.config/amp/plugins/`, load only with `PLUGINS=all amp`, and are marked experimental. The legacy permissions page recommends moving to plugins for enforcement. — [Amp plugin API](https://ampcode.com/manual/plugin-api); [legacy permissions rules](https://ampcode.com/manual/appendix/legacy-permissions-rules.txt)

### Inferences

Comparison assembled from the findings above (grades as cited there):

| Agent | Blocking pre-tool mechanism | How the reason reaches the model | Default when the hook fails | Post-edit feedback | Status |
|---|---|---|---|---|---|
| Claude Code | `PreToolUse` exit 2, or JSON `permissionDecision: "deny"` | stderr / `permissionDecisionReason` | Fail-open; `onFailure: "block"` opts in | `PostToolUse` exit 2 or `decision: "block"`; `Stop` hook | Stable; agent hooks experimental |
| Cursor | `beforeShellExecution` / `preToolUse` permission `deny`, or exit 2 | Not confirmed (see Gaps) | Fail-open; `failClosed` opts in | `afterFileEdit`, `postToolUse` | Shipped; multiple bug reports |
| Codex CLI | `PreToolUse` allow/deny | JSON on stdout | Not established | `PostToolUse`, `Stop` | Reported GA May 2026 |
| Gemini CLI | `BeforeTool` exit 2 or `decision: "deny"` | stderr | Not established | `AfterTool` exit 2 replaces the result | Documented |
| Copilot | `preToolUse` `permissionDecision: "deny"` | Required reason | Error denies; timeout falls through (VS Code) | Other events observational | VS Code hooks in Preview |
| OpenCode | Plugin throws in `tool.execute.before` | Error message | Not established | `tool.execute.after` | Subagent gap reported |
| Amp | `amp.permissions` delegate exit 2, or `tool.call` plugin | stderr | Not established | Not established | Plugins experimental |

- The stdin-JSON / exit-2 / stderr-as-reason convention is shared by Claude Code, Gemini CLI, Cursor and Amp's delegate, and Cursor documents loading Claude-format hooks. A single guard script (read JSON, test path or command, print the fix, `exit 2`) is therefore largely portable across agents with a thin adapter per agent.
- Layering for latency. Pre-tool hooks give feedback in milliseconds but only see one proposed call. Post-edit hooks (lint the edited file) give feedback in seconds. Stop hooks (typecheck, test, full lint) give feedback at the end of a turn. Because Claude Code's `PostToolUse` cannot undo, it works as a fast feedback channel rather than a gate.
- The most valuable hook for "failure to rule" work is probably a `PostToolUse` hook running the project linter on the edited file and exiting 2 with the lint output. It means every new lint rule automatically becomes in-loop agent feedback without touching hook configuration again.
- Bypass difficulty for hooks is moderate. An agent that can run shell can usually write a file by another route (`sed`, `python -c`, redirects). Claude Code's own docs say so about the protected-file example, and say Bash rules are not a security boundary.

### Gaps
- Cursor's exact response schema could not be verified: whether the model-facing and user-facing reason fields are named `agent_message` / `user_message`, the full current event list, timeout defaults, and enterprise distribution. cursor.com was unreachable and no snippet confirmed the field names.
- Codex CLI's current official position on which tools `PreToolUse` intercepts, Windows support, and exit-code semantics are unverified; the only detail came from one third-party blog series and an older mirror, and they conflict.
- Copilot coding agent (cloud) hook file location and event list come from secondary sources and one disagrees with VS Code's docs about whether hooks run in VS Code chat.
- No documentation was found for an Amp `.agents/checks` feature; the search returned nothing on it.
- Windsurf, Cline, Augment and other agents were not researched.
- I found no vendor-published measurements of hook reliability or of how often agents route around hooks.

---

## 2. Custom rule engines: fastest path from one bad example to a rule

### Takeaway
The cheapest rules to author from a single mistake are declarative ones that need no code: ESLint `no-restricted-syntax` / `no-restricted-imports`, Ruff's banned-API settings, Clippy `disallowed-methods`, and pattern-as-code tools (ast-grep, Semgrep, Biome GritQL). Full custom ESLint rules and compiled linters cost more but handle type-aware or cross-node logic. Ruff still has no plugin system as far as this research could establish, so Python project-specific rules need ast-grep, Semgrep or a flake8/pylint plugin alongside it.

### Cited Findings

#### ESLint
- [SNIPPET] `no-restricted-syntax` entries take an object with an AST `selector` and a `message`; one write-up's example selects `alert()` calls. — [365Talents write-up](https://inside.365talents.com/blog/eslint-restricted-syntax); [kwaimind](https://kwaimind.bearblog.dev/no-restricted-syntax/)
- [SNIPPET] `no-restricted-imports` path entries carry `name`, optional `importNames` and `message`, e.g. `"name": "import-foo", "importNames": ["Bar"], "message": "Please use Bar from /import-bar/baz/ instead."`; `patterns` entries can use `group` plus `message`, e.g. `group: ["lodash/*"], message: "Please use the default import from 'lodash' instead."` — [ESLint no-restricted-imports (archived docs)](https://archive.eslint.org/docs/rules/no-restricted-imports)
- [SNIPPET, conflicting] Older docs snapshots say custom messages cannot be set for restricted patterns; later versions support `group` + `message`. Check the installed ESLint version. — [ESLint docs snapshot](https://github-redirect.dependabot.com/eslint/eslint/blob/ada46a201a8a341d6faedb255db35c97b8be48e8/docs/rules/no-restricted-imports.md); [ESLint PR 18015](https://redirect.github.com/eslint/eslint/pull/18015)
- [SNIPPET] ESLint's own rule source uses a `messages` map with `{{placeholder}}` templating (e.g. `patterns: "'{{importSource}}' import is restricted from being used by a pattern."`), the same `meta.messages` shape a custom rule uses. — [ESLint rule source mirror](https://apache.googlesource.com/nifi-fds/+/6435e394f6c7c31d3f61a4bfc9702bde2a1659fa/node_modules/eslint/lib/rules/no-restricted-imports.js)

Illustrative flat-config sketch (assembled from the option shapes above, not copied from docs):

```js
// eslint.config.js (illustrative)
export default [{
  rules: {
    "no-restricted-syntax": ["error", {
      selector: "CallExpression[callee.object.name='console'][callee.property.name='log']",
      message: "Do not use console.log in src/. Import { logger } from '@/lib/logger' and call logger.info(msg, ctx). See docs/logging.md."
    }],
    "no-restricted-imports": ["error", {
      paths: [{ name: "axios", message: "Use the typed client in '@/lib/http' instead of axios directly." }],
      patterns: [{ group: ["@/features/*/internal/*"], message: "Import a feature only through its index.ts public API." }]
    }]
  }
}];
```

#### Oxlint
- [SNIPPET] JS plugin support reached alpha in March 2026; the Oxc blog says it can run existing ESLint plugins and custom rules, and "we expect 80% of ESLint users can now switch to Oxlint and have it 'just work'." — [Oxc blog 2026-03-11](https://oxc.rs/blog/2026-03-11-oxlint-js-plugins-alpha)
- [SNIPPET] The plugin API is compatible with ESLint v9+; an alternative, faster API also exists and rules written with it remain ESLint-compatible. — [Oxlint JS plugins docs](https://oxc.rs/docs/guide/usage/linter/js-plugins); [Writing JS plugins](https://oxc.rs/docs/guide/usage/linter/writing-js-plugins)
- [SECONDARY] A wrapper package describes the API as "ESLint-compatible but not ESLint-identical" and lists context methods that broke for some plugins. — [oxlint-compat-wrapper](https://www.pkgstats.com/pkg:oxlint-compat-wrapper)

#### Biome (GritQL plugins)
- [SNIPPET] Plugins are `.grit` files listed in a top-level `plugins` array in `biome.json`; a rule matches a pattern and calls `register_diagnostic(...)` with fields such as `message` and `severity`. — [Biome linter plugins](https://biomejs.dev/linter/plugins/); [GritQL plugin recipes](https://biomejs.dev/recipes/gritql-plugins)
- [SNIPPET] GritQL in Biome targets JavaScript/TypeScript, CSS and JSON; JSON support arrived in 2.4.0. The configuration reference pins schema version 2.6.6. — [Biome 2.4.0 changelog](https://biomejs.dev/internals/changelog/version/2-4-0/); [Biome configuration reference](https://biomejs.dev/reference/configuration/)
- [SNIPPET] Biome's GritQL reference still says "We are working on supporting plugins, which can be either pure GritQL plugins or JS/TS plugins that use GritQL to select the code they wish to operate on." — [Biome GritQL reference](https://biomejs.dev/reference/gritql)
- [SECONDARY] Community guides call GritQL support experimental with some features missing, and note no automatic npm resolution of plugins (relative paths required). — [playbooks.com Biome skill](https://playbooks.com/skills/tenequm/claude-plugins/biome)
- [SNIPPET] Biome also ships a built-in `noRestrictedImports` rule. — [Biome noRestrictedImports](https://biomejs.dev/linter/rules/no-restricted-imports)

#### Ruff
- [SNIPPET] Ruff re-implements every rule in Rust as a first-party feature; "plugin" settings such as `[tool.ruff.lint.<plugin>]` only tune bundled rule sets. One search found no evidence of a custom-rule plugin system as of late 2026. — [Ruff configuration docs](https://docs.astral.sh/ruff/configuration)
- [SECONDARY, unconfirmed against the official changelog] Blogs report Ruff 0.16.0 (dated 2026-07-24) greatly expanded the default rule set (one says 413 default rules, up from 59) and added a `ruff: ignore` suppression system. — [pydevtools](https://pydevtools.com/blog/ruff-0-16-0-default-rules/); [developersdigest](https://www.developersdigest.tech/blog/ruff-v0-16-0-zero-config-linting-analysis)
- [SNIPPET] `RUF100` (unused-noqa) flags `noqa` directives that no longer apply and has an autofix. — [Ruff unused-noqa](https://docs.astral.sh/ruff/rules/unused-noqa/)

#### ast-grep
- [SNIPPET] Rules are YAML; one file may hold several rules separated by `---`. Severity is `hint`, `info`, `warning`, `error` or `off`. Matched metavariables can be used inside `message`. The lint-rule guide's sample pairs `pattern: console.log($GREET)` with `fix: console.log('Hello ' + $GREET)`. — [ast-grep lint rule guide](https://ast-grep.github.io/guide/project/lint-rule.html); [YAML cheat sheet](https://ast-grep.github.io/cheatsheet/yaml.html); [severity guide](https://ast-grep.github.io/guide/project/severity.html)

Illustrative rule (field names per the config reference at [ast-grep YAML reference](https://ast-grep.github.io/reference/yaml.html); content mine):

```yaml
# rules/no-raw-requests.yml (illustrative)
id: no-raw-requests
language: python
severity: error
rule:
  pattern: requests.$METHOD($$$ARGS)
message: Call the shared http client instead of requests.$METHOD directly.
note: Use `from app.http import client` then `client.$METHOD(...)`; it adds timeouts and retries.
files: ["src/**/*.py"]
```

#### Semgrep
- [SNIPPET] Rule fields: `message` should say why the pattern matched "and how to remediate it"; severities are LOW / MEDIUM / HIGH / CRITICAL, with legacy INFO / WARNING / ERROR mapping to LOW / MEDIUM / HIGH; `languages` is an array; `fix` gives search-and-replace autofix; `metadata` holds arbitrary data. — [Semgrep rule syntax](https://docs.semgrep.dev/writing-rules/rule-syntax)

#### Go
- [SNIPPET] Ruleguard rules are written in a Go DSL and run inside golangci-lint through go-critic, configured under `linters-settings.gocritic.settings.ruleguard.rules`; ruleguard's pitch is "custom linting rules without re-compilation and Go plugins." — [go-ruleguard](https://github.com/quasilyte/go-ruleguard); [golangci-lint linters](https://golangci-lint.run/usage/linters)
- [SNIPPET] Fully custom analyzers use `go/analysis` and are built into a custom binary with `golangci-lint custom -v`, with modules declared in `.custom-gcl.yml`. — [golangci example plugin linter](https://github.com/golangci/example-plugin-linter)

#### Rust
- [SNIPPET] Clippy `disallowed_methods` only fires for methods listed in `clippy.toml`; entries take a fully qualified `path`, an optional `reason` shown in the warning, and an optional `replacement` offered as a suggestion. — [Clippy disallowed_methods source](https://rust.googlesource.com/rust-clippy/+/refs/heads/beta/clippy_lints/src/disallowed_methods.rs)
- [SECONDARY] A November 2025 post uses this to ban `std::fs` calls, mentions `disallowed-macros`, and notes that clippy.toml rules "won't block your cargo build." — [schneems.com](https://www.schneems.com/2025/11/19/find-accidental-code-usage-with-a-custom-clippytoml/)
- [SNIPPET] TiKV's `clippy.toml` uses `[[disallowed-methods]]` entries whose reasons point at a wrapper function or a RustSec advisory. — [TiKV clippy.toml](https://redirect.github.com/tikv/tikv/blob/master/clippy.toml)

#### Agents writing rules themselves
- [SECONDARY] Summaries of OpenAI's "Harness engineering" post (Ryan Lopopolo, Feb 2026) say its custom linters were themselves generated by coding agents, and that they enforce naming conventions, file-size limits, structured logging and module boundaries. — [ignorance.ai summary](https://www.ignorance.ai/p/the-emerging-harness-engineering); [ingest notes](https://zby.github.io/commonplace/sources/harness-engineering-leveraging-codex-agent-first-world/)
- [SECONDARY] A practitioner guide recommends building rules from real incidents or recurring review comments, keeping a rule-to-incident mapping, and pruning rules with false positives. — [VirtusLab "Bug → Codify → Lint Rule"](https://visdom-maturity-matrix.virtuslab.com/guides/development/architecture-guardrails-bug-codify-lint-rule)
- [SECONDARY] Rule sets aimed at common AI coding mistakes exist as packages, e.g. an Oxlint "anti-slop" set and `eslint-plugin-llm-core` (20 rules). — [Better Stack anti-slop Oxlint guide](https://betterstack.com/community/guides/ai/anti-slop-oxlint/)

### Inferences

Authoring-cost ranking for a single observed mistake. This ordering is my judgement from the mechanisms above, not a measured result:

| Tier | Mechanism | Languages | Why it sits here |
|---|---|---|---|
| 1 (minutes, config only) | ESLint `no-restricted-imports` / `no-restricted-syntax`; Biome `noRestrictedImports`; Clippy `disallowed-methods`; Ruff banned-API settings | JS/TS, Rust, Python | One config entry with a message; no test harness needed; the diff is small enough for quick review |
| 2 (minutes, one pattern file) | ast-grep YAML; Semgrep YAML; Biome GritQL | Multi-language | The pattern is the bad code with holes punched in it, so the bad example almost is the rule; easy for an agent because it can run the rule on the bad and good snippets immediately |
| 3 (tens of minutes) | Custom ESLint/Oxlint rule with tests; ruleguard DSL | JS/TS, Go | Needs AST knowledge and tests; required when the rule needs scope or type information |
| 4 (hours) | `go/analysis` analyzers; dylint; CodeQL queries | Go, Rust, multi | Compiled or query-language tooling; justified only for data-flow or whole-program properties |

- For "can an agent write it reliably", the deciding factor is whether the agent can verify the rule. A cheap protocol follows from the tools' shapes: save the bad snippet and the corrected snippet as fixtures, have the agent write the rule, run it and require a hit on the bad fixture and silence on the good one, then run it over the repo to count pre-existing hits before enabling at `error`.
- Because Ruff lacks plugins, a Python project wanting bespoke rules should expect a second tool (ast-grep or Semgrep) in the same pre-commit and CI step.
- ESLint selectors in `no-restricted-syntax` are the most likely tier-1 item for an agent to get subtly wrong, because the selector must match the parser's AST node names; the fixture check above catches that.

### Gaps
- Unverified recall, not confirmed this session: Ruff's `flake8-tidy-imports` settings (`banned-api` under `[tool.ruff.lint.flake8-tidy-imports]`, rule `TID251`) and `banned-module-level-imports` (`TID253`) as the Ruff equivalent of restricted imports. Check [Ruff rules](https://docs.astral.sh/ruff/rules/).
- Unverified recall: flake8 local plugins and pylint custom checkers as the in-ecosystem route for Python custom rules. Not researched.
- No source was found on dylint, CodeQL custom queries, or standalone Grit; the searches returned nothing usable.
- No source was found on the status of Ruff's long-standing plugin-system request; absence of evidence from one search is not proof none exists in preview.
- The exact Biome `.grit` example text could not be read (biomejs.dev unreachable), so no verbatim GritQL example is included.
- No benchmark comparing how reliably agents author rules in each engine was found.
- I could not read OpenAI's original harness-engineering post; all claims about it are from secondary summaries.

---

## 3. Architecture and structure enforcement

### Takeaway
Import-graph rules are the most mature structural guardrail and exist for both ecosystems: dependency-cruiser, eslint-plugin-boundaries and Nx module boundaries for TS/JS, and import-linter for Python, with ArchUnit-style test libraries (ts-arch, ArchUnitTS, PyTestArch) as an alternative that runs in the test suite. All are declarative enough that a layering violation seen once can become a rule in a few lines.

### Cited Findings

- [SNIPPET] **dependency-cruiser**: config has `forbidden`, `allowed`, `required` and `options` sections. A forbidden rule has `name`, optional `comment`, `severity` (`error` / `warn` / `info`; `warn` is the default) and `from` / `to` matchers whose `path` / `pathNot` are regular expressions. — [dependency-cruiser README (v17.3.10)](https://cdn.jsdelivr.net/npm/dependency-cruiser@17.3.10/README.md); [rules tutorial](https://github.com/ahalf-yuan/dependency-cruiser/blob/develop/doc/rules-tutorial.md)

Documented example shape (from the README/tutorial snippet):

```js
// .dependency-cruiser.js
module.exports = {
  forbidden: [{
    name: "not-to-test",
    comment: "don't allow dependencies from outside the test folder to test",
    severity: "error",
    from: { pathNot: "^test" },
    to: { path: "^test" }
  }]
};
```

- [SNIPPET] dependency-cruiser supports capture-group back-references from `from.path` into `to` (`$1`, `$2`), e.g. forbidding one feature folder from importing another: `from: { path: "(^features/)([^/]+)/" }`, `to: { path: "^$1", pathNot: "$1$2" }`. — [rules tutorial](https://github.com/ahalf-yuan/dependency-cruiser/blob/develop/doc/rules-tutorial.md)
- [SNIPPET] **eslint-plugin-boundaries**: elements are declared under the `boundaries/elements` setting with a type and a file pattern; the plugin now centres on a canonical `boundaries/dependencies` rule, with the older `boundaries/element-types` rule still documented. — [eslint-plugin-boundaries repo](https://github.com/javierbrea/eslint-plugin-boundaries); [element-types rule doc](https://github.com/javierbrea/eslint-plugin-boundaries/blob/HEAD/docs/rules/element-types.md)
- [SNIPPET] **Nx**: `@nx/enforce-module-boundaries` checks TypeScript imports and package.json dependencies during linting; constraints go in `depConstraints`, pairing a `sourceTag` with `onlyDependOnLibsWithTags`. A language-agnostic Conformance plugin rule checks boundaries in the Nx graph via `nx conformance:check`. — [Nx enforce module boundaries](https://nx.dev/docs/features/enforce-module-boundaries)
- [SNIPPET] **import-linter (Python)**: contract types include `forbidden` (one set of modules must not import another), `layers`, and `independence` ("no imports in any direction between the modules, even indirectly"). Config goes in `.importlinter` (INI) or `pyproject.toml` as `[[tool.importlinter.contracts]]` under `[tool.importlinter]` with `root_package`. — [import-linter contract types](https://import-linter.readthedocs.io/en/v2.0/contract_types.html); [forbidden contract](https://import-linter.readthedocs.io/en/latest/contract_types/forbidden/)

Illustrative `pyproject.toml` (keys per the snippets above; module names mine):

```toml
[tool.importlinter]
root_package = "app"

[[tool.importlinter.contracts]]
name = "Domain must not import infrastructure"
type = "forbidden"
source_modules = ["app.domain"]
forbidden_modules = ["app.infrastructure", "sqlalchemy"]
```

- [SNIPPET] **ts-arch**: fluent chain `filesOfProject()` → file filter → `.shouldNot().dependOnFiles()` → target filter, asserted in Jest with `await expect(rule).toPassAsync()`; paths are relative to a tsconfig. A separate library, ArchUnitTS, uses a similar `projectFiles()` chain. — [ts-arch docs](https://ts-arch.github.io/ts-arch); [ArchUnitTS](https://github.com/LukasNiessen/ArchUnitTS)
- [SECONDARY] An Angular Architects article frames tsarch specifically as an architecture guardrail for AI coding agents. — [angulararchitects.io](https://www.angulararchitects.io/en/?p=34037)
- [SNIPPET] **PyTestArch**: rules follow subject / verb / import-type / object, e.g. `Rule().modules_that().are_named(...).should_not().import_modules_that().are_named(...)`, checked with `.assert_applies(architecture)`. `pytest-archon` is a similar alternative. — [PyTestArch module import checks](https://zyskarch.github.io/pytestarch/latest/features/module_import_checks); [pytest-archon](https://github.com/jwbargsten/pytest-archon/blob/main/README.md)
- [SECONDARY] **knip**: reports unused files, exports and dependencies plus unlisted dependencies; `--max-issues N` fails the build above N; `knip --fix` removes the `export` keyword from unused exports, and deleting files needs `--allow-remove-files`. — [knip repo](https://github.com/webpro/knip); [knip guide, May 2026](https://recca0120.github.io/en/2026/05/02/knip-dead-code-detector/)
- [SECONDARY] Summaries of OpenAI's harness-engineering post say the team enforced a layered domain architecture with custom linters plus structural tests, and ran recurring "garbage collection" agents that scan for drift and open refactoring PRs. — [ignorance.ai summary](https://www.ignorance.ai/p/the-emerging-harness-engineering)

### Inferences
- Lint-integrated boundary tools (eslint-plugin-boundaries, Nx, `no-restricted-imports`) report per file and so fit a post-edit hook. Graph tools (dependency-cruiser, import-linter) and test-library tools need the whole project and fit a Stop hook, pre-commit or CI.
- import-linter's `independence` and `layers` contracts catch indirect imports, which per-file import bans cannot.
- For a single observed violation ("feature A imported feature B's internals"), the dependency-cruiser back-reference pattern and an import-linter `forbidden` contract are each a handful of lines, putting them in the same cost tier as restricted-import lint rules.
- Dead-code tools are a countermeasure to a characteristic agent failure (leaving orphaned files and exports after a refactor), but `knip --fix` in a hook would let the tool edit code unprompted, so reporting with `--max-issues` is the safer gate.

### Gaps
- No source was retrieved for jscpd (duplication thresholds) or for complexity/size limits (ESLint `complexity`, `max-lines`, Ruff `C901`); I recall these exist but did not verify them this session.
- No source was retrieved for ArchUnit (Java) itself or its `FreezingArchRule` baseline feature; unverified recall is that ArchUnit supports freezing existing violations so only new ones fail.
- The full `boundaries/dependencies` option schema was not retrieved.
- dependency-cruiser's rules reference page itself was not read; details come from README/tutorial snippets and one fork's tutorial copy.

---

## 4. Making the mistake unrepresentable: type system, API design, and tests as guardrails

### Takeaway
Evidence gathered this session for this question is thin: I confirmed the typescript-eslint strictness presets and Clippy's ban lists, but most type-level techniques (branded types, exhaustive `never` checks, strict compiler flags) and test-level techniques (mutation, property, golden tests) could not be sourced because the relevant doc sites were unreachable. Treat this section as a list of pointers to verify, not as established findings.

### Cited Findings
- [SNIPPET] typescript-eslint's `strict-type-checked` config contains everything in `recommended` and `recommended-type-checked`, with some rules set stricter; it is one of the configs not considered "stable" under semver (rules can change in minor versions). The docs suggest it when most developers on the project are comfortable with TypeScript. — [typescript-eslint shared configs](https://typescript-eslint.io/users/configs)
- [SECONDARY] One developer reported `strict-type-checked` did not catch missing returns that `"strict": true` in `tsconfig.json` does, i.e. lint presets and compiler flags are complementary. — [dev.to "TypeScript strictly typed"](https://dev.to/cyrilletuzi/typescript-strictly-typed-part-1-configuring-a-project-9ca)
- [SNIPPET] Clippy ban lists (`disallowed-methods`, with `reason` and `replacement`) make a wrapper API the only lint-clean route to a capability. — [Clippy disallowed_methods source](https://rust.googlesource.com/rust-clippy/+/refs/heads/beta/clippy_lints/src/disallowed_methods.rs)
- [SECONDARY] Summaries of OpenAI's harness-engineering post pair custom linters with "structural tests" that validate compliance and prevent layer violations before merge. — [agentpatterns.ai summary](https://agentpatterns.ai/patterns/agent-design/harness-engineering/)
- [SECONDARY] A course on self-testing agents treats lint and types jointly as guardrails. — [Steve Kinney, "Lint and types as guardrails"](https://stevekinney.com/courses/self-testing-ai-agents/lint-and-types-as-guardrails)

### Inferences
- The general ordering "type error > lint error > test failure > review comment" for cost of feedback follows from where each runs (compiler on every edit versus suite at the end), but I found no source that measures it for agents.
- A wrapper API plus a ban on the raw API (restricted import or `disallowed-methods`) is the cheapest form of "unrepresentable": the ban is tier-1 config, and the message names the wrapper.
- Architecture test libraries from section 3 are the concrete form of "structural tests" for TS and Python.

### Gaps
- Unverified recall, none confirmed this session:
  - typescript-eslint rules `no-explicit-any`, `switch-exhaustiveness-check` (type-aware), `ban-ts-comment` with an `allow-with-description` option, and `no-restricted-types`. Check [typescript-eslint rules](https://typescript-eslint.io/rules/).
  - TypeScript compiler flags such as `strict`, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`, `noImplicitOverride`.
  - Branded/nominal types and `never`-based exhaustiveness helpers as idioms.
  - Python equivalents: `mypy --strict` / pyright strict, `typing.NewType`, `assert_never`, Ruff's `ANN401` for `Any`.
- No sources were retrieved on mutation testing (Stryker, mutmut, cargo-mutants), property-based testing (fast-check, Hypothesis), or snapshot/golden tests as agent guardrails.
- No evidence was found, either way, on whether stricter type settings reduce agent error rates.

---

## 5. Anti-bypass measures

### Takeaway
Agents bypass guardrails in four documented or reported ways: adding inline suppressions, editing the lint or tool config, writing files through the shell to dodge edit hooks, and exploiting fail-open hooks. Each has a specific countermeasure (suppression-policing rules, protected-path hooks plus deny rules, sandboxing, fail-closed options), but no local layer is airtight, so the check that cannot be skipped has to run in CI.

### Cited Findings

#### Policing inline suppressions
- [SNIPPET] ESLint `linterOptions`:
  - `noInlineConfig: true` makes ESLint ignore all inline configuration comments (CLI: `--no-inline-config`).
  - `reportUnusedDisableDirectives` accepts a severity (`"error"`, `"warn"`, `"off"`) and flags `eslint-disable` comments that suppress nothing.
  - `reportUnusedInlineConfigs` flags inline config that merely matches the existing configuration.
  — [ESLint configuration files](https://eslint.org/docs/head/use/configure/configuration-files); [ESLint v8.56.0 release notes](https://hi.eslint.org/blog/2023/12/eslint-v8.56.0-released/)
- [SNIPPET] `@eslint-community/eslint-plugin-eslint-comments`:
  - `require-description` requires a reason after `--` (e.g. `/* eslint-disable no-console -- needed for debugging */`) and is not in the recommended set.
  - `no-unlimited-disable` (recommended) rejects directives that name no rule.
  - `no-restricted-disable` forbids disabling specific rules by glob, with negation, e.g. `"*"` with `"!no-console"`.
  — [eslint-comments no-restricted-disable](https://eslint-community.github.io/eslint-plugin-eslint-comments/rules/no-restricted-disable.html); [package overview](https://socket.dev/npm/package/@eslint-community/eslint-plugin-eslint-comments)
- [SNIPPET] An issue reports `no-restricted-disable` not working together with `require-description` (#78, opened June 2023); resolution unknown. — [eslint-plugin-eslint-comments issues](https://fr.github.com/mysticatea/eslint-plugin-eslint-comments/issues)
- [SNIPPET] ast-grep: `ast-grep-ignore: <rule-id>` suppresses a specific rule and a bare `ast-grep-ignore` suppresses everything on the line. A built-in `unused-suppression` rule reports stale directives. One guide recommends `ast-grep scan --error=no-suppress-all --error=unused-suppression` once the baseline is clean, noting `no-suppress-all` exists only in newer versions. — [ast-grep severity guide](https://ast-grep.github.io/guide/project/severity.html); [pi-lens ast-grep skill](https://unpkg.com/pi-lens@4.4.0/skills/pi-lens-write-ast-grep-rule/SKILL.md)
- [SNIPPET] Ruff `RUF100` flags unused `noqa`; a GitHub issue notes `PGH004` (blanket-noqa) does not check file-level pragmas. — [Ruff unused-noqa](https://docs.astral.sh/ruff/rules/unused-noqa/)
- [SECONDARY] Semgrep inline suppression is `# nosemgrep: <rule-id>` (seen in a real config; official suppression docs not retrieved). — [example .semgrep.yml](https://git.cleverthis.com/cleveragents/cleveragents-core/src/commit/747513bc591edeab1d4971141c605bd6dd024b0e/.semgrep.yml)

Illustrative ESLint hardening (assembled from the options above):

```js
// eslint.config.js (illustrative)
import comments from "@eslint-community/eslint-plugin-eslint-comments/configs";
export default [
  comments.recommended,
  {
    linterOptions: { reportUnusedDisableDirectives: "error", reportUnusedInlineConfigs: "error" },
    rules: {
      "@eslint-community/eslint-comments/require-description": "error",
      "@eslint-community/eslint-comments/no-restricted-disable": ["error", "no-restricted-imports", "no-restricted-syntax"]
    }
  }
];
```

#### Baselines and ratchets
- [SNIPPET] ESLint bulk suppressions (announced April 2025):
  - `--suppress-all` suppresses existing violations of all rules set to `"error"`.
  - `--suppress-rule` limits suppression to named rules.
  - Suppressions are stored in `eslint-suppressions.json` at the project root, to be committed.
  - ESLint warns about suppressions that no longer match, and `--prune-suppressions` removes them.
  - Running with `--fix` first is recommended.
  — [ESLint suppressions docs](https://eslint.org/docs/head/use/suppressions); [ESLint blog](https://eslint.org/blog/2025/04/introducing-bulk-suppressions/)
- [SNIPPET] A separate tool, `@rushstack/eslint-bulk`, uses `eslint-bulk suppress --all` / `eslint-bulk prune` and a `.eslint-bulk-suppressions.json` file. — [@rushstack/eslint-bulk](https://www.npmjs.com/package/@rushstack/eslint-bulk)
- [SECONDARY] knip's `--max-issues N` serves as a numeric ratchet for dead code. — [knip guide](https://recca0120.github.io/en/2026/05/02/knip-dead-code-detector/)

#### Protecting config and guardrail files (Claude Code)
- [PRIMARY-READ] "Permission rules are enforced by Claude Code, not by the model. Instructions in your prompt or `CLAUDE.md` shape what Claude tries to do, but they don't change what Claude Code allows." — [Permissions](https://code.claude.com/docs/en/permissions)
- [PRIMARY-READ] Rules are evaluated deny, then ask, then allow; an allow rule cannot carve an exception out of a deny rule. `Edit` rules "apply to all built-in tools that edit files." Path patterns are gitignore-style: `Edit(/src/**)` anchors at the project root and `Edit(**/src/**)` matches at any depth. — [Permissions](https://code.claude.com/docs/en/permissions)
- [PRIMARY-READ] Shell redirects are checked against `Edit` rules: for `> file`, `>> file`, `2> file`, "the check covers your `Edit` allow and deny rules"; `tee` targets are checked too (v2.1.269+). — [Permissions](https://code.claude.com/docs/en/permissions)
- [PRIMARY-READ] Bash rules match the command text as Claude writes it and are not "a security boundary around the program"; a deny rule "doesn't match the same program by path or inside `sh -c`." For enforcement independent of command text the docs point to sandboxing, and to a `PreToolUse` hook for inspecting the full command. — [Permissions](https://code.claude.com/docs/en/permissions)
- [PRIMARY-READ] In `bypassPermissions` mode Claude Code skips prompts including for writes to protected paths such as `.git` and `.claude`; `permissions.disableBypassPermissionsMode: "disable"` prevents that mode and is most useful in managed settings. Hook denies still apply in that mode. — [Permissions](https://code.claude.com/docs/en/permissions); [Hooks guide](https://code.claude.com/docs/en/hooks-guide)
- [PRIMARY-READ] `ConfigChange` hooks can block changes to user, project and local settings and skills (not `policy_settings`), and `allowManagedHooksOnly` restricts hooks to managed ones. — [Hooks reference](https://code.claude.com/docs/en/hooks)
- [SECONDARY] A practitioner playbook reports that Claude "will sometimes modify .ruff.toml, biome.json, or tsconfig.json to disable rules rather than fix violations" and recommends a `PreToolUse` hook on Write/Edit protecting those files, plus a Stop-time audit hook for config changes. — [Proxify hooks playbook](https://agentic.proxify.io/setup/hooks-playbook)

Illustrative Claude Code settings combining the documented pieces (rule syntax per the permissions page; file list mine):

```json
{
  "permissions": {
    "deny": [
      "Edit(/eslint.config.js)", "Edit(/eslint-suppressions.json)", "Edit(/biome.json)",
      "Edit(/tsconfig.json)", "Edit(/pyproject.toml)", "Edit(/.importlinter)",
      "Edit(/.claude/**)", "Edit(/.github/**)", "Edit(/.husky/**)",
      "Bash(git commit --no-verify *)", "Bash(git push --no-verify *)"
    ]
  }
}
```

The Bash entries stop the usual phrasing only; the docs say such rules are not a boundary.

#### Fail-open hooks
- [PRIMARY-READ] Claude Code `PreToolUse` command hooks fail open unless `onFailure: "block"` is set. — [Hooks reference](https://code.claude.com/docs/en/hooks)
- [SECONDARY] Cursor hooks fail open unless `failClosed` is set, and a Windows BOM bug caused guard hooks to "silently degrade to allowing commands." — [startdebugging.net](https://startdebugging.net/2026/10/fail-closed-vs-fail-open-hooks-for-coding-agent-guardrails/); [Cursor forum](https://forum.cursor.com/t/on-windows-cursor-s-hook-stdin-json-payload-includes-a-utf-8-bom-that-breaks-standard-json-parse-causing-security-guards-to-silently-degrade-to-allowing-commands-across-all-agent-channels/166794)
- [SNIPPET] VS Code Copilot: a `preToolUse` timeout falls through to normal permissions even for policy hooks. — [VS Code agent hooks](https://code.visualstudio.com/docs/copilot/customization/hooks)

### Inferences
- Bypass resistance by layer, from weakest to strongest, as implied by the findings: instructions in CLAUDE.md / AGENTS.md (advisory) < agent hooks and permission rules (deterministic but local, path-specific and often fail-open) < sandboxed filesystem < local git hooks (skippable with `--no-verify`) < CI required checks with branch protection (outside the agent's reach unless it can edit workflow files).
- The suppression-policing rules turn "the agent silenced the linter" into a new lint error: `no-restricted-disable` on the guardrail rules, `require-description`, and unused-directive reporting at `error`. Combined with a deny rule on the lint config, the remaining route is the shell, which is why CI re-running the same lint is necessary.
- Bulk-suppression files are themselves a bypass surface. An agent that runs `eslint --suppress-all` makes every violation disappear. The file should be on the protected list, and CI should fail if it grows.
- A rule that is denied with no stated alternative invites workarounds; the hook's stderr should name the permitted route. This links to section 6.

### Gaps
- No source retrieved on GitHub CODEOWNERS, branch protection or rulesets as protection for lint config and workflow files; unverified recall is that CODEOWNERS plus required reviews can force human review of changes to those paths.
- No source retrieved on betterer; I could not confirm its current maintenance status or API.
- No source retrieved on pre-commit framework specifics or on detecting `--no-verify` server-side.
- No source retrieved on test-weakening detection (deleted assertions, `.skip`, lowered coverage thresholds) beyond the general advice to protect files.
- Unverified recall: Ruff `PGH003` (blanket type-ignore), `PGH004` (blanket noqa); typescript-eslint `ban-ts-comment` requiring descriptions; golangci-lint `nolintlint` requiring explanation and specific linter names. None confirmed this session.
- Semgrep's official suppression docs and any flag to ignore `nosemgrep` comments were not retrieved.

---

## 6. Rule message design

### Takeaway
The consistent practitioner advice, and the reported OpenAI practice, is to treat the lint message as a prompt: state what is wrong, why, and the exact replacement, and make the rule an error rather than a warning. This is anecdotal: I found no measured evidence that remediation-style messages improve agent outcomes.

### Cited Findings
- [SECONDARY] Summaries of OpenAI's harness-engineering post say custom linter messages include remediation instructions that feed directly into the agent's context, described by one summariser as "a positive form of prompt injection," and by another as "actionable remediation, not violation flags." — [ignorance.ai](https://www.ignorance.ai/p/the-emerging-harness-engineering); [agentpatterns.ai](https://agentpatterns.ai/patterns/agent-design/harness-engineering/)
- [SECONDARY] "Custom lint rules are one of the most effective harness engineering tools. Unlike documentation that agents might misinterpret, linters provide immediate, unambiguous feedback with actionable remediation instructions." The same README contrasts a vague "invalid import" with a fuller message, suggests linking docs, running the custom lint in pre-commit and CI with verbose output, and telling agents in AGENTS.md to read the full error text. — [harness-copilot linters README](https://cdn.jsdelivr.net/npm/harness-copilot@0.1.2/templates/linters/README.md)
- [SECONDARY] Posts describe error messages as "teaching prompts" with examples, and advise writing lint messages like prompts because they function as prompts. — [understandingdata.com](https://understandingdata.com/posts/custom-eslint-rules-determinism/); [dev.to "The best agent prompt is a lint error"](https://dev.to/romanonthego/the-best-agent-prompt-is-a-lint-error-3kim)
- [SECONDARY] Errors work better than warnings for agents: "a warning is a suggestion the agent might or might not act on," whereas an error acts as a gate. — [Mechanical constraints for agents](https://contexthorizon.substack.com/p/mechanical-constraints-for-agents)
- [SNIPPET] Tool support for remediation text:
  - Semgrep's `message` is defined as including why the pattern matched "and how to remediate it." — [Semgrep rule syntax](https://docs.semgrep.dev/writing-rules/rule-syntax)
  - ast-grep messages can interpolate matched metavariables. — [ast-grep cheat sheet](https://ast-grep.github.io/cheatsheet/yaml.html)
  - Clippy `disallowed-methods` entries accept `reason` and `replacement`. — [Clippy source](https://rust.googlesource.com/rust-clippy/+/refs/heads/beta/clippy_lints/src/disallowed_methods.rs)
  - GitHub Copilot hooks require a reason on deny, shown to the agent. — [Copilot CLI hooks reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-hooks-reference)
- [PRIMARY-READ] Claude Code constraints that shape message design:
  - On exit 2, stderr is the text the model sees.
  - `additionalContext` and stdout are capped at 10,000 characters with a 2,000-character preview on overflow.
  - `additionalContext` should be phrased as factual statements, not imperative system commands.
  - On blocked edits, "Claude receives feedback explaining why the edit was blocked, so it can adjust its approach."
  — [Hooks reference](https://code.claude.com/docs/en/hooks); [Hooks guide](https://code.claude.com/docs/en/hooks-guide)

### Inferences
- A message template consistent with the advice above: (1) what was matched, using the captured identifier; (2) the one-line reason; (3) the exact replacement, as an import path or call; (4) a pointer to a doc or exemplar file; (5) what not to do, e.g. "do not add a disable comment; if this is a false positive, stop and report it."
- Because hook output is truncated at 10,000 characters, a hook that runs a linter should limit output to the edited file and the first N findings, or the remediation text may be cut off.
- Messages that name the wrapper or alternative reduce the incentive to bypass, which complements the suppression rules in section 5.
- Offering an autofix (`fix` in ast-grep and Semgrep, `replacement` in Clippy, ESLint fixers) converts a guardrail into a correction and removes a model round trip, at the cost of the model not seeing the correction unless the hook reports it.

### Gaps
- No controlled or quantitative evidence was found that remediation-style messages improve agent fix rates; every source is practitioner opinion or a secondary summary.
- OpenAI's original post was not read; the wording attributed to it comes from summaries and may not match the original.
- No guidance was found from ESLint, Biome, Ruff or Anthropic specifically on writing lint messages for LLM consumers.
- No source was found on message length trade-offs (context cost of long messages repeated across many violations).
