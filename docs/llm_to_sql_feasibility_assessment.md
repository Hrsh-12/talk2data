# LLM-to-SQL Feasibility Assessment

**Dataset:** `3_months_UP_0m_6y_data.csv`
**Date:** March 2, 2026
**Scope:** Assess whether this dataset can support an LLM-to-SQL system for querying child health/nutrition program data

---

## 1. Executive Summary

The current dataset contains **18.3 million individual child-level records** from Uttar Pradesh (Feb–Apr 2024) with height, weight, and hemoglobin measurements. While the data volume is substantial, **the dataset in its current form cannot support most of the target LLM-to-SQL queries** due to a critical missing element: **there are no geographic hierarchy columns** (state, district, project/block, sector, Anganwadi centre). Without these, location-based drill-downs — which form the majority of the sample queries — are impossible. Additionally, the data is in a wide/pivoted format (one column per month per metric), which is poorly suited for SQL querying.

**Verdict: Significant data enrichment and schema restructuring are required before building an LLM-to-SQL system.**

---

## 2. Dataset Overview

| Property | Value |
|---|---|
| Rows | 18,332,035 |
| Columns | 23 |
| Geography | Uttar Pradesh only (single state) |
| Time Range | February, March, April 2024 (3 months) |
| Granularity | Individual beneficiary (child) |
| Age Group | 0–6 years |

### 2.1 Column Inventory

| Column | Type | Description | Completeness |
|---|---|---|---|
| `beneficiary_id` | Integer | Unique child identifier | 100% |
| `dob` | Date (string) | Date of birth | 100% |
| `gender` | Categorical | M / F | 100% |
| `birth_height` | Float | Height at birth (cm) | **19.8%** |
| `birth_weight` | Float | Weight at birth (kg) | **19.8%** |
| `{month}_status` | Categorical | active / inactive / migrated_out | 83–85% |
| `{month}_height` | Float | Measured height (cm) for that month | 83–85% |
| `{month}_weight` | Float | Measured weight (kg) for that month | 83–85% |
| `{month}_height_weight_entered_date` | Datetime | When measurement was entered | 79–83% |
| `{month}_hb` | Float | Hemoglobin reading (g/dL) | 83–85% present but **virtually all zeros** |
| `{month}_hb_test_date` | Date | When Hb test was performed | **~0%** (effectively empty) |

> `{month}` = `feb24`, `mar24`, `apr24`

### 2.2 Data Quality Issues

1. **Hemoglobin data is essentially empty.** The `hb_test_date` columns are >99.99% null, and `hb` values are overwhelmingly 0.00. Only 1–5 records across the entire dataset have actual Hb values. Anemia analysis is **not possible**.

2. **Birth measurements are 80% missing.** Only ~3.6M of 18.3M records have `birth_height` and `birth_weight`. Any birth-related analytics will be severely limited.

3. **"Measured" vs "zero" ambiguity.** Many active children have `height=0.0` and `weight=0.0`. It is unclear whether 0 means "not measured" or is an erroneous entry. This must be clarified — treating 0 as a valid measurement would distort all aggregations.

4. **Wide/pivoted format.** Monthly metrics are spread across separate columns per month rather than being in a normalized row-per-month structure. This makes SQL queries for trends, comparisons, and aggregations awkward and non-scalable.

---

## 3. Sample Query Feasibility Analysis

Below we evaluate each sample query against the available data.

### Query 1: "What percentage of children were measured nationally last month?"

| Aspect | Assessment |
|---|---|
| **Can answer?** | **Partially — within UP only, not nationally** |
| **What's available** | Can compute: (count of children with non-zero height/weight) / (count of active children) per month |
| **What's missing** | No national data; only Uttar Pradesh. No concept of "last month" — only 3 hardcoded months exist. The word "nationally" implies multi-state data. |
| **Concerns** | Zero-value ambiguity — need to confirm that height/weight = 0 means "not measured." |

### Query 2: "Which states are below the national average in growth monitoring?"

| Aspect | Assessment |
|---|---|
| **Can answer?** | **No** |
| **What's available** | Nothing relevant — dataset contains a single state (UP). |
| **What's missing** | Multi-state data, state identifier column. |
| **Concerns** | Fundamental data gap. Cannot be addressed without additional datasets. |

### Query 3: "Which districts in this state are performing poorly?"

| Aspect | Assessment |
|---|---|
| **Can answer?** | **No** |
| **What's available** | None — no geographic columns exist in the data. |
| **What's missing** | `district` column (or a mapping from `beneficiary_id` to district). |
| **Concerns** | This is the most critical gap. District-level analysis is a core use case for program monitoring. |

### Query 4: "Which projects are dragging down this district's performance?"

| Aspect | Assessment |
|---|---|
| **Can answer?** | **No** |
| **What's available** | None — no project/block column exists. |
| **What's missing** | `project` (or `block`) column, `district` column. |
| **Concerns** | Requires two levels of geographic hierarchy that are absent. |

### Query 5: "Which sectors need immediate attention this month?"

| Aspect | Assessment |
|---|---|
| **Can answer?** | **No** |
| **What's available** | None — no sector column exists. |
| **What's missing** | `sector` column, plus a definition of "attention" (measurement rate? malnutrition rate?). |
| **Concerns** | Even with a sector column, "attention" must be precisely defined for SQL translation. |

### Query 6: "Where is the biggest gap between registered and measured children?"

| Aspect | Assessment |
|---|---|
| **Can answer?** | **Partially — aggregate only, no location breakdown** |
| **What's available** | Can compute overall: children with `status=active` vs children with non-zero height/weight. Can show per-month trend. |
| **What's missing** | Geographic columns to show *where* the gap exists. Without location, the answer is a single statewide number — not actionable. |
| **Concerns** | The value of this query lies in geographic specificity. |

### Query 7: "Which Anganwadi centres are underperforming in this sector?"

| Aspect | Assessment |
|---|---|
| **Can answer?** | **No** |
| **What's available** | None — no AWC (Anganwadi centre) or sector identifier exists. |
| **What's missing** | `awc_id` / `awc_name`, `sector` columns. |
| **Concerns** | This is the leaf-level of the geographic hierarchy (State > District > Project > Sector > AWC) — all levels are missing. |

### Feasibility Summary Table

| # | Query | Answerable? | Blocker |
|---|---|---|---|
| 1 | % children measured nationally | Partial (UP only) | No national/multi-state data |
| 2 | States below national average | **No** | Single-state dataset |
| 3 | Poorly performing districts | **No** | No district column |
| 4 | Projects dragging down district | **No** | No project/district columns |
| 5 | Sectors needing attention | **No** | No sector column |
| 6 | Gap: registered vs measured | Partial (no location) | No geographic columns |
| 7 | Underperforming AWCs | **No** | No AWC/sector columns |

---

## 4. What CAN Be Answered Today

Despite the gaps, the data does support a limited set of queries at the **statewide aggregate level**:

### 4.1 Registration & Coverage Queries
- "How many children are registered in UP?" → Count of unique `beneficiary_id`
- "How many children were active in [month]?" → Count where `{month}_status = 'active'`
- "What is the measurement coverage rate for [month]?" → Ratio of non-zero height/weight to active count
- "How has measurement coverage changed from Feb to Apr 2024?" → Compare rates across 3 months
- "How many children were inactive or migrated out in [month]?" → Filter by status

### 4.2 Demographic Queries
- "What is the gender distribution of registered children?" → Group by `gender`
- "What is the age distribution of children?" → Compute age from `dob`
- "How many children are under 1 year? Under 3? Under 6?" → Age bucketing from `dob`

### 4.3 Measurement & Growth Queries
- "What is the average height/weight for [month]?" → Mean of non-zero values
- "What is the height/weight distribution by age group?" → Cross-tabulate age buckets × measurements
- "How many children had their measurements taken on [specific date]?" → Filter by `entered_date`
- "What is the data entry pattern — on which dates are most measurements entered?" → Value counts of `entered_date`
- "How many children showed weight loss between Feb and Mar?" → Compare `feb24_weight` vs `mar24_weight`

### 4.4 Data Quality Queries
- "How many children have missing birth weight?" → Count nulls
- "How many active children have zero height/weight values?" → Filter status=active, height=0
- "What percentage of children have Hb test data?" → Essentially 0%

---

## 5. Critical Gaps & Risks

### 5.1 Missing Geographic Hierarchy (BLOCKER)

The ICDS (Integrated Child Development Services) program operates on a 5-level hierarchy:

```
State → District → Project (Block) → Sector → Anganwadi Centre (AWC)
```

**None of these levels are present in the dataset.** This is the single biggest blocker. Without geographic columns, the system cannot answer the most common and valuable operational queries — identifying underperforming locations and enabling targeted interventions.

**Resolution required:** Either:
- Add geographic columns to each beneficiary record (preferred), OR
- Provide a separate mapping table: `beneficiary_id → awc_id → sector → project → district → state`

### 5.2 Wide Format is SQL-Hostile

The current schema encodes time in column names (`feb24_height`, `mar24_height`, `apr24_height`). This has serious consequences for LLM-to-SQL:

- **Query generation complexity:** An LLM must generate different SQL depending on which month is queried, rather than using a simple `WHERE month = ...` filter.
- **Scalability:** Adding more months means adding more columns, requiring schema changes and model re-training.
- **Aggregation difficulty:** Queries like "trend over last 6 months" become unwieldy UNION or CASE statements.

**Recommended normalized schema:**

```sql
-- Beneficiary dimension
CREATE TABLE beneficiaries (
    beneficiary_id  BIGINT PRIMARY KEY,
    dob             DATE,
    gender          CHAR(1),
    birth_height    DECIMAL(5,2),
    birth_weight    DECIMAL(4,2),
    awc_id          BIGINT        -- FK to location hierarchy
);

-- Location hierarchy dimension
CREATE TABLE locations (
    awc_id      BIGINT PRIMARY KEY,
    awc_name    VARCHAR(200),
    sector      VARCHAR(200),
    project     VARCHAR(200),
    district    VARCHAR(200),
    state       VARCHAR(100)
);

-- Monthly measurements fact table
CREATE TABLE monthly_measurements (
    beneficiary_id  BIGINT,
    month           DATE,          -- e.g., '2024-02-01'
    status          VARCHAR(20),   -- active / inactive / migrated_out
    height          DECIMAL(5,2),
    weight          DECIMAL(5,2),
    measurement_date DATE,         -- when height/weight was entered
    hb_value        DECIMAL(4,2),
    hb_test_date    DATE,
    PRIMARY KEY (beneficiary_id, month)
);
```

With this schema, the sample queries become straightforward SQL:

```sql
-- "What % of children were measured last month?"
SELECT
    COUNT(CASE WHEN m.height > 0 AND m.weight > 0 THEN 1 END) * 100.0
    / COUNT(*) AS pct_measured
FROM monthly_measurements m
WHERE m.month = '2024-04-01'
  AND m.status = 'active';

-- "Which districts are performing poorly?"
SELECT
    l.district,
    COUNT(CASE WHEN m.height > 0 AND m.weight > 0 THEN 1 END) * 100.0
    / COUNT(*) AS pct_measured
FROM monthly_measurements m
JOIN beneficiaries b ON b.beneficiary_id = m.beneficiary_id
JOIN locations l ON l.awc_id = b.awc_id
WHERE m.month = '2024-04-01'
  AND m.status = 'active'
  AND l.state = 'Uttar Pradesh'
GROUP BY l.district
ORDER BY pct_measured ASC;
```

### 5.3 Limited Temporal Coverage

Only 3 months of data (Feb–Apr 2024) is available. This limits:
- Trend analysis (seasonal patterns need 12+ months)
- Year-over-year comparisons
- Meaningful "last month" queries (only 3 options)

### 5.4 No Nutrition Status Classification

The raw height/weight data alone does not tell us whether a child is **stunted**, **wasted**, or **underweight**. These classifications require:
- Computing WHO Z-scores (Height-for-Age, Weight-for-Age, Weight-for-Height)
- Reference tables based on age and gender

Without pre-computed nutrition status, queries like "How many children are malnourished?" cannot be directly answered. Z-score computation is complex and should not be delegated to SQL — it should be pre-computed and stored as additional columns.

### 5.5 Hemoglobin / Anemia Data is Unusable

The Hb columns are >99.99% empty. Any anemia-related queries will return no meaningful results. Either:
- This data is collected through a different system, OR
- Hb testing has not been rolled out at scale

---

## 6. Recommendations

### 6.1 Must-Have Before Building LLM-to-SQL

| # | Action | Priority | Effort |
|---|---|---|---|
| 1 | **Add geographic hierarchy** (district, project, sector, AWC) to the dataset or as a join table | **P0 — Blocker** | Medium |
| 2 | **Normalize the schema** from wide to long format (one row per beneficiary per month) | **P0 — Blocker** | Low |
| 3 | **Clarify zero-value semantics** — define whether height/weight = 0 means "not measured" or is a data error | **P0 — Blocker** | Low |
| 4 | **Expand to multi-state data** if "national" queries are required | **P1 — Important** | High |
| 5 | **Expand temporal range** to at least 12 months for trend analysis | **P1 — Important** | Medium |

### 6.2 Should-Have for Rich Query Support

| # | Action | Priority | Effort |
|---|---|---|---|
| 6 | **Pre-compute nutrition status** (stunted/wasted/underweight using WHO Z-scores) and store as columns | **P1** | Medium |
| 7 | **Add beneficiary metadata** — mother's name, registration date, age in months (pre-computed) | **P2** | Low |
| 8 | **Populate Hb data** or explicitly exclude anemia queries from scope | **P2** | Depends on source |
| 9 | **Define a metrics dictionary** — formal definitions of "measured," "performing poorly," "needing attention" for consistent SQL generation | **P1** | Low |

### 6.3 LLM-to-SQL System Design Considerations

1. **Schema documentation is critical.** The LLM needs a well-documented schema with column descriptions, valid values, and business rules. Provide this as system prompt context.

2. **Metrics must be pre-defined.** Ambiguous terms like "performing poorly" or "needing attention" must be translated into specific SQL predicates (e.g., measurement rate < 50%). Maintain a metrics glossary.

3. **Guard against zero-confusion.** The SQL generation layer must consistently filter out height/weight = 0 when computing measurement coverage, averages, or distributions.

4. **Row-level security.** With 18M+ rows and a geographic hierarchy, ensure the system can scope queries appropriately (e.g., a district officer only sees their district).

5. **Query result size.** Some queries could return millions of rows. The system should default to aggregated results and paginate or limit raw record queries.

---

## 7. Conclusion

The underlying use case — monitoring child nutrition program performance through natural language queries — is strong and well-suited for LLM-to-SQL. However, **the current dataset lacks the geographic hierarchy that is foundational to 5 out of 7 sample queries**. The data also needs normalization from its wide format into a SQL-friendly star/snowflake schema.

Once the geographic hierarchy is added and the schema is restructured, the dataset can support a rich set of queries spanning coverage monitoring, performance comparison, trend analysis, and nutrition status tracking at every level from state down to individual Anganwadi centres.

### Immediate Next Steps

1. Obtain the beneficiary-to-location mapping (beneficiary_id → AWC → sector → project → district → state)
2. Restructure the data into the recommended normalized schema
3. Clarify zero-value semantics with the data source team
4. Load into a SQL database (e.g., PostgreSQL, DuckDB) and validate with the sample queries
5. Prototype the LLM-to-SQL layer with the restructured schema

---

*Document generated from exploratory analysis of `3_months_UP_0m_6y_data.csv` (18.3M records, Uttar Pradesh, Feb–Apr 2024).*
