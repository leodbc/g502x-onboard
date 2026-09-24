"""Public application facade and typed Phase-1 contracts."""

from .composition import create_application
from .facade import ApplicationFacade
from .models import (
    ApplicationError,
    ApplyReview,
    CompatibilityObservation,
    ErrorCode,
    OperationResult,
    PersistentOperationKind,
    PreparedOperation,
    PrivacyClass,
    ProbeSnapshot,
    ProfileSwitchResult,
    ValidationSnapshot,
    WriteEligibility,
)

__all__ = [
    "ApplicationError",
    "ApplicationFacade",
    "ApplyReview",
    "CompatibilityObservation",
    "ErrorCode",
    "OperationResult",
    "PersistentOperationKind",
    "PreparedOperation",
    "PrivacyClass",
    "ProbeSnapshot",
    "ProfileSwitchResult",
    "ValidationSnapshot",
    "WriteEligibility",
    "create_application",
]
