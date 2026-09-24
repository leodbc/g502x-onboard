from __future__ import annotations

from contextlib import nullcontext
import ast
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from g502x_onboard.application import (
    PersistentOperationKind,
    PrivacyClass,
    WriteEligibility,
)
from g502x_onboard.application.backend import Backend
from g502x_onboard.application.real_backend import RealBackend
from g502x_onboard.application.facade import ApplicationFacade
from g502x_onboard.application.fake_backend import FakeBackend
from g502x_onboard.application.models import (
    ApplicationError,
    CompatibilityObservation,
    ErrorCode,
    OperationResult,
    ProfileSwitchResult,
)
from g502x_onboard.codec import crc16_ccitt
from g502x_onboard.constants import SECTOR_SIZE


ROOT = Path(__file__).resolve().parents[1]


def fake_sector(fill: int = 0xFF) -> bytes:
    data = bytearray([fill] * SECTOR_SIZE)
    data[-2:] = struct.pack(">H", crc16_ccitt(data[:-2]))
    return bytes(data)


def fake_baseline() -> dict[int, bytes]:
    images = {sector: fake_sector() for sector in range(16)}
    directory = bytearray(images[0])
    for offset in (2, 6):
        directory[offset] = 1
    for offset in (10, 14, 18):
        directory[offset] = 0
    directory[-2:] = struct.pack(">H", crc16_ccitt(directory[:-2]))
    images[0] = bytes(directory)
    for profile in (1, 2, 3, 4, 5):
        page = bytearray(images[profile])
        page[0] = 1
        page[1] = 0
        page[2] = 0
        page[-2:] = struct.pack(">H", crc16_ccitt(page[:-2]))
        images[profile] = bytes(page)
    return images


class CliRegressionSmokeTests(unittest.TestCase):
    def test_python_g502x_help_command(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "g502x.py"), "--help"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("usage:", result.stdout.lower())
        self.assertIn("g502x", result.stdout.lower())


class ApplicationBoundaryTests(unittest.TestCase):
    def test_public_package_does_not_export_real_backend(self):
        import g502x_onboard.application as application

        self.assertNotIn("RealBackend", application.__all__)
        self.assertFalse(hasattr(application, "RealBackend"))

    def test_public_facade_has_no_persistent_execute_bypass(self):
        forbidden = {
            "execute_prepared",
            "commit_prepared",
            "run_prepared",
            "apply_prepared",
            "restore_prepared",
            "write_prepared",
            "apply_plan_preserved",
            "restore_backup_preserved",
            "restore_baseline_preserved",
        }
        self.assertTrue(forbidden.isdisjoint(dir(ApplicationFacade)))

    def test_application_package_init_has_no_backend_import(self):
        source = (ROOT / "g502x_onboard" / "application" / "__init__.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        self.assertNotIn("RealBackend", imported)
        self.assertNotIn("Backend", imported)

    def test_public_facade_instance_does_not_expose_backend(self):
        backend = FakeBackend(fake_baseline())
        app = ApplicationFacade(backend)

        self.assertFalse(hasattr(app, "_backend"))
        self.assertFalse(hasattr(app, "__dict__"))
        for name in dir(app):
            if name.startswith("__"):
                continue
            self.assertIsNot(getattr(app, name), backend)

    def test_public_factory_does_not_expose_real_backend_through_globals(self):
        from g502x_onboard.application import composition

        self.assertNotIn("RealBackend", vars(composition))
        app = composition.create_application()
        self.assertNotIn("RealBackend", vars(composition))
        self.assertFalse(hasattr(app, "_backend"))

    def test_current_cli_does_not_import_application_layer(self):
        source = (ROOT / "g502x_onboard" / "cli.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        application_imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "application" or module.startswith("application."):
                    application_imports.append(module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("g502x_onboard.application"):
                        application_imports.append(alias.name)
        self.assertEqual(application_imports, [])

    def test_fake_backend_satisfies_protocol(self):
        self.assertIsInstance(FakeBackend(fake_baseline()), Backend)


class ApplicationModelPrivacyTests(unittest.TestCase):
    def test_privacy_classes_are_distinct_and_named(self):
        self.assertIsNot(PrivacyClass.SHAREABLE, PrivacyClass.LOCAL_SENSITIVE)
        self.assertIsNot(PrivacyClass.LOCAL_SENSITIVE, PrivacyClass.PRIVATE_DIAGNOSTIC)
        self.assertEqual(PrivacyClass.SHAREABLE.value, "privacy-safe")
        self.assertEqual(PrivacyClass.LOCAL_SENSITIVE.value, "user-authored")
        self.assertEqual(PrivacyClass.PRIVATE_DIAGNOSTIC.value, "private-diagnostic")

    def test_typed_error_retains_privacy(self):
        error = ApplicationError(
            code=ErrorCode.BACKEND_FAILURE,
            message="sanitized",
            privacy=PrivacyClass.PRIVATE_DIAGNOSTIC,
        )
        result = OperationResult(ok=False, error=error, privacy=error.privacy)
        self.assertFalse(result.ok)
        self.assertIs(result.error.privacy, PrivacyClass.PRIVATE_DIAGNOSTIC)

    def test_operation_result_rejects_failure_with_stale_value(self):
        error = ApplicationError(
            code=ErrorCode.BACKEND_FAILURE,
            message="sanitized",
            privacy=PrivacyClass.PRIVATE_DIAGNOSTIC,
        )
        with self.assertRaises(ValueError):
            OperationResult(
                ok=False,
                value="stale",
                error=error,
                privacy=PrivacyClass.PRIVATE_DIAGNOSTIC,
            )

    def test_operation_result_rejects_error_privacy_mismatch(self):
        error = ApplicationError(
            code=ErrorCode.BACKEND_FAILURE,
            message="sanitized",
            privacy=PrivacyClass.PRIVATE_DIAGNOSTIC,
        )
        with self.assertRaises(ValueError):
            OperationResult(
                ok=False,
                error=error,
                privacy=PrivacyClass.SHAREABLE,
            )

    def test_operation_result_rejects_value_privacy_mismatch(self):
        value = ProfileSwitchResult(
            active_profile=2,
            confirmation_phrase="ENTER PROFILE 2",
        )
        with self.assertRaises(ValueError):
            OperationResult(
                ok=True,
                value=value,
                privacy=PrivacyClass.PRIVATE_DIAGNOSTIC,
            )

    def test_compatibility_rejects_contradictory_write_eligibility(self):
        with self.assertRaises(ValueError):
            CompatibilityObservation(
                architecture="compatible",
                transport="tested",
                identity="unit-bound",
                write_allowed=True,
                eligibility=WriteEligibility.READ_ONLY,
            )

    def test_raw_backend_exception_is_not_promoted_to_shareable_text(self):
        class ExplodingBackend(FakeBackend):
            def probe(self):
                raise RuntimeError("unit-id=SECRET-123 private/path/profile")

        app = ApplicationFacade(ExplodingBackend(fake_baseline()))
        result = app.probe()
        self.assertFalse(result.ok)
        self.assertIs(result.privacy, PrivacyClass.PRIVATE_DIAGNOSTIC)
        self.assertNotIn("SECRET-123", result.error.message)
        self.assertNotIn("private/path", result.error.message)


class ApplicationWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend(fake_baseline())
        self.app = ApplicationFacade(self.backend, id_factory=lambda: "prep-fixed")

    def test_representative_read_only_workflows_use_backend(self):
        probe = self.app.probe()
        validate = self.app.validate()
        self.assertTrue(probe.ok)
        self.assertTrue(validate.ok)
        self.assertEqual(self.backend.read_history, ["probe", "validate"])
        self.assertEqual(self.backend.persistent_write_count, 0)

    def test_volatile_profile_switch_preserves_exact_confirmation(self):
        refused = self.app.switch_profile(2, "yes")
        self.assertFalse(refused.ok)
        self.assertEqual(self.backend.write_history, [])

        accepted = self.app.switch_profile(2, "  ENTER PROFILE 2  ")
        self.assertTrue(accepted.ok)
        self.assertEqual(accepted.value.active_profile, 2)
        self.assertEqual(self.backend.write_history, ["volatile-profile:2"])
        self.assertEqual(self.backend.persistent_write_count, 0)

        safe = self.app.switch_profile(1, "BACK TO SAFE")
        self.assertTrue(safe.ok)
        self.assertEqual(safe.value.confirmation_phrase, "BACK TO SAFE")

    def test_volatile_profile_switch_fails_closed_on_host_guard(self):
        self.backend.host_guard_clear = False
        result = self.app.switch_profile(2, "ENTER PROFILE 2")
        self.assertFalse(result.ok)
        self.assertEqual(self.backend.write_history, [])
        self.assertEqual(self.backend.persistent_write_count, 0)

    def test_volatile_safe_switch_requires_recovery_validity(self):
        self.backend.active_profile = 2
        self.backend.recovery_ok = False
        result = self.app.switch_profile(1, "BACK TO SAFE")
        self.assertFalse(result.ok)
        self.assertEqual(self.backend.write_history, [])
        self.assertEqual(self.backend.persistent_write_count, 0)

    def test_volatile_switch_rechecks_host_guard_before_mutation(self):
        self.backend.host_guard_recheck_clear = False
        result = self.app.switch_profile(2, "ENTER PROFILE 2")
        self.assertFalse(result.ok)
        self.assertEqual(self.backend.write_history, [])
        self.assertEqual(self.backend.persistent_write_count, 0)

    def test_prepare_apply_is_immutable_bound_and_performs_zero_persistent_writes(self):
        payload = {
            "format": 1,
            "profiles": {
                "2": {
                    "settings": {"name": "WORK"},
                    "buttons": {"G4": "copy"},
                }
            },
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "work.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            first = self.app.prepare_apply(path)
            second = self.app.prepare_apply(path)

        self.assertTrue(first.ok, first.error)
        prepared = first.value
        self.assertEqual(prepared.preparation_id, "prep-fixed")
        self.assertIs(prepared.kind, PersistentOperationKind.APPLY_CONFIG)
        self.assertEqual(prepared.required_confirmation_phrase, "APPLY CONFIG")
        self.assertIs(prepared.review.privacy, PrivacyClass.LOCAL_SENSITIVE)
        self.assertIs(prepared.privacy, PrivacyClass.PRIVATE_DIAGNOSTIC)
        self.assertEqual(prepared.active_baseline_binding, "baseline-fixture")
        self.assertEqual(prepared.exact_unit_binding, "unit-fixture")
        self.assertIs(prepared.compatibility.eligibility, WriteEligibility.ELIGIBLE)
        self.assertEqual(
            prepared.review.managed_sectors,
            (0, 2, 3, 4, 5, 8, 9, 10, 11, 12, 13, 14, 15),
        )
        self.assertTrue({1, 6, 7}.isdisjoint(prepared.review.managed_sectors))
        self.assertEqual(prepared.plan_digest, second.value.plan_digest)
        self.assertEqual(self.backend.persistent_write_count, 0)
        self.assertFalse(any("unit-fixture" in str(value) for value in prepared.review.__dict__.values()))
        self.assertNotIn("unit-fixture", repr(prepared))
        self.assertNotIn("baseline-fixture", repr(prepared))
        self.assertNotIn("WORK", repr(prepared))
        with self.assertRaises(FrozenInstanceError):
            prepared.plan_digest = "changed"

    def test_prepare_apply_digest_is_independent_of_preparation_id(self):
        payload = {
            "format": 1,
            "profiles": {
                "2": {
                    "settings": {"name": "WORK"},
                    "buttons": {"G4": "copy"},
                }
            },
        }
        ids = iter(("prep-a", "prep-b"))
        app = ApplicationFacade(self.backend, id_factory=lambda: next(ids))
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "work.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            first = app.prepare_apply(path)
            second = app.prepare_apply(path)

        self.assertTrue(first.ok, first.error)
        self.assertTrue(second.ok, second.error)
        self.assertNotEqual(first.value.preparation_id, second.value.preparation_id)
        self.assertEqual(first.value.plan_digest, second.value.plan_digest)

    def test_prepare_apply_refuses_read_only_backend_without_writes(self):
        self.backend.write_allowed = False
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            path.write_text(json.dumps({"format": 1, "profiles": {"2": {}}}), encoding="utf-8")
            result = self.app.prepare_apply(path)
        self.assertFalse(result.ok)
        self.assertIs(result.error.code, ErrorCode.READ_ONLY)
        self.assertEqual(self.backend.persistent_write_count, 0)

    def test_fake_backend_cannot_broaden_real_write_policy(self):
        cases = (
            {"architecture": "unknown"},
            {"transport": "untested"},
            {"stable_identity": False},
            {"baseline_matches": False},
            {"exact_unit_matches": False},
        )
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            path.write_text(
                json.dumps({"format": 1, "profiles": {"2": {}}}),
                encoding="utf-8",
            )
            for state in cases:
                with self.subTest(state=state):
                    backend = FakeBackend(fake_baseline(), **state)
                    app = ApplicationFacade(backend)
                    result = app.prepare_apply(path)
                    self.assertFalse(result.ok)
                    self.assertIs(result.error.code, ErrorCode.READ_ONLY)
                    self.assertEqual(backend.persistent_write_count, 0)

    def test_invalid_config_error_is_local_sensitive_and_sanitized(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "secret-profile-name.json"
            path.write_text("not-json", encoding="utf-8")
            result = self.app.prepare_apply(path)
        self.assertFalse(result.ok)
        self.assertIs(result.privacy, PrivacyClass.LOCAL_SENSITIVE)
        self.assertNotIn("secret-profile-name", result.error.message)


class RealBackendParityTests(unittest.TestCase):
    def test_probe_never_promotes_unexpected_identity_to_shareable_unit_data(self):
        backend = RealBackend()
        raw_identity = "0123456789ABCDEF01234567"
        probe_result = {
            "device": {
                "device_name": "G502 X LIGHTSPEED",
                "protocol": "HID++ 2.0",
            },
            "active_profile": 1,
            "compatibility": {
                "architecture": "compatible",
                "transport": "tested",
                "identity": raw_identity,
                "write_allowed": True,
            },
        }
        with (
            patch(
                "g502x_onboard.application.real_backend.active_target",
                return_value=(0xC547, 1),
            ),
            patch(
                "g502x_onboard.application.real_backend.exclusive_operation_lock",
                side_effect=lambda _path: nullcontext(),
            ),
            patch(
                "g502x_onboard.device.probe_device",
                return_value=probe_result,
            ),
        ):
            result = backend.probe()

        self.assertIs(result.privacy, PrivacyClass.SHAREABLE)
        self.assertEqual(result.compatibility.identity, "unknown")
        self.assertFalse(result.compatibility.write_allowed)
        self.assertNotIn(raw_identity, repr(result))

    def test_real_preparation_returns_typed_read_only_on_exact_unit_mismatch(self):
        backend = RealBackend()
        baseline_fingerprint = "a" * 24
        live_fingerprint = "b" * 24
        baseline = fake_baseline()
        manifest = {
            "fingerprint": baseline_fingerprint,
            "transport": {"pid": 0xC547, "index": 1},
        }
        probe_result = {
            "fingerprint": live_fingerprint,
            "active_profile": 1,
            "compatibility": {
                "architecture": "compatible",
                "transport": "tested",
                "identity": "unit-bound",
                "write_allowed": True,
            },
        }
        app = ApplicationFacade(backend)
        with (
            patch(
                "g502x_onboard.application.real_backend.active_baseline",
                return_value=(baseline, manifest),
            ),
            patch(
                "g502x_onboard.application.real_backend.active_target",
                return_value=(0xC547, 1),
            ),
            patch(
                "g502x_onboard.application.real_backend.exclusive_operation_lock",
                side_effect=lambda _path: nullcontext(),
            ),
            patch("g502x_onboard.device.require_ghub_closed"),
            patch(
                "g502x_onboard.device.probe_device",
                return_value=probe_result,
            ),
            patch("g502x_onboard.validator.validate_device") as validate,
        ):
            result = app.prepare_apply("not-read-because-read-only.json")

        self.assertFalse(result.ok)
        self.assertIs(result.error.code, ErrorCode.READ_ONLY)
        self.assertIs(result.privacy, PrivacyClass.SHAREABLE)
        validate.assert_not_called()

    def test_safe_profile_switch_preserves_guard_and_recovery_sequence(self):
        backend = RealBackend()
        events = []

        class Device:
            def close(self):
                events.append("close")

        def guard():
            events.append("guard")

        def assert_match():
            events.append("assert")
            return {"fingerprint": "a" * 24}

        def recovery():
            events.append("recovery")

        def connect(_manifest):
            events.append("connect")
            return Device()

        def switch(_dev, target):
            events.append(f"switch:{target}")

        with (
            patch(
                "g502x_onboard.application.real_backend.exclusive_operation_lock",
                side_effect=lambda _path: nullcontext(),
            ),
            patch(
                "g502x_onboard.device.require_ghub_closed",
                side_effect=guard,
            ),
            patch(
                "g502x_onboard.device.assert_active_device_matches_baseline",
                side_effect=assert_match,
            ),
            patch(
                "g502x_onboard.device.validate_recovery",
                side_effect=recovery,
            ),
            patch(
                "g502x_onboard.device.connect_manifest_unit",
                side_effect=connect,
            ),
            patch(
                "g502x_onboard.device.switch_profile",
                side_effect=switch,
            ),
            patch("g502x_onboard.validator.validate_device") as validate,
        ):
            self.assertEqual(backend.switch_profile_guarded(1), 1)

        self.assertEqual(
            events,
            ["guard", "assert", "recovery", "guard", "connect", "switch:1", "close"],
        )
        validate.assert_not_called()

    def test_programmable_profile_switch_preserves_validation_sequence(self):
        backend = RealBackend()
        events = []
        report = SimpleNamespace(ok=True, errors=[], enabled_profiles=(1, 2))

        class Device:
            def close(self):
                events.append("close")

        def guard():
            events.append("guard")

        def assert_match():
            events.append("assert")
            return {"fingerprint": "a" * 24}

        def validate():
            events.append("validate")
            return {}, report

        def connect(_manifest):
            events.append("connect")
            return Device()

        def switch(_dev, target):
            events.append(f"switch:{target}")

        with (
            patch(
                "g502x_onboard.application.real_backend.exclusive_operation_lock",
                side_effect=lambda _path: nullcontext(),
            ),
            patch(
                "g502x_onboard.device.require_ghub_closed",
                side_effect=guard,
            ),
            patch(
                "g502x_onboard.device.assert_active_device_matches_baseline",
                side_effect=assert_match,
            ),
            patch("g502x_onboard.device.validate_recovery") as recovery,
            patch(
                "g502x_onboard.device.connect_manifest_unit",
                side_effect=connect,
            ),
            patch(
                "g502x_onboard.device.switch_profile",
                side_effect=switch,
            ),
            patch(
                "g502x_onboard.validator.validate_device",
                side_effect=validate,
            ),
        ):
            self.assertEqual(backend.switch_profile_guarded(2), 2)

        self.assertEqual(
            events,
            ["guard", "assert", "validate", "guard", "connect", "switch:2", "close"],
        )
        recovery.assert_not_called()

    def test_apply_wrapper_delegates_to_existing_authority(self):
        backend = RealBackend()
        sentinel = Path("backup")
        with patch("g502x_onboard.device.apply_plan", return_value=sentinel) as call:
            result = backend.apply_plan_preserved(
                {"plan": True},
                expected_baseline_fingerprint="abc",
            )
        self.assertEqual(result, sentinel)
        call.assert_called_once_with(
            {"plan": True},
            expected_baseline_fingerprint="abc",
        )

    def test_restore_backup_wrapper_delegates_to_existing_authority(self):
        backend = RealBackend()
        sentinel = Path("safety")
        with patch("g502x_onboard.device.restore_backup", return_value=sentinel) as call:
            result = backend.restore_backup_preserved("backup-name")
        self.assertEqual(result, sentinel)
        call.assert_called_once_with("backup-name")

    def test_restore_baseline_wrapper_delegates_to_existing_authority(self):
        backend = RealBackend()
        sentinel = Path("safety")
        with patch("g502x_onboard.device.restore_baseline", return_value=sentinel) as call:
            result = backend.restore_baseline_preserved()
        self.assertEqual(result, sentinel)
        call.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
