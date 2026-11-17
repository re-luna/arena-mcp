import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import arena_api
from arena_mcp import results


class ResultsTests(unittest.TestCase):
    def test_results_connections_are_closed_after_success(self):
        for reader, args in (
            (results.inspect_results_file, ()),
            (results.read_results_file, ("project", 10, lambda value: value)),
        ):
            with self.subTest(reader=reader.__name__):
                with closing(sqlite3.connect(":memory:")) as connection:
                    connection.row_factory = sqlite3.Row
                    connection.execute("CREATE VIEW ProjectQuery AS SELECT 1 AS value")
                    with mock.patch.object(
                        results, "_open_database", return_value=connection
                    ):
                        reader(Path(__file__), *args)

                    with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed"):
                        connection.execute("SELECT 1")

    def test_results_connections_are_closed_after_query_failure(self):
        for reader, args in (
            (results.inspect_results_file, ()),
            (results.read_results_file, ("project", 10, lambda value: value)),
        ):
            with self.subTest(reader=reader.__name__):
                connection = mock.MagicMock(spec=sqlite3.Connection)
                connection.__enter__.return_value = connection
                connection.execute.side_effect = sqlite3.OperationalError("query failed")
                with mock.patch.object(
                    results, "_open_database", return_value=connection
                ):
                    with self.assertRaisesRegex(sqlite3.OperationalError, "query failed"):
                        reader(Path(__file__), *args)

                connection.close.assert_called_once_with()

    def test_results_reader_uses_read_only_database_access(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "results.sqlite"
            with closing(sqlite3.connect(database_path)) as connection:
                with connection:
                    connection.execute("CREATE TABLE stats (name TEXT, value REAL)")
                    connection.execute(
                        "INSERT INTO stats VALUES (?, ?)", ("sentinel", 1e20)
                    )
                    connection.execute(
                        "CREATE VIEW ProjectQuery AS SELECT * FROM stats"
                    )

            with mock.patch.object(
                arena_api, "_resolve_allowed_file", return_value=database_path
            ):
                inventory = arena_api.inspect_results_database(str(database_path))
                result = arena_api.read_results(str(database_path))

            self.assertIn("ProjectQuery", inventory["views"])
            self.assertEqual(result["rows"], [{"name": "sentinel", "value": 1e20}])
            self.assertEqual(len(result["warnings"]), 1)

            with closing(sqlite3.connect(database_path)) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM stats").fetchone()[0],
                    1,
                )

    def test_database_connection_rejects_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "results.sqlite"
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute("CREATE TABLE stats (value REAL)")
                connection.commit()

            with closing(results._open_database(database_path)) as connection:
                with self.assertRaisesRegex(sqlite3.OperationalError, "readonly"):
                    connection.execute("INSERT INTO stats VALUES (1)")
