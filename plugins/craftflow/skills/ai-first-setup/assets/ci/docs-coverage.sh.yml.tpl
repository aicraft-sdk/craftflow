name: AI contract pack coverage

on:
  pull_request:
    branches-ignore:
      - release/**
  push:
    branches:
      - main

jobs:
  ai-contract-pack:
    name: AI Context Pack (AI.md / TESTS.md / DESIGN.md)
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - name: Run ai-contract-pack-lint
        run: bash tools/scripts/ai-contract-pack-lint.sh
