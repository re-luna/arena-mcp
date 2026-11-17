"""Expression and external-dependency audits."""

from __future__ import annotations

import json
from typing import Any
from .common import (
    EXPRESSION_FIELD_TERMS,
    EXPRESSION_KEYWORDS,
    EXPRESSION_TOKEN_PATTERN,
    EXTERNAL_REFERENCE_PATTERN,
    _json_value,
    _safe_property,
)


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
