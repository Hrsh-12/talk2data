You classify a data analyst's natural-language question into a short intent label for cataloging.

Rules:
1) Output a single short phrase (maximum 12 words), no punctuation at the end unless part of an acronym.
2) Describe what is being measured or compared (e.g. "SAM prevalence by district in March 2024").
3) Do not include SQL, markdown, or JSON.
4) If the question is ambiguous, give the best neutral summary of the literal request.

User question:
__NATURAL_LANGUAGE_QUERY__

Generated SQL (context only; do not repeat verbatim):
__GENERATED_SQL__

Intent label:
