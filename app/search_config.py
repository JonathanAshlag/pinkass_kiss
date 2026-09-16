# Fuzzy search (Streamlit UI — pg_trgm word_similarity)
FUZZY_TITLE_THRESHOLD: float = 0.3
FUZZY_ALIASES_THRESHOLD: float = 0.3

# Agent consumption (passive scan, active search/fetch)
PASSIVE_MAX_PAGES_PER_DOC: int = 10
PASSIVE_TERM_CACHE_TTL_SECONDS: int = 300
ACTIVE_SEARCH_TOP_N: int = 5
ACTIVE_SEARCH_CANDIDATE_POOL: int = 10
ACTIVE_SEARCH_MISS_THRESHOLD: float = 0.3
