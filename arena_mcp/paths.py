"""Model file roots and path validation."""

from __future__ import annotations

import os
from pathlib import Path


def _default_roots() -> list[Path]:
    configured = os.environ.get("ARENA_MODEL_ROOTS", "").strip()
    if configured:
        candidates = [Path(item) for item in configured.split(os.pathsep) if item]
    else:
        home = Path.home()
        candidates = [
            home / "OneDrive" / "Documents",
            home / "OneDrive" / "Desktop",
            home / "Documents",
            home / "Desktop",
            Path(r"C:\Users\Public\Documents\Rockwell Software\Arena"),
        ]

    roots: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve(strict=True)
        except OSError:
            continue
        key = os.path.normcase(str(resolved))
        if resolved.is_dir() and key not in seen:
            seen.add(key)
            roots.append(resolved)
    return roots

def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False

def _resolve_allowed_file(raw_path: str, extensions: set[str]) -> Path:
    if not raw_path or not raw_path.strip():
        raise ValueError("A file path is required.")
    try:
        path = Path(os.path.expandvars(raw_path)).expanduser().resolve(strict=True)
    except OSError as error:
        raise FileNotFoundError(f"File not found: {raw_path}") from error
    if not path.is_file():
        raise ValueError(f"Not a file: {path}")
    if path.suffix.lower() not in extensions:
        allowed = ", ".join(sorted(extensions))
        raise ValueError(f"Unsupported file type {path.suffix!r}; expected {allowed}.")

    roots = _default_roots()
    if os.environ.get("ARENA_ALLOW_ANY_PATH") != "1" and not any(
        _is_within(path, root) for root in roots
    ):
        allowed_roots = ", ".join(str(root) for root in roots) or "<none>"
        raise PermissionError(
            f"Path is outside ARENA_MODEL_ROOTS: {path}. Allowed roots: {allowed_roots}"
        )
    return path
