"""Single-instance lock regressions without loading real configuration or user data."""
import importlib.util
from contextlib import redirect_stdout
from io import StringIO
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_utils_lock_test_plugin"


class UtilsLockTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="utils-lock-tests-")
        self.addCleanup(temporary.cleanup)
        self.data_dir = Path(temporary.name) / "Data"
        self.lock_path = self.data_dir / "bot.lock"
        self.other_pid = os.getpid() + 100000
        modules = {}
        for name in (PACKAGE, f"{PACKAGE}.core", f"{PACKAGE}.utils"):
            package = ModuleType(name)
            package.__path__ = []  # Never fall through to real core/user_data imports.
            modules[name] = package
        config = ModuleType(f"{PACKAGE}.core.config")
        config.DATA_DIR = str(self.data_dir)
        modules[config.__name__] = config
        colorama = ModuleType("colorama")
        colorama.Fore = SimpleNamespace(RED="", YELLOW="")
        colorama.Style = SimpleNamespace(RESET_ALL="")
        modules["colorama"] = colorama
        spec = importlib.util.spec_from_file_location(
            f"{PACKAGE}.utils.lock", ROOT / "utils" / "lock.py"
        )
        self.lock = importlib.util.module_from_spec(spec)
        modules[spec.name] = self.lock
        module_patch = patch.dict(sys.modules, modules)
        module_patch.start()
        self.addCleanup(module_patch.stop)
        with patch.object(sys, "dont_write_bytecode", True):
            spec.loader.exec_module(self.lock)
        self.exit_callbacks = []
        # Do not register real interpreter-shutdown callbacks against test paths.
        self.lock.atexit = SimpleNamespace(register=self.exit_callbacks.append)

    def write_lock(self, pid):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.lock_path.write_text(str(pid), encoding="utf-8")

    def test_missing_lock_status_does_not_create_user_data(self):
        self.assertEqual(
            self.lock.bot_lock_status(clean_stale=True),
            {"locked": False, "pid": None, "stale": False},
        )
        self.assertFalse(self.data_dir.exists())
        self.assertNotIn(f"{PACKAGE}.core.user_data", sys.modules)

    def test_windows_without_psutil_uses_non_destructive_native_probe(self):
        self.write_lock(self.other_pid)
        fake_os = SimpleNamespace(name="nt", path=os.path, kill=Mock(), remove=Mock())
        with patch.object(self.lock, "os", fake_os), patch.dict(sys.modules, {"psutil": None}), \
                patch.object(self.lock, "_windows_pid_is_running", return_value=True) as probe:
            self.assertTrue(self.lock._pid_is_running(self.other_pid))
            self.assertEqual(
                self.lock.bot_lock_status(clean_stale=True),
                {"locked": True, "pid": self.other_pid, "stale": False},
            )
            probe.return_value = False
            self.assertFalse(self.lock._pid_is_running(self.other_pid))
        fake_os.kill.assert_not_called()
        fake_os.remove.assert_not_called()
        self.assertEqual(self.lock_path.read_text(encoding="utf-8"), str(self.other_pid))

    @unittest.skipUnless(os.name == "nt", "Windows process handles only")
    def test_native_windows_probe_recognizes_live_and_exited_process(self):
        import subprocess

        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            self.assertTrue(self.lock._windows_pid_is_running(process.pid))
        finally:
            process.terminate()
            process.wait(timeout=10)
        self.assertFalse(self.lock._windows_pid_is_running(process.pid))

    def test_empty_lock_is_not_deleted_during_another_process_acquisition(self):
        self.write_lock("")
        with redirect_stdout(StringIO()):
            self.assertEqual(self.lock.bot_lock_status(clean_stale=True),
                             {"locked": True, "pid": None, "stale": False})
            self.assertFalse(self.lock._acquire_bot_lock())
        self.assertTrue(self.lock_path.exists())
        self.assertEqual(self.lock_path.read_text(encoding="utf-8"), "")
        self.assertFalse(self.lock._bot_lock_acquired)

    def test_windows_with_psutil_uses_pid_exists_without_signaling(self):
        psutil = ModuleType("psutil")
        psutil.pid_exists = Mock(return_value=False)
        fake_os = SimpleNamespace(name="nt", kill=Mock())
        with patch.object(self.lock, "os", fake_os), patch.dict(sys.modules, {"psutil": psutil}):
            self.assertFalse(self.lock._pid_is_running(self.other_pid))
            psutil.pid_exists.assert_called_once_with(self.other_pid)
            psutil.pid_exists.return_value = True
            self.assertTrue(self.lock._pid_is_running(self.other_pid))
        fake_os.kill.assert_not_called()

    def test_posix_probe_handles_missing_and_inaccessible_processes(self):
        for error, expected in ((None, True), (ProcessLookupError(), False),
                                (PermissionError(), True), (OSError(), False)):
            with self.subTest(error=error):
                fake_os = SimpleNamespace(name="posix", kill=Mock(side_effect=error))
                with patch.object(self.lock, "os", fake_os):
                    self.assertEqual(self.lock._pid_is_running(self.other_pid), expected)
                fake_os.kill.assert_called_once_with(self.other_pid, 0)

    def test_stale_lock_is_reported_then_cleaned_only_when_requested(self):
        self.write_lock(self.other_pid)
        with patch.object(self.lock, "_pid_is_running", return_value=False):
            expected = {"locked": False, "pid": self.other_pid, "stale": True}
            self.assertEqual(self.lock.bot_lock_status(), expected)
            self.assertTrue(self.lock_path.exists())
            self.assertEqual(self.lock.bot_lock_status(clean_stale=True), expected)
        self.assertFalse(self.lock_path.exists())

    def test_stale_lock_can_be_acquired_and_owned_lock_released(self):
        self.write_lock(self.other_pid)
        with patch.object(self.lock, "_pid_is_running", return_value=False) as probe, \
                redirect_stdout(StringIO()):
            self.assertTrue(self.lock._acquire_bot_lock())
        probe.assert_called_once_with(self.other_pid)
        self.assertEqual(self.lock_path.read_text(encoding="utf-8"), str(os.getpid()))
        self.assertTrue(self.lock._bot_lock_acquired)
        self.assertEqual(self.exit_callbacks, [self.lock._release_bot_lock])
        self.lock._release_bot_lock()
        self.assertFalse(self.lock_path.exists())
        self.assertFalse(self.lock._bot_lock_acquired)
        self.lock._release_bot_lock()  # Releasing twice is harmless.

    def test_active_other_lock_is_not_overwritten(self):
        self.write_lock(self.other_pid)
        original = self.lock_path.read_bytes()
        with patch.object(self.lock, "_pid_is_running", return_value=True), \
                redirect_stdout(StringIO()):
            self.assertFalse(self.lock._acquire_bot_lock())
        self.assertEqual(self.lock_path.read_bytes(), original)
        self.assertFalse(self.lock._bot_lock_acquired)
        self.assertEqual(self.exit_callbacks, [])

    def test_release_without_acquisition_preserves_lock_even_for_own_pid(self):
        self.write_lock(os.getpid())
        self.lock._release_bot_lock()
        self.assertEqual(self.lock_path.read_text(encoding="utf-8"), str(os.getpid()))
        self.assertFalse(self.lock._bot_lock_acquired)

    def test_release_preserves_lock_replaced_by_another_owner(self):
        self.assertTrue(self.lock._acquire_bot_lock())
        self.write_lock(self.other_pid)
        self.lock._release_bot_lock()
        self.assertEqual(self.lock_path.read_text(encoding="utf-8"), str(self.other_pid))
        self.assertFalse(self.lock._bot_lock_acquired)


if __name__ == "__main__":
    unittest.main()
