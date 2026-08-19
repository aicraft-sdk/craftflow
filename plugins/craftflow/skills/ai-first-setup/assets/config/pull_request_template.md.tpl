## Summary

<!-- One sentence describing what this PR does. -->

## Spec

<!-- Link to the feature spec under docs/ai/specs/ -->
Spec: `docs/ai/specs/{{TICKET_OR_SLUG}}.md`

## Plan

<!-- Link to the AIDLC plan under docs/plans/ -->
Plan: `docs/plans/{{SLUG}}.md`

## Changes

<!-- Bullet list of key changes. -->

-

## Verification

<!-- Paste test and build command output. No "done" without evidence. -->

```
$ {{TEST_CMD}}
# paste output here
```

```
$ {{BUILD_CMD}}
# paste output here
```

## Checklist

- [ ] Tests pass (evidence above)
- [ ] Build passes (evidence above)
- [ ] `node tools/scripts/agents-md-lint.js` exits 0
- [ ] `node tools/scripts/ai-contract-pack-lint.js` exits 0
- [ ] No secrets committed
- [ ] No `.only` in committed tests
- [ ] Conventional commit format used
- [ ] PR title includes ticket reference (if applicable)
