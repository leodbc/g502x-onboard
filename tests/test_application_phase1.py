from __future__ import annotations

import ast
import json
import struct
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
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
from g502x_onboard.application.models import ApplicationError, ErrorCode, OperationResult
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
        self.assertEqual(prepared.plan_digest, second.value.plan_digest)
        self.assertEqual(self.backend.persistent_write_count, 0)
        self.assertFalse(any("unit-fixture" in str(value) for value in prepared.review.__dict__.values()))
        with self.assertRaises(FrozenInstanceError):
            prepared.plan_digest = "changed"

    def test_prepare_apply_refuses_read_only_backend_without_writes(self):
        self.backend.write_allowed = False
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            path.write_text(json.dumps({"format": 1, "profiles": {"2": {}}}), encoding="utf-8")
            result = self.app.prepare_apply(path)
        self.assertFalse(result.ok)
        self.assertIs(result.error.code, ErrorCode.READ_ONLY)
        self.assertEqual(self.backend.persistent_write_count, 0)

    def test_invalid_config_error_is_local_sensitive_and_sanitized(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "secret-profile-name.json"
            path.write_text("not-json", encoding="utf-8")
            result = self.app.prepare_apply(path)
        self.assertFalse(result.ok)
        self.assertIs(result.privacy, PrivacyClass.LOCAL_SENSITIVE)
        self.assertNotIn("secret-profile-name", result.error.message)


class RealBackendParityTests(unittest.TestCase):
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
