# ML Pipeline — Technical Reference

This document describes every stage of the Talk to Data system in ML terms: what problem each stage solves, what model or algorithm is used, what the inputs and outputs are, and exactly where in the codebase it is implemented.

The system is best read as a **deterministic feature-engineering pipeline feeding a constrained code-generation model**, with a nearest-neighbour retrieval layer in front to short-circuit repeated or similar queries.

---

## Stage 0 — Reference Data Extraction

### ML framing
This is **closed-world threshold library construction** - not learned, not probabilistic. The WHO assessment PDFs define exact piecewise decision boundaries in (age, sex, anthropometry) space. The output is a deterministic lookup function, not a model.

### What it does
Parses three WHO-style PDFs into structured CSV lookup tables used by the classifier in Stage 1.

| Output file | Key | Value |
|-------------|-----|-------|
| `stunting_lookup.csv` | `(sex, age_days)` | `(h_severe_max, h_normal_min)` |
| `underweight_lookup.csv` | `(sex, age_days)` | `(w_severe_max, w_normal_min)` |
| `wasting_lookup.csv` | `(sex, age_band, height_cm)` | `(sam_upper, mam_upper, normal_upper, overweight_upper)` |

### Codebase
| File | Role |
|------|------|
| [`scripts/parse_assessment_pdfs.py`](../scripts/parse_assessment_pdfs.py) | Entry point: PyPDF2 text extraction + regex parsing → CSV output |
| [`data/processed/`](../data/processed/) | Output directory for lookup CSVs |

---

## Stage 1 — Multi-task Rule-based Labeling

### ML framing
This is **multi-task, multi-head labeling** over a large tabular dataset. For each child × month combination, three independent classifiers run in parallel, each producing one categorical status head and one or more binary indicator heads. No weights are learned — the classifiers are deterministic threshold functions applied to `(sex, age, measurement)` triples.

The nearest-neighbour interpolation step is analogous to a **1-NN lookup** in a discrete feature space: when an exact (sex, day) key is absent, the classifier finds the closest key by binary search.

### Input / output
- **Input:** `(sex, age_days, height_cm, weight_kg)` per child per month
- **Output:** 7 values per child per month (21 new columns total across 3 months)

| Head | Type | Values |
|------|------|--------|
| `stunting_status` | 3-class categorical | `normal` / `moderately_stunted` / `severely_stunted` |
| `is_stunted` | binary | 0 / 1 |
| `underweight_status` | 3-class categorical | `normal` / `moderately_underweight` / `severely_underweight` |
| `is_underweight` | binary | 0 / 1 |
| `wasting_status` | 5-class categorical | `normal` / `overweight` / `obese` / `MAM` / `SAM` |
| `is_wasted` | binary | 0 / 1 |
| `is_sam` | binary | 0 / 1 — SAM is a sub-condition of wasting |

### Classification logic per indicator

**Stunting and Underweight** — same structure, different lookup tables:
```python
row = _nearest_day(sex, age_days, table)   # binary search to closest age
h_severe_max, h_normal_min = row
if measurement <= h_severe_max:  → severely_*
elif measurement <= h_normal_min: → moderately_*
else:                             → normal
```

**Wasting** — uses both height and weight, with an age-band bucketing step:
```python
age_band = "0-2" if age_days <= 730 else "2-5"
row = _nearest_height(sex, age_band, height_cm, table)  # nearest-height lookup
sam_upper, mam_upper, normal_upper, overweight_upper = row
if weight <= sam_upper:        → SAM   (is_wasted=True,  is_sam=True)
elif weight <= mam_upper:      → MAM   (is_wasted=True,  is_sam=False)
elif weight <= normal_upper:   → normal
elif weight <= overweight_upper: → overweight
else:                          → obese
```

### Interpolation
When an exact lookup key is missing, both `_nearest_day` and `_nearest_height` use **1-NN by Euclidean distance** along a single axis (age in days, or height in cm). There is no smoothing or interpolation between adjacent entries — the nearest discrete threshold row is used as-is.

### Codebase
| File | Role |
|------|------|
| [`src/utils/nutrition_labels.py`](../src/utils/nutrition_labels.py) | All three classifiers: `classify_stunting`, `classify_underweight`, `classify_wasting`, `classify_all`; lookup loading; nearest-day/height interpolation |
| [`scripts/add_nutrition_labels.py`](../scripts/add_nutrition_labels.py) | Streaming runner: reads `cleaned_dataset.csv` in 50k-row chunks, calls `classify_all` per row per month, appends 21 label columns |

---

## Stage 2 — Materialized Analytics Warehouse

### ML framing
This is a **materialized wide-table view** of the full labeled tensor. The schema is deliberately denormalised: one row per child, three time waves as column prefixes (`feb24_`, `mar24_`, `apr24_`). This is not a normalised relational model — it is a columnar layout optimised for analytical queries.

The design choice has a direct consequence for the LM in Stage 3: **longitudinal questions are expressed as cross-column constraints**, not as `GROUP BY month` on a tall table. The model must understand this wide layout to generate correct SQL.

### Input / output
- **Input:** `cleaned_dataset_with_labels.csv` — 3.64M rows × 46 columns
- **Output:** `nutrition_data` table in DuckDB — ~3.45M rows (rows with zero height/weight dropped during build)

### Codebase
| File | Role |
|------|------|
| [`scripts/build_nutrition_db.py`](../scripts/build_nutrition_db.py) | CSV → pandas → `CREATE OR REPLACE TABLE nutrition_data` in DuckDB; normalises `*_is_*` columns to numeric 0/1 |
| [`database/nutrition_data_filtered.duckdb`](../database/) | Output: the analytics substrate used at runtime |

---

## Stage 3 — Conditional Code Generation (NL→SQL)

This is the core ML stage. The task is **conditional program synthesis**: map a natural-language question to a single read-only DuckDB `SELECT`/`WITH` statement, conditioned on schema, domain rules, and few-shot demonstrations — all injected via a structured system prompt.

### 3.1 Schema Grounding — Context Construction

Before any LLM call, the engine constructs the grounding context once at initialisation and caches it for the session.

**Table DDL (`__TABLE_INFO__`):**
Fetched via `SQLDatabase.get_table_info(["nutrition_data"])` — LangChain's DuckDB introspection produces a DDL-style description of the table: column names, types, and constraints. This is the model's primary source of truth about schema structure.

**Few-shot rows (`__SAMPLE_ROWS__`):**
`SELECT * FROM nutrition_data LIMIT 3` — three literal rows from the actual DuckDB, providing the model with concrete value formats (date formats, numeric precision, string casing, enum values) that DDL alone cannot convey. Braces in values are escaped (`{` → `{{`) to prevent corruption of the surrounding prompt template.

**Caching:**
Both `table_info` and `sample_rows_text` are fetched once and stored in `self._prompt` at `__init__` time. All queries in the same engine session share the same grounded prefix — the prompt is **stable per session**, not re-fetched per question.

```python
# src/text_sql/engine/pipeline.py — __init__
table_info, sample_rows_text = get_table_info_and_samples(settings, db_path)
self._prompt = build_prompt(settings, table_info, sample_rows_text, top_k)
```

### Codebase
| File | Function | Role |
|------|----------|------|
| [`src/text_sql/utils/utils.py`](../src/text_sql/utils/utils.py) | `get_table_info_and_samples()` | Fetches DDL + 3-row sample from DuckDB |
| [`src/text_sql/utils/utils.py`](../src/text_sql/utils/utils.py) | `build_prompt()` | String-replaces `__TABLE_INFO__`, `__SAMPLE_ROWS__`, `__TOP_K__` in template |
| [`configs/prompts/nutrition_system.md`](../configs/prompts/nutrition_system.md) | — | Prompt template with all 5 sections |

---

### 3.2 Prompt Structure

The full prompt has five sections, assembled once at init:

```
[1] Role + scope           "You are an expert DuckDB SQL analyst..."
[2] Schema context         __TABLE_INFO__  (live DDL from DuckDB)
[3] Business grounding     month prefixes, enum values, district filtering rules
[4] Hard SQL rules         12 numbered rules (see below)
[5] Reference SQL patterns 4 literal SQL templates as few-shot demonstrations
[6] Data sample            __SAMPLE_ROWS__ (3 literal rows)
[7] top_k hint             __TOP_K__
```

Per-query, the user's question is appended as a new user turn:
```
{full_prompt}

User question:
{question}
```

This is a **single-turn call** — there is no chat history, no multi-turn context, and no retrieval-augmented generation. The model sees only the static prompt + the current question.

**The 12 hard SQL rules** are the highest-signal part of the prompt. Each targets a known failure mode:

| Rule | Failure it prevents |
|------|---------------------|
| Return exactly one `SELECT`/`WITH` | Multi-statement outputs breaking the extractor |
| No markdown fences or commentary | Explanatory text wrapping the SQL |
| `NULLIF(..., 0)` on all ratio denominators | Division-by-zero on small cohorts |
| `AVG(CASE WHEN indicator = 1 THEN 1 ELSE 0 END) * 100.0` for prevalence | Implicit bool→int casting failures in DuckDB |
| Low birth weight = `birth_weight > 0 AND birth_weight < 2.5` | Model inventing different thresholds |
| Ranked outputs: only return requested fields | Extra columns causing shape-mismatch in eval |
| Exact `LIMIT N` as stated in the question | Model using a different N |
| No `LIMIT` on single-aggregate questions | Unnecessary limits truncating scalar results |
| Month-wise outputs: `UNION ALL` with month label column | Multi-month output collapsed into a single aggregate |
| "Normal for all three" → joint condition in one metric | Separate per-indicator flags instead of conjunction |
| "How many" → `COUNT(*)`, "what percentage" → percentage formula | Count/rate confusion |
| "Moved from A to B" → adjacent-month conjunction | Treating transitions as per-month snapshots |

**Reference SQL patterns** are four verbatim SQL templates pasted into the system prompt. These are **in-prompt few-shot demonstrations** for the four most structurally complex query types: data completeness rate, longitudinal improvement rate, single-period transition count, multi-period transition count with `UNION ALL`.

### Codebase
| File | Role |
|------|------|
| [`configs/prompts/nutrition_system.md`](../configs/prompts/nutrition_system.md) | Full prompt template — role, grounding, 12 rules, SQL patterns, sample rows |
| [`configs/nutrition_text_to_sql.yaml`](../configs/nutrition_text_to_sql.yaml) | Config: model (`gpt-5-mini`), temperature (`0.0`), `repair_enabled`, sample row limit (`3`) |

---

### 3.3 LLM Call — Decoding

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Model | `gpt-5-mini` | Balance between cost and SQL generation quality |
| Temperature | `0.0` | Greedy decoding — SQL generation is a deterministic mapping, not a creative task; `temperature > 0` adds variance without improving correctness |
| Single-turn | Yes | No chat history; model sees only grounded prompt + question |
| Token tracking | Merged across calls | Prompt + completion tokens summed; repair call tokens added to the same counters |

```python
# src/text_sql/engine/pipeline.py — run_single_question
llm_input = f"{self._prompt}\n\nUser question:\n{question}"
llm_raw_output, usage = self._llm.complete_tracked(llm_input)
```

### Codebase
| File | Role |
|------|------|
| [`src/text_sql/adapters/providers.py`](../src/text_sql/adapters/providers.py) | `LangChainOpenAIChatClient.complete_tracked()` — wraps LangChain `ChatOpenAI`, returns `(content_str, usage_dict)` |
| [`src/text_sql/engine/pipeline.py`](../src/text_sql/engine/pipeline.py) | `run_single_question()` — orchestrates the full LLM → extract → execute → repair loop |

---

### 3.4 SQL Extraction

The LLM output is free-form text — `extract_sql()` applies a **three-tier fallback** to locate the SQL statement:

```
Priority 1: ```sql ... ```  fenced block     ← preferred
Priority 2: generic ``` ... ``` block         ← only if block contains "select"
Priority 3: regex from first SELECT/WITH keyword to ; or end-of-string
```

After extraction: strip trailing semicolons → re-terminate with exactly one `;`. This avoids double-semicolon errors in DuckDB.

A `normalize_sql()` function (lowercase + collapse whitespace) is available separately for comparison purposes in the eval layer — it is not applied before execution.

### Codebase
| File | Function | Role |
|------|----------|------|
| [`src/text_sql/sql/extract.py`](../src/text_sql/sql/extract.py) | `extract_sql(text)` | Three-tier regex fallback |
| [`src/text_sql/sql/extract.py`](../src/text_sql/sql/extract.py) | `normalize_sql(sql)` | Lowercase + whitespace collapse for comparison only |

---

### 3.5 Execution-Guided Validation

The extracted SQL is executed against DuckDB via LangChain's `SQLDatabase.run()`. Before execution, the engine enforces a **read-only constraint**: only `SELECT` and `WITH` statements are permitted. DDL, DML, and system commands raise an error without touching the database.

Execution returns:
```python
{"ok": bool, "raw_output": Any, "error": str | None}
```

The `ok` flag drives both the repair decision and the eval verdict.

### Codebase
| File | Function | Role |
|------|----------|------|
| [`src/text_sql/sql/execute.py`](../src/text_sql/sql/execute.py) | `execute_sql(db, sql)` | Read-only check + DuckDB execution via LangChain SQLDatabase |
| [`src/text_sql/sql/execute.py`](../src/text_sql/sql/execute.py) | `open_sql_database(db_path)` | Opens a DuckDB connection as a LangChain SQLDatabase |

---

### 3.6 Self-Repair Loop

When `first_pass_exec_ok` is `False` and `repair_enabled` is `True` (default), a **second LLM call** is made with a minimal repair prompt:

```
Fix the DuckDB SQL query so it executes successfully.
Return only corrected SQL, no explanation.

Broken SQL:
{sql_query}

Execution error:
{error_message}
```

This is a **single-shot execution-guided refinement** — analogous to one rejection sample in a generate-and-test loop. There is no beam search, no k-best candidates, no multi-step chain-of-thought. The repair call uses the same model (`gpt-5-mini`) and temperature (`0.0`) as the primary call.

**Current performance:** 16% of queries trigger repair; repair success rate is **0%**. The model repairs syntax but not semantics — it produces a different SQL string that still returns wrong results. The root cause is that the repair prompt provides only the error string, which is sufficient for syntax errors but insufficient for semantic mismatches (e.g. wrong column, wrong aggregation logic). Token counts from both calls are merged.

```python
# src/text_sql/engine/pipeline.py
repair_prompt = (
    "Fix the DuckDB SQL query so it executes successfully.\n"
    "Return only corrected SQL, no explanation.\n\n"
    f"Broken SQL:\n{sql_query}\n\n"
    f"Execution error:\n{first_pass_exec.get('error')}\n"
)
repair_llm_output, repair_usage = self._llm.complete_tracked(repair_prompt)
repaired_sql = extract_sql(repair_llm_output)
sql_exec = execute_sql(self._db, repaired_sql)
```

### Codebase
| File | Role |
|------|------|
| [`src/text_sql/engine/pipeline.py`](../src/text_sql/engine/pipeline.py) | Lines 76–96 — repair branch, token merging, `first_pass_exec_ok` flag |
| [`configs/nutrition_text_to_sql.yaml`](../configs/nutrition_text_to_sql.yaml) | `llm.repair_enabled: true` — repair toggle |

---

## Stage 4 — Nearest-Neighbour Query Retrieval (Semantic Cache)

### ML framing
This is a **nearest-neighbour retrieval system** over a pre-indexed corpus of previously answered questions. The retrieval model is a bi-encoder (`all-MiniLM-L6-v2`) that maps questions to a 384-dimensional embedding space. A cosine similarity threshold gates whether the retrieval result is accepted or falls through to the generation model. This is **retrieval as a caching layer**, not retrieval-augmented generation — the retrieved item is a complete cached answer, not a context document.

---

### 4.1 Embedding Model

| Property | Value |
|----------|-------|
| Model | `sentence-transformers/all-MiniLM-L6-v2` |
| Architecture | Bi-encoder (query and document use the same encoder) |
| Embedding dimension | 384 |
| Output normalisation | L2-normalised to unit sphere (`normalize_embeddings=True` + explicit `normalize_rows()` with floor `1e-12`) |
| Similarity metric | **Inner product on L2-normalised vectors = cosine similarity** |
| Index type | FAISS `IndexFlatIP` — exact brute-force inner product search |

The L2 normalisation makes inner product identical to cosine similarity. `IndexFlatIP` is exact (no approximation) — appropriate for a corpus of up to a few thousand seed questions.

```python
# src/text_sql/semantic_cache/embedding_model.py
emb = self._model.encode(texts, normalize_embeddings=True, ...)
return normalize_rows(np.asarray(emb, dtype=np.float32))
```

### Codebase
| File | Role |
|------|------|
| [`src/text_sql/semantic_cache/embedding_model.py`](../src/text_sql/semantic_cache/embedding_model.py) | `SentenceEmbeddingModel` — lazy-loads `SentenceTransformer`, `encode_query()`, `normalize_rows()` |

---

### 4.2 Build Phase

The build script runs offline to populate the FAISS index and JSONL corpus from a set of seed questions.

**For each seed question, in order:**

```
1. TextToSQLEngine.run_single_question(question)
      ↓
   generated_sql + sql_execution payload

2. classify_query_intent(question, sql)        ← separate LLM call, gpt-4o-mini
      ↓
   intent_summary: short phrase ≤12 words
   e.g. "SAM prevalence by district in March 2024"

3. rephrase_reply(question, exec_payload)      ← separate LLM call, gpt-4o-mini
      ↓
   natural_language_summary: grounded 1-2 sentence answer

4. SentenceEmbeddingModel.encode_query(question)
      ↓
   384-dim L2-normalised float32 vector

5. Construct IndexedQuery record + append vector to matrix
```

After all questions are processed, writes two artifacts atomically:

```
database/semantic_query_cache/
  ├── embedding_index.faiss    ← FAISS IndexFlatIP, dim=384
  └── indexed_queries.jsonl   ← one JSON record per line, row i ↔ FAISS vector i
```

The `IndexedQuery` record schema:

| Field | Type | Content |
|-------|------|---------|
| `indexed_query_id` | int | Row index — must match FAISS vector position |
| `natural_language_query` | str | The original seed question |
| `query_intent_summary` | str | Short phrase from intent classifier |
| `generated_sql` | str | SQL produced by the engine |
| `sql_execution` | dict | `{ok, raw_output, error}` |
| `natural_language_summary` | str \| null | Rephrased NL answer |
| `database_path` | str | Absolute DuckDB path at build time |
| `database_file_mtime_ns` | int | File modification time in nanoseconds |

The JSONL row order is enforced to match FAISS vector order — a mismatch raises a `ValueError` at load time.

### Codebase
| File | Role |
|------|------|
| [`scripts/build_semantic_query_index.py`](../scripts/build_semantic_query_index.py) | Full build pipeline: engine → intent → rephrase → embed → write FAISS + JSONL |
| [`src/text_sql/semantic_cache/records.py`](../src/text_sql/semantic_cache/records.py) | `IndexedQuery` dataclass with `to_json_dict()` / `from_json_dict()` |
| [`src/text_sql/semantic_cache/index.py`](../src/text_sql/semantic_cache/index.py) | `SemanticQueryIndex.write_artifacts()` — builds `IndexFlatIP`, writes FAISS + JSONL atomically |

---

### 4.3 Runtime Lookup

At Gradio query time, `find_best_match()` runs a single FAISS top-1 search:

```python
# src/text_sql/semantic_cache/index.py — find_best_match
query_vec = self._embedding_model.encode_query(question)   # (1, 384) float32
scores, indices = self._faiss_index.search(query_vec, 1)   # exact top-1 IP search
best_score = float(scores[0][0])
best_idx   = int(indices[0][0])

if best_idx < 0 or best_score < self._minimum_similarity:  # threshold = 0.95
    return None, best_score                                 # cache miss
return self._corpus[best_idx], best_score                   # cache hit
```

**Threshold design:** 0.95 cosine similarity is deliberately strict. A nutrition prevalence question about SAM in March and one about stunting in February both belong to the "nutrition" domain, but they are not semantically equivalent for query purposes. The threshold ensures only near-paraphrases hit the cache — structural synonyms like "what percentage of children" vs "what fraction of kids" would hit; questions about different indicators or months would not.

---

### 4.4 Cache Freshness

On a cache hit, the app checks whether the database has changed since the cache was built:

```python
# apps/gradio_app.py — _sql_execution_for_cache_hit
current_mtime = int(db_path.stat().st_mtime_ns)
needs_rerun = (
    SEMANTIC_QUERY_CACHE_REEXECUTE_ON_HIT          # forced re-execution flag
    or current_mtime != indexed.database_file_mtime_ns
    or current_path  != indexed.database_path
)
if needs_rerun:
    return execute_sql(db, indexed.generated_sql), True   # re-execute cached SQL
else:
    return copy.deepcopy(indexed.sql_execution), False    # return stored result
```

The `mtime_ns` check is a **data freshness guard**: if the DuckDB file has been replaced (e.g. after a full ETL rebuild), cached execution results from the old database are stale and must be re-run. The guard operates at nanosecond granularity to catch even rapid rebuilds.

### Codebase
| File | Role |
|------|------|
| [`src/text_sql/semantic_cache/index.py`](../src/text_sql/semantic_cache/index.py) | `SemanticQueryIndex` class — `_reload_from_disk()`, `find_best_match()` |
| [`apps/gradio_app.py`](../apps/gradio_app.py) | `_sql_execution_for_cache_hit()` — freshness check + conditional re-execution; `chat_handler()` — cache lookup wired into the query path |

---

### 4.5 Intent Classification

A secondary LLM call at **build time** produces a short intent label for each indexed record. This is not used for retrieval — it is metadata for human inspection.

**Model:** `gpt-4o-mini`, temperature `0.0`, max_tokens `128`

**Prompt (`query_intent_classification.md`):**
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

The prompt provides the SQL alongside the question so the model can resolve ambiguities in the NL question using the generated SQL as ground truth. For example, a vague question like "how are children doing?" would be labelled by the SQL it produced, not by the question text alone.

### Codebase
| File | Role |
|------|------|
| [`src/text_sql/semantic_cache/intent_classification.py`](../src/text_sql/semantic_cache/intent_classification.py) | `classify_query_intent()` — template fill + `ChatOpenAI` call, returns stripped string |
| [`configs/prompts/query_intent_classification.md`](../configs/prompts/query_intent_classification.md) | Intent classification prompt template |

---

## Stage 5 — Grounded Natural Language Generation (Rephrase)

### ML framing
This is a **constrained conditional generation** task: map (question, execution_result) → a 1-2 sentence human-readable answer. The grounding constraint is strict — the model is forbidden from mentioning values not present in the execution payload. This is a second-head architecture: the SQL generator optimises for structural correctness; the rephrase model optimises for faithful compression of structured output into natural language.

### What it does
`rephrase_reply()` takes the user's original question and the DuckDB execution payload and calls `gpt-4o-mini` to produce the chat response text. On cache hits where a stored `natural_language_summary` exists and the SQL was not re-executed, the stored summary is used directly without an LLM call.

### Model
| Parameter | Value |
|-----------|-------|
| Model | `gpt-4o-mini` (env: `REPHRASE_MODEL_NAME`) |
| Temperature | `0.0` |
| Input | User question + JSON execution payload (row-capped preview) |
| Output | 1-2 grounded sentences |

### Codebase
| File | Role |
|------|------|
| [`apps/result_utils.py`](../apps/result_utils.py) | `rephrase_reply(question, exec_payload)` — builds prompt, calls gpt-4o-mini, returns NL answer string |

---

## Stage 6 — Evaluation Framework

### ML framing
**Behavioural evaluation by execution equivalence**: the metric is not string similarity between SQL statements, but whether the executed result set matches the ground-truth result set within numeric tolerance. This tests semantic alignment to reference analytics, not syntactic similarity to a reference query. A query is `right` even when the SQL text is completely different from the golden SQL, as long as it produces the same answer.

---

### 6.1 Golden SQL Corpus

`queries_verified.sql` is the ground-truth dataset. Each entry has the format:
```sql
-- Q{N}: <question>
<verified SQL>;
-- Result (auto-generated...):
-- [json result]
```

`parse_verified_sql_by_query()` parses this file into `dict[query_index → list[sql_string]]` by scanning for `-- Q{N}:` headers, stripping all comment lines, and extracting executable SQL statements.

### Codebase
| File | Function | Role |
|------|----------|------|
| [`src/text_sql/eval/verification.py`](../src/text_sql/eval/verification.py) | `parse_verified_sql_by_query(path)` | Parses `queries_verified.sql` → `dict[int, list[str]]` |
| [`data/queries/queries_verified.sql`](../data/queries/queries_verified.sql) | — | 123 golden SQL entries with verified results |

---

### 6.2 Execution-Based Comparison

For each query, both the generated and verified SQL are executed on the same DuckDB instance. The results are compared by `compare_generated_with_verified()`.

**Comparison pipeline:**

```
generated_sql → execute_sql(db) → actual_output
verified_sql  → execute_sql(db) → expected_output
                                        ↓
                          _pair_result_comparisons()
                                        ↓
              compare_structured_values() — recursive type-aware comparison
                                        ↓
                   {same_result, shape_match, max_numeric_diff}
```

**`compare_structured_values()` handles five types recursively:**

| Type | Comparison method |
|------|------------------|
| Scalar numeric | Absolute + relative tolerance: `|a - b| ≤ max(abs_tol, rel_tol × max(|a|, |b|, 1))` |
| Scalar string | `_normalize_dimension_label()` — normalise month tokens (e.g. `"Feb-Mar"` → `"feb"`) before equality |
| List (rows) | Sorted by `repr`-based stable key (normalises column aliases), then pairwise recursive comparison |
| Dict (row) | If column names match → compare by key; if names differ (SQL aliases vary) → compare by column position |
| Tabular (tall/wide) | `_canonicalize_tabular_value()` converts wide single-row outputs and label+numeric multi-row outputs to a normalised `(label, values)` list before comparison |

This tolerance model means a query producing `33.33%` when the golden SQL produces `33.333333333%` is `right`. A query returning the correct values but with an extra column is `wrong` (shape mismatch).

**Tolerances:** `abs_tol = 1e-9`, `rel_tol = 1e-9` (configured in `nutrition_text_to_sql.yaml`)

### Codebase
| File | Function | Role |
|------|----------|------|
| [`src/text_sql/eval/verification.py`](../src/text_sql/eval/verification.py) | `compare_generated_with_verified()` | Top-level comparator: execute both, return verdict dict |
| [`src/text_sql/eval/verification.py`](../src/text_sql/eval/verification.py) | `compare_structured_values()` | Recursive type-aware comparison with tolerance |
| [`src/text_sql/eval/verification.py`](../src/text_sql/eval/verification.py) | `_canonicalize_tabular_value()` | Normalises wide/tall result formats for cross-format comparison |
| [`src/text_sql/eval/verification.py`](../src/text_sql/eval/verification.py) | `_normalize_dimension_label()` | Month-token normalisation in string comparison |

---

### 6.3 Metrics Aggregation

`aggregate_verdicts()` takes the list of per-query result dicts and computes all four evaluation layers into an `EvalReport` dataclass.

**Layer 1 — Execution Accuracy:**
```python
execution_accuracy = right / (right + wrong)
not_checked_rate   = not_checked / total
```

**Layer 2 — Reliability:**
```python
first_pass_exec_rate  = first_pass_ok_count / total_with_known_first_pass
repair_rate           = repaired_count / total
repair_success_rate   = repair_right / repaired_count   # None if repaired_count == 0
total_failure_rate    = wrong / total
```
`repaired` is detected by checking `r.get("repaired_sql") is not None`. `first_pass_exec_ok` is read directly from the engine's return dict.

**Layer 3 — Latency:**
```python
avg_latency_ms = mean(latencies)
p95_latency_ms = sorted(latencies)[ceil(0.95 * n) - 1]
```
Token totals are summed across all queries and all calls (including repair calls).

**Layer 4 — Per-category:**
For each unique `category` value, accumulates `{n, right, wrong, not_checked}` and computes `accuracy = right / (right + wrong)`.

**Per-noise-type (noisy benchmark only):**
Detected by presence of `noisy_id` in the first benchmark entry. Same structure as per-category but keyed on `noise_type`.

### Codebase
| File | Function | Role |
|------|----------|------|
| [`src/text_sql/eval/metrics.py`](../src/text_sql/eval/metrics.py) | `aggregate_verdicts(results, model, db_path)` | Computes all four layers, returns `EvalReport` |
| [`src/text_sql/eval/metrics.py`](../src/text_sql/eval/metrics.py) | `EvalReport` dataclass | Typed container for all metrics |
| [`scripts/run_eval.py`](../scripts/run_eval.py) | `main()` | Orchestrates the full eval loop: load benchmark → run engine → compare → aggregate → write JSON report |
| [`scripts/run_eval.py`](../scripts/run_eval.py) | `_load_benchmark()` | Detects normal vs noisy benchmark via `noisy_id` presence; sorts by appropriate key |

---

## End-to-End Data Flow Summary

```
[OFFLINE — run once]

WHO PDFs
  └─► parse_assessment_pdfs.py          Stage 0: threshold library
        └─► stunting/underweight/wasting_lookup.csv

Raw ICDS CSV (18.3M rows)
  └─► add_nutrition_labels.py            Stage 1: multi-task labeling
        classify_all(sex, age, h, w) ×3 months
        └─► cleaned_dataset_with_labels.csv (3.64M × 46 cols)

  └─► build_nutrition_db.py             Stage 2: materialized warehouse
        └─► nutrition_data DuckDB table

  └─► build_semantic_query_index.py     Stage 4 build:
        engine → intent LLM → rephrase LLM → embed → FAISS+JSONL
        └─► embedding_index.faiss + indexed_queries.jsonl


[RUNTIME — per user query]

User question
  │
  ▼ [Stage 4 retrieval — Gradio only]
  SemanticQueryIndex.find_best_match()
    encode_query()  →  FAISS top-1 IP search
    cosine ≥ 0.95?
    ├── HIT  → IndexedQuery → freshness check (mtime_ns) → sql_exec
    │           └─► rephrase_reply() if re-executed or no cached summary
    │           └─► return cached NL answer
    │
    └── MISS ─────────────────────────────────────────────────────────────────────►
                                                                                  │
  ▼ [Stage 3 — NL→SQL code generation]                                           │
  TextToSQLEngine.run_single_question(question)                                   │
    │                                                                             │
    ├─ build llm_input = grounded_prompt + "\n\nUser question:\n" + question      │
    ├─ gpt-5-mini (temp=0.0)  →  llm_raw_output                                 │
    ├─ extract_sql()  →  sql (```sql fence / generic fence / regex fallback)      │
    ├─ execute_sql()  →  {ok, raw_output, error}                                 │
    │                                                                             │
    └─ if not ok and repair_enabled:                                              │
         repair prompt (broken SQL + error) → gpt-5-mini → extract → execute     │
         merge token counts                                                       │
    │                                                                             │
    └─► {sql, exec_result, latency_ms, llm_calls, usage, first_pass_exec_ok} ◄──┘
                │
                ▼ [Stage 5 — Grounded NLG — Gradio only]
  rephrase_reply(question, exec_payload)
    gpt-4o-mini (grounded, hallucination-averse)
    └─► NL answer + result table shown to user


[EVAL — offline, against golden SQL]

eval_benchmark.json + queries_verified.sql
  └─► run_eval.py
        for each question:
          TextToSQLEngine.run_single_question()
          compare_generated_with_verified()       Stage 6: execution comparison
            execute generated SQL on DuckDB
            execute golden SQL on DuckDB
            compare_structured_values()           recursive, toleranced
        aggregate_verdicts()                      4-layer metrics
        └─► outputs/eval_reports/eval_TIMESTAMP.json
```
