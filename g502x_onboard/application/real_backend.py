from __future__ import annotations

from pathlib import Path

from ..baseline import HOME as STATE_HOME, active_baseline, active_target
from ..constants import SAFE_PROFILE
from ..private_io import exclusive_operation_lock
from .models import (
    CompatibilityObservation,
    PreparationContext,
    ProbeSnapshot,
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

    # Preserved persistent primitives are deliberately backend-internal in
    # Phase 1. The public facade exposes no method that invokes these wrappers.
    def apply_plan_preserved(self, plan: dict, *, expected_baseline_fingerprint: str | None = None) -> Path:
        from ..device import apply_plan

        return apply_plan(plan, expected_baseline_fingerprint=expected_baseline_fingerprint)

    def restore_backup_preserved(self, path: str | Path) -> Path:
        from ..device import restore_backup

        return restore_backup(path)

    def restore_baseline_preserved(self) -> Path:
        from ..device import restore_baseline

        return restore_baseline()
