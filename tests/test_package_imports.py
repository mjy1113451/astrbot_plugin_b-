"""Issue #39: AstrBot package loading without exposing the repo on sys.path.

Only the package entry points and config bridge are real. Business modules are
stubbed by their qualified names; full business-import integrity is tested
separately. Run with stdlib only:
python -B -m unittest discover -s tests -p test_package_imports.py -v
"""
import asyncio
import builtins
from contextlib import ExitStack, contextmanager
from copy import deepcopy
import importlib
from importlib.abc import MetaPathFinder
import importlib.metadata
from importlib.machinery import ModuleSpec
import importlib.util
from pathlib import Path
import runpy
import shutil
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_A = "data.plugins.fixture_a"
PACKAGE_B = "data.plugins.fixture_b"
PACKAGES = (PACKAGE_A, PACKAGE_B)
BARE_ROOTS = {
    "api", "brain", "cli", "core", "editdistance", "knowledge", "main",
    "ob_bridge", "persona", "security", "services", "utils", "xingye_bot",
}
CORE_VERSIONS = {
    "openai": "1.78.0", "pydantic": "2.12.5", "httpx": "0.28.1",
    "qrcode": "8.2", "python-docx": "1.2.0", "reportlab": "5.0.0",
    "pillow": "11.2.1",
}


def _module(name, paths=None):
    module = ModuleType(name)
    module.__package__ = name if paths is not None else name.rpartition(".")[0]
    module.__spec__ = ModuleSpec(name, loader=None, is_package=paths is not None)
    if paths is not None:
        module.__path__ = list(paths)
        module.__spec__.submodule_search_locations = module.__path__
    return module


class _InvalidVersion(ValueError):
    pass


class _Version:
    """Numeric version stand-in; PEP 440 itself is not under test here."""

    def __init__(self, value):
        try:
            self.release = tuple(int(part) for part in value.split("."))
        except ValueError as exc:
            raise _InvalidVersion(value) from exc

    def __lt__(self, other):
        return self.release < other.release


class _BusinessImportGuard(MetaPathFinder):
    def __init__(self, environment):
        self.environment = environment

    def find_spec(self, fullname, path=None, target=None):
        is_bare = fullname.partition(".")[0] in BARE_ROOTS
        is_business = any(fullname.startswith(package + ".")
                          for package in self.environment.installs)
        if (is_bare or is_business) and fullname not in self.environment.real_modules:
            self.environment.unexpected_imports.append(fullname)
            raise AssertionError(f"Unstubbed business import: {fullname}")
        return None


class _PackageEnvironment:
    def __init__(self, second_install):
        self.installs = {PACKAGE_A: ROOT, PACKAGE_B: second_install}
        self.real_modules = {package + ".main" for package in PACKAGES}
        self.unexpected_imports = []
        self.loaded_mains = []
        self.logger = Mock()
        self.original_import = builtins.__import__

    def install(self, module):
        sys.modules[module.__name__] = module
        parent_name, _, child = module.__name__.rpartition(".")
        parent = sys.modules.get(parent_name)
        if parent is not None:
            setattr(parent, child, module)
        return module

    def guarded_import(self, name, globals=None, locals=None, fromlist=(), level=0):
        if level == 0 and name.partition(".")[0] in BARE_ROOTS:
            self.unexpected_imports.append(name)
            raise AssertionError(f"Bare business import: {name}")
        return self.original_import(name, globals, locals, fromlist, level)

    def install_dependencies(self):
        self.install(_module("astrbot", []))
        api = self.install(_module("astrbot.api", []))
        api.logger = self.logger
        event = self.install(_module("astrbot.api.event"))
        event.AstrMessageEvent = object
        event.filter = SimpleNamespace(command=lambda name: lambda func: func)
        star = self.install(_module("astrbot.api.star"))
        star.Context = object
        star.Star = type("Star", (), {"__init__": lambda self, context: None})
        for name in ("openai", "pydantic", "httpx", "qrcode", "docx", "reportlab",
                     "PIL", "socksio"):
            self.install(_module(name))
        self.install(_module("packaging", []))
        version = self.install(_module("packaging.version"))
        version.Version = _Version
        version.InvalidVersion = _InvalidVersion

    def install_foreign_modules(self):
        # These are unrelated host packages, never fallback business stubs.
        for name in ("cli", "core", "utils"):
            foreign = self.install(_module(name, []))
            foreign.origin = "unrelated-host-package"
        self.foreign_cli = self.install(_module("cli.app"))
        self.foreign_cli.config = {"owner": "foreign-cli", "nested": {"keep": [1]}}
        self.foreign_cli._release_bot_lock = Mock()
        self.foreign_config = self.install(_module("core.config"))
        self.foreign_config.config = {"owner": "foreign-core"}
        self.foreign_bridge = self.install(_module("core.astrbot_config_bridge"))
        self.foreign_bridge.apply_astrbot_config = Mock()
        self.foreign_display = self.install(_module("utils.display"))
        self.foreign_display.log = Mock()
        self.foreign_cli_config = deepcopy(self.foreign_cli.config)
        self.foreign_core_config = deepcopy(self.foreign_config.config)

    def load_main(self, package):
        if package not in sys.modules:
            root = self.installs[package]
            spec = importlib.util.spec_from_file_location(
                package, root / "__init__.py", submodule_search_locations=[str(root)]
            )
            module = self.install(importlib.util.module_from_spec(spec))
            spec.loader.exec_module(module)
        main = importlib.import_module(package + ".main")
        self.loaded_mains.append(main)
        return main

    def install_package(self, package, child):
        name = package + "." + child
        if name not in sys.modules:
            self.install(_module(name, [str(self.installs[package] / child)]))

    def install_cli(self, package):
        self.install_package(package, "cli")
        app = self.install(_module(package + ".cli.app"))
        app.config = {"owner": package, "nested": {"keep": [package]}}
        app._release_bot_lock = Mock()
        return app

    def install_runtime(self, package, *, with_bridge=True):
        app = self.install_cli(package)
        self.install_package(package, "brain")
        finished = asyncio.Event()

        async def run():
            await finished.wait()

        brain = self.install(_module(package + ".brain.agent_brain"))
        brain.AgentBrain = Mock(return_value=SimpleNamespace(run=run))
        bridge = None
        if with_bridge:
            self.install_package(package, "core")
            bridge = self.install(_module(package + ".core.astrbot_config_bridge"))
            bridge.apply_astrbot_config = Mock()
        return SimpleNamespace(app=app, brain=brain, bridge=bridge, finished=finished)

    def load_real_bridge(self, package):
        self.install_package(package, "core")
        name = package + ".core.astrbot_config_bridge"
        self.real_modules.add(name)
        return importlib.import_module(name)

    def drop_namespace(self, package):
        for name in tuple(sys.modules):
            if name == package or name.startswith(package + "."):
                del sys.modules[name]
        parent_name, _, child = package.rpartition(".")
        parent = sys.modules.get(parent_name)
        if parent is not None and hasattr(parent, child):
            delattr(parent, child)
        importlib.invalidate_caches()

    def assert_isolated(self, test):
        test.assertIs(sys.path, self.path_object)
        test.assertEqual(sys.path, self.path_snapshot)
        for entry in sys.path:
            test.assertNotIn(Path(entry or Path.cwd()).resolve(), self.installs.values())
        bare = {name: module for name, module in sys.modules.items()
                if name.partition(".")[0] in BARE_ROOTS}
        test.assertEqual(set(bare), set(self.bare_modules))
        for name, module in self.bare_modules.items():
            test.assertIs(bare[name], module, name)
        for name, module in tuple(sys.modules.items()):
            module_name = getattr(module, "__name__", "")
            if any(module_name.startswith(package + ".") or module_name == package
                   for package in self.installs):
                test.assertEqual(name, module_name, "Plugin module gained an alias")
        test.assertEqual(self.unexpected_imports, [])
        test.assertEqual(self.foreign_cli.config, self.foreign_cli_config)
        test.assertEqual(self.foreign_config.config, self.foreign_core_config)
        self.foreign_cli._release_bot_lock.assert_not_called()
        self.foreign_bridge.apply_astrbot_config.assert_not_called()
        self.foreign_display.log.assert_not_called()


@contextmanager
def _package_environment():
    with ExitStack() as stack:
        # Copy source files only, never real settings, user data, or dependencies.
        temporary = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        second = temporary / "different-install" / "fixture_b"
        for filename in ("__init__.py", "main.py", "core/astrbot_config_bridge.py"):
            target = second / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / filename, target)
        environment = _PackageEnvironment(second)
        stack.enter_context(patch.dict(sys.modules))
        for name in tuple(sys.modules):
            if (name == "data" or name.startswith("data.")
                    or name.startswith("_bili_learning_")
                    or name.partition(".")[0] in BARE_ROOTS):
                del sys.modules[name]
        paths = [entry for entry in sys.path
                 if Path(entry or Path.cwd()).resolve() not in environment.installs.values()]
        stack.enter_context(patch.object(sys, "path", paths))
        stack.enter_context(patch.object(sys, "dont_write_bytecode", True))
        environment.path_object = sys.path
        environment.path_snapshot = list(sys.path)
        environment.install(_module("data", []))
        environment.install(_module("data.plugins", []))
        environment.install_dependencies()
        environment.install_foreign_modules()
        environment.bare_modules = {
            name: module for name, module in sys.modules.items()
            if name.partition(".")[0] in BARE_ROOTS
        }
        stack.enter_context(patch("importlib.metadata.version", side_effect=CORE_VERSIONS.__getitem__))
        stack.enter_context(patch.object(sys, "meta_path", [_BusinessImportGuard(environment), *sys.meta_path]))
        stack.enter_context(patch("builtins.__import__", new=environment.guarded_import))
        yield environment


class _PackageTestMixin:
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.environment = stack.enter_context(_package_environment())


class PackageImportTests(_PackageTestMixin, unittest.TestCase):
    def test_entry_and_constructor_keep_business_lazy_without_changing_path(self):
        mains = []
        for package in PACKAGES:
            with self.subTest(package=package):
                main = self.environment.load_main(package)
                mains.append(main)
                self.assertEqual(main.__package__, package)
                self.assertEqual(main.__name__, package + ".main")
                self.assertEqual(Path(main.__file__).parent, self.environment.installs[package])
                self.assertEqual(sys.modules[package].__package__, package)
                self.assertEqual(sys.modules[package].__path__, [str(self.environment.installs[package])])
                self.assertEqual(main._missing_core_deps, [])
                for config in (None, {"use_astrbot_config": True}):
                    bot = main.BilibiliLearningBot(None, config)
                    self.assertIs(bot.config, config)
                loaded = {name for name in sys.modules
                          if name == package or name.startswith(package + ".")}
                self.assertEqual(loaded, {package, package + ".main"})
                self.assertIsNone(main._brain_task)
                self.environment.assert_isolated(self)
        self.assertNotEqual(Path(mains[0].__file__).parent, Path(mains[1].__file__).parent)
        self.assertIsNot(mains[0]._brain_task_lock, mains[1]._brain_task_lock)

    def test_cli_app_returns_its_qualified_stub_not_foreign_or_other_install(self):
        mains = {package: self.environment.load_main(package) for package in PACKAGES}
        apps = {package: self.environment.install_cli(package) for package in PACKAGES}
        for package in (*PACKAGES, PACKAGE_A):
            with self.subTest(package=package):
                self.assertIs(mains[package]._cli_app(), apps[package])
                self.assertEqual(apps[package].__name__, package + ".cli.app")
                self.environment.assert_isolated(self)

    def test_standalone_run_path_creates_private_lazy_package_without_path_changes(self):
        namespace = runpy.run_path(str(ROOT / "main.py"), run_name="standalone_fixture")
        prefix = namespace["__package__"]
        self.assertTrue(prefix.startswith("_bili_learning_"), prefix)
        self.assertNotIn(".", prefix)
        self.assertEqual(namespace["__name__"], "standalone_fixture")
        self.assertEqual(namespace["_missing_core_deps"], [])
        self.assertIn(prefix, sys.modules)
        self.assertEqual(sys.modules[prefix].__path__, [str(ROOT)])
        self.assertEqual({name for name in sys.modules
                          if name == prefix or name.startswith(prefix + ".")}, {prefix})
        self.assertNotIn("standalone_fixture", sys.modules)
        self.environment.installs[prefix] = ROOT
        app = self.environment.install_cli(prefix)
        self.assertIs(namespace["_cli_app"](), app)
        self.assertEqual(app.__name__, prefix + ".cli.app")
        self.environment.assert_isolated(self)

    def test_namespace_cleanup_allows_fresh_entry_and_cli_reload(self):
        first = self.environment.load_main(PACKAGE_A)
        first_package = sys.modules[PACKAGE_A]
        first_app = self.environment.install_cli(PACKAGE_A)
        other = self.environment.load_main(PACKAGE_B)
        other_app = self.environment.install_cli(PACKAGE_B)
        self.assertIs(first._cli_app(), first_app)

        self.environment.drop_namespace(PACKAGE_A)
        self.assertFalse(any(name == PACKAGE_A or name.startswith(PACKAGE_A + ".")
                             for name in sys.modules))
        self.assertFalse(hasattr(sys.modules["data.plugins"], "fixture_a"))
        reloaded = self.environment.load_main(PACKAGE_A)
        self.assertIsNot(reloaded, first)
        self.assertIsNot(sys.modules[PACKAGE_A], first_package)
        self.assertIsNone(reloaded._brain_task)
        self.assertIsNot(reloaded._brain_task_lock, first._brain_task_lock)
        self.assertNotIn(PACKAGE_A + ".cli", sys.modules)
        replacement = self.environment.install_cli(PACKAGE_A)
        self.assertIsNot(replacement, first_app)
        self.assertIs(reloaded._cli_app(), replacement)
        self.assertIs(sys.modules[PACKAGE_B + ".main"], other)
        self.assertIs(other._cli_app(), other_app)
        self.environment.assert_isolated(self)


class PackageStartBotTests(_PackageTestMixin, unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        tasks = {main._brain_task for main in self.environment.loaded_mains
                 if main._brain_task is not None}
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.sleep(0)

    async def start(self, main, config=None):
        event = SimpleNamespace(plain_result=lambda text: text)
        bot = main.BilibiliLearningBot(None, config)
        replies = [reply async for reply in bot.start_bot(event)]
        self.assertEqual(len(replies), 1)
        self.assertIn("已在后台启动", replies[0])
        self.assertIsNotNone(main._brain_task)
        return main._brain_task

    async def settle(self, task):
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)

    async def test_start_without_config_uses_qualified_brain_without_loading_bridge(self):
        tasks = []
        for package in PACKAGES:
            with self.subTest(package=package):
                main = self.environment.load_main(package)
                runtime = self.environment.install_runtime(package, with_bridge=False)
                tasks.append(await self.start(main))
                runtime.brain.AgentBrain.assert_called_once_with()
                self.assertNotIn(package + ".core.astrbot_config_bridge", sys.modules)
                self.assertNotIn(package + ".core", sys.modules)
                self.environment.assert_isolated(self)
        self.assertIsNot(tasks[0], tasks[1])

    async def test_start_with_config_applies_qualified_bridge_before_qualified_brain(self):
        for package in PACKAGES:
            with self.subTest(package=package):
                main = self.environment.load_main(package)
                runtime = self.environment.install_runtime(package)
                panel = {"use_astrbot_config": True, "api": {"model_brain": package}}
                calls = Mock()
                calls.attach_mock(runtime.bridge.apply_astrbot_config, "apply")
                calls.attach_mock(runtime.brain.AgentBrain, "brain")
                await self.start(main, panel)
                self.assertEqual(calls.mock_calls, [call.apply(panel), call.brain()])
                self.assertIs(runtime.bridge.apply_astrbot_config.call_args.args[0], panel)
                self.environment.assert_isolated(self)

    async def test_bridge_failure_does_not_create_a_brain_or_background_task(self):
        for package in PACKAGES:
            with self.subTest(package=package):
                main = self.environment.load_main(package)
                runtime = self.environment.install_runtime(package)
                panel = {"use_astrbot_config": True}
                runtime.bridge.apply_astrbot_config.side_effect = ValueError("invalid fixture config")
                event = SimpleNamespace(plain_result=lambda text: text)
                bot = main.BilibiliLearningBot(None, panel)
                replies = [reply async for reply in bot.start_bot(event)]
                self.assertEqual(len(replies), 1)
                self.assertIn("初始化失败", replies[0])
                runtime.bridge.apply_astrbot_config.assert_called_once_with(panel)
                runtime.brain.AgentBrain.assert_not_called()
                runtime.app._release_bot_lock.assert_not_called()
                self.assertIsNone(main._brain_task)
                self.environment.assert_isolated(self)

    async def test_cancelled_task_releases_only_its_own_cli_lock(self):
        mains = {package: self.environment.load_main(package) for package in PACKAGES}
        runtimes = {package: self.environment.install_runtime(package) for package in PACKAGES}
        tasks = {package: await self.start(mains[package]) for package in PACKAGES}
        await asyncio.sleep(0)
        tasks[PACKAGE_A].cancel()
        await self.settle(tasks[PACKAGE_A])
        self.assertTrue(tasks[PACKAGE_A].cancelled())
        self.assertIsNone(mains[PACKAGE_A]._brain_task)
        self.assertFalse(mains[PACKAGE_A]._brain_stopping)
        runtimes[PACKAGE_A].app._release_bot_lock.assert_called_once_with()
        runtimes[PACKAGE_B].app._release_bot_lock.assert_not_called()
        self.assertIs(mains[PACKAGE_B]._brain_task, tasks[PACKAGE_B])
        self.assertFalse(tasks[PACKAGE_B].done())

        tasks[PACKAGE_B].cancel()
        await self.settle(tasks[PACKAGE_B])
        self.assertIsNone(mains[PACKAGE_B]._brain_task)
        runtimes[PACKAGE_B].app._release_bot_lock.assert_called_once_with()
        runtimes[PACKAGE_A].app._release_bot_lock.assert_called_once_with()
        self.environment.assert_isolated(self)

    async def test_completed_task_releases_only_its_own_cli_lock(self):
        mains = {package: self.environment.load_main(package) for package in PACKAGES}
        runtimes = {package: self.environment.install_runtime(package) for package in PACKAGES}
        tasks = {package: await self.start(mains[package]) for package in PACKAGES}
        runtimes[PACKAGE_A].finished.set()
        await self.settle(tasks[PACKAGE_A])
        self.assertFalse(tasks[PACKAGE_A].cancelled())
        self.assertIsNone(tasks[PACKAGE_A].exception())
        self.assertIsNone(mains[PACKAGE_A]._brain_task)
        runtimes[PACKAGE_A].app._release_bot_lock.assert_called_once_with()
        runtimes[PACKAGE_B].app._release_bot_lock.assert_not_called()
        self.assertIs(mains[PACKAGE_B]._brain_task, tasks[PACKAGE_B])
        self.assertFalse(tasks[PACKAGE_B].done())

        runtimes[PACKAGE_B].finished.set()
        await self.settle(tasks[PACKAGE_B])
        self.assertIsNone(mains[PACKAGE_B]._brain_task)
        runtimes[PACKAGE_B].app._release_bot_lock.assert_called_once_with()
        runtimes[PACKAGE_A].app._release_bot_lock.assert_called_once_with()
        self.environment.assert_isolated(self)

    async def test_stale_done_callback_does_not_release_replacement_task_lock(self):
        main = self.environment.load_main(PACKAGE_A)
        runtime = self.environment.install_runtime(PACKAGE_A)
        old_task = await self.start(main)
        runtime.finished.set()
        await self.settle(old_task)
        runtime.app._release_bot_lock.assert_called_once_with()
        runtime.finished.clear()
        replacement = await self.start(main)
        main._on_brain_done(old_task)
        self.assertIs(main._brain_task, replacement)
        self.assertFalse(replacement.done())
        runtime.app._release_bot_lock.assert_called_once_with()
        self.environment.assert_isolated(self)


class PackageConfigBridgeTests(_PackageTestMixin, unittest.TestCase):
    def test_real_bridge_sync_updates_only_owning_cli_in_place(self):
        mains = {package: self.environment.load_main(package) for package in PACKAGES}
        apps = {package: self.environment.install_cli(package) for package in PACKAGES}
        bridges = {package: self.environment.load_real_bridge(package) for package in PACKAGES}
        for package, other in ((PACKAGE_A, PACKAGE_B), (PACKAGE_B, PACKAGE_A)):
            with self.subTest(package=package):
                bridge = bridges[package]
                self.assertEqual(mains[package].__package__, package)
                self.assertEqual(bridge.__package__, package + ".core")
                expected_cache = bridge.__package__.rsplit(".", 1)[0] + ".cli.app"
                self.assertIs(sys.modules[expected_cache], apps[package])
                shared = self.environment.install(_module(package + ".core.config"))
                shared.config = {"stale": True}
                shared_reference = shared.config
                cli_reference = apps[package].config
                other_reference = apps[other].config
                other_before = deepcopy(other_reference)
                updated = {"api": {"model_brain": package, "models": [package]}}
                bridge._sync_loaded_config(shared, updated)
                self.assertIs(shared.config, shared_reference)
                self.assertEqual(shared_reference, updated)
                self.assertIs(apps[package].config, cli_reference)
                self.assertEqual(cli_reference, updated)
                self.assertIsNot(cli_reference["api"], updated["api"])
                self.assertIsNot(cli_reference["api"]["models"], updated["api"]["models"])
                cli_reference["api"]["models"].append("cli-only")
                self.assertEqual(shared_reference["api"]["models"], [package])
                self.assertIs(apps[other].config, other_reference)
                self.assertEqual(other_reference, other_before)
                self.environment.assert_isolated(self)

    def test_real_bridge_does_not_import_absent_cli_or_fall_back_to_foreign_cli(self):
        self.environment.load_main(PACKAGE_A)
        self.environment.load_main(PACKAGE_B)
        other_app = self.environment.install_cli(PACKAGE_B)
        other_reference = other_app.config
        other_before = deepcopy(other_reference)
        bridge = self.environment.load_real_bridge(PACKAGE_A)
        shared = self.environment.install(_module(PACKAGE_A + ".core.config"))
        shared.config = {"stale": True}
        reference = shared.config
        updated = {"api": {"model_brain": "only-shared"}}
        bridge._sync_loaded_config(shared, updated)
        self.assertIs(shared.config, reference)
        self.assertEqual(reference, updated)
        self.assertNotIn(PACKAGE_A + ".cli", sys.modules)
        self.assertNotIn(PACKAGE_A + ".cli.app", sys.modules)
        self.assertIs(other_app.config, other_reference)
        self.assertEqual(other_reference, other_before)
        self.environment.assert_isolated(self)


if __name__ == "__main__":
    unittest.main()
