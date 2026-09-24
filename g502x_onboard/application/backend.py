from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

from .models import (
    BackupSnapshot,
    BaselineListSnapshot,
    BaselineShowSnapshot,
    BaselineUseSnapshot,
    CancellationToken,
    CapacitySnapshot,
    CompatibilityObservation,
    DebugExportSnapshot,
    InspectSnapshot,
    PersistentOperationKind,
    PersistentPhase,
    PlanSnapshot,
    PreparationContext,
    ProbeDetails,
    ProbeSnapshot,
    PublicReportSnapshot,
    ReadonlySmokeSnapshot,
    ReportCheckResult,
    SetupSnapshot,
    StatusSnapshot,
    ValidationDetails,
    ValidationSnapshot,
)


@dataclass(frozen=True)
class PersistentTargetSnapshot:
    source_path: str | None = field(default=None, repr=False)
    source_name: str = ""
    target_digest: str = ""


@dataclass(frozen=True)
class PersistentBackendIntent:
    kind: PersistentOperationKind
    target_digest: str
    active_baseline_binding: str = field(repr=False)
    exact_unit_binding: str = field(repr=False)
    compatibility: CompatibilityObservation
    managed_sectors: tuple[int, ...]
    source_path: str | None = field(default=None, repr=False)
    source_digest: str | None = field(default=None, repr=False)
    plan: dict[str, Any] | None = field(default=None, repr=False)
    normalized_config: dict[str, Any] | None = field(default=None, repr=False)


@dataclass(frozen=True)
class PersistentBackendResult:
    enabled_profiles: tuple[int, ...]
    safety_backup_name: str | None
    reconciliation_completed: bool
    post_validation_completed: bool


class CooperativeCancellationError(RuntimeError):
    """Raised only before the first potentially persistent backend action."""


@runtime_checkable
class Backend(Protocol):
    """Synchronous, use-case-shaped application/backend boundary."""

    def probe(self) -> ProbeSnapshot: ...

    def validate(self) -> ValidationSnapshot: ...

    def switch_profile_guarded(self, target: int) -> int: ...

    def preparation_context(self) -> PreparationContext: ...

    def probe_details(
        self,
        *,
        pid: int | None,
        index: int | None,
        read_sectors: bool,
        private: bool,
    ) -> ProbeDetails: ...

    def validate_details(self, *, private: bool) -> ValidationDetails: ...

    def status(self, *, private: bool) -> StatusSnapshot: ...

    def inspect(self, *, private: bool) -> InspectSnapshot: ...

    def plan(self, config_path: str) -> PlanSnapshot: ...

    def capacity(self, config_path: str | None) -> CapacitySnapshot: ...

    def setup_baseline(
        self,
        *,
        pid: int | None,
        index: int | None,
        replace: bool,
    ) -> SetupSnapshot: ...

    def list_baselines(self, *, private: bool) -> BaselineListSnapshot: ...

    def show_baseline(self, *, private: bool) -> BaselineShowSnapshot: ...

    def use_baseline(self, fingerprint: str) -> BaselineUseSnapshot: ...

    def create_backup(self, label: str) -> BackupSnapshot: ...

    def report_probe(
        self,
        *,
        pid: int | None,
        index: int | None,
    ) -> PublicReportSnapshot: ...

    def report_device(self, *, include_state: bool) -> PublicReportSnapshot: ...

    def check_public_report(self, payload: dict[str, Any]) -> ReportCheckResult: ...

    def debug_export(self, *, include_raw: bool) -> DebugExportSnapshot: ...

    def readonly_smoke(
        self,
        *,
        report_path: str,
        label: str,
    ) -> ReadonlySmokeSnapshot: ...

    def persistent_target(
        self,
        kind: PersistentOperationKind,
        source: str | None = None,
    ) -> PersistentTargetSnapshot: ...

    def execute_persistent(
        self,
        intent: PersistentBackendIntent,
        cancellation: CancellationToken,
        phase_callback: Callable[[PersistentPhase], None],
    ) -> PersistentBackendResult: ...
