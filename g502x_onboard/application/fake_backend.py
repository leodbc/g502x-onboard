from __future__ import annotations

from dataclasses import dataclass, field

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
    firmware_supported: bool = True
    persistent_policy_authorized: bool = True
    post_validation_ok: bool = True
    backup_targets: dict[str, dict[int, bytes]] = field(default_factory=dict, repr=False)
    fault_at: str | None = None
    block_at: str | None = None
    block_entered: object | None = field(default=None, repr=False)
    block_release: object | None = field(default=None, repr=False)

    def _compatibility(self) -> CompatibilityObservation:
        allowed = (
            self.write_allowed
            and self.stable_identity
            and self.architecture == "compatible"
            and self.transport == "tested"
            and self.firmware_supported
            and self.persistent_policy_authorized
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

    def _require_validated_target(self) -> None:
        self._require_bound_target()
        if not self._compatibility().write_allowed:
            raise RuntimeError("device is read-only")

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
        self._require_validated_target()
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
        if not self.active_baseline_binding or not self.exact_unit_binding:
            raise RuntimeError("preparation is missing its exact-unit binding")

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

    def probe_details(
        self,
        *,
        pid: int | None,
        index: int | None,
        read_sectors: bool,
        private: bool,
    ) -> ProbeDetails:
        self.read_history.append("probe-details")
        pid = 0xC547 if pid is None else int(pid)
        index = 1 if index is None else int(index)
        compatibility = self._compatibility()
        deep_read_allowed = self.architecture == "compatible"
        live_scope = (
            "read_all_16"
            if read_sectors and deep_read_allowed
            else (
                "skipped_unknown_architecture"
                if not deep_read_allowed
                else "skipped_by_request"
            )
        )
        oob_scope = "read" if deep_read_allowed else "skipped_unknown_architecture"
        payload = {
            "tool_version": "fake",
            "transport": {
                "pid": pid,
                "pid_hex": f"0x{pid:04X}",
                "index": index,
                "index_hex": f"0x{index:02X}",
            },
            "device": {
                "device_name": self.device_name,
                "protocol": self.protocol,
                "firmware": [],
            },
            "active_profile": self.active_profile,
            "compatibility": {
                "architecture": compatibility.architecture,
                "transport": compatibility.transport,
                "identity": compatibility.identity,
                "write_allowed": compatibility.write_allowed,
            },
            "descriptor": {
                "profile_format": 3,
                "macro_format": 1,
                "sector_count": 16,
                "sector_size": 255,
            },
            "read_scope": {
                "live_sectors": live_scope,
                "oob": oob_scope,
                "active_profile": "read",
            },
            "oob": {"directory": [], "page_count": 0, "pages_available": []},
        }
        if read_sectors and deep_read_allowed:
            payload["sector_health"] = {
                str(i): {"crc_ok": True, "health": "crc_valid", "erased": False}
                for i in range(16)
            }
        if private:
            payload["fingerprint"] = self.exact_unit_binding
            return ProbeDetails(
                payload=payload,
                privacy=PrivacyClass.PRIVATE_DIAGNOSTIC,
            )
        return ProbeDetails(payload=payload, privacy=PrivacyClass.SHAREABLE)

    def validate_details(self, *, private: bool) -> ValidationDetails:
        self.read_history.append("validate-details")
        self._require_validated_target()
        ok = self._validation_passes()
        summary = {
            "ok": ok,
            "enabled_profiles": list(self.enabled_profiles),
            "recovery": {"1": self.recovery_ok, "6": self.recovery_ok, "7": self.recovery_ok},
            "profiles": {
                str(profile): {
                    "enabled": profile in self.enabled_profiles,
                    "crc_ok": True,
                    **(
                        {"metadata": {"name": f"P{profile}", "polling_rate_hz": 1000}}
                        if private else {}
                    ),
                }
                for profile in (2, 3, 4, 5)
            },
            "macro_pages": {
                str(sector): {
                    "crc_ok": True,
                    "health": "crc_valid",
                    "erased": False,
                    **(
                        {"payload_non_ff": 0, "high_water": 0, "payload_capacity": 253}
                        if private else {}
                    ),
                }
                for sector in range(8, 16)
            },
            "referenced_macro_starts": 0,
        }
        if not private:
            summary["error_count"] = 0 if ok else 1
            summary["warning_count"] = 0
        return ValidationDetails(
            ok=ok,
            enabled_profiles=self.enabled_profiles,
            referenced_macro_starts=0,
            warnings=(),
            errors=() if ok else ("fake validation failure",),
            summary=summary,
        )

    def status(self, *, private: bool) -> StatusSnapshot:
        self.read_history.append("status")
        details = self.validate_details(private=private)
        return StatusSnapshot(
            active_profile=self.active_profile,
            descriptor={"profile_format": 3, "macro_format": 1},
            summary=details.summary,
            enabled_profiles=self.enabled_profiles,
            privacy=(
                PrivacyClass.LOCAL_SENSITIVE
                if private
                else PrivacyClass.SHAREABLE
            ),
        )

    def inspect(self, *, private: bool) -> InspectSnapshot:
        self.read_history.append("inspect")
        self._require_validated_target()
        if not self._validation_passes():
            raise RuntimeError("device state is invalid; run validate:\n  fake validation failure")
        return InspectSnapshot(
            rows=(),
            enabled_profiles=self.enabled_profiles,
            privacy=(
                PrivacyClass.LOCAL_SENSITIVE
                if private
                else PrivacyClass.SHAREABLE
            ),
        )

    def plan(self, config_path: str) -> PlanSnapshot:
        from ..codec import build_plan, plan_json
        from ..config import load_config

        self.read_history.append("plan")
        path, _config = load_config(config_path)
        plan = build_plan(_config, self.baseline_images)
        return PlanSnapshot(
            config_path=str(path),
            plan=plan,
            rendered_json=plan_json(plan),
        )

    def capacity(self, config_path: str | None) -> CapacitySnapshot:
        self.read_history.append("capacity")
        if config_path is None:
            return CapacitySnapshot(privacy=PrivacyClass.SHAREABLE)
        planned = self.plan(config_path)
        store = planned.plan["global_store"]
        return CapacitySnapshot(
            config_path=planned.config_path,
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
        del pid, index, replace
        self.read_history.append("setup-baseline")
        if not self.host_guard_clear:
            raise RuntimeError("Logitech configuration software must be closed")
        if self.architecture != "compatible":
            raise RuntimeError("device memory geometry is not compatible")
        if self.active_profile != 1:
            raise RuntimeError("Profile 1 SAFE must be active before setup")
        if not self.validation_ok:
            raise RuntimeError("setup state is structurally invalid")
        return SetupSnapshot(
            root="/private/fake-baseline",
            fingerprint=self.exact_unit_binding,
            architecture=self.architecture,
            transport=self.transport,
            write_allowed=self._compatibility().write_allowed,
        )

    def list_baselines(self, *, private: bool) -> BaselineListSnapshot:
        self.read_history.append("baseline-list")
        row = {
            "active": True,
            "device_name": self.device_name,
            "transport": {"pid_hex": "0xC547", "index_hex": "0x01"},
            "compatibility": {"transport": self.transport},
        }
        if private:
            row["fingerprint"] = self.exact_unit_binding
            row["path"] = "/private/fake-baseline"
        return BaselineListSnapshot(
            rows=(row,),
            privacy=(
                PrivacyClass.PRIVATE_DIAGNOSTIC
                if private
                else PrivacyClass.SHAREABLE
            ),
        )

    def show_baseline(self, *, private: bool) -> BaselineShowSnapshot:
        self.read_history.append("baseline-show")
        if private:
            payload = {
                "format": "g502x-device-baseline-v1",
                "fingerprint": self.exact_unit_binding,
            }
            privacy = PrivacyClass.PRIVATE_DIAGNOSTIC
        else:
            payload = {
                "format": "g502x-device-report-v1",
                "tool_version": "fake",
                "transport": {},
                "compatibility": {},
                "descriptor": {},
                "device": {"firmware": []},
                "oob": {"directory": [], "page_count": 0, "pages_available": []},
                "sector_health": {},
            }
            privacy = PrivacyClass.SHAREABLE
        return BaselineShowSnapshot(payload=payload, privacy=privacy)

    def use_baseline(self, fingerprint: str) -> BaselineUseSnapshot:
        self.read_history.append("baseline-use")
        if fingerprint != self.active_baseline_binding:
            raise RuntimeError("unknown baseline")
        return BaselineUseSnapshot(root="/private/fake-baseline")

    def create_backup(self, label: str) -> BackupSnapshot:
        from ..private_io import normalize_private_component

        self.read_history.append("backup")
        label = normalize_private_component(label, context="checkpoint label")
        self._require_validated_target()
        return BackupSnapshot(name=f"{label}-fake")

    def report_probe(
        self,
        *,
        pid: int | None,
        index: int | None,
    ) -> PublicReportSnapshot:
        self.read_history.append("report-probe")
        details = self.probe_details(
            pid=pid,
            index=index,
            read_sectors=True,
            private=False,
        )
        from ..baseline import assert_public_report_safe

        payload = dict(details.payload)
        payload["format"] = "g502x-probe-report-v1"
        assert_public_report_safe(payload)
        return PublicReportSnapshot(payload=payload)

    def report_device(self, *, include_state: bool) -> PublicReportSnapshot:
        self.read_history.append("report-device")
        payload = {
            "format": "g502x-device-report-v1",
            "tool_version": "fake",
            "transport": {},
            "compatibility": {},
            "descriptor": {},
            "device": {"firmware": []},
            "oob": {"directory": [], "page_count": 0, "pages_available": []},
            "sector_health": {},
        }
        if include_state:
            payload["current_state"] = self.validate_details(private=False).summary

        from ..baseline import assert_public_report_safe

        assert_public_report_safe(payload)
        return PublicReportSnapshot(payload=payload)

    def check_public_report(self, payload: dict) -> ReportCheckResult:
        from ..baseline import assert_public_report_safe

        self.read_history.append("report-check")
        assert_public_report_safe(payload)
        return ReportCheckResult(format=str(payload["format"]))

    def debug_export(self, *, include_raw: bool) -> DebugExportSnapshot:
        self.read_history.append("debug-export")
        self._require_validated_target()
        payload = {
            "format": "g502x-state-export-v1",
            "privacy": {
                "classification": "private-diagnostic",
                "shareable": False,
                "contains_raw_sectors": include_raw,
            },
        }
        return DebugExportSnapshot(
            payload=payload,
            default_directory="/private/exports",
        )

    def readonly_smoke(
        self,
        *,
        report_path: str,
        label: str,
    ) -> ReadonlySmokeSnapshot:
        self.read_history.append("readonly-smoke")
        compatibility = self._compatibility()
        if not self.host_guard_clear:
            raise RuntimeError("read-only smoke requires host guard clear")
        self._require_bound_target()
        if compatibility.architecture != "compatible":
            raise RuntimeError("read-only smoke requires compatible architecture")
        if compatibility.transport != "tested":
            raise RuntimeError("read-only smoke requires the tested transport")
        if not compatibility.write_allowed:
            raise RuntimeError("read-only smoke requires write-authorized exact unit")
        if self.active_profile != 1:
            raise RuntimeError("read-only smoke requires Profile 1 SAFE")
        if not self._validation_passes():
            raise RuntimeError("read-only smoke requires valid recovery/device state")
        recheck = (
            self.host_guard_clear
            if self.host_guard_recheck_clear is None
            else self.host_guard_recheck_clear
        )
        if not recheck:
            raise RuntimeError("Logitech configuration software became active")

        from ..private_io import normalize_private_component

        normalize_private_component(label, context="checkpoint label")
        return ReadonlySmokeSnapshot(
            report_name=str(report_path).rsplit("/", 1)[-1],
            checkpoint_name="fake-checkpoint",
            architecture=self.architecture,
            transport=self.transport,
            profile_format=3,
            macro_format=1,
            sector_count=16,
            sector_size=255,
            enabled_profiles=self.enabled_profiles,
            macro_starts=0,
        )

    def persistent_target(self, kind, source=None):
        from ._fake_persistent import prepare_fake_target

        return prepare_fake_target(self, kind, source)

    def execute_persistent(self, intent, cancellation, phase_callback):
        from ._fake_persistent import execute_fake_persistent

        return execute_fake_persistent(
            self,
            intent,
            cancellation,
            phase_callback,
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
