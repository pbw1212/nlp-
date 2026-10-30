"""Pin worker-visible task context to the admitted preparation snapshot."""

from dataclasses import replace
from typing import Any


class IsolatedWorkerRuntime:
    """Reconcile real tools durably without exposing undeclared peer state.

    Task 3 still receives every actual tool result through the existing runtime.
    Workers see their initial task/memory projection plus their own explicit tool
    observations and declared verified dependencies, rather than the mutable
    global ledger that another branch may have updated in the meantime.
    """

    def __init__(self, runtime: Any, prepared: Any) -> None:
        self.runtime, self.prepared = runtime, prepared

    def process_tool_result(self, result: Any, task_id: str) -> Any:
        outcome = self.runtime.process_tool_result(result, task_id=task_id)
        if outcome.context is None:
            return outcome
        context = replace(
            outcome.context,
            working_context=self.prepared.working_context,
            structured_task_context=self.prepared.structured_task_context,
            evidence_bundle=self.prepared.evidence_bundle,
            task_analysis=self.prepared.task_analysis,
            warnings=self.prepared.warnings
            + (
                ("本次工具的任务状态处理有警告，真实工具结果另见回执。",)
                if outcome.context.warnings
                else ()
            ),
        )
        return replace(outcome, context=context)
