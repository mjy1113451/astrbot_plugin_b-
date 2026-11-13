"""Integration tests for AstrBot configuration using isolated real core.config.

Run with: python -m unittest discover -s tests -p test_astrbot_config_bridge.py
"""
import copy
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_astrbot_config_bridge_test_plugin"


class AstrBotConfigBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        data = root / "Data"
        data.mkdir()
        kb = root / "KnowledgeBase"
        self.disk_config = {
            "api": {"unified_api_key": "disk-secret", "vision_api_key": "disk-vision-secret",
                    "unified_base_url": "https://disk.example/v1", "model_brain": "disk-model",
                    "max_retries": 7, "legacy_setting": "keep-me"},
            "fallback_provider": {"api_key": "fallback-secret", "models": {
                "chat": "disk-fallback", "vision": "disk-vision", "legacy": "keep"}},
            "prompt_injection": {"enabled": True, "custom_terms": ["diskterm"]},
            "approval_review": {"enabled": True, "action_types": {"coin": True, "public_comment": True}},
            "legacy_section": {"important": [1, 2]},
            "knowledge_base_dir": str(kb),
        }
        self.path = data / "config.json"
        self.path.write_text(json.dumps(self.disk_config, ensure_ascii=False), encoding="utf-8")
        self.original_bytes = self.path.read_bytes()
        package = ModuleType(PACKAGE)
        package.__path__ = [str(ROOT)]
        package.__package__ = PACKAGE
        user_data = ModuleType(f"{PACKAGE}.core.user_data")
        user_data.USER_DATA_DIR = root
        user_data.DATA_DIR = data
        user_data.PROJECT_DIR = root
        user_data.ARTIFACTS_DIR = root
        user_data.KNOWLEDGE_BASE_DIR = kb
        user_data.HIGHLIGHTS_DIR = root / "highlights"
        user_data.HTML_EXPORTS_DIR = root / "html_exports"
        user_data.MINDMAPS_DIR = root / "MindMaps"
        user_data.WORD_DIR = root / "Word"
        user_data.QR_CODES_DIR = root / "qr_codes"
        user_data.ensure_user_data_dir = lambda: root
        colorama = ModuleType("colorama")
        colorama.Fore = SimpleNamespace(**dict.fromkeys((
            "BLACK", "RED", "GREEN", "YELLOW", "BLUE", "MAGENTA", "CYAN", "WHITE",
            "LIGHTBLACK_EX", "LIGHTRED_EX", "LIGHTGREEN_EX", "LIGHTYELLOW_EX",
            "LIGHTBLUE_EX", "LIGHTMAGENTA_EX", "LIGHTCYAN_EX", "LIGHTWHITE_EX",
            "RESET",
        ), ""))
        colorama.Style = SimpleNamespace(RESET_ALL="", BRIGHT="", DIM="", NORMAL="")
        module_patch = patch.dict(sys.modules, {PACKAGE: package,
                                               user_data.__name__: user_data,
                                               "colorama": colorama})
        module_patch.start()
        self.addCleanup(module_patch.stop)
        for name in list(sys.modules):
            if name.startswith(f"{PACKAGE}.") and name != user_data.__name__:
                del sys.modules[name]
        environment = patch.dict(os.environ, {"BILI_BACKUP_DIR": str(root / "backups")})
        environment.start()
        self.addCleanup(environment.stop)
        self.cfg = importlib.import_module(f"{PACKAGE}.core.config")
        self.bridge = importlib.import_module(f"{PACKAGE}.core.astrbot_config_bridge")
        self.initial = copy.deepcopy(self.cfg.config)
        self.config_reference = self.cfg.config

    def apply(self, sections, **fields):
        self.bridge.apply_astrbot_config({"use_astrbot_config": True,
                                         "apply_sections": sections, **fields})

    def read_disk(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def assert_disk_untouched(self):
        self.assertEqual(self.path.read_bytes(), self.original_bytes)

    def test_disabled_or_missing_switch_preserves_existing_file_and_config(self):
        for mapping in ({"use_astrbot_config": False, "api": {"model_brain": "ignored"}},
                        {"api": {"model_brain": "ignored"}}):
            with self.subTest(mapping=mapping):
                self.bridge.apply_astrbot_config(mapping)
                self.assertEqual(self.cfg.config, self.initial)
                self.assert_disk_untouched()

    def test_disabled_reloads_changed_disk_in_place_without_writing(self):
        cli = ModuleType(f"{PACKAGE}.cli")
        cli.__path__ = [str(ROOT / "cli")]
        app = ModuleType(f"{PACKAGE}.cli.app")
        cli.app = app
        app.config = copy.deepcopy(self.initial)
        cli_reference = app.config
        guard_module = importlib.import_module(f"{PACKAGE}.security.guard")
        updated = copy.deepcopy(self.disk_config)
        updated["api"]["model_brain"] = "changed-on-disk"
        updated["prompt_injection"]["custom_terms"] = ["fresh-disk-term"]
        self.path.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
        disk_bytes = self.path.read_bytes()
        self.config_reference["api"]["model_brain"] = "stale-memory"
        cli_reference["api"]["model_brain"] = "stale-cli"
        with patch.dict(sys.modules, {cli.__name__: cli, app.__name__: app}), \
                patch.object(self.cfg, "save_config", wraps=self.cfg.save_config) as save, \
                patch.object(self.cfg.os, "replace", wraps=self.cfg.os.replace) as replace:
            self.bridge.apply_astrbot_config({"use_astrbot_config": False,
                                              "api": {"model_brain": "ignored-panel"}})
        save.assert_not_called()
        replace.assert_not_called()
        self.assertEqual(self.path.read_bytes(), disk_bytes)
        self.assertIs(self.cfg.config, self.config_reference)
        self.assertIs(app.config, cli_reference)
        for config in (self.config_reference, cli_reference):
            self.assertEqual(config["api"]["model_brain"], "changed-on-disk")
            self.assertEqual(config["prompt_injection"]["custom_terms"], ["fresh-disk-term"])
        self.assertTrue(guard_module.ReplySafetyGuard().detect_injection("fresh-disk-term")[0])

    def test_disabled_with_missing_config_does_not_write(self):
        self.path.unlink()
        with patch.object(self.cfg, "save_config", wraps=self.cfg.save_config) as save:
            self.bridge.apply_astrbot_config({"use_astrbot_config": False})
        save.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_enabled_without_selected_groups_preserves_existing_settings(self):
        panel = {
            "api": {"model_brain": "panel-model", "unified_api_key": "panel-secret"},
            "prompt_injection": {"enabled": False, "custom_terms": []},
            "approval_review": {"enabled": False, "action_types": {"coin": False}},
            "reply_safety": {"enabled": False, "blocked_keywords": []},
        }
        for selection in ({}, {"apply_sections": []}):
            with self.subTest(selection=selection):
                self.bridge.apply_astrbot_config({"use_astrbot_config": True, **selection, **panel})
                for config in (self.cfg.config, self.cfg.load_config()):
                    for section in panel:
                        self.assertEqual(config[section], self.initial[section])
                    self.assertEqual(config["legacy_section"], self.initial["legacy_section"])
                disk = self.read_disk()
                for section in ("api", "prompt_injection", "approval_review", "legacy_section"):
                    self.assertEqual(disk[section], self.disk_config[section])

    def test_only_selected_group_is_applied(self):
        self.apply(["api"], api={"model_brain": "selected"},
                   prompt_injection={"enabled": False, "custom_terms": []},
                   approval_review={"enabled": False},
                   fallback_provider={"models": "invalid-but-unselected"})
        for config in (self.cfg.config, self.cfg.load_config()):
            self.assertEqual(config["api"]["model_brain"], "selected")
            for section in ("prompt_injection", "approval_review", "fallback_provider", "legacy_section"):
                self.assertEqual(config[section], self.initial[section])
        disk = self.read_disk()
        for section in ("prompt_injection", "approval_review", "fallback_provider", "legacy_section"):
            self.assertEqual(disk[section], self.disk_config[section])

    def test_invalid_section_selection_is_rejected_atomically(self):
        for selection in (None, "api", {}, True, ["api", 1], [None],
                          ["api", "not_a_section"], ["legacy_section"],
                          ["use_astrbot_config"], ["apply_sections"]):
            with self.subTest(selection=selection):
                with self.assertRaises((TypeError, ValueError)):
                    self.bridge.apply_astrbot_config({
                        "use_astrbot_config": True, "apply_sections": selection,
                        "api": {"model_brain": "must-not-be-written"},
                    })
                self.assertEqual(self.cfg.config, self.initial)
                self.assert_disk_untouched()

    def test_api_update_preserves_encrypted_safety_keywords(self):
        keywords = ["bridge-sensitive-alpha", "bridge-sensitive-beta"]
        config = copy.deepcopy(self.initial)
        config["reply_safety"].update({"enabled": True, "blocked_keywords": keywords})
        self.assertTrue(self.cfg.save_config(config))
        encrypted_before = self.read_disk()["reply_safety"]["blocked_keywords"]
        self.assertNotEqual(encrypted_before, keywords)
        self.assertEqual([self.cfg._cipher_decrypt(word) for word in encrypted_before], keywords)
        self.assertEqual(self.cfg.load_config()["reply_safety"]["blocked_keywords"], keywords)

        guard_module = importlib.import_module(f"{PACKAGE}.security.guard")
        self.apply(["api"], api={"model_brain": "updated-after-encryption"})

        disk = self.read_disk()
        self.assertEqual(disk["api"]["model_brain"], "updated-after-encryption")
        self.assertNotEqual(disk["reply_safety"]["blocked_keywords"], keywords)
        self.assertEqual([self.cfg._cipher_decrypt(word)
                          for word in disk["reply_safety"]["blocked_keywords"]], keywords)
        self.assertEqual(self.cfg.load_config()["reply_safety"]["blocked_keywords"], keywords)
        self.assertEqual(self.cfg.config["reply_safety"]["blocked_keywords"], keywords)
        guard = guard_module.ReplySafetyGuard()
        self.assertTrue(guard.should_block("bridge-sensitive-alpha bridge-sensitive-beta"))
        guard.recheck()
        self.assertTrue(guard.should_block("bridge-sensitive-alpha bridge-sensitive-beta"))

    def test_enabled_deep_merge_persists_without_losing_legacy_values(self):
        self.apply(["api", "fallback_provider"], api={"unified_base_url": "https://panel.example/v1", "model_brain": "panel-model"},
                   fallback_provider={"models": {"chat": "panel-fallback"}})
        self.assertIs(self.cfg.config, self.config_reference)
        for cfg in (self.cfg.config, self.read_disk(), self.cfg.load_config()):
            self.assertEqual(cfg["api"]["model_brain"], "panel-model")
            self.assertEqual(cfg["api"]["unified_base_url"], "https://panel.example/v1")
            self.assertEqual(cfg["api"]["legacy_setting"], "keep-me")
            self.assertEqual(cfg["legacy_section"], {"important": [1, 2]})
            self.assertEqual(cfg["fallback_provider"]["models"],
                             {"chat": "panel-fallback", "vision": "disk-vision", "legacy": "keep"})

    def test_schema_whitelist_blocks_unknown_keys_at_every_depth(self):
        self.apply(
            ["api", "fallback_provider", "approval_review"],
            api={"model_brain": "panel", "vision_api_key": "overwrite", "legacy_setting": "changed", "unknown": 1},
            fallback_provider={"models": {"chat": "panel-chat", "legacy": "changed", "unknown": 2}},
            legacy_section={"important": []}, made_up_section={"enabled": True},
            approval_review={"action_types": {"coin": False, "public_comment": False}},
        )
        for cfg in (self.cfg.config, self.read_disk()):
            self.assertEqual(cfg["api"]["model_brain"], "panel")
            self.assertEqual(cfg["api"]["vision_api_key"], "disk-vision-secret")
            self.assertEqual(cfg["api"]["legacy_setting"], "keep-me")
            self.assertNotIn("unknown", cfg["api"])
            self.assertEqual(cfg["fallback_provider"]["models"],
                             {"chat": "panel-chat", "vision": "disk-vision", "legacy": "keep"})
            self.assertEqual(cfg["legacy_section"], {"important": [1, 2]})
            self.assertNotIn("made_up_section", cfg)
            self.assertFalse(cfg["approval_review"]["action_types"]["coin"])
            self.assertTrue(cfg["approval_review"]["action_types"]["public_comment"])

    def test_empty_and_masked_secrets_preserve_existing_values(self):
        for placeholder in ("", "[已隐藏]"):
            with self.subTest(placeholder=placeholder):
                self.apply(["api", "fallback_provider"], api={"unified_api_key": placeholder}, fallback_provider={"api_key": placeholder})
                for cfg in (self.cfg.config, self.read_disk()):
                    self.assertEqual(cfg["api"]["unified_api_key"], "disk-secret")
                    self.assertEqual(cfg["fallback_provider"]["api_key"], "fallback-secret")
        self.apply(["api", "fallback_provider"], api={"unified_api_key": "new-secret"}, fallback_provider={"api_key": "new-fallback"})
        for cfg in (self.cfg.config, self.read_disk()):
            self.assertEqual(cfg["api"]["unified_api_key"], "new-secret")
            self.assertEqual(cfg["fallback_provider"]["api_key"], "new-fallback")

    def test_false_zero_and_empty_list_are_valid_values(self):
        self.apply(["api", "prompt_injection"], api={"max_retries": 0}, prompt_injection={"enabled": False, "custom_terms": []})
        for cfg in (self.cfg.config, self.read_disk(), self.cfg.load_config()):
            self.assertEqual(cfg["api"]["max_retries"], 0)
            self.assertIs(cfg["prompt_injection"]["enabled"], False)
            self.assertEqual(cfg["prompt_injection"]["custom_terms"], [])

    def test_invalid_types_rejected_atomically(self):
        cases = (
            {"api": {"max_retries": True, "model_brain": "valid"}},
            {"api": {"model_brain": 123}},
            {"prompt_injection": {"enabled": "false"}},
            {"prompt_injection": {"custom_terms": "not-a-list"}},
            {"fallback_provider": {"models": "not-an-object"}},
            {"vision": []},
        )
        for fields in cases:
            with self.subTest(fields=fields):
                with self.assertRaises(TypeError):
                    self.apply(list(fields), **fields)
                self.assertEqual(self.cfg.config, self.initial)
                self.assert_disk_untouched()

    def test_disable_does_not_revert_previously_persisted_values(self):
        self.apply(["api", "prompt_injection"], api={"model_brain": "panel", "unified_api_key": "panel-secret"},
                   prompt_injection={"enabled": False})
        saved = self.path.read_bytes()
        self.bridge.apply_astrbot_config({"use_astrbot_config": False})
        self.assertIs(self.cfg.config, self.config_reference)
        self.assertEqual(self.cfg.config["api"]["model_brain"], "panel")
        self.assertEqual(self.cfg.config["api"]["unified_api_key"], "panel-secret")
        self.assertIs(self.cfg.config["prompt_injection"]["enabled"], False)
        self.assertEqual(self.path.read_bytes(), saved)

    def test_invalid_mapping_or_switch_raises_without_changes(self):
        for invalid in (None, [], "string", {"use_astrbot_config": "false"},
                        {"use_astrbot_config": 0}, {"use_astrbot_config": None}):
            with self.subTest(invalid=invalid):
                with self.assertRaises(TypeError):
                    self.bridge.apply_astrbot_config(invalid)
                self.assertEqual(self.cfg.config, self.initial)
                self.assert_disk_untouched()

    def test_corrupt_disk_does_not_change_memory_or_rewrite_file(self):
        self.path.write_text("{invalid", encoding="utf-8")
        corrupt_bytes = self.path.read_bytes()
        with self.assertRaises((ValueError, OSError)):
            self.apply(["api"], api={"model_brain": "panel"})
        self.assertEqual(self.cfg.config, self.initial)
        self.assertEqual(self.path.read_bytes(), corrupt_bytes)

    def test_broken_schema_does_not_change_memory_or_disk(self):
        schema_path = Path(self.temp.name) / "bad-schema.json"
        schema_path.write_text("{invalid", encoding="utf-8")
        with patch.object(self.bridge, "SCHEMA_PATH", schema_path):
            with self.assertRaises((ValueError, OSError)):
                self.apply(["api"], api={"model_brain": "panel"})
        self.assertEqual(self.cfg.config, self.initial)
        self.assert_disk_untouched()

    def test_failed_atomic_replace_preserves_memory_and_existing_file(self):
        with patch.object(self.cfg.os, "replace", side_effect=OSError("write failed")):
            with self.assertRaises(OSError):
                self.apply(["api"], api={"model_brain": "panel"})
        self.assertEqual(self.cfg.config, self.initial)
        self.assert_disk_untouched()

    def test_loaded_cli_config_reference_tracks_persisted_changes(self):
        cli = ModuleType(f"{PACKAGE}.cli")
        cli.__path__ = [str(ROOT / "cli")]
        app = ModuleType(f"{PACKAGE}.cli.app")
        cli.app = app
        app.config = copy.deepcopy(self.initial)
        reference = app.config
        with patch.dict(sys.modules, {cli.__name__: cli, app.__name__: app}):
            self.apply(["api"], api={"model_brain": "panel"})
            self.assertIs(app.config, reference)
            self.assertEqual(reference["api"]["model_brain"], "panel")
            self.bridge.apply_astrbot_config({"use_astrbot_config": False})
            self.assertIs(app.config, reference)
            self.assertEqual(reference["api"]["model_brain"], "panel")

    def test_consumer_imported_before_change_reads_current_config(self):
        guard_module = importlib.import_module(f"{PACKAGE}.security.guard")
        self.apply(["prompt_injection"], prompt_injection={"enabled": True, "custom_terms": ["bridge-unique-term"]})
        guard = guard_module.ReplySafetyGuard()
        self.assertTrue(guard.detect_injection("bridge-unique-term")[0])
        self.assertFalse(guard.detect_injection("diskterm")[0])
        self.bridge.apply_astrbot_config({"use_astrbot_config": False})
        guard = guard_module.ReplySafetyGuard()
        self.assertTrue(guard.detect_injection("bridge-unique-term")[0])
        self.assertFalse(guard.detect_injection("diskterm")[0])


if __name__ == "__main__":
    unittest.main()
