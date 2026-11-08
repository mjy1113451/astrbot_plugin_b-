"""保护安装依赖清单和修复版本，避免核心依赖冲突再次进入安装包。"""
from pathlib import Path
import ast
import re
import unittest

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


ROOT = Path(__file__).resolve().parents[1]
CORE_PACKAGES = {
    "openai", "pydantic", "httpx", "pillow", "qrcode", "python-docx", "reportlab",
}


def read_requirements(text):
    requirements = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            requirements.append(Requirement(line))
    return requirements


def find_hardcoded_plugin_version(text, version, filename, dependency_names=()):
    number = re.escape(version) + r"(?![a-z0-9_+!-]|\.[a-z0-9])"
    value = r"(?:\*{1,2}[ \t]*)?[`\"']?[vV]?" + number
    label = (
        r"(?:plugin[_ -]?version|__version__|version|(?:插件|当前)?版本)"
        r"[`\"']?(?:[ \t]*\*{1,2})?[ \t]*[:：=][ \t]*"
    )
    if filename == "cli/app.py":
        tree = ast.parse(text)
        assignments = [*tree.body]
        for statement in tree.body:
            if isinstance(statement, ast.ClassDef) and any(
                isinstance(base, ast.Name) and base.id == "Star" for base in statement.bases
            ):
                assignments.extend(statement.body)
        for statement in assignments:
            if isinstance(statement, ast.Assign):
                targets = statement.targets
            elif isinstance(statement, ast.AnnAssign):
                targets = [statement.target]
            else:
                continue
            if (any(isinstance(target, ast.Name) and target.id in {
                "version", "VERSION", "PLUGIN_VERSION", "__version__"
            } for target in targets) and isinstance(statement.value, ast.Constant)
                    and statement.value.value == version):
                return f"line {statement.lineno}: {ast.unparse(statement)}"
        for line_number, line in enumerate(text.splitlines(), 1):
            match = re.search(
                r"(?<![a-z0-9_])(?:插件|当前)?版本[ \t]*[:：][ \t]*" + value,
                line, re.IGNORECASE,
            )
            if match:
                return f"line {line_number}: {match.group(0)}"
        return None
    if filename == "README.md":
        pattern = (
            r"(?m)^[ \t]*(?:[-*>|][ \t]*)?(?:\*{1,2})?" + label + value
            + r"|(?<![\w])(?:当前|current)[ \t]+" + value
            + r"|(?<![a-z0-9_])Version[ \t]+" + value
        )
    elif filename == "requirements.txt":
        pattern = (
            r"(?m)^[ \t]*\#[ \t]*(?:bilibili[_ -]learning[_ -]bot|插件|plugin)"
            r"[ \t]+[vV]" + number
            + r"|(?m:^[ \t]*\#[ \t]*(?:" + label + value + r"))"
        )
    else:
        raise ValueError(f"不支持的版本检查文件: {filename}")
    dependencies = {canonicalize_name(name) for name in dependency_names}
    for match in re.finditer(pattern, text, re.IGNORECASE):
        if filename == "README.md":
            prefix = text[:match.start()].rsplit("\n", 1)[-1]
            previous_word = re.search(r"([a-z0-9][a-z0-9_.-]*)[ \t]+$", prefix, re.IGNORECASE)
            if previous_word and canonicalize_name(previous_word.group(1)) in dependencies:
                continue
        return match.group(0)
    return None


class InstallationManifestTests(unittest.TestCase):
    def test_direct_requirements_do_not_redeclare_core_packages(self):
        requirements = read_requirements((ROOT / "requirements.txt").read_text(encoding="utf-8"))
        self.assertTrue(requirements)
        for requirement in requirements:
            with self.subTest(requirement=str(requirement)):
                self.assertNotIn(canonicalize_name(requirement.name), CORE_PACKAGES)
                self.assertIsNone(requirement.url)
                self.assertFalse(any(item.operator in {"==", "==="} for item in requirement.specifier))

    def test_guard_detects_historical_openai_pin_and_normalizes_names(self):
        requirements = read_requirements(
            "# openai is provided by AstrBot\nOpenAI==2.33.0\nHTTPX[socks]>=0.28.1\npython_docx>=1.2.0\n"
        )
        self.assertEqual(
            [canonicalize_name(item.name) for item in requirements],
            ["openai", "httpx", "python-docx"],
        )
        self.assertTrue(all(canonicalize_name(item.name) in CORE_PACKAGES for item in requirements))

    def test_version_is_defined_only_in_metadata_and_main(self):
        main_source = (ROOT / "main.py").read_text(encoding="utf-8")
        # main.py 中 PLUGIN_VERSION 只能声明一次。
        version_decls = re.findall(r'(?m)^PLUGIN_VERSION\s*=\s*"([^"]+)"\s*$', main_source)
        self.assertEqual(len(version_decls), 1, "main.py 必须且只能声明一次 PLUGIN_VERSION")
        version = version_decls[0]

        # metadata.yaml 中 version 只能定义一次，且值与 PLUGIN_VERSION 一致。
        metadata = (ROOT / "metadata.yaml").read_text(encoding="utf-8")
        metadata_versions = re.findall(r'(?m)^version:\s*(.+?)\s*$', metadata)
        self.assertEqual(len(metadata_versions), 1, "metadata.yaml 中 version 必须且只能定义一次")
        self.assertEqual(metadata_versions[0].strip('"'), version)
        self.assertIn('astrbot_version: ">=4.29.1,<5"', metadata)

        # CHANGELOG 允许保留历史版本号，但最新一条发布记录必须与当前版本一致。
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        latest = re.search(r'(?m)^##\s+([^\s—]+)\s*[—-]', changelog)
        self.assertIsNotNone(latest, "CHANGELOG.md 缺少最新版本标题")
        self.assertEqual(latest.group(1).strip(), version)

        # VERSION 文件已移除，其它文件不允许硬编码当前插件版本声明。
        self.assertFalse((ROOT / "VERSION").exists())
        dependency_names = CORE_PACKAGES | {"AstrBot"} | {
            requirement.name for requirement in read_requirements(
                (ROOT / "requirements.txt").read_text(encoding="utf-8")
            )
        }
        for filename in ("README.md", "requirements.txt", "cli/app.py"):
            with self.subTest(filename=filename):
                text = (ROOT / filename).read_text(encoding="utf-8")
                match = find_hardcoded_plugin_version(text, version, filename, dependency_names)
                self.assertIsNone(match, f"{filename} 硬编码了插件版本: {match}")


class HardcodedPluginVersionTests(unittest.TestCase):
    def test_explicit_version_declarations_are_detected(self):
        cases = (
            ("cli/app.py", "version = '3.1.6'", "3.1.6"),
            ("cli/app.py", '__version__ = "3.1.6"', "3.1.6"),
            ("cli/app.py", 'PLUGIN_VERSION = "3.1.6"', "3.1.6"),
            ("cli/app.py", 'PLUGIN_VERSION: str = "3.1.6"', "3.1.6"),
            ("cli/app.py", 'PLUGIN_VERSION = (\n    "3.1.6"\n)', "3.1.6"),
            ("cli/app.py", 'class Plugin(Star):\n    version = "3.1.6"', "3.1.6"),
            ("cli/app.py", 'print("版本: v3.1.6 B站视频学习版")', "3.1.6"),
            ("cli/app.py", 'print("版本: v3.1.6学习版")', "3.1.6"),
            ("README.md", '> Version: **3.1.6** | License: MIT', "3.1.6"),
            ("README.md", '- Version **3.1.6**, MIT', "3.1.6"),
            ("README.md", 'Version: 3.1.6.', "3.1.6"),
            ("README.md", '插件版本：3.1.6', "3.1.6"),
            ("README.md", '当前版本：3.1.6', "3.1.6"),
            ("README.md", '**版本**：`3.1.6`', "3.1.6"),
            ("README.md", 'v3.1.2+（当前 3.1.6）', "3.1.6"),
            ("README.md", 'v3.1.2+ (current 3.1.6)', "3.1.6"),
            ("README.md", 'plugin version: v3.1', "3.1"),
            ("requirements.txt", '# plugin_version=3.1', "3.1"),
            ("requirements.txt", '# bilibili_learning_bot v3.1.6 依赖清单', "3.1.6"),
        )
        for filename, text, version in cases:
            with self.subTest(filename=filename, text=text, version=version):
                self.assertIsNotNone(find_hardcoded_plugin_version(text, version, filename))

    def test_unrelated_numbers_and_similar_versions_are_ignored(self):
        cases = (
            ("requirements.txt", 'Flask>=3.1,<4', "3.1"),
            ("requirements.txt", 'Flask>=3.1 # version: 3.1', "3.1"),
            ("requirements.txt", '# 插件依赖 Flask v3.1', "3.1"),
            ("README.md", 'Flask version: 3.1', "3.1"),
            ("README.md", '## 3.1 安装说明', "3.1"),
            ("README.md", '版本：见 metadata.yaml 的 version', "3.1"),
            ("README.md", 'astrbot_version: "3.1"', "3.1"),
            ("cli/app.py", 'minimum_version = "3.1"', "3.1"),
            ("cli/app.py", 'schema_version = "3.1"', "3.1"),
            ("cli/app.py", 'export_data = {"version": "3.1"}', "3.1"),
            ("cli/app.py", '# PLUGIN_VERSION = "3.1"', "3.1"),
            ("cli/app.py", 'version = "3.1.6"', "3.1"),
            ("cli/app.py", 'version = "3.10"', "3.1"),
            ("cli/app.py", 'version = "13.1"', "3.1"),
            ("cli/app.py", 'version = "3.1rc1"', "3.1"),
            ("cli/app.py", 'version = "3.1+local"', "3.1"),
            ("cli/app.py", 'version = "3.1-1"', "3.1"),
            ("cli/app.py", 'version = "3.1.60"', "3.1.6"),
            ("cli/app.py", 'version = "3.1.6rc1"', "3.1.6"),
            ("README.md", 'Version: 3.1.6', "3.1"),
            ("README.md", 'Version: 3.1rc1', "3.1"),
        )
        for filename, text, version in cases:
            with self.subTest(filename=filename, text=text, version=version):
                self.assertIsNone(find_hardcoded_plugin_version(text, version, filename))

    def test_inline_readme_versions_are_detected(self):
        for text in (
            'Some text Version **3.1.6**',
            '| Version **3.1.6** |',
            '| 插件 | Version **3.1.6** |',
            'Plugin information (Version `3.1.6`).',
        ):
            with self.subTest(text=text):
                self.assertIsNotNone(find_hardcoded_plugin_version(text, "3.1.6", "README.md"))

    def test_inline_readme_versions_preserve_token_boundaries(self):
        for text in (
            'Some text Version **3.1.60**',
            'Some text Version **3.1.6rc1**',
            'Some text Version **3.1.6+local**',
            'Some text Version **13.1.6**',
            'Some text LibraryVersion **3.1.6**',
            'Some text schema_version **3.1.6**',
        ):
            with self.subTest(text=text):
                self.assertIsNone(find_hardcoded_plugin_version(text, "3.1.6", "README.md"))
        self.assertIsNone(find_hardcoded_plugin_version(
            'Some text Version **3.1.6**', "3.1", "README.md",
        ))

    def test_named_dependency_does_not_hide_a_later_plugin_declaration(self):
        dependencies = {"Flask", "httpx", "python-docx", "AstrBot"}
        for name in ("Flask", "HTTPX", "python_docx", "AstrBot"):
            text = f'{name} Version v3.1.6'
            with self.subTest(dependency=name):
                self.assertIsNone(find_hardcoded_plugin_version(
                    text, "3.1.6", "README.md", dependencies,
                ))
                combined = text + '\nSome text Version **3.1.6**'
                self.assertEqual(find_hardcoded_plugin_version(
                    combined, "3.1.6", "README.md", dependencies,
                ), find_hardcoded_plugin_version(
                    'Some text Version **3.1.6**', "3.1.6", "README.md",
                ))

    def test_runtime_versions_are_derived_from_plugin_version(self):
        main_source = (ROOT / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(main_source)
        values = [
            statement.value.value
            for statement in tree.body
            if isinstance(statement, (ast.Assign, ast.AnnAssign))
            and isinstance(statement.value, ast.Constant)
            and any(isinstance(target, ast.Name) and target.id == "PLUGIN_VERSION"
                    for target in (statement.targets if isinstance(statement, ast.Assign)
                                   else [statement.target]))
        ]
        self.assertEqual(len(values), 1)
        version = values[0]
        examples = (
            ("README.md", f'Version: **{version}**'),
            ("requirements.txt", f'# bilibili_learning_bot v{version} 依赖清单'),
            ("cli/app.py", f'__version__ = "{version}"'),
        )
        for filename, text in examples:
            with self.subTest(filename=filename):
                self.assertIsNotNone(find_hardcoded_plugin_version(text, version, filename))

    def test_unsupported_file_is_rejected(self):
        with self.assertRaises(ValueError):
            find_hardcoded_plugin_version('version: "3.1"', "3.1", "CHANGELOG.md")


if __name__ == "__main__":
    unittest.main()

