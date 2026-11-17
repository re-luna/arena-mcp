"""Shared constants, serialization, and COM-safe property helpers."""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timezone
from typing import Any
from arena_mcp.com import (
    ARENA_PROG_ID,
    DEFAULT_COM_TIMEOUT_SECONDS,
    ArenaSession,
    _ARENA_LOCK,
    arena_registration as _arena_registration,
    is_com_time as _is_com_time,
    require_com_dependencies as _require_com_dependencies,
)


SERVER_NAME = "Arena Read-Only Extractor"

IR_SCHEMA_VERSION = "0.3.0"

AUDIT_SCHEMA_VERSION = "2.0.0"

REPORT_SCHEMA_VERSION = "1.0.0"

DEFAULT_MAX_REPEAT_ROWS = 250

DEFAULT_MAX_MODULES = 1_000

DEFAULT_MAX_AUDIT_ITEMS = 5_000

DEFAULT_MAX_VBA_LINES = 10_000

DEFAULT_MAX_BINARY_BYTES = 2_000_000

DEFAULT_MAX_SIMAN_CHARS = 2_000_000

MODEL_EXTENSIONS = {".doe"}

DATABASE_EXTENSIONS = {".db", ".sqlite", ".sqlite3"}

AUTOMATIC_MODULES = {
    "Assign",
    "Attribute",
    "Create",
    "Decide",
    "Dispose",
    "Entity",
    "Expression",
    "Process",
    "Queue",
    "Record",
    "Resource",
    "Schedule",
    "Set",
    "Variable",
}

ASSISTED_MODULES = {
    "Access",
    "Allocate",
    "Batch",
    "Branch",
    "Convey",
    "Delay",
    "Dropoff",
    "Free",
    "Hold",
    "Match",
    "Output",
    "Pickup",
    "ReadWrite",
    "Release",
    "Remove",
    "Route",
    "Search",
    "Seize",
    "Separate",
    "Signal",
    "Station",
    "Station Data",
    "Storage",
    "Transport",
}

MODEL_COLLECTIONS = (
    "NamedViews",
    "Modules",
    "Embeddeds",
    "Shapes",
    "Connections",
    "Stations",
    "Intersections",
    "Routes",
    "Segments",
    "Distances",
    "NetworkLinks",
    "Queues",
    "StorageAreas",
    "SeizeAreas",
    "ParkingAreas",
    "EntityPictures",
    "ResourcePictures",
    "TransporterPictures",
    "GlobalPictures",
    "StatusVariables",
    "StatusClocks",
    "StatusDates",
    "StatusLevels",
    "StatusHistograms",
    "StatusPlots",
    "Submodels",
    "OPCDataItems",
)

TRANSLATION_DATA_MODULES = {"Expression", "Resource", "Schedule", "Set"}

EXPRESSION_FIELD_TERMS = (
    "expression",
    "condition",
    "value",
    "duration",
    "delay",
    "time",
    "capacity",
    "quantity",
    "distribution",
    "formula",
)

EXTERNAL_REFERENCE_PATTERN = re.compile(
    r"(?:[A-Za-z]:[\\/]|\\\\|https?://|(?:ODBC|DSN)\s*=|"
    r"[^\s\"']+\.(?:xlsx?|xlsm|csv|txt|db|sqlite3?|mdb|accdb|dll|exe|py|"
    r"xml|json|doe)\b)",
    re.IGNORECASE,
)

TEMPLATE_DESCRIPTION_PATTERN = re.compile(
    r"From template:\s*([^\r\n]+)", re.IGNORECASE
)

EXPRESSION_TOKEN_PATTERN = re.compile(
    r"(?P<space>\s+)|"
    r"(?P<number>(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)|"
    r"(?P<string>\"(?:\"\"|[^\"])*\")|"
    r"(?P<identifier>[A-Za-z_][A-Za-z0-9_.:]*)|"
    r"(?P<operator><=|>=|<>|==|!=|&&|\|\||[+\-*/^%=<>&|!,()\[\]{}])|"
    r"(?P<other>.)"
)

EXPRESSION_KEYWORDS = {
    "and",
    "else",
    "false",
    "if",
    "not",
    "or",
    "then",
    "true",
}

ANIMATION_COLLECTIONS = (
    "StatusVariables",
    "StatusClocks",
    "StatusDates",
    "StatusLevels",
    "StatusHistograms",
    "StatusPlots",
)

PICTURE_COLLECTIONS = (
    "EntityPictures",
    "ResourcePictures",
    "TransporterPictures",
    "GlobalPictures",
)

MATERIAL_COLLECTIONS = (
    "Stations",
    "Intersections",
    "Routes",
    "Segments",
    "Distances",
    "NetworkLinks",
    "Queues",
    "StorageAreas",
    "SeizeAreas",
    "ParkingAreas",
)

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _clamp(value: int, minimum: int, maximum: int, label: str) -> int:
    if value < minimum or value > maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}.")
    return value

def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (datetime, date)) or _is_com_time(value):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return str(value)

def _safe_property(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name)
    except Exception:
        return default

def _safe_call(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name)()
    except Exception:
        return default

def _collection_count(obj: Any, name: str) -> int | None:
    collection = _safe_property(obj, name)
    if collection is None:
        return None
    try:
        return int(collection.Count)
    except Exception:
        return None
