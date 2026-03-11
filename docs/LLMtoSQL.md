# LLM-to-SQL Implementation Status

This document reflects the implemented PoC architecture (not just the initial plan).

## Implemented architecture

The workflow is split into two scripts:

1. `scripts/build_nutrition_db.py`
   - Reads `data/cleaned_dataset_with_labels.csv`
   - Normalizes bool-like `*_is_*` columns to numeric `0/1`
   - Persists DuckDB file: `database/nutrition_data.duckdb`
   - Writes table: `nutrition_data`

2. `scripts/llm_to_sql.py`
   - Connects to saved DB (`database/nutrition_data.duckdb`)
   - Creates LangChain SQL agent (`create_sql_agent`) over DuckDB
   - Runs NL questions against `nutrition_data`
   - Uses schema + sample row grounding in prompt

## Environment

```bash
source ~/.bashrc
conda activate eda
pip install -r requirements.txt
```

Core libs used: `duckdb`, `pandas`, `langchain`, `langchain-community`, `langchain-openai`, `duckdb-engine`, `sqlalchemy<2`, `python-dotenv`.

## Business rules in prompt grounding

- Prefix mapping: `feb24_`, `mar24_`, `apr24_`
- SAM definition: prioritize `*_is_sam` columns
- Hb handling: if asked for haemoglobin, respond that data is missing

## Implemented query logic patterns

- Prevalence: `AVG(binary_column) * 100`
- Completeness: `(COUNT(entered_date_col) * 100.0) / COUNT(beneficiary_id)`
- Feb->Mar underweight improvement:
  - `(COUNT(*) FILTER (WHERE feb24_is_underweight = 1 AND mar24_is_underweight = 0) * 100.0) / NULLIF(COUNT(*) FILTER (WHERE feb24_is_underweight = 1), 0)`
- District SAM reduction:
  - `AVG(feb24_is_sam) - AVG(mar24_is_sam)` grouped by `district_id`, ordered desc, top 5

## Commands

Build DB (run once, or when labeled CSV changes):

```bash
python scripts/build_nutrition_db.py --csv data/cleaned_dataset_with_labels.csv --db database/nutrition_data.duckdb
```

Run LLM query:

```bash
python scripts/llm_to_sql.py --db database/nutrition_data.duckdb --top-k 5 "Top 5 districts by SAM reduction from Feb to Mar"
```

## Outputs

Batch run outputs are stored at:

- `outputs/llm_to_sql_batch_results.md`
- `outputs/llm_to_sql_batch_results.json`