from __future__ import annotations

from dataclasses import dataclass

from g502x_onboard.application.models import (
    ApplicationError,
    PersistentExecutionResult,
    PersistentOperationKind,
    PersistentPhaseSnapshot,
    PreparedOperation,
    PrivacyClass,
)
from .model import FocusIntent, OperationAction, OperationId, PresentationPayload, Route


@dataclass(frozen=True)
class Navigate:
    route: Route
    focus: FocusIntent = FocusIntent.PRIMARY


@dataclass(frozen=True)
class SetHelp:
    open: bool


@dataclass(frozen=True)
class SetDisclosure:
    payload: PresentationPayload | None


@dataclass(frozen=True)
class ChangePrivacySurface:
    privacy: PrivacyClass


@dataclass(frozen=True)
class CompatibilityChanged:
    read_only: bool
    reason: ApplicationError | None = None


@dataclass(frozen=True)
class OperationRequested:
    operation_id: OperationId
    action: OperationAction
    hardware_affecting: bool = True
    persistent_kind: PersistentOperationKind | None = None


@dataclass(frozen=True)
class OperationStarted:
    operation_id: OperationId


@dataclass(frozen=True)
class PreparedReceived:
    operation_id: OperationId
    prepared: PreparedOperation


@dataclass(frozen=True)
class EnterReview:
    operation_id: OperationId


@dataclass(frozen=True)
class ReviewAcknowledged:
    operation_id: OperationId
    acknowledged: bool


@dataclass(frozen=True)
class ConfirmationChanged:
    operation_id: OperationId
    value: str


@dataclass(frozen=True)
class ConfirmationSubmitted:
    operation_id: OperationId


@dataclass(frozen=True)
class PersistentProgress:
    operation_id: OperationId
    snapshot: PersistentPhaseSnapshot


@dataclass(frozen=True)
class ApplicationCompleted:
    operation_id: OperationId
    message: str
    privacy: PrivacyClass = PrivacyClass.SHAREABLE


@dataclass(frozen=True)
class PersistentCompleted:
    operation_id: OperationId
    result: PersistentExecutionResult


@dataclass(frozen=True)
class ApplicationFailed:
    operation_id: OperationId
    error: ApplicationError


@dataclass(frozen=True)
class PreparationInvalidated:
    operation_id: OperationId
    error: ApplicationError


@dataclass(frozen=True)
class CancellationRequested:
    operation_id: OperationId


@dataclass(frozen=True)
class CancellationAcknowledged:
    operation_id: OperationId


@dataclass(frozen=True)
class RefreshRequested:
    operation_id: OperationId
