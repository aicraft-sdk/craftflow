### Fast Path Reference

This file is canonical law for fast-path BUILD routing. Read by the router before BUILD preparation when `build_mode` is `"fast_path"` or `"fast_path_escalated"`.

#### risk_keyword_scan — Keyword Table

Scans request text (case-insensitive). Returns matched keywords. Empty array = fast path.

| Group | Keywords |
|-------|----------|
| Security | `auth`, `authz`, `oauth`, `jwt`, `password`, `credential`, `secret`, `cert`, `ssl`, `tls`, `encrypt`, `decrypt`, `permission`, `role`, `session`, `access control` |
| Database / schema | `migration`, `schema change`, `alter table`, `drop table`, `seed`, `remove column`, `drop column`, `export data`, `data export` |
| Payment | `payment`, `billing`, `stripe`, `checkout`, `subscription`, `invoice` |
| Explicit risk markers | `critical path`, `production data`, `irreversible`, `truncate`, `delete all`, `purge` |

**Conservative principle:** When in doubt, add the keyword to the list. The cost of a false positive (full chain on a simple request) is wasted tokens. The cost of a false negative (fast path on auth code) is a missed review.

#### Workflow Artifact Fields

Three fields are added to the artifact schema and initialized at workflow creation:

```json
{
  "build_mode": "fast_path",
  "fast_path_risk_signals": [],
  "fast_path_escalated": false
}
```

Values:
- `build_mode`: `"fast_path"` | `"fast_path_escalated"` | `"standard"`
- `fast_path_risk_signals`: `[]` (fast path taken) or `["auth", "schema change"]` (full chain)
- `fast_path_escalated`: `false` | `true`

#### Agent Dispatch Table

| Phase | Agent | Standard BUILD | Fast path | Escalated |
|-------|-------|---------------|-----------|-----------|
| `build-implement` | component-builder | ✓ | ✓ | ✓ (already done) |
| `build-review` | code-reviewer | ✓ | ✗ skip | ✓ |
| `build-hunt` | silent-failure-hunter | ✓ | ✗ skip | ✓ |
| `build-verify` | integration-verifier | ✓ | ✓ | ✓ (re-verify) |
| `build-doc-sync` | doc-syncer | ✓ | ✗ skip | ✗ skip |
| `memory-finalize` | (inline) | ✓ | ✓ | ✓ |

#### Gate Table

| Gate / Rule | Standard | Fast path | Escalated |
|-------------|----------|-----------|-----------|
| `phase_exit_gate` | ✓ | ✓ | ✓ |
| `failure_stop_gate` | ✓ | ✓ | ✓ |
| `memory_sync_gate` | ✓ | ✓ | ✓ |
| `1a-SCOPE rule` | ✓ | ✗ dropped | ✓ RESTORED |
| `doubt_verify_gate` | conditional | ✗ | ✗ |

`1a-SCOPE rule`: dropped on fast path (no reviewer/hunter findings to scope); RESTORED on escalated path. On escalated fast path, `1a-SCOPE` applies using the same CRITICAL+HIGH threshold (at least one CRITICAL and at least one HIGH in the escalated reviewer+hunter output) as the standard parallel review phase. Rationale: the escalated spawn produces equivalent output to the standard parallel review phase — same agents, same output shape.

#### Escalation Cap

Max one REM-FIX cycle after escalation. After one REM-FIX + re-verify:
- Re-verify PASS → memory-finalize → done
- Re-verify FAIL → `failure_stop_gate` fires → stop with `BLOCKING: true`
- No further escalation cycles are permitted

#### Announcement Protocol

Router announces path before child task creation (one line each):
- `-> FAST-PATH BUILD (no risk signals)` — fast path taken
- `-> FULL BUILD (risk signals: {matched keywords})` — standard path taken
- `-> FAST-PATH BUILD [ESCALATED] (verifier FAIL — reviewer + hunter spawned)` — escalation event

#### Fast-Path Verifier Prompt Note

On fast path, the `## Previous Agent Findings` section is OMITTED from the verifier prompt (no reviewer/hunter ran). The verifier runs its own independent scenario coverage rather than reconciling reviewer/hunter findings.

On escalated path, the standard merged findings handoff is used (same as standard BUILD §5 pattern).
