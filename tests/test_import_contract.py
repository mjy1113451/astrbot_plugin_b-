"""Check package imports without loading optional business dependencies."""
import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
INTERNAL_PACKAGES = {
    "api", "brain", "cli", "core", "knowledge", "ob_bridge", "persona",
    "security", "services", "utils", "xingye_bot",
}
INTERNAL_MODULES = {"_runtime"}


class ImportContractTests(unittest.TestCase):
    def sources(self):
        yield ROOT / "main.py"
        for name in sorted(INTERNAL_MODULES):
            yield ROOT / f"{name}.py"
        for name in sorted(INTERNAL_PACKAGES):
            yield from sorted((ROOT / name).rglob("*.py"))

    def test_production_sources_parse_and_internal_imports_are_relative(self):
        for path in self.sources():
            with self.subTest(file=str(path.relative_to(ROOT))):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and not node.level and node.module:
                        self.assertNotIn(node.module.split(".")[0], INTERNAL_PACKAGES | INTERNAL_MODULES)
                    elif isinstance(node, ast.Import):
                        for alias in node.names:
                            self.assertNotIn(alias.name.split(".")[0], INTERNAL_PACKAGES | INTERNAL_MODULES)

    def test_runtime_internal_import_targets_exist(self):
        for path in self.sources():
            # This unreferenced preview package predates the runtime import repair.
            if path.relative_to(ROOT).as_posix() == "services/biliclaw/__init__.py":
                continue
            package = path.relative_to(ROOT).parent.parts
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or not node.level:
                    continue
                base = package[:len(package) - node.level + 1]
                parts = base + tuple(node.module.split(".")) if node.module else base
                target = ROOT.joinpath(*parts)
                with self.subTest(file=str(path.relative_to(ROOT)), line=node.lineno):
                    self.assertTrue(target.is_dir() or target.with_suffix(".py").is_file(), str(target))
                    if node.module is None:
                        for alias in node.names:
                            child = target / alias.name
                            self.assertTrue(child.is_dir() or child.with_suffix(".py").is_file(), str(child))

    def test_production_does_not_create_or_reconfigure_python_loggers(self):
        for path in self.sources():
            with self.subTest(file=str(path.relative_to(ROOT))):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                        self.assertNotIn(node.func.attr, {"getLogger", "basicConfig", "setLevel"})

    def test_required_tools_are_included(self):
        for name in ("display", "helpers", "lock", "storage", "subtitles",
                     "desktop_notifications", "web_launcher"):
            with self.subTest(module=name):
                self.assertTrue((ROOT / "utils" / f"{name}.py").is_file())


if __name__ == "__main__":
    unittest.main()
