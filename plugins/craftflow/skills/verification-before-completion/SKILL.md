---
name: verification-before-completion
description: "Use when about to claim work is complete, fixed, or passing, or before commit, PR, or task completion, and fresh verification evidence must exist first."
allowed-tools: Read Grep Glob Bash LSP
---

# Verification Before Completion

Claiming work is complete without verification is dishonesty, not efficiency. **Evidence before claims, always.**

## The Iron Law

```
NO COMPLETION CLAIMS WITHOUT FRESH VERIFICATION EVIDENCE
```

If you haven't run the verification command in this message, you cannot claim it passes.

## Gate (before any status claim or expression of satisfaction)

1. IDENTIFY the command that proves the claim. 2. RUN it fully, fresh. 3. READ full output and exit code. 4. VERIFY output confirms the claim (if not, state the actual status). 5. ONLY THEN claim, WITH evidence.

Skip any step = lying, not verifying.

Claim requires: tests = 0 failures; build = build exit 0 (a linter is not a build); bug fixed = original symptom test passes; regression test = revert the fix, it must FAIL, then restore; delegated work = check the VCS diff.

## Evidence shape (required)

One entry per claim, from THIS session, exit code mandatory: `[command] → exit [code]: [result summary]`, grouped in an `EVIDENCE:` block before the Status line. "Looks good" is not evidence. A status without an EVIDENCE block is INVALID.

## Red flags - STOP

"should/probably/seems to"; satisfaction before verification ("Great!", "Done!"); commit/push/PR without verification; trusting agent success reports; partial verification; "just this once"; ANY wording implying success without a fresh run. Run verification, get evidence, THEN speak.

## Goal-backward proof (phase exit)

- **Truths:** What must be TRUE? (observable outcomes)
- **Artifacts:** What must EXIST? (files, endpoints, tests)
- **Wiring:** What must be WIRED? (component → API → database)

## Phase-Exit Proof vs Extended Audit

Phase-exit proof (truths, artifacts, wiring, fresh scenario evidence) is never skipped. Extended audit is extra work; if not run, say so.

## Completion Guard (IMMEDIATELY before the Router Contract)

All four must hold, else fix first: (1) acceptance criteria met, any gap = STATUS:FAIL; (2) every claim has a fresh `[command] → exit [code]` entry; (3) no stubs in files YOU modified (run the scans in references/stub-and-wiring-checks.md; sensitive route without auth check -> add protection before claiming completion); (4) last test/build ran in THIS message.

## Read on demand (one reference per trigger)

- Before running the verification commands (self-critique, validation levels, checklist, output format): `references/self-critique-and-output.md`
- A claim feels weak or you are rationalizing (claims table, key patterns, rationalizations): `references/claims-and-rationalizations.md`
- At the Completion Guard, and when scanning for stubs, wiring, exports, auth gaps, or the goal-backward template: `references/stub-and-wiring-checks.md`
- Full verbatim gate text (gate function, red flags, evidence protocol, completion guard): `references/core-gate-full.md`
- Plan requires real/seeded/production-like proof (API, browser, stress): `references/live-production-testing.md`
