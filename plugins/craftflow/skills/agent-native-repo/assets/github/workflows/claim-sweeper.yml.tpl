name: claim-sweeper

# Returns stale task claims to `ready`: issues labelled in-progress with no activity, and no open PR referencing them
# updated, within the claim TTL (.agents/guards.json `claim_ttl_hours`).

on:
  schedule:
    - cron: "{{SWEEP_CRON}}"
  workflow_dispatch:

permissions:
  contents: read
  issues: write
  pull-requests: read

jobs:
  sweep:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Find and release stale claims
        env:
          GH_TOKEN: ${{ github.token }}
          REPO: ${{ github.repository }}
        run: |
          gh api "repos/$REPO/issues?state=open&labels=in-progress&per_page=100" > "$RUNNER_TEMP/issues.json"
          gh api "repos/$REPO/pulls?state=open&per_page=100" > "$RUNNER_TEMP/prs.json"
          python3 tools/agent_guards.py sweep \
            --issues-json "$RUNNER_TEMP/issues.json" --prs-json "$RUNNER_TEMP/prs.json" > "$RUNNER_TEMP/out.json"
          cat "$RUNNER_TEMP/out.json"
          for n in $(python3 -c 'import json,sys; print(" ".join(map(str, json.load(open(sys.argv[1]))["release"])))' "$RUNNER_TEMP/out.json"); do
            echo "Releasing #$n"
            echo '{"assignees":[]}' | gh api -X PATCH "repos/$REPO/issues/$n" --input - > /dev/null
            gh api -X DELETE "repos/$REPO/issues/$n/labels/in-progress" > /dev/null || true
            gh api "repos/$REPO/issues/$n/labels" -f "labels[]=ready" > /dev/null
            gh api "repos/$REPO/issues/$n/comments" \
              -f body="Claim released by claim-sweeper: no activity within the claim TTL. The task is back in \`ready\`." > /dev/null
          done
