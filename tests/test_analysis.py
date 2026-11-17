import unittest

from arena_mcp import coverage, expressions, model
from tests.fakes import FakeModule


class ExpressionTests(unittest.TestCase):
    def test_expression_audit_finds_scalar_and_repeat_values(self):
        module = {
            "id": "module-0001",
            "definition": "Expression",
            "caption": "Rates",
            "operands": model._extract_operands(FakeModule(), max_repeat_rows=10),
        }

        result = expressions._audit_expressions([module], max_items=10)

        self.assertEqual(result["candidate_count"], 5)
        self.assertFalse(result["truncated"])
        self.assertEqual(result["candidates"][0]["module_id"], "module-0001")
        self.assertTrue(result["candidates"][0]["analysis"]["lexically_valid"])

    def test_expression_analysis_extracts_symbols_and_functions(self):
        result = expressions._analyze_expression(
            "MAX(Resource.Capacity, Variable_A[2]) + 1.5E-3"
        )

        self.assertTrue(result["lexically_valid"])
        self.assertEqual(result["functions"], ["MAX"])
        self.assertEqual(result["identifiers"], ["Resource.Capacity", "Variable_A"])

    def test_expression_analysis_reports_unbalanced_delimiter(self):
        result = expressions._analyze_expression("NORM(10, 2")

        self.assertFalse(result["lexically_valid"])
        self.assertIn("Unclosed '(' at offset 4.", result["errors"])

    def test_external_dependency_audit_detects_file_operand(self):
        module = {
            "id": "module-0001",
            "definition": "ReadWrite",
            "caption": "Load input",
            "operands": {
                "scalars": [
                    {
                        "name": "FileName",
                        "prompt": "File Name",
                        "value": r"C:\data\input.xlsx",
                    }
                ],
                "repeat_groups": [],
            },
        }
        arena_model = type(
            "Model",
            (),
            {
                "ExternalRef": "",
                "VisualizationFileName": "",
                "AutoPublishSimVariables": False,
            },
        )()

        result = expressions._audit_external_dependencies(
            arena_model, [module], {"projects": []}, max_items=10
        )

        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual(
            {item["kind"] for item in result["candidates"]},
            {"integration_module", "operand_reference"},
        )


class CoverageTests(unittest.TestCase):
    def test_coverage_report_blocks_uncaptured_vba(self):
        modules = [
            {
                "id": "module-0001",
                "definition": "VBA",
                "caption": "Hook",
                "operands": {"scalars": [], "repeat_groups": []},
            }
        ]

        result = coverage._build_coverage_report(
            modules=modules,
            module_total=1,
            collections={"Connections": {"count": 0}},
            expressions={"candidate_count": 0, "truncated": False},
            vba={
                "accessible": True,
                "project_count": 1,
                "line_count": 12,
                "captured_lines": 0,
                "source_complete": False,
            },
            submodels={"accessible": True, "count": 0},
            templates={"accessible": True, "installed_panels": []},
            dependencies={"candidate_count": 1, "truncated": False},
        )

        vba_surface = next(
            item for item in result["surfaces"] if item["key"] == "vba"
        )
        resource_surface = next(
            item
            for item in result["surfaces"]
            if item["key"] == "resources_schedules_sets"
        )
        self.assertEqual(vba_surface["status"], "metadata_only")
        self.assertEqual(resource_surface["status"], "not_present")
        self.assertEqual(result["translation_readiness"], "review_required")
        self.assertIn("VBA", result["manual_or_unmapped_definitions"])

    def test_complete_expression_and_siman_template_coverage(self):
        result = coverage._build_coverage_report(
            modules=[],
            module_total=0,
            collections={"Connections": {"count": 0}},
            expressions={
                "candidate_count": 2,
                "truncated": False,
                "lexical_error_count_in_returned": 0,
            },
            vba={"accessible": True, "line_count": 0},
            submodels={"accessible": True, "count": 0},
            templates={
                "accessible": True,
                "installed_panels": [],
                "unresolved_definitions": [],
            },
            dependencies={"candidate_count": 0, "truncated": False},
            siman_source={"requested": True, "source_complete": True},
        )
        statuses = {item["key"]: item["status"] for item in result["surfaces"]}

        self.assertEqual(statuses["expressions"], "extracted")
        self.assertEqual(statuses["templates"], "extracted")
