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

`app/llm/client.py:_call_llm_json()` logs and re-raises on any error (bad response, timeout,
unparseable JSON). This is **intentional**: the ingestion pipeline must not fabricate
placeholder content (e.g. a candidate page named after the source file, or raw document text
as page content) when the LLM is unreachable or misbehaving. The exception propagates up
through `run_ingestion_pipeline` to `_run_batch` (`app/routers/produce.py`), which marks the
whole batch `error` — a broken LLM call means zero candidates are proposed, not a wrong one.

```python
# This is the pattern — let failures propagate, don't default
result = await _call_llm_json(
    messages=[...],
    name="extract_candidates"
)
```

**Adding new LLM workflows:**

1. Add orchestration logic to `app/llm/` (e.g., new phase function, new endpoint in `produce.py`)
2. Add corresponding prompt template(s) to `app/IP/prompts/` (import and call from orchestration)
3. Both sides must be kept in sync — a new orchestration function needs a corresponding prompt, and vice versa.
