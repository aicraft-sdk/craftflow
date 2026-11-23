# Claim steps for the Agent Session Protocol

Insert the block for the chosen tracker in place of `{{CLAIM_STEPS}}`.

## GitHub Issues

1. Pick work: open issues labelled `ready` with no assignee (`gh issue list --label ready --search "no:assignee"`). Also list open PRs (`gh pr list`) and skip anything an open PR already references.
2. Claim it: `gh issue edit <N> --add-assignee @me --add-label in-progress --remove-label ready`.
3. Re-read the issue (`gh issue view <N>`). If someone else is also assigned, the earlier assignee keeps it: unassign yourself and pick another task.
4. Create the branch, push one commit, and open a **draft** PR whose description says `Closes #<N>`. The draft PR is the claim CI checks: a second PR for the same task fails `agent-guards`.

## beads

1. Pick work: `bd ready` lists unblocked, unclaimed tasks.
2. Claim it: `bd update <id> --claim`. It is atomic: if it fails because the task is already assigned, run `bd ready` again and pick another.
3. Create the branch and open a **draft** PR whose description has a line `Task: <id>`.
