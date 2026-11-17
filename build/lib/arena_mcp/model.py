"""Arena model metadata, operands, connections, and neutral IR."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
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
from arena_mcp.errors import ArenaExtractorError, error_text as _error_text
from .common import (
    ASSISTED_MODULES,
    AUTOMATIC_MODULES,
    DEFAULT_MAX_MODULES,
    DEFAULT_MAX_REPEAT_ROWS,
    IR_SCHEMA_VERSION,
    MODEL_COLLECTIONS,
    MODEL_EXTENSIONS,
    TEMPLATE_DESCRIPTION_PATTERN,
    _clamp,
    _collection_count,
    _json_value,
    _safe_call,
    _safe_property,
    _utc_now,
)
from .paths import (
    _resolve_allowed_file,
)


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
