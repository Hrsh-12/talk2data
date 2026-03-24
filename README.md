# Child Nutrition LLM-to-SQL Pipeline

This project builds a nutrition analytics dataset for Uttar Pradesh children, stores it in DuckDB, and supports natural-language querying through an LLM.

The core flow is:
1. Parse reference PDFs into lookup tables.
2. Add month-wise nutrition labels to cleaned child records.
3. Build a persistent DuckDB table for analytics.
4. Run natural-language queries and capture SQL traces/results.
5. (Optional) Use a Gradio chat UI for interactive querying.

## Pipeline Diagram

```text
data/*.pdf
   │
   ├── scripts/parse_assessment_pdfs.py
   ▼
data/processed/*_lookup.csv
   │
   ├── scripts/add_nutrition_labels.py
   │      input:  data/cleaned_dataset.csv
   ▼
data/cleaned_dataset_with_labels.csv
   │
   ├── scripts/build_nutrition_db.py
   ▼
database/nutrition_data.duckdb (table: nutrition_data)
   │
   ├── scripts/llm_to_sql.py (single or batch CLI)
   ├── src/nutrition_sql/service.py (shared query service)
   ├── apps/gradio_app.py (interactive chat UI)
   ▼
outputs/llm_to_sql_trace_*.json|.md
outputs/llm_to_sql_trace_*.json
```

## Pipeline Overview

### Step 0: Environment setup

```bash
source ~/.bashrc
conda activate eda
pip install -r requirements.txt
```

### Step 1: Parse assessment PDFs to lookup CSVs

Generates lookup tables used for nutrition classification.

```bash
python scripts/parse_assessment_pdfs.py
```

Outputs in `data/processed/`:
- `stunting_lookup.csv`
- `underweight_lookup.csv`
- `wasting_lookup.csv`

### Step 2: Add nutrition labels to cleaned dataset

Reads `data/cleaned_dataset.csv` and adds month-wise label columns for:
- stunting
- underweight
- wasting
- SAM

```bash
python scripts/add_nutrition_labels.py \
  --input data/cleaned_dataset.csv \
  --output data/cleaned_dataset_with_labels.csv
```

Main output:
- `data/cleaned_dataset_with_labels.csv`

### Step 3: Build persistent DuckDB database

Loads labeled CSV into DuckDB table `nutrition_data` and normalizes bool-like fields to numeric `0/1`.

```bash
python scripts/build_nutrition_db.py \
  --csv data/cleaned_dataset_with_labels.csv \
  --db database/nutrition_data.duckdb \
  --table nutrition_data
```

Main output:
- `database/nutrition_data.duckdb`

### Step 4: Query with LLM-to-SQL

#### Single query mode

```bash
python scripts/llm_to_sql.py \
  --db database/nutrition_data.duckdb \
  --top-k 5 \
  "What percentage of children were underweight in February 2024?"
```

#### Batch mode from file

```bash
python scripts/llm_to_sql.py \
  --db database/nutrition_data.duckdb \
  --queries-file "data/queries /queries.txt" \
  --output-dir outputs
```

Optional comparison input:
- `--verified-sql-file` to compare generated SQL results against verified SQL by query index.

Batch trace outputs include:
- LLM raw output
- generated SQL
- SQL execution output/error
- comparison verdict + diagnostics (`same_result`, `shape_match`, `max_numeric_diff`, tolerance used)
- generated and verified execution payloads (when available)

Saved to:
- `outputs/llm_to_sql_trace_<timestamp>.json`
- `outputs/llm_to_sql_trace_latest.json`

Notes:
- LLM routing to hardcoded verified templates is disabled; every query is inferred by the model.
- Verified SQL parser strips comments/result blocks before extracting executable SQL.
- Verified SQL coverage now includes queries `Q1`-`Q28` in `data/queries /queries_verified.sql`.

### Step 5: Launch Gradio demo

Run an interactive web app for natural-language nutrition analytics:

```bash
python apps/gradio_app.py
```

The current UI is a minimal chat-first interface with:
- Chat answer (includes a compact result preview)
- Generated SQL panel
- Raw SQL output panel

Environment variables (optional):
- `GRADIO_SERVER_NAME` (default: `127.0.0.1`)
- `GRADIO_SERVER_PORT` (default: `7860`)
- `GRADIO_SHARE` (`true`/`false`, default: `false`)
- `DB_PATH` (default: `database/nutrition_data.duckdb`)
- `MODEL_NAME` (default: `gpt-5-mini`)
- `TOP_K` (default: `5`)
- `TEMPERATURE` (default: `0.0`)

To share quickly with others, start with a temporary public link:

```bash
GRADIO_SHARE=true python apps/gradio_app.py
```


## Data and Table Notes

- Active DB file: `database/nutrition_data.duckdb`
- Active table: `nutrition_data`
- Month prefixes: `feb24_`, `mar24_`, `apr24_`
- Geography columns include `district_id`, `project_id`, `sector_id`, `awc_id`

## Common Regeneration Workflow

If source cleaned CSV changes:
1. Re-run Step 2 (`add_nutrition_labels.py`)
2. Re-run Step 3 (`build_nutrition_db.py`)
3. Re-run Step 4 for validation queries

## Repository Pointers

- Project context: `docs/PROJECT.md`
- Codebase details: `docs/CODEBASE.md`
- EDA notes: `docs/Data EDA.md`
- LLM-to-SQL implementation notes: `docs/LLMtoSQL.md`
