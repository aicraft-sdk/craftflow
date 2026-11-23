# Branch protection for an agent-native repo

Show these to the user with `{owner}`, `{repo}` and `{branch}` filled in. Run them only after an explicit yes: they change the merge rules for everyone.

Required checks are the job names from the installed workflows, `no gaming the gates` and `one task, one PR` (agent-guards), plus the repo's existing test and lint jobs. `ai-review` posts a review rather than a pass/fail status, so it is not required.

```bash
gh api -X PUT repos/{owner}/{repo}/branches/{branch}/protection --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["no gaming the gates", "one task, one PR"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "require_code_owner_reviews": true,
    "dismiss_stale_reviews": true
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
```

Add the repository's existing CI job names to `contexts`; read them from a recent commit's checks with `gh api repos/{owner}/{repo}/commits/{sha}/check-runs --jq '.check_runs[].name'`.

`require_code_owner_reviews` is what makes `CODEOWNERS` bind: harness files (AGENTS.md, CI, guards, lint config) cannot change without a human code owner's approval, on top of the `harness-change` label the guard requires.
