import unittest

from arena_mcp import model, submodels
from tests.fakes import FakeModule


class ModelTests(unittest.TestCase):
    def test_repeat_group_extraction(self):
        result = model._extract_operands(FakeModule(), max_repeat_rows=10)

        self.assertEqual(result["scalars"][0]["value"], "Initialize")
        group = result["repeat_groups"][0]
        self.assertEqual(group["name"], "Assignments")
        self.assertEqual(len(group["rows"]), 2)
        self.assertEqual(group["rows"][1]["values"]["Type"], "Attribute")
        self.assertFalse(group["truncated"])

    def test_compatibility_classification(self):
        result = model._compatibility_from_modules(
            [
                {"definition": "Assign"},
                {"definition": "Assign"},
                {"definition": "Route"},
                {"definition": "VBA"},
            ]
        )

        self.assertEqual(result["automatic"], [{"definition": "Assign", "count": 2}])
        self.assertEqual(result["assisted"], [{"definition": "Route", "count": 1}])
        self.assertEqual(
            result["manual_or_unmapped"], [{"definition": "VBA", "count": 1}]
        )

    def test_scope_prefix_updates_module_and_connection_ids(self):
        modules = [{"id": "module-0001"}, {"id": "module-0002"}]
        connections = [
            {
                "id": "connection-0001",
                "source": {
                    "module_id": "module-0001",
                    "candidate_module_ids": ["module-0001"],
                },
                "destination": {
                    "module_id": "module-0002",
                    "candidate_module_ids": ["module-0002"],
                },
            }
        ]

        submodels._prefix_scope_ids(modules, connections, "root/submodel-0001")

        self.assertEqual(modules[0]["id"], "root/submodel-0001/module-0001")
        self.assertEqual(
            connections[0]["destination"]["module_id"],
            "root/submodel-0001/module-0002",
        )
