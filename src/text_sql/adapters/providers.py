from __future__ import annotations

from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path
from typing import Any

from langchain_community.utilities import SQLDatabase
from langchain_openai import ChatOpenAI

from ..config.settings import NutriSqlSettings
from ..grounding.schema import get_table_info_and_samples


class SchemaContextProvider(ABC):
    @abstractmethod
    def table_info_and_samples(self, db_path: Path) -> tuple[str, str]:
        """Return ``(table_info_ddl, sample_rows_text)`` for prompting."""


class SettingsBackedSchemaProvider(SchemaContextProvider):
    def __init__(self, settings: NutriSqlSettings) -> None:
        self._settings = settings

    def table_info_and_samples(self, db_path: Path) -> tuple[str, str]:
        return get_table_info_and_samples(self._settings, db_path)


class PromptBuilder(ABC):
    @abstractmethod
    def build(self, *, table_info: str, sample_rows_text: str, top_k: int) -> str:
        """Full system / instruction block for SQL generation."""


class FileTemplatePromptBuilder(PromptBuilder):
    def __init__(self, template_path: Path) -> None:
        self._template_path = template_path
        self._cached_text: str | None = None

    def _load_template(self) -> str:
        if self._cached_text is None:
            self._cached_text = self._template_path.read_text(encoding="utf-8")
        return self._cached_text

    def build(self, *, table_info: str, sample_rows_text: str, top_k: int) -> str:
        return (
            self._load_template()
            .replace("__TABLE_INFO__", table_info)
            .replace("__SAMPLE_ROWS__", sample_rows_text)
            .replace("__TOP_K__", str(top_k))
        )


class LLMClient(ABC):
    @abstractmethod
    def complete(self, user_text: str) -> str:
        """Return model text content (legacy single-string chat input)."""


@lru_cache(maxsize=8)
def cached_chat_openai(model: str, temperature: float) -> ChatOpenAI:
    return ChatOpenAI(model=model, temperature=temperature)


class LangChainOpenAIChatClient(LLMClient):
    def __init__(self, model: str, temperature: float) -> None:
        self._llm = cached_chat_openai(model=model, temperature=temperature)

    def complete(self, user_text: str) -> str:
        response = self._llm.invoke(user_text)
        return str(getattr(response, "content", response))


class SqlExecutor(ABC):
    @abstractmethod
    def execute(self, sql: str) -> dict[str, Any]:
        ...


class LangChainReadOnlyExecutor(SqlExecutor):
    def __init__(self, db: SQLDatabase) -> None:
        self._db = db

    def execute(self, sql: str) -> dict[str, Any]:
        from ..sql.execute import execute_sql

        return execute_sql(self._db, sql)
