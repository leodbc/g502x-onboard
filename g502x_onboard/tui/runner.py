from __future__ import annotations

import json
from threading import Lock
from typing import Callable

from g502x_onboard.application import (
    ApplicationFacade,
    ApplicationError,
    CancellationToken,
    ErrorCode,
    PlanSnapshot,
    ProbeSnapshot,
    ProfileSwitchResult,
    PublicReportSnapshot,
    StatusSnapshot,
    ValidationSnapshot,
    PrivacyClass,
    PersistentPhase,
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
    CompatibilityChanged,
    OperationStarted,
    PersistentCompleted,
    PersistentProgress,
    PreparedReceived,
    WorkerTransportFault,
)
from .model import OperationAction, OperationId


EmitEvent = Callable[[object], None]


class EffectRunner:
    """Single audited boundary from typed TUI effects to ApplicationFacade calls."""

    def __init__(self, facade: ApplicationFacade, emit: EmitEvent) -> None:
        self._facade = facade
        self._emit = emit
        self._lock = Lock()
        self._dispatched: set[tuple[str, str]] = set()
        self._tokens: dict[str, CancellationToken] = {}
        self._pending_cancellation: set[str] = set()

    def run(self, effect: Effect) -> bool:
        """Consume one exact effect once. Returns False for a duplicate dispatch."""
        if not self._claim_effect(effect):
            return False
        if isinstance(effect, RequestCooperativeCancellation):
            self._request_cancellation(effect.operation_id)
            return True
        try:
            if isinstance(effect, RequestExplicitRefresh):
                self._run_refresh(effect)
            elif isinstance(effect, StartForegroundOperation):
                self._run_foreground(effect)
            elif isinstance(effect, PreparePersistentOperation):
                self._run_prepare(effect)
            elif isinstance(effect, ExecutePreparedOperation):
                self._run_execute(effect)
            else:
                raise TypeError(f"unsupported effect: {type(effect).__name__}")
        except Exception:
            self._emit(
                WorkerTransportFault(
                    effect.operation_id,
                    self._adapter_fault(),
                )
            )
        return True

    def _claim_effect(self, effect: Effect) -> bool:
        key = (type(effect).__name__, str(effect.operation_id))
        with self._lock:
            if key in self._dispatched:
                return False
            self._dispatched.add(key)
            return True

    def _request_cancellation(self, operation_id: OperationId) -> None:
        key = str(operation_id)
        with self._lock:
            token = self._tokens.get(key)
            if token is None:
                self._pending_cancellation.add(key)
            else:
                token.cancel()
        self._emit(CancellationAcknowledged(operation_id))

    def _run_refresh(self, effect: RequestExplicitRefresh) -> None:
        self._emit(OperationStarted(effect.operation_id))
        result = self._facade.status(private=False)
        self._finish_ordinary(effect.operation_id, result)

    def _run_foreground(self, effect: StartForegroundOperation) -> None:
        self._emit(OperationStarted(effect.operation_id))
        if effect.action is OperationAction.PROBE:
            result = self._facade.probe()
            if result.ok:
                compatibility = result.value.compatibility
                if compatibility.write_allowed:
                    self._emit(CompatibilityChanged(False, None))
                else:
                    self._emit(
                        CompatibilityChanged(
                            True,
                            ApplicationError(
                                ErrorCode.READ_ONLY,
                                "connected target is read-only or incompatible",
                                PrivacyClass.SHAREABLE,
                            ),
                        )
                    )
            self._finish_ordinary(effect.operation_id, result)
            return
        if effect.action is OperationAction.STATUS:
            self._finish_ordinary(
                effect.operation_id, self._facade.status(private=False)
            )
            return
        if effect.action is OperationAction.VALIDATE:
            self._finish_ordinary(
                effect.operation_id, self._facade.validate()
            )
            return
        if effect.action is OperationAction.PLAN:
            if not effect.config_path:
                self._emit(
                    ApplicationFailed(
                        effect.operation_id,
                        ApplicationError(
                            ErrorCode.INVALID_INPUT,
                            "config path is required for plan",
                            PrivacyClass.SHAREABLE,
                        ),
                    )
                )
                return
            self._finish_ordinary(
                effect.operation_id, self._facade.plan(effect.config_path)
            )
            return
        if effect.action is OperationAction.PROFILE_SWITCH:
            if effect.profile_target is None:
                self._emit(
                    ApplicationFailed(
                        effect.operation_id,
                        ApplicationError(
                            ErrorCode.INVALID_INPUT,
                            "profile must be 1, 2, 3, 4, or 5",
                            PrivacyClass.SHAREABLE,
                        ),
                    )
                )
                return
            self._finish_ordinary(
                effect.operation_id,
                self._facade.switch_profile(
                    effect.profile_target, effect.confirmation or ""
                ),
            )
            return
        if effect.action is OperationAction.REPORT:
            self._finish_ordinary(
                effect.operation_id,
                self._facade.report_probe(pid=None, index=None),
            )
            return
        if effect.action is OperationAction.REFRESH:
            self._finish_ordinary(
                effect.operation_id, self._facade.status(private=False)
            )
            return
        self._emit(
            ApplicationFailed(
                effect.operation_id,
                ApplicationError(
                    ErrorCode.INVALID_INPUT,
                    "unsupported TUI operation",
                    PrivacyClass.SHAREABLE,
                ),
            )
        )

    def _run_prepare(self, effect: PreparePersistentOperation) -> None:
        from g502x_onboard.application import PersistentOperationKind

        self._emit(OperationStarted(effect.operation_id))
        if effect.kind is PersistentOperationKind.APPLY_CONFIG:
            if not effect.config_path:
                self._emit(
                    ApplicationFailed(
                        effect.operation_id,
                        ApplicationError(
                            ErrorCode.INVALID_INPUT,
                            "config path is required for apply preparation",
                            PrivacyClass.SHAREABLE,
                        ),
                    )
                )
                return
            result = self._facade.prepare_apply(effect.config_path)
        elif effect.kind is PersistentOperationKind.RESTORE_BACKUP:
            if not effect.backup_path:
                self._emit(
                    ApplicationFailed(
                        effect.operation_id,
                        ApplicationError(
                            ErrorCode.INVALID_INPUT,
                            "backup path is required for restore preparation",
                            PrivacyClass.SHAREABLE,
                        ),
                    )
                )
                return
            result = self._facade.prepare_restore_backup(effect.backup_path)
        elif effect.kind is PersistentOperationKind.RESTORE_BASELINE:
            result = self._facade.prepare_restore_baseline()
        else:
            self._emit(
                ApplicationFailed(
                    effect.operation_id,
                    ApplicationError(
                        ErrorCode.INVALID_INPUT,
                        "unsupported persistent preparation kind",
                        PrivacyClass.SHAREABLE,
                    ),
                )
            )
            return

        if result.ok:
            self._emit(PreparedReceived(effect.operation_id, result.value))
        else:
            self._emit(ApplicationFailed(effect.operation_id, result.error))

    def _run_execute(self, effect: ExecutePreparedOperation) -> None:
        key = str(effect.operation_id)
        token = CancellationToken()
        with self._lock:
            self._tokens[key] = token
            if key in self._pending_cancellation:
                token.cancel()
                self._pending_cancellation.discard(key)

        last_phase: PersistentPhase | None = None

        def observer(snapshot) -> None:
            nonlocal last_phase
            last_phase = snapshot.phase
            self._emit(PersistentProgress(effect.operation_id, snapshot))

        try:
            result = self._facade.execute_prepared(
                effect.prepared,
                effect.confirmation,
                cancellation=token,
                observer=observer,
            )
            if result.ok:
                self._emit(PersistentCompleted(effect.operation_id, result.value))
                return
            if last_phase in {
                PersistentPhase.WRITING,
                PersistentPhase.RECONCILING,
                PersistentPhase.POST_VALIDATING,
            }:
                self._emit(
                    WorkerTransportFault(
                        effect.operation_id,
                        self._unresolved_fault(),
                    )
                )
            else:
                self._emit(ApplicationFailed(effect.operation_id, result.error))
        except Exception:
            if last_phase in {
                PersistentPhase.WRITING,
                PersistentPhase.RECONCILING,
                PersistentPhase.POST_VALIDATING,
            }:
                self._emit(
                    WorkerTransportFault(
                        effect.operation_id,
                        self._unresolved_fault(),
                    )
                )
            else:
                self._emit(
                    WorkerTransportFault(
                        effect.operation_id,
                        self._adapter_fault(),
                    )
                )
        finally:
            with self._lock:
                self._tokens.pop(key, None)
                self._pending_cancellation.discard(key)

    def _finish_ordinary(self, operation_id: OperationId, result) -> None:
        if not result.ok:
            self._emit(ApplicationFailed(operation_id, result.error))
            return
        self._emit(
            ApplicationCompleted(
                operation_id,
                self._message_for(result.value),
                result.privacy,
            )
        )

    @staticmethod
    def _message_for(value) -> str:
        if isinstance(value, ProbeSnapshot):
            enabled = "eligible" if value.compatibility.write_allowed else "read-only"
            return (
                f"Probe complete: device={value.device_name or 'unknown'}; "
                f"protocol={value.protocol or 'unknown'}; "
                f"active_profile={value.active_profile}; "
                f"compatibility={value.compatibility.architecture}/"
                f"{value.compatibility.transport}; write={enabled}"
            )
        if isinstance(value, StatusSnapshot):
            profiles = ",".join(map(str, value.enabled_profiles)) or "none"
            return (
                f"Status complete: active_profile={value.active_profile}; "
                f"enabled_profiles={profiles}"
            )
        if isinstance(value, ValidationSnapshot):
            return (
                f"Validation {'passed' if value.ok else 'failed'}: "
                f"errors={value.error_count}; warnings={value.warning_count}; "
                f"recovery={'ok' if value.recovery_ok else 'not-ok'}"
            )
        if isinstance(value, PlanSnapshot):
            return value.rendered_json
        if isinstance(value, ProfileSwitchResult):
            return f"Profile switch complete: active_profile={value.active_profile}"
        if isinstance(value, PublicReportSnapshot):
            return json.dumps(value.payload, indent=2, sort_keys=True)
        return "Operation completed"

    @staticmethod
    def _adapter_fault() -> ApplicationError:
        return ApplicationError(
            ErrorCode.BACKEND_FAILURE,
            "TUI adapter worker failed before authoritative completion",
            PrivacyClass.SHAREABLE,
        )

    @staticmethod
    def _unresolved_fault() -> ApplicationError:
        return ApplicationError(
            ErrorCode.BACKEND_FAILURE,
            "adapter transport fault during a non-cancellable transaction; hardware outcome is unresolved",
            PrivacyClass.SHAREABLE,
        )
