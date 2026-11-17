import json
import unittest
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator

from arena_mcp import reports


class ReportTests(unittest.TestCase):
    def test_combined_report_reuses_one_arena_session(self):
        session = object()
        ir = {
            "modules": [{"id": "module-0001"}],
            "warnings": ["Module extraction was truncated."],
        }
        audit = {
            "coverage_report": {
                "translation_readiness": "review_required",
                "surfaces": [
                    {
                        "key": "module_logic",
                        "evidence": {"model_modules": 3},
                    }
                ],
            }
        }
        session_context = mock.MagicMock()
        session_context.__enter__.return_value = session

        with (
            mock.patch.object(
                reports, "_resolve_allowed_file", return_value=Path("model.doe")
            ),
            mock.patch.object(reports, "inspect_compound_file", return_value={}),
            mock.patch.object(
                reports, "ArenaSession", return_value=session_context
            ) as arena_session,
            mock.patch.object(
                reports, "_extract_model_ir_for_session", return_value=ir
            ) as extract_ir,
            mock.patch.object(
                reports, "_audit_model_data_for_session", return_value=audit
            ) as audit_model,
        ):
            report = reports.extract_model_report("model.doe")

        self.assertEqual(arena_session.call_count, 1)
        self.assertIs(extract_ir.call_args.args[0], session)
        self.assertIs(audit_model.call_args.args[0], session)
        self.assertEqual(report["completeness"]["total_modules"], 3)
        self.assertTrue(report["completeness"]["modules_truncated"])
        self.assertEqual(report["completeness"]["warnings"], ir["warnings"])

    def test_combined_report_example_validates_against_schema(self):
        package = Path(__file__).resolve().parent.parent / "arena_mcp"
        schema = json.loads(
            (package / "schemas" / "arena-report.schema.json").read_text(
                encoding="utf-8"
            )
        )
        example = json.loads(
            (package / "examples" / "report.example.json").read_text(
                encoding="utf-8"
            )
        )

        Draft202012Validator.check_schema(schema)
        Draft202012Validator(
            schema, format_checker=Draft202012Validator.FORMAT_CHECKER
        ).validate(example)
