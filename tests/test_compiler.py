from __future__ import annotations

import copy
import json
import random
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CHECKPOINT_ROOT = Path(__file__).resolve().parents[1]
OMM_ROOT = Path(__file__).resolve().parents[2]
if str(OMM_ROOT) not in sys.path:
    sys.path.insert(0, str(OMM_ROOT))

from g502x_onboard.codec import (
    allocate_unique_macros,
    build_plan,
    crc16_ccitt,
    decode_instruction,
    plan_json,
    macro_pointer,
    profile3_offset,
    sector_crc_ok,
    walk_macro,
)
from g502x_onboard.config import ConfigError, validate_config
from g502x_onboard.validator import validate_images
from libs.utils import (
    escape_bytes,
    prepare_windows_hidapi_directory,
    unescape_bytes,
    verify_windows_hidapi_loaded,
)

from g502x_onboard.constants import (
    GLOBAL_MACRO_SECTORS,
    GLOBAL_PAYLOAD_CAPACITY,
    OP_END,
    OP_JUMP,
    PAGE_DATA_SIZE,
    SECTOR_SIZE,
)


def fake_sector(fill=0xFF):
    data = bytearray([fill] * SECTOR_SIZE)
    data[-2:] = struct.pack(">H", crc16_ccitt(data[:-2]))
    return bytes(data)


def fake_golden():
    golden = {i: fake_sector() for i in range(16)}

    # Minimal directory compatible with current validation.
    d = bytearray(golden[0])
    # profile entry flags at 2,6,10,14,18
    for off in (2, 6):
        d[off] = 1
    for off in (10, 14, 18):
        d[off] = 0
    d[-2:] = struct.pack(">H", crc16_ccitt(d[:-2]))
    golden[0] = bytes(d)

    # Minimal profile images. Header values only need to be mutable in tests.
    for p in (1, 2, 3, 4, 5):
        d = bytearray(golden[p])
        d[0] = 1
        d[1] = 0
        d[2] = 0
        # all binding areas remain FF = unused
        d[-2:] = struct.pack(">H", crc16_ccitt(d[:-2]))
        golden[p] = bytes(d)

    return golden


class CliWorkingDirectoryTests(unittest.TestCase):
    def _run_cli(self, *args: str):
        script = CHECKPOINT_ROOT / "g502x.py"
        with tempfile.TemporaryDirectory() as td:
            return subprocess.run(
                [sys.executable, str(script), *args],
                cwd=td,
                check=True,
                capture_output=True,
                text=True,
            )

    def test_offline_cli_does_not_depend_on_current_directory(self):
        self.assertIn("PASS", self._run_cli("selftest").stdout)
        self.assertIn("0.1.0", self._run_cli("--version").stdout)
        payload = json.loads(self._run_cli("capabilities", "--json").stdout)
        self.assertIsInstance(payload, list)
        self.assertGreater(len(payload), 0)
        required = {"name", "status", "scope", "note"}
        self.assertTrue(
            all(
                isinstance(row, dict) and required.issubset(row)
                for row in payload
            )
        )


class WindowsHidapiBootstrapTests(unittest.TestCase):
    def test_non_windows_is_noop(self):
        self.assertIsNone(
            prepare_windows_hidapi_directory(
                __file__,
                platform="linux",
                pointer_bits=64,
                add_dll_directory=lambda _path: self.fail(
                    "non-Windows path should not register a DLL directory"
                ),
            )
        )

    def test_windows_arch_selects_existing_vendored_directory_and_returns_handle(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            module = root / "LogiHPP20.py"
            module.write_text("# fixture\n", encoding="utf-8")
            (root / "x64").mkdir()
            (root / "x86").mkdir()
            (root / "x64" / "hidapi.dll").write_bytes(b"x64")
            (root / "x86" / "hidapi.dll").write_bytes(b"x86")

            calls = []
            marker64 = object()
            marker32 = object()

            out64 = prepare_windows_hidapi_directory(
                module,
                platform="win32",
                pointer_bits=64,
                add_dll_directory=lambda path: (
                    calls.append(path),
                    marker64,
                )[1],
            )
            out32 = prepare_windows_hidapi_directory(
                module,
                platform="win32",
                pointer_bits=32,
                add_dll_directory=lambda path: (
                    calls.append(path),
                    marker32,
                )[1],
            )

            self.assertIs(out64, marker64)
            self.assertIs(out32, marker32)
            self.assertEqual(Path(calls[0]).name, "x64")
            self.assertEqual(Path(calls[1]).name, "x86")

    def test_windows_missing_arch_dll_fails_before_pyhidapi_import(self):
        with tempfile.TemporaryDirectory() as td:
            module = Path(td) / "LogiHPP20.py"
            module.write_text("# fixture\n", encoding="utf-8")
            with self.assertRaisesRegex(ImportError, "bundled Windows hidapi.dll missing"):
                prepare_windows_hidapi_directory(
                    module,
                    platform="win32",
                    pointer_bits=64,
                    add_dll_directory=lambda _path: object(),
                )

    def test_windows_unknown_pointer_width_fails_closed(self):
        with self.assertRaisesRegex(ImportError, "unsupported Python pointer width"):
            prepare_windows_hidapi_directory(
                __file__,
                platform="win32",
                pointer_bits=128,
                add_dll_directory=lambda _path: object(),
            )


    def test_windows_loaded_dll_must_match_vendored_path_and_version(self):
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            module = root / "LogiHPP20.py"
            module.write_text("# fixture\n", encoding="utf-8")
            dll = root / "x64" / "hidapi.dll"
            dll.parent.mkdir()
            dll.write_bytes(b"fixture")

            hid_module = SimpleNamespace(
                version=(0, 15, 0),
                hidapi=SimpleNamespace(_handle=123),
            )
            actual = verify_windows_hidapi_loaded(
                module,
                hid_module,
                platform="win32",
                pointer_bits=64,
                get_module_filename=lambda handle: str(dll),
                expected_hashes={
                    "x64": __import__("hashlib").sha256(
                        dll.read_bytes()
                    ).hexdigest()
                },
            )
            self.assertEqual(actual, dll.resolve())

            other = root / "other" / "hidapi.dll"
            other.parent.mkdir()
            other.write_bytes(b"other")
            with self.assertRaisesRegex(ImportError, "unexpected native library"):
                verify_windows_hidapi_loaded(
                    module,
                    hid_module,
                    platform="win32",
                    pointer_bits=64,
                    get_module_filename=lambda handle: str(other),
                    expected_hashes={
                        "x64": __import__("hashlib").sha256(
                            dll.read_bytes()
                        ).hexdigest()
                    },
                )

    def test_windows_loaded_dll_version_must_be_exact(self):
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            module = root / "LogiHPP20.py"
            module.write_text("# fixture\n", encoding="utf-8")
            dll = root / "x64" / "hidapi.dll"
            dll.parent.mkdir()
            dll.write_bytes(b"fixture")

            hid_module = SimpleNamespace(
                version=(0, 14, 0),
                hidapi=SimpleNamespace(_handle=123),
            )
            with self.assertRaisesRegex(ImportError, "version"):
                verify_windows_hidapi_loaded(
                    module,
                    hid_module,
                    platform="win32",
                    pointer_bits=64,
                    get_module_filename=lambda handle: str(dll),
                )

    def test_non_windows_loaded_dll_verification_is_noop(self):
        self.assertIsNone(
            verify_windows_hidapi_loaded(
                __file__,
                object(),
                platform="linux",
            )
        )


    def test_windows_loaded_dll_hash_must_match_pin(self):
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            module = root / "LogiHPP20.py"
            module.write_text("# fixture\n", encoding="utf-8")
            dll = root / "x64" / "hidapi.dll"
            dll.parent.mkdir()
            dll.write_bytes(b"tampered")

            hid_module = SimpleNamespace(
                version=(0, 15, 0),
                hidapi=SimpleNamespace(_handle=123),
            )
            with self.assertRaisesRegex(ImportError, "SHA-256"):
                verify_windows_hidapi_loaded(
                    module,
                    hid_module,
                    platform="win32",
                    pointer_bits=64,
                    get_module_filename=lambda handle: str(dll),
                    expected_hashes={"x64": "0" * 64},
                )


class EscapedBytesCompatibilityTests(unittest.TestCase):
    def test_all_byte_values_round_trip(self):
        raw = bytes(range(256))
        text = escape_bytes(raw)
        self.assertEqual(len(text), 4 * 256)
        self.assertTrue(text.startswith("\\x00\\x01\\x02"))
        self.assertTrue(text.endswith("\\xfd\\xfe\\xff"))
        self.assertEqual(unescape_bytes(text), raw)

    def test_uppercase_hex_is_accepted_for_legacy_input(self):
        self.assertEqual(
            unescape_bytes("\\x00\\xAF\\xff"),
            bytes([0x00, 0xAF, 0xFF]),
        )

    def test_malformed_escape_text_fails_closed(self):
        for value in (
            "\\x0",
            "\\xGG",
            "41",
            "\\n",
            "\\x00tail",
            " \\x00",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    unescape_bytes(value)

    def test_empty_escape_string_round_trips_empty_bytes(self):
        self.assertEqual(escape_bytes(b""), "")
        self.assertEqual(unescape_bytes(""), b"")


class VmBoundaryHardeningTests(unittest.TestCase):
    @staticmethod
    def crc_page(payload: bytes = b"") -> bytes:
        page = bytearray(b"\xFF" * SECTOR_SIZE)
        page[: len(payload)] = payload
        page[-2:] = struct.pack(">H", crc16_ccitt(page[:-2]))
        return bytes(page)

    @staticmethod
    def raw_page(payload: bytes = b"") -> bytes:
        page = bytearray(b"\xFF" * SECTOR_SIZE)
        page[: len(payload)] = payload
        return bytes(page)

    def test_crc_page_never_decodes_inside_crc_trailer(self):
        page = self.crc_page()
        with self.assertRaisesRegex(ValueError, "outside page payload"):
            decode_instruction({8: page}, 8, 253)
        with self.assertRaisesRegex(ValueError, "outside page payload"):
            decode_instruction({8: page}, 8, 254)

    def test_raw_page_can_use_final_byte_as_end(self):
        page = bytearray(self.raw_page(b"\x43\x00\x04"))
        page[254] = OP_END
        chain = walk_macro({8: bytes(page)}, (8, 254))
        self.assertEqual(len(chain), 1)
        self.assertEqual(chain[0].opcode, OP_END)
        self.assertEqual(chain[0].offset, 254)

    def test_three_byte_opcode_boundary_is_exact(self):
        page = bytearray(self.crc_page())
        page[250:253] = bytes.fromhex("43 00 04")
        page[-2:] = struct.pack(">H", crc16_ccitt(page[:-2]))
        ins = decode_instruction({8: bytes(page)}, 8, 250)
        self.assertEqual(ins.size, 3)

        page = bytearray(self.crc_page())
        page[251] = 0x43
        page[-2:] = struct.pack(">H", crc16_ccitt(page[:-2]))
        with self.assertRaisesRegex(ValueError, "truncated 3-byte opcode"):
            decode_instruction({8: bytes(page)}, 8, 251)

    def test_five_byte_jump_boundary_is_exact(self):
        page8 = bytearray(self.crc_page())
        page9 = self.crc_page()
        page8[248:253] = bytes.fromhex("60 00 09 00 00")
        page8[-2:] = struct.pack(">H", crc16_ccitt(page8[:-2]))
        ins = decode_instruction({8: bytes(page8), 9: page9}, 8, 248)
        self.assertEqual(ins.size, 5)
        self.assertEqual(ins.jump_target, (9, 0))

        page8 = bytearray(self.crc_page())
        page8[249] = OP_JUMP
        page8[-2:] = struct.pack(">H", crc16_ccitt(page8[:-2]))
        with self.assertRaisesRegex(ValueError, "truncated JUMP"):
            decode_instruction({8: bytes(page8), 9: page9}, 8, 249)

    def test_cross_page_jump_cycle_fails_closed(self):
        page8 = bytearray(self.raw_page())
        page9 = bytearray(self.raw_page())
        page8[0:5] = bytes.fromhex("60 00 09 00 00")
        page9[0:5] = bytes.fromhex("60 00 08 00 00")
        with self.assertRaisesRegex(ValueError, "cycle"):
            walk_macro({8: bytes(page8), 9: bytes(page9)}, (8, 0))

    def test_jump_target_offset_respects_target_page_encoding(self):
        raw = bytearray(self.raw_page(b"\x00"))
        raw[254] = OP_END
        source = bytearray(self.raw_page())
        source[0:5] = bytes.fromhex("60 00 09 00 FE")
        chain = walk_macro({8: bytes(source), 9: bytes(raw)}, (8, 0))
        self.assertEqual(chain[-1].offset, 254)

        crc_target = self.crc_page()
        with self.assertRaisesRegex(ValueError, "invalid offset"):
            decode_instruction(
                {8: bytes(source), 9: crc_target},
                8,
                0,
            )

    def test_deterministic_adversarial_corpus_never_crashes_unbounded(self):
        rng = random.Random(0x502)
        for case in range(500):
            pages = {}
            for sector in range(6, 16):
                raw = bytes(rng.randrange(256) for _ in range(SECTOR_SIZE))
                # Mix raw external pages and compiler-style CRC pages.
                if (case + sector) % 3 == 0:
                    page = bytearray(raw)
                    page[-2:] = struct.pack(
                        ">H",
                        crc16_ccitt(page[:-2]),
                    )
                    raw = bytes(page)
                pages[sector] = raw

            sector = rng.randrange(6, 16)
            limit = PAGE_DATA_SIZE if sector_crc_ok(pages[sector]) else SECTOR_SIZE
            offset = rng.randrange(limit)
            try:
                chain = walk_macro(
                    pages,
                    (sector, offset),
                    max_instructions=64,
                )
            except ValueError:
                continue

            self.assertGreaterEqual(len(chain), 1)
            self.assertLessEqual(len(chain), 64)
            self.assertEqual(
                chain[-1].opcode,
                OP_END,
                f"case {case} returned without END",
            )


class ConfigTests(unittest.TestCase):
    def test_canonical_config(self):
        cfg = validate_config({
            "format": 1,
            "profiles": {
                "2": {
                    "settings": {
                        "name": "WORK",
                        "polling_rate_hz": 1000,
                        "dpi": [800, 1600],
                        "default_dpi": 1600,
                        "shift_dpi": 800,
                    },
                    "buttons": {
                        "G4": "copy",
                        "G5": {"action": "mouse", "command": "back"},
                    },
                }
            },
        })
        self.assertIn(2, cfg["profiles"])

    def test_config_file_requires_explicit_format(self):
        from g502x_onboard.config import load_config

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            path.write_text(
                json.dumps({"profiles": {"2": {}}}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ConfigError, "root.format is required"):
                load_config(path)

    def test_unknown_field_is_rejected(self):
        with self.assertRaises(ConfigError):
            validate_config({
                "profiles": {"2": {"surprise": True}},
            })

    def test_host_action_is_rejected(self):
        with self.assertRaisesRegex(ConfigError, "HOST_REQUIRED"):
            validate_config({
                "profiles": {
                    "2": {
                        "buttons": {
                            "G4": {"action": "emoji", "value": "x"},
                        }
                    }
                }
            })


    def test_normal_wheel_customization_is_rejected(self):
        with self.assertRaises(ConfigError):
            validate_config({
                "profiles": {
                    "2": {
                        "buttons": {
                            "WHEEL_UP": "volume_up"
                        }
                    }
                }
            })

    def test_gshift_wheel_requires_direct_consumer(self):
        with self.assertRaises(ConfigError):
            validate_config({
                "profiles": {
                    "2": {
                        "g_shift_button": "G6",
                        "g_shift": {
                            "WHEEL_UP": "copy"
                        }
                    }
                }
            })

    def test_gshift_requires_activator(self):
        with self.assertRaises(ConfigError):
            validate_config({
                "profiles": {
                    "2": {
                        "g_shift": {"G4": "copy"},
                    }
                }
            })


class AllocatorTests(unittest.TestCase):
    def test_long_text_deduplicates_across_profiles_and_pages(self):
        text = "abcdefghijklmnopqrstuvwxyz0123456789 " * 3
        cfg = validate_config({
            "profiles": {
                "2": {"buttons": {"G4": {"action": "text", "value": text}}},
                "3": {"buttons": {"G4": {"action": "text", "value": text}}},
            }
        })
        plan = build_plan(cfg, fake_golden())
        self.assertEqual(len(plan["unique_macros"]), 1)

        p2 = plan["profiles"][2]["results"]["NORMAL:G4"]
        p3 = plan["profiles"][3]["results"]["NORMAL:G4"]
        self.assertEqual(
            (p2["sector"], p2["offset"]),
            (p3["sector"], p3["offset"]),
        )
        self.assertGreaterEqual(len(p2["fragments"]), 2)

    def test_macro_pages_reserve_crc_bytes(self):
        cfg = validate_config({
            "profiles": {
                "2": {
                    "buttons": {
                        "G4": {
                            "action": "text",
                            "value": "a" * 100,
                        }
                    }
                }
            }
        })
        plan = build_plan(cfg, fake_golden())
        for image in plan["global_store"]["sector_images"].values():
            self.assertEqual(len(image), SECTOR_SIZE)
            self.assertTrue(sector_crc_ok(image))

    def test_jump_never_enters_crc_region(self):
        cfg = validate_config({
            "profiles": {
                "2": {
                    "buttons": {
                        "G4": {
                            "action": "text",
                            "value": "a" * 100,
                        }
                    }
                }
            }
        })
        plan = build_plan(cfg, fake_golden())
        alloc = plan["global_store"]["allocations"][0]
        for fragment in alloc["fragments"]:
            self.assertLessEqual(fragment["end"], PAGE_DATA_SIZE)


    def test_exact_payload_page_fill(self):
        # 84 x 3-byte DELAY + END = exactly 253 payload bytes.
        exact = bytes([0x40, 0, 1]) * 84 + bytes([OP_END])
        unique = [{
            "sha256": "exact",
            "bytecode": exact,
            "routes": [(2, "NORMAL:G4")],
        }]
        store = allocate_unique_macros(unique)
        alloc = store["allocations"][0]
        self.assertEqual(len(alloc["fragments"]), 1)
        self.assertEqual(alloc["fragments"][0]["end"], PAGE_DATA_SIZE)
        self.assertEqual(store["fragmentation_waste"], 0)

    def test_small_tail_is_counted_as_fragmentation(self):
        # 246 + WAIT + END = 248, leaving 5 bytes. A new macro cannot start
        # there because the allocator reserves enough room for at least one token + JUMP.
        first = bytes([0x40, 0, 1]) * 82 + bytes([0x01, OP_END])
        second = bytes([0x40, 0, 1, OP_END])
        unique = [
            {"sha256": "a", "bytecode": first, "routes": [(2, "NORMAL:G4")]},
            {"sha256": "b", "bytecode": second, "routes": [(2, "NORMAL:G5")]},
        ]
        store = allocate_unique_macros(unique)
        self.assertEqual(store["fragmentation_waste"], 5)
        self.assertEqual(store["allocations"][1]["sector"], 9)
        self.assertEqual(store["allocations"][1]["offset"], 0)

    def test_pointer_rejects_crc_region_and_recovery_pages(self):
        with self.assertRaises(ValueError):
            macro_pointer(7, 0)
        with self.assertRaises(ValueError):
            macro_pointer(8, PAGE_DATA_SIZE)

    def test_exhaustion_is_rejected(self):
        huge = bytes([0x43, 0, 4, 0x44, 0, 4]) * 400 + bytes([OP_END])
        unique = [{
            "sha256": "x",
            "bytecode": huge,
            "routes": [(2, "NORMAL:G4")],
        }]
        with self.assertRaises(ValueError):
            allocate_unique_macros(unique)

    def test_deterministic_plan_json(self):
        cfg = validate_config({
            "profiles": {
                "2": {"buttons": {"G4": "copy", "G5": "volume_up"}},
            }
        })
        golden = fake_golden()
        a = plan_json(build_plan(copy.deepcopy(cfg), golden))
        b = plan_json(build_plan(copy.deepcopy(cfg), golden))
        self.assertEqual(a, b)
        json.loads(a)


class ValidatorErasedPageTests(unittest.TestCase):
    def test_unreferenced_erased_macro_page_is_accepted(self):
        baseline = fake_golden()
        images = copy.deepcopy(baseline)
        images[8] = b"\xFF" * SECTOR_SIZE

        report = validate_images(images, baseline=baseline)
        self.assertTrue(report.ok, report.errors)

    def test_reference_into_erased_macro_page_is_rejected(self):
        baseline = fake_golden()
        images = copy.deepcopy(baseline)
        images[8] = b"\xFF" * SECTOR_SIZE

        p2 = bytearray(images[2])
        off = profile3_offset("NORMAL", "G4")
        p2[off:off + 4] = bytes([0x00, 0x08, 0x00, 0x00])
        p2[-2:] = struct.pack(">H", crc16_ccitt(p2[:-2]))
        images[2] = bytes(p2)

        report = validate_images(images, baseline=baseline)
        self.assertFalse(report.ok)
        self.assertTrue(
            any("END/empty byte" in error for error in report.errors),
            report.errors,
        )


class MacroWalkerTests(unittest.TestCase):
    def _pages(self):
        pages = {
            sector: bytearray([0xFF] * SECTOR_SIZE)
            for sector in GLOBAL_MACRO_SECTORS
        }
        return pages

    def test_cross_page_jump(self):
        pages = self._pages()
        pages[8][0:5] = bytes([OP_JUMP, 0, 9, 0, 0])
        pages[9][0] = OP_END
        result = walk_macro({k: bytes(v) for k, v in pages.items()}, (8, 0))
        self.assertEqual(result[-1].description, "END")

    def test_corrupt_jump_target_is_rejected(self):
        pages = self._pages()
        pages[8][0:5] = bytes([OP_JUMP, 0, 7, 0, 0])
        with self.assertRaises(ValueError):
            walk_macro({k: bytes(v) for k, v in pages.items()}, (8, 0))

    def test_jump_cycle_is_rejected(self):
        pages = self._pages()
        pages[8][0:5] = bytes([OP_JUMP, 0, 9, 0, 0])
        pages[9][0:5] = bytes([OP_JUMP, 0, 8, 0, 0])
        with self.assertRaisesRegex(ValueError, "cycle"):
            walk_macro({k: bytes(v) for k, v in pages.items()}, (8, 0))


if __name__ == "__main__":
    unittest.main()
