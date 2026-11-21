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

    def test_config_and_cli_have_no_eager_filesystem_or_terminal_initialization(self):
        forbidden = {"mkdir", "makedirs", "write_text", "write_bytes", "write_private_json",
                     "save_config", "ensure_user_data_dir", "initialize_config", "initialize_cli",
                     "_initialize_cli_terminal"}

        def eager_calls(node):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                return
            if isinstance(node, ast.Call):
                yield node
            for child in ast.iter_child_nodes(node):
                yield from eager_calls(child)

        for relative in ("core/config.py", "cli/app.py"):
            source = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
            for call in eager_calls(source):
                name = getattr(call.func, "id", getattr(call.func, "attr", ""))
                with self.subTest(file=relative, line=call.lineno):
                    self.assertNotIn(name, forbidden)

    def test_required_tools_are_included(self):
        for name in ("display", "helpers", "lock", "storage", "subtitles"):
            with self.subTest(module=name):
                self.assertTrue((ROOT / "utils" / f"{name}.py").is_file())


if __name__ == "__main__":
    unittest.main()
