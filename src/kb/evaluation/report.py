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
from typing import List, Literal

from pydantic import BaseModel

ABSTAIN = "unanswerable"

Outcome = Literal["TP", "FN", "FP", "TN"]


def is_abstain(text: str, marker: str = ABSTAIN) -> bool:
    """True if `text` is the abstain marker (case/punctuation-insensitive) or empty."""
    cleaned = re.sub(r"[^\w\s]", "", text.lower()).strip()
    return not cleaned or cleaned == marker


class ItemResult(BaseModel):
    """One graded question."""
    question: str
    reference: str
    answer: str
    correct: bool
    outcome: Outcome


class QuizReport(BaseModel):
    """Per-question results plus the confusion matrix and metrics over them."""
    items: List[ItemResult]

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
    def combine(cls, reports: List["QuizReport"]) -> "QuizReport":
        """One report over several reports' questions (e.g. all quizzes of a dataset)."""
        return cls(items=[i for r in reports for i in r.items])

    def __str__(self) -> str:
        m = self.metrics()
        lines = [
            f"questions: {self.total}",
            "confusion matrix (rows = reference, cols = prediction)",
            "                    pred: abstain/miss   pred: hit",
            f"  ref: unanswerable {self.tn:>14}  {self.fp:>14}",
            f"  ref: answerable   {self.fn:>14}  {self.tp:>14}",
            "  ".join(f"{k}: {v:.2f}" for k, v in m.items()),
        ]
        return "\n".join(lines)


def _ratio(num: float, den: float) -> float:
    return num / den if den else 0.0


def classify(answerable: bool, abstained: bool, correct: bool) -> Outcome:
    if answerable:
        return "TP" if correct and not abstained else "FN"
    return "TN" if abstained else "FP"
