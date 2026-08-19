{
  "_comment": "Harness subsystem: Scope. Hard rule: a feature CANNOT be 'done' with an empty evidence field. Do not rewrite statuses to hide unfinished work.",
  "features": [
    {
      "id": "{{FEATURE_ID}}",
      "title": "{{FEATURE_TITLE}}",
      "description": "{{FEATURE_DESCRIPTION}}",
      "status": "todo",
      "acceptance_criteria": [
        "{{CRITERION_1}}"
      ],
      "verification": {
        "commands": [
          "{{VERIFICATION_COMMAND}}"
        ],
        "expected_result": "{{EXPECTED_RESULT}}"
      },
      "evidence": ""
    },
    {
      "id": "feature-002",
      "title": "Example in-progress feature",
      "description": "This entry shows what an in-progress feature looks like.",
      "status": "in_progress",
      "acceptance_criteria": [
        "The feature does X when Y",
        "All existing tests still pass"
      ],
      "verification": {
        "commands": [
          "npm test -- --grep 'feature-002'",
          "npm run build"
        ],
        "expected_result": "All tests pass, exit 0"
      },
      "evidence": ""
    },
    {
      "id": "feature-003",
      "title": "Example completed feature",
      "description": "This entry shows what a done feature looks like — evidence field is non-empty.",
      "status": "done",
      "acceptance_criteria": [
        "The feature returns the expected value",
        "Unit tests cover the happy path and error path"
      ],
      "verification": {
        "commands": [
          "npm test -- --grep 'feature-003'"
        ],
        "expected_result": "5/5 tests pass, exit 0"
      },
      "evidence": "$ npm test -- --grep 'feature-003'\n✓ feature-003 happy path (12ms)\n✓ feature-003 error path (8ms)\n5 passed, 0 failed — exit 0"
    },
    {
      "id": "feature-004",
      "title": "Example blocked feature",
      "description": "This entry shows what a blocked feature looks like — use this status when the feature cannot proceed due to an external dependency or decision that must be resolved first.",
      "status": "blocked",
      "acceptance_criteria": [
        "Unblocked once the external dependency is resolved"
      ],
      "verification": {
        "commands": [
          "{{VERIFICATION_COMMAND}}"
        ],
        "expected_result": "{{EXPECTED_RESULT}}"
      },
      "evidence": "",
      "_blocked_reason": "Waiting on: {{BLOCKER_DESCRIPTION}}"
    }
  ]
}
