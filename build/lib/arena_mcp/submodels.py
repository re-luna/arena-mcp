"""Recursive submodel and SIMAN runtime audits."""

from __future__ import annotations

from typing import Any
from arena_mcp.com import ArenaSession
from arena_mcp.errors import error_text as _error_text
from .common import (
    DEFAULT_MAX_AUDIT_ITEMS,
    DEFAULT_MAX_MODULES,
    DEFAULT_MAX_REPEAT_ROWS,
    MODEL_EXTENSIONS,
    _clamp,
    _json_value,
    _safe_property,
)
from .model import _collection_inventory, _extract_connections, _module_record
from .paths import _resolve_allowed_file
from .visual import (
    _audit_animation,
    _audit_material_handling,
)


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
