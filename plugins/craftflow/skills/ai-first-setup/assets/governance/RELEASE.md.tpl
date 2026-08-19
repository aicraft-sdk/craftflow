# Release and commits

## Commits

- **Conventional commits**: `<type>(<scope>): <description>`
- **Mandatory scope**: project name or area of change.
- **PR title**: include ticket reference (`{{TICKET_PREFIX}}-id`) when using a ticketing system; otherwise use a descriptive title.

## Changelog

- Follow the project's changelog policy (if defined) when your change requires a doc update.
- For new features and breaking changes, update `CHANGELOG.md` or the relevant release notes file.

## AI-first PRs

- PR template requires links to **Spec** and **Plan** and verification evidence.
- Do not merge without required checks (linter, tests, validators) passing.

## Versioning

- Follow semantic versioning (`MAJOR.MINOR.PATCH`) for published packages.
- Breaking changes: require explicit approval and a major version bump.
