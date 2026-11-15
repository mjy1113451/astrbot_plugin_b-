"""Issue #41 logging tests: fake AstrBot only, no real user data or network."""
import builtins
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import importlib
from importlib.abc import MetaPathFinder
from io import StringIO
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_plugin_logging_test_plugin"
CANARY = "CREDENTIAL_CANARY_41_DO_NOT_LOG"


class _RejectBusinessImports(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(PACKAGE + ".core") or fullname.startswith("core"):
            raise AssertionError(f"Logging imported business configuration: {fullname}")
        return None


class PluginLoggingTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        package = ModuleType(PACKAGE)
        package.__path__ = [str(ROOT)]
        package.__package__ = PACKAGE
        colorama = ModuleType("colorama")
        colorama.Fore = SimpleNamespace(**dict.fromkeys((
            "WHITE", "GREEN", "YELLOW", "RED", "CYAN", "MAGENTA", "BLUE",
            "LIGHTBLUE_EX", "LIGHTBLACK_EX", "LIGHTMAGENTA_EX", "LIGHTCYAN_EX",
            "LIGHTYELLOW_EX", "LIGHTGREEN_EX",
        ), "\x1b[32m"))
        colorama.Style = SimpleNamespace(RESET_ALL="\x1b[0m")
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
        self.display = importlib.import_module(PACKAGE + ".utils.display")
        self.runtime = sys.modules[PACKAGE + "._runtime"]
        self.runtime.plugin_mode = True

    def messages(self):
        return "\n".join(str(call.args[0]) for call in self.logger.mock_calls)

    def test_plugin_levels_use_host_logger_without_terminal_output(self):
        stdout, stderr = StringIO(), StringIO()
        levels = {
            "INFO": "info", "SUCCESS": "info", "SCAN": "info", "DM": "info",
            "BRAIN": "info", "ENGINE": "info", "UNKNOWN": "info",
            "DEBUG": "debug", "DIAG": "debug", "TRACE": "debug",
            "WARN": "warning", "WARNING": "warning", "warn": "warning",
            "ERROR": "error", "ERR": "error", "CRITICAL": "critical", "FATAL": "critical",
        }
        with redirect_stdout(stdout), redirect_stderr(stderr):
            for level, method in levels.items():
                with self.subTest(level=level):
                    self.logger.reset_mock()
                    self.display.log("\x1b[31mready\x1b[0m", level)
                    self.assertEqual(len(self.logger.mock_calls), 1)
                    self.assertEqual(self.logger.mock_calls[0][0], method)
                    self.assertIn("ready", self.messages())
                    self.assertNotIn("\x1b", self.messages())
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")

    def test_quiet_reads_only_qualified_loaded_config_and_updates_live(self):
        config = ModuleType(PACKAGE + ".core.config")
        config.config = {"system": {"quiet_mode": True}}
        foreign = ModuleType("core.config")
        foreign.config = {"system": {"quiet_mode": False}}
        with patch.dict(sys.modules, {config.__name__: config, "core.config": foreign}):
            for level in ("INFO", "SCAN", "DM"):
                self.display.log("hidden", level)
            self.logger.info.assert_not_called()
            self.display.log("warning remains", "WARN")
            self.display.log("failure remains", "ERROR")
            self.display.log("debug remains", "DEBUG")
            self.logger.warning.assert_called_once()
            self.logger.error.assert_called_once()
            self.logger.debug.assert_called_once()
            config.config["system"]["quiet_mode"] = False
            self.display.log("visible", "INFO")
            self.logger.info.assert_called_once()

    def test_foreign_quiet_config_is_ignored(self):
        foreign = ModuleType("core.config")
        foreign.config = {"system": {"quiet_mode": True}}
        with patch.dict(sys.modules, {"core.config": foreign}):
            self.display.log("own installation")
        self.logger.info.assert_called_once()

    def test_logging_and_fresh_display_import_never_import_business_or_create_data(self):
        finder = _RejectBusinessImports()
        with patch.object(sys, "meta_path", [finder, *sys.meta_path]), \
                patch("os.makedirs", side_effect=AssertionError("created real data")), \
                patch.object(Path, "mkdir", side_effect=AssertionError("created real data")):
            del sys.modules[PACKAGE + ".utils.display"]
            display = importlib.import_module(PACKAGE + ".utils.display")
            display.log("early startup")
            display.log_exception("early failure", ValueError(CANARY))
        self.assertNotIn(PACKAGE + ".core", sys.modules)
        self.assertNotIn(PACKAGE + ".core.config", sys.modules)
        self.assertNotIn(PACKAGE + ".core.globals", sys.modules)
        self.assertNotIn(PACKAGE + ".core.user_data", sys.modules)
        self.assertNotIn(CANARY, self.messages())

    def test_redaction_covers_headers_json_labels_and_proxy_auth(self):
        messages = (
            f"Cookie: SESSDATA={CANARY}; other={CANARY}",
            f"Set-Cookie: session={CANARY}; HttpOnly",
            f"Authorization: Bearer {CANARY}",
            f"Proxy-Authorization: Basic {CANARY}",
            f"Authorization Bearer {CANARY}",
            f"https://user:{CANARY}@proxy.example:8080/path",
            f"socks5://{CANARY}:password@proxy.example:1080/path",
            f'{{"api_key": "{CANARY}", "status": "retry"}}',
            f'{{"unified_api_key": "{CANARY}"}}',
            f'{{"vision_api_key": "{CANARY}"}}',
            f"{{'Cookie': {{'session': '{CANARY}', 'other': ['{CANARY}']}}}}",
            f'{{"proxy_auth": ["user", "{CANARY}"]}}',
            f'{{"Authorization": "Bearer {CANARY} with extra {CANARY}"}}',
            f'{{"key": "{CANARY}", "access_token": "{CANARY}"}}',
            f"password='{CANARY} with spaces {CANARY}'",
            f"bili_jct={CANARY}; DedeUserID={CANARY}",
            f"api\x1b[31m_key: {CANARY}",
            f"api_\x00key: {CANARY}",
        )
        for message in messages:
            with self.subTest(message=message):
                redacted = self.display.redact_sensitive_text(message)
                self.assertNotIn(CANARY, redacted)
                self.assertIn("***", redacted)
                self.assertNotIn("\x1b", redacted)
                self.assertNotIn("\x00", redacted)
                self.display.log(message, "WARN")
        self.assertNotIn(CANARY, self.messages())
        self.assertIn('"status": "retry"', self.display.redact_sensitive_text(messages[7]))
        self.assertEqual(self.display.redact_sensitive_text("connection timed out; retry 2"),
                         "connection timed out; retry 2")

    def test_exception_uses_saved_stack_without_message_source_or_locals(self):
        def fail_with_secret():
            local_secret = CANARY
            raise ValueError("unlabelled raw response " + local_secret)

        try:
            fail_with_secret()
        except ValueError as caught:
            exc = caught
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            self.display.log_exception("fetch video", exc)
        self.logger.error.assert_called_once()
        text = self.messages()
        self.assertIn("fetch video", text)
        self.assertIn("ValueError", text)
        self.assertRegex(text, r"test_plugin_logging\.py:\d+ in fail_with_secret")
        self.assertNotIn("NoneType: None", text)
        self.assertNotIn(CANARY, text)
        self.assertNotIn("unlabelled raw response", text)
        self.assertNotIn("raise ValueError", text)
        self.assertNotIn(str(ROOT), text)
        self.assertEqual(stdout.getvalue() + stderr.getvalue(), "")

    def test_exception_never_stringifies_exception_or_follows_its_cause(self):
        class UnsafeError(Exception):
            def __str__(self):
                raise AssertionError("exception stringification is forbidden")

            def __repr__(self):
                raise AssertionError("exception repr is forbidden")

        exc = UnsafeError(CANARY)
        exc.__cause__ = ValueError(CANARY)
        self.display.log_exception("safe failure", exc)
        self.assertIn("UnsafeError", self.messages())
        self.assertNotIn(CANARY, self.messages())

    def test_standalone_keeps_terminal_and_never_imports_astrbot(self):
        self.runtime.plugin_mode = False
        original_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name == "astrbot" or name.startswith("astrbot."):
                raise AssertionError("standalone imported AstrBot")
            return original_import(name, *args, **kwargs)

        stdout, stderr = StringIO(), StringIO()
        with patch("builtins.__import__", side_effect=guarded_import), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            self.display.log("terminal ready", "SUCCESS")
            self.display.log(f"Cookie: session={CANARY}", "WARN")
        self.logger.info.assert_not_called()
        self.logger.warning.assert_not_called()
        self.assertIn("terminal ready", stdout.getvalue())
        self.assertIn("[OK]", stdout.getvalue())
        self.assertIn("***", stdout.getvalue())
        self.assertNotIn(CANARY, stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")

    def test_logging_does_not_replace_global_print_streams_or_logger(self):
        original_print = builtins.print
        original_stdout, original_stderr = sys.stdout, sys.stderr
        logger_methods = {name: getattr(self.logger, name) for name in
                          ("debug", "info", "warning", "error", "critical")}
        self.display.log("local change only")
        self.assertIs(builtins.print, original_print)
        self.assertIs(sys.stdout, original_stdout)
        self.assertIs(sys.stderr, original_stderr)
        self.assertIs(sys.modules["astrbot.api"].logger, self.logger)
        for name, method in logger_methods.items():
            self.assertIs(getattr(self.logger, name), method)


if __name__ == "__main__":
    unittest.main()
