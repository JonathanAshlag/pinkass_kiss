"""
An LLM agent answers QASPER questions using the KB's DCI tools (ls / grep / read),
orchestrated with LangGraph. Two tests:

- `test_agent_graph_wiring`: a scripted fake model drives the same graph + tools, so the
  tool-calling loop and the KB tool wrappers are checked for free (no API key).
- `test_llm_answers_questions_with_kb_tools`: a real model answers the first
  QASPER_LLM_N (default 10) questions, each against its paper's manifest (the paper's
  folder tree). QASPER_LLM_PROVIDER picks the model: "anthropic" (default; costs money,
  needs ANTHROPIC_API_KEY), "ollama" (local and free; needs a running server and a
  tool-calling model) or "openai" (any OpenAI-compatible server at OPENAI_BASE_URL, e.g.
  vLLM / vllm-mlx with tool calling enabled). Skipped when unavailable. Asserts the model used the tools on
  every question and that the mean token-F1 against the best annotator answer (QASPER's
  Answer-F1) meets QASPER_LLM_MIN_F1 (default 0.3).

Env (all read from .env, see .env.example): QASPER_LLM_PROVIDER, ANTHROPIC_API_KEY,
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
from langchain_core.tools import tool  # noqa: E402
from langgraph.graph import END, START, MessagesState, StateGraph  # noqa: E402
from langgraph.prebuilt import ToolNode, tools_condition  # noqa: E402

SYSTEM_PROMPT = (
    "You answer questions about one scientific paper using only a small knowledge base "
    "you can explore with tools: list_paths (ls), search_lines (grep), read_lines (read). "
    "The paper is a folder laid out like its own outline: read the folder itself for the "
    "title, abstract and outline; sections are numbered files/folders in reading order, "
    "and figures/ and tables/ hold captions. Search and read before answering. When "
    "done, reply with "
    "ONLY the answer: as short as possible (a phrase, number, list, or yes/no), no "
    "explanation. If the paper does not contain the answer, reply exactly: unanswerable"
)


def make_tools(manifest_id: uuid.UUID) -> list:
    """The KB's DCI tools as LangChain tools, scoped to one manifest."""
    from kb import service
    from kb.db import SessionLocal

    def run(fn, *args, **kwargs) -> str:
        with SessionLocal() as session:
            try:
                return fn(session, manifest_id, *args, **kwargs).text
            except (ValueError, service.PatternError) as exc:
                return f"error: {exc}"

    @tool
    def list_paths(under: str | None = None, recursive: bool = False) -> str:
        """List documents in the knowledge base (like `ls`/`find`)."""
        return run(service.list_paths, under=under, recursive=recursive)

    @tool
    def search_lines(pattern: str, ignore_case: bool = True, files_only: bool = False) -> str:
        """Regex search over all documents (like `grep -n`). Output: path:line:text."""
        return run(service.search_lines, pattern, ignore_case=ignore_case, files_only=files_only)

    @tool
    def read_lines(path: str, offset: int = 1, limit: int = 50) -> str:
        """Read a line range of one document by path (like `sed -n`)."""
        return run(service.read_lines, path, offset=offset, limit=limit)

    return [list_paths, search_lines, read_lines]


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


def ask(llm, manifest_id: uuid.UUID, question: str, callbacks: list | None = None) -> tuple[str, int]:
    """Returns (final answer text, number of tool calls made). `callbacks` are LangChain
    callback handlers for the run (e.g. an Opik tracer)."""
    result = build_agent(llm, make_tools(manifest_id)).invoke(
        {"messages": [SystemMessage(SYSTEM_PROMPT), HumanMessage(question)]},
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
    tools = {t.name: t for t in make_tools(manifests[paper["id"]])}
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


@pytest.mark.llm
def test_llm_answers_questions_with_kb_tools(papers, manifests, session):
    llm = make_llm()
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
        )
        answer, calls = ask(llm, manifests[paper_id], q["question"], [tracer] if tracer else None)
        if calls == 0:
            no_tools.append(q["id"])
        f1 = best_f1(answer, q["answers"])
        scores.append(f1)
        opik_score(tracer, answer_f1=f1, tool_calls=calls)
        if f1 < 0.5:
            wrong.append((q["question"], [gold_strings(a) for a in q["answers"]], answer))

    mean_f1 = sum(scores) / len(scores)
    print(f"\nmean answer-F1 {mean_f1:.2f} over {len(scores)} questions")
    for question, gold, got in wrong:
        print(f"  LOW: {question!r} gold={gold!r} got={got!r}")
    assert not no_tools, f"model answered without using the KB tools: {no_tools}"
    assert mean_f1 >= min_f1, f"mean F1 {mean_f1:.2f} < {min_f1:.2f}"
