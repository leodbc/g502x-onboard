from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from itertools import count
import re

from g502x_onboard.application.models import (
    ApplicationError,
    ErrorCode,
    PersistentOperationKind,
    PersistentPhase,
    PreparedOperation,
    PrivacyClass,
)

_OPERATION_ID_RE = re.compile(r"op-[0-9a-f]{8,64}\Z")

_OPERATION_ISSUANCE_COUNTER = count(1)


def _next_operation_issuance() -> int:
    return next(_OPERATION_ISSUANCE_COUNTER)


class Route(str, Enum):
    HOME = "home"
    OPERATION = "operation"
    REVIEW = "review"
    CONFIRMATION = "confirmation"
    RESULT = "result"


class FocusIntent(str, Enum):
    PRIMARY = "primary"
    REVIEW = "review"
    CONFIRMATION = "confirmation"
    HELP = "help"


class OperationAction(str, Enum):
    PROBE = "probe"
    STATUS = "status"
    VALIDATE = "validate"
    PLAN = "plan"
    PROFILE_SWITCH = "profile-switch"
    REPORT = "report"
    PREPARE_PERSISTENT = "prepare-persistent"
    REFRESH = "refresh"


class TerminalOutcome(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"


@dataclass(frozen=True)
class OperationId:
    value: str
    issuance: int = field(
        default_factory=_next_operation_issuance,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if not _OPERATION_ID_RE.fullmatch(self.value):
            raise ValueError("operation id must be a short opaque token")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class PresentationPayload:
    message: str
    privacy: PrivacyClass


@dataclass(frozen=True)
class ForegroundOperation:
    operation_id: OperationId
    action: OperationAction
    hardware_affecting: bool
    persistent_kind: PersistentOperationKind | None = None
    phase: PersistentPhase | None = None
    started: bool = False
    cancellation_requested: bool = False
    cancellation_acknowledged: bool = False
    cancellation_deferred: bool = False
    application_cancellation_allowed: bool | None = None
    execution_requested: bool = False
    worker_fault_unresolved: bool = False

    @property
    def non_cancellable(self) -> bool:
        return self.phase in {
            PersistentPhase.WRITING,
            PersistentPhase.RECONCILING,
            PersistentPhase.POST_VALIDATING,
        }

    @property
    def cancellation_available(self) -> bool:
        if self.non_cancellable or self.phase in {
            PersistentPhase.SUCCEEDED,
            PersistentPhase.FAILED,
        }:
            return False
        if self.application_cancellation_allowed is not None:
            return self.application_cancellation_allowed
        return True


@dataclass(frozen=True)
class TerminalState:
    outcome: TerminalOutcome
    persistent_phase: PersistentPhase | None = None
    error_code: ErrorCode | None = None
    writing_started: bool = False
    reconciliation_completed: bool = False
    post_validation_completed: bool = False


@dataclass(frozen=True)
class TuiModel:
    route: Route = Route.HOME
    focus: FocusIntent = FocusIntent.PRIMARY
    surface_privacy: PrivacyClass = PrivacyClass.SHAREABLE
    read_only: bool = False
    read_only_reason: ApplicationError | None = None
    active: ForegroundOperation | None = None
    prepared: PreparedOperation | None = None
    review_acknowledged: bool = False
    confirmation_input: str = ""
    config_path_input: str = ""
    backup_path_input: str = ""
    profile_target_input: str = "1"
    profile_confirmation_input: str = ""
    last_result: PresentationPayload | None = None
    last_error: ApplicationError | None = None
    terminal: TerminalState | None = None
    help_open: bool = False
    disclosure: PresentationPayload | None = None
    transient_notice: PresentationPayload | None = None


def privacy_allows(surface: PrivacyClass, payload: PrivacyClass) -> bool:
    if surface is PrivacyClass.PRIVATE_DIAGNOSTIC:
        return True
    if surface is PrivacyClass.LOCAL_SENSITIVE:
        return payload is not PrivacyClass.PRIVATE_DIAGNOSTIC
    return payload is PrivacyClass.SHAREABLE
