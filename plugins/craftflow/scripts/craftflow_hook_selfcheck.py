#!/usr/bin/env python3
"""SessionStart self-check: verifies every sibling craftflow_*.py hook script
imports cleanly under the real python3 interpreter that hooks.json invokes.

Runs at every SessionStart. Silent (exit 0, no output) when every sibling
script imports cleanly. On any import failure, emits a SessionStart
hookSpecificOutput/additionalContext warning naming the failing script(s)
and each failure's exception summary line, plus a generic degradation note.

Design: docs/plans/2026-07-24-craftflow-hook-selfcheck-design.md

Constraints (see design doc — do not relax without updating the design):
- Must NOT import any checked script (including craftflow_hooklib.py)
  directly in this process. Subprocess-only testing, so a broken shared
  module can never crash the checker itself.
- Resolves the scripts directory via Path(__file__).parent (never a
  hardcoded path, never CLAUDE_PLUGIN_ROOT).
- Resolves the interpreter as the bare literal "python3" on PATH, matching
  hooks.json's own existing command pattern -- never sys.executable.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

PER_SCRIPT_TIMEOUT_SECONDS = 5
INTERPRETER = "python3"
# If fewer than this many sibling scripts are ever discovered, that itself
# indicates this checker's OWN directory resolution is broken (e.g. a future
# packaging change, an unexpected __file__ resolution surprise -- this repo
# has direct history of exactly this class of bug: git show 76020c0), not
# that every other hook script has vanished. A near-empty result must not
# look identical to a genuinely clean run.
MIN_EXPECTED_SIBLING_SCRIPTS = 5
# Bounds discover_sibling_scripts' own directory listing (scripts_dir.glob()),
# which is unbounded filesystem I/O just like a per-script subprocess check.
# A slow filesystem (NFS mount, unsynced iCloud path, etc.) must degrade the
# same way a slow per-script check now does, instead of hanging silently
# BEFORE the per-script deadline clock below ever engages -- worse than the
# original bug, since the truncation-flush mitigation has nothing to flush
# yet at that point.
DISCOVERY_TIMEOUT_SECONDS = 2
# Internal soft deadline for the whole per-script sweep, checked AFTER each
# script finishes. Must stay well under the registered SessionStart hook
# timeout (15s in hooks/hooks.json) -- with ~33 real siblings today,
# PER_SCRIPT_TIMEOUT_SECONDS(5) x 33 = 165s worst case with no deadline at
# all, which Claude Code's own hook-timeout mechanism would kill long before
# a single already-detected failure is ever printed (batched-then-printed
# design + no deadline = silent loss, one layer down from the exact
# "silently broken for a month" failure mode this feature exists to close).
# Because the deadline is only checked AFTER a script finishes, the true
# worst-case wall time is bounded by DISCOVERY_TIMEOUT_SECONDS +
# SELFCHECK_TIME_BUDGET_SECONDS + PER_SCRIPT_TIMEOUT_SECONDS (one script's
# own worst case can still complete after the sweep budget is exceeded) =
# 2 + 5 + 5 = 12s, leaving a ~3s margin under the 15s hook timeout for
# process startup/shutdown overhead. test_selfcheck_internal_budget_stays_
# under_registered_hook_timeout (craftflow_hook_unit_tests.py) asserts this
# arithmetic against the real hooks/hooks.json value, so a future change to
# either side without updating the other fails the test suite instead of
# silently reopening this exact class of bug.
SELFCHECK_TIME_BUDGET_SECONDS = 5


def discover_sibling_scripts(
    scripts_dir: Path, self_path: Path, timeout: float | None = None
) -> tuple[list[Path] | None, str | None]:
    """Return (siblings, None) on success, or (None, reason) if the listing
    itself could not be bounded in time.

    When timeout is None, runs the glob synchronously in-process (fast path
    for callers that don't need bounding, e.g. tests with no slow-filesystem
    concern). When timeout is given, runs the glob on a daemon thread and
    returns as soon as either it finishes or the timeout elapses -- never
    blocks the caller on a stuck filesystem call. A daemon thread that is
    still stuck when the timeout fires is abandoned (not joined) at process
    exit, never delaying shutdown.
    """

    def _do_glob() -> list[Path]:
        return sorted(
            p for p in scripts_dir.glob("craftflow_*.py")
            if p.resolve() != self_path.resolve()
        )

    if timeout is None:
        return _do_glob(), None

    result_box: dict[str, object] = {}

    def _worker() -> None:
        try:
            result_box["siblings"] = _do_glob()
        except Exception as exc:
            result_box["error"] = str(exc)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return None, f"sibling-directory discovery timed out after {timeout}s"
    if "error" in result_box:
        return None, f"sibling-directory discovery failed: {result_box['error']}"
    return result_box.get("siblings", []), None


def _last_nonblank_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()
    return ""


def check_script(scripts_dir: Path, script_path: Path) -> str | None:
    """Return None if script_path imports cleanly under python3, else a short
    human-readable failure reason (the exception's own summary line)."""
    module_name = script_path.stem
    try:
        # cf:shortcut: no process-group/session isolation here -- if a future
        # sibling script ever spawns its own grandchild process at import
        # time, a timeout on this subprocess.run would leave that grandchild
        # orphaned. No existing craftflow_*.py script does this today;
        # revisit (e.g. start_new_session=True + killpg on timeout) if one
        # ever does.
        result = subprocess.run(
            [INTERPRETER, "-c", f"import {module_name}"],
            cwd=str(scripts_dir),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=PER_SCRIPT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return f"timed out after {PER_SCRIPT_TIMEOUT_SECONDS}s"
    except FileNotFoundError:
        return f"interpreter '{INTERPRETER}' not found on PATH"
    if result.returncode == 0:
        return None
    reason = _last_nonblank_line(result.stderr)
    return reason or f"exited {result.returncode} with no stderr output"


def run_selfcheck(
    scripts_dir: Path, siblings: list[Path], deadline: float | None = None
) -> tuple[list[tuple[str, str]], int, int]:
    """Check each of the given sibling scripts, in order, up to an optional
    soft time deadline (seconds). Returns (failures, checked_count,
    total_count). Discovery is the caller's responsibility (see
    discover_sibling_scripts) -- this function never re-globs, so its own
    bounded discovery timeout is not duplicated or bypassed here.

    The deadline is checked AFTER each script finishes (never mid-check), so
    a script's own result is always fully recorded before the sweep can stop.
    If checked_count < total_count on return, the sweep was truncated by the
    deadline, not by having genuinely checked everything -- callers must not
    treat that as "clean" silence.
    """
    failures: list[tuple[str, str]] = []
    start = time.monotonic()
    checked = 0
    for script_path in siblings:
        try:
            reason = check_script(scripts_dir, script_path)
        except Exception as exc:
            # Per-item isolation: one script's unexpected error (e.g. a real
            # resource-pressure OSError from spawning ~35 sequential python3
            # subprocesses) must never erase already-collected results for
            # every other script checked in this run -- that would recreate,
            # one layer down, the exact silent-failure mode this feature
            # exists to close.
            reason = f"self-check error while testing this script: {exc}"
        checked += 1
        if reason is not None:
            failures.append((script_path.name, reason))
        if deadline is not None and (time.monotonic() - start) > deadline:
            break
    return failures, checked, len(siblings)


def emit_warning(
    failures: list[tuple[str, str]],
    truncated: bool = False,
    checked: int = 0,
    total: int = 0,
) -> None:
    parts = []
    if failures:
        failing_desc = ", ".join(f"{name} ({reason})" for name, reason in failures)
        parts.append(
            f"{len(failures)} hook script(s) failed to import under {INTERPRETER}: {failing_desc}."
        )
    if truncated:
        parts.append(
            f"Sweep truncated by its internal time budget after checking {checked} of "
            f"{total} sibling script(s); the rest were not checked this run."
        )
    message = "CRAFTFLOW SELF-CHECK: " + " ".join(parts) + " Plugin functionality may be degraded."
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": message,
                }
            },
            ensure_ascii=True,
        )
    )


def emit_low_count_warning(discovered_count: int, floor: int) -> None:
    message = (
        f"CRAFTFLOW SELF-CHECK: only {discovered_count} sibling hook script(s) discovered "
        f"(expected at least {floor}) -- this most likely means the self-check's own "
        f"directory resolution is broken, not that every other hook script is genuinely "
        f"missing. Plugin functionality may be degraded."
    )
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": message,
                }
            },
            ensure_ascii=True,
        )
    )


def emit_discovery_error_warning(reason: str) -> None:
    message = (
        f"CRAFTFLOW SELF-CHECK: could not enumerate sibling hook scripts ({reason}) -- "
        f"this self-check could not run this session. Plugin functionality may be degraded."
    )
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": message,
                }
            },
            ensure_ascii=True,
        )
    )


def main() -> int:
    try:
        self_path = Path(__file__).resolve()
        scripts_dir = self_path.parent
        siblings, discovery_error = discover_sibling_scripts(
            scripts_dir, self_path, timeout=DISCOVERY_TIMEOUT_SECONDS
        )
        if discovery_error is not None:
            emit_discovery_error_warning(discovery_error)
            return 0
        if len(siblings) < MIN_EXPECTED_SIBLING_SCRIPTS:
            emit_low_count_warning(len(siblings), MIN_EXPECTED_SIBLING_SCRIPTS)
            return 0
        failures, checked, total = run_selfcheck(
            scripts_dir, siblings, deadline=SELFCHECK_TIME_BUDGET_SECONDS
        )
        truncated = checked < total
        if failures or truncated:
            emit_warning(failures, truncated=truncated, checked=checked, total=total)
        return 0
    except Exception:
        # Never let the checker itself crash or block the SessionStart hook chain.
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
