"""Read-only Arena compound-file and SIMAN artifact access."""

from __future__ import annotations

import base64
import hashlib
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any
import olefile
from arena_mcp.com import (
    ARENA_PROG_ID,
    DEFAULT_COM_TIMEOUT_SECONDS,
    ArenaSession,
    _ARENA_LOCK,
    arena_registration as _arena_registration,
    is_com_time as _is_com_time,
    require_com_dependencies as _require_com_dependencies,
)
from .common import (
    DEFAULT_MAX_BINARY_BYTES,
    DEFAULT_MAX_SIMAN_CHARS,
    MODEL_EXTENSIONS,
    _clamp,
)
from .paths import (
    _resolve_allowed_file,
)


def inspect_compound_file(
    model_path: str,
    include_payloads: bool = False,
    max_payload_bytes: int = DEFAULT_MAX_BINARY_BYTES,
) -> dict[str, Any]:
    path = _resolve_allowed_file(model_path, MODEL_EXTENSIONS)
    max_payload_bytes = _clamp(
        max_payload_bytes, 1, 100_000_000, "max_payload_bytes"
    )
    if not olefile.isOleFile(str(path)):
        return {"path": str(path), "ole_compound_file": False, "entries": []}

    entries = []
    remaining = max_payload_bytes
    with olefile.OleFileIO(str(path)) as compound:
        for entry_path in compound.listdir(streams=True, storages=True):
            entry_type = compound.get_type(entry_path)
            record: dict[str, Any] = {
                "path": "/".join(entry_path),
                "type": "stream" if entry_type == 2 else "storage",
            }
            if entry_type == 2:
                data = compound.openstream(entry_path).read()
                record["size_bytes"] = len(data)
                record["sha256"] = hashlib.sha256(data).hexdigest()
                if include_payloads:
                    captured = min(len(data), remaining)
                    record["captured_bytes"] = captured
                    record["payload_truncated"] = captured < len(data)
                    record["payload_base64"] = base64.b64encode(
                        data[:captured]
                    ).decode("ascii")
                    remaining -= captured
            entries.append(record)
    streams = [item for item in entries if item["type"] == "stream"]
    return {
        "path": str(path),
        "ole_compound_file": True,
        "entry_count": len(entries),
        "stream_count": len(streams),
        "storage_count": len(entries) - len(streams),
        "embedding_stream_count": sum(
            item["path"].lower().startswith("embedding ") for item in streams
        ),
        "vba_stream_count": sum(
            item["path"].lower().startswith("apc/") for item in streams
        ),
        "payloads_included": include_payloads,
        "payload_budget_bytes": max_payload_bytes,
        "captured_payload_bytes": max_payload_bytes - remaining,
        "entries": entries,
    }

def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def _extract_siman_source_from_path(
    path: Path, max_chars: int = DEFAULT_MAX_SIMAN_CHARS
) -> dict[str, Any]:
    max_chars = _clamp(max_chars, 1, 20_000_000, "max_chars")
    original_hash_before = _file_sha256(path)
    with tempfile.TemporaryDirectory(prefix="arena_mcp_siman_") as temp_directory:
        temp_path = Path(temp_directory) / path.name
        shutil.copy2(path, temp_path)
        pythoncom, _, _, _ = _require_com_dependencies()
        with ArenaSession(temp_path) as session:
            session.model.WriteSIMAN()
            deadline = time.monotonic() + DEFAULT_COM_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                generated_suffixes = {
                    item.suffix.lower()
                    for item in Path(temp_directory).iterdir()
                    if item.is_file() and item != temp_path
                }
                if {".mod", ".exp"}.issubset(generated_suffixes):
                    break
                pythoncom.PumpWaitingMessages()
                time.sleep(0.1)

        files = []
        remaining = max_chars
        for generated in sorted(Path(temp_directory).iterdir()):
            if generated == temp_path or not generated.is_file():
                continue
            data = generated.read_bytes()
            try:
                content = data.decode("utf-8-sig")
                encoding = "utf-8-sig"
            except UnicodeDecodeError:
                content = data.decode("cp1252", errors="replace")
                encoding = "cp1252"
            captured = min(len(content), remaining)
            files.append(
                {
                    "name": generated.name,
                    "suffix": generated.suffix.lower(),
                    "size_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "encoding": encoding,
                    "character_count": len(content),
                    "captured_characters": captured,
                    "truncated": captured < len(content),
                    "content": content[:captured],
                }
            )
            remaining -= captured
        original_hash_after = _file_sha256(path)
        generated_suffixes = {item["suffix"] for item in files}
        required_suffixes = {".mod", ".exp"}
        missing_required_suffixes = sorted(required_suffixes - generated_suffixes)
        generated = not missing_required_suffixes
        return {
            "generated": generated,
            "missing_required_suffixes": missing_required_suffixes,
            "temporary_copy": True,
            "original_untouched": original_hash_before == original_hash_after,
            "original_sha256_before": original_hash_before,
            "original_sha256_after": original_hash_after,
            "source_complete": generated
            and all(not item["truncated"] for item in files),
            "file_count": len(files),
            "files": files,
        }

def extract_siman_source(
    model_path: str, max_chars: int = DEFAULT_MAX_SIMAN_CHARS
) -> dict[str, Any]:
    path = _resolve_allowed_file(model_path, MODEL_EXTENSIONS)
    result = _extract_siman_source_from_path(path, max_chars)
    return {"source": str(path), **result}
