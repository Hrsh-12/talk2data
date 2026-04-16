# Codebase.md — Structural Design

This document explains **where code lives** and **what each part does**. If you are new to the repo, read [§ Quick tour](#quick-tour-for-beginners) first, then skim the folder table and the `pipeline/` library breakdown.

---

## Quick tour for beginners

**What this project does (one sentence):** It turns child nutrition records into a DuckDB database, then answers **natural-language questions** by having an LLM write **read-only SQL** against that database (CLI or Gradio UI).

**Typical flow:**

1. **Data & ETL** — Raw/cleaned CSVs and PDF-derived lookups live under `data/`. Scripts in `scripts/` run in order: parse PDFs → add labels → build the DuckDB file.
2. **Shared logic** — Reusable Python lives under `pipeline/`: nutrition classification (`nutrition_labels.py`) and the NL→SQL stack (`service.py`, `db/`, `llm/`, etc.).
3. **Apps** — `apps/` is the **web UI** (Gradio). It imports `pipeline/`; there is no separate backend server.
4. **Queries** — You can run `scripts/llm_to_sql.py` from the terminal or launch `apps/gradio_app.py` for chat.

**Mental model:**

| You want to… | Look here |
|----------------|-----------|
| Change how CSVs become the database | `scripts/build_nutrition_db.py`, `data/` |
| Change WHO-style stunting/wasting rules | `pipeline/nutrition_labels.py`, `data/processed/*_lookup.csv` |
| Change SQL generation prompts or repair text | `conf/prompts/*.yaml` (per dataset), `pipeline/llm/chains.py`, `pipeline/db/engine.py` |
| Plug in another DuckDB / query catalog (e.g. BIRD) | `conf/dataset/*.yaml`, `conf/prompts/*.yaml`, `data/*_queries.jsonl` — see [§ 2.3.1](#231-pluggable-datasets-nl-sql-runtime) |
| Change the chat UI | `apps/gradio_app.py`, `conf/gradio/`, `apps/ui_content.py`, `apps/result_utils.py` |
| Run batch benchmark queries | `scripts/llm_to_sql.py`, `outputs/` |
| Understand table/column meanings | [§ Data Schema](#3-data-schema) below |

**Why ETL steps use `scripts/` but shared rules use `pipeline/`** (even though both are pre-processing and end up in the DB): see **§2.2.1** under [Component Breakdown](#2-component-breakdown).

---

## 1. High-Level Architecture

**Pattern: Modular Monolith (Script-Orchestrated Data Pipeline)**

The codebase follows a **pipeline-oriented monolith** architecture. There is no service mesh, no API gateway, and no microservice boundary. Instead, a chain of standalone Python scripts transforms raw data through successive stages, culminating in an embedded analytics database queried via an LLM-powered UI.

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Repository Layout                           │
│                                                                     │
│  data/          Static assets: raw CSVs, PDF-derived lookups        │
│  scripts/       Sequential ETL scripts (parse → label → build → Q)  │
│  pipeline/      Reusable library (labels + NL→SQL query stack)      │
│  database/      Runtime artifact: DuckDB file (often gitignored)    │
│  apps/          Gradio web UI + result formatting                   │
│  conf/          Hydra YAML (dataset profiles, paths, LLM, rephrase, Gradio) │
│  notebooks/     Exploratory analysis (EDA, validation)              │
│  docs/          Project documentation (this file, etc.)           │
│  tests/         Pytest unit tests (pure helpers, no live API/DB)    │
│  outputs/       LLM trace JSON (often gitignored)                     │
└─────────────────────────────────────────────────────────────────────┘
```

Key architectural traits:

| Trait | Description |
|-------|-------------|
| **Embedded DB** | DuckDB (single file, no server process) serves as the analytics store. |
| **LLM-in-the-loop** | OpenAI models generate SQL at runtime; a repair step retries on execution failure. |
| **No HTTP service layer** | No REST/gRPC API between components; Gradio calls `pipeline/` functions in-process. |
| **Script orchestration** | Pipeline stages are run manually via `python scripts/<name>.py` in sequence. |

---

## 2. Component Breakdown

### 2.1 `data/` — Static Data Assets

| Subdirectory / File | Responsibility |
|---------------------|----------------|
| `3_months_UP_0m_6y_data*.csv` | Raw ICDS-style child health extracts for Uttar Pradesh (0–6y, Feb–Apr 2024). Large row counts. |
| `cleaned_dataset.csv` | Post-cleaning subset used as input to labeling. |
| `cleaned_dataset_with_labels.csv` | Analytics-ready table with extra nutrition label columns. |
| `cleaned_dataset_with_labels_filtered.csv` | Optional stricter-filtered labeled subset. |
| `district_mapping.csv` | Reference: `district_name` ↔ `district_id` for UP districts. |
| `processed/stunting_lookup.csv` | Height-for-age thresholds by (sex, day). |
| `processed/underweight_lookup.csv` | Weight-for-age thresholds by (sex, day). |
| `processed/wasting_lookup.csv` | Weight-for-height thresholds by (sex, age_band, height_cm). |
| `queries/queries.txt` | Benchmark questions (legacy path used to have a trailing-space folder name). |
| `queries/queries_verified.sql` | Legacy reference SQL (superseded for NL→SQL benchmarks by JSONL in many flows). |
| `nutrition_queries.jsonl` | BIRD-style query catalog (gold `SQL`, `question`, `evidence`, etc.) — default `paths.queries_path` for nutrition. |

**Interaction:** `data/` is read by `scripts/` and `pipeline/nutrition_labels.py`. Much of `data/` may be gitignored locally; files often must be provisioned on each machine.

### 2.2 `scripts/` — ETL Pipeline Scripts

| Script | Stage | Input | Output |
|--------|-------|-------|--------|
| `parse_assessment_pdfs.py` | 1 | `data/*.pdf` (WHO-style assessment PDFs) | `data/processed/*_lookup.csv` |
| `add_nutrition_labels.py` | 2 | `cleaned_dataset.csv` + lookups | `cleaned_dataset_with_labels.csv` |
| `build_nutrition_db.py` | 3 | Labeled CSV | `database/nutrition_data.duckdb` |
| `llm_to_sql.py` | 4 | DuckDB + NL question(s) | Console output + `outputs/*.json` (batch mode) |

**Interaction:** Scripts are stateless CLI tools. Each reads its predecessor’s output where applicable. `add_nutrition_labels.py` imports `pipeline.nutrition_labels`. `llm_to_sql.py` imports `pipeline.service`.

### 2.2.1 Design note: why ETL scripts live in `scripts/` but WHO-style logic lives in `pipeline/`

It can look odd that **CSV-writing steps** sit under `scripts/` while **stunting / underweight / wasting math** sits under `pipeline/nutrition_labels.py`, when **all of that is pre-processing** and ends up **baked into** the DuckDB file via `build_nutrition_db.py`. The split is intentional:

| | **`scripts/`** | **`pipeline/`** |
|---|------------------|----------------|
| **Role** | **Pipeline drivers** — runnable stages you execute from the shell: which files to read/write, chunk size, progress, CLI flags, and wiring. | **Reusable library** — **what** the computation means: classification against lookup tables, SQL/LLM behavior, anything you might import from a notebook or test. |
| **Analogy** | The **job** (“read this CSV, add columns, save there”). | The **rules** (“given sex, age, height, weight, return these labels”). |

**Why not put everything in `scripts/`?** You could inline the WHO-style logic inside `add_nutrition_labels.py`, but then notebooks, experiments, and tests would either duplicate code or import a script module awkwardly. Keeping **`classify_all` / `classify_stunting`** in `pipeline/` means one canonical definition of labels, reused by the labeling script and by exploratory work.

**Why not put scripts in `pipeline/`?** Scripts are **thin orchestration**: they are not imported as stable APIs; they tie together paths, I/O, and library calls. The NL→SQL stack under **`pipeline/`** follows the same idea: **`execution/runner.py`** runs the flow; **`llm/chains.py`** wires LangChain; **prompt text** lives in **`conf/prompts/*.yaml`**.

**End state:** Yes — once the labeled CSV exists and `build_nutrition_db.py` runs, the **database** holds the result. The **labeling** code is not needed at **query time** for nutrition labels (only the LLM stack is). The split matters while **building** and **validating** the dataset, not because the runtime DB “knows” where the code lived.

### 2.3 `pipeline/` — Core Library

```
pipeline/
├── __init__.py
├── nutrition_labels.py      # WHO-style classification (stunting, underweight, wasting, SAM)
├── dataset_runtime.py       # DatasetRuntime.from_hydra — DB URI, schema tables, prompt paths
├── paths.py                 # Canonical paths + resolve_config_path for Hydra entry points
├── service.py               # Facade: load_dotenv + stable re-exports for NL→SQL
├── sql/                     # SQL text: guards, extraction, normalization
│   └── text.py
├── queries/                 # BIRD-style JSONL / legacy SQL catalog loaders
│   └── catalog.py
├── db/                      # LangChain SQLDatabase, caches, execute_sql, warmup
│   └── engine.py
├── llm/                     # LangChain chains; templates loaded from YAML on disk
│   ├── chains.py
│   └── prompt_loader.py
├── execution/               # run_single_question, dataframe helper, trace I/O
│   ├── runner.py
│   └── traces.py
└── benchmark/               # compare_generated_with_verified + numeric diff
    └── verification.py
```

#### What each NL→SQL submodule is for

| Path | Beginner-friendly role |
|------|-------------------------|
| **`paths.py`** | Canonical paths and `resolve_config_path()` (supports `~` expansion) for repo-relative paths when using Hydra. Defaults are composed from `conf/dataset/*.yaml`. |
| **`sql/text.py`** | **Safety + parsing:** ensures only `SELECT`/`WITH`, pulls SQL out of markdown fences, normalizes SQL strings when comparing to “verified” benchmark SQL. |
| **`db/engine.py`** | **Database + execution:** opens DuckDB through LangChain’s `SQLDatabase`, caches schema/sample rows for prompts, caches `ChatOpenAI` clients, runs SQL and parses string results for the UI/benchmarks. |
| **`llm/chains.py`** | **Model calls:** loads generation/repair **templates** via `prompt_loader.load_prompt_template()` from `conf/prompts/*.yaml`, applies `str.format` for `table_info`, `sample_rows_text`, etc.; runs LangChain chains for “question → SQL” and “broken SQL → fix”. |
| **`llm/prompt_loader.py`** | Reads YAML with a top-level `template` string (optional `meta`); supports legacy plain-text files by extension. |
| **`queries/catalog.py`** | Loads BIRD-style JSONL/JSON (`question_id`, `SQL`, …) or legacy `-- Qn:` `.sql` for gold SQL maps and the Gradio ground-truth panel. |
| **`dataset_runtime.py`** | Frozen config: DuckDB SQLAlchemy URI, `schema_tables`, paths to generation/repair YAML, optional `hints_path` / `schema_notes_path`, `inject_evidence`. |
| **`execution/runner.py`** | **Main user journey:** `run_single_question` ties LLM → execute → optional repair. |
| **`execution/traces.py`** | Saves JSON traces and reads query list files for batch mode. |
| **`benchmark/verification.py`** | **Benchmarking:** compares generated vs gold SQL execution results (`compare_generated_with_verified`); gold SQL comes from the catalog loader. |
| **`service.py`** | **Stable entrypoint:** `load_dotenv()` and re-exports; use `from pipeline.service import …`. |

| Module | Responsibility |
|--------|----------------|
| `nutrition_labels.py` | Loads `data/processed/*_lookup.csv` (cached). Exposes `classify_all(...)` for stunting / underweight / wasting / SAM for one measurement. |

**Internal coupling:** `nutrition_labels` and the NL→SQL modules do not import each other. Orchestration happens in `scripts/` and `apps/`.

#### 2.3.1 Pluggable datasets (NL→SQL runtime)

The chat CLI and Gradio do **not** hard-code the nutrition schema. A **single process** loads one **dataset profile** from Hydra (`defaults: - dataset: nutrition` in [`conf/config.yaml`](../conf/config.yaml)), implemented as [`conf/dataset/nutrition.yaml`](../conf/dataset/nutrition.yaml) or [`conf/dataset/bird.yaml`](../conf/dataset/bird.yaml). Switching datasets is **config-only** (e.g. `python apps/gradio_app.py dataset=bird`); there is no in-UI picker.

**What you must provide for a new analytical database:**

| Artifact | Role |
|----------|------|
| **Database file** | DuckDB path (`paths.db_path` or env `DB_PATH`). The code builds a SQLAlchemy URI `duckdb:///{absolute_path}`. |
| **Schema exposure** | `dataset.schema_tables`: list of table names passed to LangChain `get_table_info` and used for the default `SELECT * FROM <first_table> LIMIT 3` sample unless `sample_sql` is set. |
| **Generation + repair prompts** | `sql_generation_prompt_path` and `sql_repair_prompt_path` pointing to YAML files under `conf/prompts/`. Each file must contain a string key **`template`** (Python `str.format` placeholders such as `{table_info}`, `{sample_rows_text}`, `{schema_notes_block}`, `{hints_block}`; use `{{` / `}}` for literal braces). Optional `meta.description` is ignored at runtime. |
| **Query catalog** | `paths.queries_path`: JSONL (or JSON array) with BIRD-compatible records (`question_id`, `db_id`, `question`, `evidence`, `SQL`, `difficulty`). Used for batch gold-SQL comparison and the Gradio “Ground Truth Reference” panel. Legacy `.sql` with `-- Qn:` headers still loads if you point `queries_path` at a `.sql` file. |
| **Optional** | `schema_notes_path`, `hints_path` (markdown text inlined into the generation template), `dataset.ui` (title, subtitle, tips), `inject_evidence` (append catalog `evidence` to the user message when not `NA`). |

**Assumptions:** (1) Default runtime target is **DuckDB on disk** via `duckdb-engine` / LangChain `SQLDatabase`. (2) **One dataset per process** — no hot-swapping DB without restart. (3) Prompt templates remain **data-driven** (YAML), not embedded as large strings in `chains.py`. (4) New databases that are not DuckDB would require extending `dataset_runtime.duckdb_sqlalchemy_uri` / runner execution paths (not done generically yet).

### 2.4 `database/` — DuckDB Runtime Store

Usually contains a `.gitkeep`; the real `nutrition_data.duckdb` is produced by `build_nutrition_db.py` and is often gitignored. The DB holds a single wide table (`nutrition_data`) with monthly columns and label fields.

### 2.5 `conf/` — Hydra configuration

| Path | Role |
|------|------|
| **`config.yaml`** | Composes defaults: **`dataset`** (nutrition or bird), `llm`, `rephrase`, `gradio`; sets `hydra.job.chdir: false` so repo-relative paths behave like plain scripts. |
| **`dataset/nutrition.yaml`**, **`dataset/bird.yaml`** | `@package _global_`: `paths` (`db_path`, `queries_path`, `output_dir`), `dataset` (schema tables, `db_id`, YAML prompt paths, UI, flags). |
| **`paths/default.yaml`** | Legacy shim: same keys documented for env overrides; prefer `dataset` + `paths` from `dataset/*.yaml`. |
| **`prompts/*.yaml`** | NL→SQL **generation** and **repair** templates (`template` key). Not Hydra-composed as separate configs — referenced by path from `dataset/*.yaml`. |
| **`llm/default.yaml`** | SQL model name, temperature, `top_k`, and optional `client` `_target_` for `ChatOpenAI` (used by entry points via `hydra.utils.instantiate`). |
| **`rephrase/default.yaml`** | Small chat model settings for answer rephrasing in the UI. |
| **`gradio/default.yaml`** | Queue sizes, trace/history flags, server host/port defaults. |

**Overrides:** `python apps/gradio_app.py dataset=bird gradio.server_port=8080` or `python scripts/llm_to_sql.py llm.model=gpt-4o-mini question="Your question?"`. The CLI is pure Hydra: provide `question="..."` for single mode or `queries_file="..."` for batch mode.

**Interaction:** `apps/gradio_app.py` uses `@hydra.main` against `conf/config.yaml`. `scripts/llm_to_sql.py` is also `@hydra.main`, but uses `conf/llm_to_sql.yaml` so `question` / `queries_file` are first-class keys (no `+` overrides required). Env vars such as `MODEL_NAME`, `TEMPERATURE`, `GRADIO_SERVER_PORT` still override after compose (same behavior as before).

### 2.6 `apps/` — Gradio Web Interface

| File | Role |
|------|------|
| **`gradio_app.py`** | Entry point (`@hydra.main`): chat UI, calls `run_single_question` with an instantiated `ChatOpenAI`, optional trace save, side panel for results and ground truth. |
| **`config.py`** | Minimal: project `ROOT`, `sys.path`, `load_dotenv()`, and optional path shims (`DEFAULT_DB_PATH`, …) for back-compat imports. |
| **`ui_content.py`** | Static tips markdown, sample queries/labels, and `APP_CSS` (not Hydra-driven). |
| **`result_utils.py`** | Formats SQL results as tables/markdown, ground-truth HTML from **JSONL** (or legacy `.sql`), **rephrases** numeric/tabular results with a small LangChain chat model (parameters from Hydra + env). |

**Interaction:** Imports `run_single_question`, `save_single_trace`, `warmup_runtime` from `pipeline.service`. Paths come from `DatasetRuntime.from_hydra` (`conf/dataset/*.yaml` + env: `DB_PATH`, `QUERIES_PATH`, …).

### 2.7 `tests/` — Automated tests

| File | Role |
|------|------|
| `test_nutrition_sql_pure.py` | Tests pure helpers (`extract_sql`, `normalize_sql`, verification parsing, structured comparison) **without** calling OpenAI or opening a real DB file. |
| `test_pipeline_units.py` | SQL safety, DuckDB fixture, `resolve_config_path`, runner preflight, benchmark helpers, Gradio `build_app`, result/rephrase helpers (mocked where needed). |
| `test_connectivity_inference.py` | Optional `requires_db` checks against a real DuckDB file (`RUN_CONNECTIVITY=1`). |
| `conftest.py` | Skips `requires_db` tests unless `RUN_CONNECTIVITY` is set. |

Run from repo root: `PYTHONPATH=. python -m pytest tests/`

### 2.8 `notebooks/` — Exploratory Analysis

| Notebook | Purpose | Connects to |
|----------|---------|-------------|
| `01_initial_exploration.ipynb` | Heavy EDA on raw CSV: profiling, cleaning, label ideas. | `data/`, `pipeline/nutrition_labels` |
| `01_explore.ipynb` | Schema inspection (legacy DB names may appear). | `database/` |
| `eda_processed_database.ipynb` | Validation of processed DuckDB variants. | `database/` |

### 2.9 Component Interaction Diagram

```mermaid
flowchart TD
    A[WHO Assessment PDFs] --> B[parse_assessment_pdfs.py]
    B --> C[Lookup CSVs]
    D[Raw / cleaned CSVs] --> E[Notebook or external cleaning]
    E --> F[cleaned_dataset.csv]
    C --> G[add_nutrition_labels.py]
    F --> G
    G --> H[Labeled CSV]
    H --> I[build_nutrition_db.py]
    I --> J[(DuckDB nutrition_data)]
    J --> K[pipeline NL→SQL]
    L[OpenAI API] <--> K
    K --> M[llm_to_sql.py CLI]
    K --> N[Gradio in apps/]
    L <--> N
```

---

## 3. Data Schema

### 3.1 Database Engine

**DuckDB** (embedded, serverless, single-file). Connected via:

- Direct: `duckdb.connect(str(db_path))`
- LangChain: `SQLDatabase.from_uri("duckdb:///{db_path}")`

### 3.2 Table: `nutrition_data`

A single denormalized wide table. Schema is inferred at load time (e.g. `CREATE TABLE AS SELECT *` from a registered DataFrame in `build_nutrition_db.py`).

#### Column Groups (conceptual)

| Group | Columns | Type | Notes |
|-------|---------|------|-------|
| **Identity** | `beneficiary_id` | VARCHAR/INT | One row per child |
| **Demographics** | `dob`, `gender` | DATE/VARCHAR | |
| **Birth metrics** | `birth_height`, `birth_weight` | FLOAT | |
| **Admin hierarchy** | `state_id`, `district_id`, … | INT/VARCHAR | ICDS / AWC hierarchy |
| **Monthly measurements** (×3) | `{month}_status`, `{month}_height`, `{month}_weight`, … | | `feb24`, `mar24`, `apr24` |
| **Monthly labels** (×3) | `{month}_stunting_status`, `{month}_is_stunted`, … | VARCHAR / 0–1 | Derived labels |

**Approximate shape:** on the order of **~46 columns** and **~3.6M rows** when using the full labeled CSV (exact numbers depend on your local artifact).

### 3.3 Data Flow: Database Layer → `pipeline` NL→SQL Logic

```mermaid
flowchart LR
    A[Labeled CSV] --> B[build_nutrition_db.py]
    B --> C[(DuckDB)]
    C --> D[pipeline.execution]
    D --> E[YAML prompts + llm/chains.py]
    E --> F[OpenAI - generate SQL]
    F --> G[Execute SQL via db/engine.py]
    G --> H{Success?}
    H -- Yes --> I[Query Result]
    H -- No --> J[Repair SQL via llm/chains.py]
    J --> G
```

Key points:

- Schema + sample rows for prompts are loaded through LangChain and **cached** (`db/engine.py`).
- Bool-like label columns are normalized during DB build where applicable.

---

## 4. Related docs

- [README.md](../README.md) — how to run the pipeline end-to-end from the command line.
- [ProjectStatus.md](ProjectStatus.md) — purpose, pipeline, what exists, how to run.
