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
      - uses: actions/setup-node@v4
        with:
          node-version: '22'
      - name: Run ai-contract-pack-lint
        run: node tools/scripts/ai-contract-pack-lint.js
