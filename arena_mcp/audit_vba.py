"""VBA and Arena module-template audits."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any
from arena_mcp.errors import error_text as _error_text
from .common import _json_value, _safe_property
from .model import _operand_metadata


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
