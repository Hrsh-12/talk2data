#!/usr/bin/env python3
"""One-off: convert legacy queries_verified.sql to BIRD-style JSONL (run from repo root)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.queries.catalog import split_sql_statements  # noqa: E402


def migrate(sql_path: Path, out_path: Path, db_id: str) -> int:
    text = sql_path.read_text(encoding="utf-8")
    header_re = re.compile(r"(?m)^--\s*Q(\d+)(?:\s+follow-up)?\s*:\s*(.*)$")
    matches = list(header_re.finditer(text))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out_path.open("w", encoding="utf-8") as fh:
        for idx, match in enumerate(matches):
            qnum = int(match.group(1))
            qtitle = match.group(2).strip()
            start = match.start()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            block = text[start:end]
            stmts = split_sql_statements(block)
            if not stmts:
                continue
            sql_joined = "\n".join(stmts)
            rec = {
                "question_id": qnum,
                "db_id": db_id,
                "question": qtitle or f"Q{qnum}",
                "evidence": "NA",
                "SQL": sql_joined,
                "difficulty": "NA",
            }
            fh.write(json.dumps(rec, ensure_ascii=True) + "\n")
            n += 1
    print(f"Wrote {n} records to {out_path}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--sql",
        type=Path,
        default=ROOT / "data" / "queries" / "queries_verified.sql",
        help="Legacy verified SQL path",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data" / "nutrition_queries.jsonl",
        help="Output JSONL path",
    )
    p.add_argument("--db-id", default="up_child_nutrition")
    args = p.parse_args()
    return migrate(args.sql, args.out, args.db_id)


if __name__ == "__main__":
    raise SystemExit(main())
