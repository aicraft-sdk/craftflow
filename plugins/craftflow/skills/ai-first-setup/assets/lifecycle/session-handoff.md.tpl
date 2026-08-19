# Session Handoff — {{PROJECT_NAME}}
<!-- Harness subsystem: Session Lifecycle + State -->
<!-- Overwrite this file at the end of every session. One screen only. -->

## Resume here
{{RESUME_LINE}}
<!-- E.g. "Continue `feature_list.json` feature {{FEATURE_ID}}: {{FEATURE_TITLE}}" -->

## Active feature
- ID: {{FEATURE_ID}}
- Title: {{FEATURE_TITLE}}
- Status: {{FEATURE_STATUS}}
- Branch: {{BRANCH}}
- Last safe commit: {{LAST_COMMIT_SHA}} — {{LAST_COMMIT_MSG}}

## Environment state
- Deps installed: {{YES_NO}}
- Build passing: {{YES_NO}}
- Tests passing: {{YES_NO}}

## Done vs not done (this feature)
Done:
- {{DONE_ITEM}}

Not done:
- {{NOT_DONE_ITEM}}

## Landmines
<!-- Non-obvious context that would cost the next session an hour to rediscover -->
- {{LANDMINE}}

## Last updated
{{ISO_DATE}}
