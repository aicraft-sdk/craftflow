<!-- Moved verbatim from the verification-before-completion SKILL.md preload (PR 2 split). Contents: Full gate text: overview, iron law, gate function, red flags, live proof, evidence protocol, phase-exit proof, completion guard. -->

# Verification Before Completion

## Overview

Claiming work is complete without verification is dishonesty, not efficiency.

**Core principle:** Evidence before claims, always.

<!-- CRAFTFLOW-M7: Overlap with integration-verifier is intentional (defense in depth). VBC is loaded by WRITE agents for self-verification before reporting done; integration-verifier is a separate router-spawned agent. Both check different moments in the workflow. -->

**Violating the letter of this rule is violating the spirit of this rule.**

## The Iron Law

```
NO COMPLETION CLAIMS WITHOUT FRESH VERIFICATION EVIDENCE
```

If you haven't run the verification command in this message, you cannot claim it passes.

## The Gate Function

```
BEFORE claiming any status or expressing satisfaction:

1. IDENTIFY: What command proves this claim?
2. RUN: Execute the FULL command (fresh, complete)
3. READ: Full output, check exit code, count failures
4. VERIFY: Does output confirm the claim?
   - If NO: State actual status with evidence
   - If YES: State claim WITH evidence
5. REFLECT: Pause to consider tool results before next action
6. ONLY THEN: Make the claim

Skip any step = lying, not verifying
```

## Red Flags - STOP

If you find yourself:

- Using "should", "probably", "seems to"
- Expressing satisfaction before verification ("Great!", "Perfect!", "Done!", etc.)
- About to commit/push/PR without verification
- Trusting agent success reports
- Relying on partial verification
- Thinking "just this once"
- Tired and wanting work over
- **ANY wording implying success without having run verification**

**STOP. Run verification. Get evidence. THEN speak.**

## Production-Like Live Proof

If the accepted plan or current task requires real, seeded, production-like verification, read `references/live-production-testing.md` before claiming completion.

Use the live harness when the task depends on:
- real API calls
- seeded or resettable data
- browser or worker orchestration
- cross-service side effects
- load or stress behavior

Do not treat replay fixtures, unit tests, or manual spot-checks as equivalent proof when the plan requires live-system evidence.

## Evidence Array Protocol

**Every claim in verification output MUST have a corresponding evidence entry.**

**Format:** `[command] → exit [code]: [result summary]`

**Rules:**
1. One evidence entry per claim — no claim without evidence, no evidence without claim
2. Evidence must be from THIS session (not recalled from memory)
3. Exit codes are mandatory — "looks good" is not evidence
4. Group evidence by claim type:

```
EVIDENCE:
  tests: ["CI=true npm test → exit 0: 34/34 passed"]
  build: ["npm run build → exit 0: compiled in 2.3s"]
  feature: ["curl localhost:3000/api/health → exit 0: {status: ok}"]
  regression: ["npm test -- auth.test.ts → exit 0: regression case passes"]
```

**Verification Summary must include this EVIDENCE block before the Status line.**

**Anti-pattern:** `Status: COMPLETE - All verifications passed` without EVIDENCE block = INVALID.

## Phase-Exit Proof vs Extended Audit

Use this distinction when verification gets expensive:

- **Phase-exit proof** is the non-negotiable minimum:
  - truths
  - artifacts
  - wiring
  - fresh scenario evidence
- **Extended audit** is additional confidence work:
  - broader scans
  - extra pattern sweeps
  - deeper blast-radius checks

Never skip phase-exit proof. If extended audit is not run, say so explicitly instead of implying it happened.

Goal-backward asks: "Does the GOAL work?" not "Did the TASK complete?"

## Completion Guard (Final Gate Before Router Contract)

**IMMEDIATELY before writing `### Router Contract (MACHINE-READABLE)`, verify ALL:**

1. **Acceptance criteria met?** — Re-read task description. Check each criterion. Any gap = STATUS:FAIL
2. **Evidence array complete?** — Every claim has `[command] → exit [code]` entry from THIS session
3. **No stubs in changed files?** — Run stub detection on files YOU modified (not entire repo)
4. **Fresh verification?** — Last test/build command ran in THIS message (not earlier in conversation)

**If ANY check fails:** Fix it FIRST, then re-run Completion Guard. Do NOT emit Router Contract with STATUS:PASS/FIXED/APPROVE until all 4 pass.

**This is the LAST gate. No exceptions. No "close enough."**
