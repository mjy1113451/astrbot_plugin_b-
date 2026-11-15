"""Issue #41 background branches: extracted real functions, no network/account I/O."""
import ast
import asyncio
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
from typing import Any
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_async_video_entry import extract


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_background_runtime_fixture"
CANARY = "RAW_PROVIDER_RESPONSE_CANARY_41"


class BackgroundRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.stack.enter_context(patch.dict(sys.modules))
        self.log, self.log_exception = Mock(), Mock()
        self.namespace = {"__package__": PACKAGE + ".brain", "asyncio": asyncio, "json": json,
                          "os": os, "Path": Path, "Any": Any,
                          "_runtime": SimpleNamespace(plugin_mode=True),
                          "log": self.log, "log_exception": self.log_exception}

    async def test_missing_credentials_never_launches_login_or_reads_stdin(self):
        login = AsyncMock(side_effect=AssertionError("plugin launched interactive login"))
        self.namespace.update(login_bilibili=login, COOKIE_FILE=str(self.temp / "missing-cookie"))
        fn = extract("brain/_brain_session.py", "initialize_login", self.namespace)
        brain = SimpleNamespace(bili=SimpleNamespace(_load_credential=Mock(return_value=None)))
        with patch("builtins.print", side_effect=AssertionError("plugin printed login menu")), \
                patch("builtins.input", side_effect=AssertionError("host read stdin")):
            self.assertFalse(await fn(brain))
        login.assert_not_called()
        self.log.assert_called()

    async def test_main_startup_never_prints_category_tree(self):
        package = ModuleType(PACKAGE)
        package.__path__ = []
        services = ModuleType(PACKAGE + ".services")
        services.__path__ = []
        interests = ModuleType(PACKAGE + ".services.interest_engine")
        interests.get_engine = lambda: SimpleNamespace(get_keywords=lambda: [])
        sys.modules.update({PACKAGE: package, services.__name__: services, interests.__name__: interests})
        self.namespace.update(_acquire_bot_lock=lambda: True, KNOWLEDGE_BASE_DIR=str(self.temp / "kb"))
        fn = extract("brain/_brain_loop.py", "run", self.namespace)
        classifier = SimpleNamespace(show_category_structure=Mock(side_effect=AssertionError("tree printed")),
                                     cleanup_empty_folders=lambda: 0)
        brain = SimpleNamespace(update_runtime_clock=Mock(), previous_seen_at="",
                                interest_mgr=SimpleNamespace(get_interests=lambda: []),
                                persona_mgr=SimpleNamespace(get_active_persona=lambda: "test-persona"),
                                mood_mgr=SimpleNamespace(get_current=lambda: "test-mood"),
                                classifier=classifier, initialize_login=AsyncMock(return_value=False))
        with patch("builtins.print", side_effect=AssertionError("plugin printed startup")):
            await fn(brain)
        classifier.show_category_structure.assert_not_called()
        brain.initialize_login.assert_awaited_once()

    async def test_learning_failure_uses_exception_helper_not_raw_message(self):
        self.namespace.update(AI_SUBTITLE_VERIFY_ENABLED=False, LEARN_MIN_SCORE=6, config={})
        fn = extract("brain/_brain_learn.py", "learn_from_video", self.namespace)
        classifier = SimpleNamespace(classify_content=AsyncMock(return_value="safe"),
                                     get_or_create_folder=Mock(side_effect=RuntimeError(CANARY)))
        brain = SimpleNamespace(classifier=classifier)
        with patch("builtins.print", side_effect=AssertionError("plugin printed failure")):
            self.assertFalse(await fn(brain, "BV1Q541167Qg", "title", "up", "url", "subtitle " * 20, "topic"))
        self.log_exception.assert_called_once()
        self.assertNotIn(CANARY, str(self.log.call_args_list))

    async def test_deep_dive_ask_refuses_instead_of_confirming_in_plugin(self):
        self.namespace.update(__package__=PACKAGE + ".services", _live_config=lambda: {"api_key": "fake"},
                              call_ai=AsyncMock(return_value="keyword"), _search_bilibili=AsyncMock(return_value=[]))
        fn = extract("services/deep_dive.py", "run_deep_dive", self.namespace)
        with patch("builtins.input", side_effect=AssertionError("host read stdin")), \
                patch("builtins.print", side_effect=AssertionError("plugin printed menu")):
            result = await fn(topic="test-topic", mode="bilibili", video_count=2, shortage_policy="ask")
        self.assertFalse(result["success"])
        self.assertIn("无法交互确认", result["error"])

    async def test_learning_agent_never_logs_raw_user_tool_or_response_content(self):
        session = SimpleNamespace(session_type="deep_dive", messages=[], topic="", save=Mock())
        tools = AsyncMock(return_value=CANARY)
        self.namespace.update(LearningAgentSession=object, SYSTEM_PROMPT_DEEP_DIVE="system", TOOLS_DEEP_DIVE=[],
                              _live_config=lambda: {"api_key": "fake"}, _make_tool_handler=lambda *args: None,
                              call_ai_with_tools=tools)
        fn = extract("services/learning_agent.py", "run_learning_agent", self.namespace)
        with patch("builtins.print", side_effect=AssertionError("plugin printed tool output")):
            self.assertEqual(await fn(session, user_input=CANARY, verbose=True), CANARY)
        self.assertFalse(tools.call_args.kwargs["verbose"])
        self.assertNotIn(CANARY, str(self.log.call_args_list))
        session.save.assert_called_once()

    def test_standby_log_delegates_and_has_no_colorama_initialization(self):
        source = (ROOT / "brain" / "standby.py").read_text(encoding="utf-8")
        self.assertNotIn("colorama_init", source)
        self.assertNotIn("getLogger", source)
        fn = extract("brain/standby.py", "_log", self.namespace)
        with patch("builtins.print", side_effect=AssertionError("plugin printed standby")):
            fn(None, "safe", "ERR")
        self.log.assert_called_once_with("[SB] safe", "ERROR")

    def test_scoped_logs_never_interpolate_caught_exception_or_process_body(self):
        for relative in ("brain/_brain_loop.py", "brain/_brain_learn.py", "brain/_brain_video.py", "api/client.py"):
            tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
            for handler in (node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler) and node.name):
                for call in ast.walk(handler):
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "log":
                        names = {n.id for arg in call.args for n in ast.walk(arg) if isinstance(n, ast.Name)}
                        with self.subTest(file=relative, line=call.lineno):
                            self.assertNotIn(handler.name, names)


if __name__ == "__main__":
    unittest.main()
