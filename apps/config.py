from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# ── Project root & path setup ───────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv()

# ── Paths ───────────────────────────────────────────────────────────────
_db_env = os.getenv("DB_PATH", "database/nutrition_data_filtered.duckdb")
_db_p = Path(_db_env)
DEFAULT_DB_PATH = _db_p if _db_p.is_absolute() else (ROOT / _db_p)
VERIFIED_SQL_PATH = ROOT / "data" / "queries " / "queries_verified.sql"
DEFAULT_OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "outputs"))

# ── SQL / model settings ────────────────────────────────────────────────
DEFAULT_MODEL = os.getenv("MODEL_NAME", "gpt-5-mini")
DEFAULT_TOP_K = int(os.getenv("TOP_K", "5"))
DEFAULT_TEMPERATURE = float(os.getenv("TEMPERATURE", "0.0"))
DEFAULT_TABLE_ROW_LIMIT = 10

REPHRASE_MODEL = os.getenv("REPHRASE_MODEL_NAME", "gpt-4o-mini")
REPHRASE_TEMPERATURE = float(os.getenv("REPHRASE_TEMPERATURE", "0.0"))
REPHRASE_MAX_TOKENS = int(os.getenv("REPHRASE_MAX_TOKENS", "96"))
REPHRASE_TIMEOUT_SECONDS = float(os.getenv("REPHRASE_TIMEOUT_SECONDS", "20"))
REPHRASE_MAX_RETRIES = int(os.getenv("REPHRASE_MAX_RETRIES", "1"))

# ── Runtime flags ───────────────────────────────────────────────────────
SAVE_TRACE = os.getenv("SAVE_TRACE", "true").lower() == "true"
SAVE_HISTORY = os.getenv("GRADIO_SAVE_HISTORY", "true").lower() == "true"
QUEUE_CONCURRENCY = int(os.getenv("GRADIO_QUEUE_CONCURRENCY", "2"))
QUEUE_MAX_SIZE = int(os.getenv("GRADIO_QUEUE_MAX_SIZE", "32"))
WARMUP_ON_START = os.getenv("WARMUP_ON_START", "true").lower() == "true"

# ── UI content ──────────────────────────────────────────────────────────
SAMPLE_QUERIES = [
    "What is the overall SAM prevalence in March 2024?",
    "Which 5 districts have the highest stunting rates in April 2024?",
    "What is the total number of underweight children in February 2024?",
    "Compare wasting rates between male and female children in March 2024",
    "How has the SAM count changed from February to April 2024?",
    "What percentage of children are stunted in Lucknow district?",
]

SAMPLE_QUERY_LABELS = [
    "SAM Prevalence (Mar 2024)",
    "Top Stunted Districts (Apr 2024)",
    "Underweight Count (Feb 2024)",
    "Wasting by Gender (Mar 2024)",
    "SAM Trend (Feb → Apr)",
    "Stunting in Lucknow",
]

TIPS_MD = """
### About this system
Ask natural-language questions about child nutrition data from **Uttar Pradesh, India**.
The system translates your question into SQL and queries the database automatically.

---

### Data at a glance
| | |
|---|---|
| **Children** | ~3.6 million |
| **Districts** | 75 UP districts |
| **Months** | February, March, April 2024 |
| **Age group** | 0 – 6 years |
| **Programme** | ICDS (Anganwadi centres) |

---

### Indicators — quick glossary
| Term | What it means |
|---|---|
| **Stunting** | Low height-for-age (chronic undernutrition) |
| **Wasting** | Low weight-for-height (acute undernutrition) |
| **Underweight** | Low weight-for-age (combined indicator) |
| **SAM** | Severe Acute Malnutrition |
| **MAM** | Moderate Acute Malnutrition |

---

### Query patterns that work well

**Prevalence / rates**
> "What is the SAM prevalence in March 2024?"
> "What percentage of children are stunted in April 2024?"

**District comparisons**
> "Which 10 districts have the highest wasting rates in February 2024?"
> "Compare stunting rates across all districts in March 2024."

**Gender breakdown**
> "Compare SAM rates between male and female children."
> "What is the underweight rate for girls in April 2024?"

**Trends over time**
> "How has the SAM count changed from February to April 2024?"
> "Show the monthly stunting rate for Agra district across all three months."

**Counts vs. percentages**
> "How many children are severely malnourished in Lucknow?"
> "What fraction of children in Varanasi are underweight?"

**Filters & combinations**
> "What is the SAM rate in rural AWCs of Gorakhpur district in March 2024?"

---

### Tips for best results
- **Specify the month** — the data covers Feb, Mar and Apr 2024. Queries that don't mention a month may aggregate all three or default to one.
- **Use district names exactly** — e.g. "Lucknow", "Varanasi", "Agra", "Gorakhpur". Partial names may not match.
- **Ask one question at a time** — compound questions ("...and also compare with...") can confuse the SQL generator. Break them up.
- **If a result looks wrong, rephrase** — try being more specific, e.g. add "as a percentage" or "count of children".
- **Scalar answers appear as large numbers** in the chat; tabular answers show the first 5 rows inline with the full table in the Results panel.
"""

APP_CSS = """
/* ── Remove default max-width constraint ───────────────────────────── */
.gradio-container {
    max-width: 100% !important;
    padding: 0 10px 8px 10px !important;
}

/* ── Slim app header ────────────────────────────────────────────────── */
#app-header {
    padding: 10px 2px 8px 2px !important;
    border-bottom: 1px solid #e2e8f0;
    margin-bottom: 8px !important;
    align-items: center !important;
    gap: 8px !important;
}
#app-title p {
    margin: 0 !important;
    font-size: 13.5px !important;
    color: #64748b !important;
    font-weight: 400 !important;
}
#app-title strong { color: #1e293b !important; font-weight: 600 !important; }
#panel-toggle-btn {
    height: 30px !important;
    min-width: 110px !important;
    font-size: 12.5px !important;
    border-radius: 6px !important;
}

/* ── Side panel ─────────────────────────────────────────────────────── */
#side-col {
    border-left: 1px solid #e2e8f0 !important;
    padding-left: 10px !important;
    overflow-y: auto;
}

/* ── Chat markdown tables — blue palette ───────────────────────────── */
.message-wrap table {
    border-collapse: collapse;
    width: 100%;
    font-size: 12.5px;
    margin-top: 10px;
    border-radius: 4px;
    overflow: hidden;
}
.message-wrap table th {
    background: #eff6ff;
    border: 1px solid #bfdbfe;
    padding: 6px 10px;
    text-align: left;
    font-weight: 600;
    color: #1e40af;
}
.message-wrap table td {
    border: 1px solid #dbeafe;
    padding: 5px 10px;
    vertical-align: top;
    color: #374151;
}
.message-wrap table tr:nth-child(even) td { background: #f8faff; }
"""
