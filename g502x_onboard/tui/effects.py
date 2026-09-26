from __future__ import annotations

from dataclasses import dataclass

from g502x_onboard.application.models import PersistentOperationKind, PreparedOperation
from .model import OperationAction, OperationId


@dataclass(frozen=True)
class StartForegroundOperation:
    operation_id: OperationId
    action: OperationAction
    config_path: str | None = None
    profile_target: int | None = None
    confirmation: str | None = None


@dataclass(frozen=True)
class PreparePersistentOperation:
    operation_id: OperationId
    kind: PersistentOperationKind
    config_path: str | None = None
    backup_path: str | None = None


@dataclass(frozen=True)
class ExecutePreparedOperation:
    operation_id: OperationId
    prepared: PreparedOperation
    confirmation: str


@dataclass(frozen=True)
class RequestCooperativeCancellation:
    operation_id: OperationId


@dataclass(frozen=True)
class RequestExplicitRefresh:
    operation_id: OperationId


Effect = (
    StartForegroundOperation
    | PreparePersistentOperation
    | ExecutePreparedOperation
    | RequestCooperativeCancellation
    | RequestExplicitRefresh
)
