#!/usr/bin/env python3
"""
craftflow_feature_backlog.py

Append/update-in-place, durable, git-tracked project-level feature backlog.
Mirrors craftflow_reliability_gates.py's atomic-write/lock/fail-closed idiom.
Planner-owned: registered/activated/completed by the ROUTER at PLAN/BUILD
memory-finalize points, never by an agent directly.

Subcommands:
    --register JSON_OBJECT [--state-dir PATH] [--backlog PATH]
        JSON_OBJECT: {"id": str, "title": str, "plan_file": str (optional)}
        Idempotent: if id already exists, no-op (exit 0, reports registered:false).

    --activate ID [--state-dir PATH] [--backlog PATH]
        not_started -> active. Idempotent no-op if already active/passing.
        Exit 1 if ID was never registered.

    --complete ID [--state-dir PATH] [--backlog PATH]
        not_started|active -> passing. Idempotent no-op if already passing.
        Exit 1 if ID was never registered.

    --report {text|json} [--state-dir PATH] [--backlog PATH]
        Prints entries + VCR = passing / activated, where
        activated = count(status in {active, passing}). If activated == 0,
        VCR is explicitly "N/A (no activated features yet)" -- NEVER a
        silent 0.0/0-of-0 fallback (see this project's own recorded gotcha
        about silent 0/0-fallback bugs, patterns.md line 58).

Backlog file default: .craftflow/state/project/feature-backlog.json
Exit 0 on success (including idempotent no-ops). Exit 1 on usage error,
multiple subcommands given at once, unknown id for --activate/--complete, or
fail-closed corruption -- a backlog file that EXISTS but fails to parse or
has an invalid schema shape raises FeatureBacklogCorruptError and is reported
as a stderr JSON error, mirroring craftflow_reliability_gates.py's actual
LedgerCorruptError fail-closed behavior (that sibling module raises on
identical corruption rather than silently discarding data). A missing file
is the only benign case: it auto-inits to an empty store, since nothing has
ever been written there yet.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
DEFAULT_BACKLOG_REL = "project/feature-backlog.json"
DEFAULT_STATE_DIR = ".craftflow/state"
_STATUSES = ("not_started", "active", "passing")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _empty_backlog() -> dict:
    return {"schema_version": SCHEMA_VERSION, "features": []}


class FeatureBacklogCorruptError(Exception):
    """Backlog file EXISTS but content cannot be trusted (parse failure or
    invalid top-level shape) -- mirrors craftflow_reliability_gates.py's
    LedgerCorruptError. Distinguished from "file does not exist" (benign,
    auto-inits to an empty store instead, since there is nothing to lose)."""


def load_backlog(path: Path) -> dict:
    if not path.exists():
        return _empty_backlog()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        # Fail closed on corruption -- mirrors craftflow_reliability_gates.py's
        # real LedgerCorruptError behavior. A file that EXISTS but cannot be
        # trusted must never be silently treated as empty: the next write
        # would persist that empty store over the corrupted file, permanently
        # destroying any pre-existing real entries.
        raise FeatureBacklogCorruptError(
            f"backlog file at {path} exists but failed to parse: {exc}"
        ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("features"), list):
        raise FeatureBacklogCorruptError(
            f"backlog file at {path} exists but has an invalid schema shape"
        )
    return data


@contextlib.contextmanager
def _backlog_file_lock(backlog_path: Path):
    backlog_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = backlog_path.with_suffix(backlog_path.suffix + ".lock")
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def save_backlog_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".feature-backlog-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp_name, str(path))
    except Exception:
        try:
            os.remove(tmp_name)
        except OSError:
            pass
        raise


def find_feature(backlog: dict, feature_id: str):
    for feat in backlog.get("features", []):
        if isinstance(feat, dict) and feat.get("id") == feature_id:
            return feat
    return None


def cmd_register(args) -> int:
    backlog_path = Path(args.backlog)
    payload = json.loads(args.register)
    feature_id = payload["id"]
    with _backlog_file_lock(backlog_path):
        backlog = load_backlog(backlog_path)
        if find_feature(backlog, feature_id) is not None:
            print(json.dumps({"registered": False, "reason": "id already exists"}, indent=2))
            return 0
        backlog.setdefault("features", []).append({
            "id": feature_id,
            "title": payload.get("title", ""),
            "plan_file": payload.get("plan_file"),
            "status": "not_started",
            "created_at": now_iso(),
            "activated_at": None,
            "completed_at": None,
        })
        save_backlog_atomic(backlog_path, backlog)
    print(json.dumps({"registered": True, "id": feature_id}, indent=2))
    return 0


def cmd_activate(args) -> int:
    if not args.activate:
        print(json.dumps({"error": "--activate requires a non-empty feature id"}), file=sys.stderr)
        return 1
    backlog_path = Path(args.backlog)
    with _backlog_file_lock(backlog_path):
        backlog = load_backlog(backlog_path)
        feat = find_feature(backlog, args.activate)
        if feat is None:
            print(json.dumps({"error": f"unknown feature id: {args.activate!r} -- never registered"}), file=sys.stderr)
            return 1
        if feat["status"] in ("active", "passing"):
            print(json.dumps({"activated": False, "reason": f"already {feat['status']}"}, indent=2))
            return 0
        feat["status"] = "active"
        feat["activated_at"] = now_iso()
        save_backlog_atomic(backlog_path, backlog)
    print(json.dumps({"activated": True, "id": args.activate}, indent=2))
    return 0


def cmd_complete(args) -> int:
    if not args.complete:
        print(json.dumps({"error": "--complete requires a non-empty feature id"}), file=sys.stderr)
        return 1
    backlog_path = Path(args.backlog)
    with _backlog_file_lock(backlog_path):
        backlog = load_backlog(backlog_path)
        feat = find_feature(backlog, args.complete)
        if feat is None:
            print(json.dumps({"error": f"unknown feature id: {args.complete!r} -- never registered or activated"}), file=sys.stderr)
            return 1
        if feat["status"] == "passing":
            print(json.dumps({"completed": False, "reason": "already passing"}, indent=2))
            return 0
        feat["status"] = "passing"
        feat["completed_at"] = now_iso()
        save_backlog_atomic(backlog_path, backlog)
    print(json.dumps({"completed": True, "id": args.complete}, indent=2))
    return 0


def _valid_features(features: list) -> list:
    """Skip malformed list entries (non-dict, or dict missing "id") so one
    bad record anywhere in the backlog never crashes VCR computation or the
    report loop -- mirrors find_feature()'s existing isinstance(feat, dict)
    skip-guard, applied consistently at every other site that iterates the
    same shared "features" list."""
    return [f for f in features if isinstance(f, dict) and "id" in f]


def _compute_vcr(features: list) -> dict:
    valid = _valid_features(features)
    passing = sum(1 for f in valid if f.get("status") == "passing")
    activated = sum(1 for f in valid if f.get("status") in ("active", "passing"))
    if activated == 0:
        return {"passing": passing, "activated": activated, "ratio": None, "display": "N/A (no activated features yet)"}
    ratio = passing / activated
    return {"passing": passing, "activated": activated, "ratio": ratio, "display": f"{passing}/{activated}"}


def cmd_report(args) -> int:
    backlog = load_backlog(Path(args.backlog))
    features = backlog.get("features", [])
    vcr = _compute_vcr(features)
    if args.report == "json":
        print(json.dumps({"features": features, "vcr": vcr}, indent=2))
    else:
        for f in _valid_features(features):
            print(f"{f['id']}: {f.get('status', '?')} -- {f.get('title', '')}")
        print(f"VCR: {vcr['display']}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Craftflow project-level feature backlog + VCR metric.")
    parser.add_argument("--register", metavar="JSON", help='JSON object: {"id":...,"title":...,"plan_file":...}')
    parser.add_argument("--activate", metavar="ID")
    parser.add_argument("--complete", metavar="ID")
    parser.add_argument("--report", choices=("text", "json"))
    parser.add_argument("--state-dir", default=DEFAULT_STATE_DIR, help=f"Craftflow state dir (default: {DEFAULT_STATE_DIR})")
    parser.add_argument("--backlog", default=None, help=f"Backlog file path (default: derived from --state-dir, {DEFAULT_BACKLOG_REL})")
    return parser


def _resolve_backlog_path(args) -> str:
    if args.backlog:
        return args.backlog
    return str(Path(args.state_dir) / "project" / "feature-backlog.json")


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    args.backlog = _resolve_backlog_path(args)

    given = [
        name
        for name, value in (
            ("--register", args.register),
            ("--activate", args.activate),
            ("--complete", args.complete),
            ("--report", args.report),
        )
        if value is not None
    ]
    if len(given) > 1:
        parser.error(
            "only one of --register/--activate/--complete/--report may be given at a time "
            f"(got {', '.join(given)})"
        )

    try:
        if args.register is not None:
            return cmd_register(args)
        if args.activate is not None:
            return cmd_activate(args)
        if args.complete is not None:
            return cmd_complete(args)
        if args.report is not None:
            return cmd_report(args)
    except (
        OSError,
        UnicodeDecodeError,
        AttributeError,
        TypeError,
        KeyError,
        json.JSONDecodeError,
        FeatureBacklogCorruptError,
    ) as exc:
        print(json.dumps({"error": f"unexpected error: {exc}"}), file=sys.stderr)
        return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
