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
    PreparedOperation,
    PrivacyClass,
    ProbeSnapshot,
    StatusSnapshot,
    WriteEligibility,
)

try:
    from textual import events as textual_events
    from textual.widgets import Button, Input, Static
    from g502x_onboard.tui.app import G502XTuiApp
    TEXTUAL_AVAILABLE = True
except ModuleNotFoundError:
    TEXTUAL_AVAILABLE = False
    G502XTuiApp = None


def make_prepared() -> PreparedOperation:
    compatibility = CompatibilityObservation(
        architecture="compatible",
        transport="tested",
        identity="stable",
        write_allowed=True,
        eligibility=WriteEligibility.ELIGIBLE,
    )
    return PreparedOperation(
        preparation_id="prep-harness",
        kind=PersistentOperationKind.APPLY_CONFIG,
        review=ApplyReview(
            config_name="x.json",
            config_path="x.json",
            enabled_profiles=(2,),
            profile_names=((2, "WORK"),),
            managed_sectors=(0, 2, 8),
            warnings=(),
            plan_digest="digest",
        ),
        target_digest="digest",
        compatibility=compatibility,
        observed_preconditions=("safe",),
        host_guard_clear=True,
        required_confirmation_phrase="APPLY CONFIG",
    )


class HarnessFacade:
    def __init__(self, *, read_only=False, execute_mode="success"):
        self.calls = []
        self.read_only = read_only
        self.execute_mode = execute_mode
        self.execute_calls = 0
        self.entered = Event()
        self.release = Event()
        self.cancel_seen = False

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

    def prepare_apply(self, config_path):
        self.calls.append(("prepare_apply", str(config_path)))
        value = make_prepared()
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
        if self.execute_mode == "cancel-before-write":
            self.entered.set()
            for _ in range(400):
                if cancellation.is_cancelled:
                    self.cancel_seen = True
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
        observer(PersistentPhaseSnapshot(PersistentPhase.WRITING, prepared.kind, False))
        self.entered.set()
        if self.execute_mode == "fault-after-writing":
            raise RuntimeError("synthetic worker transport fault")
        if self.execute_mode == "block-writing":
            self.release.wait(2.0)
        observer(PersistentPhaseSnapshot(PersistentPhase.RECONCILING, prepared.kind, False))
        observer(PersistentPhaseSnapshot(PersistentPhase.POST_VALIDATING, prepared.kind, False))
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


async def wait_until(pilot, predicate, message: str, limit: int = 500) -> None:
    for _ in range(limit):
        if predicate():
            return
        # Yield directly to the Textual event loop. pilot.pause() also waits for
        # CPU-idle semantics, which is inappropriate while a thread worker is
        # deliberately active and made the harness timing-dependent.
        await asyncio.sleep(0.01)
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
            await pilot.press("a", "p", "p", "l", "y", "g", "r", "v", "n", "s", "b", "d", "h")
            await wait_until(
                pilot,
                lambda: app.tui_model.config_path_input == "applygrvnsbdh",
                "focused Input did not consume printable shortcut letters",
            )
            self.assertEqual(facade.calls, [])
            self.assertIsNone(app.tui_model.active)

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
            await pilot.press("enter")
            await wait_until(
                pilot,
                lambda: app.tui_model.terminal is not None,
                "persistent success did not reach terminal state",
            )
            self.assertEqual(facade.execute_calls, 1)
            self.assertEqual(app.tui_model.terminal.outcome.value, "success")
            detail = str(app.query_one("#detail", Static).render())
            self.assertIn("Terminal: SUCCESS", detail)

    async def test_double_submit_dispatches_one_execution(self):
        facade = HarnessFacade(execute_mode="block-writing")
        app = G502XTuiApp(facade=facade)
        async with app.run_test(size=(80, 24)) as pilot:
            await self._keyboard_prepare_to_confirmation(app, pilot)
            await pilot.press("enter", "enter")
            await wait_until(
                pilot,
                lambda: facade.entered.is_set(),
                "persistent worker never reached WRITING",
            )
            self.assertEqual(facade.execute_calls, 1)
            facade.release.set()
            await wait_until(
                pilot,
                lambda: app.tui_model.terminal is not None,
                "blocked persistent execution did not finish",
            )

    async def test_keyboard_escape_before_write_uses_cooperative_token(self):
        facade = HarnessFacade(execute_mode="cancel-before-write")
        app = G502XTuiApp(facade=facade)
        async with app.run_test(size=(80, 24)) as pilot:
            await self._keyboard_prepare_to_confirmation(app, pilot)
            await pilot.press("enter")
            await wait_until(
                pilot,
                lambda: (
                    facade.entered.is_set()
                    and app.tui_model.active is not None
                    and app.tui_model.active.phase is PersistentPhase.REVALIDATING
                ),
                "persistent worker never reached REVALIDATING",
            )
            await pilot.press("escape")
            await wait_until(
                pilot,
                lambda: facade.cancel_seen,
                "priority Esc did not reach the cooperative cancellation token",
            )
            facade.release.set()
            await wait_until(
                pilot,
                lambda: app.tui_model.terminal is not None,
                "cancelled execution did not produce authoritative terminal result",
            )
            self.assertEqual(app.tui_model.terminal.error_code, ErrorCode.CANCELLED)
            self.assertFalse(app.tui_model.terminal.writing_started)

    async def test_safe_quit_during_writing_does_not_cancel_or_exit(self):
        facade = HarnessFacade(execute_mode="block-writing")
        app = G502XTuiApp(facade=facade)
        async with app.run_test(size=(80, 24)) as pilot:
            await self._keyboard_prepare_to_confirmation(app, pilot)
            await pilot.press("enter")
            await wait_until(
                pilot,
                lambda: (
                    facade.entered.is_set()
                    and app.tui_model.active is not None
                    and app.tui_model.active.phase is PersistentPhase.WRITING
                ),
                "persistent worker never reached WRITING",
            )
            await pilot.press("ctrl+q")
            await wait_until(
                pilot,
                lambda: (
                    app.tui_model.active is not None
                    and app.tui_model.active.cancellation_deferred
                ),
                "safe quit did not defer cancellation during WRITING",
            )
            self.assertFalse(facade.cancel_seen)
            facade.release.set()
            await wait_until(
                pilot,
                lambda: app.tui_model.terminal is not None,
                "WRITING transaction did not finish authoritatively",
            )
            self.assertEqual(app.tui_model.terminal.outcome.value, "success")

    async def test_writing_transport_fault_is_visible_unresolved_and_safe_quit_stays_open(self):
        facade = HarnessFacade(execute_mode="fault-after-writing")
        app = G502XTuiApp(facade=facade)
        async with app.run_test(size=(80, 24)) as pilot:
            await self._keyboard_prepare_to_confirmation(app, pilot)
            await pilot.press("enter")
            await wait_until(
                pilot,
                lambda: (
                    app.tui_model.active is not None
                    and app.tui_model.active.worker_fault_unresolved
                ),
                "WRITING transport fault was not retained as unresolved",
            )
            self.assertIsNone(app.tui_model.terminal)
            detail = str(app.query_one("#detail", Static).render())
            self.assertIn("OUTCOME UNKNOWN", detail)
            await pilot.press("ctrl+q")
            await pilot.pause()
            self.assertTrue(app.tui_model.active.worker_fault_unresolved)
            self.assertIsNone(app.tui_model.terminal)


if __name__ == "__main__":
    unittest.main()
