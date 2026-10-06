"""
Quiz report: a confusion matrix and the usual metrics derived from it.

Positive = "the question has an answer" (its reference isn't the abstain marker).
An answer is a *hit* when it is judged correct. Every question lands in exactly one cell:

- TP: answerable, answered correctly
- FN: answerable, but the agent abstained or answered wrongly
- FP: unanswerable, but the agent gave an answer instead of abstaining
- TN: unanswerable, and the agent abstained

Metrics follow scikit-learn's definitions (ratios with a zero denominator are 0.0), and
`confusion_matrix()` uses its layout: rows = reference, columns = prediction,
`[[TN, FP], [FN, TP]]`.
"""

import re
import statistics
from typing import Dict, List, Literal, Optional, Sequence, Tuple

from pydantic import BaseModel

ABSTAIN = "unanswerable"

Outcome = Literal["TP", "FN", "FP", "TN"]


def is_abstain(text: str, marker: str = ABSTAIN) -> bool:
    """True if `text` is the abstain marker (case/punctuation-insensitive) or empty."""
    cleaned = re.sub(r"[^\w\s]", "", text.lower()).strip()
    return not cleaned or cleaned == marker


class RunStats(BaseModel):
    """Cost of producing one answer: model steps, tool calls and wall-clock seconds."""
    steps: int = 0  # model turns (LLM calls) the agent took
    tool_calls: int = 0
    seconds: float = 0.0


class Stats(BaseModel):
    """Descriptive statistics over a list of numbers. `variance`/`std` are the sample
    (n-1) estimates, 0.0 when there are fewer than two values."""
    n: int = 0
    mean: float = 0.0
    variance: float = 0.0
    std: float = 0.0
    min: float = 0.0
    median: float = 0.0
    max: float = 0.0

    @classmethod
    def of(cls, values: Sequence[float]) -> "Stats":
        if not values:
            return cls()
        variance = statistics.variance(values) if len(values) > 1 else 0.0
        return cls(
            n=len(values),
            mean=statistics.fmean(values),
            variance=variance,
            std=variance**0.5,
            min=min(values),
            median=statistics.median(values),
            max=max(values),
        )

    def __str__(self) -> str:
        return (
            f"mean {self.mean:.2f}  var {self.variance:.2f}  std {self.std:.2f}  "
            f"min {self.min:.2f}  median {self.median:.2f}  max {self.max:.2f}"
        )


class ItemResult(BaseModel):
    """One graded answer (one repetition of one question)."""
    question: str
    reference: str
    answer: str
    correct: bool
    outcome: Outcome
    question_index: int = 0  # position in its quiz
    repetition: int = 0
    case_id: str = ""  # which quiz/knowledge base, when a dataset has several
    stats: RunStats = RunStats()


class QuestionSummary(BaseModel):
    """All repetitions of one question."""
    case_id: str
    question_index: int
    question: str
    reference: str
    runs: int
    correct_rate: float
    steps: Stats
    seconds: Stats


class QuizReport(BaseModel):
    """Per-question results plus the confusion matrix and metrics over them."""
    items: List[ItemResult]
    wall_seconds: Optional[float] = None  # elapsed time of the whole (parallel) run

    def _count(self, outcome: Outcome) -> int:
        return sum(i.outcome == outcome for i in self.items)

    @property
    def tp(self) -> int:
        return self._count("TP")

    @property
    def fn(self) -> int:
        return self._count("FN")

    @property
    def fp(self) -> int:
        return self._count("FP")

    @property
    def tn(self) -> int:
        return self._count("TN")

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def accuracy(self) -> float:
        """Fraction of questions handled correctly (answered right, or rightly abstained)."""
        return _ratio(self.tp + self.tn, self.total)

    @property
    def precision(self) -> float:
        """TP / (TP + FP): of the confident hits, how many weren't hallucinated answers."""
        return _ratio(self.tp, self.tp + self.fp)

    @property
    def recall(self) -> float:
        """TP / (TP + FN): of the answerable questions, how many were answered correctly."""
        return _ratio(self.tp, self.tp + self.fn)

    @property
    def specificity(self) -> float:
        """TN / (TN + FP): of the unanswerable questions, how many were abstained on."""
        return _ratio(self.tn, self.tn + self.fp)

    @property
    def f1(self) -> float:
        return _ratio(2 * self.precision * self.recall, self.precision + self.recall)

    @property
    def runtime(self) -> Stats:
        """Per-answer wall-clock seconds."""
        return Stats.of([i.stats.seconds for i in self.items])

    @property
    def steps(self) -> Stats:
        """Per-answer model steps."""
        return Stats.of([i.stats.steps for i in self.items])

    @property
    def tool_calls(self) -> Stats:
        return Stats.of([i.stats.tool_calls for i in self.items])

    def by_question(self) -> List[QuestionSummary]:
        """One summary per question, aggregating its repetitions."""
        groups: Dict[Tuple[str, int], List[ItemResult]] = {}
        for item in self.items:
            groups.setdefault((item.case_id, item.question_index), []).append(item)
        return [
            QuestionSummary(
                case_id=case_id,
                question_index=index,
                question=group[0].question,
                reference=group[0].reference,
                runs=len(group),
                correct_rate=sum(i.correct for i in group) / len(group),
                steps=Stats.of([i.stats.steps for i in group]),
                seconds=Stats.of([i.stats.seconds for i in group]),
            )
            for (case_id, index), group in groups.items()
        ]

    def confusion_matrix(self) -> list[list[int]]:
        """[[TN, FP], [FN, TP]] (scikit-learn layout)."""
        return [[self.tn, self.fp], [self.fn, self.tp]]

    def metrics(self) -> dict[str, float]:
        return {
            "accuracy": self.accuracy,
            "precision": self.precision,
            "recall": self.recall,
            "specificity": self.specificity,
            "f1": self.f1,
        }

    @classmethod
    def combine(
        cls, reports: List["QuizReport"], wall_seconds: Optional[float] = None
    ) -> "QuizReport":
        """One report over several reports' answers (e.g. all quizzes/repetitions of a
        dataset). `wall_seconds` is the elapsed time of the run that produced them."""
        return cls(items=[i for r in reports for i in r.items], wall_seconds=wall_seconds)

    def __str__(self) -> str:
        m = self.metrics()
        lines = [
            f"answers: {self.total}",
            "confusion matrix (rows = reference, cols = prediction)",
            "                    pred: abstain/miss   pred: hit",
            f"  ref: unanswerable {self.tn:>14}  {self.fp:>14}",
            f"  ref: answerable   {self.fn:>14}  {self.tp:>14}",
            "  ".join(f"{k}: {v:.2f}" for k, v in m.items()),
            f"runtime s/answer: {self.runtime}",
            f"steps/answer:     {self.steps}",
            f"tool calls/answer: {self.tool_calls}",
        ]
        if self.wall_seconds is not None:
            lines.append(f"wall-clock: {self.wall_seconds:.1f}s for {self.total} answers")
        return "\n".join(lines)


def _ratio(num: float, den: float) -> float:
    return num / den if den else 0.0


def classify(answerable: bool, abstained: bool, correct: bool) -> Outcome:
    if answerable:
        return "TP" if correct and not abstained else "FN"
    return "TN" if abstained else "FP"
