"""依赖预检回归测试：无需安装 AstrBot 或业务依赖。

运行：python -m unittest discover -s tests -v
"""
import asyncio
import builtins
from contextlib import contextmanager
import importlib.metadata
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = ROOT / "main.py"
PACKAGE = "_dependency_test_plugin"
CORE_VERSIONS = {
    "openai": "1.78.0",
    "pydantic": "2.12.5",
    "httpx": "0.28.1",
    "qrcode": "8.2",
    "python-docx": "1.2.0",
    "reportlab": "5.0.0",
    "pillow": "11.2.1",
}
CORE_MODULES = {"openai", "pydantic", "httpx", "qrcode", "docx", "reportlab", "PIL"}


@contextmanager
def dependency_environment(*, missing=(), versions=None, unknown=()):
    original_import = builtins.__import__
    installed = CORE_VERSIONS | (versions or {})

    def import_module(name, *args, **kwargs):
        if name in missing:
            raise ModuleNotFoundError(name)
        if name in CORE_MODULES or name == "socksio":
            return ModuleType(name)
        return original_import(name, *args, **kwargs)

    def version(name):
        if name in unknown:
            raise importlib.metadata.PackageNotFoundError(name)
        return installed[name]

    with patch("builtins.__import__", side_effect=import_module), patch(
        "importlib.metadata.version", side_effect=version
    ):
        yield


def load_main():
    logger = Mock()
    api = ModuleType("astrbot.api")
    api.logger = logger
    event = ModuleType("astrbot.api.event")
    event.AstrMessageEvent = object
    event.filter = SimpleNamespace(command=lambda name: lambda func: func)
    star = ModuleType("astrbot.api.star")
    star.Context = object
    star.Star = type("Star", (), {"__init__": lambda self, context: None})
    package = ModuleType(PACKAGE)
    package.__path__ = [str(ROOT)]
    package.__package__ = PACKAGE
    modules = {
        PACKAGE: package,
        "astrbot": ModuleType("astrbot"),
        "astrbot.api": api,
        "astrbot.api.event": event,
        "astrbot.api.star": star,
    }
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.main", MAIN_PATH)
    module = importlib.util.module_from_spec(spec)
    modules[spec.name] = module
    with patch.dict(sys.modules, modules), dependency_environment():
        spec.loader.exec_module(module)
    logger.reset_mock()
    return module, logger


class CoreDependencyTests(unittest.TestCase):
    def setUp(self):
        self.main, self.logger = load_main()

    def test_loading_main_restores_astrbot_modules_and_keeps_business_imports_lazy(self):
        names = (
            "astrbot", "astrbot.api", "astrbot.api.event", "astrbot.api.star",
            "core.config", "cli.app", "brain.agent_brain", PACKAGE,
            f"{PACKAGE}.main", f"{PACKAGE}.core.config",
            f"{PACKAGE}.cli.app", f"{PACKAGE}.brain.agent_brain",
        )
        missing = object()
        before = {name: sys.modules.get(name, missing) for name in names}
        path_before = sys.path.copy()
        main, _ = load_main()
        self.assertEqual(main.__package__, PACKAGE)
        self.assertEqual(sys.path, path_before)
        for name, original in before.items():
            self.assertIs(sys.modules.get(name, missing), original, name)

    def test_core_minimums_are_accepted(self):
        with dependency_environment():
            self.assertEqual(self.main._check_core_dependencies(), [])

    def test_astrbot_4293_openai_version_is_accepted(self):
        with dependency_environment(versions={"openai": "2.46.0"}):
            self.assertEqual(self.main._check_core_dependencies(), [])

    def test_missing_socksio_only_warns(self):
        with dependency_environment(missing={"socksio"}):
            self.assertEqual(self.main._check_core_dependencies(), [])
        self.logger.warning.assert_called_once()
        self.assertIn("socksio", self.logger.warning.call_args.args[0])
        self.assertIn("HTTP", self.logger.warning.call_args.args[0])
        self.logger.error.assert_not_called()

    def test_missing_core_module_blocks(self):
        with dependency_environment(missing={"pydantic"}):
            problems = self.main._check_core_dependencies()
        self.assertEqual(len(problems), 1)
        self.assertIn("pydantic", problems[0])
        self.assertIn("缺失", problems[0])

    def test_missing_distribution_metadata_blocks(self):
        with dependency_environment(unknown={"openai"}):
            problems = self.main._check_core_dependencies()
        self.assertEqual(len(problems), 1)
        self.assertIn("无法确定版本", problems[0])

    def test_pep440_version_ordering(self):
        cases = {
            "1.77.9": False,
            "1.78.0": True,
            "1.78.0rc1": False,
            "1.78.0b1": False,
            "1.78.0.dev0": False,
            "1.78.0.post1": True,
            "1.78.0+vendor.1": True,
            "1!1.0": True,
            "0!1.77.9": False,
        }
        for installed, accepted in cases.items():
            with self.subTest(installed=installed), dependency_environment(
                versions={"openai": installed}
            ):
                problems = self.main._check_core_dependencies()
                self.assertEqual(not problems, accepted)
                if not accepted:
                    self.assertIn(installed, problems[0])
                    self.assertIn(">=1.78.0", problems[0])

    def test_invalid_version_is_reported(self):
        with dependency_environment(versions={"openai": "not-a-version"}):
            problems = self.main._check_core_dependencies()
        self.assertEqual(len(problems), 1)
        self.assertIn("无法解析版本", problems[0])
        self.assertIn("not-a-version", problems[0])

    def test_missing_packaging_is_reported_without_crashing(self):
        with dependency_environment(missing={"packaging.version"}):
            problems = self.main._check_core_dependencies()
        self.assertEqual(len(problems), 1)
        self.assertIn("packaging", problems[0])

    def test_dependency_repair_is_detected_on_next_check(self):
        with dependency_environment(missing={"openai"}):
            self.assertTrue(self.main._check_core_dependencies())
        with dependency_environment():
            self.assertEqual(self.main._check_core_dependencies(), [])

    def test_documentation_references_single_version_source(self):
        for filename in ("requirements.txt", "README.md"):
            text = (ROOT / filename).read_text(encoding="utf-8")
            self.assertIn("_CORE_PROVIDED_DEPENDENCIES", text)
            for package, minimum in self.main._CORE_PROVIDED_DEPENDENCIES.values():
                self.assertNotIn(f"{package}>={minimum}", text)


class StartBotDependencyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.main, self.logger = load_main()
        self.bot = self.main.BilibiliLearningBot(None)
        self.event = SimpleNamespace(plain_result=lambda text: text)
        self.cli = SimpleNamespace(_release_bot_lock=Mock())

    async def asyncTearDown(self):
        task = self.main._brain_task
        if task is not None:
            task.cancel()
            with patch.object(self.main, "_cli_app", return_value=self.cli):
                await asyncio.gather(task, return_exceptions=True)
                await asyncio.sleep(0)

    async def start(self):
        return [reply async for reply in self.bot.start_bot(self.event)]

    @contextmanager
    def brain_environment(self):
        async def run():
            await asyncio.Event().wait()

        prefix = self.main.__package__
        package = ModuleType(prefix)
        package.__path__ = [str(ROOT)]
        package.__package__ = prefix
        core = ModuleType(f"{prefix}.core")
        core.__path__ = [str(ROOT / "core")]
        brain_package = ModuleType(f"{prefix}.brain")
        brain_package.__path__ = [str(ROOT / "brain")]
        brain = ModuleType(f"{prefix}.brain.agent_brain")
        brain.AgentBrain = Mock(return_value=SimpleNamespace(run=run))
        package.core = core
        package.brain = brain_package
        brain_package.agent_brain = brain
        with patch.dict(sys.modules, {
            prefix: package, f"{prefix}.main": self.main,
            core.__name__: core, brain_package.__name__: brain_package,
            brain.__name__: brain,
        }):
            yield brain.AgentBrain

    async def test_panel_configuration_is_applied_before_brain_creation(self):
        panel = {"use_astrbot_config": True, "api": {"model_brain": "test-model"}}
        self.bot.config = panel
        bridge = ModuleType(f"{self.main.__package__}.core.astrbot_config_bridge")
        bridge.apply_astrbot_config = Mock()
        with dependency_environment(), self.brain_environment() as factory, patch.dict(
            sys.modules, {bridge.__name__: bridge}
        ):
            factory.side_effect = lambda: (
                bridge.apply_astrbot_config.assert_called_once_with(panel)
                or SimpleNamespace(run=lambda: asyncio.Event().wait())
            )
            replies = await self.start()
        self.assertIn("已在后台启动", replies[0])

    async def test_invalid_panel_configuration_prevents_brain_creation(self):
        self.bot.config = {"use_astrbot_config": True}
        bridge = ModuleType(f"{self.main.__package__}.core.astrbot_config_bridge")
        bridge.apply_astrbot_config = Mock(side_effect=ValueError("invalid configuration"))
        with dependency_environment(), self.brain_environment() as factory, patch.dict(
            sys.modules, {bridge.__name__: bridge}
        ):
            replies = await self.start()
        self.assertIn("初始化失败", replies[0])
        factory.assert_not_called()

    async def test_missing_socksio_does_not_block_background_start(self):
        with dependency_environment(missing={"socksio"}), self.brain_environment():
            replies = await self.start()
        self.assertIn("已在后台启动", replies[0])
        self.assertIsNotNone(self.main._brain_task)
        self.logger.warning.assert_called_once()

    async def test_missing_required_dependency_blocks_start(self):
        with dependency_environment(missing={"pydantic"}), self.brain_environment() as factory:
            replies = await self.start()
        self.assertIn("pydantic", replies[0])
        self.assertIsNone(self.main._brain_task)
        factory.assert_not_called()

    async def test_repaired_dependency_allows_retry_without_reload(self):
        with dependency_environment(missing={"openai"}):
            replies = await self.start()
        self.assertIn("openai", replies[0])
        with dependency_environment(), self.brain_environment():
            replies = await self.start()
        self.assertIn("已在后台启动", replies[0])
        self.assertIsNotNone(self.main._brain_task)


if __name__ == "__main__":
    unittest.main()
