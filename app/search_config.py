# Fuzzy search (Streamlit UI — pg_trgm word_similarity)
FUZZY_TITLE_THRESHOLD: float = 0.3
FUZZY_DESCRIPTION_THRESHOLD: float = 0.25
FUZZY_SEARCH_LIMIT: int = 10

# Similarity-based dedup pre-filter (pg_trgm word_similarity)
DEDUP_SIMILARITY_THRESHOLD: float = 0.3   # skip dedup if no existing page clears this
DEDUP_TOP_K: int = 5                       # max candidates passed to judge_duplicate LLM call
