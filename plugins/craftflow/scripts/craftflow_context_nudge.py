#!/usr/bin/env python3
"""craftflow_context_nudge.py -- context-size nudge (SPEC-0016 / ADR-0051).
Modes: (no flag) UserPromptSubmit hook on stdin; --reset SessionStart(compact) on stdin;
--boundary --wf <id> [--phase ID] [--project-root DIR] [--session-id ID] router CLI (one JSON line)."""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

from craftflow_hooklib import (
    json_print,
    load_input,
    load_mode,
    log_event,
    now_iso,
    plugin_config_dir,
    read_workflow_state,
    state_root,
)
from craftflow_transcript_usage import last_turn_context_tokens

SCHEMA_VERSION = 1
LEVELS = ("none", "warn", "critical")
DEFAULTS = {"warnTokens": 120000, "criticalTokens": 160000, "assumedWindow": 200000}
TAIL_BYTES = 262144
RETRY_TAIL_BYTES = 4194304
BOUNDARY_STATE_MAX_AGE_S = 43200
STATE_PRUNE_AGE_S = 1209600
STATE_DIRNAME = "context-nudge"
_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_WF_RE = re.compile(r"^wf-[A-Za-z0-9-]{1,160}$")
_BOUNDARY_KEYS = ("mode", "level", "tokens", "threshold", "assumed_window", "relay", "advisory",
                  "checkpoint_path", "session_id", "session_source", "phase", "outcome", "error")


# ---------------------------------------------------------------------------
# Pure core
# ---------------------------------------------------------------------------

def rank(level):
    """Index of a level in LEVELS; 0 for anything unknown."""
    try:
        return LEVELS.index(level)
    except (ValueError, TypeError):
        return 0


def _level_or_none(level):
    return level if isinstance(level, str) and level in LEVELS else "none"


def classify(tokens, warn, critical):
    """none | warn | critical using tokens >= threshold. Non-int / non-positive -> none."""
    try:
        if type(tokens) is not int or tokens <= 0:
            return "none"
        if tokens >= critical:
            return "critical"
        if tokens >= warn:
            return "warn"
    except Exception:  # noqa: BLE001 - totality (P3)
        pass
    return "none"


def resolve_mode(raw):
    """(effective_mode, logged_mode) for the contextNudge toggle (DD-6)."""
    if raw == "off":
        return "off", "off"
    if raw == "on":
        return "on", "on"
    if raw == "audit" or raw is None:
        return "audit", "audit"
    return "audit", "audit-unrecognized-config-value"


def decide(level, last_level, mode):
    """Hook decision (DD-5). new_last_level is always the observed level."""
    level = _level_or_none(level)
    if mode == "off":
        action = "off"
    elif rank(level) > rank(last_level):
        action = "nudge" if mode == "on" else "would_nudge"
    elif rank(level) < rank(last_level):
        action = "rearmed"
    else:
        action = "below_threshold" if level == "none" else "suppressed"
    return {"action": action, "level": level, "new_last_level": level}


def decide_boundary(level, boundary_level, mode, measured):
    """Boundary relay decision (DD-11); `relay` is a literal bool computed here."""
    level = _level_or_none(level)
    kept = _level_or_none(boundary_level)
    if not measured:
        return {"relay": False, "new_boundary_level": kept, "outcome": "no_session_state"}
    if mode == "off":
        return {"relay": False, "new_boundary_level": kept, "outcome": "off"}
    if level == "none":
        return {"relay": False, "new_boundary_level": kept, "outcome": "below_threshold"}
    if rank(level) > rank(kept):
        if mode == "on":
            return {"relay": True, "new_boundary_level": level, "outcome": "advised"}
        return {"relay": False, "new_boundary_level": kept, "outcome": "would_advise"}
    return {"relay": False, "new_boundary_level": kept, "outcome": "already_advised"}


def validate_config(obj):
    """(cfg, None) or (None, "config_invalid") per DD-7."""
    try:
        if not isinstance(obj, dict):
            return None, "config_invalid"
        cfg = {}
        for key in DEFAULTS:
            v = obj.get(key)
            if type(v) is not int or v <= 0:
                return None, "config_invalid"
            cfg[key] = v
        if cfg["criticalTokens"] <= cfg["warnTokens"] or cfg["assumedWindow"] < cfg["criticalTokens"]:
            return None, "config_invalid"
        return cfg, None
    except Exception:  # noqa: BLE001 - totality (P3)
        return None, "config_invalid"


def safe_session_id(value):
    return value if isinstance(value, str) and _SESSION_RE.match(value) else None


def safe_wf(value):
    return value if isinstance(value, str) and _WF_RE.match(value) else None


def _fmt(n):
    try:
        return format(n, ",") if type(n) is int else str(n)
    except Exception:  # noqa: BLE001
        return "?"


def render_advisory(level, tokens, cfg):
    """Single-line advisory text (DD-10); empty string for level none / unknown."""
    try:
        cfg = cfg if isinstance(cfg, dict) else DEFAULTS
        window = _fmt(cfg.get("assumedWindow", DEFAULTS["assumedWindow"]))
        if level == "warn":
            return (f"CRAFTFLOW context advisory: this session's context is about {_fmt(tokens)} tokens "
                    f"(warn threshold {_fmt(cfg.get('warnTokens', DEFAULTS['warnTokens']))}; "
                    f"assumed window {window}). Tell the user once, in one sentence, that running "
                    "/compact at the next natural pause (for example a craftflow phase boundary) keeps "
                    "answer quality up; craftflow workflow state survives compaction. Do not stop the "
                    "current task because of this note.")
        if level == "critical":
            return (f"CRAFTFLOW context advisory: this session's context is about {_fmt(tokens)} tokens "
                    f"(critical threshold {_fmt(cfg.get('criticalTokens', DEFAULTS['criticalTokens']))}; "
                    f"assumed window {window}). Auto-compaction may fire soon: finish the current step, "
                    "checkpoint durable state, and ask the user to run /compact before starting new work.")
    except Exception:  # noqa: BLE001 - totality (P3)
        pass
    return ""


def boundary_result(**fields):
    """Boundary JSON dict with every DD-11 key present (missing -> None, relay False)."""
    out = {"schema": SCHEMA_VERSION}
    for key in _BOUNDARY_KEYS:
        out[key] = fields.get(key)
    out["relay"] = fields.get("relay") is True
    return out


# ---------------------------------------------------------------------------
# Impure shells (transcript + state + config). Each returns instead of raising.
# ---------------------------------------------------------------------------

def load_config(path):
    """(cfg, None) | (defaults, None) when the file is missing | (None, "config_invalid")."""
    try:
        p = str(path)
        if not os.path.exists(p):
            return dict(DEFAULTS), None
        with open(p, "r", encoding="utf-8") as handle:
            return validate_config(json.load(handle))
    except Exception:  # noqa: BLE001 - fail-open contract: invalid config, never raise
        return None, "config_invalid"


def read_tail_lines(path, max_bytes):
    """Last `max_bytes` of the file as lines; the possibly truncated first line is dropped."""
    with open(path, "rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        offset = max(0, size - max_bytes)
        handle.seek(offset)
        data = handle.read()
    lines = data.decode("utf-8", errors="replace").splitlines()
    if offset > 0 and lines:
        lines = lines[1:]
    return lines


def current_context_tokens(transcript_path):
    """Measure current context from the transcript tail (DD-4). Never raises."""
    out = {"tokens": None, "model": None, "source": None, "error": None}
    try:
        if not transcript_path or not isinstance(transcript_path, (str, os.PathLike)):
            out["error"] = "no_transcript_path"
            return out
        p = str(transcript_path)
        if not os.path.exists(p):
            out["error"] = "transcript_missing"
            return out
        if not p.endswith(".jsonl") or not os.path.isfile(p):
            out["error"] = "transcript_not_regular_jsonl"
            return out
        size = os.path.getsize(p)
        res = last_turn_context_tokens(read_tail_lines(p, TAIL_BYTES))
        if res["tokens"] is None and res["source"] is None and size > TAIL_BYTES:
            res = last_turn_context_tokens(read_tail_lines(p, RETRY_TAIL_BYTES))
        out.update(tokens=res["tokens"], model=res["model"], source=res["source"])
        if res["tokens"] is None:
            out["error"] = "post_tokens_missing" if res["source"] == "compact_boundary" else "no_usage"
        return out
    except Exception as exc:  # noqa: BLE001 - fail-open contract: never raise
        out["tokens"] = None
        out["error"] = "unexpected:" + type(exc).__name__
        return out


def state_path(state_dir, sid):
    return Path(state_dir) / (str(sid) + ".json")


def load_state(path):
    """Per-session state merged over the defaults; unknown levels -> none. Never raises."""
    state = {"last_level": "none", "boundary_level": "none"}
    try:
        with open(str(path), "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if isinstance(raw, dict):
            state.update(raw)
    except Exception:  # noqa: BLE001 - missing/corrupt state is treated as fresh
        pass
    state["last_level"] = _level_or_none(state.get("last_level"))
    state["boundary_level"] = _level_or_none(state.get("boundary_level"))
    return state


def save_state(path, state):
    """Atomic write (mkstemp + os.replace). Returns False on any failure, cleaning the temp."""
    tmp = None
    try:
        parent = os.path.dirname(str(path)) or "."
        os.makedirs(parent, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=parent, prefix=".ctx-nudge-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle)
        os.replace(tmp, str(path))
        return True
    except Exception:  # noqa: BLE001 - fail-open contract
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return False


# ---------------------------------------------------------------------------
# Entry points (each returns 0; nothing propagates out)
# ---------------------------------------------------------------------------

def hook_main(data):
    """UserPromptSubmit hook. Check order: event guard, mode, config, session_id, transcript."""
    row = {"schema": SCHEMA_VERSION, "source": "hook", "mode": None, "session_id": None, "level": None,
           "tokens": None, "token_source": None, "threshold": None, "last_level": None,
           "outcome": "error", "error": None}
    try:
        data = data if isinstance(data, dict) else {}
        if data.get("hook_event_name") not in (None, "UserPromptSubmit"):
            return 0
        effective, row["mode"] = resolve_mode(load_mode().get("contextNudge"))
        if effective == "off":
            return 0
        cfg, cerr = load_config(plugin_config_dir() / "context-nudge.json")
        if cerr:
            row["error"] = cerr
            log_event("context_nudge", row)
            return 0
        sid = safe_session_id(data.get("session_id"))
        row["session_id"] = sid
        if sid is None:
            row["error"] = "bad_session_id"
            log_event("context_nudge", row)
            return 0
        m = current_context_tokens(data.get("transcript_path"))
        if m["tokens"] is None:
            skipped = m["error"] in ("transcript_missing", "no_usage", "post_tokens_missing")
            row["outcome"] = "skipped" if skipped else "error"
            row["error"] = m["error"]
            log_event("context_nudge", row)
            return 0
        level = classify(m["tokens"], cfg["warnTokens"], cfg["criticalTokens"])
        sp = state_path(state_root() / STATE_DIRNAME, sid)
        exists = sp.exists()
        st = load_state(sp)
        d = decide(level, st["last_level"], effective)
        # boundary_level can only fall (min) when the observed level drops below it
        new_b = st["boundary_level"] if rank(level) >= rank(st["boundary_level"]) else level
        tpath = str(data.get("transcript_path"))
        saved = True
        if (not exists or d["new_last_level"] != st["last_level"] or new_b != st["boundary_level"]
                or st.get("transcript_path") != tpath):  # DD-9 write cadence
            saved = save_state(sp, {"schema": SCHEMA_VERSION, "session_id": sid,
                                    "last_level": d["new_last_level"], "boundary_level": new_b,
                                    "last_tokens": m["tokens"], "transcript_path": tpath,
                                    "updated_at": now_iso()})
        if d["action"] in ("nudge", "would_nudge", "rearmed") or not saved:
            threshold = (cfg["criticalTokens"] if level == "critical"
                         else cfg["warnTokens"] if level == "warn" else None)
            row.update(level=level, tokens=m["tokens"], token_source=m["source"],
                       last_level=st["last_level"], threshold=threshold,
                       outcome="nudged" if d["action"] == "nudge" else d["action"],
                       error=None if saved else "state_write_failed")
            log_event("context_nudge", row)
        if d["action"] == "nudge":
            msg = render_advisory(level, m["tokens"], cfg)
            json_print({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                               "additionalContext": msg},
                        "systemMessage": msg})
        return 0
    except Exception as exc:  # noqa: BLE001 - fail-open contract
        try:
            row.update(outcome="error", error="unexpected:" + type(exc).__name__)
            log_event("context_nudge", row)
        except Exception:  # noqa: BLE001
            pass
        return 0


def reset_main(data):
    """SessionStart(compact): drop this session's dedup state and prune stale state files."""
    row = {"schema": SCHEMA_VERSION, "source": "reset", "mode": None, "session_id": None,
           "outcome": "error", "had_state": False, "error": None}
    try:
        data = data if isinstance(data, dict) else {}
        effective, row["mode"] = resolve_mode(load_mode().get("contextNudge"))
        if effective == "off" or data.get("source") != "compact":
            return 0
        sid = safe_session_id(data.get("session_id"))
        row["session_id"] = sid
        if sid is None:
            row["error"] = "bad_session_id"
            log_event("context_nudge", row)
            return 0
        state_dir = state_root() / STATE_DIRNAME
        sp = state_path(state_dir, sid)
        try:
            row["had_state"] = sp.exists()
            os.unlink(str(sp))
        except FileNotFoundError:
            pass
        cutoff = time.time() - STATE_PRUNE_AGE_S
        if state_dir.is_dir():
            for entry in state_dir.glob("*.json"):
                if entry.name.startswith("checkpoint-"):
                    continue
                try:
                    if entry.stat().st_mtime < cutoff:
                        entry.unlink()
                except OSError:
                    pass
        row.update(outcome="reset", error=None)
        log_event("context_nudge", row)
        return 0
    except Exception as exc:  # noqa: BLE001 - fail-open contract
        try:
            row.update(outcome="error", error="unexpected:" + type(exc).__name__)
            log_event("context_nudge", row)
        except Exception:  # noqa: BLE001
            pass
        return 0


def resolve_session(state_dir, arg_sid, env_sid, now):
    """(sid, state, source) per DD-12: arg > env > newest non-checkpoint state within 12 h.
    An explicit/env id whose state file is absent falls through. (None, None, None) if unresolved."""
    try:
        for source, raw in (("arg", arg_sid), ("env", env_sid)):
            sid = safe_session_id(raw)
            if sid is not None and state_path(state_dir, sid).is_file():
                return sid, load_state(state_path(state_dir, sid)), source
        best = None
        d = Path(state_dir)
        if d.is_dir():
            for entry in d.glob("*.json"):
                sid = entry.stem
                if entry.name.startswith("checkpoint-") or safe_session_id(sid) is None:
                    continue
                try:
                    mtime = entry.stat().st_mtime
                except OSError:
                    continue
                if now - mtime <= BOUNDARY_STATE_MAX_AGE_S and (best is None or mtime > best[0]):
                    best = (mtime, sid, entry)
        if best is not None:
            return best[1], load_state(best[2]), "mtime_fallback"
    except Exception:  # noqa: BLE001 - fail-open contract
        pass
    return None, None, None


def write_checkpoint(path, snapshot):
    """Atomic write of the phase-boundary checkpoint. Returns True on success."""
    return save_state(path, snapshot)


def _build_checkpoint(wf, phase, tokens, level, sid, cfg):
    """Snapshot dict via the PreCompact builders, or (None, error). Lazy import (DD-3)."""
    payload, path, perr = read_workflow_state(wf)
    if path is None:
        return None, "workflow_missing"
    if perr or not isinstance(payload, dict):
        return None, "workflow_unreadable"
    import craftflow_precompact_state as pcs
    try:
        digest = pcs._narrative_digest()
    except Exception:  # noqa: BLE001 - digest is best-effort
        digest = None
    usage = None
    if tokens is not None:
        window = cfg["assumedWindow"]
        usage = {"percent_full": round(tokens * 100.0 / window, 1), "total": tokens,
                 "model_context": window, "source": "transcript"}
    snap = pcs._build_snapshot(payload, "phase_boundary", usage, digest)
    snap.update(source="phase_boundary", phase=phase, context_tokens=tokens, context_level=level,
                session_id=sid)
    return snap, None


def boundary_main(args):
    """Router CLI: always prints exactly one JSON line, returns 0 (DD-11/12/13)."""
    res = boundary_result(outcome="error", error="unexpected")
    try:
        if args.project_root:
            os.environ["CLAUDE_PROJECT_DIR"] = args.project_root
        effective, logged = resolve_mode(load_mode().get("contextNudge"))
        if effective == "off":
            res = boundary_result(mode="off", outcome="off")
            return 0
        res = boundary_result(mode=logged, phase=args.phase, outcome="error")
        cfg, cerr = load_config(plugin_config_dir() / "context-nudge.json")
        if cerr:
            res = boundary_result(mode=logged, phase=args.phase, outcome="error", error=cerr)
            log_event("context_nudge", _boundary_row(res, args.wf))
            return 0
        error = None
        wf = safe_wf(args.wf)
        if wf is None:
            error = "bad_wf"
        state_dir = state_root() / STATE_DIRNAME
        sid, state, session_source = resolve_session(
            state_dir, args.session_id, os.environ.get("CLAUDE_CODE_SESSION_ID"), time.time())
        tokens, level = None, "none"
        if state is not None:
            m = current_context_tokens(state.get("transcript_path"))
            tokens = m["tokens"]
            if tokens is None:
                error = error or "remeasure_failed"
                last = state.get("last_tokens")
                tokens = last if type(last) is int and last > 0 else None
            level = classify(tokens, cfg["warnTokens"], cfg["criticalTokens"])
        checkpoint_path = None
        if wf is not None:
            try:  # a checkpoint failure must never suppress relay (E21)
                snap, cerr2 = _build_checkpoint(wf, args.phase, tokens, level, sid, cfg)
                target = state_dir / ("checkpoint-" + wf + ".json")
                if snap is not None and write_checkpoint(target, snap):
                    checkpoint_path = str(target)
                elif snap is not None:
                    cerr2 = "checkpoint_write_failed"
                error = error or cerr2
            except Exception:  # noqa: BLE001
                checkpoint_path = None
                error = error or "workflow_unreadable"
        b = decide_boundary(level, (state or {}).get("boundary_level", "none"), effective,
                            measured=tokens is not None)
        if b["relay"] and state is not None:
            state = dict(state)
            state["boundary_level"] = b["new_boundary_level"]
            state["updated_at"] = now_iso()
            save_state(state_path(state_dir, sid), state)
        threshold = (cfg["criticalTokens"] if level == "critical"
                     else cfg["warnTokens"] if level == "warn" else None)
        res = boundary_result(
            mode=logged, level=level if tokens is not None else None, tokens=tokens,
            threshold=threshold, assumed_window=cfg["assumedWindow"], relay=b["relay"],
            advisory=render_advisory(level, tokens, cfg) if b["relay"] else None,
            checkpoint_path=checkpoint_path, session_id=sid, session_source=session_source,
            phase=args.phase, outcome=b["outcome"], error=error)
        log_event("context_nudge", _boundary_row(res, args.wf))
        return 0
    except Exception as exc:  # noqa: BLE001 - fail-open contract
        res = boundary_result(phase=getattr(args, "phase", None), outcome="error",
                              error="unexpected:" + type(exc).__name__)
        return 0
    finally:
        print(json.dumps(res))


def _boundary_row(res, wf):
    """Log row for a boundary invocation; no 'event' key (DD-8)."""
    row = {k: res.get(k) for k in ("mode", "level", "tokens", "threshold", "relay", "checkpoint_path",
                                   "session_id", "session_source", "phase", "outcome", "error")}
    row.update(schema=SCHEMA_VERSION, source="boundary", wf=wf)
    return row


def _parse_args(argv):
    """(namespace, None) or (None, "bad_args"); argparse's SystemExit never escapes."""
    import argparse
    import contextlib
    import io
    parser = argparse.ArgumentParser(prog="craftflow_context_nudge", add_help=False)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--boundary", action="store_true")
    parser.add_argument("--wf")
    parser.add_argument("--phase")
    parser.add_argument("--project-root")
    parser.add_argument("--session-id")
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            return parser.parse_args(argv), None
    except SystemExit:
        return None, "bad_args"


def main(argv=None):
    """Dispatch hook / --reset / --boundary. Always returns 0 (fail-open)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        args, err = _parse_args(argv)
        if err:
            if "--boundary" in argv:
                print(json.dumps(boundary_result(outcome="error", error=err)))
            return 0
        if args.boundary:
            return boundary_main(args)
        if args.reset:
            return reset_main(load_input())
        return hook_main(load_input())
    except Exception:  # noqa: BLE001 - fail-open contract
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
