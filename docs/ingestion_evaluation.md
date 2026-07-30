# Ingestion Quality Evaluation Guide

## Goal

Evaluate the quality of the ingestion pipeline's **topic detection** and **duplicate detection** components. These are the measurable, high-impact steps where errors cascade downstream.

## Why Not Evaluate Content Generation?

Measuring the quality of generated page content requires either:
- Humans manually writing reference pages for each topic (expensive)
- An LLM judge scoring generated content against source material (requires calibration with human annotators to trust the scores)

Both approaches require significant annotation resources we don't currently have. Topic detection and duplicate detection are sufficient to evaluate pipeline quality because:
1. If topics are extracted correctly, the generated content is constrained to the right scope. 
2. If dedup is accurate, content either goes to the right page or creates a legitimate new one.
3. Also, people can update the pages when issue is found so over time the content quality should get better.

We focus evaluation effort where it's most actionable.

## Ground Truth Construction

### Step 1: Collect Documents

Select **~30 documents** from a single domain (e.g., one course).

Requirements:
- Mix of single-topic and multi-topic documents in addition to docs with no relevant terms
- Some documents should cover overlapping subject matter (to produce natural duplicates)
- Real organizational documents, not synthetic

### Step 2: Human Topic Annotation

Have domain experts read each of the 30 documents and write wiki pages based on them — as if they were manually populating the wiki (using personal knowledge out of the document scope is not allowed). Duplicates across documents are allowed and expected (two documents about the same topic should produce pages that cover the same/overlapping ground).

**Output:** A set of human-authored wiki pages, each tagged with:
- `title`
- `description`
- `source_document` (which of the 30 documents it came from)
- `page_id` (automatically generated afterwards, to be used for evaluation)

This produces the ground truth topic set: the list of topics (pages) that should be extracted from each document.

### Step 3: Human Duplicate Annotation

Sample 200-300 human-authored pages, run search against all other pages using multiple retrieval approaches:

- **Lexical search (BM25)** — keyword overlap
- **Semantic search (word embeddings)** — e.g., sentence-transformers, `all-MiniLM-L6-v2`
- **Hybrid** — weighted combination of lexical + semantic scores

For each page, take the **top 10 results** from the combined retrieval and present them to a human annotator. The annotator labels each result as:

- **Duplicate/Near-duplicate** — covers the same topic, both pages should be unified to a single page
- **Not** - both pages should stay apart

**Output:** A labeled pairs dataset: `[(page_a, page_b, label)]` where label ∈ {duplicate, not}.

This produces the ground truth for duplicate detection.

## Evaluation Procedure

When evaluating a new ingestion pipeline (new model, new prompt, new approach), run the following:

### Topic Detection Evaluation

1. Run the pipeline's topic extraction on each of the 30 source documents.
2. For each document, collect the predicted topics: `[{title, description}]`.
3. Use an **LLM-as-a-Judge** to perform topic mapping between predicted topics and ground truth topics for that document.

**Important:** The mapping is many-to-many, not 1-to-1. A single predicted topic may cover multiple ground truth pages (the prediction is broader), and a single ground truth page may be covered by multiple predicted topics (the prediction split it more finely). Both are acceptable outcomes — what matters is coverage, not granularity matching.

**LLM judge prompt** (per document):

> Given the following predicted topics and ground truth topics for a document, determine which predicted topics cover which ground truth topics. A predicted topic "covers" a ground truth topic if the ground truth topic's subject matter falls within the scope of the predicted topic. A ground truth topic "is covered by" a predicted topic if a reader of the predicted page would find the ground truth topic's information there.
>
> Note: mappings are many-to-many. A single predicted topic may cover multiple ground truth topics (if the prediction is broader). Multiple predicted topics may together cover a single ground truth topic (if the prediction is more granular).
>
> Return a JSON list of mappings: `[{"predicted_idx": int, "ground_truth_idx": int}]`
> Include all valid coverage relationships. Only include confident matches — if unsure, omit.

4. From the matching output, compute:

| Metric | Formula | Meaning |
|--------|---------|---------|
| **Precision** | predicted_topics_with_at_least_one_match / total_predicted | What fraction of predicted topics correspond to real topics? |
| **Recall** | ground_truth_topics_with_at_least_one_match / total_ground_truth | What fraction of real topics were covered by at least one prediction? |
| **F1** | 2 × (P × R) / (P + R) | Balanced measure |

Compute per-document, then average across all 30 documents (macro-average).

**Interpretation:**
- High precision, low recall → the pipeline misses topics but what it finds is real
- Low precision, high recall → the pipeline invents spurious topics
- A pipeline that splits one topic into three fine-grained pages is not penalized (all three match the ground truth topic, recall is maintained, precision is maintained as long as each sub-topic is legitimate)

### Duplicate Detection Evaluation

1. For each page produced by the pipeline, run the same search (BM25, semantic, hybrid) against the existing pages in the wiki.
2. Run the pipeline's `judge_duplicate` on the top candidates.
3. Compare the pipeline's duplicate/not-duplicate decisions against the human-annotated ground truth pairs.

| Metric | Formula | Meaning |
|--------|---------|---------|
| **Precision** | true_duplicates_found / all_predicted_duplicates | When it says "duplicate," how often is it right? |
| **Recall** | true_duplicates_found / all_actual_duplicates | Of all true duplicates, how many did it catch? |
| **F1** | 2 × (P × R) / (P + R) | Balanced measure |

**Note:** False merges are worse than missed duplicates (data loss vs. redundancy), so prioritize precision over recall when selecting configurations.

## Decision Framework

### Comparing Configurations

A configuration is a combination of: model, prompt, temperature, retrieval method.

For each configuration, compute:
- Topic detection F1 (macro-averaged over 30 documents)
- Duplicate detection F1
- Duplicate detection precision (as a safety check)

### Selection Criteria

1. **Eliminate** any configuration with duplicate detection precision < 0.85 (too many false merges).
2. **Rank** remaining configurations by: `0.6 × topic_F1 + 0.4 × dedup_F1`.
3. If top-2 are within 3% of each other, prefer the cheaper/faster option.

### When to Re-evaluate

- Switching the underlying LLM model
- Modifying prompts in `app/prompts/ingestion.py`
- Changing the retrieval/search approach used for dedup candidates
- Users report duplicate pages or missed topics in production
