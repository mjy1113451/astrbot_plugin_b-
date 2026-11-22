"""Issue #41 host/CLI boundaries without importing business dependencies."""
import asyncio
from contextlib import ExitStack
import runpy
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from test_package_imports import ROOT, PACKAGE_A, PACKAGES, _package_environment


BUSINESS_COMMANDS = (
    ("start_bot", (), "brain.agent_brain"),
    ("analyze_video", ("BV1Q541167Qg",), "brain.video_analysis"),
    ("up_learn", ("1",), "brain.video_analysis"),
    ("video_to_html", ("BV1Q541167Qg",), "cli.app"),
)


class RuntimeEntryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.environment = self.stack.enter_context(_package_environment())
        self.main = self.environment.load_main(PACKAGE_A)

    async def asyncTearDown(self):
        tasks = {bot._brain_runtime.task for bot in self.environment.bots
                 if bot._brain_runtime.task is not None}
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.sleep(0)

    async def finish_brain(self, bot, runtime):
        task = bot._brain_runtime.task
        if task is not None:
            runtime.finished.set()
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.sleep(0)

    async def test_all_business_commands_initialize_then_apply_once_before_business_import(self):
        panel = {
            "use_astrbot_config": True, "apply_sections": ["ai_permissions"],
            "ai_permissions": {"enabled": True, "actions": {"coin": True}},
        }
        for package in PACKAGES:
            main = self.main if package == PACKAGE_A else self.environment.load_main(package)
            for name, args, business_import in BUSINESS_COMMANDS:
                with self.subTest(package=package, command=name):
                    runtime = self.environment.install_runtime(package)
                    bot = self.environment.construct_bot(main, panel)
                    event = SimpleNamespace(plain_result=lambda text: text)
                    order = Mock()
                    order.attach_mock(runtime.config.initialize_config, "initialize")
                    order.attach_mock(runtime.bridge.apply_astrbot_config, "apply")
                    imports = []

                    def ordered_import(name, globals=None, locals=None, fromlist=(), level=0):
                        if level == 1 and globals.get("__package__") == package:
                            if name == "core.astrbot_config_bridge":
                                runtime.config.initialize_config.assert_called_once_with()
                                runtime.bridge.apply_astrbot_config.assert_not_called()
                                imports.append(name)
                            elif name == business_import:
                                runtime.config.initialize_config.assert_called_once_with()
                                runtime.bridge.apply_astrbot_config.assert_called_once_with(panel)
                                imports.append(name)
                        return self.environment.guarded_import(name, globals, locals, fromlist, level)

                    with patch("builtins.__import__", new=ordered_import):
                        replies = [reply async for reply in getattr(bot, name)(event, *args)]
                    self.assertEqual(len(replies), 1)
                    self.assertIn("已在后台启动" if name == "start_bot" else "完成", replies[0])
                    self.assertEqual(order.mock_calls, [call.initialize(), call.apply(panel)])
                    self.assertIs(runtime.bridge.apply_astrbot_config.call_args.args[0], panel)
                    self.assertEqual(imports, ["core.astrbot_config_bridge", business_import])
                    if name == "start_bot":
                        runtime.brain.AgentBrain.assert_called_once_with()
                    elif name == "analyze_video":
                        runtime.video.analyze_bilibili_video_input.assert_awaited_once_with(*args)
                    elif name == "up_learn":
                        runtime.video.up_homepage_learn.assert_awaited_once_with(
                            query=args[0], limit=10, skip_without_subtitle=True, min_score=7.0,
                        )
                    else:
                        runtime.app.video_to_html_bg.assert_awaited_once_with(query=args[0])
                    await self.finish_brain(bot, runtime)
                    self.environment.assert_isolated(self)

    async def test_noninteractive_jobs_without_panel_initialize_without_loading_bridge(self):
        bot = self.environment.construct_bot(self.main)
        runtime = self.environment.install_runtime(PACKAGE_A, with_bridge=False)
        event = SimpleNamespace(plain_result=lambda text: text)
        for name, args, _ in BUSINESS_COMMANDS[1:]:
            with self.subTest(command=name):
                runtime.config.initialize_config.reset_mock()
                replies = [reply async for reply in getattr(bot, name)(event, *args)]
                self.assertIn("完成", replies[0])
                runtime.config.initialize_config.assert_called_once_with()
                self.assertNotIn(PACKAGE_A + ".core.astrbot_config_bridge", sys.modules)
        self.environment.assert_isolated(self)

    async def test_all_business_commands_abort_before_business_import_when_bridge_fails(self):
        panel = {"use_astrbot_config": True}
        bot = self.environment.construct_bot(self.main, panel)
        event = SimpleNamespace(plain_result=lambda text: text)
        for name, args, business_import in BUSINESS_COMMANDS:
            with self.subTest(command=name):
                runtime = self.environment.install_runtime(PACKAGE_A)
                runtime.bridge.apply_astrbot_config.side_effect = ValueError("PRIVATE_PANEL_CANARY")
                with patch("builtins.__import__", wraps=self.environment.guarded_import) as imports:
                    replies = [reply async for reply in getattr(bot, name)(event, *args)]
                self.assertEqual(len(replies), 1)
                self.assertIn("失败", replies[0])
                self.assertNotIn("PRIVATE_PANEL_CANARY", replies[0])
                runtime.config.initialize_config.assert_called_once_with()
                runtime.bridge.apply_astrbot_config.assert_called_once_with(panel)
                self.assertNotIn(business_import, [item.args[0] for item in imports.call_args_list])
                runtime.brain.AgentBrain.assert_not_called()
                runtime.video.analyze_bilibili_video_input.assert_not_awaited()
                runtime.video.up_homepage_learn.assert_not_awaited()
                runtime.app.video_to_html_bg.assert_not_awaited()
                self.assertIsNone(bot._brain_runtime.task)
                self.environment.assert_isolated(self)

    async def test_plugin_never_enters_terminal_menus_even_when_host_has_tty(self):
        bot = self.environment.construct_bot(self.main)
        event = SimpleNamespace(plain_result=lambda text: text)
        with patch.object(sys, "stdin", SimpleNamespace(isatty=lambda: True)):
            self.assertFalse(self.main._is_interactive())
            for name in ("login", "kb_revisit", "kb_organize", "kb_custom", "interest",
                         "comment", "export", "import_cfg", "reset"):
                with self.subTest(command=name):
                    replies = [reply async for reply in getattr(bot, name)(event)]
                    self.assertEqual(len(replies), 1)
                    self.assertIn("CLI" if name in ("kb_revisit", "kb_organize") else "终端", replies[0])
        self.environment.assert_isolated(self)

    async def test_empty_business_commands_never_initialize_or_import_business(self):
        bot = self.environment.construct_bot(self.main)
        event = SimpleNamespace(plain_result=lambda text: text)
        for name in ("analyze_video", "up_learn", "video_to_html"):
            with self.subTest(command=name):
                replies = [reply async for reply in getattr(bot, name)(event)]
                self.assertEqual(len(replies), 1)
                self.assertIn("用法", replies[0])
        self.environment.assert_isolated(self)

    async def test_all_business_commands_stop_when_profile_initialization_fails(self):
        bot = self.environment.construct_bot(self.main, {"use_astrbot_config": True})
        runtime = self.environment.install_runtime(PACKAGE_A)
        runtime.config.initialize_config.side_effect = ValueError("PRIVATE_CONFIG_CANARY")
        event = SimpleNamespace(plain_result=lambda text: text)
        for name, args, business_import in BUSINESS_COMMANDS:
            with self.subTest(command=name):
                with patch("builtins.__import__", wraps=self.environment.guarded_import) as imports:
                    replies = [reply async for reply in getattr(bot, name)(event, *args)]
                self.assertEqual(len(replies), 1)
                self.assertIn("失败", replies[0])
                self.assertNotIn("PRIVATE_CONFIG_CANARY", replies[0])
                imported = [item.args[0] for item in imports.call_args_list]
                self.assertNotIn("core.astrbot_config_bridge", imported)
                self.assertNotIn(business_import, imported)
        self.assertEqual(runtime.config.initialize_config.call_count, len(BUSINESS_COMMANDS))
        runtime.bridge.apply_astrbot_config.assert_not_called()
        runtime.brain.AgentBrain.assert_not_called()
        runtime.video.analyze_bilibili_video_input.assert_not_awaited()
        runtime.video.up_homepage_learn.assert_not_awaited()
        runtime.app.video_to_html_bg.assert_not_awaited()
        self.assertIsNone(bot._brain_runtime.task)
        self.environment.assert_isolated(self)

    def test_host_data_api_failure_does_not_fall_back_or_import_business(self):
        self.environment.star_tools.get_data_dir.side_effect = OSError("host root unavailable")
        with self.assertRaises(OSError):
            self.main.BilibiliLearningBot(None)
        self.assertFalse(self.main._runtime.plugin_mode)
        self.assertIsNone(self.main._runtime.data_root)
        self.environment.assert_isolated(self)

    def test_cli_entry_cannot_be_used_inside_plugin_runtime(self):
        self.environment.construct_bot(self.main)
        with self.assertRaisesRegex(RuntimeError, "Interactive CLI"):
            self.main.run_cli()
        self.environment.assert_isolated(self)

    async def test_handler_failure_does_not_expose_original_exception(self):
        self.environment.construct_bot(self.main)
        runtime = self.environment.install_runtime(PACKAGE_A)
        runtime.lock.bot_lock_status.side_effect = ValueError("PRIVATE_RESPONSE_CANARY")
        bot = self.environment.construct_bot(self.main)
        replies = [reply async for reply in bot.status(SimpleNamespace(plain_result=lambda text: text))]
        self.assertEqual(len(replies), 1)
        self.assertIn("当前实例未登记受管任务", replies[0])
        self.assertIn("锁状态未知", replies[0])
        self.assertNotIn("PRIVATE_RESPONSE_CANARY", replies[0])
        runtime.lock.bot_lock_status.assert_called_once_with(clean_stale=False)
        runtime.display.log_exception.assert_called_once()
        runtime.app._release_bot_lock.assert_not_called()
        self.environment.assert_isolated(self)

    def standalone(self):
        namespace = runpy.run_path(str(ROOT / "main.py"), run_name="standalone_fixture")
        prefix = namespace["__package__"]
        self.environment.installs[prefix] = ROOT
        self.environment.install_runtime(prefix)
        return namespace

    def test_standalone_keyboard_interrupt_exits_without_custom_logger(self):
        namespace = self.standalone()
        app = sys.modules[namespace["__package__"] + ".cli.app"]
        app.show_main_menu = Mock(side_effect=KeyboardInterrupt)
        entry = namespace["run_cli"]
        with patch.dict(entry.__globals__, {"_is_interactive": lambda: False}), patch("builtins.print"):
            entry()
        app.show_main_menu.assert_called_once_with()

    def test_standalone_failure_uses_safe_exception_helper(self):
        namespace = self.standalone()
        prefix = namespace["__package__"]
        app = sys.modules[prefix + ".cli.app"]
        app.show_main_menu = Mock(side_effect=RuntimeError("PRIVATE_RESPONSE_CANARY"))
        entry = namespace["run_cli"]
        with patch.dict(entry.__globals__, {"_is_interactive": lambda: False}), \
                patch("builtins.print", side_effect=AssertionError("raw error printed")):
            entry()
        sys.modules[prefix + ".utils.display"].log_exception.assert_called_once()

    def test_explicit_migration_cli_does_not_load_config_or_open_menu(self):
        namespace = self.standalone()
        prefix = namespace["__package__"]
        data = ModuleType(prefix + ".core.user_data")
        data.migrate_legacy_data = Mock(return_value=["copied"])
        app = sys.modules[prefix + ".cli.app"]
        app.show_main_menu = Mock()
        # This scenario begins before any business config is imported; the shared
        # startup fixture also supports brain tests that explicitly initialize it.
        sys.modules.pop(prefix + ".core.config", None)
        with patch.dict(sys.modules, {data.__name__: data}), \
                patch.object(sys, "argv", ["main.py", "--migrate-data", "explicit-source"]):
            namespace["run_cli"]()
        data.migrate_legacy_data.assert_called_once_with("explicit-source")
        app.show_main_menu.assert_not_called()
        self.assertFalse(prefix + ".core.config" in sys.modules)
        self.environment.star_tools.get_data_dir.assert_not_called()

    def test_migration_failure_exits_nonzero_without_opening_menu(self):
        namespace = self.standalone()
        prefix = namespace["__package__"]
        data = ModuleType(prefix + ".core.user_data")
        data.migrate_legacy_data = Mock(side_effect=ValueError("PRIVATE_RESPONSE_CANARY"))
        with patch.dict(sys.modules, {data.__name__: data}), \
                patch.object(sys, "argv", ["main.py", "--migrate-data", "explicit-source"]):
            with self.assertRaises(SystemExit) as caught:
                namespace["run_cli"]()
        self.assertEqual(caught.exception.code, 1)
        sys.modules[prefix + ".utils.display"].log_exception.assert_called_once()


if __name__ == "__main__":
    unittest.main()
