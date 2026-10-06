"""
Question classes for evaluation purposes.
"""

from typing import List
from pydantic import BaseModel


class OpenQuestion(BaseModel):
    """A question that requires an open-ended answer."""
    question: str
    correct_answer: str


class ClosedQuestion(BaseModel):
    """A question with predefined options and a single correct answer."""
    question: str
    options: List[str]
    correct_answer: str
