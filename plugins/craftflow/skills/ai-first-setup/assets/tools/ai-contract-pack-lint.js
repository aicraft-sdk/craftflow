#!/usr/bin/env node

/**
 * AI Context Pack linter
 *
 * For each first-level project directory (configured via scanRoots in .agents-md-validator.json)
 * that contains AGENTS.md, require AI.md and TESTS.md. Optionally require DESIGN.md for UI
 * projects (configured via designPathPatterns in .agents-md-validator.json).
 *
 * Exit code follows `aiContractPack.severity`: "warn" -> always 0, "error" -> 1 if missing.
 */

const fs = require('fs');
const path = require('path');

const ROOT = process.cwd();
const CONFIG_PATH = path.join(ROOT, '.agents-md-validator.json');

const DEFAULT_CONFIG = {
  aiContractPack: {
    severity: 'warn',
    scanRoots: ['.'],
    designPathPatterns: [],
    exemptions: [],
  },
};

function loadConfig() {
  if (!fs.existsSync(CONFIG_PATH)) {
    return DEFAULT_CONFIG;
  }
  try {
    const raw = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
    return {
      ...DEFAULT_CONFIG,
      ...raw,
      aiContractPack: {
        ...DEFAULT_CONFIG.aiContractPack,
        ...(raw.aiContractPack || {}),
      },
    };
  } catch (e) {
    console.error('ai-contract-pack-lint: invalid .agents-md-validator.json', e.message);
    process.exit(1);
  }
}

function needsDesign(relDir, rules) {
  for (const rule of rules) {
    if (rule.type === 'exactDir' && relDir === rule.path) {
      return true;
    }
    if (rule.type === 'dirnamePrefix' && rule.root) {
      const prefix = `${rule.root}${path.sep}${rule.prefix}`;
      if (relDir.startsWith(prefix)) {
        return true;
      }
    }
  }
  return false;
}

function listProjectDirs(scanRoot) {
  const base = path.join(ROOT, scanRoot);
  if (!fs.existsSync(base)) {
    return [];
  }
  return fs
    .readdirSync(base, { withFileTypes: true })
    .filter((d) => d.isDirectory())
    .map((d) => path.join(base, d.name));
}

function main() {
  const config = loadConfig();
  const pack = config.aiContractPack || DEFAULT_CONFIG.aiContractPack;
  const severity = pack.severity === 'error' ? 'error' : 'warn';
  const exemptions = new Set(pack.exemptions || []);
  const designRules = pack.designPathPatterns || DEFAULT_CONFIG.aiContractPack.designPathPatterns;

  const issues = [];

  for (const scanRoot of pack.scanRoots || ['.']) {
    for (const dir of listProjectDirs(scanRoot)) {
      const agentsPath = path.join(dir, 'AGENTS.md');
      if (!fs.existsSync(agentsPath)) {
        continue;
      }
      const relDir = path.relative(ROOT, dir);
      if (exemptions.has(relDir)) {
        continue;
      }

      const rel = (name) => path.join(relDir, name);
      if (!fs.existsSync(path.join(dir, 'AI.md'))) {
        issues.push({ level: severity, msg: `missing AI.md (add AI Context Pack): ${rel('AI.md')}` });
      }
      if (!fs.existsSync(path.join(dir, 'TESTS.md'))) {
        issues.push({ level: severity, msg: `missing TESTS.md: ${rel('TESTS.md')}` });
      }
      if (needsDesign(relDir, designRules) && !fs.existsSync(path.join(dir, 'DESIGN.md'))) {
        issues.push({ level: severity, msg: `missing DESIGN.md (required for UI / MFE shared): ${rel('DESIGN.md')}` });
      }
    }
  }

  const label = severity === 'error' ? 'ERROR' : 'WARN';
  for (const issue of issues) {
    console.error(`AI contract pack ${label}: ${issue.msg}`);
  }

  if (issues.length === 0) {
    console.log('AI contract pack lint passed');
    process.exit(0);
  }

  if (severity === 'error') {
    process.exit(1);
  }
  console.error(`AI contract pack: ${issues.length} warning(s) (severity=warn, not blocking)`);
  process.exit(0);
}

main();
