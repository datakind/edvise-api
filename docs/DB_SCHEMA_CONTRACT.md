# Database schema contract (edvise-ui + edvise-api)

Canonical DDL and ownership rules for the **shared Cloud SQL database** used by `edvise-ui` (Laravel) and `edvise-api` (FastAPI) in deployed environments.

**Staging verified:** 2026-06-24 against Cloud SQL database `all_tables` (staging instance). Evidence: workspace `github/docs/dbtables/` (`SHOW CREATE TABLE` CSV exports).

See also: migration plan in workspace `github/.cursor/docs/database_table_ownership.md`.

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

## Staging verification summary (2026-06-24)

| Finding | Implication |
|---------|-------------|
| `users` has DB FK `inst_id` → `inst.id` ON DELETE CASCADE | Present on staging (likely from historical API `create_all`). UI migrations do not add it; **do not drop** without explicit decision. |
| `job.model_run_id` is `VARCHAR(255)` with FK to `model.id` | Matches API ORM. **No `ALTER` needed on staging** before Alembic stamp. |
| `job` lacks composite / `triggered_at` indexes from UI migration | Optional future Alembic migration for perf only — not blocking cutover. |
| `institutions` table absent | Safe to remove UI migration (PR 5). |
| `inst_custom_to_legacy_backup`, `schema_registry_custom_ext_backup` exist | Not in contract inventory; **exclude from Alembic** unless explicitly adopted. |

**Still required:** repeat `SHOW CREATE TABLE` / dump on **dev** `all_tables` before dev Alembic stamp (PR 11).

---

## Greenfield bootstrap order

1. **API** `alembic upgrade head` — creates `inst`, `model`, `job`, and all API-owned tables.
2. **UI** `php artisan migrate` — creates `users`, teams, and UI-only tables.
3. Seed institutions via API; users via UI registration.

---

## Shared table: `users`

**DDL owner:** edvise-ui (`database/migrations/`).  
**ORM (API):** `AccountTable` in `src/webapp/database.py`.

### Canonical columns (staging `all_tables`, 2026-06-24)

| Column | Type (MySQL) | Nullable | Default | Notes |
|--------|--------------|----------|---------|-------|
| `id` | `CHAR(32)` | NO | — | PK; UUID without dashes on staging |
| `inst_id` | `CHAR(32)` | YES | NULL | Index; **FK → `inst.id` ON DELETE CASCADE on staging** |
| `name` | `VARCHAR(255)` | NO | — | |
| `email` | `VARCHAR(255)` | NO | — | UNIQUE |
| `invite_validated` | `TINYINT(1)` | NO | `0` | |
| `google_id` | `VARCHAR(255)` | YES | NULL | |
| `azure_id` | `VARCHAR(255)` | YES | NULL | |
| `email_verified_at` | `DATETIME` | YES | NULL | |
| `password` | `VARCHAR(255)` | NO | — | |
| `two_factor_secret` | `TEXT` | YES | NULL | |
| `two_factor_recovery_codes` | `TEXT` | YES | NULL | |
| `two_factor_confirmed_at` | `DATETIME` | YES | NULL | |
| `remember_token` | `VARCHAR(255)` | YES | NULL | Laravel migration allows 100; staging uses 255 |
| `current_team_id` | `CHAR(36)` | YES | NULL | |
| `access_type` | `VARCHAR(36)` | YES | NULL | |
| `accepted_terms` | `TINYINT(1)` | NO | `0` | API ORM must mirror |
| `created_at` | `DATETIME` | YES | `now()` on staging | |
| `updated_at` | `DATETIME` | YES | NULL | |

### Constraints (staging)

```sql
CONSTRAINT `users_ibfk_1` FOREIGN KEY (`inst_id`) REFERENCES `inst` (`id`) ON DELETE CASCADE
```

**Policy:** UI Laravel migrations must not rely on this FK for local SQLite. Staging/prod may retain it from API history. New UI migrations must not drop it without coordinated review.

**Source migrations (UI):** `2014_10_12_000000_create_users_table.php`, `2025_06_06_142209_add_accepted_terms_to_users_table.php`, `2025_08_24_210106_add_invite_validated_to_users_table.php`.

---

## Shared table: `job`

**DDL owner:** edvise-api (Alembic, Phase 1+).  
**ORM (API):** `JobTable` in `src/webapp/database.py`.  
**UI model:** `App\Models\Job` (read paths only until Phase 1.5).

### Canonical columns (staging `all_tables`, 2026-06-24)

| Column | Type (MySQL) | Nullable | Default | Notes |
|--------|--------------|----------|---------|-------|
| `id` | `BIGINT` | NO | AUTO_INCREMENT | PK; inference `run_id` |
| `model_id` | `CHAR(32)` | NO | — | FK → `model.id` ON DELETE CASCADE |
| `created_by` | `CHAR(32)` | NO | — | User UUID |
| `triggered_at` | `DATETIME` | NO | — | |
| `batch_name` | `VARCHAR(255)` | NO | — | |
| `output_filename` | `VARCHAR(255)` | YES | NULL | |
| `err_msg` | `VARCHAR(255)` | YES | NULL | |
| `completed` | `TINYINT(1)` | YES | NULL | |
| `output_valid` | `TINYINT(1)` | YES | NULL | |
| `model_run_id` | `VARCHAR(255)` | YES | NULL | |
| `model_version` | `VARCHAR(255)` | YES | NULL | |

### Constraints and indexes (staging)

```sql
PRIMARY KEY (`id`)
KEY `model_id` (`model_id`)
CONSTRAINT `job_ibfk_1` FOREIGN KEY (`model_id`) REFERENCES `model` (`id`) ON DELETE CASCADE
```

### Optional vs UI migration (not blocking)

| Item | UI Laravel migration | Staging | Action |
|------|----------------------|---------|--------|
| `model_run_id` length | `VARCHAR(150)` | `VARCHAR(255)` | **No action on staging** — already 255 |
| `model_id` FK | Not in UI migration | Present | **No action on staging** |
| Composite `(model_id, completed, output_valid)` | In UI migration | Absent | Optional Alembic index migration later |
| Index on `triggered_at` | In UI migration | Absent | Optional Alembic index migration later |

---

## Tables on staging outside Alembic scope

| Table | Notes |
|-------|-------|
| `inst_custom_to_legacy_backup` | One-off backup; do not include in baseline autogenerate |
| `schema_registry_custom_ext_backup` | One-off backup; do not include in baseline autogenerate |

---

## edvise-ui only tables (Laravel DDL)

Verified on staging 2026-06-24: `teams`, `team_user`, `team_invitations`, `personal_access_tokens`, `password_reset_tokens`, `sessions`, `failed_jobs`, `dk_api_tokens`, `data_dictionary`, `invites`, `migrations`.

**Removed in Phase 0:** `institutions` — never existed on staging; duplicate of API `inst`.

---

## edvise-api only tables (Alembic DDL)

Verified on staging 2026-06-24: `inst`, `apikey`, `account_history`, `file`, `batch`, `file_batch_association_table`, `model`, `schema_registry`, `job`.

After Phase 1: `alembic_version`.

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
