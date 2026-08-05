# Alembic cutover runbook (edvise-api)

Phase 1: API-owned tables are migrated with Alembic. Shared Cloud SQL `all_tables`
still holds UI tables; Laravel continues to own `users` DDL.

See also: [DB_SCHEMA_CONTRACT.md](./DB_SCHEMA_CONTRACT.md).

## What shipped in code

| Piece | Location |
|-------|----------|
| Alembic config | `alembic.ini`, `alembic/` |
| Baseline revision | `alembic/versions/20260803_596894_baseline_api_tables.py` |
| Cloud Run job | `${ENV}-api-migrate` (create via terraform or `gcloud run jobs create`) |
| Cloud Build (live path) | `cloudbuild-webapp.yaml` on triggers like `edvise-api-web-dev` |
| Skip flag | `_SKIP_API_MIGRATE` substitution (default `"true"` in the YAML) |
| `create_all` | LOCAL only (`database.py` `setup_db`) |

**Important:** pushes to `develop` deploy via **`edvise-api-web-dev`** + `cloudbuild-webapp.yaml`,
not the terraform-inline `dev-webapp` trigger. Enable auto-migrate by setting that trigger’s
`_SKIP_API_MIGRATE=false` **after** stamp (and only once `${env}-api-migrate` exists).

Default in YAML: **`_SKIP_API_MIGRATE=true`** so QA / unstamped envs skip migrate.

## Local verification (before merge)

```bash
cd edvise-api
export ENV=LOCAL
rm -f alembic_local.db
uv run alembic upgrade head          # creates API tables in alembic_local.db
uv run alembic stamp head            # no-op if already at head
# Confirm users was not created:
PYTHONPATH=src uv run python -c "
from sqlalchemy import create_engine, inspect
print(sorted(inspect(create_engine('sqlite:///./alembic_local.db')).get_table_names()))
"
```

Stamp-then-upgrade on an “existing” DB:

```bash
rm -f alembic_local.db
# Pretend tables already exist:
PYTHONPATH=src uv run python -c "
from sqlalchemy import create_engine
from webapp.database import Base
e = create_engine('sqlite:///./alembic_local.db')
tables = [Base.metadata.tables[n] for n in (
  'inst','apikey','account_history','file','batch',
  'file_batch_association_table','model','schema_registry','job')]
Base.metadata.create_all(e, tables=tables)
"
uv run alembic stamp head
uv run alembic upgrade head   # should be a no-op
```

Or run the automated check: `uv run pytest tests/alembic -q`

## Dev cutover (merge day)

1. Merge this PR to `develop` (auto-deploy; migrate step **skipped** while
   `skip_api_migrate=true`).
2. Ensure terraform has applied so `${env}-api-migrate` exists.
   - The `${env}-terraform` Cloud Build trigger is **manual** (not push-on-develop).
   - Run that trigger, or `terraform apply` in `terraform/environments/dev`.
3. Export / compare **dev** DDL to the schema contract if not already done.
4. Stamp (pick one):

   **Option A — Cloud Run job** (required: refresh the job image first).

   Terraform creates `${ENV}-api-migrate` once, then **ignores** later image
   changes on the job. While Cloud Build skips migrate, it never updates that
   image either. Before stamp, point the job at a post-merge webapp image that
   contains Alembic:

   ```bash
   # Use the COMMIT_SHA (or :latest) from the merge deploy that includes alembic/
   gcloud run jobs update ${ENV}-api-migrate \
     --image=${REGION}-docker.pkg.dev/${PROJECT}/edvise-api/webapp:${COMMIT_SHA} \
     --region=${REGION}
   ```

   Then stamp (job command is already `alembic`; override **args** only):

   ```bash
   gcloud run jobs execute ${ENV}-api-migrate \
     --region=${REGION} \
     --args=stamp,head \
     --wait
   ```

   Verify with a no-op upgrade:

   ```bash
   gcloud run jobs execute ${ENV}-api-migrate \
     --region=${REGION} \
     --wait
   ```

   (Default job args are `upgrade head`.)

   **Option B — Cloud SQL Auth Proxy + local Alembic** with `ENV=DEV` and DB_* / certs set:

   ```bash
   alembic stamp head
   alembic upgrade head   # expect no-op
   ```

5. Verify stamp (pick one):
   - `gcloud run jobs execute ${ENV}-api-migrate --region=${REGION} --args=current --wait`
     and check logs for `20260803_596894`
   - or `SELECT * FROM alembic_version;` → `20260803_596894`
6. Enable auto-migrate on the **live** webapp trigger (Console):
   - Trigger: `edvise-api-web-dev` (project `dev-sst-02`)
   - Substitution: `_SKIP_API_MIGRATE` = `false`
   - Leave other env triggers at `true` until those DBs are stamped and have
     `${_ENVIRONMENT}-api-migrate`.
   - Do **not** rely on a full `dev-terraform` apply to flip this; that apply can
     reconcile large unrelated drift. Prefer Console (or a narrowly scoped change).
7. Re-run webapp Cloud Build (or push a no-op commit); confirm the
   `RUN api-migrate job` step updates/executes `${_ENVIRONMENT}-api-migrate` with
   `--wait` before deploy.
8. Smoke: login/auth + inference run.

## Staging cutover (after short dev soak)

1. Cloud SQL **on-demand backup**
2. Ensure staging `${ENV}-api-migrate` exists; refresh image, then stamp
3. Set that staging webapp trigger’s `_SKIP_API_MIGRATE=false` (Console), leave
   prod/other envs at `true` until stamped
4. Manual Cloud Build for webapp
5. Smoke auth, inference, UI pages that use run metadata

Staging `job` already has `VARCHAR(255)` `model_run_id` and FK to `model` — no reconciliation ALTER required unless **dev** differs from the contract.

## Stamp vs upgrade

| Command | Effect |
|---------|--------|
| `alembic stamp head` | Record version only; **no SQL** |
| `alembic upgrade head` | Apply pending migrations |

Existing DBs that already have API tables must **stamp** once before the first deploy that runs `upgrade`.

## Creating future migrations

```bash
# Prefer Alembic's generator so revision ids stay unique (random hex by default):
uv run alembic revision -m "add_foo_column"
```

This baseline uses a date + short hash (`20260803_596894`) for readability;
new revisions from `alembic revision` typically look like `a1b2c3d4e5f6_…`
without a date prefix — that is expected and still unique.

CI / pytest also checks that revision ids are unique and match the filename
prefix (`tests/alembic/revision_uniqueness_test.py`).

## Baseline and future model changes

The baseline revision builds tables from the **current** SQLAlchemy `Base.metadata`
(filtered to API tables). That is convenient for cutover but is a footgun once ORM
models diverge: greenfield `upgrade head` can create columns in the baseline step
and then fail on a later ALTER. After this lands:

- Any ORM column/table change for API-owned tables **must** ship as a **new** Alembic revision.
- Before the first post-cutover ALTER, keep models matching the baseline **or** freeze the baseline as explicit `op.create_table` DDL.
- Do not rely on `create_all` in cloud (LOCAL only).
- Baseline `downgrade()` is refused (would drop shared API tables).

## Greenfield / empty MySQL

`account_history` has an FK to `users`. On empty MySQL:

1. Run **Laravel** migrations first (`users` and UI tables).
2. Then `alembic upgrade head` for API tables.

Shared Cloud SQL cutover environments already have `users`; stamp-first is the production path.

## Job retries

Both `${env}-migrate` (Laravel) and `${env}-api-migrate` (Alembic) use
`max_retries = 0` so failed DDL is not auto-retried.

## Exclusions

Alembic must not manage: `users`, Laravel-only tables, `*_backup` tables
(`inst_custom_to_legacy_backup`, `schema_registry_custom_ext_backup`).

Laravel-only tables are not in `Base.metadata`, so autogenerate will not propose them.
`include_object` still excludes `users` (present on `AccountTable`) and any `*_backup`
names if they appear during reflection.
