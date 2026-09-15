# Changelog

All notable changes to the craftflow plugin are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Releases are produced automatically by `.github/workflows/publish-craftflow-plugin.yml`.
Do not hand-edit released sections.

## [1.5.0] - 2026-09-15

### Features

- sync code-generation ladder with upstream ponytail

### Documentation

- add next-steps tracker and craftflow-plugin guardrails summary
- fix stale agent count and script-name references

## [1.4.0] - 2026-08-28

### Features

- add workspace-tier discovery to router memory load (both hosts)
- add workspace-setup skill for workspace-tier memory provisioning
- add craftflow_workspace_init.py for workspace-tier memory provisioning

### Fixes

- close command-injection risk and stale-doc gaps in workspace-setup skill

### Refactoring

- hoist argparse import to top-level in craftflow_workspace_init.py

## [1.3.2] - 2026-08-27

### Fixes

- dispatch housekeeping agents on haiku
- drop duplicate prose report from write-agent output

## [1.3.1] - 2026-08-26

### Fixes

- cap+archive project-tier ## Last Updated / ## Completed

## [1.3.0] - 2026-08-25

### Features

- wire plan-bakeoff-judge into SKILL.md contract/dispatch tables
- PLAN bake-off fan-out, judge dispatch, degraded-candidate tolerance
- PLAN scout-then-qualify branch, stem derivation, N parsing, artifact defaults
- planner supports router-supplied Target Plan File override
- add plan-bakeoff-judge agent and contract overlay

### Fixes

- correct plan-bakeoff-judge field list and step 5b cross-reference

### Documentation

- regenerate architecture graph for plan-bakeoff-judge agent

## [1.2.1] - 2026-08-23

### Fixes

- wire ai-craft repo-root marketplace.json into the version-consistency surface

## [1.2.0] - 2026-08-23

### Features

- version-first staleness check in craftflow:update + release ADR

## [1.1.0] - 2026-08-23

### Features

- heads-up permission-prompt note in cursor-router dispatch template
- dispatch-only CI publish workflow and release live-harness manifest
- version bump writers, CHANGELOG generation, and CLI
- conventional-commit bump classifier (pure core)
- standalone version-consistency gate extracted from harness audit

### Chores

- add version-surface baseline (README line + CHANGELOG)

## [1.0.0] - 2026-06-14

### Added

- Initial tracked release. This section is a baseline marker for the automated
  release pipeline; it does not enumerate the plugin's pre-automation history.
  See `git log -- tools/craftflow-plugin/` for the full record.
