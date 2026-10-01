#!/usr/bin/env python3
"""Terminal-only arm CLI for the stop-gate continue-ACT (SPEC-0019 / ADR-0056, DD-8..DD-10).

  craftflow_stop_gate_arm.py arm --workflow WF [--hours 1..24]   (default 8; needs a TTY on stdin AND stdout)
  craftflow_stop_gate_arm.py disarm
  craftflow_stop_gate_arm.py status [--workflow WF]

``arm`` runs the calibration report with ``--scope jev`` and refuses (exit 2) unless the GO criteria hold, asks
the user to type the project folder name, then writes the ``actContinue`` entry (GO stamp, thresholds, budget)
into the passwd-home consent file: atomic, 0600, directories 0700, symlinks refused, other keys preserved.
The GO stamp and the TTY check are calibration and friction, not a security boundary. No log events, no
network. Prints ONE JSON object. Python 3.9 stdlib only.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import stat
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import craftflow_stop_gate_core as core  # noqa: E402
import craftflow_stop_gate_report as report  # noqa: E402

HOURS_MIN, HOURS_MAX, HOURS_DEFAULT = 1, 24, 8
EVENTS_SEGMENTS = (".craftflow", "state", "stop-gate", "events.jsonl")
DIR_MODE, FILE_MODE = 0o700, 0o600


class CliError(Exception):
    def __init__(self, code, error, **extra):
        super().__init__(error)
        self.code, self.payload = code, dict(extra, error=error)


def iso_utc(epoch):
    return datetime.datetime.fromtimestamp(int(epoch), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def valid_hours(hours):
    return type(hours) is int and HOURS_MIN <= hours <= HOURS_MAX


def build_arm_entry(now, hours, project_root, workflow, settings, go_report):
    """The ``actContinue`` entry. Pure. Raises ValueError for hours outside 1..24 (exact int)."""
    if not valid_hours(hours):
        raise ValueError("hours")
    criteria = go_report if isinstance(go_report, dict) else {}
    return {
        "version": core.ARM_ENTRY_VERSION,
        "armedAt": iso_utc(now),
        "expiresAt": iso_utc(now + hours * 3600),
        "projectRoot": project_root,
        "workflow": workflow,
        "maxAutoContinuesPerSession": settings["maxAutoContinuesPerSession"],
        "go": {"met": criteria.get("met") is True, "scope": core.GO_SCOPE,
               "jevKindThreshold": settings["jevKindThreshold"], "jevNeedsHumanMax": settings["jevNeedsHumanMax"],
               "schemas": [report.GO_ROW_SCHEMA], "at": iso_utc(now), "stats": criteria.get("stats")},
    }


def project_root(env, cwd):
    """realpath of CLAUDE_PROJECT_DIR, else the git toplevel of ``cwd``, else ``cwd`` (as the hook does)."""
    chosen = env.get("CLAUDE_PROJECT_DIR")
    if not chosen:
        try:
            proc = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd, capture_output=True,
                                  text=True, timeout=10)
            chosen = proc.stdout.strip() if proc.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            chosen = ""
    return os.path.realpath(chosen or cwd)


def _plugin_config(env):
    root = env.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    return core.load_json_file(os.path.join(root, "config", "stop-gate.json"))[0]


def _read_consent(home):
    obj, tag, ctime = core.read_consent_file_ex(home)
    if tag is not None:
        raise CliError(1, "consent_file_refused", tag=tag)
    return obj, ctime


def _ensure_dirs(home):
    current = home
    for segment in core.USER_OVERRIDE_SEGMENTS[:-1]:
        current = os.path.join(current, segment)
        try:
            st = os.lstat(current)
        except FileNotFoundError:
            os.mkdir(current, DIR_MODE)
            continue
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise CliError(1, "consent_dir_unsafe")
    return current


def _write_consent(home, obj):
    """Atomic 0600 replace of the consent file; the temp file is created O_EXCL|O_NOFOLLOW."""
    directory = _ensure_dirs(home)
    path = os.path.join(directory, core.USER_OVERRIDE_SEGMENTS[-1])
    tmp = "%s.tmp.%d" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, FILE_MODE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(obj, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _settings(plugin_obj, consent_obj):
    return core.parse_settings(plugin_obj, consent_obj, "passwd", consent_obj)[0]


def _go_report(root, args):
    events = args.events or os.path.join(root, *EVENTS_SEGMENTS)
    try:
        rows, cut = report.load_events_ex(events)
        return report.build_report(rows, args.transcripts_root, cut, scope=core.GO_SCOPE)
    except Exception as exc:  # noqa: BLE001 - report failure means no GO
        raise CliError(2, "report_failed", kind=type(exc).__name__)


def cmd_arm(args, env, home, tty, confirm, now, cwd):
    if not tty:
        raise CliError(2, "no_tty")
    if not valid_hours(args.hours):
        raise CliError(2, "bad_hours", min=HOURS_MIN, max=HOURS_MAX)
    if not (isinstance(args.workflow, str) and core.cc.WF_ID_RE.fullmatch(args.workflow)):
        raise CliError(2, "bad_workflow")
    root = project_root(env, cwd)
    consent, _ctime = _read_consent(home)
    settings = _settings(_plugin_config(env), consent)
    go = _go_report(root, args)["go_criteria"]
    if go.get("met") is not True:
        raise CliError(2, "go_not_met", criteria=go.get("criteria"))
    expected = os.path.basename(root)
    if confirm("Type the project folder name (%s) to arm auto-continue for %d h: " % (expected, args.hours)) \
            != expected:
        raise CliError(2, "not_confirmed")
    entry = build_arm_entry(now, args.hours, root, args.workflow, settings, go)
    updated = dict(consent or {})
    updated["actContinue"] = entry
    _write_consent(home, updated)
    return {"armed": True, "expiresAt": entry["expiresAt"], "workflow": args.workflow, "projectRoot": root}


def cmd_disarm(args, env, home, tty, confirm, now, cwd):
    consent, _ctime = _read_consent(home)
    if not isinstance(consent, dict) or "actContinue" not in consent:
        return {"armed": False, "removed": False}
    updated = {k: v for k, v in consent.items() if k != "actContinue"}
    _write_consent(home, updated)
    return {"armed": False, "removed": True}


def cmd_status(args, env, home, tty, confirm, now, cwd):
    consent, ctime = _read_consent(home)
    root = project_root(env, cwd)
    entry = consent.get("actContinue") if isinstance(consent, dict) else None
    wf = args.workflow or (entry.get("workflow") if isinstance(entry, dict) else None)
    settings = _settings(_plugin_config(env), consent)
    # the last-human-line rule needs the transcript, so status treats the ctime as satisfied
    status = core.arm_status(consent, ctime, now, root, settings, ctime, wf)
    out = {"status": status, "projectRoot": root, "workflow": wf}
    if isinstance(entry, dict):
        out["expiresAt"] = entry.get("expiresAt")
    return out


COMMANDS = {"arm": cmd_arm, "disarm": cmd_disarm, "status": cmd_status}


def _stdin_confirm(prompt):
    sys.stderr.write(prompt)
    sys.stderr.flush()
    return sys.stdin.readline().strip()


def main(argv=None, env=None, home=None, tty=None, confirm=None, now=None, cwd=None):
    """Seams ``env``, ``home``, ``tty``, ``confirm``, ``now``, ``cwd`` exist for tests; the defaults are the
    process environment, the passwd home, a real TTY check and the stdin prompt."""
    parser = argparse.ArgumentParser(description="Arm or disarm the stop-gate auto-continue (terminal only).")
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--workflow", default=None)
    parser.add_argument("--hours", type=int, default=HOURS_DEFAULT)
    parser.add_argument("--events", default=None)
    parser.add_argument("--transcripts-root", default=None)
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    if tty is None:
        tty = sys.stdin.isatty() and sys.stdout.isatty()
    try:
        if home is None:
            home = core.passwd_home()
        if not (isinstance(home, str) and os.path.isabs(home)):
            raise CliError(1, "home_unresolved")
        result = COMMANDS[args.command](
            args, env, home, tty, confirm or _stdin_confirm,
            time.time() if now is None else now, cwd or os.getcwd())
        code = 0
    except CliError as exc:
        result, code = exc.payload, exc.code
    except OSError as exc:
        result, code = {"error": "io_error", "kind": type(exc).__name__}, 1
    print(json.dumps(result, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
