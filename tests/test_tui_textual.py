from __future__ import annotations

import asyncio
import os
from threading import Event
import unittest
from unittest.mock import patch

from g502x_onboard.application.models import (
    ApplyReview,
    CompatibilityObservation,
    ErrorCode,
    OperationResult,
    PersistentExecutionResult,
    PersistentOperationKind,
    PersistentPhase,
    PersistentPhaseSnapshot,
    PlanSnapshot,
    PreparedOperation,
    PrivacyClass,
    ProbeSnapshot,
    ProfileSwitchResult,
    PublicReportSnapshot,
    RestoreBackupReview,
    RestoreBaselineReview,
    StatusSnapshot,
    ValidationSnapshot,
    WriteEligibility,
)
from g502x_onboard.tui.events import ConfirmationSubmitted
from g502x_onboard.tui.model import OperationId

try:
    from textual import events as textual_events
    from textual.widgets import Button, Input, Static
    from g502x_onboard.tui.app import G502XTuiApp
    TEXTUAL_AVAILABLE = True
except ModuleNotFoundError:
    TEXTUAL_AVAILABLE = False
    G502XTuiApp = None


def make_prepared(
    kind: PersistentOperationKind = PersistentOperationKind.APPLY_CONFIG,
) -> PreparedOperation:
    compatibility = CompatibilityObservation(
        architecture="compatible",
        transport="tested",
        identity="stable",
        write_allowed=True,
        eligibility=WriteEligibility.ELIGIBLE,
    )
    if kind is PersistentOperationKind.APPLY_CONFIG:
        review = ApplyReview(
            config_name="x.json",
            config_path="x.json",
            enabled_profiles=(2,),
            profile_names=((2, "WORK"),),
            managed_sectors=(0, 2, 8),
            warnings=(),
            plan_digest="digest",
        )
        phrase = "APPLY CONFIG"
    elif kind is PersistentOperationKind.RESTORE_BACKUP:
        review = RestoreBackupReview(
            backup_name="backup.bin",
            managed_sectors=(0, 2, 8),
            protected_sectors=(6, 7),
            target_digest="digest",
        )
        phrase = "RESTORE BACKUP"
    else:
        review = RestoreBaselineReview(
            managed_sectors=(0, 2, 8),
            protected_sectors=(6, 7),
            target_digest="digest",
        )
        phrase = "RESTORE BASELINE"

    return PreparedOperation(
        preparation_id=f"prep-{kind.value}",
        kind=kind,
        review=review,
        target_digest="digest",
        compatibility=compatibility,
        observed_preconditions=("safe",),
        host_guard_clear=True,
        required_confirmation_phrase=phrase,
    )


class HarnessFacade:
    def __init__(self, *, read_only=False, execute_mode="success"):
        self.calls = []
        self.read_only = read_only
        self.execute_mode = execute_mode
        self.execute_calls = 0
        self.phase_events = {
            phase: Event()
            for phase in (
                PersistentPhase.REVALIDATING,
                PersistentPhase.ARMED,
                PersistentPhase.WRITING,
                PersistentPhase.RECONCILING,
                PersistentPhase.POST_VALIDATING,
            )
        }
        self.release = Event()
        self.cancel_seen = Event()

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

    def probe(self):
        self.calls.append(("probe",))
        compatibility = CompatibilityObservation(
            architecture="unknown" if self.read_only else "compatible",
            transport="tested",
            identity="stable",
            write_allowed=not self.read_only,
            eligibility=(
                WriteEligibility.READ_ONLY
                if self.read_only
                else WriteEligibility.ELIGIBLE
            ),
        )
        value = ProbeSnapshot(
            device_name="G502 X LIGHTSPEED",
            protocol="HID++ 2.0",
            active_profile=1,
            compatibility=compatibility,
        )
        return OperationResult(ok=True, value=value, privacy=value.privacy)


    def validate(self):
        self.calls.append(("validate",))
        value = ValidationSnapshot(
            ok=True,
            enabled_profiles=(1, 2),
            error_count=0,
            warning_count=0,
            referenced_macro_starts=0,
            recovery_ok=True,
        )
        return OperationResult(ok=True, value=value, privacy=value.privacy)

    def plan(self, config_path):
        self.calls.append(("plan", str(config_path)))
        value = PlanSnapshot(
            config_path=str(config_path),
            plan={"enabled_profiles": [2]},
            rendered_json='{"enabled_profiles":[2]}',
        )
        return OperationResult(ok=True, value=value, privacy=value.privacy)

    def switch_profile(self, target, confirmation):
        self.calls.append(("switch_profile", target, confirmation))
        value = ProfileSwitchResult(
            active_profile=target,
            confirmation_phrase=confirmation,
        )
        return OperationResult(ok=True, value=value, privacy=value.privacy)

    def report_probe(self, *, pid, index):
        self.calls.append(("report_probe", pid, index))
        value = PublicReportSnapshot(
            payload={"format": "g502x-device-report-v1", "privacy": "shareable"}
        )
        return OperationResult(ok=True, value=value, privacy=value.privacy)

    def prepare_apply(self, config_path):
        self.calls.append(("prepare_apply", str(config_path)))
        value = make_prepared()
        return OperationResult(ok=True, value=value, privacy=value.privacy)

    def prepare_restore_backup(self, backup_path):
        self.calls.append(("prepare_restore_backup", str(backup_path)))
        value = make_prepared(PersistentOperationKind.RESTORE_BACKUP)
        return OperationResult(ok=True, value=value, privacy=value.privacy)

    def prepare_restore_baseline(self):
        self.calls.append(("prepare_restore_baseline",))
        value = make_prepared(PersistentOperationKind.RESTORE_BASELINE)
        return OperationResult(ok=True, value=value, privacy=value.privacy)

    def execute_prepared(self, prepared, confirmation, *, cancellation, observer):
        self.execute_calls += 1
        self.calls.append(("execute_prepared", confirmation))
        observer(
            PersistentPhaseSnapshot(
                PersistentPhase.REVALIDATING,
                prepared.kind,
                True,
            )
        )
        self.phase_events[PersistentPhase.REVALIDATING].set()
        if self.execute_mode == "cancel-before-write":
            for _ in range(400):
                if cancellation.is_cancelled:
                    self.cancel_seen.set()
                    break
                if self.release.wait(0.005):
                    break
            value = PersistentExecutionResult(
                operation_kind=prepared.kind,
                terminal_phase=PersistentPhase.FAILED,
                success=False,
                pre_write_status="cancelled-before-write",
                writing_started=False,
                reconciliation_completed=False,
                post_validation_completed=False,
                error_code=ErrorCode.CANCELLED,
                message="cancelled before write",
                phase_trace=(PersistentPhase.REVALIDATING, PersistentPhase.FAILED),
            )
            return OperationResult(ok=True, value=value, privacy=value.privacy)

        observer(PersistentPhaseSnapshot(PersistentPhase.ARMED, prepared.kind, True))
        self.phase_events[PersistentPhase.ARMED].set()
        if self.execute_mode == "cancel-at-armed":
            for _ in range(400):
                if cancellation.is_cancelled:
                    self.cancel_seen.set()
                    break
                if self.release.wait(0.005):
                    break
            value = PersistentExecutionResult(
                operation_kind=prepared.kind,
                terminal_phase=PersistentPhase.FAILED,
                success=False,
                pre_write_status="cancelled-before-write",
                writing_started=False,
                reconciliation_completed=False,
                post_validation_completed=False,
                error_code=ErrorCode.CANCELLED,
                message="cancelled before write",
                phase_trace=(
                    PersistentPhase.REVALIDATING,
                    PersistentPhase.ARMED,
                    PersistentPhase.FAILED,
                ),
            )
            return OperationResult(ok=True, value=value, privacy=value.privacy)
        observer(PersistentPhaseSnapshot(PersistentPhase.WRITING, prepared.kind, False))
        self.phase_events[PersistentPhase.WRITING].set()
        if self.execute_mode == "fault-after-writing":
            raise RuntimeError("synthetic worker transport fault")
        if self.execute_mode == "block-writing":
            self.release.wait(2.0)
        observer(PersistentPhaseSnapshot(PersistentPhase.RECONCILING, prepared.kind, False))
        self.phase_events[PersistentPhase.RECONCILING].set()
        observer(PersistentPhaseSnapshot(PersistentPhase.POST_VALIDATING, prepared.kind, False))
        self.phase_events[PersistentPhase.POST_VALIDATING].set()
        value = PersistentExecutionResult(
            operation_kind=prepared.kind,
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
        return OperationResult(ok=True, value=value, privacy=value.privacy)


async def wait_until(pilot, predicate, message: str, limit: int = 12) -> None:
    for _ in range(limit):
        if predicate():
            return
        await pilot.pause()
    raise AssertionError(message)


async def wait_thread_event(event: Event, message: str, timeout: float = 3.0) -> None:
    if not await asyncio.to_thread(event.wait, timeout):
        raise AssertionError(message)


async def focus_id(pilot, app, target: str, limit: int = 50):
    for _ in range(limit):
        focused = app.focused
        if focused is not None and focused.id == target:
            return
        await pilot.press("tab")
        await pilot.pause()
    raise AssertionError(
        f"could not keyboard-focus #{target}; focused={getattr(app.focused, 'id', None)}"
    )


@unittest.skipUnless(TEXTUAL_AVAILABLE, "Textual optional dependencies are not installed")
class TextualHarnessTests(unittest.IsolatedAsyncioTestCase):
    async def test_safety_key_bindings_are_priority(self):
        bindings = {binding.key: binding for binding in G502XTuiApp.BINDINGS}
        self.assertTrue(bindings["escape"].priority)
        self.assertTrue(bindings["ctrl+q"].priority)

    async def test_reused_factory_object_is_reissued_with_distinct_internal_identity(self):
        facade = HarnessFacade()
        fixed = OperationId("op-deadbeef")
        app = G502XTuiApp(
            facade=facade,
            operation_id_factory=lambda: fixed,
        )

        first = app._new_operation_id()
        second = app._new_operation_id()
        self.assertEqual(str(first), "op-deadbeef")
        self.assertEqual(str(second), "op-deadbeef")
        self.assertNotEqual(first, second)
        self.assertNotEqual(first.issuance, second.issuance)

    async def test_mount_80x24_exposes_semantic_safety_labels(self):
        facade = HarnessFacade()
        app = G502XTuiApp(facade=facade)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            self.assertFalse(app.query_one("#constrained", Static).display)
            summary = str(app.query_one("#summary", Static).render())
            self.assertIn("Privacy: SHAREABLE", summary)
            self.assertIn("State: IDLE", summary)
            self.assertEqual(facade.calls, [])

    async def test_below_minimum_is_explicit_and_hides_safety_controls(self):
        facade = HarnessFacade()
        app = G502XTuiApp(facade=facade)
        async with app.run_test(size=(79, 23)) as pilot:
            await pilot.pause()
            self.assertTrue(app.query_one("#constrained", Static).display)
            self.assertFalse(app.query_one("#main").display)
            self.assertEqual(facade.calls, [])

            app.set_focus(None)
            await pilot.press("p", "r", "v", "n", "a", "b", "l", "s", "g")
            await pilot.pause()
            self.assertEqual(facade.calls, [])
            self.assertIsNone(app.tui_model.active)

    async def test_privacy_downgrade_clears_hidden_sensitive_widget_values(self):
        facade = HarnessFacade()
        app = G502XTuiApp(facade=facade)
        async with app.run_test(size=(80, 24)) as pilot:
            await focus_id(pilot, app, "config-path")
            await pilot.press("s", "e", "c", "r", "e", "t")
            await focus_id(pilot, app, "backup-path")
            await pilot.press("b", "a", "c", "k", "u", "p")
            await wait_until(
                pilot,
                lambda: (
                    app.query_one("#config-path", Input).value == "secret"
                    and app.query_one("#backup-path", Input).value == "backup"
                ),
                "sensitive widget inputs did not populate",
            )

            await pilot.resize_terminal(79, 23)
            await wait_until(
                pilot,
                lambda: app.query_one("#constrained", Static).display,
                "constrained layout did not settle after resize",
            )

            # LOCAL_SENSITIVE -> PRIVATE_DIAGNOSTIC -> SHAREABLE.
            app.action_privacy()
            app.action_privacy()
            await pilot.pause()

            self.assertIs(app.tui_model.surface_privacy, PrivacyClass.SHAREABLE)
            self.assertEqual(app.tui_model.config_path_input, "")
            self.assertEqual(app.tui_model.backup_path_input, "")
            self.assertEqual(app.query_one("#config-path", Input).value, "")
            self.assertEqual(app.query_one("#backup-path", Input).value, "")
            self.assertIsNone(app.tui_model.disclosure)

    async def test_idle_navigation_help_focus_resize_do_not_poll_then_refresh_once(self):
        facade = HarnessFacade()
        app = G502XTuiApp(facade=facade)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("h")
            await pilot.press("h")
            await pilot.press("tab", "tab", "tab")
            await pilot.resize_terminal(100, 30)
            await pilot.resize_terminal(80, 24)
            await pilot.pause()
            self.assertEqual(facade.calls, [])

            app.set_focus(None)
            await pilot.press("r")
            await wait_until(
                pilot,
                lambda: facade.calls == [("status", False)],
                "explicit refresh did not produce exactly one status call",
            )

    async def test_read_only_probe_disables_mutating_actions_with_visible_reason(self):
        facade = HarnessFacade(read_only=True)
        app = G502XTuiApp(facade=facade)
        async with app.run_test(size=(80, 24)) as pilot:
            app.set_focus(None)
            await pilot.press("p")
            await wait_until(
                pilot,
                lambda: app.tui_model.read_only and app.tui_model.active is None,
                "read-only probe did not settle",
            )
            self.assertTrue(app.query_one("#apply", Button).disabled)
            self.assertTrue(app.query_one("#profile-switch", Button).disabled)
            detail = str(app.query_one("#detail", Static).render())
            self.assertIn("READ ONLY", detail)
            self.assertIn("Read-only reason", detail)

    async def test_no_color_keeps_semantics_in_text(self):
        facade = HarnessFacade(read_only=True)
        with patch.dict(os.environ, {"NO_COLOR": "1"}):
            app = G502XTuiApp(facade=facade)
            async with app.run_test(size=(80, 24)) as pilot:
                app.set_focus(None)
                await pilot.press("p")
                await wait_until(
                    pilot,
                    lambda: app.tui_model.read_only and app.tui_model.active is None,
                    "NO_COLOR probe did not settle",
                )
                summary = str(app.query_one("#summary", Static).render())
                self.assertIn("NO_COLOR: semantic labels active", summary)
                self.assertIn("READ ONLY", summary)


    async def test_focused_text_input_consumes_letter_shortcuts_without_dispatch(self):
        facade = HarnessFacade()
        app = G502XTuiApp(facade=facade)
        async with app.run_test(size=(80, 24)) as pilot:
            await focus_id(pilot, app, "config-path")
            await pilot.press("a", "p", "p", "l", "y", "q", "g", "r", "v", "n", "s", "b", "d", "h")
            await wait_until(
                pilot,
                lambda: app.tui_model.config_path_input == "applyqgrvnsbdh",
                "focused Input did not consume printable shortcut letters",
            )
            self.assertEqual(facade.calls, [])
            self.assertIsNone(app.tui_model.active)

    async def test_keyboard_shortcuts_cover_all_remaining_operation_entrypoints(self):
        facade = HarnessFacade()
        app = G502XTuiApp(facade=facade)
        async with app.run_test(size=(80, 24)) as pilot:
            async def press_and_wait(key, call_name):
                app.set_focus(None)
                await pilot.press(key)
                await wait_until(
                    pilot,
                    lambda: (
                        any(call[0] == call_name for call in facade.calls)
                        and app.tui_model.active is None
                    ),
                    f"{key} did not complete {call_name}",
                )

            await press_and_wait("p", "probe")
            await press_and_wait("r", "status")
            await press_and_wait("v", "validate")

            await focus_id(pilot, app, "config-path")
            await pilot.press("x", ".", "j", "s", "o", "n")
            await wait_until(
                pilot,
                lambda: app.tui_model.config_path_input == "x.json",
                "plan path did not reach model",
            )
            await press_and_wait("n", "plan")
            await press_and_wait("g", "report_probe")

            await focus_id(pilot, app, "profile-target")
            await pilot.press("backspace", "2")
            await focus_id(pilot, app, "profile-confirmation")
            profile_confirmation = app.query_one("#profile-confirmation", Input)
            for character in "ENTER PROFILE 2":
                key = "space" if character == " " else character
                profile_confirmation.post_message(
                    textual_events.Key(key, character)
                )
            await wait_until(
                pilot,
                lambda: (
                    app.tui_model.profile_target_input == "2"
                    and app.tui_model.profile_confirmation_input == "ENTER PROFILE 2"
                ),
                "profile keyboard inputs did not reach model",
            )
            await press_and_wait("s", "switch_profile")

            # Each persistent shortcut must reach the canonical preparation path.
            # Esc then abandons the prepared capability without executing it.
            await focus_id(pilot, app, "config-path")
            await pilot.press("x", ".", "j", "s", "o", "n")
            app.set_focus(None)
            await pilot.press("a")
            await wait_until(
                pilot,
                lambda: (
                    app.tui_model.prepared is not None
                    and any(call[0] == "prepare_apply" for call in facade.calls)
                ),
                "a did not prepare apply",
            )
            await pilot.press("escape")
            await wait_until(
                pilot,
                lambda: app.tui_model.active is None,
                "Esc did not abandon apply preparation",
            )

            await focus_id(pilot, app, "backup-path")
            for character in "backup.bin":
                await pilot.press(character)
            app.set_focus(None)
            await pilot.press("b")
            await wait_until(
                pilot,
                lambda: (
                    app.tui_model.prepared is not None
                    and any(
                        call[0] == "prepare_restore_backup"
                        for call in facade.calls
                    )
                ),
                "b did not prepare backup restore",
            )
            await pilot.press("escape")
            await wait_until(
                pilot,
                lambda: app.tui_model.active is None,
                "Esc did not abandon backup preparation",
            )

            app.set_focus(None)
            await pilot.press("l")
            await wait_until(
                pilot,
                lambda: (
                    app.tui_model.prepared is not None
                    and any(
                        call[0] == "prepare_restore_baseline"
                        for call in facade.calls
                    )
                ),
                "l did not prepare baseline restore",
            )
            await pilot.press("escape")
            await wait_until(
                pilot,
                lambda: app.tui_model.active is None,
                "Esc did not abandon baseline preparation",
            )

            calls_before_local_ui = len(facade.calls)
            app.set_focus(None)
            await pilot.press("h")
            await pilot.press("h")
            await pilot.press("d")
            await pilot.press("d")
            await pilot.pause()
            self.assertEqual(len(facade.calls), calls_before_local_ui)

            call_names = [call[0] for call in facade.calls]
            for expected in (
                "probe",
                "status",
                "validate",
                "plan",
                "report_probe",
                "switch_profile",
                "prepare_apply",
                "prepare_restore_backup",
                "prepare_restore_baseline",
            ):
                self.assertIn(expected, call_names)

    async def _keyboard_prepare_to_confirmation(self, app, pilot):
        await focus_id(pilot, app, "config-path")
        await pilot.press("x", ".", "j", "s", "o", "n")
        await wait_until(
            pilot,
            lambda: app.tui_model.config_path_input == "x.json",
            "keyboard config path did not reach the model",
        )

        await focus_id(pilot, app, "apply")
        await pilot.press("enter")
        await wait_until(
            pilot,
            lambda: app.tui_model.prepared is not None,
            "prepare worker did not return a prepared operation",
        )

        await focus_id(pilot, app, "review-ack")
        await pilot.press("space")
        await wait_until(
            pilot,
            lambda: (
                app.tui_model.review_acknowledged
                and app.query_one("#persistent-confirm", Input).display
            ),
            "review acknowledgement did not enter confirmation",
        )

        await focus_id(pilot, app, "persistent-confirm")
        confirmation = app.query_one("#persistent-confirm", Input)
        for character in "APPLY CONFIG":
            key = "space" if character == " " else character
            confirmation.post_message(textual_events.Key(key, character))
        await wait_until(
            pilot,
            lambda: app.tui_model.confirmation_input == "APPLY CONFIG",
            "keyboard confirmation did not reach the model",
        )

    async def test_keyboard_only_prepare_review_exact_confirm_success(self):
        facade = HarnessFacade()
        app = G502XTuiApp(facade=facade)
        async with app.run_test(size=(80, 24)) as pilot:
            await self._keyboard_prepare_to_confirmation(app, pilot)

            # Keep the submit itself as a real keyboard Enter. Run it as a task
            # so the test can observe the synchronous facade worker phases
            # without requiring Pilot.press() to become the worker scheduler.
            submit = asyncio.create_task(pilot.press("enter"))
            await wait_thread_event(
                facade.phase_events[PersistentPhase.POST_VALIDATING],
                "keyboard Enter did not drive the operation through post-validation",
            )
            await submit
            await pilot.pause()

            self.assertEqual(facade.execute_calls, 1)
            self.assertIsNotNone(app.tui_model.terminal)
            self.assertEqual(app.tui_model.terminal.outcome.value, "success")
            detail = str(app.query_one("#detail", Static).render())
            self.assertIn("Terminal: SUCCESS", detail)

    async def test_double_submit_dispatches_one_execution(self):
        facade = HarnessFacade(execute_mode="block-writing")
        app = G502XTuiApp(facade=facade)
        async with app.run_test(size=(80, 24)) as pilot:
            await self._keyboard_prepare_to_confirmation(app, pilot)
            operation_id = app.tui_model.active.operation_id
            app._accept_event(ConfirmationSubmitted(operation_id))
            app._accept_event(ConfirmationSubmitted(operation_id))
            await wait_thread_event(
                facade.phase_events[PersistentPhase.WRITING],
                "persistent worker never reached WRITING",
            )
            self.assertEqual(facade.execute_calls, 1)
            facade.release.set()
            await pilot.pause()
            self.assertIsNotNone(app.tui_model.terminal)

    async def test_cancel_before_write_uses_cooperative_token(self):
        facade = HarnessFacade(execute_mode="cancel-before-write")
        app = G502XTuiApp(facade=facade)
        async with app.run_test(size=(80, 24)) as pilot:
            await self._keyboard_prepare_to_confirmation(app, pilot)
            operation_id = app.tui_model.active.operation_id
            app._accept_event(ConfirmationSubmitted(operation_id))
            await wait_thread_event(
                facade.phase_events[PersistentPhase.REVALIDATING],
                "persistent worker never reached REVALIDATING",
            )
            self.assertIs(app.tui_model.active.phase, PersistentPhase.REVALIDATING)
            app.action_cancel_or_back()
            await wait_thread_event(
                facade.cancel_seen,
                "cooperative cancellation did not reach the application token",
            )
            facade.release.set()
            await pilot.pause()
            self.assertEqual(app.tui_model.terminal.error_code, ErrorCode.CANCELLED)
            self.assertFalse(app.tui_model.terminal.writing_started)

    async def test_q_safe_quit_prewrite_requests_only_cooperative_cancellation(self):
        for mode, phase in (
            ("cancel-before-write", PersistentPhase.REVALIDATING),
            ("cancel-at-armed", PersistentPhase.ARMED),
        ):
            with self.subTest(mode=mode):
                facade = HarnessFacade(execute_mode=mode)
                app = G502XTuiApp(facade=facade)
                async with app.run_test(size=(80, 24)) as pilot:
                    await self._keyboard_prepare_to_confirmation(app, pilot)
                    operation_id = app.tui_model.active.operation_id
                    app._accept_event(ConfirmationSubmitted(operation_id))
                    await wait_thread_event(
                        facade.phase_events[phase],
                        f"persistent worker never reached {phase.value}",
                    )
                    await pilot.pause()

                    # This must exercise the actual non-priority q binding after
                    # the confirmation Input has been hidden/disabled.
                    await pilot.press("q")
                    await wait_thread_event(
                        facade.cancel_seen,
                        f"q did not route cooperative cancellation at {phase.value}",
                    )
                    facade.release.set()
                    await wait_until(
                        pilot,
                        lambda: app.tui_model.terminal is not None,
                        "authoritative cancellation result did not arrive",
                    )
                    self.assertEqual(
                        app.tui_model.terminal.error_code,
                        ErrorCode.CANCELLED,
                    )
                    self.assertFalse(
                        app.tui_model.terminal.writing_started
                    )

    async def test_safe_quit_during_writing_does_not_cancel_or_exit(self):
        facade = HarnessFacade(execute_mode="block-writing")
        app = G502XTuiApp(facade=facade)
        async with app.run_test(size=(80, 24)) as pilot:
            await self._keyboard_prepare_to_confirmation(app, pilot)
            operation_id = app.tui_model.active.operation_id
            app._accept_event(ConfirmationSubmitted(operation_id))
            await wait_thread_event(
                facade.phase_events[PersistentPhase.WRITING],
                "persistent worker never reached WRITING",
            )
            self.assertIs(app.tui_model.active.phase, PersistentPhase.WRITING)
            app.action_safe_quit()
            self.assertIsNotNone(app.tui_model.active)
            self.assertTrue(app.tui_model.active.cancellation_deferred)
            self.assertFalse(facade.cancel_seen.is_set())
            facade.release.set()
            await pilot.pause()
            self.assertEqual(app.tui_model.terminal.outcome.value, "success")

    async def test_writing_transport_fault_is_visible_unresolved_and_safe_quit_stays_open(self):
        facade = HarnessFacade(execute_mode="fault-after-writing")
        app = G502XTuiApp(facade=facade)
        async with app.run_test(size=(80, 24)) as pilot:
            await self._keyboard_prepare_to_confirmation(app, pilot)
            operation_id = app.tui_model.active.operation_id
            app._accept_event(ConfirmationSubmitted(operation_id))
            await wait_thread_event(
                facade.phase_events[PersistentPhase.WRITING],
                "persistent worker never reached WRITING",
            )
            await pilot.pause()
            self.assertTrue(app.tui_model.active.worker_fault_unresolved)
            self.assertIsNone(app.tui_model.terminal)
            detail = str(app.query_one("#detail", Static).render())
            self.assertIn("OUTCOME UNKNOWN", detail)
            app.action_safe_quit()
            self.assertTrue(app.tui_model.active.worker_fault_unresolved)
            self.assertIsNone(app.tui_model.terminal)


if __name__ == "__main__":
    unittest.main()
