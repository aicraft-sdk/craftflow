name: AI checks (Nx lint affected)

on:
  pull_request:
    branches-ignore:
      - release/**
  workflow_dispatch:

jobs:
  nx-lint-affected:
    name: Nx affected lint
    runs-on: ubuntu-latest
    timeout-minutes: 45
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - name: Setup Common
        uses: ./.github/actions/setup-common
        with:
          node-auth-token: ${{ secrets.CLIENT_TEAM_JFROG_ACCESS_TOKEN }}
      - name: Derive base for affected
        id: shas
        run: |
          if [ "${{ github.event_name }}" = "pull_request" ]; then
            echo "BASE=${{ github.event.pull_request.base.sha }}" >> $GITHUB_OUTPUT
            echo "HEAD=${{ github.event.pull_request.head.sha }}" >> $GITHUB_OUTPUT
          else
            echo "BASE=HEAD~1" >> $GITHUB_OUTPUT
            echo "HEAD=HEAD" >> $GITHUB_OUTPUT
          fi
      - name: Nx affected lint
        run: yarn nx affected -t lint --base=${{ steps.shas.outputs.BASE }} --head=${{ steps.shas.outputs.HEAD }} --parallel=3 --skip-nx-cache
