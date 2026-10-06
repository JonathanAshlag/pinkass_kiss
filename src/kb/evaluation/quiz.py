"""
Quiz class for evaluation purposes.
"""

from typing import List, Union
from pydantic import BaseModel
from kb.evaluation.question import OpenQuestion, ClosedQuestion


class Quiz(BaseModel):
    """A collection of questions."""
    questions: List[Union[OpenQuestion, ClosedQuestion]]
    
    def grade(self, answers: List[str]) -> List[bool]:
        """
        Grade a set of answers against the correct answers.
        
        Args:
            answers: List of answers in the same order as the questions
            
        Returns:
            List of boolean values indicating whether each answer is correct
        """
        if len(answers) != len(self.questions):
            raise ValueError("Number of answers must match number of questions")
            
        results = []
        for i, (question, answer) in enumerate(zip(self.questions, answers)):
            if isinstance(question, OpenQuestion):
                # For open questions, we can't automatically grade - return False
                # In a real implementation, this would involve LLM comparison
                results.append(False)
            else:  # ClosedQuestion
                # Check if the answer index matches the correct answer index
                if answer.isdigit() and int(answer) < len(question.options):
                    results.append(int(answer) == question.answer_index)
                else:
                    results.append(False)
        
        return results


# For LLM-based grading, we'll create a separate class
class LLMJudge(BaseModel):
    """An LLM-based judge for evaluating answers."""
    
    def grade_open_question(self, question: OpenQuestion, answer: str) -> bool:
        """
        Grade an open-ended question using LLM comparison.
        
        This is a placeholder implementation - in practice this would call
        an actual LLM API to compare the answer with the correct answer.
        
        Args:
            question: The open question
            answer: The student's answer
            
        Returns:
            True if the answer is considered correct, False otherwise
        """
        # In a real implementation, this would use an LLM to compare answers
        # For now, we'll do a simple string comparison for demonstration
        return answer.lower().strip() == question.text_answer.lower().strip()
    
    def grade_closed_question(self, question: ClosedQuestion, answer: str) -> bool:
        """
        Grade a multiple choice question.
        
        Args:
            question: The closed question
            answer: The student's answer (as an index or text)
            
        Returns:
            True if the answer is correct, False otherwise
        """
        if answer.isdigit() and int(answer) < len(question.options):
            return int(answer) == question.answer_index
        else:
            # If answer is text, check if it matches the correct option
            if question.answer_index < len(question.options):
                return answer.strip() == question.options[question.answer_index].strip()
        return False
