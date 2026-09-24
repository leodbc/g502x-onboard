from __future__ import annotations

from pathlib import Path

from ..baseline import (
    DEFAULT_INDEX,
    DEFAULT_PID,
    HOME as STATE_HOME,
    active_baseline,
    active_target,
)
from ..constants import SAFE_PROFILE
from ..private_io import exclusive_operation_lock
from .models import (
    BackupSnapshot,
    BaselineListSnapshot,
    BaselineShowSnapshot,
    BaselineUseSnapshot,
    CapacitySnapshot,
    CompatibilityObservation,
    DebugExportSnapshot,
    InspectSnapshot,
    PlanSnapshot,
    PreparationContext,
    PrivacyClass,
    ProbeDetails,
    ProbeSnapshot,
    PublicReportSnapshot,
    ReadonlySmokeSnapshot,
    ReportCheckResult,
    SetupSnapshot,
    StatusSnapshot,
    ValidationDetails,
    ValidationSnapshot,
    WriteEligibility,
)


OPERATION_LOCK = STATE_HOME / "operation.lock"


def _compatibility(raw: dict) -> CompatibilityObservation:
    architecture = str(raw.get("architecture", "unknown"))
    transport = str(raw.get("transport", "unknown"))
    identity = str(raw.get("identity", "unknown"))
    if identity not in {"unit-bound", "insufficient"}:
        # Adapter-facing compatibility is shareable. Never forward a future
        # raw/unit-specific identity value through this field.
        identity = "unknown"

    write_allowed = (
        raw.get("write_allowed") is True
        and architecture == "compatible"
        and transport == "tested"
        and identity == "unit-bound"
    )
    return CompatibilityObservation(
        architecture=architecture,
        transport=transport,
        identity=identity,
        write_allowed=write_allowed,
        eligibility=(WriteEligibility.ELIGIBLE if write_allowed else WriteEligibility.READ_ONLY),
    )


def _read_only(observed: CompatibilityObservation) -> CompatibilityObservation:
    if observed.eligibility is WriteEligibility.READ_ONLY:
        return observed
    return CompatibilityObservation(
        architecture=observed.architecture,
        transport=observed.transport,
        identity=observed.identity,
        write_allowed=False,
        eligibility=WriteEligibility.READ_ONLY,
    )


class RealBackend:
    """Sole Phase-1 application bridge to the existing synchronous device stack."""

    def probe(self) -> ProbeSnapshot:
        from ..device import probe_device

        pid, index = active_target()
        with exclusive_operation_lock(OPERATION_LOCK):
            result = probe_device(pid=pid, index=index, read_sectors=False)
        device = result.get("device") or {}
        return ProbeSnapshot(
            device_name=device.get("device_name"),
            protocol=device.get("protocol"),
            active_profile=result.get("active_profile"),
            compatibility=_compatibility(result.get("compatibility") or {}),
        )

    def validate(self) -> ValidationSnapshot:
        from ..validator import public_state_summary, validate_device

        with exclusive_operation_lock(OPERATION_LOCK):
            images, report = validate_device()
            summary = public_state_summary(images, report)
        return ValidationSnapshot(
            ok=bool(summary.get("ok")),
            enabled_profiles=tuple(int(v) for v in summary.get("enabled_profiles") or ()),
            error_count=int(summary.get("error_count", 0)),
            warning_count=int(summary.get("warning_count", 0)),
            referenced_macro_starts=int(summary.get("referenced_macro_starts", 0)),
            recovery_ok=all(bool(v) for v in (summary.get("recovery") or {}).values()),
        )

    def switch_profile_guarded(self, target: int) -> int:
        from ..device import (
            assert_active_device_matches_baseline,
            connect_manifest_unit,
            require_ghub_closed,
            switch_profile,
            validate_recovery,
        )
        from ..validator import validate_device

        with exclusive_operation_lock(OPERATION_LOCK):
            require_ghub_closed()
            manifest = assert_active_device_matches_baseline()

            if target == SAFE_PROFILE:
                validate_recovery()
            else:
                _images, report = validate_device()
                if not report.ok:
                    raise RuntimeError(
                        "refusing programmable profile switch because validate failed:\n  "
                        + "\n  ".join(report.errors)
                    )
                if target not in report.enabled_profiles:
                    raise RuntimeError(f"Profile {target} is disabled")

            # Preserve the current CLI race boundary: all safety checks and the
            # volatile mutation remain under the same OS-backed operation lock.
            require_ghub_closed()
            dev = connect_manifest_unit(manifest)
            try:
                switch_profile(dev, target)
            finally:
                dev.close()
        return target

    def preparation_context(self) -> PreparationContext:
        from ..device import (
            connect_manifest_unit,
            get_current_profile,
            probe_device,
            require_ghub_closed,
        )
        from ..validator import validate_device

        with exclusive_operation_lock(OPERATION_LOCK):
            require_ghub_closed()
            baseline_images, baseline_manifest = active_baseline()
            pid, index = active_target()

            # Preparation must be able to return a typed READ_ONLY refusal for
            # a currently unsupported or mismatched target. Probe first using
            # the existing read-only authority instead of requiring write
            # authority merely to observe preparation preconditions.
            probe = probe_device(pid=pid, index=index, read_sectors=False)
            compatibility = _compatibility(probe.get("compatibility") or {})

            baseline_binding = str(baseline_manifest.get("fingerprint") or "")
            exact_unit_binding = str(probe.get("fingerprint") or "")
            if not baseline_binding or not exact_unit_binding:
                raise RuntimeError("preparation is missing its exact-unit binding")

            if exact_unit_binding != baseline_binding:
                compatibility = _read_only(compatibility)

            active_profile = probe.get("active_profile")
            if compatibility.eligibility is WriteEligibility.READ_ONLY:
                return PreparationContext(
                    baseline_images=tuple(sorted(baseline_images.items())),
                    active_baseline_binding=baseline_binding,
                    exact_unit_binding=exact_unit_binding,
                    compatibility=compatibility,
                    active_profile=active_profile,
                    validation_ok=False,
                    host_guard_clear=True,
                    observed_preconditions=(
                        f"active_profile={active_profile}",
                        "validation_ok=false",
                    ),
                )

            # Eligible preparations preserve the current exact-unit and
            # validation authority. These calls are read-only and remain under
            # the same cross-process operation lock.
            images, report = validate_device()
            del images

            manifest = baseline_manifest
            dev = connect_manifest_unit(manifest)
            try:
                active_profile = get_current_profile(dev)
            finally:
                dev.close()

            return PreparationContext(
                baseline_images=tuple(sorted(baseline_images.items())),
                active_baseline_binding=baseline_binding,
                exact_unit_binding=exact_unit_binding,
                compatibility=compatibility,
                active_profile=active_profile,
                validation_ok=report.ok,
                host_guard_clear=True,
                observed_preconditions=(
                    f"active_profile={active_profile}",
                    f"validation_ok={str(report.ok).lower()}",
                ),
            )

    @staticmethod
    def _explicit_target(pid: int | None, index: int | None) -> tuple[int, int]:
        return (
            DEFAULT_PID if pid is None else int(pid),
            DEFAULT_INDEX if index is None else int(index),
        )

    @staticmethod
    def _compile_plan(config_path: str):
        from ..codec import build_plan, plan_json
        from ..config import load_config
        from ..storage import baseline_map

        path, config = load_config(config_path)
        try:
            baseline = baseline_map()
        except Exception as exc:
            raise RuntimeError(
                "planning for a physical device requires an active local baseline. "
                "Run probe and then setup first."
            ) from exc
        plan = build_plan(config, baseline)
        return path, config, plan, plan_json(plan)

    def probe_details(
        self,
        *,
        pid: int | None,
        index: int | None,
        read_sectors: bool,
        private: bool,
    ) -> ProbeDetails:
        from ..baseline import assert_public_report_safe, public_probe_report
        from ..device import probe_device

        pid, index = self._explicit_target(pid, index)
        with exclusive_operation_lock(OPERATION_LOCK):
            raw = probe_device(pid=pid, index=index, read_sectors=read_sectors)

        if private:
            return ProbeDetails(
                payload=raw,
                privacy=PrivacyClass.PRIVATE_DIAGNOSTIC,
            )

        payload = public_probe_report(raw)
        assert_public_report_safe(payload)
        return ProbeDetails(payload=payload, privacy=PrivacyClass.SHAREABLE)

    def validate_details(self, *, private: bool) -> ValidationDetails:
        from ..validator import public_state_summary, state_summary, validate_device

        with exclusive_operation_lock(OPERATION_LOCK):
            images, report = validate_device()
            summary = (
                state_summary(images, report)
                if private
                else public_state_summary(images, report)
            )
        return ValidationDetails(
            ok=report.ok,
            enabled_profiles=tuple(report.enabled_profiles),
            referenced_macro_starts=len(report.macro_starts),
            warnings=tuple(report.warnings),
            errors=tuple(report.errors),
            summary=summary,
            privacy=(
                PrivacyClass.LOCAL_SENSITIVE
                if private
                else PrivacyClass.LOCAL_SENSITIVE
            ),
        )

    def status(self, *, private: bool) -> StatusSnapshot:
        from ..device import (
            assert_active_device_matches_baseline,
            connect_manifest_unit,
            get_current_profile,
            get_descriptor,
        )
        from ..validator import public_state_summary, state_summary, validate_device

        with exclusive_operation_lock(OPERATION_LOCK):
            manifest = assert_active_device_matches_baseline()
            images, report = validate_device()
            summary = (
                state_summary(images, report)
                if private
                else public_state_summary(images, report)
            )

            dev = connect_manifest_unit(manifest)
            try:
                desc = {
                    key: value
                    for key, value in get_descriptor(dev).items()
                    if key != "raw"
                }
                active = get_current_profile(dev)
            finally:
                dev.close()

        return StatusSnapshot(
            active_profile=active,
            descriptor=desc,
            summary=summary,
            enabled_profiles=tuple(report.enabled_profiles),
            privacy=(
                PrivacyClass.LOCAL_SENSITIVE
                if private
                else PrivacyClass.SHAREABLE
            ),
        )

    def inspect(self, *, private: bool) -> InspectSnapshot:
        from ..validator import inspect_rows, validate_device

        with exclusive_operation_lock(OPERATION_LOCK):
            images, report = validate_device()
        if not report.ok:
            raise RuntimeError(
                "device state is invalid; run validate:\n  "
                + "\n  ".join(report.errors)
            )

        rows = inspect_rows(images, report)
        if not private:
            rows = [
                {
                    **row,
                    "instructions": [
                        {
                            key: value
                            for key, value in ins.items()
                            if key != "description"
                        }
                        for ins in row["instructions"]
                    ],
                }
                for row in rows
            ]
        return InspectSnapshot(
            rows=tuple(rows),
            enabled_profiles=tuple(report.enabled_profiles),
            privacy=(
                PrivacyClass.LOCAL_SENSITIVE
                if private
                else PrivacyClass.SHAREABLE
            ),
        )

    def plan(self, config_path: str) -> PlanSnapshot:
        with exclusive_operation_lock(OPERATION_LOCK):
            path, _config, plan, rendered = self._compile_plan(config_path)
        return PlanSnapshot(
            config_path=str(path),
            plan=plan,
            rendered_json=rendered,
        )

    def capacity(self, config_path: str | None) -> CapacitySnapshot:
        if config_path is None:
            return CapacitySnapshot(privacy=PrivacyClass.SHAREABLE)

        path, _config, plan, _rendered = self._compile_plan(config_path)
        store = plan["global_store"]
        return CapacitySnapshot(
            config_path=str(path),
            source_bytes=int(store["source_bytes"]),
            allocated_bytes=int(store["allocated_bytes"]),
            jump_overhead=int(store["jump_overhead"]),
            fragmentation_waste=int(store["fragmentation_waste"]),
            raw_free_bytes=int(store["raw_free_bytes"]),
            usable_free_bytes=int(store["usable_free_bytes"]),
        )

    def setup_baseline(
        self,
        *,
        pid: int | None,
        index: int | None,
        replace: bool,
    ) -> SetupSnapshot:
        from ..device import setup_device

        pid, index = self._explicit_target(pid, index)
        with exclusive_operation_lock(OPERATION_LOCK):
            root, manifest = setup_device(pid=pid, index=index, replace=replace)
        compat = manifest["compatibility"]
        return SetupSnapshot(
            root=str(root),
            fingerprint=str(manifest["fingerprint"]),
            architecture=str(compat["architecture"]),
            transport=str(compat["transport"]),
            write_allowed=bool(compat["write_allowed"]),
        )

    def list_baselines(self, *, private: bool) -> BaselineListSnapshot:
        from ..baseline import list_baselines

        rows = list_baselines()
        if not private:
            rows = [
                {
                    key: value
                    for key, value in row.items()
                    if key not in {"fingerprint", "path"}
                }
                for row in rows
            ]
        return BaselineListSnapshot(
            rows=tuple(rows),
            privacy=(
                PrivacyClass.PRIVATE_DIAGNOSTIC
                if private
                else PrivacyClass.SHAREABLE
            ),
        )

    def show_baseline(self, *, private: bool) -> BaselineShowSnapshot:
        from ..baseline import active_manifest, public_manifest

        manifest = active_manifest()
        return BaselineShowSnapshot(
            payload=manifest if private else public_manifest(manifest),
            privacy=(
                PrivacyClass.PRIVATE_DIAGNOSTIC
                if private
                else PrivacyClass.SHAREABLE
            ),
        )

    def use_baseline(self, fingerprint: str) -> BaselineUseSnapshot:
        from ..baseline import activate

        with exclusive_operation_lock(OPERATION_LOCK):
            root = activate(fingerprint)
        return BaselineUseSnapshot(root=str(root))

    def create_backup(self, label: str) -> BackupSnapshot:
        from ..device import create_backup

        with exclusive_operation_lock(OPERATION_LOCK):
            out = create_backup(label)
        return BackupSnapshot(name=out.name)

    def report_probe(
        self,
        *,
        pid: int | None,
        index: int | None,
    ) -> PublicReportSnapshot:
        from ..baseline import assert_public_report_safe, public_probe_report
        from ..device import probe_device

        pid, index = self._explicit_target(pid, index)
        with exclusive_operation_lock(OPERATION_LOCK):
            result = probe_device(pid=pid, index=index, read_sectors=True)
        report = public_probe_report(result)
        assert_public_report_safe(report)
        return PublicReportSnapshot(payload=report)

    def report_device(self, *, include_state: bool) -> PublicReportSnapshot:
        from ..baseline import (
            active_manifest,
            assert_public_report_safe,
            public_manifest,
        )
        from ..validator import public_state_summary, validate_device

        if include_state:
            with exclusive_operation_lock(OPERATION_LOCK):
                manifest = active_manifest()
                images, validation = validate_device()
                report = public_manifest(manifest)
                report["current_state"] = public_state_summary(images, validation)
        else:
            report = public_manifest(active_manifest())

        assert_public_report_safe(report)
        return PublicReportSnapshot(payload=report)

    def check_public_report(self, payload: dict) -> ReportCheckResult:
        from ..baseline import assert_public_report_safe

        assert_public_report_safe(payload)
        return ReportCheckResult(format=str(payload["format"]))

    def debug_export(self, *, include_raw: bool) -> DebugExportSnapshot:
        from ..validator import export_state, validate_device

        with exclusive_operation_lock(OPERATION_LOCK):
            images, report = validate_device()
            payload = export_state(images, report=report, include_raw=include_raw)
        return DebugExportSnapshot(
            payload=payload,
            default_directory=str(STATE_HOME / "exports"),
        )

    def readonly_smoke(
        self,
        *,
        report_path: str,
        label: str,
    ) -> ReadonlySmokeSnapshot:
        import json

        from ..baseline import (
            assert_public_report_safe,
            public_manifest,
            public_probe_report,
        )
        from ..constants import SAFE_PROFILE, SECTOR_COUNT
        from ..device import (
            assert_active_device_matches_baseline,
            create_backup,
            load_backup,
            probe_device,
            require_ghub_closed,
        )
        from ..private_io import atomic_local_write_text
        from ..validator import public_state_summary, validate_device

        out = Path(report_path).resolve()
        with exclusive_operation_lock(OPERATION_LOCK):
            require_ghub_closed()
            manifest = assert_active_device_matches_baseline()

            probe = probe_device(
                pid=int(manifest["transport"]["pid"]),
                index=int(manifest["transport"]["index"]),
                read_sectors=True,
            )
            probe_report = public_probe_report(probe)
            assert_public_report_safe(probe_report)

            compat = probe_report.get("compatibility") or {}
            scope = probe_report.get("read_scope") or {}
            if compat.get("architecture") != "compatible":
                raise RuntimeError("read-only smoke requires compatible architecture")
            if compat.get("transport") != "tested":
                raise RuntimeError("read-only smoke requires the tested transport")
            if compat.get("write_allowed") is not True:
                raise RuntimeError(
                    "read-only smoke requires the exact unit/firmware write policy "
                    "to remain authorized before release freeze"
                )
            if scope.get("live_sectors") != "read_all_16":
                raise RuntimeError(
                    "read-only smoke requires a complete 16-sector probe; "
                    f"got {scope.get('live_sectors')!r}"
                )

            health = probe_report.get("sector_health") or {}
            if len(health) != SECTOR_COUNT:
                raise RuntimeError(
                    "read-only smoke probe did not report all 16 sector health rows"
                )
            invalid = [
                sector
                for sector, row in health.items()
                if (row or {}).get("health") == "invalid"
            ]
            if invalid:
                raise RuntimeError(
                    "read-only smoke probe found invalid sector(s): "
                    + ", ".join(map(str, invalid))
                )
            if probe_report.get("active_profile") != SAFE_PROFILE:
                raise RuntimeError(
                    "read-only smoke requires Profile 1 SAFE to already be active; "
                    "no automatic profile switch is performed by this gate"
                )

            require_ghub_closed()
            manifest = assert_active_device_matches_baseline()
            images, validation = validate_device()
            if not validation.ok:
                raise RuntimeError(
                    "read-only smoke validate failed:\n  "
                    + "\n  ".join(validation.errors)
                )

            require_ghub_closed()
            manifest = assert_active_device_matches_baseline()
            checkpoint = create_backup(label, manifest=manifest)
            _root, checkpoint_images, _checkpoint_manifest = load_backup(checkpoint)
            if checkpoint_images != images:
                raise RuntimeError(
                    "read-only smoke checkpoint does not exactly match the "
                    "validated sector snapshot"
                )

            report = public_manifest(manifest)
            report["current_state"] = public_state_summary(images, validation)
            assert_public_report_safe(report)
            atomic_local_write_text(
                out,
                json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            )
            emitted = json.loads(out.read_text(encoding="utf-8"))
            assert_public_report_safe(emitted)

        descriptor = probe_report.get("descriptor") or {}
        return ReadonlySmokeSnapshot(
            report_name=out.name,
            checkpoint_name=checkpoint.name,
            architecture=str(compat.get("architecture")),
            transport=str(compat.get("transport")),
            profile_format=descriptor.get("profile_format"),
            macro_format=descriptor.get("macro_format"),
            sector_count=descriptor.get("sector_count"),
            sector_size=descriptor.get("sector_size"),
            enabled_profiles=tuple(validation.enabled_profiles),
            macro_starts=len(validation.macro_starts),
        )

    def persistent_target(self, kind, source=None):
        from ._persistent_backend import prepare_real_target

        return prepare_real_target(kind, source)

    def execute_persistent(self, intent, cancellation, phase_callback):
        from ._persistent_backend import execute_real_persistent

        return execute_real_persistent(intent, cancellation, phase_callback)

    # Preserved persistent primitives remain backend-internal parity scaffolding.
    def apply_plan_preserved(self, plan: dict, *, expected_baseline_fingerprint: str | None = None) -> Path:
        from ..device import apply_plan

        return apply_plan(plan, expected_baseline_fingerprint=expected_baseline_fingerprint)

    def restore_backup_preserved(self, path: str | Path) -> Path:
        from ..device import restore_backup

        return restore_backup(path)

    def restore_baseline_preserved(self) -> Path:
        from ..device import restore_baseline

        return restore_baseline()
