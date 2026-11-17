"""Animation, visual, and material-handling audits."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from arena_mcp.com import (
    ArenaSession,
    is_com_time as _is_com_time,
    require_com_dependencies as _require_com_dependencies,
)
from arena_mcp.errors import error_text as _error_text
from .common import (
    ANIMATION_COLLECTIONS,
    DEFAULT_MAX_AUDIT_ITEMS,
    DEFAULT_MAX_BINARY_BYTES,
    MATERIAL_COLLECTIONS,
    MODEL_EXTENSIONS,
    PICTURE_COLLECTIONS,
    _clamp,
    _collection_count,
    _json_value,
    _safe_property,
)
from .model import _endpoint_record, _module_record
from .artifacts import inspect_compound_file
from .paths import _resolve_allowed_file
from .audit_vba import (
    _audit_template_panels,
)


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

def extract_material_handling(
    model_path: str, max_items: int = DEFAULT_MAX_AUDIT_ITEMS
) -> dict[str, Any]:
    path = _resolve_allowed_file(model_path, MODEL_EXTENSIONS)
    max_items = _clamp(max_items, 1, 10_000, "max_items")
    with ArenaSession(path) as session:
        material = _audit_material_handling(session.model, max_items)
    return {"source": str(path), "read_only": True, **material}
