"""
Quiz class for evaluation purposes.
"""

from typing import List, Union
from pydantic import BaseModel
from kb.evaluation.question import OpenQuestion, ClosedQuestion


class Quiz(BaseModel):
    """A collection of questions."""
    questions: List[Union[OpenQuestion, ClosedQuestion]]
