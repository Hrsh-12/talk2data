# Project: Child health LLM-to-SQL

**Goal:** Ingest child health data into a SQL database and support natural-language questions over it (via LLM-generated SQL), including precomputed nutrition labels for consistent querying.

---

## Current state

| Area | Status |
|------|--------|
| **CSV → DB** | Active build script exists: `scripts/build_nutrition_db.py` creates `database/nutrition_data.duckdb` from labeled CSV. |
| **Labels** | CSV labeling script is available: `scripts/add_nutrition_labels.py` writes month-wise labels to `data/cleaned_dataset_with_labels.csv`. |
| **LLM-to-SQL** | Query script `scripts/llm_to_sql.py` runs LangChain SQL agent over persisted DuckDB. |

**Decisions:** (1) Use lookup-driven precomputed labels in a post-ETL Step 2 to stabilize NL-to-SQL results. (2) Keep raw measurements and derived labels side-by-side in DB. (3) Project state is tracked here; codebase details in [CODEBASE.md](./CODEBASE.md).

---

## CSV file

| Property | Value |
|----------|--------|
| **Path** | `data/3_months_UP_0m_6y_data.csv` |
| **Rows** | ~18.3 million (one per child) |
| **Columns** | 23 |
| **Geography** | Uttar Pradesh only |
| **Time range** | February, March, April 2024 |
| **Granularity** | One row per beneficiary; monthly metrics in wide columns |

**Columns (summary):** `beneficiary_id`, `dob`, `gender`, `birth_height`, `birth_weight`; for each of `feb24`, `mar24`, `apr24`: `{month}_status`, `{month}_height`, `{month}_weight`, `{month}_height_weight_entered_date`, `{month}_hb`, `{month}_hb_test_date`. Hb columns are effectively empty; birth measurements are ~20% complete.

**Format:** Wide (one column per month per metric). ETL unpivots to one row per beneficiary per month.

**Labeled CSV artifact:** `scripts/add_nutrition_labels.py` enriches `data/cleaned_dataset.csv` with month-wise stunting/underweight/wasting columns and writes `data/cleaned_dataset_with_labels.csv`.

---

## Database

| Property | Value |
|----------|--------|
| **Location (active LLM DB)** | `database/nutrition_data.duckdb` |
| **Engine** | DuckDB (single file) |
| **Produced by** | `scripts/build_nutrition_db.py` from `data/cleaned_dataset_with_labels.csv`. |

Active table for LLM querying:

- **`nutrition_data`** — Wide-format beneficiary table with Feb/Mar/Apr anthropometric metrics, admin hierarchy, and precomputed nutrition label columns (e.g. `*_is_underweight`, `*_is_sam`).

Legacy DuckDB artifact retained in repo:

- `database/child_health.duckdb` (older ETL output; not used by current LLM query script).

**Tables:**

- **`beneficiaries`** — One row per child: `beneficiary_id` (PK), `dob`, `gender`, `birth_height`, `birth_weight`.
- **`monthly_measurements`** — One row per child per month: `beneficiary_id`, `month`, `status`, `height`, `weight`, `measurement_date`, `age_days`. PK: `(beneficiary_id, month)`.
- **`stunting_lookup`** — From PDF: `sex`, `age_group`, `day`, `h_severe_max`, `h_normal_min`.
- **`underweight_lookup`** — From PDF: `sex`, `age_group`, `day`, `w_severe_max`, `w_normal_min`.
- **`wasting_lookup`** — From PDF: `sex`, `age_band`, `height_cm`, `sam_upper`, `mam_upper`, `normal_upper`, `overweight_upper`.
- **`monthly_child_labels`** — Derived one row per child per month with measurement coverage flags (`has_height`, `has_weight`, `has_height_weight`), underweight status/flag, wasting status/SAM flag, eligibility flags, quality flag, and matched lookup thresholds.
- **`child_month_transitions`** — Derived transitions from prior month (`prev_underweight_status`, `prev_is_sam`, `improved_underweight_to_normal_from_prev`, `improved_sam_from_prev`).

`beneficiaries` also includes derived `is_low_birth_weight` (`birth_weight < 2.5kg`, null if missing/invalid).

### Created database (current build verification)

**Connected file:** `/home/harsh_wadhwaniai_org/eda-project/database/nutrition_data.duckdb`

**Tables discovered:** `nutrition_data`

| Table | Rows |
|------|------:|
| `nutrition_data` | 3,637,040 |

The persisted DB is now used directly by `scripts/llm_to_sql.py`, avoiding rebuild on each query.

---

## LLM-to-SQL artifacts

- Query script: `scripts/llm_to_sql.py`
- Batch execution outputs:
  - `outputs/llm_to_sql_batch_results.md`
  - `outputs/llm_to_sql_batch_results.json`
- Example covered outputs include: underweight prevalence, data completeness, Feb->Mar improvement, district SAM reduction, April average weight, and Hb-missing response.

---

## References

- [LLM-to-SQL Feasibility Assessment](./llm_to_sql_feasibility_assessment.md) — dataset constraints and gaps.
- [CODEBASE.md](./CODEBASE.md) — scripts, layout, how to run.


---

## Operational notes

- Build DB once with `scripts/build_nutrition_db.py`; rebuild only when source labeled CSV changes.
- `scripts/add_nutrition_labels.py` currently reads the full CSV into memory; use a machine with sufficient RAM for full-data runs.
