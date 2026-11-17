import unittest
from unittest import mock

from arena_mcp import com, common, environment
from arena_mcp.errors import ArenaExtractorError


class EnvironmentTests(unittest.TestCase):
    def test_clamp_rejects_out_of_range_value(self):
        with self.assertRaises(ValueError):
            common._clamp(0, 1, 10, "limit")

    def test_arena_session_lock_timeout_is_bounded(self):
        com._ARENA_LOCK.acquire()
        try:
            with self.assertRaisesRegex(ArenaExtractorError, "Timed out waiting"):
                com.ArenaSession(lock_timeout=0.001).__enter__()
        finally:
            com._ARENA_LOCK.release()

    def test_doctor_reports_non_windows_platform(self):
        with (
            mock.patch.object(environment.sys, "platform", "linux"),
            mock.patch.object(
                environment,
                "get_arena_status",
                return_value={
                    "registration": {"registered": False},
                    "allowed_roots": [],
                },
            ),
        ):
            result = environment.diagnose_arena()

        self.assertFalse(result["ok"])
        platform_check = next(
            check for check in result["checks"] if check["name"] == "platform"
        )
        self.assertFalse(platform_check["ok"])
        self.assertIn("Windows", platform_check["message"])
