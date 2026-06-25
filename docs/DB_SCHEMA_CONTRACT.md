# Database schema contract (edvise-ui + edvise-api)

Canonical DDL and ownership rules for the **shared Cloud SQL database** used by `edvise-ui` (Laravel) and `edvise-api` (FastAPI) in deployed environments.

> **Verify in staging:** Before Alembic cutover, run `SHOW CREATE TABLE` for each table below on staging Cloud SQL and reconcile any drift with this document. This file is derived from repo migrations/ORM as of Phase 0; live DDL is authoritative.

See also: full migration plan in workspace `github/.cursor/docs/database_table_ownership.md`.

---

## Migration ownership

| Table(s) | DDL owner | Migration tool | Runtime writers | Runtime readers |
|----------|-----------|----------------|-----------------|-----------------|
| `users` | **edvise-ui** | Laravel | UI (primary), API (auth) | Both |
| `job` | **edvise-api** (Phase 1+) | Alembic | API | API, UI (read-only until Phase 1.5) |
| UI-only tables | **edvise-ui** | Laravel | UI | UI |
| API-only tables | **edvise-api** | Alembic | API | API |

**Rules:**

- API Alembic must **never** `CREATE TABLE users`.
- UI Laravel must **not** add new migrations for `job` after Phase 1 cutover (bootstrap migration may remain idempotent).
- Shared column changes require **paired PRs** (see plan doc).

---

## Greenfield bootstrap order

1. **API** `alembic upgrade head` — creates `inst`, `model`, `job`, and all API-owned tables.
2. **UI** `php artisan migrate` — creates `users`, teams, and UI-only tables.
3. Seed institutions via API; users via UI registration.

---

## Shared table: `users`

**DDL owner:** edvise-ui (`database/migrations/`).  
**ORM (API):** `AccountTable` in `src/webapp/database.py`.

### Canonical columns

| Column | Type (MySQL) | Nullable | Default | Notes |
|--------|--------------|----------|---------|-------|
| `id` | `CHAR(36)` / UUID | NO | — | PK |
| `name` | `VARCHAR(255)` | NO | — | |
| `email` | `VARCHAR(255)` | NO | — | UNIQUE |
| `google_id` | `VARCHAR(255)` | YES | NULL | |
| `azure_id` | `VARCHAR(255)` | YES | NULL | |
| `email_verified_at` | `DATETIME` | YES | NULL | |
| `password` | `VARCHAR(255)` | NO | — | |
| `two_factor_secret` | `TEXT` | YES | NULL | |
| `two_factor_recovery_codes` | `TEXT` | YES | NULL | |
| `two_factor_confirmed_at` | `DATETIME` | YES | NULL | If Fortify 2FA confirm enabled |
| `remember_token` | `VARCHAR(100)` | YES | NULL | |
| `inst_id` | `CHAR(36)` | YES | NULL | Logical FK to `inst.id`; **no DB FK in Laravel** |
| `current_team_id` | `CHAR(36)` | YES | NULL | Jetstream |
| `access_type` | `VARCHAR(36)` | YES | NULL | |
| `accepted_terms` | `BOOLEAN` / `TINYINT(1)` | NO | `0` | UI-only auth flow; API ORM must mirror |
| `invite_validated` | `BOOLEAN` / `TINYINT(1)` | NO | `0` | UI-only invite flow; API ORM must mirror |
| `created_at` | `DATETIME` | YES | NULL | |
| `updated_at` | `DATETIME` | YES | NULL | |

### Known drift

| Item | UI | API ORM | Policy |
|------|----|---------|--------|
| `inst_id` FK | No DB constraint | `ForeignKey("inst.id", ON DELETE CASCADE)` in ORM only | Do not add UI FK (local SQLite isolation) |
| `accepted_terms`, `invite_validated` | Laravel migrations | Must match in `AccountTable` | Paired PR on any change |

**Source migrations (UI):** `2014_10_12_000000_create_users_table.php`, `2025_06_06_142209_add_accepted_terms_to_users_table.php`, `2025_08_24_210106_add_invite_validated_to_users_table.php`.

---

## Shared table: `job`

**DDL owner:** edvise-api (Alembic, Phase 1+).  
**ORM (API):** `JobTable` in `src/webapp/database.py`.  
**UI model:** `App\Models\Job` (read paths only until Phase 1.5).

### Canonical columns (target)

| Column | Type (MySQL) | Nullable | Default | Notes |
|--------|--------------|----------|---------|-------|
| `id` | `BIGINT` | NO | AUTO_INCREMENT | PK; inference `run_id` |
| `model_id` | `CHAR(36)` | NO | — | FK → `model.id` (API); UI bootstrap may lack FK |
| `created_by` | `CHAR(36)` | NO | — | User UUID |
| `triggered_at` | `DATETIME` | NO | — | |
| `batch_name` | `VARCHAR(255)` | NO | — | |
| `output_filename` | `VARCHAR(255)` | YES | NULL | |
| `err_msg` | `VARCHAR(255)` | YES | NULL | |
| `completed` | `BOOLEAN` | YES | NULL | |
| `output_valid` | `BOOLEAN` | YES | NULL | |
| `model_run_id` | `VARCHAR(255)` | YES | NULL | Databricks training run id |
| `model_version` | `VARCHAR(255)` | YES | NULL | |

### Indexes (API ORM / Laravel)

- `model_id` (index)
- `(model_id, completed, output_valid)` composite
- `triggered_at`

### Known drift (reconcile via Alembic after stamp)

| Item | UI migration | API ORM | Reconciliation |
|------|--------------|---------|----------------|
| `model_run_id` length | `VARCHAR(150)` | `VARCHAR(255)` | Prefer **255**; `ALTER` if staging has 150 |
| `model_id` FK | No FK | FK → `model.id` | Alembic `ALTER` if missing in prod |
| Table creator | Laravel `2025_10_29_*` may have run first | `create_all` may differ | Compare `SHOW CREATE TABLE` per env |

---

## edvise-ui only tables (Laravel DDL)

| Table | Purpose |
|-------|---------|
| `teams`, `team_user`, `team_invitations` | Jetstream |
| `personal_access_tokens` | Sanctum |
| `password_reset_tokens` | Auth |
| `sessions` | DB sessions |
| `failed_jobs` | Queue failures |
| `dk_api_tokens` | Backend API token storage |
| `data_dictionary` | UI feature |
| `invites` | Invite allowlist |
| `migrations` | Laravel history |

**Do not deploy:** `institutions` — duplicate of API `inst`; removed in Phase 0.

---

## edvise-api only tables (Alembic DDL)

| Table | Purpose |
|-------|---------|
| `inst` | Institutions (canonical) |
| `apikey` | API key auth |
| `account_history` | Audit trail (mostly unimplemented) |
| `file`, `batch`, `file_batch_association_table` | Uploads |
| `model` | ML models per institution |
| `schema_registry` | Versioned JSON schemas |
| `job` | Inference run records |
| `alembic_version` | Alembic history (after Phase 1) |

---

## Change process checklist

### `users` column change

- [ ] Laravel migration (`edvise-ui`)
- [ ] `AccountTable` update (`edvise-api`)
- [ ] This contract updated
- [ ] Both READMEs accurate
- [ ] Deploy UI migrate before API deploy

### `job` column change

- [ ] Alembic migration (`edvise-api`)
- [ ] UI `Job` model updated if UI reads the field
- [ ] This contract updated
- [ ] No new Laravel `job` migration
- [ ] Deploy API `api-migrate` before UI deploy
