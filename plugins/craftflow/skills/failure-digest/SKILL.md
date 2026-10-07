---
name: failure-digest
description: |
  Read-only cross-workflow failure digest — ranks recurring failure signatures
  (from every workflow's event log, not just the current one) by occurrence
  count, so recurring pain points can become roadmap items instead of staying
  buried in per-workflow memory. Never writes anything; safe to call at any point.

  Use this skill when: what keeps failing, what's breaking most, failure
  digest, recurring failures, what should we fix next, roadmap from failures,
  what's the most common blocker, top gotchas, failure taxonomy.

  Triggers: failure digest, what keeps failing, recurring failures, top
  blockers, what breaks most, failure roadmap, craftflow failure report.

  NOTE: This skill does NOT go through craftflow-router. It is an inspection
  tool — read-only, same exception as the `status` skill.
allowed-tools: Read, Bash, Glob
---

# craftflow Failure Digest

Non-mutating cross-workflow failure report. Reads `.craftflow/state/workflows/*.events.jsonl`
as-is via the existing failure-pattern miner — does not scan or infer anything new.

**Does NOT go through craftflow-router.** This is an inspection tool, not a
development task — the same explicitly allowed exception as `craftflow:status`.

This complements `learn-distiller` (which quietly folds occurrences≥2 clusters
into `patterns.md ## Common Gotchas` for future agents to avoid) by surfacing
the same underlying data as a human-facing report: what keeps failing across
every workflow, ranked, so a recurring cluster can be read as "build this" or
"harden this" rather than just "avoid this."

---

## Step 1 — Resolve Script Path

The first rule that yields an existing file wins.

**(a) Skill-relative (both hosts).** `SKILL_FILE` is this skill's own SKILL.md: in Claude Code, `<Base directory for this skill>/SKILL.md`; in Cursor (context contains `CRAFTFLOW_PLATFORM: cursor`), `~/.cursor/skills/failure-digest/SKILL.md`. Run:

```bash
python3 -c "import pathlib,sys; p=pathlib.Path(sys.argv[1]).expanduser().resolve().parents[2]/'scripts'/'craftflow_learn_scan.py'; print(p if p.is_file() else '')" "<SKILL_FILE>"
```

A non-empty output is `SCRIPT`. `resolve()` follows the Cursor symlink into the plugin checkout, and in Claude Code it points at the same plugin copy the skill was loaded from.

**Cursor workspace copy.** In Cursor only, if (a) printed nothing, take `WS_ROOT` from `git rev-parse --show-toplevel` (the current directory if that fails; your shell may be in a subdirectory) and run the same command once more with `SKILL_FILE` = `<WS_ROOT>/tools/craftflow-plugin/plugins/craftflow/skills/failure-digest/SKILL.md` (a Conductor-provisioned workspace carries this copy and the scripts it needs). A non-empty output is `SCRIPT`. When `SCRIPT` came from this workspace copy, substitute the printed absolute path for `<WS_ROOT>` and pass `--state-dir "<WS_ROOT>/.craftflow/state"` instead of `--state-dir .craftflow/state` in every command below (a wrong directory would silently print an empty list).

**(b) Claude Code only, and only if (a) printed nothing.** Read `~/.claude/plugins/installed_plugins.json`, take `craftflow@craftflow` → `installPath`, then run:

```bash
test -f "<installPath>/scripts/craftflow_learn_scan.py"
```

Use it only if that exits 0. Never use an `installPath` script without that check (an `installPath` without the script is unusable).

**(c) Otherwise** print exactly the following and stop. Never fall back to reading `.craftflow/state` by hand.

```
craftflow_learn_scan.py not found next to this skill. craftflow:failure-digest needs the whole craftflow plugin (skill + scripts/). Claude Code: update the craftflow plugin. Cursor: run install-cursor.sh from a local craftflow checkout (npx skills add copies only the skill folder and is not supported for failure-digest).
```

## Step 2 — Run the Miner

```bash
python3 "$SCRIPT" --state-dir .craftflow/state
```

Output is a JSON array of clusters: `signature`, `occurrences`, `example_reasons`,
`first_seen`, `last_seen`, `event_types`. If the array is empty, report "No
failure history recorded yet" and stop — do not fabricate findings.

## Step 3 — Rank and Present

- Sort clusters by `occurrences` descending.
- Report only clusters with `occurrences >= 2` as the digest — a single
  occurrence is noise, not a pattern (same threshold `learn-distiller` uses).
- For each, show: signature, occurrence count, first/last seen, and one
  `example_reasons` entry as concrete evidence.
- Do not editorialize beyond the data — if a cluster's cause isn't obvious
  from the signature/examples, say "cause unclear from signature" rather than
  guessing.

## Step 4 — Boundaries

- **Do NOT call craftflow-router. Do NOT create tasks. Do NOT modify files.**
- This is a reporting lens on data `learn-distiller` and the router's event
  logging already produce — it adds no new failure-tracking mechanism and
  changes no agent behavior.
- If the user wants to act on a finding (fix a recurring failure, add a
  guard), that is a normal development task — route it through
  `craftflow:craftflow-router` as usual (in Cursor: ask for it as a normal request; the Cursor router handles it); this skill's job ends at reporting.
