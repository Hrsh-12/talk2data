# Data EDA

## Source and Execution Context

- **Notebook:** `notebooks/01_initial_exploration.ipynb`
- **Data file analyzed:** `data/3_months_UP_0m_6y_data_with_demographic_info.csv`
- **Schema discovered in notebook output:** `29` columns
- **Chunk profile output:** first chunk `20,000 x 29`
- **Full streaming scan output:** `917` chunks, `18,332,035` rows

## Data Shape and Column Groups

- Grain: one row per beneficiary with month-wise fields in wide format.
- Column groups:
  - identity: `beneficiary_id`, `dob`, `gender`
  - birth fields: `birth_height`, `birth_weight`
  - monthly fields (Feb/Mar/Apr): `status`, `height`, `weight`, entered date, Hb, Hb test date
  - geography/admin hierarchy: `state_id`, `district_id`, `project_id`, `sector_id`, `awc_id`, `awc_code`

## Missingness Profile (Executed Output)

Highest missing columns:

- `feb24_hb_test_date`: `18,332,034` (`100.0000%`)
- `mar24_hb_test_date`: `18,332,034` (`100.0000%`)
- `apr24_hb_test_date`: `18,332,030` (`100.0000%`)
- `birth_height`: `14,694,995` (`80.1602%`)
- `birth_weight`: `14,694,995` (`80.1602%`)
- `apr24_height_weight_entered_date`: `3,906,307` (`21.3086%`)
- `mar24_height_weight_entered_date`: `3,533,813` (`19.2767%`)
- `feb24_height_weight_entered_date`: `3,184,006` (`17.3685%`)

Columns with zero missing in the output table:

- `beneficiary_id`, `dob`, `gender`
- `state_id`, `district_id`, `project_id`, `sector_id`, `awc_id`, `awc_code`

## Numeric Summary (Executed Output)

- Monthly mean heights:
  - `feb24_height`: `86.7091`
  - `mar24_height`: `86.6628`
  - `apr24_height`: `86.3437`
- Monthly mean weights:
  - `feb24_weight`: `12.2589`
  - `mar24_weight`: `12.2885`
  - `apr24_weight`: `12.2636`
- Birth metrics on non-missing rows:
  - `birth_height` mean `47.4671` (min `12.0`, max `76.2`)
  - `birth_weight` mean `3.3992` (min `0.5`, max `8.0`)
- Hb fields (`feb24_hb`, `mar24_hb`, `apr24_hb`) are near-zero and zero-dominant in output summaries.

## Categorical and Status Distribution

- Gender:
  - `M`: `9,558,181` (`52.1392%`)
  - `F`: `8,773,854` (`47.8608%`)
- DOB:
  - distinct values: `2,202`
  - top DOB value: `2020-01-01` (`72,223`, `0.3940%`)

Month-wise status distributions:

- `feb24_status`: active `83.6516%`, missing `14.7178%`, inactive `0.9623%`, migrated_out `0.6683%`
- `mar24_status`: active `82.3594%`, missing `16.1265%`, inactive `0.8941%`, migrated_out `0.6200%`
- `apr24_status`: active `80.8050%`, missing `17.4659%`, inactive `0.9865%`, migrated_out `0.7426%`

## Month-Wise Measurement Coverage

From the notebook coverage table:

- **Feb:** status present `85.2822%`, height>0 `82.6315%`, weight>0 `82.6315%`, both>0 `82.6315%`
- **Mar:** status present `83.8735%`, height>0 `80.7233%`, weight>0 `80.7233%`, both>0 `80.7233%`
- **Apr:** status present `82.5341%`, height>0 `78.6914%`, weight>0 `78.6914%`, both>0 `78.6914%`

Observed in executed output: coverage declines from February to April.

## DOB and Age Quality

- DOB non-null rows: `18,332,035`
- DOB invalid rows: `0`
- DOB invalid % among non-null: `0.0000%`
- Age summary (months, reference date 2024-04-01):
  - count: `18,332,035`
  - mean: `43.7180`
  - min: `4.0407`
  - max: `83.7057`

## Cleaning Run Output (Notebook Cells 19-25)

The executed cleaning outputs show:

- Hb columns dropped: `6`
  - `feb24_hb`, `feb24_hb_test_date`, `mar24_hb`, `mar24_hb_test_date`, `apr24_hb`, `apr24_hb_test_date`
- Column count changed: `29 -> 23`
- Output file:
  - `data/3_months_UP_0m_6y_data_with_demographic_info_no_hb_dob_gender_clean.csv`

Row summary from the executed output table:

- `rows_total`: `18,332,035`
- `rows_dropped_null_birth_height`: `14,694,995`
- `rows_dropped_null_birth_weight`: `14,694,995`
- `rows_dropped_null_birth_height_or_weight`: `14,694,995`
- `rows_remaining`: `3,637,040`
- `retention_pct`: `19.8398`

Validation outputs:

- validation sample shape: `(5, 23)`
- full read length check: `3,637,040`

## Final Note

This document is intentionally aligned to the current executed notebook outputs.  
The active cleaning code/output currently filters by `birth_height`/`birth_weight` nulls, in addition to dropping Hb columns.

---

## Downstream artifacts (current pipeline)

The EDA/cleaning output is now used by downstream scripts as follows:

- Labeled CSV: `data/cleaned_dataset_with_labels.csv` (produced by `scripts/add_nutrition_labels.py`)
- Persisted DuckDB: `database/nutrition_data.duckdb` (produced by `scripts/build_nutrition_db.py`)
- Active query table: `nutrition_data`
- LLM querying script: `scripts/llm_to_sql.py`

Recent batch QA outputs are stored in:

- `outputs/llm_to_sql_batch_results.md`
- `outputs/llm_to_sql_batch_results.json`
