from __future__ import annotations

import hashlib
import json

from ..codec import build_plan, plan_json
from ..config import load_config
from ..constants import (
    GLOBAL_MACRO_SECTORS,
    PROGRAMMABLE_PROFILES,
    PROGRAMMABLE_SECTORS,
    RECOVERY_SECTORS,
)
from .backend import (
    CooperativeCancellationError,
    PersistentBackendResult,
    PersistentTargetSnapshot,
)
from .models import PersistentOperationKind, PersistentPhase, WriteEligibility


def _target_digest(images: dict[int, bytes]) -> str:
    digest = hashlib.sha256()
    for sector in PROGRAMMABLE_SECTORS:
        digest.update(int(sector).to_bytes(2, "big"))
        digest.update(images[sector])
    return digest.hexdigest()


def _normalized_config_digest(config: dict) -> str:
    payload = json.dumps(
        config,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def prepare_fake_target(backend, kind, source=None):
    backend.read_history.append(f"persistent-target:{kind.value}")
    if kind is PersistentOperationKind.RESTORE_BACKUP:
        if source is None or str(source) not in backend.backup_targets:
            raise RuntimeError("unknown fake backup")
        target = backend.backup_targets[str(source)]
        for sector in RECOVERY_SECTORS:
            if target[sector] != backend.baseline_images[sector]:
                raise RuntimeError("fake backup changes protected recovery sector")
        return PersistentTargetSnapshot(
            source_path=str(source),
            source_name=str(source).rsplit("/", 1)[-1],
            target_digest=_target_digest(target),
        )
    if kind is PersistentOperationKind.RESTORE_BASELINE:
        return PersistentTargetSnapshot(
            source_path=None,
            source_name="active local baseline",
            target_digest=_target_digest(backend.baseline_images),
        )
    raise RuntimeError("fake persistent target is valid only for restore")


def _maybe_block(backend, point: str) -> None:
    if backend.block_at != point:
        return
    if backend.block_entered is not None:
        backend.block_entered.set()
    if backend.block_release is not None:
        if not backend.block_release.wait(timeout=3.0):
            raise RuntimeError("fake persistent block release timeout")


def _cancel(cancellation) -> None:
    if cancellation.is_cancelled:
        raise CooperativeCancellationError("cancelled before persistent write")


def execute_fake_persistent(backend, intent, cancellation, phase_callback):
    backend.write_history.append("os-lock:enter")
    try:
        backend.read_history.append("persistent-revalidate")
        _maybe_block(backend, "revalidating")
        _cancel(cancellation)

        if not backend.baseline_matches:
            raise RuntimeError("active baseline changed")
        if not backend.exact_unit_matches:
            raise RuntimeError("exact unit changed")
        if backend.active_baseline_binding != intent.active_baseline_binding:
            raise RuntimeError("baseline binding changed")
        if backend.exact_unit_binding != intent.exact_unit_binding:
            raise RuntimeError("unit binding changed")
        if backend._compatibility().eligibility is not WriteEligibility.ELIGIBLE:
            raise RuntimeError("write eligibility changed")
        recheck = (
            backend.host_guard_clear
            if backend.host_guard_recheck_clear is None
            else backend.host_guard_recheck_clear
        )
        if not recheck:
            raise RuntimeError("host guard became active")
        if not backend.recovery_ok:
            raise RuntimeError("recovery precondition changed")
        if tuple(intent.managed_sectors) != tuple(PROGRAMMABLE_SECTORS):
            raise RuntimeError("managed domain changed")
        if any(s in RECOVERY_SECTORS for s in intent.managed_sectors):
            raise RuntimeError("protected domain detected")

        if intent.kind is PersistentOperationKind.APPLY_CONFIG:
            if intent.source_path is None or intent.plan is None:
                raise RuntimeError("missing prepared apply target")
            _path, config = load_config(intent.source_path)
            plan = build_plan(config, backend.baseline_images)
            digest = hashlib.sha256(plan_json(plan).encode("utf-8")).hexdigest()
            if digest != intent.target_digest:
                raise RuntimeError("apply target changed")
            if _normalized_config_digest(config) != intent.source_digest:
                raise RuntimeError("source config changed")
            if backend.active_profile != 1:
                raise RuntimeError("Profile 1 SAFE is not active")
        elif intent.kind is PersistentOperationKind.RESTORE_BACKUP:
            if intent.source_path not in backend.backup_targets:
                raise RuntimeError("backup target disappeared")
            if _target_digest(backend.backup_targets[intent.source_path]) != intent.target_digest:
                raise RuntimeError("backup target changed")
            backend.active_profile = 1
        elif intent.kind is PersistentOperationKind.RESTORE_BASELINE:
            if _target_digest(backend.baseline_images) != intent.target_digest:
                raise RuntimeError("baseline target changed")
            backend.active_profile = 1
        else:
            raise RuntimeError("unknown persistent operation")

        _cancel(cancellation)
        if backend.fault_at == "before-writing":
            raise RuntimeError("synthetic pre-write failure")

        phase_callback(PersistentPhase.ARMED)
        _maybe_block(backend, "armed")
        _cancel(cancellation)
        _cancel(cancellation)
        phase_callback(PersistentPhase.WRITING)
        _maybe_block(backend, "writing")

        if backend.fault_at == "first-persistent-action":
            raise RuntimeError("synthetic first persistent action failure")

        if intent.kind is PersistentOperationKind.APPLY_CONFIG:
            prefix = "apply"
        elif intent.kind is PersistentOperationKind.RESTORE_BACKUP:
            prefix = "restore"
        else:
            prefix = "baseline"

        labels = [f"{prefix}:sector-0-staging"]
        labels.extend(f"{prefix}:macro-{s}" for s in GLOBAL_MACRO_SECTORS)
        labels.extend(f"{prefix}:profile-{p}" for p in (3, 4, 5, 2))
        labels.append(f"{prefix}:sector-0-final")

        for index, label in enumerate(labels):
            backend.persistent_write_count += 1
            backend.write_history.append(label)
            if index == 0 and backend.fault_at == "after-potential-commit":
                raise RuntimeError("synthetic failure after possible commit")
            if index == 0 and backend.fault_at == "fresh-readback-indeterminate":
                backend.write_history.append("reconcile:indeterminate")
                raise RuntimeError("PARTIAL/INDETERMINATE")
            if index == 0 and backend.fault_at == "fresh-readback-previous":
                backend.write_history.append("reconcile:previous")
                for retry in (2, 3):
                    backend.persistent_write_count += 1
                    backend.write_history.append(f"{label}:retry-{retry}")
                raise RuntimeError("remained unchanged after bounded retries")
            if index == 0 and backend.fault_at == "fresh-readback-target":
                backend.write_history.append("reconcile:target")

        phase_callback(PersistentPhase.RECONCILING)
        backend.read_history.append("final-reconciliation")
        _maybe_block(backend, "reconciling")
        if backend.fault_at == "final-reconciliation":
            raise RuntimeError("synthetic final reconciliation failure")

        phase_callback(PersistentPhase.POST_VALIDATING)
        backend.read_history.append("post-validation")
        _maybe_block(backend, "post-validating")
        if backend.fault_at == "post-validation" or not backend.post_validation_ok:
            raise RuntimeError("synthetic post-validation failure")

        return PersistentBackendResult(
            enabled_profiles=tuple(backend.enabled_profiles),
            safety_backup_name=(
                "pre-apply-fake"
                if intent.kind is PersistentOperationKind.APPLY_CONFIG
                else (
                    "pre-restore-fake"
                    if intent.kind is PersistentOperationKind.RESTORE_BACKUP
                    else "pre-baseline-restore-fake"
                )
            ),
            reconciliation_completed=True,
            post_validation_completed=True,
        )
    finally:
        backend.write_history.append("os-lock:exit")
