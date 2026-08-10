---
paths:
  - app/llm/**/*.py
  - app/IP/prompts/**/*.py
  - app/routers/produce.py
---

# LLM integration: orchestration and prompts

The LLM layer separates orchestration logic from prompt templates.

**Structure:**

- `app/llm/` — orchestration: client setup, pipeline phases, retrieval logic, ingestion coordinator
- `app/IP/prompts/` — templates only: prompt strings for ingestion and retrieval workflows
- `app/routers/produce.py` — HTTP endpoint for document ingestion that drives the pipeline

**Error handling pattern:**

`app/llm/client.py:_call_llm_json()` wraps LLM calls in broad try/except and returns a `default` value on any error (no exception thrown). This is **intentional** for resilience — LLM calls are inherently unreliable. When calling the LLM, provide a sensible default (empty list, None, fallback structure) and let the caller decide what to do with it.

```python
# This is the pattern — catch all, return default
result = await _call_llm_json(
    messages=[...],
    default=[],  # Return empty list if LLM fails
    name="extract_candidates"
)
```

**Adding new LLM workflows:**

1. Add orchestration logic to `app/llm/` (e.g., new phase function, new endpoint in `produce.py`)
2. Add corresponding prompt template(s) to `app/IP/prompts/` (import and call from orchestration)
3. Both sides must be kept in sync — a new orchestration function needs a corresponding prompt, and vice versa.
