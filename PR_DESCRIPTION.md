# feat(api): legacy school type with any-format uploads, PII check, and Edvise Schema (ES) naming

<!--- Provide a brief description of your changes in the title above. -->

## changes

<!--- Describe your changes in detail, to guide reviewers through the git diff. -->

- **Legacy school type**
  - Added `legacy_id` (nullable) to `InstTable` and to institution create/update/read APIs. Mutual exclusivity: at most one of `pdp_id`, `edvise_id`, or `legacy_id` per institution via shared `has_at_most_one_school_type()` (institutions + data router).
  - Legacy institutions use **any-format** uploads: encoding check + CSV read only, no schema validation; `schema_namespace = "legacy"`, no extension schema load. Validated output is the DataFrame as-read.
  - `LEGACY_SCHEMA_GROUP` (STUDENT, COURSE) used for allowed schemas/filenames for legacy schools.

- **Auto-assign Edvise / Legacy IDs**
  - On create, if `is_edvise` is set and `edvise_id` is empty, assign `edvise_id = "edvise_{count+1}"`. If `is_legacy` is set and `legacy_id` is empty, assign `legacy_id = "legacy_{count+1}"`. No auto-assign on PATCH. Early validation rejects requests that indicate more than one of PDP, Edvise Schema (ES), or Legacy.

- **PII check for legacy uploads**
  - Before writing to raw/ or validated/, legacy CSV column names are checked with `_is_pii_column()`. Uploads with PII-like columns (e.g. email, ssn, first_name) are rejected with a clear error and column list. `student_id` is **not** treated as PII (false positive) for all institution types.

- **Principles and refactor**
  - `create_institution` refactored into helpers to keep functions under 50 lines; added docstrings (e.g. Args/Returns for `has_at_most_one_school_type`), comment for lazy import in validation, and mypy fix for create path (single `row` variable).

- **Tests**
  - New/updated tests: `has_at_most_one_school_type`, legacy header-only CSV, legacy PII rejection → 400, create with explicit `legacy_id`, PATCH add `legacy_id`, storage bucket and Databricks setup failure paths.

- **Naming**
  - All references to the schema type (not the product) use **Edvise Schema (ES)** in docstrings, comments, and user-facing error messages to avoid confusion with "Edvise" the product.

- **Removed**
  - GET `/institutions/legacy-id/{legacy_id}` was not used and has been removed.

## context

<!--- Why are these change required? What problem does it solve? -->
<!--- If this fixes an open issue / is ticketed, put the link(s) here! -->

- **Legacy schools** (e.g. PDP/Edvise-style partners) need to upload data in **any CSV format** without strict schema validation, while we still enforce encoding and basic CSV readability and block obvious PII columns.
- **Edvise Schema (ES)** vs **Edvise (product)** needed a consistent naming convention (ES) in the codebase and in API messages to reduce confusion.
- **Auto-assign** for `edvise_id` and `legacy_id` lets Datakinders create Edvise Schema (ES) or Legacy institutions without supplying external IDs when none exist.

## questions

<!--- Ask any specific questions that you'd like reviewers to address. -->

- Is the **PII column list** (high/medium risk + false positives in `validation_error_formatter`) sufficient for legacy uploads, or should we add/remove any column names?
- Do we want **unique constraints** on `edvise_id` and `legacy_id` to avoid duplicate auto-assigned IDs under concurrent create? (Currently documented as a known limitation.)
- **DB migration**: `ALTER TABLE inst ADD COLUMN legacy_id VARCHAR(36) NULL` (or equivalent) must be run in each environment before or with this deploy—confirm migration plan and order.
