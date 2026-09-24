from __future__ import annotations

import ast
import json
import struct
import tempfile
import unittest
from pathlib import Path

from g502x_onboard.application import ErrorCode, PrivacyClass
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
        first = app.probe_details(
            pid=0xC547,
            index=1,
            read_sectors=False,
            private=False,
        )
        second = app.probe_details(
            pid=0xC547,
            index=1,
            read_sectors=False,
            private=False,
        )
        self.assertFalse(first.ok)
        self.assertTrue(second.ok)
        self.assertEqual(backend.persistent_write_count, 0)

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
