# Child Nutrition LLM-to-SQL Pipeline

Cleaned child health records are labeled, loaded into **DuckDB** (embedded, file-based — there is no separate SQL server process), and queried with natural language via an LLM (CLI or Gradio).

## Setup

```bash
python -m venv .venv && source .venv/bin/activate   # or: conda activate eda
pip install -r requirements.txt
```

Create a `.env` in the repo root with `OPENAI_API_KEY=...` for LLM-backed features (CLI and Gradio).

## Database (DuckDB)

DuckDB runs **in-process** against a single file. Build or refresh that file after you have a labeled CSV:

```bash
python scripts/build_nutrition_db.py \
  --csv data/cleaned_dataset_with_labels.csv \
  --db database/nutrition_data.duckdb
```

Point tools at it with **`DB_PATH`** (defaults to `database/nutrition_data.duckdb` in Hydra config). No daemon or port to start for DuckDB itself.

Earlier pipeline stages (PDF lookups, labeling) are documented in [docs/Codebase.md](docs/Codebase.md).

## Run tests

From the **repository root**:

```bash
PYTHONPATH=. python -m pytest tests/
```

Skip optional tests that need a real DuckDB file on disk:

```bash
PYTHONPATH=. python -m pytest tests/ -m "not requires_db"
```

Run those connectivity tests (expects `DB_PATH` or default `database/nutrition_data.duckdb` to exist):

```bash
RUN_CONNECTIVITY=1 PYTHONPATH=. python -m pytest tests/ -m requires_db
```

Details and test IDs: [docs/TestSuite.md](docs/TestSuite.md).

## Run Gradio (web UI)

```bash
python apps/gradio_app.py
```

Open the URL printed in the terminal (default **http://127.0.0.1:7860**). Override host/port with `GRADIO_SERVER_NAME` and `GRADIO_SERVER_PORT`, or Hydra, e.g. `python apps/gradio_app.py gradio.server_port=8080`.

## Run CLI queries

```bash
python scripts/llm_to_sql.py "Your question here?"
```

Batch mode: `python scripts/llm_to_sql.py --queries-file "data/queries /queries.txt" --output-dir outputs`. Hydra-style overrides can follow the script name (see `scripts/llm_to_sql.py` docstring).

## Further reading

- [docs/Codebase.md](docs/Codebase.md) — layout and components  
- [docs/ProjectStatus.md](docs/ProjectStatus.md) — status and gaps  
