## 1.3.0 (2026-07-22)
- feat: validate ES uploads with bronze `dataio` converters and Pandera (#273)
- feat: apply bronze `config.toml` `grade_map` before ES course Pandera (#273)
- feat: add PATCH endpoint to archive models (`/{inst_id}/models/{model_name}/archive`) (#274)
- feat: expose `archived` on `/models` endpoint responses (#275)

## 1.2.0 (2026-07-15)
- feat: list institution bronze volume CSVs via `/input/bronze-datasets` (#206)
- feat: import a bronze volume dataset into GCS via `/input/upload-from-volume-to-gcs-bucket` (#206)
- feat: add DELETE endpoint for model runs (`/{inst_id}/models/{model_name}/run/{job_run_id}`) (#268)
- feat: seed models and runs from `local_inst_data.json` for local development (#268)
- chore: pin `edvise` dependency to tag `v1.4.8`
- ci: add post-release shared workflow

## 1.1.0 (2026-06-29)
- feat: hook up Edvise Schema (ES) inference to the API (#253)
- feat: use batch parameters for run-inference endpoint (GenAI/Edvise/Legacy) (#257)
- feat: add `is_genai_institution` parameter to GenAI/Edvise inference job for SSoT (#254)
- feat: use `batch_id` for subfolder naming in GCS→bronze async job (#256)
- feat: expose `model_run_id` and `model_version` on RunInfo endpoints (#260)
- feat: mirror `accepted_terms` and `invite_validated` on `AccountTable` (#263)
- fix: sync ES pipeline rename and allow greater flexibility for legacy/GenAI/ES uploads (#262)
- fix: coerce Databricks model version to `str` for RunInfo responses (#264)
- refactor: remove API JSON schema validation from upload pipeline (#246)
- docs: add staging-verified shared DB schema contract for UI and API (#258)

## 1.0.0 (2026-06-16)
- feat: add GenAI as a fourth schema type (alongside PDP, Edvise, and Legacy) in API and uploads (#244)
- feat: legacy school inference Databricks job trigger (#212)
- feat: validate Edvise uploads against repo schemas (#242)
- feat: trigger Databricks GCS→bronze sync after file validation (Edvise/Legacy) (#239)
- feat: simplify create-model request to name only (#238)
- feat: add `clear_cache` option to `/eda` endpoint (#233)
- feat: establish `pyproject.toml` as canonical Edvise API version (#243)
- fix: derive PDP batch schema configs from institution schemas (#247)
- chore: bump `edvise` dependency to 1.2.0
- chore: standardize Python 3.12 across project, CI, devcontainer, and Docker
- ci: adopt DataKind shared workflows (#245)
- docs: inherit org community health files (#237)
