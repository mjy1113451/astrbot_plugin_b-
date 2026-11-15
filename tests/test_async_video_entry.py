"""Caller regressions execute extracted production functions with fake dependencies."""
import ast
import asyncio
from contextlib import ExitStack
from functools import wraps
import json
from pathlib import Path
import re
import sys
import tempfile
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]
BV = "BV1Q541167Qg"
PACKAGE = "_video_entry_fixture"


def extract(relative, name, namespace):
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
    node = next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), relative, "exec"), namespace)
    return namespace[name]


class AsyncVideoEntryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.stack.enter_context(patch.dict(sys.modules))
        self.log, self.log_exception = Mock(), Mock()
        self.namespace = {"__package__": PACKAGE + ".brain", "asyncio": asyncio,
                          "json": json, "re": re, "Path": Path, "time": time,
                          "_runtime": SimpleNamespace(plugin_mode=True),
                          "log": self.log, "log_exception": self.log_exception}

    async def test_original_short_url_is_awaited_once_without_bv_reexpansion(self):
        resolver = AsyncMock(return_value=BV)
        self.namespace["extract_video_id_async"] = resolver
        extract("brain/video_analysis.py", "_extract_bvid_async", self.namespace)
        resolve = extract("brain/video_analysis.py", "_resolve_b23_short", self.namespace)
        original = "https://b23.tv/original"
        self.assertEqual(await resolve(original), BV)
        resolver.assert_awaited_once_with(original, "bilibili")
        resolver.reset_mock()
        self.assertEqual(await resolve(BV), BV)
        resolver.assert_not_called()
        self.assertEqual(await resolve("original"), BV)
        resolver.assert_awaited_once_with(original, "bilibili")

    async def test_analyze_entry_awaits_adapter_before_constructing_brain(self):
        entered = asyncio.Event()

        async def resolve(value):
            entered.set()
            await asyncio.sleep(0)
            return None

        factory = Mock(side_effect=AssertionError("brain initialized before input validation"))
        self.namespace.update(_extract_bvid_async=resolve, AgentBrain=factory)
        analyze = extract("brain/video_analysis.py", "analyze_bilibili_video_input", self.namespace)
        self.assertEqual(await analyze("invalid-short"), (False, "未识别到有效 BV 号"))
        self.assertTrue(entered.is_set())
        factory.assert_not_called()

    def test_profile_cookie_loader_rejects_external_symlink_and_never_scans_siblings(self):
        root = self.temp / "profile"
        root.mkdir()
        current = root / "cookie.json"
        foreign = self.temp / "foreign.json"
        foreign.write_text('{"SESSDATA": "foreign-canary"}', encoding="utf-8")

        def managed(value):
            if Path(value).is_symlink():
                raise ValueError("link rejected")
            return Path(value)

        self.namespace.update(COOKIE_FILE=str(current), managed_file_path=managed)
        load = extract("brain/video_analysis.py", "_load_profile_cookies", self.namespace)
        self.assertIsNone(load())
        current.write_text("{}", encoding="utf-8")
        self.assertIsNone(load())
        current.unlink()
        try:
            current.symlink_to(foreign)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Symlinks unavailable: {exc}")
        self.assertIsNone(load())
        self.assertNotIn("foreign-canary", str(self.log.call_args_list))
        self.log_exception.assert_called_once()

    def test_cli_terminal_initialization_does_not_touch_host(self):
        output, error, colorama = Mock(), Mock(), Mock()
        self.namespace.update(sys=SimpleNamespace(stdout=output, stderr=error), colorama=colorama)
        initialize = extract("cli/app.py", "_initialize_cli_terminal", self.namespace)
        initialize()
        output.reconfigure.assert_not_called()
        error.reconfigure.assert_not_called()
        colorama.init.assert_not_called()

    async def test_cli_only_guard_does_not_authorize_sync_or_async_operation(self):
        self.namespace["wraps"] = wraps
        guard = extract("cli/app.py", "_cli_only", self.namespace)
        operation = Mock()
        operation.__name__ = "interactive_menu"
        with self.assertRaisesRegex(RuntimeError, "交互"):
            guard(operation)()
        operation.assert_not_called()

        async def async_menu():
            raise AssertionError("interactive async body executed")

        with self.assertRaisesRegex(RuntimeError, "交互"):
            await guard(async_menu)()

    def test_module_level_initializers_are_not_cli_only(self):
        tree = ast.parse((ROOT / "cli/app.py").read_text(encoding="utf-8"))
        guarded = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and any(isinstance(d, ast.Name) and d.id == "_cli_only" for d in node.decorator_list)}
        calls = {node.value.func.id for node in tree.body if isinstance(node, ast.Expr)
                 and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)}
        self.assertFalse(guarded & calls, f"Import-time CLI-only calls: {guarded & calls}")
        self.assertIn("configure_openai_client", calls)
        self.assertNotIn("_initialize_cli_terminal", calls)
        self.assertNotIn("initialize_cli", calls)
        menu = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "show_main_menu")
        self.assertIsInstance(menu.body[1], ast.Expr)
        self.assertIsInstance(menu.body[1].value, ast.Call)
        self.assertEqual(menu.body[1].value.func.id, "initialize_cli")

    def test_cli_initialization_updates_existing_reference_after_shared_success(self):
        reference = {"api": {"model_brain": "before"}}
        updated = {"api": {"model_brain": "after"}}
        shared = SimpleNamespace(initialize_config=Mock(return_value=updated))
        reload_config, terminal = Mock(), Mock()
        self.namespace.update(config=reference, _run_with_shared_config_path=lambda operation: operation(shared),
                              _reload_all_globals=reload_config, _initialize_cli_terminal=terminal)
        initialize = extract("cli/app.py", "initialize_cli", self.namespace)
        initialize()
        self.assertIs(self.namespace["config"], reference)
        self.assertEqual(reference, updated)
        reload_config.assert_called_once_with(reference)
        terminal.assert_called_once_with()
        shared.initialize_config.side_effect = OSError("controlled initialization failure")
        reload_config.reset_mock()
        terminal.reset_mock()
        with self.assertRaises(OSError):
            initialize()
        self.assertEqual(reference, updated)
        reload_config.assert_not_called()
        terminal.assert_not_called()

    async def test_plugin_up_entry_requires_explicit_parameters_without_input(self):
        fn = extract("brain/video_analysis.py", "up_homepage_learn", self.namespace)
        with patch("builtins.input", side_effect=AssertionError("host read stdin")):
            with self.assertRaises(RuntimeError):
                await fn()
            with self.assertRaises(ValueError):
                await fn(query="1")

    async def test_plugin_html_entry_has_no_input_animation_preview_or_stdout(self):
        for name in (PACKAGE, PACKAGE + ".brain", PACKAGE + ".xingye_bot"):
            module = ModuleType(name)
            module.__path__ = []
            sys.modules[name] = module
        video = ModuleType(PACKAGE + ".brain.video_analysis")
        video._extract_bvid_async = AsyncMock(return_value=BV)
        settings = ModuleType(PACKAGE + ".xingye_bot.settings")
        settings.load_settings = lambda: SimpleNamespace()
        state = ModuleType(PACKAGE + ".xingye_bot.state")
        state.BotState = lambda: SimpleNamespace()
        llm = ModuleType(PACKAGE + ".xingye_bot.llm")
        chat = AsyncMock(return_value="<html>safe export</html>")
        llm.ModelClient = lambda *args: SimpleNamespace(chat=chat)
        for module in (video, settings, state, llm):
            sys.modules[module.__name__] = module
        meta = {"code": 0, "data": {"title": "safe-title", "owner": {"name": "fake-up", "mid": 1}, "stat": {}}}
        bili = SimpleNamespace(_load_credential=Mock(), _wbi_get=AsyncMock(return_value=SimpleNamespace(json=lambda: meta)))
        brain = SimpleNamespace(bili=bili, understand_video_for_decision=AsyncMock(return_value=(True, "safe subtitle " * 5)))

        def resolve(value, default=None):
            path = Path(default if value is None or value == "" else value)
            return path if path.is_absolute() else self.temp / path

        self.namespace.update(__package__=PACKAGE + ".cli", AgentBrain=lambda: brain,
                              _load_profile_cookies=lambda: None, config={"learning_workflow": {"read_comments": False}},
                              resolve_output_path=resolve, _SHARED_HTML_EXPORTS_DIR=self.temp / "html_exports",
                              Fore=SimpleNamespace(), Style=SimpleNamespace())
        fn = extract("cli/app.py", "video_to_html_bg", self.namespace)
        with patch("builtins.print", side_effect=AssertionError("plugin printed")), \
                patch("builtins.input", side_effect=AssertionError("host read stdin")):
            await fn(query="https://b23.tv/original")
        video._extract_bvid_async.assert_awaited_once_with("https://b23.tv/original")
        chat.assert_awaited_once()
        files = list((self.temp / "html_exports").glob("*.html"))
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].read_text(encoding="utf-8"), "<html>safe export</html>")

    def test_all_known_async_video_callers_use_async_extraction(self):
        for relative, names in (("brain/video_analysis.py", {"analyze_bilibili_video_input", "manual_video_analysis"}),
                                ("cli/app.py", {"_mindmap_from_video_input", "manual_visual_note_analysis", "video_to_html_bg"})):
            tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
            for fn in tree.body:
                if not isinstance(fn, ast.AsyncFunctionDef) or fn.name not in names:
                    continue
                with self.subTest(function=fn.name):
                    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
                    self.assertFalse(any(isinstance(n.func, ast.Name) and n.func.id in
                                         {"extract_video_id", "_extract_bvid", "resolve_bilibili_short_url"} for n in calls))
                    self.assertTrue(any(isinstance(n, ast.Await) and isinstance(n.value, ast.Call)
                                        and isinstance(n.value.func, ast.Name)
                                        and n.value.func.id in {"_extract_bvid_async", "extract_video_id_async"} for n in ast.walk(fn)))


if __name__ == "__main__":
    unittest.main()
