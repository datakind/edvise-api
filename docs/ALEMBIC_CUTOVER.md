# Alembic cutover runbook (edvise-api)

Phase 1: API-owned tables are migrated with Alembic. Shared Cloud SQL `all_tables`
still holds UI tables; Laravel continues to own `users` DDL.

See also: [DB_SCHEMA_CONTRACT.md](./DB_SCHEMA_CONTRACT.md).

## What shipped in code

| Piece | Location |
|-------|----------|
| Alembic config | `alembic.ini`, `alembic/` |
| Baseline revision | `alembic/versions/20260803_baseline_api_tables.py` |
| Cloud Run job | `${ENV}-api-migrate` (terraform `deployment` jobs) |
| Cloud Build | webapp trigger runs api-migrate unless `_SKIP_API_MIGRATE=true` |
| `create_all` | LOCAL only (`database.py` `setup_db`) |

Default: **`_SKIP_API_MIGRATE=true`** so auto-deploy does not run `upgrade` before stamp.

## Local verification (before merge)

```bash
cd edvise-api
export ENV=LOCAL
rm -f alembic_local.db
uv run alembic upgrade head          # creates API tables in alembic_local.db
uv run alembic stamp head            # no-op if already at head
# Confirm users was not created:
sqlite3 alembic_local.db ".tables"   # should list API tables only, not users
```

Stamp-then-upgrade on an “existing” DB:

```bash
rm -f alembic_local.db
# Pretend tables already exist:
uv run python -c "
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

## Dev cutover (merge day)

1. Merge this PR to `develop` (auto-deploy; migrate step **skipped**).
2. Ensure terraform has applied so `${env}-api-migrate` exists (terraform Cloud Build trigger or manual apply).
3. Export / compare **dev** DDL to the schema contract if not already done.
4. Stamp (pick one):

   **Option A — Cloud Run job args override** (after image with Alembic is deployed):

   ```bash
   gcloud run jobs execute DEV_OR_ENV-api-migrate \
     --region=REGION \
     --update-env-vars=... \  # usually already set on the job
     --args=stamp,head
   ```

   If the job command is fixed as `alembic upgrade head`, override:

   ```bash
   gcloud run jobs execute ${ENV}-api-migrate \
     --region=${REGION} \
     --command=alembic \
     --args=stamp,head
   ```

   (Exact override flags depend on current `gcloud` version — see `gcloud run jobs execute --help`.)

   **Option B — Cloud SQL Auth Proxy + local Alembic** with `ENV=DEV` and DB_* / certs set, then:

   ```bash
   alembic stamp head
   alembic upgrade head   # expect no-op
   ```

5. Verify: `SELECT * FROM alembic_version;` → `20260803_baseline`
6. Flip skip off: set Cloud Build trigger substitution `_SKIP_API_MIGRATE=false`
   (Console → Trigger → Substitutions, or terraform change + apply).
7. Re-run webapp Cloud Build (or push a no-op commit); confirm migrate step succeeds.
8. Smoke: login/auth + inference run.

## Staging cutover (after short dev soak)

1. Cloud SQL **on-demand backup**
2. Stamp staging the same way
3. Set staging `_SKIP_API_MIGRATE=false`
4. Manual Cloud Build for webapp
5. Smoke auth, inference, UI pages that use run metadata

Staging `job` already has `VARCHAR(255)` `model_run_id` and FK to `model` — no reconciliation ALTER required unless **dev** differs from the contract.

## Stamp vs upgrade

| Command | Effect |
|---------|--------|
| `alembic stamp head` | Record version only; **no SQL** |
| `alembic upgrade head` | Apply pending migrations |

Existing DBs that already have API tables must **stamp** once before the first deploy that runs `upgrade`.

## Exclusions

Alembic must not manage: `users`, Laravel-only tables, `*_backup` tables
(`inst_custom_to_legacy_backup`, `schema_registry_custom_ext_backup`).
