# Talk to Data (TTD) — Technical Overview

**Talk to Data** is an AI-powered natural language analytics interface proposed for MoWCD's Poshan Tracker ecosystem. This document covers the full system: the programmatic problem it solves, the data it works on, the current PoC pipeline, evaluation methodology, benchmark results, and the path forward.

---

## 1. Problem Statement

Poshan Tracker captures child nutrition and programme data at unprecedented scale across India's ICDS network — millions of Anganwadi-level records updated monthly. Despite this richness, stakeholders at every level struggle to extract meaningful insights:

| # | Pain Point |
|---|-----------|
| 1 | Data is available but difficult to interpret quickly for timely decision-making |
| 2 | Field and programme teams are limited to predefined reports with no room for exploration |
| 3 | Identifying critical gaps (e.g. high undernutrition or anemia zones) is slow and manual |
| 4 | Dashboards offer a fixed view — there is no way to ask follow-up or contextual questions |
| 5 | Multilingual environments make data access harder at the last mile |

**Old Way vs TTD Way:**

| Old Way | TTD Way |
|---------|---------|
| Scroll through complex dashboards, apply multiple filters, still unsure if the answer is right | Ask a direct question in plain language, get a precise answer instantly |
| Export to Excel, manually calculate aggregations — time-consuming and error-prone | Ask for aggregations, comparisons, and trends directly without leaving the dashboard |
| Back-and-forth with data teams; days of wait for one report | Ask follow-up questions naturally, drill deeper, generate and download reports in one conversation |

**Goal:** Build a conversational AI interface that lets any user — from a ministry official to an Anganwadi worker — type or speak a question about nutrition data and receive a factually grounded answer, without needing any technical expertise or knowledge of the underlying schema.

---

## 2. Programmatic Constraints

The deployment context imposes non-negotiable constraints that shape every technical choice.

### Pre-deployment requirements

- **Language processing via Bhashini** — speech and NLP must use Bhashini, the Government of India's own multilingual AI platform, for ASR and translation
- **Model clearance** — no model may be integrated without explicit government clearance and documented due diligence
- **India-only infrastructure** — all compute must be scoped to MeitY-empanelled cloud providers with data centres located within India
- **DPDPA 2023 compliance** — full compliance with the Digital Personal Data Protection Act before go-live

### Post-deployment requirements

- All data stored and processed on servers within India only
- Beneficiary data encrypted at rest and in transit at all times
- Role-based access — no unauthorised visibility of sensitive records
- Model processes minimum data needed (principle of data minimisation)
- Queries must never be used to retrain or modify the underlying model
- Every interaction logged and auditable at any point

### Target users and example queries

| User Role | Level | Example Query |
|-----------|-------|---------------|
| Ministry / Leadership Stakeholders | National | *"How many states have met their Q3 POSHAN 2.0 targets?"* |
| State & District Programme Managers | State | *"Show me districts with worsening stunting trends this quarter"* |
| CDPOs / Supervisors | Block | *"Which centres in my block have not submitted data this month?"* |
| Field Workers (Anganwadi ecosystem) | Last mile | *"Mere centre mein kitni mahilaon ka weight record hua?"* |

---

## 3. Available Data

The current PoC is built on the **UP Growth Faltering dataset** — a subset of Poshan Tracker data for Uttar Pradesh covering children aged 0–6 years over February–April 2024.

### Scale

| Layer | File | Rows | Description |
|-------|------|------|-------------|
| Raw ICDS CSV | `cleaned_dataset.csv` | ~3.64M | One row per child after notebook cleaning (~80% reduction from 18.3M raw) |
| Labeled CSV | `cleaned_dataset_with_labels.csv` | ~3.64M × 46 cols | WHO-derived nutrition status columns added per child per month |
| DuckDB | `nutrition_data` table | ~3.45M | Final analytics substrate |

### Schema structure

The table is a **single wide row per child** (`beneficiary_id`), with three fixed time waves encoded as column prefixes — `feb24_`, `mar24_`, `apr24_` — rather than a normalised time index. This is a deliberate design choice: longitudinal questions are expressed as cross-column constraints, not `GROUP BY month`.

For each wave `{m}`, the per-child columns are:

| Family | Columns | Notes |
|--------|---------|-------|
| Raw measurements | `{m}_height`, `{m}_weight`, `{m}_height_weight_entered_date` | Physical measurements and date of entry |
| Derived status | `{m}_stunting_status`, `{m}_underweight_status`, `{m}_wasting_status` | Categorical severity labels |
| Binary indicators | `{m}_is_stunted`, `{m}_is_underweight`, `{m}_is_wasted`, `{m}_is_sam` | 0/1 flags, used for `AVG(...)` prevalence queries |

**Label vocabularies (closed, case-sensitive):**

- Stunting: `normal` / `moderately_stunted` / `severely_stunted`
- Underweight: `normal` / `moderately_underweight` / `severely_underweight`
- Wasting: `normal` / `overweight` / `obese` / `MAM` / `SAM`

**Geography hierarchy:** `district_name` → `project_id` → `sector_id` → `awc_id`

| Level | Column | Unique values | What it represents |
|-------|--------|---------------|--------------------|
| District | `district_name` (VARCHAR) | 75 | UP's 75 administrative districts (e.g. Agra, Lucknow, Varanasi) |
| Project | `project_id` | 897 | ICDS project — roughly one per block/tehsil (~12 per district) |
| Sector | `sector_id` | 6,977 | Supervisor circle (~8 sectors per project) |
| AWC | `awc_id` | 173,264 | Anganwadi Centre — the frontline unit (~25 AWCs per sector) |

The hierarchy is strict: every AWC belongs to exactly one sector, every sector to one project, every project to one district. All geography is denormalised onto every child row — district-level aggregates are a plain `GROUP BY district_name` with no joins. `district_name` is a VARCHAR string (not an ID), so district filters are equality comparisons like `WHERE district_name = 'Lucknow'`. `project_id`, `sector_id`, and `awc_id` are numeric IDs.

### Labeling logic

Labels are derived from three WHO-style assessment parameter PDFs parsed into lookup CSVs by `scripts/parse_assessment_pdfs.py`. The classifier in `src/utils/nutrition_labels.py` applies piecewise thresholds — a deterministic, closed-world rule engine with no learned weights. For each child and each month, `classify_all(sex, age_days, height_cm, weight_kg)` produces 7 output values.

#### How labels are produced end-to-end

```
WHO Assessment PDFs
  (StuntedAssessmentParameters.pdf, etc.)
         │
         ▼ parse_assessment_pdfs.py  (PyPDF2 + regex)
         │
  stunting_lookup.csv      ← (sex, age_days)           → (h_severe_max, h_normal_min)
  underweight_lookup.csv   ← (sex, age_days)           → (w_severe_max, w_normal_min)
  wasting_lookup.csv       ← (sex, age_band, height_cm) → (sam_upper, mam_upper, normal_upper, overweight_upper)
         │
         ▼ add_nutrition_labels.py  (streams in 50k-row chunks)
         │  For each row × each month (feb24, mar24, apr24):
         │    classify_all(sex, age_days, height_cm, weight_kg)
         │    → nearest-day or nearest-height lookup if exact key is missing
         │    → returns 7 values per month
         │
  cleaned_dataset_with_labels.csv  (3.64M rows, +21 label columns: 7 × 3 months)
         │
         ▼ build_nutrition_db.py
         │
  nutrition_data table in DuckDB
```

#### Indicator 1 — Stunting (height-for-age)

**What it captures:** Chronic, long-term malnutrition. A child is stunted if their height is too low for their age and sex. Reflects cumulative deprivation over months or years — not a recent event.

**Inputs:** `sex`, `age_days`, `height_cm`

**Lookup:** The table `stunting_lookup.csv` has one row per `(sex, age_in_days)` pair with two height thresholds. If no exact day match exists, the classifier uses **nearest-day interpolation** (binary search to the closest age in the table).

**Classification:**
```
height ≤ h_severe_max   →  severely_stunted    (is_stunted = True)
height ≤ h_normal_min   →  moderately_stunted  (is_stunted = True)
height >  h_normal_min  →  normal              (is_stunted = False)
```

**Output columns:** `{m}_stunting_status`, `{m}_is_stunted`

**Feb 2024 distribution (3.45M children):**

| Status | Count | Share |
|--------|-------|-------|
| normal | 1,570,127 | 45.5% |
| moderately_stunted | 931,992 | 27.0% |
| severely_stunted | 831,923 | 24.1% |
| null (missing height) | 113,667 | 3.3% |

#### Indicator 2 — Underweight (weight-for-age)

**What it captures:** Overall nutritional status — a composite of both chronic and acute signals. A child is underweight if their weight is too low for their age and sex. Less specific than stunting or wasting individually, but widely used for programme tracking.

**Inputs:** `sex`, `age_days`, `weight_kg`

**Lookup:** Same structure as stunting — `(sex, age_in_days)` → two weight thresholds, with nearest-day interpolation.

**Classification:**
```
weight ≤ w_severe_max   →  severely_underweight    (is_underweight = True)
weight ≤ w_normal_min   →  moderately_underweight  (is_underweight = True)
weight >  w_normal_min  →  normal                  (is_underweight = False)
```

**Output columns:** `{m}_underweight_status`, `{m}_is_underweight`

**Feb 2024 distribution:**

| Status | Count | Share |
|--------|-------|-------|
| normal | 2,552,159 | 74.0% |
| moderately_underweight | 548,000 | 15.9% |
| severely_underweight | 233,883 | 6.8% |
| null (missing weight) | 113,667 | 3.3% |

#### Indicator 3 — Wasting (weight-for-height)

**What it captures:** Acute, recent malnutrition. A child is wasted if their weight is too low for their current height — independent of age. Unlike stunting, this can change rapidly; it reflects current food intake and illness, making it the most actionable indicator for immediate intervention.

**Inputs:** `sex`, `age_days`, `height_cm`, `weight_kg`

**Lookup:** The key is a triple `(sex, age_band, height_cm)`. Age is first bucketed into `"0-2"` (≤730 days) or `"2-5"` (>730 days). Then the table is searched by (sex, age_band, rounded height). If no exact height match, **nearest-height interpolation** finds the closest row within the same sex+age_band group. The row returns four weight thresholds.

**Classification:**
```
weight ≤ sam_upper         →  SAM        (is_wasted=True,  is_sam=True)
weight ≤ mam_upper         →  MAM        (is_wasted=True,  is_sam=False)
weight ≤ normal_upper      →  normal     (is_wasted=False, is_sam=False)
weight ≤ overweight_upper  →  overweight (is_wasted=False, is_sam=False)
weight >  overweight_upper →  obese      (is_wasted=False, is_sam=False)
```

Wasting is the only indicator with **5 output classes** (including overweight and obese at the upper end) and the only one that produces `is_sam` as a separate flag. SAM (Severe Acute Malnutrition) is the most critical state and the primary target for ICDS intervention programmes.

**Output columns:** `{m}_wasting_status`, `{m}_is_wasted`, `{m}_is_sam`

**Feb 2024 distribution:**

| Status | Count | Share |
|--------|-------|-------|
| normal | 2,200,778 | 63.8% |
| null (missing height or weight) | 933,984 | 27.1% |
| obese | 110,449 | 3.2% |
| overweight | 93,190 | 2.7% |
| MAM | 77,658 | 2.3% |
| SAM | 31,650 | 0.9% |

Wasting has significantly more nulls than the other two indicators because it requires **both** height and weight to be non-zero — a stricter requirement than stunting (height only) or underweight (weight only).

---

## 4. Pipeline

### Technical Workflow (from pitch deck)

```
Input
  ├─ ASR (speech → text via Bhashini)       ─┐
  ├─ Text (typed question)                   ├─► Language Processing (NLP Model)
  └─ Query Chip (preloaded question)         ─┘          │
                                                          ▼
                                              ┌─────────────────────┐
                         Poshan Data ────────►│    AI Framework      │───► Output
                         (JSON / Flat Files / │                      │     ├─ Translation
                          Hierarchical / API  │  • Interprets query  │     └─ Answer in
                          Feeds / Relational) │  • Structures request│       user's language
                                              │  • Fetches records   │
                                              │  • Generates response│
                                              └─────────────────────┘
                                                (Response Model — government-cleared)
```

### Current PoC implementation

#### ETL (offline, run once)

```
WHO Assessment PDFs
   └─ parse_assessment_pdfs.py  →  data/processed/*_lookup.csv

Raw ICDS CSV (18.3M rows)
   └─ Notebook cleaning  →  cleaned_dataset.csv (3.64M rows)

cleaned_dataset.csv + lookup CSVs
   └─ add_nutrition_labels.py  →  cleaned_dataset_with_labels.csv (46 cols)

cleaned_dataset_with_labels.csv
   └─ build_nutrition_db.py  →  nutrition_data_filtered.duckdb
```

#### Query path (per question)

```
User question
   │
   ├─ [Gradio only] Semantic cache lookup (FAISS cosine ≥ 0.95)
   │         hit  ──► return cached SQL + result (no LLM call)
   │         miss ──► fall through
   │
   └─ TextToSQLEngine.run_single_question()
         1. Build prompt: schema DDL + 3 sample rows + domain rules
         2. LLM call  (gpt-5-mini, temp=0.0)
         3. Extract SQL from response
         4. Execute (read-only) on DuckDB
         5. If execution fails → one repair LLM call → re-execute
         6. Return {sql, result, latency, tokens, first_pass_exec_ok}
   │
   └─ [Gradio only] Rephrase result via gpt-4o-mini
         └─ Chat answer + result table displayed to user
```

#### Key code modules

| File | Role |
|------|------|
| `src/text_sql/engine/pipeline.py` | `TextToSQLEngine` — prompt build, LLM call, SQL extract, execute, repair |
| `src/text_sql/adapters/providers.py` | LangChain OpenAI client with token tracking |
| `src/text_sql/semantic_cache/` | FAISS-backed cache for previously answered questions |
| `apps/gradio_app.py` | Gradio chat UI with result table and rephrase |
| `scripts/run_eval.py` | Evaluation runner |
| `src/text_sql/eval/` | Verification, metrics aggregation |

#### Semantic query cache

Built offline with `build_semantic_query_index.py` from seed questions. At runtime, embeds the user's question using `all-MiniLM-L6-v2` and searches a FAISS flat-IP index. Hits (cosine ≥ 0.95) skip the LLM call entirely. The cache is bypassed during evaluation — every eval query runs the full LLM path.

---

## 5. ML Details — NL→SQL Engine and Semantic Cache

### 5.1 Prompt Construction

The prompt is built **once at engine initialisation** and reused for every query in that session. It is a single concatenated string with five sections.

```
[System prompt template]              ← configs/prompts/nutrition_system.md
  __TABLE_INFO__  ← replaced at init with live DDL from DuckDB
  __SAMPLE_ROWS__ ← replaced at init with 3 literal rows from the table
  __TOP_K__       ← replaced at init with the top_k hint value

+ "\n\nUser question:\n{question}"    ← appended per query at runtime
```

The template has no chat history — every query is a fresh single-turn call. The full prompt is constructed by `build_prompt()` in `src/text_sql/utils/utils.py` using simple string replacement.

#### Section-by-section breakdown of `nutrition_system.md`

**Role + scope:**
```
You are an expert DuckDB SQL analyst for public health nutrition analytics.
Generate SQL for the `nutrition_data` table only.
```
Locks the model to a single table and a specific analytical persona. Prevents cross-table joins that would fail at execution.

**Schema context (`__TABLE_INFO__`):**
Replaced with the output of `SQLDatabase.get_table_info(["nutrition_data"])` — the full DDL description of the table including column names, types, and constraints, retrieved live from DuckDB via LangChain. This is the model's primary source of truth about column names.

**Business grounding:**
Domain-specific invariants injected to close the gap between natural language and the schema:
- Month prefixes are `feb24_`, `mar24_`, `apr24_` — the model must never invent a `jan24_` or `may24_`
- Status string literals must be used exactly as defined (underscore-sensitive, case-sensitive)
- District filtering uses `WHERE district_name = '...'` with no joins required
- The model is told not to use implicit bool/int casting for prevalence

**Hard SQL rules (12 explicit rules):**
These are the highest-signal part of the prompt. Key rules:

| Rule | What it prevents |
|------|-----------------|
| Return exactly one `SELECT` or `WITH` | Multi-statement outputs that break the extractor |
| No markdown fences or commentary | LLM adding explanatory text around the SQL |
| Use `NULLIF(..., 0)` on denominators | Division-by-zero errors on small cohorts |
| Prefer `AVG(CASE WHEN indicator_col = 1 THEN 1 ELSE 0 END) * 100.0` | Implicit bool-to-int casting failures in DuckDB |
| Low birth weight = `birth_weight > 0 AND birth_weight < 2.5` | Model inventing different thresholds |
| Ranked outputs: only return requested fields | Extra columns causing shape-mismatch verdict in eval |
| `LIMIT N` exactly as asked | Model using a different N |
| Month-wise outputs: use `UNION ALL` with a month label column | Model collapsing multi-month output into a single aggregate |
| "Moved from A to B" = adjacent-month conjunction | Model misreading transitions as per-month snapshots |
| "How many" → `COUNT(*)`, "what percentage" → percentage formula | Count/rate confusion (the most common failure mode) |

**Reference SQL patterns:**
Four literal SQL templates pasted directly into the prompt for the most compositionally complex queries — longitudinal improvement rate, data completeness rate, and single/multi-period transition counts. These are **few-shot demonstrations** inside the system prompt rather than in-context examples.

**Data sample (`__SAMPLE_ROWS__`):**
Replaced with `SELECT * FROM nutrition_data LIMIT 3` at init. Three literal rows from the actual DuckDB, escaped so that braces in column values don't corrupt the f-string template. Provides the model with a concrete sense of value formats (date formats, numeric precision, string casing) that DDL alone cannot convey.

#### Why temperature=0.0

The model is configured with `temperature=0.0` (greedy decoding). SQL generation is a deterministic mapping task — for a given schema and question there is one correct query structure. Temperature > 0 introduces randomness that does not help SQL correctness and makes results non-reproducible across runs.

---

### 5.2 SQL Extraction

After the LLM returns a response, `extract_sql()` in `src/text_sql/sql/extract.py` applies a **three-tier fallback** to extract the SQL statement:

```
1. Look for ```sql ... ``` fenced block            ← preferred (model wraps in code fence)
2. Look for generic ``` ... ``` block              ← only accepted if it contains "select"
3. Regex from first SELECT/WITH keyword to ; or $  ← fallback when model gives raw SQL
```

After extraction, the SQL is stripped of trailing semicolons and re-terminated with exactly one `;`. This normalisation avoids double-semicolon errors in DuckDB execution.

---

### 5.3 Self-Repair Loop

When the first SQL attempt fails to execute, the engine makes a **second LLM call** with a minimal repair prompt:

```
Fix the DuckDB SQL query so it executes successfully.
Return only corrected SQL, no explanation.

Broken SQL:
{sql_query}

Execution error:
{error_message}
```

This is a single-shot repair — there is no retry loop, no beam search, and no candidate ranking. Token counts from both calls are summed. The engine records `first_pass_exec_ok` before repair, allowing the eval to distinguish first-pass failures from repaired successes.

**Current performance:** 16% of queries trigger the repair call; repair success rate is 0%. The model tends to produce a syntactically different query that still fails semantically. The repair prompt currently uses the same model (`gpt-5-mini`) rather than a stronger one, which limits its ability to reason about semantic SQL errors.

---

### 5.4 Semantic Query Cache

The semantic cache is a **retrieval layer** in front of the LLM path that avoids re-running the full NL→SQL pipeline for questions that are sufficiently similar to something already answered.

#### Build phase (`build_semantic_query_index.py`)

For each seed question in `queries.txt`, the build script:

```
1. Run TextToSQLEngine.run_single_question(question)
      → generates SQL, executes on DuckDB, captures result payload

2. classify_query_intent(question, sql, model=gpt-4o-mini, temp=0.0)
      → one LLM call with query_intent_classification.md prompt
      → returns a short phrase (≤12 words) describing what is being measured
      → e.g. "SAM prevalence by district in March 2024"

3. rephrase_reply(question, exec_payload)
      → one LLM call (gpt-4o-mini) to produce a human-readable NL summary
      → e.g. "3.2% of children in Lucknow were severely acutely malnourished in March 2024"

4. SentenceEmbeddingModel.encode_query(question)
      → encodes the natural-language question with all-MiniLM-L6-v2
      → L2-normalises the embedding vector to float32

5. Store IndexedQuery record + embedding vector
```

Each `IndexedQuery` record stores:

| Field | Content |
|-------|---------|
| `natural_language_query` | The original seed question |
| `query_intent_summary` | Short phrase from the intent classifier |
| `generated_sql` | The SQL produced by the engine |
| `sql_execution` | Full execution result dict `{ok, raw_output, error}` |
| `natural_language_summary` | Rephrased NL answer from gpt-4o-mini |
| `database_path` | Absolute path to the DuckDB file at build time |
| `database_file_mtime_ns` | File modification timestamp in nanoseconds |

After processing all seed questions, the script writes two artifacts atomically:

```
database/semantic_query_cache/
  ├── embedding_index.faiss       ← FAISS IndexFlatIP, dim=384, vectors L2-normalised
  └── indexed_queries.jsonl       ← one JSON record per line, aligned with FAISS row order
```

#### Embedding model: `all-MiniLM-L6-v2`

- Model: `sentence-transformers/all-MiniLM-L6-v2`
- Embedding dimension: 384
- Output: L2-normalised float32 vectors (the model's `normalize_embeddings=True` output is further normalised by `normalize_rows()` with a floor of `1e-12` to guard against zero vectors)
- Similarity metric: **inner product** on L2-normalised vectors = **cosine similarity**

The FAISS index is `IndexFlatIP` — a flat (brute-force) index using inner product. This is exact (no approximation), appropriate for a seed corpus of up to a few thousand questions.

#### Runtime lookup (`find_best_match`)

At Gradio query time:

```
1. Embed the incoming question with all-MiniLM-L6-v2 (same model as build)
2. faiss_index.search(query_vec, k=1)  →  (best_score, best_idx)
3. if best_score >= 0.95:
       return corpus[best_idx]         ← cache hit
   else:
       return None                     ← fall through to LLM
```

The **0.95 cosine similarity threshold** is the key design parameter. It is deliberately high — a near-exact semantic match is required, not just topic similarity. A question about SAM prevalence in March would not match one about stunting trends, even though both are nutrition questions.

#### Cache freshness

On a cache hit, Gradio checks whether the cached record's `database_path` and `database_file_mtime_ns` match the currently loaded DuckDB. If they differ (database was updated since the cache was built), it re-executes the cached SQL against the live database before returning the result. If re-execution fails, it falls back to the full LLM path.

#### Intent classification prompt

A separate LLM call to `gpt-4o-mini` produces a short intent label for each indexed record. The prompt (`query_intent_classification.md`) instructs the model to return a single phrase of ≤12 words describing what is being measured, using the question text and generated SQL as context:

```
You classify a data analyst's natural-language question into a short intent label.
Rules:
1) Output a single short phrase (maximum 12 words), no punctuation at the end.
2) Describe what is being measured or compared.
3) Do not include SQL, markdown, or JSON.

User question: __NATURAL_LANGUAGE_QUERY__
Generated SQL (context only): __GENERATED_SQL__
Intent label:
```

This label is stored in the corpus but is not used for retrieval — it is metadata for human inspection of the cache contents.

---

### 5.5 Two-Model Architecture

The system uses two distinct model roles:

| Role | Model | Temp | Where used |
|------|-------|------|------------|
| **SQL generation** | `gpt-5-mini` | 0.0 | `TextToSQLEngine` — main generation and repair |
| **NLG / intent** | `gpt-4o-mini` | 0.0 | `rephrase_reply` (Gradio answer) + `classify_query_intent` (cache build) |

The SQL generator optimises for **executable, schema-correct structure**. The rephrase model optimises for **faithful, human-readable summaries** conditioned on actual execution output. The rephrase prompt explicitly forbids inventing values not present in the execution payload, making it a grounded generation task rather than open-ended NLG.

---

### 5.6 Full Query Path — Data Flow

```
User question (string)
        │
        ▼
[Gradio only] SemanticQueryIndex.find_best_match(question)
        │  embed question with all-MiniLM-L6-v2 (dim=384, L2-norm)
        │  FAISS IndexFlatIP top-1 search
        │  cosine similarity ≥ 0.95?
        ├── YES → IndexedQuery (cached SQL + result + NL summary)
        │           │ freshness check (db_path + mtime_ns)
        │           └── return cached result (no LLM call)
        │
        └── NO ──► TextToSQLEngine.run_single_question(question)
                        │
                        ├─ 1. llm_input = full_prompt + "\n\nUser question:\n" + question
                        │
                        ├─ 2. LLM call: gpt-5-mini (temp=0.0)
                        │      → llm_raw_output (free-form text)
                        │
                        ├─ 3. extract_sql(llm_raw_output)
                        │      → try ```sql fence → try generic fence → regex SELECT/WITH
                        │      → strip/re-terminate with ";"
                        │
                        ├─ 4. execute_sql(db, sql)
                        │      → read-only check (SELECT/WITH only)
                        │      → DuckDB execution
                        │      → {ok, raw_output, error}
                        │
                        ├─ 5. if not ok and repair_enabled:
                        │        repair_prompt = broken SQL + error text
                        │        LLM call #2: gpt-5-mini
                        │        extract_sql → execute_sql again
                        │        merge token counts
                        │
                        └─ 6. return {sql, result, latency_ms, llm_calls, usage, first_pass_exec_ok}
                                        │
                                [Gradio] rephrase_reply(question, exec_payload)
                                        → gpt-4o-mini (grounded NLG)
                                        → NL answer + result table displayed to user
```

---

## 6. Current Status

### What works

| Component | Status |
|-----------|--------|
| ETL pipeline (PDF → label CSV → DuckDB) | Working |
| `TextToSQLEngine` (NL → SQL → DuckDB) | Working |
| Self-repair loop (one retry on execution failure) | Working, but 0% repair success in practice — see §8 |
| Semantic query cache (FAISS + JSONL) | Working |
| Gradio chat UI | Working |
| Eval runner + golden SQL comparison | Working |
| Noisy benchmark infrastructure | Built, not yet run |

### Known gaps

| Gap | Impact |
|-----|--------|
| Data cleaning is a manual notebook step | Rebuild from scratch requires human intervention |
| Token tracking returns 0 for gpt-5-mini | Cannot measure cost per run |
| Repair LLM call has 0% success rate | Adds ~1.5s latency on 16% of queries with no benefit |
| Only 3 sample rows in prompt | Distribution shift for rare districts/values |
| No feedback loop from wrong answers | Errors are not captured for future improvement |
| Language support is English only | Hindi and regional language support not yet implemented |

---

## 6. Eval Setup

The evaluation framework answers: **how reliably does LLM-generated SQL return the correct answer for a given natural-language question?**

### Architecture

```
eval_benchmark.json          ─┐
  (123 tagged questions)      ├─► scripts/run_eval.py ─► TextToSQLEngine (per question)
queries_verified.sql          │                                  │
  (golden SQL, Q1–Q123)     ─┘                                  ▼
                                                  compare_generated_with_verified()
                                                       (execute both on DuckDB,
                                                        compare results numerically)
                                                                  │
                                                                  ▼
                                                       aggregate_verdicts()
                                                                  │
                                                                  ▼
                                              outputs/eval_reports/eval_TIMESTAMP.json
```

### Four measurement layers

| Layer | What it measures | Key metric |
|-------|-----------------|------------|
| **L1 — Execution Accuracy** | Did generated SQL return the same result as golden SQL? | `execution_accuracy = right / (right + wrong)` |
| **L2 — Reliability** | Where do failures occur? Does repair help? | `first_pass_exec_rate`, `repair_rate`, `repair_success_rate` |
| **L3 — Cost & Latency** | Wall-clock time and token spend per question | `avg_latency_ms`, `p95_latency_ms`, token totals |
| **L4 — Per-Category** | Which question types fail? | `per_category[cat].accuracy` |

**Comparison logic:** Both the generated and golden SQL are executed against the same DuckDB. Results are compared recursively with numeric tolerance (1e-9 abs/rel). Row order is normalised before comparison. A query is `right` if executed output matches — even when the SQL text differs. Unexecutable SQL that cannot be compared is `not_checked`.

### How to run

```bash
# Standard benchmark (123 questions)
python scripts/run_eval.py

# Noisy benchmark (40 robustness questions)
python scripts/run_eval.py --benchmark-file data/queries/eval_benchmark_noisy.json

# Without category tags (plain queries.txt)
python scripts/run_eval.py --no-benchmark-tags
```

---

## 7. Eval Dataset

### Standard benchmark — `data/queries/eval_benchmark.json`

123 questions, each tagged with `query_index`, `category`, and `notes`. The golden SQL for every entry lives in `queries_verified.sql` under the `-- Q{N}:` header with verified results stored in comments. All golden SQL was executed against the live DuckDB before being added.

**Category breakdown:**

| Category | n | What it tests |
|----------|---|---------------|
| `prevalence` | 20 | Single or multi-month percentage indicators (`AVG(is_*)`) |
| `ranking` | 19 | `GROUP BY` + `ORDER BY` + `LIMIT` at district/project/sector/AWC level |
| `trend` | 17 | Month-to-month status transitions across column prefixes |
| `demographic_filter` | 16 | Subgroup filters — gender, birth weight, age |
| `data_quality` | 14 | Height/weight/date completeness queries |
| `count` | 13 | `COUNT(*)` / `COUNT DISTINCT` (not percentages) |
| `multi_axis` | 13 | Joint conditions across stunting + underweight + wasting simultaneously |
| `cohort` | 11 | Longitudinal recovery — "were X in Feb and recovered by April" |
| **Total** | **123** | |

**Representative queries by category:**

- *prevalence:* "What is the month-wise percentage of SAM children in Feb, Mar, and Apr?"
- *ranking:* "Which 10 districts showed the biggest reduction in SAM percentage from February to April?"
- *trend:* "What percentage of SAM children in February improved to normal wasting status by March?"
- *demographic_filter:* "Among children with low birth weight, what percentage are SAM in February?"
- *data_quality:* "What percentage of children have missing height or weight in at least one of the three months?"
- *count:* "How many children were both SAM and stunted in March 2024?"
- *multi_axis:* "What percentage of children were stunted, underweight, and wasted at the same time in April?"
- *cohort:* "What percentage of SAM children in February had fully recovered to normal wasting status by April?"

### Noisy benchmark — `data/queries/eval_benchmark_noisy.json`

40 questions designed to test the robustness of the SQL agent to real-world query noise. Each entry carries a `noisy_id` (unique key), a `query_index` pointing to the original golden SQL, and a `noise_type`.

| Noise type | n | What it introduces |
|------------|---|-------------------|
| `lexical` | 10 | Typos (`underwieght`, `stuned`), abbreviations (`%`, `SAM%`, `underwt`, `n` for "and") |
| `rephrasing` | 10 | Semantically equivalent rewordings ("proportion/share/fraction", "bounced back to normal", "gender gap") |
| `implicit_time` | 10 | Implicit month references ("first month of data", "most recent month") instead of explicit month names |
| `extra_context` | 10 | Professional preambles that add irrelevant context before the actual question |
| **Total** | **40** | |

---

## 8. Results and Performance

**Latest eval run:** 12 June 2026 · model `gpt-5-mini` · 163 queries

### Overall metrics

| Metric | Value | Notes |
|--------|-------|-------|
| `execution_accuracy` | **89.9%** | right / (right + wrong), on checked queries only |
| `not_checked_rate` | 15.3% | ~25 queries where generated SQL was unexecutable and couldn't be compared |
| `first_pass_exec_rate` | 84.0% | SQL executed without error on the first try |
| `repair_rate` | 16.0% | Triggered the second repair LLM call |
| `repair_success_rate` | **0%** | Repair never recovered a wrong answer |
| `total_failure_rate` | 8.6% | 14 definitively wrong answers out of 163 |
| `avg_latency_ms` | 9,558 ms | ~9.6 s per question end-to-end |
| `p95_latency_ms` | 18,028 ms | Tail latency ~18 s |

### Per-category accuracy

| Category | n | Right | Wrong | Accuracy |
|----------|---|-------|-------|----------|
| `count` | 17 | 13 | 0 | **100%** |
| `data_quality` | 15 | 15 | 0 | **100%** |
| `multi_axis` | 17 | 17 | 0 | **100%** |
| `prevalence` | 32 | 31 | 1 | 96.9% |
| `demographic_filter` | 18 | 17 | 1 | 94.4% |
| `trend` | 22 | 17 | 2 | 89.5% |
| `cohort` | 16 | 11 | 5 | 68.8% |
| `ranking` | 26 | 3 | 5 | **37.5%** |

### Failure analysis

**`ranking` (37.5% — worst category, 5 failures):**
Failures at sector and AWC level. Root causes: model returns extra columns causing shape mismatch; returns wrong geography level; omits `HAVING COUNT(*) >= N` guard, causing small-sample distortion (e.g. a sector with only 41 children ranking first at 24% SAM).

**`cohort` (68.8%, 5 failures):**
Cohort questions require cross-column conjunction (`feb24_is_X = 1 AND apr24_is_X = 0`). The model occasionally writes `GROUP BY month` as if the table were tall/normalised, or conflates Feb→Mar recovery with Feb→Apr recovery.

**`trend` (89.5%, 2 failures):**
Multi-step transitions using `UNION ALL` are sometimes collapsed into a single subquery, producing aggregated rather than per-step percentages.

**Repair loop:**
16% of queries triggered a repair call. Repair success rate is 0% — the model produces a syntactically different query that still fails semantically. The repair path currently adds latency (~1.5s) with no benefit.

---

## 9. Phased Approach

The pitch deck defines four deployment phases. The current PoC corresponds to Phase 1.

| Phase | Name | Description | Status |
|-------|------|-------------|--------|
| **Phase 1** | Proof of Concept | Connect TTD with a sample subset of Poshan Tracker data. Validate that NL queries return accurate, meaningful results across key indicators. | **Current — UP Growth Faltering dataset (3.64M children, Feb–Apr 2024)** |
| **Phase 2** | Internal Validation | Test with a small group of MoWCD programme officers and state officials. Gather feedback on query accuracy, language support, and usability. | Not started |
| **Phase 3** | Controlled Pilot | Deploy across 2–3 selected states with CDPOs and district officials as primary users. Measure time saved, quality of insights, and adoption across user roles. | Not started |
| **Phase 4** | Full Deployment | Roll out across all states and UTs. Enable multilingual support, integrate additional Poshan Tracker data tables, establish ongoing model governance and audit processes. | Not started |

---

## 10. Next Steps

### High-impact, low-effort (PoC improvements)

1. **Fix `ranking` queries** — add explicit prompt instructions to use the correct geography-level hierarchy in `GROUP BY`, and always apply `HAVING COUNT(*) >= 50` for sector/AWC-level rankings. These 5 failures are the largest single category gap.

2. **Fix `cohort` queries** — add one worked example for Feb→Apr longitudinal recovery to the system prompt, showing the correct cross-column conjunction. One clear example would likely close most of the 5 cohort failures.

3. **Disable the repair loop** — current repair success rate is 0%. Remove it to reduce latency by ~1.5s on 16% of queries, or replace the repair model with a stronger one (e.g. gpt-4o).

4. **Run the noisy benchmark** — `eval_benchmark_noisy.json` is built and the runner supports it, but no noisy eval run exists yet. Run it to establish robustness baselines across the four noise types.

5. **Fix token tracking** — `gpt-5-mini` returns zero token counts via the current LangChain adapter. Fix usage extraction to enable cost-per-run monitoring.

### Phase 2 readiness

6. **Hindi language support** — implement query translation before SQL generation (Bhashini integration), and response translation after result rephrasing. Required for all user roles below state level.

7. **Expand semantic cache** — seed from 123 verified questions (currently seeded from an earlier subset). More coverage = more cache hits = lower latency and cost in the Gradio app.

8. **Add structured logging** — INFO-level logs for LLM-generated SQL, execution results, and repair attempts. Currently all observability is through trace JSON files written to `outputs/`.

### Structural / compliance

9. **Automate data cleaning** — replace the manual notebook cleaning step with a `clean_dataset.py` script to make the full ETL pipeline reproducible end-to-end.

10. **Feedback capture** — when a user marks an answer as wrong in Gradio, log the (question, bad SQL, correction) triple. Even 50 corrections would enable targeted prompt improvements and support Phase 2 accuracy assessment.

11. **Role-based access controls** — implement the user-role model from the pitch deck (Ministry / State / Block / Field) so that query scope and visible data are restricted by role, in line with post-deployment security requirements.
