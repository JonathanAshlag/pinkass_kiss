"""
Question classes for evaluation purposes.
"""

from typing import List
from pydantic import BaseModel


class OpenQuestion(BaseModel):
    """A question that requires an open-ended answer."""
    question: str
    text_answer: str


class ClosedQuestion(BaseModel):
    """A question with predefined options and a single correct answer."""
    question: str
    options: List[str]
    answer_index: int
