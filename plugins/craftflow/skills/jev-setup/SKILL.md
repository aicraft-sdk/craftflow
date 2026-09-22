---
name: jev-setup
description: "Use when the user asks to 'enable jev', 'set up typesafe', 'turn on the jev routing hint', 'disable jev', or 'check jev status' for the craftflow plugin."
allowed-tools: Read Bash
---

## Mission

Walks the user through the optional, off-by-default Jev (TypeSafe AI) routing + skill hint
feature: check the current state, run a canary call, confirm the privacy note, and flip
`config/jev.json`'s `enabled` flag. This skill never edits `config/jev.json` itself — it only
runs `scripts/craftflow_jev_setup.py`, which owns every read/write of that file.

## Step 0 — Resolve `PLUGIN_ROOT`

Use `${CLAUDE_PLUGIN_ROOT}` if set; otherwise resolve the repo path to
`tools/craftflow-plugin/plugins/craftflow`. All commands below run as:

```bash
python3 "$PLUGIN_ROOT/scripts/craftflow_jev_setup.py" <flag>
```

## Step 1 — Check current status

```bash
python3 "$PLUGIN_ROOT/scripts/craftflow_jev_setup.py" --status
```

Show the output (enabled state, model, feature modes, thresholds, and whether a key is
present — the key value itself is never printed).

## Step 2 — No key? Show the setup path and stop

If `--status` reports `key: absent`, show this setup path and stop here — do not proceed to
Step 3 until the user has a key exported:

1. Join the waitlist at https://typesafe.ai (reported same-day approval).
2. Get an API key at https://console.typesafe.ai/keys.
3. Export it in your shell: `export TYPESAFE_API_KEY=...`

Re-run Step 1 once the key is exported.

## Step 3 — Run the canary check

```bash
python3 "$PLUGIN_ROOT/scripts/craftflow_jev_setup.py" --check
```

This makes one canary call and prints the `PRIVACY:` note. Show the full output to the user —
it never writes `config/jev.json`. Then **ask the user to confirm the privacy note before running --enable**. Do not run Step 4 without that confirmation.

## Step 4 — Enable

Once confirmed:

```bash
python3 "$PLUGIN_ROOT/scripts/craftflow_jev_setup.py" --enable
```

This re-runs the canary check and, only on success, flips `enabled:true` in `config/jev.json`
(preserving every other key and the file's indentation). If the check fails, the config is
left untouched and the user should re-check their key.

## Step 5 — What happens next

- Both `routingHint` and `skillHint` start in `audit` mode: the hook makes one batched Jev
  call per user prompt, logs a telemetry row to `.craftflow/state/jev/events.jsonl`, and
  injects nothing into the router's context. Nothing changes for the user yet.
- Run `python3 "$PLUGIN_ROOT/scripts/craftflow_jev_report.py"` to see the agreement rate
  between Jev and the keyword-table router, per feature, plus a `PROMOTE`/`HOLD` verdict
  (`n >= 100` rows and the agreement threshold for that feature).
- `advise` mode (where the hint is actually injected via `additionalContext`) is never
  auto-flipped by this skill or by `craftflow_jev_report.py` — moving a feature from
  `audit` to `advise` in `config/jev.json` after a `PROMOTE` verdict is a manual, human
  decision.
- To turn the feature back off: `python3 "$PLUGIN_ROOT/scripts/craftflow_jev_setup.py" --disable`.

**Note:** a plugin update resets `config/jev.json` to its defaults (`enabled:false`). If Jev
was previously enabled, re-run this skill (Steps 3-4) after updating.
