#!/usr/bin/env bash
set -euo pipefail
# init.sh — AI harness initialization script
# Run at the start of every agent session to verify the environment before work.
# Subsystem: Session Lifecycle (START)
#
# FILL INSTRUCTIONS — replace every {{...}} token listed below before using:
#   {{TOOL_VERSION_CHECKS}}   — commands to verify required tool versions, e.g.:
#                               go version; node --version; python --version
#   {{INSTALL_CMD}}           — install/sync dependencies, e.g.:
#                               npm ci | go mod download | pip install -r requirements.txt
#   {{LINT_CMD}}              — lint command, e.g.:
#                               npx eslint . | golangci-lint run | ruff check .
#   {{TYPE_CHECK_CMD}}        — type-check command, e.g.:
#                               npx tsc --noEmit | go vet ./... | mypy .
#   {{BUILD_TEST_COMMAND}}    — full build + test, e.g.:
#                               npm ci && npm run build && npm test
#                               go build ./... && go test -race ./...

echo "==> [init] Checking environment health..."
# --- Tool version checks ---
{{TOOL_VERSION_CHECKS}}   # e.g. go version, node --version, python --version

echo "==> [init] Installing dependencies..."
{{INSTALL_CMD}}

echo "==> [init] Running lint..."
{{LINT_CMD}}

echo "==> [init] Running type-check..."
{{TYPE_CHECK_CMD}}

echo "==> [init] Running tests..."
{{BUILD_TEST_COMMAND}}

echo "==> [init] Checking git state..."
git status --short
git log --oneline -5

echo "==> [init] Reading session state..."
cat progress.md 2>/dev/null | head -30 || echo "(no progress.md yet)"
cat feature_list.json 2>/dev/null | python3 -m json.tool | grep '"status"' || echo "(no feature_list.json yet)"

echo "==> [init] Environment healthy. Ready to work."
