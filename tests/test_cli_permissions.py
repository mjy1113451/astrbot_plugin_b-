"""CLI permission regressions run real AST functions with isolated dependencies.

Run: python -B -m unittest discover -s tests -p test_cli_permissions.py -v
Only temporary profiles are used; no application startup, accounts, or network.
"""
import ast
import asyncio
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
from functools import wraps
import importlib.util
from io import StringIO
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "cli/app.py"
PACKAGE = "_cli_permissions_fixture"


def extract(name, namespace):
    """Compile the production function unchanged, including its decorators."""
    tree = ast.parse(APP.read_text(encoding="utf-8"), filename=str(APP))
    node = next((node for node in tree.body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name == name), None)
    if node is None:
        raise AssertionError(f"cli/app.py must define {name}")
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    exec(compile(module, str(APP), "exec"), namespace)
    return namespace[name]


class CliPermissionsTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(sys, "dont_write_bytecode", True))
        self.stack.enter_context(patch.dict(sys.modules))
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        for name in (PACKAGE, PACKAGE + ".core", PACKAGE + ".cli"):
            module = ModuleType(name)
            module.__path__ = []
            sys.modules[name] = module
        config_module = ModuleType(PACKAGE + ".core.config")
        self.disk_load = Mock(side_effect=AssertionError("permissions must not read config"))
        config_module.load_config = self.disk_load
        sys.modules[config_module.__name__] = config_module
        spec = importlib.util.spec_from_file_location(
            PACKAGE + ".core.action_permissions", ROOT / "core/action_permissions.py"
        )
        self.permissions = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.permissions
        spec.loader.exec_module(self.permissions)
        self.output = Mock()
        colors = SimpleNamespace(**dict.fromkeys(
            ("CYAN", "YELLOW", "RED", "GREEN", "BLUE", "MAGENTA", "LIGHTMAGENTA_EX",
             "LIGHTBLUE_EX", "RESET_ALL"), ""
        ))
        self.namespace = {
            "__name__": PACKAGE + ".cli.app", "__package__": PACKAGE + ".cli",
            "_runtime": SimpleNamespace(plugin_mode=False), "print": self.output,
            "input": Mock(side_effect=AssertionError("unexpected terminal input")),
            "load_config": Mock(side_effect=AssertionError("unexpected CLI config read")),
            "save_config": Mock(side_effect=AssertionError("unexpected config write")),
            "_reload_all_globals": Mock(), "_initialize_cli_terminal": Mock(),
            "Fore": colors, "Style": colors, "json": json, "os": os,
            "asyncio": asyncio, "wraps": wraps, "log": Mock(),
        }
        extract("_cli_only", self.namespace)

    def legacy(self):
        return {
            "automation": {"allow_like": True, "allow_coin": True, "allow_comment": True},
            "approval_review": {"enabled": False},
            "interaction": {"prob_coin": 1, "prob_like_solo": 1, "prob_fav": 1,
                            "prob_comment_others": 1, "coin_threshold": 0,
                            "max_coins_daily": 0, "random_enabled": False},
            "behavior": {"comment_mode": "real"},
            "private_message": {"enabled": True, "auto_reply": True,
                                "agent": {"allow_account_actions": True,
                                          "allow_social_follow_actions": True,
                                          "allow_proactive_social_follow": True}},
            "up_follow": {"enabled": True, "auto_follow_prob": 1},
            "danmaku": {"enabled": True, "send_prob": 1, "like_prob": 1},
        }

    def grant(self, *actions):
        return {"ai_permissions": {"enabled": True, "actions": dict.fromkeys(actions, True)}}

    def text(self):
        return "\n".join(" ".join(str(arg) for arg in invocation.args)
                         for invocation in self.output.call_args_list)

    def statuses(self):
        labels = "|".join(re.escape(label) for label in self.permissions.ACTIONS.values())
        return re.findall(rf"({labels})\s*[:：]\s*(已授权|未授权)", self.text())

    def expected_statuses(self, granted=(), actions=None):
        return [(self.permissions.ACTIONS[action], "已授权" if action in granted else "未授权")
                for action in (self.permissions.ACTIONS if actions is None else actions)]

    def shared_menu(self):
        for name in ("_flatten_shared_settings", "_shared_setting_is_sensitive",
                     "_shared_setting_display", "_parse_shared_setting_value"):
            extract(name, self.namespace)
        return extract("show_shared_settings_menu", self.namespace)

    def test_legacy_automation_does_not_authorize_any_of_the_ten_actions(self):
        show = extract("_show_cli_permissions", self.namespace)
        legacy = self.legacy()
        before = deepcopy(legacy)
        show(legacy)
        self.assertEqual(len(self.permissions.ACTIONS), 10)
        self.assertCountEqual(self.statuses(), self.expected_statuses())
        self.assertEqual(legacy, before)
        self.assertNotIn("ai_permissions", legacy)
        self.disk_load.assert_not_called()
        self.namespace["load_config"].assert_not_called()
        self.namespace["save_config"].assert_not_called()

    def test_static_guidance_separates_strategy_review_and_migration_from_authorization(self):
        extract("_show_cli_permissions", self.namespace)({})
        text = self.text()
        for pattern in (r"ai_permissions[^\n]*唯一[^\n]*授权", r"候选[^\n]*不能授权",
                        r"审批[^\n]*不构成授权", r"升级/导入[^\n]*不会自动授权",
                        r"ALL[^\n]*ai_permissions"):
            with self.subTest(pattern=pattern):
                self.assertRegex(text, pattern)

    def test_master_and_each_action_require_actual_booleans(self):
        show = extract("_show_cli_permissions", self.namespace)
        for master in (True, False, 0, 1, "true", "false", None, {}, []):
            for action_flag in (True, False, 0, 1, "true", "false", None, {}, []):
                with self.subTest(master=master, action=action_flag):
                    current = {"ai_permissions": {"enabled": master,
                                                 "actions": {"coin": action_flag,
                                                             "private_reply": True}}}
                    before = deepcopy(current)
                    self.output.reset_mock()
                    show(current, ("coin",))
                    expected = "已授权" if master is True and action_flag is True else "未授权"
                    self.assertEqual(self.statuses(), [(self.permissions.ACTIONS["coin"], expected)])
                    self.assertEqual(current, before)
        for action in self.permissions.ACTIONS:
            with self.subTest(granted_action=action):
                self.output.reset_mock()
                show(self.grant(action))
                self.assertEqual(self.statuses(), self.expected_statuses((action,)))
        self.disk_load.assert_not_called()

    def test_malformed_supplied_config_denies_without_implicit_disk_load(self):
        show = extract("_show_cli_permissions", self.namespace)
        configs = [None, [], "malformed-config-canary", False, 0,
                   {"ai_permissions": None}, {"ai_permissions": []},
                   {"ai_permissions": {"enabled": True}},
                   {"ai_permissions": {"enabled": True, "actions": []}}]
        for current in configs:
            with self.subTest(config=current):
                before = deepcopy(current)
                self.output.reset_mock()
                show(current)
                self.assertEqual(self.statuses(), self.expected_statuses())
                self.assertEqual(current, before)
                self.assertNotIn("malformed-config-canary", self.text())
        self.disk_load.assert_not_called()
        self.namespace["load_config"].assert_not_called()
        self.namespace["save_config"].assert_not_called()

    def test_subset_uses_real_permissions_and_fixed_labels_without_leaking_inputs(self):
        show = extract("_show_cli_permissions", self.namespace)
        unknown = "action-name-canary\nFORGED: 已授权"
        current = self.grant("coin")
        current.update(api={"unified_api_key": "api-key-canary"},
                       cookies={"SESSDATA": "cookie-canary", "bili_jct": "csrf-canary"},
                       private_value="configuration-canary")
        current["ai_permissions"]["actions"][unknown] = True
        before = deepcopy(current)
        with patch.object(self.permissions, "allowed", wraps=self.permissions.allowed) as allowed, \
                patch("builtins.open", side_effect=AssertionError("helper opened a file")):
            show(current, ("coin", unknown, "private_reply"))
        self.assertEqual(allowed.call_args_list,
                         [call("coin", current), call("private_reply", current)])
        self.assertTrue(all(invocation.args[1] is current for invocation in allowed.call_args_list))
        self.assertEqual(self.statuses(), self.expected_statuses(("coin",), ("coin", "private_reply")))
        for canary in (unknown, "action-name-canary", "api-key-canary", "cookie-canary",
                       "csrf-canary", "configuration-canary", "SESSDATA", "bili_jct"):
            self.assertNotIn(canary, self.text())
        self.assertEqual(current, before)
        self.disk_load.assert_not_called()
        self.namespace["input"].assert_not_called()
        self.namespace["load_config"].assert_not_called()
        self.namespace["save_config"].assert_not_called()

    def test_plugin_returns_before_permission_import_iteration_or_stdout(self):
        show = extract("_show_cli_permissions", self.namespace)
        self.namespace["_runtime"].plugin_mode = True
        del sys.modules[self.permissions.__name__]
        stdout = StringIO()
        with redirect_stdout(stdout):
            show(object(), actions=object())
        self.assertEqual(stdout.getvalue(), "")
        self.output.assert_not_called()
        self.disk_load.assert_not_called()
        self.namespace["input"].assert_not_called()
        self.namespace["save_config"].assert_not_called()

    def test_initialization_displays_updated_reference_only_after_terminal_success(self):
        show = extract("_show_cli_permissions", self.namespace)
        reference, updated = self.legacy(), self.grant("coin")
        ordered = Mock()
        ordered.initialize.return_value = updated
        ordered.attach_mock(Mock(wraps=show), "display")
        shared = SimpleNamespace(initialize_config=ordered.initialize)
        self.namespace.update(
            config=reference, _run_with_shared_config_path=lambda operation: operation(shared),
            _reload_all_globals=ordered.reload, _initialize_cli_terminal=ordered.terminal,
            _show_cli_permissions=ordered.display,
        )
        extract("initialize_cli", self.namespace)()
        self.assertIs(self.namespace["config"], reference)
        self.assertEqual(reference, updated)
        self.assertEqual(ordered.mock_calls,
                         [call.initialize(), call.reload(reference), call.terminal(), call.display(reference)])
        self.assertIs(ordered.display.call_args.args[0], reference)
        self.assertEqual(self.statuses(), self.expected_statuses(("coin",)))

    def test_initialization_failure_never_displays_a_permission_summary(self):
        show = extract("_show_cli_permissions", self.namespace)
        initialize = extract("initialize_cli", self.namespace)
        for failing_stage in ("initialize", "reload", "terminal"):
            with self.subTest(stage=failing_stage):
                self.output.reset_mock()
                reference = self.legacy()
                before = deepcopy(reference)
                initialize_config = Mock(return_value=self.grant("coin"))
                reload_config, terminal, display = Mock(), Mock(), Mock(wraps=show)
                stages = {"initialize": initialize_config, "reload": reload_config, "terminal": terminal}
                stages[failing_stage].side_effect = OSError("controlled initialization failure")
                shared = SimpleNamespace(initialize_config=initialize_config)
                self.namespace.update(
                    config=reference, _run_with_shared_config_path=lambda operation: operation(shared),
                    _reload_all_globals=reload_config, _initialize_cli_terminal=terminal,
                    _show_cli_permissions=display,
                )
                with self.assertRaisesRegex(OSError, "controlled initialization failure"):
                    initialize()
                display.assert_not_called()
                self.output.assert_not_called()
                if failing_stage == "initialize":
                    self.assertEqual(reference, before)
                    reload_config.assert_not_called()
                    terminal.assert_not_called()
                elif failing_stage == "reload":
                    terminal.assert_not_called()

    def test_import_time_configuration_block_is_read_only_and_does_not_display(self):
        tree = ast.parse(APP.read_text(encoding="utf-8"), filename=str(APP))
        names = {node.name: index for index, node in enumerate(tree.body)
                 if isinstance(node, ast.FunctionDef)}
        # Execute the real module-level config load plus helper/initializer definitions.
        body = tree.body[names["load_config"]:names["initialize_cli"] + 1]
        current = self.legacy()
        shared = SimpleNamespace(load_config=Mock(return_value=current),
                                 initialize_config=Mock(), save_config=Mock())
        self.namespace["_run_with_shared_config_path"] = lambda operation: operation(shared)
        exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])),
                     str(APP), "exec"), self.namespace)
        self.assertIs(self.namespace["config"], current)
        shared.load_config.assert_called_once_with()
        shared.initialize_config.assert_not_called()
        shared.save_config.assert_not_called()
        self.namespace["_initialize_cli_terminal"].assert_not_called()
        self.namespace["_reload_all_globals"].assert_not_called()
        self.output.assert_not_called()
        for statement in tree.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            calls = [node.func.id for node in ast.walk(statement)
                     if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
            self.assertNotIn("_show_cli_permissions", calls)
            self.assertNotIn("initialize_cli", calls)

    def test_main_menu_reuses_initialization_summary_without_a_second_direct_display(self):
        class StopBeforeMenuBody(Exception):
            pass

        extract("_show_cli_permissions", self.namespace)
        current = self.grant("coin")
        shared = SimpleNamespace(initialize_config=Mock(return_value=current))
        self.namespace.update(config={}, _run_with_shared_config_path=lambda operation: operation(shared),
                              InterestManager=Mock(side_effect=StopBeforeMenuBody))
        extract("initialize_cli", self.namespace)
        menu = extract("show_main_menu", self.namespace)
        with self.assertRaises(StopBeforeMenuBody):
            menu()
        shared.initialize_config.assert_called_once_with()
        self.assertEqual(self.statuses(), self.expected_statuses(("coin",)))
        node = next(node for node in ast.parse(APP.read_text(encoding="utf-8")).body
                    if isinstance(node, ast.FunctionDef) and node.name == "show_main_menu")
        self.assertFalse(any(isinstance(item, ast.Call) and isinstance(item.func, ast.Name)
                             and item.func.id == "_show_cli_permissions" for item in ast.walk(node)))

    def test_shared_settings_refreshes_summary_from_each_new_load_not_startup_cache(self):
        show = extract("_show_cli_permissions", self.namespace)
        reference = self.grant(*self.permissions.ACTIONS)
        first, second = self.grant("coin"), self.legacy()
        loaded = [deepcopy(first), deepcopy(second)]
        self.namespace.update(config=reference, load_config=Mock(side_effect=loaded),
                              input=Mock(side_effect=["1", "0", "0"]))
        display = Mock(wraps=show)
        self.namespace["_show_cli_permissions"] = display
        self.shared_menu()()
        self.assertEqual(display.call_count, 2)
        self.assertTrue(all(invocation.args[0] is reference for invocation in display.call_args_list))
        self.assertIs(self.namespace["config"], reference)
        self.assertEqual(reference, second)
        self.assertEqual(loaded, [first, second])
        self.assertEqual(self.statuses(), self.expected_statuses(("coin",)) + self.expected_statuses())
        self.assertEqual(self.namespace["load_config"].call_count, 2)
        self.namespace["save_config"].assert_not_called()
        self.disk_load.assert_not_called()

    def test_reading_legacy_shared_settings_does_not_rewrite_or_migrate_the_file(self):
        extract("_show_cli_permissions", self.namespace)
        path = self.temp / "config.json"
        original = (json.dumps(self.legacy(), ensure_ascii=False, indent=1) + "\n\n").encode("utf-8")
        path.write_bytes(original)
        original_mode = stat.S_IMODE(path.stat().st_mode)
        reference = self.grant(*self.permissions.ACTIONS)
        self.namespace.update(
            config=reference, input=Mock(return_value="0"),
            load_config=Mock(side_effect=lambda: json.loads(path.read_text(encoding="utf-8"))),
        )
        self.shared_menu()()
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), original_mode)
        self.assertEqual(sorted(item.name for item in self.temp.iterdir()), ["config.json"])
        self.assertNotIn("ai_permissions", reference)
        self.assertEqual(self.statuses(), self.expected_statuses())
        self.namespace["save_config"].assert_not_called()
        self.disk_load.assert_not_called()

    def test_shared_settings_save_displays_the_reloaded_object_before_returning(self):
        show = extract("_show_cli_permissions", self.namespace)
        initial = self.grant("coin")
        initial["interaction"] = {"coin_threshold": 8}
        refreshed = deepcopy(initial)
        refreshed["ai_permissions"]["enabled"] = False
        refreshed["interaction"]["coin_threshold"] = 9
        snapshots, submitted = [], []

        def display(current):
            snapshots.append(deepcopy(current))
            show(current)

        def save(current):
            submitted.append(deepcopy(current))
            return True

        ordered = Mock()
        ordered.attach_mock(Mock(side_effect=[deepcopy(initial), deepcopy(refreshed), deepcopy(refreshed)]), "load")
        ordered.attach_mock(Mock(side_effect=save), "save")
        ordered.attach_mock(Mock(side_effect=display), "display")
        reference = {}
        self.namespace.update(config=reference, load_config=ordered.load, save_config=ordered.save,
                              _reload_all_globals=ordered.reload, _show_cli_permissions=ordered.display,
                              input=Mock(side_effect=["2", "1", "9", "0", "0"]))
        self.shared_menu()()
        self.assertEqual(snapshots, [initial, refreshed, refreshed])
        self.assertEqual([invocation[0] for invocation in ordered.mock_calls],
                         ["load", "display", "save", "load", "reload", "display", "load", "display"])
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0]["interaction"]["coin_threshold"], 9)
        self.assertIs(submitted[0]["ai_permissions"]["enabled"], True)
        self.assertIs(self.namespace["config"], reference)
        self.assertEqual(reference, refreshed)
        self.assertEqual(self.statuses(), self.expected_statuses(("coin",)) + self.expected_statuses() * 2)
        self.disk_load.assert_not_called()

    def test_import_executes_real_restore_and_displays_rebound_config_without_auto_migration(self):
        show = extract("_show_cli_permissions", self.namespace)
        cases = [("legacy", self.legacy(), ()), ("explicit", self.grant("coin"), ("coin",))]
        for name, imported, granted in cases:
            with self.subTest(profile=name):
                self.output.reset_mock()
                profile = self.temp / name
                profile.mkdir()
                paths = ("CONFIG_FILE", "COOKIE_FILE", "MOOD_STATE_FILE", "PERSONAS_FILE",
                         "USER_PROFILES_FILE", "INTERESTS_FILE", "COMMENT_LOG_FILE",
                         "PRIVATE_MESSAGE_LOG_FILE", "HISTORY_VIDEOS_FILE", "AGENT_SKILL_LOG_FILE",
                         "SELF_EVOLUTION_FILE", "BOT_DIARY_FILE", "RUNTIME_STATE_FILE", "MEMORY_FILE",
                         "KB_METADATA_FILE", "LEARNING_LOG_FILE")
                self.namespace.update({key: str(profile / (key.lower() + ".json")) for key in paths})
                backup = profile / "backup.json"
                imported["api"] = {"unified_api_key": "import-credential-canary"}
                original = (json.dumps({"version": "fixture", "exported_at": "fixture", "config": imported},
                                       ensure_ascii=False, indent=1) + "\n\n").encode("utf-8")
                backup.write_bytes(original)
                backup_mode = stat.S_IMODE(backup.stat().st_mode)
                target = Path(self.namespace["CONFIG_FILE"])
                stale = self.grant(*self.permissions.ACTIONS) if not granted else self.legacy()
                stale_before = deepcopy(stale)
                target.write_text(json.dumps(stale), encoding="utf-8")
                loaded, raw_snapshots = [], []

                def load_restored():
                    current = json.loads(target.read_text(encoding="utf-8"))
                    loaded.append(current)
                    raw_snapshots.append((target.read_bytes(), stat.S_IMODE(target.stat().st_mode)))
                    return current

                display = Mock(wraps=show)
                self.namespace.update(config=stale, BACKUP_FILE=str(backup), DATA_DIR=str(profile),
                                      input=Mock(side_effect=["", "YES", ""]),
                                      load_config=Mock(side_effect=load_restored),
                                      _reload_all_globals=Mock(), _show_cli_permissions=display)
                extract("import_config", self.namespace)()
                self.namespace["load_config"].assert_called_once_with()
                self.assertIs(self.namespace["config"], loaded[0])
                self.assertIsNot(self.namespace["config"], stale)
                self.assertEqual(stale, stale_before)
                self.assertEqual(loaded[0], imported)
                self.namespace["_reload_all_globals"].assert_called_once_with(loaded[0])
                display.assert_called_once_with(loaded[0])
                self.assertIs(display.call_args.args[0], loaded[0])
                self.assertEqual(self.statuses(), self.expected_statuses(granted))
                self.assertNotIn("import-credential-canary", self.text())
                self.assertEqual(backup.read_bytes(), original)
                self.assertEqual(stat.S_IMODE(backup.stat().st_mode), backup_mode)
                self.assertEqual((target.read_bytes(), stat.S_IMODE(target.stat().st_mode)), raw_snapshots[0])
                self.assertEqual(json.loads(target.read_text(encoding="utf-8")), imported)
                self.assertEqual(len(list(profile.iterdir())), 2)
                self.namespace["save_config"].assert_not_called()
                self.disk_load.assert_not_called()

    def test_legacy_menus_display_only_their_action_subset_on_each_visit(self):
        show = extract("_show_cli_permissions", self.namespace)
        numeric_names = (
            "PROB_COMMENT_OTHERS", "COMMENT_CHECK_INTERVAL", "MAX_REPLIES_PER_CHECK",
            "PRIVATE_MESSAGE_CHECK_INTERVAL", "PRIVATE_MESSAGE_MAX_REPLIES", "COIN_THRESHOLD",
            "FAV_THRESHOLD", "INTEREST_THRESHOLD", "LEARN_MIN_SCORE", "LEARN_MIN_DURATION_SECONDS",
            "UP_FOLLOW_AUTO_PROB", "UP_FOLLOW_MAX_DAILY", "UP_FOLLOW_COOLDOWN_MINUTES",
            "UP_FOLLOW_BROWSE_PROB", "UP_FOLLOW_MAX_BROWSE", "UP_FOLLOW_UNFOLLOW_INACTIVE_DAYS",
            "UP_FOLLOW_MIN_SCORE", "UP_FOLLOW_MIN_IMPRESSIONS", "UP_FOLLOW_EXCEPTIONAL_SCORE",
            "DANMAKU_READ_PROB", "DANMAKU_LIKE_PROB", "DANMAKU_MAX_DAILY_LIKES",
            "DANMAKU_SEND_PROB", "DANMAKU_MAX_DAILY_SEND",
        )
        self.namespace.update(dict.fromkeys(numeric_names, 1))
        self.namespace.update(COMMENT_MODE="real", COMMENT_CHECK_ENABLED=True, RANDOM_ENABLED=False,
                              REPLY_SAFETY_ENABLED=False, REPLY_SAFETY_BLOCKED_KEYWORDS=[],
                              PRIVATE_MESSAGE_ENABLED=True, PRIVATE_MESSAGE_AUTO_REPLY=True,
                              ACTIVE_CHAT_ENABLED=True, UP_FOLLOW_ENABLED=True, DANMAKU_ENABLED=True)
        cases = [
            ("configure_interaction_params", [""] * 10,
             ("video_like", "coin", "favorite", "public_comment", "comment_like", "private_reply"), 1),
            ("show_coin_settings_menu", ["invalid", "0"], ("coin",), 2),
            ("show_comment_menu", ["invalid", "9"], ("public_comment", "comment_like"), 2),
            ("show_private_message_menu", ["invalid", "0"],
             ("private_reply", "video_like", "coin", "favorite", "follow_up", "unfollow_user"), 2),
            ("show_up_danmaku_menu", ["invalid", "0"],
             ("follow_up", "unfollow_user", "send_danmaku", "danmaku_like"), 2),
        ]
        for name, answers, actions, visits in cases:
            with self.subTest(menu=name):
                self.output.reset_mock()
                current = self.legacy()
                before = deepcopy(current)
                display = Mock(wraps=show)
                self.namespace.update(config=current, input=Mock(side_effect=answers),
                                      save_config=Mock(return_value=True), _show_cli_permissions=display)
                extract(name, self.namespace)()
                self.assertEqual(display.call_count, visits)
                for invocation in display.call_args_list:
                    self.assertIs(invocation.args[0], current)
                self.assertEqual(self.statuses(), self.expected_statuses(actions=actions) * visits)
                self.assertEqual(self.namespace["input"].call_count, len(answers))
                self.assertEqual(current, before)
                self.assertNotIn("ai_permissions", current)
                self.disk_load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
