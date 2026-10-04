"""
An LLM agent answers HotPotQA questions using the KB's DCI tools (ls / grep / read),
orchestrated with LangGraph. Two tests:
∏
- `test_agent_graph_wiring`: a scripted fake model drives the same graph + tools, PPPPPso the
  tool-calling loop and the KB tool wrappers are checked for free (no API key).
- `test_llm_answers_questions_with_kb_tools`: a real model answers the first
  HOTPOTQA_LLM_N (default 10) questions, each against that question's manifest (its 10
  paragraphs, the distractor setting). HOTPOTQA_LLM_PROVIDER picks the model:
  "anthropic" (default; costs money, needs ANTHROPIC_API_KEY) or "ollama" (local and
  free; needs a running server and a tool-calling model). Skipped when unavailable. Asserts the model used the tools on every question and that
  accuracy (normalized exact match or containment) meets HOTPOTQA_LLM_MIN_ACC (default 0.6).

Env (all read from .env, see .env.example): HOTPOTQA_LLM_PROVIDER, ANTHROPIC_API_KEY,
OLLAMA_BASE_URL, HOTPOTQA_LLM_MODEL (default claude-opus-5-5 / qwen3-coder:30b), HOTPOTQA_LLM_N, HOTPOTQA_LLM_MIN_ACC.
"""

import os
import re
import string
import uuid

import pytest

pytest.importorskip("langgraph")

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage  # noqa: E402
from langchain_core.tools import tool  # noqa: E402
from langgraph.graph import END, START, MessagesState, StateGraph  # noqa: E402
from langgraph.prebuilt import ToolNode, tools_condition  # noqa: E402

SYSTEM_PROMPT = (
    "You answer questions using only a small knowledge base you can explore with tools: "
    "list_paths (ls), search_lines (grep), read_lines (read). Search and read before "
    "answering; questions often need two documents combined. When done, reply with ONLY "
    "the answer: as short as possible (a name, date, number, or yes/no), no explanation."
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


def ask(llm, manifest_id: uuid.UUID, question: str) -> tuple[str | None, int]:
    """Returns (final answer text, number of tool calls made).

    The answer is None if the agent never stopped calling tools (recursion limit): that's
    an unanswered question for the eval, not a crash.
    """
    from langgraph.errors import GraphRecursionError

    app = build_agent(llm, make_tools(manifest_id))
    messages: list = []
    try:
        for state in app.stream(
            {"messages": [SystemMessage(SYSTEM_PROMPT), HumanMessage(question)]},
            {"recursion_limit": 30},
            stream_mode="values",
        ):
            messages = state["messages"]
    except GraphRecursionError:
        calls = sum(len(m.tool_calls) for m in messages if isinstance(m, AIMessage))
        return None, calls
    calls = sum(len(m.tool_calls) for m in messages if isinstance(m, AIMessage))
    return str(messages[-1].text).strip(), calls


def _normalize(s: str) -> str:
    s = s.lower()
    s = "".join(ch for ch in s if ch not in set(string.punctuation))
    s = re.sub(r"\b(a|an|the)\b", " ", s)
    return " ".join(s.split())


def is_correct(prediction: str, gold: str) -> bool:
    p, g = _normalize(prediction), _normalize(gold)
    return bool(g) and (p == g or g in p)


class _ScriptedModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def test_agent_graph_wiring(examples, manifests, session):
    ex = next(e for e in examples if e["answer"].lower() not in ("yes", "no"))
    script = _ScriptedModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_lines",
                        "args": {"pattern": re.escape(ex["answer"]), "files_only": True},
                        "id": "call_1",
                    }
                ],
            ),
            AIMessage(content=ex["answer"]),
        ]
    )
    answer, calls = ask(script, manifests[ex["id"]], ex["question"])
    assert calls == 1 and answer == ex["answer"]

    # the tool result the model saw came from the KB: a gold paragraph path
    tools = {t.name: t for t in make_tools(manifests[ex["id"]])}
    hits = tools["search_lines"].invoke({"pattern": re.escape(ex["answer"]), "files_only": True})
    gold = {f"HotPotQA/{t.replace('/', '-').strip()}" for t in ex["supporting_titles"]}
    assert gold & set(hits.split("\n"))
    assert tools["search_lines"].invoke({"pattern": "("}).startswith("error:")  # bad regex is reported, not raised


def make_llm():
    """The chat model for HOTPOTQA_LLM_PROVIDER, or pytest.skip if it isn't available."""
    provider = os.environ.get("HOTPOTQA_LLM_PROVIDER", "anthropic").lower()
    if provider == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            pytest.skip("ANTHROPIC_API_KEY not set")
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=os.environ.get("HOTPOTQA_LLM_MODEL") or "claude-opus-5-5", max_tokens=4096
        )
    if provider == "ollama":
        import httpx
        from langchain_ollama import ChatOllama

        base_url = os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434"
        model = os.environ.get("HOTPOTQA_LLM_MODEL") or "qwen3-coder:30b"
        try:
            tags = httpx.get(f"{base_url}/api/tags", timeout=5).json()["models"]
        except Exception as exc:  # server down / unreachable
            pytest.skip(f"ollama not reachable at {base_url}: {exc}")
        if model not in {m["name"] for m in tags}:
            pytest.skip(f"ollama model '{model}' not pulled (`ollama pull {model}`)")
        return ChatOllama(model=model, base_url=base_url, temperature=0, num_ctx=8192)
    pytest.fail(f"unknown HOTPOTQA_LLM_PROVIDER '{provider}' (anthropic | ollama)")


@pytest.mark.llm
def test_llm_answers_questions_with_kb_tools(examples, manifests, session):
    llm = make_llm()
    n = int(os.environ.get("HOTPOTQA_LLM_N", "10"))
    min_acc = float(os.environ.get("HOTPOTQA_LLM_MIN_ACC", "0.6"))

    correct, no_tools, wrong, unanswered = 0, [], [], 0
    for ex in examples[:n]:
        answer, calls = ask(llm, manifests[ex["id"]], ex["question"])
        if calls == 0:
            no_tools.append(ex["id"])
        if answer is None:
            unanswered += 1
            wrong.append((ex["question"], ex["answer"], "<no answer: hit recursion limit>"))
        elif is_correct(answer, ex["answer"]):
            correct += 1
        else:
            wrong.append((ex["question"], ex["answer"], answer))

    accuracy = correct / n
    print(f"\naccuracy {correct}/{n} = {accuracy:.0%} ({unanswered} unanswered)")
    for q, gold, got in wrong:
        print(f"  WRONG: {q!r} gold={gold!r} got={got!r}")
    assert not no_tools, f"model answered without using the KB tools: {no_tools}"
    assert accuracy >= min_acc, f"accuracy {accuracy:.0%} < {min_acc:.0%}"
