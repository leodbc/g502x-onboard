from __future__ import annotations

import ast
from contextlib import contextmanager, nullcontext
import hashlib
from dataclasses import replace
import json
import struct
import tempfile
import unittest
from itertools import count
from pathlib import Path
from threading import Event, Thread
from unittest.mock import patch

from g502x_onboard.application import (
    CancellationToken,
    CompatibilityObservation,
    ErrorCode,
    PersistentOperationKind,
    PersistentPhase,
    PreparedOperation,
    PrivacyClass,
    WriteEligibility,
)
from g502x_onboard.application.backend import (
    PersistentBackendIntent,
    PersistentTargetSnapshot,
)
from g502x_onboard.application.facade import ApplicationFacade
from g502x_onboard.application.fake_backend import FakeBackend
from g502x_onboard.codec import crc16_ccitt
from g502x_onboard.constants import (
    GLOBAL_MACRO_SECTORS,
    PROGRAMMABLE_PROFILES,
    PROGRAMMABLE_SECTORS,
    SECTOR_SIZE,
)


ROOT = Path(__file__).resolve().parents[1]
_IDS = count()


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


def app_for(backend: FakeBackend) -> ApplicationFacade:
    return ApplicationFacade(
        backend,
        id_factory=lambda: f"phase3-{next(_IDS)}",
    )


class PreparedPersistentWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Path(self.temp.name) / "config.json"
        self.config.write_text(
            json.dumps(
                {
                    "format": 1,
                    "profiles": {
                        "2": {
                            "settings": {"name": "WORK"},
                            "buttons": {"G4": "copy"},
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

    def prepare_apply(self, backend=None):
        backend = backend or FakeBackend(fake_baseline())
        app = app_for(backend)
        prepared = app.prepare_apply(self.config)
        self.assertTrue(prepared.ok, prepared.error)
        return backend, app, prepared.value

    def test_prepare_contains_no_private_identity_or_raw_target(self):
        backend, _app, prepared = self.prepare_apply()
        self.assertIs(prepared.privacy, PrivacyClass.LOCAL_SENSITIVE)
        self.assertFalse(hasattr(prepared, "active_baseline_binding"))
        self.assertFalse(hasattr(prepared, "exact_unit_binding"))
        text = repr(prepared)
        self.assertNotIn(backend.active_baseline_binding, text)
        self.assertNotIn(backend.exact_unit_binding, text)
        self.assertNotIn("b'\\x", text)
        self.assertEqual(backend.persistent_write_count, 0)

    def test_wrong_confirmation_does_not_consume_preparation(self):
        backend, app, prepared = self.prepare_apply()
        refused = app.execute_prepared(prepared, "yes")
        self.assertTrue(refused.ok)
        self.assertFalse(refused.value.success)
        self.assertIs(refused.value.error_code, ErrorCode.SAFETY_REFUSAL)
        self.assertEqual(backend.persistent_write_count, 0)

        accepted = app.execute_prepared(prepared, "APPLY CONFIG")
        self.assertTrue(accepted.value.success)
        self.assertGreater(backend.persistent_write_count, 0)

    def test_fabricated_and_tampered_preparations_refuse_zero_write(self):
        backend, app, prepared = self.prepare_apply()
        fabricated = replace(
            prepared,
            preparation_id=f"fabricated-{next(_IDS)}",
        )
        result = app.execute_prepared(fabricated, "APPLY CONFIG")
        self.assertFalse(result.value.success)
        self.assertIs(result.value.error_code, ErrorCode.UNKNOWN_PREPARATION)
        self.assertEqual(backend.persistent_write_count, 0)

        for tampered in (
            replace(prepared, target_digest="0" * 64),
            replace(prepared, kind=PersistentOperationKind.RESTORE_BASELINE),
            replace(prepared, required_confirmation_phrase="RESTORE BASELINE"),
        ):
            with self.subTest(tampered=tampered.kind):
                result = app.execute_prepared(
                    tampered,
                    tampered.required_confirmation_phrase,
                )
                self.assertFalse(result.value.success)
                self.assertIs(
                    result.value.error_code,
                    ErrorCode.STALE_PREPARATION,
                )
                self.assertEqual(backend.persistent_write_count, 0)

    def test_preparation_is_single_use_after_success(self):
        backend, app, prepared = self.prepare_apply()
        first = app.execute_prepared(prepared, "APPLY CONFIG")
        self.assertTrue(first.value.success)
        writes = backend.persistent_write_count
        second = app.execute_prepared(prepared, "APPLY CONFIG")
        self.assertFalse(second.value.success)
        self.assertIs(second.value.error_code, ErrorCode.CONSUMED_PREPARATION)
        self.assertEqual(backend.persistent_write_count, writes)

    def test_zero_write_revalidation_refusal_matrix(self):
        cases = (
            {"baseline_matches": False},
            {"exact_unit_matches": False},
            {"architecture": "unknown"},
            {"transport": "untested"},
            {"stable_identity": False},
            {"firmware_supported": False},
            {"persistent_policy_authorized": False},
            {"host_guard_recheck_clear": False},
            {"recovery_ok": False},
        )
        for state in cases:
            with self.subTest(state=state):
                backend = FakeBackend(fake_baseline())
                app = app_for(backend)
                prepared = app.prepare_apply(self.config)
                self.assertTrue(prepared.ok, prepared.error)
                for key, value in state.items():
                    setattr(backend, key, value)
                result = app.execute_prepared(
                    prepared.value,
                    "APPLY CONFIG",
                )
                self.assertFalse(result.value.success)
                self.assertEqual(backend.persistent_write_count, 0)

    def test_changed_config_refuses_before_first_write(self):
        backend, app, prepared = self.prepare_apply()
        self.config.write_text(
            json.dumps(
                {
                    "format": 1,
                    "profiles": {
                        "2": {
                            "settings": {"name": "CHANGED"},
                            "buttons": {"G5": "paste"},
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        result = app.execute_prepared(prepared, "APPLY CONFIG")
        self.assertFalse(result.value.success)
        self.assertEqual(backend.persistent_write_count, 0)

    def test_backup_and_baseline_target_staleness_refuse_zero_write(self):
        baseline = fake_baseline()
        backup = dict(baseline)
        backend = FakeBackend(baseline, backup_targets={"backup-A": backup})
        app = app_for(backend)

        prepared_backup = app.prepare_restore_backup("backup-A")
        self.assertTrue(prepared_backup.ok, prepared_backup.error)
        backend.backup_targets["backup-A"] = {
            **backup,
            2: fake_sector(0x11),
        }
        result = app.execute_prepared(
            prepared_backup.value,
            "RESTORE BACKUP",
        )
        self.assertFalse(result.value.success)
        self.assertEqual(backend.persistent_write_count, 0)

        backend.backup_targets["backup-A"] = backup
        prepared_baseline = app.prepare_restore_baseline()
        self.assertTrue(prepared_baseline.ok, prepared_baseline.error)
        backend.baseline_images[2] = fake_sector(0x22)
        result = app.execute_prepared(
            prepared_baseline.value,
            "RESTORE BASELINE",
        )
        self.assertFalse(result.value.success)
        self.assertEqual(backend.persistent_write_count, 0)

    def test_cancellation_before_acceptance_does_not_consume(self):
        backend, app, prepared = self.prepare_apply()
        token = CancellationToken()
        token.cancel()
        result = app.execute_prepared(
            prepared,
            "APPLY CONFIG",
            cancellation=token,
        )
        self.assertFalse(result.value.success)
        self.assertEqual(backend.persistent_write_count, 0)
        retry = app.execute_prepared(prepared, "APPLY CONFIG")
        self.assertTrue(retry.value.success)

    def test_cancellation_during_revalidating_consumes_and_writes_zero(self):
        backend, app, prepared = self.prepare_apply()
        token = CancellationToken()

        def observer(state):
            if state.phase is PersistentPhase.REVALIDATING:
                token.cancel()

        result = app.execute_prepared(
            prepared,
            "APPLY CONFIG",
            cancellation=token,
            observer=observer,
        )
        self.assertFalse(result.value.success)
        self.assertEqual(backend.persistent_write_count, 0)
        retry = app.execute_prepared(prepared, "APPLY CONFIG")
        self.assertFalse(retry.value.success)
        self.assertIs(
            retry.value.error_code,
            ErrorCode.CONSUMED_PREPARATION,
        )

    def test_cancellation_at_armed_writes_zero(self):
        backend, app, prepared = self.prepare_apply()
        token = CancellationToken()

        def observer(state):
            if state.phase is PersistentPhase.ARMED:
                token.cancel()

        result = app.execute_prepared(
            prepared,
            "APPLY CONFIG",
            cancellation=token,
            observer=observer,
        )
        self.assertFalse(result.value.success)
        self.assertEqual(backend.persistent_write_count, 0)
        self.assertIn(PersistentPhase.ARMED, result.value.phase_trace)
        self.assertNotIn(PersistentPhase.WRITING, result.value.phase_trace)

    def test_cancellation_immediately_before_first_write_writes_zero(self):
        backend, app, prepared = self.prepare_apply()
        token = CancellationToken()
        entered = Event()
        release = Event()
        backend.block_at = "before-first-write"
        backend.block_entered = entered
        backend.block_release = release
        results = []
        worker = Thread(
            target=lambda: results.append(
                app.execute_prepared(
                    prepared,
                    "APPLY CONFIG",
                    cancellation=token,
                )
            )
        )
        worker.start()
        self.assertTrue(entered.wait(timeout=2.0))
        token.cancel()
        release.set()
        worker.join(timeout=3.0)
        self.assertFalse(worker.is_alive())
        self.assertEqual(backend.persistent_write_count, 0)
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].value.success)
        self.assertIn(PersistentPhase.ARMED, results[0].value.phase_trace)
        self.assertNotIn(PersistentPhase.WRITING, results[0].value.phase_trace)

    def test_cancellation_from_writing_onward_is_deferred(self):
        for phase in (
            PersistentPhase.WRITING,
            PersistentPhase.RECONCILING,
            PersistentPhase.POST_VALIDATING,
        ):
            with self.subTest(phase=phase):
                backend, app, prepared = self.prepare_apply()
                token = CancellationToken()

                def observer(state, phase=phase):
                    if state.phase is phase:
                        token.cancel()

                result = app.execute_prepared(
                    prepared,
                    "APPLY CONFIG",
                    cancellation=token,
                    observer=observer,
                )
                self.assertTrue(result.value.success)
                self.assertTrue(result.value.reconciliation_completed)
                self.assertTrue(result.value.post_validation_completed)

    def test_observer_failure_never_controls_transaction(self):
        backend, app, prepared = self.prepare_apply()

        def observer(_state):
            raise RuntimeError("observer failure")

        result = app.execute_prepared(
            prepared,
            "APPLY CONFIG",
            observer=observer,
        )
        self.assertTrue(result.value.success)

    def test_first_write_boundary_and_failure_classification(self):
        backend, app, prepared = self.prepare_apply()
        backend.fault_at = "first-persistent-action"
        result = app.execute_prepared(prepared, "APPLY CONFIG")
        self.assertFalse(result.value.success)
        self.assertTrue(result.value.writing_started)
        self.assertIn(PersistentPhase.WRITING, result.value.phase_trace)
        self.assertEqual(backend.persistent_write_count, 0)

    def test_failure_and_reconciliation_fault_matrix(self):
        cases = (
            ("before-writing", 0, False, False, False),
            ("after-potential-commit", 1, True, False, False),
            ("fresh-readback-indeterminate", 1, True, False, False),
            ("fresh-readback-previous", 3, True, False, False),
            ("final-reconciliation", 14, True, False, False),
            ("post-validation", 14, True, True, False),
        )
        for fault, expected_writes, writing_started, reconciled, post_validated in cases:
            with self.subTest(fault=fault):
                backend, app, prepared = self.prepare_apply()
                backend.fault_at = fault
                result = app.execute_prepared(prepared, "APPLY CONFIG")
                self.assertFalse(result.value.success)
                self.assertEqual(
                    backend.persistent_write_count,
                    expected_writes,
                )
                self.assertEqual(
                    result.value.writing_started,
                    writing_started,
                )
                self.assertEqual(
                    result.value.reconciliation_completed,
                    reconciled,
                )
                self.assertEqual(
                    result.value.post_validation_completed,
                    post_validated,
                )

    def test_reconciled_target_failure_continues_without_duplicate_transaction(self):
        backend, app, prepared = self.prepare_apply()
        backend.fault_at = "fresh-readback-target"
        result = app.execute_prepared(prepared, "APPLY CONFIG")
        self.assertTrue(result.value.success)
        self.assertEqual(backend.persistent_write_count, 14)
        self.assertEqual(
            backend.write_history.count("reconcile:target"),
            1,
        )

    def test_full_post_validation_is_required_for_success(self):
        backend, app, prepared = self.prepare_apply()
        backend.post_validation_ok = False
        result = app.execute_prepared(prepared, "APPLY CONFIG")
        self.assertFalse(result.value.success)
        self.assertTrue(result.value.writing_started)
        self.assertTrue(result.value.reconciliation_completed)
        self.assertTrue(result.value.post_validation_completed)

    def test_concurrent_duplicate_and_busy_different_preparation(self):
        backend = FakeBackend(fake_baseline())
        app = app_for(backend)
        first = app.prepare_apply(self.config).value
        second = app.prepare_apply(self.config).value
        entered = Event()
        release = Event()
        backend.block_at = "revalidating"
        backend.block_entered = entered
        backend.block_release = release
        results = []
        worker = Thread(
            target=lambda: results.append(
                app.execute_prepared(first, "APPLY CONFIG")
            )
        )
        worker.start()
        self.assertTrue(entered.wait(timeout=2.0))
        try:
            duplicate = app.execute_prepared(first, "APPLY CONFIG")
            self.assertFalse(duplicate.value.success)
            self.assertIn(
                duplicate.value.error_code,
                {ErrorCode.CONSUMED_PREPARATION, ErrorCode.BUSY},
            )
            busy = app.execute_prepared(second, "APPLY CONFIG")
            self.assertFalse(busy.value.success)
            self.assertIs(busy.value.error_code, ErrorCode.BUSY)

            refresh = app.validate_details(private=False)
            self.assertFalse(refresh.ok)
            self.assertIs(refresh.error.code, ErrorCode.BUSY)
        finally:
            release.set()
            worker.join(timeout=3.0)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].value.success)

        backend.block_at = None
        backend.block_entered = None
        backend.block_release = None
        retry = app.execute_prepared(second, "APPLY CONFIG")
        self.assertTrue(retry.value.success)

    def test_preparation_cannot_cross_facade_boundary(self):
        backend, app1, prepared = self.prepare_apply()
        app2 = app_for(backend)
        result = app2.execute_prepared(prepared, "APPLY CONFIG")
        self.assertFalse(result.value.success)
        self.assertIs(
            result.value.error_code,
            ErrorCode.UNKNOWN_PREPARATION,
        )
        self.assertEqual(backend.persistent_write_count, 0)


class RealBackendPersistentParityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.baseline = fake_baseline()
        self.manifest = {"fingerprint": "fixture"}
        self.config = Path(self.temp.name) / "config.json"
        config = {
            "format": 1,
            "profiles": {
                "2": {
                    "settings": {"name": "WORK"},
                    "buttons": {"G4": "copy"},
                }
            },
        }
        self.config.write_text(json.dumps(config), encoding="utf-8")
        from g502x_onboard.codec import build_plan, plan_json

        self.plan = build_plan(config, self.baseline)
        self.plan_digest = hashlib.sha256(
            plan_json(self.plan).encode("utf-8")
        ).hexdigest()
        self.source_digest = hashlib.sha256(
            json.dumps(
                config,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        self.compat = CompatibilityObservation(
            architecture="compatible",
            transport="tested",
            identity="unit-bound",
            write_allowed=True,
            eligibility=WriteEligibility.ELIGIBLE,
        )

    def _intent(self, kind):
        if kind is PersistentOperationKind.APPLY_CONFIG:
            return PersistentBackendIntent(
                kind=kind,
                target_digest=self.plan_digest,
                active_baseline_binding="fixture",
                exact_unit_binding="fixture",
                compatibility=self.compat,
                managed_sectors=tuple(PROGRAMMABLE_SECTORS),
                source_path=str(self.config),
                source_digest=self.source_digest,
                plan=self.plan,
                normalized_config=json.loads(
                    self.config.read_text(encoding="utf-8")
                ),
            )
        return PersistentBackendIntent(
            kind=kind,
            target_digest="restore-target",
            active_baseline_binding="fixture",
            exact_unit_binding="fixture",
            compatibility=self.compat,
            managed_sectors=tuple(PROGRAMMABLE_SECTORS),
            source_path=(
                "backup-fixture"
                if kind is PersistentOperationKind.RESTORE_BACKUP
                else None
            ),
        )

    def test_shared_real_backend_path_keeps_outer_lock_through_post_validation(self):
        import g502x_onboard.application._persistent_backend as pb

        for kind in (
            PersistentOperationKind.APPLY_CONFIG,
            PersistentOperationKind.RESTORE_BACKUP,
            PersistentOperationKind.RESTORE_BASELINE,
        ):
            with self.subTest(kind=kind):
                events = []
                phases = []
                captured = {}

                @contextmanager
                def lock(_path):
                    events.append("lock-enter")
                    try:
                        yield
                    finally:
                        events.append("lock-exit")

                class Dev:
                    def close(self):
                        events.append("device-close")

                def primitive(*args, **kwargs):
                    captured.update(kwargs)
                    events.append("primitive")
                    kwargs["before_first_write"]()
                    events.append("staging")
                    kwargs["before_final_reconcile"]()
                    events.append("final-readback")
                    return Path("safety")

                report = type(
                    "Report",
                    (),
                    {"ok": True, "enabled_profiles": (1, 2)},
                )()
                patches = [
                    patch.object(
                        pb,
                        "exclusive_operation_lock",
                        side_effect=lock,
                    ),
                    patch.object(
                        pb,
                        "require_ghub_closed",
                        side_effect=lambda: events.append("host-guard"),
                    ),
                    patch.object(
                        pb,
                        "active_baseline",
                        return_value=(self.baseline, self.manifest),
                    ),
                    patch.object(
                        pb,
                        "assert_active_device_matches_baseline",
                        return_value=self.manifest,
                    ),
                    patch.object(
                        pb,
                        "validate_recovery",
                        side_effect=lambda: events.append("recovery"),
                    ),
                    patch.object(
                        pb,
                        "connect_manifest_unit",
                        return_value=Dev(),
                    ),
                    patch.object(pb, "get_current_profile", return_value=1),
                    patch.object(
                        pb,
                        "ensure_safe_profile",
                        side_effect=lambda _m: events.append("safe-profile"),
                    ),
                    patch.object(
                        pb,
                        "_load_target_locked",
                        return_value=PersistentTargetSnapshot(
                            source_path="backup-fixture",
                            source_name="backup-fixture",
                            target_digest="restore-target",
                        ),
                    ),
                    patch.object(pb, "apply_plan", side_effect=primitive),
                    patch.object(pb, "restore_backup", side_effect=primitive),
                    patch.object(pb, "restore_baseline", side_effect=primitive),
                    patch.object(
                        pb,
                        "validate_device",
                        side_effect=lambda: (
                            events.append("post-validate")
                            or (self.baseline, report)
                        ),
                    ),
                    patch.object(
                        pb,
                        "private_write_text",
                        side_effect=lambda *a, **k: events.append(
                            "private-record"
                        ),
                    ),
                ]
                with patches[0], patches[1], patches[2], patches[3], \
                     patches[4], patches[5], patches[6], patches[7], \
                     patches[8], patches[9], patches[10], patches[11], \
                     patches[12], patches[13]:
                    result = pb.execute_real_persistent(
                        self._intent(kind),
                        CancellationToken(),
                        lambda phase: phases.append(phase),
                    )

                self.assertTrue(result.reconciliation_completed)
                self.assertTrue(result.post_validation_completed)
                self.assertEqual(events[0], "lock-enter")
                self.assertEqual(events[-1], "lock-exit")
                self.assertLess(
                    events.index("primitive"),
                    events.index("post-validate"),
                )
                self.assertIn(PersistentPhase.ARMED, phases)
                self.assertIn(PersistentPhase.WRITING, phases)
                self.assertIn(PersistentPhase.RECONCILING, phases)
                self.assertIn(PersistentPhase.POST_VALIDATING, phases)
                self.assertEqual(
                    captured["expected_baseline_fingerprint"],
                    "fixture",
                )
                if kind is not PersistentOperationKind.APPLY_CONFIG:
                    self.assertEqual(
                        captured["expected_target_digest"],
                        "restore-target",
                    )


class Phase3ArchitectureTests(unittest.TestCase):
    def test_cli_has_no_direct_persistent_authority(self):
        source = (ROOT / "g502x_onboard" / "cli.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        direct_device_imports = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module == "device"
        ]
        self.assertEqual(direct_device_imports, [])
        direct_calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
        }
        self.assertTrue(
            {
                "apply_plan",
                "restore_backup",
                "restore_baseline",
                "exclusive_operation_lock",
                "validate_device",
                "RealBackend",
            }.isdisjoint(direct_calls)
        )
        self.assertNotIn("exclusive_operation_lock", source)
        self.assertNotIn("validate_device", source)
        self.assertNotIn("RealBackend", source)

    def test_one_public_persistent_executor_no_operation_specific_bypass(self):
        self.assertTrue(callable(getattr(ApplicationFacade, "execute_prepared")))
        for name in (
            "apply_now",
            "restore_now",
            "restore_baseline_now",
            "write_plan",
            "execute_backup_restore_direct",
            "apply_prepared",
            "restore_prepared",
            "execute_persistent",
        ):
            self.assertFalse(hasattr(ApplicationFacade, name))

    def test_all_three_persistent_commands_use_execute_prepared(self):
        source = (ROOT / "g502x_onboard" / "cli.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for name in ("cmd_apply", "cmd_restore", "cmd_baseline_restore"):
            node = next(
                n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == name
            )
            attrs = {
                child.func.attr
                for child in ast.walk(node)
                if isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
            }
            self.assertIn("execute_prepared", attrs)


class DeviceOrderingRegressionTests(unittest.TestCase):
    def setUp(self):
        self.baseline = fake_baseline()
        self.manifest = {"fingerprint": "fixture"}
        self.dev = type(
            "Dev",
            (),
            {"close": lambda self: None},
        )()

    def _patch_common(self, writes):
        return (
            patch("g502x_onboard.device.require_ghub_closed"),
            patch("g502x_onboard.device.require_baseline"),
            patch(
                "g502x_onboard.device.assert_active_device_matches_baseline",
                return_value=self.manifest,
            ),
            patch(
                "g502x_onboard.device.read_all",
                return_value=self.baseline,
            ),
            patch("g502x_onboard.device.validate_recovery_images"),
            patch(
                "g502x_onboard.device.connect_manifest_unit",
                return_value=self.dev,
            ),
            patch(
                "g502x_onboard.device.get_current_profile",
                return_value=1,
            ),
            patch(
                "g502x_onboard.device.create_backup",
                return_value=Path("safety"),
            ),
            patch(
                "g502x_onboard.device.baseline_map",
                return_value=self.baseline,
            ),
            patch(
                "g502x_onboard.device.build_directory",
                side_effect=lambda base, enabled: base,
            ),
            patch("g502x_onboard.device._validate_recovery_fresh"),
            patch(
                "g502x_onboard.device.fresh_write_sector",
                side_effect=lambda sector, data, **kwargs: writes.append(sector),
            ),
        )

    def test_apply_write_order_unchanged_and_hooks_boundaries(self):
        from g502x_onboard.device import apply_plan

        writes = []
        events = []
        plan = {
            "directory": self.baseline[0],
            "global_store": {
                "sector_images": {
                    s: self.baseline[s] for s in GLOBAL_MACRO_SECTORS
                }
            },
            "profiles": {
                p: {"profile": self.baseline[p]}
                for p in PROGRAMMABLE_PROFILES
            },
        }
        patches = self._patch_common(writes)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patches[5], patches[6], patches[7], patches[8], patches[9], \
             patches[10], patches[11]:
            apply_plan(
                plan,
                before_first_write=lambda: events.append("writing"),
                before_final_reconcile=lambda: events.append("reconciling"),
            )
        self.assertEqual(
            writes,
            [0, *GLOBAL_MACRO_SECTORS, 3, 4, 5, 2, 0],
        )
        self.assertEqual(events, ["writing", "reconciling"])

    def test_restore_orders_remain_identical_to_legacy(self):
        from g502x_onboard.device import restore_backup, restore_baseline

        expected = [0, *GLOBAL_MACRO_SECTORS, 3, 4, 5, 2, 0]
        for kind in ("backup", "baseline"):
            with self.subTest(kind=kind):
                writes = []
                patches = self._patch_common(writes)
                extra = [
                    patch(
                        "g502x_onboard.device.load_backup",
                        return_value=(Path("backup"), self.baseline, {}),
                    ),
                    patch(
                        "g502x_onboard.validator.validate_images",
                        return_value=type("Report", (), {"ok": True, "errors": ()})(),
                    ),
                    patch("g502x_onboard.device.validate_directory"),
                    patch("g502x_onboard.device.ensure_safe_profile"),
                ]
                with patches[0], patches[1], patches[2], patches[3], patches[4], \
                     patches[5], patches[6], patches[7], patches[8], patches[9], \
                     patches[10], patches[11], extra[0], extra[1], extra[2], extra[3]:
                    if kind == "backup":
                        restore_backup("backup")
                    else:
                        restore_baseline()
                self.assertEqual(writes, expected)


if __name__ == "__main__":
    unittest.main()
