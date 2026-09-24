from __future__ import annotations

import argparse
from datetime import datetime
import json
import sys
from pathlib import Path

from . import VERSION
from .application import ErrorCode, create_application
from .baseline import (
    DEFAULT_INDEX,
    DEFAULT_PID,
    HOME as STATE_HOME,
    active_manifest,
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
from .validator import validate_device


SCHEMA_PATH = ROOT / "g502x_onboard" / "config.schema.json"
OPERATION_LOCK = STATE_HOME / "operation.lock"


def _int_auto(value: str) -> int:
    return int(str(value), 0)


def _app_value(result):
    if result.ok:
        return result.value
    if result.error.code is ErrorCode.CONFIG_ERROR:
        raise ConfigError(result.error.message)
    raise RuntimeError(result.error.message)


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
    value = _app_value(
        create_application().probe_details(
            pid=args.pid,
            index=args.index,
            read_sectors=not args.no_sectors,
            private=bool(args.private or args.json),
        )
    )
    result = value.payload

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
    for key, item in result["descriptor"].items():
        print(f"  {key}: {item}")
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

    sector_rows = result.get("sectors")
    if sector_rows is None:
        sector_rows = result.get("sector_health")
    if sector_rows:
        def _health(row):
            return row.get(
                "health",
                "crc_valid" if row.get("crc_ok") else "invalid",
            )

        invalid = [
            sector
            for sector, row in sector_rows.items()
            if _health(row) == "invalid"
        ]
        erased = [
            sector
            for sector, row in sector_rows.items()
            if _health(row) == "erased"
        ]
        count = len(sector_rows)
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
    value = _app_value(
        create_application().report_probe(pid=args.pid, index=args.index)
    )
    out = Path(args.path).resolve()
    atomic_local_write_text(
        out,
        json.dumps(value.payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    )
    print(f"Shareable probe report: {out.name}")
    print(
        "No unit ID, serial number, private fingerprint, stable sector hashes, "
        "raw sectors, or user profile names are included."
    )



def cmd_setup(args):
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

    value = _app_value(
        create_application().setup_baseline(
            pid=args.pid,
            index=args.index,
            replace=args.replace,
        )
    )
    print()
    print("LOCAL DEVICE BASELINE READY.")
    if args.private:
        print(f"Path: {value.root}")
        print(f"Fingerprint: {value.fingerprint}")
    else:
        print("Identity: unit-bound / redacted")
        print("Storage: private G502X_HOME baseline")
    print(
        f"Architecture: {value.architecture}  "
        f"Transport: {value.transport}  "
        f"Writes enabled: {value.write_allowed}"
    )
    if not value.write_allowed:
        print(
            "This baseline is read-only for now. Create a shareable device report "
            "before enabling writes on an untested transport."
        )



def cmd_baseline_list(args):
    value = _app_value(create_application().list_baselines(private=args.private))
    rows = list(value.rows)
    if args.json:
        print(json.dumps(rows, indent=2, sort_keys=True))
        return
    if not rows:
        print("No local baselines. Run setup first.")
        return
    for row in rows:
        marker = "*" if row["active"] else " "
        transport = row.get("transport") or {}
        identity = row.get("fingerprint", "<redacted>")
        print(
            f"{marker} {identity} "
            f"{row.get('device_name') or '?'} "
            f"PID={transport.get('pid_hex')} "
            f"index={transport.get('index_hex')} "
            f"{row.get('compatibility', {}).get('transport')}"
        )



def cmd_baseline_show(args):
    value = _app_value(create_application().show_baseline(private=args.private))
    print(json.dumps(value.payload, indent=2, sort_keys=True, ensure_ascii=False))



def cmd_baseline_use(args):
    value = _app_value(create_application().use_baseline(args.fingerprint))
    if args.private:
        print(f"Active baseline: {value.root}")
    else:
        print("Active baseline switched in private local state.")



def cmd_report_check(args):
    path = Path(args.path).resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    value = _app_value(create_application().check_public_report(payload))
    print("PUBLIC REPORT CHECK: PASS")
    print(f"Format: {value.format}")
    print(f"File: {path.name}")



def cmd_report_device(args):
    value = _app_value(
        create_application().report_device(include_state=args.state)
    )
    out = Path(args.path).resolve()
    atomic_local_write_text(
        out,
        json.dumps(value.payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    )
    print(f"Shareable device report: {out.name}")
    print(
        "No unit ID, serial number, private fingerprint, stable sector hashes, "
        "raw sectors, or user profile names are included."
    )



def cmd_smoke_readonly(args):
    value = _app_value(
        create_application().readonly_smoke(
            report_path=str(Path(args.report).resolve()),
            label=args.label,
        )
    )
    print(f"G502 X - READ-ONLY SMOKE {VERSION}")
    print("=" * 40)
    print("Result: PASS")
    print("Mouse writes: NONE")
    print("G HUB: closed")
    print("Identity: unit-bound / redacted")
    print(
        "Compatibility: "
        f"architecture={value.architecture} "
        f"transport={value.transport}"
    )
    print(
        "Descriptor: "
        f"profile_format={value.profile_format} "
        f"macro_format={value.macro_format} "
        f"sectors={value.sector_count}x{value.sector_size}"
    )
    print(
        "Validation: PASS; "
        f"enabled={','.join(map(str, value.enabled_profiles)) or '-'}; "
        f"macro_starts={value.macro_starts}"
    )
    print("Sector health: no invalid sectors")
    print(f"Checkpoint: {value.checkpoint_name} (private state)")
    print("Checkpoint equality: PASS")
    print(f"Shareable device report: {value.report_name}")
    print("Report privacy check: PASS")



def cmd_plan(args):
    value = _app_value(create_application().plan(args.config))
    if args.json:
        if args.json == "-":
            print(value.rendered_json, end="")
        else:
            out = Path(args.json).resolve()
            atomic_local_write_text(out, value.rendered_json)
            print(f"Plan JSON: {out.name}")
            print(
                "Plan JSON is local configuration output and may contain "
                "user-authored bindings/macros; do not treat it as a "
                "privacy-minimized shareable report."
            )
    else:
        _print_plan(Path(value.config_path), value.plan)
    print()
    print("DRY RUN. No device writes performed.")



def cmd_capacity(args):
    print(f"G502 X - CAPACITY {VERSION}")
    print(
        f"Global store: {GLOBAL_RAW_CAPACITY} raw / "
        f"{GLOBAL_PAYLOAD_CAPACITY} VM payload bytes"
    )
    value = _app_value(create_application().capacity(args.config))
    if args.config:
        print(f"Config: {value.config_path}")
        print(f"Source bytes:    {value.source_bytes}")
        print(f"Allocated bytes: {value.allocated_bytes}")
        print(f"JUMP overhead:   {value.jump_overhead}")
        print(f"Fragmentation:   {value.fragmentation_waste}")
        print(f"Raw free:        {value.raw_free_bytes}")
        print(f"Usable free:     {value.usable_free_bytes}")


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
    value = _app_value(create_application().validate_details(private=args.private))
    if args.json:
        print(json.dumps(value.summary, indent=2, sort_keys=True))
    else:
        print(f"G502 X - VALIDATE {VERSION}")
        print("=" * 40)
        print("Result:", "PASS" if value.ok else "FAIL")
        print("Enabled: " + ", ".join(map(str, value.enabled_profiles)))
        print(f"Referenced macro starts: {value.referenced_macro_starts}")
        for warning in value.warnings:
            print("WARNING:", warning)
        for error in value.errors:
            print("ERROR:", error)
    if not value.ok:
        raise SystemExit(1)



def cmd_status(args):
    value = _app_value(create_application().status(private=args.private))
    summary = value.summary
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return

    desc = value.descriptor
    print("G502 X LIGHTSPEED")
    print("=" * 40)
    print(f"Tool version: {VERSION}")
    print(f"Active profile: {value.active_profile}")
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
    print("  enabled: " + ", ".join(map(str, value.enabled_profiles)))
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
    print("Structural validation:", "PASS" if summary["ok"] else "FAIL")



def cmd_inspect(args):
    value = _app_value(create_application().inspect(private=args.private))
    rows = list(value.rows)
    if args.json:
        print(json.dumps(rows, indent=2, sort_keys=True))
        return

    print(f"G502 X - INSPECT {VERSION}")
    print("=" * 40)
    print("Enabled: " + ", ".join(map(str, value.enabled_profiles)))
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

    value = _app_value(create_application().debug_export(include_raw=args.raw))
    payload = value.payload

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
        export_dir = private_mkdir(Path(value.default_directory))
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

    _app_value(create_application().switch_profile(target, phrase))



def cmd_backup(args):
    value = _app_value(create_application().create_backup(args.label))
    print(f"Backup created in private state: {value.name}")


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
