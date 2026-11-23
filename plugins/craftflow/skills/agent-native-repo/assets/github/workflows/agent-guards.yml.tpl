name: agent-guards

# Deterministic checks every agent's PR must pass, whatever tool wrote it.
# The guard script and its config are read from the BASE branch, so a PR cannot weaken the checks it is judged by.

on:
  pull_request:
    types: [opened, edited, synchronize, reopened, labeled, unlabeled, ready_for_review]

permissions:
  contents: read
  pull-requests: read

jobs:
  diff-check:
    name: no gaming the gates
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Load guards from the base branch
        env:
          BASE_REF: ${{ github.base_ref }}
        run: |
          mkdir -p "$RUNNER_TEMP/guards"
          if ! git show "origin/$BASE_REF:tools/agent_guards.py" > "$RUNNER_TEMP/guards/agent_guards.py" 2>/dev/null; then
            echo "::notice::No guards on the base branch yet (bootstrap PR): using this PR's copy."
            cp tools/agent_guards.py "$RUNNER_TEMP/guards/agent_guards.py"
          fi
          git show "origin/$BASE_REF:.agents/guards.json" > "$RUNNER_TEMP/guards/guards.json" 2>/dev/null \
            || cp .agents/guards.json "$RUNNER_TEMP/guards/guards.json" 2>/dev/null \
            || echo '{}' > "$RUNNER_TEMP/guards/guards.json"
      - name: Scan the PR diff
        env:
          BASE_REF: ${{ github.base_ref }}
          LABELS: ${{ join(github.event.pull_request.labels.*.name, ',') }}
        run: |
          python3 "$RUNNER_TEMP/guards/agent_guards.py" --config "$RUNNER_TEMP/guards/guards.json" \
            diff-check --base "origin/$BASE_REF" --labels "$LABELS"

  task-ref:
    name: one task, one PR
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Check the task reference and overlap
        env:
          GH_TOKEN: ${{ github.token }}
          REPO: ${{ github.repository }}
          BASE_REF: ${{ github.base_ref }}
          PR_JSON: ${{ toJSON(github.event.pull_request) }}
        run: |
          git show "origin/$BASE_REF:tools/agent_guards.py" > "$RUNNER_TEMP/agent_guards.py" 2>/dev/null \
            || cp tools/agent_guards.py "$RUNNER_TEMP/agent_guards.py"
          printf '%s' "$PR_JSON" > "$RUNNER_TEMP/pr.json"
          # Up to 100 open PRs; page further if this repo keeps more open at once.
          gh api "repos/$REPO/pulls?state=open&per_page=100" > "$RUNNER_TEMP/prs.json"
          python3 "$RUNNER_TEMP/agent_guards.py" task-ref \
            --pr-json "$RUNNER_TEMP/pr.json" --open-prs-json "$RUNNER_TEMP/prs.json"
