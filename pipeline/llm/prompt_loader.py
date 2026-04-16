"""Load NL→SQL prompt templates from YAML (or plain text for legacy paths)."""

from __future__ import annotations

from pathlib import Path


def load_prompt_template(path: Path) -> str:
    """
    Load a generation or repair prompt template.

    YAML files must be a mapping with a single string field ``template`` (folded or literal block).
    Placeholders use Python ``str.format`` syntax, e.g. ``{table_info}``, ``{{literal_braces}}``.
    """
    suffix = path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        import yaml

        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            raise ValueError(f"Prompt YAML must be a mapping: {path}")
        tpl = doc.get("template")
        if not isinstance(tpl, str) or not tpl.strip():
            raise ValueError(f"Prompt YAML must define non-empty string 'template': {path}")
        return tpl
    return path.read_text(encoding="utf-8")
