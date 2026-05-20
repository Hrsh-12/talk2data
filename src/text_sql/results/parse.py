from __future__ import annotations

import ast
from typing import Any


def parse_verified_comparison_value(raw_output: Any) -> Any:
    """Parse LangChain/SQLDatabase string output for golden-set comparison (matches legacy literal_eval path)."""
    if raw_output is None:
        return None
    text = str(raw_output).strip()
    if not text:
        return []
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return text


def parse_exec_output(raw_output: object) -> tuple[str, object]:
    """
    Normalize execution payloads for UI / rephrase (dict/list/tuple passthrough;
    otherwise try literal_eval like the Gradio result panel).
    """
    if isinstance(raw_output, (list, dict, tuple)):
        return str(raw_output).strip(), raw_output

    text = str(raw_output).strip()
    if not text or text == "None":
        return "", ""
    try:
        return text, ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return text, text
