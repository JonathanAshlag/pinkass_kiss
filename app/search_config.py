# Fuzzy search (Streamlit UI — pg_trgm word_similarity)
FUZZY_TITLE_THRESHOLD: float = 0.3
FUZZY_ALIASES_THRESHOLD: float = 0.3
FUZZY_SEARCH_LIMIT: int = 10

# Similarity-based dedup pre-filter (pg_trgm word_similarity)
DEDUP_SIMILARITY_THRESHOLD: float = 0.3   # skip dedup if no existing page clears this
DEDUP_TOP_K: int = 5                       # max candidates passed to judge_duplicate LLM call

# Agent consumption (passive scan, active search/fetch)
PASSIVE_MAX_PAGES_PER_DOC: int = 10
PASSIVE_TERM_CACHE_TTL_SECONDS: int = 300
ACTIVE_SEARCH_TOP_N: int = 5
ACTIVE_SEARCH_CANDIDATE_POOL: int = 10
ACTIVE_SEARCH_MISS_THRESHOLD: float = 0.3
