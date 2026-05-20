from __future__ import annotations

from pathlib import Path

from langchain_openai import ChatOpenAI


def classify_query_intent(
    *,
    natural_language_query: str,
    generated_sql: str,
    model: str,
    temperature: float,
    prompt_path: Path,
) -> str:
    template = prompt_path.read_text(encoding="utf-8")
    prompt = (
        template.replace("__NATURAL_LANGUAGE_QUERY__", natural_language_query.strip())
        .replace("__GENERATED_SQL__", generated_sql.strip())
    )
    llm = ChatOpenAI(model=model, temperature=temperature, max_tokens=128)
    content = str(getattr(llm.invoke(prompt), "content", "")).strip()
    return " ".join(content.split()) if content else ""
