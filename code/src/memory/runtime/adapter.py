"""Task 3 state to Task 2 retrieval context adapter."""

from __future__ import annotations

from typing import Tuple

from src.memory.retrieval.interpretation_models import ContextSource
from src.memory.task_state.models import StructuredTaskContext


class TaskRetrievalContextAdapter:
    """Thin adapter converting updated Task3 state into Task2 public retrieval inputs.

    Ensures that current turn text and freshly updated task state (e.g. new budget 4000)
    are properly reflected in the query and recent_context provided to Task2.
    """

    def adapt(
        self,
        user_input: str,
        structured_context: StructuredTaskContext,
        *,
        turn_id: str = "current-turn",
    ) -> Tuple[str, Tuple[ContextSource, ...]]:
        """Constructs (query, recent_context) for Task2 retrieval facade."""

        # Use current user turn text as the primary retrieval query
        query = user_input.strip()

        # Build recent_context sources from structured task context's active goals and constraints
        sources = []
        
        # Add turn source
        sources.append(ContextSource(source_id=f"turn:{turn_id}", text=user_input))

        # Add active goals and constraints from current updated task state
        if structured_context.goal:
            goals_text = "; ".join(item.content for item in structured_context.goal)
            sources.append(ContextSource(source_id="task3:active_goals", text=goals_text))

        if structured_context.constraints:
            constraints_text = "; ".join(item.content for item in structured_context.constraints)
            sources.append(ContextSource(source_id="task3:active_constraints", text=constraints_text))

        return query, tuple(sources)
