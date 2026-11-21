"""Configuration schema contracts without AstrBot or business dependencies."""
import importlib
import json
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_config_schema_test_plugin"
ACTION_KEYS = {
    "video_like", "coin", "favorite", "follow_up", "unfollow_user",
    "public_comment", "comment_like", "private_reply", "send_danmaku", "danmaku_like",
}


class ConfigSchemaTests(unittest.TestCase):
    def setUp(self):
        self.schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        package = ModuleType(PACKAGE)
        package.__path__ = [str(ROOT)]
        package.__package__ = PACKAGE
        modules = patch.dict(sys.modules, {PACKAGE: package})
        modules.start()
        self.addCleanup(modules.stop)
        self.permissions = importlib.import_module(f"{PACKAGE}.core.action_permissions")

    def test_config_schema_is_a_complete_json_object(self):
        self.assertIsInstance(self.schema, dict)
        self.assertTrue(self.schema)

    def test_permission_schema_matches_shared_definitions_and_false_defaults(self):
        self.assertEqual(set(self.permissions.ACTIONS), ACTION_KEYS)
        defaults = self.permissions.defaults()
        permission = self.schema["ai_permissions"]
        self.assertEqual(permission["type"], "object")
        self.assertEqual(set(permission["items"]), {"enabled", "actions"})
        enabled = permission["items"]["enabled"]
        self.assertEqual(enabled["type"], "bool")
        self.assertIs(enabled["default"], False)
        self.assertIs(defaults["enabled"], False)
        actions = permission["items"]["actions"]
        self.assertEqual(actions["type"], "object")
        self.assertEqual(set(actions["items"]), ACTION_KEYS)
        self.assertEqual(set(defaults["actions"]), ACTION_KEYS)
        for action, node in actions["items"].items():
            with self.subTest(action=action):
                self.assertEqual(node["type"], "bool")
                self.assertIs(node["default"], False)
                self.assertIs(defaults["actions"][action], False)
                self.assertTrue(node["description"])
        self.assertNotIn(f"{PACKAGE}.core.config", sys.modules)

    def test_permission_warning_is_static_and_panel_application_stays_opt_in(self):
        permission = self.schema["ai_permissions"]
        enabled = permission["items"]["enabled"]
        self.assertIn("不会自动授权", permission["hint"])
        self.assertIn("逐项", permission["hint"])
        self.assertIn("不会自动授权", enabled["hint"])
        for node in (permission, enabled, permission["items"]["actions"]):
            self.assertNotIn("condition", node)
            self.assertNotIn("visible_when", node)
        self.assertEqual(self.schema["use_astrbot_config"]["type"], "bool")
        self.assertIs(self.schema["use_astrbot_config"]["default"], False)
        self.assertEqual(self.schema["apply_sections"]["type"], "list")
        self.assertEqual(self.schema["apply_sections"]["default"], [])
        self.assertIn("ai_permissions", self.schema["apply_sections"]["description"])

    def test_review_and_safety_defaults_are_not_replaced_by_permissions(self):
        for section in ("approval_review", "reply_safety", "prompt_injection"):
            with self.subTest(section=section):
                self.assertIs(self.schema[section]["items"]["enabled"]["default"], True)
        self.assertNotIn("knowledge_write", self.permissions.ACTIONS)
        self.assertNotIn("file_export", self.permissions.ACTIONS)


if __name__ == "__main__":
    unittest.main()
