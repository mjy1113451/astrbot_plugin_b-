"""Permission decisions use synthetic configuration, never real profiles or APIs."""
import importlib
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_action_permissions_fixture"


class ActionPermissionsTests(unittest.TestCase):
    def setUp(self):
        package = ModuleType(PACKAGE)
        package.__path__ = [str(ROOT)]
        self.modules = patch.dict(sys.modules, {PACKAGE: package})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.permissions = importlib.import_module(PACKAGE + ".core.action_permissions")

    def grant(self, action, enabled=True):
        return {"ai_permissions": {"enabled": enabled, "actions": {action: True}}}

    def test_import_does_not_load_business_config(self):
        self.assertNotIn(PACKAGE + ".core.config", sys.modules)

    def test_defaults_are_independent_and_deny_every_platform_action(self):
        one, two = self.permissions.defaults(), self.permissions.defaults()
        self.assertIs(one["enabled"], False)
        self.assertEqual(set(one["actions"]), set(self.permissions.ACTIONS))
        self.assertTrue(all(value is False for value in one["actions"].values()))
        one["actions"]["coin"] = True
        self.assertIs(two["actions"]["coin"], False)

    def test_master_and_exact_action_are_both_required(self):
        for action in self.permissions.ACTIONS:
            with self.subTest(action=action):
                self.assertFalse(self.permissions.allowed(action, {}))
                self.assertFalse(self.permissions.allowed(action, self.grant(action, False)))
                self.assertFalse(self.permissions.allowed(action, {"ai_permissions": {"enabled": True}}))
                self.assertTrue(self.permissions.allowed(action, self.grant(action)))
                self.assertIsNone(self.permissions.require(action, self.grant(action)))
                other = next(key for key in self.permissions.ACTIONS if key != action)
                self.assertFalse(self.permissions.allowed(action, self.grant(other)))

    def test_legacy_settings_and_approval_are_not_authorization(self):
        legacy = {"automation": {"allow_like": True, "allow_coin": True, "allow_comment": True},
                  "approval_review": {"enabled": False},
                  "interaction": {"prob_coin": 1, "prob_like_solo": 1},
                  "private_message": {"auto_reply": True}}
        for action in self.permissions.ACTIONS:
            with self.subTest(action=action):
                self.assertFalse(self.permissions.allowed(action, legacy))

    def test_malformed_and_unknown_values_fail_closed(self):
        configs = [[], "invalid", {"ai_permissions": None}, {"ai_permissions": []},
                   {"ai_permissions": {"enabled": True, "actions": []}}]
        for value in (None, 0, 1, "true", {}, []):
            configs.append({"ai_permissions": {"enabled": value, "actions": {"coin": True}}})
            configs.append({"ai_permissions": {"enabled": True, "actions": {"coin": value}}})
        for config in configs:
            with self.subTest(config=config):
                self.assertFalse(self.permissions.allowed("coin", config))
        self.assertFalse(self.permissions.allowed("unregistered_platform_write", self.grant("coin")))
        with self.assertRaises(PermissionError):
            self.permissions.require("coin", {})

    def test_decision_reloads_and_read_failure_denies_without_import_side_effects(self):
        config = ModuleType(PACKAGE + ".core.config")
        config.load_config = Mock(return_value=self.grant("coin"))
        with patch.dict(sys.modules, {config.__name__: config}):
            self.assertTrue(self.permissions.allowed("coin"))
            config.load_config.return_value = self.grant("coin", False)
            self.assertFalse(self.permissions.allowed("coin"))
            config.load_config.side_effect = ValueError("synthetic private content")
            self.assertFalse(self.permissions.allowed("coin"))
        self.assertEqual(config.load_config.call_count, 3)

    def test_each_installation_loads_only_its_own_configuration(self):
        other_name = PACKAGE + "_other"
        other = ModuleType(other_name)
        other.__path__ = [str(ROOT)]
        config = ModuleType(PACKAGE + ".core.config")
        config.load_config = Mock(return_value=self.grant("coin"))
        foreign = ModuleType(other_name + ".core.config")
        foreign.load_config = Mock(return_value=self.grant("coin", False))
        with patch.dict(sys.modules, {other_name: other, config.__name__: config, foreign.__name__: foreign}):
            other_permissions = importlib.import_module(other_name + ".core.action_permissions")
            self.assertTrue(self.permissions.allowed("coin"))
            self.assertFalse(other_permissions.allowed("coin"))
        config.load_config.assert_called_once_with()
        foreign.load_config.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
