from __future__ import annotations

import gc
import json
import struct
import tempfile
import unittest
import weakref
from contextlib import contextmanager
from dataclasses import replace
from itertools import count
from pathlib import Path
from threading import Event, Thread

import g502x_onboard.application.persistent as persistent
from g502x_onboard.application import CancellationToken, ErrorCode, PersistentPhase
from g502x_onboard.application.coordinator import OperationBusyError
from g502x_onboard.application.facade import ApplicationFacade
from g502x_onboard.application.fake_backend import FakeBackend
from g502x_onboard.codec import crc16_ccitt
from g502x_onboard.constants import SECTOR_SIZE


_EXPECTED_FULL_RECORD_BOUND = 32
_EXPECTED_TOMBSTONE_BOUND = 128


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


class PreparationRegistryLifetimeTests(unittest.TestCase):
    def setUp(self):
        with persistent._REGISTRY_LOCK:
            persistent._PREPARATIONS.clear()
            persistent._TOMBSTONES.clear()
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

    def tearDown(self):
        with persistent._REGISTRY_LOCK:
            persistent._PREPARATIONS.clear()
            persistent._TOMBSTONES.clear()

    def make_app(self, backend=None, id_factory=None):
        return ApplicationFacade(
            backend or FakeBackend(fake_baseline()),
            id_factory=id_factory or (lambda: "lifetime-default"),
        )

    def registry_sizes(self):
        with persistent._REGISTRY_LOCK:
            return len(persistent._PREPARATIONS), len(persistent._TOMBSTONES)

    def consume_without_writing(self, app, backend, prepared):
        backend.host_guard_recheck_clear = False
        try:
            result = app.execute_prepared(prepared, "APPLY CONFIG")
        finally:
            backend.host_guard_recheck_clear = True
        self.assertFalse(result.value.success)
        self.assertEqual(backend.persistent_write_count, 0)
        return result

    def test_declared_registry_bounds_are_explicit(self):
        self.assertEqual(
            persistent._MAX_ACTIVE_PREPARATIONS,
            _EXPECTED_FULL_RECORD_BOUND,
        )
        self.assertEqual(
            persistent._MAX_PREPARATION_TOMBSTONES,
            _EXPECTED_TOMBSTONE_BOUND,
        )

    def test_live_facade_abandoned_preparations_are_bounded(self):
        ids = count()
        backend = FakeBackend(fake_baseline())
        app = self.make_app(
            backend,
            id_factory=lambda: f"abandoned-{next(ids)}",
        )
        for _ in range(_EXPECTED_FULL_RECORD_BOUND * 6):
            prepared = app.prepare_apply(self.config)
            self.assertTrue(prepared.ok)

        active, tombstones = self.registry_sizes()
        self.assertLessEqual(active, _EXPECTED_FULL_RECORD_BOUND)
        self.assertLessEqual(tombstones, _EXPECTED_TOMBSTONE_BOUND)
        self.assertEqual(backend.persistent_write_count, 0)

    def test_consumed_preparations_do_not_accumulate_full_records(self):
        ids = count()
        backend = FakeBackend(fake_baseline())
        app = self.make_app(
            backend,
            id_factory=lambda: f"consumed-{next(ids)}",
        )
        for _ in range(_EXPECTED_TOMBSTONE_BOUND + 40):
            prepared = app.prepare_apply(self.config)
            self.assertTrue(prepared.ok)
            result = self.consume_without_writing(app, backend, prepared.value)
            self.assertIsNotNone(result.value.error_code)

        active, tombstones = self.registry_sizes()
        self.assertLessEqual(active, _EXPECTED_FULL_RECORD_BOUND)
        self.assertLessEqual(tombstones, _EXPECTED_TOMBSTONE_BOUND)

    def test_dead_owner_full_records_are_reclaimed_synchronously(self):
        owner_refs = []
        for index in range(20):
            app = self.make_app(
                id_factory=lambda i=index: f"dead-owner-{i}",
            )
            prepared = app.prepare_apply(self.config)
            self.assertTrue(prepared.ok)
            owner_refs.append(weakref.ref(app))
            del prepared
            del app

        gc.collect()
        self.assertTrue(all(ref() is None for ref in owner_refs))

        live = self.make_app(id_factory=lambda: "cleanup-trigger")
        self.assertTrue(live.prepare_apply(self.config).ok)
        with persistent._REGISTRY_LOCK:
            self.assertFalse(
                any(record.owner() is None for record in persistent._PREPARATIONS.values())
            )
            self.assertFalse(
                any(tombstone.owner() is None for tombstone in persistent._TOMBSTONES.values())
            )

    def test_current_unconsumed_preparation_remains_executable(self):
        backend = FakeBackend(fake_baseline())
        app = self.make_app(backend, id_factory=lambda: "current")
        prepared = app.prepare_apply(self.config).value
        result = app.execute_prepared(prepared, "APPLY CONFIG")
        self.assertTrue(result.value.success)

    def test_two_live_preparations_remain_supported(self):
        ids = count()
        backend = FakeBackend(fake_baseline())
        app = self.make_app(
            backend,
            id_factory=lambda: f"two-live-{next(ids)}",
        )
        first = app.prepare_apply(self.config).value
        second = app.prepare_apply(self.config).value
        self.assertNotEqual(first.preparation_id, second.preparation_id)
        self.assertTrue(app.execute_prepared(first, "APPLY CONFIG").value.success)
        self.assertTrue(app.execute_prepared(second, "APPLY CONFIG").value.success)

    def test_wrong_confirmation_does_not_consume_or_retire(self):
        backend = FakeBackend(fake_baseline())
        app = self.make_app(backend, id_factory=lambda: "wrong-confirmation")
        prepared = app.prepare_apply(self.config).value

        refused = app.execute_prepared(prepared, "WRONG")
        self.assertFalse(refused.value.success)
        self.assertIs(refused.value.error_code, ErrorCode.SAFETY_REFUSAL)
        with persistent._REGISTRY_LOCK:
            record = persistent._PREPARATIONS[prepared.preparation_id]
            self.assertFalse(record.consumed)
            self.assertNotIn(prepared.preparation_id, persistent._TOMBSTONES)

        accepted = app.execute_prepared(prepared, "APPLY CONFIG")
        self.assertTrue(accepted.value.success)

    def test_immediate_replay_keeps_consumed_refusal(self):
        backend = FakeBackend(fake_baseline())
        app = self.make_app(backend, id_factory=lambda: "immediate-replay")
        prepared = app.prepare_apply(self.config).value
        first = self.consume_without_writing(app, backend, prepared)
        self.assertFalse(first.value.success)

        replay = app.execute_prepared(prepared, "APPLY CONFIG")
        self.assertFalse(replay.value.success)
        self.assertIs(
            replay.value.error_code,
            ErrorCode.CONSUMED_PREPARATION,
        )
        self.assertEqual(backend.persistent_write_count, 0)

    def test_historical_replay_after_tombstone_compaction_fails_closed(self):
        ids = count()
        backend = FakeBackend(fake_baseline())
        app = self.make_app(
            backend,
            id_factory=lambda: f"historical-{next(ids)}",
        )
        historical = app.prepare_apply(self.config).value
        self.consume_without_writing(app, backend, historical)

        for _ in range(_EXPECTED_TOMBSTONE_BOUND + 8):
            prepared = app.prepare_apply(self.config).value
            self.consume_without_writing(app, backend, prepared)

        with persistent._REGISTRY_LOCK:
            self.assertNotIn(historical.preparation_id, persistent._TOMBSTONES)

        replay = app.execute_prepared(historical, "APPLY CONFIG")
        self.assertFalse(replay.value.success)
        self.assertIs(
            replay.value.error_code,
            ErrorCode.UNKNOWN_PREPARATION,
        )
        self.assertEqual(backend.persistent_write_count, 0)

    def test_fabricated_id_attack_does_not_grow_bookkeeping(self):
        backend = FakeBackend(fake_baseline())
        app = self.make_app(backend, id_factory=lambda: "fabrication-anchor")
        anchor = app.prepare_apply(self.config).value
        before = self.registry_sizes()

        for index in range(500):
            fabricated = replace(
                anchor,
                preparation_id=f"fabricated-{index}",
            )
            result = app.execute_prepared(fabricated, "APPLY CONFIG")
            self.assertFalse(result.value.success)
            self.assertIs(
                result.value.error_code,
                ErrorCode.UNKNOWN_PREPARATION,
            )

        self.assertEqual(self.registry_sizes(), before)
        self.assertEqual(backend.persistent_write_count, 0)

    def test_tampered_copy_is_zero_write_and_does_not_consume_legitimate_record(self):
        backend = FakeBackend(fake_baseline())
        app = self.make_app(backend, id_factory=lambda: "tamper")
        prepared = app.prepare_apply(self.config).value
        tampered = replace(prepared, target_digest="0" * 64)

        refused = app.execute_prepared(tampered, "APPLY CONFIG")
        self.assertFalse(refused.value.success)
        self.assertIs(refused.value.error_code, ErrorCode.STALE_PREPARATION)
        self.assertEqual(backend.persistent_write_count, 0)

        legitimate = app.execute_prepared(prepared, "APPLY CONFIG")
        self.assertTrue(legitimate.value.success)

    def test_oldest_abandoned_preparation_is_retired_zero_write(self):
        ids = count()
        backend = FakeBackend(fake_baseline())
        app = self.make_app(
            backend,
            id_factory=lambda: f"retired-{next(ids)}",
        )
        first = app.prepare_apply(self.config).value
        latest = first
        for _ in range(_EXPECTED_FULL_RECORD_BOUND):
            latest = app.prepare_apply(self.config).value

        with persistent._REGISTRY_LOCK:
            self.assertNotIn(first.preparation_id, persistent._PREPARATIONS)
            self.assertIn(first.preparation_id, persistent._TOMBSTONES)

        retired = app.execute_prepared(first, "APPLY CONFIG")
        self.assertFalse(retired.value.success)
        self.assertIs(retired.value.error_code, ErrorCode.STALE_PREPARATION)
        self.assertEqual(backend.persistent_write_count, 0)

        current = app.execute_prepared(latest, "APPLY CONFIG")
        self.assertTrue(current.value.success)

    def test_repeated_factory_id_is_safely_disambiguated(self):
        backend = FakeBackend(fake_baseline())
        app = self.make_app(backend, id_factory=lambda: "repeat")
        first = app.prepare_apply(self.config).value
        second = app.prepare_apply(self.config).value

        self.assertNotEqual(first.preparation_id, second.preparation_id)
        self.assertTrue(first.preparation_id.startswith("repeat#"))
        self.assertTrue(second.preparation_id.startswith("repeat#"))

    def test_repeated_factory_after_compaction_cannot_alias_old_object(self):
        backend = FakeBackend(fake_baseline())
        app = self.make_app(backend, id_factory=lambda: "repeat-after-compaction")
        historical = app.prepare_apply(self.config).value
        self.consume_without_writing(app, backend, historical)

        latest = historical
        for _ in range(_EXPECTED_TOMBSTONE_BOUND + 8):
            latest = app.prepare_apply(self.config).value
            self.consume_without_writing(app, backend, latest)

        current = app.prepare_apply(self.config).value
        self.assertNotEqual(historical.preparation_id, current.preparation_id)
        with persistent._REGISTRY_LOCK:
            self.assertNotIn(historical.preparation_id, persistent._TOMBSTONES)

        old = app.execute_prepared(historical, "APPLY CONFIG")
        self.assertFalse(old.value.success)
        self.assertIs(old.value.error_code, ErrorCode.UNKNOWN_PREPARATION)
        self.assertEqual(backend.persistent_write_count, 0)

        current_result = app.execute_prepared(current, "APPLY CONFIG")
        self.assertTrue(current_result.value.success)

    def test_retired_record_does_not_keep_private_intent_reachable(self):
        backend = FakeBackend(fake_baseline())
        app = self.make_app(backend, id_factory=lambda: "private-intent")
        prepared = app.prepare_apply(self.config).value
        with persistent._REGISTRY_LOCK:
            intent_ref = weakref.ref(
                persistent._PREPARATIONS[prepared.preparation_id].intent
            )

        self.consume_without_writing(app, backend, prepared)
        gc.collect()

        self.assertIsNone(intent_ref())
        with persistent._REGISTRY_LOCK:
            tombstone = persistent._TOMBSTONES[prepared.preparation_id]
            self.assertFalse(hasattr(tombstone, "intent"))
            self.assertFalse(hasattr(tombstone, "public"))

    def test_tombstone_compaction_is_bounded_for_abandoned_retirement(self):
        ids = count()
        backend = FakeBackend(fake_baseline())
        app = self.make_app(
            backend,
            id_factory=lambda: f"abandoned-tombstone-{next(ids)}",
        )
        for _ in range(
            _EXPECTED_FULL_RECORD_BOUND
            + _EXPECTED_TOMBSTONE_BOUND
            + 40
        ):
            self.assertTrue(app.prepare_apply(self.config).ok)

        active, tombstones = self.registry_sizes()
        self.assertLessEqual(active, _EXPECTED_FULL_RECORD_BOUND)
        self.assertLessEqual(tombstones, _EXPECTED_TOMBSTONE_BOUND)
        self.assertEqual(backend.persistent_write_count, 0)


    def test_repeated_factory_is_unique_across_heavy_churn(self):
        backend = FakeBackend(fake_baseline())
        app = self.make_app(backend, id_factory=lambda: "repeat-heavy")
        issued = []
        for _ in range(512):
            result = app.prepare_apply(self.config)
            self.assertTrue(result.ok, result.error)
            issued.append(result.value.preparation_id)

        self.assertEqual(len(issued), len(set(issued)))
        self.assertTrue(all(value.startswith("repeat-heavy#") for value in issued))
        active, tombstones = self.registry_sizes()
        self.assertLessEqual(active, _EXPECTED_FULL_RECORD_BOUND)
        self.assertLessEqual(tombstones, _EXPECTED_TOMBSTONE_BOUND)
        self.assertEqual(backend.persistent_write_count, 0)

    def test_fabricated_execution_does_not_advance_issuance_serial(self):
        backend = FakeBackend(fake_baseline())
        app = self.make_app(backend, id_factory=lambda: "serial-probe")
        first = app.prepare_apply(self.config).value
        first_generation = int(first.preparation_id.rsplit("#", 1)[1], 16)

        for index in range(300):
            fabricated = replace(
                first,
                preparation_id=f"fabricated-serial-{index}",
            )
            result = app.execute_prepared(fabricated, "APPLY CONFIG")
            self.assertFalse(result.value.success)
            self.assertIs(result.value.error_code, ErrorCode.UNKNOWN_PREPARATION)

        second = app.prepare_apply(self.config).value
        second_generation = int(second.preparation_id.rsplit("#", 1)[1], 16)
        self.assertEqual(second_generation, first_generation + 1)
        self.assertEqual(backend.persistent_write_count, 0)

    def test_abandoned_retirement_reclaims_private_intent(self):
        backend = FakeBackend(fake_baseline())
        ids = count()
        app = self.make_app(
            backend,
            id_factory=lambda: f"abandoned-private-{next(ids)}",
        )
        historical = app.prepare_apply(self.config).value
        with persistent._REGISTRY_LOCK:
            intent_ref = weakref.ref(
                persistent._PREPARATIONS[historical.preparation_id].intent
            )

        for _ in range(_EXPECTED_FULL_RECORD_BOUND):
            self.assertTrue(app.prepare_apply(self.config).ok)

        gc.collect()
        with persistent._REGISTRY_LOCK:
            self.assertNotIn(historical.preparation_id, persistent._PREPARATIONS)
            self.assertIn(historical.preparation_id, persistent._TOMBSTONES)
        self.assertIsNone(intent_ref())
        self.assertEqual(backend.persistent_write_count, 0)

    def test_dead_owner_tombstones_are_reclaimed_synchronously(self):
        backend = FakeBackend(fake_baseline())
        app = self.make_app(backend, id_factory=lambda: "dead-tombstone")
        prepared = app.prepare_apply(self.config).value
        preparation_id = prepared.preparation_id
        self.consume_without_writing(app, backend, prepared)
        with persistent._REGISTRY_LOCK:
            self.assertIn(preparation_id, persistent._TOMBSTONES)

        owner_ref = weakref.ref(app)
        del prepared
        del app
        gc.collect()
        self.assertIsNone(owner_ref())

        live = self.make_app(id_factory=lambda: "dead-tombstone-cleanup")
        self.assertTrue(live.prepare_apply(self.config).ok)
        with persistent._REGISTRY_LOCK:
            self.assertNotIn(preparation_id, persistent._TOMBSTONES)
            self.assertFalse(
                any(t.owner() is None for t in persistent._TOMBSTONES.values())
            )

    def test_mixed_churn_stays_bounded_without_writes(self):
        backend = FakeBackend(fake_baseline())
        ids = count()
        app = self.make_app(
            backend,
            id_factory=lambda: f"mixed-{next(ids)}",
        )

        for index in range(220):
            if index % 5 == 4:
                temp_app = self.make_app(
                    backend,
                    id_factory=lambda i=index: f"mixed-dead-{i}",
                )
                temp_prepared = temp_app.prepare_apply(self.config)
                self.assertTrue(temp_prepared.ok)
                del temp_prepared
                del temp_app
                continue

            prepared = app.prepare_apply(self.config)
            self.assertTrue(prepared.ok)
            if index % 5 == 1:
                refused = app.execute_prepared(prepared.value, "WRONG")
                self.assertIs(refused.value.error_code, ErrorCode.SAFETY_REFUSAL)
            elif index % 5 == 2:
                self.consume_without_writing(app, backend, prepared.value)
            elif index % 5 == 3:
                fabricated = replace(
                    prepared.value,
                    preparation_id=f"mixed-fabricated-{index}",
                )
                refused = app.execute_prepared(fabricated, "APPLY CONFIG")
                self.assertIs(
                    refused.value.error_code,
                    ErrorCode.UNKNOWN_PREPARATION,
                )

        gc.collect()
        self.assertTrue(app.prepare_apply(self.config).ok)
        active, tombstones = self.registry_sizes()
        self.assertLessEqual(active, _EXPECTED_FULL_RECORD_BOUND)
        self.assertLessEqual(tombstones, _EXPECTED_TOMBSTONE_BOUND)
        with persistent._REGISTRY_LOCK:
            self.assertFalse(
                any(r.owner() is None for r in persistent._PREPARATIONS.values())
            )
            self.assertFalse(
                any(t.owner() is None for t in persistent._TOMBSTONES.values())
            )
        self.assertEqual(backend.persistent_write_count, 0)

    def test_cross_facade_capacity_eviction_never_crosses_owner_boundary(self):
        backend = FakeBackend(fake_baseline())
        ids_a = count()
        ids_b = count()
        app_a = self.make_app(
            backend,
            id_factory=lambda: f"owner-a-{next(ids_a)}",
        )
        app_b = self.make_app(
            backend,
            id_factory=lambda: f"owner-b-{next(ids_b)}",
        )
        historical = app_a.prepare_apply(self.config).value

        for _ in range(_EXPECTED_FULL_RECORD_BOUND):
            self.assertTrue(app_b.prepare_apply(self.config).ok)

        with persistent._REGISTRY_LOCK:
            self.assertNotIn(historical.preparation_id, persistent._PREPARATIONS)
            self.assertIn(historical.preparation_id, persistent._TOMBSTONES)

        wrong_owner = app_b.execute_prepared(historical, "APPLY CONFIG")
        self.assertIs(
            wrong_owner.value.error_code,
            ErrorCode.UNKNOWN_PREPARATION,
        )
        right_owner = app_a.execute_prepared(historical, "APPLY CONFIG")
        self.assertIs(right_owner.value.error_code, ErrorCode.STALE_PREPARATION)

        for _ in range(_EXPECTED_TOMBSTONE_BOUND + 8):
            self.assertTrue(app_b.prepare_apply(self.config).ok)

        with persistent._REGISTRY_LOCK:
            self.assertNotIn(historical.preparation_id, persistent._TOMBSTONES)
        compacted = app_a.execute_prepared(historical, "APPLY CONFIG")
        self.assertIs(
            compacted.value.error_code,
            ErrorCode.UNKNOWN_PREPARATION,
        )
        self.assertEqual(backend.persistent_write_count, 0)

    def test_executing_record_is_protected_from_capacity_retirement(self):
        backend = FakeBackend(fake_baseline())
        ids = count()
        app = self.make_app(
            backend,
            id_factory=lambda: f"executing-capacity-{next(ids)}",
        )
        prepared = [
            app.prepare_apply(self.config).value
            for _ in range(_EXPECTED_FULL_RECORD_BOUND)
        ]
        target = prepared[0]
        entered = Event()
        release = Event()
        backend.block_at = "revalidating"
        backend.block_entered = entered
        backend.block_release = release
        results = []
        worker = Thread(
            target=lambda: results.append(
                app.execute_prepared(target, "APPLY CONFIG")
            )
        )
        worker.start()
        self.assertTrue(entered.wait(timeout=2.0))
        try:
            with persistent._REGISTRY_LOCK:
                record = persistent._PREPARATIONS[target.preparation_id]
                self.assertTrue(record.executing)
                self.assertTrue(persistent._make_active_room_locked())
                self.assertIn(target.preparation_id, persistent._PREPARATIONS)
                self.assertTrue(
                    persistent._PREPARATIONS[target.preparation_id].executing
                )
                self.assertEqual(
                    len(persistent._PREPARATIONS),
                    _EXPECTED_FULL_RECORD_BOUND - 1,
                )
        finally:
            release.set()
            worker.join(timeout=3.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].value.success)

    def test_capacity_saturation_with_only_executing_records_fails_without_overflow(self):
        backend = FakeBackend(fake_baseline())
        ids = count()
        app = self.make_app(
            backend,
            id_factory=lambda: f"saturated-{next(ids)}",
        )
        for _ in range(_EXPECTED_FULL_RECORD_BOUND):
            self.assertTrue(app.prepare_apply(self.config).ok)

        with persistent._REGISTRY_LOCK:
            records = list(persistent._PREPARATIONS.values())
            for record in records:
                record.executing = True
            try:
                self.assertFalse(persistent._make_active_room_locked())
                self.assertEqual(
                    len(persistent._PREPARATIONS),
                    _EXPECTED_FULL_RECORD_BOUND,
                )
            finally:
                for record in records:
                    record.executing = False
        self.assertEqual(backend.persistent_write_count, 0)

    def test_fifo_retirement_remains_issuance_order_after_access(self):
        backend = FakeBackend(fake_baseline())
        ids = count()
        app = self.make_app(
            backend,
            id_factory=lambda: f"fifo-{next(ids)}",
        )
        first = app.prepare_apply(self.config).value
        second = app.prepare_apply(self.config).value
        for _ in range(_EXPECTED_FULL_RECORD_BOUND - 2):
            self.assertTrue(app.prepare_apply(self.config).ok)

        with persistent._REGISTRY_LOCK:
            self.assertIsNotNone(
                persistent._PREPARATIONS.get(first.preparation_id)
            )
            self.assertIsNotNone(
                persistent._PREPARATIONS.get(second.preparation_id)
            )

        self.assertTrue(app.prepare_apply(self.config).ok)
        with persistent._REGISTRY_LOCK:
            self.assertNotIn(first.preparation_id, persistent._PREPARATIONS)
            self.assertIn(second.preparation_id, persistent._PREPARATIONS)
            self.assertIn(first.preparation_id, persistent._TOMBSTONES)

    def test_tombstone_replay_does_not_reorder_fifo_eviction(self):
        backend = FakeBackend(fake_baseline())
        ids = count()
        app = self.make_app(
            backend,
            id_factory=lambda: f"tombstone-fifo-{next(ids)}",
        )
        first = app.prepare_apply(self.config).value
        self.consume_without_writing(app, backend, first)
        second = app.prepare_apply(self.config).value
        self.consume_without_writing(app, backend, second)

        replay = app.execute_prepared(first, "APPLY CONFIG")
        self.assertIs(replay.value.error_code, ErrorCode.CONSUMED_PREPARATION)

        for _ in range(_EXPECTED_TOMBSTONE_BOUND - 2):
            current = app.prepare_apply(self.config).value
            self.consume_without_writing(app, backend, current)
        with persistent._REGISTRY_LOCK:
            self.assertIn(first.preparation_id, persistent._TOMBSTONES)
            self.assertIn(second.preparation_id, persistent._TOMBSTONES)

        current = app.prepare_apply(self.config).value
        self.consume_without_writing(app, backend, current)
        with persistent._REGISTRY_LOCK:
            self.assertNotIn(first.preparation_id, persistent._TOMBSTONES)
            self.assertIn(second.preparation_id, persistent._TOMBSTONES)

    def test_review_payload_tamper_is_zero_write_and_legitimate_record_survives(self):
        backend = FakeBackend(fake_baseline())
        app = self.make_app(backend, id_factory=lambda: "review-tamper")
        prepared = app.prepare_apply(self.config).value
        tampered_review = replace(
            prepared.review,
            warnings=prepared.review.warnings + ("tampered",),
        )
        tampered = replace(prepared, review=tampered_review)

        refused = app.execute_prepared(tampered, "APPLY CONFIG")
        self.assertFalse(refused.value.success)
        self.assertIs(refused.value.error_code, ErrorCode.STALE_PREPARATION)
        self.assertEqual(backend.persistent_write_count, 0)

        legitimate = app.execute_prepared(prepared, "APPLY CONFIG")
        self.assertTrue(legitimate.value.success)

    def test_accepted_cancellation_cleanup_reclaims_private_intent(self):
        backend = FakeBackend(fake_baseline())
        app = self.make_app(backend, id_factory=lambda: "cancel-cleanup")
        prepared = app.prepare_apply(self.config).value
        with persistent._REGISTRY_LOCK:
            intent_ref = weakref.ref(
                persistent._PREPARATIONS[prepared.preparation_id].intent
            )
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
        self.assertIs(result.value.error_code, ErrorCode.CANCELLED)
        self.assertEqual(backend.persistent_write_count, 0)
        gc.collect()
        self.assertIsNone(intent_ref())
        with persistent._REGISTRY_LOCK:
            self.assertNotIn(prepared.preparation_id, persistent._PREPARATIONS)
            self.assertTrue(
                persistent._TOMBSTONES[prepared.preparation_id].consumed
            )

        replay = app.execute_prepared(prepared, "APPLY CONFIG")
        self.assertIs(replay.value.error_code, ErrorCode.CONSUMED_PREPARATION)

    def test_baseexception_after_acceptance_retires_record_and_reclaims_intent(self):
        for exc_type in (KeyboardInterrupt, SystemExit):
            with self.subTest(exc_type=exc_type.__name__):
                backend = FakeBackend(fake_baseline())
                app = self.make_app(
                    backend,
                    id_factory=lambda e=exc_type: f"baseexception-{e.__name__}",
                )
                prepared = app.prepare_apply(self.config).value
                with persistent._REGISTRY_LOCK:
                    intent_ref = weakref.ref(
                        persistent._PREPARATIONS[prepared.preparation_id].intent
                    )

                def observer(state, exc_type=exc_type):
                    if state.phase is PersistentPhase.REVALIDATING:
                        raise exc_type()

                with self.assertRaises(exc_type):
                    app.execute_prepared(
                        prepared,
                        "APPLY CONFIG",
                        observer=observer,
                    )

                self.assertTrue(app.validate_details(private=False).ok)
                gc.collect()
                self.assertIsNone(intent_ref())
                with persistent._REGISTRY_LOCK:
                    self.assertNotIn(
                        prepared.preparation_id,
                        persistent._PREPARATIONS,
                    )
                    self.assertTrue(
                        persistent._TOMBSTONES[
                            prepared.preparation_id
                        ].consumed
                    )
                replay = app.execute_prepared(prepared, "APPLY CONFIG")
                self.assertIs(
                    replay.value.error_code,
                    ErrorCode.CONSUMED_PREPARATION,
                )

    def test_execute_vs_retire_race_is_fail_closed(self):
        entered = Event()
        release = Event()

        class GateCoordinator:
            mode = "normal"

            @contextmanager
            def claim(self):
                if self.mode == "gate":
                    entered.set()
                    if not release.wait(timeout=2.0):
                        raise RuntimeError("gate release timeout")
                yield

        coordinator = GateCoordinator()
        backend = FakeBackend(fake_baseline())
        app = ApplicationFacade(
            backend,
            coordinator=coordinator,
            id_factory=lambda: "execute-retire-race",
        )
        prepared = app.prepare_apply(self.config).value
        coordinator.mode = "gate"
        results = []
        worker = Thread(
            target=lambda: results.append(
                app.execute_prepared(prepared, "APPLY CONFIG")
            )
        )
        worker.start()
        self.assertTrue(entered.wait(timeout=2.0))
        try:
            with persistent._REGISTRY_LOCK:
                record = persistent._PREPARATIONS[prepared.preparation_id]
                persistent._retire_locked(prepared.preparation_id, record)
        finally:
            release.set()
            worker.join(timeout=3.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].value.success)
        self.assertIs(
            results[0].value.error_code,
            ErrorCode.STALE_PREPARATION,
        )
        self.assertEqual(backend.persistent_write_count, 0)

    def test_busy_vs_retire_race_is_fail_closed_and_retry_stales(self):
        entered = Event()
        release = Event()

        class BusyGateCoordinator:
            mode = "normal"

            @contextmanager
            def claim(self):
                if self.mode == "busy":
                    entered.set()
                    if not release.wait(timeout=2.0):
                        raise RuntimeError("busy release timeout")
                    raise OperationBusyError("synthetic busy")
                yield

        coordinator = BusyGateCoordinator()
        backend = FakeBackend(fake_baseline())
        app = ApplicationFacade(
            backend,
            coordinator=coordinator,
            id_factory=lambda: "busy-retire-race",
        )
        prepared = app.prepare_apply(self.config).value
        coordinator.mode = "busy"
        results = []
        worker = Thread(
            target=lambda: results.append(
                app.execute_prepared(prepared, "APPLY CONFIG")
            )
        )
        worker.start()
        self.assertTrue(entered.wait(timeout=2.0))
        try:
            with persistent._REGISTRY_LOCK:
                record = persistent._PREPARATIONS[prepared.preparation_id]
                persistent._retire_locked(prepared.preparation_id, record)
        finally:
            release.set()
            worker.join(timeout=3.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].value.success)
        self.assertIs(results[0].value.error_code, ErrorCode.BUSY)
        self.assertEqual(backend.persistent_write_count, 0)

        coordinator.mode = "normal"
        retry = app.execute_prepared(prepared, "APPLY CONFIG")
        self.assertFalse(retry.value.success)
        self.assertIs(retry.value.error_code, ErrorCode.STALE_PREPARATION)
        self.assertEqual(backend.persistent_write_count, 0)


if __name__ == "__main__":
    unittest.main()
