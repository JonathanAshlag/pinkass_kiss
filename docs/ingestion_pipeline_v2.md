# Ingestion Pipeline V2: Single-Pass Topic + Content Extraction

## Motivation

The current pipeline makes N+1 LLM calls per document:
1. One call to extract topics (`extract_topic_candidates`)
2. For each new topic, one call to generate page content (`generate_page_content`)

Each `generate_page_content` call re-sends the full source document text as context. For a document with 5 topics, we send the document 6 times total (1 extraction + 5 generation calls).

**Proposed change:** Combine topic extraction and content generation into a single LLM call that returns topics with their content in one pass.

## Current Pipeline (V1)

```
Document
  │
  ├─ [LLM Call 1] extract_topic_candidates(document)
  │    → [{title, description}, ...]
  │
  ├─ For each candidate:
  │    ├─ [Search] find similar pages
  │    ├─ [LLM Call] judge_duplicate(candidate, search_results)
  │    │
  │    ├─ If duplicate:
  │    │    └─ [LLM Call] merge_content(existing_page, document)
  │    │
  │    └─ If new:
  │         └─ [LLM Call] generate_page_content(title, description, document)  ← sends full doc again
  │
  └─ Return results
```

**Total LLM calls per document:** 1 + N (dedup) + K (generate for new) + M (merge for duplicates) = 1 + N + K + M

For a 5-topic document with 1 duplicate: 1 + 5 + 4 + 1 = 11 calls.

## Proposed Pipeline (V2)

```
Document
  │
  ├─ [LLM Call 1] extract_topics_with_content(document)
  │    → [{title, description, content}, ...]
  │
  ├─ For each candidate:
  │    ├─ [Search] find similar pages
  │    ├─ [LLM Call] judge_duplicate(candidate, search_results)
  │    │
  │    ├─ If duplicate:
  │    │    └─ [LLM Call] merge_content(existing_page, candidate.content)  ← uses generated content, not raw doc
  │    │
  │    └─ If new:
  │         └─ Use candidate.content directly (no extra call)
  │
  └─ Return results
```

**Total LLM calls per document:** 1 + N (dedup) + M (merge for duplicates) = 1 + N + M

For a 5-topic document with 1 duplicate: 1 + 5 + 1 = 7 calls (36% reduction).

## Key Differences

| Aspect | V1 | V2 |
|--------|----|----|
| LLM calls for a 5-topic doc | 11 | 7 |
| Document sent to LLM | N+1 times | 1 time |
| Content for new topics | Generated per-topic with dedicated call | Extracted in bulk in the first call |
| Merge input | Raw document text | Pre-generated content for the topic |
| Output token count (first call) | Small (titles + descriptions only) | Large (titles + descriptions + full content) |
| Failure blast radius | One topic's generation fails → only that topic affected | First call fails → entire document fails |

## Benefits

1. **Fewer LLM calls** — reduces latency, cost, and rate-limit pressure.
2. **No redundant context** — the document text is sent once instead of N+1 times.
3. **Better content coherence** — the model sees all topics simultaneously when generating content, so it can avoid repeating shared context across pages and draw clearer boundaries.
4. **Simpler merge** — instead of asking the merge step to find new info in a large raw document, it receives a focused content block that's already scoped to the topic.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Output token limit exceeded | Model truncates or refuses | Cap at 5 topics per call. If extraction alone would produce >5 topics, fall back to a two-step approach: extract titles first, then batch generate in groups of 5. |
| Wasted generation for duplicates | Content generated for topics that turn out to be duplicates is discarded | The generated content is small relative to the input. The merge step can also use `candidate.content` to detect new info more precisely — so it's not fully wasted. |
| Single point of failure | If the one big call fails, nothing is produced | Add retry with exponential backoff. On repeated failure, fall back to V1 (extract topics only, then generate individually). |
| Lower content quality | Generating multiple pages in one call may reduce per-page quality due to attention dilution | Evaluate empirically. If quality drops, the fallback is to use the single-pass output only for topics with `boundary_clarity=clear` and regenerate ambiguous ones individually. |

## Implementation Plan

### Phase 1: New Prompt

Create a new prompt in `app/prompts/ingestion.py`:

- **`EXTRACT_TOPICS_WITH_CONTENT_SYSTEM`** — system prompt instructing the model to extract topics and write full wiki page content for each.
- **`build_extract_topics_with_content_user(filename)`** — user prompt requesting a JSON array of `[{title, description, content}]`.

The prompt should instruct the model to:
- Identify distinct wiki-worthy topics
- Write complete markdown content for each topic (same quality expectations as current `generate_page_content`)
- Keep topics independent — no cross-references between generated pages
- Cap at 5 topics — if the document has more, include `{title, description, content: null}` for additional topics beyond 5

### Phase 2: New Ingestion Function

Add to `app/llm/ingestion.py`:

```python
async def extract_topics_with_content(text: str, filename: str, content_parts: list[dict] = None) -> list[dict]:
    """Single-pass extraction: topics + content together.

    Returns list of {title, description, content (may be None for overflow topics)}
    """
```

This mirrors `extract_topic_candidates` but returns content as well.

### Phase 3: Update Pipeline

Modify `app/llm/pipeline.py` → `run_ingestion_pipeline`:

1. Call `extract_topics_with_content` instead of `extract_topic_candidates`.
2. For each candidate:
   - Run `judge_duplicate` (unchanged).
   - If duplicate and `candidate.content` is not None: pass `candidate.content` to a simplified merge check (does the generated content add info to the existing page?).
   - If new and `candidate.content` is not None: use it directly in `PageCreate`.
   - If `candidate.content` is None (overflow): fall back to individual `generate_page_content` call.

### Phase 4: Update Merge Step

Modify `merge_content` to accept a `new_content` parameter (the pre-generated content) instead of the full raw document text. This makes the merge comparison more focused: "does this content block add info to the existing page?" rather than "does this entire document add info?"

Add a new variant or parameter:

```python
async def merge_content_from_generated(
    existing_title: str,
    existing_content: str,
    new_content: str,
) -> dict:
```

### Phase 5: Fallback Logic

Add configuration to switch between V1 and V2:

- `app/config.py`: add `ingestion_pipeline_version: int = 2` (default to V2 once validated).
- `run_ingestion_pipeline` checks this setting and dispatches accordingly.
- If V2's single-pass call fails after retries, automatically fall back to V1 for that document and log a warning.

### Phase 6: Evaluation

Run the evaluation process described in `docs/ingestion_evaluation.md` comparing V1 vs V2 on:
- Topic detection precision/recall (are the same topics extracted?)
- Duplicate detection precision/recall (does dedup still work with the new flow?)
- Latency and cost per document (should improve)

## File Changes Summary

| File | Change |
|------|--------|
| `app/prompts/ingestion.py` | Add `EXTRACT_TOPICS_WITH_CONTENT_SYSTEM` and `build_extract_topics_with_content_user` |
| `app/llm/ingestion.py` | Add `extract_topics_with_content()`, add `merge_content_from_generated()` |
| `app/llm/pipeline.py` | Update `run_ingestion_pipeline` to use single-pass extraction with fallback |
| `app/config.py` | Add `ingestion_pipeline_version: int = 2` |
