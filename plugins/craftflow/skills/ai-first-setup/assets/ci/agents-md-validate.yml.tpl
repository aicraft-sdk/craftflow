name: Agents MD validate

on:
  pull_request:
    branches-ignore:
      - release/**
  push:
    branches:
      - main

jobs:
  agents-md:
    name: AGENTS.md lint
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-node@v4
        with:
          node-version: '22'
      - name: Run agents-md-lint
        run: node tools/scripts/agents-md-lint.js
