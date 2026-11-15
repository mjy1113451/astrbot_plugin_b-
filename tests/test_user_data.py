"""Issue #41 user-data isolation; all writable fixtures live in temporary dirs.

Run: python -B -m unittest discover -s tests -p test_user_data.py -v
"""
from contextlib import ExitStack
import asyncio
import copy
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]


class UserDataTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.stack.enter_context(patch.dict(sys.modules))
        self.stack.enter_context(patch.dict(os.environ, {
            "HOME": str(self.temp / "home"), "USERPROFILE": str(self.temp / "home"),
            "LOCALAPPDATA": str(self.temp / "local"), "BILI_USER_DATA_DIR": "",
            "BILI_BACKUP_DIR": str(self.temp / "foreign-backups"),
        }))
        self.stack.enter_context(patch.object(Path, "home", return_value=self.temp / "home"))
        self.stack.enter_context(patch.object(sys, "dont_write_bytecode", True))
        self.sequence = 0

    def load(self, *, plugin=True, root=None, install=ROOT):
        self.sequence += 1
        name = f"_user_data_fixture_{self.sequence}"
        package = ModuleType(name)
        package.__path__ = [str(install)]
        package.__package__ = name
        sys.modules[name] = package
        runtime = importlib.import_module(name + "._runtime")
        if plugin:
            runtime.configure_plugin(root or self.temp / f"profile-{self.sequence}")
        elif root is not None:
            os.environ["BILI_USER_DATA_DIR"] = str(root)
        user_data = importlib.import_module(name + ".core.user_data")
        return name, runtime, user_data

    def load_config(self, name):
        colorama = ModuleType("colorama")
        colorama.Fore = SimpleNamespace(**dict.fromkeys((
            "BLACK", "RED", "GREEN", "YELLOW", "BLUE", "MAGENTA", "CYAN", "WHITE",
            "LIGHTBLACK_EX", "LIGHTRED_EX", "LIGHTGREEN_EX", "LIGHTYELLOW_EX",
            "LIGHTBLUE_EX", "LIGHTMAGENTA_EX", "LIGHTCYAN_EX", "LIGHTWHITE_EX", "RESET",
        ), ""))
        colorama.Style = SimpleNamespace(RESET_ALL="", BRIGHT="", DIM="", NORMAL="")
        sys.modules["colorama"] = colorama
        # Keep tests independent of the host logger and network packages.
        display = ModuleType(name + ".utils.display")
        display.log = Mock()
        display.log_exception = Mock()
        display.mask_secret = lambda value: "masked"
        sys.modules[display.__name__] = display
        return importlib.import_module(name + ".core.config")

    def write(self, path, text="fixture"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def symlink(self, link, target):
        try:
            link.symlink_to(target, target_is_directory=target.is_dir())
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Symlinks unavailable: {exc}")

    def test_plugin_root_overrides_global_env_and_all_artifacts_share_root(self):
        env_root = self.temp / "global-profile"
        os.environ["BILI_USER_DATA_DIR"] = str(env_root)
        _, _, data = self.load()
        self.assertFalse(data.USER_DATA_DIR.exists(), "import must not create directories")
        self.assertFalse(env_root.exists())
        self.assertEqual(data.ARTIFACTS_DIR, data.USER_DATA_DIR)
        names = ("DATA_DIR", "KNOWLEDGE_BASE_DIR", "HIGHLIGHTS_DIR", "HTML_EXPORTS_DIR",
                 "MINDMAPS_DIR", "WORD_DIR", "QR_CODES_DIR", "BACKUPS_DIR", "TEMP_DIR",
                 "CACHE_DIR", "MODELS_DIR")
        data.ensure_user_data_dir()
        for name in names:
            with self.subTest(path=name):
                path = getattr(data, name)
                self.assertTrue(path.is_relative_to(data.USER_DATA_DIR))
                self.assertTrue(path.is_dir())
        self.assertFalse(env_root.exists())

    def test_plugin_without_runtime_root_fails_closed(self):
        name, runtime, _ = self.load()
        del sys.modules[name + ".core.user_data"]
        runtime.data_root = None
        with self.assertRaises(RuntimeError):
            importlib.import_module(name + ".core.user_data")

    def test_two_package_profiles_are_independent(self):
        first_name, first_runtime, first = self.load()
        _, second_runtime, second = self.load()
        self.assertIsNot(first_runtime, second_runtime)
        self.assertNotEqual(first.USER_DATA_DIR, second.USER_DATA_DIR)
        self.write(first.DATA_DIR / "bilibili_cookies.json", "first")
        self.assertFalse((second.DATA_DIR / "bilibili_cookies.json").exists())
        self.assertEqual(importlib.import_module(first_name + ".core.user_data").USER_DATA_DIR,
                         first.USER_DATA_DIR)

    def test_private_file_paths_never_follow_links_or_allow_external_standalone_paths(self):
        _, _, data = self.load(plugin=False, root=self.temp / "standalone")
        outside = self.write(self.temp / "outside.json", "untouched")
        data.DATA_DIR.mkdir(parents=True)
        link = data.DATA_DIR / "config.json"
        self.symlink(link, outside)
        with self.assertRaises(ValueError):
            data.managed_file_path(link)
        with self.assertRaises(ValueError):
            data.write_private_json(link, {"api_key": "fake"})
        with self.assertRaises(ValueError):
            data.managed_file_path(outside)
        self.assertEqual(outside.read_text(encoding="utf-8"), "untouched")

    def test_private_json_ignores_fixed_tmp_links_and_cleans_random_temp(self):
        _, _, data = self.load()
        outside = self.write(self.temp / "outside.json", "untouched")
        data.DATA_DIR.mkdir(parents=True)
        target = data.DATA_DIR / "config.json"
        self.symlink(Path(str(target) + ".tmp"), outside)
        data.write_private_json(target, {"api_key": "synthetic"})
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"api_key": "synthetic"})
        self.assertEqual(outside.read_text(encoding="utf-8"), "untouched")
        self.assertEqual(list(data.DATA_DIR.glob(".private-*.tmp")), [])
        before = target.read_bytes()
        with patch.object(data.os, "replace", side_effect=OSError("controlled failure")):
            with self.assertRaises(OSError):
                data.write_private_json(target, {"changed": True})
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(list(data.DATA_DIR.glob(".private-*.tmp")), [])

    def test_config_and_key_reject_final_links_without_importing_foreign_profile(self):
        name, _, data = self.load()
        cfg = self.load_config(name)
        config_path = Path(cfg.CONFIG_FILE)
        config_path.unlink()
        outside = self.write(self.temp / "foreign-config.json", '{"api": {"unified_api_key": "foreign"}}')
        self.symlink(config_path, outside)
        with self.assertRaises(ValueError):
            cfg.load_config()
        self.assertFalse(cfg.save_config(copy.deepcopy(cfg.DEFAULT_CONFIG)))
        self.assertIn("foreign", outside.read_text(encoding="utf-8"))
        key = Path(cfg.CIPHER_KEY_FILE)
        if key.exists():
            key.unlink()
        missing = self.temp / "outside-new-key"
        self.symlink(key, missing)
        with self.assertRaises(ValueError):
            cfg._get_or_create_cipher_key()
        self.assertFalse(missing.exists())

    def test_standalone_default_and_explicit_roots(self):
        _, _, default = self.load(plugin=False)
        self.assertEqual(default.USER_DATA_DIR, self.temp / "local" / "BiliLearn")
        root = self.temp / "portable"
        _, _, explicit = self.load(plugin=False, root=root)
        self.assertEqual(explicit.USER_DATA_DIR, root)
        self.assertEqual(explicit.ARTIFACTS_DIR, root)
        self.assertFalse(root.exists())

    def test_frozen_and_checkout_use_same_writable_root(self):
        resources = self.temp / "read-only-resources"
        with patch.object(sys, "frozen", True, create=True), patch.object(
            sys, "_MEIPASS", str(resources), create=True
        ):
            _, _, data = self.load()
        self.assertEqual(data.PROJECT_DIR, resources)
        data.ensure_user_data_dir()
        self.assertFalse(resources.exists())
        self.assertEqual(data.KNOWLEDGE_BASE_DIR, data.USER_DATA_DIR / "KnowledgeBase")

    def test_relative_paths_keep_subdirectories_and_default(self):
        _, _, data = self.load()
        self.assertEqual(data.resolve_output_path("exports/nested/notes"),
                         data.USER_DATA_DIR / "exports" / "nested" / "notes")
        self.assertEqual(data.resolve_output_path(None, "Word/custom"),
                         data.USER_DATA_DIR / "Word" / "custom")
        self.assertEqual(data.resolve_output_path("", data.MINDMAPS_DIR), data.MINDMAPS_DIR)
        self.assertEqual(data.resolve_output_path(None), data.USER_DATA_DIR)
        self.assertFalse(data.USER_DATA_DIR.exists())

    def test_plugin_rejects_traversal_absolute_and_prefix_collision(self):
        _, _, data = self.load()
        outside = self.temp / (data.USER_DATA_DIR.name + "-other")
        for value in ("../escape", data.USER_DATA_DIR / ".." / "escape", outside,
                      self.temp / "other" / "nested"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                data.resolve_output_path(value)
        self.assertEqual(data.resolve_output_path(data.USER_DATA_DIR / "nested"),
                         data.USER_DATA_DIR / "nested")

    def test_standalone_explicit_absolute_is_allowed_but_relative_escape_is_not(self):
        _, _, data = self.load(plugin=False, root=self.temp / "standalone")
        absolute = self.temp / "chosen-output" / "nested"
        self.assertEqual(data.resolve_output_path(absolute), absolute)
        with self.assertRaises(ValueError):
            data.resolve_output_path("../escape")

    def test_symlink_escape_is_rejected_before_creating_directories(self):
        _, _, data = self.load()
        data.USER_DATA_DIR.mkdir()
        outside = self.temp / "outside"
        outside.mkdir()
        self.symlink(data.USER_DATA_DIR / "exports", outside)
        self.symlink(data.KNOWLEDGE_BASE_DIR, outside)
        with self.assertRaises(ValueError):
            data.resolve_output_path("exports/deep/file.html")
        with self.assertRaises(ValueError):
            data.ensure_user_data_dir()
        self.assertEqual(list(outside.iterdir()), [])

    def test_import_and_ensure_never_auto_migrate_or_copy_back(self):
        install = self.temp / "portable-package"
        for relative in ("_runtime.py", "core/user_data.py"):
            target = install / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        legacy_cookie = self.write(install / "Data" / "bilibili_cookies.json", "package-account")
        old_home = self.write(self.temp / "home" / "BiliLearn" / "Data" / "config.json", "home-account")
        root = self.temp / "portable-profile"
        root_note = self.write(root / "KnowledgeBase" / "private-note.md", "private")
        _, _, data = self.load(plugin=False, root=root, install=install)
        data.ensure_user_data_dir()
        self.assertFalse((data.DATA_DIR / "bilibili_cookies.json").exists())
        self.assertFalse((data.DATA_DIR / "config.json").exists())
        self.assertFalse((install / "KnowledgeBase").exists())
        self.assertFalse(any(root.glob(".*migration*")))
        self.assertEqual(legacy_cookie.read_text(encoding="utf-8"), "package-account")
        self.assertEqual(old_home.read_text(encoding="utf-8"), "home-account")
        self.assertEqual(root_note.read_text(encoding="utf-8"), "private")

    def test_explicit_migration_is_fixed_list_single_direction_and_no_clobber(self):
        _, _, data = self.load()
        source = self.temp / "legacy"
        old_note = self.write(source / "KnowledgeBase" / "same.md", "old")
        new_note = self.write(data.KNOWLEDGE_BASE_DIR / "same.md", "new")
        cookie = self.write(source / "Data" / "bilibili_cookies.json", "legacy-account")
        nested = self.write(source / "Word" / "nested" / "note.docx", "document")
        self.write(source / ".cipher_key", "legacy-key")
        self.write(source / "main.py", "must not copy")
        self.write(source / "docs" / "source.md", "must not copy")
        self.write(source / "unlisted.txt", "must not copy")
        before = {str(p.relative_to(source)): p.read_bytes() for p in source.rglob("*") if p.is_file()}
        copied = data.migrate_legacy_data(source)
        self.assertIn(data.DATA_DIR / "bilibili_cookies.json", copied)
        self.assertIn(data.WORD_DIR / "nested" / nested.name, copied)
        self.assertNotIn(new_note, copied)
        self.assertEqual(new_note.read_text(encoding="utf-8"), "new")
        self.assertEqual(old_note.read_text(encoding="utf-8"), "old")
        self.assertEqual(cookie.read_text(encoding="utf-8"), "legacy-account")
        for name in ("main.py", "docs", "unlisted.txt"):
            self.assertFalse((data.USER_DATA_DIR / name).exists())
        after = {str(p.relative_to(source)): p.read_bytes() for p in source.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_migration_rejects_existing_account_set_without_merging(self):
        _, _, data = self.load()
        source = self.temp / "legacy"
        self.write(source / "Data" / "bilibili_cookies.json", "foreign-account")
        self.write(source / "KnowledgeBase" / "foreign.md", "foreign-note")
        for entry in ("Data/config.json", "Data/bilibili_cookies.json", ".cipher_key", "Data/accounts.json"):
            target = self.write(data.USER_DATA_DIR / entry, "existing")
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                data.migrate_legacy_data(source)
            self.assertEqual(target.read_text(encoding="utf-8"), "existing")
            self.assertFalse((data.KNOWLEDGE_BASE_DIR / "foreign.md").exists())
            target.unlink()

    def test_migration_skips_source_and_destination_symlinks(self):
        _, _, data = self.load()
        source = self.temp / "legacy"
        source.mkdir()
        outside = self.temp / "outside"
        secret = self.write(outside / "secret.txt", "do not touch")
        self.symlink(source / "Data", outside)
        (source / "KnowledgeBase").mkdir()
        self.symlink(source / "KnowledgeBase" / "linked.txt", secret)
        self.write(source / "Word" / "old.docx", "old")
        data.USER_DATA_DIR.mkdir()
        self.symlink(data.WORD_DIR, outside)
        # Destination-link escapes fail closed before creating/copying data.
        with self.assertRaises(ValueError):
            data.migrate_legacy_data(source)
        data.WORD_DIR.unlink()
        self.assertEqual(data.migrate_legacy_data(source), [data.WORD_DIR / "old.docx"])
        self.assertFalse((outside / "old.docx").exists())
        self.assertFalse((data.DATA_DIR / "secret.txt").exists())
        self.assertFalse((data.KNOWLEDGE_BASE_DIR / "linked.txt").exists())
        self.assertEqual(secret.read_text(encoding="utf-8"), "do not touch")

    def test_migration_does_not_follow_destination_links_even_inside_profile(self):
        _, _, data = self.load()
        source = self.temp / "legacy"
        self.write(source / "Word" / "note.docx", "source")
        internal = data.USER_DATA_DIR / "other-output"
        internal.mkdir(parents=True)
        self.symlink(data.WORD_DIR, internal)
        self.assertEqual(data.migrate_legacy_data(source), [])
        self.assertFalse((internal / "note.docx").exists())

    def test_migration_rejects_symlink_root_and_overlapping_roots(self):
        _, _, data = self.load()
        source = self.temp / "legacy"
        source.mkdir()
        link = self.temp / "legacy-link"
        self.symlink(link, source)
        with self.assertRaises(ValueError):
            data.migrate_legacy_data(link)
        data.USER_DATA_DIR.mkdir()
        nested = data.USER_DATA_DIR / "legacy"
        nested.mkdir()
        for value in (data.USER_DATA_DIR, nested, data.USER_DATA_DIR.parent):
            with self.subTest(value=value), self.assertRaises(ValueError):
                data.migrate_legacy_data(value)

    def test_backup_dir_ignores_global_and_android_shared_locations(self):
        name, _, data = self.load()
        storage = importlib.import_module(name + ".utils.storage")
        self.assertEqual(storage.get_backup_dir(), data.BACKUPS_DIR)
        self.assertFalse(data.BACKUPS_DIR.exists())

    def test_initial_config_import_rejects_non_object_json_without_rewriting(self):
        for value in (["CONFIG_CONTENT_CANARY"], "CONFIG_CONTENT_CANARY", None):
            with self.subTest(type=type(value).__name__):
                name, _, data = self.load()
                config_file = self.write(data.DATA_DIR / "config.json", json.dumps(value))
                before = config_file.read_bytes()
                with self.assertRaises(ValueError) as caught:
                    self.load_config(name)
                self.assertIn(str(config_file), str(caught.exception))
                self.assertNotIn("CONFIG_CONTENT_CANARY", str(caught.exception))
                self.assertEqual(config_file.read_bytes(), before)
                self.assertNotIn(name + ".core.config", sys.modules)
                self.assertEqual(list(data.DATA_DIR.glob(".private-*.tmp")), [])

    def test_config_knowledge_paths_use_root_and_validate_before_save(self):
        name, _, data = self.load()
        cfg = self.load_config(name)
        self.assertEqual(Path(cfg.DATA_DIR), data.DATA_DIR)
        self.assertEqual(Path(cfg.BACKUP_DIR), data.BACKUPS_DIR)
        self.assertEqual(Path(cfg.resolve_knowledge_base_dir({"knowledge_base_dir": "notes/nested"})),
                         data.USER_DATA_DIR / "notes" / "nested")
        self.assertEqual(Path(cfg.resolve_knowledge_base_dir({"knowledge": {"base_dir": "kb/deep"}})),
                         data.USER_DATA_DIR / "kb" / "deep")
        self.assertEqual(Path(cfg.resolve_knowledge_base_dir({})), data.KNOWLEDGE_BASE_DIR)
        bad = copy.deepcopy(cfg.config)
        bad["knowledge_base_dir"] = str(self.temp / "outside")
        before = Path(cfg.CONFIG_FILE).read_bytes()
        self.assertFalse(cfg.save_config(bad))
        self.assertEqual(Path(cfg.CONFIG_FILE).read_bytes(), before)
        self.assertFalse((self.temp / "outside").exists())

    def test_mindmap_and_document_output_preserve_nested_configured_paths(self):
        name, _, data = self.load()
        self.load_config(name)
        mindmap = importlib.import_module(name + ".services.mindmap_export")
        document = importlib.import_module(name + ".services.document_export")
        note = self.write(data.KNOWLEDGE_BASE_DIR / "note.md", "# Note\n\n- body")
        output = Path(mindmap.export_mindmap(note, cfg={"mindmap": {"output_dir": "maps/nested"}}))
        self.assertEqual(output, data.USER_DATA_DIR / "maps" / "nested" / "note.mindmap.html")
        self.assertTrue(output.is_file())
        self.assertEqual(document._resolve_out_dir("docs/deep"), data.USER_DATA_DIR / "docs" / "deep")
        with patch.object(document, "_read_doc_cfg", return_value={"folder_name": "custom/word"}):
            self.assertEqual(document._resolve_out_dir(), data.USER_DATA_DIR / "custom" / "word")
        for outside in ("../outside", self.temp / "outside"):
            with self.subTest(outside=outside):
                with self.assertRaises(ValueError):
                    mindmap.export_mindmap(note, output_dir=outside)
                with self.assertRaises(ValueError):
                    document._resolve_out_dir(outside)

    def test_mindmap_rejects_final_output_file_symlink(self):
        name, _, data = self.load()
        mindmap = importlib.import_module(name + ".services.mindmap_export")
        note = self.write(data.KNOWLEDGE_BASE_DIR / "note.md", "# Note")
        outside = self.write(self.temp / "outside.html", "keep")
        data.MINDMAPS_DIR.mkdir()
        self.symlink(data.MINDMAPS_DIR / "note.mindmap.html", outside)
        with self.assertRaises(ValueError):
            mindmap.export_mindmap(note)
        self.assertEqual(outside.read_text(encoding="utf-8"), "keep")

    def test_document_mindmap_temporary_file_is_root_owned_and_cleaned(self):
        name, _, data = self.load()
        self.load_config(name)
        document = importlib.import_module(name + ".services.document_export")
        mindmap = importlib.import_module(name + ".services.mindmap_export")
        observed = []

        def export(path):
            path = Path(path)
            self.assertTrue(path.is_relative_to(data.TEMP_DIR))
            self.assertTrue(path.is_file())
            observed.append(path)
            return str(data.MINDMAPS_DIR / "result.html")

        with patch.object(mindmap, "export_mindmap", side_effect=export):
            result = asyncio.run(document.export_video_content("Note", "up", "url", "body", ["mindmap"],
                                                               bvid="../../unsafe"))
        self.assertIn("path", result["mindmap"])
        self.assertEqual(len(observed), 1)
        self.assertFalse(observed[0].exists())

    def test_factory_reset_never_targets_package_outside_root_or_backups_by_default(self):
        name, _, data = self.load()
        reset = importlib.import_module(name + ".core.factory_reset")
        outside = self.write(self.temp / "outside" / "keep.md", "outside")
        package = self.write(self.temp / "package" / "docs" / "keep.md", "package")
        private = self.write(data.DATA_DIR / "private.json", "private")
        backup = self.write(data.BACKUPS_DIR / "keep.json", "backup")
        custom = self.write(data.USER_DATA_DIR / "custom" / "nested" / "note.md", "note")
        kwargs = {
            "user_data_dir": data.USER_DATA_DIR, "data_dir": data.DATA_DIR,
            "project_dir": package.parent.parent, "backup_dir": outside.parent,
            "cipher_key_file": data.USER_DATA_DIR / ".cipher_key",
            "config": {"knowledge_base_dir": "custom/nested", "mindmap": {"output_dir": str(outside.parent)},
                       "document_export": {"output_dir": "backups"},
                       "asr": {"funasr_model_dir": str(package.parent)}},
        }
        grouped = reset.collect_reset_target_groups(**kwargs)
        for paths in grouped.values():
            for path in paths:
                self.assertTrue(path.is_relative_to(data.USER_DATA_DIR))
                self.assertNotEqual(path, data.USER_DATA_DIR)
        self.assertIn(custom.parent, grouped["knowledge_generated"])
        self.assertNotIn(data.BACKUPS_DIR, grouped["knowledge_generated"])
        self.assertEqual(grouped["backup_files"], [data.BACKUPS_DIR])
        result = reset.erase_all_user_data(**kwargs)
        self.assertEqual(result["failures"], [])
        self.assertFalse(private.exists())
        self.assertFalse(custom.exists())
        for path in (outside, package, backup):
            self.assertTrue(path.is_file())
        self.assertTrue(data.DATA_DIR.is_dir())
        self.assertEqual(reset.erase_all_user_data(selected_groups=["backup_files"], **kwargs)["failures"], [])
        self.assertFalse(backup.exists())
        with self.assertRaises(ValueError):
            reset.collect_reset_target_groups(user_data_dir=self.temp / "other")

    def test_factory_reset_preview_and_delete_do_not_follow_symlinks(self):
        name, _, data = self.load()
        reset = importlib.import_module(name + ".core.factory_reset")
        outside = self.write(self.temp / "outside" / "keep.md", "keep")
        data.USER_DATA_DIR.mkdir()
        self.symlink(data.KNOWLEDGE_BASE_DIR, outside.parent)
        self.assertNotIn(data.KNOWLEDGE_BASE_DIR, reset.collect_reset_targets())
        self.assertEqual(reset._path_usage(data.KNOWLEDGE_BASE_DIR), (0, 0))
        reset.erase_all_user_data(selected_groups=["knowledge_generated"])
        self.assertEqual(outside.read_text(encoding="utf-8"), "keep")

    def test_modular_settings_and_video_download_directory_use_profile(self):
        import ast

        name, _, data = self.load()
        settings = importlib.import_module(name + ".xingye_bot.settings")
        settings.write_runtime_config({"video": {"download_dir": "nested/downloads"}})
        self.assertEqual(settings.read_runtime_config()["video"]["download_dir"], "nested/downloads")
        tree = ast.parse((ROOT / "xingye_bot" / "video_modes.py").read_text(encoding="utf-8"))
        klass = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "VideoUnderstanding")
        getter = copy.deepcopy(next(node for node in klass.body if isinstance(node, ast.FunctionDef) and node.name == "download_root"))
        getter.decorator_list = []
        namespace = {"Path": Path, "CACHE_DIR": data.DATA_DIR / "video_cache",
                     "resolve_output_path": data.resolve_output_path}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[getter], type_ignores=[])), "video_modes.py", "exec"), namespace)
        owner = SimpleNamespace(settings=SimpleNamespace(video_download_dir="nested/downloads"))
        self.assertEqual(namespace["download_root"](owner), data.USER_DATA_DIR / "nested" / "downloads")
        owner.settings.video_download_dir = str(self.temp / "outside")
        with self.assertRaises(ValueError):
            namespace["download_root"](owner)
        outside = self.write(self.temp / "foreign-settings.json", '{"api": {"unified_api_key": "foreign"}}')
        settings.CONFIG_FILE.unlink()
        self.symlink(settings.CONFIG_FILE, outside)
        with self.assertRaises(ValueError):
            settings.read_runtime_config()
        self.assertIn("foreign", outside.read_text(encoding="utf-8"))

    def test_asr_models_audio_and_whisper_cache_stay_under_root(self):
        name, _, data = self.load()
        self.load_config(name)
        asr = importlib.import_module(name + ".xingye_bot.asr_engine")
        engine = asr.ASREngine()
        self.assertEqual(Path(engine._get_model_dir()), data.MODELS_DIR / "asr")
        engine.funasr_model_dir = "custom/models/nested"
        self.assertEqual(Path(engine._get_model_dir()), data.USER_DATA_DIR / "custom" / "models" / "nested")
        engine.funasr_model_dir = str(self.temp / "foreign-models")
        with self.assertRaises(ValueError):
            engine._get_model_dir()
        video = self.write(self.temp / "read-only-input" / "video.mp4", "video")

        def ffmpeg(command, **kwargs):
            self.write(Path(command[-1]), "a" * 2048)

        with patch.object(engine, "_find_ffmpeg", return_value=sys.executable), patch.object(
            asr.subprocess, "run", side_effect=ffmpeg
        ):
            audio, _ = engine.extract_audio(video)
            self.assertEqual(audio, data.TEMP_DIR / "asr" / "video_audio.wav")
            self.assertFalse((video.parent / "video_audio.wav").exists())
            with self.assertRaises(ValueError):
                engine.extract_audio(video, self.temp / "outside-audio")
        whisper = SimpleNamespace(load_model=Mock(return_value=SimpleNamespace(
            transcribe=Mock(return_value={"segments": [{"text": "hello", "confidence": 1.0}]})
        )))
        with patch.object(asr, "_get_whisper", return_value=whisper), patch.object(
            engine, "_get_audio_duration", return_value=1
        ), patch("builtins.print"):
            self.assertTrue(engine.transcribe_whisper(audio).success)
        self.assertEqual(Path(whisper.load_model.call_args.kwargs["download_root"]), data.CACHE_DIR / "whisper")

    def test_plugin_funasr_initialization_does_not_change_streams_or_environment(self):
        name, _, data = self.load()
        self.load_config(name)
        asr = importlib.import_module(name + ".xingye_bot.asr_engine")
        engine = asr.ASREngine({"backend": "funasr"})
        executable = self.write(data.TEMP_DIR / "ffmpeg.exe", "fake executable")
        options = []
        stdout, stderr = sys.stdout, sys.stderr
        before = dict(os.environ)

        def model(**kwargs):
            options.append(kwargs)
            self.assertEqual(dict(os.environ), before)
            self.assertIs(sys.stdout, stdout)
            self.assertIs(sys.stderr, stderr)
            return SimpleNamespace(kwargs={})

        with patch.object(asr, "_get_funasr", return_value=model), patch.object(
            engine, "_find_ffmpeg", return_value=str(executable)
        ), patch.object(engine, "_check_cuda", return_value=False):
            engine._load_funasr_model()
            with asr._asr_output_context():
                self.assertIs(sys.stdout, stdout)
                self.assertIs(sys.stderr, stderr)
        self.assertEqual(dict(os.environ), before)
        self.assertEqual(Path(options[0]["cache_dir"]), Path(engine._get_model_dir()))
        self.assertTrue(options[0]["disable_pbar"])

    def test_asr_speaker_cache_uses_profile(self):
        name, _, data = self.load()
        self.load_config(name)
        asr = importlib.import_module(name + ".xingye_bot.asr_engine")
        engine = asr.ASREngine({"backend": "whisper"})
        pipeline = Mock(return_value=SimpleNamespace(itertracks=lambda **kwargs: []))
        factory = SimpleNamespace(from_pretrained=Mock(return_value=pipeline))
        audio = self.write(data.TEMP_DIR / "audio.wav", "audio")
        with patch.object(asr, "_get_pyannote_pipeline", return_value=factory):
            self.assertEqual(engine.separate_speakers(audio, []), [])
        self.assertEqual(Path(factory.from_pretrained.call_args.kwargs["cache_dir"]),
                         data.CACHE_DIR / "pyannote")

    def test_qr_login_only_cleans_its_unique_file_on_success_timeout_and_cancel(self):
        name, _, data = self.load()
        self.load_config(name)
        saved = []
        qrcode = ModuleType("qrcode")
        qrcode.constants = SimpleNamespace(ERROR_CORRECT_H=1, ERROR_CORRECT_L=2)

        class QR:
            def __init__(self, **kwargs):
                pass

            def add_data(self, value):
                pass

            def make(self, **kwargs):
                pass

            def make_image(self, **kwargs):
                return self

            def save(qr, path):
                path = Path(path)
                self.assertTrue(path.is_relative_to(data.QR_CODES_DIR))
                self.assertTrue(path.exists())
                self.assertNotEqual(path.name, "bilibili_login_qr.png")
                path.write_bytes(b"fixture qr")
                saved.append(path)

            def print_ascii(self, **kwargs):
                pass

        qrcode.QRCode = QR
        sys.modules["qrcode"] = qrcode
        bilibili = ModuleType("bilibili_api")
        bilibili.Credential = object
        sys.modules["bilibili_api"] = bilibili
        login = ModuleType("bilibili_api.login_v2")
        events = SimpleNamespace(DONE="done", TIMEOUT="timeout", SCAN="scan", CONF="confirm")
        login.QrCodeLoginEvents = events
        status = [events.DONE]

        class Login:
            async def generate_qrcode(self):
                self._QrCodeLogin__qr_key = "fixture-key"
                self._QrCodeLogin__qr_link = "https://fixture.invalid/qr"

            async def check_state(self):
                if status[0] == "cancel":
                    raise asyncio.CancelledError()
                return status[0]

            def get_credential(self):
                return SimpleNamespace(sessdata="fixture-session", bili_jct="fixture-csrf", dedeuserid="1")

        login.QrCodeLogin = Login
        sys.modules[login.__name__] = login
        auth = importlib.import_module(name + ".api.auth")
        unrelated = self.write(data.QR_CODES_DIR / "another-login.png", "do not delete")
        with patch("builtins.print", side_effect=AssertionError("plugin printed login progress")):
            self.assertTrue(asyncio.run(auth.login_bilibili()))
            status[0] = events.TIMEOUT
            self.assertFalse(asyncio.run(auth.login_bilibili()))
            status[0] = "cancel"
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(auth.login_bilibili())
        self.assertEqual(len(set(saved)), 3)
        self.assertTrue(all(not path.exists() for path in saved))
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "do not delete")
        self.assertEqual(json.loads((data.DATA_DIR / "bilibili_cookies.json").read_text())["DedeUserID"], "1")


if __name__ == "__main__":
    unittest.main()
