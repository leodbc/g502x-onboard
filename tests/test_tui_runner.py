from __future__ import annotations

import unittest

from g502x_onboard.application.models import (
    ApplicationError,
    ApplyReview,
    CompatibilityObservation,
    ErrorCode,
    OperationResult,
    PersistentExecutionResult,
    PersistentOperationKind,
    PersistentPhase,
    PersistentPhaseSnapshot,
    PreparedOperation,
    PrivacyClass,
    StatusSnapshot,
    WriteEligibility,
)
from g502x_onboard.tui.effects import (
    ExecutePreparedOperation,
    RequestCooperativeCancellation,
    RequestExplicitRefresh,
)
from g502x_onboard.tui.events import (
    ApplicationCompleted,
    CancellationAcknowledged,
    PersistentCompleted,
    PersistentProgress,
    WorkerTransportFault,
)
from g502x_onboard.tui.model import OperationId
from g502x_onboard.tui.runner import EffectRunner


def make_prepared() -> PreparedOperation:
    compatibility = CompatibilityObservation(
        architecture="compatible",
        transport="tested",
        identity="stable",
        write_allowed=True,
        eligibility=WriteEligibility.ELIGIBLE,
    )
    return PreparedOperation(
        preparation_id="prep-runner",
        kind=PersistentOperationKind.APPLY_CONFIG,
        review=ApplyReview(
            config_name="fixture.json",
            config_path="/private/fixture.json",
            enabled_profiles=(2,),
            profile_names=(),
            managed_sectors=(0, 2),
            warnings=(),
            plan_digest="digest",
        ),
        target_digest="digest",
        compatibility=compatibility,
        observed_preconditions=("safe",),
        host_guard_clear=True,
        required_confirmation_phrase="APPLY CONFIG",
    )


class RefreshFacade:
    def __init__(self):
        self.calls = []

    def status(self, *, private):
        self.calls.append(("status", private))
        value = StatusSnapshot(
            active_profile=1,
            descriptor={},
            summary={},
            enabled_profiles=(1, 2),
            privacy=PrivacyClass.SHAREABLE,
        )
        return OperationResult(ok=True, value=value, privacy=value.privacy)


class PersistentFacade:
    def __init__(self, phases=(), *, raise_after=False, success=True):
        self.phases = tuple(phases)
        self.raise_after = raise_after
        self.success = success
        self.tokens = []
        self.calls = 0

    def execute_prepared(self, prepared, confirmation, *, cancellation, observer):
        self.calls += 1
        self.tokens.append(cancellation)
        for phase in self.phases:
            observer(
                PersistentPhaseSnapshot(
                    phase=phase,
                    kind=prepared.kind,
                    cancellation_allowed=phase in {
                        PersistentPhase.REVALIDATING,
                        PersistentPhase.ARMED,
                    },
                )
            )
        if self.raise_after:
            raise RuntimeError("synthetic transport fault")
        value = PersistentExecutionResult(
            operation_kind=prepared.kind,
            terminal_phase=(
                PersistentPhase.SUCCEEDED if self.success else PersistentPhase.FAILED
            ),
            success=self.success,
            pre_write_status="ok",
            writing_started=PersistentPhase.WRITING in self.phases,
            reconciliation_completed=PersistentPhase.RECONCILING in self.phases,
            post_validation_completed=PersistentPhase.POST_VALIDATING in self.phases,
            error_code=None if self.success else ErrorCode.BACKEND_FAILURE,
            message="authoritative result",
            phase_trace=self.phases,
        )
        return OperationResult(ok=True, value=value, privacy=value.privacy)


class EffectRunnerTests(unittest.TestCase):
    def test_explicit_refresh_dispatches_exactly_one_status_facade_call(self):
        emitted = []
        facade = RefreshFacade()
        runner = EffectRunner(facade, emitted.append)
        effect = RequestExplicitRefresh(OperationId("op-11111111"))

        self.assertTrue(runner.run(effect))
        self.assertEqual(facade.calls, [("status", False)])
        self.assertEqual(sum(isinstance(e, ApplicationCompleted) for e in emitted), 1)

        self.assertFalse(runner.run(effect))
        self.assertEqual(facade.calls, [("status", False)])

    def test_pending_cancellation_is_bound_to_exact_execution_token(self):
        emitted = []
        facade = PersistentFacade(
            phases=(PersistentPhase.REVALIDATING,),
            success=False,
        )
        runner = EffectRunner(facade, emitted.append)
        op = OperationId("op-22222222")

        runner.run(RequestCooperativeCancellation(op))
        self.assertTrue(any(isinstance(e, CancellationAcknowledged) for e in emitted))
        runner.run(ExecutePreparedOperation(op, make_prepared(), "APPLY CONFIG"))

        self.assertEqual(facade.calls, 1)
        self.assertTrue(facade.tokens[0].is_cancelled)

    def test_authoritative_persistent_return_maps_to_persistent_completed(self):
        emitted = []
        phases = (
            PersistentPhase.REVALIDATING,
            PersistentPhase.ARMED,
            PersistentPhase.WRITING,
            PersistentPhase.RECONCILING,
            PersistentPhase.POST_VALIDATING,
        )
        facade = PersistentFacade(phases=phases, success=True)
        runner = EffectRunner(facade, emitted.append)
        op = OperationId("op-33333333")

        runner.run(ExecutePreparedOperation(op, make_prepared(), "APPLY CONFIG"))

        progress = [e for e in emitted if isinstance(e, PersistentProgress)]
        completed = [e for e in emitted if isinstance(e, PersistentCompleted)]
        self.assertEqual([e.snapshot.phase for e in progress], list(phases))
        self.assertEqual(len(completed), 1)
        self.assertTrue(completed[0].result.success)

    def test_writing_and_later_transport_faults_are_unresolved_not_terminal(self):
        for fault_phase in (
            PersistentPhase.WRITING,
            PersistentPhase.RECONCILING,
            PersistentPhase.POST_VALIDATING,
        ):
            with self.subTest(fault_phase=fault_phase):
                emitted = []
                ordered = [
                    PersistentPhase.REVALIDATING,
                    PersistentPhase.ARMED,
                    PersistentPhase.WRITING,
                    PersistentPhase.RECONCILING,
                    PersistentPhase.POST_VALIDATING,
                ]
                phases = tuple(ordered[: ordered.index(fault_phase) + 1])
                facade = PersistentFacade(phases=phases, raise_after=True)
                runner = EffectRunner(facade, emitted.append)
                runner.run(
                    ExecutePreparedOperation(
                        OperationId("op-44444444"),
                        make_prepared(),
                        "APPLY CONFIG",
                    )
                )

                faults = [e for e in emitted if isinstance(e, WorkerTransportFault)]
                self.assertEqual(len(faults), 1)
                self.assertIn("outcome is unresolved", faults[0].error.message)
                self.assertFalse(any(isinstance(e, PersistentCompleted) for e in emitted))

    def test_prewrite_transport_fault_is_sanitized(self):
        for phases in (
            (),
            (PersistentPhase.REVALIDATING,),
            (PersistentPhase.REVALIDATING, PersistentPhase.ARMED),
        ):
            with self.subTest(phases=phases):
                emitted = []
                facade = PersistentFacade(phases=phases, raise_after=True)
                runner = EffectRunner(facade, emitted.append)
                runner.run(
                    ExecutePreparedOperation(
                        OperationId("op-55555555"),
                        make_prepared(),
                        "APPLY CONFIG",
                    )
                )
                faults = [e for e in emitted if isinstance(e, WorkerTransportFault)]
                self.assertEqual(len(faults), 1)
                self.assertNotIn("synthetic", faults[0].error.message)
                self.assertFalse(any(isinstance(e, PersistentCompleted) for e in emitted))


    def test_long_run_terminal_effect_history_is_reclaimed(self):
        emitted = []
        facade = RefreshFacade()
        runner = EffectRunner(facade, emitted.append)

        for index in range(1000):
            effect = RequestExplicitRefresh(
                OperationId(f"op-{index:08x}")
            )
            self.assertTrue(runner.run(effect))

        self.assertEqual(len(facade.calls), 1000)
        self.assertEqual(runner._dispatched, {})
        self.assertEqual(runner._tokens, {})
        self.assertEqual(runner._pending_cancellation, set())

    def test_delayed_persistent_duplicate_is_rejected_after_terminal_retirement(self):
        emitted = []
        facade = PersistentFacade(
            phases=(PersistentPhase.REVALIDATING,),
            success=False,
        )
        runner = EffectRunner(facade, emitted.append)
        op = OperationId("op-deadbeef")
        effect = ExecutePreparedOperation(
            op, make_prepared(), "APPLY CONFIG"
        )

        self.assertTrue(runner.run(effect))
        self.assertFalse(runner.run(effect))
        self.assertEqual(facade.calls, 1)
        self.assertEqual(runner._dispatched, {})
        self.assertEqual(runner._tokens, {})

    def test_retirement_clears_pending_cancel_and_same_visible_id_cannot_misroute(self):
        emitted = []
        facade = PersistentFacade(
            phases=(PersistentPhase.REVALIDATING,),
            success=False,
        )
        runner = EffectRunner(facade, emitted.append)
        old = OperationId("op-deadbeef")

        self.assertTrue(runner.run(RequestCooperativeCancellation(old)))
        self.assertIn(old.issuance, runner._pending_cancellation)
        runner.retire_operation(old)
        self.assertEqual(runner._pending_cancellation, set())

        new = OperationId("op-deadbeef")
        self.assertNotEqual(old, new)
        self.assertTrue(
            runner.run(
                ExecutePreparedOperation(
                    new, make_prepared(), "APPLY CONFIG"
                )
            )
        )
        self.assertEqual(facade.calls, 1)
        self.assertFalse(facade.tokens[0].is_cancelled)
        self.assertEqual(runner._tokens, {})
        self.assertEqual(runner._pending_cancellation, set())


if __name__ == "__main__":
    unittest.main()
