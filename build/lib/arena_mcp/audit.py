"""Top-level audit orchestration and public audit API."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from arena_mcp.com import ArenaSession
from arena_mcp.errors import error_text as _error_text
from .common import (
    AUDIT_SCHEMA_VERSION,
    DEFAULT_MAX_AUDIT_ITEMS,
    DEFAULT_MAX_BINARY_BYTES,
    DEFAULT_MAX_MODULES,
    DEFAULT_MAX_REPEAT_ROWS,
    DEFAULT_MAX_SIMAN_CHARS,
    DEFAULT_MAX_VBA_LINES,
    IR_SCHEMA_VERSION,
    MODEL_EXTENSIONS,
    TRANSLATION_DATA_MODULES,
    _clamp,
    _json_value,
    _utc_now,
)
from .model import (
    _collection_inventory,
    _model_run_configuration,
    _model_summary,
    _module_definition_inventory,
    _module_record,
)
from .artifacts import _extract_siman_source_from_path, inspect_compound_file
from .paths import _resolve_allowed_file
from .visual import (
    _audit_animation,
    _audit_material_handling,
)
from .expressions import (
    _audit_expressions,
    _audit_external_dependencies,
)
from .submodels import (
    _audit_siman_runtime,
    _audit_submodels,
)
from .audit_vba import (
    _audit_template_panels,
    _audit_vba,
)
from .coverage import (
    _build_coverage_report,
)


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
