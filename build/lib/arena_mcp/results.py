"""Read-only access to Arena SQLite results databases."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Callable

from .errors import ArenaExtractorError


RESULT_VIEWS = {
    "project": "ProjectQuery",
    "output": "OutputStatsQuery",
    "continuous": "ContinuousTimeStatsQuery",
    "counter": "CounterStatsQuery",
    "discrete": "DiscreteTimeStatsQuery",
    "frequency": "FrequencyStatsQuery",
}


def _open_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def inspect_results_file(path: Path) -> dict[str, Any]:
    with _open_database(path) as connection:
        objects = connection.execute(
            "SELECT type, name FROM sqlite_master "
            "WHERE type IN ('table', 'view') ORDER BY type, name"
        ).fetchall()
        tables: list[dict[str, Any]] = []
        views: list[str] = []
        for row in objects:
            if row["type"] == "view":
                views.append(row["name"])
                continue
            count = connection.execute(
                f"SELECT COUNT(*) FROM {_quote_identifier(row['name'])}"
            ).fetchone()[0]
            tables.append({"name": row["name"], "rows": count})
        return {
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "tables": tables,
            "views": views,
            "supported_sections": sorted(RESULT_VIEWS),
        }


def read_results_file(
    path: Path,
    section: str,
    limit: int,
    normalize_value: Callable[[Any], Any],
) -> dict[str, Any]:
    section_key = section.strip().lower()
    if section_key not in RESULT_VIEWS:
        raise ValueError(
            f"Unknown section {section!r}; choose from {', '.join(sorted(RESULT_VIEWS))}."
        )
    view = RESULT_VIEWS[section_key]
    with _open_database(path) as connection:
        available = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='view' AND name=?", (view,)
        ).fetchone()
        if available is None:
            raise ArenaExtractorError(f"Arena results view is missing: {view}")
        rows = [
            {key: normalize_value(value) for key, value in dict(row).items()}
            for row in connection.execute(
                f"SELECT * FROM {_quote_identifier(view)} LIMIT ?", (limit,)
            ).fetchall()
        ]

    sentinel_cells = sum(
        1
        for row in rows
        for value in row.values()
        if isinstance(value, (int, float)) and abs(value) >= 1e19
    )
    warnings = []
    if sentinel_cells:
        warnings.append(
            f"Found {sentinel_cells} Arena sentinel result values with magnitude >= 1e19."
        )
    return {
        "path": str(path),
        "section": section_key,
        "view": view,
        "returned": len(rows),
        "limit": limit,
        "rows": rows,
        "warnings": warnings,
    }
