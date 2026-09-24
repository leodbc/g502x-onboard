from __future__ import annotations

import hashlib
import json

from ..baseline import active_baseline
from ..codec import build_plan, plan_json, validate_directory
from ..config import load_config
from ..constants import PROGRAMMABLE_SECTORS, RECOVERY_SECTORS, SAFE_PROFILE
from ..device import (
    _programmable_target_digest,
    apply_plan,
    assert_active_device_matches_baseline,
    connect_manifest_unit,
    ensure_safe_profile,
    get_current_profile,
    load_backup,
    require_ghub_closed,
    restore_backup,
    restore_baseline,
    validate_recovery,
)
from ..private_io import exclusive_operation_lock, private_write_text
from ..validator import validate_device, validate_images
from .backend import (
    CooperativeCancellationError,
    PersistentBackendResult,
    PersistentTargetSnapshot,
)
from .models import PersistentOperationKind, PersistentPhase
from .real_backend import OPERATION_LOCK


def _normalized_config_digest(config: dict) -> str:
    payload = json.dumps(
        config,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_backup_target(target: dict[int, bytes]) -> None:
    target_report = validate_images(target)
    if not target_report.ok:
        raise RuntimeError(
            "backup is not a structurally valid restorable state"
        )
    baseline, _manifest = active_baseline()
    for sector in RECOVERY_SECTORS:
        if target[sector] != baseline[sector]:
            raise RuntimeError(
                f"backup recovery sector {sector} differs from device baseline"
            )
    validate_directory(target[0], baseline[0])


def _load_target_locked(kind: PersistentOperationKind, source: str | None):
    if kind is PersistentOperationKind.RESTORE_BACKUP:
        if not source:
            raise RuntimeError("backup restore requires a target")
        root, target, _manifest = load_backup(source)
        _validate_backup_target(target)
        return PersistentTargetSnapshot(
            source_path=str(root),
            source_name=root.name,
            target_digest=_programmable_target_digest(target),
        )
    if kind is PersistentOperationKind.RESTORE_BASELINE:
        target, _manifest = active_baseline()
        return PersistentTargetSnapshot(
            source_path=None,
            source_name="active local baseline",
            target_digest=_programmable_target_digest(target),
        )
    raise RuntimeError("persistent target lookup is valid only for restore operations")


def prepare_real_target(
    kind: PersistentOperationKind,
    source: str | None,
) -> PersistentTargetSnapshot:
    with exclusive_operation_lock(OPERATION_LOCK):
        return _load_target_locked(kind, source)


def _check_cancel(cancellation) -> None:
    if cancellation.is_cancelled:
        raise CooperativeCancellationError(
            "cooperative cancellation requested before persistent write"
        )


def execute_real_persistent(intent, cancellation, phase_callback):
    with exclusive_operation_lock(OPERATION_LOCK):
        _check_cancel(cancellation)
        require_ghub_closed()

        baseline_images, baseline_manifest = active_baseline()
        baseline_fp = str(baseline_manifest.get("fingerprint") or "")
        if (
            not baseline_fp
            or baseline_fp != intent.active_baseline_binding
            or baseline_fp != intent.exact_unit_binding
        ):
            raise RuntimeError("active baseline or exact unit changed after review")

        manifest = assert_active_device_matches_baseline()
        if str(manifest.get("fingerprint") or "") != intent.active_baseline_binding:
            raise RuntimeError("active baseline changed after review")
        if tuple(intent.managed_sectors) != tuple(PROGRAMMABLE_SECTORS):
            raise RuntimeError("prepared managed domain is not the approved domain")
        if any(sector in RECOVERY_SECTORS for sector in intent.managed_sectors):
            raise RuntimeError("prepared target includes protected recovery state")

        validate_recovery()
        _check_cancel(cancellation)
        require_ghub_closed()

        if intent.kind is PersistentOperationKind.APPLY_CONFIG:
            if not intent.source_path or intent.plan is None:
                raise RuntimeError("prepared apply is missing its executable target")
            path, config = load_config(intent.source_path)
            current_plan = build_plan(config, baseline_images)
            current_digest = hashlib.sha256(
                plan_json(current_plan).encode("utf-8")
            ).hexdigest()
            if current_digest != intent.target_digest:
                raise RuntimeError("configuration target changed after review")
            if _normalized_config_digest(config) != intent.source_digest:
                raise RuntimeError("source configuration changed after review")
            dev = connect_manifest_unit(manifest)
            try:
                if get_current_profile(dev) != SAFE_PROFILE:
                    raise RuntimeError("Profile 1 SAFE must be active before apply")
            finally:
                dev.close()
            del path
        elif intent.kind is PersistentOperationKind.RESTORE_BACKUP:
            target = _load_target_locked(intent.kind, intent.source_path)
            if target.target_digest != intent.target_digest:
                raise RuntimeError("backup target changed after review")
            ensure_safe_profile(manifest)
            validate_recovery()
        elif intent.kind is PersistentOperationKind.RESTORE_BASELINE:
            target = _load_target_locked(intent.kind, None)
            if target.target_digest != intent.target_digest:
                raise RuntimeError("baseline target changed after review")
            ensure_safe_profile(manifest)
            validate_recovery()
        else:
            raise RuntimeError("unsupported persistent operation kind")

        _check_cancel(cancellation)
        require_ghub_closed()
        phase_callback(PersistentPhase.ARMED)
        _check_cancel(cancellation)

        def before_first_write() -> None:
            _check_cancel(cancellation)
            phase_callback(PersistentPhase.WRITING)

        def before_final_reconcile() -> None:
            phase_callback(PersistentPhase.RECONCILING)

        if intent.kind is PersistentOperationKind.APPLY_CONFIG:
            safety = apply_plan(
                intent.plan,
                expected_baseline_fingerprint=intent.active_baseline_binding,
                before_first_write=before_first_write,
                before_final_reconcile=before_final_reconcile,
            )
        elif intent.kind is PersistentOperationKind.RESTORE_BACKUP:
            safety = restore_backup(
                intent.source_path,
                expected_baseline_fingerprint=intent.active_baseline_binding,
                expected_target_digest=intent.target_digest,
                before_first_write=before_first_write,
                before_final_reconcile=before_final_reconcile,
            )
        else:
            safety = restore_baseline(
                expected_baseline_fingerprint=intent.active_baseline_binding,
                expected_target_digest=intent.target_digest,
                before_first_write=before_first_write,
                before_final_reconcile=before_final_reconcile,
            )

        phase_callback(PersistentPhase.POST_VALIDATING)
        _images, report = validate_device()
        if not report.ok:
            raise RuntimeError("full post-write validation failed")

        if intent.kind is PersistentOperationKind.APPLY_CONFIG:
            private_write_text(safety / "intended-plan.json", plan_json(intent.plan))
            private_write_text(
                safety / "source-config.normalized.json",
                json.dumps(
                    intent.normalized_config,
                    indent=2,
                    sort_keys=True,
                    ensure_ascii=False,
                )
                + "\n",
            )

        return PersistentBackendResult(
            enabled_profiles=tuple(report.enabled_profiles),
            safety_backup_name=safety.name,
            reconciliation_completed=True,
            post_validation_completed=True,
        )
