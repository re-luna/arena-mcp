import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from arena_mcp import cli


class CliTests(unittest.TestCase):
    def test_doctor_cli_returns_nonzero_when_checks_fail(self):
        output = io.StringIO()
        with (
            mock.patch.object(cli.sys, "argv", ["arena-mcp", "doctor"]),
            mock.patch.object(
                cli.extractor,
                "diagnose_arena",
                return_value={"ok": False, "checks": [], "recommended_actions": []},
            ),
            contextlib.redirect_stdout(output),
        ):
            exit_code = cli.main()

        self.assertEqual(exit_code, 1)
        self.assertFalse(json.loads(output.getvalue())["ok"])

    def test_cli_writes_new_json_file_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "report.json"
            cli._write_json({"result": "first"}, str(output_path))
            self.assertEqual(json.loads(output_path.read_text())["result"], "first")

            with self.assertRaises(FileExistsError):
                cli._write_json({"result": "second"}, str(output_path))

            self.assertEqual(json.loads(output_path.read_text())["result"], "first")

    def test_extract_cli_forwards_shared_limits(self):
        output = io.StringIO()
        with (
            mock.patch.object(
                cli.sys,
                "argv",
                [
                    "arena-mcp",
                    "--extract",
                    "model.doe",
                    "--max-modules",
                    "12",
                    "--max-repeat-rows",
                    "7",
                ],
            ),
            mock.patch.object(
                cli.extractor, "extract_model_ir", return_value={"result": "ok"}
            ) as extract,
            contextlib.redirect_stdout(output),
        ):
            exit_code = cli.main()

        self.assertEqual(exit_code, 0)
        extract.assert_called_once_with("model.doe", 12, 7)
        self.assertEqual(json.loads(output.getvalue()), {"result": "ok"})
