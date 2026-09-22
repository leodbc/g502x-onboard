from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .constants import (
    ALIASES,
    CONSUMER_USAGES,
    DIRECT_MOUSE_MASKS,
    KEYS,
    MODS,
    PROFILE3_TARGET_ORDER,
    PROGRAMMABLE_PROFILES,
    REPORT_RATE_CODES,
    REQUIRES_HOST_ACTIONS,
)


class ConfigError(ValueError):
    pass


def _err(path: str, message: str) -> ConfigError:
    return ConfigError(f"{path}: {message}")


def _check_keys(obj: dict[str, Any], allowed: set[str], path: str) -> None:
    extra = sorted(set(obj) - allowed)
    if extra:
        raise _err(path, "unknown field(s): " + ", ".join(extra))


def _validate_action(spec: Any, path: str) -> None:
    if isinstance(spec, str):
        if spec.lower() not in ALIASES:
            raise _err(path, f"unknown alias {spec!r}")
        return
    if not isinstance(spec, dict):
        raise _err(path, "binding must be an alias string or object")

    kind = str(spec.get("action", "")).lower()
    if not kind:
        raise _err(path, "missing action")
    if kind in REQUIRES_HOST_ACTIONS:
        raise _err(path, f"HOST_REQUIRED action is not available as an onboard action: {kind}")

    if kind == "hotkey":
        _check_keys(spec, {"action", "keys"}, path)
        keys = spec.get("keys")
        if not isinstance(keys, list) or len(keys) < 2:
            raise _err(path, "hotkey.keys requires modifier(s) + normal key")
        names = [str(x).upper() for x in keys]
        for mod in names[:-1]:
            if mod not in MODS:
                raise _err(path, f"unsupported modifier {mod!r}")
        if names[-1] in MODS or names[-1] not in KEYS:
            raise _err(path, f"unsupported final key {names[-1]!r}")
        return

    if kind == "tap":
        _check_keys(spec, {"action", "key"}, path)
        key = str(spec.get("key", "")).upper()
        if key not in KEYS and key not in MODS:
            raise _err(path, f"unsupported tap key {key!r}")
        return

    if kind == "text":
        _check_keys(spec, {"action", "value", "layout"}, path)
        if not isinstance(spec.get("value"), str):
            raise _err(path, "text.value must be a string")
        if spec.get("layout", "basic") not in ("basic", "us"):
            raise _err(path, "text.layout must be 'basic' or 'us'")
        return

    if kind == "delay":
        _check_keys(spec, {"action", "ms"}, path)
        ms = spec.get("ms")
        if not isinstance(ms, int) or not 0 <= ms <= 65535:
            raise _err(path, "delay.ms must be 0..65535")
        return

    if kind == "consumer":
        _check_keys(spec, {"action", "command"}, path)
        if str(spec.get("command", "")).lower() not in CONSUMER_USAGES:
            raise _err(path, "unsupported consumer command")
        return

    if kind == "mouse":
        _check_keys(spec, {"action", "command"}, path)
        if str(spec.get("command", "")).lower() not in DIRECT_MOUSE_MASKS:
            raise _err(path, "unsupported direct mouse command")
        return

    if kind in ("wheel", "horizontal_wheel"):
        _check_keys(spec, {"action", "amount"}, path)
        amount = spec.get("amount")
        if not isinstance(amount, int) or amount == 0 or not -127 <= amount <= 127:
            raise _err(path, "amount must be -127..127 except 0")
        return

    if kind in ("wait_for_release", "repeat_while_pressed"):
        _check_keys(spec, {"action"}, path)
        return

    if kind == "sequence":
        _check_keys(spec, {"action", "steps"}, path)
        steps = spec.get("steps")
        if not isinstance(steps, list) or not steps:
            raise _err(path, "sequence.steps must be non-empty")
        for index, step in enumerate(steps):
            _validate_action(step, f"{path}.steps[{index}]")
            if isinstance(step, dict) and str(step.get("action", "")).lower() == "mouse":
                raise _err(
                    f"{path}.steps[{index}]",
                    "mouse is direct-only; VM mouse down/up is unresolved",
                )
        return

    raise _err(path, f"unknown action {kind!r}")


def _validate_settings(settings: Any, path: str) -> dict[str, Any]:
    if settings is None:
        return {}
    if not isinstance(settings, dict):
        raise _err(path, "settings must be an object")
    _check_keys(
        settings,
        {"name", "polling_rate_hz", "dpi", "default_dpi", "shift_dpi"},
        path,
    )

    out = dict(settings)
    if "name" in out:
        if not isinstance(out["name"], str):
            raise _err(path + ".name", "must be a string")
        if len(out["name"].encode("utf-16le")) > 48:
            raise _err(path + ".name", "exceeds 24 UTF-16 code units")

    if "polling_rate_hz" in out and out["polling_rate_hz"] not in REPORT_RATE_CODES:
        raise _err(path + ".polling_rate_hz", "must be 1000, 500, 250, or 125")

    if "dpi" in out:
        dpi = out["dpi"]
        if not isinstance(dpi, list) or not 1 <= len(dpi) <= 5:
            raise _err(path + ".dpi", "must contain 1..5 integer values")
        for index, value in enumerate(dpi):
            if not isinstance(value, int) or not 1 <= value <= 65535:
                raise _err(path + f".dpi[{index}]", "must be 1..65535")

    for key in ("default_dpi", "shift_dpi"):
        if key in out:
            value = out[key]
            if not isinstance(value, int) or not 1 <= value <= 65535:
                raise _err(path + "." + key, "must be 1..65535")
            if "dpi" in out and value not in out["dpi"]:
                raise _err(path + "." + key, "must match one of the values in settings.dpi")

    return out

def _is_consumer_spec(spec: Any) -> bool:
    if isinstance(spec, str):
        alias = ALIASES.get(spec.lower())
        return bool(alias and alias.get("action") == "consumer")
    return (
        isinstance(spec, dict)
        and str(spec.get("action", "")).lower() == "consumer"
    )


def _validate_layer(layer: Any, path: str, layer_name: str) -> dict[str, Any]:
    if layer is None:
        return {}
    if not isinstance(layer, dict):
        raise _err(path, "must be an object")
    out = {}
    for raw_target, spec in layer.items():
        target = str(raw_target).upper()
        if target not in PROFILE3_TARGET_ORDER:
            raise _err(path, f"unknown target {raw_target!r}")
        _validate_action(spec, f"{path}.{target}")

        if target in ("WHEEL_DOWN", "WHEEL_UP"):
            if layer_name == "NORMAL":
                raise _err(
                    f"{path}.{target}",
                    "custom NORMAL wheel-event bindings are not hardware validated",
                )
            if not _is_consumer_spec(spec):
                raise _err(
                    f"{path}.{target}",
                    "GSHIFT wheel slots are validated only for direct Consumer HID",
                )

        out[target] = spec
    return out


def validate_config(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ConfigError("root: config must be an object")
    _check_keys(payload, {"format", "profiles"}, "root")

    if payload.get("format", 1) != 1:
        raise _err("root.format", "supported config format is 1")

    profiles = payload.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise _err("root.profiles", "must be a non-empty object")

    normalized: dict[int, dict[str, Any]] = {}
    for raw_profile, raw_block in profiles.items():
        try:
            profile_num = int(raw_profile)
        except Exception as exc:
            raise _err("root.profiles", f"invalid profile key {raw_profile!r}") from exc

        if profile_num not in PROGRAMMABLE_PROFILES:
            raise _err(f"root.profiles.{raw_profile}", "only profiles 2, 3, 4, 5 are programmable")
        if profile_num in normalized:
            raise _err("root.profiles", f"duplicate profile {profile_num}")
        if not isinstance(raw_block, dict):
            raise _err(f"root.profiles.{profile_num}", "must be an object")

        path = f"root.profiles.{profile_num}"
        _check_keys(raw_block, {"settings", "buttons", "g_shift_button", "g_shift"}, path)

        buttons = _validate_layer(raw_block.get("buttons"), path + ".buttons", "NORMAL")
        g_shift = _validate_layer(raw_block.get("g_shift"), path + ".g_shift", "GSHIFT")
        settings = _validate_settings(raw_block.get("settings"), path + ".settings")

        g_shift_button = raw_block.get("g_shift_button")
        if g_shift_button is not None:
            g_shift_button = str(g_shift_button).upper()
            if g_shift_button not in PROFILE3_TARGET_ORDER[:-2]:
                raise _err(path + ".g_shift_button", "must be a physical mouse button")
            if g_shift_button in buttons:
                raise _err(
                    path + ".g_shift_button",
                    "same button cannot be both a normal action and G-Shift activator",
                )
        elif g_shift:
            raise _err(path + ".g_shift", "requires g_shift_button")

        normalized[profile_num] = {
            "settings": settings,
            "buttons": buttons,
            "g_shift_button": g_shift_button,
            "g_shift": g_shift,
        }

    if 2 not in normalized:
        raise _err("root.profiles", "Profile 2 is required so omitted programmable state is never ambiguous")

    return {"format": 1, "profiles": normalized}

def load_config(path: str | Path) -> tuple[Path, dict[str, Any]]:
    resolved = Path(path).resolve()
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"{resolved}:{exc.lineno}:{exc.colno}: invalid JSON: {exc.msg}"
        ) from exc
    if not isinstance(payload, dict) or "format" not in payload:
        raise ConfigError(f"{resolved}: root.format is required")
    return resolved, validate_config(payload)
