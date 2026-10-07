<!-- Moved verbatim from the verification-before-completion SKILL.md preload (PR 2 split). Contents: Self-critique gate, validation levels, checklist, output format. -->

## Self-Critique Gate (BEFORE Verification Commands)

**MANDATORY: Check these BEFORE running verification commands:**

### Code Quality
- [ ] Follows patterns from reference files?
- [ ] Naming matches project conventions?
- [ ] Error handling in place?
- [ ] No debug artifacts (console.log, TODO)?
- [ ] No commented-out code?
- [ ] No hardcoded values that should be constants?

### Implementation Completeness
- [ ] All required files modified?
- [ ] No unexpected files changed?
- [ ] Requirements fully met?
- [ ] No scope creep?

### Self-Critique Verdict

**PROCEED:** [YES/NO]
**CONFIDENCE:** [High/Medium/Low]

- If NO → Fix issues before verification
- If YES → Proceed to verification commands below

---

## Validation Levels

**Match validation depth to task complexity:**

| Level | Name | Commands | When to Use |
|-------|------|----------|-------------|
| 1 | Syntax & Style | `npm run lint`, `tsc --noEmit` | Every task |
| 2 | Unit Tests | `npm test` | Low-Medium risk tasks |
| 3 | Integration Tests | `npm run test:integration` | Medium-High risk tasks |
| 4 | Manual Validation | User flow walkthrough | High-Critical risk tasks |

**Include the appropriate validation level for each verification step.**

## Verification Checklist

Before marking work complete:

- [ ] All relevant tests pass (exit 0) - **with fresh evidence**
- [ ] Build succeeds (exit 0) - **with fresh evidence**
- [ ] Feature functionality verified - **with command output**
- [ ] No regressions introduced - **with test output**
- [ ] Evidence captured for each check - **in this message**
- [ ] Deviations from plan documented - **if implementation differed from design**
- [ ] Appropriate validation level applied for task risk

## Output Format

```markdown
## Verification Summary

### Scope
[What was completed]

### Criteria
[What was verified]

### Evidence

| Check | Command | Exit Code | Result |
|-------|---------|-----------|--------|
| Tests | `npm test` | 0 | PASS (34/34) |
| Build | `npm run build` | 0 | PASS |
| Feature | `npm test -- --grep "feature"` | 0 | PASS (3/3) |

### Deviations from Plan (if any)
| Planned | Actual | Reason |
|---------|--------|--------|
| [Original design] | [What changed] | [Why] |

### Status
COMPLETE - All verifications passed with fresh evidence
```
