name: AI checks

on:
  pull_request:
    branches-ignore:
      - release/**
  workflow_dispatch:

jobs:
  build-and-test:
    name: Build and test
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-node@v4
        with:
          node-version: '22'
      - name: Build and test
        run: npm ci && npm run build && npm test
