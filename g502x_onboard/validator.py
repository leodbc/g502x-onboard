from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

from .codec import (
    macro_page_health,
    macro_payload_limit,
    profile3_offset,
    profile_metadata,
    sector_crc_ok,
    validate_directory,
    walk_macro,
)
from .constants import (
    ADDRESSABLE_MACRO_SECTORS,
    CONSUMER_USAGES,
    DIRECT_MOUSE_MASKS,
    GLOBAL_MACRO_SECTORS,
    KEYS,
    PAGE_DATA_SIZE,
    PROFILE3_TARGET_ORDER,
    PROGRAMMABLE_PROFILES,
    RECOVERY_SECTORS,
    REPORT_RATE_BY_CODE,
    SECTOR_COUNT,
    SECTOR_SIZE,
)
from .storage import baseline_map


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    enabled_profiles: tuple[int, ...] = ()
    macro_starts: dict[tuple[int, int], list[str]] = field(default_factory=dict)
    macro_chains: dict[tuple[int, int], list[Any]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def binding_kind(binding: bytes) -> str:
    if binding == b"\xFF\xFF\xFF\xFF":
        return "unused"
    if binding[0] == 0x80 and binding[1] in (0x01, 0x02, 0x03):
        return "direct"
    if binding[0] == 0x90:
        return "special"
    if binding[0] == 0x00:
        return "macro"
    return "unknown"


def validate_images(
    images: dict[int, bytes],
    baseline: dict[int, bytes] | None = None,
) -> ValidationReport:
    baseline = baseline or baseline_map()
    report = ValidationReport()

    missing = [sector for sector in range(SECTOR_COUNT) if sector not in images]
    if missing:
        report.errors.append(f"missing sectors: {missing}")
        return report

    for sector in range(SECTOR_COUNT):
        if len(images[sector]) != SECTOR_SIZE:
            report.errors.append(
                f"sector {sector}: size={len(images[sector])}, expected={SECTOR_SIZE}"
            )

    if report.errors:
        return report

    for sector in RECOVERY_SECTORS:
        if images[sector] != baseline[sector]:
            report.errors.append(
                f"recovery sector {sector} differs from active device baseline"
            )

    try:
        report.enabled_profiles = validate_directory(images[0], baseline[0])
    except Exception as exc:
        report.errors.append(f"Sector 0: {exc}")

    for profile in PROGRAMMABLE_PROFILES:
        if not sector_crc_ok(images[profile]):
            report.errors.append(f"Profile {profile}: CRC invalid")

    pages = {sector: images[sector] for sector in ADDRESSABLE_MACRO_SECTORS}
    for sector, page in pages.items():
        health = macro_page_health(page)
        if health == "invalid":
            report.errors.append(f"macro-capable sector {sector}: invalid page size")
        elif health == "raw_no_crc" and any(byte != 0xFF for byte in page):
            report.warnings.append(
                f"macro-capable sector {sector}: populated raw page without "
                "materialized CRC; referenced bytecode is validated structurally"
            )
    mouse_masks = set(DIRECT_MOUSE_MASKS.values())
    key_usages = set(KEYS.values())
    consumer_usages = set(CONSUMER_USAGES.values())

    for profile in PROGRAMMABLE_PROFILES:
        data = images[profile]
        metadata = profile_metadata(data)

        if profile in report.enabled_profiles:
            if data[0] not in REPORT_RATE_BY_CODE:
                report.errors.append(
                    f"Profile {profile}: unknown report-rate code 0x{data[0]:02X}"
                )
            for key in ("default_dpi_index", "shift_dpi_index"):
                if not 0 <= metadata[key] <= 4:
                    report.errors.append(
                        f"Profile {profile}: {key} out of range: {metadata[key]}"
                    )

        if profile not in report.enabled_profiles:
            continue

        for layer in ("NORMAL", "GSHIFT"):
            for target in PROFILE3_TARGET_ORDER:
                off = profile3_offset(layer, target)
                binding = data[off:off + 4]
                kind = binding_kind(binding)
                route = f"P{profile}:{layer}:{target}"

                if kind == "unknown":
                    report.errors.append(
                        f"{route}: unknown binding {binding.hex(' ').upper()}"
                    )
                    continue

                if kind == "direct":
                    subtype = binding[1]
                    if subtype == 0x01:
                        mask = (binding[2] << 8) | binding[3]
                        if mask not in mouse_masks:
                            report.errors.append(
                                f"{route}: unvalidated direct mouse mask 0x{mask:04X}"
                            )
                    elif subtype == 0x02:
                        if binding[3] not in key_usages:
                            report.errors.append(
                                f"{route}: unknown direct keyboard usage 0x{binding[3]:02X}"
                            )
                    elif subtype == 0x03:
                        usage = (binding[2] << 8) | binding[3]
                        if usage not in consumer_usages:
                            report.errors.append(
                                f"{route}: unvalidated direct consumer usage 0x{usage:04X}"
                            )
                    continue

                if kind != "macro":
                    continue

                sector = binding[1]
                macro_offset = (binding[2] << 8) | binding[3]
                if sector not in ADDRESSABLE_MACRO_SECTORS:
                    report.errors.append(
                        f"{route}: macro pointer targets sector {sector}, "
                        "outside readable Macro Format 1 store"
                    )
                    continue
                if not 0 <= macro_offset < macro_payload_limit(pages[sector]):
                    report.errors.append(
                        f"{route}: macro pointer offset 0x{macro_offset:04X} "
                        "outside page payload"
                    )
                    continue

                if pages[sector][macro_offset] == 0xFF:
                    report.errors.append(
                        f"{route}: macro pointer targets END/empty byte at "
                        f"s{sector}:0x{macro_offset:02X}"
                    )
                    continue

                start = (sector, macro_offset)
                report.macro_starts.setdefault(start, []).append(route)

    for start, routes in sorted(report.macro_starts.items()):
        try:
            report.macro_chains[start] = walk_macro(pages, start)
        except Exception as exc:
            report.errors.append(
                f"{', '.join(routes)} -> s{start[0]}:0x{start[1]:02X}: {exc}"
            )

    return report


def validate_device() -> tuple[dict[int, bytes], ValidationReport]:
    from .device import assert_active_device_matches_baseline, read_all

    manifest = assert_active_device_matches_baseline()
    images = read_all(manifest=manifest)
    return images, validate_images(images)


def inspect_rows(
    images: dict[int, bytes],
    report: ValidationReport | None = None,
    *,
    baseline: dict[int, bytes] | None = None,
) -> list[dict[str, Any]]:
    report = report or validate_images(images, baseline)
    rows = []
    for start, routes in sorted(report.macro_starts.items()):
        chain = report.macro_chains.get(start, [])
        rows.append({
            "start": {"sector": start[0], "offset": start[1]},
            "routes": list(routes),
            "instructions": [
                {
                    "sector": ins.sector,
                    "offset": ins.offset,
                    "opcode": ins.opcode,
                    "size": ins.size,
                    "description": ins.description,
                    "jump_target": (
                        {
                            "sector": ins.jump_target[0],
                            "offset": ins.jump_target[1],
                        }
                        if ins.jump_target is not None
                        else None
                    ),
                }
                for ins in chain
            ],
        })
    return rows


def state_summary(
    images: dict[int, bytes],
    report: ValidationReport,
    *,
    baseline: dict[int, bytes] | None = None,
) -> dict[str, Any]:
    baseline = baseline or baseline_map()

    profiles = {}
    for profile in PROGRAMMABLE_PROFILES:
        profiles[str(profile)] = {
            "enabled": profile in report.enabled_profiles,
            "crc_ok": sector_crc_ok(images[profile]),
            "metadata": profile_metadata(images[profile]),
        }

    pages = {}
    for sector in GLOBAL_MACRO_SECTORS:
        limit = macro_payload_limit(images[sector])
        payload = images[sector][:limit]
        used_positions = [i for i, byte in enumerate(payload) if byte != 0xFF]
        health = macro_page_health(images[sector])
        pages[str(sector)] = {
            "crc_ok": sector_crc_ok(images[sector]),
            "health": health,
            "erased": health == "erased",
            "payload_non_ff": len(used_positions),
            "high_water": max(used_positions) + 1 if used_positions else 0,
            "payload_capacity": limit,
        }

    return {
        "ok": report.ok,
        "errors": list(report.errors),
        "warnings": list(report.warnings),
        "enabled_profiles": list(report.enabled_profiles),
        "recovery": {
            str(sector): images[sector] == baseline[sector]
            for sector in RECOVERY_SECTORS
        },
        "profiles": profiles,
        "macro_pages": pages,
        "referenced_macro_starts": len(report.macro_starts),
    }


def public_state_summary(
    images: dict[int, bytes],
    report: ValidationReport,
    *,
    baseline: dict[int, bytes] | None = None,
) -> dict[str, Any]:
    """Minimal shareable state: structural health, not user configuration."""
    baseline = baseline or baseline_map()

    profiles = {
        str(profile): {
            "enabled": profile in report.enabled_profiles,
            "crc_ok": sector_crc_ok(images[profile]),
        }
        for profile in PROGRAMMABLE_PROFILES
    }

    macro_pages = {}
    for sector in ADDRESSABLE_MACRO_SECTORS:
        health = macro_page_health(images[sector])
        macro_pages[str(sector)] = {
            "health": health,
            "crc_ok": sector_crc_ok(images[sector]),
            "erased": health == "erased",
        }

    return {
        "ok": report.ok,
        "error_count": len(report.errors),
        "warning_count": len(report.warnings),
        "enabled_profiles": list(report.enabled_profiles),
        "recovery": {
            str(sector): images[sector] == baseline[sector]
            for sector in RECOVERY_SECTORS
        },
        "profiles": profiles,
        "macro_pages": macro_pages,
        "referenced_macro_starts": len(report.macro_starts),
    }


def export_state(
    images: dict[int, bytes],
    *,
    report: ValidationReport | None = None,
    include_raw: bool = False,
    baseline: dict[int, bytes] | None = None,
) -> dict[str, Any]:
    baseline = baseline or baseline_map()
    report = report or validate_images(images, baseline)

    profiles: dict[str, Any] = {}
    for profile in PROGRAMMABLE_PROFILES:
        data = images[profile]
        routes: dict[str, Any] = {}
        for layer in ("NORMAL", "GSHIFT"):
            for target in PROFILE3_TARGET_ORDER:
                off = profile3_offset(layer, target)
                binding = data[off:off + 4]
                row: dict[str, Any] = {
                    "kind": binding_kind(binding),
                    "binding_hex": binding.hex(" ").upper(),
                    "profile_offset": off,
                }
                if row["kind"] == "macro":
                    row["macro_pointer"] = {
                        "sector": binding[1],
                        "offset": (binding[2] << 8) | binding[3],
                    }
                routes[f"{layer}:{target}"] = row

        profiles[str(profile)] = {
            "enabled": profile in report.enabled_profiles,
            "metadata": profile_metadata(data),
            "crc_ok": sector_crc_ok(data),
            "routes": routes,
        }

    result: dict[str, Any] = {
        "format": "g502x-state-export-v1",
        "privacy": {
            "classification": "private-diagnostic",
            "shareable": False,
            "contains_profile_names": True,
            "contains_bindings_and_macro_bytecode": True,
            "contains_raw_sectors": bool(include_raw),
        },
        "validation": {
            "ok": report.ok,
            "errors": list(report.errors),
            "warnings": list(report.warnings),
        },
        "enabled_profiles": list(report.enabled_profiles),
        "profiles": profiles,
        "macros": inspect_rows(images, report, baseline=baseline),
        "baseline_differences": {
            str(sector): images[sector] != baseline[sector]
            for sector in range(SECTOR_COUNT)
        },
    }

    if include_raw:
        result["raw_sectors_base64"] = {
            str(sector): base64.b64encode(images[sector]).decode("ascii")
            for sector in range(SECTOR_COUNT)
        }

    return result
