# Alembic migrations (edvise-api)

**Day-to-day guide:** how to add and test a database schema change locally.

For Cloud SQL cutover / stamp / deploy wiring, see [ALEMBIC_CUTOVER.md](./ALEMBIC_CUTOVER.md).  
For which repo owns which tables, see [DB_SCHEMA_CONTRACT.md](./DB_SCHEMA_CONTRACT.md).

---

## 30-second mental model

| Piece | Role |
|-------|------|
| `src/webapp/database.py` | SQLAlchemy models — how the **app** sees tables and reads/writes **rows** |
| `alembic/versions/*.py` | Versioned migrations — how the **database schema** changes over time |
| `alembic_version` table | Bookmark of which migration this DB has already applied |

Changing only `database.py` updates the app’s idea of the schema. **Cloud DBs do not pick that up** unless you also ship an Alembic revision and it gets applied (migrate job on deploy).

Local app startup may still use `create_all` (`ENV=LOCAL` only). That is convenient for the app, but **it is not a substitute for testing your migration**. Always exercise `alembic upgrade` as below.

---

## Before you start

1. Work from the **repo root** (`edvise-api/`), with deps installed (`uv sync`).
2. Only change **API-owned** tables (`inst`, `model`, `job`, `apikey`, …).  
   Do **not** migrate `users` (or other Laravel tables) — UI owns that DDL.
3. Prefer **nullable** new columns (or safe defaults) when you can. Harder changes (non-null, renames, big backfills) need more care.

---

## Local workflow (happy path)

### 1. Start from a DB that matches *current* `head` (without your new change)

If you do not already have a good local migration DB:

```bash
cd edvise-api
export ENV=LOCAL
rm -f alembic_local.db

# Important: ORM models should still match committed migrations
# (no unfinished column/table edits yet).
uv run alembic upgrade head
uv run alembic current
```

That creates `alembic_local.db` (SQLite) and applies all existing revisions.

If `alembic_local.db` already exists and is at head, you can skip the reset.

### 2. Change the ORM

Edit the relevant class in `src/webapp/database.py` (e.g. add a column on `ModelTable`).

### 3. Create a revision file

Use the project wrapper so revision ids look like `YYYYMMDD_<hash>` (same shape as the baseline):

```bash
uv run edvise-alembic revision -m "add_model_is_good"
```

This only **creates** a file under `alembic/versions/`. It does not change the database.

Avoid plain `uv run alembic revision` for new migrations — that generates a bare hex id.

### 4. Fill in `upgrade()` / `downgrade()`

Open the new file. Replace the `pass` stubs.

**Example — add a nullable boolean column:**

```python
def upgrade() -> None:
    op.add_column("model", sa.Column("is_good", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("model", "is_good")
```

| Symbol | Meaning |
|--------|---------|
| `op` | Alembic operations (`add_column`, `drop_column`, `create_table`, …) |
| `sa` | SQLAlchemy types / SQL helpers (`sa.Boolean()`, `sa.text("...")`) |

Match `__tablename__`, column name, type, and nullability to the ORM.

**Optional — autogenerate** (still review the file):

```bash
uv run edvise-alembic revision --autogenerate -m "add_model_is_good"
```

### 5. Apply and verify locally

```bash
export ENV=LOCAL
uv run alembic upgrade head
uv run alembic current
```

Optional checks:

```bash
# See the chain
uv run alembic history

# Confirm column exists
PYTHONPATH=src uv run python - <<'PY'
from sqlalchemy import create_engine, inspect
cols = inspect(create_engine("sqlite:///./alembic_local.db")).get_columns("model")
print([c["name"] for c in cols])
PY

# Unit checks for Alembic plumbing
uv run pytest tests/alembic -q
```

Optional undo/redo for confidence:

```bash
uv run alembic downgrade -1
uv run alembic upgrade head
```

### 6. Open a PR

Include **both**:

- ORM change in `database.py`
- New file(s) under `alembic/versions/`

After merge (on stamped envs with migrate enabled), Cloud Build runs `${ENV}-api-migrate` (`alembic upgrade head`) **before** deploying the webapp.

---

## Schema change vs data backfill

For non-trivial fills of existing rows, prefer **two revisions**:

1. **Schema only** — e.g. `op.add_column(..., nullable=True)`
2. **Backfill** — e.g. `op.execute(sa.text("UPDATE ..."))`

Small/demo changes can live in one file. Do not edit a revision that has already been applied on a shared environment — add a **new** revision instead.

---

## Common pitfalls

| Pitfall | What to do |
|---------|------------|
| Changed ORM only; no revision | Cloud/app will disagree. Always add a revision. |
| Fresh `alembic_local.db` + `upgrade head` **after** editing models | Baseline uses live `Base.metadata.create_all`, so it can create your new column early and the later `ADD COLUMN` fails. Reset/upgrade to head **before** editing models, then add the revision and upgrade again. |
| Relied only on local app `create_all` | Proves the ORM, not the migration. Run `alembic upgrade`. |
| Migrating `users` in Alembic | Don’t. Laravel owns that DDL. |
| Edited an already-applied migration | No-op on DBs that already ran it. Ship a new revision. |

---

## Command cheat sheet

All from repo root. Local DB uses `ENV=LOCAL` → `alembic_local.db`.

| Command | What it does |
|---------|----------------|
| `uv run edvise-alembic revision -m "msg"` | Create a new revision file (dated id) |
| `uv run alembic upgrade head` | Apply all pending migrations |
| `uv run alembic upgrade +1` | Apply the next one only |
| `uv run alembic downgrade -1` | Undo the latest applied revision |
| `uv run alembic current` | Show this DB’s revision |
| `uv run alembic history` | Show the revision chain |
| `uv run pytest tests/alembic -q` | Alembic unit checks |

`edvise-alembic` is a thin wrapper around Alembic that only changes how new revision ids are generated (`src/webapp/alembic_cli.py`). For `upgrade` / `current` / `history`, `uv run alembic …` and `uv run edvise-alembic …` are equivalent.

---

## Related docs

| Doc | Use when |
|-----|----------|
| [ALEMBIC_CUTOVER.md](./ALEMBIC_CUTOVER.md) | Stamp vs upgrade, Cloud Run job, enabling migrate on deploy |
| [DB_SCHEMA_CONTRACT.md](./DB_SCHEMA_CONTRACT.md) | Table ownership between edvise-api and edvise-ui |
| [src/webapp/README.md](../src/webapp/README.md) | Broader webapp / local app setup |
