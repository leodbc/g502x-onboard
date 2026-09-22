from __future__ import annotations

import argparse
from datetime import datetime
import json
import sys
from pathlib import Path

from . import VERSION
from .baseline import (
    DEFAULT_INDEX,
    DEFAULT_PID,
    HOME as STATE_HOME,
    activate,
    active_manifest,
    assert_public_report_safe,
    list_baselines,
    public_manifest,
    public_probe_report,
)
from .codec import (
    build_plan,
    plan_json,
    profile_display_metadata,
    with_crc,
)
from .config import ConfigError, load_config
from .constants import (
    CAPABILITIES,
    GLOBAL_MACRO_SECTORS,
    GLOBAL_PAYLOAD_CAPACITY,
    GLOBAL_RAW_CAPACITY,
    PAGE_DATA_SIZE,
    PROFILE_DIRECTORY_ENABLE_OFFSETS,
    PROGRAMMABLE_PROFILES,
    RECOVERY_SECTORS,
    SAFE_PROFILE,
    SECTOR_COUNT,
    SECTOR_SIZE,
)
from .storage import ROOT, baseline_map
from .private_io import atomic_local_write_text, exclusive_operation_lock, private_mkdir, private_write_text
from .validator import (
    export_state,
    inspect_rows,
    public_state_summary,
    state_summary,
    validate_device,
)


SCHEMA_PATH = ROOT / "g502x_onboard" / "config.schema.json"
OPERATION_LOCK = STATE_HOME / "operation.lock"


def _int_auto(value: str) -> int:
    return int(str(value), 0)


def _load_plan(path: str):
    config_path, config = load_config(path)
    try:
        baseline = baseline_map()
    except Exception as exc:
        raise RuntimeError(
            "planning for a physical device requires an active local baseline. "
            "Run probe and then setup first."
        ) from exc
    return config_path, config, build_plan(config, baseline)


def _print_plan(path: Path, plan: dict) -> None:
    print(f"G502 X - PLAN {VERSION}")
    print("=" * 40)
    print(f"Config: {path}")
    print("Runtime: autonomous onboard HID / Macro VM")
    print(
        f"Global store: {GLOBAL_RAW_CAPACITY} raw / "
        f"{GLOBAL_PAYLOAD_CAPACITY} VM payload bytes"
    )
    print("Enabled profiles: " + ", ".join(map(str, plan["enabled_profiles"])))
    if plan["warnings"]:
        print()
        for warning in plan["warnings"]:
            print("WARNING:", warning)
    print()

    for profile in PROGRAMMABLE_PROFILES:
        p = plan["profiles"][profile]
        print(f"PROFILE {profile}")
        print("-" * 36)
        if p["disabled"]:
            print("disabled -> local baseline template")
            print()
            continue
        meta = profile_display_metadata(p["profile"])
        print(
            f"name={meta['name']!r} polling={meta['polling_rate_hz']}Hz "
            f"default_dpi={meta['default_dpi']} shift_dpi={meta['shift_dpi']}"
        )
        print("dpi=" + ",".join(map(str, meta["dpi"])))
        for route, item in sorted(p["results"].items()):
            if item["kind"] == "direct":
                print(
                    f"{route:20} DIRECT "
                    f"{item['binding'].hex(' ').upper()} "
                    f"{item['description']}"
                )
            else:
                print(
                    f"{route:20} MACRO "
                    f"s{item['sector']}:0x{item['offset']:02X} "
                    f"src={item['source_length']}B "
                    f"fragments={len(item['fragments'])} "
                    f"{item['description']}"
                )
        print()

    store = plan["global_store"]
    print("GLOBAL STORE")
    print("-" * 36)
    print(f"Unique macros:   {len(plan['unique_macros'])}")
    print(f"Source bytes:    {store['source_bytes']}")
    print(f"Allocated bytes: {store['allocated_bytes']}")
    print(f"JUMP overhead:   {store['jump_overhead']}")
    print(f"Fragmentation:   {store['fragmentation_waste']}")
    print(f"Raw free:        {store['raw_free_bytes']}")
    print(f"Usable free:     {store['usable_free_bytes']}")
    for index, allocation in sorted(store["allocations"].items()):
        routes = ", ".join(
            f"P{p}:{route}" for p, route in allocation["routes"]
        )
        print(
            f"macro#{index}: s{allocation['sector']}:0x{allocation['offset']:02X} "
            f"src={allocation['source_length']}B "
            f"fragments={len(allocation['fragments'])} -> {routes}"
        )
        for fragment in allocation["fragments"]:
            jump = fragment["jump"]
            suffix = (
                f" JUMP->s{jump['sector']}:0x{jump['offset']:02X}"
                if jump
                else " END"
            )
            print(
                f"  s{fragment['sector']}:0x{fragment['offset']:02X}.."
                f"0x{fragment['end'] - 1:02X}{suffix}"
            )


def _offline_fixture() -> dict[int, bytes]:
    images = {}
    for sector in range(SECTOR_COUNT):
        page = bytearray([0xFF] * SECTOR_SIZE)
        images[sector] = with_crc(bytes(page))

    directory = bytearray(images[0])
    for profile, offset in PROFILE_DIRECTORY_ENABLE_OFFSETS.items():
        directory[offset] = 1 if profile in (1, 2) else 0
    images[0] = with_crc(bytes(directory))

    for profile in (1, 2, 3, 4, 5):
        page = bytearray(images[profile])
        page[0] = 0x01
        page[1] = 0
        page[2] = 0
        for i, dpi in enumerate((800, 1600, 3200, 0, 0)):
            page[3 + i * 2:5 + i * 2] = int(dpi).to_bytes(2, "little")
        images[profile] = with_crc(bytes(page))

    return images


def cmd_selftest(_args):
    config_path, config = load_config(ROOT / "examples" / "basic.json")
    fixture = _offline_fixture()
    plan = build_plan(config, fixture)

    if not plan["profiles"][2]["results"]:
        raise RuntimeError("selftest: example produced no Profile 2 routes")
    for sector, image in plan["global_store"]["sector_images"].items():
        from .codec import sector_crc_ok
        if not sector_crc_ok(image):
            raise RuntimeError(
                f"selftest: macro page CRC invalid in sector {sector}"
            )

    rendered_a = plan_json(plan)
    rendered_b = plan_json(build_plan(config, fixture))
    if rendered_a != rendered_b:
        raise RuntimeError("selftest: plan output is not deterministic")

    print(f"G502 X {VERSION} OFFLINE SELFTEST: PASS")
    try:
        config_label = config_path.relative_to(ROOT).as_posix()
    except ValueError:
        config_label = config_path.name
    print(f"  config: {config_label}")
    print("  schema/config validation: PASS")
    print("  compiler + global allocator: PASS")
    print("  macro-page CRC generation: PASS")
    print("  deterministic plan JSON: PASS")
    print("  physical device/baseline required: NO")
    print("  device writes: NONE")


def cmd_schema(args):
    if args.path:
        print(SCHEMA_PATH)
    else:
        print(SCHEMA_PATH.read_text(encoding="utf-8"), end="")


def cmd_probe(args):
    from .device import probe_device

    with exclusive_operation_lock(OPERATION_LOCK):
        result = probe_device(
            pid=args.pid,
            index=args.index,
            read_sectors=not args.no_sectors,
        )
    if args.json and args.private:
        raise ValueError("probe --json is already private; do not combine with --private")
    if args.json:
        print(
            "WARNING: probe --json is PRIVATE diagnostics and contains "
            "per-unit identifiers/fingerprints. Use `report probe` for sharing.",
            file=sys.stderr,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return

    print(f"G502 X - READ-ONLY PROBE {VERSION}")
    print("=" * 40)
    print(
        f"Transport: PID {result['transport']['pid_hex']} "
        f"index {result['transport']['index_hex']}"
    )
    print(f"Device: {result['device'].get('device_name')}")
    print(f"Protocol: {result['device'].get('protocol')}")
    print(f"Active profile: {result.get('active_profile')}")
    if args.private:
        print(f"Fingerprint: {result['fingerprint']}")
    else:
        identity = result.get("compatibility", {}).get("identity")
        label = "unit-bound / redacted" if identity == "unit-bound" else "redacted"
        print(f"Identity: {label}")
    print(
        "Compatibility: "
        f"architecture={result['compatibility']['architecture']} "
        f"transport={result['compatibility']['transport']} "
        f"write_allowed={result['compatibility']['write_allowed']}"
    )
    print("Descriptor:")
    for key, value in result["descriptor"].items():
        print(f"  {key}: {value}")
    firmware = result["device"].get("firmware") or []
    if firmware:
        print("Firmware entities:")
        for row in firmware:
            print(
                f"  #{row['index']} type={row['entity_type']} "
                f"{row['prefix']} {row['version_raw']} "
                f"active={row['active']} pid={row['transport_pid']}"
            )
    scope = result.get("read_scope") or {}
    if scope:
        print(
            "Read scope: "
            f"live_sectors={scope.get('live_sectors')} "
            f"oob={scope.get('oob')} "
            f"active_profile={scope.get('active_profile')}"
        )
    if "sectors" in result:
        def _health(row):
            return row.get(
                "health",
                "crc_valid" if row.get("crc_ok") else "invalid",
            )

        invalid = [
            sector
            for sector, row in result["sectors"].items()
            if _health(row) == "invalid"
        ]
        erased = [
            sector
            for sector, row in result["sectors"].items()
            if _health(row) == "erased"
        ]
        count = len(result["sectors"])
        prefix = "" if count == SECTOR_COUNT else f"partial {count}/{SECTOR_COUNT}; "
        if invalid:
            summary = prefix + "INVALID: " + ", ".join(invalid)
        elif erased:
            summary = (
                prefix + "stable; erased page(s): " + ", ".join(erased)
                + "; remaining read sectors CRC-valid"
            )
        else:
            summary = prefix + f"all {count} read sectors CRC-valid"
        print("Sector health:", summary)
    print()
    print("READ ONLY. Nothing was written and no baseline was changed.")


def cmd_report_probe(args):
    from .device import probe_device

    with exclusive_operation_lock(OPERATION_LOCK):
        result = probe_device(
            pid=args.pid,
            index=args.index,
            read_sectors=True,
        )
    report = public_probe_report(result)
    assert_public_report_safe(report)
    out = Path(args.path).resolve()
    atomic_local_write_text(
        out,
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    )
    print(f"Shareable probe report: {out.name}")
    print(
        "No unit ID, serial number, private fingerprint, stable sector hashes, "
        "raw sectors, or user profile names are included."
    )


def cmd_setup(args):
    from .device import setup_device

    print("SETUP IS READ-ONLY TO THE MOUSE.")
    print("Profile 1 SAFE must already be active.")
    print(
        "The current onboard state becomes this unit's local safety/template "
        "baseline. Capture after restoring the known default/safety state you "
        "want the compiler to inherit."
    )
    if args.replace:
        phrase = input("Type exactly REPLACE LOCAL BASELINE: ").strip()
        if phrase != "REPLACE LOCAL BASELINE":
            print("Cancelled.")
            return

    with exclusive_operation_lock(OPERATION_LOCK):
        root, manifest = setup_device(
            pid=args.pid,
            index=args.index,
            replace=args.replace,
        )
    print()
    print("LOCAL DEVICE BASELINE READY.")
    if args.private:
        print(f"Path: {root}")
        print(f"Fingerprint: {manifest['fingerprint']}")
    else:
        print("Identity: unit-bound / redacted")
        print("Storage: private G502X_HOME baseline")
    compat = manifest["compatibility"]
    print(
        f"Architecture: {compat['architecture']}  "
        f"Transport: {compat['transport']}  "
        f"Writes enabled: {compat['write_allowed']}"
    )
    if not compat["write_allowed"]:
        print(
            "This baseline is read-only for now. Create a shareable device report "
            "before enabling writes on an untested transport."
        )


def cmd_baseline_list(args):
    rows = list_baselines()
    if args.json:
        if args.private:
            payload = rows
        else:
            payload = [
                {
                    key: value
                    for key, value in row.items()
                    if key != "fingerprint"
                }
                for row in rows
            ]
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    if not rows:
        print("No local baselines. Run setup first.")
        return
    for row in rows:
        marker = "*" if row["active"] else " "
        transport = row.get("transport") or {}
        identity = row["fingerprint"] if args.private else "<redacted>"
        print(
            f"{marker} {identity} "
            f"{row.get('device_name') or '?'} "
            f"PID={transport.get('pid_hex')} "
            f"index={transport.get('index_hex')} "
            f"{row.get('compatibility', {}).get('transport')}"
        )

def cmd_baseline_show(args):
    manifest = active_manifest()
    output = manifest if args.private else public_manifest(manifest)
    print(json.dumps(output, indent=2, sort_keys=True, ensure_ascii=False))


def cmd_baseline_use(args):
    with exclusive_operation_lock(OPERATION_LOCK):
        root = activate(args.fingerprint)
    if args.private:
        print(f"Active baseline: {root}")
    else:
        print("Active baseline switched in private local state.")


def cmd_report_check(args):
    path = Path(args.path).resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert_public_report_safe(payload)
    print("PUBLIC REPORT CHECK: PASS")
    print(f"Format: {payload['format']}")
    print(f"File: {path.name}")


def cmd_report_device(args):
    if args.state:
        with exclusive_operation_lock(OPERATION_LOCK):
            manifest = active_manifest()
            images, validation = validate_device()
            report = public_manifest(manifest)
            report["current_state"] = public_state_summary(
                images,
                validation,
            )
    else:
        report = public_manifest(active_manifest())

    assert_public_report_safe(report)
    out = Path(args.path).resolve()
    atomic_local_write_text(
        out,
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    )
    print(f"Shareable device report: {out.name}")
    print(
        "No unit ID, serial number, private fingerprint, stable sector hashes, "
        "raw sectors, or user profile names are included."
    )

def cmd_smoke_readonly(args):
    from .device import (
        assert_active_device_matches_baseline,
        create_backup,
        load_backup,
        probe_device,
        require_ghub_closed,
    )

    out = Path(args.report).resolve()
    with exclusive_operation_lock(OPERATION_LOCK):
        require_ghub_closed()
        manifest = assert_active_device_matches_baseline()

        probe = probe_device(
            pid=int(manifest["transport"]["pid"]),
            index=int(manifest["transport"]["index"]),
            read_sectors=True,
        )
        probe_report = public_probe_report(probe)
        assert_public_report_safe(probe_report)

        compat = probe_report.get("compatibility") or {}
        scope = probe_report.get("read_scope") or {}
        if compat.get("architecture") != "compatible":
            raise RuntimeError(
                "read-only smoke requires compatible architecture"
            )
        if compat.get("transport") != "tested":
            raise RuntimeError(
                "read-only smoke requires the tested transport"
            )
        if compat.get("write_allowed") is not True:
            raise RuntimeError(
                "read-only smoke requires the exact unit/firmware write policy "
                "to remain authorized before release freeze"
            )
        if scope.get("live_sectors") != "read_all_16":
            raise RuntimeError(
                "read-only smoke requires a complete 16-sector probe; "
                f"got {scope.get('live_sectors')!r}"
            )

        health = probe_report.get("sector_health") or {}
        if len(health) != SECTOR_COUNT:
            raise RuntimeError(
                "read-only smoke probe did not report all 16 sector health rows"
            )
        invalid = [
            sector
            for sector, row in health.items()
            if (row or {}).get("health") == "invalid"
        ]
        if invalid:
            raise RuntimeError(
                "read-only smoke probe found invalid sector(s): "
                + ", ".join(map(str, invalid))
            )
        if probe_report.get("active_profile") != SAFE_PROFILE:
            raise RuntimeError(
                "read-only smoke requires Profile 1 SAFE to already be active; "
                "no automatic profile switch is performed by this gate"
            )

        # Re-check the external host interlock and exact unit between physical
        # snapshots. The OS lock only serializes g502x processes; it cannot
        # prevent G HUB from starting or the physical mouse from being swapped.
        require_ghub_closed()
        manifest = assert_active_device_matches_baseline()
        images, validation = validate_device()
        if not validation.ok:
            raise RuntimeError(
                "read-only smoke validate failed:\n  "
                + "\n  ".join(validation.errors)
            )

        require_ghub_closed()
        manifest = assert_active_device_matches_baseline()
        checkpoint = create_backup(
            args.label,
            manifest=manifest,
        )
        _checkpoint_root, checkpoint_images, _checkpoint_manifest = load_backup(
            checkpoint
        )
        if checkpoint_images != images:
            raise RuntimeError(
                "read-only smoke checkpoint does not exactly match the "
                "validated sector snapshot"
            )

        report = public_manifest(manifest)
        report["current_state"] = public_state_summary(
            images,
            validation,
        )
        assert_public_report_safe(report)
        atomic_local_write_text(
            out,
            json.dumps(
                report,
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
            + "\n",
        )

        # Parse the emitted artifact again from disk. This intentionally checks
        # the actual file handed to a maintainer, not only the in-memory object.
        emitted = json.loads(out.read_text(encoding="utf-8"))
        assert_public_report_safe(emitted)

    compat = probe_report.get("compatibility") or {}
    descriptor = probe_report.get("descriptor") or {}
    print(f"G502 X - READ-ONLY SMOKE {VERSION}")
    print("=" * 40)
    print("Result: PASS")
    print("Mouse writes: NONE")
    print("G HUB: closed")
    print("Identity: unit-bound / redacted")
    print(
        "Compatibility: "
        f"architecture={compat.get('architecture')} "
        f"transport={compat.get('transport')}"
    )
    print(
        "Descriptor: "
        f"profile_format={descriptor.get('profile_format')} "
        f"macro_format={descriptor.get('macro_format')} "
        f"sectors={descriptor.get('sector_count')}x"
        f"{descriptor.get('sector_size')}"
    )
    print(
        "Validation: PASS; "
        f"enabled={','.join(map(str, validation.enabled_profiles)) or '-'}; "
        f"macro_starts={len(validation.macro_starts)}"
    )
    print("Sector health: no invalid sectors")
    print(f"Checkpoint: {checkpoint.name} (private state)")
    print("Checkpoint equality: PASS")
    print(f"Shareable device report: {out.name}")
    print("Report privacy check: PASS")


def cmd_plan(args):
    with exclusive_operation_lock(OPERATION_LOCK):
        path, _config, plan = _load_plan(args.config)
    if args.json:
        rendered = plan_json(plan)
        if args.json == "-":
            print(rendered, end="")
        else:
            out = Path(args.json).resolve()
            atomic_local_write_text(out, rendered)
            print(f"Plan JSON: {out.name}")
            print(
                "Plan JSON is local configuration output and may contain "
                "user-authored bindings/macros; do not treat it as a "
                "privacy-minimized shareable report."
            )
    else:
        _print_plan(path, plan)
    print()
    print("DRY RUN. No device writes performed.")


def cmd_capacity(args):
    print(f"G502 X - CAPACITY {VERSION}")
    print(
        f"Global store: {GLOBAL_RAW_CAPACITY} raw / "
        f"{GLOBAL_PAYLOAD_CAPACITY} VM payload bytes"
    )
    if args.config:
        path, _config, plan = _load_plan(args.config)
        store = plan["global_store"]
        print(f"Config: {path}")
        print(f"Source bytes:    {store['source_bytes']}")
        print(f"Allocated bytes: {store['allocated_bytes']}")
        print(f"JUMP overhead:   {store['jump_overhead']}")
        print(f"Fragmentation:   {store['fragmentation_waste']}")
        print(f"Raw free:        {store['raw_free_bytes']}")
        print(f"Usable free:     {store['usable_free_bytes']}")


def cmd_apply(args):
    from .device import apply_plan

    # Planning inherits bytes from the active local baseline. Bind the visible
    # plan to that exact baseline so a baseline-use in another process while the
    # human reviews the plan cannot silently change the template underneath it.
    with exclusive_operation_lock(OPERATION_LOCK):
        planned_baseline_fp = active_manifest()["fingerprint"]
        path, config, plan = _load_plan(args.config)

    _print_plan(path, plan)
    print()
    print("Managed domain: Sector 0, Profiles 2-5, macro sectors 8-15.")
    print("Profile 1 + recovery sectors 6/7 are immutable.")
    phrase = input("Type exactly APPLY CONFIG: ").strip()
    if phrase != "APPLY CONFIG":
        print("Cancelled.")
        return

    with exclusive_operation_lock(OPERATION_LOCK):
        current_baseline_fp = active_manifest()["fingerprint"]
        if current_baseline_fp != planned_baseline_fp:
            raise RuntimeError(
                "active baseline changed after the plan was compiled; "
                "refusing to apply stale inherited bytes. Re-run plan/apply."
            )

        backup = apply_plan(
            plan,
            expected_baseline_fingerprint=planned_baseline_fp,
        )
        _images, report = validate_device()
        if not report.ok:
            raise RuntimeError(
                "post-apply validation failed:\n  " + "\n  ".join(report.errors)
            )

        private_write_text(
            backup / "intended-plan.json",
            plan_json(plan),
        )
        private_write_text(
            backup / "source-config.normalized.json",
            json.dumps(config, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        )

    print()
    print("CONFIG APPLIED AND FULLY VALIDATED.")
    print("Enabled: " + ", ".join(map(str, report.enabled_profiles)))
    print(f"Pre-apply backup: {backup.name} (private state)")
    print("Profile 1 SAFE remained active.")

def cmd_validate(args):
    with exclusive_operation_lock(OPERATION_LOCK):
        images, report = validate_device()
    if args.json:
        summary = (
            state_summary(images, report)
            if args.private
            else public_state_summary(images, report)
        )
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"G502 X - VALIDATE {VERSION}")
        print("=" * 40)
        print("Result:", "PASS" if report.ok else "FAIL")
        print("Enabled: " + ", ".join(map(str, report.enabled_profiles)))
        print(f"Referenced macro starts: {len(report.macro_starts)}")
        for warning in report.warnings:
            print("WARNING:", warning)
        for error in report.errors:
            print("ERROR:", error)
    if not report.ok:
        raise SystemExit(1)


def cmd_status(args):
    from .device import (
        assert_active_device_matches_baseline,
        connect_manifest_unit,
        get_current_profile,
        get_descriptor,
    )

    with exclusive_operation_lock(OPERATION_LOCK):
        manifest = assert_active_device_matches_baseline()
        images, report = validate_device()
        summary = (
            state_summary(images, report)
            if args.private
            else public_state_summary(images, report)
        )

        dev = connect_manifest_unit(manifest)
        try:
            desc = get_descriptor(dev)
            active = get_current_profile(dev)
        finally:
            dev.close()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return

    print("G502 X LIGHTSPEED")
    print("=" * 40)
    print(f"Tool version: {VERSION}")
    print(f"Active profile: {active}")
    print(f"Profile format: {desc['profile_format']}")
    print(f"Macro format: {desc['macro_format']}")
    print(
        f"Global store: {GLOBAL_RAW_CAPACITY} raw / "
        f"{GLOBAL_PAYLOAD_CAPACITY} payload bytes"
    )
    print()
    print("RECOVERY")
    for sector in RECOVERY_SECTORS:
        print(
            f"  sector {sector:02d}: "
            f"{'OK' if summary['recovery'][str(sector)] else 'CHANGED'}"
        )
    print("  enabled: " + ", ".join(map(str, report.enabled_profiles)))
    print()
    print("PROFILES")
    for profile in PROGRAMMABLE_PROFILES:
        row = summary["profiles"][str(profile)]
        if args.private:
            meta = row["metadata"]
            detail = (
                f"name={meta['name']!r} "
                f"polling={meta['polling_rate_hz']}Hz"
            )
        else:
            detail = "metadata=<redacted>"
        print(
            f"  P{profile}: "
            f"{'ENABLED' if row['enabled'] else 'disabled':8} "
            f"CRC={'OK' if row['crc_ok'] else 'INVALID':7} "
            f"{detail}"
        )
    print()
    print("GLOBAL MACRO STORE")
    for sector in GLOBAL_MACRO_SECTORS:
        row = summary["macro_pages"][str(sector)]
        health = row.get(
            "health",
            "crc_valid" if row["crc_ok"] else "invalid",
        )
        print(
            f"  s{sector}: state={health:9} "
            f"payload_non-FF={row['payload_non_ff']:3d} "
            f"high-water={row['high_water']:3d}/{PAGE_DATA_SIZE}"
        )
    print()
    print("Structural validation:", "PASS" if report.ok else "FAIL")


def cmd_inspect(args):
    with exclusive_operation_lock(OPERATION_LOCK):
        images, report = validate_device()
    if not report.ok:
        raise RuntimeError(
            "device state is invalid; run validate:\n  "
            + "\n  ".join(report.errors)
        )
    rows = inspect_rows(images, report)
    if not args.private:
        rows = [
            {
                **row,
                "instructions": [
                    {
                        key: value
                        for key, value in ins.items()
                        if key != "description"
                    }
                    for ins in row["instructions"]
                ],
            }
            for row in rows
        ]
    if args.json:
        print(json.dumps(rows, indent=2, sort_keys=True))
        return

    print(f"G502 X - INSPECT {VERSION}")
    print("=" * 40)
    print("Enabled: " + ", ".join(map(str, report.enabled_profiles)))
    print(f"Macro starts referenced: {len(rows)}")
    print()
    for row in rows:
        start = row["start"]
        print(
            f"s{start['sector']}:0x{start['offset']:02X} <- "
            + ", ".join(row["routes"])
        )
        for ins in row["instructions"]:
            suffix = (
                f"  {ins['description']}"
                if args.private
                else f"  opcode=0x{ins['opcode']:02X} size={ins['size']}"
            )
            print(
                f"  s{ins['sector']}:0x{ins['offset']:02X}"
                f"{suffix}"
            )
        print()


def cmd_debug_export(args):
    print(
        "PRIVATE DIAGNOSTIC EXPORT. Do not attach this file to a public issue; "
        "it contains profile names, bindings and macro bytecode."
    )
    if args.raw:
        print(
            "RAW MODE ENABLED: all 16 raw sectors will also be included "
            "as base64."
        )

    with exclusive_operation_lock(OPERATION_LOCK):
        images, report = validate_device()
        payload = export_state(
            images,
            report=report,
            include_raw=args.raw,
        )

    if args.path:
        out = Path(args.path).expanduser().resolve()
        try:
            out.relative_to(ROOT.resolve())
            inside_repo_surface = True
        except ValueError:
            inside_repo_surface = False
        if inside_repo_surface and not args.allow_repo_output:
            raise RuntimeError(
                "PRIVATE export path is inside the project tree. "
                "Use the default private G502X_HOME location, choose another "
                "path, or pass --allow-repo-output explicitly."
            )
        atomic_local_write_text(
            out,
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
            + "\n",
        )
        location = out.name
    else:
        export_dir = private_mkdir(STATE_HOME / "exports")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        out = export_dir / f"debug-export-{stamp}.json"
        private_write_text(
            out,
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
            + "\n",
        )
        location = f"{out.name} (private state)"

    print(f"State export: {location}")
    if args.raw:
        print("Raw sectors included: this file may contain device-specific state.")

def cmd_profile(args):
    from .device import (
        assert_active_device_matches_baseline,
        connect_manifest_unit,
        require_ghub_closed,
        switch_profile,
        validate_recovery,
    )

    raw = str(args.target).lower()
    target = SAFE_PROFILE if raw == "safe" else int(raw)
    if target not in (1, 2, 3, 4, 5):
        raise ValueError("profile must be safe/1/2/3/4/5")

    phrase = (
        "BACK TO SAFE"
        if target == SAFE_PROFILE
        else f"ENTER PROFILE {target}"
    )
    if input(f"Type exactly {phrase}: ").strip() != phrase:
        print("Cancelled.")
        return

    with exclusive_operation_lock(OPERATION_LOCK):
        require_ghub_closed()
        manifest = assert_active_device_matches_baseline()

        if target == SAFE_PROFILE:
            validate_recovery()
        else:
            _images, report = validate_device()
            if not report.ok:
                raise RuntimeError(
                    "refusing programmable profile switch because validate failed:\n  "
                    + "\n  ".join(report.errors)
                )
            if target not in report.enabled_profiles:
                raise RuntimeError(f"Profile {target} is disabled")

        # The confirmation prompt is a race boundary: after it, all checks and
        # the volatile switch run under the same per-user hardware lock.
        require_ghub_closed()
        dev = connect_manifest_unit(manifest)
        try:
            switch_profile(dev, target)
        finally:
            dev.close()

def cmd_backup(args):
    from .device import create_backup

    with exclusive_operation_lock(OPERATION_LOCK):
        out = create_backup(args.label)
    print(f"Backup created in private state: {out.name}")


def cmd_restore(args):
    from .device import load_backup, restore_backup

    root, _images, _manifest = load_backup(args.backup)
    print(f"Backup: {root.name} (private state)")
    print("Only the programmable domain will be restored.")
    print("Profile 1 and recovery sectors 6/7 will never be written.")
    if input("Type exactly RESTORE BACKUP: ").strip() != "RESTORE BACKUP":
        print("Cancelled.")
        return
    with exclusive_operation_lock(OPERATION_LOCK):
        safety = restore_backup(root)
        _images, report = validate_device()
        if not report.ok:
            raise RuntimeError(
                "post-restore validation failed:\n  " + "\n  ".join(report.errors)
            )
    print("RESTORE COMPLETE AND VALIDATED.")
    print(f"Pre-restore safety backup: {safety.name} (private state)")


def cmd_baseline_restore(_args):
    from .device import restore_baseline

    print(
        "This restores Sector 0, Profiles 2-5 and sectors 8-15 to the "
        "active device baseline captured during setup."
    )
    print("Profile 1 and sectors 6/7 remain protected.")
    if input("Type exactly RESTORE BASELINE: ").strip() != "RESTORE BASELINE":
        print("Cancelled.")
        return
    with exclusive_operation_lock(OPERATION_LOCK):
        safety = restore_baseline()
        _images, report = validate_device()
        if not report.ok:
            raise RuntimeError(
                "post-reset validation failed:\n  " + "\n  ".join(report.errors)
            )
    print("LOCAL BASELINE PROGRAMMABLE STATE RESTORED.")
    print(f"Pre-reset safety backup: {safety.name} (private state)")


def cmd_capabilities(args):
    rows = [
        {
            "name": name,
            "status": status,
            "scope": scope,
            "note": note,
        }
        for name, (status, scope, note) in CAPABILITIES.items()
    ]
    if args.json:
        print(json.dumps(rows, indent=2, sort_keys=True))
        return
    print(f"G502 X - CAPABILITIES {VERSION}")
    print("=" * 40)
    for row in rows:
        print(
            f"{row['name']:24} {row['status']:16} "
            f"[{row['scope']}]"
        )
        print("  " + row["note"])


def build_parser():
    parser = argparse.ArgumentParser(
        prog="g502x",
        description="Declarative onboard configuration for Logitech G502 X LIGHTSPEED",
    )
    parser.add_argument("--version", action="version", version=f"g502x {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("selftest", help="run offline compiler tests; no device baseline required")
    p.set_defaults(func=cmd_selftest)

    p = sub.add_parser("schema", help="print the configuration JSON schema")
    p.add_argument("--path", action="store_true")
    p.set_defaults(func=cmd_schema)

    p = sub.add_parser("probe", help="read-only compatibility probe; identity redacted by default")
    p.add_argument("--pid", type=_int_auto, default=DEFAULT_PID)
    p.add_argument("--index", type=_int_auto, default=DEFAULT_INDEX)
    p.add_argument("--no-sectors", action="store_true")
    p.add_argument("--private", action="store_true", help="PRIVATE: include stable per-unit fingerprint")
    p.add_argument("--json", action="store_true", help="PRIVATE: raw diagnostic JSON including per-unit identifiers")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("setup", help="capture and activate this unit's read-only safety baseline")
    p.add_argument("--pid", type=_int_auto, default=DEFAULT_PID)
    p.add_argument("--index", type=_int_auto, default=DEFAULT_INDEX)
    p.add_argument("--replace", action="store_true")
    p.add_argument("--private", action="store_true", help="PRIVATE: include local baseline path and fingerprint")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("plan", help="compile and allocate a config without writing")
    p.add_argument("config")
    p.add_argument("--json", metavar="PATH", help="write deterministic plan JSON; use - for stdout")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("capacity", help="show global macro capacity")
    p.add_argument("config", nargs="?")
    p.set_defaults(func=cmd_capacity)

    p = sub.add_parser("apply", help="transactionally apply a config")
    p.add_argument("config")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("validate", help="validate recovery, CRCs, pointers and macro chains")
    p.add_argument("--json", action="store_true")
    p.add_argument("--private", action="store_true", help="PRIVATE: include profile metadata in JSON output")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("status", help="show current onboard state; profile metadata redacted by default")
    p.add_argument("--json", action="store_true")
    p.add_argument("--private", action="store_true", help="PRIVATE: include profile names and metadata")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("inspect", help="inspect macro structure; semantic descriptions redacted by default")
    p.add_argument("--json", action="store_true")
    p.add_argument("--private", action="store_true", help="PRIVATE: include decoded semantic instruction descriptions")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("profile", help="switch to safe/1/2/3/4/5")
    p.add_argument("target")
    p.set_defaults(func=cmd_profile)

    p = sub.add_parser("backup", help="backup all 16 sectors with hashes")
    p.add_argument("--label", default="backup")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("restore", help="restore programmable state from backup")
    p.add_argument("backup")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("baseline", help="manage local per-device baselines")
    bsub = p.add_subparsers(dest="baseline_command", required=True)
    q = bsub.add_parser("list", help="list local device baselines")
    q.add_argument("--json", action="store_true")
    q.add_argument("--private", action="store_true", help="PRIVATE: include fingerprints")
    q.set_defaults(func=cmd_baseline_list)
    q = bsub.add_parser("show", help="show the active baseline")
    q.add_argument("--private", action="store_true", help="PRIVATE: include per-unit identifiers")
    q.set_defaults(func=cmd_baseline_show)
    q = bsub.add_parser("use", help="activate a local baseline by fingerprint")
    q.add_argument("fingerprint")
    q.add_argument("--private", action="store_true", help="PRIVATE: echo the local path")
    q.set_defaults(func=cmd_baseline_use)
    q = bsub.add_parser("restore", help="restore programmable state to the active baseline")
    q.set_defaults(func=cmd_baseline_restore)

    p = sub.add_parser("report", help="create or verify shareable reports")
    rsub = p.add_subparsers(dest="report_command", required=True)
    q = rsub.add_parser("probe", help="write a shareable read-only hardware report")
    q.add_argument("path")
    q.add_argument("--pid", type=_int_auto, default=DEFAULT_PID)
    q.add_argument("--index", type=_int_auto, default=DEFAULT_INDEX)
    q.set_defaults(func=cmd_report_probe)
    q = rsub.add_parser("device", help="write a shareable baseline-aware device report")
    q.add_argument("path")
    q.add_argument("--state", action="store_true")
    q.set_defaults(func=cmd_report_device)
    q = rsub.add_parser("check", help="verify that a report is safe to share")
    q.add_argument("path")
    q.set_defaults(func=cmd_report_check)

    p = sub.add_parser("debug", help="private diagnostic utilities")
    dsub = p.add_subparsers(dest="debug_command", required=True)
    q = dsub.add_parser("export", help="PRIVATE diagnostic export; defaults to G502X_HOME")
    q.add_argument("path", nargs="?", help="optional output path; default is private G502X_HOME/exports")
    q.add_argument("--raw", action="store_true", help="PRIVATE: include base64 of all 16 raw sectors")
    q.add_argument("--allow-repo-output", action="store_true", help="PRIVATE: allow output inside project tree")
    q.set_defaults(func=cmd_debug_export)

    p = sub.add_parser("capabilities", help="show hardware capability evidence")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_capabilities)

    return parser

def main():
    args = build_parser().parse_args()
    try:
        args.func(args)
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
