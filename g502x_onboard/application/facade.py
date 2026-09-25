from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any, Callable
from weakref import WeakKeyDictionary

from ..codec import build_plan, plan_json, profile_display_metadata
from ..config import ConfigError, load_config
from ..constants import GLOBAL_MACRO_SECTORS, PROGRAMMABLE_PROFILES
from .backend import Backend
from .coordinator import OperationBusyError, OperationCoordinator
from .models import (
    ApplicationError,
    ApplyReview,
    BackupSnapshot,
    BaselineListSnapshot,
    BaselineShowSnapshot,
    BaselineUseSnapshot,
    CancellationToken,
    CapacitySnapshot,
    DebugExportSnapshot,
    ErrorCode,
    InspectSnapshot,
    OperationResult,
    PersistentExecutionResult,
    PersistentPhaseSnapshot,
    PlanSnapshot,
    PersistentOperationKind,
    PreparedOperation,
    PrivacyClass,
    ProbeDetails,
    ProbeSnapshot,
    ProfileSwitchResult,
    PublicReportSnapshot,
    ReadonlySmokeSnapshot,
    ReportCheckResult,
    SetupSnapshot,
    StatusSnapshot,
    ValidationDetails,
    ValidationSnapshot,
    WriteEligibility,
)
from .persistent import (
    execute_prepared as _execute_prepared,
    prepare_apply as _prepare_apply,
    prepare_restore_backup as _prepare_restore_backup,
    prepare_restore_baseline as _prepare_restore_baseline,
)


class ApplicationFacade:
    """Adapter-facing use cases with one prepared persistent execution authority."""

    __slots__ = ("_id_factory", "__weakref__")

    def __init__(
        self,
        backend: Backend,
        *,
        id_factory: Callable[[], str] | None = None,
        coordinator: OperationCoordinator | None = None,
    ) -> None:
        # Do not retain the backend as facade instance state. A normal adapter
        # receiving only the public facade must not be able to walk an obvious
        # attribute path from the facade to RealBackend and its preserved
        # persistent primitives.
        _BACKENDS[self] = backend
        _COORDINATORS[self] = coordinator or OperationCoordinator()
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)

    def probe(self) -> OperationResult[ProbeSnapshot]:
        return self._run_use_case(
            lambda: _BACKENDS[self].probe(),
            failure_message="probe failed",
            expose_detail=False,
            coordinated=True,
        )

    def validate(self) -> OperationResult[ValidationSnapshot]:
        return self._run_use_case(
            lambda: _BACKENDS[self].validate(),
            failure_message="validation failed",
            expose_detail=False,
            coordinated=True,
        )

    def switch_profile(self, target: int, confirmation: str) -> OperationResult[ProfileSwitchResult]:
        if target not in (1, 2, 3, 4, 5):
            return self._failure(
                ErrorCode.INVALID_INPUT,
                "profile must be 1, 2, 3, 4, or 5",
                PrivacyClass.SHAREABLE,
            )
        phrase = "BACK TO SAFE" if target == 1 else f"ENTER PROFILE {target}"
        if confirmation.strip() != phrase:
            return self._failure(
                ErrorCode.SAFETY_REFUSAL,
                f"exact confirmation required: {phrase}",
                PrivacyClass.SHAREABLE,
            )
        result = self._run_use_case(
            lambda: _BACKENDS[self].switch_profile_guarded(target),
            failure_message="profile switch refused or failed",
            expose_detail=True,
            coordinated=True,
        )
        if not result.ok:
            return result
        value = ProfileSwitchResult(
            active_profile=int(result.value),
            confirmation_phrase=phrase,
        )
        return OperationResult(ok=True, value=value, privacy=value.privacy)

    def prepare_apply(self, config_path: str | Path) -> OperationResult[PreparedOperation]:
        return _prepare_apply(
            self,
            _BACKENDS[self],
            _COORDINATORS[self],
            self._id_factory,
            config_path,
        )

    def prepare_restore_backup(
        self,
        backup_path: str | Path,
    ) -> OperationResult[PreparedOperation]:
        return _prepare_restore_backup(
            self,
            _BACKENDS[self],
            _COORDINATORS[self],
            self._id_factory,
            backup_path,
        )

    def prepare_restore_baseline(self) -> OperationResult[PreparedOperation]:
        return _prepare_restore_baseline(
            self,
            _BACKENDS[self],
            _COORDINATORS[self],
            self._id_factory,
        )

    def execute_prepared(
        self,
        prepared: PreparedOperation,
        confirmation: str,
        *,
        cancellation: CancellationToken | None = None,
        observer: Callable[[PersistentPhaseSnapshot], None] | None = None,
    ) -> OperationResult[PersistentExecutionResult]:
        return _execute_prepared(
            self,
            _BACKENDS[self],
            _COORDINATORS[self],
            prepared,
            confirmation,
            cancellation=cancellation,
            observer=observer,
        )

    def probe_details(
        self,
        *,
        pid: int | None,
        index: int | None,
        read_sectors: bool,
        private: bool,
    ) -> OperationResult[ProbeDetails]:
        return self._run_use_case(
            lambda: _BACKENDS[self].probe_details(
                pid=pid,
                index=index,
                read_sectors=read_sectors,
                private=private,
            ),
            failure_message="probe failed",
            expose_detail=True,
            coordinated=True,
        )

    def validate_details(self, *, private: bool) -> OperationResult[ValidationDetails]:
        return self._run_use_case(
            lambda: _BACKENDS[self].validate_details(private=private),
            failure_message="validation failed",
            expose_detail=True,
            coordinated=True,
        )

    def status(self, *, private: bool) -> OperationResult[StatusSnapshot]:
        return self._run_use_case(
            lambda: _BACKENDS[self].status(private=private),
            failure_message="status failed",
            expose_detail=True,
            coordinated=True,
        )

    def inspect(self, *, private: bool) -> OperationResult[InspectSnapshot]:
        return self._run_use_case(
            lambda: _BACKENDS[self].inspect(private=private),
            failure_message="inspection failed",
            expose_detail=True,
            coordinated=True,
        )

    def plan(self, config_path: str) -> OperationResult[PlanSnapshot]:
        return self._run_use_case(
            lambda: _BACKENDS[self].plan(config_path),
            failure_message="planning failed",
            expose_detail=True,
            coordinated=False,
        )

    def capacity(self, config_path: str | None) -> OperationResult[CapacitySnapshot]:
        return self._run_use_case(
            lambda: _BACKENDS[self].capacity(config_path),
            failure_message="capacity calculation failed",
            expose_detail=True,
            coordinated=False,
        )

    def setup_baseline(
        self,
        *,
        pid: int | None,
        index: int | None,
        replace: bool,
    ) -> OperationResult[SetupSnapshot]:
        return self._run_use_case(
            lambda: _BACKENDS[self].setup_baseline(
                pid=pid,
                index=index,
                replace=replace,
            ),
            failure_message="setup failed",
            expose_detail=True,
            coordinated=True,
        )

    def list_baselines(self, *, private: bool) -> OperationResult[BaselineListSnapshot]:
        return self._run_use_case(
            lambda: _BACKENDS[self].list_baselines(private=private),
            failure_message="baseline listing failed",
            expose_detail=True,
            coordinated=False,
        )

    def show_baseline(self, *, private: bool) -> OperationResult[BaselineShowSnapshot]:
        return self._run_use_case(
            lambda: _BACKENDS[self].show_baseline(private=private),
            failure_message="baseline display failed",
            expose_detail=True,
            coordinated=False,
        )

    def use_baseline(self, fingerprint: str) -> OperationResult[BaselineUseSnapshot]:
        return self._run_use_case(
            lambda: _BACKENDS[self].use_baseline(fingerprint),
            failure_message="baseline activation failed",
            expose_detail=True,
            coordinated=False,
        )

    def create_backup(self, label: str) -> OperationResult[BackupSnapshot]:
        return self._run_use_case(
            lambda: _BACKENDS[self].create_backup(label),
            failure_message="backup failed",
            expose_detail=True,
            coordinated=True,
        )

    def report_probe(
        self,
        *,
        pid: int | None,
        index: int | None,
    ) -> OperationResult[PublicReportSnapshot]:
        return self._run_use_case(
            lambda: _BACKENDS[self].report_probe(pid=pid, index=index),
            failure_message="probe report failed",
            expose_detail=True,
            coordinated=True,
        )

    def report_device(self, *, include_state: bool) -> OperationResult[PublicReportSnapshot]:
        return self._run_use_case(
            lambda: _BACKENDS[self].report_device(include_state=include_state),
            failure_message="device report failed",
            expose_detail=True,
            coordinated=include_state,
        )

    def check_public_report(self, payload: dict[str, Any]) -> OperationResult[ReportCheckResult]:
        return self._run_use_case(
            lambda: _BACKENDS[self].check_public_report(payload),
            failure_message="public report check failed",
            expose_detail=True,
            coordinated=False,
        )

    def debug_export(self, *, include_raw: bool) -> OperationResult[DebugExportSnapshot]:
        return self._run_use_case(
            lambda: _BACKENDS[self].debug_export(include_raw=include_raw),
            failure_message="debug export failed",
            expose_detail=True,
            coordinated=True,
        )

    def readonly_smoke(
        self,
        *,
        report_path: str,
        label: str,
    ) -> OperationResult[ReadonlySmokeSnapshot]:
        return self._run_use_case(
            lambda: _BACKENDS[self].readonly_smoke(
                report_path=report_path,
                label=label,
            ),
            failure_message="read-only smoke failed",
            expose_detail=True,
            coordinated=True,
        )

    def _run_use_case(
        self,
        call: Callable[[], Any],
        *,
        failure_message: str,
        expose_detail: bool,
        coordinated: bool,
    ) -> OperationResult:
        try:
            if coordinated:
                with _COORDINATORS[self].claim():
                    value = call()
            else:
                value = call()
            privacy = getattr(value, "privacy", PrivacyClass.PRIVATE_DIAGNOSTIC)
            return OperationResult(ok=True, value=value, privacy=privacy)
        except ConfigError as exc:
            return self._failure(
                ErrorCode.CONFIG_ERROR,
                str(exc),
                PrivacyClass.LOCAL_SENSITIVE,
            )
        except OperationBusyError as exc:
            return self._failure(
                ErrorCode.BUSY,
                str(exc),
                PrivacyClass.SHAREABLE,
            )
        except Exception as exc:
            if expose_detail:
                return self._failure(
                    ErrorCode.BACKEND_FAILURE,
                    failure_message,
                    PrivacyClass.PRIVATE_DIAGNOSTIC,
                    detail=str(exc),
                )
            return self._backend_failure(failure_message)

    @staticmethod
    def _failure(
        code: ErrorCode,
        message: str,
        privacy: PrivacyClass,
        *,
        detail: str | None = None,
    ) -> OperationResult:
        return OperationResult(
            ok=False,
            error=ApplicationError(
                code=code,
                message=message,
                privacy=privacy,
                detail=detail,
            ),
            privacy=privacy,
        )

    @classmethod
    def _backend_failure(cls, message: str) -> OperationResult:
        # The raw exception remains backend/private state. The adapter receives
        # only a classified, stable summary.
        return cls._failure(ErrorCode.BACKEND_FAILURE, message, PrivacyClass.PRIVATE_DIAGNOSTIC)


_BACKENDS: WeakKeyDictionary[ApplicationFacade, Backend] = WeakKeyDictionary()
_COORDINATORS: WeakKeyDictionary[ApplicationFacade, OperationCoordinator] = WeakKeyDictionary()
