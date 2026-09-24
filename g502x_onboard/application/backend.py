from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .models import (
    BackupSnapshot,
    BaselineListSnapshot,
    BaselineShowSnapshot,
    BaselineUseSnapshot,
    CapacitySnapshot,
    DebugExportSnapshot,
    InspectSnapshot,
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
