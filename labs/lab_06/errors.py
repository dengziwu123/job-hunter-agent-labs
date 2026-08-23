from __future__ import annotations


class WorkflowStopError(ValueError):
    def __init__(self, message: str, *, stop_reason: str) -> None:
        super().__init__(message)
        self.stop_reason = stop_reason


class BudgetExceeded(WorkflowStopError):
    pass


class ContextBudgetExceeded(WorkflowStopError):
    def __init__(self, message: str) -> None:
        super().__init__(message, stop_reason="context_budget_exceeded")
