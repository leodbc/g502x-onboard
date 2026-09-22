from __future__ import annotations

import ast
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from g502x_onboard.baseline import (
    _load_baseline_for_fingerprint,
    DEFAULT_INDEX,
    DEFAULT_PID,
    apply_identity_policy,
    assert_public_report_safe,
    baseline_sector_capture_ok,
    baseline_sector_health,
    compatibility_class,
    fingerprint,
    manifest_fingerprint_matches,
    normalize_fingerprint,
    public_manifest,
    probe_read_policy,
    public_probe_report,
    require_complete_sector_hashes,
    require_manifest_fingerprint,
)
from g502x_onboard.codec import (
    macro_page_health,
    profile3_offset,
    sector_crc_ok,
    sector_erased,
    sector_health,
    with_crc,
)
from g502x_onboard.constants import PROFILE_DIRECTORY_ENABLE_OFFSETS, SECTOR_SIZE
from g502x_onboard.validator import public_state_summary, validate_images
from g502x_onboard.host_guard import windows_process_names_matching
from g502x_onboard.private_io import (
    atomic_local_write_text,
    private_commit_dir,
    private_discard_dir,
    private_mkdir,
    private_replace_dir,
    private_stage_dir,
    exclusive_operation_lock,
    normalize_private_component,
    private_write_bytes,
    private_write_text,
    require_private_directory,
    require_private_regular_file,
)


GOOD_DESCRIPTOR = {
    "memory_model": 1,
    "profile_format": 3,
    "macro_format": 1,
    "profile_count": 5,
    "factory_profiles": 2,
    "button_count": 11,
    "sector_count": 16,
    "sector_size": 255,
    "mechanical_layout": 10,
}


GOOD_DEVICE_INFO = {
    "device_name": "G502 X LIGHTSPEED",
    "protocol": "4.2 hid++ 2.0",
    "unit_id": "A1B2C3D4",
    "model_ids": ["409F", "C098", "0000"],
    "firmware": [
        {
            "index": 1,
            "entity_type": 0,
            "prefix": "MPM",
            "version_raw": "30.00.B0014",
            "active": True,
            "transport_pid": "409F",
        }
    ],
}


class ProbeCliPrivacyTests(unittest.TestCase):
    def test_probe_identity_is_redacted_by_default(self):
        from g502x_onboard.cli import build_parser

        args = build_parser().parse_args(["probe"])
        self.assertFalse(args.private)
        self.assertFalse(args.json)

    def test_probe_private_is_explicit_opt_in(self):
        from g502x_onboard.cli import build_parser

        args = build_parser().parse_args(["probe", "--private"])
        self.assertTrue(args.private)
        self.assertFalse(args.json)


    def test_baseline_admin_identity_is_redacted_by_default(self):
        from g502x_onboard.cli import build_parser

        boot = build_parser().parse_args(["setup"])
        listing = build_parser().parse_args(["baseline", "list"])
        use = build_parser().parse_args(["baseline", "use", "a" * 24])
        self.assertFalse(boot.private)
        self.assertFalse(listing.private)
        self.assertFalse(use.private)

    def test_baseline_private_output_requires_explicit_opt_in(self):
        from g502x_onboard.cli import build_parser

        boot = build_parser().parse_args(["setup", "--private"])
        listing = build_parser().parse_args(["baseline", "list", "--private"])
        use = build_parser().parse_args(
            ["baseline", "use", "a" * 24, "--private"]
        )
        self.assertTrue(boot.private)
        self.assertTrue(listing.private)
        self.assertTrue(use.private)


    def test_diagnostic_semantics_are_private_opt_in(self):
        from g502x_onboard.cli import build_parser

        parser = build_parser()
        for argv in (
            ["validate", "--json"],
            ["status"],
            ["status", "--json"],
            ["inspect"],
            ["inspect", "--json"],
        ):
            with self.subTest(argv=argv):
                args = parser.parse_args(argv)
                self.assertFalse(args.private)

        for argv in (
            ["validate", "--json", "--private"],
            ["status", "--private"],
            ["inspect", "--private"],
        ):
            with self.subTest(argv=argv):
                args = parser.parse_args(argv)
                self.assertTrue(args.private)


    def test_export_state_defaults_to_private_home(self):
        from g502x_onboard.cli import build_parser

        args = build_parser().parse_args(["debug", "export"])
        self.assertIsNone(args.path)
        self.assertFalse(args.raw)
        self.assertFalse(args.allow_repo_output)

    def test_export_state_repo_output_requires_explicit_opt_in(self):
        from g502x_onboard.cli import build_parser

        args = build_parser().parse_args(
            ["debug", "export", "debug.json", "--allow-repo-output"]
        )
        self.assertEqual(args.path, "debug.json")
        self.assertTrue(args.allow_repo_output)

    def test_readonly_smoke_reaches_operation_lock_without_device_validator_import(self):
        from g502x_onboard.cli import cmd_smoke_readonly

        class StopBeforeHardware(Exception):
            pass

        def stop_before_hardware(_lock_path):
            raise StopBeforeHardware

        fake_device = ModuleType("g502x_onboard.device")
        for name in (
            "assert_active_device_matches_baseline",
            "create_backup",
            "load_backup",
            "probe_device",
            "require_ghub_closed",
        ):
            setattr(fake_device, name, lambda *args, **kwargs: None)

        args = SimpleNamespace(
            report="device-report-smoke.json",
            label="release-smoke",
        )
        with patch.dict(sys.modules, {"g502x_onboard.device": fake_device}):
            with patch(
                "g502x_onboard.cli.exclusive_operation_lock",
                side_effect=stop_before_hardware,
            ):
                with self.assertRaises(StopBeforeHardware):
                    cmd_smoke_readonly(args)

class DeviceProbeDependencyTests(unittest.TestCase):
    def test_device_probe_imports_probe_read_policy_from_baseline(self):
        device_path = Path(__file__).resolve().parents[1] / "g502x_onboard" / "device.py"
        tree = ast.parse(device_path.read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.level == 1
            and node.module == "baseline"
            for alias in node.names
        }
        self.assertIn("probe_read_policy", imported)



class HostProcessGuardTests(unittest.TestCase):
    def test_non_windows_does_not_call_tasklist(self):
        called = False

        def run(*_args, **_kwargs):
            nonlocal called
            called = True
            raise AssertionError("tasklist must not run off Windows")

        self.assertEqual(
            windows_process_names_matching(
                "lghub",
                platform="linux",
                run=run,
            ),
            [],
        )
        self.assertFalse(called)

    def test_windows_detects_all_matching_image_names(self):
        output = (
            '"LGHUB.exe","100","Console","1","10,000 K"\n'
            '"lghub_agent.exe","101","Console","1","10,000 K"\n'
            '"lghub_system_tray.exe","102","Console","1","10,000 K"\n'
            '"explorer.exe","103","Console","1","10,000 K"\n'
        )

        result = windows_process_names_matching(
            "lghub",
            platform="win32",
            run=lambda *_args, **_kwargs: SimpleNamespace(
                returncode=0,
                stdout=output,
                stderr="",
            ),
        )
        self.assertEqual(
            result,
            [
                "LGHUB.exe",
                "lghub_agent.exe",
                "lghub_system_tray.exe",
            ],
        )

    def test_windows_detects_onboard_memory_manager_name_variants(self):
        output = (
            '"OnboardMemoryManager.exe","200","Console","1","8,000 K"\n'
            '"Onboard Memory Manager.exe","201","Console","1","8,000 K"\n'
            '"explorer.exe","202","Console","1","10,000 K"\n'
        )

        result = windows_process_names_matching(
            "onboardmemorymanager",
            platform="win32",
            run=lambda *_args, **_kwargs: SimpleNamespace(
                returncode=0,
                stdout=output,
                stderr="",
            ),
        )
        self.assertEqual(
            result,
            [
                "Onboard Memory Manager.exe",
                "OnboardMemoryManager.exe",
            ],
        )

    def test_windows_tasklist_nonzero_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "exited with code 5"):
            windows_process_names_matching(
                "lghub",
                platform="win32",
                run=lambda *_args, **_kwargs: SimpleNamespace(
                    returncode=5,
                    stdout="",
                    stderr="Access is denied.",
                ),
            )

    def test_windows_tasklist_exception_fails_closed(self):
        def run(*_args, **_kwargs):
            raise OSError("tasklist unavailable")

        with self.assertRaisesRegex(RuntimeError, "execution failed"):
            windows_process_names_matching(
                "lghub",
                platform="win32",
                run=run,
            )

    def test_windows_non_text_output_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "output is not text"):
            windows_process_names_matching(
                "lghub",
                platform="win32",
                run=lambda *_args, **_kwargs: SimpleNamespace(
                    returncode=0,
                    stdout=b"binary",
                    stderr="",
                ),
            )


class OperationLockTests(unittest.TestCase):
    def test_operation_lock_rejects_second_holder(self):
        with tempfile.TemporaryDirectory() as td:
            lock = Path(td) / "operation.lock"
            with exclusive_operation_lock(lock):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "another g502x hardware operation",
                ):
                    with exclusive_operation_lock(lock):
                        pass

            # A released/stale lock file is harmless and may be acquired again.
            with exclusive_operation_lock(lock):
                self.assertTrue(lock.exists())
                if os.name == "posix":
                    self.assertEqual(lock.stat().st_mode & 0o777, 0o600)


class PrivateComponentTests(unittest.TestCase):
    def test_private_component_accepts_simple_checkpoint_labels(self):
        for value in ("backup", "pre-apply", "release_test", "label1.1"):
            self.assertEqual(
                normalize_private_component(value),
                value,
            )

    def test_private_component_rejects_path_escape(self):
        for value in (
            "../outside",
            "..\\outside",
            "/absolute",
            ".hidden",
            "a/b",
            "a\\b",
            "",
            " " * 3,
            "a" * 65,
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_private_component(
                        value,
                        context="checkpoint label",
                    )


class AtomicLocalOutputTests(unittest.TestCase):
    def test_atomic_local_write_replaces_complete_file(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "report.json"
            path.write_text("old\n", encoding="utf-8")
            atomic_local_write_text(path, "new\n")
            self.assertEqual(path.read_text(encoding="utf-8"), "new\n")
            if os.name == "posix":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_atomic_local_write_rejects_symlink_destination(self):
        if os.name == "nt":
            self.skipTest("Windows symlink privilege/semantics vary")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target.txt"
            target.write_text("keep\n", encoding="utf-8")
            link = root / "report.json"
            link.symlink_to(target)
            with self.assertRaisesRegex(RuntimeError, "symbolic links"):
                atomic_local_write_text(link, "replace\n")
            self.assertEqual(target.read_text(encoding="utf-8"), "keep\n")


class PrivateLocalStateTests(unittest.TestCase):
    def test_private_state_loader_rejects_symlinks(self):
        if os.name == "nt":
            self.skipTest("symlink privilege/semantics vary on Windows")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            real_file = root / "real.bin"
            real_file.write_bytes(b"secret")
            link_file = root / "link.bin"
            link_file.symlink_to(real_file)
            with self.assertRaisesRegex(RuntimeError, "symbolic links"):
                require_private_regular_file(
                    link_file,
                    context="test file",
                )

            real_dir = root / "real-dir"
            real_dir.mkdir()
            link_dir = root / "link-dir"
            link_dir.symlink_to(real_dir, target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, "symbolic links"):
                require_private_directory(
                    link_dir,
                    context="test dir",
                )

    def test_private_io_round_trip_and_posix_modes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "state"
            private_mkdir(root)
            raw = private_write_bytes(root / "sector.bin", b"secret")
            text_file = private_write_text(root / "manifest.json", '{"secret": true}\n')

            self.assertEqual(raw.read_bytes(), b"secret")
            self.assertEqual(text_file.read_text(encoding="utf-8"), '{"secret": true}\n')

            if os.name == "posix":
                self.assertEqual(root.stat().st_mode & 0o777, 0o700)
                self.assertEqual(raw.stat().st_mode & 0o777, 0o600)
                self.assertEqual(text_file.stat().st_mode & 0o777, 0o600)


    def test_private_directory_is_not_published_until_commit(self):
        with tempfile.TemporaryDirectory() as td:
            parent = Path(td) / "checkpoints"
            private_mkdir(parent)
            target = parent / "backup-1"
            stage = private_stage_dir(parent, prefix=".partial-test-")
            private_write_bytes(stage / "sector-00.bin", b"abc")
            private_write_text(stage / "manifest.json", "{}\n")

            self.assertFalse(target.exists())
            committed = private_commit_dir(stage, target)
            self.assertEqual(committed, target)
            self.assertFalse(stage.exists())
            self.assertEqual((target / "sector-00.bin").read_bytes(), b"abc")

    def test_private_staging_can_be_discarded_without_visible_checkpoint(self):
        with tempfile.TemporaryDirectory() as td:
            parent = Path(td) / "checkpoints"
            private_mkdir(parent)
            stage = private_stage_dir(parent, prefix=".partial-test-")
            private_write_bytes(stage / "sector-00.bin", b"partial")
            private_discard_dir(stage)
            self.assertFalse(stage.exists())
            self.assertEqual(list(parent.iterdir()), [])



    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_private_discard_unlinks_symlink_without_following_target(self):
        if os.name == "nt":
            self.skipTest("Windows symlink privilege/semantics vary")
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            target = base / "target"
            target.mkdir()
            marker = target / "keep.txt"
            marker.write_text("keep\n", encoding="utf-8")
            link = base / "staging-link"
            link.symlink_to(target, target_is_directory=True)

            private_discard_dir(link)

            self.assertFalse(link.exists())
            self.assertTrue(marker.exists())
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep\n")

    def test_private_replace_rolls_forward_complete_directory(self):
        with tempfile.TemporaryDirectory() as td:
            parent = Path(td) / "baselines"
            private_mkdir(parent)
            target = private_mkdir(parent / "target")
            private_write_text(target / "manifest.json", "old\n")
            stage = private_stage_dir(parent, prefix=".partial-test-")
            private_write_text(stage / "manifest.json", "new\n")

            private_replace_dir(stage, target)
            self.assertEqual((target / "manifest.json").read_text(), "new\n")
            self.assertFalse(stage.exists())
            self.assertEqual(
                [p for p in parent.iterdir() if p.name.startswith(".previous-")],
                [],
            )


class PreviousBaselineRecoveryTests(unittest.TestCase):
    def _candidate(self, root: Path, fingerprint_value: str) -> Path:
        candidate = root / f".previous-{fingerprint_value}-fixture"
        candidate.mkdir()
        sectors = {}
        for sector in range(16):
            data = bytes([sector]) * SECTOR_SIZE
            (candidate / f"sector-{sector:02d}.bin").write_bytes(data)
            sectors[str(sector)] = {
                "sha256": __import__("hashlib").sha256(data).hexdigest()
            }

        manifest = {
            "format": "g502x-device-baseline-v1",
            "fingerprint": fingerprint_value,
            "sectors": sectors,
        }
        (candidate / "manifest.json").write_text(
            json.dumps(manifest, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return candidate

    def test_integral_previous_baseline_validates_before_rename(self):
        fp = "a" * 24
        with tempfile.TemporaryDirectory() as td:
            candidate = self._candidate(Path(td), fp)
            images, manifest = _load_baseline_for_fingerprint(
                candidate,
                fp,
                context="previous baseline",
            )
            self.assertEqual(len(images), 16)
            self.assertEqual(manifest["fingerprint"], fp)

    def test_previous_baseline_hash_mismatch_fails(self):
        fp = "b" * 24
        with tempfile.TemporaryDirectory() as td:
            candidate = self._candidate(Path(td), fp)
            (candidate / "sector-07.bin").write_bytes(
                b"tampered" + b"\x00" * (SECTOR_SIZE - len("tampered"))
            )
            with self.assertRaisesRegex(Exception, "sector 7 hash"):
                _load_baseline_for_fingerprint(
                    candidate,
                    fp,
                    context="previous baseline",
                )

    def test_previous_baseline_fingerprint_mismatch_fails(self):
        fp = "c" * 24
        with tempfile.TemporaryDirectory() as td:
            candidate = self._candidate(Path(td), fp)
            with self.assertRaisesRegex(Exception, "fingerprint"):
                _load_baseline_for_fingerprint(
                    candidate,
                    "d" * 24,
                    context="previous baseline",
                )


class ManifestIntegrityTests(unittest.TestCase):
    def test_local_fingerprint_format_blocks_path_escape(self):
        self.assertEqual(
            normalize_fingerprint("AABBCCDDEEFF001122334455"),
            "aabbccddeeff001122334455",
        )
        for value in (
            "../outside",
            "..\\outside",
            "abc",
            "g" * 24,
            "a" * 23,
            "a" * 25,
            "",
            None,
        ):
            with self.subTest(value=value):
                with self.assertRaises(Exception):
                    normalize_fingerprint(value)

    def _manifest(self):
        return {
            "sectors": {
                str(i): {"sha256": "a" * 64}
                for i in range(16)
            }
        }

    def test_sector_hash_table_must_be_complete(self):
        manifest = self._manifest()
        require_complete_sector_hashes(
            manifest,
            context="test",
        )
        del manifest["sectors"]["7"]
        with self.assertRaisesRegex(Exception, "incomplete"):
            require_complete_sector_hashes(
                manifest,
                context="test",
            )

    def test_sector_hash_must_be_real_sha256_shape(self):
        manifest = self._manifest()
        manifest["sectors"]["3"]["sha256"] = "not-a-hash"
        with self.assertRaisesRegex(Exception, "valid SHA-256"):
            require_complete_sector_hashes(
                manifest,
                context="test",
            )

    def test_fingerprint_is_required_and_exact(self):
        manifest = {}
        with self.assertRaisesRegex(Exception, "missing"):
            require_manifest_fingerprint(
                manifest,
                key="baseline_fingerprint",
                expected="abc",
                context="backup manifest",
            )
        manifest["baseline_fingerprint"] = "other"
        with self.assertRaisesRegex(Exception, "does not match"):
            require_manifest_fingerprint(
                manifest,
                key="baseline_fingerprint",
                expected="abc",
                context="backup manifest",
            )
        manifest["baseline_fingerprint"] = "abc"
        self.assertEqual(
            require_manifest_fingerprint(
                manifest,
                key="baseline_fingerprint",
                expected="abc",
                context="backup manifest",
            ),
            "abc",
        )


class SectorHealthTests(unittest.TestCase):
    def test_erased_post_profile_page_is_stable_but_not_crc_valid(self):
        erased = b"\xFF" * SECTOR_SIZE
        self.assertTrue(sector_erased(erased))
        self.assertEqual(sector_health(erased), "erased")
        self.assertTrue(baseline_sector_capture_ok(7, erased))
        self.assertTrue(baseline_sector_capture_ok(8, erased))
        self.assertFalse(baseline_sector_capture_ok(1, erased))

    def test_non_crc_post_profile_page_is_role_aware_raw_storage(self):
        raw = bytearray(b"\xFF" * SECTOR_SIZE)
        raw[0:7] = bytes.fromhex("43 00 04 44 00 04 FF")
        raw = bytes(raw)
        self.assertEqual(sector_health(raw), "invalid")
        self.assertEqual(macro_page_health(raw), "raw_no_crc")
        self.assertEqual(baseline_sector_health(8, raw), "raw_no_crc")
        self.assertTrue(baseline_sector_capture_ok(7, raw))
        self.assertTrue(baseline_sector_capture_ok(8, raw))
        self.assertFalse(baseline_sector_capture_ok(1, raw))

    def test_crc_materialized_page_is_crc_valid(self):
        page = with_crc(b"\xFF" * SECTOR_SIZE)
        self.assertEqual(sector_health(page), "crc_valid")
        self.assertTrue(baseline_sector_capture_ok(1, page))
        self.assertTrue(baseline_sector_capture_ok(8, page))


class ExternalMacroCompatibilityTests(unittest.TestCase):
    @staticmethod
    def fixture():
        images = {
            sector: with_crc(b"\xFF" * SECTOR_SIZE)
            for sector in range(16)
        }

        directory = bytearray(images[0])
        for profile, offset in PROFILE_DIRECTORY_ENABLE_OFFSETS.items():
            directory[offset] = 1 if profile in (1, 2) else 0
        images[0] = with_crc(bytes(directory))

        p2 = bytearray(images[2])
        p2[0] = 0x01
        p2[1] = 0
        p2[2] = 0
        p2[profile3_offset("NORMAL", "G4"):profile3_offset("NORMAL", "G4") + 4] = (
            bytes.fromhex("00 08 00 00")
        )
        images[2] = with_crc(bytes(p2))
        return images

    def test_referenced_raw_no_crc_macro_page_validates(self):
        images = self.fixture()
        raw = bytearray(b"\xFF" * SECTOR_SIZE)
        raw[0:7] = bytes.fromhex("43 00 04 44 00 04 FF")
        images[8] = bytes(raw)
        baseline = dict(images)

        report = validate_images(images, baseline=baseline)
        self.assertTrue(report.ok, report.errors)
        self.assertIn((8, 0), report.macro_chains)
        self.assertFalse(sector_crc_ok(images[8]))
        self.assertTrue(any("raw page" in w for w in report.warnings))

    def test_external_jump_may_read_protected_sector_without_allocating_it(self):
        images = self.fixture()
        page8 = bytearray(b"\xFF" * SECTOR_SIZE)
        page8[0:11] = bytes.fromhex(
            "43 00 04 44 00 04 60 00 06 00 00"
        )
        page6 = bytearray(b"\xFF" * SECTOR_SIZE)
        page6[0:7] = bytes.fromhex("43 00 05 44 00 05 FF")
        images[8] = bytes(page8)
        images[6] = bytes(page6)
        baseline = dict(images)

        report = validate_images(images, baseline=baseline)
        self.assertTrue(report.ok, report.errors)
        chain = report.macro_chains[(8, 0)]
        self.assertTrue(any(ins.sector == 6 for ins in chain))


class ProbeReadPolicyTests(unittest.TestCase):
    def test_unknown_architecture_stays_descriptor_only(self):
        compat = {
            "architecture": "unknown",
            "transport": "untested",
            "write_allowed": False,
        }
        policy = probe_read_policy(compat, read_sectors=True)
        self.assertEqual(
            policy["live_sectors"],
            "skipped_unknown_architecture",
        )
        self.assertEqual(
            policy["oob"],
            "skipped_unknown_architecture",
        )

    def test_known_architecture_may_deep_read_even_on_untested_transport(self):
        compat = {
            "architecture": "compatible",
            "transport": "untested",
            "write_allowed": False,
        }
        policy = probe_read_policy(compat, read_sectors=True)
        self.assertEqual(policy["live_sectors"], "requested_known_geometry")
        self.assertEqual(policy["oob"], "requested_known_geometry")

    def test_no_sector_flag_is_respected_on_known_geometry(self):
        compat = {
            "architecture": "compatible",
            "transport": "tested",
            "write_allowed": True,
        }
        policy = probe_read_policy(compat, read_sectors=False)
        self.assertEqual(policy["live_sectors"], "skipped_by_request")
        self.assertEqual(policy["oob"], "requested_known_geometry")


class CompatibilityTests(unittest.TestCase):
    def test_validated_receiver_transport_can_write(self):
        result = compatibility_class(
            GOOD_DESCRIPTOR,
            DEFAULT_PID,
            DEFAULT_INDEX,
        )
        self.assertEqual(result["architecture"], "compatible")
        self.assertEqual(result["transport"], "tested")
        self.assertTrue(result["write_allowed"])

    def test_same_architecture_unknown_transport_is_read_only(self):
        result = compatibility_class(
            GOOD_DESCRIPTOR,
            0xC098,
            0xFF,
        )
        self.assertEqual(result["architecture"], "compatible")
        self.assertEqual(result["transport"], "untested")
        self.assertFalse(result["write_allowed"])

    def test_descriptor_mismatch_is_unknown(self):
        descriptor = dict(GOOD_DESCRIPTOR)
        descriptor["profile_format"] = 4
        result = compatibility_class(
            descriptor,
            DEFAULT_PID,
            DEFAULT_INDEX,
        )
        self.assertEqual(result["architecture"], "unknown")
        self.assertFalse(result["write_allowed"])
        self.assertIn("profile_format", result["mismatches"])


    def test_manifest_fingerprint_match_rejects_same_geometry_other_unit(self):
        device_a = {
            "device_name": "G502 X LIGHTSPEED",
            "protocol": "4.5",
            "unit_id": "A1B2C3D4",
            "model_ids": ["C547"],
            "firmware": [],
        }
        manifest = {
            "transport": {"pid": DEFAULT_PID, "index": DEFAULT_INDEX},
            "fingerprint": fingerprint(
                descriptor=GOOD_DESCRIPTOR,
                device_info=device_a,
                pid=DEFAULT_PID,
                index=DEFAULT_INDEX,
            ),
        }
        self.assertTrue(
            manifest_fingerprint_matches(
                manifest,
                descriptor=GOOD_DESCRIPTOR,
                device_info=device_a,
            )
        )
        device_b = dict(device_a)
        device_b["unit_id"] = "11223344"
        self.assertFalse(
            manifest_fingerprint_matches(
                manifest,
                descriptor=GOOD_DESCRIPTOR,
                device_info=device_b,
            )
        )

    def test_write_policy_requires_stable_unit_identity(self):
        compat = compatibility_class(
            GOOD_DESCRIPTOR,
            DEFAULT_PID,
            DEFAULT_INDEX,
        )
        info = dict(GOOD_DEVICE_INFO)
        info["unit_id"] = None
        result = apply_identity_policy(compat, info)
        self.assertEqual(result["identity"], "insufficient")
        self.assertFalse(result["write_allowed"])

    def test_write_policy_accepts_stable_unit_identity(self):
        compat = compatibility_class(
            GOOD_DESCRIPTOR,
            DEFAULT_PID,
            DEFAULT_INDEX,
        )
        result = apply_identity_policy(
            compat,
            GOOD_DEVICE_INFO,
        )
        self.assertEqual(result["identity"], "unit-bound")
        self.assertTrue(result["write_allowed"])

    def test_write_policy_requires_validated_device_name(self):
        compat = compatibility_class(
            GOOD_DESCRIPTOR,
            DEFAULT_PID,
            DEFAULT_INDEX,
        )
        info = dict(GOOD_DEVICE_INFO)
        info["device_name"] = "G502 X PLUS"
        result = apply_identity_policy(compat, info)
        self.assertEqual(result["device_target"], "unvalidated")
        self.assertFalse(result["write_allowed"])

    def test_write_policy_requires_validated_model_id(self):
        compat = compatibility_class(
            GOOD_DESCRIPTOR,
            DEFAULT_PID,
            DEFAULT_INDEX,
        )
        info = dict(GOOD_DEVICE_INFO)
        info["model_ids"] = ["DEAD", "BEEF"]
        result = apply_identity_policy(compat, info)
        self.assertEqual(result["device_target"], "unvalidated")
        self.assertFalse(result["write_allowed"])

    def test_new_firmware_is_read_only_until_promoted(self):
        compat = compatibility_class(
            GOOD_DESCRIPTOR,
            DEFAULT_PID,
            DEFAULT_INDEX,
        )
        info = dict(GOOD_DEVICE_INFO)
        info["firmware"] = [
            dict(GOOD_DEVICE_INFO["firmware"][0], version_raw="30.01.B0001")
        ]
        result = apply_identity_policy(compat, info)
        self.assertEqual(result["identity"], "unit-bound")
        self.assertEqual(result["device_target"], "unvalidated")
        self.assertFalse(result["write_allowed"])

    def test_factory_profile_and_mechanical_layout_are_architecture_gates(self):
        for key, value in (
            ("factory_profiles", 3),
            ("mechanical_layout", 11),
        ):
            descriptor = dict(GOOD_DESCRIPTOR)
            descriptor[key] = value
            result = compatibility_class(
                descriptor,
                DEFAULT_PID,
                DEFAULT_INDEX,
            )
            self.assertEqual(result["architecture"], "unknown", key)
            self.assertFalse(result["write_allowed"], key)

    def test_all_zero_or_ff_identity_is_not_accepted(self):
        compat = compatibility_class(
            GOOD_DESCRIPTOR,
            DEFAULT_PID,
            DEFAULT_INDEX,
        )
        for value in ("00000000", "FFFFFFFF", ""):
            info = dict(GOOD_DEVICE_INFO)
            info["unit_id"] = value
            result = apply_identity_policy(compat, info)
            self.assertFalse(result["write_allowed"], value)

    def test_public_report_linter_accepts_generated_formats(self):
        probe = {
            "tool_version": "x",
            "transport": {"pid_hex": "0xC547", "index_hex": "0x01"},
            "compatibility": {"architecture": "compatible"},
            "descriptor": GOOD_DESCRIPTOR,
            "device": {
                "device_name": "G502 X LIGHTSPEED",
                "firmware": [{"prefix": "MPM", "version_raw": "30.00.B0014"}],
            },
            "active_profile": 1,
            "read_scope": {
                "descriptor": "read",
                "device_information": "read",
                "active_profile": "read",
                "live_sectors": "read_all_16",
                "oob": "read",
            },
            "oob": {"directory": [], "pages": {}},
            "sectors": {},
        }
        public_probe = public_probe_report(probe)
        self.assertIs(assert_public_report_safe(public_probe), public_probe)

        public_community = public_manifest({
            "tool_version": "x",
            "transport": {"pid_hex": "0xC547", "index_hex": "0x01"},
            "compatibility": {"architecture": "compatible"},
            "descriptor": GOOD_DESCRIPTOR,
            "device": {"device_name": "G502 X LIGHTSPEED", "firmware": []},
            "oob": {"directory": [], "pages": {}},
            "sectors": {},
        })
        self.assertIs(
            assert_public_report_safe(public_community),
            public_community,
        )

    def test_public_report_linter_rejects_private_fields_and_hashes(self):
        base = {
            "format": "g502x-probe-report-v1",
            "tool_version": "x",
            "transport": {},
            "compatibility": {},
            "descriptor": {},
            "device": {},
            "active_profile": 1,
            "oob": {},
            "sector_health": {},
        }
        cases = [
            ("unit id", {"device": {"unit_id": "AABBCCDD"}}),
            ("fingerprint key", {"device": {"fingerprint": "secret"}}),
            ("raw sectors", {"sectors": {}}),
            ("sha key", {"device": {"sha256": "a" * 64}}),
            ("hidden hash value", {"device": {"opaque": "a" * 64}}),
            ("hidden fingerprint value", {"device": {"opaque": "a" * 24}}),
        ]
        for label, patch in cases:
            payload = json.loads(json.dumps(base))
            payload.update(patch)
            with self.subTest(label=label):
                with self.assertRaises(Exception):
                    assert_public_report_safe(payload)

    def test_public_report_linter_rejects_unknown_format_and_top_level(self):
        with self.assertRaises(Exception):
            assert_public_report_safe({"format": "g502x-private-debug-v1"})

        payload = {
            "format": "g502x-probe-report-v1",
            "tool_version": "x",
            "transport": {},
            "compatibility": {},
            "descriptor": {},
            "device": {},
            "active_profile": 1,
            "oob": {},
            "sector_health": {},
            "debug": "should not be public",
        }
        with self.assertRaisesRegex(Exception, "unexpected top-level"):
            assert_public_report_safe(payload)

    def test_public_manifest_removes_identifiers_and_stable_hashes(self):
        report = public_manifest({
            "tool_version": "x",
            "fingerprint": "secret-hash",
            "transport": {"pid": 0xC547},
            "compatibility": {"write_allowed": True},
            "descriptor": GOOD_DESCRIPTOR,
            "device": {
                "device_name": "G502 X LIGHTSPEED",
                "unit_id": "AABBCCDD",
                "serial_number": "serial",
                "firmware": [{
                    "prefix": "MPM",
                    "version_raw": "30.00.B0014",
                    "extra_version": "opaque-private-ish",
                }],
            },
            "oob": {
                "directory": [{"page": 0x0101, "enabled": 1}],
                "pages": {
                    "0x0101": {
                        "sha256": "f" * 64,
                        "crc_ok": False,
                    }
                },
            },
            "sectors": {
                "0": {
                    "crc_ok": True,
                    "health": "crc_valid",
                    "erased": False,
                    "sha256": "0123456789abcdef",
                }
            },
        })
        rendered = json.dumps(report, sort_keys=True)
        self.assertEqual(report["format"], "g502x-device-report-v1")
        for secret in (
            "secret-hash",
            "AABBCCDD",
            "serial",
            "0123456789ab",
            "opaque-private-ish",
            "f" * 32,
        ):
            self.assertNotIn(secret, rendered)
        self.assertNotIn("sha256_prefix", rendered)
        self.assertNotIn("sha256", rendered)
        self.assertEqual(report["sector_health"]["0"]["health"], "crc_valid")
        self.assertEqual(report["oob"]["page_count"], 1)

    def test_public_probe_report_is_baseline_free_and_non_linkable(self):
        probe = {
            "tool_version": "x",
            "fingerprint": "probe-secret",
            "transport": {"pid_hex": "0xC547", "index_hex": "0x01"},
            "compatibility": {"architecture": "compatible", "identity": "unit-bound"},
            "descriptor": GOOD_DESCRIPTOR,
            "device": {
                "device_name": "G502 X LIGHTSPEED",
                "unit_id": "11223344",
                "serial_number": "SERIAL",
                "firmware": [{"prefix": "MPM", "version_raw": "30.00.B0014"}],
            },
            "active_profile": 1,
            "oob": {"directory": [], "pages": {}},
            "sectors": {
                "7": {
                    "crc_ok": False,
                    "health": "erased",
                    "erased": True,
                    "sha256": "a" * 64,
                }
            },
        }
        report = public_probe_report(probe)
        rendered = json.dumps(report, sort_keys=True)
        self.assertEqual(report["format"], "g502x-probe-report-v1")
        for secret in ("probe-secret", "11223344", "SERIAL", "a" * 24):
            self.assertNotIn(secret, rendered)
        self.assertEqual(report["sector_health"]["7"]["health"], "erased")

    def test_public_state_summary_omits_profile_metadata_and_occupancy(self):
        images = ExternalMacroCompatibilityTests.fixture()
        raw = bytearray(b"\xFF" * SECTOR_SIZE)
        raw[0:7] = bytes.fromhex("43 00 04 44 00 04 FF")
        images[8] = bytes(raw)
        report = validate_images(images, baseline=images)
        self.assertTrue(report.ok, report.errors)
        public = public_state_summary(images, report, baseline=images)
        rendered = json.dumps(public, sort_keys=True)
        self.assertNotIn("metadata", rendered)
        self.assertNotIn("high_water", rendered)
        self.assertNotIn("payload_non_ff", rendered)
        self.assertIn("referenced_macro_starts", public)


if __name__ == "__main__":
    unittest.main()
