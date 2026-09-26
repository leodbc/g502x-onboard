from __future__ import annotations

from dataclasses import dataclass

from g502x_onboard.application.models import (
    ApplyReview,
    ErrorCode,
    PersistentOperationKind,
    PersistentPhase,
    PreparedOperation,
    PrivacyClass,
    RestoreBackupReview,
    RestoreBaselineReview,
)
from .model import (
    OperationAction,
    Route,
    TerminalOutcome,
    TuiModel,
    privacy_allows,
)


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
    review_lines: tuple[str, ...]
    review_acknowledged: bool
    confirmation_visible: bool
    confirmation_input: str
    required_confirmation_phrase: str | None
    confirmation_matches: bool
    cancellation_available: bool
    non_cancellable: bool
    worker_fault_unresolved: bool
    safety_label: str | None
    terminal_outcome: TerminalOutcome | None
    terminal_label: str | None
    error_code: ErrorCode | None
    message: str | None
    detail: str | None
    help_open: bool
    disclosure_message: str | None
    config_path_input: str
    backup_path_input: str
    profile_target_input: str
    profile_confirmation_input: str
    progress_percent: int | None = None


def _privacy_label(value: PrivacyClass) -> str:
    if value is PrivacyClass.SHAREABLE:
        return "SHAREABLE"
    if value is PrivacyClass.LOCAL_SENSITIVE:
        return "LOCAL SENSITIVE"
    return "PRIVATE"


def _review_lines(prepared: PreparedOperation | None) -> tuple[str, ...]:
    if prepared is None:
        return ()
    review = prepared.review
    common = [
        f"Operation: {prepared.kind.value}",
        f"Target digest: {prepared.target_digest}",
        f"Compatibility: {prepared.compatibility.architecture} / {prepared.compatibility.transport}",
        f"Write eligibility: {'eligible' if prepared.compatibility.write_allowed else 'read-only'}",
        f"Host guard: {'clear' if prepared.host_guard_clear else 'blocked'}",
        "Preconditions: " + (", ".join(prepared.observed_preconditions) or "none"),
        f"Exact confirmation: {prepared.required_confirmation_phrase}",
    ]
    if isinstance(review, ApplyReview):
        lines = [
            f"Config: {review.config_name}",
            "Enabled profiles: " + (", ".join(map(str, review.enabled_profiles)) or "none"),
            "Managed sectors: " + ", ".join(map(str, review.managed_sectors)),
        ]
        if review.profile_names:
            lines.append(
                "Profile names: "
                + ", ".join(f"{number}={name}" for number, name in review.profile_names)
            )
        if review.warnings:
            lines.extend(f"Warning: {warning}" for warning in review.warnings)
        return tuple(common + lines)
    if isinstance(review, RestoreBackupReview):
        return tuple(
            common
            + [
                f"Backup: {review.backup_name}",
                "Managed sectors: " + ", ".join(map(str, review.managed_sectors)),
                "Protected sectors: " + ", ".join(map(str, review.protected_sectors)),
            ]
        )
    if isinstance(review, RestoreBaselineReview):
        return tuple(
            common
            + [
                "Target: active validated baseline",
                "Managed sectors: " + ", ".join(map(str, review.managed_sectors)),
                "Protected sectors: " + ", ".join(map(str, review.protected_sectors)),
            ]
        )
    return tuple(common)


def view(model: TuiModel) -> ViewModel:
    active = model.active
    phase = active.phase if active is not None else (
        model.terminal.persistent_phase if model.terminal else None
    )
    non_cancellable = bool(active and active.non_cancellable)
    cancellation_available = bool(active and active.cancellation_available)
    worker_fault_unresolved = bool(active and active.worker_fault_unresolved)

    prepared = (
        model.prepared
        if model.prepared is not None
        and privacy_allows(model.surface_privacy, model.prepared.privacy)
        else None
    )
    last_error = (
        model.last_error
        if model.last_error is not None
        and privacy_allows(model.surface_privacy, model.last_error.privacy)
        else None
    )
    last_result = (
        model.last_result
        if model.last_result is not None
        and privacy_allows(model.surface_privacy, model.last_result.privacy)
        else None
    )
    transient_notice = (
        model.transient_notice
        if model.transient_notice is not None
        and privacy_allows(
            model.surface_privacy, model.transient_notice.privacy
        )
        else None
    )
    read_only_reason = (
        model.read_only_reason
        if model.read_only_reason is not None
        and privacy_allows(
            model.surface_privacy, model.read_only_reason.privacy
        )
        else None
    )
    disclosure = (
        model.disclosure
        if model.disclosure is not None
        and privacy_allows(model.surface_privacy, model.disclosure.privacy)
        else None
    )

    message = None
    detail = None
    error_code = model.terminal.error_code if model.terminal else None
    if last_error is not None:
        message = last_error.message
        detail = last_error.detail
        error_code = last_error.code
    elif last_result is not None:
        message = last_result.message
    elif transient_notice is not None:
        message = transient_notice.message

    terminal_outcome = model.terminal.outcome if model.terminal else None
    terminal_label = None
    if terminal_outcome is TerminalOutcome.SUCCESS:
        terminal_label = "SUCCESS"
    elif terminal_outcome is TerminalOutcome.FAILURE:
        terminal_label = "FAILURE"

    review_visible = bool(
        prepared is not None
        and active is not None
        and not active.execution_requested
        and active.phase in {
            PersistentPhase.PREPARED,
            PersistentPhase.REVIEWING,
        }
        and model.route is Route.REVIEW
    )
    confirmation_visible = bool(
        prepared is not None
        and active is not None
        and not active.execution_requested
        and active.phase is PersistentPhase.CONFIRMING
        and model.route is Route.CONFIRMATION
    )
    confirmation_matches = bool(
        confirmation_visible
        and model.confirmation_input.strip()
        == prepared.required_confirmation_phrase
    )

    safety_label = None
    if worker_fault_unresolved:
        safety_label = "UNRESOLVED ADAPTER FAULT — HARDWARE OUTCOME UNKNOWN"
    elif non_cancellable:
        safety_label = "NON-CANCELLABLE SAFETY PHASE"

    return ViewModel(
        route=model.route,
        privacy=model.surface_privacy,
        privacy_label=_privacy_label(model.surface_privacy),
        read_only=model.read_only,
        read_only_label=("READ ONLY" if model.read_only else None),
        read_only_reason=(
            read_only_reason.message
            if read_only_reason is not None
            else None
        ),
        operation_active=active is not None,
        operation_id=(str(active.operation_id) if active is not None else None),
        operation_action=(active.action if active is not None else None),
        persistent_kind=(
            active.persistent_kind
            if active is not None
            else (prepared.kind if prepared is not None else None)
        ),
        phase=phase,
        phase_label=(phase.value if phase is not None else None),
        review_visible=review_visible,
        prepared=prepared,
        review_lines=_review_lines(prepared),
        review_acknowledged=model.review_acknowledged,
        confirmation_visible=confirmation_visible,
        confirmation_input=(
            model.confirmation_input if confirmation_visible else ""
        ),
        required_confirmation_phrase=(
            prepared.required_confirmation_phrase
            if review_visible or confirmation_visible
            else None
        ),
        confirmation_matches=confirmation_matches,
        cancellation_available=cancellation_available,
        non_cancellable=non_cancellable,
        worker_fault_unresolved=worker_fault_unresolved,
        safety_label=safety_label,
        terminal_outcome=terminal_outcome,
        terminal_label=terminal_label,
        error_code=error_code,
        message=message,
        detail=detail,
        help_open=model.help_open,
        disclosure_message=(
            disclosure.message if disclosure is not None else None
        ),
        config_path_input=model.config_path_input,
        backup_path_input=model.backup_path_input,
        profile_target_input=model.profile_target_input,
        profile_confirmation_input=model.profile_confirmation_input,
        progress_percent=None,
    )
