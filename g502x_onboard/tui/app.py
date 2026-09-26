from __future__ import annotations

import os
import secrets
from typing import Callable

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import Button, Checkbox, Input, Static

from g502x_onboard.application.models import PersistentOperationKind, PrivacyClass
from .effects import Effect
from .events import (
    BackupPathChanged,
    CancellationRequested,
    ChangePrivacySurface,
    ConfigPathChanged,
    ConfirmationChanged,
    ConfirmationSubmitted,
    EnterReview,
    Navigate,
    OperationRequested,
    ProfileConfirmationChanged,
    ProfileTargetChanged,
    RefreshRequested,
    ReviewAcknowledged,
    SetDisclosure,
    SetHelp,
)
from .model import (
    OperationAction,
    OperationId,
    PresentationPayload,
    Route,
    TuiModel,
)
from .runner import EffectRunner
from .update import update
from .view import view


OperationIdFactory = Callable[[], OperationId]


def _default_operation_id() -> OperationId:
    return OperationId(f"op-{secrets.token_hex(8)}")


class G502XTuiApp(App[None]):
    """Thin Textual renderer/worker adapter around the pure TUI state engine."""

    TITLE = "G502 X Onboard"
    SUB_TITLE = "optional Textual adapter"
    MINIMUM_SIZE = (80, 24)

    BINDINGS = [
        Binding("p", "probe", "Probe"),
        Binding("r", "refresh", "Refresh"),
        Binding("v", "validate", "Validate"),
        Binding("n", "plan", "Plan"),
        Binding("a", "apply", "Apply"),
        Binding("b", "restore_backup", "Restore backup"),
        Binding("l", "restore_baseline", "Restore baseline"),
        Binding("s", "profile_switch", "Switch profile"),
        Binding("g", "report", "Public report"),
        Binding("h", "help", "Help"),
        Binding("d", "privacy", "Privacy"),
        Binding("escape", "cancel_or_back", "Cancel/back", show=False, priority=True),
        Binding("q", "safe_quit", "Quit"),
        Binding("ctrl+q", "safe_quit", "Safe quit", show=False, priority=True),
    ]

    CSS = """
    Screen {
        layout: vertical;
        overflow-x: hidden;
    }

    #constrained {
        display: none;
        height: 100%;
        padding: 1 2;
    }

    #main {
        height: 100%;
    }

    #summary {
        height: 3;
        padding: 0 1;
    }

    #action-grid {
        layout: grid;
        grid-size: 3 3;
        grid-columns: 1fr 1fr 1fr;
        grid-rows: 1 1 1;
        grid-gutter: 0 1;
        height: 3;
    }

    Button {
        height: 1;
        min-width: 10;
        border: none;
        padding: 0 1;
    }

    #input-panel {
        height: 4;
    }

    Input {
        height: 1;
        border: none;
        padding: 0 1;
    }

    #detail-scroll {
        height: 1fr;
        overflow-x: hidden;
    }

    #detail {
        height: auto;
        padding: 0 1;
    }

    #review-ack, #persistent-confirm, #execute, #cancel {
        display: none;
        height: 1;
    }

    Checkbox {
        height: 1;
    }
    """

    def __init__(
        self,
        *,
        facade,
        operation_id_factory: OperationIdFactory | None = None,
    ) -> None:
        super().__init__()
        self._model = TuiModel()
        self._operation_id_factory = operation_id_factory or _default_operation_id
        self._syncing_widgets = False
        self._runner = EffectRunner(facade, self._emit_from_runner)

    @property
    def tui_model(self) -> TuiModel:
        return self._model

    def compose(self) -> ComposeResult:
        yield Static(
            "Terminal size is below the supported 80x24 minimum. "
            "Safety-critical controls are disabled until the terminal is enlarged.",
            id="constrained",
            markup=False,
        )
        with Vertical(id="main"):
            yield Static("", id="summary", markup=False)
            with Grid(id="action-grid"):
                yield Button("Probe [p]", id="probe")
                yield Button("Refresh/status [r]", id="refresh")
                yield Button("Validate [v]", id="validate")
                yield Button("Plan [n]", id="plan")
                yield Button("Prepare apply [a]", id="apply")
                yield Button("Restore backup [b]", id="restore-backup")
                yield Button("Restore baseline [l]", id="restore-baseline")
                yield Button("Switch profile [s]", id="profile-switch")
                yield Button("Public report [g]", id="report")
            with Vertical(id="input-panel"):
                yield Input(
                    placeholder="Config path — LOCAL SENSITIVE",
                    id="config-path",
                )
                yield Input(
                    placeholder="Backup path — LOCAL SENSITIVE",
                    id="backup-path",
                )
                yield Input(
                    value="1",
                    placeholder="Profile target: 1..5",
                    id="profile-target",
                )
                yield Input(
                    placeholder="Exact profile confirmation",
                    id="profile-confirmation",
                )
            with VerticalScroll(id="detail-scroll"):
                yield Static("", id="detail", markup=False)
                yield Checkbox(
                    "I reviewed this exact prepared operation",
                    id="review-ack",
                )
                yield Input(
                    placeholder="Exact persistent confirmation",
                    id="persistent-confirm",
                )
                yield Button(
                    "Execute prepared operation",
                    id="execute",
                )
                yield Button(
                    "Request cooperative cancellation",
                    id="cancel",
                )

    def on_mount(self) -> None:
        self._render()

    def on_resize(self, _event: events.Resize) -> None:
        self._render()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "probe":
            self.action_probe()
        elif button_id == "refresh":
            self.action_refresh()
        elif button_id == "validate":
            self.action_validate()
        elif button_id == "plan":
            self.action_plan()
        elif button_id == "apply":
            self.action_apply()
        elif button_id == "restore-backup":
            self.action_restore_backup()
        elif button_id == "restore-baseline":
            self.action_restore_baseline()
        elif button_id == "profile-switch":
            self.action_profile_switch()
        elif button_id == "report":
            self.action_report()
        elif button_id == "execute":
            active = self._model.active
            if active is not None:
                self._accept_event(ConfirmationSubmitted(active.operation_id))
        elif button_id == "cancel":
            active = self._model.active
            if active is not None:
                self._accept_event(CancellationRequested(active.operation_id))

    def on_input_changed(self, event: Input.Changed) -> None:
        if self._syncing_widgets:
            return
        input_id = event.input.id
        if input_id == "config-path":
            self._accept_event(ConfigPathChanged(event.value))
        elif input_id == "backup-path":
            self._accept_event(BackupPathChanged(event.value))
        elif input_id == "profile-target":
            self._accept_event(ProfileTargetChanged(event.value))
        elif input_id == "profile-confirmation":
            self._accept_event(ProfileConfirmationChanged(event.value))
        elif input_id == "persistent-confirm":
            active = self._model.active
            if active is not None:
                self._accept_event(
                    ConfirmationChanged(active.operation_id, event.value)
                )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        input_id = event.input.id
        if input_id == "config-path":
            self.action_plan()
        elif input_id == "backup-path":
            self.action_restore_backup()
        elif input_id == "profile-confirmation":
            self.action_profile_switch()
        elif input_id == "persistent-confirm":
            active = self._model.active
            if active is not None:
                self._accept_event(ConfirmationSubmitted(active.operation_id))

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if self._syncing_widgets or event.checkbox.id != "review-ack":
            return
        active = self._model.active
        if active is None:
            return
        self._accept_event(EnterReview(active.operation_id))
        self._accept_event(
            ReviewAcknowledged(active.operation_id, event.value)
        )
        if event.value:
            self._accept_event(
                ConfirmationChanged(
                    active.operation_id, self._model.confirmation_input
                )
            )
            self.query_one("#persistent-confirm", Input).focus()

    def action_probe(self) -> None:
        self._request_operation(OperationAction.PROBE)

    def action_refresh(self) -> None:
        self._accept_event(RefreshRequested(self._operation_id_factory()))

    def action_validate(self) -> None:
        self._request_operation(OperationAction.VALIDATE)

    def action_plan(self) -> None:
        self._request_operation(
            OperationAction.PLAN, hardware_affecting=False
        )

    def action_apply(self) -> None:
        self._request_operation(
            OperationAction.PREPARE_PERSISTENT,
            persistent_kind=PersistentOperationKind.APPLY_CONFIG,
        )

    def action_restore_backup(self) -> None:
        self._request_operation(
            OperationAction.PREPARE_PERSISTENT,
            persistent_kind=PersistentOperationKind.RESTORE_BACKUP,
        )

    def action_restore_baseline(self) -> None:
        self._request_operation(
            OperationAction.PREPARE_PERSISTENT,
            persistent_kind=PersistentOperationKind.RESTORE_BASELINE,
        )

    def action_profile_switch(self) -> None:
        self._request_operation(OperationAction.PROFILE_SWITCH)

    def action_report(self) -> None:
        self._request_operation(OperationAction.REPORT)

    def action_help(self) -> None:
        self._accept_event(SetHelp(not self._model.help_open))

    def action_privacy(self) -> None:
        current = self._model.surface_privacy
        if current is PrivacyClass.SHAREABLE:
            target = PrivacyClass.LOCAL_SENSITIVE
        elif current is PrivacyClass.LOCAL_SENSITIVE:
            target = PrivacyClass.PRIVATE_DIAGNOSTIC
        else:
            target = PrivacyClass.SHAREABLE
        self._accept_event(ChangePrivacySurface(target))
        if target is PrivacyClass.PRIVATE_DIAGNOSTIC:
            self._accept_event(
                SetDisclosure(
                    PresentationPayload(
                        "PRIVATE DIAGNOSTIC surface: do not share unit-specific or raw diagnostic content.",
                        PrivacyClass.PRIVATE_DIAGNOSTIC,
                    )
                )
            )
        elif target is PrivacyClass.SHAREABLE:
            self._accept_event(SetDisclosure(None))

    def action_cancel_or_back(self) -> None:
        active = self._model.active
        if active is not None:
            self._accept_event(CancellationRequested(active.operation_id))
            return
        if self._model.help_open:
            self._accept_event(SetHelp(False))
            return
        self._model, _ = update(self._model, ChangePrivacySurface(PrivacyClass.SHAREABLE))
        self._model, _ = update(self._model, SetHelp(False))
        self._model, _ = update(self._model, SetDisclosure(None))
        self._model = TuiModel(
            surface_privacy=self._model.surface_privacy,
            read_only=self._model.read_only,
            read_only_reason=self._model.read_only_reason,
        )
        self._render()

    def action_safe_quit(self) -> None:
        active = self._model.active
        if (
            active is not None
            and active.persistent_kind is not None
            and active.execution_requested
        ):
            self._accept_event(CancellationRequested(active.operation_id))
            return
        self.exit()

    def _request_operation(
        self,
        action: OperationAction,
        *,
        hardware_affecting: bool = True,
        persistent_kind: PersistentOperationKind | None = None,
    ) -> None:
        self._accept_event(
            OperationRequested(
                self._operation_id_factory(),
                action,
                hardware_affecting=hardware_affecting,
                persistent_kind=persistent_kind,
            )
        )

    def _emit_from_runner(self, event: object) -> None:
        self.call_from_thread(self._accept_event, event)

    def _accept_event(self, event: object) -> None:
        self._model, effects = update(self._model, event)
        active = self._model.active
        if (
            active is not None
            and active.persistent_kind is not None
            and not active.execution_requested
            and active.cancellation_acknowledged
            and self._model.prepared is not None
        ):
            # Preserve the Phase 4 acknowledgement semantics, then abandon the
            # pre-execution preparation through ordinary model transitions.
            # No Textual worker/thread cancellation is used as hardware authority.
            self._model, _ = update(
                self._model,
                ChangePrivacySurface(PrivacyClass.SHAREABLE),
            )
            self._model, _ = update(
                self._model,
                Navigate(Route.HOME),
            )
        self._render()
        for effect in effects:
            self._dispatch_effect(effect)

    def _dispatch_effect(self, effect: Effect) -> None:
        operation_id = str(effect.operation_id)
        name = f"g502x-{type(effect).__name__.lower()}-{operation_id}"
        self.run_worker(
            lambda: self._runner.run(effect),
            name=name,
            group="g502x-effects",
            exit_on_error=False,
            thread=True,
        )

    def _render(self) -> None:
        if not self.is_mounted:
            return
        width = self.size.width
        height = self.size.height
        constrained = width < self.MINIMUM_SIZE[0] or height < self.MINIMUM_SIZE[1]
        try:
            constrained_widget = self.query_one("#constrained", Static)
            main_widget = self.query_one("#main", Vertical)
        except NoMatches:
            # A worker callback may arrive while Textual is tearing down the
            # screen. State remains authoritative; rendering is simply over.
            return
        constrained_widget.display = constrained
        main_widget.display = not constrained
        if constrained:
            return

        vm = view(self._model)
        summary = [
            f"Privacy: {vm.privacy_label}",
            f"State: {'ACTIVE' if vm.operation_active else 'IDLE'}",
        ]
        if vm.operation_action is not None:
            summary.append(f"Operation: {vm.operation_action.value}")
        if vm.persistent_kind is not None:
            summary.append(f"Persistent: {vm.persistent_kind.value}")
        if vm.phase_label:
            summary.append(f"Phase: {vm.phase_label}")
        if vm.read_only:
            summary.append("READ ONLY")
        if os.environ.get("NO_COLOR") is not None:
            summary.append("NO_COLOR: semantic labels active")
        self.query_one("#summary", Static).update(" | ".join(summary))

        busy = vm.operation_active
        read_only_mutation = vm.read_only
        for button_id in (
            "probe",
            "refresh",
            "validate",
            "plan",
            "report",
        ):
            self.query_one(f"#{button_id}", Button).disabled = busy
        for button_id in (
            "apply",
            "restore-backup",
            "restore-baseline",
            "profile-switch",
        ):
            self.query_one(f"#{button_id}", Button).disabled = (
                busy or read_only_mutation
            )

        self._syncing_widgets = True
        try:
            input_values = {
                "config-path": vm.config_path_input,
                "backup-path": vm.backup_path_input,
                "profile-target": vm.profile_target_input,
                "profile-confirmation": vm.profile_confirmation_input,
            }
            for input_id, value in input_values.items():
                widget = self.query_one(f"#{input_id}", Input)
                widget.disabled = busy
                if widget.value != value:
                    widget.value = value

            review_ack = self.query_one("#review-ack", Checkbox)
            review_ack.display = vm.review_visible
            if review_ack.value != vm.review_acknowledged:
                review_ack.value = vm.review_acknowledged

            confirmation = self.query_one("#persistent-confirm", Input)
            confirmation.display = vm.confirmation_visible
            confirmation.disabled = not vm.confirmation_visible
            phrase = vm.required_confirmation_phrase or ""
            confirmation.placeholder = (
                f"Type exactly: {phrase}" if phrase else "Exact persistent confirmation"
            )
            if confirmation.value != vm.confirmation_input:
                confirmation.value = vm.confirmation_input

            execute = self.query_one("#execute", Button)
            execute.display = vm.confirmation_visible
            execute.disabled = not vm.confirmation_matches

            cancel = self.query_one("#cancel", Button)
            cancel.display = vm.operation_active
            cancel.disabled = not vm.cancellation_available
        finally:
            self._syncing_widgets = False

        detail: list[str] = []
        if vm.help_open:
            detail.extend(
                [
                    "HELP — local presentation only; opening help performs zero facade/backend calls.",
                    "Keys: p probe | r refresh/status | v validate | n plan | a apply",
                    "b restore backup | l restore baseline | s profile | g public report",
                    "h help | d privacy surface | Esc cooperative cancel/back | q or Ctrl+Q safe quit",
                    "Paths are LOCAL SENSITIVE. Persistent writes always require prepare/review/exact confirmation.",
                ]
            )
        else:
            if vm.safety_label:
                detail.append(vm.safety_label)
            if vm.read_only_label:
                detail.append(vm.read_only_label)
            if vm.read_only_reason:
                detail.append(f"Read-only reason: {vm.read_only_reason}")
            if vm.review_lines:
                detail.append("REVIEW")
                detail.extend(vm.review_lines)
            if vm.required_confirmation_phrase:
                detail.append(
                    f"Required exact confirmation: {vm.required_confirmation_phrase}"
                )
            if vm.terminal_label:
                detail.append(f"Terminal: {vm.terminal_label}")
                detail.append(
                    "Authoritative facts: "
                    f"writing_started={str(vm.writing_started).lower()}; "
                    f"reconciliation_completed={str(vm.reconciliation_completed).lower()}; "
                    f"post_validation_completed={str(vm.post_validation_completed).lower()}"
                )
            if vm.error_code:
                detail.append(f"Error code: {vm.error_code.value}")
            if vm.message:
                detail.append(vm.message)
            if vm.detail and vm.privacy is PrivacyClass.PRIVATE_DIAGNOSTIC:
                detail.append(f"PRIVATE detail: {vm.detail}")
            if vm.disclosure_message:
                detail.append(vm.disclosure_message)
            if not detail:
                detail.append(
                    "Idle. No background polling is active. Use an explicit action to read hardware."
                )
        self.query_one("#detail", Static).update("\n".join(detail))
