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
    ReadTruth,
    Route,
    TerminalOutcome,
    TuiModel,
    privacy_allows,
)


@dataclass(frozen=True)
class ViewModel:
    route: Route
    route_title: str
    privacy: PrivacyClass
    privacy_label: str
    read_truth: ReadTruth
    primary_lines: tuple[str, ...]
    help_lines: tuple[str, ...]
    technical_open: bool
    technical_lines: tuple[str, ...]
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
    writing_started: bool
    reconciliation_completed: bool
    post_validation_completed: bool
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


def _route_title(route: Route) -> str:
    return {
        Route.HOME: "Home",
        Route.CONFIGURATION: "Configuration",
        Route.BACKUP_RESTORE: "Backup & Restore",
        Route.DIAGNOSTICS: "Diagnostics",
        Route.OPERATION: "Active operation",
        Route.REVIEW: "Review",
        Route.CONFIRMATION: "Confirmation",
        Route.RESULT: "Result",
    }[route]


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


def _profile_label(profile: int | None) -> str | None:
    if profile is None:
        return None
    if profile == 1:
        return "Profile 1 — SAFE"
    return f"Profile {profile}"


def _primary_lines(model: TuiModel) -> tuple[str, ...]:
    if model.route is Route.HOME:
        if model.read_truth is ReadTruth.NEVER_READ:
            return (
                "[ ] Device state not read yet",
                "Read the current onboard state before making decisions from this screen.",
                "Reading state does not modify the mouse.",
                "R Read state   C Configuration   B Backup & Restore   D Diagnostics",
            )
        if model.read_truth is ReadTruth.READING:
            return (
                "[>] Reading current onboard state",
                "Reading state does not modify the mouse.",
                "No background polling is active.",
            )
        if model.read_truth is ReadTruth.READ_FAILED:
            return (
                "[X] Device state could not be read",
                "The application could not obtain a usable device state from this read.",
                "Check the connection and try again.",
                "Open Help or Diagnostics for additional information available to this privacy surface.",
                "R Try again   ? Help   Q Exit",
            )
        state = model.read_state
        if state is None:
            return (
                "[X] Device state could not be read",
                "Typed read state is unavailable. Refresh before making device-state decisions.",
            )
        device = state.device_name or "Supported device available"
        lines = [f"[OK] {device}", "[OK] State read successfully"]
        if model.read_only:
            lines.append("[!] This device is read-only")
        profile = _profile_label(state.active_profile)
        if profile:
            lines.append(f"Current profile     {profile}")
        if state.enabled_profiles:
            lines.append(
                "Enabled profiles     " + ", ".join(map(str, state.enabled_profiles))
            )
        lines.extend(
            [
                "No operation is pending.",
                "C Configuration   B Backup & Restore   D Diagnostics   R Refresh",
            ]
        )
        return tuple(lines)

    if model.route is Route.CONFIGURATION:
        return (
            "Configuration",
            "Plan a local configuration, prepare an apply, or switch the active profile.",
            "Apply keeps the existing Prepare -> Review -> Confirm -> execution authority.",
            "Paths and user-authored configuration are LOCAL SENSITIVE.",
            "N Plan   A Apply   S Switch profile   R Refresh   T Technical details",
        )
    if model.route is Route.BACKUP_RESTORE:
        return (
            "Backup & Restore",
            "Restore an existing backup or the active validated baseline.",
            "This area does not create a new manual backup operation.",
            "B Restore backup   L Restore baseline   R Refresh   T Technical details",
        )
    if model.route is Route.DIAGNOSTICS:
        return (
            "Diagnostics",
            "Read-oriented diagnostics and privacy-governed technical disclosure.",
            "Opening this area performs zero hardware reads.",
            "P Probe   V Validate   G Public report   R Refresh/status",
        )

    active = model.active
    if model.route is Route.OPERATION and active is not None:
        if active.action is OperationAction.REFRESH:
            return (
                "[>] Reading current onboard state",
                "Reading state does not modify the mouse.",
            )
        if active.worker_fault_unresolved:
            return (
                "[!] HARDWARE OUTCOME UNKNOWN",
                "Keep this process open. Do not retry while the outcome is unknown.",
                "Cancellation is unavailable.",
            )
        if active.non_cancellable:
            return (
                "[>] Writing/verification is still in progress",
                "[!] Cancellation is unavailable",
                "Keep this process open until authoritative completion.",
            )
        return (
            f"[>] {active.action.value.replace('-', ' ').title()} in progress",
            (
                "Cooperative cancellation is available."
                if active.cancellation_available
                else "Cooperative cancellation is unavailable."
            ),
        )

    if model.route is Route.REVIEW:
        return (
            "[ ] Review the exact prepared operation",
            "Preparation/review has not written persistent state.",
        )
    if model.route is Route.CONFIRMATION:
        return (
            "[ ] Exact confirmation required",
            "Confirmation requests execution-time revalidation; it does not bypass safety checks.",
        )
    if model.route is Route.RESULT:
        if model.terminal is not None and model.terminal.outcome is TerminalOutcome.SUCCESS:
            return ("[DONE] Operation completed",)
        if model.terminal is not None:
            return ("[FAIL] Operation did not complete successfully",)
        return ("Result",)
    return ()


def _help_lines(model: TuiModel) -> tuple[str, ...]:
    active = model.active
    if model.route is Route.HOME:
        read_text = {
            ReadTruth.NEVER_READ: "No physical state has been read in this session.",
            ReadTruth.READING: "An explicit foreground read is in progress.",
            ReadTruth.READ_OK: "Home shows the last explicit successful read, not live polling.",
            ReadTruth.READ_FAILED: "The latest explicit read failed; Home does not infer its cause from private text.",
        }[model.read_truth]
        return (
            "HELP — Home",
            read_text,
            "R explicitly reads current state and does not itself modify onboard state.",
            "C opens Configuration; B opens Backup & Restore; D opens Diagnostics.",
            "Writing has not started from Home.",
            "No operation is cancellable from idle Home.",
            "Q exits only when no foreground operation owns hardware state.",
        )
    if model.route is Route.CONFIGURATION:
        return (
            "HELP — Configuration",
            "Plan is local/read-only with respect to hardware.",
            "Apply uses Prepare -> Review -> Confirm -> execution-time revalidation.",
            "Profile switch remains a volatile mutation with its exact confirmation phrase.",
            "No hidden refresh occurs when this area opens.",
            "Esc returns to Home when idle; active operation cancellation follows application authority.",
        )
    if model.route is Route.BACKUP_RESTORE:
        return (
            "HELP — Backup & Restore",
            "Only existing Restore backup and Restore baseline operations are exposed.",
            "Backup paths are LOCAL SENSITIVE.",
            "Nothing is written merely by opening this area.",
            "Esc returns to Home when idle; active operation cancellation follows application authority.",
        )
    if model.route is Route.DIAGNOSTICS:
        return (
            "HELP — Diagnostics",
            "Probe, Validate, Refresh/status and Public report are existing read-oriented tasks.",
            "Opening Diagnostics or Help performs zero hardware calls.",
            "SHAREABLE, LOCAL SENSITIVE and PRIVATE remain distinct privacy surfaces.",
            "Technical details are local presentation, not a facade operation.",
            "Esc returns to Home when idle.",
        )
    if model.route is Route.REVIEW:
        return (
            "HELP — Review",
            "This is an immutable prepared intent; preparation/review has not written persistent state.",
            "Acknowledge the exact review before confirmation.",
            "Esc requests cancellation/abandonment only where current application authority allows it.",
        )
    if model.route is Route.CONFIRMATION:
        return (
            "HELP — Confirmation",
            "Nothing has been written yet.",
            "Type exactly the application-supplied phrase; surrounding-whitespace handling is unchanged.",
            "Confirmation triggers execution-time revalidation rather than bypassing safety checks.",
            "Esc requests cancellation before write only where current authority allows it.",
        )
    if model.route is Route.OPERATION and active is not None:
        if active.non_cancellable or active.worker_fault_unresolved:
            return (
                "HELP — Active operation",
                "Persistent writing or verification has begun or may have begun.",
                "Cooperative cancellation is unavailable.",
                "Q/Esc do not terminate the active transaction or fabricate cancellation.",
                "Await authoritative reconciliation/completion and do not retry an unresolved outcome.",
            )
        return (
            "HELP — Active operation",
            "A foreground operation owns hardware-operation precedence.",
            "No second hardware operation or Refresh may start.",
            (
                "Cooperative cancellation is currently available."
                if active.cancellation_available
                else "Cooperative cancellation is currently unavailable."
            ),
        )
    return (
        "HELP — Result",
        "Review the authoritative result and its privacy-safe primary message.",
        "T opens permitted Technical details.",
        "Esc returns to Home.",
    )


def _technical_lines(
    model: TuiModel,
    *,
    error_code: ErrorCode | None,
) -> tuple[str, ...]:
    lines = [
        "TECHNICAL DETAILS",
        f"Privacy: {_privacy_label(model.surface_privacy)}",
        f"Route: {model.route.value}",
        f"Read truth: {model.read_truth.value}",
    ]
    if model.read_state is not None:
        lines.append(f"Read source: {model.read_state.source.value}")
        lines.append(f"Active profile: {model.read_state.active_profile}")
        lines.append(
            "Enabled profiles: "
            + (", ".join(map(str, model.read_state.enabled_profiles)) or "none")
        )
    if model.probe_state is not None:
        lines.append(
            f"Last typed probe device: {model.probe_state.device_name or 'not supplied'}"
        )
        lines.append(f"Probe read-only: {model.probe_state.read_only}")
    if model.active is not None:
        lines.append(f"Operation id: {model.active.operation_id}")
        lines.append(f"Action: {model.active.action.value}")
        if model.active.persistent_kind is not None:
            lines.append(f"Persistent kind: {model.active.persistent_kind.value}")
        if model.active.phase is not None:
            lines.append(f"Phase: {model.active.phase.value}")
        lines.append(
            f"Cancellation available: {model.active.cancellation_available}"
        )
    if error_code is not None:
        lines.append(f"Error code: {error_code.value}")
    if model.terminal is not None:
        lines.extend(
            [
                f"Terminal outcome: {model.terminal.outcome.value}",
                f"writing_started={str(model.terminal.writing_started).lower()}",
                "reconciliation_completed="
                + str(model.terminal.reconciliation_completed).lower(),
                "post_validation_completed="
                + str(model.terminal.post_validation_completed).lower(),
            ]
        )
    return tuple(lines)


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
        and privacy_allows(model.surface_privacy, model.transient_notice.privacy)
        else None
    )
    read_only_reason = (
        model.read_only_reason
        if model.read_only_reason is not None
        and privacy_allows(model.surface_privacy, model.read_only_reason.privacy)
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
        route_title=_route_title(model.route),
        privacy=model.surface_privacy,
        privacy_label=_privacy_label(model.surface_privacy),
        read_truth=model.read_truth,
        primary_lines=_primary_lines(model),
        help_lines=_help_lines(model),
        technical_open=model.technical_open,
        technical_lines=_technical_lines(model, error_code=error_code),
        read_only=model.read_only,
        read_only_label=("READ ONLY" if model.read_only else None),
        read_only_reason=(
            read_only_reason.message if read_only_reason is not None else None
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
        writing_started=(
            model.terminal.writing_started if model.terminal is not None else False
        ),
        reconciliation_completed=(
            model.terminal.reconciliation_completed if model.terminal is not None else False
        ),
        post_validation_completed=(
            model.terminal.post_validation_completed if model.terminal is not None else False
        ),
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
