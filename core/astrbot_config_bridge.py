"""Apply the explicitly enabled AstrBot settings to the shared business config."""
from collections.abc import Mapping
from copy import deepcopy
import json
from pathlib import Path
import sys


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "_conf_schema.json"
_REDACTED = {"[已隐藏]", "[hidden]", "******", "********"}


def _filtered_patch(values, schema, path=()):
    patch = {}
    for key, node in schema.items():
        if key not in values:
            continue
        value = values[key]
        field_path = path + (key,)
        kind = node.get("type")
        if kind == "object":
            if not isinstance(value, Mapping):
                raise TypeError(f"Invalid configuration type: {'.'.join(field_path)}")
            children = _filtered_patch(value, node.get("items", {}), field_path)
            if children:
                patch[key] = children
            continue
        expected = {"bool": bool, "int": int, "float": (int, float),
                    "string": str, "text": str, "list": list}.get(kind)
        if expected is None or not isinstance(value, expected) or (
            kind in {"int", "float"} and isinstance(value, bool)
        ):
            raise TypeError(f"Invalid configuration type: {'.'.join(field_path)}")
        if kind == "list" and any(not isinstance(item, str) for item in value):
            raise TypeError(f"Invalid configuration list: {'.'.join(field_path)}")
        if node.get("secret") and (not value.strip() or value.strip().lower() in _REDACTED):
            continue
        patch[key] = deepcopy(value)
    return patch


def _merge(target, patch):
    for key, value in patch.items():
        if isinstance(value, dict):
            if not isinstance(target.get(key), dict):
                target[key] = {}
            _merge(target[key], value)
        else:
            target[key] = deepcopy(value)


def apply_astrbot_config(values):
    """Persist an opted-in, validated patch without dropping unexposed settings."""
    if not isinstance(values, Mapping):
        raise TypeError("AstrBot configuration must be a mapping")
    enabled = values.get("use_astrbot_config", False)
    if not isinstance(enabled, bool):
        raise TypeError("use_astrbot_config must be a boolean")
    if not enabled:
        shared = sys.modules.get(f"{__package__}.config")
        if shared is not None and Path(shared.CONFIG_FILE).exists():
            _sync_loaded_config(shared, shared.load_config())
        return

    with SCHEMA_PATH.open(encoding="utf-8") as stream:
        schema = json.load(stream)
    schema.pop("use_astrbot_config", None)
    schema.pop("apply_sections", None)
    sections = values.get("apply_sections", [])
    if not isinstance(sections, list) or any(not isinstance(name, str) for name in sections):
        raise TypeError("apply_sections must be a list of configuration group names")
    if any(name not in schema for name in sections):
        raise ValueError("apply_sections contains an unknown configuration group")
    if not sections:
        shared = sys.modules.get(f"{__package__}.config")
        if shared is not None and Path(shared.CONFIG_FILE).exists():
            _sync_loaded_config(shared, shared.load_config())
        return
    patch = _filtered_patch(values, {name: schema[name] for name in sections})

    from . import config as shared

    path = Path(shared.CONFIG_FILE)
    if path.exists():
        with path.open(encoding="utf-8") as stream:
            existing = json.load(stream)
        if not isinstance(existing, dict):
            raise ValueError("Business configuration must be a JSON object")
        safety = existing.get("reply_safety")
        if isinstance(safety, dict) and "blocked_keywords" in safety:
            safety["blocked_keywords"] = shared.load_config().get("reply_safety", {}).get(
                "blocked_keywords", []
            )
    else:
        existing = deepcopy(shared.DEFAULT_CONFIG)
    _merge(existing, patch)
    if not shared.save_config(deepcopy(existing)):
        raise OSError("Unable to save business configuration")
    _sync_loaded_config(shared, shared.load_config())


def _sync_loaded_config(shared, updated):
    shared.config.clear()
    shared.config.update(updated)
    app = sys.modules.get(f"{__package__.rsplit('.', 1)[0]}.cli.app")
    if app is not None and isinstance(getattr(app, "config", None), dict):
        app.config.clear()
        app.config.update(deepcopy(updated))
