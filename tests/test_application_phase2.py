from __future__ import annotations

import ast
import json
import struct
import tempfile
import unittest
from pathlib import Path
from threading import Event, Thread
from unittest.mock import patch

from g502x_onboard.application import ErrorCode, PrivacyClass, create_application
from g502x_onboard.application.coordinator import OperationCoordinator
from g502x_onboard.application.facade import ApplicationFacade
from g502x_onboard.application.fake_backend import FakeBackend
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


def function_node(tree: ast.AST, name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"missing function {name}")


class Phase2CliArchitectureTests(unittest.TestCase):
    def setUp(self):
        source = (ROOT / "g502x_onboard" / "cli.py").read_text(encoding="utf-8")
        self.tree = ast.parse(source)

    def test_migrated_commands_enter_through_public_facade(self):
        migrated = (
            "cmd_probe",
            "cmd_report_probe",
            "cmd_setup",
            "cmd_baseline_list",
            "cmd_baseline_show",
            "cmd_baseline_use",
            "cmd_report_check",
            "cmd_report_device",
            "cmd_smoke_readonly",
            "cmd_plan",
            "cmd_capacity",
            "cmd_validate",
            "cmd_status",
            "cmd_inspect",
            "cmd_debug_export",
            "cmd_profile",
            "cmd_backup",
        )
        forbidden_calls = {
            "probe_device",
            "setup_device",
            "validate_device",
            "active_manifest",
            "list_baselines",
            "activate",
            "assert_public_report_safe",
            "public_manifest",
            "public_probe_report",
            "state_summary",
            "public_state_summary",
            "inspect_rows",
            "export_state",
            "create_backup",
            "assert_active_device_matches_baseline",
            "connect_manifest_unit",
            "get_current_profile",
            "get_descriptor",
            "require_ghub_closed",
            "switch_profile",
            "validate_recovery",
        }
        for name in migrated:
            with self.subTest(command=name):
                node = function_node(self.tree, name)
                called = {
                    child.func.id
                    for child in ast.walk(node)
                    if isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Name)
                }
                self.assertIn("create_application", called)
                self.assertTrue(forbidden_calls.isdisjoint(called))

                direct_device_imports = [
                    child
                    for child in ast.walk(node)
                    if isinstance(child, ast.ImportFrom)
                    and child.module == "device"
                ]
                self.assertEqual(direct_device_imports, [])

    def test_only_phase3_deferred_commands_keep_legacy_persistent_authority(self):
        expected = {
            "cmd_apply": {"apply_plan", "active_manifest", "validate_device"},
            "cmd_restore": {"load_backup", "restore_backup", "validate_device"},
            "cmd_baseline_restore": {"restore_baseline", "validate_device"},
        }
        for name, required_calls in expected.items():
            node = function_node(self.tree, name)
            called = {
                child.func.id
                for child in ast.walk(node)
                if isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
            }
            self.assertTrue(required_calls.issubset(called))
            self.assertNotIn("create_application", called)

    def test_persistent_confirmation_literals_remain_exact(self):
        source = (ROOT / "g502x_onboard" / "cli.py").read_text(encoding="utf-8")
        for phrase in ("APPLY CONFIG", "RESTORE BACKUP", "RESTORE BASELINE"):
            self.assertIn(phrase, source)


class Phase2ApplicationWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend(fake_baseline())
        self.app = ApplicationFacade(self.backend)

    def test_all_migrated_nonpersistent_use_cases_leave_persistent_count_zero(self):
        self.assertTrue(self.app.probe_details(
            pid=0xC547,
            index=1,
            read_sectors=True,
            private=False,
        ).ok)
        self.assertTrue(self.app.validate_details(private=False).ok)
        self.assertTrue(self.app.status(private=False).ok)
        self.assertTrue(self.app.inspect(private=False).ok)
        self.assertTrue(self.app.capacity(None).ok)
        self.assertTrue(self.app.setup_baseline(
            pid=0xC547,
            index=1,
            replace=False,
        ).ok)
        self.assertTrue(self.app.list_baselines(private=False).ok)
        self.assertTrue(self.app.show_baseline(private=False).ok)
        self.assertTrue(self.app.use_baseline("baseline-fixture").ok)
        self.assertTrue(self.app.create_backup("phase2").ok)
        self.assertTrue(self.app.report_probe(pid=0xC547, index=1).ok)
        self.assertTrue(self.app.report_device(include_state=True).ok)
        self.assertTrue(self.app.check_public_report(
            {"format": "g502x-probe-report-v1"}
        ).ok)
        self.assertTrue(self.app.debug_export(include_raw=False).ok)
        self.assertTrue(self.app.readonly_smoke(
            report_path="report.json",
            label="smoke",
        ).ok)
        self.assertTrue(self.app.switch_profile(2, "ENTER PROFILE 2").ok)

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            path.write_text(
                json.dumps({"format": 1, "profiles": {"2": {}}}),
                encoding="utf-8",
            )
            self.assertTrue(self.app.plan(str(path)).ok)
            self.assertTrue(self.app.capacity(str(path)).ok)

        self.assertEqual(self.backend.persistent_write_count, 0)

    def test_public_baseline_listing_redacts_identity_and_local_path(self):
        result = self.app.list_baselines(private=False)
        self.assertTrue(result.ok)
        row = result.value.rows[0]
        self.assertNotIn("fingerprint", row)
        self.assertNotIn("path", row)
        self.assertIs(result.privacy, PrivacyClass.SHAREABLE)

        private = self.app.list_baselines(private=True)
        self.assertTrue(private.ok)
        self.assertIn("fingerprint", private.value.rows[0])
        self.assertIn("path", private.value.rows[0])
        self.assertIs(private.privacy, PrivacyClass.PRIVATE_DIAGNOSTIC)

    def test_config_errors_retain_cli_distinction(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bad.json"
            path.write_text("not-json", encoding="utf-8")
            result = self.app.plan(str(path))
        self.assertFalse(result.ok)
        self.assertIs(result.error.code, ErrorCode.CONFIG_ERROR)
        self.assertIs(result.privacy, PrivacyClass.LOCAL_SENSITIVE)

    def test_coordinator_rejects_overlap_without_queueing(self):
        coordinator = OperationCoordinator()
        app = ApplicationFacade(self.backend, coordinator=coordinator)
        with coordinator.claim():
            result = app.validate_details(private=False)
        self.assertFalse(result.ok)
        self.assertIs(result.error.code, ErrorCode.BUSY)
        self.assertEqual(self.backend.persistent_write_count, 0)

    def _assert_process_wide_overlap_refused(self, app1, app2, entered, release):
        first_results = []
        worker = Thread(
            target=lambda: first_results.append(
                app1.validate_details(private=False)
            )
        )
        worker.start()
        try:
            self.assertTrue(entered.wait(timeout=2.0))
            second = app2.validate_details(private=False)
            self.assertFalse(second.ok)
            self.assertIs(second.error.code, ErrorCode.BUSY)
        finally:
            release.set()
            worker.join(timeout=2.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(len(first_results), 1)
        self.assertTrue(first_results[0].ok)
        self.assertTrue(app2.validate_details(private=False).ok)

    def test_default_direct_facades_share_one_process_coordinator(self):
        entered = Event()
        release = Event()

        class BlockingBackend(FakeBackend):
            def validate_details(self, *, private):
                entered.set()
                if not release.wait(timeout=2.0):
                    raise RuntimeError("test release timeout")
                return super().validate_details(private=private)

        app1 = ApplicationFacade(BlockingBackend(fake_baseline()))
        app2 = ApplicationFacade(FakeBackend(fake_baseline()))
        self._assert_process_wide_overlap_refused(app1, app2, entered, release)

    def test_public_factory_facades_share_one_process_coordinator(self):
        entered = Event()
        release = Event()

        class BlockingBackend(FakeBackend):
            def validate_details(self, *, private):
                entered.set()
                if not release.wait(timeout=2.0):
                    raise RuntimeError("test release timeout")
                return super().validate_details(private=private)

        first_backend = BlockingBackend(fake_baseline())
        second_backend = FakeBackend(fake_baseline())
        with patch(
            "g502x_onboard.application.real_backend.RealBackend",
            side_effect=[first_backend, second_backend],
        ):
            app1 = create_application()
            app2 = create_application()

        self._assert_process_wide_overlap_refused(app1, app2, entered, release)

    def test_coordinator_releases_after_backend_failure(self):
        class OneShotFailure(FakeBackend):
            failed = False

            def probe_details(self, **kwargs):
                if not self.failed:
                    self.failed = True
                    raise RuntimeError("synthetic read failure")
                return super().probe_details(**kwargs)

        backend = OneShotFailure(fake_baseline())
        app = ApplicationFacade(backend)
        other = ApplicationFacade(FakeBackend(fake_baseline()))
        first = app.probe_details(
            pid=0xC547,
            index=1,
            read_sectors=False,
            private=False,
        )
        second = other.probe_details(
            pid=0xC547,
            index=1,
            read_sectors=False,
            private=False,
        )
        self.assertFalse(first.ok)
        self.assertTrue(second.ok)
        self.assertEqual(backend.persistent_write_count, 0)

    def test_cli_private_error_detail_requires_explicit_opt_in(self):
        secret = "unit-id=SECRET-123 /private/baseline/profile"

        class ExplodingBackend(FakeBackend):
            def status(self, *, private):
                del private
                raise RuntimeError(secret)

        result = ApplicationFacade(
            ExplodingBackend(fake_baseline())
        ).status(private=False)
        self.assertFalse(result.ok)
        self.assertIs(result.privacy, PrivacyClass.PRIVATE_DIAGNOSTIC)
        self.assertEqual(result.error.message, "status failed")
        self.assertEqual(result.error.detail, secret)

        from g502x_onboard.cli import _app_value

        with self.assertRaises(RuntimeError) as public_error:
            _app_value(result)
        self.assertEqual(str(public_error.exception), "status failed")
        self.assertNotIn("SECRET-123", str(public_error.exception))

        with self.assertRaises(RuntimeError) as private_error:
            _app_value(result, expose_private_detail=True)
        self.assertIn("PRIVATE diagnostic:", str(private_error.exception))
        self.assertIn("SECRET-123", str(private_error.exception))

    def test_fake_backend_readonly_smoke_cannot_broaden_real_policy(self):
        cases = (
            {"architecture": "unknown"},
            {"transport": "untested"},
            {"stable_identity": False},
            {"write_allowed": False},
            {"baseline_matches": False},
            {"exact_unit_matches": False},
            {"host_guard_clear": False},
            {"host_guard_recheck_clear": False},
            {"active_profile": 2},
            {"recovery_ok": False},
        )
        for state in cases:
            with self.subTest(state=state):
                backend = FakeBackend(fake_baseline(), **state)
                app = ApplicationFacade(backend)
                result = app.readonly_smoke(
                    report_path="report.json",
                    label="smoke",
                )
                self.assertFalse(result.ok)
                self.assertEqual(backend.persistent_write_count, 0)

        unsafe_label = self.app.readonly_smoke(
            report_path="report.json",
            label="../escape",
        )
        self.assertFalse(unsafe_label.ok)
        self.assertEqual(self.backend.persistent_write_count, 0)

    def test_fake_backend_hardware_paths_refuse_bound_target_mismatch(self):
        for state in (
            {"baseline_matches": False},
            {"exact_unit_matches": False},
        ):
            with self.subTest(state=state):
                backend = FakeBackend(fake_baseline(), **state)
                app = ApplicationFacade(backend)
                operations = (
                    lambda: app.validate_details(private=False),
                    lambda: app.status(private=False),
                    lambda: app.inspect(private=False),
                    lambda: app.create_backup("phase2"),
                    lambda: app.report_device(include_state=True),
                    lambda: app.debug_export(include_raw=False),
                )
                for operation in operations:
                    result = operation()
                    self.assertFalse(result.ok)
                self.assertTrue(app.report_device(include_state=False).ok)
                self.assertEqual(backend.persistent_write_count, 0)

    def test_fake_backend_setup_preserves_real_readonly_preconditions(self):
        for state in (
            {"architecture": "unknown"},
            {"host_guard_clear": False},
            {"active_profile": 2},
            {"validation_ok": False},
        ):
            with self.subTest(state=state):
                backend = FakeBackend(fake_baseline(), **state)
                result = ApplicationFacade(backend).setup_baseline(
                    pid=0xC547,
                    index=1,
                    replace=False,
                )
                self.assertFalse(result.ok)
                self.assertEqual(backend.persistent_write_count, 0)

    def test_fake_probe_unknown_architecture_never_claims_deep_sector_reads(self):
        backend = FakeBackend(fake_baseline(), architecture="unknown")
        result = ApplicationFacade(backend).probe_details(
            pid=0xC547,
            index=1,
            read_sectors=True,
            private=False,
        )
        self.assertTrue(result.ok)
        self.assertEqual(
            result.value.payload["read_scope"]["live_sectors"],
            "skipped_unknown_architecture",
        )
        self.assertNotIn("sector_health", result.value.payload)
        self.assertEqual(backend.persistent_write_count, 0)

    def test_fake_backend_public_report_check_uses_authoritative_validator(self):
        report = self.app.report_probe(pid=0xC547, index=1)
        self.assertTrue(report.ok)
        unsafe = dict(report.value.payload)
        unsafe["fingerprint"] = "a" * 24
        checked = self.app.check_public_report(unsafe)
        self.assertFalse(checked.ok)
        self.assertEqual(self.backend.persistent_write_count, 0)

    def test_fake_backend_rejects_unknown_baseline_and_unsafe_backup_label(self):
        unknown = self.app.use_baseline("not-the-fixture")
        self.assertFalse(unknown.ok)

        unsafe_label = self.app.create_backup("../escape")
        self.assertFalse(unsafe_label.ok)
        self.assertEqual(self.backend.persistent_write_count, 0)

    def test_public_facade_has_no_persistent_execution_surface(self):
        forbidden = {
            "execute_prepared",
            "commit_prepared",
            "run_prepared",
            "apply_prepared",
            "restore_prepared",
            "write_prepared",
            "execute_persistent",
            "perform_write",
            "apply_plan_preserved",
            "restore_backup_preserved",
            "restore_baseline_preserved",
        }
        self.assertTrue(forbidden.isdisjoint(dir(ApplicationFacade)))


if __name__ == "__main__":
    unittest.main()
