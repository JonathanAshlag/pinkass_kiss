"""
End-to-end eval, generic over datasets: an LLM agent answers a dataset's quiz using the
KB's DCI tools (ls / grep / read), orchestrated with LangGraph, and an LLM judge
(`kb.evaluation.LLMJudge`, langchain's ChatOpenAI) grades the answers.

A dataset is anything that yields `Case`s: a knowledge base (a manifest id) plus a
`kb.evaluation.Quiz` to ask against it. To add one, write a function returning a
`Dataset` and register it in `DATASETS`; the agent, grading, tracing and tests don't
change. Today: "qasper" (one case per paper: its manifest + that paper's questions).
E2E_DATASET picks one (default qasper).

Two tests:

- `test_agent_graph_wiring`: a scripted fake model drives the same graph + tools, so the
  tool-calling loop and the KB tool wrappers are checked for free (no API key).
- `test_llm_answers_quizzes_with_kb_tools`: a real model answers the first E2E_LLM_N
  (default 10) questions of the dataset, each E2E_LLM_K times (default 5), in parallel
  (E2E_LLM_WORKERS threads, default 8). Every (question, repetition) is answered by its own
  fresh agent; nothing is shared between runs. The report adds steps and runtime per
  answer (mean/variance/...) and the wall-clock total. E2E_LLM_PROVIDER picks the model: "auto"
  (default: the local Ollama when it's reachable, else Anthropic), "anthropic" (costs money,
  needs ANTHROPIC_API_KEY), "ollama" (local and free; needs a running server and a
  tool-calling model) or "openai" (any OpenAI-compatible server at
  OPENAI_BASE_URL, e.g. vLLM / vllm-mlx with tool calling enabled). Skipped when
  unavailable. Asserts the model used the tools on every question and that accuracy
  meets E2E_LLM_MIN_ACC (default 0.3).

Env (all read from .env, see .env.example): E2E_DATASET, E2E_LLM_PROVIDER, E2E_LLM_MODEL
(default claude-opus-5-5 / qwen3-coder:30b), E2E_LLM_N, E2E_LLM_K, E2E_LLM_WORKERS, E2E_LLM_MIN_ACC,
ANTHROPIC_API_KEY, OLLAMA_BASE_URL, OPENAI_BASE_URL; judge: OPENAI_API_KEY, JUDGE_MODEL
(default gpt-4o-mini), JUDGE_BASE_URL. With no judge key/URL set, the judge also falls
back to the local Ollama (its OpenAI-compatible /v1 endpoint, same model).

Observability: when OPIK_API_KEY is set (Comet cloud; also OPIK_WORKSPACE, optional
OPIK_PROJECT_NAME, default "pinkass-kiss-e2e"), every question of the real-LLM test
becomes an Opik trace -- the LangGraph graph, each model call and each KB tool call with
its arguments and output -- tagged with dataset/provider/model and the case id, and
scored with a `correct` feedback score. Without it, nothing is sent.
"""

import os
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

import pytest

pytest.importorskip("langgraph")

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage  # noqa: E402
from langchain_core.tools import tool  # noqa: E402
from langgraph.graph import START, MessagesState, StateGraph  # noqa: E402
from langgraph.prebuilt import ToolNode, tools_condition  # noqa: E402

from kb.evaluation.question import OpenQuestion  # noqa: E402
from kb.evaluation.quiz import LLMJudge, Quiz  # noqa: E402
from kb.evaluation.report import QuizReport, RunStats  # noqa: E402

# --------------------------------------------------------------------------
# Datasets: a knowledge base + a Quiz about it
# --------------------------------------------------------------------------

DEFAULT_SYSTEM_PROMPT = (
    "You answer questions using only a small knowledge base you can explore with tools: "
    "list_paths (ls), search_lines (grep), read_lines (read). Search and read before "
    "answering. When done, reply with ONLY the answer: as short as possible (a phrase, "
    "name, number, list, or yes/no), no explanation. If the knowledge base does not "
    "contain the answer, reply exactly: unanswerable"
)

QASPER_SYSTEM_PROMPT = (
    "You answer questions about one scientific paper using only a small knowledge base "
    "you can explore with tools: list_paths (ls), search_lines (grep), read_lines (read). "
    "The paper is a folder laid out like its own outline: read the folder itself for the "
    "title, abstract and outline; sections are numbered files/folders in reading order, "
    "and figures/ and tables/ hold captions. Search and read before answering. When "
    "done, reply with "
    "ONLY the answer: as short as possible (a phrase, number, list, or yes/no), no "
    "explanation. If the paper does not contain the answer, reply exactly: unanswerable"
)


@dataclass
class Case:
    """One knowledge base (a manifest) and the quiz to ask against it."""

    id: str
    manifest_id: uuid.UUID
    quiz: Quiz


@dataclass
class Dataset:
    name: str
    cases: list[Case]
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    meta: dict = field(default_factory=dict)

    def truncated(self, n: int) -> "Dataset":
        """The same dataset limited to its first `n` questions overall."""
        cases, left = [], n
        for case in self.cases:
            if left <= 0:
                break
            qs = case.quiz.questions[:left]
            if qs:
                cases.append(Case(case.id, case.manifest_id, Quiz(questions=qs)))
                left -= len(qs)
        return Dataset(self.name, cases, self.system_prompt, self.meta)


def gold_answer(answer: dict) -> str:
    """One QASPER annotator's answer as the string an agent should produce."""
    if answer["unanswerable"]:
        return "unanswerable"
    if answer["yes_no"] is not None:
        return "yes" if answer["yes_no"] else "no"
    if answer["extractive_spans"]:
        return ", ".join(answer["extractive_spans"])
    return answer["free_form_answer"]


def qasper_dataset(request) -> Dataset:
    """One case per paper (its manifest + its questions); the first annotator's answer
    is the reference the judge compares against."""
    papers = request.getfixturevalue("papers")
    manifests = request.getfixturevalue("manifests")
    cases = [
        Case(
            id=p["id"],
            manifest_id=manifests[p["id"]],
            quiz=Quiz(
                questions=[
                    OpenQuestion(question=q["question"], text_answer=gold_answer(q["answers"][0]))
                    for q in p["questions"]
                ]
            ),
        )
        for p in papers
    ]
    return Dataset("qasper", cases, system_prompt=QASPER_SYSTEM_PROMPT)


# name -> builder(request) -> Dataset. Builders pull what they need via fixtures.
DATASETS: dict[str, Callable[..., Dataset]] = {"qasper": qasper_dataset}


@pytest.fixture
def dataset(request) -> Dataset:
    name = os.environ.get("E2E_DATASET", "qasper").lower()
    if name not in DATASETS:
        pytest.fail(f"unknown E2E_DATASET '{name}' ({' | '.join(DATASETS)})")
    return DATASETS[name](request)


# --------------------------------------------------------------------------
# Agent
# --------------------------------------------------------------------------

def make_tools(manifest_id: uuid.UUID) -> list:
    """The KB's DCI tools as LangChain tools, scoped to one manifest."""
    from kb.retrieval.agent_tools import AgentTools

    return AgentTools(manifest_id).as_langchain()


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
    llm, manifest_id: uuid.UUID, question: str, system_prompt: str, callbacks: list | None = None
) -> tuple[str, RunStats]:
    """One question, answered by a fresh agent (its own graph, tools and message history).

    Returns (final answer text, RunStats: model steps, tool calls, wall-clock seconds).
    `callbacks` are LangChain callback handlers for the run (e.g. an Opik tracer). An agent
    that never stops calling tools (recursion limit), or a model/server error, yields an
    empty answer with `stats.failure` set: reported as an ERR outcome, never as an
    abstention.
    """
    from langgraph.errors import GraphRecursionError

    app = build_agent(llm, make_tools(manifest_id))
    messages: list = []
    answer = ""
    failure = None
    start = time.perf_counter()
    try:
        for state in app.stream(
            {"messages": [SystemMessage(system_prompt), HumanMessage(question)]},
            {"recursion_limit": 30, "callbacks": callbacks or []},
            stream_mode="values",
        ):
            messages = state["messages"]
        # reasoning models (Qwen3, ...) served without a reasoning parser inline their thoughts
        answer = re.sub(r"<think>.*?</think>", "", str(messages[-1].text), flags=re.DOTALL).strip()
    except GraphRecursionError:
        failure = "recursion limit"
    except Exception as exc:  # model/server error: one failed run must not abort the whole eval
        failure = f"error: {type(exc).__name__}: {exc}"
    seconds = time.perf_counter() - start
    ai = [m for m in messages if isinstance(m, AIMessage)]
    stats = RunStats(
        steps=len(ai),
        tool_calls=sum(len(m.tool_calls) for m in ai),
        seconds=seconds,
        failure=failure,
    )
    return answer, stats


class _ScriptedModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def test_agent_graph_wiring(dataset):
    case = dataset.cases[0]
    question = case.quiz.questions[0].question
    script = _ScriptedModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "list_paths", "args": {"recursive": True}, "id": "call_1"}
                ],
            ),
            AIMessage(content="unanswerable"),
        ]
    )
    answer, stats = ask(script, case.manifest_id, question, dataset.system_prompt)
    assert answer == "unanswerable"
    assert stats.tool_calls == 1 and stats.steps == 2 and stats.seconds > 0

    # the tools are backed by the KB: the manifest has documents, a bad regex is reported
    tools = {t.name: t for t in make_tools(case.manifest_id)}
    assert tools["list_paths"].invoke({"recursive": True}).strip()
    assert tools["search_lines"].invoke({"pattern": "("}).startswith("error:")


def _ollama_available() -> tuple[str, str, str | None]:
    """(base_url, model, problem): where a local Ollama should be, the model to use, and
    why it isn't usable (None when it is: reachable and the model is pulled)."""
    import httpx

    base_url = os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434"
    model = os.environ.get("E2E_LLM_MODEL") or "qwen3-coder:30b"
    try:
        tags = httpx.get(f"{base_url}/api/tags", timeout=5).json()["models"]
    except Exception as exc:  # server down / unreachable
        return base_url, model, f"ollama not reachable at {base_url}: {exc}"
    if model not in {m["name"] for m in tags}:
        return base_url, model, f"ollama model '{model}' not pulled (`ollama pull {model}`)"
    return base_url, model, None


def make_judge() -> LLMJudge:
    """The LLM judge (langchain ChatOpenAI). Uses OpenAI (OPENAI_API_KEY) or any
    OpenAI-compatible JUDGE_BASE_URL; with neither, falls back to the local Ollama's
    OpenAI-compatible endpoint when it's available, else skips."""
    base_url = os.environ.get("JUDGE_BASE_URL") or None
    model = os.environ.get("JUDGE_MODEL")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key and not base_url:
        ollama_url, ollama_model, problem = _ollama_available()
        if problem:
            pytest.skip(f"no LLM judge: OPENAI_API_KEY not set and {problem}")
        base_url, model = f"{ollama_url}/v1", model or ollama_model
    return LLMJudge(
        model=model or "gpt-4o-mini", base_url=base_url, api_key=api_key or "not-needed"
    )


def resolve_provider() -> str:
    """E2E_LLM_PROVIDER, with "auto" (the default) resolved to ollama if usable, else anthropic."""
    provider = os.environ.get("E2E_LLM_PROVIDER", "auto").lower()
    if provider != "auto":
        return provider
    return "ollama" if _ollama_available()[2] is None else "anthropic"


def make_llm():
    """The chat model for E2E_LLM_PROVIDER, or pytest.skip if it isn't available.

    The default provider, "auto", uses the local Ollama when it's reachable (and has the
    model), else Anthropic when ANTHROPIC_API_KEY is set, else skips."""
    provider = resolve_provider()
    if provider == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            pytest.skip("ANTHROPIC_API_KEY not set")
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=os.environ.get("E2E_LLM_MODEL") or "claude-opus-5-5", max_tokens=4096
        )
    if provider == "ollama":
        from langchain_ollama import ChatOllama

        base_url, model, problem = _ollama_available()
        if problem:
            pytest.skip(problem)
        return ChatOllama(model=model, base_url=base_url, temperature=0, num_ctx=8192)
    if provider == "openai":  # any OpenAI-compatible server: vLLM, vllm-mlx, LM Studio, ...
        import httpx
        from langchain_openai import ChatOpenAI

        base_url = os.environ.get("OPENAI_BASE_URL") or "http://127.0.0.1:8080/v1"
        try:
            served = httpx.get(f"{base_url}/models", timeout=5).json()["data"]
        except Exception as exc:  # server down / unreachable
            pytest.skip(f"OpenAI-compatible server not reachable at {base_url}: {exc}")
        model = os.environ.get("E2E_LLM_MODEL") or served[0]["id"]
        if model not in {m["id"] for m in served}:
            pytest.skip(f"model '{model}' not served at {base_url}")
        return ChatOpenAI(
            model=model,
            base_url=base_url,
            api_key=os.environ.get("OPENAI_API_KEY") or "not-needed",
            temperature=0,
            max_tokens=4096,
        )
    pytest.fail(f"unknown E2E_LLM_PROVIDER '{provider}' (anthropic | ollama | openai)")


OPIK_PROJECT = os.environ.get("OPIK_PROJECT_NAME") or "pinkass-kiss-e2e"


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
def test_llm_answers_quizzes_with_kb_tools(dataset):
    llm = make_llm()
    judge = make_judge()
    n = int(os.environ.get("E2E_LLM_N", "10"))
    k = int(os.environ.get("E2E_LLM_K", "5"))
    workers = int(os.environ.get("E2E_LLM_WORKERS", "8"))
    min_acc = float(os.environ.get("E2E_LLM_MIN_ACC", "0.3"))
    provider = resolve_provider()
    model = getattr(llm, "model_name", None) or getattr(llm, "model", "?")

    cases = dataset.truncated(n).cases
    # every (question, repetition) is its own job, answered by its own fresh agent
    jobs = [
        (ci, qi, rep)
        for ci, case in enumerate(cases)
        for qi in range(len(case.quiz.questions))
        for rep in range(k)
    ]

    def answer_job(job):
        ci, qi, rep = job
        case, question = cases[ci], cases[ci].quiz.questions[qi]
        tracer = opik_tracer(
            dataset=dataset.name,
            provider=provider,
            model=model,
            case_id=case.id,
            question_index=qi,
            repetition=rep,
            gold=question.text_answer,
        )
        answer, stats = ask(
            llm, case.manifest_id, question.question, dataset.system_prompt,
            [tracer] if tracer else None,
        )
        return answer, stats, tracer

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        outputs = dict(zip(jobs, pool.map(answer_job, jobs)))
    wall = time.perf_counter() - start

    def grade_repetition(key):  # one quiz repetition: judge calls run in parallel too
        ci, rep = key
        case = cases[ci]
        row = [outputs[(ci, qi, rep)] for qi in range(len(case.quiz.questions))]
        return case.quiz.report(
            [answer for answer, _, _ in row],
            judge,
            stats=[stats for _, stats, _ in row],
            repetition=rep,
            case_id=case.id,
        )

    keys = [(ci, rep) for ci in range(len(cases)) for rep in range(k)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        reports = list(pool.map(grade_repetition, keys))

    for (ci, rep), rep_report in zip(keys, reports):
        for qi, item in enumerate(rep_report.items):
            _, stats, tracer = outputs[(ci, qi, rep)]
            opik_score(
                tracer,
                correct=float(item.correct),
                steps=stats.steps,
                tool_calls=stats.tool_calls,
                seconds=stats.seconds,
            )

    report = QuizReport.combine(reports, wall_seconds=wall)
    print(f"\n[{dataset.name}] {k} repetition(s) per question, {workers} workers\n{report}")
    print("per question (correct rate / steps / seconds):")
    for q in report.by_question():
        print(
            f"  {q.correct_rate:>4.0%}  steps {q.steps.mean:>4.1f} (var {q.steps.variance:.2f})  "
            f"{q.seconds.mean:>6.1f}s (var {q.seconds.variance:.2f})  {q.question[:70]!r}"
        )
    for item in report.items:
        if not item.correct:
            print(f"  {item.outcome}: {item.question!r} gold={item.reference!r} got={item.answer!r}")
    for item in report.items:
        if item.outcome == "ERR":
            print(f"  ERR: {item.question!r} -- {item.stats.failure}")
    infra = [i.stats.failure for i in report.items if (i.stats.failure or "").startswith("error:")]
    assert not infra, f"{len(infra)} run(s) failed with model/server errors, e.g. {infra[0]}"
    assert not judge.unparseable, f"judge gave {len(judge.unparseable)} unparseable verdict(s): {judge.unparseable[:3]}"
    no_tools = [
        (i.case_id, i.question) for i in report.items if i.stats.tool_calls == 0 and i.outcome != "ERR"
    ]
    assert not no_tools, f"model answered without using the KB tools: {no_tools}"
    assert report.accuracy >= min_acc, f"accuracy {report.accuracy:.0%} < {min_acc:.0%}"
