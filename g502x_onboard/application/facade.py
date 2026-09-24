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
    CapacitySnapshot,
    DebugExportSnapshot,
    ErrorCode,
    InspectSnapshot,
    OperationResult,
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


class ApplicationFacade:
    """Adapter-facing Phase-1 use cases; no direct persistent execution exists."""

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
        """Prepare APPLY CONFIG against a backend snapshot without persistent writes."""
        context_result = self._run_use_case(
            lambda: _BACKENDS[self].preparation_context(),
            failure_message="unable to observe preparation preconditions",
            expose_detail=False,
            coordinated=True,
        )
        if not context_result.ok:
            return context_result
        context = context_result.value

        if context.compatibility.eligibility is not WriteEligibility.ELIGIBLE:
            return self._failure(
                ErrorCode.READ_ONLY,
                "connected target is read-only; persistent preparation is refused",
                PrivacyClass.SHAREABLE,
            )
        if not context.host_guard_clear:
            return self._failure(
                ErrorCode.SAFETY_REFUSAL,
                "host guard is not clear",
                PrivacyClass.SHAREABLE,
            )

        try:
            path, config = load_config(config_path)
            plan = build_plan(config, context.baseline_map())
        except (ConfigError, OSError, ValueError) as exc:
            # Config filenames/content are user-authored. Do not surface raw
            # exception text as a default/shareable application error.
            del exc
            return self._failure(
                ErrorCode.INVALID_INPUT,
                "configuration could not be loaded or compiled",
                PrivacyClass.LOCAL_SENSITIVE,
            )
        except Exception:
            return self._backend_failure("configuration preparation failed")

        rendered_plan = plan_json(plan).encode("utf-8")
        digest = hashlib.sha256(rendered_plan).hexdigest()
        profile_names = []
        for profile in PROGRAMMABLE_PROFILES:
            row = plan["profiles"][profile]
            if row["disabled"]:
                continue
            profile_names.append(
                (profile, str(profile_display_metadata(row["profile"])["name"]))
            )

        managed_sectors = (0, *PROGRAMMABLE_PROFILES, *GLOBAL_MACRO_SECTORS)
        review = ApplyReview(
            config_name=path.name,
            enabled_profiles=tuple(int(p) for p in plan["enabled_profiles"]),
            profile_names=tuple(profile_names),
            managed_sectors=tuple(int(s) for s in managed_sectors),
            warnings=tuple(str(w) for w in plan["warnings"]),
            plan_digest=digest,
        )
        value = PreparedOperation(
            preparation_id=self._id_factory(),
            kind=PersistentOperationKind.APPLY_CONFIG,
            review=review,
            plan_digest=digest,
            active_baseline_binding=context.active_baseline_binding,
            exact_unit_binding=context.exact_unit_binding,
            compatibility=context.compatibility,
            observed_preconditions=context.observed_preconditions,
            host_guard_clear=context.host_guard_clear,
            required_confirmation_phrase="APPLY CONFIG",
        )
        return OperationResult(ok=True, value=value, privacy=value.privacy)

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
