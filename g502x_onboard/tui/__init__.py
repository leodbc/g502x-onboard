"""Pure, framework-free TUI state engine for the v0.2.0 adapter."""

from .model import (
    FocusIntent,
    ForegroundOperation,
    OperationAction,
    OperationId,
    PresentationPayload,
    Route,
    TerminalOutcome,
    TerminalState,
    TuiModel,
)
from .update import update
from .view import ViewModel, view

__all__ = [
    "FocusIntent",
    "ForegroundOperation",
    "OperationAction",
    "OperationId",
    "PresentationPayload",
    "Route",
    "TerminalOutcome",
    "TerminalState",
    "TuiModel",
    "ViewModel",
    "update",
    "view",
]
