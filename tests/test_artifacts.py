"""验证历史包保留、最新索引及安装包完整性，不访问真实仓库或 Profile。"""
import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from pythonProject.common import OperationError
from pythonProject.package_artifacts import load_history, load_latest, publish_archive


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mrm artifacts ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / "dist/.staging/source.tgz"
        self.source.parent.mkdir(parents=True)

    def package(self, version="1.0.0", content="first", name="demo-plugin"):
        package = {"name": name, "version": version, "peerDependencies": {}}
        with tarfile.open(self.source, "w:gz") as archive:
            for filename, value in (("package/package.json", json.dumps(package)), ("package/lib/index.js", content)):
                data = value.encode()
                item = tarfile.TarInfo(filename)
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))
        return package

    def test_same_version_overwrites_and_new_version_preserves_previous_version(self):
        self.package("1.0.0", "first")
        first = publish_archive(self.root, self.source)
        original = first.history.read_bytes()
        self.package("1.0.0", "rebuilt")
        rebuilt = publish_archive(self.root, self.source)
        self.assertEqual(rebuilt.history, self.root / "dist/demo-plugin-1.0.0.tgz")
        self.assertEqual(first.history, rebuilt.history)
        self.assertNotEqual(rebuilt.history.read_bytes(), original)
        preserved = rebuilt.history.read_bytes()
        self.assertEqual(rebuilt.history.read_bytes(), rebuilt.latest.read_bytes())
        self.assertEqual(len(list((self.root / "dist").glob("*.tgz"))), 1)
        self.package("2.0.0", "upgraded")
        upgraded = publish_archive(self.root, self.source)
        self.assertEqual(upgraded.latest, self.root / "dist/latest/demo-plugin.tgz")
        self.assertEqual(upgraded.history.read_bytes(), upgraded.latest.read_bytes())
        self.assertEqual(rebuilt.history.read_bytes(), preserved)
        self.assertEqual(len(list((self.root / "dist").glob("*.tgz"))), 2)
        self.assertEqual(len(list((self.root / "dist/latest").glob("*.tgz"))), 1)
        self.assertEqual(load_latest(self.root, "demo-plugin").package["version"], "2.0.0")
        self.assertEqual(load_history(self.root, rebuilt.history.name).package["version"], "1.0.0")

    def test_failed_verification_leaves_latest_and_history_unchanged(self):
        self.package()
        first = publish_archive(self.root, self.source)
        before = first.latest.read_bytes()
        self.package("2.0.0")
        with self.assertRaises(OperationError):
            publish_archive(self.root, self.source, {"name": "demo-plugin", "version": "3.0.0"})
        with self.assertRaises(OperationError):
            publish_archive(self.root, self.source, build_time="invalid")
        self.assertEqual(first.latest.read_bytes(), before)
        self.assertEqual(len(list((self.root / "dist").glob("*.tgz"))), 1)
        self.source.write_bytes(b"invalid tgz")
        with self.assertRaises(OperationError):
            publish_archive(self.root, self.source)
        self.assertEqual(load_latest(self.root, "demo-plugin").digest, first.digest)

    def test_tampered_history_blocks_latest_even_when_latest_is_intact(self):
        self.package()
        artifact = publish_archive(self.root, self.source)
        artifact.history.write_bytes(b"tampered history")
        with self.assertRaises(OperationError):
            load_latest(self.root, "demo-plugin")

    def test_latest_version_index_and_digest_must_agree(self):
        self.package()
        artifact = publish_archive(self.root, self.source)
        with self.assertRaises(OperationError):
            load_latest(self.root, "demo-plugin", "9.0.0")
        index = artifact.latest.with_suffix(".json")
        original = index.read_bytes()
        for change in ({"history": "../outside.tgz"}, {"version": "9.0.0"}, {"sha256": "0" * 64},
                       {"buildTime": "invalid"}, {"buildTime": "2026-10-06T14:00:00"}):
            data = json.loads(original)
            data.update(change)
            index.write_text(json.dumps(data))
            with self.subTest(change=change), self.assertRaises(OperationError):
                load_latest(self.root, "demo-plugin")

    def test_build_time_is_preserved_in_latest_index(self):
        self.package()
        build_time = "2026-10-06T14:00:00+08:00"
        artifact = publish_archive(self.root, self.source, build_time=build_time)
        self.assertEqual(artifact.build_time, build_time)
        self.assertEqual(json.loads(artifact.latest.with_suffix(".json").read_text())["buildTime"], build_time)

    def test_historical_input_cannot_escape_dist_or_use_latest_path(self):
        self.package(name="@scope/demo-plugin")
        artifact = publish_archive(self.root, self.source)
        self.assertEqual(artifact.latest.name, "scope-demo-plugin.tgz")
        for path in ("../outside.tgz", artifact.latest, self.source):
            with self.subTest(path=path), self.assertRaises(OperationError):
                load_history(self.root, path)

    def test_sha256_sidecar_must_name_the_actual_archive(self):
        self.package()
        artifact = publish_archive(self.root, self.source)
        digest = hashlib.sha256(artifact.history.read_bytes()).hexdigest()
        Path(str(artifact.history) + ".sha256").write_text(f"{digest}  wrong.tgz\n")
        with self.assertRaises(OperationError):
            load_history(self.root, artifact.history)


if __name__ == "__main__":
    unittest.main()
