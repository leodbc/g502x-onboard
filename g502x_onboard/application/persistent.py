from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
import hashlib
from itertools import count
import json
from pathlib import Path
from threading import Lock
from typing import Callable
import weakref

from .. import VERSION
from ..codec import build_plan, plan_json, profile_display_metadata
from ..config import ConfigError, load_config
from ..constants import (
    GLOBAL_PAYLOAD_CAPACITY,
    GLOBAL_RAW_CAPACITY,
    PROGRAMMABLE_PROFILES,
    PROGRAMMABLE_SECTORS,
    RECOVERY_SECTORS,
)
from .backend import (
    Backend,
    CooperativeCancellationError,
    PersistentBackendFailure,
    PersistentBackendIntent,
)
from .coordinator import OperationBusyError, OperationCoordinator
from .models import (
    ApplicationError,
    ApplyReview,
    CancellationToken,
    ErrorCode,
    OperationResult,
    PersistentExecutionResult,
    PersistentOperationKind,
    PersistentPhase,
    PersistentPhaseSnapshot,
    PreparedOperation,
    PrivacyClass,
    RestoreBackupReview,
    RestoreBaselineReview,
    WriteEligibility,
)


@dataclass
class _IssuedPreparation:
    owner: weakref.ReferenceType
    public: PreparedOperation
    intent: PersistentBackendIntent
    consumed: bool = False
    executing: bool = False


@dataclass(frozen=True)
class _PreparationTombstone:
    owner: weakref.ReferenceType
    consumed: bool


_MAX_ACTIVE_PREPARATIONS = 32
_MAX_PREPARATION_TOMBSTONES = 128
_ISSUANCE_SERIAL = count()
_REGISTRY_LOCK = Lock()
_PREPARATIONS: OrderedDict[str, _IssuedPreparation] = OrderedDict()
_TOMBSTONES: OrderedDict[str, _PreparationTombstone] = OrderedDict()


def _issued_preparation_id(candidate: str) -> str:
    # The injected id_factory is intentionally deterministic/testable. Add a
    # process-local issuance generation so a repeated factory value can never
    # make an old PreparedOperation equal a later issuance after compaction.
    return f"{candidate}#{next(_ISSUANCE_SERIAL):016x}"


def _trim_tombstones_locked() -> None:
    while len(_TOMBSTONES) > _MAX_PREPARATION_TOMBSTONES:
        _TOMBSTONES.popitem(last=False)


def _add_tombstone_locked(
    preparation_id: str,
    owner: weakref.ReferenceType,
    *,
    consumed: bool,
) -> None:
    _TOMBSTONES[preparation_id] = _PreparationTombstone(
        owner=owner,
        consumed=consumed,
    )
    _TOMBSTONES.move_to_end(preparation_id)
    _trim_tombstones_locked()


def _retire_locked(
    preparation_id: str,
    record: _IssuedPreparation,
    *,
    consumed: bool | None = None,
) -> None:
    current = _PREPARATIONS.get(preparation_id)
    if current is not record:
        return
    del _PREPARATIONS[preparation_id]
    _add_tombstone_locked(
        preparation_id,
        record.owner,
        consumed=record.consumed if consumed is None else consumed,
    )


def _reclaim_dead_locked() -> None:
    # Weak owner references prevent facade lifetime extension. Once the owner is
    # gone, no full/private payload needs to remain reachable from the registry.
    for preparation_id, record in list(_PREPARATIONS.items()):
        if record.owner() is None and not record.executing:
            del _PREPARATIONS[preparation_id]
    for preparation_id, tombstone in list(_TOMBSTONES.items()):
        if tombstone.owner() is None:
            del _TOMBSTONES[preparation_id]


def _make_active_room_locked() -> bool:
    _reclaim_dead_locked()
    while len(_PREPARATIONS) >= _MAX_ACTIVE_PREPARATIONS:
        retired = False
        for preparation_id, record in list(_PREPARATIONS.items()):
            if record.executing:
                continue
            _retire_locked(preparation_id, record)
            retired = True
            break
        if not retired:
            return False
    return True


def _retired_terminal(
    owner,
    prepared: PreparedOperation,
    trace: list[PersistentPhase] | tuple[PersistentPhase, ...],
):
    tombstone = _TOMBSTONES.get(prepared.preparation_id)
    if tombstone is None or tombstone.owner() is not owner:
        return None
    if tombstone.consumed:
        return _terminal(
            prepared,
            success=False,
            status="consumed-preparation",
            error_code=ErrorCode.CONSUMED_PREPARATION,
            message="preparation has already been consumed; prepare again",
            trace=trace,
        )
    return _terminal(
        prepared,
        success=False,
        status="stale-preparation",
        error_code=ErrorCode.STALE_PREPARATION,
        message="preparation has been retired; prepare again",
        trace=trace,
    )


def _failure(code: ErrorCode, message: str, privacy: PrivacyClass) -> OperationResult:
    return OperationResult(
        ok=False,
        error=ApplicationError(code=code, message=message, privacy=privacy),
        privacy=privacy,
    )


def _terminal(
    prepared: PreparedOperation,
    *,
    success: bool,
    status: str,
    error_code: ErrorCode | None,
    message: str,
    trace: list[PersistentPhase] | tuple[PersistentPhase, ...],
    writing_started: bool = False,
    reconciliation_completed: bool = False,
    post_validation_completed: bool = False,
    enabled_profiles: tuple[int, ...] = (),
    safety_backup_name: str | None = None,
) -> OperationResult[PersistentExecutionResult]:
    phases = list(trace)
    terminal = PersistentPhase.SUCCEEDED if success else PersistentPhase.FAILED
    if not phases or phases[-1] is not terminal:
        phases.append(terminal)
    value = PersistentExecutionResult(
        operation_kind=prepared.kind,
        terminal_phase=terminal,
        success=success,
        pre_write_status=status,
        writing_started=writing_started,
        reconciliation_completed=reconciliation_completed,
        post_validation_completed=post_validation_completed,
        enabled_profiles=enabled_profiles,
        safety_backup_name=safety_backup_name,
        error_code=error_code,
        message=message,
        phase_trace=tuple(phases),
    )
    return OperationResult(ok=True, value=value, privacy=value.privacy)


def _call_preparation_context(
    backend: Backend,
    coordinator: OperationCoordinator,
):
    try:
        with coordinator.claim():
            value = backend.preparation_context()
        return OperationResult(
            ok=True,
            value=value,
            privacy=PrivacyClass.PRIVATE_DIAGNOSTIC,
        )
    except OperationBusyError as exc:
        return _failure(ErrorCode.BUSY, str(exc), PrivacyClass.SHAREABLE)
    except Exception:
        return _failure(
            ErrorCode.BACKEND_FAILURE,
            "unable to observe preparation preconditions",
            PrivacyClass.PRIVATE_DIAGNOSTIC,
        )


def _require_preparable(context):
    if context.compatibility.eligibility is not WriteEligibility.ELIGIBLE:
        return _failure(
            ErrorCode.READ_ONLY,
            "connected target is read-only; persistent preparation is refused",
            PrivacyClass.SHAREABLE,
        )
    if not context.host_guard_clear:
        return _failure(
            ErrorCode.SAFETY_REFUSAL,
            "host guard is not clear",
            PrivacyClass.SHAREABLE,
        )
    return None


def _normalized_config_digest(config: dict) -> str:
    payload = json.dumps(
        config,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _apply_review_lines(path: Path, plan: dict) -> tuple[str, ...]:
    lines: list[str] = [
        f"G502 X - PLAN {VERSION}",
        "=" * 40,
        f"Config: {path}",
        "Runtime: autonomous onboard HID / Macro VM",
        f"Global store: {GLOBAL_RAW_CAPACITY} raw / {GLOBAL_PAYLOAD_CAPACITY} VM payload bytes",
        "Enabled profiles: " + ", ".join(map(str, plan["enabled_profiles"])),
    ]
    if plan["warnings"]:
        lines.append("")
        for warning in plan["warnings"]:
            lines.append("WARNING: " + str(warning))
    lines.append("")

    for profile in PROGRAMMABLE_PROFILES:
        p = plan["profiles"][profile]
        lines.extend((f"PROFILE {profile}", "-" * 36))
        if p["disabled"]:
            lines.extend(("disabled -> local baseline template", ""))
            continue
        meta = profile_display_metadata(p["profile"])
        lines.append(
            f"name={meta['name']!r} polling={meta['polling_rate_hz']}Hz "
            f"default_dpi={meta['default_dpi']} shift_dpi={meta['shift_dpi']}"
        )
        lines.append("dpi=" + ",".join(map(str, meta["dpi"])))
        for route, item in sorted(p["results"].items()):
            if item["kind"] == "direct":
                lines.append(
                    f"{route:20} DIRECT {item['binding'].hex(' ').upper()} "
                    f"{item['description']}"
                )
            else:
                lines.append(
                    f"{route:20} MACRO s{item['sector']}:0x{item['offset']:02X} "
                    f"src={item['source_length']}B "
                    f"fragments={len(item['fragments'])} {item['description']}"
                )
        lines.append("")

    store = plan["global_store"]
    lines.extend((
        "GLOBAL STORE",
        "-" * 36,
        f"Unique macros:   {len(plan['unique_macros'])}",
        f"Source bytes:    {store['source_bytes']}",
        f"Allocated bytes: {store['allocated_bytes']}",
        f"JUMP overhead:   {store['jump_overhead']}",
        f"Fragmentation:   {store['fragmentation_waste']}",
        f"Raw free:        {store['raw_free_bytes']}",
        f"Usable free:     {store['usable_free_bytes']}",
    ))
    for index, allocation in sorted(store["allocations"].items()):
        routes = ", ".join(f"P{p}:{route}" for p, route in allocation["routes"])
        lines.append(
            f"macro#{index}: s{allocation['sector']}:0x{allocation['offset']:02X} "
            f"src={allocation['source_length']}B "
            f"fragments={len(allocation['fragments'])} -> {routes}"
        )
        for fragment in allocation["fragments"]:
            jump = fragment["jump"]
            suffix = (
                f" JUMP->s{jump['sector']}:0x{jump['offset']:02X}"
                if jump else " END"
            )
            lines.append(
                f"  s{fragment['sector']}:0x{fragment['offset']:02X}.."
                f"0x{fragment['end'] - 1:02X}{suffix}"
            )
    return tuple(lines)


def _register(owner, public: PreparedOperation, intent: PersistentBackendIntent):
    with _REGISTRY_LOCK:
        if not _make_active_room_locked():
            return _failure(
                ErrorCode.BUSY,
                "preparation registry is temporarily full; retry",
                PrivacyClass.SHAREABLE,
            )
        issued = replace(
            public,
            preparation_id=_issued_preparation_id(public.preparation_id),
        )
        if (
            issued.preparation_id in _PREPARATIONS
            or issued.preparation_id in _TOMBSTONES
        ):
            return _failure(
                ErrorCode.BACKEND_FAILURE,
                "preparation issuance collision; prepare again",
                PrivacyClass.PRIVATE_DIAGNOSTIC,
            )
        _PREPARATIONS[issued.preparation_id] = _IssuedPreparation(
            owner=weakref.ref(owner),
            public=issued,
            intent=intent,
        )
    return OperationResult(ok=True, value=issued, privacy=issued.privacy)


def prepare_apply(
    owner,
    backend: Backend,
    coordinator: OperationCoordinator,
    id_factory: Callable[[], str],
    config_path: str | Path,
) -> OperationResult[PreparedOperation]:
    observed = _call_preparation_context(backend, coordinator)
    if not observed.ok:
        return observed
    context = observed.value
    refused = _require_preparable(context)
    if refused is not None:
        return refused

    try:
        path, config = load_config(config_path)
        plan = build_plan(config, context.baseline_map())
    except ConfigError:
        return _failure(
            ErrorCode.CONFIG_ERROR,
            "configuration could not be loaded or compiled",
            PrivacyClass.LOCAL_SENSITIVE,
        )
    except (OSError, ValueError):
        return _failure(
            ErrorCode.INVALID_INPUT,
            "configuration could not be loaded or compiled",
            PrivacyClass.LOCAL_SENSITIVE,
        )
    except Exception:
        return _failure(
            ErrorCode.BACKEND_FAILURE,
            "configuration preparation failed",
            PrivacyClass.PRIVATE_DIAGNOSTIC,
        )

    digest = hashlib.sha256(plan_json(plan).encode("utf-8")).hexdigest()
    profile_names = []
    for profile in PROGRAMMABLE_PROFILES:
        row = plan["profiles"][profile]
        if not row["disabled"]:
            profile_names.append(
                (profile, str(profile_display_metadata(row["profile"])["name"]))
            )
    managed = tuple(int(s) for s in PROGRAMMABLE_SECTORS)
    review = ApplyReview(
        config_name=path.name,
        config_path=str(path),
        enabled_profiles=tuple(int(p) for p in plan["enabled_profiles"]),
        profile_names=tuple(profile_names),
        managed_sectors=managed,
        warnings=tuple(str(w) for w in plan["warnings"]),
        plan_digest=digest,
        lines=_apply_review_lines(path, plan),
    )
    public = PreparedOperation(
        preparation_id=str(id_factory()),
        kind=PersistentOperationKind.APPLY_CONFIG,
        review=review,
        target_digest=digest,
        compatibility=context.compatibility,
        observed_preconditions=context.observed_preconditions,
        host_guard_clear=context.host_guard_clear,
        required_confirmation_phrase="APPLY CONFIG",
    )
    intent = PersistentBackendIntent(
        kind=public.kind,
        target_digest=digest,
        active_baseline_binding=context.active_baseline_binding,
        exact_unit_binding=context.exact_unit_binding,
        compatibility=context.compatibility,
        managed_sectors=managed,
        source_path=str(path),
        source_digest=_normalized_config_digest(config),
        plan=plan,
        normalized_config=config,
    )
    return _register(owner, public, intent)


def _prepare_restore(
    owner,
    backend: Backend,
    coordinator: OperationCoordinator,
    id_factory: Callable[[], str],
    kind: PersistentOperationKind,
    source: str | None,
) -> OperationResult[PreparedOperation]:
    observed = _call_preparation_context(backend, coordinator)
    if not observed.ok:
        return observed
    context = observed.value
    refused = _require_preparable(context)
    if refused is not None:
        return refused

    try:
        with coordinator.claim():
            target = backend.persistent_target(kind, source)
    except OperationBusyError as exc:
        return _failure(ErrorCode.BUSY, str(exc), PrivacyClass.SHAREABLE)
    except Exception:
        return _failure(
            ErrorCode.INVALID_INPUT,
            "restore target could not be loaded or validated",
            PrivacyClass.LOCAL_SENSITIVE,
        )

    managed = tuple(int(s) for s in PROGRAMMABLE_SECTORS)
    protected = tuple(int(s) for s in RECOVERY_SECTORS)
    if kind is PersistentOperationKind.RESTORE_BACKUP:
        review = RestoreBackupReview(
            backup_name=target.source_name,
            managed_sectors=managed,
            protected_sectors=protected,
            target_digest=target.target_digest,
        )
        phrase = "RESTORE BACKUP"
    else:
        review = RestoreBaselineReview(
            managed_sectors=managed,
            protected_sectors=protected,
            target_digest=target.target_digest,
        )
        phrase = "RESTORE BASELINE"

    public = PreparedOperation(
        preparation_id=str(id_factory()),
        kind=kind,
        review=review,
        target_digest=target.target_digest,
        compatibility=context.compatibility,
        observed_preconditions=context.observed_preconditions,
        host_guard_clear=context.host_guard_clear,
        required_confirmation_phrase=phrase,
    )
    intent = PersistentBackendIntent(
        kind=kind,
        target_digest=target.target_digest,
        active_baseline_binding=context.active_baseline_binding,
        exact_unit_binding=context.exact_unit_binding,
        compatibility=context.compatibility,
        managed_sectors=managed,
        source_path=target.source_path,
    )
    return _register(owner, public, intent)


def prepare_restore_backup(
    owner,
    backend: Backend,
    coordinator: OperationCoordinator,
    id_factory: Callable[[], str],
    backup_path: str | Path,
) -> OperationResult[PreparedOperation]:
    return _prepare_restore(
        owner,
        backend,
        coordinator,
        id_factory,
        PersistentOperationKind.RESTORE_BACKUP,
        str(backup_path),
    )


def prepare_restore_baseline(
    owner,
    backend: Backend,
    coordinator: OperationCoordinator,
    id_factory: Callable[[], str],
) -> OperationResult[PreparedOperation]:
    return _prepare_restore(
        owner,
        backend,
        coordinator,
        id_factory,
        PersistentOperationKind.RESTORE_BASELINE,
        None,
    )


def _execute_accepted(
    backend: Backend,
    prepared: PreparedOperation,
    intent: PersistentBackendIntent,
    token: CancellationToken,
    trace: list[PersistentPhase],
    observer: Callable[[PersistentPhaseSnapshot], None] | None,
) -> OperationResult[PersistentExecutionResult]:
    def emit(phase: PersistentPhase) -> None:
        if not trace or trace[-1] is not phase:
            trace.append(phase)
        if observer is not None:
            try:
                observer(
                    PersistentPhaseSnapshot(
                        phase=phase,
                        kind=prepared.kind,
                        cancellation_allowed=phase in {
                            PersistentPhase.PREPARING,
                            PersistentPhase.PREPARED,
                            PersistentPhase.REVIEWING,
                            PersistentPhase.CONFIRMING,
                            PersistentPhase.REVALIDATING,
                            PersistentPhase.ARMED,
                        },
                    )
                )
            except Exception:
                pass

    emit(PersistentPhase.REVALIDATING)
    if token.is_cancelled:
        return _terminal(
            prepared,
            success=False,
            status="cancelled-before-write",
            error_code=ErrorCode.CANCELLED,
            message="operation cancelled during revalidation",
            trace=trace,
        )

    try:
        value = backend.execute_persistent(intent, token, emit)
    except CooperativeCancellationError:
        return _terminal(
            prepared,
            success=False,
            status="cancelled-before-write",
            error_code=ErrorCode.CANCELLED,
            message="operation cancelled before the first persistent write",
            trace=trace,
            writing_started=PersistentPhase.WRITING in trace,
        )
    except PersistentBackendFailure as exc:
        reconciled = bool(exc.reconciliation_completed)
        post_validated = bool(exc.post_validation_completed) and reconciled
        return _terminal(
            prepared,
            success=False,
            status=(
                "failed-after-write"
                if PersistentPhase.WRITING in trace
                else "revalidation-refused"
            ),
            error_code=ErrorCode.BACKEND_FAILURE,
            message="persistent operation refused or failed",
            trace=trace,
            writing_started=PersistentPhase.WRITING in trace,
            reconciliation_completed=reconciled,
            post_validation_completed=post_validated,
        )
    except Exception:
        return _terminal(
            prepared,
            success=False,
            status=(
                "failed-after-write"
                if PersistentPhase.WRITING in trace
                else "revalidation-refused"
            ),
            error_code=ErrorCode.BACKEND_FAILURE,
            message="persistent operation refused or failed",
            trace=trace,
            writing_started=PersistentPhase.WRITING in trace,
            reconciliation_completed=False,
            post_validation_completed=False,
        )

    reconciled = bool(value.reconciliation_completed)
    post_validated = bool(value.post_validation_completed) and reconciled
    required_success_phases = (
        PersistentPhase.ARMED,
        PersistentPhase.WRITING,
        PersistentPhase.RECONCILING,
        PersistentPhase.POST_VALIDATING,
    )
    lifecycle_complete = all(phase in trace for phase in required_success_phases)
    if not (reconciled and post_validated and lifecycle_complete):
        return _terminal(
            prepared,
            success=False,
            status=(
                "failed-after-write"
                if PersistentPhase.WRITING in trace
                else "revalidation-refused"
            ),
            error_code=ErrorCode.BACKEND_FAILURE,
            message="persistent operation refused or failed",
            trace=trace,
            writing_started=PersistentPhase.WRITING in trace,
            reconciliation_completed=reconciled,
            post_validation_completed=post_validated,
        )

    return _terminal(
        prepared,
        success=True,
        status="completed",
        error_code=None,
        message="persistent operation completed and fully validated",
        trace=trace,
        writing_started=True,
        reconciliation_completed=True,
        post_validation_completed=True,
        enabled_profiles=value.enabled_profiles,
        safety_backup_name=value.safety_backup_name,
    )


def _finish_accepted_record(
    preparation_id: str,
    accepted_record: _IssuedPreparation,
) -> None:
    with _REGISTRY_LOCK:
        current = _PREPARATIONS.get(preparation_id)
        accepted_record.executing = False
        if current is accepted_record:
            _retire_locked(
                preparation_id,
                accepted_record,
                consumed=True,
            )
        _reclaim_dead_locked()
        _trim_tombstones_locked()


def execute_prepared(
    owner,
    backend: Backend,
    coordinator: OperationCoordinator,
    prepared: PreparedOperation,
    confirmation: str,
    *,
    cancellation: CancellationToken | None = None,
    observer: Callable[[PersistentPhaseSnapshot], None] | None = None,
) -> OperationResult[PersistentExecutionResult]:
    token = cancellation or CancellationToken()
    trace: list[PersistentPhase] = [PersistentPhase.CONFIRMING]

    if confirmation.strip() != prepared.required_confirmation_phrase:
        return _terminal(
            prepared,
            success=False,
            status="confirmation-refused",
            error_code=ErrorCode.SAFETY_REFUSAL,
            message="exact typed confirmation was not supplied",
            trace=trace,
        )
    if token.is_cancelled:
        return _terminal(
            prepared,
            success=False,
            status="cancelled-before-acceptance",
            error_code=ErrorCode.CANCELLED,
            message="operation cancelled before execution was accepted",
            trace=trace,
        )

    with _REGISTRY_LOCK:
        _reclaim_dead_locked()
        record = _PREPARATIONS.get(prepared.preparation_id)
        if record is None:
            retired = _retired_terminal(owner, prepared, trace)
            if retired is not None:
                return retired
            return _terminal(
                prepared,
                success=False,
                status="unknown-preparation",
                error_code=ErrorCode.UNKNOWN_PREPARATION,
                message="preparation was not issued by this application",
                trace=trace,
            )
        if record.owner() is not owner:
            return _terminal(
                prepared,
                success=False,
                status="unknown-preparation",
                error_code=ErrorCode.UNKNOWN_PREPARATION,
                message="preparation was not issued by this application",
                trace=trace,
            )
        if prepared != record.public:
            return _terminal(
                prepared,
                success=False,
                status="tampered-preparation",
                error_code=ErrorCode.STALE_PREPARATION,
                message="prepared review data no longer matches the issued preparation",
                trace=trace,
            )
        if record.consumed:
            return _terminal(
                prepared,
                success=False,
                status="consumed-preparation",
                error_code=ErrorCode.CONSUMED_PREPARATION,
                message="preparation has already been consumed; prepare again",
                trace=trace,
            )

    try:
        # Use the coordinator as a real context manager. Once __enter__ has
        # acquired the process-wide claim, Python establishes __exit__ cleanup
        # before any accepted-execution code runs. BaseException (including
        # KeyboardInterrupt/SystemExit) still propagates and the accepted record
        # is retired in the finally block below without catching BaseException.
        with coordinator.claim():
            with _REGISTRY_LOCK:
                _reclaim_dead_locked()
                record = _PREPARATIONS.get(prepared.preparation_id)
                if record is None:
                    retired = _retired_terminal(owner, prepared, trace)
                    if retired is not None:
                        return retired
                    return _terminal(
                        prepared,
                        success=False,
                        status="stale-preparation",
                        error_code=ErrorCode.STALE_PREPARATION,
                        message="preparation changed before execution claim",
                        trace=trace,
                    )
                if record.owner() is not owner or prepared != record.public:
                    return _terminal(
                        prepared,
                        success=False,
                        status="stale-preparation",
                        error_code=ErrorCode.STALE_PREPARATION,
                        message="preparation changed before execution claim",
                        trace=trace,
                    )
                if record.consumed:
                    return _terminal(
                        prepared,
                        success=False,
                        status="consumed-preparation",
                        error_code=ErrorCode.CONSUMED_PREPARATION,
                        message="preparation has already been consumed; prepare again",
                        trace=trace,
                    )
                record.consumed = True
                record.executing = True
                intent = record.intent
                accepted_record = record

            try:
                return _execute_accepted(
                    backend,
                    prepared,
                    intent,
                    token,
                    trace,
                    observer,
                )
            finally:
                _finish_accepted_record(
                    prepared.preparation_id,
                    accepted_record,
                )
    except OperationBusyError:
        with _REGISTRY_LOCK:
            record = _PREPARATIONS.get(prepared.preparation_id)
            tombstone = _TOMBSTONES.get(prepared.preparation_id)
            consumed = bool(
                (record and record.consumed)
                or (
                    tombstone
                    and tombstone.owner() is owner
                    and tombstone.consumed
                )
            )
        return _terminal(
            prepared,
            success=False,
            status="consumed-preparation" if consumed else "busy",
            error_code=(
                ErrorCode.CONSUMED_PREPARATION if consumed else ErrorCode.BUSY
            ),
            message=(
                "preparation has already been consumed; prepare again"
                if consumed else "another hardware-facing operation is already active"
            ),
            trace=trace,
        )
