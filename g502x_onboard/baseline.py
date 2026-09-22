from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import VERSION
from .codec import macro_page_health, sector_crc_ok, sector_health, sha256
from .constants import SECTOR_COUNT, SECTOR_SIZE
from .private_io import (
    private_commit_dir,
    private_discard_dir,
    private_mkdir,
    private_replace_dir,
    private_stage_dir,
    require_private_directory,
    require_private_regular_file,
    private_write_bytes,
    private_write_text,
)

HOME = Path(
    os.environ.get("G502X_HOME", str(Path.home() / ".g502x"))
).expanduser()
BASELINES = HOME / "baselines"
ACTIVE = HOME / "active-baseline.json"

DEFAULT_PID = 0xC547
DEFAULT_INDEX = 0x01
TESTED_WRITE_TARGETS = {
    (0xC547, 0x01): "G502 X LIGHTSPEED via original LIGHTSPEED receiver",
}


TESTED_DEVICE_TARGET = {
    "device_name": "G502 X LIGHTSPEED",
    "model_id": "409F",
    "firmware_prefix": "MPM",
    "firmware_version": "30.00.B0014",
    "firmware_transport_pid": "409F",
}


class BaselineError(RuntimeError):
    pass


SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


FINGERPRINT_RE = re.compile(r"^[0-9a-fA-F]{24}$")


def normalize_fingerprint(value: Any, *, context: str = "fingerprint") -> str:
    """Validate the only legal local baseline directory identifier."""
    if not isinstance(value, str):
        raise BaselineError(f"{context}: expected a 24-hex string")
    normalized = value.strip().lower()
    if not FINGERPRINT_RE.fullmatch(normalized):
        raise BaselineError(f"{context}: expected exactly 24 hexadecimal characters")
    return normalized


def require_complete_sector_hashes(
    manifest: dict[str, Any],
    *,
    context: str,
) -> dict[str, dict[str, Any]]:
    rows = manifest.get("sectors")
    if not isinstance(rows, dict):
        raise BaselineError(f"{context}: sector hash table missing")
    expected_keys = {str(i) for i in range(SECTOR_COUNT)}
    actual_keys = set(rows)
    if actual_keys != expected_keys:
        missing = sorted(expected_keys - actual_keys)
        extra = sorted(actual_keys - expected_keys)
        raise BaselineError(
            f"{context}: sector hash table incomplete; "
            f"missing={missing} extra={extra}"
        )
    for sector in range(SECTOR_COUNT):
        row = rows.get(str(sector))
        digest = row.get("sha256") if isinstance(row, dict) else None
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise BaselineError(
                f"{context}: sector {sector} missing valid SHA-256"
            )
    return rows


def require_manifest_fingerprint(
    manifest: dict[str, Any],
    *,
    key: str,
    expected: str,
    context: str,
) -> str:
    value = manifest.get(key)
    if not isinstance(value, str) or not value:
        raise BaselineError(f"{context}: {key} missing")
    if value != expected:
        raise BaselineError(
            f"{context}: {key} does not match active device baseline"
        )
    return value


def baseline_sector_health(sector: int, data: bytes) -> str:
    """Role-aware health for setup/public reporting."""
    if not 0 <= int(sector) < SECTOR_COUNT or len(data) != SECTOR_SIZE:
        return "invalid"
    if int(sector) <= 5:
        return sector_health(data)
    return macro_page_health(data)


def baseline_sector_capture_ok(sector: int, data: bytes) -> bool:
    """Conservative, role-aware setup policy.

    Directory/profile sectors 0..5 require a materialized valid CRC.
    Post-profile sectors 6..15 are firmware macro/opaque storage and may be
    CRC-materialized, erased, or raw without CRC. Referenced macro bytecode is
    validated structurally by the VM validator before baseline capture.
    """
    health = baseline_sector_health(sector, data)
    if int(sector) <= 5:
        return health == "crc_valid"
    return health in ("crc_valid", "erased", "raw_no_crc")


def _hex(value: int) -> str:
    return f"0x{int(value):04X}"


def compatibility_class(
    descriptor: dict[str, Any],
    pid: int,
    index: int,
) -> dict[str, Any]:
    expected = {
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
    mismatches = {
        key: {"expected": expected_value, "observed": descriptor.get(key)}
        for key, expected_value in expected.items()
        if descriptor.get(key) != expected_value
    }
    architecture = "compatible" if not mismatches else "unknown"
    transport = (
        "tested"
        if (int(pid), int(index)) in TESTED_WRITE_TARGETS
        else "untested"
    )
    return {
        "architecture": architecture,
        "transport": transport,
        "write_allowed": architecture == "compatible" and transport == "tested",
        "mismatches": mismatches,
        "tested_target": TESTED_WRITE_TARGETS.get((int(pid), int(index))),
    }


def probe_read_policy(
    compatibility: dict[str, Any],
    *,
    read_sectors: bool,
) -> dict[str, str]:
    """Define how far a read-only probe may inspect device memory.

    Descriptor/device-information discovery is generic. Deep memory reads use
    our Profile Format 3 / 16x255 assumptions and therefore run only when the
    descriptor already matches the validated architecture exactly.
    """
    known = compatibility.get("architecture") == "compatible"
    if not known:
        live = "skipped_unknown_architecture"
        oob = "skipped_unknown_architecture"
    else:
        live = "requested_known_geometry" if read_sectors else "skipped_by_request"
        oob = "requested_known_geometry"

    return {
        "descriptor": "read",
        "device_information": "read",
        "active_profile": "best_effort",
        "live_sectors": live,
        "oob": oob,
    }


def validated_device_target(device_info: dict[str, Any]) -> tuple[str, str]:
    """Classify the physical product/firmware against local write evidence."""
    expected = TESTED_DEVICE_TARGET
    name = str(device_info.get("device_name") or "").strip()
    model_ids = {
        str(value).strip().upper()
        for value in (device_info.get("model_ids") or [])
        if value is not None
    }
    firmware = device_info.get("firmware") or []

    if name != expected["device_name"]:
        return (
            "unvalidated",
            f"device name {name!r} is not the locally validated target",
        )
    if expected["model_id"] not in model_ids:
        return (
            "unvalidated",
            "validated model ID is absent from DEVICE INFORMATION",
        )

    match = any(
        bool(row.get("active"))
        and str(row.get("prefix") or "") == expected["firmware_prefix"]
        and str(row.get("version_raw") or "") == expected["firmware_version"]
        and str(row.get("transport_pid") or "").upper()
        == expected["firmware_transport_pid"]
        for row in firmware
        if isinstance(row, dict)
    )
    if not match:
        return (
            "unvalidated",
            "active firmware does not match the locally validated MPM build",
        )

    return (
        "tested",
        "device name, model ID and active firmware match local write evidence",
    )


def apply_identity_policy(
    compatibility: dict[str, Any],
    device_info: dict[str, Any],
) -> dict[str, Any]:
    """Require exact target class + stable per-unit identity for writes."""
    out = dict(compatibility)
    target, target_reason = validated_device_target(device_info)
    out["device_target"] = target
    out["device_target_reason"] = target_reason

    raw = device_info.get("unit_id")
    unit_id = str(raw).strip().upper() if raw is not None else ""
    strong = bool(unit_id) and set(unit_id) not in ({"0"}, {"F"})

    if strong:
        out["identity"] = "unit-bound"
        out["identity_reason"] = "stable HID++ unit_id available"
    else:
        out["identity"] = "insufficient"
        out["identity_reason"] = (
            "stable HID++ unit_id unavailable; baseline cannot be safely "
            "bound to one physical device"
        )

    out["write_allowed"] = bool(out.get("write_allowed")) and strong and (
        target == "tested"
    )
    return out

def fingerprint(
    *,
    descriptor: dict[str, Any],
    device_info: dict[str, Any],
    pid: int,
    index: int,
) -> str:
    identity = {
        "pid": int(pid),
        "index": int(index),
        "descriptor": {
            key: descriptor.get(key)
            for key in (
                "memory_model",
                "profile_format",
                "macro_format",
                "profile_count",
                "factory_profiles",
                "button_count",
                "sector_count",
                "sector_size",
                "mechanical_layout",
            )
        },
        "device_name": device_info.get("device_name"),
        "protocol": device_info.get("protocol"),
        "unit_id": device_info.get("unit_id"),
        "model_ids": device_info.get("model_ids"),
        "firmware": device_info.get("firmware"),
    }
    raw = json.dumps(
        identity,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def manifest_fingerprint_matches(
    manifest: dict[str, Any],
    *,
    descriptor: dict[str, Any],
    device_info: dict[str, Any],
) -> bool:
    """Match a live unit against the baseline using the baseline transport."""
    transport = manifest.get("transport", {})
    pid = int(transport.get("pid", DEFAULT_PID))
    index = int(transport.get("index", DEFAULT_INDEX))
    return fingerprint(
        descriptor=descriptor,
        device_info=device_info,
        pid=pid,
        index=index,
    ) == manifest.get("fingerprint")


def baseline_path(fingerprint_value: str) -> Path:
    return BASELINES / normalize_fingerprint(
        fingerprint_value,
        context="baseline fingerprint",
    )


def _manifest_path(root: Path) -> Path:
    return root / "manifest.json"


def _read_images(root: Path) -> dict[int, bytes]:
    require_private_directory(root, context="baseline")
    images: dict[int, bytes] = {}
    for sector in range(SECTOR_COUNT):
        path = require_private_regular_file(
            root / f"sector-{sector:02d}.bin",
            context=f"baseline sector {sector}",
        )
        data = path.read_bytes()
        if len(data) != SECTOR_SIZE:
            raise BaselineError(
                f"baseline sector {sector} has {len(data)} bytes; "
                f"expected {SECTOR_SIZE}"
            )
        images[sector] = data
    return images

def _load_manifest_for_fingerprint(
    root: Path,
    expected_fingerprint: str,
    *,
    context: str,
) -> dict[str, Any]:
    try:
        require_private_directory(root, context=context)
        path = require_private_regular_file(
            _manifest_path(root),
            context=f"{context} manifest",
        )
    except RuntimeError as exc:
        raise BaselineError(str(exc)) from exc

    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("format") != "g502x-device-baseline-v1":
        raise BaselineError(f"{context}: unsupported baseline format")

    manifest_fp = normalize_fingerprint(
        manifest.get("fingerprint"),
        context=f"{context} manifest fingerprint",
    )
    expected_fp = normalize_fingerprint(
        expected_fingerprint,
        context=f"{context} expected fingerprint",
    )
    if manifest_fp != expected_fp:
        raise BaselineError(
            f"{context}: manifest fingerprint does not match expected device"
        )

    require_complete_sector_hashes(
        manifest,
        context=f"{context} manifest",
    )
    return manifest


def _load_baseline_for_fingerprint(
    root: Path,
    expected_fingerprint: str,
    *,
    context: str,
) -> tuple[dict[int, bytes], dict[str, Any]]:
    manifest = _load_manifest_for_fingerprint(
        root,
        expected_fingerprint,
        context=context,
    )
    rows = require_complete_sector_hashes(
        manifest,
        context=f"{context} manifest",
    )
    images = _read_images(root)
    for sector, data in images.items():
        expected = rows[str(sector)]["sha256"]
        if sha256(data) != expected:
            raise BaselineError(
                f"{context}: sector {sector} hash does not match manifest"
            )
    return images, manifest


def load_manifest(root: Path) -> dict[str, Any]:
    root_name = normalize_fingerprint(
        root.name,
        context="baseline directory name",
    )
    return _load_manifest_for_fingerprint(
        root,
        root_name,
        context="baseline",
    )


def load_baseline(root: Path) -> tuple[dict[int, bytes], dict[str, Any]]:
    root_name = normalize_fingerprint(
        root.name,
        context="baseline directory name",
    )
    return _load_baseline_for_fingerprint(
        root,
        root_name,
        context="baseline",
    )

def active_baseline_root() -> Path:
    try:
        active_file = require_private_regular_file(
            ACTIVE,
            context="active baseline pointer",
        )
    except RuntimeError as exc:
        raise BaselineError(
            "no valid active device baseline pointer. "
            "Run `g502x.py setup` or `g502x.py baseline use` after reviewing local state. "
            f"({exc})"
        ) from exc

    payload = json.loads(active_file.read_text(encoding="utf-8"))
    fp = normalize_fingerprint(
        payload.get("fingerprint"),
        context="active baseline fingerprint",
    )
    root = baseline_path(fp)

    if not root.exists():
        candidates = sorted(
            BASELINES.glob(f".previous-{fp}-*"),
            key=lambda p: p.stat().st_mtime_ns,
            reverse=True,
        )
        valid: list[Path] = []
        invalid: list[str] = []
        for candidate in candidates:
            try:
                _load_baseline_for_fingerprint(
                    candidate,
                    fp,
                    context="previous baseline",
                )
            except Exception as exc:
                invalid.append(f"{candidate.name}: {exc}")
                continue
            valid.append(candidate)

        if len(valid) == 1:
            private_commit_dir(valid[0], root)
        elif len(valid) > 1:
            raise BaselineError(
                "active baseline is missing and multiple fully valid previous "
                f"copies exist for {fp}; manual review required"
            )
        elif candidates:
            detail = "; ".join(invalid)
            raise BaselineError(
                "active baseline is missing and no previous copy passed "
                f"integrity validation for {fp}: {detail}"
            )
        else:
            raise BaselineError(
                f"active baseline directory no longer exists: {root}"
            )

    # Do not return an active pointer to unverified local state.
    load_baseline(root)
    return root

def active_baseline() -> tuple[dict[int, bytes], dict[str, Any]]:
    return load_baseline(active_baseline_root())


def active_manifest() -> dict[str, Any]:
    return load_manifest(active_baseline_root())


def active_target() -> tuple[int, int]:
    try:
        manifest = active_manifest()
    except BaselineError:
        return DEFAULT_PID, DEFAULT_INDEX
    transport = manifest.get("transport", {})
    return (
        int(transport.get("pid", DEFAULT_PID)),
        int(transport.get("index", DEFAULT_INDEX)),
    )


def activate(fingerprint_value: str) -> Path:
    fp = normalize_fingerprint(
        fingerprint_value,
        context="baseline fingerprint",
    )
    root = baseline_path(fp)
    if not root.exists():
        raise BaselineError(f"unknown baseline: {fp}")

    # Switching the active pointer is security-sensitive local state. Verify the
    # full manifest + every sector hash before making it authoritative.
    _images, manifest = load_baseline(root)
    manifest_fp = normalize_fingerprint(
        manifest.get("fingerprint"),
        context="baseline manifest fingerprint",
    )
    if manifest_fp != fp:
        raise BaselineError("baseline fingerprint mismatch during activation")

    private_mkdir(HOME)
    private_write_text(
        ACTIVE,
        json.dumps(
            {
                "fingerprint": fp,
                "activated_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    return root

def create_baseline(
    *,
    images: dict[int, bytes],
    descriptor: dict[str, Any],
    device_info: dict[str, Any],
    pid: int,
    index: int,
    replace: bool = False,
    oob_directory: list[tuple[int, int]] | None = None,
    oob_pages: dict[int, bytes] | None = None,
) -> tuple[Path, dict[str, Any]]:
    if sorted(images) != list(range(SECTOR_COUNT)):
        raise BaselineError("setup requires all 16 sectors")

    for sector, data in images.items():
        if len(data) != SECTOR_SIZE:
            raise BaselineError(
                f"sector {sector}: expected {SECTOR_SIZE} bytes"
            )
        health = sector_health(data)
        if not baseline_sector_capture_ok(sector, data):
            requirement = (
                "CRC-valid directory/profile"
                if sector <= 5
                else "255-byte post-profile page; referenced macros must validate"
            )
            raise BaselineError(
                f"sector {sector}: state={health}; expected {requirement}; "
                "refusing baseline capture"
            )

    compat = apply_identity_policy(
        compatibility_class(descriptor, pid, index),
        device_info,
    )
    if compat["architecture"] != "compatible":
        raise BaselineError(
            "device memory geometry is not compatible with the validated "
            f"architecture: {compat['mismatches']}"
        )

    fp = fingerprint(
        descriptor=descriptor,
        device_info=device_info,
        pid=pid,
        index=index,
    )
    root = baseline_path(fp)

    if root.exists() and not replace:
        existing_images, existing_manifest = load_baseline(root)
        if all(existing_images[s] == images[s] for s in range(SECTOR_COUNT)):
            activate(fp)
            return root, existing_manifest
        raise BaselineError(
            f"baseline {fp} already exists but differs. "
            "Use --replace only after reviewing the current device state."
        )

    private_mkdir(HOME)
    private_mkdir(BASELINES)
    stage = private_stage_dir(
        BASELINES,
        prefix=f".partial-{fp}-",
    )

    oob_directory = list(oob_directory or [])
    oob_pages = dict(oob_pages or {})

    try:
        for sector, data in images.items():
            private_write_bytes(stage / f"sector-{sector:02d}.bin", data)

        if oob_pages:
            rom = private_mkdir(stage / "rom")
            for page, data in oob_pages.items():
                if len(data) != SECTOR_SIZE:
                    raise BaselineError(
                        f"ROM page 0x{page:04X} has {len(data)} bytes"
                    )
                private_write_bytes(rom / f"page-{page:04x}.bin", data)

        manifest = {
            "format": "g502x-device-baseline-v1",
            "tool_version": VERSION,
            "fingerprint": fp,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "source": "read-only-device-setup",
            "transport": {
                "pid": int(pid),
                "pid_hex": _hex(pid),
                "index": int(index),
                "index_hex": _hex(index),
            },
            "compatibility": compat,
            "descriptor": {
                key: value
                for key, value in descriptor.items()
                if key != "raw"
            },
            "device": device_info,
            "oob": {
                "directory": [
                    {"page": int(page), "enabled": int(enabled)}
                    for page, enabled in oob_directory
                ],
                "pages": {
                    f"0x{page:04X}": {
                        "sha256": sha256(data),
                        "crc_ok": sector_crc_ok(data),
                    }
                    for page, data in sorted(oob_pages.items())
                },
            },
            "sectors": {
                str(sector): {
                    "sha256": sha256(data),
                    "crc_ok": sector_crc_ok(data),
                    "health": baseline_sector_health(sector, data),
                    "erased": baseline_sector_health(sector, data) == "erased",
                }
                for sector, data in images.items()
            },
        }
        require_complete_sector_hashes(
            manifest,
            context="new baseline manifest",
        )
        private_write_text(
            _manifest_path(stage),
            json.dumps(
                manifest,
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
            + "\n",
        )

        if root.exists():
            private_replace_dir(stage, root)
        else:
            private_commit_dir(stage, root)
    except Exception:
        private_discard_dir(stage)
        raise

    activate(fp)
    return root, manifest


def list_baselines() -> list[dict[str, Any]]:
    if not BASELINES.exists():
        return []
    active_fp = None
    if ACTIVE.exists():
        try:
            active_fp = json.loads(
                ACTIVE.read_text(encoding="utf-8")
            ).get("fingerprint")
        except Exception:
            active_fp = None

    rows = []
    for root in sorted(BASELINES.iterdir()):
        if not root.is_dir() or root.name.startswith("."):
            continue
        try:
            normalize_fingerprint(
                root.name,
                context="baseline directory name",
            )
            manifest = load_manifest(root)
        except Exception:
            continue
        rows.append({
            "fingerprint": manifest.get("fingerprint", root.name),
            "active": root.name == active_fp,
            "device_name": manifest.get("device", {}).get("device_name"),
            "firmware": manifest.get("device", {}).get("firmware"),
            "transport": manifest.get("transport"),
            "compatibility": manifest.get("compatibility"),
            "captured_at": manifest.get("captured_at"),
            "path": str(root),
        })
    return rows


def _public_device_info(device_info: dict[str, Any]) -> dict[str, Any]:
    """Return compatibility evidence without per-unit or opaque unique fields."""
    firmware = []
    for row in device_info.get("firmware") or []:
        firmware.append({
            key: row.get(key)
            for key in (
                "index",
                "entity_type",
                "prefix",
                "version_raw",
                "active",
                "transport_pid",
            )
        })

    out = {
        key: device_info.get(key)
        for key in (
            "device_name",
            "protocol",
            "model_ids",
            "entity_count",
            "transport_flags",
            "extended_model_id",
            "capabilities",
        )
        if key in device_info
    }
    out["firmware"] = firmware
    return out


def _public_oob(oob: dict[str, Any]) -> dict[str, Any]:
    """Expose ROM/OOB availability without stable page-content fingerprints."""
    pages = oob.get("pages") or {}
    return {
        "directory": list(oob.get("directory") or []),
        "page_count": len(pages),
        "pages_available": sorted(str(page) for page in pages),
    }


def _public_sector_health(rows: dict[str, Any]) -> dict[str, Any]:
    return {
        str(sector): {
            "crc_ok": row.get("crc_ok"),
            "health": row.get(
                "health",
                "crc_valid" if row.get("crc_ok") else "invalid",
            ),
            "erased": row.get("erased", False),
        }
        for sector, row in rows.items()
    }


def public_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Privacy-minimized, non-linkable device report from a baseline."""
    return {
        "format": "g502x-device-report-v1",
        "tool_version": manifest.get("tool_version"),
        "transport": manifest.get("transport"),
        "compatibility": manifest.get("compatibility"),
        "descriptor": manifest.get("descriptor"),
        "device": _public_device_info(manifest.get("device", {})),
        "oob": _public_oob(manifest.get("oob", {})),
        "sector_health": _public_sector_health(manifest.get("sectors", {})),
    }


def public_probe_report(probe: dict[str, Any]) -> dict[str, Any]:
    """Create a shareable read-only report without requiring a local baseline."""
    report = {
        "format": "g502x-probe-report-v1",
        "tool_version": probe.get("tool_version"),
        "transport": probe.get("transport"),
        "compatibility": probe.get("compatibility"),
        "descriptor": probe.get("descriptor"),
        "device": _public_device_info(probe.get("device", {})),
        "active_profile": probe.get("active_profile"),
        "read_scope": probe.get("read_scope"),
        "oob": _public_oob(probe.get("oob", {})),
    }
    if "sectors" in probe:
        report["sector_health"] = _public_sector_health(probe.get("sectors", {}))
    return report


PUBLIC_REPORT_TOP_LEVEL = {
    "g502x-probe-report-v1": {
        "format",
        "tool_version",
        "transport",
        "compatibility",
        "descriptor",
        "device",
        "active_profile",
        "read_scope",
        "oob",
        "sector_health",
    },
    "g502x-device-report-v1": {
        "format",
        "tool_version",
        "transport",
        "compatibility",
        "descriptor",
        "device",
        "oob",
        "sector_health",
        "current_state",
    },
}

PUBLIC_REPORT_FORBIDDEN_KEYS = {
    "unit_id",
    "serial",
    "serial_number",
    "fingerprint",
    "sha256",
    "sha256_prefix",
    "raw",
    "raw_sector",
    "raw_sectors",
    "sector_images",
    "sectors",
    "profile_metadata",
    "profile_name",
    "baseline_path",
    "local_path",
    "extra_version",
    "data_b64",
    "raw_b64",
}

PUBLIC_REPORT_STABLE_HEX_RE = re.compile(
    r"^(?:[0-9A-Fa-f]{24}|[0-9A-Fa-f]{64})$"
)


def assert_public_report_safe(payload: dict[str, Any]) -> dict[str, Any]:
    """Fail closed if a supposedly shareable report contains private state."""
    if not isinstance(payload, dict):
        raise BaselineError("public report must be a JSON object")

    fmt = payload.get("format")
    allowed = PUBLIC_REPORT_TOP_LEVEL.get(fmt)
    if allowed is None:
        raise BaselineError(
            f"unsupported public report format: {fmt!r}"
        )

    extra = sorted(set(payload) - allowed)
    if extra:
        raise BaselineError(
            "public report has unexpected top-level field(s): "
            + ", ".join(extra)
        )

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for raw_key, child in value.items():
                key = str(raw_key)
                normalized = key.strip().lower().replace("-", "_")
                if (
                    normalized in PUBLIC_REPORT_FORBIDDEN_KEYS
                    or "fingerprint" in normalized
                    or "serial" in normalized
                    or "sha256" in normalized
                ):
                    raise BaselineError(
                        f"public report contains forbidden field at "
                        f"{path}.{key}"
                    )
                walk(child, f"{path}.{key}")
            return

        if isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")
            return

        if isinstance(value, str) and PUBLIC_REPORT_STABLE_HEX_RE.fullmatch(
            value.strip()
        ):
            raise BaselineError(
                f"public report contains stable hash/fingerprint-like value "
                f"at {path}"
            )

    walk(payload, "$")
    return payload
