#!/usr/bin/env python3
"""Tests for craftflow_jev_config.py.

Run: python3 tests/fixtures/test_craftflow_jev_config.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from craftflow_jev_config import DEFAULTS, normalize, load_config, MODES  # noqa: E402
from craftflow_jev_config import consent_status  # noqa: E402

_passes = 0
_errors: list[str] = []


def ok(name: str) -> None:
    global _passes
    _passes += 1
    print(f"  PASS: {name}")


def fail(name: str, reason: str) -> None:
    _errors.append(f"FAIL [{name}]: {reason}")
    print(f"  FAIL: {name}: {reason}")


def test_defaults_when_missing_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg, decisions = load_config(Path(tmp) / "jev.json")
        if cfg == DEFAULTS and decisions == []:
            ok("defaults when config file is missing")
        else:
            fail("defaults-when-missing-file", f"cfg={cfg!r} decisions={decisions!r}")


def test_unrecognized_feature_mode_degrades_to_off_with_distinct_decision() -> None:
    cfg, decisions = normalize({"enabled": True, "features": {"routingHint": "Advise", "skillHint": "audit"}})
    if (
        cfg["features"]["routingHint"] == "off"
        and cfg["features"]["skillHint"] == "audit"
        and ("routingHint", "off-unrecognized-config-value") in decisions
    ):
        ok("unrecognized feature mode degrades to off with distinct decision")
    else:
        fail("unrecognized-feature-mode", f"cfg={cfg!r} decisions={decisions!r}")


def test_non_dict_and_bad_thresholds_fall_back() -> None:
    cfg, decisions = normalize([1, 2, 3])
    if cfg != DEFAULTS or ("config", "config_unparseable") not in decisions:
        fail("non-dict-config", f"cfg={cfg!r} decisions={decisions!r}")
        return
    cfg2, _decisions2 = normalize({"thresholds": {"routing": "high", "skill": 1.7}})
    if cfg2["thresholds"] == DEFAULTS["thresholds"]:
        ok("non-dict config and bad thresholds fall back to defaults")
    else:
        fail("bad-thresholds-fall-back", f"cfg={cfg2!r}")


def test_committed_config_is_disabled_by_default() -> None:
    raw = json.loads((PLUGIN_ROOT / "config" / "jev.json").read_text())
    if raw["enabled"] is False and raw["features"] == {"routingHint": "audit", "skillHint": "audit"}:
        ok("committed config/jev.json is disabled by default")
    else:
        fail("committed-config-disabled", f"raw={raw!r}")


def test_is_active_requires_enabled_and_key() -> None:
    from craftflow_jev_config import is_active

    checks = (
        not is_active(DEFAULTS, {"TYPESAFE_API_KEY": "k"}),
        not is_active({**DEFAULTS, "enabled": True}, {}),
        not is_active({**DEFAULTS, "enabled": True}, {"TYPESAFE_API_KEY": "   "}),
        is_active({**DEFAULTS, "enabled": True}, {"TYPESAFE_API_KEY": "k"}),
    )
    if all(checks):
        ok("is_active requires both enabled and a non-blank key")
    else:
        fail("is-active-requires-enabled-and-key", f"checks={checks!r}")


def test_consent_defaults_when_missing_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg, decisions = load_config(Path(tmp) / "jev.json")
        if cfg.get("consent") == {"status": "unset", "ts": None} and decisions == []:
            ok("consent defaults to unset/null when config file is missing")
        else:
            fail("consent-defaults-when-missing-file", f"cfg={cfg!r} decisions={decisions!r}")


def test_unrecognized_consent_status_degrades_to_unset_with_distinct_decision() -> None:
    cfg, decisions = normalize({"consent": {"status": "yes", "ts": "2026-01-01T00:00:00Z"}})
    if (
        cfg["consent"]["status"] == "unset"
        and ("consent.status", "config_unparseable") in decisions
    ):
        ok("unrecognized consent.status degrades to unset with distinct decision")
    else:
        fail("unrecognized-consent-status", f"cfg={cfg!r} decisions={decisions!r}")


def test_committed_config_now_carries_unset_consent_by_default() -> None:
    raw = json.loads((PLUGIN_ROOT / "config" / "jev.json").read_text())
    if raw.get("consent") == {"status": "unset", "ts": None}:
        ok("committed config/jev.json carries unset consent by default")
    else:
        fail("committed-config-carries-unset-consent", f"raw={raw!r}")


def test_consent_status_helper_reads_nested_field() -> None:
    checks = (
        consent_status(DEFAULTS) == "unset",
        consent_status({**DEFAULTS, "consent": {"status": "granted", "ts": "x"}}) == "granted",
        consent_status({}) == "unset",  # missing key degrades to unset, never raises
    )
    if all(checks):
        ok("consent_status helper reads nested field with safe fallback")
    else:
        fail("consent-status-helper", f"checks={checks!r}")


def test_remediation_scope_defaults_off_with_085_threshold() -> None:
    cfg, decisions = load_config(Path("/nonexistent/jev.json"))
    if cfg["features"]["remediationScope"] == "off" and cfg["thresholds"]["remediationScope"] == 0.85 and decisions == []:
        ok("remediationScope defaults to off with 0.85 threshold")
    else:
        fail("remediation-scope-defaults", f"cfg={cfg!r}")


def test_unrecognized_remediation_scope_mode_degrades_to_off_with_distinct_decision() -> None:
    cfg, decisions = normalize({"features": {"remediationScope": "Advise"}})
    if cfg["features"]["remediationScope"] == "off" and ("remediationScope", "off-unrecognized-config-value") in decisions:
        ok("unrecognized remediationScope mode degrades to off with distinct decision")
    else:
        fail("unrecognized-remediation-scope-mode", f"cfg={cfg!r} decisions={decisions!r}")


def test_bad_remediation_scope_threshold_falls_back_to_default() -> None:
    cfg, decisions = normalize({"thresholds": {"remediationScope": "high"}})
    if cfg["thresholds"]["remediationScope"] == 0.85 and ("thresholds.remediationScope", "config_unparseable") in decisions:
        ok("bad remediationScope threshold falls back to 0.85 default")
    else:
        fail("bad-remediation-scope-threshold", f"cfg={cfg!r} decisions={decisions!r}")


def main() -> int:
    print("test_craftflow_jev_config: running")
    print(f"  (MODES = {MODES})")
    test_defaults_when_missing_file()
    test_unrecognized_feature_mode_degrades_to_off_with_distinct_decision()
    test_non_dict_and_bad_thresholds_fall_back()
    test_committed_config_is_disabled_by_default()
    test_is_active_requires_enabled_and_key()
    test_consent_defaults_when_missing_file()
    test_unrecognized_consent_status_degrades_to_unset_with_distinct_decision()
    test_committed_config_now_carries_unset_consent_by_default()
    test_consent_status_helper_reads_nested_field()
    test_remediation_scope_defaults_off_with_085_threshold()
    test_unrecognized_remediation_scope_mode_degrades_to_off_with_distinct_decision()
    test_bad_remediation_scope_threshold_falls_back_to_default()

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
