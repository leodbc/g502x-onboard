from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Callable
from weakref import WeakKeyDictionary

from ..codec import build_plan, plan_json, profile_display_metadata
from ..config import ConfigError, load_config
from ..constants import GLOBAL_MACRO_SECTORS, PROGRAMMABLE_PROFILES
from .backend import Backend
from .models import (
    ApplicationError,
    ApplyReview,
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


class ApplicationFacade:
    """Adapter-facing Phase-1 use cases; no direct persistent execution exists."""

    __slots__ = ("_id_factory", "__weakref__")

    def __init__(self, backend: Backend, *, id_factory: Callable[[], str] | None = None) -> None:
        # Do not retain the backend as facade instance state. A normal adapter
        # receiving only the public facade must not be able to walk an obvious
        # attribute path from the facade to RealBackend and its preserved
        # persistent primitives.
        _BACKENDS[self] = backend
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)

    def probe(self) -> OperationResult[ProbeSnapshot]:
        try:
            value = _BACKENDS[self].probe()
            return OperationResult(ok=True, value=value, privacy=value.privacy)
        except Exception:
            return self._backend_failure("probe failed")

    def validate(self) -> OperationResult[ValidationSnapshot]:
        try:
            value = _BACKENDS[self].validate()
            return OperationResult(ok=True, value=value, privacy=value.privacy)
        except Exception:
            return self._backend_failure("validation failed")

    def switch_profile(self, target: int, confirmation: str) -> OperationResult[ProfileSwitchResult]:
        if target not in (1, 2, 3, 4, 5):
            return self._failure(
                ErrorCode.INVALID_INPUT,
                "profile must be 1, 2, 3, 4, or 5",
                PrivacyClass.SHAREABLE,
            )
        phrase = "BACK TO SAFE" if target == 1 else f"ENTER PROFILE {target}"
        if confirmation.strip() != phrase:
            return self._failure(
                ErrorCode.SAFETY_REFUSAL,
                f"exact confirmation required: {phrase}",
                PrivacyClass.SHAREABLE,
            )
        try:
            active = _BACKENDS[self].switch_profile_guarded(target)
        except Exception:
            return self._backend_failure("profile switch refused or failed")
        value = ProfileSwitchResult(active_profile=active, confirmation_phrase=phrase)
        return OperationResult(ok=True, value=value, privacy=value.privacy)

    def prepare_apply(self, config_path: str | Path) -> OperationResult[PreparedOperation]:
        """Prepare APPLY CONFIG against a backend snapshot without persistent writes."""
        try:
            context = _BACKENDS[self].preparation_context()
        except Exception:
            return self._backend_failure("unable to observe preparation preconditions")

        if context.compatibility.eligibility is not WriteEligibility.ELIGIBLE:
            return self._failure(
                ErrorCode.READ_ONLY,
                "connected target is read-only; persistent preparation is refused",
                PrivacyClass.SHAREABLE,
            )
        if not context.host_guard_clear:
            return self._failure(
                ErrorCode.SAFETY_REFUSAL,
                "host guard is not clear",
                PrivacyClass.SHAREABLE,
            )

        try:
            path, config = load_config(config_path)
            plan = build_plan(config, context.baseline_map())
        except (ConfigError, OSError, ValueError) as exc:
            # Config filenames/content are user-authored. Do not surface raw
            # exception text as a default/shareable application error.
            del exc
            return self._failure(
                ErrorCode.INVALID_INPUT,
                "configuration could not be loaded or compiled",
                PrivacyClass.LOCAL_SENSITIVE,
            )
        except Exception:
            return self._backend_failure("configuration preparation failed")

        rendered_plan = plan_json(plan).encode("utf-8")
        digest = hashlib.sha256(rendered_plan).hexdigest()
        profile_names = []
        for profile in PROGRAMMABLE_PROFILES:
            row = plan["profiles"][profile]
            if row["disabled"]:
                continue
            profile_names.append(
                (profile, str(profile_display_metadata(row["profile"])["name"]))
            )

        managed_sectors = (0, *PROGRAMMABLE_PROFILES, *GLOBAL_MACRO_SECTORS)
        review = ApplyReview(
            config_name=path.name,
            enabled_profiles=tuple(int(p) for p in plan["enabled_profiles"]),
            profile_names=tuple(profile_names),
            managed_sectors=tuple(int(s) for s in managed_sectors),
            warnings=tuple(str(w) for w in plan["warnings"]),
            plan_digest=digest,
        )
        value = PreparedOperation(
            preparation_id=self._id_factory(),
            kind=PersistentOperationKind.APPLY_CONFIG,
            review=review,
            plan_digest=digest,
            active_baseline_binding=context.active_baseline_binding,
            exact_unit_binding=context.exact_unit_binding,
            compatibility=context.compatibility,
            observed_preconditions=context.observed_preconditions,
            host_guard_clear=context.host_guard_clear,
            required_confirmation_phrase="APPLY CONFIG",
        )
        return OperationResult(ok=True, value=value, privacy=value.privacy)

    @staticmethod
    def _failure(code: ErrorCode, message: str, privacy: PrivacyClass) -> OperationResult:
        return OperationResult(
            ok=False,
            error=ApplicationError(code=code, message=message, privacy=privacy),
            privacy=privacy,
        )

    @classmethod
    def _backend_failure(cls, message: str) -> OperationResult:
        # The raw exception remains backend/private state. The adapter receives
        # only a classified, stable summary.
        return cls._failure(ErrorCode.BACKEND_FAILURE, message, PrivacyClass.PRIVATE_DIAGNOSTIC)


_BACKENDS: WeakKeyDictionary[ApplicationFacade, Backend] = WeakKeyDictionary()
