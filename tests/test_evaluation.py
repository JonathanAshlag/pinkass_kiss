"""
Deterministic tests for `kb.evaluation` (quiz grading, the judge, the report metrics).
No database, no network: the judge's chat model is a stub.
"""

import warnings
from types import SimpleNamespace

import pytest

from kb.evaluation.question import ClosedQuestion, OpenQuestion
from kb.evaluation.quiz import LLMJudge, Quiz, parse_verdict
from kb.evaluation.report import ItemResult, QuizReport, RunStats, Stats, classify, is_abstain


class StubLLM:
    """A judge model that replies from a script (cycling) and counts its calls."""

    def __init__(self, *replies: str):
        self.replies = list(replies) or ["CORRECT"]
        self.calls = 0

    def invoke(self, prompt: str):
        reply = self.replies[self.calls % len(self.replies)]
        self.calls += 1
        return SimpleNamespace(text=reply)


def judge(*replies: str) -> LLMJudge:
    return LLMJudge(llm=StubLLM(*replies))


def open_quiz(*refs: str) -> Quiz:
    return Quiz(questions=[OpenQuestion(question=f"q{i}", text_answer=r) for i, r in enumerate(refs)])


# --------------------------------------------------------------------------
# is_abstain / parse_verdict
# --------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["unanswerable", "Unanswerable.", "  UNANSWERABLE! ", "", "   ", "..."])
def test_is_abstain_true(text):
    assert is_abstain(text)


@pytest.mark.parametrize("text", ["no", "the answer is unanswerable", "yes", "0"])
def test_is_abstain_false(text):
    assert not is_abstain(text)


@pytest.mark.parametrize(
    "reply, verdict",
    [
        ("CORRECT", True),
        ("correct", True),
        ("INCORRECT", False),
        ("Incorrect.", False),
        ("The answer is correct.", True),
        ("The answer is incorrect.", False),  # "incorrect" must not read as "correct"
        ("<think>The reference says INCORRECT but ... hmm</think>\nCORRECT", True),
        ("<think>maybe\nCORRECT?\n</think>INCORRECT", False),
        ("  \n INCORRECT\n", False),
        ("I cannot tell", None),
        ("", None),
        ("<think>CORRECT</think>", None),  # only the reasoning mentions a verdict
        ("uncorrected", None),  # no standalone verdict word
    ],
)
def test_parse_verdict(reply, verdict):
    assert parse_verdict(reply) is verdict


# --------------------------------------------------------------------------
# LLMJudge
# --------------------------------------------------------------------------


def test_judge_grades_from_the_reply():
    q = OpenQuestion(question="q", text_answer="Paris")
    assert judge("CORRECT").grade_open_question(q, "Paris")
    assert not judge("INCORRECT").grade_open_question(q, "Rome")


def test_judge_handles_reasoning_models():
    q = OpenQuestion(question="q", text_answer="Paris")
    assert judge("<think>is it right? CORRECT or INCORRECT</think>CORRECT").grade_open_question(q, "Paris")


def test_unparseable_reply_is_wrong_but_recorded_and_warned():
    j = judge("hmm, hard to say")
    q = OpenQuestion(question="q", text_answer="Paris")
    with pytest.warns(UserWarning, match="no CORRECT/INCORRECT verdict"):
        assert j.grade_open_question(q, "Paris") is False
    assert j.unparseable == ["hmm, hard to say"]


def test_judge_prompt_carries_question_reference_and_answer():
    prompts = []

    class Spy(StubLLM):
        def invoke(self, prompt):
            prompts.append(prompt)
            return super().invoke(prompt)

    LLMJudge(llm=Spy()).grade_open_question(OpenQuestion(question="Capital?", text_answer="Paris"), "Lyon")
    assert "Capital?" in prompts[0] and "Paris" in prompts[0] and "Lyon" in prompts[0]


# --------------------------------------------------------------------------
# Quiz.grade
# --------------------------------------------------------------------------


def test_grade_uses_the_judge_for_open_questions():
    quiz = open_quiz("a", "b")
    assert quiz.grade(["x", "y"], judge("CORRECT", "INCORRECT")) == [True, False]


def test_grade_needs_matching_answer_count():
    with pytest.raises(ValueError):
        open_quiz("a", "b").grade(["only one"], judge())


def test_grade_needs_a_judge_only_when_it_has_to_judge():
    with pytest.raises(ValueError, match="LLMJudge"):
        open_quiz("a").grade(["x"])
    assert open_quiz("unanswerable").grade(["unanswerable"]) == [True]  # no content to judge


def test_grade_skips_the_judge_for_abstentions():
    llm = StubLLM("CORRECT")
    j = LLMJudge(llm=llm)
    quiz = open_quiz("real answer", "unanswerable", "unanswerable", "real answer")
    # abstained on an answerable | abstained on an unanswerable | answered an unanswerable | answered
    results = quiz.grade(["unanswerable", "Unanswerable.", "made up", "real answer"], j)
    assert results == [False, True, False, True]
    assert llm.calls == 1  # only the last one needed the judge


def test_grade_closed_questions_without_a_judge():
    quiz = Quiz(questions=[ClosedQuestion(question="q", options=["a", "b", "c"], answer_index=1)])
    assert quiz.grade(["1"]) == [True]
    assert quiz.grade(["b"]) == [True]
    assert quiz.grade(["0"]) == [False]
    assert quiz.grade(["c"]) == [False]


# --------------------------------------------------------------------------
# classify / Quiz.report / QuizReport
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answerable, abstained, correct, failed, outcome",
    [
        (True, False, True, False, "TP"),
        (True, False, False, False, "WA"),  # confidently wrong
        (True, True, False, False, "FN"),  # gave up
        (False, True, True, False, "TN"),
        (False, False, False, False, "FP"),
        (True, False, False, True, "ERR"),
        (False, False, False, True, "ERR"),
    ],
)
def test_classify(answerable, abstained, correct, failed, outcome):
    assert classify(answerable, abstained, correct, failed) == outcome


def report_for(refs, answers, replies, stats=None):
    return open_quiz(*refs).report(answers, judge(*replies), stats=stats)


def test_report_outcomes():
    report = report_for(
        refs=["a", "a", "a", "unanswerable", "unanswerable"],
        answers=["a", "wrong", "unanswerable", "unanswerable", "invented"],
        replies=["CORRECT", "INCORRECT"],
    )
    assert [i.outcome for i in report.items] == ["TP", "WA", "FN", "TN", "FP"]
    assert (report.tp, report.wa, report.fn, report.tn, report.fp, report.err) == (1, 1, 1, 1, 1, 0)


def test_wrong_answers_count_against_precision():
    # the regression this suite exists for: 1 right, 9 confidently wrong must NOT read as precision 1.0
    report = report_for(
        refs=["a"] * 10,
        answers=["a"] + ["wrong"] * 9,
        replies=["CORRECT"] + ["INCORRECT"] * 9,
    )
    assert report.precision == pytest.approx(0.1)
    assert report.recall == pytest.approx(0.1)
    assert report.accuracy == pytest.approx(0.1)


def test_abstaining_costs_recall_not_precision():
    report = report_for(
        refs=["a"] * 4,
        answers=["a", "unanswerable", "unanswerable", "unanswerable"],
        replies=["CORRECT"],
    )
    assert report.precision == 1.0  # everything it chose to answer was right
    assert report.recall == 0.25


def test_failed_run_is_an_error_not_an_abstention():
    stats = [RunStats(failure="recursion limit"), RunStats(failure="error: ConnectError: down"), RunStats()]
    # references are unanswerable: an empty answer from a crashed run must not be credited as a TN
    report = report_for(
        refs=["unanswerable", "unanswerable", "unanswerable"],
        answers=["", "", "unanswerable"],
        replies=["CORRECT"],
        stats=stats,
    )
    assert [i.outcome for i in report.items] == ["ERR", "ERR", "TN"]
    assert [i.correct for i in report.items] == [False, False, True]
    assert report.err == 2 and report.tn == 1
    assert report.accuracy == pytest.approx(1 / 3)  # errors lower accuracy
    assert report.specificity == 1.0  # ...but aren't scored as answers either way


def test_failed_run_on_an_answerable_question_is_not_a_miss_in_the_matrix():
    report = report_for(refs=["a"], answers=[""], replies=["CORRECT"], stats=[RunStats(failure="recursion limit")])
    assert report.items[0].outcome == "ERR" and report.fn == 0 and report.confusion_matrix() == [[0, 0], [0, 0]]


def test_early_abstentions_count_only_quick_give_ups_on_answerable_questions():
    stats = [RunStats(tool_calls=1), RunStats(tool_calls=3), RunStats(tool_calls=4), RunStats(tool_calls=1), RunStats(tool_calls=1)]
    report = report_for(
        refs=["a", "a", "a", "a", "unanswerable"],
        answers=["unanswerable", "unanswerable", "unanswerable", "wrong", "unanswerable"],
        replies=["INCORRECT"],
        stats=stats,
    )
    assert [i.outcome for i in report.items] == ["FN", "FN", "FN", "WA", "TN"]
    assert report.early_abstentions() == 2  # <= 3 tool calls; a wrong answer or a right abstention isn't one
    assert report.early_abstentions(max_tool_calls=0) == 0
    assert report.early_abstentions(max_tool_calls=10) == 3
    assert "2 of the abstentions after <= 3 tool calls" in str(report)


def test_confusion_matrix_layout():
    report = report_for(
        refs=["a", "a", "a", "unanswerable", "unanswerable"],
        answers=["a", "wrong", "unanswerable", "unanswerable", "invented"],
        replies=["CORRECT", "INCORRECT"],
    )
    assert report.confusion_matrix() == [[1, 1], [2, 1]]  # [[TN, FP], [FN + WA, TP]]
    assert set(report.metrics()) == {"accuracy", "precision", "recall", "specificity", "f1"}


def test_empty_report_has_zero_metrics():
    report = QuizReport(items=[])
    assert report.total == 0 and all(v == 0.0 for v in report.metrics().values())


def test_report_text_mentions_the_breakdown():
    text = str(report_for(refs=["a", "a"], answers=["wrong", ""], replies=["INCORRECT"]))
    assert "1 abstained, 1 answered wrongly" in text and "failed runs (not graded): 0" in text


def test_report_custom_abstain_marker():
    quiz = open_quiz("n/a", "x")
    report = quiz.report(["n/a", "n/a"], judge("CORRECT"), abstain="n/a")
    assert [i.outcome for i in report.items] == ["TN", "FN"]


def test_report_needs_matching_stats_order_and_keeps_them():
    stats = [RunStats(steps=3, tool_calls=2, seconds=1.5)]
    item = report_for(refs=["a"], answers=["a"], replies=["CORRECT"], stats=stats).items[0]
    assert (item.stats.steps, item.stats.tool_calls, item.stats.seconds) == (3, 2, 1.5)


def test_combine_and_by_question_across_repetitions():
    quiz = open_quiz("a", "b")
    reps = [
        quiz.report(["a", "b"], judge("CORRECT"), repetition=0, case_id="c1", stats=[RunStats(steps=2)] * 2),
        quiz.report(["a", "x"], judge("CORRECT", "INCORRECT"), repetition=1, case_id="c1", stats=[RunStats(steps=4)] * 2),
    ]
    combined = QuizReport.combine(reps, wall_seconds=9.0)
    assert combined.total == 4 and combined.wall_seconds == 9.0
    first, second = combined.by_question()
    assert (first.runs, first.correct_rate, first.steps.mean) == (2, 1.0, 3.0)
    assert (second.runs, second.correct_rate) == (2, 0.5)


def test_stats_of_numbers():
    s = Stats.of([1.0, 2.0, 3.0])
    assert (s.n, s.mean, s.median, s.min, s.max) == (3, 2.0, 2.0, 1.0, 3.0)
    assert s.variance == 1.0 and s.std == 1.0
    assert Stats.of([]) == Stats() and Stats.of([5.0]).variance == 0.0


def test_item_result_defaults_are_independent():
    a, b = ItemResult(question="q", reference="r", answer="a", correct=True, outcome="TP"), ItemResult(
        question="q", reference="r", answer="a", correct=True, outcome="TP"
    )
    a.stats.steps = 7
    assert b.stats.steps == 0


def test_no_warnings_on_a_clean_run():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        report_for(refs=["a"], answers=["a"], replies=["CORRECT"])
