# LLM-to-SQL Implementation Status

This document reflects the implemented PoC architecture (not just the initial plan).

## Implemented architecture

The current workflow has three serving layers:

1. `scripts/build_nutrition_db.py`
   - Reads `data/cleaned_dataset_with_labels.csv`
   - Normalizes bool-like `*_is_*` columns to numeric `0/1`
   - Persists DuckDB file: `database/nutrition_data.duckdb`
   - Writes table: `nutrition_data`

2. `src/nutrition_sql/service.py`
   - Shared LLM-to-SQL core logic (prompting, SQL extraction, execution, repair, traces)
   - Batch comparison utilities against verified SQL (`queries_verified.sql`)
   - Robust verified SQL parsing (ignores comments and stored result blocks)
   - Structured result comparison with numeric tolerance and shape diagnostics
   - Read-only SQL guard (`SELECT`/`WITH`)

3. Client entrypoints
   - `scripts/llm_to_sql.py`: CLI wrapper around service
   - `apps/gradio_app.py`: interactive Gradio chat UI

## Environment

```bash
source ~/.bashrc
conda activate eda
pip install -r requirements.txt
```

Core libs used: `duckdb`, `pandas`, `langchain`, `langchain-community`, `langchain-openai`, `duckdb-engine`, `sqlalchemy<2`, `python-dotenv`, `gradio`.

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

Run batch comparison against verified SQL:

```bash
python scripts/llm_to_sql.py \
  --db database/nutrition_data.duckdb \
  --queries-file "data/queries /queries.txt" \
  --verified-sql-file "data/queries /queries_verified.sql"
```

Behavior notes:
- LLM inference is always used (no verified-template routing).
- Comparison payload includes `same_result`, `shape_match`, `max_numeric_diff`, and tolerance metadata.
- `queries_verified.sql` currently includes verified mappings/results for `Q1` through `Q28`.

Run Gradio chat app:

```bash
python apps/gradio_app.py
```

Temporary public link:

```bash
GRADIO_SHARE=true python apps/gradio_app.py
```

## Outputs

Trace files are stored at:

- `outputs/llm_to_sql_trace_<timestamp>.json`
- `outputs/llm_to_sql_trace_latest.json`

The Gradio app currently surfaces:
- chat response with compact result preview
- generated SQL panel
- raw SQL output panel