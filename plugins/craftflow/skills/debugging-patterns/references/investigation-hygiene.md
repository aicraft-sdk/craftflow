# Investigation Hygiene

## Table of Contents
- [Keep Context Lean](#keep-context-lean)
- [Capture Evidence Before Fixes](#capture-evidence-before-fixes)
- [Rule Out Sandbox/Environment Failure Before Blaming The Code](#rule-out-sandboxenvironment-failure-before-blaming-the-code)
- [Track Hypotheses Explicitly](#track-hypotheses-explicitly)
- [Watch For Stalled Investigation](#watch-for-stalled-investigation)
- [Restart Cleanly](#restart-cleanly)
- [Escalate Architectural Problems Early](#escalate-architectural-problems-early)

## Keep Context Lean

Borrow the good part of GSD's context-budget discipline without importing its
orchestration model:

- Read only the files on the active failure path.
- Do not inline giant logs or large files into follow-up prompts when a tight
  excerpt or path reference will do.
- Prefer exact stack frames, failing assertions, and narrow diffs over full
  transcripts.
- If the investigation spans many files, checkpoint the current facts before
  reading more.

Warning signs that context quality is degrading:
- your explanations get vague
- you stop citing concrete evidence
- you begin stacking fixes instead of testing hypotheses

## Capture Evidence Before Fixes

Keep a short evidence log while investigating:

```text
Observed:
- exact failure
- reproduction step
- file:line involved

Confirmed:
- what is definitely true
- what has been ruled out

Unknown:
- what still needs proof
```

If you cannot explain what is known versus unknown, you are not ready to fix.

## Rule Out Sandbox/Environment Failure Before Blaming The Code

A command that fails because *your own execution environment* blocks it —
denied network access, missing credentials, a sandboxed filesystem, a blocked
IPC/socket call — looks identical at first glance to a command that fails
because the code under test is actually broken. Treating the first as the
second is a common, expensive misread: it sends the investigation down a
code-change path for a problem that has nothing to do with the code.

Before concluding a required command's failure proves a bug:

1. Read the failure text for sandbox/permission signatures, not just the
   surface error: `EACCES`/`EPERM` on a path outside the project, `ECONNREFUSED`
   or DNS failures on outbound calls, "operation not permitted," missing
   credentials that are known to exist outside this session, or a tool that
   normally works failing at the exact step that needs elevated access.
2. If any of those signatures are present, retry the **exact same command,
   unchanged** with the narrowest available escalation (e.g. the minimum
   broader permission/sandbox mode this environment offers) before writing a
   single line of code. Do not simplify, mock around, or "fix" the command on
   this retry — the retry's only job is to tell you whether the environment
   was the cause.
3. If the unchanged command now succeeds, the code was never the problem —
   stop investigating it as one. Record that this step required an
   escalated/unsandboxed environment (so the next investigator doesn't repeat
   the misdiagnosis) and move on.
4. If the unchanged command still fails the same way even with the narrowest
   escalation, the sandbox hypothesis is refuted — proceed with the normal
   root-cause investigation above.

**Never bypass a genuine failure to make the symptom disappear** — no
`--no-verify`, no swallowing the error, no rewriting the check to accept the
blocked state. The goal of the retry in step 2 is strictly diagnostic:
confirm or refute the sandbox hypothesis, not route around whichever failure
is actually occurring.

## Track Hypotheses Explicitly

Maintain 2-3 hypotheses until one clearly wins.

```text
H1: [claim]
Evidence for:
Evidence against:
Next test:
Confidence: [0-100]
```

Rules:
- below 50 = speculation
- 50-79 = needs more evidence
- 80+ = strong enough to implement a fix

Never let the first plausible explanation become the only explanation.

## Watch For Stalled Investigation

You are stalled when:
- issue count or uncertainty is not decreasing
- you have tried 2 fixes without understanding why they failed
- each new fix reveals a different symptom in a different layer
- you cannot explain the current behavior in one paragraph

When stalled, stop producing fixes. Return to evidence gathering.

## Restart Cleanly

Use this reset protocol when tunnel vision sets in:
1. Close the current branch of investigation.
2. Write down what is certain.
3. Write down what is ruled out.
4. Generate new hypotheses that are meaningfully different.
5. Re-enter Phase 1 from the main skill.

Do not carry over lucky fixes from a failed investigation branch.

## Escalate Architectural Problems Early

After 3 failed fix attempts, assume the pattern may be wrong, not just the
implementation.

Architectural warning signs:
- the fix requires touching many unrelated layers
- every symptom points to shared state or tight coupling
- the "minimal fix" is no longer minimal
- you are preserving a broken pattern out of inertia

When these appear, pause and frame the conversation as:
"The local fix path is exhausted. We should question the underlying design
before attempting another patch."
