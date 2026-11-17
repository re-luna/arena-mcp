"""Read-only MCP extractor for Rockwell Automation Arena models.

The server asks Arena itself to open .doe files through its Windows COM object
model. It never saves, runs, or edits a model. Extracted data is returned as a
versioned, JSON-compatible intermediate representation for a later Python
simulation translator.
"""

from __future__ import annotations

import base64
import gc
import hashlib
import json
import logging
import math
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from collections import Counter, defaultdict
from contextlib import AbstractContextManager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import olefile
from arena_mcp.errors import ArenaExtractorError
from arena_mcp.results import (
    RESULT_VIEWS,
    inspect_results_file,
    read_results_file,
)

try:
    import winreg
except ImportError:
    winreg = None

try:
    import pythoncom
    import pywintypes
    import win32api
    from win32com.client import DispatchEx
except ImportError as error:
    pythoncom = None
    pywintypes = None
    win32api = None
    DispatchEx = None
    _COM_IMPORT_ERROR: ImportError | None = error
else:
    _COM_IMPORT_ERROR = None


SERVER_NAME = "Arena Read-Only Extractor"
IR_SCHEMA_VERSION = "0.3.0"
AUDIT_SCHEMA_VERSION = "2.0.0"
REPORT_SCHEMA_VERSION = "1.0.0"
ARENA_PROG_ID = "Arena.Application"
DEFAULT_COM_TIMEOUT_SECONDS = 20.0
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

_ARENA_LOCK = threading.Lock()
_LOGGER = logging.getLogger(__name__)


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


def _is_com_time(value: Any) -> bool:
    return pywintypes is not None and isinstance(value, pywintypes.TimeType)


def _error_text(error: BaseException) -> str:
    text = str(error).replace("\r", " ").replace("\n", " ").strip()
    return text[:500] or type(error).__name__


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


def _default_roots() -> list[Path]:
    configured = os.environ.get("ARENA_MODEL_ROOTS", "").strip()
    if configured:
        candidates = [Path(item) for item in configured.split(os.pathsep) if item]
    else:
        home = Path.home()
        candidates = [
            home / "OneDrive" / "Documents",
            home / "OneDrive" / "Desktop",
            home / "Documents",
            home / "Desktop",
            Path(r"C:\Users\Public\Documents\Rockwell Software\Arena"),
        ]

    roots: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve(strict=True)
        except OSError:
            continue
        key = os.path.normcase(str(resolved))
        if resolved.is_dir() and key not in seen:
            seen.add(key)
            roots.append(resolved)
    return roots


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_allowed_file(raw_path: str, extensions: set[str]) -> Path:
    if not raw_path or not raw_path.strip():
        raise ValueError("A file path is required.")
    try:
        path = Path(os.path.expandvars(raw_path)).expanduser().resolve(strict=True)
    except OSError as error:
        raise FileNotFoundError(f"File not found: {raw_path}") from error
    if not path.is_file():
        raise ValueError(f"Not a file: {path}")
    if path.suffix.lower() not in extensions:
        allowed = ", ".join(sorted(extensions))
        raise ValueError(f"Unsupported file type {path.suffix!r}; expected {allowed}.")

    roots = _default_roots()
    if os.environ.get("ARENA_ALLOW_ANY_PATH") != "1" and not any(
        _is_within(path, root) for root in roots
    ):
        allowed_roots = ", ".join(str(root) for root in roots) or "<none>"
        raise PermissionError(
            f"Path is outside ARENA_MODEL_ROOTS: {path}. Allowed roots: {allowed_roots}"
        )
    return path


def _arena_registration() -> dict[str, Any]:
    result: dict[str, Any] = {
        "registered": False,
        "prog_id": ARENA_PROG_ID,
        "clsid": None,
        "executable": None,
        "file_version": None,
    }
    if winreg is None:
        result["error"] = "Arena registration checks require Windows."
        return result
    if win32api is None:
        detail = f" {_error_text(_COM_IMPORT_ERROR)}" if _COM_IMPORT_ERROR else ""
        result["error"] = f"pywin32 is required for Arena registration checks.{detail}"
        return result

    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{ARENA_PROG_ID}\CLSID") as key:
            clsid = winreg.QueryValue(key, None)
        result["clsid"] = clsid
        with winreg.OpenKey(
            winreg.HKEY_CLASSES_ROOT, rf"CLSID\{clsid}\LocalServer32"
        ) as key:
            executable = winreg.QueryValue(key, None).strip().strip('"')
        result["executable"] = executable
        if Path(executable).is_file():
            info = win32api.GetFileVersionInfo(executable, "\\")
            ms = info["FileVersionMS"]
            ls = info["FileVersionLS"]
            result["file_version"] = ".".join(
                str(value)
                for value in (
                    win32api.HIWORD(ms),
                    win32api.LOWORD(ms),
                    win32api.HIWORD(ls),
                    win32api.LOWORD(ls),
                )
            )
        result["registered"] = True
    except Exception as error:
        if isinstance(error, OSError) or (
            pywintypes is not None and isinstance(error, pywintypes.error)
        ):
            result["error"] = _error_text(error)
        else:
            raise
    return result


def _require_com_dependencies() -> tuple[Any, Any, Any, Any]:
    if sys.platform != "win32":
        raise ArenaExtractorError("Arena COM extraction is available only on Windows.")
    if (
        pythoncom is None
        or pywintypes is None
        or win32api is None
        or DispatchEx is None
    ):
        detail = f" {_error_text(_COM_IMPORT_ERROR)}" if _COM_IMPORT_ERROR else ""
        raise ArenaExtractorError(f"pywin32 is required for Arena COM extraction.{detail}")
    return pythoncom, pywintypes, win32api, DispatchEx


class ArenaSession(AbstractContextManager["ArenaSession"]):
    """Own one isolated Arena COM instance on one STA thread."""

    def __init__(
        self,
        model_path: Path | None = None,
        lock_timeout: float = DEFAULT_COM_TIMEOUT_SECONDS,
    ) -> None:
        if lock_timeout <= 0:
            raise ValueError("lock_timeout must be greater than zero.")
        self.model_path = model_path
        self.lock_timeout = lock_timeout
        self.application: Any = None
        self.models: Any = None
        self.model: Any = None
        self._locked = False
        self._com_initialized = False

    def __enter__(self) -> "ArenaSession":
        if not _ARENA_LOCK.acquire(timeout=self.lock_timeout):
            raise ArenaExtractorError(
                f"Timed out waiting for another Arena operation after "
                f"{self.lock_timeout:g} seconds."
            )
        self._locked = True
        try:
            pythoncom, _, _, dispatch_ex = _require_com_dependencies()
            pythoncom.CoInitialize()
            self._com_initialized = True
            self.application = dispatch_ex(ARENA_PROG_ID)
            self.models = self._wait_for_models()
            if self.model_path is not None:
                self.models.Open(str(self.model_path))
                self.model = self._wait_for_active_model()
                try:
                    self.model.QuietMode = True
                except Exception:
                    pass
            return self
        except Exception:
            self.__exit__(*sys.exc_info())
            raise

    def _wait_for(
        self,
        getter: Callable[[], Any],
        description: str,
        timeout: float = DEFAULT_COM_TIMEOUT_SECONDS,
    ) -> Any:
        deadline = time.monotonic() + timeout
        last_error: BaseException | None = None
        while time.monotonic() < deadline:
            try:
                value = getter()
                if value is not None:
                    return value
            except Exception as error:
                last_error = error
            _require_com_dependencies()[0].PumpWaitingMessages()
            time.sleep(0.1)
        detail = f": {_error_text(last_error)}" if last_error else ""
        raise ArenaExtractorError(f"Timed out waiting for {description}{detail}")

    def _wait_for_models(self) -> Any:
        return self._wait_for(lambda: self.application.Models, "Arena Models collection")

    def _wait_for_active_model(self) -> Any:
        return self._wait_for(lambda: self.application.ActiveModel, "Arena ActiveModel")

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        try:
            if self.model is not None:
                try:
                    self.model.Close()
                except Exception as error:
                    _LOGGER.warning(
                        "Failed to close Arena model: %s", _error_text(error)
                    )
            if self.application is not None:
                try:
                    self.application.Quit()
                except Exception as error:
                    _LOGGER.warning(
                        "Failed to quit Arena application: %s", _error_text(error)
                    )
        finally:
            self.model = None
            self.models = None
            self.application = None
            try:
                gc.collect()
            finally:
                try:
                    if self._com_initialized:
                        _require_com_dependencies()[0].CoUninitialize()
                finally:
                    self._com_initialized = False
                    if self._locked:
                        _ARENA_LOCK.release()
                        self._locked = False


def get_arena_status(live_check: bool = False) -> dict[str, Any]:
    status = {
        "server": SERVER_NAME,
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "python_bits": 64 if sys.maxsize > 2**32 else 32,
        "registration": _arena_registration(),
        "allowed_roots": [str(root) for root in _default_roots()],
        "read_only": True,
        "live_check": None,
    }
    if live_check:
        with ArenaSession() as session:
            status["live_check"] = {
                "connected": True,
                "version": _json_value(session.application.Version),
                "license_type": _json_value(session.application.LicenseType),
                "has_optquest_license": bool(session.application.HasOptQuestLicense),
            }
    return status


def diagnose_arena(live_check: bool = False) -> dict[str, Any]:
    status = get_arena_status(live_check=False)
    registration = status["registration"]
    roots = status["allowed_roots"]
    checks = [
        {
            "name": "platform",
            "ok": sys.platform == "win32",
            "message": (
                "Running on Windows."
                if sys.platform == "win32"
                else "Arena COM requires Windows."
            ),
        },
        {
            "name": "arena_registration",
            "ok": bool(registration["registered"]),
            "message": (
                "Arena is registered."
                if registration["registered"]
                else registration.get("error")
                or "Arena is not registered as Arena.Application."
            ),
        },
        {
            "name": "model_roots",
            "ok": bool(roots),
            "message": (
                f"{len(roots)} allowed model root(s) are available."
                if roots
                else "No configured model folders exist. Set ARENA_MODEL_ROOTS."
            ),
        },
    ]
    if live_check:
        try:
            live_status = get_arena_status(live_check=True)["live_check"]
        except Exception as error:
            checks.append(
                {
                    "name": "live_connection",
                    "ok": False,
                    "message": _error_text(error),
                }
            )
        else:
            checks.append(
                {
                    "name": "live_connection",
                    "ok": True,
                    "message": f"Connected to Arena {live_status['version']}.",
                }
            )

    failed_checks = [check for check in checks if not check["ok"]]
    return {
        "ok": not failed_checks,
        "checks": checks,
        "recommended_actions": [
            check["message"] for check in failed_checks
        ],
    }


def discover_models(
    root: str | None = None,
    include_backups: bool = False,
    limit: int = 200,
) -> dict[str, Any]:
    limit = _clamp(limit, 1, 2_000, "limit")
    allowed_roots = _default_roots()
    if root:
        candidate = Path(os.path.expandvars(root)).expanduser().resolve(strict=True)
        if not candidate.is_dir():
            raise ValueError(f"Not a directory: {candidate}")
        if os.environ.get("ARENA_ALLOW_ANY_PATH") != "1" and not any(
            _is_within(candidate, allowed) for allowed in allowed_roots
        ):
            raise PermissionError(f"Directory is outside ARENA_MODEL_ROOTS: {candidate}")
        search_roots = [candidate]
    else:
        search_roots = allowed_roots

    found: list[dict[str, Any]] = []
    for search_root in search_roots:
        try:
            paths = search_root.rglob("*.doe")
            for path in paths:
                is_backup = path.name.lower().endswith(".backup.doe")
                if is_backup and not include_backups:
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                found.append(
                    {
                        "path": str(path.resolve()),
                        "name": path.name,
                        "backup": is_backup,
                        "size_bytes": stat.st_size,
                        "modified_at": datetime.fromtimestamp(
                            stat.st_mtime, timezone.utc
                        ).isoformat(),
                    }
                )
                if len(found) >= limit:
                    break
        except OSError:
            continue
        if len(found) >= limit:
            break

    found.sort(key=lambda item: item["modified_at"], reverse=True)
    return {
        "roots": [str(item) for item in search_roots],
        "models": found[:limit],
        "count": min(len(found), limit),
        "truncated": len(found) >= limit,
    }


def _model_run_configuration(model: Any) -> dict[str, Any]:
    names = [
        "BaseTimeUnits",
        "NumberOfReplications",
        "ReplicationLength",
        "ReplicationLengthTimeUnits",
        "WarmUpPeriod",
        "WarmUpPeriodTimeUnits",
        "TerminatingCondition",
        "HoursPerDay",
        "StartDateTime",
        "UseCurrentStartDateTime",
        "InitializeSystemBetweenReplications",
        "InitializeStatisticsBetweenReplications",
        "DisableRandomness",
        "ParallelReplications",
        "ParallelProcesses",
        "AlwaysCompileOnGo",
        "CostingStatistics",
        "EntityStatistics",
        "QueueStatistics",
        "ResourceStatistics",
        "ProcessStatistics",
        "TransporterStatistics",
        "ConveyorStatistics",
        "StationStatistics",
        "ActivityAreaStatistics",
        "TankStatistics",
        "UseFractionalResourceUnits",
        "DisplayDefaultReport",
        "DefaultReport",
        "DisableReportDatabase",
        "AutoPublishSimVariables",
        "VisualizationFileName",
    ]
    return {name: _json_value(_safe_property(model, name)) for name in names}


def _model_summary(model: Any, source_path: Path) -> dict[str, Any]:
    collection_names = [
        "Modules",
        "Connections",
        "Queues",
        "Stations",
        "Routes",
        "Distances",
        "Submodels",
        "StatusLevels",
        "StatusVariables",
        "StatusPlots",
    ]
    return {
        "name": _json_value(_safe_property(model, "Name")),
        "full_name": _json_value(_safe_property(model, "FullName", str(source_path))),
        "project_title": _json_value(_safe_property(model, "ProjectTitle")),
        "project_description": _json_value(
            _safe_property(model, "ProjectDescription")
        ),
        "analyst_name": _json_value(_safe_property(model, "AnalystName")),
        "last_modified": _json_value(_safe_property(model, "LastModifiedDateTime")),
        "saved": _json_value(_safe_property(model, "Saved")),
        "demo_limits_exceeded": _json_value(
            _safe_property(model, "AreDemoLimitsExceeded")
        ),
        "collections": {
            name: _collection_count(model, name) for name in collection_names
        },
    }


def _operand_parent_name(operand: Any) -> str | None:
    parent = _safe_property(operand, "ParentOperand")
    if parent in (None, ""):
        return None
    name = _safe_property(parent, "Name")
    return str(name if name not in (None, "") else parent)


def _operand_metadata(operand: Any) -> dict[str, Any]:
    return {
        "name": str(_safe_property(operand, "Name", "")),
        "prompt": _json_value(_safe_property(operand, "Prompt")),
        "default_value": _json_value(_safe_property(operand, "DefaultValue")),
        "required": _json_value(_safe_property(operand, "Required")),
        "control_type": _json_value(_safe_property(operand, "ControlType")),
        "array": bool(_safe_property(operand, "Array", False)),
        "entry": bool(_safe_property(operand, "Entry", False)),
        "exit": bool(_safe_property(operand, "Exit", False)),
        "parent_operand": _operand_parent_name(operand),
    }


def _read_module_data(module: Any, key: str) -> tuple[Any, str | None]:
    try:
        return _json_value(module.Data(key)), None
    except Exception as error:
        return None, _error_text(error)


def _extract_operands(module: Any, max_repeat_rows: int) -> dict[str, Any]:
    max_repeat_rows = _clamp(
        max_repeat_rows, 1, 10_000, "max_repeat_rows"
    )
    definition = _safe_property(module, "Definition")
    operands_collection = _safe_property(definition, "Operands")
    operands = [item for item in list(operands_collection or []) if item is not None]
    metadata = [_operand_metadata(operand) for operand in operands]

    scalar_values: list[dict[str, Any]] = []
    group_definitions: dict[str, dict[str, Any]] = {}
    group_children: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for operand_meta in metadata:
        name = operand_meta["name"]
        parent_name = operand_meta["parent_operand"]
        if operand_meta["array"] and not parent_name:
            group_definitions[name] = operand_meta
            continue
        if parent_name:
            group_children[parent_name].append(operand_meta)
            continue

        value, read_error = _read_module_data(module, name)
        item = {**operand_meta, "value": value}
        if read_error:
            item["read_error"] = read_error
        scalar_values.append(item)

    repeat_groups: list[dict[str, Any]] = []
    all_group_names = list(
        dict.fromkeys([*group_definitions.keys(), *group_children.keys()])
    )
    for group_name in all_group_names:
        columns = group_children.get(group_name, [])
        rows: list[dict[str, Any]] = []
        truncated = False
        for index in range(1, max_repeat_rows + 1):
            values: dict[str, Any] = {}
            errors: dict[str, str] = {}
            successes = 0
            for column in columns:
                value, read_error = _read_module_data(
                    module, f"{column['name']}({index})"
                )
                if read_error:
                    errors[column["name"]] = read_error
                else:
                    successes += 1
                    values[column["name"]] = value
            if successes == 0:
                break
            row: dict[str, Any] = {"index": index, "values": values}
            if errors:
                row["read_errors"] = errors
            rows.append(row)
        else:
            truncated = True

        repeat_groups.append(
            {
                "name": group_name,
                "definition": group_definitions.get(group_name),
                "columns": columns,
                "rows": rows,
                "truncated": truncated,
            }
        )

    return {
        "scalars": scalar_values,
        "repeat_groups": repeat_groups,
        "operand_count": len(metadata),
    }


def _module_definition_name(module: Any) -> str:
    definition = _safe_property(module, "Definition")
    return str(_safe_property(definition, "Name", "Unknown"))


def _module_source_template(module: Any) -> str | None:
    shapes = []
    primary_shape = _safe_property(module, "shape")
    if primary_shape is not None:
        shapes.append(primary_shape)
    try:
        shapes.extend(item for item in module.Shapes if item is not None)
    except Exception:
        pass
    for shape in shapes:
        description = str(_safe_property(shape, "DefaultDescription", "") or "")
        match = TEMPLATE_DESCRIPTION_PATTERN.search(description)
        if match:
            return match.group(1).strip()
    return None


def _module_record(
    module: Any,
    index: int,
    include_operands: bool,
    max_repeat_rows: int,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": f"module-{index:04d}",
        "index": index,
        "caption": str(_safe_property(module, "Caption", "")),
        "definition": _module_definition_name(module),
        "source_template": _module_source_template(module),
        "data_module": bool(
            _safe_property(_safe_property(module, "Definition"), "DataModule", False)
        ),
        "incoming_connections": _collection_count(module, "ToConnections"),
        "outgoing_connections": _collection_count(module, "FromConnections"),
    }
    if include_operands:
        record["operands"] = _extract_operands(module, max_repeat_rows)
    return record


def _same_com_object(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return False
    try:
        pythoncom, _, _, _ = _require_com_dependencies()
        left_unknown = left._oleobj_.QueryInterface(pythoncom.IID_IUnknown)
        right_unknown = right._oleobj_.QueryInterface(pythoncom.IID_IUnknown)
        return bool(left_unknown == right_unknown)
    except Exception:
        return False


def _endpoint_record(endpoint: Any, modules: list[Any]) -> dict[str, Any] | None:
    if endpoint is None:
        return None
    caption = str(_safe_property(endpoint, "Caption", ""))
    caption_ids = []
    if caption:
        caption_ids = [
            f"module-{index:04d}"
            for index, module in enumerate(modules, start=1)
            if str(_safe_property(module, "Caption", "")) == caption
        ]
    if len(caption_ids) == 1:
        matching_ids = caption_ids
    else:
        matching_ids = [
            f"module-{index:04d}"
            for index, module in enumerate(modules, start=1)
            if _same_com_object(endpoint, module)
        ]
        if not matching_ids:
            matching_ids = caption_ids
    return {
        "caption": caption,
        "definition": _module_definition_name(endpoint),
        "module_id": matching_ids[0] if len(matching_ids) == 1 else None,
        "candidate_module_ids": matching_ids,
    }


def _extract_connections(model: Any, modules: list[Any]) -> list[dict[str, Any]]:
    connections = [item for item in list(model.Connections) if item is not None]
    records: list[dict[str, Any]] = []
    for index, connection in enumerate(connections, start=1):
        source = _safe_call(connection, "Source")
        destination = _safe_call(connection, "Destination")
        records.append(
            {
                "id": f"connection-{index:04d}",
                "index": index,
                "source": _endpoint_record(source, modules),
                "destination": _endpoint_record(destination, modules),
                "smart": _json_value(_safe_property(connection, "Smart")),
                "source_connection_point": _json_value(
                    _safe_call(connection, "SourceConnectionPoint")
                ),
                "destination_connection_point": _json_value(
                    _safe_call(connection, "DestinationConnectionPoint")
                ),
            }
        )
    return records


def _collection_inventory(model: Any) -> dict[str, dict[str, Any]]:
    inventory: dict[str, dict[str, Any]] = {}
    for name in MODEL_COLLECTIONS:
        try:
            collection = getattr(model, name)
            inventory[name] = {
                "available": True,
                "count": int(collection.Count),
            }
        except Exception as error:
            inventory[name] = {
                "available": False,
                "count": None,
                "error": _error_text(error),
            }
    return inventory


def _module_definition_inventory(
    modules: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for module in modules:
        definition = module["definition"]
        entry = grouped.setdefault(
            definition,
            {
                "definition": definition,
                "count": 0,
                "data_module": bool(module.get("data_module")),
                "operand_definitions": 0,
                "scalar_values": 0,
                "repeat_groups": 0,
                "repeat_rows": 0,
                "read_errors": 0,
            },
        )
        entry["count"] += 1
        operands = module.get("operands", {})
        entry["operand_definitions"] = max(
            entry["operand_definitions"], operands.get("operand_count", 0)
        )
        entry["scalar_values"] += len(operands.get("scalars", []))
        entry["repeat_groups"] += len(operands.get("repeat_groups", []))
        for scalar in operands.get("scalars", []):
            entry["read_errors"] += int("read_error" in scalar)
        for group in operands.get("repeat_groups", []):
            entry["repeat_rows"] += len(group.get("rows", []))
            entry["read_errors"] += sum(
                len(row.get("read_errors", {})) for row in group.get("rows", [])
            )
    return [grouped[name] for name in sorted(grouped)]


def _iter_module_operand_values(
    module: dict[str, Any],
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    operands = module.get("operands", {})
    for scalar in operands.get("scalars", []):
        values.append(
            {
                "operand": scalar.get("name"),
                "prompt": scalar.get("prompt"),
                "value": scalar.get("value"),
                "location": scalar.get("name"),
            }
        )
    for group in operands.get("repeat_groups", []):
        prompts = {
            column.get("name"): column.get("prompt")
            for column in group.get("columns", [])
        }
        for row in group.get("rows", []):
            for name, value in row.get("values", {}).items():
                values.append(
                    {
                        "operand": name,
                        "prompt": prompts.get(name),
                        "value": value,
                        "location": f"{group.get('name')}[{row.get('index')}].{name}",
                    }
                )
    return values


def _analyze_expression(value: str) -> dict[str, Any]:
    tokens: list[dict[str, Any]] = []
    identifiers: list[str] = []
    functions: list[str] = []
    unsupported: list[dict[str, Any]] = []
    delimiters: list[tuple[str, int]] = []
    errors: list[str] = []
    matches = list(EXPRESSION_TOKEN_PATTERN.finditer(value))

    for index, match in enumerate(matches):
        kind = str(match.lastgroup)
        text = match.group(0)
        if kind == "space":
            continue
        token = {"kind": kind, "value": text, "offset": match.start()}
        tokens.append(token)
        if kind == "other":
            unsupported.append(token)
            continue
        if kind == "identifier":
            lower = text.lower()
            if lower in EXPRESSION_KEYWORDS:
                continue
            following = next(
                (
                    item
                    for item in matches[index + 1 :]
                    if item.lastgroup != "space"
                ),
                None,
            )
            if following is not None and following.group(0) == "(":
                functions.append(text)
            else:
                identifiers.append(text)
        if kind == "operator":
            if text in "([{":
                delimiters.append((text, match.start()))
            elif text in ")]}":
                expected = {")": "(", "]": "[", "}": "{"}[text]
                if not delimiters or delimiters[-1][0] != expected:
                    errors.append(f"Unmatched {text!r} at offset {match.start()}.")
                else:
                    delimiters.pop()

    errors.extend(
        f"Unclosed {delimiter!r} at offset {offset}."
        for delimiter, offset in delimiters
    )
    if unsupported:
        errors.append(
            "Unsupported characters: "
            + ", ".join(repr(item["value"]) for item in unsupported[:10])
        )
    return {
        "lexically_valid": not errors,
        "token_count": len(tokens),
        "identifiers": sorted(set(identifiers), key=str.casefold),
        "functions": sorted(set(functions), key=str.casefold),
        "unsupported_tokens": unsupported,
        "errors": errors,
    }


def _audit_expressions(
    modules: list[dict[str, Any]], max_items: int
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    total = 0
    lexical_errors = 0
    all_identifiers: set[str] = set()
    all_functions: set[str] = set()
    for module in modules:
        for operand in _iter_module_operand_values(module):
            value = operand.get("value")
            if not isinstance(value, str) or not value.strip():
                continue
            field = " ".join(
                str(item or "")
                for item in (operand.get("operand"), operand.get("prompt"))
            ).lower()
            is_expression_module = module["definition"] == "Expression"
            is_semantic_field = any(term in field for term in EXPRESSION_FIELD_TERMS)
            if not is_expression_module and not is_semantic_field:
                continue
            total += 1
            if len(candidates) < max_items:
                analysis = _analyze_expression(value)
                lexical_errors += int(not analysis["lexically_valid"])
                all_identifiers.update(analysis["identifiers"])
                all_functions.update(analysis["functions"])
                candidates.append(
                    {
                        "module_id": module["id"],
                        "definition": module["definition"],
                        "caption": module["caption"],
                        "operand": operand["location"],
                        "value": value,
                        "detection": (
                            "expression_data_module"
                            if is_expression_module
                            else "operand_name_heuristic"
                        ),
                        "analysis": analysis,
                    }
                )
    return {
        "candidate_count": total,
        "returned": len(candidates),
        "truncated": total > len(candidates),
        "analyzed": True,
        "analysis_scope": "lexical structure, delimiters, symbols, and function calls",
        "lexical_error_count_in_returned": lexical_errors,
        "identifiers_in_returned": sorted(all_identifiers, key=str.casefold),
        "functions_in_returned": sorted(all_functions, key=str.casefold),
        "candidates": candidates,
    }


def _audit_external_dependencies(
    model: Any,
    modules: list[dict[str, Any]],
    vba: dict[str, Any],
    max_items: int,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    total = 0

    def add(kind: str, location: str, value: Any) -> None:
        nonlocal total
        if value in (None, ""):
            return
        json_value = _json_value(value)
        key = (kind, location, json.dumps(json_value, sort_keys=True))
        if key in seen:
            return
        seen.add(key)
        total += 1
        if len(candidates) < max_items:
            candidates.append(
                {"kind": kind, "location": location, "value": json_value}
            )

    for property_name in ("ExternalRef", "VisualizationFileName"):
        value = _safe_property(model, property_name)
        if value not in (None, ""):
            add("model_property", property_name, value)

    if bool(_safe_property(model, "AutoPublishSimVariables", False)):
        add("opc_configuration", "AutoPublishSimVariables", True)

    for module in modules:
        if module["definition"] in {"ReadWrite", "VBA"}:
            add(
                "integration_module",
                f"{module['id']}:{module['definition']}",
                module["caption"] or True,
            )
        for operand in _iter_module_operand_values(module):
            value = operand.get("value")
            if isinstance(value, str) and EXTERNAL_REFERENCE_PATTERN.search(value):
                add(
                    "operand_reference",
                    f"{module['id']}.{operand['location']}",
                    value,
                )

    for project in vba.get("projects", []):
        for reference in project.get("references", []):
            if not reference.get("built_in"):
                add(
                    "vba_reference",
                    f"VBA.{project.get('name')}.References.{reference.get('name')}",
                    reference.get("full_path") or reference.get("name"),
                )
        for component in project.get("components", []):
            source = component.get("source")
            if not isinstance(source, str):
                continue
            for line_number, line in enumerate(source.splitlines(), start=1):
                if EXTERNAL_REFERENCE_PATTERN.search(line):
                    add(
                        "vba_source_reference",
                        (
                            f"VBA.{project.get('name')}."
                            f"{component.get('name')}:{line_number}"
                        ),
                        line.strip(),
                    )

    return {
        "candidate_count": total,
        "returned": len(candidates),
        "truncated": total > len(candidates),
        "detection": "static heuristic; candidates require validation",
        "candidates": candidates,
    }


def _audit_vba(
    application: Any, include_source: bool, max_vba_lines: int
) -> dict[str, Any]:
    audit: dict[str, Any] = {
        "accessible": False,
        "source_requested": include_source,
        "source_complete": False,
        "projects": [],
    }
    remaining_lines = max_vba_lines
    try:
        projects = application.VBE.VBProjects
        audit["accessible"] = True
        audit["project_count"] = int(projects.Count)
        source_complete = include_source
        for project in projects:
            project_record: dict[str, Any] = {
                "name": str(_safe_property(project, "Name", "")),
                "file_name": _json_value(_safe_property(project, "FileName")),
                "components": [],
                "references": [],
            }
            try:
                for reference in project.References:
                    project_record["references"].append(
                        {
                            "name": _json_value(_safe_property(reference, "Name")),
                            "full_path": _json_value(
                                _safe_property(reference, "FullPath")
                            ),
                            "built_in": bool(
                                _safe_property(reference, "BuiltIn", False)
                            ),
                            "broken": bool(_safe_property(reference, "IsBroken", False)),
                            "major": _json_value(_safe_property(reference, "Major")),
                            "minor": _json_value(_safe_property(reference, "Minor")),
                        }
                    )
            except Exception as error:
                project_record["references_error"] = _error_text(error)

            for component in project.VBComponents:
                component_record: dict[str, Any] = {
                    "name": str(_safe_property(component, "Name", "")),
                    "type": _json_value(_safe_property(component, "Type")),
                }
                try:
                    code_module = component.CodeModule
                    line_count = int(code_module.CountOfLines)
                    component_record["line_count"] = line_count
                    if include_source:
                        captured = min(line_count, remaining_lines)
                        component_record["captured_lines"] = captured
                        component_record["source_truncated"] = captured < line_count
                        component_record["source"] = (
                            str(code_module.Lines(1, captured)) if captured else ""
                        )
                        remaining_lines -= captured
                        source_complete = source_complete and captured == line_count
                except Exception as error:
                    component_record["code_error"] = _error_text(error)
                    source_complete = False
                project_record["components"].append(component_record)
            audit["projects"].append(project_record)
        audit["source_complete"] = source_complete
        audit["line_count"] = sum(
            int(component.get("line_count", 0))
            for project in audit["projects"]
            for component in project["components"]
        )
        audit["captured_lines"] = sum(
            int(component.get("captured_lines", 0))
            for project in audit["projects"]
            for component in project["components"]
        )
    except Exception as error:
        audit["error"] = _error_text(error)
    return audit


def _audit_template_panels(
    application: Any, modules: list[dict[str, Any]]
) -> dict[str, Any]:
    panels: list[dict[str, Any]] = []
    definition_panels: dict[str, list[str]] = defaultdict(list)
    try:
        for panel in application.Panels:
            panel_name = str(_safe_property(panel, "Name", ""))
            definitions: list[dict[str, Any]] = []
            try:
                for definition in panel.ModuleDefinitions:
                    definition_name = str(_safe_property(definition, "Name", ""))
                    operands = []
                    for operand in definition.Operands:
                        operand_record = _operand_metadata(operand)
                        pick_count = int(_safe_property(operand, "PickListCount", 0))
                        operand_record["pick_list_count"] = pick_count
                        if pick_count:
                            try:
                                operand_record["pick_list"] = _json_value(
                                    operand.PickList(None)
                                )
                            except Exception as error:
                                operand_record["pick_list_error"] = _error_text(error)
                        operands.append(operand_record)
                    definitions.append(
                        {
                            "name": definition_name,
                            "required": bool(
                                _safe_property(definition, "Required", False)
                            ),
                            "data_module": bool(
                                _safe_property(definition, "DataModule", False)
                            ),
                            "operands": operands,
                        }
                    )
            except Exception:
                pass
            definitions.sort(key=lambda item: item["name"].casefold())
            panels.append(
                {
                    "name": panel_name,
                    "module_definition_count": len(definitions),
                    "module_definitions": definitions,
                }
            )
            for definition in definitions:
                definition_panels[definition["name"]].append(panel_name)
    except Exception as error:
        return {"accessible": False, "error": _error_text(error), "panels": []}

    used = []
    for definition, definition_modules in sorted(
        (
            (name, [module for module in modules if module["definition"] == name])
            for name in {module["definition"] for module in modules}
        ),
        key=lambda item: item[0].casefold(),
    ):
        template_counts = Counter(
            module.get("source_template")
            for module in definition_modules
            if module.get("source_template")
        )
        used.append(
            {
                "definition": definition,
                "count": len(definition_modules),
                "source_templates": dict(sorted(template_counts.items())),
                "candidate_panels": definition_panels.get(definition, []),
                "panel_resolved": bool(template_counts)
                or len(definition_panels.get(definition, [])) == 1,
            }
        )
    unresolved = [item["definition"] for item in used if not item["panel_resolved"]]
    return {
        "accessible": True,
        "installed_panels": panels,
        "used_module_definitions": used,
        "unresolved_definitions": unresolved,
        "definition_count": sum(
            panel["module_definition_count"] for panel in panels
        ),
        "operand_definition_count": sum(
            len(definition["operands"])
            for panel in panels
            for definition in panel["module_definitions"]
        ),
        "note": (
            "Arena exposes attached panel schemas and pick lists, but not the "
            "template implementation source through this COM interface."
        ),
    }


def _com_scalar_properties(
    obj: Any, excluded: set[str] | None = None
) -> dict[str, Any]:
    excluded = (excluded or set()) | {"Application", "Parent"}
    values: dict[str, Any] = {}
    errors: dict[str, str] = {}
    try:
        type_info = obj._oleobj_.GetTypeInfo()
        type_attr = type_info.GetTypeAttr()
        pythoncom, _, _, _ = _require_com_dependencies()
        seen: set[str] = set()
        for index in range(type_attr[6]):
            descriptor = type_info.GetFuncDesc(index)
            names = type_info.GetNames(descriptor[0])
            if not names or descriptor[4] != pythoncom.DISPATCH_PROPERTYGET:
                continue
            name = names[0]
            if name in seen or name in excluded or len(names) > 1:
                continue
            seen.add(name)
            try:
                value = getattr(obj, name)
                if value is None or isinstance(
                    value, (str, bool, int, float, datetime, date)
                ) or _is_com_time(value):
                    values[name] = _json_value(value)
                elif hasattr(value, "Count"):
                    values[name] = {"collection_count": int(value.Count)}
                elif hasattr(value, "SerialNumber"):
                    values[name] = {
                        "shape_serial_number": _json_value(value.SerialNumber)
                    }
            except Exception as error:
                errors[name] = _error_text(error)
    except Exception as error:
        return {"properties": {}, "type_info_error": _error_text(error)}
    result: dict[str, Any] = {"properties": values}
    if errors:
        result["property_errors"] = errors
    return result


def _shape_record(shape: Any, index: int, modules: list[Any]) -> dict[str, Any]:
    property_names = (
        "Left",
        "Top",
        "Right",
        "Bottom",
        "Tag",
        "SerialNumber",
        "Type",
        "Selected",
        "Visible",
        "LineColor",
        "FillColor",
        "TextValue",
        "TextColor",
        "LineStyle",
        "LineWidth",
        "BrushPattern",
        "GraphicType",
        "DefaultDescription",
        "UserDescription",
        "FontSize",
        "FontBold",
        "FontItalic",
        "FontFaceName",
        "ShowDimensions",
        "ShowDimensionsLineLength",
        "BeginArrowheadStyle",
        "EndArrowheadStyle",
        "LinePatternName",
        "LinePatternType",
        "ReferencePoint",
    )
    record: dict[str, Any] = {"index": index}
    errors: dict[str, str] = {}
    for name in property_names:
        try:
            record[name] = _json_value(getattr(shape, name))
        except Exception as error:
            errors[name] = _error_text(error)

    parent_module = _safe_property(shape, "ParentModule")
    endpoint = _endpoint_record(parent_module, modules)
    if endpoint:
        record["parent_module"] = endpoint
    point_count = int(_safe_property(shape, "PointCount", 0) or 0)
    record["point_count"] = point_count
    points = []
    for point_index in range(1, point_count + 1):
        try:
            points.append(_json_value(shape.Point(point_index)))
        except Exception as error:
            errors[f"Point({point_index})"] = _error_text(error)
    record["points"] = points
    record["child_shape_count"] = _collection_count(shape, "Shapes")
    if errors:
        record["unavailable_properties"] = errors
    return record


def _audit_animation(
    model: Any, modules: list[Any], max_items: int
) -> dict[str, Any]:
    shapes = [item for item in model.Shapes if item is not None]
    shape_records = [
        _shape_record(shape, index, modules)
        for index, shape in enumerate(shapes[:max_items], start=1)
    ]
    embedded_records = []
    try:
        embedded_items = [item for item in model.Embeddeds if item is not None]
        for index, embedded in enumerate(embedded_items[:max_items], start=1):
            embedded_shape = _safe_property(embedded, "shape")
            embedded_records.append(
                {
                    "index": index,
                    "name": _json_value(_safe_property(embedded, "Name")),
                    "shape_serial_number": _json_value(
                        _safe_property(embedded_shape, "SerialNumber")
                    ),
                    "object_api_exposed": True,
                }
            )
    except Exception as error:
        embedded_items = []
        embedded_records = [{"collection_error": _error_text(error)}]

    status_collections: dict[str, Any] = {}
    for collection_name in ANIMATION_COLLECTIONS:
        try:
            items = [item for item in getattr(model, collection_name) if item is not None]
            records = []
            for index, item in enumerate(items[:max_items], start=1):
                item_record = {"index": index, **_com_scalar_properties(item)}
                item_shape = _safe_property(item, "shape")
                if item_shape is not None:
                    item_record["shape"] = _shape_record(item_shape, index, modules)
                records.append(item_record)
            status_collections[collection_name] = {
                "count": len(items),
                "returned": len(records),
                "truncated": len(items) > len(records),
                "items": records,
            }
        except Exception as error:
            status_collections[collection_name] = {
                "accessible": False,
                "error": _error_text(error),
            }

    pictures: dict[str, Any] = {}
    for collection_name in PICTURE_COLLECTIONS:
        try:
            items = [item for item in getattr(model, collection_name) if item is not None]
            records = []
            for index, item in enumerate(items[:max_items], start=1):
                states = []
                try:
                    states = [
                        {
                            "name": _json_value(_safe_property(state, "Name")),
                            "picture_id": _json_value(
                                _safe_property(state, "PictureID")
                            ),
                        }
                        for state in item.States
                        if state is not None
                    ]
                except Exception:
                    pass
                record: dict[str, Any] = {"index": index, "states": states}
                picture_shape = _safe_property(item, "shape")
                if picture_shape is not None:
                    record["shape"] = _shape_record(picture_shape, index, modules)
                records.append(record)
            pictures[collection_name] = {
                "count": len(items),
                "returned": len(records),
                "truncated": len(items) > len(records),
                "items": records,
            }
        except Exception as error:
            pictures[collection_name] = {
                "accessible": False,
                "error": _error_text(error),
            }

    return {
        "shapes": {
            "count": len(shapes),
            "returned": len(shape_records),
            "truncated": len(shapes) > len(shape_records),
            "items": shape_records,
        },
        "embedded_objects": {
            "count": len(embedded_items),
            "returned": len(embedded_records),
            "truncated": len(embedded_items) > len(embedded_records),
            "items": embedded_records,
        },
        "status_displays": status_collections,
        "pictures": pictures,
    }


def _audit_material_handling(model: Any, max_items: int) -> dict[str, Any]:
    collections: dict[str, Any] = {}
    for collection_name in MATERIAL_COLLECTIONS:
        try:
            items = [item for item in getattr(model, collection_name) if item is not None]
            records = [
                {"index": index, **_com_scalar_properties(item)}
                for index, item in enumerate(items[:max_items], start=1)
            ]
            collections[collection_name] = {
                "count": len(items),
                "returned": len(records),
                "truncated": len(items) > len(records),
                "items": records,
            }
        except Exception as error:
            collections[collection_name] = {
                "accessible": False,
                "error": _error_text(error),
            }
    return {"collections": collections}


def inspect_compound_file(
    model_path: str,
    include_payloads: bool = False,
    max_payload_bytes: int = DEFAULT_MAX_BINARY_BYTES,
) -> dict[str, Any]:
    path = _resolve_allowed_file(model_path, MODEL_EXTENSIONS)
    max_payload_bytes = _clamp(
        max_payload_bytes, 1, 100_000_000, "max_payload_bytes"
    )
    if not olefile.isOleFile(str(path)):
        return {"path": str(path), "ole_compound_file": False, "entries": []}

    entries = []
    remaining = max_payload_bytes
    with olefile.OleFileIO(str(path)) as compound:
        for entry_path in compound.listdir(streams=True, storages=True):
            entry_type = compound.get_type(entry_path)
            record: dict[str, Any] = {
                "path": "/".join(entry_path),
                "type": "stream" if entry_type == 2 else "storage",
            }
            if entry_type == 2:
                data = compound.openstream(entry_path).read()
                record["size_bytes"] = len(data)
                record["sha256"] = hashlib.sha256(data).hexdigest()
                if include_payloads:
                    captured = min(len(data), remaining)
                    record["captured_bytes"] = captured
                    record["payload_truncated"] = captured < len(data)
                    record["payload_base64"] = base64.b64encode(
                        data[:captured]
                    ).decode("ascii")
                    remaining -= captured
            entries.append(record)
    streams = [item for item in entries if item["type"] == "stream"]
    return {
        "path": str(path),
        "ole_compound_file": True,
        "entry_count": len(entries),
        "stream_count": len(streams),
        "storage_count": len(entries) - len(streams),
        "embedding_stream_count": sum(
            item["path"].lower().startswith("embedding ") for item in streams
        ),
        "vba_stream_count": sum(
            item["path"].lower().startswith("apc/") for item in streams
        ),
        "payloads_included": include_payloads,
        "payload_budget_bytes": max_payload_bytes,
        "captured_payload_bytes": max_payload_bytes - remaining,
        "entries": entries,
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_siman_source_from_path(
    path: Path, max_chars: int = DEFAULT_MAX_SIMAN_CHARS
) -> dict[str, Any]:
    max_chars = _clamp(max_chars, 1, 20_000_000, "max_chars")
    original_hash_before = _file_sha256(path)
    with tempfile.TemporaryDirectory(prefix="arena_mcp_siman_") as temp_directory:
        temp_path = Path(temp_directory) / path.name
        shutil.copy2(path, temp_path)
        pythoncom, _, _, _ = _require_com_dependencies()
        with ArenaSession(temp_path) as session:
            session.model.WriteSIMAN()
            deadline = time.monotonic() + DEFAULT_COM_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                generated_suffixes = {
                    item.suffix.lower()
                    for item in Path(temp_directory).iterdir()
                    if item.is_file() and item != temp_path
                }
                if {".mod", ".exp"}.issubset(generated_suffixes):
                    break
                pythoncom.PumpWaitingMessages()
                time.sleep(0.1)

        files = []
        remaining = max_chars
        for generated in sorted(Path(temp_directory).iterdir()):
            if generated == temp_path or not generated.is_file():
                continue
            data = generated.read_bytes()
            try:
                content = data.decode("utf-8-sig")
                encoding = "utf-8-sig"
            except UnicodeDecodeError:
                content = data.decode("cp1252", errors="replace")
                encoding = "cp1252"
            captured = min(len(content), remaining)
            files.append(
                {
                    "name": generated.name,
                    "suffix": generated.suffix.lower(),
                    "size_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "encoding": encoding,
                    "character_count": len(content),
                    "captured_characters": captured,
                    "truncated": captured < len(content),
                    "content": content[:captured],
                }
            )
            remaining -= captured
        original_hash_after = _file_sha256(path)
        generated_suffixes = {item["suffix"] for item in files}
        required_suffixes = {".mod", ".exp"}
        missing_required_suffixes = sorted(required_suffixes - generated_suffixes)
        generated = not missing_required_suffixes
        return {
            "generated": generated,
            "missing_required_suffixes": missing_required_suffixes,
            "temporary_copy": True,
            "original_untouched": original_hash_before == original_hash_after,
            "original_sha256_before": original_hash_before,
            "original_sha256_after": original_hash_after,
            "source_complete": generated
            and all(not item["truncated"] for item in files),
            "file_count": len(files),
            "files": files,
        }


def extract_siman_source(
    model_path: str, max_chars: int = DEFAULT_MAX_SIMAN_CHARS
) -> dict[str, Any]:
    path = _resolve_allowed_file(model_path, MODEL_EXTENSIONS)
    result = _extract_siman_source_from_path(path, max_chars)
    return {"source": str(path), **result}


def _audit_siman_runtime(model: Any, max_items: int) -> dict[str, Any]:
    try:
        siman = model.SIMAN
        type_info = siman._oleobj_.GetTypeInfo()
        type_attr = type_info.GetTypeAttr()
        members = []
        seen = set()
        for index in range(type_attr[6]):
            descriptor = type_info.GetFuncDesc(index)
            names = type_info.GetNames(descriptor[0])
            if not names or names[0] in seen:
                continue
            seen.add(names[0])
            members.append(
                {
                    "name": names[0],
                    "invocation_kind": descriptor[4],
                    "arguments": names[1:],
                }
            )
        return {
            "accessible": True,
            "member_count": len(members),
            "returned": min(len(members), max_items),
            "truncated": len(members) > max_items,
            "members": members[:max_items],
        }
    except Exception as error:
        return {"accessible": False, "error": _error_text(error)}


def _prefix_scope_ids(
    modules: list[dict[str, Any]],
    connections: list[dict[str, Any]],
    scope_id: str,
) -> None:
    id_map = {module["id"]: f"{scope_id}/{module['id']}" for module in modules}
    for module in modules:
        module["id"] = id_map[module["id"]]
    for connection in connections:
        connection["id"] = f"{scope_id}/{connection['id']}"
        for endpoint_name in ("source", "destination"):
            endpoint = connection.get(endpoint_name)
            if not endpoint:
                continue
            endpoint["module_id"] = id_map.get(
                endpoint.get("module_id"), endpoint.get("module_id")
            )
            endpoint["candidate_module_ids"] = [
                id_map.get(item, item)
                for item in endpoint.get("candidate_module_ids", [])
            ]


def _audit_submodels(
    model: Any,
    max_items: int,
    max_modules: int,
    max_repeat_rows: int,
) -> dict[str, Any]:
    state = {"submodels": 0, "modules": 0, "truncated": False}

    def walk(container: Any, scope: str, depth: int) -> list[dict[str, Any]]:
        if depth > 50:
            state["truncated"] = True
            return []
        try:
            submodels = [item for item in container.Submodels if item is not None]
        except Exception:
            return []
        records = []
        for local_index, submodel in enumerate(submodels, start=1):
            if state["submodels"] >= max_items:
                state["truncated"] = True
                break
            state["submodels"] += 1
            submodel_id = f"{scope}/submodel-{local_index:04d}"
            record: dict[str, Any] = {
                "id": submodel_id,
                "depth": depth,
                "name": _json_value(_safe_property(submodel, "Name")),
                "description": _json_value(_safe_property(submodel, "Description")),
                "entry_points": _json_value(
                    _safe_property(submodel, "NumEntryPoints")
                ),
                "exit_points": _json_value(_safe_property(submodel, "NumExitPoints")),
            }
            try:
                nested_model = submodel.Model
                com_modules = [item for item in nested_model.Modules if item is not None]
                remaining = max(0, max_modules - state["modules"])
                selected = com_modules[:remaining]
                modules = [
                    _module_record(item, index, True, max_repeat_rows)
                    for index, item in enumerate(selected, start=1)
                ]
                connections = _extract_connections(nested_model, com_modules)
                _prefix_scope_ids(modules, connections, submodel_id)
                state["modules"] += len(modules)
                if len(selected) < len(com_modules):
                    state["truncated"] = True
                record.update(
                    {
                        "collections": _collection_inventory(nested_model),
                        "modules": modules,
                        "module_total": len(com_modules),
                        "connections": connections,
                        "animation": _audit_animation(
                            nested_model, com_modules, max_items
                        ),
                        "material_handling": _audit_material_handling(
                            nested_model, max_items
                        ),
                        "children": walk(nested_model, submodel_id, depth + 1),
                    }
                )
            except Exception as error:
                record["model_error"] = _error_text(error)
            records.append(record)
        return records

    try:
        root_count = int(model.Submodels.Count)
    except Exception as error:
        return {"accessible": False, "count": None, "error": _error_text(error)}
    items = walk(model, "root", 0)
    return {
        "accessible": True,
        "root_count": root_count,
        "count": state["submodels"],
        "module_count": state["modules"],
        "truncated": state["truncated"],
        "items": items,
    }


def _coverage_item(
    key: str,
    label: str,
    status: str,
    evidence: dict[str, Any],
    captured: str,
    gap: str | None,
    translation_action: str,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "status": status,
        "evidence": evidence,
        "captured": captured,
        "gap": gap,
        "translation_action": translation_action,
    }


def _build_coverage_report(
    modules: list[dict[str, Any]],
    module_total: int,
    collections: dict[str, dict[str, Any]],
    expressions: dict[str, Any],
    vba: dict[str, Any],
    submodels: dict[str, Any],
    templates: dict[str, Any],
    dependencies: dict[str, Any],
    animation: dict[str, Any] | None = None,
    material_handling: dict[str, Any] | None = None,
    compound_file: dict[str, Any] | None = None,
    siman_source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    animation = animation or {}
    material_handling = material_handling or {}
    compound_file = compound_file or {}
    siman_source = siman_source or {"requested": False}
    definitions = Counter(module["definition"] for module in modules)
    module_truncated = len(modules) < module_total
    repeat_truncated = any(
        group.get("truncated", False)
        for module in modules
        for group in module.get("operands", {}).get("repeat_groups", [])
    )
    read_errors = sum(
        int("read_error" in scalar)
        for module in modules
        for scalar in module.get("operands", {}).get("scalars", [])
    ) + sum(
        len(row.get("read_errors", {}))
        for module in modules
        for group in module.get("operands", {}).get("repeat_groups", [])
        for row in group.get("rows", [])
    )
    vba_lines = int(vba.get("line_count", 0))
    vba_status = "not_present"
    if not vba.get("accessible"):
        vba_status = "unavailable"
    elif vba_lines and vba.get("source_complete"):
        vba_status = "extracted"
    elif vba_lines:
        vba_status = "metadata_only"
    elif any(definition == "VBA" for definition in definitions):
        vba_status = "partial"

    resource_data_counts = {
        name: definitions.get(name, 0) for name in ("Resource", "Schedule", "Set")
    }
    manual_definitions = sorted(
        name
        for name in definitions
        if name not in AUTOMATIC_MODULES and name not in ASSISTED_MODULES
    )
    material_collections = material_handling.get("collections", {})
    material_count = sum(
        int(item.get("count", 0) or 0) for item in material_collections.values()
    )
    material_truncated = any(
        item.get("truncated", False) for item in material_collections.values()
    )
    shapes = animation.get("shapes", {})
    embedded = animation.get("embedded_objects", {})
    animation_truncated = bool(shapes.get("truncated") or embedded.get("truncated"))
    siman_status = "not_requested"
    if siman_source.get("requested"):
        if siman_source.get("source_complete"):
            siman_status = "extracted"
        elif siman_source.get("generated"):
            siman_status = "partial"
        else:
            siman_status = "unavailable"
    compound_status = "metadata_only"
    if compound_file.get("payloads_included"):
        compound_status = (
            "extracted"
            if compound_file.get("captured_payload_bytes", 0)
            >= sum(
                int(item.get("size_bytes", 0) or 0)
                for item in compound_file.get("entries", [])
                if item.get("type") == "stream"
            )
            else "partial"
        )
    expressions_complete = (
        not expressions.get("truncated", False)
        and not expressions.get("lexical_error_count_in_returned", 0)
    )
    templates_complete = (
        templates.get("accessible", False)
        and not templates.get("unresolved_definitions")
        and siman_source.get("source_complete", False)
    )

    items = [
        _coverage_item(
            "project_metadata",
            "Project metadata",
            "extracted",
            {"model_opened": True},
            "Identity, path, title, description, analyst, modification, and save state.",
            None,
            "Map directly into Python model metadata.",
        ),
        _coverage_item(
            "run_configuration",
            "Run configuration",
            "extracted",
            {
                "groups": [
                    "replication",
                    "time",
                    "initialization",
                    "statistics",
                    "reporting",
                    "randomness",
                    "parallelism",
                ]
            },
            "Replication, timing, initialization, statistics, reporting, randomness, and parallel settings.",
            "Arena defaults and version-specific behavior still require semantic validation.",
            "Normalize Arena time-unit enums and termination expressions.",
        ),
        _coverage_item(
            "module_logic",
            "Module logic and operands",
            "partial" if module_truncated or repeat_truncated or read_errors else "extracted",
            {
                "modules": len(modules),
                "model_modules": module_total,
                "read_errors": read_errors,
                "module_truncated": module_truncated,
                "repeat_truncated": repeat_truncated,
            },
            "Definitions, captions, scalar operands, and repeating operand rows.",
            "Raw operand capture does not prove Python semantic equivalence.",
            "Translate supported definitions and validate every operand mapping.",
        ),
        _coverage_item(
            "connections",
            "Process-flow connections",
            "extracted",
            {"count": collections.get("Connections", {}).get("count")},
            "Directed connection endpoints and connection points.",
            "Material-handling routes and network links are separate surfaces.",
            "Build the Python event-flow graph.",
        ),
        _coverage_item(
            "expressions",
            "Expressions and conditions",
            "extracted" if expressions_complete else "partial",
            {
                "candidates": expressions.get("candidate_count", 0),
                "truncated": expressions.get("truncated", False),
            },
            "Raw strings plus lexical tokens, delimiter validation, identifiers, and function calls.",
            "Arena types, overloads, and runtime symbol resolution still require translator validation.",
            "Resolve extracted symbols against data modules and Python runtime mappings.",
        ),
        _coverage_item(
            "resources_schedules_sets",
            "Resources, schedules, and sets",
            "extracted" if any(resource_data_counts.values()) else "not_present",
            resource_data_counts,
            "All visible operands and repeating rows for these data modules.",
            "Calendar semantics, failures, state sets, and indirect references are not validated.",
            "Create normalized Python resource, schedule, and set definitions.",
        ),
        _coverage_item(
            "vba",
            "VBA projects and code",
            vba_status,
            {
                "accessible": vba.get("accessible", False),
                "projects": vba.get("project_count", 0),
                "lines": vba_lines,
                "captured_lines": vba.get("captured_lines", 0),
            },
            "Project, component, reference, and line-count metadata; source when requested.",
            None if vba_status in {"extracted", "not_present"} else "VBA source is not fully captured.",
            "Manually translate VBA side effects and event hooks into explicit Python functions.",
        ),
        _coverage_item(
            "submodels",
            "Submodels",
            (
                "partial"
                if submodels.get("truncated")
                else "extracted"
                if submodels.get("count")
                else "not_present"
            ),
            {
                "accessible": submodels.get("accessible", False),
                "count": submodels.get("count"),
                "truncated": submodels.get("truncated", False),
            },
            "Recursive nested model modules, operands, connections, collection counts, and entry/exit metadata.",
            "External submodel references still require dependency resolution.",
            "Preserve scoped module identifiers and explicit submodel boundaries.",
        ),
        _coverage_item(
            "templates",
            "Template and module-definition provenance",
            (
                "extracted"
                if templates_complete
                else "partial"
                if templates.get("accessible")
                else "unavailable"
            ),
            {
                "installed_panels": len(templates.get("installed_panels", [])),
                "unresolved_definitions": templates.get("unresolved_definitions", []),
            },
            "Full attached panel schemas, module definitions, operand metadata, defaults, control types, and pick lists.",
            (
                None
                if templates_complete
                else "Arena does not expose template implementation source through the panel COM API; request SIMAN source to capture generated semantics."
            ),
            "Require a mapping plugin for unresolved or nonstandard definitions.",
        ),
        _coverage_item(
            "external_dependencies",
            "External dependencies",
            "partial",
            {
                "candidates": dependencies.get("candidate_count", 0),
                "truncated": dependencies.get("truncated", False),
            },
            "Model properties, integration modules, file/URL/ODBC-like operands, OPC flags, and VBA references.",
            "Static heuristics cannot discover dynamically constructed references.",
            "Resolve, allowlist, and replace each dependency with a Python adapter.",
        ),
        _coverage_item(
            "material_handling",
            "Material-handling networks",
            (
                "partial"
                if material_truncated
                else "extracted"
                if material_count
                else "not_present"
            ),
            {"item_count": material_count, "truncated": material_truncated},
            "All readable scalar properties and collection metadata for stations, routes, distances, segments, links, queues, and areas.",
            "Arena may reject individual version-specific property getters; errors are preserved per item.",
            "Map collection records and related module operands into transport-network objects.",
        ),
        _coverage_item(
            "animation",
            "Animation and visual layout",
            "partial" if animation_truncated else "extracted",
            {
                name: collections.get(name, {}).get("count")
                for name in ("Shapes", "Embeddeds", "StatusVariables", "StatusPlots")
            },
            "Shape geometry, points, styling, text, module links, status displays, picture states, and embedded-object identities.",
            "Raw embedded bytes are reported by the compound-file surface.",
            "Keep visualization separate from simulation semantics while preserving source layout.",
        ),
        _coverage_item(
            "siman",
            "Generated SIMAN model",
            siman_status,
            {
                "requested": siman_source.get("requested", False),
                "generated_files": siman_source.get("file_count", 0),
                "source_complete": siman_source.get("source_complete", False),
            },
            "Generated .mod, .exp, and .opw text from an isolated temporary model copy when requested.",
            None if siman_status == "extracted" else "SIMAN source was not requested or was truncated/unavailable.",
            "Use generated SIMAN as a secondary semantic cross-check, not the primary IR.",
        ),
        _coverage_item(
            "compound_payloads",
            "Embedded and proprietary compound-file streams",
            compound_status,
            {
                "streams": compound_file.get("stream_count", 0),
                "embedding_streams": compound_file.get("embedding_stream_count", 0),
                "vba_streams": compound_file.get("vba_stream_count", 0),
                "payloads_included": compound_file.get("payloads_included", False),
            },
            "Every OLE storage/stream path, byte size, and SHA-256; optional base64 payload bytes.",
            None if compound_status == "extracted" else "Raw bytes are omitted or limited by the payload budget.",
            "Decode only payload formats needed by the Python visualization or integration layer.",
        ),
        _coverage_item(
            "opc",
            "OPC data items",
            (
                "extracted"
                if collections.get("OPCDataItems", {}).get("available")
                else "unavailable"
            ),
            collections.get("OPCDataItems", {}),
            "OPC collection metadata when Arena permits the getter.",
            None if collections.get("OPCDataItems", {}).get("available") else "Arena returned a COM failure without initializing or running OPC.",
            "Do not mutate InitOPCSimVariables during a read-only audit; provide OPC configuration separately if needed.",
        ),
        _coverage_item(
            "results",
            "Simulation results",
            "separate_tool",
            {"supported_sections": sorted(RESULT_VIEWS)},
            "SQLite result schema and six supported statistics views through separate tools.",
            "The model audit does not run Arena or discover every report view automatically.",
            "Use results as regression baselines for the translated Python model.",
        ),
    ]

    blockers = []
    if module_truncated or repeat_truncated or read_errors:
        blockers.append("Module operand extraction is incomplete.")
    if vba_lines and vba_status != "extracted":
        blockers.append("VBA code exists but its source is not fully captured.")
    if dependencies.get("candidate_count", 0):
        blockers.append("External dependency candidates require validation.")
    if expressions.get("truncated"):
        blockers.append("Expression analysis reached max_audit_items.")
    if expressions.get("lexical_error_count_in_returned", 0):
        blockers.append("One or more expression candidates failed lexical validation.")
    if manual_definitions:
        blockers.append(
            "Manual or unmapped module definitions exist: " + ", ".join(manual_definitions)
        )
    if submodels.get("count"):
        if submodels.get("truncated"):
            blockers.append("Recursive submodel extraction reached an audit limit.")
    if siman_source.get("requested") and not siman_source.get("source_complete"):
        blockers.append("Requested SIMAN source extraction is incomplete.")

    status_counts = dict(sorted(Counter(item["status"] for item in items).items()))
    return {
        "translation_readiness": "review_required" if blockers else "ready_for_mapping",
        "summary": {
            "surface_count": len(items),
            "status_counts": status_counts,
            "blocker_count": len(blockers),
        },
        "blockers": blockers,
        "manual_or_unmapped_definitions": manual_definitions,
        "surfaces": items,
        "status_meanings": {
            "extracted": "Data is captured, but translation still requires semantic validation.",
            "partial": "Only part of the data or semantics is captured.",
            "metadata_only": "Presence and basic metadata are captured, not the full content.",
            "not_present": "No evidence of this surface was found in the model.",
            "not_extracted": "Arena exposes this surface, but this MCP does not read it.",
            "not_requested": "The optional extraction was not requested for this audit.",
            "unavailable": "The surface could not be accessed through the current COM session.",
            "separate_tool": "Coverage is provided by another MCP tool or artifact.",
        },
    }


def _audit_model_data_for_session(
    session: ArenaSession,
    path: Path,
    *,
    include_vba_source: bool,
    max_modules: int,
    max_repeat_rows: int,
    max_audit_items: int,
    max_vba_lines: int,
    compound_file: dict[str, Any],
    siman_source: dict[str, Any],
) -> dict[str, Any]:
    model = session.model
    com_modules = [item for item in model.Modules if item is not None]
    modules = [
        _module_record(module, index, True, max_repeat_rows)
        for index, module in enumerate(com_modules[:max_modules], start=1)
    ]
    collections = _collection_inventory(model)
    expressions = _audit_expressions(modules, max_audit_items)
    vba = _audit_vba(session.application, include_vba_source, max_vba_lines)
    submodels = _audit_submodels(
        model, max_audit_items, max_modules, max_repeat_rows
    )
    templates = _audit_template_panels(session.application, modules)
    animation = _audit_animation(model, com_modules, max_audit_items)
    material_handling = _audit_material_handling(model, max_audit_items)
    siman_runtime = _audit_siman_runtime(model, max_audit_items)
    dependencies = _audit_external_dependencies(
        model, modules, vba, max_audit_items
    )
    data_modules = [
        module
        for module in modules
        if module["definition"] in TRANSLATION_DATA_MODULES
    ]
    coverage = _build_coverage_report(
        modules,
        len(com_modules),
        collections,
        expressions,
        vba,
        submodels,
        templates,
        dependencies,
        animation,
        material_handling,
        compound_file,
        siman_source,
    )
    return {
        "audit_schema_version": AUDIT_SCHEMA_VERSION,
        "ir_schema_version": IR_SCHEMA_VERSION,
        "audited_at": _utc_now(),
        "source": {"path": str(path), "read_only": True},
        "arena": {
            "version": _json_value(session.application.Version),
            "license_type": _json_value(session.application.LicenseType),
        },
        "model": _model_summary(model, path),
        "run_configuration": _model_run_configuration(model),
        "inventory": {
            "collections": collections,
            "module_definitions": _module_definition_inventory(modules),
            "data_modules": data_modules,
            "expressions": expressions,
            "vba": vba,
            "submodels": submodels,
            "templates": templates,
            "external_dependencies": dependencies,
            "animation": animation,
            "material_handling": material_handling,
            "compound_file": compound_file,
            "siman_runtime_api": siman_runtime,
            "siman_source": siman_source,
        },
        "coverage_report": coverage,
    }


def audit_model_data(
    model_path: str,
    include_vba_source: bool = False,
    include_siman_source: bool = False,
    include_binary_payloads: bool = False,
    max_modules: int = DEFAULT_MAX_MODULES,
    max_repeat_rows: int = DEFAULT_MAX_REPEAT_ROWS,
    max_audit_items: int = DEFAULT_MAX_AUDIT_ITEMS,
    max_vba_lines: int = DEFAULT_MAX_VBA_LINES,
    max_siman_chars: int = DEFAULT_MAX_SIMAN_CHARS,
    max_binary_bytes: int = DEFAULT_MAX_BINARY_BYTES,
) -> dict[str, Any]:
    path = _resolve_allowed_file(model_path, MODEL_EXTENSIONS)
    max_modules = _clamp(max_modules, 1, 10_000, "max_modules")
    max_repeat_rows = _clamp(max_repeat_rows, 1, 10_000, "max_repeat_rows")
    max_audit_items = _clamp(max_audit_items, 1, 10_000, "max_audit_items")
    max_vba_lines = _clamp(max_vba_lines, 1, 100_000, "max_vba_lines")
    max_siman_chars = _clamp(max_siman_chars, 1, 20_000_000, "max_siman_chars")
    max_binary_bytes = _clamp(
        max_binary_bytes, 1, 100_000_000, "max_binary_bytes"
    )

    compound_file = inspect_compound_file(
        str(path), include_binary_payloads, max_binary_bytes
    )
    siman_source: dict[str, Any] = {"requested": include_siman_source}
    if include_siman_source:
        try:
            siman_source.update(
                _extract_siman_source_from_path(path, max_siman_chars)
            )
        except Exception as error:
            siman_source.update({"generated": False, "error": _error_text(error)})

    with ArenaSession(path) as session:
        return _audit_model_data_for_session(
            session,
            path,
            include_vba_source=include_vba_source,
            max_modules=max_modules,
            max_repeat_rows=max_repeat_rows,
            max_audit_items=max_audit_items,
            max_vba_lines=max_vba_lines,
            compound_file=compound_file,
            siman_source=siman_source,
        )


def extract_project_bar_catalog(model_path: str) -> dict[str, Any]:
    path = _resolve_allowed_file(model_path, MODEL_EXTENSIONS)
    with ArenaSession(path) as session:
        modules = [
            _module_record(item, index, False, 1)
            for index, item in enumerate(session.model.Modules, start=1)
            if item is not None
        ]
        return {
            "source": str(path),
            "read_only": True,
            "project_bar": _audit_template_panels(session.application, modules),
        }


def extract_visual_model(
    model_path: str,
    max_items: int = DEFAULT_MAX_AUDIT_ITEMS,
    include_binary_payloads: bool = False,
    max_binary_bytes: int = DEFAULT_MAX_BINARY_BYTES,
) -> dict[str, Any]:
    path = _resolve_allowed_file(model_path, MODEL_EXTENSIONS)
    max_items = _clamp(max_items, 1, 10_000, "max_items")
    compound_file = inspect_compound_file(
        str(path), include_binary_payloads, max_binary_bytes
    )
    with ArenaSession(path) as session:
        modules = [item for item in session.model.Modules if item is not None]
        animation = _audit_animation(session.model, modules, max_items)
    return {
        "source": str(path),
        "read_only": True,
        "animation": animation,
        "compound_file": compound_file,
    }


def extract_submodel_tree(
    model_path: str,
    max_items: int = DEFAULT_MAX_AUDIT_ITEMS,
    max_modules: int = DEFAULT_MAX_MODULES,
    max_repeat_rows: int = DEFAULT_MAX_REPEAT_ROWS,
) -> dict[str, Any]:
    path = _resolve_allowed_file(model_path, MODEL_EXTENSIONS)
    max_items = _clamp(max_items, 1, 10_000, "max_items")
    max_modules = _clamp(max_modules, 1, 10_000, "max_modules")
    max_repeat_rows = _clamp(max_repeat_rows, 1, 10_000, "max_repeat_rows")
    with ArenaSession(path) as session:
        submodels = _audit_submodels(
            session.model, max_items, max_modules, max_repeat_rows
        )
    return {"source": str(path), "read_only": True, "submodels": submodels}


def extract_material_handling(
    model_path: str, max_items: int = DEFAULT_MAX_AUDIT_ITEMS
) -> dict[str, Any]:
    path = _resolve_allowed_file(model_path, MODEL_EXTENSIONS)
    max_items = _clamp(max_items, 1, 10_000, "max_items")
    with ArenaSession(path) as session:
        material = _audit_material_handling(session.model, max_items)
    return {"source": str(path), "read_only": True, **material}


def _compatibility_from_modules(modules: list[dict[str, Any]]) -> dict[str, Any]:
    definition_counts = Counter(item["definition"] for item in modules)
    groups: dict[str, list[dict[str, Any]]] = {
        "automatic": [],
        "assisted": [],
        "manual": [],
    }
    for definition, count in sorted(definition_counts.items()):
        if definition in AUTOMATIC_MODULES:
            category = "automatic"
        elif definition in ASSISTED_MODULES:
            category = "assisted"
        else:
            category = "manual"
        groups[category].append({"definition": definition, "count": count})

    return {
        "status": "preliminary",
        "automatic": groups["automatic"],
        "assisted": groups["assisted"],
        "manual_or_unmapped": groups["manual"],
        "notes": [
            "Classification is based on module definition names, not full expression semantics.",
            "Use audit_arena_model_data for VBA, templates, integrations, visuals, submodels, and SIMAN coverage.",
            "No Python simulation code is generated by this extractor.",
        ],
    }


def inspect_model(model_path: str) -> dict[str, Any]:
    path = _resolve_allowed_file(model_path, MODEL_EXTENSIONS)
    with ArenaSession(path) as session:
        model = session.model
        modules = [
            _module_record(item, index, False, 1)
            for index, item in enumerate(list(model.Modules), start=1)
            if item is not None
        ]
        return {
            "schema_version": IR_SCHEMA_VERSION,
            "extracted_at": _utc_now(),
            "source": {"path": str(path), "read_only": True},
            "model": _model_summary(model, path),
            "run_configuration": _model_run_configuration(model),
            "module_definitions": dict(
                sorted(Counter(item["definition"] for item in modules).items())
            ),
            "compatibility": _compatibility_from_modules(modules),
        }


def extract_modules(
    model_path: str,
    offset: int = 0,
    limit: int = 100,
    include_operands: bool = True,
    max_repeat_rows: int = DEFAULT_MAX_REPEAT_ROWS,
) -> dict[str, Any]:
    path = _resolve_allowed_file(model_path, MODEL_EXTENSIONS)
    offset = _clamp(offset, 0, 1_000_000, "offset")
    limit = _clamp(limit, 1, DEFAULT_MAX_MODULES, "limit")
    max_repeat_rows = _clamp(max_repeat_rows, 1, 10_000, "max_repeat_rows")
    with ArenaSession(path) as session:
        all_modules = [item for item in list(session.model.Modules) if item is not None]
        selected = all_modules[offset : offset + limit]
        records = [
            _module_record(
                module,
                offset + relative_index,
                include_operands,
                max_repeat_rows,
            )
            for relative_index, module in enumerate(selected, start=1)
        ]
        return {
            "schema_version": IR_SCHEMA_VERSION,
            "source": str(path),
            "offset": offset,
            "limit": limit,
            "total": len(all_modules),
            "returned": len(records),
            "modules": records,
        }


def extract_connections(model_path: str) -> dict[str, Any]:
    path = _resolve_allowed_file(model_path, MODEL_EXTENSIONS)
    with ArenaSession(path) as session:
        modules = [item for item in list(session.model.Modules) if item is not None]
        records = _extract_connections(session.model, modules)
        return {
            "schema_version": IR_SCHEMA_VERSION,
            "source": str(path),
            "count": len(records),
            "connections": records,
        }


def _extract_model_ir_for_session(
    session: ArenaSession,
    path: Path,
    max_modules: int,
    max_repeat_rows: int,
) -> dict[str, Any]:
    model = session.model
    com_modules = [item for item in list(model.Modules) if item is not None]
    truncated = len(com_modules) > max_modules
    selected_modules = com_modules[:max_modules]
    modules = [
        _module_record(module, index, True, max_repeat_rows)
        for index, module in enumerate(selected_modules, start=1)
    ]
    connections = _extract_connections(model, com_modules)
    warnings: list[str] = []
    if truncated:
        warnings.append(
            f"Module extraction was truncated at {max_modules} of {len(com_modules)} modules."
        )
    if any(
        group["truncated"]
        for module in modules
        for group in module["operands"]["repeat_groups"]
    ):
        warnings.append(
            f"One or more repeat groups reached max_repeat_rows={max_repeat_rows}."
        )
    return {
        "schema_version": IR_SCHEMA_VERSION,
        "extracted_at": _utc_now(),
        "source": {"path": str(path), "read_only": True},
        "arena": {
            "version": _json_value(session.application.Version),
            "license_type": _json_value(session.application.LicenseType),
        },
        "model": _model_summary(model, path),
        "run_configuration": _model_run_configuration(model),
        "modules": modules,
        "connections": connections,
        "compatibility": _compatibility_from_modules(modules),
        "warnings": warnings,
    }


def extract_model_ir(
    model_path: str,
    max_modules: int = DEFAULT_MAX_MODULES,
    max_repeat_rows: int = DEFAULT_MAX_REPEAT_ROWS,
) -> dict[str, Any]:
    path = _resolve_allowed_file(model_path, MODEL_EXTENSIONS)
    max_modules = _clamp(max_modules, 1, 10_000, "max_modules")
    max_repeat_rows = _clamp(max_repeat_rows, 1, 10_000, "max_repeat_rows")
    with ArenaSession(path) as session:
        return _extract_model_ir_for_session(
            session, path, max_modules, max_repeat_rows
        )


def extract_model_report(
    model_path: str,
    include_vba_source: bool = False,
    include_siman_source: bool = False,
    include_binary_payloads: bool = False,
    max_modules: int = DEFAULT_MAX_MODULES,
    max_repeat_rows: int = DEFAULT_MAX_REPEAT_ROWS,
    max_audit_items: int = DEFAULT_MAX_AUDIT_ITEMS,
    max_vba_lines: int = DEFAULT_MAX_VBA_LINES,
    max_siman_chars: int = DEFAULT_MAX_SIMAN_CHARS,
    max_binary_bytes: int = DEFAULT_MAX_BINARY_BYTES,
) -> dict[str, Any]:
    path = _resolve_allowed_file(model_path, MODEL_EXTENSIONS)
    max_modules = _clamp(max_modules, 1, 10_000, "max_modules")
    max_repeat_rows = _clamp(max_repeat_rows, 1, 10_000, "max_repeat_rows")
    max_audit_items = _clamp(max_audit_items, 1, 10_000, "max_audit_items")
    max_vba_lines = _clamp(max_vba_lines, 1, 100_000, "max_vba_lines")
    max_siman_chars = _clamp(max_siman_chars, 1, 20_000_000, "max_siman_chars")
    max_binary_bytes = _clamp(
        max_binary_bytes, 1, 100_000_000, "max_binary_bytes"
    )
    compound_file = inspect_compound_file(
        str(path), include_binary_payloads, max_binary_bytes
    )
    siman_source: dict[str, Any] = {"requested": include_siman_source}
    if include_siman_source:
        try:
            siman_source.update(
                _extract_siman_source_from_path(path, max_siman_chars)
            )
        except Exception as error:
            siman_source.update({"generated": False, "error": _error_text(error)})

    with ArenaSession(path) as session:
        ir = _extract_model_ir_for_session(
            session, path, max_modules, max_repeat_rows
        )
        audit = _audit_model_data_for_session(
            session,
            path,
            include_vba_source=include_vba_source,
            max_modules=max_modules,
            max_repeat_rows=max_repeat_rows,
            max_audit_items=max_audit_items,
            max_vba_lines=max_vba_lines,
            compound_file=compound_file,
            siman_source=siman_source,
        )

    module_surface = next(
        item
        for item in audit["coverage_report"]["surfaces"]
        if item["key"] == "module_logic"
    )
    total_modules = module_surface["evidence"]["model_modules"]
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": _utc_now(),
        "source": {"path": str(path), "read_only": True},
        "completeness": {
            "module_count": len(ir["modules"]),
            "total_modules": total_modules,
            "modules_truncated": len(ir["modules"]) < total_modules,
            "warnings": ir["warnings"],
            "translation_readiness": audit["coverage_report"][
                "translation_readiness"
            ],
        },
        "ir": ir,
        "audit": audit,
    }


def analyze_compatibility(model_path: str) -> dict[str, Any]:
    path = _resolve_allowed_file(model_path, MODEL_EXTENSIONS)
    with ArenaSession(path) as session:
        modules = [
            _module_record(item, index, False, 1)
            for index, item in enumerate(list(session.model.Modules), start=1)
            if item is not None
        ]
        return {
            "source": str(path),
            "module_count": len(modules),
            "compatibility": _compatibility_from_modules(modules),
        }


def inspect_results_database(database_path: str) -> dict[str, Any]:
    path = _resolve_allowed_file(database_path, DATABASE_EXTENSIONS)
    return inspect_results_file(path)


def read_results(
    database_path: str,
    section: str = "project",
    limit: int = 100,
) -> dict[str, Any]:
    path = _resolve_allowed_file(database_path, DATABASE_EXTENSIONS)
    limit = _clamp(limit, 1, 10_000, "limit")
    return read_results_file(path, section, limit, _json_value)


def _main() -> int:
    from arena_mcp.cli import main

    return main()


if __name__ == "__main__":
    raise SystemExit(_main())
