"""Translation-readiness coverage report construction."""

from __future__ import annotations

from collections import Counter
from typing import Any
from arena_mcp.results import RESULT_VIEWS
from .common import ASSISTED_MODULES, AUTOMATIC_MODULES


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
