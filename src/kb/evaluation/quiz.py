"""
Quiz class for evaluation purposes.
"""

from typing import List, Optional, Union
from pydantic import BaseModel
from kb.evaluation.question import OpenQuestion, ClosedQuestion
from kb.evaluation.report import ABSTAIN, ItemResult, QuizReport, classify, is_abstain

JUDGE_PROMPT = (
    "You are grading an answer to a question against a reference answer. The answer is "
    "correct if it conveys the same information as the reference (wording, formatting, "
    "and extra brevity or detail that doesn't contradict the reference don't matter). "
    "Reply with exactly one word: CORRECT or INCORRECT.\n\n"
    "Question: {question}\nReference answer: {reference}\nAnswer to grade: {answer}"
)


class LLMJudge:
    """An LLM-based judge for evaluating answers, backed by langchain's `ChatOpenAI`.

    Pass a ready-made chat model as `llm` (any langchain chat model works, which keeps
    tests injectable), or let it build a `ChatOpenAI` from `model` / `base_url` /
    `api_key` (the key falls back to `OPENAI_API_KEY`).
    """

    def __init__(
        self,
        llm=None,
        *,
        model: str = "gpt-4o-mini",
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        if llm is None:
            from langchain_openai import ChatOpenAI

            llm = ChatOpenAI(model=model, base_url=base_url, api_key=api_key, temperature=0)
        self.llm = llm

    def grade_open_question(self, question: OpenQuestion, answer: str) -> bool:
        """Ask the LLM whether `answer` matches the question's reference `text_answer`."""
        verdict = self.llm.invoke(
            JUDGE_PROMPT.format(
                question=question.question, reference=question.text_answer, answer=answer
            )
        )
        # "INCORRECT" contains "CORRECT", so test for the negative first
        text = str(verdict.text).strip().upper()
        return text.startswith("CORRECT")

    def grade_closed_question(self, question: ClosedQuestion, answer: str) -> bool:
        """
        Grade a multiple choice question (no LLM needed).

        Args:
            question: The closed question
            answer: The student's answer (as an index or text)

        Returns:
            True if the answer is correct, False otherwise
        """
        return _grade_closed(question, answer)


def _grade_closed(question: ClosedQuestion, answer: str) -> bool:
    answer = answer.strip()
    if answer.isdigit() and int(answer) < len(question.options):
        return int(answer) == question.answer_index
    # If answer is text, check if it matches the correct option
    if question.answer_index < len(question.options):
        return answer == question.options[question.answer_index].strip()
    return False


class Quiz(BaseModel):
    """A collection of questions."""
    questions: List[Union[OpenQuestion, ClosedQuestion]]

    def grade(self, answers: List[str], judge: Optional[LLMJudge] = None) -> List[bool]:
        """
        Grade a set of answers against the correct answers.

        Args:
            answers: List of answers in the same order as the questions
            judge: Grades open questions; required if the quiz has any

        Returns:
            List of boolean values indicating whether each answer is correct
        """
        if len(answers) != len(self.questions):
            raise ValueError("Number of answers must match number of questions")

        results = []
        for question, answer in zip(self.questions, answers):
            if isinstance(question, OpenQuestion):
                if judge is None:
                    raise ValueError("an LLMJudge is required to grade open questions")
                results.append(judge.grade_open_question(question, answer))
            else:  # ClosedQuestion
                results.append(_grade_closed(question, answer))

        return results

    def report(
        self,
        answers: List[str],
        judge: Optional[LLMJudge] = None,
        *,
        results: Optional[List[bool]] = None,
        abstain: str = ABSTAIN,
    ) -> QuizReport:
        """
        Grade `answers` and build a confusion-matrix report (see `kb.evaluation.report`).

        Args:
            answers: List of answers in the same order as the questions
            judge: Grades open questions (as in `grade`)
            results: Already-computed `grade()` output, to avoid judging twice
            abstain: The marker meaning "unanswerable", for references and answers
        """
        if results is None:
            results = self.grade(answers, judge)
        items = []
        for question, answer, correct in zip(self.questions, answers, results):
            if isinstance(question, OpenQuestion):
                reference = question.text_answer
                answerable = not is_abstain(reference, abstain)
            else:
                reference = question.options[question.answer_index]
                answerable = True
            abstained = is_abstain(answer, abstain)
            if not answerable:
                correct = abstained  # the right answer to an unanswerable question
            items.append(
                ItemResult(
                    question=question.question,
                    reference=reference,
                    answer=answer,
                    correct=correct,
                    outcome=classify(answerable, abstained, correct),
                )
            )
        return QuizReport(items=items)
