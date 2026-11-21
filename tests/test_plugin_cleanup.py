"""Keep approved plugin dependencies and review rules while retiring unused shells."""
import ast
from datetime import datetime
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock
import uuid


ROOT = Path(__file__).resolve().parents[1]
RETIRED = (
    "services/biliclaw/__init__.py", "services/home_chat.py", "services/research_archive.py",
    "utils/web_launcher.py", "utils/desktop_notifications.py", "xingye_bot/background.py",
    "xingye_bot/diary.py", "xingye_bot/evolution.py", "xingye_bot/memory.py", "xingye_bot/owner.py",
    "xingye_bot/proactive.py", "xingye_bot/skills.py", "xingye_bot/web_search.py", "xingye_bot/video_asr.py",
)
PROTECTED_BLOBS = {
    ".apertureprism/rules/README.md": "0f52ab5541f144e7e984ed4bf9da251ce9614de9",
    "security/guard.py": "934093c4d0172f819946c580cdf73840fa958a3e",
    "xingye_bot/safety.py": "ed0bff2233c8bb6da564bd3946568d0d2050e37e",
    "xingye_bot/bilibili_ops.py": "4333bdd445db8eeaf2988da0734c1284ece52911",
}


class PluginCleanupTests(unittest.TestCase):
    def test_retired_files_are_not_distributed(self):
        for relative in RETIRED:
            with self.subTest(file=relative):
                self.assertFalse((ROOT / relative).exists())

    def test_review_and_safety_rules_match_protected_baseline(self):
        for relative, expected in PROTECTED_BLOBS.items():
            with self.subTest(file=relative):
                # Git normalizes text checkouts; compare the protected blob independent of CRLF.
                content = (ROOT / relative).read_bytes().replace(b"\r\n", b"\n")
                blob = b"blob " + str(len(content)).encode("ascii") + b"\0" + content
                self.assertEqual(hashlib.sha1(blob).hexdigest(), expected)

    def test_cli_has_no_web_launcher_or_private_default_copy(self):
        source = (ROOT / "cli/app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertNotIn("open_web_panel", names)
        self.assertNotIn("BILI_DISCLAIMER_SKIP", source)
        self.assertNotIn("desktop notifications", source)
        self.assertNotIn("web_panel.py", source)
        default = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == "DEFAULT_CONFIG"
                               for target in node.targets))
        self.assertIsInstance(default, ast.Call)
        self.assertEqual(getattr(default.func, "id", ""), "deepcopy")
        self.assertIsInstance(default.args[0], ast.Name)

    def test_retained_modules_and_preview_are_still_present(self):
        for relative in ("main.py", "_conf_schema.json", "metadata.yaml", "LICENSE", "cli/app.py",
                         "core/user_data.py", "utils/display.py", "utils/lock.py", "utils/helpers.py",
                         "utils/storage.py", "utils/subtitles.py", "xingye_bot/llm.py",
                         "xingye_bot/settings.py", "xingye_bot/state.py", "xingye_bot/asr_engine.py",
                         "xingye_bot/video_modes.py", "xingye_bot/kb_search.py", "ob_bridge/client.py",
                         "services/like_review.py", "services/video_to_ppt.py"):
            with self.subTest(file=relative):
                self.assertTrue((ROOT / relative).is_file())
        preview = (ROOT / "services/video_to_ppt.py").read_text(encoding="utf-8")
        self.assertIn("def start_preview_server", preview)

    def test_legacy_reset_delegates_without_executing_old_web_or_source_deletion(self):
        tree = ast.parse((ROOT / "cli/app.py").read_text(encoding="utf-8"))
        reset = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name == "factory_reset_all")
        reset.decorator_list = []
        operation = Mock(return_value="confirmed-result")
        namespace = {"quick_factory_reset_all": operation}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[reset], type_ignores=[])),
                     "cli/app.py", "exec"), namespace)
        self.assertEqual(namespace["factory_reset_all"](), "confirmed-result")
        operation.assert_called_once_with()

    def test_review_proposal_keeps_pending_and_audit_without_desktop_or_payload_log(self):
        source = (ROOT / "services/like_review.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        # Real module body; only imports are supplied by this isolated fixture.
        body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
        logger = Mock()
        namespace = {"json": json, "threading": threading, "uuid": uuid,
                     "datetime": datetime, "Path": Path, "log": logger}
        exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])),
                     "services/like_review.py", "exec"), namespace)
        self.assertNotIn("desktop_notification", namespace["default_review_settings"]())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inbox = namespace["ActionReviewInbox"](root)
            row = inbox.propose("coin", "PRIVATE_TITLE_CANARY", "PRIVATE_SUMMARY_CANARY",
                                payload={"bvid": "PRIVATE_PAYLOAD_CANARY"})
            self.assertEqual(row["status"], "pending")
            self.assertEqual(inbox.list()[0]["id"], row["id"])
            self.assertFalse((root / "desktop_notifications.jsonl").exists())
            self.assertEqual(inbox.decide(row["id"], "rejected")["status"], "rejected")
            self.assertTrue(inbox.audit())
        logger.assert_called_once()
        self.assertNotIn("CANARY", str(logger.call_args))

    def test_web_account_projection_and_obsolete_dependencies_are_gone(self):
        source = (ROOT / "xingye_bot/settings.py").read_text(encoding="utf-8")
        for marker in ("panel_password", "BILI_LEARNING_PANEL_PASSWORD", "def public_config("):
            self.assertNotIn(marker, source)
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        for obsolete in ("pystray", "flask-cors"):
            self.assertNotIn(obsolete, requirements)
        self.assertIn("flask>=", requirements)


if __name__ == "__main__":
    unittest.main()
