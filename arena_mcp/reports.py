"""Combined audit and model extraction reports."""

from __future__ import annotations

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
    DEFAULT_MAX_AUDIT_ITEMS,
    DEFAULT_MAX_BINARY_BYTES,
    DEFAULT_MAX_MODULES,
    DEFAULT_MAX_REPEAT_ROWS,
    DEFAULT_MAX_SIMAN_CHARS,
    DEFAULT_MAX_VBA_LINES,
    MODEL_EXTENSIONS,
    REPORT_SCHEMA_VERSION,
    _clamp,
    _utc_now,
)
from .audit import (
    _audit_model_data_for_session,
)
from .model import (
    _extract_model_ir_for_session,
)
from .artifacts import (
    _extract_siman_source_from_path,
    inspect_compound_file,
)
from .paths import (
    _resolve_allowed_file,
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
