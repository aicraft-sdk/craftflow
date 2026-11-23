{
  "_comment": "Config for tools/agent_guards.py. Harness files need the harness_label (applied by a human) to change; tighten globs to this repo.",
  "harness_label": "harness-change",
  "in_progress_label": "in-progress",
  "claim_ttl_hours": {{CLAIM_TTL_HOURS}},
  "test_name_globs": ["test_*.py", "*_test.py", "*_test.go", "*.test.*", "*.spec.*", "*Test.java", "*Tests.cs"],
  "test_dirs": ["tests", "test", "__tests__", "spec"],
  "harness_globs": [
    "AGENTS.md", "*/AGENTS.md", "CLAUDE.md", "*/CLAUDE.md", ".claude/*", ".cursor/*", ".agents/guards.json",
    ".github/workflows/*", "CODEOWNERS", ".github/CODEOWNERS", ".husky/*", ".githooks/*", "tools/agent_guards.py", "tools/claude-stop-gate.sh",
    ".eslintrc*", "eslint.config.*", "ruff.toml", ".ruff.toml", "tsconfig*.json", ".golangci.*", "biome.json"
  ],
  "text_globs": ["*.md", "*.mdx", "*.txt", "*.rst"],
  "scan_exempt_globs": ["tools/agent_guards.py"]
}
