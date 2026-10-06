"""
Quiz class for evaluation purposes.
"""

import re
import warnings
from typing import List, Optional, Union
from pydantic import BaseModel
from kb.evaluation.question import OpenQuestion, ClosedQuestion
from kb.evaluation.report import ABSTAIN, ItemResult, QuizReport, RunStats, classify, is_abstain

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
        # replies with neither CORRECT nor INCORRECT (graded as wrong); check after a run
        self.unparseable: list[str] = []

    def grade_open_question(self, question: OpenQuestion, answer: str) -> bool:
        """Ask the LLM whether `answer` matches the question's reference `text_answer`.

        A reply with no CORRECT/INCORRECT verdict grades as wrong, but is recorded in
        `self.unparseable` (and warned about) so a broken judge is visible, not silent."""
        reply = str(
            self.llm.invoke(
                JUDGE_PROMPT.format(
                    question=question.question, reference=question.text_answer, answer=answer
                )
            ).text
        )
        verdict = parse_verdict(reply)
        if verdict is None:
            self.unparseable.append(reply)
            warnings.warn(f"judge reply had no CORRECT/INCORRECT verdict: {reply[:80]!r}")
            return False
        return verdict

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


def parse_verdict(reply: str) -> Optional[bool]:
    """True/False for the first CORRECT/INCORRECT word in a judge reply (a reasoning
    model's <think> block is ignored), None if there is neither."""
    reply = re.sub(r"<think>.*?</think>", "", reply, flags=re.DOTALL | re.IGNORECASE)
    match = re.search(r"\b(INCORRECT|CORRECT)\b", reply, flags=re.IGNORECASE)
    return None if match is None else match.group(1).upper() == "CORRECT"


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

    def grade(
        self, answers: List[str], judge: Optional[LLMJudge] = None, *, abstain: str = ABSTAIN
    ) -> List[bool]:
        """
        Grade a set of answers against the correct answers.

        Args:
            answers: List of answers in the same order as the questions
            judge: Grades open questions; required if the quiz has any (abstentions are
                graded without it)
            abstain: The marker meaning "unanswerable"

        Returns:
            List of boolean values indicating whether each answer is correct
        """
        if len(answers) != len(self.questions):
            raise ValueError("Number of answers must match number of questions")

        results = []
        for question, answer in zip(self.questions, answers):
            if isinstance(question, OpenQuestion):
                if is_abstain(answer, abstain) or is_abstain(question.text_answer, abstain):
                    # no judge call: an abstention only matches an expected abstention
                    results.append(
                        is_abstain(answer, abstain) and is_abstain(question.text_answer, abstain)
                    )
                    continue
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
        stats: Optional[List[RunStats]] = None,
        repetition: int = 0,
        case_id: str = "",
        abstain: str = ABSTAIN,
    ) -> QuizReport:
        """
        Grade `answers` and build a confusion-matrix report (see `kb.evaluation.report`).

        Args:
            answers: List of answers in the same order as the questions
            judge: Grades open questions (as in `grade`)
            results: Already-computed `grade()` output, to avoid judging twice
            stats: Per-answer cost (steps, tool calls, seconds), same order as the answers
            repetition: Which repetition of the quiz these answers are
            case_id: Label for the quiz, to tell several quizzes apart when combining
            abstain: The marker meaning "unanswerable", for references and answers
        """
        if results is None:
            results = self.grade(answers, judge, abstain=abstain)
        stats = stats or [RunStats()] * len(answers)
        items = []
        for index, (question, answer, correct, run) in enumerate(
            zip(self.questions, answers, results, stats)
        ):
            if isinstance(question, OpenQuestion):
                reference = question.text_answer
                answerable = not is_abstain(reference, abstain)
            else:
                reference = question.options[question.answer_index]
                answerable = True
            failed = run.failure is not None
            abstained = is_abstain(answer, abstain) and not failed  # a failed run isn't an abstention
            if not answerable:
                correct = abstained  # the right answer to an unanswerable question
            if failed:
                correct = False
            items.append(
                ItemResult(
                    question=question.question,
                    reference=reference,
                    answer=answer,
                    correct=correct,
                    outcome=classify(answerable, abstained, correct, failed),
                    question_index=index,
                    repetition=repetition,
                    case_id=case_id,
                    stats=run,
                )
            )
        return QuizReport(items=items)
