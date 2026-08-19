# Clean-State Checklist — {{PROJECT_NAME}}
<!-- Harness subsystem: Session Lifecycle (END) -->
<!-- Run before ending a session. Answer every item. Final gate: could a fresh agent resume cleanly? -->

## Verification ran
- [ ] Ran `{{BUILD_TEST_COMMAND}}` — exit 0, output captured
- [ ] Ran lint — exit 0
- [ ] Evidence recorded in `progress.md` (command + result, not vague summary)

## Scope stayed honest
- [ ] Worked on exactly one feature from `feature_list.json`
- [ ] Feature status is accurate (no premature `done`)
- [ ] No features marked `done` with an empty `evidence` field

## State is written down
- [ ] `progress.md` updated (newest entry on top, includes what was verified)
- [ ] `feature_list.json` updated (status + evidence for the active feature)
- [ ] `session-handoff.md` overwritten with current session state

## Tree is resumable
- [ ] No uncommitted half-finished edits (commit or stash)
- [ ] Last commit is safe to resume from
- [ ] `session-handoff.md` has the exact resume line

## Final gate
> **Could a fresh agent with no memory of this session resume cleanly from here?**
> If no, do not end the session yet.
