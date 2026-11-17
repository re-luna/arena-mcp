"""Arena status checks and model discovery."""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
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
    SERVER_NAME,
    _clamp,
    _json_value,
)
from .paths import (
    _default_roots,
    _is_within,
)


def get_arena_status(live_check: bool = False) -> dict[str, Any]:
    status = {
        "server": SERVER_NAME,
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "python_bits": 64 if sys.maxsize > 2**32 else 32,
        "registration": _arena_registration(),
        "allowed_roots": [str(root) for root in _default_roots()],
        "read_only": True,
        "live_check": None,
    }
    if live_check:
        with ArenaSession() as session:
            status["live_check"] = {
                "connected": True,
                "version": _json_value(session.application.Version),
                "license_type": _json_value(session.application.LicenseType),
                "has_optquest_license": bool(session.application.HasOptQuestLicense),
            }
    return status

def diagnose_arena(live_check: bool = False) -> dict[str, Any]:
    status = get_arena_status(live_check=False)
    registration = status["registration"]
    roots = status["allowed_roots"]
    checks = [
        {
            "name": "platform",
            "ok": sys.platform == "win32",
            "message": (
                "Running on Windows."
                if sys.platform == "win32"
                else "Arena COM requires Windows."
            ),
        },
        {
            "name": "arena_registration",
            "ok": bool(registration["registered"]),
            "message": (
                "Arena is registered."
                if registration["registered"]
                else registration.get("error")
                or "Arena is not registered as Arena.Application."
            ),
        },
        {
            "name": "model_roots",
            "ok": bool(roots),
            "message": (
                f"{len(roots)} allowed model root(s) are available."
                if roots
                else "No configured model folders exist. Set ARENA_MODEL_ROOTS."
            ),
        },
    ]
    if live_check:
        try:
            live_status = get_arena_status(live_check=True)["live_check"]
        except Exception as error:
            checks.append(
                {
                    "name": "live_connection",
                    "ok": False,
                    "message": _error_text(error),
                }
            )
        else:
            checks.append(
                {
                    "name": "live_connection",
                    "ok": True,
                    "message": f"Connected to Arena {live_status['version']}.",
                }
            )

    failed_checks = [check for check in checks if not check["ok"]]
    return {
        "ok": not failed_checks,
        "checks": checks,
        "recommended_actions": [
            check["message"] for check in failed_checks
        ],
    }

def discover_models(
    root: str | None = None,
    include_backups: bool = False,
    limit: int = 200,
) -> dict[str, Any]:
    limit = _clamp(limit, 1, 2_000, "limit")
    allowed_roots = _default_roots()
    if root:
        candidate = Path(os.path.expandvars(root)).expanduser().resolve(strict=True)
        if not candidate.is_dir():
            raise ValueError(f"Not a directory: {candidate}")
        if os.environ.get("ARENA_ALLOW_ANY_PATH") != "1" and not any(
            _is_within(candidate, allowed) for allowed in allowed_roots
        ):
            raise PermissionError(f"Directory is outside ARENA_MODEL_ROOTS: {candidate}")
        search_roots = [candidate]
    else:
        search_roots = allowed_roots

    found: list[dict[str, Any]] = []
    for search_root in search_roots:
        try:
            paths = search_root.rglob("*.doe")
            for path in paths:
                is_backup = path.name.lower().endswith(".backup.doe")
                if is_backup and not include_backups:
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                found.append(
                    {
                        "path": str(path.resolve()),
                        "name": path.name,
                        "backup": is_backup,
                        "size_bytes": stat.st_size,
                        "modified_at": datetime.fromtimestamp(
                            stat.st_mtime, timezone.utc
                        ).isoformat(),
                    }
                )
                if len(found) >= limit:
                    break
        except OSError:
            continue
        if len(found) >= limit:
            break

    found.sort(key=lambda item: item["modified_at"], reverse=True)
    return {
        "roots": [str(item) for item in search_roots],
        "models": found[:limit],
        "count": min(len(found), limit),
        "truncated": len(found) >= limit,
    }
