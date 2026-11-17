import tempfile
import unittest
from pathlib import Path
from unittest import mock

from arena_mcp import artifacts


class FakeSIMANModel:
    def __init__(self, path, suffixes):
        self.path = path
        self.suffixes = suffixes

    def WriteSIMAN(self):
        for suffix in self.suffixes:
            self.path.with_suffix(suffix).write_text("source", encoding="utf-8")


class FakeSIMANSession:
    def __init__(self, model_path, suffixes):
        self.model = FakeSIMANModel(model_path, suffixes)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None


class SimanTests(unittest.TestCase):
    def test_siman_is_not_complete_when_required_output_is_missing(self):
        self._assert_siman_output(suffixes=(".mod",), generated=False)

    def test_siman_is_complete_when_both_required_outputs_exist(self):
        self._assert_siman_output(suffixes=(".mod", ".exp"), generated=True)

    def _assert_siman_output(self, suffixes, generated):
        with tempfile.TemporaryDirectory() as directory:
            model_path = Path(directory) / "model.doe"
            model_path.write_bytes(b"original")
            session_factory = lambda path: FakeSIMANSession(path, suffixes)
            fake_pythoncom = mock.Mock()
            with (
                mock.patch.object(
                    artifacts,
                    "_require_com_dependencies",
                    return_value=(fake_pythoncom, None, None, None),
                ),
                mock.patch.object(
                    artifacts, "ArenaSession", side_effect=session_factory
                ),
                mock.patch.object(artifacts, "DEFAULT_COM_TIMEOUT_SECONDS", 0.001),
            ):
                result = artifacts._extract_siman_source_from_path(model_path)

        self.assertEqual(result["generated"], generated)
        self.assertEqual(result["source_complete"], generated)
        self.assertEqual(
            result["missing_required_suffixes"],
            [] if generated else [".exp"],
        )
        self.assertTrue(result["original_untouched"])
