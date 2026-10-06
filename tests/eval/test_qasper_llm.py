"""
An LLM agent answers QASPER questions using the KB's DCI tools (ls / grep / read),
orchestrated with LangGraph. Two tests:

- `test_agent_graph_wiring`: a scripted fake model drives the same graph + tools
  (`kb.retrieval.agent_tools`), so the tool-calling loop is checked for free (no API key).
- `test_llm_answers_questions_with_kb_tools`: a real model answers the first
  QASPER_LLM_N (default 10) questions, each against its paper's manifest (the paper's
  folder tree). QASPER_LLM_PROVIDER picks the model: "anthropic" (default; costs money,
  needs ANTHROPIC_API_KEY), "ollama" (local and free; needs a running server and a
  tool-calling model) or "openai" (any OpenAI-compatible server at OPENAI_BASE_URL, e.g.
  vLLM / vllm-mlx with tool calling enabled). Skipped when unavailable. Asserts the model used the tools on
  every question and that the mean token-F1 against the best annotator answer (QASPER's
  Answer-F1) meets QASPER_LLM_MIN_F1 (default 0.3).

QASPER_SEMANTIC=1 adds the fourth tool, `semantic_search` (embedding search over
kb_chunks): the fixture corpus is indexed once per session (needs the embeddings model,
EMBEDDINGS_MODEL, to be reachable) and the agent gets `as_langchain(include_semantic=True)`
plus a prompt line about it -- run with and without it to compare Answer-F1.

Env (all read from .env, see .env.example): QASPER_LLM_PROVIDER, QASPER_SEMANTIC, ANTHROPIC_API_KEY,
OLLAMA_BASE_URL, OPENAI_BASE_URL, QASPER_LLM_MODEL (default claude-opus-5-5 / qwen3-coder:30b),
QASPER_LLM_N, QASPER_LLM_MIN_F1.

Observability: when OPIK_API_KEY is set (Comet cloud; also OPIK_WORKSPACE, optional
OPIK_PROJECT_NAME, default "pinkass-kiss-qasper"), every question of the real-LLM test
becomes an Opik trace -- the LangGraph graph, each model call and each KB tool call with
its arguments and output -- tagged with provider/model and the paper/question ids, and
scored with an `answer_f1` feedback score. Without it, nothing is sent.
"""

import os
import re
import string
import uuid
from collections import Counter

import pytest

pytest.importorskip("langgraph")

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage  # noqa: E402
from langgraph.graph import END, START, MessagesState, StateGraph  # noqa: E402
from langgraph.prebuilt import ToolNode, tools_condition  # noqa: E402

from kb.retrieval.agent_tools import AgentTools  # noqa: E402

SYSTEM_PROMPT = (
    "You answer questions about one scientific paper using only a small knowledge base "
    "you can explore with tools: list_paths (ls), search_lines (grep), read_lines (read). "
    "The paper is a folder laid out like its own outline: read its 00-overview.md for the "
    "title, abstract and outline; sections are numbered files/folders in reading order, "
    "and figures/ and tables/ hold captions. Search and read before answering. When "
    "done, reply with "
    "ONLY the answer: as short as possible (a phrase, number, list, or yes/no), no "
    "explanation. If the paper does not contain the answer, reply exactly: unanswerable"
)
SEMANTIC_PROMPT = (
    " You also have semantic_search, which finds passages by meaning: prefer it over "
    "search_lines when you don't know the paper's exact wording, then read_lines the hits."
)


def semantic_enabled() -> bool:
    return os.environ.get("QASPER_SEMANTIC", "").strip().lower() in ("1", "true", "yes", "on")


def build_agent(llm, tools):
    """agent node -> (tool calls? -> tools node -> agent node) -> END"""
    model = llm.bind_tools(tools)

    def call_model(state: MessagesState):
        return {"messages": [model.invoke(state["messages"])]}

    graph = StateGraph(MessagesState)
    graph.add_node("agent", call_model)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition)  # -> "tools" or END
    graph.add_edge("tools", "agent")
    return graph.compile()


def ask(
    llm,
    manifest_id: uuid.UUID,
    question: str,
    callbacks: list | None = None,
    *,
    semantic: bool = False,
) -> tuple[str, int]:
    """Returns (final answer text, number of tool calls made). `callbacks` are LangChain
    callback handlers for the run (e.g. an Opik tracer). `semantic` adds the
    semantic_search tool (the corpus must be indexed)."""
    tools = AgentTools(manifest_id).as_langchain(include_semantic=semantic)
    prompt = SYSTEM_PROMPT + (SEMANTIC_PROMPT if semantic else "")
    result = build_agent(llm, tools).invoke(
        {"messages": [SystemMessage(prompt), HumanMessage(question)]},
        {"recursion_limit": 30, "callbacks": callbacks or []},
    )
    messages = result["messages"]
    calls = sum(len(m.tool_calls) for m in messages if isinstance(m, AIMessage))
    # reasoning models (Qwen3, ...) served without a reasoning parser inline their thoughts
    answer = re.sub(r"<think>.*?</think>", "", str(messages[-1].text), flags=re.DOTALL)
    return answer.strip(), calls


def _normalize(s: str) -> str:
    s = s.lower()
    s = "".join(ch for ch in s if ch not in set(string.punctuation))
    s = re.sub(r"\b(a|an|the)\b", " ", s)
    return " ".join(s.split())


def token_f1(prediction: str, gold: str) -> float:
    p, g = _normalize(prediction).split(), _normalize(gold).split()
    if not p or not g:
        return float(p == g)
    common = sum((Counter(p) & Counter(g)).values())
    if not common:
        return 0.0
    precision, recall = common / len(p), common / len(g)
    return 2 * precision * recall / (precision + recall)


def gold_strings(answer: dict) -> str:
    """One annotator's answer as the string an agent should produce."""
    if answer["unanswerable"]:
        return "unanswerable"
    if answer["yes_no"] is not None:
        return "yes" if answer["yes_no"] else "no"
    if answer["extractive_spans"]:
        return ", ".join(answer["extractive_spans"])
    return answer["free_form_answer"]


def best_f1(prediction: str, answers: list[dict]) -> float:
    """QASPER's Answer-F1: the best token-F1 over the annotators' answers."""
    return max(token_f1(prediction, gold_strings(a)) for a in answers)


class _ScriptedModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def _first_evidence_question(papers):
    for paper in papers:
        for q in paper["questions"]:
            for a in q["answers"]:
                for ev in a["evidence"]:
                    if len(ev) > 40 and "FLOAT SELECTED" not in ev and not a["unanswerable"]:
                        return paper, q, ev.strip()
    raise AssertionError("no question with text evidence in the fixture")


def test_agent_graph_wiring(papers, manifests, session):
    paper, q, ev = _first_evidence_question(papers)
    script = _ScriptedModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_lines",
                        "args": {"pattern": re.escape(ev), "files_only": True},
                        "id": "call_1",
                    }
                ],
            ),
            AIMessage(content="unanswerable"),
        ]
    )
    answer, calls = ask(script, manifests[paper["id"]], q["question"])
    assert calls == 1 and answer == "unanswerable"

    # the tool result the model saw came from the KB: a path inside this paper's folder
    tools = {t.name: t for t in AgentTools(manifests[paper["id"]]).as_langchain()}
    hits = tools["search_lines"].invoke({"pattern": re.escape(ev), "files_only": True})
    prefix = f"QASPER/{paper['title'].replace('/', '-').strip()}/"
    assert hits.split("\n") and all(h.startswith(prefix) for h in hits.split("\n"))
    assert tools["search_lines"].invoke({"pattern": "("}).startswith("error:")  # bad regex is reported, not raised


def make_llm():
    """The chat model for QASPER_LLM_PROVIDER, or pytest.skip if it isn't available."""
    provider = os.environ.get("QASPER_LLM_PROVIDER", "anthropic").lower()
    if provider == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            pytest.skip("ANTHROPIC_API_KEY not set")
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=os.environ.get("QASPER_LLM_MODEL") or "claude-opus-5-5", max_tokens=4096
        )
    if provider == "ollama":
        import httpx
        from langchain_ollama import ChatOllama

        base_url = os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434"
        model = os.environ.get("QASPER_LLM_MODEL") or "qwen3-coder:30b"
        try:
            tags = httpx.get(f"{base_url}/api/tags", timeout=5).json()["models"]
        except Exception as exc:  # server down / unreachable
            pytest.skip(f"ollama not reachable at {base_url}: {exc}")
        if model not in {m["name"] for m in tags}:
            pytest.skip(f"ollama model '{model}' not pulled (`ollama pull {model}`)")
        return ChatOllama(model=model, base_url=base_url, temperature=0, num_ctx=8192)
    if provider == "openai":  # any OpenAI-compatible server: vLLM, vllm-mlx, LM Studio, ...
        import httpx
        from langchain_openai import ChatOpenAI

        base_url = os.environ.get("OPENAI_BASE_URL") or "http://127.0.0.1:8080/v1"
        try:
            served = httpx.get(f"{base_url}/models", timeout=5).json()["data"]
        except Exception as exc:  # server down / unreachable
            pytest.skip(f"OpenAI-compatible server not reachable at {base_url}: {exc}")
        model = os.environ.get("QASPER_LLM_MODEL") or served[0]["id"]
        if model not in {m["id"] for m in served}:
            pytest.skip(f"model '{model}' not served at {base_url}")
        return ChatOpenAI(
            model=model,
            base_url=base_url,
            api_key=os.environ.get("OPENAI_API_KEY") or "not-needed",
            temperature=0,
            max_tokens=4096,
        )
    pytest.fail(f"unknown QASPER_LLM_PROVIDER '{provider}' (anthropic | ollama | openai)")


OPIK_PROJECT = os.environ.get("OPIK_PROJECT_NAME") or "pinkass-kiss-qasper"


def opik_tracer(**metadata):
    """An OpikTracer for one question, or None when Opik isn't configured."""
    if not os.environ.get("OPIK_API_KEY"):
        return None
    from opik.integrations.langchain import OpikTracer

    tags = ["qasper", f"provider:{metadata['provider']}", f"model:{metadata['model']}"]
    if metadata.get("semantic"):
        tags.append("semantic_search")
    return OpikTracer(project_name=OPIK_PROJECT, tags=tags, metadata=metadata)


def opik_score(tracer, **scores) -> None:
    """Attach feedback scores (e.g. answer_f1) to the trace(s) `tracer` created."""
    if tracer is None:
        return
    import opik

    tracer.flush()
    client = opik.Opik(project_name=OPIK_PROJECT)
    client.log_traces_feedback_scores(
        [
            {"id": trace.id, "name": name, "value": value}
            for trace in tracer.created_traces()
            for name, value in scores.items()
        ],
        project_name=OPIK_PROJECT,
    )
    client.flush()


@pytest.fixture(scope="session")
def semantic_index(manifests):
    """With QASPER_SEMANTIC=1: index the whole fixture corpus once (True); else False."""
    if not semantic_enabled():
        return False
    from kb import service
    from kb.storage.db import SessionLocal

    with SessionLocal() as s:
        node_ids = [n.id for n in service.resolve_manifest(s, manifests["corpus"])]
    result = service.index_files(node_ids)
    assert not result.failed, f"indexing failed: {result.failed[:3]}"
    return True


@pytest.mark.llm
def test_llm_answers_questions_with_kb_tools(papers, manifests, session, request):
    llm = make_llm()
    # only after make_llm(), so a skipped run never pays for indexing
    semantic = request.getfixturevalue("semantic_index")
    n = int(os.environ.get("QASPER_LLM_N", "10"))
    min_f1 = float(os.environ.get("QASPER_LLM_MIN_F1", "0.3"))

    questions = [(p["id"], q) for p in papers for q in p["questions"]][:n]
    scores, no_tools, wrong = [], [], []
    for paper_id, q in questions:
        tracer = opik_tracer(
            provider=os.environ.get("QASPER_LLM_PROVIDER", "anthropic").lower(),
            model=getattr(llm, "model_name", None) or getattr(llm, "model", "?"),
            paper_id=paper_id,
            question_id=q["id"],
            gold=[gold_strings(a) for a in q["answers"]],
            semantic=semantic,
        )
        answer, calls = ask(
            llm, manifests[paper_id], q["question"], [tracer] if tracer else None, semantic=semantic
        )
        if calls == 0:
            no_tools.append(q["id"])
        f1 = best_f1(answer, q["answers"])
        scores.append(f1)
        opik_score(tracer, answer_f1=f1, tool_calls=calls)
        if f1 < 0.5:
            wrong.append((q["question"], [gold_strings(a) for a in q["answers"]], answer))

    mean_f1 = sum(scores) / len(scores)
    print(f"\nmean answer-F1 {mean_f1:.2f} over {len(scores)} questions (semantic_search: {semantic})")
    for question, gold, got in wrong:
        print(f"  LOW: {question!r} gold={gold!r} got={got!r}")
    assert not no_tools, f"model answered without using the KB tools: {no_tools}"
    assert mean_f1 >= min_f1, f"mean F1 {mean_f1:.2f} < {min_f1:.2f}"
