{
  "maxSizeBytes": 8192,
  "requiredSections": {
    "root": ["Project / Scope", "Non-Negotiable", "Build", "Security", "Agent Operating"]
  },
  "forbiddenPatterns": ["/src/", "/lib/", "/components/"],
  "exemptions": [],
  "aiContractPack": {
    "severity": "warn",
    "scanRoots": ["{{SCAN_ROOTS}}"],
    "designPathPatterns": [],
    "exemptions": []
  }
}
