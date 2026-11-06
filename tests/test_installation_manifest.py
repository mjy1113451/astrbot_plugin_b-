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
        match = re.search(r'(?m)^PLUGIN_VERSION = "([^"]+)"$', main_source)
        self.assertIsNotNone(match, "main.py 必须声明 PLUGIN_VERSION")
        version = match.group(1)

        metadata = (ROOT / "metadata.yaml").read_text(encoding="utf-8")
        self.assertRegex(metadata, rf"(?m)^version: {re.escape(version)}\s*$")
        self.assertIn('astrbot_version: ">=4.29.1,<5"', metadata)

        # 版本只允许出现在 metadata.yaml 与 main.py 的注册处。
        self.assertFalse((ROOT / "VERSION").exists())
        for filename in ("README.md", "requirements.txt", "cli/app.py"):
            with self.subTest(filename=filename):
                self.assertNotIn(version, (ROOT / filename).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
