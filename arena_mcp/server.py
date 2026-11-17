"""Read-only MCP adapter for Arena extraction functions."""

from __future__ import annotations

from typing import Any

import arena_api as extractor
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations


READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

mcp = FastMCP(
    extractor.SERVER_NAME,
    instructions=(
        "Read Rockwell Automation Arena models through Arena's COM object model. "
        "All tools are read-only and never save, run, or edit models. Use "
        "extract_arena_report for a combined coverage audit and neutral IR, or "
        "use audit_arena_model_data and extract_arena_model independently."
    ),
    json_response=True,
    log_level="INFO",
)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def arena_status(live_check: bool = False) -> dict[str, Any]:
    """Check Arena registration, model roots, and optionally the live COM connection."""
    return extractor.get_arena_status(live_check)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def list_arena_models(
    root: str | None = None,
    include_backups: bool = False,
    limit: int = 200,
) -> dict[str, Any]:
    """Discover .doe models under the configured read-only model roots."""
    return extractor.discover_models(root, include_backups, limit)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def inspect_arena_model(model_path: str) -> dict[str, Any]:
    """Return model metadata, run settings, module counts, and compatibility."""
    return extractor.inspect_model(model_path)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def list_arena_modules(
    model_path: str,
    offset: int = 0,
    limit: int = 100,
    include_operands: bool = True,
    max_repeat_rows: int = extractor.DEFAULT_MAX_REPEAT_ROWS,
) -> dict[str, Any]:
    """Return a page of Arena modules, including scalar and repeat-group operands."""
    return extractor.extract_modules(
        model_path, offset, limit, include_operands, max_repeat_rows
    )


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def list_arena_connections(model_path: str) -> dict[str, Any]:
    """Return directed connections and resolved source/destination modules."""
    return extractor.extract_connections(model_path)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def extract_arena_model(
    model_path: str,
    max_modules: int = extractor.DEFAULT_MAX_MODULES,
    max_repeat_rows: int = extractor.DEFAULT_MAX_REPEAT_ROWS,
) -> dict[str, Any]:
    """Extract a complete versioned neutral representation of an Arena model."""
    return extractor.extract_model_ir(model_path, max_modules, max_repeat_rows)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def extract_arena_report(
    model_path: str,
    include_vba_source: bool = False,
    include_siman_source: bool = False,
    include_binary_payloads: bool = False,
    max_modules: int = extractor.DEFAULT_MAX_MODULES,
    max_repeat_rows: int = extractor.DEFAULT_MAX_REPEAT_ROWS,
) -> dict[str, Any]:
    """Audit and extract one model in a single Arena session."""
    return extractor.extract_model_report(
        model_path=model_path,
        include_vba_source=include_vba_source,
        include_siman_source=include_siman_source,
        include_binary_payloads=include_binary_payloads,
        max_modules=max_modules,
        max_repeat_rows=max_repeat_rows,
    )


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def analyze_arena_model_compatibility(model_path: str) -> dict[str, Any]:
    """Classify module definitions for automatic, assisted, or manual translation."""
    return extractor.analyze_compatibility(model_path)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def audit_arena_model_data(
    model_path: str,
    include_vba_source: bool = False,
    include_siman_source: bool = False,
    include_binary_payloads: bool = False,
    max_modules: int = extractor.DEFAULT_MAX_MODULES,
    max_repeat_rows: int = extractor.DEFAULT_MAX_REPEAT_ROWS,
    max_audit_items: int = extractor.DEFAULT_MAX_AUDIT_ITEMS,
    max_vba_lines: int = extractor.DEFAULT_MAX_VBA_LINES,
    max_siman_chars: int = extractor.DEFAULT_MAX_SIMAN_CHARS,
    max_binary_bytes: int = extractor.DEFAULT_MAX_BINARY_BYTES,
) -> dict[str, Any]:
    """Inventory Arena data surfaces and report Python translation coverage gaps."""
    return extractor.audit_model_data(
        model_path=model_path,
        include_vba_source=include_vba_source,
        include_siman_source=include_siman_source,
        include_binary_payloads=include_binary_payloads,
        max_modules=max_modules,
        max_repeat_rows=max_repeat_rows,
        max_audit_items=max_audit_items,
        max_vba_lines=max_vba_lines,
        max_siman_chars=max_siman_chars,
        max_binary_bytes=max_binary_bytes,
    )


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def inspect_arena_project_bar(model_path: str) -> dict[str, Any]:
    """Return full attached Project Bar panel and operand-definition schemas."""
    return extractor.extract_project_bar_catalog(model_path)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def extract_arena_submodels(
    model_path: str,
    max_items: int = extractor.DEFAULT_MAX_AUDIT_ITEMS,
    max_modules: int = extractor.DEFAULT_MAX_MODULES,
    max_repeat_rows: int = extractor.DEFAULT_MAX_REPEAT_ROWS,
) -> dict[str, Any]:
    """Recursively extract submodel modules, operands, connections, and boundaries."""
    return extractor.extract_submodel_tree(
        model_path, max_items, max_modules, max_repeat_rows
    )


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def extract_arena_visual_model(
    model_path: str,
    max_items: int = extractor.DEFAULT_MAX_AUDIT_ITEMS,
    include_binary_payloads: bool = False,
    max_binary_bytes: int = extractor.DEFAULT_MAX_BINARY_BYTES,
) -> dict[str, Any]:
    """Extract shape geometry, animation records, pictures, and optional raw payloads."""
    return extractor.extract_visual_model(
        model_path, max_items, include_binary_payloads, max_binary_bytes
    )


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def extract_arena_material_handling(
    model_path: str, max_items: int = extractor.DEFAULT_MAX_AUDIT_ITEMS
) -> dict[str, Any]:
    """Extract material-handling collections and their readable properties."""
    return extractor.extract_material_handling(model_path, max_items)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def inspect_arena_compound_file(
    model_path: str,
    include_payloads: bool = False,
    max_payload_bytes: int = extractor.DEFAULT_MAX_BINARY_BYTES,
) -> dict[str, Any]:
    """Inventory every .doe OLE stream with hashes and optional base64 payloads."""
    return extractor.inspect_compound_file(
        model_path, include_payloads, max_payload_bytes
    )


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def extract_arena_siman_source(
    model_path: str, max_chars: int = extractor.DEFAULT_MAX_SIMAN_CHARS
) -> dict[str, Any]:
    """Generate SIMAN text from an isolated temporary copy of the Arena model."""
    return extractor.extract_siman_source(model_path, max_chars)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def inspect_arena_results(database_path: str) -> dict[str, Any]:
    """Inspect the schema and row counts of an Arena SQLite results database."""
    return extractor.inspect_results_database(database_path)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def read_arena_results(
    database_path: str,
    section: str = "project",
    limit: int = 100,
) -> dict[str, Any]:
    """Read a supported statistics section from an Arena SQLite database."""
    return extractor.read_results(database_path, section, limit)
