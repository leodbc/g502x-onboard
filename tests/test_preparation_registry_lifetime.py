from __future__ import annotations

import json
import struct
import tempfile
import unittest
from itertools import count
from pathlib import Path

import g502x_onboard.application.persistent as persistent
from g502x_onboard.application.facade import ApplicationFacade
from g502x_onboard.application.fake_backend import FakeBackend
from g502x_onboard.codec import crc16_ccitt
from g502x_onboard.constants import SECTOR_SIZE


_EXPECTED_FULL_RECORD_BOUND = 32


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


class PreparationRegistryBaselineRegressionTests(unittest.TestCase):
    def setUp(self):
        with persistent._REGISTRY_LOCK:
            persistent._PREPARATIONS.clear()
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

    def test_live_facade_abandoned_preparations_are_bounded(self):
        ids = count()
        app = ApplicationFacade(
            FakeBackend(fake_baseline()),
            id_factory=lambda: f"abandoned-{next(ids)}",
        )
        for _ in range(_EXPECTED_FULL_RECORD_BOUND + 8):
            prepared = app.prepare_apply(self.config)
            self.assertTrue(prepared.ok)

        with persistent._REGISTRY_LOCK:
            retained = len(persistent._PREPARATIONS)
        self.assertLessEqual(retained, _EXPECTED_FULL_RECORD_BOUND)

    def test_consumed_preparations_do_not_accumulate_full_records(self):
        ids = count()
        backend = FakeBackend(fake_baseline())
        app = ApplicationFacade(
            backend,
            id_factory=lambda: f"consumed-{next(ids)}",
        )
        for _ in range(_EXPECTED_FULL_RECORD_BOUND + 8):
            prepared = app.prepare_apply(self.config)
            self.assertTrue(prepared.ok)
            backend.host_guard_recheck_clear = False
            result = app.execute_prepared(prepared.value, "APPLY CONFIG")
            backend.host_guard_recheck_clear = True
            self.assertFalse(result.value.success)
            self.assertEqual(backend.persistent_write_count, 0)

        with persistent._REGISTRY_LOCK:
            retained = len(persistent._PREPARATIONS)
        self.assertLessEqual(retained, _EXPECTED_FULL_RECORD_BOUND)


if __name__ == "__main__":
    unittest.main()
