"""Issue #41 host/CLI boundaries without importing business dependencies."""
from contextlib import ExitStack
import runpy
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_package_imports import ROOT, PACKAGE_A, _package_environment


class RuntimeEntryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.environment = self.stack.enter_context(_package_environment())
        self.main = self.environment.load_main(PACKAGE_A)

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
        runtime.app._show_bg_tasks = Mock(side_effect=ValueError("PRIVATE_RESPONSE_CANARY"))
        bot = self.environment.construct_bot(self.main)
        replies = [reply async for reply in bot.status(SimpleNamespace(plain_result=lambda text: text))]
        self.assertIn("ValueError", replies[0])
        self.assertNotIn("PRIVATE_RESPONSE_CANARY", replies[0])
        runtime.display.log_exception.assert_called_once()

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
        with patch.dict(sys.modules, {data.__name__: data}), \
                patch.object(sys, "argv", ["main.py", "--migrate-data", "explicit-source"]):
            namespace["run_cli"]()
        data.migrate_legacy_data.assert_called_once_with("explicit-source")
        app.show_main_menu.assert_not_called()
        self.assertNotIn(prefix + ".core.config", sys.modules)
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
