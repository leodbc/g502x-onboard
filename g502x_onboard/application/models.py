from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Generic, TypeVar


class PrivacyClass(str, Enum):
    """Classification carried by every adapter-facing application payload."""

    SHAREABLE = "privacy-safe"
    PRIVACY_SAFE = "privacy-safe"
    LOCAL_SENSITIVE = "user-authored"
    USER_AUTHORED = "user-authored"
    PRIVATE_DIAGNOSTIC = "private-diagnostic"


class ErrorCode(str, Enum):
    INVALID_INPUT = "invalid-input"
    CONFIG_ERROR = "config-error"
    READ_ONLY = "read-only"
    SAFETY_REFUSAL = "safety-refusal"
    BUSY = "busy"
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
    detail: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.detail is not None and self.privacy is PrivacyClass.SHAREABLE:
            raise ValueError("shareable errors cannot carry private diagnostic detail")


T = TypeVar("T")


@dataclass(frozen=True)
class OperationResult(Generic[T]):
    ok: bool
    value: T | None = None
    error: ApplicationError | None = None
    privacy: PrivacyClass = PrivacyClass.SHAREABLE

    def __post_init__(self) -> None:
        if self.ok:
            if self.error is not None:
                raise ValueError("successful results cannot contain an error")
            if self.value is None:
                raise ValueError("successful results require a typed value")
            value_privacy = getattr(self.value, "privacy", None)
            if value_privacy is not None and value_privacy != self.privacy:
                raise ValueError("result privacy must match typed value privacy")
            return

        if self.error is None:
            raise ValueError("failed results require a typed error")
        if self.value is not None:
            raise ValueError("failed results cannot carry a stale value")
        if self.error.privacy != self.privacy:
            raise ValueError("result privacy must match error privacy")


@dataclass(frozen=True)
class CompatibilityObservation:
    architecture: str
    transport: str
    identity: str
    write_allowed: bool
    eligibility: WriteEligibility

    def __post_init__(self) -> None:
        expected = self.eligibility is WriteEligibility.ELIGIBLE
        if self.write_allowed != expected:
            raise ValueError("write_allowed and eligibility must describe the same policy")


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
    review: ApplyReview = field(repr=False)
    plan_digest: str = field()
    active_baseline_binding: str = field(repr=False)
    exact_unit_binding: str = field(repr=False)
    compatibility: CompatibilityObservation
    observed_preconditions: tuple[str, ...]
    host_guard_clear: bool
    required_confirmation_phrase: str
    privacy: PrivacyClass = PrivacyClass.PRIVATE_DIAGNOSTIC


@dataclass(frozen=True)
class PreparationContext:
    """Backend-only snapshot consumed while constructing a preparation."""

    baseline_images: tuple[tuple[int, bytes], ...] = field(repr=False)
    active_baseline_binding: str = field(repr=False)
    exact_unit_binding: str = field(repr=False)
    compatibility: CompatibilityObservation
    active_profile: int | None
    validation_ok: bool
    host_guard_clear: bool
    observed_preconditions: tuple[str, ...] = field(default_factory=tuple)

    def baseline_map(self) -> dict[int, bytes]:
        return dict(self.baseline_images)


@dataclass(frozen=True)
class ProbeDetails:
    """CLI-compatible probe payload with an explicit privacy class."""

    payload: dict[str, Any] = field(repr=False)
    privacy: PrivacyClass = PrivacyClass.SHAREABLE


@dataclass(frozen=True)
class ValidationDetails:
    """Validation data needed by both JSON and text adapters."""

    ok: bool
    enabled_profiles: tuple[int, ...]
    referenced_macro_starts: int
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
    summary: dict[str, Any] = field(repr=False)
    privacy: PrivacyClass = PrivacyClass.LOCAL_SENSITIVE


@dataclass(frozen=True)
class StatusSnapshot:
    """Observed status plus the already-redacted summary selected by the caller."""

    active_profile: int | None
    descriptor: dict[str, Any] = field(repr=False)
    summary: dict[str, Any] = field(repr=False)
    enabled_profiles: tuple[int, ...] = ()
    privacy: PrivacyClass = PrivacyClass.SHAREABLE


@dataclass(frozen=True)
class InspectSnapshot:
    rows: tuple[dict[str, Any], ...] = field(repr=False)
    enabled_profiles: tuple[int, ...] = ()
    privacy: PrivacyClass = PrivacyClass.SHAREABLE


@dataclass(frozen=True)
class PlanSnapshot:
    config_path: str
    plan: dict[str, Any] = field(repr=False)
    rendered_json: str = field(repr=False)
    privacy: PrivacyClass = PrivacyClass.LOCAL_SENSITIVE


@dataclass(frozen=True)
class CapacitySnapshot:
    config_path: str | None = None
    source_bytes: int | None = None
    allocated_bytes: int | None = None
    jump_overhead: int | None = None
    fragmentation_waste: int | None = None
    raw_free_bytes: int | None = None
    usable_free_bytes: int | None = None
    privacy: PrivacyClass = PrivacyClass.LOCAL_SENSITIVE


@dataclass(frozen=True)
class SetupSnapshot:
    root: str = field(repr=False)
    fingerprint: str = field(repr=False)
    architecture: str = "unknown"
    transport: str = "unknown"
    write_allowed: bool = False
    privacy: PrivacyClass = PrivacyClass.PRIVATE_DIAGNOSTIC


@dataclass(frozen=True)
class BaselineListSnapshot:
    rows: tuple[dict[str, Any], ...] = field(repr=False)
    privacy: PrivacyClass = PrivacyClass.SHAREABLE


@dataclass(frozen=True)
class BaselineShowSnapshot:
    payload: dict[str, Any] = field(repr=False)
    privacy: PrivacyClass = PrivacyClass.SHAREABLE


@dataclass(frozen=True)
class BaselineUseSnapshot:
    root: str = field(repr=False)
    privacy: PrivacyClass = PrivacyClass.PRIVATE_DIAGNOSTIC


@dataclass(frozen=True)
class PublicReportSnapshot:
    payload: dict[str, Any] = field(repr=False)
    privacy: PrivacyClass = PrivacyClass.SHAREABLE


@dataclass(frozen=True)
class ReportCheckResult:
    format: str
    privacy: PrivacyClass = PrivacyClass.SHAREABLE


@dataclass(frozen=True)
class DebugExportSnapshot:
    payload: dict[str, Any] = field(repr=False)
    default_directory: str = field(repr=False)
    privacy: PrivacyClass = PrivacyClass.PRIVATE_DIAGNOSTIC


@dataclass(frozen=True)
class BackupSnapshot:
    name: str
    privacy: PrivacyClass = PrivacyClass.PRIVATE_DIAGNOSTIC


@dataclass(frozen=True)
class ReadonlySmokeSnapshot:
    report_name: str
    checkpoint_name: str = field(repr=False)
    architecture: str = "unknown"
    transport: str = "unknown"
    profile_format: int | None = None
    macro_format: int | None = None
    sector_count: int | None = None
    sector_size: int | None = None
    enabled_profiles: tuple[int, ...] = ()
    macro_starts: int = 0
    privacy: PrivacyClass = PrivacyClass.PRIVATE_DIAGNOSTIC
