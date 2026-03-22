# Overview

This repo contains:

* [src/webapp/](https://github.com/datakind/edvise-api/tree/develop/src/webapp): The source code for the SST API (which is called by the SST frontend and by any direct API callers)
* [src/worker/](https://github.com/datakind/edvise-api/tree/develop/src/worker): The source code for the SFTP Worker (which calls the SST API)
* [terraform/]
(https://github.com/datakind/edvise-api/tree/develop/terraform): The Terraform configuration for the SST API/Frontend and other GCP resources including Cloud SQL setup, networking setup, secrets setup
* .devcontainer/ and .vscode/: which allow easy setup if you are using VSCode as your IDE.
* [devtools/](https://github.com/datakind/edvise-api/tree/develop/devtools): is a place to put utility scripts
* .github/: contains mostly copied over files when this directory was forked from the student-success-tool repo, so likely much of it is outdated. The only Github action we've added is the [webapp-and-worker-precommit](https://github.com/datakind/edvise-api/blob/develop/.github/workflows/webapp-and-worker-precommit.yml) which is run on every push to develop. This action contains a python linter (we use [black](https://black.readthedocs.io/en/stable/)), and automated runs of the unit tests in the src/webapp/ and src/worker/ directories.
* Additionally, [pyproject.toml](https://github.com/datakind/edvise-api/blob/develop/pyproject.toml) and [uv.lock](https://github.com/datakind/edvise-api/blob/develop/uv.lock) are important for dependency management. At time of writing, the worker is just skeleton code so there's no separate dependency management. In the long-term consider separating out the dependency management for the two programs. 


NOTE: this repo was forked from the https://github.com/datakind/student-success-tool repo, which means some of the static files (e.g. CONTRIBUTING.md) may be outdated or may include irrelevant information from that repo. Please update those as you see fit. For information about the specific items listed above, defer to the specific readmes in the relevant directory.

## Local edvise development override

Production uses a pinned Git reference for `edvise`. For local development, use an
editable install after syncing the environment.

1. Clone `edvise` alongside `edvise-api` (so `../edvise` exists).
2. Run `uv sync`.
3. Override locally: `uv pip install -e ../edvise`

To revert back to the pinned Git dependency, run `uv sync --reinstall-package edvise`.

## Deploying to dev (GCP Cloud Run)

The **dev API** runs on **Google Cloud Run** in the dev GCP project. Pushing to the **`develop`** branch on this repo triggers **Cloud Build**, which builds the Docker image and deploys the webapp. **GitHub Actions** (`.github/workflows/webapp-and-worker-precommit.yml`) only run lint and tests on `develop`; they do **not** deploy.

**Databricks** is not the deploy target for this API—the app **calls** Databricks. Deploying means updating the Cloud Run service.

### Quick links (dev project)

| What | URL |
|------|-----|
| Dev Swagger / OpenAPI | `https://dev-sst.datakind.org/api/v1/docs` |
| Cloud Build triggers | [Triggers](https://console.cloud.google.com/cloud-build/triggers?project=dev-sst-02) |
| Build history | [History](https://console.cloud.google.com/cloud-build/builds?project=dev-sst-02) |
| Cloud SQL | [Instances](https://console.cloud.google.com/sql?project=dev-sst-02) |
| Secret Manager | [Secrets](https://console.cloud.google.com/security/secret-manager?project=dev-sst-02) |

Dev is behind **Identity-Aware Proxy (IAP)**; sign in when prompted. Trigger names created by Terraform are typically **`dev-webapp`**, **`dev-worker`**, and **`dev-frontend`** (frontend is a separate repo). If you do not see triggers, confirm the GCP project selector is the **dev** project, not staging or prod.

### Deploy the API

1. Merge into **`develop`** and push:
   ```bash
   git checkout develop
   git pull origin develop
   git merge <your-branch>
   git push origin develop
   ```
2. In **Cloud Build → History**, confirm the **dev-webapp** build **succeeded**.
3. If no build ran or you need a redeploy: **Cloud Build → Triggers → dev-webapp → Run**.
4. Verify behavior in the [dev Swagger UI](https://dev-sst.datakind.org/api/v1/docs). Use a hard refresh or private window if the schema looks stale.

### Database migrations (Cloud SQL)

Schema changes (e.g. a new column) must be applied to the **MySQL** database the dev API uses—not the local SQLite DB.

1. Open **Cloud SQL Studio** (or another approved path) for the dev instance (e.g. **`dev-db-instance`**) in the dev project.
2. Select database **`all_tables`** (see Terraform `database_name` default in `terraform/environments/dev/`).
3. Run your DDL, for example:
   ```sql
   ALTER TABLE inst ADD COLUMN legacy_id VARCHAR(36) NULL;
   ```
4. If MySQL reports the column already exists, you can skip that statement.
5. Apply the same changes to **staging** and **prod** databases when you promote releases there.

**Connecting with `gcloud sql connect` (MySQL):** omit `--database`; it is not valid for MySQL. Example (from Cloud Shell with the dev project selected):

```bash
gcloud sql connect dev-db-instance --user=dev-sst-02-sql-user
```

When prompted for the password, use the value stored in **LastPass** for this SQL user. If you do not have access, ask the tech team (e.g. Emma) to share it with you.

After connecting, select the app database and run SQL:

```sql
USE all_tables;
```

**Cloud SQL Studio** uses your Google identity and avoids handling the SQL password in the terminal. **Secret Manager** (e.g. `{environment}-db-password`) is also used by services and automation; credentials are **not** committed to this repo.

### Local run and tests (summary)

See [src/webapp/README.md](src/webapp/README.md) for full detail. In short:

- `uv sync --all-extras --dev` from the repo root.
- Tests: `uv run coverage run -m pytest -v -s ./src/webapp/` then `uv run coverage report -m`.
- Run the API: set `ENV_FILE_PATH` to the absolute path of `src/webapp/.env` (copy from `.env.example`), then `uv run fastapi dev src/webapp/main.py --port 8000`. Open `http://127.0.0.1:8000/api/v1/docs`.
- **GCS signed URLs** (e.g. upload-url) require Application Default Credentials locally (`gcloud auth application-default login` or `GOOGLE_APPLICATION_CREDENTIALS`).

### Release checklist (dev)

- [ ] Code merged to `develop`
- [ ] **dev-webapp** Cloud Build succeeded
- [ ] Required SQL applied on dev **`all_tables`** if the release needs schema changes
- [ ] Dev API / Swagger smoke-tested
