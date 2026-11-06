"""保护安装依赖清单和修复版本，避免核心依赖冲突再次进入安装包。"""
from pathlib import Path
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

        # VERSION 文件已移除，其它文件不允许再出现当前版本号。
        self.assertFalse((ROOT / "VERSION").exists())
        for filename in ("README.md", "requirements.txt", "cli/app.py"):
            with self.subTest(filename=filename):
                self.assertNotIn(version, (ROOT / filename).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

