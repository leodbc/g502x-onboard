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
    SetTechnicalDetails,
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
    """Task-oriented Textual presentation over the existing pure state engine."""

    TITLE = "G502 X Onboard"
    SUB_TITLE = "Phase 7 presentation"
    MINIMUM_SIZE = (80, 24)

    BINDINGS = [
        Binding("up", "focus_previous", "Previous", show=False),
        Binding("down", "focus_next", "Next", show=False),
        Binding("c", "configuration", "Configuration"),
        Binding("b", "backup_context", "Backup & Restore"),
        Binding("d", "diagnostics", "Diagnostics"),
        Binding("r", "refresh", "Refresh"),
        Binding("n", "plan", "Plan"),
        Binding("a", "apply", "Apply"),
        Binding("s", "profile_switch", "Switch profile"),
        Binding("l", "restore_baseline", "Restore baseline"),
        Binding("p", "probe", "Probe"),
        Binding("v", "validate", "Validate"),
        Binding("g", "report", "Public report"),
        Binding("t", "technical", "Technical details"),
        Binding("question_mark", "help", "Help"),
        Binding("enter", "activate_focused", "Select", show=False, priority=True),
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

    #workspace {
        height: 1fr;
        layout: vertical;
    }

    #task-area {
        height: auto;
        max-height: 10;
        padding: 0 1;
    }

    #main.expanded #workspace {
        layout: horizontal;
    }

    #main.expanded #task-area {
        width: 1fr;
        height: 1fr;
        max-height: 100%;
    }

    #main.expanded #detail-scroll {
        width: 1fr;
        height: 1fr;
    }

    #home-panel, #configuration-panel, #backup-panel, #diagnostics-panel {
        display: none;
        height: auto;
    }

    .action-row {
        layout: grid;
        grid-size: 2 2;
        grid-columns: 1fr 1fr;
        grid-rows: 1 1;
        height: 2;
        grid-gutter: 0 1;
    }

    #home-panel {
        height: 4;
    }

    #configuration-panel {
        height: 8;
    }

    #backup-panel {
        height: 4;
    }

    #diagnostics-panel {
        height: 4;
    }

    #utility-row {
        layout: grid;
        grid-size: 2 1;
        grid-columns: 1fr 1fr;
        height: 1;
        grid-gutter: 0 1;
    }

    Button {
        height: 1;
        min-width: 10;
        border: none;
        padding: 0 1;
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
        yield Static("", id="constrained", markup=False)
        with Vertical(id="main"):
            yield Static("", id="summary", markup=False)
            with Grid(id="workspace"):
                with Vertical(id="task-area"):
                    with Vertical(id="home-panel"):
                        yield Button("Read current onboard state [R]", id="refresh")
                        yield Button("Configuration [C]", id="nav-config")
                        yield Button("Backup & Restore [B]", id="nav-backup")
                        yield Button("Diagnostics [D]", id="nav-diagnostics")
                    with Vertical(id="configuration-panel"):
                        yield Input(
                            placeholder="Config path — LOCAL SENSITIVE",
                            id="config-path",
                        )
                        yield Button("Plan configuration [N]", id="plan")
                        yield Button("Prepare apply [A]", id="apply")
                        yield Input(
                            value="1",
                            placeholder="Profile target: 1..5",
                            id="profile-target",
                        )
                        yield Input(
                            placeholder="Exact profile confirmation",
                            id="profile-confirmation",
                        )
                        yield Button("Switch active profile [S]", id="profile-switch")
                    with Vertical(id="backup-panel"):
                        yield Input(
                            placeholder="Backup path — LOCAL SENSITIVE",
                            id="backup-path",
                        )
                        yield Button("Restore backup [B]", id="restore-backup")
                        yield Button("Restore baseline [L]", id="restore-baseline")
                    with Vertical(id="diagnostics-panel"):
                        yield Button("Probe [P]", id="probe")
                        yield Button("Validate [V]", id="validate")
                        yield Button("Public report [G]", id="report")
                        yield Button("Privacy surface", id="privacy")
                    with Grid(id="utility-row"):
                        yield Button("Technical details [T]", id="technical")
                        yield Button("Help [?]", id="help")
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
                    yield Button("Execute prepared operation", id="execute")
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
        if button_id == "refresh":
            self.action_refresh()
        elif button_id == "nav-config":
            self.action_configuration()
        elif button_id == "nav-backup":
            self._navigate_task(Route.BACKUP_RESTORE)
        elif button_id == "nav-diagnostics":
            self.action_diagnostics()
        elif button_id == "probe":
            self.action_probe()
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
        elif button_id == "privacy":
            self.action_privacy()
        elif button_id == "technical":
            self.action_technical()
        elif button_id == "help":
            self.action_help()
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
        self._accept_event(ReviewAcknowledged(active.operation_id, event.value))
        if event.value:
            self._accept_event(
                ConfirmationChanged(
                    active.operation_id, self._model.confirmation_input
                )
            )
            try:
                self.query_one("#persistent-confirm", Input).focus()
            except NoMatches:
                return

    def _new_operation_id(self) -> OperationId:
        return OperationId(str(self._operation_id_factory()))

    def _navigate_task(self, route: Route) -> None:
        if self._layout_constrained() or self._model.active is not None:
            return
        self._accept_event(Navigate(route))

    def _on_route(self, route: Route) -> bool:
        return self._model.route is route and self._model.active is None

    def action_configuration(self) -> None:
        if self._model.route is Route.HOME:
            self._navigate_task(Route.CONFIGURATION)

    def action_backup_context(self) -> None:
        if self._model.route is Route.HOME:
            self._navigate_task(Route.BACKUP_RESTORE)
        elif self._model.route is Route.BACKUP_RESTORE:
            self.action_restore_backup()

    def action_diagnostics(self) -> None:
        if self._model.route is Route.HOME:
            self._navigate_task(Route.DIAGNOSTICS)

    def action_probe(self) -> None:
        if self._on_route(Route.DIAGNOSTICS):
            self._request_operation(OperationAction.PROBE)

    def action_refresh(self) -> None:
        if self._layout_constrained() or self._model.active is not None:
            return
        if self._model.route not in {
            Route.HOME,
            Route.CONFIGURATION,
            Route.BACKUP_RESTORE,
            Route.DIAGNOSTICS,
        }:
            return
        self._accept_event(RefreshRequested(self._new_operation_id()))

    def action_validate(self) -> None:
        if self._on_route(Route.DIAGNOSTICS):
            self._request_operation(OperationAction.VALIDATE)

    def action_plan(self) -> None:
        if self._on_route(Route.CONFIGURATION):
            self._request_operation(
                OperationAction.PLAN, hardware_affecting=False
            )

    def action_apply(self) -> None:
        if self._on_route(Route.CONFIGURATION):
            self._request_operation(
                OperationAction.PREPARE_PERSISTENT,
                persistent_kind=PersistentOperationKind.APPLY_CONFIG,
            )

    def action_restore_backup(self) -> None:
        if self._on_route(Route.BACKUP_RESTORE):
            self._request_operation(
                OperationAction.PREPARE_PERSISTENT,
                persistent_kind=PersistentOperationKind.RESTORE_BACKUP,
            )

    def action_restore_baseline(self) -> None:
        if self._on_route(Route.BACKUP_RESTORE):
            self._request_operation(
                OperationAction.PREPARE_PERSISTENT,
                persistent_kind=PersistentOperationKind.RESTORE_BASELINE,
            )

    def action_profile_switch(self) -> None:
        if self._on_route(Route.CONFIGURATION):
            self._request_operation(OperationAction.PROFILE_SWITCH)

    def action_report(self) -> None:
        if self._on_route(Route.DIAGNOSTICS):
            self._request_operation(OperationAction.REPORT)

    def action_help(self) -> None:
        self._accept_event(SetHelp(not self._model.help_open))

    def action_activate_focused(self) -> None:
        """Preserve focused-control Enter semantics under the task shell."""
        focused = self.focused
        if (
            focused is None
            or getattr(focused, "disabled", False)
            or not getattr(focused, "display", True)
        ):
            return

        if isinstance(focused, Input):
            input_id = focused.id
            if input_id == "config-path":
                self.action_plan()
            elif input_id == "backup-path":
                self.action_restore_backup()
            elif input_id == "profile-confirmation":
                self.action_profile_switch()
            elif input_id == "persistent-confirm":
                active = self._model.active
                if active is not None:
                    self._accept_event(
                        ConfirmationSubmitted(active.operation_id)
                    )
            return

        if isinstance(focused, Checkbox):
            focused.toggle()
            return

        if isinstance(focused, Button):
            focused.press()

    def action_technical(self) -> None:
        self._accept_event(
            SetTechnicalDetails(not self._model.technical_open)
        )

    def action_privacy(self) -> None:
        if not self._on_route(Route.DIAGNOSTICS):
            return
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
        if self._model.help_open:
            self._accept_event(SetHelp(False))
            return
        if self._model.active is not None:
            self._accept_event(
                CancellationRequested(self._model.active.operation_id)
            )
            return
        if self._model.technical_open:
            self._accept_event(SetTechnicalDetails(False))
            return
        if self._model.route is Route.HOME:
            return
        self._model, _ = update(
            self._model, ChangePrivacySurface(PrivacyClass.SHAREABLE)
        )
        self._model, _ = update(self._model, SetDisclosure(None))
        self._model, _ = update(self._model, Navigate(Route.HOME))
        self._render()

    def action_safe_quit(self) -> None:
        active = self._model.active
        if active is not None:
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
        if self._layout_constrained() or self._model.active is not None:
            return
        if self._model.read_only and (
            action is OperationAction.PROFILE_SWITCH
            or action is OperationAction.PREPARE_PERSISTENT
        ):
            return
        self._accept_event(
            OperationRequested(
                self._new_operation_id(),
                action,
                hardware_affecting=hardware_affecting,
                persistent_kind=persistent_kind,
            )
        )

    def _emit_from_runner(self, event: object) -> None:
        self.call_from_thread(self._accept_event, event)

    def _accept_event(self, event: object) -> None:
        previous_active = self._model.active
        self._model, effects = update(self._model, event)
        active = self._model.active
        if (
            active is not None
            and active.persistent_kind is not None
            and not active.execution_requested
            and active.cancellation_acknowledged
            and self._model.prepared is not None
        ):
            self._model, _ = update(
                self._model,
                ChangePrivacySurface(PrivacyClass.SHAREABLE),
            )
            self._model, _ = update(self._model, Navigate(Route.HOME))
        current_active = self._model.active
        if previous_active is not None and (
            current_active is None
            or current_active.operation_id != previous_active.operation_id
        ):
            self._runner.retire_operation(previous_active.operation_id)
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

    def _layout_constrained(self) -> bool:
        if not self.is_mounted:
            return False
        return (
            self.size.width < self.MINIMUM_SIZE[0]
            or self.size.height < self.MINIMUM_SIZE[1]
        )

    def _constrained_text(self) -> str:
        active = self._model.active
        if active is None:
            text = (
                "G502 X Onboard\n\n"
                "[X] Terminal is smaller than the supported 80x24 minimum.\n\n"
                "Enlarge the terminal to use device and configuration actions.\n"
                "No hardware action is available in this layout.\n\n"
                "? Help   Q Exit"
            )
        elif active.non_cancellable or active.worker_fault_unresolved:
            text = (
                "G502 X Onboard\n\n"
                "[>] Writing/verification is still in progress\n"
                "[!] Cancellation is unavailable\n\n"
                "Keep this process open. Enlarge the terminal for full detail.\n"
                "No new hardware action is available.\n\n"
                "? Help"
            )
        else:
            text = (
                "G502 X Onboard\n\n"
                f"[>] {active.action.value.replace('-', ' ').title()} is still active\n"
                "[ ] Persistent writing has not been reported as started.\n"
                + (
                    "Cooperative cancellation is available.\n"
                    if active.cancellation_available
                    else "Cooperative cancellation is unavailable.\n"
                )
                + "\nEnlarge the terminal for full detail.\n? Help   Esc Cancel/back"
            )
        if self._model.help_open:
            text += "\n\n" + "\n".join(view(self._model).help_lines)
        return text

    def _render(self) -> None:
        if not self.is_mounted:
            return
        constrained = self._layout_constrained()
        try:
            constrained_widget = self.query_one("#constrained", Static)
            main_widget = self.query_one("#main", Vertical)
        except NoMatches:
            return

        constrained_widget.update(self._constrained_text())
        constrained_widget.display = constrained
        main_widget.display = not constrained
        main_widget.set_class(self.size.width > 100, "expanded")

        vm = view(self._model)
        summary = [
            f"G502 X Onboard — {vm.route_title}",
            f"Privacy: {vm.privacy_label}",
            f"Read: {vm.read_truth.value}",
        ]
        if vm.operation_active:
            summary.append("[>] foreground operation active")
        elif vm.read_only:
            summary.append("[!] READ ONLY")
        if os.environ.get("NO_COLOR") is not None:
            summary.append("NO_COLOR: text/markers carry semantic state")
        self.query_one("#summary", Static).update(" | ".join(summary))

        task_route = self._model.route
        panel_routes = {
            "home-panel": Route.HOME,
            "configuration-panel": Route.CONFIGURATION,
            "backup-panel": Route.BACKUP_RESTORE,
            "diagnostics-panel": Route.DIAGNOSTICS,
        }
        for panel_id, route in panel_routes.items():
            self.query_one(f"#{panel_id}").display = (
                not constrained and task_route is route and not vm.operation_active
            )

        busy = vm.operation_active
        for button_id in ("probe", "refresh", "validate", "plan", "report"):
            self.query_one(f"#{button_id}", Button).disabled = busy
        for button_id in (
            "apply",
            "restore-backup",
            "restore-baseline",
            "profile-switch",
        ):
            self.query_one(f"#{button_id}", Button).disabled = (
                busy or vm.read_only
            )
        self.query_one("#privacy", Button).disabled = busy

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
                f"Type exactly: {phrase}"
                if phrase
                else "Exact persistent confirmation"
            )
            if (
                confirmation.value != vm.confirmation_input
                and (
                    not confirmation.has_focus
                    or not vm.confirmation_visible
                )
            ):
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
            detail.extend(vm.help_lines)
        elif vm.technical_open:
            detail.extend(vm.technical_lines)
            if vm.disclosure_message:
                detail.append(vm.disclosure_message)
            if vm.detail and vm.privacy is PrivacyClass.PRIVATE_DIAGNOSTIC:
                detail.append(f"PRIVATE detail: {vm.detail}")
        else:
            detail.extend(vm.primary_lines)
            if vm.safety_label and vm.safety_label not in detail:
                detail.append(vm.safety_label)
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
            if vm.message and self._model.route is not Route.HOME:
                detail.append(vm.message)
            if vm.disclosure_message and vm.privacy is PrivacyClass.PRIVATE_DIAGNOSTIC:
                detail.append(vm.disclosure_message)
            if not detail:
                detail.append(
                    "Idle. No background polling is active. Use an explicit action to read hardware."
                )
        self.query_one("#detail", Static).update("\n".join(detail))
