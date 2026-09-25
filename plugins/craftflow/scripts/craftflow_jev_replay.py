#!/usr/bin/env python3
"""Isolated replay driver -- replays each corpus prompt through the real
craftflow_jev_prompt_hint.py hook, in one temp plugin/project root reused
for the whole batch, appending telemetry to an isolated events.jsonl and a
ground-truth replay_manifest.jsonl -- never touching the live shipped
config/jev.json or the live .craftflow/state/jev/events.jsonl.

All path resolution below is built purely from local `tempfile`-scoped
Path variables -- this module never reads CLAUDE_PROJECT_DIR (or any other
project-identity signal) from this process's own os.environ. The only use
of os.environ is inside run_hook()'s subprocess env merge, where the
caller-supplied env dict always overrides it for CLAUDE_PROJECT_DIR /
CLAUDE_PLUGIN_ROOT / TYPESAFE_API_KEY.

Run: python3 scripts/craftflow_jev_replay.py --corpus <path> \
    --events-out <path> --manifest-out <path>
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "scripts"


def run_hook(payload: dict, env: dict) -> Tuple[int, str, str]:
    """Same subprocess technique as tests/live/jev_audit_roundtrip.py's
    run_hook() -- copied, not imported, per the plan's "do not import the
    monolith" rule. `env`'s keys always win over this process's own
    os.environ in the merge below, so a caller-supplied CLAUDE_PROJECT_DIR
    here fully overrides whatever this process's own os.environ happens to
    hold. `timeout=15` matches that driver's rationale (comfortably above
    the client's 4s call budget plus process startup)."""
    merged_env = {**os.environ, **env}
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "craftflow_jev_prompt_hint.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=merged_env,
        timeout=15,
    )
    return result.returncode, result.stdout.strip(), result.stderr


def _read_new_lines(path: Path, start_line: int) -> List[str]:
    """Read raw non-empty lines from `path` starting at index `start_line`
    (0-based). Never raises -- a missing file returns []."""
    if not path.exists():
        return []
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return lines[start_line:]


def replay_corpus(
    corpus_rows: List[Dict[str, Any]],
    *,
    real_plugin_root: Path,
    events_out: Path,
    manifest_out: Path,
    api_key: str,
) -> Dict[str, int]:
    """Impure driver: replays every corpus row through run_hook() inside one
    isolated temp plugin/project root, built once and reused for the whole
    batch (matching jev_audit_roundtrip.py's tempdir technique). Appends
    telemetry rows to `events_out` and one ground-truth row per successful
    corpus row to `manifest_out`, keyed by the shared call_id read back out
    of that row's own just-appended telemetry pair.

    Fail-open per corpus row (matches craftflow_jev_client.call()'s own
    fail-open contract): a non-zero hook exit, an unexpected row count, or
    any exception for one row is logged as a warning to stderr and the
    batch continues -- never raises, never crashes the whole run.

    `api_key` is read once by the caller and passed here as a plain local
    value; it is placed only into the per-call subprocess env dict, never
    logged or printed.
    """
    events_out = Path(events_out)
    manifest_out = Path(manifest_out)
    events_out.parent.mkdir(parents=True, exist_ok=True)
    manifest_out.parent.mkdir(parents=True, exist_ok=True)

    n_events = 0
    n_manifest = 0
    n_failed = 0

    with tempfile.TemporaryDirectory() as tmp_name:
        root = Path(tmp_name)
        project = root / "project"
        plugin = root / "plugin"
        project.mkdir(parents=True)
        (plugin / "config").mkdir(parents=True)
        (plugin / "skills").mkdir(parents=True)

        cfg = json.loads((Path(real_plugin_root) / "config" / "jev.json").read_text(encoding="utf-8"))
        cfg["enabled"] = True
        cfg["features"] = {"routingHint": "audit", "skillHint": "audit"}
        (plugin / "config" / "jev.json").write_text(json.dumps(cfg), encoding="utf-8")

        internal_events_path = project / ".craftflow" / "state" / "jev" / "events.jsonl"
        cursor = 0

        with events_out.open("a", encoding="utf-8") as events_fh, manifest_out.open(
            "a", encoding="utf-8"
        ) as manifest_fh:
            for row in corpus_rows:
                workflow_uuid = row.get("workflow_uuid")
                workflow_type = row.get("workflow_type")
                user_request = row.get("user_request", "")

                env = {
                    "CLAUDE_PROJECT_DIR": str(project),
                    "CLAUDE_PLUGIN_ROOT": str(plugin),
                    "TYPESAFE_API_KEY": api_key,
                }

                try:
                    code, _out, err = run_hook(
                        {
                            "hook_event_name": "UserPromptSubmit",
                            "prompt": user_request,
                            "cwd": str(project),
                            "session_id": f"jev-ab-bench-{workflow_uuid}",
                        },
                        env,
                    )
                except Exception as exc:
                    print(
                        f"WARNING: replay row {workflow_uuid!r} raised {type(exc).__name__}: {exc}",
                        file=sys.stderr,
                    )
                    n_failed += 1
                    continue

                if code != 0:
                    print(
                        f"WARNING: replay row {workflow_uuid!r} hook exited {code} (stderr={err!r})",
                        file=sys.stderr,
                    )
                    n_failed += 1
                    continue

                new_lines = _read_new_lines(internal_events_path, cursor)
                cursor += len(new_lines)

                if len(new_lines) != 2:
                    print(
                        f"WARNING: replay row {workflow_uuid!r} produced {len(new_lines)} telemetry "
                        "rows, expected 2 -- skipping manifest entry",
                        file=sys.stderr,
                    )
                    n_failed += 1
                    continue

                call_ids: set = set()
                parsed_rows = []
                for line in new_lines:
                    try:
                        parsed = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(parsed, dict):
                        parsed_rows.append(line)
                        call_id = parsed.get("call_id")
                        if call_id:
                            call_ids.add(call_id)

                if len(call_ids) != 1:
                    print(
                        f"WARNING: replay row {workflow_uuid!r} produced {len(call_ids)} distinct "
                        "call_ids, expected 1 -- skipping manifest entry",
                        file=sys.stderr,
                    )
                    n_failed += 1
                    continue

                for line in parsed_rows:
                    events_fh.write(line + "\n")
                    n_events += 1

                manifest_row = {
                    "call_id": next(iter(call_ids)),
                    "source_workflow_uuid": workflow_uuid,
                    "workflow_type": workflow_type,
                }
                manifest_fh.write(json.dumps(manifest_row, ensure_ascii=True) + "\n")
                n_manifest += 1
                events_fh.flush()
                manifest_fh.flush()

    return {
        "n_rows": len(corpus_rows),
        "n_events": n_events,
        "n_manifest": n_manifest,
        "n_failed": n_failed,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _read_corpus(path: Path) -> List[Dict[str, Any]]:
    """Impure: reads a corpus JSONL file (Phase 1's `{"workflow_uuid": str,
    "workflow_type": str, "user_request": str}` shape). A malformed line is
    skipped, never raised."""
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replay a corpus of real historical prompts through the real "
        "craftflow_jev_prompt_hint.py hook, in an isolated temp plugin/project root, "
        "producing an isolated events.jsonl + replay_manifest.jsonl."
    )
    parser.add_argument("--corpus", type=str, required=True)
    parser.add_argument(
        "--plugin-root",
        type=str,
        default=str(PLUGIN_ROOT),
        help="Read-only source for config/jev.json (never written to). Defaults to this repo's own plugin root.",
    )
    parser.add_argument("--events-out", type=str, required=True)
    parser.add_argument("--manifest-out", type=str, required=True)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)

    api_key = os.environ.get("TYPESAFE_API_KEY")
    if not api_key:
        print("SKIP: TYPESAFE_API_KEY not set", file=sys.stderr)
        return 1

    corpus_path = Path(args.corpus)
    if not corpus_path.is_file():
        print(f"error: --corpus file not found: {corpus_path}", file=sys.stderr)
        return 1

    corpus_rows = _read_corpus(corpus_path)

    stats = replay_corpus(
        corpus_rows,
        real_plugin_root=Path(args.plugin_root),
        events_out=Path(args.events_out),
        manifest_out=Path(args.manifest_out),
        api_key=api_key,
    )
    print(
        f"rows={stats['n_rows']} events={stats['n_events']} "
        f"manifest={stats['n_manifest']} failed={stats['n_failed']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
