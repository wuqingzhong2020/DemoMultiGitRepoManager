"""验证同版本重装时的包信息刷新、失败恢复及 Profile 路径边界。"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from pythonProject.common import OperationError
from pythonProject.desktop import refresh_installed_manifests


class DesktopRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mrm refresh ")
        self.addCleanup(self.temporary.cleanup)
        self.profile = Path(self.temporary.name).resolve() / "profile"
        self.profile.mkdir()

    def manifest(self, name):
        path = self.profile / "node_modules" / name / "package.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'{"version":"1.0.0"}')
        return path

    def test_refresh_only_selected_plugins_and_keep_new_manifests(self):
        first = self.manifest("first")
        second = self.manifest("@scope/second")
        other = self.manifest("other")
        with refresh_installed_manifests(self.profile, ["first", "@scope/second", "missing"]):
            self.assertFalse(first.exists())
            self.assertFalse(second.exists())
            self.assertTrue(other.exists())
            first.write_bytes(b'{"version":"1.0.0","rebuilt":true}')
            second.write_bytes(b'{"version":"2.0.0"}')
        self.assertIn(b'rebuilt', first.read_bytes())
        self.assertIn(b'2.0.0', second.read_bytes())
        self.assertEqual(list(self.profile.glob(".mrm-package-*")), [])

    def test_failed_or_interrupted_install_restores_original_manifests(self):
        manifest = self.manifest("first")
        original = manifest.read_bytes()
        for error in (OperationError("install failed"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                with self.assertRaises(type(error)):
                    with refresh_installed_manifests(self.profile, ["first"]):
                        manifest.write_bytes(b'{"partial":true}')
                        raise error
                self.assertEqual(manifest.read_bytes(), original)
                self.assertEqual(list(self.profile.glob(".mrm-package-*")), [])

    def test_invalid_later_manifest_does_not_move_earlier_manifest(self):
        first = self.manifest("first")
        invalid = self.profile / "node_modules" / "second" / "package.json"
        invalid.mkdir(parents=True)
        with self.assertRaises(OperationError):
            with refresh_installed_manifests(self.profile, ["first", "second"]):
                self.fail("invalid manifest accepted")
        self.assertTrue(first.is_file())
        self.assertEqual(list(self.profile.glob(".mrm-package-*")), [])

    def test_shared_manifest_outside_profile_is_rejected(self):
        outside = Path(self.temporary.name) / "shared"
        outside.mkdir()
        (outside / "package.json").write_bytes(b'{"version":"1.0.0"}')
        link = self.profile / "node_modules" / "first"
        link.parent.mkdir(parents=True)
        if os.name == "nt":
            result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
            if result.returncode:
                self.skipTest("directory junctions unavailable")
            self.addCleanup(link.rmdir)
        else:
            link.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OperationError):
            with refresh_installed_manifests(self.profile, ["first"]):
                self.fail("outside Profile accepted")
        self.assertTrue((outside / "package.json").is_file())
        self.assertEqual(list(self.profile.glob(".mrm-package-*")), [])


if __name__ == "__main__":
    unittest.main()
