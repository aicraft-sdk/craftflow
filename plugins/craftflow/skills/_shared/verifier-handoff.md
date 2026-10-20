# Findings handoff (shared)

## Previous Agent Findings Handoff

When invoking `integration-verifier`, pass:

```text
## Previous Agent Findings

### Code Reviewer
**Verdict:** {Approve|Changes Requested}
**Critical Issues:**
{reviewer critical issues or "None"}

### Silent Failure Hunter
**Critical Issues:**
{hunter critical issues or "None / not in this workflow"}
```

DEBUG skips hunter findings.

(Host-specific note: Cursor's binding doc's own "Previous Agent Findings handoff
(integration-verifier only)" section states it uses the same sub-block format and the same
fast-path-omission rule as this one — see `cursor-router/SKILL.md` § 5 for its own dispatch
mechanics around when this block is attached to a `Task` call.)

## Verifier Findings Handoff

Before invoking `integration-verifier` in BUILD:
- Read `results.reviewer` and `results.hunter` from the workflow artifact.
- Build `## Previous Agent Findings` exactly in the format verifier expects.
- Never invoke verifier without that section when review/hunt already ran.
- **fast-path exception:** When `build_mode == "fast_path"`, omit `## Previous Agent Findings` from the verifier prompt entirely — no reviewer or hunter ran. The verifier must run independent scenario coverage. When `build_mode == "fast_path_escalated"` (after escalation), the merged findings handoff IS required using the standard format above.

(Host-specific note: Cursor's binding doc explicitly cross-references this as using "the
same fast-path-omission logic already used for the Claude Code router" — see
`cursor-router/SKILL.md` § 5 for its own dispatch mechanics around when this block is
attached to a `Task` call.)

