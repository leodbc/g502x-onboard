from __future__ import annotations

import json
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

# Supported source layouts may place libs at the project root or one level
# above the runtime package root.
for _candidate in (ROOT, ROOT.parent):
    if (_candidate / "libs").exists():
        if str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
        break

from libs.HidppFeatures import Feature
from libs.LogiHPP20 import LogiHPP20

from . import VERSION
from .baseline import (
    DEFAULT_INDEX,
    DEFAULT_PID,
    HOME as STATE_HOME,
    active_manifest,
    active_target,
    apply_identity_policy,
    baseline_sector_capture_ok,
    baseline_sector_health,
    compatibility_class,
    create_baseline,
    fingerprint,
    manifest_fingerprint_matches,
    probe_read_policy,
    require_complete_sector_hashes,
    require_manifest_fingerprint,
)
from .codec import (
    build_directory,
    sector_crc_ok,
    sector_health,
    sha256,
    validate_directory,
)
from .constants import (
    GLOBAL_MACRO_SECTORS,
    PROFILE_DIRECTORY_ENABLE_OFFSETS,
    PROGRAMMABLE_PROFILES,
    PROGRAMMABLE_SECTORS,
    RECOVERY_SECTORS,
    SAFE_PROFILE,
    SECTOR_COUNT,
    SECTOR_SIZE,
)
from .storage import (
    baseline_map,
    baseline_sector,
    require_baseline,
)
from .host_guard import windows_process_names_matching
from .private_io import (
    private_commit_dir,
    private_discard_dir,
    private_mkdir,
    private_stage_dir,
    normalize_private_component,
    private_write_bytes,
    require_private_directory,
    require_private_regular_file,
    private_write_text,
)

CHECKPOINTS = STATE_HOME / "checkpoints"


def connect(pid: int | None = None, index: int | None = None):
    if pid is None or index is None:
        active_pid, active_index = active_target()
        pid = active_pid if pid is None else int(pid)
        index = active_index if index is None else int(index)
    return LogiHPP20(int(pid), "", "", [int(index)])


def get_descriptor(dev) -> dict[str, Any]:
    raw = dev.call_feature(Feature.onboard_profile, 0, [0])
    if not raw:
        raise RuntimeError("unable to read ONBOARD_PROFILES 0x8100")
    payload = bytes(raw[4:14])
    vals = struct.unpack(">BBBBBBBHB", payload)
    return {
        "raw": bytes(raw),
        "memory_model": vals[0],
        "profile_format": vals[1],
        "macro_format": vals[2],
        "profile_count": vals[3],
        "factory_profiles": vals[4],
        "button_count": vals[5],
        "sector_count": vals[6],
        "sector_size": vals[7],
        "mechanical_layout": vals[8],
    }


def assert_expected_device(desc: dict[str, Any]) -> None:
    compat = compatibility_class(desc, DEFAULT_PID, DEFAULT_INDEX)
    if compat["architecture"] != "compatible":
        errors = [
            f"{key}: expected {row['expected']}, got {row['observed']}"
            for key, row in compat["mismatches"].items()
        ]
        raise RuntimeError(
            "device descriptor does not match the validated G502 X architecture:\n  "
            + "\n  ".join(errors)
        )


def get_device_information(dev) -> dict[str, Any]:
    result: dict[str, Any] = {
        "device_name": None,
        "protocol": None,
        "unit_id": None,
        "model_ids": [],
        "firmware": [],
    }

    try:
        result["device_name"] = dev.get_device_name()
    except Exception:
        pass
    try:
        result["protocol"] = dev.protocol()
    except Exception:
        pass

    try:
        raw = dev.call_feature(0x0003, 0, [0, 0, 0])
    except Exception:
        raw = None

    if not raw or len(raw) < 5:
        return result

    payload = bytes(raw[4:20])
    entity_count = payload[0] if payload else 0
    result["entity_count"] = int(entity_count)

    if len(payload) >= 5:
        result["unit_id"] = payload[1:5].hex().upper()
    if len(payload) >= 7:
        result["transport_flags"] = int(payload[6])
    if len(payload) >= 13:
        result["model_ids"] = [
            f"{int.from_bytes(payload[7:9], 'big'):04X}",
            f"{int.from_bytes(payload[9:11], 'big'):04X}",
            f"{int.from_bytes(payload[11:13], 'big'):04X}",
        ]
    if len(payload) >= 14:
        result["extended_model_id"] = int(payload[13])
    if len(payload) >= 15:
        result["capabilities"] = int(payload[14])

    firmware = []
    for entity in range(int(entity_count)):
        try:
            fw = dev.call_feature(0x0003, 1, [entity, 0, 0])
        except Exception:
            fw = None
        if not fw:
            continue
        p = bytes(fw[4:20])
        if len(p) < 11:
            continue
        prefix = p[1:4].decode("ascii", errors="replace").rstrip("\x00")
        firmware.append({
            "index": entity,
            "entity_type": int(p[0]),
            "prefix": prefix,
            "version_raw": f"{p[4]:02X}.{p[5]:02X}.B{p[6]:02X}{p[7]:02X}",
            "active": bool(p[8] & 0x01),
            "transport_pid": f"{int.from_bytes(p[9:11], 'big'):04X}",
            "extra_version": p[11:16].hex().upper(),
        })
    result["firmware"] = firmware
    return result


def assert_connection_matches_manifest(
    dev,
    manifest: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Verify every fresh/reconnected handle is the exact baseline unit."""
    desc = get_descriptor(dev)
    assert_expected_device(desc)
    info = get_device_information(dev)
    transport = manifest.get("transport", {})
    live_policy = apply_identity_policy(
        compatibility_class(
            desc,
            int(transport.get("pid", DEFAULT_PID)),
            int(transport.get("index", DEFAULT_INDEX)),
        ),
        info,
    )
    if not live_policy.get("write_allowed"):
        raise RuntimeError(
            "connected device no longer matches the locally validated "
            "write target policy; operation remains read-only"
        )
    if not manifest_fingerprint_matches(
        manifest,
        descriptor=desc,
        device_info=info,
    ):
        raise RuntimeError(
            "connected device fingerprint changed during the operation. "
            "Aborting before any further write; reconnect the baseline unit "
            "and run validate/probe before retrying."
        )
    return desc, info


def connect_manifest_unit(manifest: dict[str, Any]):
    """Open the baseline transport and fail closed if a different unit answers."""
    transport = manifest.get("transport", {})
    pid = int(transport.get("pid", DEFAULT_PID))
    index = int(transport.get("index", DEFAULT_INDEX))
    dev = connect(pid, index)
    try:
        assert_connection_matches_manifest(dev, manifest)
    except Exception:
        dev.close()
        raise
    return dev


def read_page(dev, page: int, size: int = SECTOR_SIZE) -> bytes:
    if not 0 <= int(page) <= 0xFFFF:
        raise ValueError(f"invalid memory page: {page}")
    if not 16 <= int(size) <= SECTOR_SIZE:
        raise ValueError(f"invalid page size: {size}")

    data = bytearray(size)
    offset = 0
    seen = set()

    while offset < size:
        if size - offset < 16:
            offset = size - 16
        if offset in seen:
            break
        seen.add(offset)

        result = dev.call_feature(
            Feature.onboard_profile,
            5,
            list(struct.pack(">HH", int(page), offset)),
        )
        if result is None:
            raise RuntimeError(f"failed reading page 0x{int(page):04X}, offset {offset}")

        chunk = bytes(result[4:20])
        if len(chunk) != 16:
            raise RuntimeError(
                f"page 0x{int(page):04X}, offset {offset}: got {len(chunk)} bytes"
            )
        data[offset:offset + 16] = chunk

        if offset + 16 >= size:
            break
        offset += 16

    return bytes(data)


def read_sector(dev, sector: int) -> bytes:
    if not 0 <= sector < SECTOR_COUNT:
        raise ValueError(f"invalid sector: {sector}")
    return read_page(dev, sector, SECTOR_SIZE)


def read_directory_page(dev, page: int) -> list[tuple[int, int]]:
    raw = read_page(dev, page, SECTOR_SIZE)
    entries: list[tuple[int, int]] = []
    for offset in range(0, min(64, SECTOR_SIZE - 3), 4):
        if raw[offset:offset + 2] == b"\xFF\xFF":
            break
        sector = (raw[offset] << 8) | raw[offset + 1]
        enabled = raw[offset + 2]
        entries.append((sector, enabled))
    return entries


def read_oob_pages(dev, descriptor: dict[str, Any]) -> dict[str, Any]:
    count = int(descriptor.get("factory_profiles", 0) or 0)
    if count <= 0:
        return {"directory": [], "pages": {}}

    directory = read_directory_page(dev, 0x0100)
    pages: dict[int, bytes] = {}
    for page, _enabled in directory[:count]:
        if page < 0x0100:
            continue
        pages[page] = read_page(dev, page, SECTOR_SIZE)

    return {
        "directory": directory,
        "pages": pages,
    }


def write_sector(dev, sector: int, data: bytes) -> bytes:
    if len(data) != SECTOR_SIZE:
        raise ValueError(
            f"sector {sector}: expected {SECTOR_SIZE} bytes, got {len(data)}"
        )

    start = dev.call_feature(
        Feature.onboard_profile,
        6,
        list(struct.pack(">HHH", sector, 0, SECTOR_SIZE)),
    )
    if start is None:
        raise RuntimeError(f"WRITE_START failed for sector {sector}")

    for offset in range(0, SECTOR_SIZE, 16):
        chunk = data[offset:offset + 16]
        if len(chunk) < 16:
            chunk += b"\xFF" * (16 - len(chunk))
        result = dev.call_feature(Feature.onboard_profile, 7, list(chunk))
        if result is None:
            raise RuntimeError(
                f"WRITE_DATA failed for sector {sector}, offset {offset}"
            )

    end = dev.call_feature(Feature.onboard_profile, 8)
    print(
        f"  sector {sector:02d} WRITE_END:",
        "ACK" if end is not None else "no ACK; reconciling by readback",
    )

    time.sleep(0.20)
    readback = read_sector(dev, sector)
    if readback != data:
        raise RuntimeError(
            f"READBACK failed for sector {sector}; "
            f"expected={sha256(data)} read={sha256(readback)}"
        )
    return readback


def read_all(
    pid: int | None = None,
    index: int | None = None,
    *,
    manifest: dict[str, Any] | None = None,
) -> dict[int, bytes]:
    if manifest is not None:
        dev = connect_manifest_unit(manifest)
    else:
        dev = connect(pid, index)
    try:
        if manifest is None:
            desc = get_descriptor(dev)
            assert_expected_device(desc)
        return {
            sector: read_sector(dev, sector)
            for sector in range(SECTOR_COUNT)
        }
    finally:
        dev.close()


def get_current_profile(dev) -> int | None:
    data = dev.call_feature(Feature.onboard_profile, 4, [0])
    if data is None or len(data) <= 5:
        return None
    return int.from_bytes(bytes(data[4:6]), "big")


def switch_profile(dev, profile: int) -> None:
    result = dev.call_feature(Feature.onboard_profile, 3, [0, profile, 0])
    time.sleep(0.20)
    current = get_current_profile(dev)
    print("Switch:", "ACK" if result is not None else "no ACK; using readback")
    print(f"Active profile: {current}")
    if current != profile:
        raise RuntimeError(
            f"profile switch not confirmed: expected {profile}, got {current}"
        )


def ghub_processes() -> list[str]:
    return windows_process_names_matching("lghub")


def onboard_memory_manager_processes() -> list[str]:
    return windows_process_names_matching("onboardmemorymanager")


def conflicting_logitech_writer_processes() -> list[str]:
    return sorted(
        set(ghub_processes())
        | set(onboard_memory_manager_processes())
    )


def require_ghub_closed() -> None:
    # Historical name retained for call-site compatibility. The safety
    # interlock now covers both known Logitech onboard-memory writers.
    running = conflicting_logitech_writer_processes()
    if running:
        raise RuntimeError(
            "G HUB and Logitech Onboard Memory Manager must be completely "
            "closed before direct onboard-memory access:\n  "
            + "\n  ".join(running)
        )


def _directory_sanity(data: bytes) -> None:
    if not sector_crc_ok(data):
        raise RuntimeError("Sector 0 CRC is invalid")
    for profile, offset in PROFILE_DIRECTORY_ENABLE_OFFSETS.items():
        value = data[offset]
        if value not in (0, 1):
            raise RuntimeError(
                f"Sector 0 profile {profile} flag is invalid: 0x{value:02X}"
            )
    if data[PROFILE_DIRECTORY_ENABLE_OFFSETS[1]] != 1:
        raise RuntimeError("Profile 1 must be enabled before setup")
    if data[PROFILE_DIRECTORY_ENABLE_OFFSETS[2]] != 1:
        raise RuntimeError("Profile 2 must be enabled before setup")


def probe_device(
    pid: int = DEFAULT_PID,
    index: int = DEFAULT_INDEX,
    *,
    read_sectors: bool = True,
) -> dict[str, Any]:
    dev = connect(pid, index)
    try:
        desc = get_descriptor(dev)
        info = get_device_information(dev)
        compat = apply_identity_policy(
            compatibility_class(desc, pid, index),
            info,
        )
        scope = probe_read_policy(
            compat,
            read_sectors=read_sectors,
        )

        try:
            active = get_current_profile(dev)
            scope["active_profile"] = (
                "read" if active is not None else "unavailable"
            )
        except Exception:
            active = None
            scope["active_profile"] = "unavailable"

        oob = {"directory": [], "pages": {}}
        images: dict[int, bytes] = {}

        if compat["architecture"] == "compatible":
            try:
                oob = read_oob_pages(dev, desc)
                scope["oob"] = "read"
            except Exception:
                scope["oob"] = "unavailable"

            if read_sectors:
                for sector in range(SECTOR_COUNT):
                    try:
                        images[sector] = read_sector(dev, sector)
                    except Exception:
                        # A probe is evidence collection, not a write gate.
                        # Preserve the successful read subset and report scope;
                        # setup remains strict and requires all 16 sectors.
                        continue
                scope["live_sectors"] = (
                    "read_all_16"
                    if len(images) == SECTOR_COUNT
                    else f"partial_{len(images)}_of_{SECTOR_COUNT}"
                )
    finally:
        dev.close()

    result = {
        "tool_version": VERSION,
        "transport": {
            "pid": int(pid),
            "pid_hex": f"0x{int(pid):04X}",
            "index": int(index),
            "index_hex": f"0x{int(index):02X}",
        },
        "compatibility": compat,
        "descriptor": {
            key: value
            for key, value in desc.items()
            if key != "raw"
        },
        "device": info,
        "active_profile": active,
        "read_scope": scope,
        "oob": {
            "directory": [
                {"page": page, "enabled": enabled}
                for page, enabled in oob["directory"]
            ],
            "pages": {
                f"0x{page:04X}": {
                    "sha256": sha256(data),
                    "crc_ok": sector_crc_ok(data),
                    "health": sector_health(data),
                }
                for page, data in oob["pages"].items()
            },
        },
    }
    result["fingerprint"] = fingerprint(
        descriptor=desc,
        device_info=info,
        pid=pid,
        index=index,
    )
    if images:
        result["sectors"] = {
            str(sector): {
                "sha256": sha256(data),
                "crc_ok": sector_crc_ok(data),
                "health": baseline_sector_health(sector, data),
                "erased": baseline_sector_health(sector, data) == "erased",
            }
            for sector, data in sorted(images.items())
        }
    return result

def setup_device(
    pid: int = DEFAULT_PID,
    index: int = DEFAULT_INDEX,
    *,
    replace: bool = False,
) -> tuple[Path, dict[str, Any]]:
    require_ghub_closed()

    dev = connect(pid, index)
    try:
        desc = get_descriptor(dev)
        assert_expected_device(desc)
        info = get_device_information(dev)
        images = {
            sector: read_sector(dev, sector)
            for sector in range(SECTOR_COUNT)
        }
        oob = read_oob_pages(dev, desc)
        active = get_current_profile(dev)
    finally:
        dev.close()

    if active != SAFE_PROFILE:
        raise RuntimeError(
            "Profile 1 SAFE must be active before setup. "
            "Setup is read-only, but the captured recovery baseline must "
            "come from the known SAFE operating state."
        )

    _directory_sanity(images[0])
    for sector, data in images.items():
        if not baseline_sector_capture_ok(sector, data):
            health = sector_health(data)
            raise RuntimeError(
                f"sector {sector} state={health}; setup refuses an "
                "unstable or structurally invalid state"
            )

    # Validate active profile bindings and every referenced macro chain before
    # trusting this state as a baseline. Pass the captured image as its own
    # recovery reference because setup is defining, not comparing, recovery.
    from .validator import validate_images
    setup_report = validate_images(images, baseline=images)
    if not setup_report.ok:
        raise RuntimeError(
            "setup state is structurally invalid:\n  "
            + "\n  ".join(setup_report.errors)
        )

    root, manifest = create_baseline(
        images=images,
        descriptor=desc,
        device_info=info,
        pid=pid,
        index=index,
        replace=replace,
        oob_directory=oob["directory"],
        oob_pages=oob["pages"],
    )
    manifest = dict(manifest)
    manifest["active_profile_at_capture"] = active
    private_write_text(
        root / "manifest.json",
        json.dumps(
            manifest,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
    )
    return root, manifest


def assert_active_device_matches_baseline() -> dict[str, Any]:
    require_baseline()
    manifest = active_manifest()
    transport = manifest.get("transport", {})
    pid = int(transport.get("pid", DEFAULT_PID))
    index = int(transport.get("index", DEFAULT_INDEX))

    compat = manifest.get("compatibility", {})
    if not compat.get("write_allowed"):
        raise RuntimeError(
            "active baseline transport is read-only/untested for writes. "
            "Persistent writes are enabled only on explicitly "
            "hardware-validated transport identities."
        )

    dev = connect(pid, index)
    try:
        assert_connection_matches_manifest(dev, manifest)
    finally:
        dev.close()
    return manifest


def validate_recovery_images(images: dict[int, bytes]) -> None:
    baseline = baseline_map()
    for sector in RECOVERY_SECTORS:
        if images[sector] != baseline[sector]:
            raise RuntimeError(
                f"recovery sector {sector} differs from this device baseline"
            )
    validate_directory(images[0], baseline[0])


def validate_recovery() -> None:
    manifest = assert_active_device_matches_baseline()
    images = read_all(manifest=manifest)
    validate_recovery_images(images)


def ensure_safe_profile(
    manifest: dict[str, Any] | None = None,
) -> None:
    manifest = manifest or assert_active_device_matches_baseline()
    require_ghub_closed()
    dev = connect_manifest_unit(manifest)
    try:
        current = get_current_profile(dev)
        if current != SAFE_PROFILE:
            require_ghub_closed()
            switch_profile(dev, SAFE_PROFILE)
        if get_current_profile(dev) != SAFE_PROFILE:
            raise RuntimeError("Profile 1 SAFE is not active")
    finally:
        dev.close()


def create_backup(
    label: str = "backup",
    manifest: dict[str, Any] | None = None,
) -> Path:
    label = normalize_private_component(
        label,
        context="checkpoint label",
    )
    require_baseline()
    manifest = manifest or assert_active_device_matches_baseline()

    # Capture first. A failed device read must not leave something that looks
    # like a complete checkpoint on disk.
    dev = connect_manifest_unit(manifest)
    try:
        desc = get_descriptor(dev)
        active = get_current_profile(dev)
        images = {
            sector: read_sector(dev, sector)
            for sector in range(SECTOR_COUNT)
        }
    finally:
        dev.close()

    private_mkdir(CHECKPOINTS)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = CHECKPOINTS / f"{label}-{stamp}"
    if out.exists():
        raise RuntimeError(
            f"checkpoint path already exists: {out}; retry after the timestamp changes"
        )

    stage = private_stage_dir(
        CHECKPOINTS,
        prefix=f".partial-{label}-{stamp}-",
    )
    try:
        for sector, data in images.items():
            private_write_bytes(stage / f"sector-{sector:02d}.bin", data)

        base_manifest = manifest
        backup_manifest = {
            "format": "g502x-backup-v1",
            "tool_version": VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "baseline_fingerprint": base_manifest.get("fingerprint"),
            "active_profile": active,
            "descriptor": {
                key: value
                for key, value in desc.items()
                if key != "raw"
            },
            "sectors": {
                str(sector): {
                    "sha256": sha256(data),
                    "crc_ok": sector_crc_ok(data),
                }
                for sector, data in images.items()
            },
        }

        # Manifest-last: its presence means every sector file has already been
        # written and fsynced through private_write_bytes().
        private_write_text(
            stage / "manifest.json",
            json.dumps(backup_manifest, indent=2, sort_keys=True) + "\n",
        )
        return private_commit_dir(stage, out)
    except Exception:
        private_discard_dir(stage)
        raise

def load_backup(path: str | Path) -> tuple[Path, dict[int, bytes], dict[str, Any]]:
    supplied = Path(path).expanduser()
    if supplied.is_symlink():
        raise RuntimeError(
            f"backup root symbolic links are not accepted: {supplied}"
        )
    root = supplied.resolve()
    require_private_directory(root, context="backup")
    manifest_path = require_private_regular_file(
        root / "manifest.json",
        context="backup manifest",
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != "g502x-backup-v1":
        raise RuntimeError("unsupported backup format")

    try:
        rows = require_complete_sector_hashes(
            manifest,
            context="backup manifest",
        )
        active_fp = active_manifest().get("fingerprint")
        require_manifest_fingerprint(
            manifest,
            key="baseline_fingerprint",
            expected=active_fp,
            context="backup manifest",
        )
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc

    images: dict[int, bytes] = {}
    for sector in range(SECTOR_COUNT):
        file = require_private_regular_file(
            root / f"sector-{sector:02d}.bin",
            context=f"backup sector {sector}",
        )
        data = file.read_bytes()
        if len(data) != SECTOR_SIZE:
            raise RuntimeError(
                f"backup sector {sector} has {len(data)} bytes"
            )
        expected = rows[str(sector)]["sha256"]
        if sha256(data) != expected:
            raise RuntimeError(f"backup sector {sector} hash mismatch")
        images[sector] = data

    return root, images, manifest

def _fresh_read_sector(
    sector: int,
    manifest: dict[str, Any],
) -> bytes:
    dev = connect_manifest_unit(manifest)
    try:
        return read_sector(dev, sector)
    finally:
        dev.close()


def fresh_write_sector(
    sector: int,
    target: bytes,
    *,
    label: str | None = None,
    attempts: int = 3,
    manifest: dict[str, Any] | None = None,
) -> None:
    if sector in RECOVERY_SECTORS:
        raise RuntimeError(f"refusing write to recovery sector {sector}")
    if sector not in PROGRAMMABLE_SECTORS:
        raise RuntimeError(f"sector {sector} is outside managed domain")

    manifest = manifest or active_manifest()
    compat = manifest.get("compatibility", {})
    if not compat.get("write_allowed"):
        raise RuntimeError("active baseline does not allow persistent writes")

    label = label or f"Sector {sector}"

    for attempt in range(1, attempts + 1):
        # Re-check the host-side interlock and exact physical unit on EVERY
        # connection/retry, not only once at the beginning of apply/restore.
        require_ghub_closed()
        dev = connect_manifest_unit(manifest)
        before = None
        error: Exception | None = None
        try:
            before = read_sector(dev, sector)
            if before == target:
                print(f"{label}: already correct.")
                return

            require_ghub_closed()
            print(f"{label}: writing...")
            try:
                write_sector(dev, sector, target)
                return
            except RuntimeError as exc:
                error = exc
                print(f"  transport/readback failure: {exc}")
        finally:
            dev.close()

        time.sleep(0.35 * attempt)
        # Reconciliation is read-only but still identity-bound, so a hot-swapped
        # compatible mouse cannot be mistaken for the original target.
        observed = _fresh_read_sector(sector, manifest)

        if observed == target:
            print(
                f"  {label}: committed; fresh byte-for-byte readback OK."
            )
            return

        if before is not None and observed == before:
            if attempt < attempts:
                print(
                    f"  {label}: unchanged after failure; retrying "
                    f"({attempt}/{attempts})..."
                )
                time.sleep(0.5 * attempt)
                continue
            raise RuntimeError(
                f"{label}: remained unchanged after {attempts} attempts; "
                f"last error={error}"
            )

        raise RuntimeError(
            f"{label}: PARTIAL/INDETERMINATE state after write failure; "
            "fresh readback matches neither previous nor target bytes"
        )


def _validate_recovery_fresh(manifest: dict[str, Any]) -> None:
    images = read_all(manifest=manifest)
    validate_recovery_images(images)
    dev = connect_manifest_unit(manifest)
    try:
        if get_current_profile(dev) != SAFE_PROFILE:
            raise RuntimeError("Profile 1 SAFE is not active")
    finally:
        dev.close()


def apply_plan(
    plan: dict[str, Any],
    *,
    expected_baseline_fingerprint: str | None = None,
) -> Path:
    require_ghub_closed()
    require_baseline()
    manifest = assert_active_device_matches_baseline()
    if expected_baseline_fingerprint is not None:
        require_manifest_fingerprint(
            manifest,
            key="fingerprint",
            expected=expected_baseline_fingerprint,
            context="apply plan baseline",
        )

    current = read_all(manifest=manifest)
    validate_recovery_images(current)

    dev = connect_manifest_unit(manifest)
    try:
        if get_current_profile(dev) != SAFE_PROFILE:
            raise RuntimeError("Profile 1 SAFE must be active before apply")
    finally:
        dev.close()

    backup = create_backup("pre-apply", manifest)
    baseline = baseline_map()

    staging = build_directory(baseline[0], {1, 2})
    fresh_write_sector(0, staging, label="Sector 0 staging", manifest=manifest)
    _validate_recovery_fresh(manifest)

    for sector in GLOBAL_MACRO_SECTORS:
        fresh_write_sector(
            sector,
            plan["global_store"]["sector_images"][sector],
            label=f"Global macro sector {sector}",
            manifest=manifest,
        )
        _validate_recovery_fresh(manifest)

    for profile in (3, 4, 5, 2):
        fresh_write_sector(
            profile,
            plan["profiles"][profile]["profile"],
            label=f"Profile {profile}",
            manifest=manifest,
        )
        _validate_recovery_fresh(manifest)

    fresh_write_sector(0, plan["directory"], label="Sector 0 final directory", manifest=manifest)
    _validate_recovery_fresh(manifest)

    final = read_all(manifest=manifest)
    if final[0] != plan["directory"]:
        raise RuntimeError("final Sector 0 differs from plan")
    for sector in GLOBAL_MACRO_SECTORS:
        if final[sector] != plan["global_store"]["sector_images"][sector]:
            raise RuntimeError(f"final macro sector {sector} differs from plan")
    for profile in PROGRAMMABLE_PROFILES:
        if final[profile] != plan["profiles"][profile]["profile"]:
            raise RuntimeError(f"final Profile {profile} differs from plan")

    return backup


def restore_backup(path: str | Path) -> Path:
    require_ghub_closed()
    require_baseline()
    manifest = assert_active_device_matches_baseline()
    root, target, _manifest = load_backup(path)

    from .validator import validate_images

    target_report = validate_images(target)
    if not target_report.ok:
        raise RuntimeError(
            "backup is not a structurally valid restorable state:\n  "
            + "\n  ".join(target_report.errors)
        )

    baseline = baseline_map()
    for sector in RECOVERY_SECTORS:
        if target[sector] != baseline[sector]:
            raise RuntimeError(
                f"backup recovery sector {sector} differs from device baseline; "
                "restore writes programmable state only"
            )
    validate_directory(target[0], baseline[0])

    current = read_all(manifest=manifest)
    validate_recovery_images(current)
    ensure_safe_profile(manifest)

    safety_backup = create_backup("pre-restore", manifest)

    staging = build_directory(baseline[0], {1, 2})
    fresh_write_sector(
        0,
        staging,
        label="Sector 0 staging",
        manifest=manifest,
    )
    _validate_recovery_fresh(manifest)

    for sector in GLOBAL_MACRO_SECTORS:
        fresh_write_sector(
            sector,
            target[sector],
            label=f"Restore macro sector {sector}",
            manifest=manifest,
        )
        _validate_recovery_fresh(manifest)

    for profile in (3, 4, 5, 2):
        fresh_write_sector(
            profile,
            target[profile],
            label=f"Restore Profile {profile}",
            manifest=manifest,
        )
        _validate_recovery_fresh(manifest)

    fresh_write_sector(0, target[0], label="Restore Sector 0", manifest=manifest)
    _validate_recovery_fresh(manifest)

    final = read_all(manifest=manifest)
    bad = [
        sector
        for sector in PROGRAMMABLE_SECTORS
        if final[sector] != target[sector]
    ]
    if bad:
        raise RuntimeError(f"restore incomplete; differing sectors: {bad}")

    return safety_backup


def restore_baseline() -> Path:
    require_ghub_closed()
    require_baseline()
    manifest = assert_active_device_matches_baseline()
    ensure_safe_profile(manifest)
    current = read_all(manifest=manifest)
    validate_recovery_images(current)

    safety_backup = create_backup("pre-baseline-restore", manifest)
    target = baseline_map()

    staging = build_directory(target[0], {1, 2})
    fresh_write_sector(0, staging, label="Baseline Sector 0 staging", manifest=manifest)
    _validate_recovery_fresh(manifest)

    for sector in GLOBAL_MACRO_SECTORS:
        fresh_write_sector(
            sector,
            target[sector],
            label=f"Baseline macro sector {sector}",
            manifest=manifest,
        )
        _validate_recovery_fresh(manifest)

    for profile in (3, 4, 5, 2):
        fresh_write_sector(
            profile,
            target[profile],
            label=f"Baseline Profile {profile}",
            manifest=manifest,
        )
        _validate_recovery_fresh(manifest)

    fresh_write_sector(0, target[0], label="Baseline Sector 0", manifest=manifest)
    _validate_recovery_fresh(manifest)

    final = read_all(manifest=manifest)
    bad = [
        sector
        for sector in PROGRAMMABLE_SECTORS
        if final[sector] != target[sector]
    ]
    if bad:
        raise RuntimeError(f"baseline reset incomplete: {bad}")
    validate_recovery_images(final)
    return safety_backup
