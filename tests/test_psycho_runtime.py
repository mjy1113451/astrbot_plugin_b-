"""Real psycho module with a temporary profile, fake AstrBot and synthetic errors."""
import asyncio
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
from copy import deepcopy
import importlib
from io import StringIO
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_psycho_runtime_fixture"
CANARY = "UNLABELLED_PRIVATE_CONTENT_41"


class UnsafeError(Exception):
    def __str__(self):
        raise AssertionError("exception text must not be rendered")


class PsychoRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        package = ModuleType(PACKAGE)
        package.__path__ = [str(ROOT)]
        package.__package__ = PACKAGE
        colorama = ModuleType("colorama")
        colorama.Fore = SimpleNamespace(**dict.fromkeys((
            "WHITE", "GREEN", "YELLOW", "RED", "CYAN", "MAGENTA", "BLUE",
            "LIGHTBLUE_EX", "LIGHTBLACK_EX", "LIGHTMAGENTA_EX", "LIGHTCYAN_EX",
            "LIGHTYELLOW_EX", "LIGHTGREEN_EX",
        ), ""))
        colorama.Style = SimpleNamespace(RESET_ALL="")
        astrbot = ModuleType("astrbot")
        astrbot.__path__ = []
        api = ModuleType("astrbot.api")
        self.logger = Mock(spec=["debug", "info", "warning", "error", "critical"])
        api.logger = self.logger
        astrbot.api = api
        self.stack.enter_context(patch.dict(sys.modules, {
            PACKAGE: package, "colorama": colorama, "astrbot": astrbot, "astrbot.api": api,
        }))
        self.stack.enter_context(patch.object(sys, "dont_write_bytecode", True))
        runtime = importlib.import_module(PACKAGE + "._runtime")
        runtime.configure_plugin(self.temp / "profile")
        self.runtime = runtime
        self.psycho = importlib.import_module(PACKAGE + ".persona.psycho")

    @contextmanager
    def host_output(self):
        output, error = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            yield
        self.assertEqual(output.getvalue() + error.getvalue(), "")

    def assert_safe_error(self, exception_type):
        self.logger.error.assert_called_once()
        message = self.logger.error.call_args.args[0]
        self.assertIn(exception_type, message)
        self.assertRegex(message, r"psycho\.py:\d+ in ")
        self.assertNotIn(CANARY, message)
        self.assertNotIn(str(self.temp), message)
        for method in ("debug", "info", "warning", "critical"):
            getattr(self.logger, method).assert_not_called()

    def make_profile(self, caller=None):
        profile = self.psycho.PsychoProfile(ai_caller=caller)
        profile.profile = deepcopy(self.psycho.DEFAULT_PROFILE)
        profile.tracker = SimpleNamespace(
            get_recent_actions=Mock(return_value=[{}] * 60),
            get_stats=Mock(return_value={"views": 20, "total_actions": 60}),
        )
        profile._build_l1_summary = Mock(return_value="synthetic interests")
        profile._build_actions_summary = Mock(return_value="synthetic actions")
        return profile

    def test_import_is_read_only_and_does_not_import_business_config(self):
        self.assertEqual(list(self.temp.iterdir()), [])
        self.assertNotIn(PACKAGE + ".core.config", sys.modules)
        self.logger.error.assert_not_called()

    def test_missing_json_returns_same_default_without_logging(self):
        default = {"actions": []}
        with self.host_output():
            self.assertIs(self.psycho._load_json(self.temp / "missing.json", default), default)
        self.logger.error.assert_not_called()

    def test_invalid_json_uses_host_logger_without_path_or_document(self):
        path = self.temp / (CANARY + ".json")
        path.write_text("{invalid " + CANARY, encoding="utf-8")
        default = {"actions": []}
        with self.host_output():
            self.assertIs(self.psycho._load_json(path, default), default)
        self.assert_safe_error("JSONDecodeError")

    def test_load_failure_never_renders_exception_text(self):
        path = self.temp / "profile.json"
        path.write_text("{}", encoding="utf-8")
        with patch.object(self.psycho, "open", create=True, side_effect=UnsafeError(CANARY)), \
                self.host_output():
            self.assertEqual(self.psycho._load_json(path), {})
        self.assert_safe_error("UnsafeError")

    def test_save_failure_never_renders_exception_text(self):
        path = str(self.temp / CANARY / "profile.json")
        with patch.object(self.psycho, "open", create=True, side_effect=UnsafeError(CANARY)), \
                self.host_output():
            self.assertFalse(self.psycho._save_json(path, {"value": CANARY}))
        self.assert_safe_error("UnsafeError")

    def test_json_success_keeps_return_values_and_round_trips(self):
        path = str(self.temp / "data" / "profile.json")
        data = {"actions": [{"type": "view", "title": "合成测试"}]}
        with self.host_output():
            self.assertTrue(self.psycho._save_json(path, data))
            self.assertEqual(self.psycho._load_json(path), data)
        self.assertFalse(Path(path + ".tmp").exists())
        self.logger.error.assert_not_called()

    async def test_deep_analysis_failure_uses_safe_host_error(self):
        caller = AsyncMock(side_effect=UnsafeError(CANARY))
        profile = self.make_profile(caller)
        with self.host_output():
            self.assertFalse(await profile.deep_analyze(force=True))
        caller.assert_awaited_once()
        self.assert_safe_error("UnsafeError")

    async def test_analysis_parsing_error_does_not_log_provider_body(self):
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=CANARY))])
        profile = self.make_profile(AsyncMock(return_value=response))
        profile._extract_json = Mock(return_value="{" + CANARY)
        with self.host_output():
            self.assertFalse(await profile.deep_analyze(force=True))
        self.assert_safe_error("JSONDecodeError")

    async def test_cancelled_deep_analysis_keeps_existing_false_result_without_logging(self):
        profile = self.make_profile(AsyncMock(side_effect=asyncio.CancelledError))
        with self.host_output():
            self.assertFalse(await profile.deep_analyze(force=True))
        self.logger.error.assert_not_called()

    async def test_recommendation_failure_keeps_fallback_and_uses_safe_host_error(self):
        caller = AsyncMock(side_effect=UnsafeError(CANARY))
        engine = self.psycho.RecommendationEngine(self.make_profile(), caller)
        with self.host_output():
            self.assertEqual(await engine.generate_search_queries("explore", count=2),
                             engine._fallback_queries("explore", 2))
        caller.assert_awaited_once()
        self.assert_safe_error("UnsafeError")

    async def test_cancelled_recommendation_still_propagates_without_logging(self):
        engine = self.psycho.RecommendationEngine(
            self.make_profile(), AsyncMock(side_effect=asyncio.CancelledError))
        with self.host_output(), self.assertRaises(asyncio.CancelledError):
            await engine.generate_search_queries()
        self.logger.error.assert_not_called()

    def test_standalone_failure_uses_safe_terminal_diagnostic_without_astrbot(self):
        self.runtime.plugin_mode = False
        path = self.temp / "profile.json"
        path.write_text("{" + CANARY, encoding="utf-8")
        output, error = StringIO(), StringIO()
        with patch.dict(sys.modules, {"astrbot": None, "astrbot.api": None}), \
                redirect_stdout(output), redirect_stderr(error):
            self.assertEqual(self.psycho._load_json(path), {})
        self.assertIn("JSONDecodeError", output.getvalue())
        self.assertNotIn(CANARY, output.getvalue())
        self.assertNotIn(str(self.temp), output.getvalue())
        self.assertEqual(error.getvalue(), "")
        self.logger.error.assert_not_called()


if __name__ == "__main__":
    unittest.main()
