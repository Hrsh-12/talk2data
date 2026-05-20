from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from langchain_community.utilities import SQLDatabase

from ..config.settings import NutriSqlSettings


def _build_sample_sql(table: str, limit: int, columns: tuple[str, ...] | None) -> str:
    if not columns:
        return f"SELECT * FROM {table} LIMIT {limit}"

    def quote_col(c: str) -> str:
        return '"' + c.replace('"', '""') + '"'

    quoted = ", ".join(quote_col(c) for c in columns)
    return f"SELECT {quoted} FROM {table} LIMIT {limit}"


@lru_cache(maxsize=32)
def _cached_table_info_and_samples(
    db_path_str: str,
    tables_key: str,
    sample_limit: int,
    columns_key: str,
) -> tuple[str, str]:
    tables = tuple(tables_key.split("\x1e")) if tables_key else ("nutrition_data",)
    columns: tuple[str, ...] | None
    if columns_key == "*":
        columns = None
    else:
        columns = tuple(columns_key.split("\x1e")) if columns_key else None

    db_path = Path(db_path_str)
    db = SQLDatabase.from_uri(f"duckdb:///{db_path}")
    table_list = list(tables)
    table_info = db.get_table_info(table_list)
    primary = table_list[0]
    sample_sql = _build_sample_sql(primary, sample_limit, columns)
    sample_rows_text = db.run(sample_sql)
    sample_rows_text = str(sample_rows_text).replace("{", "{{").replace("}", "}}")
    return table_info, sample_rows_text


def get_table_info_and_samples(settings: NutriSqlSettings, db_path: Path) -> tuple[str, str]:
    tables_key = "\x1e".join(settings.tables)
    cols = settings.sample_rows_columns
    columns_key = "*" if not cols else "\x1e".join(cols)
    return _cached_table_info_and_samples(
        str(db_path.resolve()),
        tables_key,
        settings.sample_rows_limit,
        columns_key,
    )


def warmup_schema_cache(settings: NutriSqlSettings, db_path: Path) -> None:
    if not db_path.exists():
        return
    get_table_info_and_samples(settings, db_path)
