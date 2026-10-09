---
title: "Beyond Semantic Similarity: Rethinking Retrieval for Agentic Search via Direct Corpus Interaction"
description: >-
  Agent-oriented summary of arXiv:2605.05242 (May 2026). Claims that LLM agents searching a raw
  corpus with terminal tools (grep/rg, find, head, file reads, small scripts) — no embeddings, no
  vector index, no retriever API — beat sparse/dense/reranker retrieval on agentic search, multi-hop
  QA and IR ranking, and explains why ("retrieval interface resolution").
tags: [retrieval, agentic-search, rag, grep, cli-agents, context-management, paper-summary]
status: stable
sources:
  - id: paper
    resource: paper.pdf
    note: arXiv:2605.05242v1 [cs.IR], 3 May 2026. Li, Zhang, Wei, ... Lin, Jiang, Zhang (Texas A&M, Waterloo, Stanford, UIUC, et al.)
  - id: code
    resource: https://github.com/DCI-Agent/DCI-Agent-Lite
---

# Direct Corpus Interaction (DCI) — paper summary

## How to use this file

- **§1 TL;DR** — read first; enough for most decisions.
- **§2 Main conclusions** — each claim with its supporting numbers. Cite these instead of re-reading the PDF.
- **§3 Operating envelope / limitations** — read before recommending DCI for a specific corpus.
- **§4 Actionable guidance** — distilled practices, if you are an agent doing DCI-style search or building a harness for one.
- **§5–7** — setup details, metric definitions, glossary. Look these up only when needed.
- All numbers come from the paper. Anything that is interpretation, not paper content, is marked **[interpretation]**.

---

## 1. TL;DR

1. **What DCI is:** the agent searches the **raw corpus files directly** with general-purpose shell tools (`rg`/`grep`, `find`/glob, `head`/`sed`, file reads, `python -c`). There is **no embedding model, vector index, reranker or retrieval API**, and no offline indexing step.
2. **It wins:** with the same backbone (Claude Sonnet 4.6) on BrowseComp-Plus, swapping a Qwen3-Embedding-8B retriever for DCI raises accuracy **69.0% → 80.0%** and cuts cost **$1,440 → $1,016 (−29.4%)**. It also leads on multi-hop QA (+30.7 pts avg) and IR ranking (+21.5 NDCG@10 avg).
3. **Why it wins:** DCI does **not** find more gold documents. Its mean recall is actually lower. It wins on **localization**: once it reaches one relevant document, it narrows to the exact span, checks constraints with exact matches, and chains to the next hop. The authors call this **"retrieval interface resolution"**: the ability to work on units smaller and more precise than whole documents or passages.
4. **Where it breaks:** cost and tool calls grow quickly with **corpus size**. From 100K to 400K documents, accuracy falls to 37.5% and tool calls reach about 122 per question. DCI is strong in search *depth* and expensive in search *breadth*.
5. **Minimal tools suffice:** `read` + `grep` alone already gives +16 pts over a dense retriever. A full bash shell adds about 12 more points at roughly 3× the cost.
6. **Thesis:** for capable agents, retrieval should be treated as an **interface-design problem**, not only a retriever-design problem.

---

## 2. Main conclusions (with evidence)

### C1. DCI beats retriever-mediated agents on end-to-end agentic search (BrowseComp-Plus, 830 Qs)

| Agent | Accuracy | Cost (full eval) |
|---|---|---|
| Sonnet 4.6 + Qwen3-Embedding-8B retriever | 69.0% | $1,440 |
| **DCI-Agent-CC (Sonnet 4.6, Claude Code)** | **80.0%** | **$1,016** |
| GPT-5 + Qwen3-Embedding-8B (best retrieval baseline) | 71.7% | — |
| o3 + Qwen3-Embedding-8B | 66.0% | ~$740 |
| **DCI-Agent-Lite (GPT-5.4 nano)** | **62.9%** | **$93** |

- With the backbone held fixed, DCI scores higher *and* costs less.
- DCI-Agent-Lite comes close to much stronger retrieval agents at a fraction of the cost (about $647 cheaper than o3 + retriever).

### C2. DCI beats retrieval agents on knowledge-intensive / multi-hop QA (accuracy %, 2018 Wikipedia corpus)

| Model | NQ | TriviaQA | Bamboogle | HotpotQA | 2Wiki | MuSiQue | Avg |
|---|---|---|---|---|---|---|---|
| ASearcher-Local-14B (best baseline) | 56 | 58 | 62 | 58 | 56 | 24 | 52.3 |
| DCI-Agent-Lite (GPT-5.4 nano) | 72 | 84 | 72 | 72 | 68 | 40 | 68.0 |
| **DCI-Agent-CC (Sonnet 4.6)** | **78** | **96** | **80** | **88** | **82** | **74** | **83.0** |

- The gains are largest on **multi-hop**: +30 HotpotQA, +26 2Wiki, **+50 MuSiQue** versus the best baseline.
- Caveat: the baselines are 7B–32B RL-trained open models, while the DCI agents use frontier or near-frontier backbones. The backbones are **not** matched here, unlike C1.

### C3. DCI beats sparse, dense and reasoning-reranker baselines on IR ranking (NDCG@10)

| Method | Bio | Earth | Econ | Robotics | ArguAna | SciFact | Avg |
|---|---|---|---|---|---|---|---|
| BM25 | 18.9 | 27.2 | 14.9 | 13.6 | 31.5 | 15.8 | 20.3 |
| ReasonRank-32B (best baseline) | 58.2 | 48.9 | 36.6 | 33.9 | 28.7 | 75.5 | 47.0 |
| DCI-Agent-Lite | 60.0 | 50.8 | 32.3 | 42.4 | 81.9 | 72.7 | 56.7 |
| **DCI-Agent-CC** | **77.1** | **69.0** | **46.8** | **56.8** | **85.3** | **75.7** | **68.5** |

- DCI-Agent-CC is best on all six datasets. Datasets: BRIGHT (Bio, Earth, Econ, Robotics) and BEIR (ArguAna, SciFact).
- The agent outputs a ranked list of up to 20 file paths, which is then scored with NDCG@10.

### C4. The advantage comes from *using* evidence better, not from *finding* more of it (core mechanism)

- On BrowseComp-Plus (Sonnet 4.6), **176** questions are solved by DCI and missed by the retrieval agent, versus **76** the other way round.
- Of those 176 DCI wins, only **34** were outright retrieval failures, where the retriever surfaced no gold document. The other 142 had already surfaced some gold evidence:
  - **83 partial-chain failures** (0 < recall < 100%): the retriever exposed some evidence, but not enough to bridge the next hop.
  - **59 post-retrieval failures** (recall = 100%): all gold documents were surfaced, yet the retrieval agent still failed to use them.
- Trajectory metrics (n=100, GPT-5.4 nano backbone for every row):

| Method | Tools/q | Cost/q | coverage_any | coverage_mean | coverage_all | Localization | Acc |
|---|---|---|---|---|---|---|---|
| BM25 retrieval agent | 19.1 | $0.053 | 63.0 | 42.8 | 17.0 | 23.5 | 32 |
| Qwen3-Emb-8B retrieval agent | 17.6 | $0.050 | **74.0** | **56.7** | **28.0** | 21.7 | 45 |
| **DCI-Agent-Lite (L4)** | 35.4 | $0.102 | 70.0 | 28.0 | 1.0 | **48.4** | **73** |

- How to read this: DCI's `coverage_any` (reaches at least one gold document) is close to the dense retriever's. Its mean and full-set coverage are far lower, but its **localization is more than 2× higher**, and accuracy is **+28 pts**. BrowseComp-Plus questions have only 1–4 gold documents each, so one good anchor plus deep local inspection is enough.
- **Trade-off:** DCI gives up exhaustive recovery of the gold chain to make high-resolution local progress. It uses about 2× the tool calls.

### C5. The agent uses DCI for composition, not for reading whole documents

- **DCI-Agent-CC tool mix:** Bash 62.4%, Grep 33.0%. Within Bash: chained search 22.3%, local context peeking 18.0%, regex matching 17.0%, file localization 14.0%, full-document reads only 9.1%.
- **DCI-Agent-Lite bash patterns (3,168 commands):** `rg | head` 56.2%, `rg | rg` 20.6%, `wc` 7.8%, single `rg` 6.3%, `ls` 4.3%, `python -c` 2.4%, `find`/`ls | rg` 2.1%, `cat` **0.1%**.
- The agent treats the shell as a **high-resolution search interface**: it composes lexical filters, reads only bounded snippets, and verifies by exact match.

### C6. A minimal tool set captures most of the gain (n=100 BrowseComp-Plus, GPT-5.4 nano)

| Configuration | Tools/q | Cost/q | Acc |
|---|---|---|---|
| BM25 retrieval agent | 19 | $0.0527 | 32 |
| Qwen3-Emb-8B retrieval agent | 18 | $0.0498 | 45 |
| **DCI: `read` + `grep` only** | 19 | **$0.0355** | 61 |
| DCI: open bash | 35 | $0.1021 | **73** |

- `read` + `grep` scores **+16 pts over dense retrieval** with the same number of tool calls and *lower* cost.
- Full bash adds +12 pts but uses about 2× the tool calls and about 3× the cost per question.

### C7. Context management has a sweet spot; more compression is not always better (n=100, DCI-Agent-Lite)

| Level | Policy | Tools/q | Latency (s) | Cost/q | Retained gold cov. | Acc |
|---|---|---|---|---|---|---|
| L0 | none | 28.5 | 2226 | $0.072 | 26.9 | 72 |
| L1 | truncate tool output at 50K chars | 29.0 | **1820** | $0.072 | **31.3** | 75 |
| L2 | truncate at 20K chars | 30.0 | 4413 | **$0.059** | 27.2 | 69 (worst) |
| **L3** | truncate at 20K + compaction | 36.9 | 8712 | $0.111 | 27.0 | **77 (best)** |
| L4 | L3 + LLM summarization | 35.4 | 4531 | $0.102 | 28.0 | 73 |

- **Compaction** in L3 is zero-LLM. Once accumulated tool output exceeds 240K chars, the contents of older tool-result turns are replaced with short placeholders. The tool-call structure is kept, and the most recent 12 turns stay intact.
- **Summarization** in L4 replaces compacted history with a model-written summary, keeping the last 20K tokens. It stops trying after 3 consecutive failures.
- **Conclusion:** the results are non-monotonic. Keeping the most verbatim evidence (L1) is *not* the same as keeping the best working state (L3). Forgetting selectively helps multi-step hypothesis revision. If compression is too weak, the agent drifts; if it is too blunt (L2, L4), useful intermediate structure is lost.

---

## 3. Operating envelope and limitations

| Factor | Effect on DCI |
|---|---|
| **Corpus size (breadth)** | Scales poorly. For DCI-Agent-CC on n=100, growing the corpus from 100K to 200K docs (FineWeb distractors) took tool calls from **38.5 to 86.9**, more than doubled latency and cost, and cut accuracy by **13.6 pts**. At 400K docs, accuracy was **37.5%**, there were **122.4 tool calls/q**, and **20/100** runs hit the tool budget. |
| **Search depth (after finding an anchor)** | Scales well. This is DCI's strength. |
| **Corpus dynamics** | No index to rebuild, so DCI adapts naturally to local, heterogeneous corpora that keep changing. |
| **Lexical mismatch** | Implied rather than measured. The failure case D.9 shows a weak agent issuing overly broad `rg` patterns, never converging, and hallucinating an answer that matches surface keywords. |
| **Reasoning errors after finding evidence** | Failure case D.8: the agent found the right entities but attributed a fact to the wrong opponent. DCI does not remove reasoning mistakes. |
| **Cost per question** | Usually higher per question than a single retrieval agent at the same backbone (about 2× tool calls), *except* in C1, where DCI was cheaper overall because the retrieval agent needed more turns or tokens. |

Authors' framing: dense and sparse retrieval are still scalable and effective for **large, static** corpora. They are one point in a larger design space of corpus interfaces, not something to replace everywhere.

Evaluation caveats:
- Most QA and BEIR datasets used a **random sample of 50** examples. BRIGHT and Bamboogle used full test sets, and the ablations used n=100. Expect noticeable variance.
- QA and BrowseComp-Plus answers were graded by an LLM judge (GPT-4.1).
- DCI-Agent-CC had web-search, web-fetch and subagents disabled, and the data directory was blocked to prevent answer leakage. The turn budget was 300.

---

## 4. Actionable guidance

### 4a. If you are an agent doing search over a file corpus

Distilled from the observed winning behavior (§2 C4–C5, appendix B.1) and the paper's prompts (appendix C):

1. **Typical order of operations:**
   - Explore the structure (`ls`, `find`).
   - Run a broad keyword search (`rg -n`).
   - Narrow iteratively (`rg -n "kw1" | rg "kw2"`).
   - Read targeted documents.
   - Search inside a document (`rg -n "term" file`).
   - Compare across documents (`rg -n "kw" f1 f2`) to verify and disambiguate.
2. **Always limit output.** Use `rg ... | head`, `-m N`, and `head -c N`. Peek at local context instead of `cat`-ing whole files; full reads were about 0.1–9% of commands.
3. **Combine weak clues with pipes** to get an AND over several rare terms. Use exact phrases and regex to enforce hard constraints such as dates, names and numbers.
4. **Once you find one relevant document, go deeper rather than wider.** Pull out new entities and constraints from it and launch the next hop from that span.
5. **Run several searches in parallel** in a single turn, with diverse keyword combinations.
6. **Rule out competing candidates** before you commit to an answer. Cite file paths inline. State low confidence (<50%) when the evidence is weak.
7. **Watch for the D.9 failure mode.** If broad patterns keep returning irrelevant hits, make the patterns *more specific*: rare tokens, exact phrases, conjunctions. Do not settle on a candidate that only matches surface keywords.

### 4b. If you are building a harness or KB that serves agents **[interpretation]**

- Exposing the corpus as **plain files plus `grep`/`read`** is a strong baseline that costs nothing to index. Try it before building embedding infrastructure, especially for small or medium corpora that change often.
- Keep tool outputs **bounded**: truncate per call at roughly 20–50K chars. Add **zero-LLM compaction** of old tool results before reaching for LLM summarization (L3 beat L4).
- For very large corpora, DCI's weakness is finding the *first anchor*. A coarse pre-filter (metadata, tags, directory structure, or an optional retriever) can supply that anchor, with DCI used for depth after it. The paper did not test this hybrid.
- Relevance to this repo (`pinkass_kiss`): this KB serves the right context to agents and can scope what they see with manifests. Its tree, titles, tags, description and status columns could serve as the cheap anchor-finding layer, with DCI-style exact search over `content` for depth. This hypothesis was not tested in the paper.

---

## 5. Setup details (reference)

- **DCI-Agent-Lite:** a minimal harness built on Pi (a terminal coding agent). Tools are `bash` + `read` only. Backbone is GPT-5.4 nano with high reasoning effort. Uses L3 context management in the main results and L4 in the ablations.
- **DCI-Agent-CC:** Claude Code with default configuration, minus web-search, web-fetch and subagents. Backbone is Claude Sonnet 4.6 with medium reasoning effort.
- **Turn budget:** 300 for both.
- **Retrieval baselines:**
  - BrowseComp-Plus: official pipeline with BM25 or a Qwen3-Embedding-8B FAISS index. Backbones were GPT-5, o3, GLM-4.7, Sonnet 4.5/4.6, among others.
  - QA: E5 index over 2018 Wikipedia, with R1-Searcher, Search-R1, ZeroSearch, Verl-Tool-Search and ASearcher.
  - IR: BM25, OpenAI text-embedding-3-large, GTE-Qwen2-7B, Rank-R1-14B, Rank1-32B, ReasonRank-32B.
- **Corpora:** BrowseComp-Plus is about 100K docs averaging about 5.2K words. Wikipedia-18 is about 21M passages averaging about 100 words. BRIGHT and BEIR corpora are 5K–121K short docs.
- **Prompt essentials (QA):**
  - "Answer using ONLY documents in @corpus."
  - Use ripgrep/Bash only, with no subagents and no web.
  - Run parallel searches with diverse keywords.
  - Rule out competing candidates.
  - Cite `[@corpus/path]`.
  - Output: `Explanation / Exact Answer / Confidence`.
- **Prompt essentials (IR):** the QA rules, plus:
  - Reflect on gaps after each round.
  - Exhaust all search angles.
  - Read each candidate before including it, because precision matters as well as recall.
  - Output up to 20 ranked paths.

## 6. Metric definitions (from §3.3)

- **Surfaced:** a gold document appears explicitly in the trace, either as a retrieved snippet or as a file returned by a tool call.
- **coverage_any:** at least one gold document was surfaced. **coverage_mean:** the fraction of gold documents surfaced. **coverage_all:** every gold document was surfaced. All three measure *reach*.
- **Localization:** for each surfaced gold document `d`, take the best per-snippet score `max(1 − log(seg(snippet_len)) / log(seg(|d|)), 0)`, where `seg(x) = max(1, ⌈x / c_seg⌉)` counts fixed-width character segments. Average this over the surfaced gold documents. A higher score means the agent isolated a smaller span relative to the document size.
  - For grep hits, the snippet is the matched line.
  - For reads, the snippet is the span that was read.
  - If a document was surfaced only by path, the snippet is the whole document, which gives a low score.

## 7. Glossary

- **DCI (Direct Corpus Interaction):** the agent searches raw files with general terminal tools and no retrieval layer.
- **Retriever-mediated access:** the agent sends a query and gets back a top-k ranked list of snippets and document IDs from a fixed similarity interface.
- **Retrieval interface resolution:** how fine-grained the units are that the interface lets the agent observe, verify and act on. Spans and lines are high resolution; whole documents and top-k passages are low resolution.
- **Partial-chain failure:** some gold evidence was surfaced, but not enough to bridge the next hop.
- **Post-retrieval failure:** all gold evidence was surfaced, but the agent failed to use it.
- **Compaction:** a zero-LLM step that replaces old tool-result contents with placeholders while keeping the call structure.
