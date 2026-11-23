{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
          {
            "type": "command",
            "command": "{{FAST_CHECK_CMD}} >&2 || exit 2",
            "timeout": 60
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "sh tools/claude-stop-gate.sh",
            "timeout": 600
          }
        ]
      }
    ]
  }
}
