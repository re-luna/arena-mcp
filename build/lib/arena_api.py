"""Shared API for inspecting and extracting Arena models.

New code should import the focused modules under :mod:`arena_mcp`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from arena_mcp.common import (
    ANIMATION_COLLECTIONS,
    ASSISTED_MODULES,
    AUDIT_SCHEMA_VERSION,
    AUTOMATIC_MODULES,
    DATABASE_EXTENSIONS,
    DEFAULT_MAX_AUDIT_ITEMS,
    DEFAULT_MAX_BINARY_BYTES,
    DEFAULT_MAX_MODULES,
    DEFAULT_MAX_REPEAT_ROWS,
    DEFAULT_MAX_SIMAN_CHARS,
    DEFAULT_MAX_VBA_LINES,
    EXPRESSION_FIELD_TERMS,
    EXPRESSION_KEYWORDS,
    EXPRESSION_TOKEN_PATTERN,
    EXTERNAL_REFERENCE_PATTERN,
    IR_SCHEMA_VERSION,
    MATERIAL_COLLECTIONS,
    MODEL_COLLECTIONS,
    MODEL_EXTENSIONS,
    PICTURE_COLLECTIONS,
    REPORT_SCHEMA_VERSION,
    SERVER_NAME,
    TEMPLATE_DESCRIPTION_PATTERN,
    TRANSLATION_DATA_MODULES,
    _clamp,
    _collection_count,
    _json_value,
    _safe_call,
    _safe_property,
    _utc_now,
)
from arena_mcp.paths import (
    _default_roots,
    _is_within,
    _resolve_allowed_file,
)
from arena_mcp.environment import (
    diagnose_arena,
    discover_models,
    get_arena_status,
)
from arena_mcp.model import (
    _collection_inventory,
    _compatibility_from_modules,
    _endpoint_record,
    _extract_connections,
    _extract_model_ir_for_session,
    _extract_operands,
    _model_run_configuration,
    _model_summary,
    _module_definition_inventory,
    _module_definition_name,
    _module_record,
    _module_source_template,
    _operand_metadata,
    _operand_parent_name,
    _read_module_data,
    _same_com_object,
    analyze_compatibility,
    extract_connections,
    extract_model_ir,
    extract_modules,
    inspect_model,
)
from arena_mcp.artifacts import (
    _extract_siman_source_from_path,
    _file_sha256,
    extract_siman_source,
    inspect_compound_file,
)
from arena_mcp.expressions import (
    _analyze_expression,
    _audit_expressions,
    _audit_external_dependencies,
    _iter_module_operand_values,
)
from arena_mcp.audit_vba import (
    _audit_template_panels,
    _audit_vba,
)
from arena_mcp.visual import (
    _audit_animation,
    _audit_material_handling,
    _com_scalar_properties,
    _shape_record,
    extract_material_handling,
    extract_project_bar_catalog,
    extract_visual_model,
)
from arena_mcp.submodels import (
    _audit_siman_runtime,
    _audit_submodels,
    _prefix_scope_ids,
    extract_submodel_tree,
)
from arena_mcp.coverage import (
    _build_coverage_report,
    _coverage_item,
)
from arena_mcp.audit import (
    _audit_model_data_for_session,
    audit_model_data,
)
from arena_mcp.reports import (
    extract_model_report,
)
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
from arena_mcp.results import (
    RESULT_VIEWS,
    inspect_results_file,
    read_results_file,
)


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
