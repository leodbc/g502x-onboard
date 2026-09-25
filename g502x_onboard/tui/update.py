from __future__ import annotations

from dataclasses import replace

from g502x_onboard.application.models import (
    ApplicationError,
    ErrorCode,
    PersistentPhase,
    PrivacyClass,
)
from .effects import (
    Effect,
    ExecutePreparedOperation,
    PreparePersistentOperation,
    RequestCooperativeCancellation,
    RequestExplicitRefresh,
    StartForegroundOperation,
)
from .events import (
    ApplicationCompleted,
    ApplicationFailed,
    CancellationAcknowledged,
    CancellationRequested,
    ChangePrivacySurface,
    CompatibilityChanged,
    ConfirmationChanged,
    ConfirmationSubmitted,
    EnterReview,
    Navigate,
    OperationRequested,
    OperationStarted,
    PersistentCompleted,
    PersistentProgress,
    PreparationInvalidated,
    PreparedReceived,
    RefreshRequested,
    ReviewAcknowledged,
    SetDisclosure,
    SetHelp,
)
from .model import (
    FocusIntent,
    ForegroundOperation,
    OperationAction,
    PresentationPayload,
    Route,
    TerminalOutcome,
    TerminalState,
    TuiModel,
    privacy_allows,
)

Event = (
    Navigate
    | SetHelp
    | SetDisclosure
    | ChangePrivacySurface
    | CompatibilityChanged
    | OperationRequested
    | OperationStarted
    | PreparedReceived
    | EnterReview
    | ReviewAcknowledged
    | ConfirmationChanged
    | ConfirmationSubmitted
    | PersistentProgress
    | ApplicationCompleted
    | PersistentCompleted
    | ApplicationFailed
    | PreparationInvalidated
    | CancellationRequested
    | CancellationAcknowledged
    | RefreshRequested
)

_PHASE_ORDER = {
    PersistentPhase.PREPARING: 0,
    PersistentPhase.PREPARED: 1,
    PersistentPhase.REVIEWING: 2,
    PersistentPhase.CONFIRMING: 3,
    PersistentPhase.REVALIDATING: 4,
    PersistentPhase.ARMED: 5,
    PersistentPhase.WRITING: 6,
    PersistentPhase.RECONCILING: 7,
    PersistentPhase.POST_VALIDATING: 8,
}


def _is_current(model: TuiModel, operation_id) -> bool:
    return model.active is not None and model.active.operation_id == operation_id


def _payload_for_surface(payload, surface):
    if payload is None:
        return None
    privacy = payload.privacy
    return payload if privacy_allows(surface, privacy) else None


def _clear_disallowed(model: TuiModel, surface: PrivacyClass) -> TuiModel:
    prepared = model.prepared
    prepared_cleared = (
        prepared is not None and not privacy_allows(surface, prepared.privacy)
    )
    if prepared_cleared:
        prepared = None
    active = model.active
    if (
        prepared_cleared
        and active is not None
        and not active.execution_requested
        and active.phase
        in {
            PersistentPhase.PREPARED,
            PersistentPhase.REVIEWING,
            PersistentPhase.CONFIRMING,
        }
    ):
        active = None
    return replace(
        model,
        active=active,
        surface_privacy=surface,
        prepared=prepared,
        review_acknowledged=(
            model.review_acknowledged if prepared is not None else False
        ),
        confirmation_input=(model.confirmation_input if prepared is not None else ""),
        last_result=_payload_for_surface(model.last_result, surface),
        last_error=_payload_for_surface(model.last_error, surface),
        read_only_reason=_payload_for_surface(model.read_only_reason, surface),
        disclosure=_payload_for_surface(model.disclosure, surface),
        transient_notice=_payload_for_surface(model.transient_notice, surface),
    )


def _accept_progress(
    current: PersistentPhase | None, new: PersistentPhase
) -> bool:
    if new in {PersistentPhase.SUCCEEDED, PersistentPhase.FAILED}:
        return False
    if current in {PersistentPhase.SUCCEEDED, PersistentPhase.FAILED}:
        return False
    if current is None:
        return True
    if current not in _PHASE_ORDER or new not in _PHASE_ORDER:
        return False
    return _PHASE_ORDER[new] >= _PHASE_ORDER[current]


def _terminal_payload(
    message: str, privacy: PrivacyClass, surface: PrivacyClass
):
    payload = PresentationPayload(message=message, privacy=privacy)
    return payload if privacy_allows(surface, privacy) else None


def update(
    model: TuiModel, event: Event
) -> tuple[TuiModel, tuple[Effect, ...]]:
    if isinstance(event, Navigate):
        return replace(model, route=event.route, focus=event.focus), ()

    if isinstance(event, SetHelp):
        return replace(
            model,
            help_open=event.open,
            focus=(FocusIntent.HELP if event.open else model.focus),
        ), ()

    if isinstance(event, SetDisclosure):
        payload = _payload_for_surface(event.payload, model.surface_privacy)
        return replace(model, disclosure=payload), ()

    if isinstance(event, ChangePrivacySurface):
        return _clear_disallowed(model, event.privacy), ()

    if isinstance(event, CompatibilityChanged):
        reason = _payload_for_surface(event.reason, model.surface_privacy)
        return replace(
            model, read_only=event.read_only, read_only_reason=reason
        ), ()

    if isinstance(event, OperationRequested):
        if model.active is not None:
            notice = PresentationPayload(
                "another foreground hardware operation is already active",
                PrivacyClass.SHAREABLE,
            )
            return replace(model, transient_notice=notice), ()
        active = ForegroundOperation(
            operation_id=event.operation_id,
            action=event.action,
            hardware_affecting=event.hardware_affecting,
            persistent_kind=event.persistent_kind,
            phase=(
                PersistentPhase.PREPARING
                if event.action is OperationAction.PREPARE_PERSISTENT
                else None
            ),
        )
        next_model = replace(
            model,
            active=active,
            route=Route.OPERATION,
            terminal=None,
            last_result=None,
            last_error=None,
            transient_notice=None,
        )
        if event.action is OperationAction.PREPARE_PERSISTENT:
            if event.persistent_kind is None:
                error = ApplicationError(
                    ErrorCode.INVALID_INPUT,
                    "persistent preparation requires an operation kind",
                    PrivacyClass.SHAREABLE,
                )
                return replace(next_model, active=None, last_error=error), ()
            return next_model, (
                PreparePersistentOperation(
                    event.operation_id, event.persistent_kind
                ),
            )
        if event.action is OperationAction.REFRESH:
            return next_model, (
                RequestExplicitRefresh(event.operation_id),
            )
        return next_model, (
            StartForegroundOperation(event.operation_id, event.action),
        )

    if isinstance(event, OperationStarted):
        if not _is_current(model, event.operation_id):
            return model, ()
        return replace(
            model, active=replace(model.active, started=True)
        ), ()

    if isinstance(event, PreparedReceived):
        if not _is_current(model, event.operation_id):
            return model, ()
        if (
            model.active.persistent_kind is not None
            and event.prepared.kind is not model.active.persistent_kind
        ):
            return model, ()
        target_surface = model.surface_privacy
        if not privacy_allows(target_surface, event.prepared.privacy):
            target_surface = event.prepared.privacy
        active = replace(
            model.active,
            phase=PersistentPhase.PREPARED,
            persistent_kind=event.prepared.kind,
        )
        return replace(
            model,
            active=active,
            surface_privacy=target_surface,
            prepared=event.prepared,
            review_acknowledged=False,
            confirmation_input="",
            route=Route.REVIEW,
        ), ()

    if isinstance(event, EnterReview):
        if (
            not _is_current(model, event.operation_id)
            or model.prepared is None
        ):
            return model, ()
        active = replace(model.active, phase=PersistentPhase.REVIEWING)
        return replace(
            model,
            active=active,
            route=Route.REVIEW,
            focus=FocusIntent.REVIEW,
        ), ()

    if isinstance(event, ReviewAcknowledged):
        if (
            not _is_current(model, event.operation_id)
            or model.prepared is None
        ):
            return model, ()
        return replace(
            model, review_acknowledged=event.acknowledged
        ), ()

    if isinstance(event, ConfirmationChanged):
        if (
            not _is_current(model, event.operation_id)
            or model.prepared is None
        ):
            return model, ()
        active = replace(model.active, phase=PersistentPhase.CONFIRMING)
        return replace(
            model,
            active=active,
            confirmation_input=event.value,
            route=Route.CONFIRMATION,
            focus=FocusIntent.CONFIRMATION,
        ), ()

    if isinstance(event, ConfirmationSubmitted):
        if (
            not _is_current(model, event.operation_id)
            or model.prepared is None
            or not model.review_acknowledged
        ):
            return model, ()
        effect = ExecutePreparedOperation(
            operation_id=event.operation_id,
            prepared=model.prepared,
            confirmation=model.confirmation_input,
        )
        active = replace(
            model.active,
            phase=PersistentPhase.CONFIRMING,
            execution_requested=True,
        )
        return replace(model, active=active), (effect,)

    if isinstance(event, PersistentProgress):
        if not _is_current(model, event.operation_id):
            return model, ()
        if (
            model.active.persistent_kind is not None
            and event.snapshot.kind is not model.active.persistent_kind
        ):
            return model, ()
        current = model.active.phase
        new = event.snapshot.phase
        if not _accept_progress(current, new):
            return model, ()
        active = replace(
            model.active,
            phase=new,
            application_cancellation_allowed=(
                event.snapshot.cancellation_allowed
            ),
        )
        return replace(model, active=active), ()

    if isinstance(event, CancellationRequested):
        if not _is_current(model, event.operation_id):
            return model, ()
        if model.active.non_cancellable:
            active = replace(
                model.active, cancellation_deferred=True
            )
            notice = PresentationPayload(
                "transaction is in a non-cancellable safety phase",
                PrivacyClass.SHAREABLE,
            )
            return replace(
                model, active=active, transient_notice=notice
            ), ()
        if not model.active.cancellation_available:
            notice = PresentationPayload(
                "cooperative cancellation is unavailable for the current phase",
                PrivacyClass.SHAREABLE,
            )
            return replace(model, transient_notice=notice), ()
        active = replace(
            model.active, cancellation_requested=True
        )
        return replace(model, active=active), (
            RequestCooperativeCancellation(event.operation_id),
        )

    if isinstance(event, CancellationAcknowledged):
        if not _is_current(model, event.operation_id):
            return model, ()
        active = replace(
            model.active, cancellation_acknowledged=True
        )
        return replace(model, active=active), ()

    if isinstance(event, RefreshRequested):
        if model.active is not None:
            notice = PresentationPayload(
                "refresh unavailable while a foreground hardware operation is active",
                PrivacyClass.SHAREABLE,
            )
            return replace(model, transient_notice=notice), ()
        active = ForegroundOperation(
            event.operation_id, OperationAction.REFRESH, True
        )
        return replace(
            model, active=active, route=Route.OPERATION
        ), (RequestExplicitRefresh(event.operation_id),)

    if isinstance(event, ApplicationCompleted):
        if not _is_current(model, event.operation_id):
            return model, ()
        payload = _terminal_payload(
            event.message, event.privacy, model.surface_privacy
        )
        terminal = TerminalState(TerminalOutcome.SUCCESS)
        return replace(
            model,
            active=None,
            terminal=terminal,
            last_result=payload,
            last_error=None,
            route=Route.RESULT,
        ), ()

    if isinstance(event, ApplicationFailed):
        if not _is_current(model, event.operation_id):
            return model, ()
        error = _payload_for_surface(
            event.error, model.surface_privacy
        )
        terminal = TerminalState(
            TerminalOutcome.FAILURE, error_code=event.error.code
        )
        return replace(
            model,
            active=None,
            terminal=terminal,
            last_result=None,
            last_error=error,
            route=Route.RESULT,
        ), ()

    if isinstance(event, PreparationInvalidated):
        if not _is_current(model, event.operation_id):
            return model, ()
        error = _payload_for_surface(
            event.error, model.surface_privacy
        )
        terminal = TerminalState(
            TerminalOutcome.FAILURE,
            PersistentPhase.FAILED,
            event.error.code,
        )
        return replace(
            model,
            active=None,
            prepared=None,
            review_acknowledged=False,
            confirmation_input="",
            last_result=None,
            last_error=error,
            terminal=terminal,
            route=Route.HOME,
        ), ()

    if isinstance(event, PersistentCompleted):
        if not _is_current(model, event.operation_id):
            return model, ()
        result = event.result
        if (
            model.active.persistent_kind is not None
            and result.operation_kind is not model.active.persistent_kind
        ):
            return model, ()
        success_consistent = (
            result.success
            and result.terminal_phase is PersistentPhase.SUCCEEDED
            and result.writing_started
            and result.reconciliation_completed
            and result.post_validation_completed
        )
        if result.success and not success_consistent:
            error = ApplicationError(
                ErrorCode.BACKEND_FAILURE,
                "inconsistent terminal success evidence",
                result.privacy,
            )
            terminal = TerminalState(
                TerminalOutcome.FAILURE,
                PersistentPhase.FAILED,
                ErrorCode.BACKEND_FAILURE,
                result.writing_started,
                result.reconciliation_completed,
                result.post_validation_completed,
            )
            return replace(
                model,
                active=None,
                prepared=None,
                review_acknowledged=False,
                confirmation_input="",
                terminal=terminal,
                last_result=None,
                last_error=_payload_for_surface(
                    error, model.surface_privacy
                ),
                route=Route.RESULT,
            ), ()

        if success_consistent:
            terminal = TerminalState(
                TerminalOutcome.SUCCESS,
                PersistentPhase.SUCCEEDED,
                None,
                True,
                True,
                True,
            )
            payload = _terminal_payload(
                result.message, result.privacy, model.surface_privacy
            )
            return replace(
                model,
                active=None,
                prepared=None,
                review_acknowledged=False,
                confirmation_input="",
                terminal=terminal,
                last_result=payload,
                last_error=None,
                route=Route.RESULT,
            ), ()

        error_code = result.error_code or ErrorCode.BACKEND_FAILURE
        terminal = TerminalState(
            TerminalOutcome.FAILURE,
            PersistentPhase.FAILED,
            error_code,
            result.writing_started,
            result.reconciliation_completed,
            result.post_validation_completed,
        )
        error = ApplicationError(
            error_code,
            result.message or "persistent operation failed",
            result.privacy,
        )
        return replace(
            model,
            active=None,
            prepared=None,
            review_acknowledged=False,
            confirmation_input="",
            terminal=terminal,
            last_result=None,
            last_error=_payload_for_surface(
                error, model.surface_privacy
            ),
            route=Route.RESULT,
        ), ()

    raise TypeError(
        f"unsupported TUI event type: {type(event).__name__}"
    )
