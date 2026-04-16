# Test suite catalog (documentation-aligned)

This document catalogs manual and automated test cases for a **database-agnostic** agent: automated tests use **generic** DuckDB fixtures (e.g. table `t`) and connectivity checks **discover** table names at runtime instead of assuming a domain schema. They do **not** assert on `OPENAI_API_KEY` or call the live OpenAI API. Scope: DuckDB connectivity, repo path resolution, SQL safety, execution helpers, benchmarks, Gradio wiring, CLI usage, and result formatting (including rephrase **fallback** paths with mocks).

**Running pytest**

```bash
PYTHONPATH=. python -m pytest tests/
# Skip optional DuckDB connectivity tests (no RUN_CONNECTIVITY):
PYTHONPATH=. python -m pytest tests/ -m "not requires_db"
# Run connectivity tests (needs a real DuckDB file, default or DB_PATH):
RUN_CONNECTIVITY=1 PYTHONPATH=. python -m pytest tests/ -m requires_db
```

Markers are defined in [pytest.ini](../pytest.ini). `requires_db` tests are skipped unless `RUN_CONNECTIVITY=1` is set (see [tests/conftest.py](../tests/conftest.py)).

---

## Test case table

| Test ID | Scenario type | Description | Preconditions | Inputs / steps | Expected result |
|---------|----------------|-------------|---------------|----------------|-----------------|
| INF-S1-01 | Happy | DuckDB file opens | Valid DB_PATH; file exists | duckdb.connect(path); SELECT 1 | Returns one row |
| INF-S1-02 | Happy | LangChain reflects an existing table | Same as S1-01; DB has ≥1 table | `SHOW TABLES` → first name → `get_table_info([name])` | DDL/info includes that table name |
| INF-S1-03 | Negative | Missing database file | Path does not exist | connect or from_uri | FileNotFoundError or driver error |
| INF-S1-04 | Negative | Empty database (no tables) | Valid empty DuckDB | Introspection / skip | No table to reflect; skip or clear failure |
| INF-S5-02 | Happy | rephrase fallback on failed SQL | None | exec_payload ok=False | _fallback_reply text mentioning error |
| INF-S6-01 | Happy | Gradio Blocks build | Paths resolvable | build_app(...); isinstance(demo, gr.Blocks) | True |
| INF-S6-02 | Edge | Empty chat message | Built app | chat_handler('', []) | Please enter a question. |
| CONN-003 | Happy | execute_sql SELECT | SQLDatabase | execute_sql(db, SELECT 1) | ok True |
| CONN-004 | Negative | execute_sql INSERT blocked | SQLDatabase | execute_sql INSERT... | ok False, blocked message |
| CONN-005 | Happy | run_query_dataframe limit | DB path | run_query_dataframe(path, SELECT...) | DataFrame rows <= limit |
| CONN-006 | Negative | run_query_dataframe mutating | DB path | DELETE... | Single error column DF |
| REPH-001 | Happy | _build_rephrase_payload rows | Parsed list output | payload from exec | status ok, preview list |
| REPH-002 | Edge | Empty raw_output | ok True empty | _build_rephrase_payload | status empty |
| PATH-001 | Happy | resolve_config_path relative | orig=/repo | `database/x.duckdb` | `/repo/database/x.duckdb` |
| PATH-002 | Happy | resolve_config_path absolute | orig=/repo | `/abs/x.duckdb` | `/abs/x.duckdb` |
| GRADIO-001 | Happy | build_app returns Blocks | tmp db, JSONL catalog path, mock llm | build_app(...) | gr.Blocks |
| GRADIO-002 | Manual | Launch smoke ephemeral port | Deps installed | demo.launch(server_port=0); close | No bind error |
| GRADIO-003 | Manual | Sample query from UI | Full stack | Click example | Table + reply |
| GRADIO-004 | Edge | Unicode question | Running app + DB | Question with non-ASCII | No crash |
| GRADIO-005 | Happy | SAVE_TRACE writes JSON | output_dir writable | chat with save_trace | file in outputs |
| SQL-001 | Happy | extract_sql markdown sql fence | None | text with ```sql | SELECT extracted |
| SQL-002 | Happy | extract_sql generic fence | SELECT inside ``` | extract | SQL extracted |
| SQL-003 | Edge | extract_sql no fence | Raw SELECT line | extract | Statement ends with ; |
| SQL-004 | Happy | normalize_sql | Whitespace | normalize_sql | lower single spaces |
| SQL-005 | Happy | is_read_only SELECT | None | is_read_only_sql('SELECT 1') | True |
| SQL-006 | Happy | is_read_only WITH | None | WITH t AS... | True |
| SQL-007 | Negative | is_read_only INSERT | None | INSERT | False |
| SQL-008 | Negative | is_read_only DELETE | None | DELETE | False |
| SQL-009 | Edge | Leading parenthesis | None | (select 1) | True if starts with select after strip |
| SQL-010 | Negative | Semicolon injection | WITH a AS (SELECT 1) DELETE... | Model output | Blocked if parser catches; else execute fails |
| DB-001 | Happy | parse_raw_output list | Valid str(list) | parse_raw_output | Python list |
| DB-002 | Negative | parse_raw_output garbage | Non-literal str | parse_raw_output | Fallback string/list behavior per impl |
| DB-003 | Happy | warmup_runtime missing db | No file | warmup_runtime | Returns without raise |
| DB-004 | Happy | execute_sql_list multiple | DB | two SELECTs | ok True |
| RUN-002 | Negative | Missing DB file | Dummy env sufficient to reach path check | run_single_question | FileNotFoundError |
| RUN-003 | Happy | prefer_verified_templates no-op | Valid args | pass True | No exception; comparison not_checked |
| BENCH-001 | Happy | compare_structured_values ints | None | 1 vs 1 | match True |
| BENCH-002 | Happy | float tolerance | None | 1.0 vs 1.0000000001 | match True |
| BENCH-003 | Negative | dict value mismatch | None | a:1 vs a:2 | match False |
| BENCH-004 | Happy | parse_verified_sql_by_query | Temp file Q1 Q2 | parse | dict keys 1,2 |
| BENCH-005 | Negative | parse missing file | Bad path | parse_verified_sql_by_query | FileNotFoundError |
| BENCH-006 | Happy | compare_generated empty verified | DB | verified_sql_list [] | verdict not_checked |
| BENCH-007 | Negative | compare_generated failed exec | sql_exec ok false | compare | verdict wrong |
| CLI-001 | Happy | Single question via Hydra | Built DB + CLI env | `llm_to_sql.py question='Q?'` | Exit 0 output |
| CLI-002 | Happy | Config override on CLI | Built DB + CLI env | `llm_to_sql.py llm.model=x question='Q'` | Uses model x |
| CLI-003 | Negative | Bad queries file path | None | `queries_file=/no` | Non-zero or error message |
| CLI-004 | Edge | Batch trace JSON | queries file | `queries_file=... paths.output_dir=...` | JSON written |
| APP-001 | Happy | ground_truth_html missing file | Path missing | ground_truth_html | Message mentions catalog / file not found |
| APP-002 | Happy | _fallback_reply scalar | ok True one tuple | _fallback_reply | mentions result |
| APP-003 | Negative | _fallback_reply sql error | ok False | _fallback_reply | SQL execution failed |
| SQL-011 | Edge | extract_sql multiple statements first wins | Text with two SELECTs | extract_sql | First statement pattern |
| BENCH-008 | Edge | compare list length mismatch | lists diff len | compare_structured_values | shape false |
| GRADIO-006 | Manual | Queue concurrency | env GRADIO_QUEUE_CONCURRENCY | stress two tabs | No deadlock |
| SQL-012 | Negative | extract_sql with no SELECT | Prose only | extract_sql | Appends `;` to stripped text per implementation |
| SQL-013 | Happy | is_read_only leading whitespace | None | `"  SELECT 1"` | True |
| DB-005 | Happy | execute_sql exception becomes dict | Broken SQL syntax | execute_sql | ok False, error string |
| DB-006 | Happy | extract_exec_items single result | No results list | extract_exec_items | Single synthetic item |
| RUN-004 | Happy | run_query_dataframe wraps LIMIT | Valid SELECT | limit=5 | At most 5 rows |
| BENCH-009 | Edge | compare_structured_values tuple order | Two tuples | element-wise compare | Match when values align |
| BENCH-010 | Negative | parse_verified_sql_by_query no Q headers | SQL file without `-- Qn:` | parse | Empty or partial dict |
| CLI-005 | Negative | llm_to_sql no question and no batch flags | None | Hydra defaults (`question=''`, `queries_file=null`) | Usage error or exit non-zero |
| APP-004 | Happy | `_build_rephrase_payload` scalar tuple | ok True, one-column row | build payload | is_scalar True |
| GRADIO-007 | Manual | Ground truth panel scroll | Verified SQL file exists | Open accordion | HTML table renders |
| GRADIO-008 | Edge | `save_history` true | Gradio 5 | Two turns | History persisted per Gradio behavior |
| CONN-008 | Happy | `parse_raw_output` None | None | parse_raw_output(None) | None |
| REPH-004 | Edge | rephrase on exception uses fallback | Mock LLM raising | rephrase_reply | Returns fallback string |
| INF-S1-05 | Edge | DuckDB concurrent read | WAL mode default | Two readers | Both succeed read-only |
| BENCH-011 | Happy | compare_generated_with_verified shape mismatch | Different column counts | compare | verdict wrong, shape_match false |
| SQL-014 | Edge | is_read_only case insensitivity | None | mixed-case SELECT | True |
| APP-005 | Happy | `build_result_table` error path | exec ok False | build_result_table | Markdown mentions error |
| GRADIO-009 | Manual | WARMUP_ON_START true | Server start | First query latency | Lower than cold if primed |

---

## Mapping: existing automated tests → Test IDs

| Automated test (file) | Covers Test IDs |
|-----------------------|-----------------|
| `tests/test_nutrition_sql_pure.py::test_extract_sql_from_markdown_fence` | SQL-001 |
| `tests/test_nutrition_sql_pure.py::test_normalize_sql_collapses_whitespace` | SQL-004 |
| `tests/test_nutrition_sql_pure.py::test_compare_structured_values` (parametrize) | BENCH-001, BENCH-002, BENCH-003 |
| `tests/test_nutrition_sql_pure.py::test_parse_verified_sql_by_query` | BENCH-004 |
| `tests/test_pipeline_units.py::test_resolve_config_path_relative` | PATH-001 |
| `tests/test_pipeline_units.py::test_resolve_config_path_absolute` | PATH-002 |
| `tests/test_pipeline_units.py::test_is_read_only_sql` | SQL-005–008, SQL-013–014 |
| `tests/test_pipeline_units.py::test_extract_sql_no_select_appends_semicolon` | SQL-012 |
| `tests/test_pipeline_units.py::test_execute_sql_*` | CONN-003, CONN-004, DB-005 |
| `tests/test_pipeline_units.py::test_run_query_dataframe_*` | CONN-005, CONN-006, RUN-004 |
| `tests/test_pipeline_units.py::test_warmup_runtime_missing_db` | DB-003 |
| `tests/test_pipeline_units.py::test_parse_raw_output_none` | CONN-008 |
| `tests/test_pipeline_units.py::test_extract_exec_items_single` | DB-006 |
| `tests/test_pipeline_units.py::test_run_single_question_missing_db` | RUN-002 |
| `tests/test_pipeline_units.py::test_compare_structured_values_*` | BENCH-008, BENCH-009 |
| `tests/test_pipeline_units.py::test_parse_verified_sql_by_query_no_headers` | BENCH-010 |
| `tests/test_pipeline_units.py::test_compare_generated_with_verified_*` | BENCH-006, BENCH-007 |
| `tests/test_pipeline_units.py::test_build_app_returns_blocks` | GRADIO-001, INF-S6-01 |
| `tests/test_pipeline_units.py::test_build_rephrase_payload_scalar` | APP-004 |
| `tests/test_pipeline_units.py::test_build_result_table_sql_error` | APP-005 |
| `tests/test_pipeline_units.py::test_rephrase_reply_falls_back_on_llm_error` | REPH-004 |
| `tests/test_connectivity_inference.py::test_duckdb_connect_and_select_one` | INF-S1-01 |
| `tests/test_connectivity_inference.py::test_langchain_reflects_existing_table` | INF-S1-02 |