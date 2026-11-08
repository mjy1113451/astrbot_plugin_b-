"""保护安装依赖清单和版本来源，允许 CHANGELOG 保留历史发布记录。"""
import ast
from pathlib import Path
import re
import unittest

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version
import yaml


ROOT = Path(__file__).resolve().parents[1]
CORE_PACKAGES = {
    "openai", "pydantic", "httpx", "pillow", "qrcode", "python-docx", "reportlab",
}


class UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise ValueError(f"metadata.yaml 中存在重复键: {key!r}")
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def read_requirements(text):
    requirements = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            requirements.append(Requirement(line))
    return requirements


def read_plugin_version(source):
    tree = ast.parse(source)
    writes = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id == "PLUGIN_VERSION"
        and isinstance(node.ctx, (ast.Store, ast.Del))
    ]
    if len(writes) != 1:
        raise ValueError("main.py 必须且只能赋值一次 PLUGIN_VERSION")
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            targets = statement.targets
        elif isinstance(statement, ast.AnnAssign):
            targets = [statement.target]
        else:
            continue
        if len(targets) == 1 and targets[0] is writes[0]:
            value = statement.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str) and value.value:
                return value.value
    raise ValueError("PLUGIN_VERSION 必须在模块顶层独立赋值为非空字符串常量")


def read_metadata(source):
    metadata = yaml.load(source, Loader=UniqueKeyLoader)
    if not isinstance(metadata, dict):
        raise ValueError("metadata.yaml 必须是映射")
    version = metadata.get("version")
    if not isinstance(version, str) or not version:
        raise ValueError("metadata.yaml 必须包含非空字符串 version")
    return metadata


def read_latest_changelog_version(source):
    versions = []
    for heading in re.findall(r"(?m)^##[ \t]+(\S+)", source):
        try:
            versions.append(Version(heading))
        except InvalidVersion:
            continue
    if not versions:
        raise ValueError("CHANGELOG.md 缺少发布版本标题")
    return max(versions)


def validate_release_versions(main_source, metadata_source, changelog_source):
    version = read_plugin_version(main_source)
    if read_metadata(metadata_source)["version"] != version:
        raise ValueError("metadata.yaml 的 version 必须与 PLUGIN_VERSION 一致")
    if read_latest_changelog_version(changelog_source) != Version(version):
        raise ValueError("CHANGELOG.md 最新发布版本必须与 PLUGIN_VERSION 一致")
    return version


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

    def test_version_sources_are_unique_and_match_latest_changelog(self):
        main_source = (ROOT / "main.py").read_text(encoding="utf-8")
        metadata = (ROOT / "metadata.yaml").read_text(encoding="utf-8")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        validate_release_versions(main_source, metadata, changelog)
        self.assertEqual(read_metadata(metadata)["astrbot_version"], ">=4.29.1,<5")

    def test_current_version_is_not_hardcoded_in_other_release_files(self):
        version = read_plugin_version((ROOT / "main.py").read_text(encoding="utf-8"))
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
        version = read_plugin_version(main_source)
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


class VersionSourceRegressionTests(unittest.TestCase):
    def test_python_accepts_literal_styles_without_importing_plugin(self):
        declarations = (
            'PLUGIN_VERSION = "3.1.6"',
            "PLUGIN_VERSION = '3.1.6'  # current release",
            'PLUGIN_VERSION: str = "3.1.6"',
            'PLUGIN_VERSION = (\n    "3.1.6"\n)',
        )
        for declaration in declarations:
            with self.subTest(declaration=declaration):
                source = (
                    'raise RuntimeError("must not execute plugin code")\n'
                    '# PLUGIN_VERSION = "0.0.0"\n'
                    'description = \'PLUGIN_VERSION = "0.0.0"\'\n'
                    + declaration + '\nclass Plugin:\n    version = PLUGIN_VERSION\n'
                )
                self.assertEqual(read_plugin_version(source), "3.1.6")

    def test_python_rejects_duplicate_assignments(self):
        second_assignments = (
            'PLUGIN_VERSION = "3.1.6"',
            "PLUGIN_VERSION = '3.1.7'",
            'PLUGIN_VERSION: str = "3.1.7"',
            'PLUGIN_VERSION += ".post1"',
            'alias = PLUGIN_VERSION = "3.1.7"',
            'PLUGIN_VERSION, other = "3.1.7", None',
            'if True:\n    PLUGIN_VERSION = "3.1.7"',
            'if (PLUGIN_VERSION := "3.1.7"):\n    pass',
            'del PLUGIN_VERSION',
        )
        for second in second_assignments:
            with self.subTest(second=second), self.assertRaisesRegex(ValueError, "只能赋值一次"):
                read_plugin_version('PLUGIN_VERSION = "3.1.6"\n' + second)

    def test_python_requires_one_top_level_literal_source(self):
        invalid_sources = (
            '# PLUGIN_VERSION = "3.1.6"',
            'def helper():\n    PLUGIN_VERSION = "3.1.6"',
            'if True:\n    PLUGIN_VERSION = "3.1.6"',
            'PLUGIN_VERSION: str',
            'PLUGIN_VERSION = 316',
            'PLUGIN_VERSION = ""',
            'PLUGIN_VERSION = get_version()',
            'PLUGIN_VERSION = VERSION',
            'PLUGIN_VERSION = alias = "3.1.6"',
        )
        for source in invalid_sources:
            with self.subTest(source=source), self.assertRaises(ValueError):
                read_plugin_version(source)

    def test_yaml_accepts_quoted_keys_values_comments_and_flow_mapping(self):
        sources = (
            'version: 3.1.6\n',
            "'version': '3.1.6' # current release\n",
            '"version": "3.1.6"\n',
            '{version: "3.1.6", author: example}\n',
            'version: &release 3.1.6\nnotes:\n  version: *release\n',
            '# version: 0.0.0\nversion: 3.1.6\n'
            'description: |\n  version: 0.0.0\n',
        )
        for source in sources:
            with self.subTest(source=source):
                self.assertEqual(read_metadata(source)["version"], "3.1.6")

    def test_yaml_rejects_duplicate_version_keys_including_quoted_and_merged_keys(self):
        sources = (
            'version: 3.1.6\nversion: 3.1.6\n',
            "version: 3.1.6\n'version': 3.1.7\n",
            'version: 3.1.6\n"version": 3.1.7\n',
            '{version: 3.1.6, "version": 3.1.7}',
            'defaults: &defaults\n  version: 3.1.6\n<<: *defaults\nversion: 3.1.7\n',
        )
        for source in sources:
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, "重复键"):
                read_metadata(source)

    def test_yaml_requires_a_top_level_string_version(self):
        sources = ("", "[]", "notes:\n  version: 3.1.6\n", "version: null",
                   "version: 3.1", "version: 316", "version: false", 'version: ""')
        for source in sources:
            with self.subTest(source=source), self.assertRaises(ValueError):
                read_metadata(source)

    def test_yaml_rejects_unsafe_tags(self):
        with self.assertRaises(yaml.constructor.ConstructorError):
            read_metadata('version: !!python/tuple [3, 1, 6]')

    def test_release_allows_unreleased_section_and_historical_versions(self):
        changelog = '# 更新日志\n\n## 未发布\n\n## 3.1.6 — 当前发布\n\n## 3.1.2 正式发布版\n'
        self.assertEqual(validate_release_versions(
            'PLUGIN_VERSION = "3.1.6"', 'version: "3.1.6"', changelog,
        ), "3.1.6")

    def test_release_rejects_metadata_version_mismatch(self):
        with self.assertRaisesRegex(ValueError, "metadata.yaml"):
            validate_release_versions(
                'PLUGIN_VERSION = "3.1.6"', 'version: "3.1.7"', '## 3.1.6 — 发布\n',
            )

    def test_changelog_selects_highest_version_regardless_of_heading_order(self):
        main_source = 'PLUGIN_VERSION = "3.1.6"'
        metadata = 'version: "3.1.6"'
        for headings in (
            ("## 3.1.6 — 最新版", "## 3.1.2 历史版本"),
            ("## 3.1.2 历史版本", "## 3.1.6 — 最新版"),
        ):
            changelog = '# 更新日志\n\n## 未发布\n\n' + '\n\n'.join(headings)
            with self.subTest(headings=headings):
                self.assertEqual(validate_release_versions(main_source, metadata, changelog), "3.1.6")

    def test_release_rejects_newer_changelog_version_in_either_order(self):
        for title in ("## 3.1.7 — 新版本", "## 3.1.7 正式发布版", "## 3.1.7"):
            for headings in ((title, "## 3.1.6 — 当前"), ("## 3.1.6 — 当前", title)):
                changelog = '# 更新日志\n\n## 未发布\n\n' + '\n\n'.join(headings)
                with self.subTest(headings=headings), self.assertRaisesRegex(ValueError, "最新发布版本"):
                    validate_release_versions(
                        'PLUGIN_VERSION = "3.1.6"', 'version: "3.1.6"', changelog,
                    )

    def test_changelog_uses_pep440_version_order(self):
        changelog = (
            '## 未发布\n## not-a-version\n## 3.1.9\n## 3.1.10rc1\n'
            '## 3.1.10\n## 3.1.2\n'
        )
        self.assertEqual(read_latest_changelog_version(changelog), Version("3.1.10"))
        self.assertEqual(validate_release_versions(
            'PLUGIN_VERSION = "3.1.10"', 'version: "3.1.10"', changelog,
        ), "3.1.10")

    def test_changelog_accepts_equivalent_pep440_spellings(self):
        self.assertEqual(validate_release_versions(
            'PLUGIN_VERSION = "3.1.6"', 'version: "3.1.6"', '## v3.1.6 — 发布\n',
        ), "3.1.6")

    def test_changelog_requires_a_release_heading(self):
        with self.assertRaisesRegex(ValueError, "缺少发布版本"):
            read_latest_changelog_version('# 更新日志\n## 未发布\n正文提到了 3.1.6\n')


if __name__ == "__main__":
    unittest.main()
