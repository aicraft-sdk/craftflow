#!/usr/bin/env python3
"""Tests for craftflow_jev_client.py.

Run: python3 tests/fixtures/test_craftflow_jev_client.py

All network I/O is mocked via mock.patch("craftflow_jev_client._urlopen").
The client must never touch the real network in these tests.
"""
from __future__ import annotations

import contextlib
import http.client
import http.server
import json
import os
import signal
import socket
import sys
import tempfile
import threading
import time
import urllib.error
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from craftflow_jev_client import call, ENDPOINT, RETRY_STATUSES  # noqa: E402

_passes = 0
_errors: list[str] = []

SENTINEL_KEY = "k-123"


def ok(name: str) -> None:
    global _passes
    _passes += 1
    print(f"  PASS: {name}")


def fail(name: str, reason: str) -> None:
    _errors.append(f"FAIL [{name}]: {reason}")
    print(f"  FAIL: {name}: {reason}")


class _FakeResponse:
    """Minimal context-manager stand-in for http.client.HTTPResponse."""

    def __init__(self, status: int, body_bytes: bytes) -> None:
        self.status = status
        self._body = body_bytes

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def fake_response(status: int, body_bytes: bytes) -> _FakeResponse:
    return _FakeResponse(status, body_bytes)


class _FakeResponseReadRaises:
    """Context-manager stand-in whose .read() raises a mid-response failure
    (connection succeeded, but the body could not be fully read)."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def read(self) -> bytes:
        raise self._exc

    def __enter__(self) -> "_FakeResponseReadRaises":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def _http_error(code: int, msg: str = "error") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(ENDPOINT, code, msg, None, None)


class _Clock:
    """Shared mutable monotonic clock so fake urlopen/sleep can advance time."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, dt: float) -> None:
        self.now += dt


def test_success_returns_answers_and_usage_and_sends_bearer_header() -> None:
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["req"] = req
        captured["timeout"] = timeout
        captured["body"] = json.loads(req.data.decode("utf-8"))
        body = json.dumps({"answers": {"workflow": "BUILD"}, "usage": {"tokens": 10}, "model": "jev-1.12"}).encode()
        return fake_response(200, body)

    with mock.patch("craftflow_jev_client._urlopen", side_effect=fake_urlopen):
        result = call(
            {"prompt": "hi"},
            {"workflow": {"criteria": {}}},
            api_key=SENTINEL_KEY,
            model="jev-1.12",
            timeout=2.5,
            cache_dir=None,
        )

    req = captured.get("req")
    body = captured.get("body", {})
    if (
        result is not None
        and result["answers"] == {"workflow": "BUILD"}
        and result["usage"] == {"tokens": 10}
        and req is not None
        and req.get_header("Authorization") == f"Bearer {SENTINEL_KEY}"
        and req.full_url == ENDPOINT
        and set(body) == {"state", "model", "questions"}
    ):
        ok("success returns answers/usage and sends Bearer header")
    else:
        fail("success-bearer-header", f"result={result!r} req={req!r} body={body!r}")


def test_401_returns_none_without_retry_and_logs_status_only() -> None:
    calls: list = []
    logged: list = []

    def fake_urlopen(req, timeout=None):
        calls.append(timeout)
        raise _http_error(401, "Unauthorized: bad key k-123")

    def fake_log(name, payload):
        logged.append((name, payload))

    with mock.patch("craftflow_jev_client._urlopen", side_effect=fake_urlopen):
        result = call(
            {}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=None, log=fake_log
        )

    payload_dump = json.dumps(logged, default=str)
    if (
        result is None
        and len(calls) == 1
        and logged
        and logged[0][1].get("status") == 401
        and SENTINEL_KEY not in payload_dump
    ):
        ok("401 returns None without retry and logs status only")
    else:
        fail("401-no-retry", f"result={result!r} calls={calls!r} logged={logged!r}")


def test_429_then_success_retries_once_with_backoff() -> None:
    calls: list = []
    slept: list = []

    def fake_urlopen(req, timeout=None):
        calls.append(timeout)
        if len(calls) == 1:
            raise _http_error(429, "Too Many Requests")
        body = json.dumps({"answers": {"a": 1}, "usage": {}, "model": "jev-1.12"}).encode()
        return fake_response(200, body)

    with mock.patch("craftflow_jev_client._urlopen", side_effect=fake_urlopen), mock.patch(
        "craftflow_jev_client.time.sleep", side_effect=lambda s: slept.append(s)
    ):
        result = call({}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=None)

    if result is not None and len(calls) == 2 and slept == [0.3]:
        ok("429 then success retries once with 0.3s backoff")
    else:
        fail("429-retry-once", f"result={result!r} calls={calls!r} slept={slept!r}")


def test_529_retries_but_500_does_not() -> None:
    calls_529: list = []

    def fake_urlopen_529(req, timeout=None):
        calls_529.append(timeout)
        if len(calls_529) == 1:
            raise _http_error(529, "Overloaded")
        body = json.dumps({"answers": {}, "usage": {}, "model": "jev-1.12"}).encode()
        return fake_response(200, body)

    with mock.patch("craftflow_jev_client._urlopen", side_effect=fake_urlopen_529), mock.patch(
        "craftflow_jev_client.time.sleep", return_value=None
    ):
        result_529 = call({}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=None)

    calls_500: list = []

    def fake_urlopen_500(req, timeout=None):
        calls_500.append(timeout)
        raise _http_error(500, "Server Error")

    with mock.patch("craftflow_jev_client._urlopen", side_effect=fake_urlopen_500):
        result_500 = call({}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=None)

    if result_529 is not None and len(calls_529) == 2 and result_500 is None and len(calls_500) == 1:
        ok("529 retries but 500 does not")
    else:
        fail(
            "529-retries-500-does-not",
            f"result_529={result_529!r} calls_529={calls_529!r} result_500={result_500!r} calls_500={calls_500!r}",
        )


def test_retry_skipped_when_budget_below_one_second() -> None:
    clock = _Clock()
    calls: list = []

    def fake_urlopen(req, timeout=None):
        calls.append(timeout)
        clock.advance(3.2)
        raise _http_error(429, "Too Many Requests")

    with mock.patch("craftflow_jev_client._urlopen", side_effect=fake_urlopen), mock.patch(
        "craftflow_jev_client.time.monotonic", side_effect=clock.monotonic
    ):
        result = call({}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=None)

    if result is None and len(calls) == 1:
        ok("retry skipped when remaining budget below 1.0s")
    else:
        fail("retry-skipped-low-budget", f"result={result!r} calls={calls!r}")


def test_second_attempt_timeout_never_exceeds_remaining_budget() -> None:
    # Finding 5: the first attempt must be a RETRYABLE failure, i.e. a slow HTTPError(429),
    # not socket.timeout.
    clock = _Clock()
    calls: list = []

    def fake_urlopen(req, timeout=None):
        calls.append(timeout)
        if len(calls) == 1:
            clock.advance(2.5)
            raise _http_error(429, "Too Many Requests")
        body = json.dumps({"answers": {}, "usage": {}, "model": "jev-1.12"}).encode()
        return fake_response(200, body)

    def fake_sleep(seconds):
        clock.advance(seconds)

    with mock.patch("craftflow_jev_client._urlopen", side_effect=fake_urlopen), mock.patch(
        "craftflow_jev_client.time.monotonic", side_effect=clock.monotonic
    ), mock.patch("craftflow_jev_client.time.sleep", side_effect=fake_sleep):
        result = call({}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=None)

    second_timeout = calls[1] if len(calls) > 1 else None
    if (
        result is not None
        and len(calls) == 2
        and second_timeout is not None
        and 1.0 <= second_timeout <= 1.2 + 1e-9
    ):
        ok("second attempt timeout never exceeds remaining budget (<=1.2, >=1.0)")
    else:
        fail("second-attempt-timeout-budget", f"result={result!r} calls={calls!r}")


def test_socket_timeout_is_not_retried() -> None:
    calls: list = []
    logged: list = []

    def fake_urlopen(req, timeout=None):
        calls.append(timeout)
        raise socket.timeout("timed out")

    def fake_log(name, payload):
        logged.append((name, payload))

    with mock.patch("craftflow_jev_client._urlopen", side_effect=fake_urlopen):
        result = call({}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=None, log=fake_log)

    if (
        result is None
        and len(calls) == 1
        and logged
        and logged[0][1].get("status") is None
        and logged[0][1].get("error") == "timeout"
    ):
        ok("socket.timeout is not retried")
    else:
        fail("socket-timeout-not-retried", f"result={result!r} calls={calls!r} logged={logged!r}")


def test_timeout_seconds_4_leaves_no_retry_budget() -> None:
    clock = _Clock()
    calls: list = []
    logged: list = []

    def fake_urlopen(req, timeout=None):
        calls.append(timeout)
        clock.advance(4.0)
        raise _http_error(429, "Too Many Requests")

    def fake_log(name, payload):
        logged.append((name, payload))

    with mock.patch("craftflow_jev_client._urlopen", side_effect=fake_urlopen), mock.patch(
        "craftflow_jev_client.time.monotonic", side_effect=clock.monotonic
    ):
        result = call({}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=4.0, cache_dir=None, log=fake_log)

    if (
        result is None
        and len(calls) == 1
        and logged
        and logged[0][1].get("attempt") == 1
    ):
        ok("timeoutSeconds=4.0 leaves no retry budget (no retry occurs)")
    else:
        fail("timeout-4-no-retry-budget", f"result={result!r} calls={calls!r} logged={logged!r}")


def test_malformed_json_and_missing_answers_return_none() -> None:
    def fake_urlopen_malformed(req, timeout=None):
        return fake_response(200, b"not-json{")

    with mock.patch("craftflow_jev_client._urlopen", side_effect=fake_urlopen_malformed):
        result_malformed = call({}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=None)

    def fake_urlopen_missing(req, timeout=None):
        body = json.dumps({"usage": {}, "model": "jev-1.12"}).encode()
        return fake_response(200, body)

    with mock.patch("craftflow_jev_client._urlopen", side_effect=fake_urlopen_missing):
        result_missing = call({}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=None)

    if result_malformed is None and result_missing is None:
        ok("malformed JSON and missing 'answers' both return None")
    else:
        fail("malformed-missing-answers", f"result_malformed={result_malformed!r} result_missing={result_missing!r}")


def test_cache_hit_skips_network_and_marks_cache_hit() -> None:
    calls: list = []

    def fake_urlopen(req, timeout=None):
        calls.append(1)
        body = json.dumps({"answers": {"a": 1}, "usage": {"tokens": 5}, "model": "jev-1.12"}).encode()
        return fake_response(200, body)

    with tempfile.TemporaryDirectory() as tmp:
        cache_dir = Path(tmp)
        with mock.patch("craftflow_jev_client._urlopen", side_effect=fake_urlopen):
            result1 = call(
                {"prompt": "same"}, {"workflow": {}}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=cache_dir
            )
            result2 = call(
                {"prompt": "same"}, {"workflow": {}}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=cache_dir
            )

    if (
        result1 is not None
        and result1["cache_hit"] is False
        and result2 is not None
        and result2["cache_hit"] is True
        and result2["answers"] == {"a": 1}
        and len(calls) == 1
    ):
        ok("cache hit skips network on second call with same inputs")
    else:
        fail("cache-hit-skips-network", f"result1={result1!r} result2={result2!r} calls={calls!r}")


def test_cache_entry_never_contains_state_or_key() -> None:
    def fake_urlopen(req, timeout=None):
        body = json.dumps({"answers": {"a": 1}, "usage": {}, "model": "jev-1.12"}).encode()
        return fake_response(200, body)

    with tempfile.TemporaryDirectory() as tmp:
        cache_dir = Path(tmp)
        with mock.patch("craftflow_jev_client._urlopen", side_effect=fake_urlopen):
            call(
                {"prompt": "secret-state-value"},
                {"workflow": {}},
                api_key=SENTINEL_KEY,
                model="jev-1.12",
                timeout=2.5,
                cache_dir=cache_dir,
            )
        cache_files = list(cache_dir.glob("*.json"))
        contents = [f.read_text(encoding="utf-8") for f in cache_files]

    joined = "\n".join(contents)
    if cache_files and "state" not in joined and SENTINEL_KEY not in joined and "secret-state-value" not in joined:
        ok("cache entry never contains state or key")
    else:
        fail("cache-entry-no-state-or-key", f"cache_files={cache_files!r} contents={contents!r}")


def test_key_never_appears_in_exception_or_log_payloads() -> None:
    logged: list = []

    def fake_log(name, payload):
        logged.append((name, payload))

    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError(f"connection refused for key {SENTINEL_KEY}")

    with tempfile.TemporaryDirectory() as tmp:
        cache_dir = Path(tmp)
        with mock.patch("craftflow_jev_client._urlopen", side_effect=fake_urlopen):
            result = call(
                {}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=cache_dir, log=fake_log
            )
        state_root_text = "\n".join(
            p.read_text(encoding="utf-8") for p in cache_dir.rglob("*") if p.is_file()
        )

    payload_dump = json.dumps(logged, default=str)
    if (
        result is None
        and SENTINEL_KEY not in payload_dump
        and SENTINEL_KEY not in state_root_text
    ):
        ok("key never appears in exception or log payloads")
    else:
        fail("key-never-in-logs", f"result={result!r} logged={logged!r} state_root_text={state_root_text!r}")


def test_incomplete_read_during_response_body_returns_none() -> None:
    logged: list = []

    def fake_log(name, payload):
        logged.append((name, payload))

    def fake_urlopen(req, timeout=None):
        return _FakeResponseReadRaises(http.client.IncompleteRead(b"", 10))

    with mock.patch("craftflow_jev_client._urlopen", side_effect=fake_urlopen):
        result = call({}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=None, log=fake_log)

    payload_dump = json.dumps(logged, default=str)
    if (
        result is None
        and logged
        and logged[0][1].get("decision") == "jev_call_failed"
        and logged[0][1].get("error") == "IncompleteRead"
        and SENTINEL_KEY not in payload_dump
    ):
        ok("IncompleteRead during response body read returns None")
    else:
        fail("incomplete-read-returns-none", f"result={result!r} logged={logged!r}")


def test_connection_reset_during_response_body_returns_none() -> None:
    logged: list = []

    def fake_log(name, payload):
        logged.append((name, payload))

    def fake_urlopen(req, timeout=None):
        return _FakeResponseReadRaises(ConnectionResetError("connection reset"))

    with mock.patch("craftflow_jev_client._urlopen", side_effect=fake_urlopen):
        result = call({}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=None, log=fake_log)

    payload_dump = json.dumps(logged, default=str)
    if (
        result is None
        and logged
        and logged[0][1].get("decision") == "jev_call_failed"
        and logged[0][1].get("error") == "ConnectionResetError"
        and SENTINEL_KEY not in payload_dump
    ):
        ok("ConnectionResetError during response body read returns None")
    else:
        fail("connection-reset-returns-none", f"result={result!r} logged={logged!r}")


def test_memory_error_during_response_read_returns_none() -> None:
    logged: list = []

    def fake_log(name, payload):
        logged.append((name, payload))

    def fake_urlopen(req, timeout=None):
        return _FakeResponseReadRaises(MemoryError())

    with mock.patch("craftflow_jev_client._urlopen", side_effect=fake_urlopen):
        result = call({}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=None, log=fake_log)

    payload_dump = json.dumps(logged, default=str)
    if (
        result is None
        and logged
        and logged[0][1].get("decision") == "jev_call_failed"
        and logged[0][1].get("error") == "MemoryError"
        and SENTINEL_KEY not in payload_dump
    ):
        ok("MemoryError during response body read returns None")
    else:
        fail("memory-error-returns-none", f"result={result!r} logged={logged!r}")


def test_recursion_error_from_hostile_json_returns_none() -> None:
    logged: list = []

    def fake_log(name, payload):
        logged.append((name, payload))

    hostile_body = (b"[" * 100000) + (b"]" * 100000)

    def fake_urlopen(req, timeout=None):
        return fake_response(200, hostile_body)

    with mock.patch("craftflow_jev_client._urlopen", side_effect=fake_urlopen):
        result = call({}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=None, log=fake_log)

    if (
        result is None
        and logged
        and logged[0][1].get("decision") == "jev_call_failed"
        and logged[0][1].get("error") == "RecursionError"
    ):
        ok("RecursionError from real json.loads on hostile body returns None")
    else:
        fail("recursion-error-returns-none", f"result={result!r} logged={logged!r}")


def test_429_retry_then_incomplete_read_logs_status_none() -> None:
    # Attempt 1: retryable 429 HTTPError sets a stale `status`. Attempt 2:
    # a non-HTTPError failure (IncompleteRead) must NOT inherit that stale
    # status -- the logged payload must report status=None, not 429.
    calls: list = []
    logged: list = []

    def fake_urlopen(req, timeout=None):
        calls.append(timeout)
        if len(calls) == 1:
            raise _http_error(429, "Too Many Requests")
        return _FakeResponseReadRaises(http.client.IncompleteRead(b"", 10))

    def fake_log(name, payload):
        logged.append((name, payload))

    with mock.patch("craftflow_jev_client._urlopen", side_effect=fake_urlopen), mock.patch(
        "craftflow_jev_client.time.sleep", return_value=None
    ):
        result = call({}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=None, log=fake_log)

    if (
        result is None
        and len(calls) == 2
        and len(logged) == 1
        and logged[0][1].get("status") is None
        and logged[0][1].get("error") == "IncompleteRead"
    ):
        ok("429 retry then IncompleteRead logs status=None (not stale 429)")
    else:
        fail("429-retry-then-incomplete-read-status-none", f"result={result!r} calls={calls!r} logged={logged!r}")


def _call_capturing_url(env=None, config_path=None, home_env=None, passwd_home=None, with_logs=False):
    """Run call() against a fake urlopen; return (url, logged decisions[, full payloads])."""
    captured: dict = {}
    logged: list = []
    payloads: list = []

    def fake_urlopen(req, timeout=None):
        captured["full_url"] = req.full_url
        return fake_response(200, json.dumps({"answers": {}, "usage": {}, "model": "jev-1.12"}).encode())

    def fake_log(name, payload):
        logged.append(payload.get("decision"))
        payloads.append(payload)

    patched_env = dict(env or {})
    if home_env is not None:
        patched_env["HOME"] = home_env
    kwargs = {} if config_path is None else {"endpoint_config_path": config_path}
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.dict("os.environ", patched_env, clear=False))
        stack.enter_context(mock.patch("craftflow_jev_client._urlopen", side_effect=fake_urlopen))
        if passwd_home is not None:
            stack.enter_context(mock.patch("craftflow_jev_client._passwd_home", return_value=passwd_home))
        call({}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=None, log=fake_log, **kwargs)
    url = captured.get("full_url", "")
    if with_logs:
        return url, logged, payloads
    return url, logged


def _write_endpoint_file(tmp: str, content: str) -> str:
    path = os.path.join(tmp, "jev-endpoint.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


def test_env_endpoint_override_is_ignored() -> None:
    url, _ = _call_capturing_url(env={"CRAFTFLOW_JEV_ENDPOINT": "http://127.0.0.1:9/"})
    if url == ENDPOINT:
        ok("env CRAFTFLOW_JEV_ENDPOINT (loopback) is ignored")
    else:
        fail("env-override-ignored", f"url={url!r}")


def test_endpoint_file_loopback_honored() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_endpoint_file(tmp, json.dumps({"endpoint": "http://127.0.0.1:9/v1"}))
        url, _ = _call_capturing_url(config_path=path)
    if url == "http://127.0.0.1:9/v1":
        ok("endpoint file with loopback URL is honored")
    else:
        fail("endpoint-file-loopback", f"url={url!r}")


def test_endpoint_file_non_loopback_ignored_and_logged() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_endpoint_file(tmp, json.dumps({"endpoint": "https://evil.example/"}))
        url, decisions = _call_capturing_url(config_path=path)
    if url == ENDPOINT and "endpoint_override_ignored" in decisions:
        ok("endpoint file with non-loopback URL ignored and logged")
    else:
        fail("endpoint-file-non-loopback", f"url={url!r} decisions={decisions!r}")


def test_endpoint_file_malformed_or_missing_falls_back() -> None:
    bad = ["not json", "[]", json.dumps({"endpoint": 5}), json.dumps({}), ""]
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for content in bad:
            results.append(_call_capturing_url(config_path=_write_endpoint_file(tmp, content))[0])
        results.append(_call_capturing_url(config_path=os.path.join(tmp, "absent.json"))[0])
        results.append(_call_capturing_url(config_path=tmp)[0])  # directory: unreadable
    if all(u == ENDPOINT for u in results):
        ok("malformed/missing/unreadable endpoint file falls back to ENDPOINT")
    else:
        fail("endpoint-file-malformed", f"urls={results!r}")


def test_home_env_is_not_used_for_endpoint_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = os.path.join(tmp, ".claude", "craftflow")
        os.makedirs(d)
        _write_endpoint_file(d, json.dumps({"endpoint": "http://127.0.0.1:9/hijack"}))
        with mock.patch("craftflow_jev_client._passwd_home", return_value=os.path.join(tmp, "nohome")):
            url, _ = _call_capturing_url(home_env=tmp)
    if url == ENDPOINT:
        ok("$HOME env is not used to locate the endpoint file (passwd home is)")
    else:
        fail("home-env-not-used", f"url={url!r}")


def test_default_endpoint_path_derives_from_passwd_home() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = os.path.join(tmp, ".claude", "craftflow")
        os.makedirs(d)
        _write_endpoint_file(d, json.dumps({"endpoint": "http://localhost:9/p"}))
        with mock.patch("craftflow_jev_client._passwd_home", return_value=tmp):
            url, _ = _call_capturing_url()
    if url == "http://localhost:9/p":
        ok("default endpoint file path derives from passwd home")
    else:
        fail("default-path-passwd-home", f"url={url!r}")


def test_endpoint_file_not_read_on_cache_hit() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp) / "cache"
        cache.mkdir()
        with mock.patch("craftflow_jev_client._urlopen", side_effect=lambda r, timeout=None: fake_response(
            200, json.dumps({"answers": {"a": 1}, "usage": {}, "model": "jev-1.12"}).encode()
        )):
            call({}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=cache)
        with mock.patch("craftflow_jev_client._read_endpoint_file", side_effect=AssertionError("read")) as rd:
            r = call({}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=cache)
    if r is not None and r["cache_hit"] is True and rd.call_count == 0:
        ok("endpoint file is not read on a cache hit")
    else:
        fail("endpoint-file-cache-hit", f"r={r!r} calls={rd.call_count}")


def test_fifo_endpoint_file_does_not_hang_and_falls_back() -> None:
    def on_alarm(signum, frame):
        raise TimeoutError("endpoint file read hung on FIFO")

    old = signal.signal(signal.SIGALRM, on_alarm)
    url, payloads = None, []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            fifo = os.path.join(tmp, "jev-endpoint.json")
            os.mkfifo(fifo)
            signal.alarm(5)
            try:
                url, _, payloads = _call_capturing_url(config_path=fifo, with_logs=True)
            finally:
                signal.alarm(0)
    except TimeoutError as exc:
        fail("fifo-no-hang", str(exc))
        return
    finally:
        signal.signal(signal.SIGALRM, old)
    events = [p for p in payloads if p.get("decision") == "endpoint_file_unreadable"]
    if url == ENDPOINT and len(events) == 1 and events[0].get("error") == "not_regular_file":
        ok("FIFO endpoint file does not hang; falls back and logs endpoint_file_unreadable")
    else:
        fail("fifo-no-hang", f"url={url!r} payloads={payloads!r}")


def test_oversized_endpoint_file_rejected_and_small_honored() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        big = json.dumps({"endpoint": "http://127.0.0.1:9/v1", "pad": "x" * 5000})
        url_big, _, payloads = _call_capturing_url(config_path=_write_endpoint_file(tmp, big), with_logs=True)
        small = json.dumps({"endpoint": "http://127.0.0.1:9/v1"})
        url_small, _ = _call_capturing_url(config_path=_write_endpoint_file(tmp, small))
    events = [p for p in payloads if p.get("decision") == "endpoint_file_unreadable"]
    if (
        url_big == ENDPOINT
        and len(events) == 1
        and events[0].get("error") == "too_large"
        and url_small == "http://127.0.0.1:9/v1"
    ):
        ok("oversized (>4096) endpoint file rejected; small file honored")
    else:
        fail("endpoint-file-size-cap", f"big={url_big!r} small={url_small!r} payloads={payloads!r}")


def test_unreadable_endpoint_file_logs_exactly_one_event_without_contents() -> None:
    secret = "SUPERSECRETVALUE"
    cases = {
        "malformed": ("{" + secret, "JSONDecodeError"),
        "non_dict": (json.dumps([secret]), "not_object"),
        "non_string": (json.dumps({"endpoint": 5, "k": secret}), "endpoint_not_string"),
    }
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        for label, (content, expected_token) in cases.items():
            path = _write_endpoint_file(tmp, content)
            url, _, payloads = _call_capturing_url(config_path=path, with_logs=True)
            events = [p for p in payloads if p.get("decision") == "endpoint_file_unreadable"]
            dump = json.dumps(payloads, default=str)
            if not (
                url == ENDPOINT
                and len(events) == 1
                and events[0].get("error") == expected_token
                and events[0].get("event") == "jev_call"
                and secret not in dump
            ):
                problems.append((label, url, payloads))
        directory = _call_capturing_url(config_path=tmp, with_logs=True)
        dir_events = [p for p in directory[2] if p.get("decision") == "endpoint_file_unreadable"]
        if len(dir_events) != 1:
            problems.append(("directory", directory))
        missing = _call_capturing_url(config_path=os.path.join(tmp, "absent.json"), with_logs=True)
        if missing[2]:
            problems.append(("missing-logs-nothing", missing))
    if not problems:
        ok("unreadable endpoint file logs exactly one event, no contents; missing file logs nothing")
    else:
        fail("endpoint-file-unreadable-logging", f"problems={problems!r}")


def test_unresolved_passwd_home_logs_endpoint_home_unresolved() -> None:
    with mock.patch("craftflow_jev_client._passwd_home", return_value=None):
        url, _, payloads = _call_capturing_url(with_logs=True)
    events = [p for p in payloads if p.get("decision") == "endpoint_home_unresolved"]
    if url == ENDPOINT and len(events) == 1 and events[0].get("event") == "jev_call":
        ok("unresolved passwd home logs endpoint_home_unresolved and uses ENDPOINT")
    else:
        fail("home-unresolved-logged", f"url={url!r} payloads={payloads!r}")


def test_invalid_override_urls_rejected() -> None:
    bad = [
        "file://localhost/x",
        "http://user:pw@127.0.0.1/",
        "http://evil.com\\@127.0.0.1/",
        "http://127.0.0.1@evil.com",
        "http://127.0.0.1/ x",
        "http://127.0.0.1/\tx",
        "http://127.0.0.1/\x01x",
        "ftp://127.0.0.1/",
    ]
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        for u in bad:
            path = _write_endpoint_file(tmp, json.dumps({"endpoint": u}))
            url, decisions = _call_capturing_url(config_path=path)
            if url != ENDPOINT or "endpoint_override_ignored" not in decisions:
                problems.append((u, url, decisions))
        for good in ("http://127.0.0.1:9/v1", "https://localhost:9/v1", "http://[::1]:9/v1"):
            path = _write_endpoint_file(tmp, json.dumps({"endpoint": good}))
            url, _ = _call_capturing_url(config_path=path)
            if url != good:
                problems.append(("good", good, url))
    if not problems:
        ok("invalid override URLs rejected (scheme/userinfo/backslash/whitespace/control); valid loopback honored")
    else:
        fail("override-url-validation", f"problems={problems!r}")


class _Recorder(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def do_POST(self):
        self.server.seen.append(dict(self.headers))
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        location = getattr(self.server, "redirect_to", None)
        if location:
            self.send_response(307)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            body = b'{"answers": {"x": 1}}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


def _start_server(redirect_to=None):
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Recorder)
    srv.seen = []
    srv.redirect_to = redirect_to
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_redirects_are_not_followed_and_key_not_forwarded() -> None:
    second = _start_server()
    first = _start_server(redirect_to=f"http://127.0.0.1:{second.server_address[1]}/stolen")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_endpoint_file(
                tmp, json.dumps({"endpoint": f"http://127.0.0.1:{first.server_address[1]}/v1"})
            )
            with mock.patch.dict("os.environ", {"no_proxy": "127.0.0.1", "NO_PROXY": "127.0.0.1"}):
                result = call(
                    {}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=None,
                    log=lambda n, p: None, endpoint_config_path=path,
                )
    finally:
        first.shutdown()
        second.shutdown()
    leaked = [h for h in second.seen if "Authorization" in h]
    if result is None and len(first.seen) == 1 and second.seen == [] and not leaked:
        ok("3xx redirect fails the call; second loopback listener never sees Authorization")
    else:
        fail("redirect-not-followed", f"result={result!r} first={len(first.seen)} second={second.seen!r}")


def test_non_json_serializable_state_returns_none() -> None:
    logged: list = []

    def fake_log(name, payload):
        logged.append((name, payload))

    # A `set` is not JSON-serializable -- json.dumps() raises TypeError.
    bad_state = {"thing": {1, 2, 3}}

    result_no_cache = call(
        bad_state, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=None, log=fake_log
    )
    no_cache_payload_dump = json.dumps(logged, default=str)
    no_cache_ok = (
        result_no_cache is None
        and logged
        and logged[0][1].get("decision") == "jev_call_failed"
        and logged[0][1].get("error") == "TypeError"
        and SENTINEL_KEY not in no_cache_payload_dump
    )

    logged.clear()
    with tempfile.TemporaryDirectory() as tmp:
        result_with_cache = call(
            bad_state,
            {},
            api_key=SENTINEL_KEY,
            model="jev-1.12",
            timeout=2.5,
            cache_dir=Path(tmp),
            log=fake_log,
        )
        cache_files = list(Path(tmp).glob("*.json"))
    with_cache_payload_dump = json.dumps(logged, default=str)
    with_cache_ok = (
        result_with_cache is None
        and not cache_files
        and logged
        and logged[0][1].get("decision") == "jev_call_failed"
        and logged[0][1].get("error") == "TypeError"
        and SENTINEL_KEY not in with_cache_payload_dump
    )

    if no_cache_ok and with_cache_ok:
        ok("non-JSON-serializable state returns None (cache_dir=None and cache_dir=tmp)")
    else:
        fail(
            "non-json-serializable-state",
            f"result_no_cache={result_no_cache!r} result_with_cache={result_with_cache!r} logged={logged!r}",
        )


def test_non_numeric_timeout_returns_none() -> None:
    logged: list = []

    def fake_log(name, payload):
        logged.append((name, payload))

    def fake_urlopen(req, timeout=None):
        raise AssertionError("network should not be reached when timeout is invalid")

    with mock.patch("craftflow_jev_client._urlopen", side_effect=fake_urlopen):
        result = call(
            {}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=None, cache_dir=None, log=fake_log
        )

    payload_dump = json.dumps(logged, default=str)
    if (
        result is None
        and logged
        and logged[0][1].get("decision") == "jev_call_failed"
        and logged[0][1].get("error") == "TypeError"
        and SENTINEL_KEY not in payload_dump
    ):
        ok("non-numeric timeout (None) returns None instead of raising")
    else:
        fail("non-numeric-timeout", f"result={result!r} logged={logged!r}")


def test_time_monotonic_failure_returns_none() -> None:
    logged: list = []

    def fake_log(name, payload):
        logged.append((name, payload))

    def fake_monotonic():
        raise OSError("clock unavailable")

    def fake_urlopen(req, timeout=None):
        raise AssertionError("network should not be reached when the clock is broken")

    try:
        with mock.patch("craftflow_jev_client.time.monotonic", side_effect=fake_monotonic), mock.patch(
            "craftflow_jev_client._urlopen", side_effect=fake_urlopen
        ):
            result = call(
                {}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=None, log=fake_log
            )
    except Exception as exc:  # pragma: no cover - only raised pre-refactor
        fail("time-monotonic-failure", f"call() raised {type(exc).__name__}: {exc} instead of returning None")
        return

    if result is None:
        ok("time.monotonic failure returns None")
    else:
        fail("time-monotonic-failure", f"result={result!r}")


def test_hostile_log_callable_never_prevents_none_return() -> None:
    def hostile_log(name, payload):
        raise RuntimeError("hostile log always raises")

    def fake_urlopen(req, timeout=None):
        raise _http_error(401, "Unauthorized")

    try:
        with mock.patch("craftflow_jev_client._urlopen", side_effect=fake_urlopen):
            result = call(
                {}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=None, log=hostile_log
            )
    except Exception as exc:  # pragma: no cover - only raised pre-refactor
        fail("hostile-log-callable", f"call() raised {type(exc).__name__}: {exc} instead of returning None")
        return

    if result is None:
        ok("hostile log callable never prevents None return")
    else:
        fail("hostile-log-callable", f"result={result!r}")


def test_default_total_budget_matches_existing_4s_constant_baseline() -> None:
    # Baseline: documents existing behavior under the unparameterized 4.0s
    # constant -- a fast (1.0s) 429 failure leaves 3.0s remaining, well above
    # the 1.3s needed for a retry to fire. Not the RED step; exists so the
    # override test below has a same-timing baseline to diff against.
    clock = _Clock()
    calls: list = []

    def fake_urlopen(req, timeout=None):
        calls.append(timeout)
        if len(calls) == 1:
            clock.advance(1.0)
            raise _http_error(429, "Too Many Requests")
        body = json.dumps({"answers": {}, "usage": {}, "model": "jev-1.12"}).encode()
        return fake_response(200, body)

    with mock.patch("craftflow_jev_client._urlopen", side_effect=fake_urlopen), mock.patch(
        "craftflow_jev_client.time.monotonic", side_effect=clock.monotonic
    ), mock.patch("craftflow_jev_client.time.sleep", return_value=None):
        result = call({}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=None)

    if result is not None and len(calls) == 2:
        ok("default total budget matches existing 4.0s constant baseline (retry fires)")
    else:
        fail("default-total-budget-baseline", f"result={result!r} calls={calls!r}")


def test_total_budget_seconds_override_suppresses_retry_that_default_would_allow() -> None:
    # Same mocked timing as the baseline above, but total_budget_seconds=2.0:
    # remaining_after_failure = 2.0 - 1.0 = 1.0; 1.0 - 0.3 backoff = 0.7 < 1.0
    # minimum -> no retry, unlike the 4.0s-default baseline.
    clock = _Clock()
    calls: list = []

    def fake_urlopen(req, timeout=None):
        calls.append(timeout)
        if len(calls) == 1:
            clock.advance(1.0)
            raise _http_error(429, "Too Many Requests")
        body = json.dumps({"answers": {}, "usage": {}, "model": "jev-1.12"}).encode()
        return fake_response(200, body)

    with mock.patch("craftflow_jev_client._urlopen", side_effect=fake_urlopen), mock.patch(
        "craftflow_jev_client.time.monotonic", side_effect=clock.monotonic
    ), mock.patch("craftflow_jev_client.time.sleep", return_value=None):
        result = call(
            {},
            {},
            api_key=SENTINEL_KEY,
            model="jev-1.12",
            timeout=2.5,
            cache_dir=None,
            total_budget_seconds=2.0,
        )

    if result is None and len(calls) == 1:
        ok("total_budget_seconds override suppresses retry that default would allow")
    else:
        fail("total-budget-override-suppresses-retry", f"result={result!r} calls={calls!r}")


def test_failure_reason_out_populated_on_http_error() -> None:
    def fake_urlopen(req, timeout=None):
        raise _http_error(401, "Unauthorized")

    reason: dict = {}
    with mock.patch("craftflow_jev_client._urlopen", side_effect=fake_urlopen):
        result = call(
            {},
            {},
            api_key=SENTINEL_KEY,
            model="jev-1.12",
            timeout=2.5,
            cache_dir=None,
            failure_reason_out=reason,
        )

    if result is None and reason == {"error": "HTTPError", "status": 401}:
        ok("failure_reason_out populated on HTTPError")
    else:
        fail("failure-reason-out-http-error", f"result={result!r} reason={reason!r}")


def test_failure_reason_out_populated_on_non_http_error() -> None:
    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("boom")

    reason: dict = {}
    with mock.patch("craftflow_jev_client._urlopen", side_effect=fake_urlopen):
        result = call(
            {},
            {},
            api_key=SENTINEL_KEY,
            model="jev-1.12",
            timeout=2.5,
            cache_dir=None,
            failure_reason_out=reason,
        )

    if result is None and reason == {"error": "URLError", "status": None}:
        ok("failure_reason_out populated on non-HTTPError (status=None, not stale)")
    else:
        fail("failure-reason-out-non-http-error", f"result={result!r} reason={reason!r}")


def test_failure_reason_out_untouched_on_success() -> None:
    def fake_urlopen(req, timeout=None):
        body = json.dumps({"answers": {}, "usage": {}, "model": "jev-1.12"}).encode()
        return fake_response(200, body)

    reason: dict = {}
    with mock.patch("craftflow_jev_client._urlopen", side_effect=fake_urlopen):
        result = call(
            {},
            {},
            api_key=SENTINEL_KEY,
            model="jev-1.12",
            timeout=2.5,
            cache_dir=None,
            failure_reason_out=reason,
        )

    if result is not None and reason == {}:
        ok("failure_reason_out untouched on success")
    else:
        fail("failure-reason-out-untouched-on-success", f"result={result!r} reason={reason!r}")


class _HostileFailureReasonOut:
    def __setitem__(self, key: str, value: object) -> None:
        raise RuntimeError("hostile failure_reason_out always raises")


def test_hostile_failure_reason_out_never_prevents_none_return() -> None:
    def fake_urlopen(req, timeout=None):
        raise _http_error(401, "Unauthorized")

    try:
        with mock.patch("craftflow_jev_client._urlopen", side_effect=fake_urlopen):
            result = call(
                {},
                {},
                api_key=SENTINEL_KEY,
                model="jev-1.12",
                timeout=2.5,
                cache_dir=None,
                failure_reason_out=_HostileFailureReasonOut(),
            )
    except Exception as exc:  # pragma: no cover - only raised pre-fix
        fail(
            "hostile-failure-reason-out",
            f"call() raised {type(exc).__name__}: {exc} instead of returning None",
        )
        return

    if result is None:
        ok("hostile failure_reason_out never prevents None return")
    else:
        fail("hostile-failure-reason-out", f"result={result!r}")


def main() -> int:
    print("test_craftflow_jev_client: running")
    print(f"  (ENDPOINT = {ENDPOINT}, RETRY_STATUSES = {RETRY_STATUSES})")
    test_success_returns_answers_and_usage_and_sends_bearer_header()
    test_401_returns_none_without_retry_and_logs_status_only()
    test_429_then_success_retries_once_with_backoff()
    test_529_retries_but_500_does_not()
    test_retry_skipped_when_budget_below_one_second()
    test_second_attempt_timeout_never_exceeds_remaining_budget()
    test_socket_timeout_is_not_retried()
    test_timeout_seconds_4_leaves_no_retry_budget()
    test_malformed_json_and_missing_answers_return_none()
    test_cache_hit_skips_network_and_marks_cache_hit()
    test_cache_entry_never_contains_state_or_key()
    test_key_never_appears_in_exception_or_log_payloads()
    test_incomplete_read_during_response_body_returns_none()
    test_connection_reset_during_response_body_returns_none()
    test_memory_error_during_response_read_returns_none()
    test_recursion_error_from_hostile_json_returns_none()
    test_429_retry_then_incomplete_read_logs_status_none()
    test_env_endpoint_override_is_ignored()
    test_endpoint_file_loopback_honored()
    test_endpoint_file_non_loopback_ignored_and_logged()
    test_endpoint_file_malformed_or_missing_falls_back()
    test_home_env_is_not_used_for_endpoint_file()
    test_default_endpoint_path_derives_from_passwd_home()
    test_endpoint_file_not_read_on_cache_hit()
    test_non_json_serializable_state_returns_none()
    test_non_numeric_timeout_returns_none()
    test_time_monotonic_failure_returns_none()
    test_hostile_log_callable_never_prevents_none_return()
    test_default_total_budget_matches_existing_4s_constant_baseline()
    test_total_budget_seconds_override_suppresses_retry_that_default_would_allow()
    test_failure_reason_out_populated_on_http_error()
    test_failure_reason_out_populated_on_non_http_error()
    test_failure_reason_out_untouched_on_success()
    test_hostile_failure_reason_out_never_prevents_none_return()
    test_fifo_endpoint_file_does_not_hang_and_falls_back()
    test_oversized_endpoint_file_rejected_and_small_honored()
    test_unreadable_endpoint_file_logs_exactly_one_event_without_contents()
    test_unresolved_passwd_home_logs_endpoint_home_unresolved()
    test_invalid_override_urls_rejected()
    test_redirects_are_not_followed_and_key_not_forwarded()

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


def _hermetic_main() -> int:
    """Run main() with the passwd home pointed at a nonexistent dir so no test
    can read the developer's real ~/.claude/craftflow/jev-endpoint.json."""
    with tempfile.TemporaryDirectory() as tmp:
        nonexistent_home = os.path.join(tmp, "no-such-home")
        with mock.patch("craftflow_jev_client._passwd_home", return_value=nonexistent_home):
            return main()


if __name__ == "__main__":
    raise SystemExit(_hermetic_main())
