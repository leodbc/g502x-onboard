from __future__ import annotations

from dataclasses import replace
import unittest

from g502x_onboard.application.models import (
    ApplicationError,
    ApplyReview,
    CompatibilityObservation,
    ErrorCode,
    PersistentExecutionResult,
    PersistentOperationKind,
    PersistentPhase,
    PersistentPhaseSnapshot,
    PreparedOperation,
    PrivacyClass,
    WriteEligibility,
)
from g502x_onboard.tui.effects import (
    ExecutePreparedOperation,
    PreparePersistentOperation,
    RequestCooperativeCancellation,
    RequestExplicitRefresh,
    StartForegroundOperation,
)
from g502x_onboard.tui.events import (
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
from g502x_onboard.tui.model import (
    FocusIntent,
    OperationAction,
    OperationId,
    PresentationPayload,
    Route,
    TerminalOutcome,
    TuiModel,
)
from g502x_onboard.tui.update import update
from g502x_onboard.tui.view import view


def make_prepared(preparation_id="prep-1", phrase="APPLY CONFIG"):
    compatibility = CompatibilityObservation(
        architecture="compatible",
        transport="tested",
        identity="stable",
        write_allowed=True,
        eligibility=WriteEligibility.ELIGIBLE,
    )
    review = ApplyReview(
        config_name="local.json",
        config_path="/private/local.json",
        enabled_profiles=(2,),
        profile_names=((2, "WORK"),),
        managed_sectors=(0, 2, 8),
        warnings=(),
        plan_digest="digest",
    )
    return PreparedOperation(
        preparation_id=preparation_id,
        kind=PersistentOperationKind.APPLY_CONFIG,
        review=review,
        target_digest="digest",
        compatibility=compatibility,
        observed_preconditions=("safe",),
        host_guard_clear=True,
        required_confirmation_phrase=phrase,
    )


def persistent_result(
    *,
    success,
    terminal_phase,
    writing_started,
    reconciled,
    post_validated,
    error_code=None,
    message="done",
    privacy=PrivacyClass.LOCAL_SENSITIVE,
):
    return PersistentExecutionResult(
        operation_kind=PersistentOperationKind.APPLY_CONFIG,
        terminal_phase=terminal_phase,
        success=success,
        pre_write_status="ok",
        writing_started=writing_started,
        reconciliation_completed=reconciled,
        post_validation_completed=post_validated,
        error_code=error_code,
        message=message,
        privacy=privacy,
    )


class TuiStateEngineTests(unittest.TestCase):
    def setUp(self):
        self.op = OperationId("op-aaaaaaaa")
        self.other = OperationId("op-bbbbbbbb")

    def request_prepare(self, model=None):
        model = model or TuiModel()
        return update(
            model,
            OperationRequested(
                self.op,
                OperationAction.PREPARE_PERSISTENT,
                persistent_kind=PersistentOperationKind.APPLY_CONFIG,
            ),
        )

    def prepared_model(self):
        model, _ = self.request_prepare()
        model, _ = update(model, OperationStarted(self.op))
        model, _ = update(
            model, PreparedReceived(self.op, make_prepared())
        )
        return model

    def writing_model(self):
        model = self.prepared_model()
        model, _ = update(model, EnterReview(self.op))
        model, _ = update(model, ReviewAcknowledged(self.op, True))
        model, _ = update(
            model, ConfirmationChanged(self.op, "APPLY CONFIG")
        )
        model, _ = update(model, ConfirmationSubmitted(self.op))
        for phase in (
            PersistentPhase.REVALIDATING,
            PersistentPhase.ARMED,
            PersistentPhase.WRITING,
        ):
            model, _ = update(
                model,
                PersistentProgress(
                    self.op,
                    PersistentPhaseSnapshot(
                        phase,
                        PersistentOperationKind.APPLY_CONFIG,
                        phase is not PersistentPhase.WRITING,
                    ),
                ),
            )
        return model

    def test_initial_model_invariants(self):
        model = TuiModel()
        self.assertEqual(model.route, Route.HOME)
        self.assertEqual(
            model.surface_privacy, PrivacyClass.SHAREABLE
        )
        self.assertIsNone(model.active)
        self.assertIsNone(model.prepared)
        self.assertEqual(model.confirmation_input, "")
        self.assertFalse(model.review_acknowledged)

    def test_operation_id_is_short_opaque_token(self):
        self.assertEqual(
            str(OperationId("op-01234567abcdef")),
            "op-01234567abcdef",
        )
        for unsafe in (
            "/tmp/private.json",
            "C:\\private\\x",
            "profile WORK",
            "error: boom",
            "op-deadbeef-WORK",
            "",
        ):
            with self.subTest(unsafe=unsafe), self.assertRaises(
                ValueError
            ):
                OperationId(unsafe)

    def test_prepare_request_is_typed_and_pure(self):
        model, effects = self.request_prepare()
        self.assertEqual(
            model.active.phase, PersistentPhase.PREPARING
        )
        self.assertEqual(
            effects,
            (
                PreparePersistentOperation(
                    self.op, PersistentOperationKind.APPLY_CONFIG
                ),
            ),
        )

    def test_generic_operation_request_emits_typed_start_effect(self):
        model, effects = update(
            TuiModel(),
            OperationRequested(self.op, OperationAction.STATUS),
        )
        self.assertEqual(
            effects,
            (
                StartForegroundOperation(
                    self.op, OperationAction.STATUS
                ),
            ),
        )
        self.assertEqual(model.active.operation_id, self.op)

    def test_worker_started_requires_current_operation_id(self):
        model, _ = self.request_prepare()
        stale, effects = update(
            model, OperationStarted(self.other)
        )
        self.assertEqual(stale, model)
        self.assertEqual(effects, ())
        current, _ = update(
            model, OperationStarted(self.op)
        )
        self.assertTrue(current.active.started)

    def test_prepared_review_confirmation_representation(self):
        model = self.prepared_model()
        self.assertEqual(
            model.surface_privacy,
            PrivacyClass.LOCAL_SENSITIVE,
        )
        self.assertEqual(model.route, Route.REVIEW)
        model, _ = update(model, EnterReview(self.op))
        model, _ = update(
            model, ReviewAcknowledged(self.op, True)
        )
        model, _ = update(
            model, ConfirmationChanged(self.op, "APPLY CONFIG")
        )
        vm = view(model)
        self.assertEqual(
            model.active.phase, PersistentPhase.CONFIRMING
        )
        self.assertTrue(vm.confirmation_visible)
        self.assertTrue(vm.confirmation_matches)
        self.assertTrue(model.review_acknowledged)

    def test_confirmation_submit_uses_prepared_object_as_authority(self):
        model = self.prepared_model()
        model, _ = update(
            model, ReviewAcknowledged(self.op, True)
        )
        model, _ = update(
            model, ConfirmationChanged(self.op, "APPLY CONFIG")
        )
        model, effects = update(
            model, ConfirmationSubmitted(self.op)
        )
        self.assertEqual(
            effects,
            (
                ExecutePreparedOperation(
                    self.op, model.prepared, "APPLY CONFIG"
                ),
            ),
        )
        self.assertNotEqual(
            str(self.op), model.prepared.preparation_id
        )
        self.assertTrue(model.active.execution_requested)

    def test_confirmation_input_reset_when_preparation_replaced(self):
        model = self.prepared_model()
        model, _ = update(
            model, ReviewAcknowledged(self.op, True)
        )
        model, _ = update(
            model, ConfirmationChanged(self.op, "APPLY CONFIG")
        )
        model, _ = update(
            model,
            PreparedReceived(
                self.op, make_prepared("prep-2")
            ),
        )
        self.assertEqual(
            model.prepared.preparation_id, "prep-2"
        )
        self.assertEqual(model.confirmation_input, "")
        self.assertFalse(model.review_acknowledged)

    def test_preparation_invalidation_clears_capability(self):
        model = self.prepared_model()
        model, _ = update(
            model, ReviewAcknowledged(self.op, True)
        )
        model, _ = update(
            model, ConfirmationChanged(self.op, "APPLY CONFIG")
        )
        error = ApplicationError(
            ErrorCode.STALE_PREPARATION,
            "prepare again",
            PrivacyClass.LOCAL_SENSITIVE,
        )
        model, effects = update(
            model, PreparationInvalidated(self.op, error)
        )
        self.assertEqual(effects, ())
        self.assertIsNone(model.prepared)
        self.assertIsNone(model.active)
        self.assertEqual(model.confirmation_input, "")
        self.assertFalse(model.review_acknowledged)
        self.assertEqual(model.route, Route.HOME)
        self.assertEqual(model.last_error, error)

    def test_legal_persistent_lifecycle_progression(self):
        model = self.prepared_model()
        model, _ = update(model, EnterReview(self.op))
        model, _ = update(
            model, ReviewAcknowledged(self.op, True)
        )
        model, _ = update(
            model, ConfirmationChanged(self.op, "APPLY CONFIG")
        )
        for phase, allowed in (
            (PersistentPhase.REVALIDATING, True),
            (PersistentPhase.ARMED, True),
            (PersistentPhase.WRITING, False),
            (PersistentPhase.RECONCILING, False),
            (PersistentPhase.POST_VALIDATING, False),
        ):
            model, _ = update(
                model,
                PersistentProgress(
                    self.op,
                    PersistentPhaseSnapshot(
                        phase,
                        PersistentOperationKind.APPLY_CONFIG,
                        allowed,
                    ),
                ),
            )
            self.assertEqual(model.active.phase, phase)

    def test_illegal_regressive_phase_is_ignored(self):
        model = self.writing_model()
        before = model
        model, effects = update(
            model,
            PersistentProgress(
                self.op,
                PersistentPhaseSnapshot(
                    PersistentPhase.ARMED,
                    PersistentOperationKind.APPLY_CONFIG,
                    True,
                ),
            ),
        )
        self.assertEqual(model, before)
        self.assertEqual(effects, ())

    def test_forward_phase_skip_is_ignored(self):
        model = self.prepared_model()
        model, _ = update(model, EnterReview(self.op))
        model, _ = update(
            model, ReviewAcknowledged(self.op, True)
        )
        model, _ = update(
            model, ConfirmationChanged(self.op, "APPLY CONFIG")
        )
        before = model
        model, effects = update(
            model,
            PersistentProgress(
                self.op,
                PersistentPhaseSnapshot(
                    PersistentPhase.ARMED,
                    PersistentOperationKind.APPLY_CONFIG,
                    True,
                ),
            ),
        )
        self.assertEqual(model, before)
        self.assertEqual(effects, ())

    def test_terminal_progress_is_not_authoritative(self):
        model = self.writing_model()
        before = model
        model, _ = update(
            model,
            PersistentProgress(
                self.op,
                PersistentPhaseSnapshot(
                    PersistentPhase.SUCCEEDED,
                    PersistentOperationKind.APPLY_CONFIG,
                    False,
                ),
            ),
        )
        self.assertEqual(model, before)

    def test_stale_phase_completion_failure_and_cancel_are_ignored(self):
        model = self.writing_model()
        candidates = [
            PersistentProgress(
                self.other,
                PersistentPhaseSnapshot(
                    PersistentPhase.RECONCILING,
                    PersistentOperationKind.APPLY_CONFIG,
                    False,
                ),
            ),
            PersistentCompleted(
                self.other,
                persistent_result(
                    success=True,
                    terminal_phase=PersistentPhase.SUCCEEDED,
                    writing_started=True,
                    reconciled=True,
                    post_validated=True,
                ),
            ),
            ApplicationFailed(
                self.other,
                ApplicationError(
                    ErrorCode.BACKEND_FAILURE,
                    "late",
                    PrivacyClass.SHAREABLE,
                ),
            ),
            CancellationAcknowledged(self.other),
        ]
        for event in candidates:
            with self.subTest(event=type(event).__name__):
                after, effects = update(model, event)
                self.assertEqual(after, model)
                self.assertEqual(effects, ())

    def test_current_nonpersistent_completion_is_accepted(self):
        model, _ = update(
            TuiModel(),
            OperationRequested(self.op, OperationAction.STATUS),
        )
        model, _ = update(
            model,
            ApplicationCompleted(
                self.op,
                "status complete",
                PrivacyClass.SHAREABLE,
            ),
        )
        self.assertIsNone(model.active)
        self.assertEqual(
            model.terminal.outcome, TerminalOutcome.SUCCESS
        )
        self.assertEqual(
            model.last_result.message, "status complete"
        )

    def test_second_hardware_operation_is_rejected(self):
        model, _ = update(
            TuiModel(),
            OperationRequested(self.op, OperationAction.STATUS),
        )
        model2, effects = update(
            model,
            OperationRequested(
                self.other, OperationAction.VALIDATE
            ),
        )
        self.assertEqual(
            model2.active.operation_id, self.op
        )
        self.assertEqual(effects, ())
        self.assertIn(
            "already active",
            model2.transient_notice.message,
        )

    def test_privacy_class_survives_local_state_transitions(self):
        model = self.prepared_model()
        self.assertEqual(
            model.prepared.privacy,
            PrivacyClass.LOCAL_SENSITIVE,
        )
        model, _ = update(model, EnterReview(self.op))
        model, _ = update(
            model, ReviewAcknowledged(self.op, True)
        )
        self.assertEqual(
            model.prepared.privacy,
            PrivacyClass.LOCAL_SENSITIVE,
        )

    def test_sensitive_payloads_are_cleared_on_shareable_transition(self):
        model = self.prepared_model()
        local_error = ApplicationError(
            ErrorCode.CONFIG_ERROR,
            "local path failed",
            PrivacyClass.LOCAL_SENSITIVE,
            detail="/private/path",
        )
        model = replace(
            model,
            last_error=local_error,
            last_result=PresentationPayload(
                "local result",
                PrivacyClass.LOCAL_SENSITIVE,
            ),
            disclosure=PresentationPayload(
                "private raw",
                PrivacyClass.PRIVATE_DIAGNOSTIC,
            ),
            transient_notice=PresentationPayload(
                "local notice",
                PrivacyClass.LOCAL_SENSITIVE,
            ),
            confirmation_input="APPLY CONFIG",
            review_acknowledged=True,
        )
        model, _ = update(
            model,
            ChangePrivacySurface(PrivacyClass.SHAREABLE),
        )
        self.assertEqual(
            model.surface_privacy, PrivacyClass.SHAREABLE
        )
        self.assertIsNone(model.prepared)
        self.assertIsNone(model.active)
        self.assertIsNone(model.last_error)
        self.assertIsNone(model.last_result)
        self.assertIsNone(model.disclosure)
        self.assertIsNone(model.transient_notice)
        self.assertEqual(model.confirmation_input, "")
        self.assertFalse(model.review_acknowledged)

    def test_private_payload_cleared_on_local_sensitive_surface(self):
        model = replace(
            TuiModel(
                surface_privacy=PrivacyClass.PRIVATE_DIAGNOSTIC
            ),
            disclosure=PresentationPayload(
                "unit-private",
                PrivacyClass.PRIVATE_DIAGNOSTIC,
            ),
        )
        model, _ = update(
            model,
            ChangePrivacySurface(
                PrivacyClass.LOCAL_SENSITIVE
            ),
        )
        self.assertIsNone(model.disclosure)

    def test_sensitive_completion_not_retained_shareably(self):
        model, _ = update(
            TuiModel(),
            OperationRequested(self.op, OperationAction.STATUS),
        )
        model, _ = update(
            model,
            ApplicationCompleted(
                self.op,
                "/private/result",
                PrivacyClass.LOCAL_SENSITIVE,
            ),
        )
        self.assertIsNone(model.last_result)
        self.assertEqual(
            model.terminal.outcome, TerminalOutcome.SUCCESS
        )
        self.assertIsNone(view(model).message)

    def test_typed_error_privacy_prevents_shareable_detail(self):
        model, _ = update(
            TuiModel(),
            OperationRequested(self.op, OperationAction.STATUS),
        )
        error = ApplicationError(
            ErrorCode.BACKEND_FAILURE,
            "diagnostic failed",
            PrivacyClass.PRIVATE_DIAGNOSTIC,
            detail="unit=secret",
        )
        model, _ = update(
            model, ApplicationFailed(self.op, error)
        )
        self.assertIsNone(model.last_error)
        self.assertIsNone(view(model).detail)
        self.assertEqual(
            model.terminal.error_code,
            ErrorCode.BACKEND_FAILURE,
        )

    def test_private_error_detail_only_on_private_surface(self):
        model = TuiModel(
            surface_privacy=PrivacyClass.PRIVATE_DIAGNOSTIC
        )
        model, _ = update(
            model,
            OperationRequested(self.op, OperationAction.STATUS),
        )
        error = ApplicationError(
            ErrorCode.BACKEND_FAILURE,
            "diagnostic failed",
            PrivacyClass.PRIVATE_DIAGNOSTIC,
            detail="unit=secret",
        )
        model, _ = update(
            model, ApplicationFailed(self.op, error)
        )
        self.assertEqual(
            view(model).detail, "unit=secret"
        )
        model, _ = update(
            model,
            ChangePrivacySurface(PrivacyClass.SHAREABLE),
        )
        self.assertIsNone(model.last_error)
        self.assertIsNone(view(model).detail)

    def test_cancellation_permitted_before_writing(self):
        model = self.prepared_model()
        model, effects = update(
            model, CancellationRequested(self.op)
        )
        self.assertTrue(
            model.active.cancellation_requested
        )
        self.assertEqual(
            effects,
            (RequestCooperativeCancellation(self.op),),
        )

    def test_cancellation_still_permitted_at_armed(self):
        model = self.prepared_model()
        model, _ = update(
            model,
            PersistentProgress(
                self.op,
                PersistentPhaseSnapshot(
                    PersistentPhase.ARMED,
                    PersistentOperationKind.APPLY_CONFIG,
                    True,
                ),
            ),
        )
        model, effects = update(
            model, CancellationRequested(self.op)
        )
        self.assertEqual(
            effects,
            (RequestCooperativeCancellation(self.op),),
        )
        self.assertTrue(
            model.active.cancellation_available
        )

    def test_application_cancellation_flag_fails_closed(self):
        model = self.prepared_model()
        model, _ = update(
            model,
            PersistentProgress(
                self.op,
                PersistentPhaseSnapshot(
                    PersistentPhase.ARMED,
                    PersistentOperationKind.APPLY_CONFIG,
                    False,
                ),
            ),
        )
        model, effects = update(
            model, CancellationRequested(self.op)
        )
        self.assertEqual(effects, ())
        self.assertFalse(
            model.active.cancellation_requested
        )

    def test_writing_and_later_emit_no_cancel_effect(self):
        model = self.writing_model()
        for phase in (
            PersistentPhase.WRITING,
            PersistentPhase.RECONCILING,
            PersistentPhase.POST_VALIDATING,
        ):
            if model.active.phase is not phase:
                model, _ = update(
                    model,
                    PersistentProgress(
                        self.op,
                        PersistentPhaseSnapshot(
                            phase,
                            PersistentOperationKind.APPLY_CONFIG,
                            False,
                        ),
                    ),
                )
            after, effects = update(
                model, CancellationRequested(self.op)
            )
            self.assertEqual(effects, ())
            self.assertTrue(
                after.active.cancellation_deferred
            )
            self.assertTrue(view(after).non_cancellable)
            model = after

    def test_terminal_result_accepted_after_writing_cancel_request(self):
        model = self.writing_model()
        model, _ = update(
            model, CancellationRequested(self.op)
        )
        result = persistent_result(
            success=True,
            terminal_phase=PersistentPhase.SUCCEEDED,
            writing_started=True,
            reconciled=True,
            post_validated=True,
        )
        model, _ = update(
            model, PersistentCompleted(self.op, result)
        )
        self.assertEqual(
            model.terminal.outcome, TerminalOutcome.SUCCESS
        )
        self.assertEqual(
            model.terminal.persistent_phase,
            PersistentPhase.SUCCEEDED,
        )

    def test_success_requires_consistent_authoritative_result(self):
        inconsistent = [
            persistent_result(
                success=True,
                terminal_phase=PersistentPhase.FAILED,
                writing_started=True,
                reconciled=True,
                post_validated=True,
            ),
            persistent_result(
                success=True,
                terminal_phase=PersistentPhase.SUCCEEDED,
                writing_started=True,
                reconciled=False,
                post_validated=True,
            ),
            persistent_result(
                success=True,
                terminal_phase=PersistentPhase.SUCCEEDED,
                writing_started=True,
                reconciled=True,
                post_validated=False,
            ),
            persistent_result(
                success=True,
                terminal_phase=PersistentPhase.SUCCEEDED,
                writing_started=False,
                reconciled=True,
                post_validated=True,
            ),
        ]
        for result in inconsistent:
            with self.subTest(result=result):
                model = self.writing_model()
                model, _ = update(
                    model,
                    PersistentCompleted(self.op, result),
                )
                self.assertEqual(
                    model.terminal.outcome,
                    TerminalOutcome.FAILURE,
                )
                self.assertEqual(
                    model.terminal.persistent_phase,
                    PersistentPhase.FAILED,
                )
                self.assertNotEqual(
                    view(model).terminal_label, "SUCCESS"
                )

    def test_consistent_success_is_accepted(self):
        model = self.writing_model()
        result = persistent_result(
            success=True,
            terminal_phase=PersistentPhase.SUCCEEDED,
            writing_started=True,
            reconciled=True,
            post_validated=True,
        )
        model, _ = update(
            model, PersistentCompleted(self.op, result)
        )
        self.assertEqual(
            model.terminal.outcome, TerminalOutcome.SUCCESS
        )
        self.assertTrue(
            model.terminal.reconciliation_completed
        )
        self.assertTrue(
            model.terminal.post_validation_completed
        )

    def test_failed_after_write_preserves_authoritative_facts(self):
        model = self.writing_model()
        result = persistent_result(
            success=False,
            terminal_phase=PersistentPhase.FAILED,
            writing_started=True,
            reconciled=False,
            post_validated=False,
            error_code=ErrorCode.BACKEND_FAILURE,
            message="persistent operation refused or failed",
        )
        model, _ = update(
            model, PersistentCompleted(self.op, result)
        )
        self.assertEqual(
            model.terminal.outcome, TerminalOutcome.FAILURE
        )
        self.assertTrue(model.terminal.writing_started)
        self.assertFalse(
            model.terminal.reconciliation_completed
        )
        self.assertFalse(
            model.terminal.post_validation_completed
        )
        self.assertEqual(
            model.terminal.error_code,
            ErrorCode.BACKEND_FAILURE,
        )

    def test_help_disclosure_navigation_do_not_mutate_lifecycle(self):
        model = self.writing_model()
        active = model.active
        model, _ = update(model, SetHelp(True))
        self.assertEqual(model.active, active)
        model, _ = update(
            model,
            SetDisclosure(
                PresentationPayload(
                    "local",
                    PrivacyClass.LOCAL_SENSITIVE,
                )
            ),
        )
        self.assertEqual(model.active, active)
        model, _ = update(
            model,
            Navigate(
                Route.OPERATION, FocusIntent.PRIMARY
            ),
        )
        self.assertEqual(model.active, active)

    def test_no_progress_percentage_is_invented(self):
        self.assertIsNone(
            view(self.writing_model()).progress_percent
        )

    def test_view_has_privacy_and_non_cancellable_labels(self):
        vm = view(self.writing_model())
        self.assertEqual(
            vm.privacy, PrivacyClass.LOCAL_SENSITIVE
        )
        self.assertEqual(
            vm.privacy_label, "LOCAL SENSITIVE"
        )
        self.assertEqual(
            vm.phase, PersistentPhase.WRITING
        )
        self.assertTrue(vm.non_cancellable)
        self.assertEqual(
            vm.safety_label,
            "NON-CANCELLABLE SAFETY PHASE",
        )

    def test_read_only_presentation_is_explicit(self):
        reason = ApplicationError(
            ErrorCode.READ_ONLY,
            "target is read-only",
            PrivacyClass.SHAREABLE,
        )
        model, _ = update(
            TuiModel(),
            CompatibilityChanged(True, reason),
        )
        vm = view(model)
        self.assertTrue(vm.read_only)
        self.assertEqual(vm.read_only_label, "READ ONLY")
        self.assertEqual(model.read_only_reason, reason)

    def test_success_failure_distinguishable_without_color(self):
        model, _ = update(
            TuiModel(),
            OperationRequested(self.op, OperationAction.STATUS),
        )
        success, _ = update(
            model, ApplicationCompleted(self.op, "ok")
        )
        self.assertEqual(
            view(success).terminal_label, "SUCCESS"
        )
        model, _ = update(
            TuiModel(),
            OperationRequested(self.op, OperationAction.STATUS),
        )
        failure, _ = update(
            model,
            ApplicationFailed(
                self.op,
                ApplicationError(
                    ErrorCode.BACKEND_FAILURE,
                    "failed",
                    PrivacyClass.SHAREABLE,
                ),
            ),
        )
        self.assertEqual(
            view(failure).terminal_label, "FAILURE"
        )

    def test_refresh_is_only_explicit(self):
        model = TuiModel()
        same, effects = update(
            model, Navigate(Route.HOME)
        )
        self.assertEqual(effects, ())
        refreshed, effects = update(
            same, RefreshRequested(self.op)
        )
        self.assertEqual(
            effects, (RequestExplicitRefresh(self.op),)
        )
        self.assertEqual(
            refreshed.active.operation_id, self.op
        )

    def test_update_is_deterministic_for_equal_inputs(self):
        event = OperationRequested(
            self.op, OperationAction.STATUS
        )
        self.assertEqual(
            update(TuiModel(), event),
            update(TuiModel(), event),
        )

    def test_stale_prepared_event_cannot_replace_state(self):
        model, _ = update(
            TuiModel(),
            OperationRequested(
                self.other, OperationAction.STATUS
            ),
        )
        after, effects = update(
            model,
            PreparedReceived(self.op, make_prepared()),
        )
        self.assertEqual(after, model)
        self.assertEqual(effects, ())

    def test_stale_confirmation_cannot_change_state(self):
        model = self.prepared_model()
        after, effects = update(
            model,
            ConfirmationChanged(
                self.other, "RESTORE BACKUP"
            ),
        )
        self.assertEqual(after, model)
        self.assertEqual(effects, ())

    def test_cancellation_ack_does_not_release_operation(self):
        model = self.prepared_model()
        model, _ = update(
            model, CancellationRequested(self.op)
        )
        model, _ = update(
            model, CancellationAcknowledged(self.op)
        )
        self.assertIsNotNone(model.active)
        self.assertTrue(
            model.active.cancellation_acknowledged
        )
        busy, effects = update(
            model,
            OperationRequested(
                self.other, OperationAction.STATUS
            ),
        )
        self.assertEqual(effects, ())
        self.assertEqual(
            busy.active.operation_id, self.op
        )

    def test_wrong_persistent_kind_events_are_ignored(self):
        model = self.prepared_model()
        wrong_snapshot = PersistentPhaseSnapshot(
            PersistentPhase.REVALIDATING,
            PersistentOperationKind.RESTORE_BACKUP,
            True,
        )
        after, effects = update(
            model,
            PersistentProgress(
                self.op, wrong_snapshot
            ),
        )
        self.assertEqual(after, model)
        self.assertEqual(effects, ())
        wrong_result = PersistentExecutionResult(
            operation_kind=PersistentOperationKind.RESTORE_BACKUP,
            terminal_phase=PersistentPhase.FAILED,
            success=False,
            pre_write_status="refused",
            writing_started=False,
            reconciliation_completed=False,
            post_validation_completed=False,
            error_code=ErrorCode.SAFETY_REFUSAL,
            message="wrong kind",
        )
        after, effects = update(
            model,
            PersistentCompleted(
                self.op, wrong_result
            ),
        )
        self.assertEqual(after, model)
        self.assertEqual(effects, ())

    def test_view_exposes_review_only_on_local_surface(self):
        model = self.prepared_model()
        vm = view(model)
        self.assertIs(vm.prepared, model.prepared)
        self.assertEqual(
            vm.persistent_kind,
            PersistentOperationKind.APPLY_CONFIG,
        )
        self.assertEqual(
            vm.required_confirmation_phrase,
            "APPLY CONFIG",
        )
        model, _ = update(
            model,
            ChangePrivacySurface(
                PrivacyClass.SHAREABLE
            ),
        )
        vm = view(model)
        self.assertIsNone(vm.prepared)
        self.assertIsNone(
            vm.required_confirmation_phrase
        )


if __name__ == "__main__":
    unittest.main()
