from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Generic, TypeVar


class PrivacyClass(str, Enum):
    """Classification carried by every adapter-facing application payload."""

    SHAREABLE = "privacy-safe"
    PRIVACY_SAFE = "privacy-safe"
    LOCAL_SENSITIVE = "user-authored"
    USER_AUTHORED = "user-authored"
    PRIVATE_DIAGNOSTIC = "private-diagnostic"


class ErrorCode(str, Enum):
    INVALID_INPUT = "invalid-input"
    READ_ONLY = "read-only"
    SAFETY_REFUSAL = "safety-refusal"
    BACKEND_FAILURE = "backend-failure"


class WriteEligibility(str, Enum):
    ELIGIBLE = "eligible"
    READ_ONLY = "read-only"


class PersistentOperationKind(str, Enum):
    APPLY_CONFIG = "apply-config"
    RESTORE_BACKUP = "restore-backup"
    RESTORE_BASELINE = "restore-baseline"


@dataclass(frozen=True)
class ApplicationError:
    code: ErrorCode
    message: str
    privacy: PrivacyClass


T = TypeVar("T")


@dataclass(frozen=True)
class OperationResult(Generic[T]):
    ok: bool
    value: T | None = None
    error: ApplicationError | None = None
    privacy: PrivacyClass = PrivacyClass.SHAREABLE

    def __post_init__(self) -> None:
        if self.ok == (self.error is not None):
            raise ValueError("successful results cannot contain an error and failures must contain one")
        if self.ok and self.value is None:
            raise ValueError("successful results require a typed value")


@dataclass(frozen=True)
class CompatibilityObservation:
    architecture: str
    transport: str
    identity: str
    write_allowed: bool
    eligibility: WriteEligibility


@dataclass(frozen=True)
class ProbeSnapshot:
    device_name: str | None
    protocol: str | None
    active_profile: int | None
    compatibility: CompatibilityObservation
    privacy: PrivacyClass = PrivacyClass.SHAREABLE


@dataclass(frozen=True)
class ValidationSnapshot:
    ok: bool
    enabled_profiles: tuple[int, ...]
    error_count: int
    warning_count: int
    referenced_macro_starts: int
    recovery_ok: bool
    privacy: PrivacyClass = PrivacyClass.SHAREABLE


@dataclass(frozen=True)
class ProfileSwitchResult:
    active_profile: int
    confirmation_phrase: str
    privacy: PrivacyClass = PrivacyClass.SHAREABLE


@dataclass(frozen=True)
class ApplyReview:
    config_name: str
    enabled_profiles: tuple[int, ...]
    profile_names: tuple[tuple[int, str], ...]
    managed_sectors: tuple[int, ...]
    warnings: tuple[str, ...]
    plan_digest: str
    privacy: PrivacyClass = PrivacyClass.LOCAL_SENSITIVE


@dataclass(frozen=True)
class PreparedOperation:
    """Immutable Phase-1 review data; this object is not write authority."""

    preparation_id: str
    kind: PersistentOperationKind
    review: ApplyReview
    plan_digest: str
    active_baseline_binding: str
    exact_unit_binding: str
    compatibility: CompatibilityObservation
    observed_preconditions: tuple[str, ...]
    host_guard_clear: bool
    required_confirmation_phrase: str
    privacy: PrivacyClass = PrivacyClass.PRIVATE_DIAGNOSTIC


@dataclass(frozen=True)
class PreparationContext:
    """Backend-only snapshot consumed while constructing a preparation."""

    baseline_images: tuple[tuple[int, bytes], ...]
    active_baseline_binding: str
    exact_unit_binding: str
    compatibility: CompatibilityObservation
    active_profile: int | None
    validation_ok: bool
    host_guard_clear: bool
    observed_preconditions: tuple[str, ...] = field(default_factory=tuple)

    def baseline_map(self) -> dict[int, bytes]:
        return dict(self.baseline_images)
