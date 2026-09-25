from __future__ import annotations

import gc
import json
import struct
import tempfile
import unittest
import weakref
from dataclasses import replace
from itertools import count
from pathlib import Path

import g502x_onboard.application.persistent as persistent
from g502x_onboard.application import ErrorCode
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


if __name__ == "__main__":
    unittest.main()
