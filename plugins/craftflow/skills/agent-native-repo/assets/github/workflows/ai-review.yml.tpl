name: ai-review

# Independent reviewer: a fresh agent that never saw the author's reasoning reviews every PR against the task's
# acceptance criteria. Needs the ANTHROPIC_API_KEY repository secret. Check the action's README for current inputs
# before enabling: https://github.com/anthropics/claude-code-action

on:
  pull_request:
    types: [opened, synchronize, ready_for_review]

permissions:
  contents: read
  pull-requests: write
  issues: read
  id-token: write

jobs:
  review:
    if: github.event.pull_request.draft == false
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: anthropics/claude-code-action@v1
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          prompt: |
            You are the independent reviewer for PR #${{ github.event.pull_request.number }} in ${{ github.repository }}.
            You did not write this code and have no access to the author's reasoning. Do not edit files.
            1. Read the task the PR references (Closes #N) and its acceptance criteria. Read AGENTS.md.
            2. Review the diff against the base branch for: unmet acceptance criteria, logic errors, missing edge
               cases, security issues, weakened or missing tests, and violations of AGENTS.md.
            3. Check the PR's Evidence section actually proves each ticked criterion.
            Post one review comment: a verdict (approve-able / changes needed), then findings ordered by severity,
            each with file:line and why it matters. Report only real problems; say so plainly if there are none.
          claude_args: "--model {{REVIEW_MODEL}}"
