#!/usr/bin/env python3
"""Load + normalize `config/jev.json` -- the opt-in flag home for the optional
Jev (TypeSafe AI) routing + skill hint feature (`craftflow_jev_prompt_hint.py`).

Unlike `craftflow_hooklib.load_mode()` (which fail-CLOSES a security guard's
block/audit toggle to the safer, more restrictive posture on missing/corrupt
config), this loader fail-OPENS every unrecognized or missing value to `off`.
That is deliberate: this feature is a network call carrying user prompt text
to a third party. The unsafe direction here is "always call Jev by default,"
not "stay off." A missing or corrupt config, or an unrecognized enum value,
must never cause this feature to silently turn itself ON.

The committed `config/jev.json` in this repo is always `enabled: false`; a
unit test (`test_committed_config_is_disabled_by_default`) guards that so a
future edit cannot silently flip the shipped default.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

MODES = ("off", "audit", "advise")
CONSENT_STATUSES = ("unset", "granted", "declined")
DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "model": "jev-latest",
    "features": {"routingHint": "audit", "skillHint": "audit"},
    "thresholds": {"routing": 0.85, "skill": 0.7},
    "maxStateChars": 4000,
    "timeoutSeconds": 2.5,
    "consent": {"status": "unset", "ts": None},
}
Decision = Tuple[str, str]  # (key, decision-string)


def normalize(raw: Any) -> Tuple[Dict[str, Any], List[Decision]]:
    decisions: List[Decision] = []
    if not isinstance(raw, dict):
        return json.loads(json.dumps(DEFAULTS)), [("config", "config_unparseable")]
    cfg = json.loads(json.dumps(DEFAULTS))
    cfg["enabled"] = raw.get("enabled") is True
    if isinstance(raw.get("model"), str) and raw["model"].strip():
        cfg["model"] = raw["model"].strip()
    feats = raw.get("features") if isinstance(raw.get("features"), dict) else {}
    for key in ("routingHint", "skillHint"):
        value = feats.get(key, DEFAULTS["features"][key])
        if value in MODES:
            cfg["features"][key] = value
        else:
            cfg["features"][key] = "off"
            decisions.append((key, "off-unrecognized-config-value"))
    th = raw.get("thresholds") if isinstance(raw.get("thresholds"), dict) else {}
    for key in ("routing", "skill"):
        value = th.get(key, DEFAULTS["thresholds"][key])
        if isinstance(value, (int, float)) and not isinstance(value, bool) and 0.0 <= value <= 1.0:
            cfg["thresholds"][key] = float(value)
        else:
            decisions.append(("thresholds." + key, "config_unparseable"))
    for key, lo, hi in (("maxStateChars", 200, 150000), ("timeoutSeconds", 0.5, 4.0)):
        value = raw.get(key, DEFAULTS[key])
        if isinstance(value, (int, float)) and not isinstance(value, bool) and lo <= value <= hi:
            cfg[key] = value
        else:
            decisions.append((key, "config_unparseable"))
    consent_raw = raw.get("consent") if isinstance(raw.get("consent"), dict) else {}
    status = consent_raw.get("status", DEFAULTS["consent"]["status"])
    if status in CONSENT_STATUSES:
        cfg["consent"]["status"] = status
    else:
        cfg["consent"]["status"] = "unset"
        decisions.append(("consent.status", "config_unparseable"))
    ts = consent_raw.get("ts", DEFAULTS["consent"]["ts"])
    cfg["consent"]["ts"] = ts if (ts is None or (isinstance(ts, str) and ts.strip())) else None
    return cfg, decisions


def load_config(path: Path) -> Tuple[Dict[str, Any], List[Decision]]:
    if not path.exists():
        return json.loads(json.dumps(DEFAULTS)), []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return json.loads(json.dumps(DEFAULTS)), [("config", "config_unparseable")]
    return normalize(raw)


def api_key(env: Dict[str, str]) -> str:
    return (env.get("TYPESAFE_API_KEY") or "").strip()


def is_active(cfg: Dict[str, Any], env: Dict[str, str]) -> bool:
    return bool(cfg.get("enabled")) and bool(api_key(env))


def consent_status(cfg: Dict[str, Any]) -> str:
    consent = cfg.get("consent") if isinstance(cfg.get("consent"), dict) else {}
    status = consent.get("status")
    return status if status in CONSENT_STATUSES else "unset"
