#!/usr/bin/env python3
"""Tests for craftflow_jev_replay.py.

Run: python3 tests/fixtures/test_craftflow_jev_replay.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import craftflow_jev_replay  # noqa: E402
from craftflow_jev_replay import replay_corpus, main as cli_main  # noqa: E402

_passes = 0
_errors: list[str] = []


def ok(name: str) -> None:
    global _passes
    _passes += 1
    print(f"  PASS: {name}")


def fail(name: str, reason: str) -> None:
    _errors.append(f"FAIL [{name}]: {reason}")
    print(f"  FAIL: {name}: {reason}")


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _make_fake_plugin_root(root: Path) -> Path:
    """A minimal temp plugin root with a config/jev.json -- read-only source
    for replay_corpus() to copy FROM. Never the real repo plugin root, so no
    test run ever touches real config/jev.json."""
    plugin_root = root / "fake_plugin_root"
    (plugin_root / "config").mkdir(parents=True)
    cfg = {
        "enabled": False,
        "model": "jev-latest",
        "features": {"routingHint": "audit", "skillHint": "audit", "remediationScope": "off"},
        "thresholds": {"routing": 0.85, "skill": 0.7, "remediationScope": 0.85},
        "maxStateChars": 4000,
        "timeoutSeconds": 2.5,
        "consent": {"status": "unset", "ts": None},
    }
    (plugin_root / "config" / "jev.json").write_text(json.dumps(cfg), encoding="utf-8")
    return plugin_root


def _make_corpus_rows(n: int) -> list[dict]:
    return [
        {
            "workflow_uuid": f"wf-{i}",
            "workflow_type": ["BUILD", "PLAN", "DEBUG"][i % 3],
            "user_request": f"fake historical request text {i}",
        }
        for i in range(n)
    ]


def _fake_run_hook_writes_two_rows(payload: dict, env: dict):
    """Simulates the real hook: appends 2 synthetic telemetry rows (sharing
    a fresh call_id) directly to <CLAUDE_PROJECT_DIR>/.craftflow/state/jev/events.jsonl
    -- the exact path the real craftflow_jev_prompt_hint.py hook would write
    to given that env, computed purely from the explicit `env` argument
    (never from this process's own os.environ)."""
    project_dir = Path(env["CLAUDE_PROJECT_DIR"])
    events_path = project_dir / ".craftflow" / "state" / "jev" / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    call_id = uuid.uuid4().hex
    routing_row = {"call_id": call_id, "session_id": payload.get("session_id"), "feature": "routing"}
    skill_row = {"call_id": call_id, "session_id": payload.get("session_id"), "feature": "skill"}
    with events_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(routing_row) + "\n")
        fh.write(json.dumps(skill_row) + "\n")
    return (0, "", "")


# ---------------------------------------------------------------------------
# Task 2.1
# ---------------------------------------------------------------------------


def test_replay_writes_isolated_events_and_manifest() -> None:
    original_run_hook = craftflow_jev_replay.run_hook
    craftflow_jev_replay.run_hook = _fake_run_hook_writes_two_rows
    try:
        with tempfile.TemporaryDirectory() as tmp_name:
            root = Path(tmp_name)
            plugin_root = _make_fake_plugin_root(root)
            events_out = root / "out" / "events.jsonl"
            manifest_out = root / "out" / "replay_manifest.jsonl"
            corpus_rows = _make_corpus_rows(3)

            replay_corpus(
                corpus_rows,
                real_plugin_root=plugin_root,
                events_out=events_out,
                manifest_out=manifest_out,
                api_key="fake-key-never-real",
            )

            event_lines = [l for l in events_out.read_text(encoding="utf-8").splitlines() if l.strip()]
            manifest_lines = [l for l in manifest_out.read_text(encoding="utf-8").splitlines() if l.strip()]

            if len(event_lines) == 6:
                ok("events.jsonl has 6 rows (3 prompts x 2 rows)")
            else:
                fail("events-row-count", f"expected 6, got {len(event_lines)}")

            if len(manifest_lines) == 3:
                ok("replay_manifest.jsonl has 3 rows (1 per prompt)")
            else:
                fail("manifest-row-count", f"expected 3, got {len(manifest_lines)}")

            event_rows = [json.loads(l) for l in event_lines]
            call_ids_by_pair: set[str] = set()
            for i in range(0, len(event_rows), 2):
                pair = event_rows[i : i + 2]
                ids = {row["call_id"] for row in pair}
                if len(ids) == 1:
                    call_ids_by_pair.add(next(iter(ids)))

            manifest_rows = [json.loads(l) for l in manifest_lines]
            manifest_call_ids = {row.get("call_id") for row in manifest_rows}

            if manifest_call_ids and manifest_call_ids.issubset(call_ids_by_pair):
                ok("every manifest call_id matches a routing/skill pair's shared call_id")
            else:
                fail(
                    "manifest-call-id-join",
                    f"manifest_call_ids={manifest_call_ids!r} not subset of {call_ids_by_pair!r}",
                )

            leaked = False
            for row in corpus_rows:
                needle = row["user_request"]
                for line in manifest_lines:
                    if needle in line:
                        leaked = True
            if not leaked:
                ok("no manifest row contains any corpus row's user_request substring")
            else:
                fail("manifest-no-prompt-text", "a manifest row contained prompt text")
    finally:
        craftflow_jev_replay.run_hook = original_run_hook


# ---------------------------------------------------------------------------
# Task 2.3
# ---------------------------------------------------------------------------


def test_replay_never_touches_real_project_dir() -> None:
    original_run_hook = craftflow_jev_replay.run_hook
    craftflow_jev_replay.run_hook = _fake_run_hook_writes_two_rows
    original_environ = dict(os.environ)
    try:
        with tempfile.TemporaryDirectory() as tmp_name:
            root = Path(tmp_name)
            plugin_root = _make_fake_plugin_root(root)
            events_out = root / "out" / "events.jsonl"
            manifest_out = root / "out" / "replay_manifest.jsonl"
            sentinel_project = root / "sentinel-real-looking-project"
            # Deliberately do NOT create sentinel_project -- if replay_corpus
            # (or anything it calls in THIS process) reads CLAUDE_PROJECT_DIR
            # from os.environ to compute a state path, that path gets
            # created as a side effect (see craftflow_hooklib.state_root()'s
            # eager .mkdir()); its continued absence after the call is the
            # proof of isolation.
            os.environ["CLAUDE_PROJECT_DIR"] = str(sentinel_project)

            replay_corpus(
                _make_corpus_rows(1),
                real_plugin_root=plugin_root,
                events_out=events_out,
                manifest_out=manifest_out,
                api_key="fake-key-never-real",
            )

            if not sentinel_project.exists():
                ok("sentinel CLAUDE_PROJECT_DIR path never created by replay_corpus()")
            else:
                fail(
                    "no-real-project-leak",
                    f"sentinel path was created: {list(sentinel_project.rglob('*'))!r}",
                )
    finally:
        craftflow_jev_replay.run_hook = original_run_hook
        os.environ.clear()
        os.environ.update(original_environ)


# ---------------------------------------------------------------------------
# Fail-open exit criterion: replay_corpus() never raises on a simulated hook
# failure -- matches craftflow_jev_client.call()'s own fail-open contract.
# ---------------------------------------------------------------------------


def test_replay_continues_on_simulated_hook_failure() -> None:
    call_count = {"n": 0}

    def flaky_run_hook(payload: dict, env: dict):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("simulated network failure")
        return _fake_run_hook_writes_two_rows(payload, env)

    original_run_hook = craftflow_jev_replay.run_hook
    craftflow_jev_replay.run_hook = flaky_run_hook
    try:
        with tempfile.TemporaryDirectory() as tmp_name:
            root = Path(tmp_name)
            plugin_root = _make_fake_plugin_root(root)
            events_out = root / "out" / "events.jsonl"
            manifest_out = root / "out" / "replay_manifest.jsonl"
            corpus_rows = _make_corpus_rows(3)

            raised = False
            stats = None
            try:
                stats = replay_corpus(
                    corpus_rows,
                    real_plugin_root=plugin_root,
                    events_out=events_out,
                    manifest_out=manifest_out,
                    api_key="fake-key-never-real",
                )
            except Exception as exc:  # pragma: no cover - assertion path
                raised = True
                fail("fail-open-no-raise", f"replay_corpus raised {type(exc).__name__}: {exc}")

            if not raised:
                ok("replay_corpus() never raises when one row's hook call fails")

            manifest_lines = (
                [l for l in manifest_out.read_text(encoding="utf-8").splitlines() if l.strip()]
                if manifest_out.exists()
                else []
            )
            if len(manifest_lines) == 2:
                ok("the 2 successful rows still produce manifest entries despite 1 failure")
            else:
                fail("fail-open-partial-progress", f"expected 2 manifest rows, got {len(manifest_lines)}")

            if stats is not None and stats.get("n_failed") == 1:
                ok("stats report exactly 1 failed row")
            else:
                fail("fail-open-stats", f"expected n_failed=1, got {stats!r}")
    finally:
        craftflow_jev_replay.run_hook = original_run_hook


# ---------------------------------------------------------------------------
# Remediation finding [CRITICAL]: cursor must resync to the file's actual
# line count after EVERY row (success or failure), not just the success
# path -- otherwise a partial write before a mid-row failure leaves an
# orphan line that bleeds into the next row's window.
# ---------------------------------------------------------------------------


def _fake_run_hook_partial_write_then_raise(payload: dict, env: dict):
    """Simulates a hook that writes exactly 1 line to events.jsonl and then
    fails (e.g. killed mid-write, or errors after its first append) --
    distinct from test_replay_continues_on_simulated_hook_failure's fake,
    which raises BEFORE any write occurs."""
    project_dir = Path(env["CLAUDE_PROJECT_DIR"])
    events_path = project_dir / ".craftflow" / "state" / "jev" / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    call_id = uuid.uuid4().hex
    orphan_row = {"call_id": call_id, "session_id": payload.get("session_id"), "feature": "routing"}
    with events_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(orphan_row) + "\n")
    raise RuntimeError("simulated partial-write failure mid-hook")


def test_replay_resyncs_cursor_after_partial_write_failure() -> None:
    call_count = {"n": 0}

    def sequenced_run_hook(payload: dict, env: dict):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _fake_run_hook_partial_write_then_raise(payload, env)
        return _fake_run_hook_writes_two_rows(payload, env)

    original_run_hook = craftflow_jev_replay.run_hook
    craftflow_jev_replay.run_hook = sequenced_run_hook
    try:
        with tempfile.TemporaryDirectory() as tmp_name:
            root = Path(tmp_name)
            plugin_root = _make_fake_plugin_root(root)
            events_out = root / "out" / "events.jsonl"
            manifest_out = root / "out" / "replay_manifest.jsonl"
            corpus_rows = _make_corpus_rows(2)

            stats = replay_corpus(
                corpus_rows,
                real_plugin_root=plugin_root,
                events_out=events_out,
                manifest_out=manifest_out,
                api_key="fake-key-never-real",
            )

            event_lines = (
                [l for l in events_out.read_text(encoding="utf-8").splitlines() if l.strip()]
                if events_out.exists()
                else []
            )
            manifest_lines = (
                [l for l in manifest_out.read_text(encoding="utf-8").splitlines() if l.strip()]
                if manifest_out.exists()
                else []
            )

            if len(manifest_lines) == 1:
                ok("row 2 (genuine success) still produces a manifest entry despite row 1's partial write")
            else:
                fail("cursor-resync-manifest", f"expected 1 manifest row, got {len(manifest_lines)}")

            if len(event_lines) == 2:
                ok("events_out gets exactly 2 lines for row 2 -- row 1's orphan line did not bleed in")
            else:
                fail("cursor-resync-events", f"expected 2 event lines, got {len(event_lines)}: {event_lines!r}")

            if stats.get("n_failed") == 1 and stats.get("n_manifest") == 1:
                ok("stats reflect 1 failed row (partial write) and 1 successful manifest row")
            else:
                fail("cursor-resync-stats", f"unexpected stats {stats!r}")
    finally:
        craftflow_jev_replay.run_hook = original_run_hook


# ---------------------------------------------------------------------------
# Remediation finding [HIGH]: a row where one of the 2 telemetry lines is
# malformed JSON (or parses to a non-dict) must be treated as a failure --
# no manifest row, no events written -- with a WARNING logged, instead of
# silently proceeding on the 1 surviving line.
# ---------------------------------------------------------------------------


def _fake_run_hook_writes_one_malformed_line(payload: dict, env: dict):
    project_dir = Path(env["CLAUDE_PROJECT_DIR"])
    events_path = project_dir / ".craftflow" / "state" / "jev" / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    call_id = uuid.uuid4().hex
    routing_row = {"call_id": call_id, "session_id": payload.get("session_id"), "feature": "routing"}
    with events_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(routing_row) + "\n")
        fh.write("{not valid json,,,\n")
    return (0, "", "")


def test_replay_rejects_row_with_malformed_telemetry_line() -> None:
    original_run_hook = craftflow_jev_replay.run_hook
    craftflow_jev_replay.run_hook = _fake_run_hook_writes_one_malformed_line
    try:
        with tempfile.TemporaryDirectory() as tmp_name:
            root = Path(tmp_name)
            plugin_root = _make_fake_plugin_root(root)
            events_out = root / "out" / "events.jsonl"
            manifest_out = root / "out" / "replay_manifest.jsonl"
            corpus_rows = _make_corpus_rows(1)

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                stats = replay_corpus(
                    corpus_rows,
                    real_plugin_root=plugin_root,
                    events_out=events_out,
                    manifest_out=manifest_out,
                    api_key="fake-key-never-real",
                )

            manifest_lines = (
                [l for l in manifest_out.read_text(encoding="utf-8").splitlines() if l.strip()]
                if manifest_out.exists()
                else []
            )
            event_lines = (
                [l for l in events_out.read_text(encoding="utf-8").splitlines() if l.strip()]
                if events_out.exists()
                else []
            )

            if len(manifest_lines) == 0:
                ok("no manifest row written when one telemetry line is malformed")
            else:
                fail("malformed-line-manifest", f"expected 0 manifest rows, got {len(manifest_lines)}")

            if len(event_lines) == 0:
                ok("events_out gets 0 lines when one telemetry line is malformed")
            else:
                fail("malformed-line-events", f"expected 0 event lines, got {len(event_lines)}: {event_lines!r}")

            if "WARNING" in stderr.getvalue():
                ok("a WARNING is logged for the malformed-telemetry row")
            else:
                fail("malformed-line-warning", f"expected WARNING in stderr, got {stderr.getvalue()!r}")

            if stats.get("n_failed") == 1:
                ok("stats report exactly 1 failed row for the malformed-telemetry case")
            else:
                fail("malformed-line-stats", f"expected n_failed=1, got {stats!r}")
    finally:
        craftflow_jev_replay.run_hook = original_run_hook


# ---------------------------------------------------------------------------
# Task 2.4 -- CLI main()
# ---------------------------------------------------------------------------


def test_cli_exits_1_without_api_key() -> None:
    original_environ = dict(os.environ)
    try:
        os.environ.pop("TYPESAFE_API_KEY", None)
        with tempfile.TemporaryDirectory() as tmp_name:
            root = Path(tmp_name)
            corpus_path = root / "corpus.jsonl"
            corpus_path.write_text(
                json.dumps({"workflow_uuid": "wf-1", "workflow_type": "BUILD", "user_request": "x"}) + "\n",
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = cli_main(
                    [
                        "--corpus",
                        str(corpus_path),
                        "--events-out",
                        str(root / "events.jsonl"),
                        "--manifest-out",
                        str(root / "manifest.jsonl"),
                    ]
                )
        if exit_code == 1 and "SKIP: TYPESAFE_API_KEY not set" in stderr.getvalue():
            ok("main() exits 1 with SKIP message when TYPESAFE_API_KEY is unset")
        else:
            fail(
                "cli-missing-key",
                f"exit_code={exit_code!r} stderr={stderr.getvalue()!r}",
            )
    finally:
        os.environ.clear()
        os.environ.update(original_environ)


def test_cli_main_success_with_mocked_run_hook() -> None:
    original_run_hook = craftflow_jev_replay.run_hook
    craftflow_jev_replay.run_hook = _fake_run_hook_writes_two_rows
    original_environ = dict(os.environ)
    try:
        os.environ["TYPESAFE_API_KEY"] = "fake-key-never-real"
        with tempfile.TemporaryDirectory() as tmp_name:
            root = Path(tmp_name)
            plugin_root = _make_fake_plugin_root(root)
            corpus_path = root / "corpus.jsonl"
            with corpus_path.open("w", encoding="utf-8") as fh:
                for row in _make_corpus_rows(2):
                    fh.write(json.dumps(row) + "\n")
            events_out = root / "out" / "events.jsonl"
            manifest_out = root / "out" / "replay_manifest.jsonl"

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = cli_main(
                    [
                        "--corpus",
                        str(corpus_path),
                        "--plugin-root",
                        str(plugin_root),
                        "--events-out",
                        str(events_out),
                        "--manifest-out",
                        str(manifest_out),
                    ]
                )

            event_lines = (
                [l for l in events_out.read_text(encoding="utf-8").splitlines() if l.strip()]
                if events_out.exists()
                else []
            )
            manifest_lines = (
                [l for l in manifest_out.read_text(encoding="utf-8").splitlines() if l.strip()]
                if manifest_out.exists()
                else []
            )

            if exit_code == 0 and len(event_lines) == 4 and len(manifest_lines) == 2:
                ok("main() with mocked hook + key writes 4 event rows and 2 manifest rows, exit 0")
            else:
                fail(
                    "cli-success-path",
                    f"exit_code={exit_code!r} events={len(event_lines)} manifest={len(manifest_lines)} "
                    f"stdout={stdout.getvalue()!r}",
                )
    finally:
        craftflow_jev_replay.run_hook = original_run_hook
        os.environ.clear()
        os.environ.update(original_environ)


def _call_cli_main(argv: list[str]) -> int:
    """Invoke cli_main(), converting an argparse usage-error SystemExit into
    a plain int return so callers can assert on it like any other exit code
    (argparse.parse_args() calls sys.exit(2) directly on unknown flags,
    bypassing main()'s own `return` path)."""
    try:
        return cli_main(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1


# ---------------------------------------------------------------------------
# Data-integrity fix: --events-out/--manifest-out append-mode-with-no-guard
# bug (real Phase 4 replay corruption, 2026-09-25). Default must fail fast
# on pre-existing output; --fresh truncates; --append opts into the old
# silent-append behavior; --fresh + --append together is a usage error.
# ---------------------------------------------------------------------------


def test_cli_default_fails_fast_on_preexisting_output() -> None:
    original_environ = dict(os.environ)
    try:
        os.environ["TYPESAFE_API_KEY"] = "fake-key-never-real"
        with tempfile.TemporaryDirectory() as tmp_name:
            root = Path(tmp_name)
            plugin_root = _make_fake_plugin_root(root)
            corpus_path = root / "corpus.jsonl"
            with corpus_path.open("w", encoding="utf-8") as fh:
                for row in _make_corpus_rows(1):
                    fh.write(json.dumps(row) + "\n")
            events_out = root / "out" / "events.jsonl"
            manifest_out = root / "out" / "replay_manifest.jsonl"
            events_out.parent.mkdir(parents=True)
            events_out.write_text("STALE_EVENT_ROW\n", encoding="utf-8")

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = _call_cli_main(
                    [
                        "--corpus",
                        str(corpus_path),
                        "--plugin-root",
                        str(plugin_root),
                        "--events-out",
                        str(events_out),
                        "--manifest-out",
                        str(manifest_out),
                    ]
                )
            err = stderr.getvalue()

            if exit_code == 1:
                ok("default invocation against pre-existing output exits 1")
            else:
                fail("preexisting-exit-code", f"expected 1, got {exit_code!r}")

            if "--events-out" in err and "--manifest-out" in err:
                ok("error message names both --events-out and --manifest-out flags")
            else:
                fail("preexisting-names-flags", f"stderr={err!r}")

            if str(events_out) in err:
                ok("error message names the specific path that already existed")
            else:
                fail("preexisting-names-path", f"stderr={err!r}")

            if events_out.read_text(encoding="utf-8") == "STALE_EVENT_ROW\n":
                ok("pre-existing events_out content is untouched (no write occurred)")
            else:
                fail(
                    "preexisting-no-write",
                    f"events_out content changed: {events_out.read_text(encoding='utf-8')!r}",
                )

            if not manifest_out.exists():
                ok("manifest_out was never created")
            else:
                fail("preexisting-no-manifest", "manifest_out unexpectedly created")
    finally:
        os.environ.clear()
        os.environ.update(original_environ)


def test_cli_fresh_truncates_and_produces_clean_dataset() -> None:
    original_run_hook = craftflow_jev_replay.run_hook
    craftflow_jev_replay.run_hook = _fake_run_hook_writes_two_rows
    original_environ = dict(os.environ)
    try:
        os.environ["TYPESAFE_API_KEY"] = "fake-key-never-real"
        with tempfile.TemporaryDirectory() as tmp_name:
            root = Path(tmp_name)
            plugin_root = _make_fake_plugin_root(root)
            corpus_path = root / "corpus.jsonl"
            with corpus_path.open("w", encoding="utf-8") as fh:
                for row in _make_corpus_rows(1):
                    fh.write(json.dumps(row) + "\n")
            events_out = root / "out" / "events.jsonl"
            manifest_out = root / "out" / "replay_manifest.jsonl"
            events_out.parent.mkdir(parents=True)
            events_out.write_text("STALE_EVENT_ROW\n", encoding="utf-8")
            manifest_out.write_text("STALE_MANIFEST_ROW\n", encoding="utf-8")

            exit_code = _call_cli_main(
                [
                    "--corpus",
                    str(corpus_path),
                    "--plugin-root",
                    str(plugin_root),
                    "--events-out",
                    str(events_out),
                    "--manifest-out",
                    str(manifest_out),
                    "--fresh",
                ]
            )

            event_lines = [l for l in events_out.read_text(encoding="utf-8").splitlines() if l.strip()]
            manifest_lines = [l for l in manifest_out.read_text(encoding="utf-8").splitlines() if l.strip()]

            if exit_code == 0:
                ok("--fresh invocation exits 0")
            else:
                fail("fresh-exit-code", f"expected 0, got {exit_code!r}")

            if "STALE_EVENT_ROW" not in event_lines and len(event_lines) == 2:
                ok("--fresh truncates events_out: stale row gone, only this run's 2 rows remain")
            else:
                fail("fresh-events-clean", f"event_lines={event_lines!r}")

            if "STALE_MANIFEST_ROW" not in manifest_lines and len(manifest_lines) == 1:
                ok("--fresh truncates manifest_out: stale row gone, only this run's 1 row remains")
            else:
                fail("fresh-manifest-clean", f"manifest_lines={manifest_lines!r}")
    finally:
        craftflow_jev_replay.run_hook = original_run_hook
        os.environ.clear()
        os.environ.update(original_environ)


def test_cli_append_preserves_old_silent_append_behavior() -> None:
    original_run_hook = craftflow_jev_replay.run_hook
    craftflow_jev_replay.run_hook = _fake_run_hook_writes_two_rows
    original_environ = dict(os.environ)
    try:
        os.environ["TYPESAFE_API_KEY"] = "fake-key-never-real"
        with tempfile.TemporaryDirectory() as tmp_name:
            root = Path(tmp_name)
            plugin_root = _make_fake_plugin_root(root)
            corpus_path = root / "corpus.jsonl"
            with corpus_path.open("w", encoding="utf-8") as fh:
                for row in _make_corpus_rows(1):
                    fh.write(json.dumps(row) + "\n")
            events_out = root / "out" / "events.jsonl"
            manifest_out = root / "out" / "replay_manifest.jsonl"
            events_out.parent.mkdir(parents=True)
            events_out.write_text("PRIOR_EVENT_ROW\n", encoding="utf-8")
            manifest_out.write_text("PRIOR_MANIFEST_ROW\n", encoding="utf-8")

            exit_code = _call_cli_main(
                [
                    "--corpus",
                    str(corpus_path),
                    "--plugin-root",
                    str(plugin_root),
                    "--events-out",
                    str(events_out),
                    "--manifest-out",
                    str(manifest_out),
                    "--append",
                ]
            )

            event_lines = [l for l in events_out.read_text(encoding="utf-8").splitlines() if l.strip()]
            manifest_lines = [l for l in manifest_out.read_text(encoding="utf-8").splitlines() if l.strip()]

            if exit_code == 0:
                ok("--append invocation exits 0")
            else:
                fail("append-exit-code", f"expected 0, got {exit_code!r}")

            if event_lines == ["PRIOR_EVENT_ROW"] + event_lines[1:] and len(event_lines) == 3:
                ok("--append preserves prior events_out row and appends this run's 2 new rows")
            else:
                fail("append-events-preserved", f"event_lines={event_lines!r}")

            if manifest_lines == ["PRIOR_MANIFEST_ROW"] + manifest_lines[1:] and len(manifest_lines) == 2:
                ok("--append preserves prior manifest_out row and appends this run's 1 new row")
            else:
                fail("append-manifest-preserved", f"manifest_lines={manifest_lines!r}")
    finally:
        craftflow_jev_replay.run_hook = original_run_hook
        os.environ.clear()
        os.environ.update(original_environ)


def test_cli_fresh_and_append_together_is_usage_error() -> None:
    original_environ = dict(os.environ)
    try:
        os.environ["TYPESAFE_API_KEY"] = "fake-key-never-real"
        with tempfile.TemporaryDirectory() as tmp_name:
            root = Path(tmp_name)
            corpus_path = root / "corpus.jsonl"
            corpus_path.write_text(
                json.dumps({"workflow_uuid": "wf-1", "workflow_type": "BUILD", "user_request": "x"}) + "\n",
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = _call_cli_main(
                    [
                        "--corpus",
                        str(corpus_path),
                        "--events-out",
                        str(root / "events.jsonl"),
                        "--manifest-out",
                        str(root / "manifest.jsonl"),
                        "--fresh",
                        "--append",
                    ]
                )
            err = stderr.getvalue()

            if exit_code != 0:
                ok("--fresh and --append together exits non-zero")
            else:
                fail("mutex-exit-code", f"expected non-zero, got {exit_code!r}")

            if "--fresh" in err and "--append" in err:
                ok("usage-error message names both --fresh and --append")
            else:
                fail("mutex-names-flags", f"stderr={err!r}")

            if not (root / "events.jsonl").exists() and not (root / "manifest.jsonl").exists():
                ok("no output files created when --fresh and --append are both passed")
            else:
                fail("mutex-no-write", "output files unexpectedly created")
    finally:
        os.environ.clear()
        os.environ.update(original_environ)


def main() -> int:
    print("test_craftflow_jev_replay: running")
    test_replay_writes_isolated_events_and_manifest()
    test_replay_never_touches_real_project_dir()
    test_replay_continues_on_simulated_hook_failure()
    test_replay_resyncs_cursor_after_partial_write_failure()
    test_replay_rejects_row_with_malformed_telemetry_line()
    test_cli_exits_1_without_api_key()
    test_cli_main_success_with_mocked_run_hook()
    test_cli_default_fails_fast_on_preexisting_output()
    test_cli_fresh_truncates_and_produces_clean_dataset()
    test_cli_append_preserves_old_silent_append_behavior()
    test_cli_fresh_and_append_together_is_usage_error()

    print()
    print("=" * 40)
    if _errors:
        for err in _errors:
            print(err, file=sys.stderr)
        print(f"\nResults: {_passes} passed, {len(_errors)} failed", file=sys.stderr)
        print("FAIL", file=sys.stderr)
        return 1
    print(f"Results: {_passes} passed, 0 failed")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
