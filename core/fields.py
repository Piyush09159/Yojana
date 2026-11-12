"""The canonical field vocabulary (schemes/fields.yaml) and value checks against it.

load_fields()                  -> {name: spec}
check_value(name, value)       -> the value, normalised (raises FieldError if invalid)
validate_profile(profile)      -> (clean_profile, errors)  unknown or bad fields are dropped
profile_json_schema(names)     -> JSON schema for the LLM extractor
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import yaml

SCHEMES_DIR = Path(__file__).resolve().parent.parent / "schemes"
TYPES = {"bool", "int", "number", "enum", "str"}
NUMERIC = {"int", "number"}


class FieldError(ValueError):
    """Raised for an unknown field or a value of the wrong type."""


@lru_cache(maxsize=None)
def load_fields(directory: str | None = None) -> dict[str, dict]:
    folder = Path(directory) if directory else SCHEMES_DIR
    fields = yaml.safe_load((folder / "fields.yaml").read_text(encoding="utf-8"))
    for name, spec in fields.items():
        if spec.get("type") not in TYPES:
            raise FieldError(f"fields.yaml: {name} has bad type {spec.get('type')!r}")
        if spec["type"] == "enum" and not spec.get("values"):
            raise FieldError(f"fields.yaml: enum {name} needs 'values'")
        if not spec.get("description"):
            raise FieldError(f"fields.yaml: {name} needs a description")
    return fields


def check_value(name: str, value, fields: dict | None = None):
    """Return the value if it fits the field (ints may arrive as whole floats), else raise."""
    fields = fields or load_fields()
    spec = fields.get(name)
    if spec is None:
        raise FieldError(f"unknown field {name!r}")
    t = spec["type"]
    if t == "bool":
        if not isinstance(value, bool):
            raise FieldError(f"{name}: expected true/false, got {value!r}")
        return value
    if t in NUMERIC:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise FieldError(f"{name}: expected a number, got {value!r}")
        if t == "int":
            if value != int(value):
                raise FieldError(f"{name}: expected a whole number, got {value!r}")
            value = int(value)
        if "min" in spec and value < spec["min"] or "max" in spec and value > spec["max"]:
            raise FieldError(f"{name}: {value} is outside {spec.get('min')}..{spec.get('max')}")
        return value
    if t == "enum":
        if value not in spec["values"]:
            raise FieldError(f"{name}: {value!r} is not one of {spec['values']}")
        return value
    if not isinstance(value, str) or not value.strip():
        raise FieldError(f"{name}: expected text, got {value!r}")
    value = value.strip()
    if spec.get("normalize") == "key":                  # "Beed" / "Dharashiv" -> "beed" / "osmanabad"
        if re.search(r"[ऀ-ॿ]", value):                 # "बीड" -> "beed"
            from .name_match import devanagari_to_latin
            value = devanagari_to_latin(value)
        else:                                           # "Puné" -> "pune"
            value = "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))
        value = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
        value = (spec.get("aliases") or {}).get(value, value)
        if name == "district":
            value = known_district(value) or value
    return value


@lru_cache(maxsize=None)
def _districts() -> dict[str, str]:
    """Every known district key and alternate name -> the district key (schemes/geo/districts.yaml)."""
    path = SCHEMES_DIR / "geo" / "districts.yaml"
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for districts in (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).values():
        for key, aliases in (districts or {}).items():
            out[key] = key
            for a in aliases or []:
                out[a] = key
    return out


def known_district(value: str) -> str | None:
    """The known district this spelling means, or None. Exact key or alias first; otherwise the
    one district that sounds the same ("bhid" and "beed" both reduce to "bid"). Two candidates that
    sound alike are ambiguous, so neither is chosen."""
    names = _districts()
    if value in names:
        return names[value]
    from .name_match import phonetic
    sound = phonetic(value.replace("_", ""))
    hits = {key for name, key in names.items() if phonetic(name.replace("_", "")) == sound}
    return hits.pop() if len(hits) == 1 else None


def validate_profile(profile: dict, fields: dict | None = None) -> tuple[dict, list[str]]:
    """Keep only known, valid fields. Keys starting with '_' are internal state and pass through.
    None means "unknown" and passes through too."""
    fields = fields or load_fields()
    clean, errors = {}, []
    for name, value in profile.items():
        if name.startswith("_") or value is None:
            clean[name] = value
            continue
        try:
            clean[name] = check_value(name, value, fields)
        except FieldError as e:
            errors.append(str(e))
    return clean, errors


def profile_json_schema(names: list[str] | None = None, fields: dict | None = None,
                        brief: bool = False) -> dict:
    """JSON schema of an extracted profile: every property optional, null allowed for 'not said'.
    brief=True shortens descriptions to save tokens on every chat turn."""
    fields = fields or load_fields()
    props = {}
    for name in names or fields:
        spec = fields[name]
        t = spec["type"]
        if t == "enum":
            prop = {"enum": list(spec["values"]) + [None]}
        else:
            prop = {"type": [{"bool": "boolean", "int": "integer", "number": "number", "str": "string"}[t], "null"]}
            for k in ("min", "max"):
                if k in spec:
                    prop["minimum" if k == "min" else "maximum"] = spec[k]
        desc = spec["description"]
        prop["description"] = desc.split(";")[0].split("(")[0].strip()[:70] if brief else desc
        props[name] = prop
    return {"type": "object", "properties": props, "additionalProperties": False}
