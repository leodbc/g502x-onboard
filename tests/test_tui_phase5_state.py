from __future__ import annotations

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
    RequestExplicitRefresh,
    StartForegroundOperation,
)
from g502x_onboard.tui.events import (
    BackupPathChanged,
    ChangePrivacySurface,
    ConfigPathChanged,
    ConfirmationChanged,
    ConfirmationSubmitted,
    EnterReview,
    OperationRequested,
    PersistentCompleted,
    PersistentProgress,
    PreparedReceived,
    ReviewAcknowledged,
    WorkerTransportFault,
)
from g502x_onboard.tui.model import OperationAction, OperationId, TuiModel
from g502x_onboard.tui.update import update
from g502x_onboard.tui.view import view


def prepared() -> PreparedOperation:
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
        preparation_id="prep-phase5",
        kind=PersistentOperationKind.APPLY_CONFIG,
        review=review,
        target_digest="digest",
        compatibility=compatibility,
        observed_preconditions=("safe",),
        host_guard_clear=True,
        required_confirmation_phrase="APPLY CONFIG",
    )


def execution_model(target: PersistentPhase):
    op = OperationId("op-aaaaaaaa")
    model, _ = update(
        TuiModel(),
        OperationRequested(
            op,
            OperationAction.PREPARE_PERSISTENT,
            persistent_kind=PersistentOperationKind.APPLY_CONFIG,
        ),
    )
    model, _ = update(model, PreparedReceived(op, prepared()))
    model, _ = update(model, EnterReview(op))
    model, _ = update(model, ReviewAcknowledged(op, True))
    model, _ = update(model, ConfirmationChanged(op, "APPLY CONFIG"))
    model, effects = update(model, ConfirmationSubmitted(op))
    assert len(effects) == 1
    for phase in (
        PersistentPhase.REVALIDATING,
        PersistentPhase.ARMED,
        PersistentPhase.WRITING,
        PersistentPhase.RECONCILING,
        PersistentPhase.POST_VALIDATING,
    ):
        if phase is target:
            if phase is PersistentPhase.REVALIDATING:
                pass
        model, _ = update(
            model,
            PersistentProgress(
                op,
                PersistentPhaseSnapshot(
                    phase=phase,
                    kind=PersistentOperationKind.APPLY_CONFIG,
                    cancellation_allowed=phase in {
                        PersistentPhase.REVALIDATING,
                        PersistentPhase.ARMED,
                    },
                ),
            ),
        )
        if phase is target:
            break
    return op, model


class Phase5StateTests(unittest.TestCase):
    def test_typed_paths_flow_into_effects_not_widget_closures(self):
        op = OperationId("op-11111111")
        model, _ = update(TuiModel(), ConfigPathChanged("/tmp/local.json"))
        model, effects = update(
            model,
            OperationRequested(
                op,
                OperationAction.PREPARE_PERSISTENT,
                persistent_kind=PersistentOperationKind.APPLY_CONFIG,
            ),
        )
        self.assertEqual(
            effects,
            (
                PreparePersistentOperation(
                    op,
                    PersistentOperationKind.APPLY_CONFIG,
                    config_path="/tmp/local.json",
                ),
            ),
        )

        model = TuiModel()
        model, _ = update(model, BackupPathChanged("/tmp/backup"))
        model, effects = update(
            model,
            OperationRequested(
                op,
                OperationAction.PREPARE_PERSISTENT,
                persistent_kind=PersistentOperationKind.RESTORE_BACKUP,
            ),
        )
        self.assertEqual(effects[0].backup_path, "/tmp/backup")

    def test_plan_effect_carries_typed_config_path(self):
        op = OperationId("op-22222222")
        model, _ = update(TuiModel(), ConfigPathChanged("/tmp/local.json"))
        model, effects = update(model, OperationRequested(op, OperationAction.PLAN))
        self.assertEqual(
            effects,
            (
                StartForegroundOperation(
                    op,
                    OperationAction.PLAN,
                    config_path="/tmp/local.json",
                ),
            ),
        )

    def test_refresh_user_intent_has_one_canonical_effect(self):
        from g502x_onboard.tui.events import RefreshRequested

        op = OperationId("op-33333333")
        model, effects = update(TuiModel(), RefreshRequested(op))
        self.assertEqual(effects, (RequestExplicitRefresh(op),))
        self.assertIs(model.active.action, OperationAction.REFRESH)

    def test_writing_fault_remains_active_unresolved_without_terminal_truth(self):
        for phase in (
            PersistentPhase.WRITING,
            PersistentPhase.RECONCILING,
            PersistentPhase.POST_VALIDATING,
        ):
            with self.subTest(phase=phase):
                op, model = execution_model(phase)
                fault = ApplicationError(
                    ErrorCode.BACKEND_FAILURE,
                    "transport failed",
                    PrivacyClass.SHAREABLE,
                )
                after, effects = update(model, WorkerTransportFault(op, fault))
                self.assertEqual(effects, ())
                self.assertIsNotNone(after.active)
                self.assertIs(after.active.phase, phase)
                self.assertTrue(after.active.worker_fault_unresolved)
                self.assertIsNone(after.terminal)
                vm = view(after)
                self.assertTrue(vm.worker_fault_unresolved)
                self.assertIn("OUTCOME UNKNOWN", vm.safety_label)
                self.assertNotIn("SUCCESS", vm.safety_label)
                self.assertNotIn("FAILED", vm.safety_label)

    def test_prewrite_worker_fault_becomes_sanitized_adapter_failure(self):
        op = OperationId("op-44444444")
        model, _ = update(TuiModel(), OperationRequested(op, OperationAction.STATUS))
        fault = ApplicationError(
            ErrorCode.BACKEND_FAILURE,
            "adapter failed",
            PrivacyClass.SHAREABLE,
        )
        after, _ = update(model, WorkerTransportFault(op, fault))
        self.assertIsNone(after.active)
        self.assertEqual(after.terminal.error_code, ErrorCode.BACKEND_FAILURE)

    def test_stale_worker_fault_cannot_mutate_current_operation(self):
        op, model = execution_model(PersistentPhase.WRITING)
        before = model
        after, effects = update(
            model,
            WorkerTransportFault(
                OperationId("op-bbbbbbbb"),
                ApplicationError(
                    ErrorCode.BACKEND_FAILURE,
                    "late",
                    PrivacyClass.SHAREABLE,
                ),
            ),
        )
        self.assertEqual(after, before)
        self.assertEqual(effects, ())

    def test_authoritative_completion_wins_after_unresolved_fault(self):
        op, model = execution_model(PersistentPhase.WRITING)
        model, _ = update(
            model,
            WorkerTransportFault(
                op,
                ApplicationError(
                    ErrorCode.BACKEND_FAILURE,
                    "unresolved",
                    PrivacyClass.SHAREABLE,
                ),
            ),
        )
        result = PersistentExecutionResult(
            operation_kind=PersistentOperationKind.APPLY_CONFIG,
            terminal_phase=PersistentPhase.SUCCEEDED,
            success=True,
            pre_write_status="ok",
            writing_started=True,
            reconciliation_completed=True,
            post_validation_completed=True,
            message="authoritative success",
            phase_trace=(
                PersistentPhase.REVALIDATING,
                PersistentPhase.ARMED,
                PersistentPhase.WRITING,
                PersistentPhase.RECONCILING,
                PersistentPhase.POST_VALIDATING,
                PersistentPhase.SUCCEEDED,
            ),
        )
        model, _ = update(model, PersistentCompleted(op, result))
        self.assertIsNone(model.active)
        self.assertEqual(view(model).terminal_label, "SUCCESS")

    def test_shareable_downgrade_clears_sensitive_paths_and_prepared_state(self):
        op = OperationId("op-55555555")
        model, _ = update(TuiModel(), ConfigPathChanged("/private/config.json"))
        model, _ = update(model, BackupPathChanged("/private/backup"))
        model, _ = update(
            model,
            OperationRequested(
                op,
                OperationAction.PREPARE_PERSISTENT,
                persistent_kind=PersistentOperationKind.APPLY_CONFIG,
            ),
        )
        model, _ = update(model, PreparedReceived(op, prepared()))
        model, _ = update(model, ChangePrivacySurface(PrivacyClass.SHAREABLE))
        self.assertEqual(model.config_path_input, "")
        self.assertEqual(model.backup_path_input, "")
        self.assertIsNone(model.prepared)
        self.assertIsNone(model.active)


if __name__ == "__main__":
    unittest.main()
