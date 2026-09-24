from __future__ import annotations

from dataclasses import dataclass, field

from .models import (
    CompatibilityObservation,
    PreparationContext,
    ProbeSnapshot,
    ValidationSnapshot,
    WriteEligibility,
)


@dataclass
class FakeBackend:
    """Deterministic in-memory Backend used by application contract tests."""

    baseline_images: dict[int, bytes]
    device_name: str = "G502 X LIGHTSPEED"
    protocol: str = "HID++ 2.0"
    architecture: str = "compatible"
    transport: str = "tested"
    stable_identity: bool = True
    write_allowed: bool = True
    active_baseline_binding: str = "baseline-fixture"
    exact_unit_binding: str = "unit-fixture"
    baseline_matches: bool = True
    exact_unit_matches: bool = True
    active_profile: int = 1
    host_guard_clear: bool = True
    host_guard_recheck_clear: bool | None = None
    validation_ok: bool = True
    recovery_ok: bool = True
    enabled_profiles: tuple[int, ...] = (1, 2)
    read_history: list[str] = field(default_factory=list)
    write_history: list[str] = field(default_factory=list)
    persistent_write_count: int = 0

    def _compatibility(self) -> CompatibilityObservation:
        allowed = (
            self.write_allowed
            and self.stable_identity
            and self.architecture == "compatible"
            and self.transport == "tested"
        )
        return CompatibilityObservation(
            architecture=self.architecture,
            transport=self.transport,
            identity="unit-bound" if self.stable_identity else "insufficient",
            write_allowed=allowed,
            eligibility=(WriteEligibility.ELIGIBLE if allowed else WriteEligibility.READ_ONLY),
        )

    def _preparation_compatibility(self) -> CompatibilityObservation:
        observed = self._compatibility()
        if (
            observed.eligibility is WriteEligibility.READ_ONLY
            or (self.baseline_matches and self.exact_unit_matches)
        ):
            return observed
        return CompatibilityObservation(
            architecture=observed.architecture,
            transport=observed.transport,
            identity=observed.identity,
            write_allowed=False,
            eligibility=WriteEligibility.READ_ONLY,
        )

    def _validation_passes(self) -> bool:
        return self.validation_ok and self.recovery_ok

    def _require_bound_target(self) -> None:
        if not self.baseline_matches:
            raise RuntimeError("active baseline no longer matches")
        if not self.exact_unit_matches:
            raise RuntimeError("connected device no longer matches the exact unit")
        if not self.active_baseline_binding or not self.exact_unit_binding:
            raise RuntimeError("exact-unit baseline binding is missing")

    def probe(self) -> ProbeSnapshot:
        self.read_history.append("probe")
        return ProbeSnapshot(
            device_name=self.device_name,
            protocol=self.protocol,
            active_profile=self.active_profile,
            compatibility=self._compatibility(),
        )

    def validate(self) -> ValidationSnapshot:
        self.read_history.append("validate")
        ok = self._validation_passes()
        return ValidationSnapshot(
            ok=ok,
            enabled_profiles=self.enabled_profiles,
            error_count=0 if ok else 1,
            warning_count=0,
            referenced_macro_starts=0,
            recovery_ok=self.recovery_ok,
        )

    def switch_profile_guarded(self, target: int) -> int:
        self.read_history.append(f"switch-profile-check:{target}")
        if not self.host_guard_clear:
            raise RuntimeError("Logitech configuration software must be closed")
        self._require_bound_target()
        if not self._compatibility().write_allowed:
            raise RuntimeError("device is read-only")

        if target == 1:
            if not self.recovery_ok:
                raise RuntimeError("recovery validation failed")
        else:
            if not self._validation_passes():
                raise RuntimeError("device validation failed")
            if target not in self.enabled_profiles:
                raise RuntimeError(f"Profile {target} is disabled")

        recheck = (
            self.host_guard_clear
            if self.host_guard_recheck_clear is None
            else self.host_guard_recheck_clear
        )
        if not recheck:
            raise RuntimeError("Logitech configuration software became active")

        self.active_profile = target
        self.write_history.append(f"volatile-profile:{target}")
        return target

    def preparation_context(self) -> PreparationContext:
        self.read_history.append("preparation-context")
        if not self.host_guard_clear:
            raise RuntimeError("host guard is not clear")

        compatibility = self._preparation_compatibility()
        validation_ok = self._validation_passes()
        return PreparationContext(
            baseline_images=tuple(sorted(self.baseline_images.items())),
            active_baseline_binding=self.active_baseline_binding,
            exact_unit_binding=self.exact_unit_binding,
            compatibility=compatibility,
            active_profile=self.active_profile,
            validation_ok=validation_ok,
            host_guard_clear=self.host_guard_clear,
            observed_preconditions=(
                f"active_profile={self.active_profile}",
                f"validation_ok={str(validation_ok).lower()}",
            ),
        )

    # Internal parity scaffolding only; no public facade method reaches these.
    def apply_plan_preserved(self, _plan: dict, *, expected_baseline_fingerprint: str | None = None) -> None:
        del expected_baseline_fingerprint
        self.persistent_write_count += 1
        self.write_history.append("persistent-apply")

    def restore_backup_preserved(self, _path: str) -> None:
        self.persistent_write_count += 1
        self.write_history.append("persistent-restore-backup")

    def restore_baseline_preserved(self) -> None:
        self.persistent_write_count += 1
        self.write_history.append("persistent-restore-baseline")
