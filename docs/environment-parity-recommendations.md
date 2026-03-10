# Environment parity: recommendations for the team

This doc summarizes how runtimes differ across local, CI, and deployed environments and recommends a path to better parity. It applies to the full Edvise workspace (edvise-api, edvise-ui, edvise).

## Current state

| Component | Local (today) | CI | Deployed (dev/staging/prod) |
|-----------|----------------|-----|-----------------------------|
| **edvise-api (Python)** | Host: `uv` + pyproject (>=3.10,<3.13) | — | **Python 3.10** (Dockerfile: `python:3.10-slim-bookworm`) |
| **edvise (Python)** | Host: `uv` + pyproject (>=3.10,<3.13) | — | Used by api/worker + Databricks |
| **edvise-ui (PHP)** | Host PHP | **PHP 8.4** (precommit-tests.yml) | **Unpinned**: buildpack chooses version (composer `^8.1`) |
= | **edvise-ui (Node)** | Host Node | — | **Unpinned**: Cloud Build uses default `node` image for `npm run build` |

**Gaps:**

- PHP: Use 8.4 (active support; 8.2 security-only until Dec 2026). Align local, CI, and (optionally) deploy.
- Node: No version documented; Cloud Build and local may differ.
- Python: Already well aligned (Dockerfile pins 3.10; local uses uv with same range).

## Recommendations

### 1. Pin PHP to 8.2 everywhere (quick win)

- **Rationale:** CI already uses 8.2; Laravel 10 and composer `^8.1` support it. Aligning on 8.2 avoids drift and matches CI.
- **Actions:**
  - **edvise-ui:** Add `.php-version` with `8.2`. Use a version manager (e.g. `phpenv`, `asdf`, or Homebrew `php@8.2`) so local matches.
  - **Optional:** In `edvise-ui`, add a Dockerfile (like edvise-api) and switch Cloud Build to it so **deployed** PHP is also 8.2 instead of buildpack-default. If the team keeps buildpacks for now, document “We standardise on PHP 8.2 for local and CI; dev runtime may vary until we add a Dockerfile.”

### 2. Pin Node for the frontend (quick win)

- **Rationale:** Cloud Build runs `npm run build` with the default Node image; local may differ. Pinning avoids subtle build differences.
- **Actions:**
  - **edvise-ui:** Add `.nvmrc` with a single version (e.g. `20` or `22`). Prefer Node 20 LTS unless the team explicitly wants 22.
  - In CI (if you add Node steps to edvise-ui or a shared workflow), use the same version.
  - Optionally add `"engines": { "node": ">=20" }` in `package.json` so `npm` can warn on wrong Node.

### 3. Keep Python as-is

- **edvise-api** and **edvise:** Dockerfile and pyproject already pin Python 3.10. Local: use `uv` and the same range (>=3.10,<3.13); no change needed beyond ensuring everyone uses the repo’s uv setup.

### 4. Document env vars and “shape” of environments

- **Rationale:** Parity isn’t only runtimes; missing or different env vars cause hard-to-repro bugs.
- **Actions:**
  - Keep `.env.example` (or equivalent) up to date in each repo and document which vars are required for local vs dev.
  - In **edvise-api**, `docs/architecture.md` already describes Secret Manager and env-file secrets; add a short “Local development” subsection that points to `.env.example` and any env-file location (e.g. `ENV_FILE_PATH`).

### 5. Dev containers (optional, for strict parity)

- **Rationale:** You have Docker; dev containers give one canonical stack (PHP 8.4, Node 20, Python 3.10) so “Open in Container” matches CI and reduces “works on my machine” issues.
- **Actions:**
  - Add a **Dev Container** at the workspace root (e.g. `edvise/.devcontainer/`) with:
    - PHP 8.4, Node 20 (or 22), Python 3.10, plus Composer, npm, uv.
    - Use the same Dockerfile or image for both `code .` (or “Reopen in Container”) and any scripts that run tests/linters.
  - Update **edvise.code-workspace** so that when opened inside the dev container, tasks (Start Backend API, Start Frontend Laravel, Start Frontend Vite) run inside the container. No change required for “open on host” workflow; document both in README or this doc.
  - **Recommendation:** Treat this as **optional**: adopt version files (`.php-version`, `.nvmrc`) first, then add a dev container for those who want full containment. Not everyone has to use the container.

## Summary table (after recommendations)

| Component | Local | CI | Deployed |
|-----------|--------|-----|----------|
| **edvise-api (Python)** | uv, 3.10+ | — | 3.10 (Dockerfile) |
| **edvise (Python)** | uv, 3.10+ | — | 3.10 (via api/worker/Databricks) |
| **edvise-ui (PHP)** | **8.4** (`.php-version` + version manager) | 8.4 | 8.4 (if Dockerfile added; else document) |
| **edvise-ui (Node)** | **20** (`.nvmrc`) | 20 (if added) | 20 (if Cloud Build image pinned; else document) |

## Suggested rollout

1. **This sprint:** Use `.php-version` = 8.4 and `.nvmrc` = 20 in **edvise-ui**; local/CI target PHP 8.4 and Node 20.
2. **Next:** (Optional) Add a Dockerfile for edvise-ui and switch Cloud Build to it so dev frontend runs PHP 8.4; pin Node in Cloud Build to match `.nvmrc`.
3. **Later:** (Optional) Add a dev container for the workspace and document “Open in Container” as an alternative to host-based development.

If you want, the next step can be adding the actual `.php-version` and `.nvmrc` in edvise-ui and a short “Local development” subsection to `docs/architecture.md`.
