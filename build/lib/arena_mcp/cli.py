"""Command-line interface for the Arena extractor."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import arena_api as extractor

from .server import mcp


def _write_json(payload: Any, output_path: str | None = None) -> None:
    if output_path is None:
        stream = sys.stdout
        close_stream = False
    else:
        stream = Path(output_path).open("x", encoding="utf-8")
        close_stream = True
    try:
        json.dump(payload, stream, indent=2, ensure_ascii=True)
        stream.write("\n")
    finally:
        if close_stream:
            stream.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=extractor.__doc__)
    parser.add_argument(
        "command",
        nargs="?",
        choices=("doctor",),
        help="Run local environment diagnostics.",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "sse", "streamable-http"),
        default="stdio",
        help="MCP transport (default: stdio).",
    )
    parser.add_argument(
        "--live-check",
        action="store_true",
        help="Connect to Arena while running the doctor command.",
    )
    parser.add_argument("--status", action="store_true", help="Print local status JSON.")
    parser.add_argument("--list-models", action="store_true", help="Print model list JSON.")
    parser.add_argument("--inspect", metavar="MODEL", help="Inspect one .doe model.")
    parser.add_argument("--extract", metavar="MODEL", help="Extract one .doe model.")
    parser.add_argument(
        "--report",
        metavar="MODEL",
        help="Audit and extract one model in a combined report.",
    )
    parser.add_argument("--audit", metavar="MODEL", help="Audit one .doe model.")
    parser.add_argument(
        "--include-vba-source",
        action="store_true",
        help="Include VBA source in --audit output (subject to the line limit).",
    )
    parser.add_argument(
        "--include-siman-source",
        action="store_true",
        help="Generate SIMAN source from a temporary copy in --audit output.",
    )
    parser.add_argument(
        "--include-binary-payloads",
        action="store_true",
        help="Include base64 OLE stream payloads in --audit output.",
    )
    parser.add_argument(
        "--max-modules",
        type=int,
        default=extractor.DEFAULT_MAX_MODULES,
        help="Maximum modules to extract (used by --extract, --report, and --audit).",
    )
    parser.add_argument(
        "--max-repeat-rows",
        type=int,
        default=extractor.DEFAULT_MAX_REPEAT_ROWS,
        help="Maximum rows per repeating operand group.",
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        help="Write JSON to a new file instead of stdout; existing files are not overwritten.",
    )
    args = parser.parse_args()

    if args.command == "doctor":
        report = extractor.diagnose_arena(live_check=args.live_check)
        _write_json(report, args.output)
        return 0 if report["ok"] else 1
    if args.status:
        _write_json(extractor.get_arena_status(live_check=False), args.output)
    elif args.list_models:
        _write_json(extractor.discover_models(), args.output)
    elif args.inspect:
        _write_json(extractor.inspect_model(args.inspect), args.output)
    elif args.report:
        _write_json(
            extractor.extract_model_report(
                args.report,
                include_vba_source=args.include_vba_source,
                include_siman_source=args.include_siman_source,
                include_binary_payloads=args.include_binary_payloads,
                max_modules=args.max_modules,
                max_repeat_rows=args.max_repeat_rows,
            ),
            args.output,
        )
    elif args.extract:
        _write_json(
            extractor.extract_model_ir(
                args.extract, args.max_modules, args.max_repeat_rows
            ),
            args.output,
        )
    elif args.audit:
        _write_json(
            extractor.audit_model_data(
                args.audit,
                include_vba_source=args.include_vba_source,
                include_siman_source=args.include_siman_source,
                include_binary_payloads=args.include_binary_payloads,
                max_modules=args.max_modules,
                max_repeat_rows=args.max_repeat_rows,
            ),
            args.output,
        )
    else:
        if args.output:
            parser.error("--output requires a JSON-producing command.")
        mcp.run(transport=args.transport)
    return 0
