from __future__ import annotations

from dataclasses import dataclass

from g502x_onboard.application.models import (
    ErrorCode,
    PersistentOperationKind,
    PersistentPhase,
    PreparedOperation,
    PrivacyClass,
)
from .model import OperationAction, Route, TerminalOutcome, TuiModel


@dataclass(frozen=True)
class ViewModel:
    route: Route
    privacy: PrivacyClass
    privacy_label: str
    read_only: bool
    read_only_label: str | None
    read_only_reason: str | None
    operation_active: bool
    operation_id: str | None
    operation_action: OperationAction | None
    persistent_kind: PersistentOperationKind | None
    phase: PersistentPhase | None
    phase_label: str | None
    review_visible: bool
    prepared: PreparedOperation | None
    review_acknowledged: bool
    confirmation_visible: bool
    confirmation_input: str
    required_confirmation_phrase: str | None
    confirmation_matches: bool
    cancellation_available: bool
    non_cancellable: bool
    safety_label: str | None
    terminal_outcome: TerminalOutcome | None
    terminal_label: str | None
    error_code: ErrorCode | None
    message: str | None
    detail: str | None
    help_open: bool
    disclosure_message: str | None
    progress_percent: int | None = None


def _privacy_label(value: PrivacyClass) -> str:
    if value is PrivacyClass.SHAREABLE:
        return "SHAREABLE"
    if value is PrivacyClass.LOCAL_SENSITIVE:
        return "LOCAL SENSITIVE"
    return "PRIVATE"


def view(model: TuiModel) -> ViewModel:
    active = model.active
    phase = active.phase if active is not None else (
        model.terminal.persistent_phase if model.terminal else None
    )
    non_cancellable = bool(active and active.non_cancellable)
    cancellation_available = bool(active and active.cancellation_available)

    message = None
    detail = None
    error_code = model.terminal.error_code if model.terminal else None
    if model.last_error is not None:
        message = model.last_error.message
        detail = model.last_error.detail
        error_code = model.last_error.code
    elif model.last_result is not None:
        message = model.last_result.message
    elif model.transient_notice is not None:
        message = model.transient_notice.message

    terminal_outcome = model.terminal.outcome if model.terminal else None
    terminal_label = None
    if terminal_outcome is TerminalOutcome.SUCCESS:
        terminal_label = "SUCCESS"
    elif terminal_outcome is TerminalOutcome.FAILURE:
        terminal_label = "FAILURE"

    review_visible = bool(
        model.prepared is not None
        and active is not None
        and not active.execution_requested
        and active.phase in {
            PersistentPhase.PREPARED,
            PersistentPhase.REVIEWING,
        }
        and model.route is Route.REVIEW
    )
    confirmation_visible = bool(
        model.prepared is not None
        and active is not None
        and not active.execution_requested
        and active.phase is PersistentPhase.CONFIRMING
        and model.route is Route.CONFIRMATION
    )
    confirmation_matches = bool(
        confirmation_visible
        and model.confirmation_input.strip()
        == model.prepared.required_confirmation_phrase
    )

    return ViewModel(
        route=model.route,
        privacy=model.surface_privacy,
        privacy_label=_privacy_label(model.surface_privacy),
        read_only=model.read_only,
        read_only_label=("READ ONLY" if model.read_only else None),
        read_only_reason=(
            model.read_only_reason.message
            if model.read_only_reason is not None
            else None
        ),
        operation_active=active is not None,
        operation_id=(str(active.operation_id) if active is not None else None),
        operation_action=(active.action if active is not None else None),
        persistent_kind=(
            active.persistent_kind
            if active is not None
            else (model.prepared.kind if model.prepared is not None else None)
        ),
        phase=phase,
        phase_label=(phase.value if phase is not None else None),
        review_visible=review_visible,
        prepared=model.prepared,
        review_acknowledged=model.review_acknowledged,
        confirmation_visible=confirmation_visible,
        confirmation_input=(
            model.confirmation_input if confirmation_visible else ""
        ),
        required_confirmation_phrase=(
            model.prepared.required_confirmation_phrase
            if review_visible or confirmation_visible
            else None
        ),
        confirmation_matches=confirmation_matches,
        cancellation_available=cancellation_available,
        non_cancellable=non_cancellable,
        safety_label=(
            "NON-CANCELLABLE SAFETY PHASE" if non_cancellable else None
        ),
        terminal_outcome=terminal_outcome,
        terminal_label=terminal_label,
        error_code=error_code,
        message=message,
        detail=detail,
        help_open=model.help_open,
        disclosure_message=(
            model.disclosure.message if model.disclosure is not None else None
        ),
        progress_percent=None,
    )
