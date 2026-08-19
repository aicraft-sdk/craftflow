#!/usr/bin/env node

/**
 * AGENTS.md Linter
 *
 * Validates AGENTS.md files according to the specification:
 * - Root AGENTS.md must exist with required sections
 * - File size must be ≤ 8 KB
 * - Subfolder AGENTS.md files must only contain overrides
 * - No forbidden patterns (volatile file paths)
 *
 * Usage:
 *   node agents-md-lint.js
 *   node /path/to/agents-md-lint.js
 *
 * Exit codes:
 *   0 - All checks passed
 *   1 - One or more violations found
 */

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const MAX_SIZE = 8 * 1024; // 8 KB in bytes

// Required sections for root AGENTS.md (with flexible matching)
const REQUIRED_SECTIONS = [
  {
    name: 'Project / Scope',
    patterns: ['Project / Scope', 'Project / Scope Identification'],
  },
  {
    name: 'Non-Negotiable',
    patterns: ['Non-Negotiable', 'Non-Negotiable Constraints'],
  },
  { name: 'Build', patterns: ['Build', 'Build, Run & Test'] },
  { name: 'Security', patterns: ['Security', 'Security & Safety'] },
  {
    name: 'Agent Operating',
    patterns: ['Agent Operating', 'Agent Operating Rules'],
  },
];

// Forbidden patterns (volatile file paths)
const FORBIDDEN_PATTERNS = [/src\//, /lib\//, /\/components\//];

// Directories to skip during traversal
const SKIP_DIRS = ['.git', 'node_modules', '.next', 'dist', 'build', '.cursor'];

/**
 * Check if a path is ignored by git
 * @param {string} filePath - Path to check (relative to repo root or absolute)
 * @returns {boolean} - True if path is ignored by git, false otherwise
 */
function isGitIgnored(filePath) {
  try {
    // Use git check-ignore to see if the path is ignored
    // Returns exit code 0 if ignored, 1 if not ignored
    execSync(`git check-ignore --quiet "${filePath}"`, { stdio: 'ignore' });
    return true; // Exit code 0 means ignored
  } catch (error) {
    // Exit code 1 means not ignored, or git command failed
    // If git command failed (e.g., not in a git repo), treat as not ignored
    return false;
  }
}

/**
 * Report an error and exit with code 1
 */
function fail(filePath, message) {
  console.error(`AGENTS.md LINT ERROR: ${filePath}: ${message}`);
  process.exit(1);
}

/**
 * Check if content contains any of the required section patterns
 */
function hasRequiredSection(content, section) {
  return section.patterns.some((pattern) => {
    // Case-insensitive header matching (look for markdown headers)
    // Handle numbered headers like "## 1. Section Name" or "## Section Name"
    const escapedPattern = pattern.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const headerPattern = new RegExp(`^#+\\s+(?:\\d+\\.\\s+)?${escapedPattern}`, 'mi');
    return headerPattern.test(content);
  });
}

/**
 * Validate a single AGENTS.md file
 */
function lintAgentsFile(filePath, isRoot) {
  if (!fs.existsSync(filePath)) {
    fail(filePath, 'File not found');
    return;
  }

  const content = fs.readFileSync(filePath, 'utf8');

  // Check file size (UTF-8 byte length)
  const byteLength = Buffer.byteLength(content, 'utf8');
  if (byteLength > MAX_SIZE) {
    fail(filePath, `exceeds size limit (${byteLength} bytes > ${MAX_SIZE} bytes)`);
  }

  // Root file must have all required sections
  if (isRoot) {
    for (const section of REQUIRED_SECTIONS) {
      if (!hasRequiredSection(content, section)) {
        fail(filePath, `missing required section: ${section.name}`);
      }
    }
  } else {
    // Subfolder files must NOT redefine root sections
    const rootSectionHeaders = [
      'Project / Scope Identification',
      'Non-Negotiable Constraints',
      'Build, Run & Test',
      'Security & Safety',
      'Agent Operating Rules',
    ];

    for (const header of rootSectionHeaders) {
      // Handle numbered headers like "## 1. Section Name" or "## Section Name"
      const escapedHeader = header.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const headerPattern = new RegExp(`^#+\\s+(?:\\d+\\.\\s+)?${escapedHeader}`, 'mi');
      if (headerPattern.test(content)) {
        fail(filePath, `redefines root sections (contains "${header}")`);
      }
    }
  }

  // Check for forbidden patterns
  for (const pattern of FORBIDDEN_PATTERNS) {
    if (pattern.test(content)) {
      fail(filePath, `contains volatile file paths (matches pattern: ${pattern})`);
    }
  }
}

/**
 * Recursively walk directory tree to find AGENTS.md files
 */
function walkDirectory(dir, rootDir, errors) {
  try {
    const entries = fs.readdirSync(dir);

    for (const entry of entries) {
      // Skip ignored directories
      if (SKIP_DIRS.includes(entry)) {
        continue;
      }

      const fullPath = path.join(dir, entry);

      // Skip if path is ignored by git
      if (isGitIgnored(fullPath)) {
        continue;
      }

      const stat = fs.statSync(fullPath);

      if (stat.isDirectory()) {
        walkDirectory(fullPath, rootDir, errors);
      } else if (entry === 'AGENTS.md') {
        // Double-check the file itself is not ignored (in case parent wasn't)
        if (isGitIgnored(fullPath)) {
          continue;
        }
        const isRoot = path.dirname(fullPath) === rootDir;
        try {
          lintAgentsFile(fullPath, isRoot);
        } catch (error) {
          errors.push({ file: fullPath, error: error.message });
        }
      }
    }
  } catch (error) {
    // Skip directories we can't read (permissions, etc.)
    if (error.code !== 'EACCES' && error.code !== 'EPERM') {
      throw error;
    }
  }
}

/**
 * Main execution function
 */
function main() {
  const rootDir = process.cwd();
  const rootAgentsPath = path.join(rootDir, 'AGENTS.md');

  // Check if root AGENTS.md exists
  if (!fs.existsSync(rootAgentsPath)) {
    fail(rootDir, 'Root AGENTS.md not found');
  }

  // Validate root AGENTS.md first
  try {
    lintAgentsFile(rootAgentsPath, true);
  } catch (error) {
    fail(rootAgentsPath, error.message);
  }

  // Walk directory tree to find and validate all AGENTS.md files
  const errors = [];
  walkDirectory(rootDir, rootDir, errors);

  // If any errors were collected, report them
  if (errors.length > 0) {
    for (const { file, error } of errors) {
      console.error(`AGENTS.md LINT ERROR: ${file}: ${error}`);
    }
    process.exit(1);
  }

  console.log('AGENTS.md lint passed');
}

// Run if executed directly
if (require.main === module) {
  main();
}

module.exports = {
  lintAgentsFile,
  hasRequiredSection,
  MAX_SIZE,
  REQUIRED_SECTIONS,
  FORBIDDEN_PATTERNS,
};
