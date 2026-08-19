# CI Workflow Templates Reference

GitHub Actions YAML reference for all three AI-first CI workflows.

---

## Choosing the right named-workflow pair

The skill ships two pairs of named CI workflows. Choose the correct pair for the repo shape:

| Condition | `agents-md-validate` | `docs-coverage` |
|---|---|---|
| JS repo (has `package.json`) | `agents-md-validate.yml.tpl` | `docs-coverage.yml.tpl` |
| Non-JS repo (Go/Rust/Python) | `agents-md-validate.sh.yml.tpl` | `docs-coverage.sh.yml.tpl` |

Option B fallback: if shell validator parity becomes a maintenance burden, use the Node templates for non-JS repos and add `actions/setup-node@v4` (node-version: `'22'`) as an extra step before the `node …` run command in each workflow. This is clearly noted in the proposal table so the user knows CI will pull Node even for a non-JS repo.

---

## `agents-md-validate.yml` — JS repos (verbatim from xfuse)

```yaml
name: Agents MD validate

on:
  pull_request:
    branches-ignore:
      - release/**
  push:
    branches:
      - main

jobs:
  agents-md:
    name: AGENTS.md lint
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-node@v4
        with:
          node-version: '22'
      - name: Run agents-md-lint
        run: node tools/scripts/agents-md-lint.js
```

Use verbatim. No substitution needed.

---

## `agents-md-validate.sh.yml.tpl` — non-JS repos

```yaml
name: Agents MD validate

on:
  pull_request:
    branches-ignore:
      - release/**
  push:
    branches:
      - main

jobs:
  agents-md:
    name: AGENTS.md lint
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - name: Run agents-md-lint
        run: bash tools/scripts/agents-md-lint.sh
```

Use verbatim. No `setup-node` step — `bash`, `find`, `grep`, and `wc` are all pre-installed on `ubuntu-latest`.

---

## `docs-coverage.yml` — JS repos (verbatim from xfuse)

```yaml
name: AI contract pack coverage

on:
  pull_request:
    branches-ignore:
      - release/**
  push:
    branches:
      - main

jobs:
  ai-contract-pack:
    name: AI Context Pack (AI.md / TESTS.md / DESIGN.md)
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '22'
      - name: Run ai-contract-pack-lint
        run: node tools/scripts/ai-contract-pack-lint.js
```

Use verbatim. No substitution needed.

---

## `docs-coverage.sh.yml.tpl` — non-JS repos

```yaml
name: AI contract pack coverage

on:
  pull_request:
    branches-ignore:
      - release/**
  push:
    branches:
      - main

jobs:
  ai-contract-pack:
    name: AI Context Pack (AI.md / TESTS.md / DESIGN.md)
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - name: Run ai-contract-pack-lint
        run: bash tools/scripts/ai-contract-pack-lint.sh
```

Use verbatim. `python3` is pre-installed on `ubuntu-latest` (used by `ai-contract-pack-lint.sh` for JSON parsing).

---

## `ai-checks.nx.yml.tpl` (Nx-monorepo variant — verbatim from xfuse)

Use this template only when `nx.json` is present. Two adaptations are required:
1. Replace `${{ secrets.CLIENT_TEAM_JFROG_ACCESS_TOKEN }}` with the actual secret name used by the organization, or remove the `node-auth-token` parameter if the registry is public.
2. Replace `uses: ./.github/actions/setup-common` with your organization's Node.js setup action (e.g. `actions/setup-node@v4` directly), as `setup-common` is a xfuse-internal composite action that does not exist in other repositories. The step typically installs Node and configures any private registry authentication.

```yaml
name: AI checks (Nx lint affected)

on:
  pull_request:
    branches-ignore:
      - release/**
  workflow_dispatch:

jobs:
  nx-lint-affected:
    name: Nx affected lint
    runs-on: ubuntu-latest
    timeout-minutes: 45
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - name: Setup Common
        uses: ./.github/actions/setup-common
        with:
          node-auth-token: ${{ secrets.CLIENT_TEAM_JFROG_ACCESS_TOKEN }}
      - name: Derive base for affected
        id: shas
        run: |
          if [ "${{ github.event_name }}" = "pull_request" ]; then
            echo "BASE=${{ github.event.pull_request.base.sha }}" >> $GITHUB_OUTPUT
            echo "HEAD=${{ github.event.pull_request.head.sha }}" >> $GITHUB_OUTPUT
          else
            echo "BASE=HEAD~1" >> $GITHUB_OUTPUT
            echo "HEAD=HEAD" >> $GITHUB_OUTPUT
          fi
      - name: Nx affected lint
        run: yarn nx affected -t lint --base=${{ steps.shas.outputs.BASE }} --head=${{ steps.shas.outputs.HEAD }} --parallel=3 --skip-nx-cache
```

---

## `ai-checks.generic.yml.tpl` (non-Nx variant)

Use this template when `nx.json` is absent. Replace `${BUILD_TEST_COMMAND}` with the detected command (default: `npm ci && npm run build && npm test`). Adjust `node-version` if the project requires a different Node.js version.

```yaml
name: AI checks

on:
  pull_request:
    branches-ignore:
      - release/**
  workflow_dispatch:

jobs:
  build-and-test:
    name: Build and test
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-node@v4
        with:
          node-version: '22'
      - name: Build and test
        run: npm ci && npm run build && npm test
```

For `yarn`: replace `npm ci && npm run build && npm test` with `yarn install --frozen-lockfile && yarn build && yarn test`.

For `pnpm`: use `pnpm install --frozen-lockfile && pnpm build && pnpm test`.

---

## Node.js version guidance

| Use case | Recommended |
|---|---|
| Default (modern JS) | `'22'` |
| Locked to specific version | Match the `.nvmrc` or `engines.node` field in `package.json` |
| LTS only | `'lts/*'` |

---

## Trigger strategy

All three workflows share the same trigger strategy:
- `pull_request` on all branches except `release/**`
- `push` to `main` (for `agents-md-validate.yml` and `docs-coverage.yml`)
- `workflow_dispatch` for manual runs (for `ai-checks.yml`)

Adapt `branches-ignore` and `branches` if the repo uses a different branching strategy (e.g., `master` instead of `main`, or `development` as the integration branch).
