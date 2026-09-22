from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass
from typing import Any

from .constants import (
    ADDRESSABLE_MACRO_SECTORS,
    ALIASES,
    CONSUMER_USAGES,
    DIRECT_MOUSE_MASKS,
    GLOBAL_MACRO_SECTORS,
    GLOBAL_PAYLOAD_CAPACITY,
    GLOBAL_RAW_CAPACITY,
    KEYS,
    MODS,
    OP_ACPAN,
    OP_CONSUMER_DOWN,
    OP_CONSUMER_UP,
    OP_DELAY,
    OP_END,
    OP_JUMP,
    OP_KEY_DOWN,
    OP_KEY_UP,
    OP_REPEAT_WHILE_PRESSED,
    OP_ROLLER,
    OP_WAIT_FOR_RELEASE,
    OP_XY,
    PAGE_DATA_SIZE,
    PROFILE3_GSHIFT_OFFSETS,
    PROFILE3_NORMAL_OFFSETS,
    PROFILE3_TARGET_ORDER,
    PROFILE_BINDING_GSHIFT,
    PROFILE_BINDING_NOOP,
    PROFILE_DIRECTORY_ENABLE_OFFSETS,
    PROGRAMMABLE_PROFILES,
    REPORT_RATE_BY_CODE,
    REPORT_RATE_CODES,
    REQUIRES_HOST_ACTIONS,
    SECTOR_SIZE,
    US_CHAR_MAP,
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def crc16_ccitt(data: bytes, crc: int = 0xFFFF) -> int:
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def with_crc(data: bytes) -> bytes:
    if len(data) != SECTOR_SIZE:
        raise ValueError(f"sector must be {SECTOR_SIZE} bytes")
    out = bytearray(data)
    out[-2:] = struct.pack(">H", crc16_ccitt(out[:-2]))
    return bytes(out)


def sector_crc_ok(data: bytes) -> bool:
    if len(data) != SECTOR_SIZE:
        return False
    stored = struct.unpack(">H", data[-2:])[0]
    return stored == crc16_ccitt(data[:-2])


def sector_erased(data: bytes) -> bool:
    """Return True only for a physically erased 255-byte page."""
    return len(data) == SECTOR_SIZE and data == (b"\xFF" * SECTOR_SIZE)


def sector_health(data: bytes) -> str:
    """Classify storage without conflating erased pages with corruption."""
    if len(data) != SECTOR_SIZE:
        return "invalid"
    if sector_erased(data):
        return "erased"
    if sector_crc_ok(data):
        return "crc_valid"
    return "invalid"


def macro_page_health(data: bytes) -> str:
    """Classify a post-profile Macro Format 1 page.

    Hardware evidence includes both CRC-materialized macro pages and populated
    macro pages with no materialized CRC. A raw_no_crc page is not declared
    structurally valid by this classifier; referenced bytecode must still pass
    the VM walker.
    """
    if len(data) != SECTOR_SIZE:
        return "invalid"
    if sector_erased(data):
        return "erased"
    if sector_crc_ok(data):
        return "crc_valid"
    return "raw_no_crc"


def macro_payload_limit(data: bytes) -> int:
    """Return the addressable VM byte count for this physical macro page.

    Our managed pages reserve the final two bytes for CRC (253-byte payload).
    Raw externally-authored pages may use the full 255 bytes.
    """
    health = macro_page_health(data)
    if health == "invalid":
        raise ValueError("macro page has invalid size")
    return PAGE_DATA_SIZE if health == "crc_valid" else SECTOR_SIZE


def profile3_offset(layer: str, target: str) -> int:
    layer = str(layer).upper()
    target = str(target).upper()
    if target not in PROFILE3_TARGET_ORDER:
        raise ValueError(f"unknown Profile Format 3 target: {target}")
    if layer == "NORMAL":
        return PROFILE3_NORMAL_OFFSETS[target]
    if layer == "GSHIFT":
        return PROFILE3_GSHIFT_OFFSETS[target]
    raise ValueError(f"unknown layer: {layer}")


def profile_name(data: bytes) -> str:
    raw = data[0xA0:0xD0]
    out = bytearray()
    for i in range(0, len(raw) - 1, 2):
        pair = raw[i:i + 2]
        if pair in (b"\x00\x00", b"\xFF\xFF"):
            break
        out += pair
    return out.decode("utf-16le", errors="replace")


def profile_metadata(data: bytes) -> dict[str, Any]:
    """Decode physical Profile Format 3 metadata.

    Slot indexes remain explicit here because structural validation operates on
    the bytes that are actually stored on the device.
    """
    return {
        "name": profile_name(data),
        "report_rate": REPORT_RATE_BY_CODE.get(
            data[0], f"unknown:0x{data[0]:02X}"
        ),
        "default_dpi_index": data[1],
        "shift_dpi_index": data[2],
        "dpi": [
            struct.unpack("<H", data[3 + i * 2:5 + i * 2])[0]
            for i in range(5)
        ],
    }


def profile_display_metadata(data: bytes) -> dict[str, Any]:
    """Human-facing metadata using DPI values rather than slot indexes."""
    physical = profile_metadata(data)
    dpi = physical["dpi"]

    def selected(index: int) -> int:
        return dpi[index] if 0 <= index < len(dpi) else 0

    return {
        "name": physical["name"],
        "polling_rate_hz": physical["report_rate"],
        "default_dpi": selected(physical["default_dpi_index"]),
        "shift_dpi": selected(physical["shift_dpi_index"]),
        "dpi": dpi,
    }

def validate_directory(data: bytes, golden: bytes) -> tuple[int, ...]:
    if len(data) != SECTOR_SIZE:
        raise ValueError("Sector 0 has invalid size")
    if not sector_crc_ok(data):
        raise ValueError("Sector 0 CRC is invalid")

    allowed = {
        PROFILE_DIRECTORY_ENABLE_OFFSETS[3],
        PROFILE_DIRECTORY_ENABLE_OFFSETS[4],
        PROFILE_DIRECTORY_ENABLE_OFFSETS[5],
        SECTOR_SIZE - 2,
        SECTOR_SIZE - 1,
    }
    unexpected = [
        i
        for i, (before, after) in enumerate(zip(golden, data))
        if before != after and i not in allowed
    ]
    if unexpected:
        raise ValueError(
            "Sector 0 changed outside managed directory bytes: "
            + ", ".join(f"0x{x:02X}" for x in unexpected)
        )

    for profile in (1, 2):
        if data[PROFILE_DIRECTORY_ENABLE_OFFSETS[profile]] != 0x01:
            raise ValueError(f"Profile {profile} must remain enabled")

    enabled = []
    for profile in range(1, 6):
        value = data[PROFILE_DIRECTORY_ENABLE_OFFSETS[profile]]
        if value not in (0, 1):
            raise ValueError(
                f"Profile {profile} directory flag invalid: 0x{value:02X}"
            )
        if value == 1:
            enabled.append(profile)
    return tuple(enabled)


def build_directory(golden: bytes, enabled_profiles: set[int] | tuple[int, ...]) -> bytes:
    enabled = {int(x) for x in enabled_profiles}
    if not enabled.issubset({1, 2, 3, 4, 5}):
        raise ValueError(f"invalid profile set: {sorted(enabled)}")
    enabled.update((1, 2))
    out = bytearray(golden)
    for profile in (3, 4, 5):
        out[PROFILE_DIRECTORY_ENABLE_OFFSETS[profile]] = (
            1 if profile in enabled else 0
        )
    result = with_crc(bytes(out))
    validate_directory(result, golden)
    return result


def key_down(name: str) -> bytes:
    name = str(name).upper()
    if name in MODS:
        mask, usage = MODS[name]
        return bytes([OP_KEY_DOWN, mask, usage])
    if name not in KEYS:
        raise ValueError(f"unsupported key: {name}")
    return bytes([OP_KEY_DOWN, 0, KEYS[name]])


def key_up(name: str) -> bytes:
    name = str(name).upper()
    if name in MODS:
        mask, usage = MODS[name]
        return bytes([OP_KEY_UP, mask, usage])
    if name not in KEYS:
        raise ValueError(f"unsupported key: {name}")
    return bytes([OP_KEY_UP, 0, KEYS[name]])


def tap(name: str) -> bytes:
    return key_down(name) + key_up(name)


def delay(ms: int) -> bytes:
    if not isinstance(ms, int) or not 0 <= ms <= 65535:
        raise ValueError("delay.ms must be an integer from 0 to 65535")
    return bytes([OP_DELAY]) + struct.pack(">H", ms)


def compile_hotkey(keys: list[str]) -> bytes:
    if not isinstance(keys, list) or len(keys) < 2:
        raise ValueError("hotkey.keys requires modifier(s) + one normal key")
    names = [str(x).upper() for x in keys]
    mods = names[:-1]
    key = names[-1]
    for mod in mods:
        if mod not in MODS:
            raise ValueError(f"unsupported modifier: {mod}")
    if key in MODS or key not in KEYS:
        raise ValueError(f"hotkey must end in a supported non-modifier key: {key}")
    out = bytearray()
    for mod in mods:
        out += key_down(mod)
    out += key_down(key)
    out += key_up(key)
    for mod in reversed(mods):
        out += key_up(mod)
    return bytes(out)


def _text_key(ch: str, layout: str) -> tuple[int, int]:
    if "a" <= ch <= "z":
        return 0, KEYS[ch.upper()]
    if "A" <= ch <= "Z":
        return MODS["SHIFT"][0], KEYS[ch]
    if ch in "0123456789":
        return 0, KEYS[ch]
    if ch == " ":
        return 0, KEYS["SPACE"]
    if ch == "\n":
        return 0, KEYS["ENTER"]
    if ch == "\t":
        return 0, KEYS["TAB"]
    if layout == "us" and ch in US_CHAR_MAP:
        return US_CHAR_MAP[ch]
    if ord(ch) > 0x7F:
        raise ValueError(
            f"HOST_REQUIRED: no validated onboard Unicode primitive for {ch!r}"
        )
    raise ValueError(
        f"text character {ch!r} is unavailable for layout={layout!r}; "
        "use layout='us' for US ASCII punctuation"
    )


def compile_text(value: str, layout: str = "basic") -> bytes:
    if not isinstance(value, str):
        raise ValueError("text.value must be a string")
    layout = str(layout).lower()
    if layout not in ("basic", "us"):
        raise ValueError("text.layout must be 'basic' or 'us'")
    out = bytearray()
    for ch in value:
        mod, usage = _text_key(ch, layout)
        if mod:
            out += bytes([OP_KEY_DOWN, mod, 0xE1])
        out += bytes([OP_KEY_DOWN, 0, usage, OP_KEY_UP, 0, usage])
        if mod:
            out += bytes([OP_KEY_UP, mod, 0xE1])
    return bytes(out)


def compile_consumer(command: str) -> bytes:
    command = str(command).lower()
    if command not in CONSUMER_USAGES:
        raise ValueError(f"unsupported consumer command: {command}")
    usage = CONSUMER_USAGES[command]
    return bytes([
        OP_CONSUMER_DOWN,
        (usage >> 8) & 0xFF,
        usage & 0xFF,
        OP_CONSUMER_UP,
        (usage >> 8) & 0xFF,
        usage & 0xFF,
    ])


def compile_axis(opcode: int, amount: int) -> bytes:
    if not isinstance(amount, int) or amount == 0 or not -127 <= amount <= 127:
        raise ValueError("wheel amount must be an integer from -127..127 except 0")
    return bytes([opcode, amount & 0xFF, 0])


def normalize_action(spec: Any) -> dict[str, Any]:
    if isinstance(spec, str):
        key = spec.lower()
        if key not in ALIASES:
            raise ValueError(f"unknown action alias: {spec}")
        return dict(ALIASES[key])
    if isinstance(spec, dict):
        return dict(spec)
    raise ValueError("binding must be an alias string or action object")


def describe_action(action: dict[str, Any]) -> str:
    kind = str(action.get("action", "")).lower()
    if kind == "hotkey":
        return "+".join(str(x).upper() for x in action.get("keys", []))
    if kind == "tap":
        return f"TAP {str(action.get('key', '')).upper()}"
    if kind == "text":
        value = action.get("value", "")
        return f'TEXT {json.dumps(value, ensure_ascii=False)}'
    if kind == "delay":
        return f"DELAY {action.get('ms')} ms"
    if kind == "consumer":
        return str(action.get("command", "")).replace("_", " ").upper()
    if kind == "mouse":
        return f"MOUSE {str(action.get('command', '')).upper()}"
    if kind in ("wheel", "horizontal_wheel"):
        return f"{kind.upper()} {action.get('amount')}"
    if kind == "sequence":
        return f"SEQUENCE ({len(action.get('steps', []))} steps)"
    return kind or "?"


def compile_action(action: dict[str, Any]) -> bytes:
    kind = str(action.get("action", "")).lower()
    if kind in REQUIRES_HOST_ACTIONS:
        raise ValueError(f"HOST_REQUIRED: {kind}")
    if kind == "hotkey":
        return compile_hotkey(action.get("keys"))
    if kind == "tap":
        return tap(action.get("key"))
    if kind == "text":
        return compile_text(action.get("value"), action.get("layout", "basic"))
    if kind == "delay":
        return delay(action.get("ms"))
    if kind == "consumer":
        return compile_consumer(action.get("command"))
    if kind == "wheel":
        return compile_axis(OP_ROLLER, action.get("amount"))
    if kind in ("hpan", "horizontal_wheel"):
        return compile_axis(OP_ACPAN, action.get("amount"))
    if kind == "wait_for_release":
        return bytes([OP_WAIT_FOR_RELEASE])
    if kind == "repeat_while_pressed":
        return bytes([OP_REPEAT_WHILE_PRESSED])
    if kind == "mouse":
        raise ValueError(
            "mouse is supported as a direct binding only; VM mouse down/up is unresolved"
        )
    if kind == "sequence":
        steps = action.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ValueError("sequence.steps must be a non-empty array")
        return b"".join(compile_action(normalize_action(step)) for step in steps)
    raise ValueError(f"unknown action: {kind!r}")


def direct_keyboard_binding(action: dict[str, Any]) -> bytes | None:
    if action.get("action") != "hotkey":
        return None
    keys = action.get("keys")
    if not isinstance(keys, list) or len(keys) < 2:
        return None
    names = [str(x).upper() for x in keys]
    modifiers, key = names[:-1], names[-1]
    if key in MODS or key not in KEYS:
        return None
    if any(mod not in ("CTRL", "SHIFT", "ALT") for mod in modifiers):
        return None
    mask = 0
    for mod in modifiers:
        mask |= MODS[mod][0]
    return bytes([0x80, 0x02, mask, KEYS[key]])


def direct_consumer_binding(action: dict[str, Any]) -> bytes | None:
    if action.get("action") != "consumer":
        return None
    command = str(action.get("command", "")).lower()
    if command not in CONSUMER_USAGES:
        return None
    usage = CONSUMER_USAGES[command]
    return bytes([0x80, 0x03, (usage >> 8) & 0xFF, usage & 0xFF])


def direct_mouse_binding(action: dict[str, Any]) -> bytes | None:
    if action.get("action") != "mouse":
        return None
    command = str(action.get("command", "")).lower()
    if command not in DIRECT_MOUSE_MASKS:
        raise ValueError(f"unsupported direct mouse command: {command}")
    mask = DIRECT_MOUSE_MASKS[command]
    return bytes([0x80, 0x01, (mask >> 8) & 0xFF, mask & 0xFF])


def compile_binding(spec: Any) -> dict[str, Any]:
    action = normalize_action(spec)
    kind = str(action.get("action", "")).lower()
    if kind in REQUIRES_HOST_ACTIONS:
        raise ValueError(f"HOST_REQUIRED: {kind}")

    for lower in (
        direct_keyboard_binding,
        direct_consumer_binding,
        direct_mouse_binding,
    ):
        binding = lower(action)
        if binding is not None:
            return {
                "kind": "direct",
                "binding": binding,
                "description": describe_action(action),
                "action": action,
            }

    bytecode = compile_action(action) + bytes([OP_END])
    return {
        "kind": "macro",
        "bytecode": bytecode,
        "description": describe_action(action),
        "action": action,
    }


def tokenize_macro(bytecode: bytes) -> list[bytes]:
    tokens: list[bytes] = []
    pc = 0
    three = {
        OP_ROLLER,
        OP_ACPAN,
        OP_DELAY,
        OP_KEY_DOWN,
        OP_KEY_UP,
        OP_CONSUMER_DOWN,
        OP_CONSUMER_UP,
        OP_XY,
    }
    while pc < len(bytecode):
        op = bytecode[pc]
        if op in (OP_WAIT_FOR_RELEASE, OP_REPEAT_WHILE_PRESSED, OP_END):
            size = 1
        elif op in three:
            size = 3
        elif op == OP_JUMP:
            size = 5
        else:
            raise ValueError(f"unknown VM opcode 0x{op:02X} at +0x{pc:02X}")
        if pc + size > len(bytecode):
            raise ValueError(f"truncated VM instruction at +0x{pc:02X}")
        tokens.append(bytecode[pc:pc + size])
        pc += size
    if not tokens or tokens[-1] != bytes([OP_END]):
        raise ValueError("compiled macro must end in END")
    return tokens


def jump_bytes(page: int, offset: int) -> bytes:
    if page not in GLOBAL_MACRO_SECTORS:
        raise ValueError(f"JUMP page outside global store: {page}")
    if not 0 <= offset < PAGE_DATA_SIZE:
        raise ValueError(f"JUMP offset outside payload: {offset}")
    return bytes([
        OP_JUMP,
        (page >> 8) & 0xFF,
        page & 0xFF,
        (offset >> 8) & 0xFF,
        offset & 0xFF,
    ])


def macro_pointer(sector: int, offset: int) -> bytes:
    if sector not in GLOBAL_MACRO_SECTORS:
        raise ValueError(f"macro pointer outside global store: {sector}")
    if not 0 <= offset < PAGE_DATA_SIZE:
        raise ValueError(f"macro pointer outside payload: {offset}")
    return bytes([0, sector, (offset >> 8) & 0xFF, offset & 0xFF])


def allocate_unique_macros(unique: list[dict[str, Any]]) -> dict[str, Any]:
    pages = {
        sector: bytearray([0xFF] * SECTOR_SIZE)
        for sector in GLOBAL_MACRO_SECTORS
    }
    page_index = 0
    offset = 0
    fragmentation_waste = 0
    allocations: dict[int, dict[str, Any]] = {}

    for index, macro in enumerate(unique):
        tokens = tokenize_macro(macro["bytecode"])

        if offset > PAGE_DATA_SIZE - 6:
            fragmentation_waste += PAGE_DATA_SIZE - offset
            page_index += 1
            offset = 0
        if page_index >= len(GLOBAL_MACRO_SECTORS):
            raise ValueError("global macro store exhausted")

        start_sector = GLOBAL_MACRO_SECTORS[page_index]
        start_offset = offset
        fragments = []
        token_index = 0

        while token_index < len(tokens):
            if page_index >= len(GLOBAL_MACRO_SECTORS):
                raise ValueError("global macro store exhausted")

            sector = GLOBAL_MACRO_SECTORS[page_index]
            available = PAGE_DATA_SIZE - offset
            remaining = sum(len(x) for x in tokens[token_index:])

            if remaining <= available:
                fragment_start = offset
                for token in tokens[token_index:]:
                    pages[sector][offset:offset + len(token)] = token
                    offset += len(token)
                fragments.append({
                    "sector": sector,
                    "offset": fragment_start,
                    "end": offset,
                    "jump": None,
                })
                token_index = len(tokens)
                break

            payload_capacity = available - 5
            fit: list[bytes] = []
            used = 0
            while token_index < len(tokens):
                token = tokens[token_index]
                if used + len(token) > payload_capacity:
                    break
                fit.append(token)
                used += len(token)
                token_index += 1

            if not fit:
                if not fragments:
                    fragmentation_waste += available
                    page_index += 1
                    offset = 0
                    if page_index >= len(GLOBAL_MACRO_SECTORS):
                        raise ValueError("global macro store exhausted")
                    start_sector = GLOBAL_MACRO_SECTORS[page_index]
                    start_offset = 0
                    continue
                raise RuntimeError("allocator could not reserve JUMP space")

            fragment_start = offset
            for token in fit:
                pages[sector][offset:offset + len(token)] = token
                offset += len(token)

            next_page_index = page_index + 1
            if next_page_index >= len(GLOBAL_MACRO_SECTORS):
                raise ValueError("macro requires more pages than global store")
            next_sector = GLOBAL_MACRO_SECTORS[next_page_index]
            jump = jump_bytes(next_sector, 0)
            pages[sector][offset:offset + len(jump)] = jump
            offset += len(jump)

            fragments.append({
                "sector": sector,
                "offset": fragment_start,
                "end": offset,
                "jump": {"sector": next_sector, "offset": 0},
            })
            fragmentation_waste += PAGE_DATA_SIZE - offset
            page_index = next_page_index
            offset = 0

        allocations[index] = {
            "sector": start_sector,
            "offset": start_offset,
            "source_length": len(macro["bytecode"]),
            "routes": list(macro["routes"]),
            "sha256": macro["sha256"],
            "fragments": fragments,
        }

    images: dict[int, bytes] = {}
    for sector, page in pages.items():
        page[-2:] = struct.pack(">H", crc16_ccitt(page[:PAGE_DATA_SIZE]))
        images[sector] = bytes(page)

    allocated_bytes = sum(
        fragment["end"] - fragment["offset"]
        for alloc in allocations.values()
        for fragment in alloc["fragments"]
    )
    source_bytes = sum(len(x["bytecode"]) for x in unique)

    return {
        "sector_images": images,
        "allocations": allocations,
        "source_bytes": source_bytes,
        "allocated_bytes": allocated_bytes,
        "jump_overhead": allocated_bytes - source_bytes,
        "fragmentation_waste": fragmentation_waste,
        "raw_free_bytes": GLOBAL_PAYLOAD_CAPACITY - allocated_bytes,
        "usable_free_bytes": (
            GLOBAL_PAYLOAD_CAPACITY - allocated_bytes - fragmentation_waste
        ),
    }


def _apply_settings(profile: bytes, settings: dict[str, Any], profile_num: int) -> bytes:
    out = bytearray(profile)

    if "polling_rate_hz" in settings:
        rate = settings["polling_rate_hz"]
        if rate not in REPORT_RATE_CODES:
            raise ValueError(
                f"Profile {profile_num}: polling_rate_hz must be 1000/500/250/125"
            )
        out[0] = REPORT_RATE_CODES[rate]

    if "dpi" in settings:
        values = list(settings["dpi"])
        values += [0] * (5 - len(values))
        for index, value in enumerate(values):
            out[3 + index * 2:5 + index * 2] = struct.pack("<H", value)

    effective_dpi = [
        struct.unpack("<H", out[3 + i * 2:5 + i * 2])[0]
        for i in range(5)
    ]
    for key, byte_offset in (("default_dpi", 1), ("shift_dpi", 2)):
        if key not in settings:
            continue
        value = settings[key]
        try:
            slot = effective_dpi.index(value)
        except ValueError as exc:
            raise ValueError(
                f"Profile {profile_num}: {key}={value} is not present in the effective DPI list"
            ) from exc
        out[byte_offset] = slot

    if "name" in settings:
        encoded = settings["name"].encode("utf-16le")
        out[0xA0:0xD0] = encoded + bytes(48 - len(encoded))

    return bytes(out)

def _neutralize_macro_pointers(profile: bytes) -> bytes:
    out = bytearray(profile)
    for layer in ("NORMAL", "GSHIFT"):
        for target in PROFILE3_TARGET_ORDER:
            off = profile3_offset(layer, target)
            binding = bytes(out[off:off + 4])
            if len(binding) == 4 and binding[0] == 0 and 6 <= binding[1] <= 15:
                out[off:off + 4] = PROFILE_BINDING_NOOP
    return bytes(out)


def build_plan(config: dict[str, Any], golden: dict[int, bytes]) -> dict[str, Any]:
    profiles = config["profiles"]

    compiled_profiles: dict[int, dict[str, dict[str, Any]]] = {}
    profile_meta: dict[int, dict[str, Any]] = {}

    for profile_num in sorted(profiles):
        block = profiles[profile_num]
        compiled: dict[str, dict[str, Any]] = {}

        for layer_key, layer_name in (("buttons", "NORMAL"), ("g_shift", "GSHIFT")):
            for target, spec in sorted(block.get(layer_key, {}).items()):
                item = compile_binding(spec)
                item["profile"] = profile_num
                item["layer"] = layer_name
                item["target"] = target
                compiled[f"{layer_name}:{target}"] = item

        compiled_profiles[profile_num] = compiled
        profile_meta[profile_num] = {
            "g_shift_button": block.get("g_shift_button"),
            "settings": dict(block.get("settings", {})),
        }

    unique: list[dict[str, Any]] = []
    hash_to_index: dict[str, int] = {}
    route_to_macro: dict[tuple[int, str], int] = {}

    for profile_num in sorted(compiled_profiles):
        for route in sorted(compiled_profiles[profile_num]):
            item = compiled_profiles[profile_num][route]
            if item["kind"] != "macro":
                continue
            digest = sha256(item["bytecode"])
            key = (profile_num, route)
            if digest not in hash_to_index:
                hash_to_index[digest] = len(unique)
                unique.append({
                    "sha256": digest,
                    "bytecode": item["bytecode"],
                    "routes": [key],
                })
            else:
                unique[hash_to_index[digest]]["routes"].append(key)
            route_to_macro[key] = hash_to_index[digest]

    store = allocate_unique_macros(unique)

    profile_plans: dict[int, dict[str, Any]] = {}
    for profile_num in PROGRAMMABLE_PROFILES:
        if profile_num not in compiled_profiles:
            profile_plans[profile_num] = {
                "profile": golden[profile_num],
                "results": {},
                "disabled": True,
            }
            continue

        profile = bytearray(_neutralize_macro_pointers(golden[profile_num]))
        block = profiles[profile_num]

        # Hardware-validated behavior: every managed profile gets a
        # deterministic shifted table. Omitted G-Shift routes are NOOP rather
        # than inherited factory actions.
        for target in PROFILE3_TARGET_ORDER:
            off = profile3_offset("GSHIFT", target)
            profile[off:off + 4] = PROFILE_BINDING_NOOP

        if block.get("g_shift_button"):
            off = profile3_offset("NORMAL", block["g_shift_button"])
            profile[off:off + 4] = PROFILE_BINDING_GSHIFT

        results: dict[str, Any] = {}
        for route in sorted(compiled_profiles[profile_num]):
            item = compiled_profiles[profile_num][route]
            off = profile3_offset(item["layer"], item["target"])

            if item["kind"] == "direct":
                profile[off:off + 4] = item["binding"]
                results[route] = {
                    "kind": "direct",
                    "binding": item["binding"],
                    "description": item["description"],
                    "profile_offset": off,
                }
                continue

            allocation = store["allocations"][
                route_to_macro[(profile_num, route)]
            ]
            profile[off:off + 4] = macro_pointer(
                allocation["sector"], allocation["offset"]
            )
            results[route] = {
                "kind": "macro",
                "sector": allocation["sector"],
                "offset": allocation["offset"],
                "source_length": allocation["source_length"],
                "fragments": allocation["fragments"],
                "sha256": allocation["sha256"],
                "description": item["description"],
                "profile_offset": off,
            }

        profile_bytes = _apply_settings(
            bytes(profile),
            profile_meta[profile_num]["settings"],
            profile_num,
        )
        profile_bytes = with_crc(profile_bytes)

        profile_plans[profile_num] = {
            "profile": profile_bytes,
            "results": results,
            "disabled": False,
            "settings": profile_meta[profile_num]["settings"],
        }

    enabled = tuple(sorted({1, 2, *[p for p in profiles if p >= 3]}))
    directory = build_directory(golden[0], set(enabled))

    warnings = []
    critical = {"G1", "G2", "G3"}
    for profile_num, block in profiles.items():
        touched = sorted(critical.intersection(block.get("buttons", {})))
        if touched:
            warnings.append(
                f"Profile {profile_num} overrides critical normal buttons: "
                + ", ".join(touched)
            )

    return {
        "format": 1,
        "enabled_profiles": enabled,
        "profiles": profile_plans,
        "global_store": store,
        "unique_macros": unique,
        "directory": directory,
        "warnings": warnings,
    }


def plan_to_jsonable(plan: dict[str, Any]) -> dict[str, Any]:
    profiles = {}
    for profile_num in PROGRAMMABLE_PROFILES:
        p = plan["profiles"][profile_num]
        profiles[str(profile_num)] = {
            "disabled": p["disabled"],
            "profile_sha256": sha256(p["profile"]),
            "metadata": profile_display_metadata(p["profile"]),
            "routes": {},
        }
        for route, item in sorted(p["results"].items()):
            row = {
                key: value
                for key, value in item.items()
                if key not in ("binding",)
            }
            if "binding" in item:
                row["binding_hex"] = item["binding"].hex(" ").upper()
            profiles[str(profile_num)]["routes"][route] = row

    allocations = []
    for index, allocation in sorted(plan["global_store"]["allocations"].items()):
        allocations.append({
            "index": index,
            **allocation,
            "routes": [
                {"profile": p, "route": route}
                for p, route in allocation["routes"]
            ],
        })

    return {
        "format": "g502x-plan-v1",
        "enabled_profiles": list(plan["enabled_profiles"]),
        "warnings": list(plan["warnings"]),
        "profiles": profiles,
        "global_store": {
            "raw_capacity": GLOBAL_RAW_CAPACITY,
            "payload_capacity": GLOBAL_PAYLOAD_CAPACITY,
            "source_bytes": plan["global_store"]["source_bytes"],
            "allocated_bytes": plan["global_store"]["allocated_bytes"],
            "jump_overhead": plan["global_store"]["jump_overhead"],
            "fragmentation_waste": plan["global_store"]["fragmentation_waste"],
            "raw_free_bytes": plan["global_store"]["raw_free_bytes"],
            "usable_free_bytes": plan["global_store"]["usable_free_bytes"],
            "pages_sha256": {
                str(sector): sha256(image)
                for sector, image in sorted(
                    plan["global_store"]["sector_images"].items()
                )
            },
            "allocations": allocations,
        },
        "directory_sha256": sha256(plan["directory"]),
    }


def plan_json(plan: dict[str, Any]) -> str:
    return json.dumps(
        plan_to_jsonable(plan),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


@dataclass(frozen=True)
class VmInstruction:
    sector: int
    offset: int
    opcode: int
    size: int
    description: str
    jump_target: tuple[int, int] | None = None


def decode_instruction(pages: dict[int, bytes], sector: int, offset: int) -> VmInstruction:
    if sector not in ADDRESSABLE_MACRO_SECTORS or sector not in pages:
        raise ValueError(f"VM address outside readable macro store: sector {sector}")

    page = pages[sector]
    limit = macro_payload_limit(page)
    if not 0 <= offset < limit:
        raise ValueError(f"VM address outside page payload: s{sector}:0x{offset:02X}")

    op = page[offset]

    if op == OP_END:
        return VmInstruction(sector, offset, op, 1, "END")
    if op == OP_WAIT_FOR_RELEASE:
        return VmInstruction(sector, offset, op, 1, "WAIT_FOR_RELEASE")
    if op == OP_REPEAT_WHILE_PRESSED:
        return VmInstruction(sector, offset, op, 1, "REPEAT_WHILE_PRESSED")

    if op == OP_JUMP:
        if offset + 5 > limit:
            raise ValueError(f"truncated JUMP at s{sector}:0x{offset:02X}")
        target_sector = (page[offset + 1] << 8) | page[offset + 2]
        target_offset = (page[offset + 3] << 8) | page[offset + 4]
        if target_sector not in ADDRESSABLE_MACRO_SECTORS or target_sector not in pages:
            raise ValueError(
                f"JUMP at s{sector}:0x{offset:02X} targets invalid sector {target_sector}"
            )
        target_limit = macro_payload_limit(pages[target_sector])
        if not 0 <= target_offset < target_limit:
            raise ValueError(
                f"JUMP at s{sector}:0x{offset:02X} targets invalid offset {target_offset}"
            )
        return VmInstruction(
            sector,
            offset,
            op,
            5,
            f"JUMP s{target_sector}:0x{target_offset:02X}",
            (target_sector, target_offset),
        )

    if op in (
        OP_ROLLER,
        OP_ACPAN,
        OP_DELAY,
        OP_KEY_DOWN,
        OP_KEY_UP,
        OP_CONSUMER_DOWN,
        OP_CONSUMER_UP,
        OP_XY,
    ):
        if offset + 3 > limit:
            raise ValueError(
                f"truncated 3-byte opcode at s{sector}:0x{offset:02X}"
            )
        a, b = page[offset + 1], page[offset + 2]
        if op == OP_DELAY:
            desc = f"DELAY {(a << 8) | b} ms"
        elif op in (OP_KEY_DOWN, OP_KEY_UP):
            desc = f"KEY {'DOWN' if op == OP_KEY_DOWN else 'UP'} mod=0x{a:02X} usage=0x{b:02X}"
        elif op in (OP_CONSUMER_DOWN, OP_CONSUMER_UP):
            desc = f"CONSUMER {'DOWN' if op == OP_CONSUMER_DOWN else 'UP'} 0x{((a << 8) | b):04X}"
        elif op in (OP_ROLLER, OP_ACPAN):
            signed = a if a < 128 else a - 256
            desc = f"{'ROLLER' if op == OP_ROLLER else 'ACPAN'} {signed}"
        else:
            desc = f"XY {a:02X} {b:02X}"
        return VmInstruction(sector, offset, op, 3, desc)

    raise ValueError(
        f"unknown opcode 0x{op:02X} at s{sector}:0x{offset:02X}"
    )


def walk_macro(
    pages: dict[int, bytes],
    start: tuple[int, int],
    *,
    max_instructions: int = 4096,
) -> list[VmInstruction]:
    sector, offset = start
    seen: set[tuple[int, int]] = set()
    result: list[VmInstruction] = []

    for _ in range(max_instructions):
        address = (sector, offset)
        if address in seen:
            raise ValueError(f"JUMP cycle detected at s{sector}:0x{offset:02X}")
        seen.add(address)

        ins = decode_instruction(pages, sector, offset)
        result.append(ins)

        if ins.opcode == OP_END:
            return result
        if ins.jump_target is not None:
            sector, offset = ins.jump_target
        else:
            offset += ins.size
            if offset >= macro_payload_limit(pages[sector]):
                raise ValueError(
                    f"macro falls off page without JUMP/END at sector {sector}"
                )

    raise ValueError("macro instruction limit exceeded without END")
