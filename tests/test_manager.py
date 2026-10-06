"""用临时工作区验证仓库写操作、路径边界、调度和 Desktop 配置。"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import envBuild
from pythonProject.common import CommandResult, ConfigError, OperationError, Runner
from pythonProject.desktop import Desktop
from pythonProject.dsh_config import PROJECT_FILE, check_dsh, expected_config, init_dsh
from pythonProject.env_config import Environment, init_environment
from pythonProject.git_manager import GitManager
from pythonProject.plugin_tasks import PluginTasks, package_info, verified_archive
from pythonProject.package_artifacts import load_latest, package_stem, publish_archive
from pythonProject.repo_config import Manifest


MANAGER = "dsh-multi-git-repo-manager"
REVIEW = "dsh-file-review-tab-Multi-git-repository"
GIT = shutil.which("git")


class RecordingRunner:
    def __init__(self, fail_name: str | None = None):
        self.calls = []
        self.fail_name = fail_name
        self.env = {}

    def run(self, args, cwd, timeout, **kwargs):
        self.calls.append((list(args), Path(cwd)))
        return CommandResult(1 if Path(cwd).name == self.fail_name else 0, stderr="测试中的失败" if Path(cwd).name == self.fail_name else "")


@unittest.skipUnless(GIT, "需要 Git CLI")
class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mrm tests ")
        self.root = Path(self.temporary.name).resolve()
        self.addCleanup(self.temporary.cleanup)
        self.environ = dict(os.environ)
        for key in list(self.environ):
            if key.startswith("MRM_"):
                del self.environ[key]
        self.environ.update(MRM_GIT_EXE=GIT, MRM_NODE_EXE=sys.executable, MRM_PNPM_EXE=sys.executable)
        self.urls = {}
        for name in (MANAGER, REVIEW):
            bare = self.root / "remotes" / (name + ".git")
            bare.parent.mkdir(exist_ok=True)
            self.git(self.root, "init", "--bare", "--initial-branch=main", str(bare))
            seed = self.root / ("seed-" + name)
            self.git(self.root, "init", "--initial-branch=main", str(seed))
            self.configure_git(seed)
            (seed / "tracked.txt").write_text("base\n", encoding="utf-8")
            self.git(seed, "add", "tracked.txt")
            self.git(seed, "commit", "-m", "seed")
            self.git(seed, "remote", "add", "origin", str(bare))
            self.git(seed, "push", "-u", "origin", "main")
            path = self.root / "dsh-plugins" / name
            path.parent.mkdir(exist_ok=True)
            self.git(self.root, "clone", str(bare), str(path))
            self.configure_git(path)
            self.urls[name] = str(bare)
            package = {"name": name.lower(), "version": "1.0.0", "scripts": {"build": "node fake.mjs", "test": "node fake.mjs", "typecheck": "node fake.mjs", "test:pack": "node fake.mjs"}}
            if name == REVIEW:
                package["peerDependencies"] = {MANAGER: "1.0.0"}
            (path / "package.json").write_text(json.dumps(package), encoding="utf-8")
            (path / "scripts").mkdir()
            (path / "scripts/patch-desktop-directory-picker.mjs").write_text("// fixture\n", encoding="utf-8")
            self.git(path, "add", ".")
            self.git(path, "commit", "-m", "package fixture")
            self.git(path, "push")
        self.write_manifest()
        self.reload()

    def git(self, cwd, *args, check=True):
        result = subprocess.run([GIT, *args], cwd=cwd, capture_output=True, encoding="utf-8", errors="replace", env=self.environ)
        if check and result.returncode:
            self.fail(f"git {' '.join(args)}: {result.stderr}")
        return result

    def configure_git(self, path):
        for key, value in (("user.name", "Local Test"), ("user.email", "test@example.invalid"), ("commit.gpgsign", "false"), ("core.autocrlf", "false")):
            self.git(path, "config", key, value)

    def write_manifest(self, extra="", review_path=None, review_dependency=MANAGER):
        text = f"""[{MANAGER}]
path = dsh-plugins/{MANAGER}
url = {self.urls[MANAGER]}
branch = main

[{REVIEW}]
path = {review_path or 'dsh-plugins/' + REVIEW}
url = {self.urls[REVIEW]}
branch = main
depends_on = {review_dependency}
{extra}
"""
        (self.root / "submodules.ini").write_text(text, encoding="utf-8-sig")

    def reload(self):
        self.manifest = Manifest.load(self.root)
        self.env = Environment(self.root, self.environ)
        self.manager = GitManager(self.manifest, self.env)

    def quiet(self, function, *args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return function(*args, **kwargs)

    def run_git(self, command, names=None, **kwargs):
        return self.quiet(self.manager.run, command, self.manifest.select(names), **kwargs)

    def create_archive(self, name, version=None):
        repo = self.manifest.by_name[name]
        package = json.loads((repo.path / "package.json").read_text())
        archive = self.root / "dist" / ".staging" / f"{package_stem(package['name'])}-fixture.tgz"
        archive.parent.mkdir(parents=True, exist_ok=True)
        packed = dict(package)
        if version:
            packed["version"] = version
        payload = json.dumps(packed).encode()
        with tarfile.open(archive, "w:gz") as target:
            item = tarfile.TarInfo("package/package.json")
            item.size = len(payload)
            target.addfile(item, io.BytesIO(payload))
        artifact = publish_archive(self.root, archive)
        archive.unlink()
        return artifact.latest

    def desktop_env(self):
        directory = self.root / "app path with spaces"
        directory.mkdir(exist_ok=True)
        (directory / "DeepSeek Harness.exe").write_bytes(b"fake executable")
        (directory / "resources").mkdir(exist_ok=True)
        (directory / "resources/app.asar").write_bytes(b"fake asar")
        profile = self.root / "profile with spaces"
        profile.mkdir(exist_ok=True)
        values = dict(self.environ, MRM_DSH_DESKTOP_DIR=str(directory), MRM_DSH_PROFILE_DIR=str(profile))
        return Environment(self.root, values, platform="windows")

    def test_manifest_bom_selection_and_dependency_order(self):
        selected = self.manifest.select([REVIEW], include_dependencies=True)
        self.assertEqual([repo.name for repo in self.manifest.ordered(selected)], [MANAGER, REVIEW])
        self.assertEqual([repo.name for repo in self.manifest.select([REVIEW])], [REVIEW])
        with self.assertRaises(ConfigError):
            self.manifest.select(["unknown"])

    def test_disabled_and_unknown_dependency(self):
        self.write_manifest(extra="enabled = false")
        self.reload()
        with self.assertRaises(ConfigError):
            self.manifest.select([REVIEW])
        self.write_manifest(review_dependency="missing")
        with self.assertRaises(ConfigError):
            Manifest.load(self.root)

    def test_dependency_cycles_rejected(self):
        path = self.root / "submodules.ini"
        text = path.read_text(encoding="utf-8-sig").replace("branch = main", f"branch = main\ndepends_on = {REVIEW}", 1)
        path.write_text(text)
        with self.assertRaises(ConfigError):
            Manifest.load(self.root)

    def test_invalid_config_fields_and_branch(self):
        for extra in ("mystery = true", "enabled = invalid", "branch = -unsafe"):
            with self.subTest(extra=extra):
                self.write_manifest(extra=extra)
                with self.assertRaises(ConfigError):
                    Manifest.load(self.root)

    def test_absolute_escape_and_overlap_rejected(self):
        for path in ("../outside", "dsh-plugins/../outside", "D:/outside/repo", "dsh-plugins", f"dsh-plugins/{MANAGER}", f"dsh-plugins/{MANAGER}/nested"):
            with self.subTest(path=path):
                self.write_manifest(review_path=path)
                with self.assertRaises(ConfigError):
                    Manifest.load(self.root)

    def test_link_escape_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.root / "dsh-plugins/escape"
        if os.name == "nt":
            result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
            if result.returncode:
                self.skipTest("系统不允许创建测试 junction")
        else:
            link.symlink_to(outside, target_is_directory=True)
        self.write_manifest(review_path="dsh-plugins/escape")
        with self.assertRaises(ConfigError):
            Manifest.load(self.root)

    def test_status_is_read_only_and_clone_reuses_existing(self):
        repo = self.manifest.by_name[MANAGER]
        before = self.git(repo.path, "rev-parse", "HEAD").stdout
        self.assertEqual(self.run_git("status"), 0)
        self.assertEqual(self.run_git("check"), 0)
        self.assertEqual(self.run_git("clone"), 0)
        self.assertEqual(self.git(repo.path, "rev-parse", "HEAD").stdout, before)
        self.assertFalse(self.git(repo.path, "status", "--porcelain").stdout)

    def test_clone_missing_dry_run_and_real_clone(self):
        name = "missing repo"
        with (self.root / "submodules.ini").open("a", encoding="utf-8") as target:
            target.write(f"\n[{name}]\npath = dsh-plugins/{name}\nurl = {self.urls[MANAGER]}\nbranch = main\nremote = upstream\n")
        self.reload()
        path = self.manifest.by_name[name].path
        self.assertEqual(self.run_git("clone", [name], dry_run=True), 0)
        self.assertFalse(path.exists())
        self.assertEqual(self.run_git("clone", [name]), 0)
        self.assertEqual(self.git(path, "remote").stdout.strip(), "upstream")
        self.assertEqual(self.run_git("check", [name]), 0)

    def test_nonempty_plain_directory_never_overwritten(self):
        plain = self.root / "dsh-plugins/plain"
        plain.mkdir()
        sentinel = plain / "keep.txt"
        sentinel.write_text("keep")
        self.write_manifest(review_path="dsh-plugins/plain")
        self.reload()
        self.assertEqual(self.run_git("clone", [REVIEW]), 1)
        self.assertEqual(sentinel.read_text(), "keep")

    def test_parent_git_is_not_adopted_as_child(self):
        self.git(self.root, "init")
        (self.root / "dsh-plugins/plain").mkdir()
        self.write_manifest(review_path="dsh-plugins/plain")
        self.reload()
        with self.assertRaises(OperationError):
            self.manager.inspect(self.manifest.by_name[REVIEW])

    def test_linked_worktree_git_file_supported(self):
        source = self.manifest.by_name[REVIEW].path
        target = self.root / "dsh-plugins/linked worktree"
        self.git(source, "worktree", "add", "-b", "worktree-main", str(target))
        self.write_manifest(review_path="dsh-plugins/linked worktree")
        path = self.root / "submodules.ini"
        path.write_text(path.read_text(encoding="utf-8-sig").replace("branch = main\ndepends_on", "branch = worktree-main\ndepends_on"))
        self.reload()
        self.assertTrue((target / ".git").is_file())
        self.assertEqual(self.run_git("check", [REVIEW]), 0)

    def test_dirty_pull_switch_and_dry_run_blocked(self):
        repo = self.manifest.by_name[MANAGER]
        (repo.path / "untracked.txt").write_text("dirty")
        for command in ("pull", "switch"):
            with self.subTest(command=command):
                self.assertEqual(self.run_git(command, [MANAGER], dry_run=True), 1)
        self.assertTrue((repo.path / "untracked.txt").exists())

    def test_config_branch_and_detached_head_block_push_pull(self):
        repo = self.manifest.by_name[MANAGER]
        self.git(repo.path, "switch", "-c", "feature")
        self.assertEqual(self.run_git("push", [MANAGER], dry_run=True), 1)
        self.git(repo.path, "switch", "--detach")
        self.assertEqual(self.run_git("pull", [MANAGER], dry_run=True), 1)
        self.assertEqual(self.run_git("switch", [MANAGER]), 0)
        self.assertEqual(self.git(repo.path, "branch", "--show-current").stdout.strip(), "main")

    def test_remote_and_push_url_mismatch_blocked(self):
        repo = self.manifest.by_name[MANAGER]
        self.git(repo.path, "remote", "set-url", "--push", "origin", self.urls[REVIEW])
        self.assertEqual(self.run_git("push", [MANAGER], dry_run=True), 1)
        self.git(repo.path, "remote", "set-url", "origin", self.urls[REVIEW])
        self.assertEqual(self.run_git("fetch", [MANAGER], dry_run=True), 1)

    def test_switch_creates_tracking_branch_from_explicit_remote(self):
        repo = self.manifest.by_name[MANAGER]
        self.git(repo.path, "switch", "-c", "other")
        self.git(repo.path, "branch", "-D", "main")
        self.assertEqual(self.run_git("switch", [MANAGER]), 0)
        self.assertEqual(self.git(repo.path, "rev-parse", "--abbrev-ref", "@{upstream}").stdout.strip(), "origin/main")

    def test_push_only_commits_then_pull_fast_forward(self):
        repo = self.manifest.by_name[MANAGER]
        (repo.path / "local.txt").write_text("local")
        self.git(repo.path, "add", "local.txt")
        self.git(repo.path, "commit", "-m", "local")
        (repo.path / "untracked.txt").write_text("not pushed")
        self.assertEqual(self.run_git("push", [MANAGER]), 0)
        seed = self.root / ("seed-" + MANAGER)
        self.git(seed, "pull", "--ff-only")
        self.assertTrue((seed / "local.txt").exists())
        self.assertFalse((seed / "untracked.txt").exists())
        (repo.path / "untracked.txt").unlink()
        (seed / "remote.txt").write_text("remote")
        self.git(seed, "add", "remote.txt")
        self.git(seed, "commit", "-m", "remote")
        self.git(seed, "push")
        self.assertEqual(self.run_git("fetch", [MANAGER]), 0)
        self.assertEqual(self.run_git("pull", [MANAGER]), 0)
        self.assertTrue((repo.path / "remote.txt").exists())

    def test_diverged_pull_never_merges_or_rebases(self):
        repo = self.manifest.by_name[MANAGER]
        (repo.path / "local.txt").write_text("local")
        self.git(repo.path, "add", "local.txt")
        self.git(repo.path, "commit", "-m", "local only")
        before = self.git(repo.path, "rev-parse", "HEAD").stdout
        seed = self.root / ("seed-" + MANAGER)
        self.git(seed, "pull", "--ff-only")
        (seed / "remote.txt").write_text("remote")
        self.git(seed, "add", "remote.txt")
        self.git(seed, "commit", "-m", "remote only")
        self.git(seed, "push")
        self.assertEqual(self.run_git("pull", [MANAGER]), 1)
        self.assertEqual(self.git(repo.path, "rev-parse", "HEAD").stdout, before)
        self.assertFalse((repo.path / ".git/MERGE_HEAD").exists())

    def test_unfinished_merge_blocks_writes(self):
        repo = self.manifest.by_name[MANAGER]
        (repo.path / ".git/MERGE_HEAD").write_text(self.git(repo.path, "rev-parse", "HEAD").stdout)
        self.assertEqual(self.run_git("push", [MANAGER], dry_run=True), 1)

    def test_fail_fast_stops_later_repository(self):
        runner = RecordingRunner()
        manager = GitManager(self.manifest, self.env, runner=runner)
        with patch.object(manager, "inspect", side_effect=[OperationError("bad"), None]) as inspect:
            self.assertEqual(self.quiet(manager.run, "batch", self.manifest.select(None), fail_fast=True, batch=["git", "log"]), 1)
            self.assertEqual(inspect.call_count, 1)
        with patch.object(manager, "inspect", side_effect=[OperationError("bad"), None]) as inspect:
            self.assertEqual(self.quiet(manager.run, "batch", self.manifest.select(None), batch=["git", "log"]), 1)
            self.assertEqual(inspect.call_count, 2)

    def test_all_git_dry_runs_preserve_head(self):
        repo = self.manifest.by_name[MANAGER]
        before = self.git(repo.path, "rev-parse", "HEAD").stdout
        for command in ("fetch", "pull", "push", "switch"):
            self.assertEqual(self.run_git(command, [MANAGER], dry_run=True), 0)
        self.assertEqual(self.run_git("batch", [MANAGER], dry_run=True, batch=["git", "reset", "--hard", "HEAD~1"]), 0)
        self.assertEqual(self.git(repo.path, "rev-parse", "HEAD").stdout, before)

    def test_build_dependency_order_and_failure_propagation(self):
        repos = self.manifest.select([REVIEW], include_dependencies=True)
        recorder = RecordingRunner(fail_name=MANAGER)
        tasks = PluginTasks(self.manifest, self.env, recorder)
        self.assertEqual(self.quiet(tasks.run, "build", repos), 1)
        self.assertEqual([cwd.name for _, cwd in recorder.calls], [MANAGER])
        recorder = RecordingRunner()
        tasks = PluginTasks(self.manifest, self.env, recorder)
        self.assertEqual(self.quiet(tasks.run, "build", repos), 0)
        self.assertEqual([cwd.name for _, cwd in recorder.calls], [MANAGER, REVIEW])
        task_environment = PluginTasks(self.manifest, self.env).runner.env
        self.assertEqual(task_environment["pnpm_config_verify_deps_before_run"], "warn")
        self.assertEqual(task_environment["MRM_DIST_DIR"], str(self.root / "dist"))

    def test_build_dry_run_never_calls_pnpm(self):
        recorder = RecordingRunner()
        tasks = PluginTasks(self.manifest, self.env, recorder)
        self.assertEqual(self.quiet(tasks.run, "build", self.manifest.select(None), dry_run=True), 0)
        self.assertEqual(recorder.calls, [])

    def test_package_checksum_and_identity(self):
        archive = self.create_archive(MANAGER)
        self.assertEqual(archive.parent, self.root / "dist/latest")
        self.assertFalse((self.manifest.by_name[MANAGER].path / "dist").exists())
        self.assertEqual(verified_archive(self.root, self.manifest.by_name[MANAGER])[0], archive)
        archive.write_bytes(archive.read_bytes() + b"tampered")
        with self.assertRaises(OperationError):
            verified_archive(self.root, self.manifest.by_name[MANAGER])
        self.create_archive(MANAGER, version="9.0.0")
        with self.assertRaises(OperationError):
            verified_archive(self.root, self.manifest.by_name[MANAGER])
        archive = self.create_archive(MANAGER)
        Path(str(archive) + ".sha256").write_bytes(b"\xff")
        with self.assertRaises(OperationError):
            verified_archive(self.root, self.manifest.by_name[MANAGER])
        repo = self.manifest.by_name[MANAGER]
        package = json.loads((repo.path / "package.json").read_text())
        package["scripts"] = None
        (repo.path / "package.json").write_text(json.dumps(package))
        with self.assertRaises(OperationError):
            package_info(repo)

    def test_plain_pack_uses_shared_dist_and_generates_checksum(self):
        repo = self.manifest.by_name[MANAGER]
        package = package_info(repo)
        del package["scripts"]["test:pack"]
        (repo.path / "package.json").write_text(json.dumps(package))
        recorder = RecordingRunner()
        original_run = recorder.run

        def pack(args, cwd, timeout):
            result = original_run(args, cwd, timeout)
            archive = Path(args[-1]) / f"{package_stem(package['name'])}-{package['version']}.tgz"
            payload = json.dumps(package).encode()
            with tarfile.open(archive, "w:gz") as target:
                item = tarfile.TarInfo("package/package.json")
                item.size = len(payload)
                target.addfile(item, io.BytesIO(payload))
            return result

        with patch.object(recorder, "run", side_effect=pack):
            tasks = PluginTasks(self.manifest, self.env, recorder)
            self.assertEqual(self.quiet(tasks.run, "pack", [repo]), 0)
        args, cwd = recorder.calls[0]
        self.assertEqual(args[-3:-1], ["pack", "--pack-destination"])
        self.assertTrue(Path(args[-1]).is_relative_to(self.root / "dist/.staging"))
        self.assertFalse(Path(args[-1]).exists())
        self.assertEqual(cwd, repo.path)
        self.assertEqual(verified_archive(self.root, repo)[0].parent, self.root / "dist/latest")
        self.assertFalse((repo.path / "dist").exists())

    def test_dsh_init_is_non_overwriting_and_checks_consistency(self):
        self.assertEqual(self.quiet(init_dsh, self.manifest, dry_run=True), 0)
        self.assertFalse((self.root / PROJECT_FILE).exists())
        self.assertEqual(self.quiet(init_dsh, self.manifest), 0)
        self.assertTrue((self.root / "dsh-multi-git-repo.json").is_file())
        self.assertEqual(self.quiet(check_dsh, self.manifest), 0)
        path = self.root / PROJECT_FILE
        original = path.read_bytes()
        self.assertEqual(self.quiet(init_dsh, self.manifest), 0)
        self.assertEqual(path.read_bytes(), original)
        config = json.loads(original)
        config["repositories"][0]["name"] = "renamed"
        path.write_text(json.dumps(config))
        self.assertEqual(self.quiet(check_dsh, self.manifest), 1)

    def test_dsh_strict_schema_rejects_old_version_and_unknown_fields(self):
        for changed in ({"version": 1}, {"mystery": True}, {"includeProjectRoot": "false"}, {"discovery": {"containers": ["../outside"]}}):
            config = expected_config(self.manifest)
            config.update(changed)
            (self.root / PROJECT_FILE).write_text(json.dumps(config))
            with self.subTest(changed=changed), self.assertRaises(ConfigError):
                check_dsh(self.manifest)

    def test_desktop_path_changes_in_configuration(self):
        env = self.desktop_env()
        first = env.desktop_executable()
        other = self.root / "another installation"
        other.mkdir()
        (other / "DeepSeek Harness.exe").write_bytes(b"another fake")
        env = Environment(self.root, dict(self.environ, MRM_DSH_DESKTOP_DIR=str(other)), platform="windows")
        self.assertNotEqual(first, env.desktop_executable())
        self.assertEqual(env.desktop_executable().parent, other)
        env = Environment(self.root, self.environ, platform="windows")
        self.assertEqual(self.quiet(env.info), 0)
        self.assertEqual(self.quiet(env.info, desktop=True), 1)

    def test_desktop_install_dry_run_and_actual_dispatch(self):
        env = self.desktop_env()
        for name in (MANAGER, REVIEW):
            self.create_archive(name)
        runner = RecordingRunner()
        desktop = Desktop(self.manifest, env, runner)
        with patch("pythonProject.desktop.desktop_processes", return_value=[]):
            self.assertEqual(self.quiet(desktop.install, self.manifest.select(None), dry_run=True), 0)
            self.assertFalse((self.root / ".cache").exists())
            self.assertEqual(runner.calls, [])
            self.assertEqual(self.quiet(desktop.install, self.manifest.select(None), dry_run=False), 0)
        args, _ = runner.calls[0]
        self.assertIn("--force", args)
        self.assertIn(str(env.profile_directory()), args)
        paths = [Path(arg.split("@file:", 1)[1]) for arg in args if "@file:" in arg]
        self.assertEqual(len(paths), 2)
        self.assertTrue(all(path.is_file() and path.parent == self.root / "dist" for path in paths))
        self.assertEqual(paths, [load_latest(self.root, name.lower()).history for name in (MANAGER, REVIEW)])
        self.assertFalse((self.root / ".cache").exists())

    def test_desktop_install_historical_versions_and_peer_mismatch(self):
        env = self.desktop_env()
        for name in (MANAGER, REVIEW):
            self.create_archive(name)
        old_manager = load_latest(self.root, MANAGER).history
        old_review = load_latest(self.root, REVIEW.lower()).history
        for name in (MANAGER, REVIEW):
            repo = self.manifest.by_name[name]
            package = package_info(repo)
            package["version"] = "2.0.0"
            if name == REVIEW:
                package["peerDependencies"][MANAGER] = "2.0.0"
            (repo.path / "package.json").write_text(json.dumps(package))
            self.create_archive(name)
        recorder = RecordingRunner()
        desktop = Desktop(self.manifest, env, recorder)
        with patch("pythonProject.desktop.desktop_processes", return_value=[]):
            with self.assertRaises(OperationError):
                desktop.install(self.manifest.select(None), False, [old_review.name])
            self.assertFalse(recorder.calls)
            self.assertEqual(self.quiet(desktop.install, self.manifest.select(None), False,
                                        [old_manager.name, old_review.name]), 0)
        actual = [Path(arg.split("@file:", 1)[1]) for arg in recorder.calls[0][0] if "@file:" in arg]
        self.assertEqual(actual, [old_manager, old_review])

    def test_desktop_running_blocks_install_and_patch(self):
        desktop = Desktop(self.manifest, self.desktop_env(), RecordingRunner())
        with patch("pythonProject.desktop.desktop_processes", return_value=[123]):
            with self.assertRaises(OperationError):
                desktop.install(self.manifest.select(None), dry_run=True)
            with self.assertRaises(OperationError):
                desktop.patch(dry_run=True, check_only=False)

    def test_desktop_patch_uses_configured_asar_and_check_flag(self):
        env = self.desktop_env()
        recorder = RecordingRunner()
        desktop = Desktop(self.manifest, env, recorder)
        self.assertEqual(self.quiet(desktop.patch, dry_run=False, check_only=True), 0)
        args, _ = recorder.calls[0]
        self.assertEqual(args[-1], "--check")
        self.assertIn(str(env.desktop_directory() / "resources/app.asar"), args)
        recorder.calls.clear()
        with patch("pythonProject.desktop.desktop_processes", return_value=[]):
            self.assertEqual(self.quiet(desktop.patch, dry_run=True, check_only=False), 0)
        self.assertFalse(recorder.calls)

    def test_cli_exit_codes_batch_and_interrupt(self):
        with patch("envBuild.project_root", return_value=self.root):
            self.assertEqual(self.quiet(envBuild.main, ["status"]), 0)
            self.assertEqual(self.quiet(envBuild.main, ["status", "--repo", "missing"]), 2)
            self.assertEqual(self.quiet(envBuild.main, ["batch"]), 2)
            self.assertEqual(self.quiet(envBuild.main, ["batch", "--repo", MANAGER, "--dry-run", "--", "git", "log", "-1"]), 0)
            with patch("envBuild.GitManager.run", side_effect=KeyboardInterrupt):
                self.assertEqual(self.quiet(envBuild.main, ["status"]), 130)
        args = envBuild.parser().parse_args(["batch", "--", "git", "log", "--oneline"])
        self.assertEqual(args.arguments[-1], "--oneline")


class EnvironmentAndRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mrm env ")
        self.root = Path(self.temporary.name).resolve()
        self.addCleanup(self.temporary.cleanup)

    def test_overlay_priority_and_no_rewrite(self):
        shared = self.root / "envVar_v2.ini"
        shared.write_text("[envVar_all]\nMRM_GIT_TIMEOUT_SECONDS = 20\n[envVar_windows]\nMRM_GIT_TIMEOUT_SECONDS = 30\n", encoding="utf-8-sig")
        local = self.root / "envVar_v2.local.ini"
        local.write_text("[envVar_all]\nMRM_GIT_TIMEOUT_SECONDS = 40\n")
        before = shared.read_bytes(), local.read_bytes()
        env = Environment(self.root, {}, platform="windows")
        self.assertEqual(env.timeout(), 40)
        env = Environment(self.root, {"MRM_GIT_TIMEOUT_SECONDS": "50"}, platform="windows")
        self.assertEqual(env.timeout(), 50)
        self.assertEqual((shared.read_bytes(), local.read_bytes()), before)

    def test_init_preserves_existing_file_and_rejects_invalid_timeout(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(init_environment(self.root))
            path = self.root / "envVar_v2.ini"
            before = path.read_bytes()
            self.assertFalse(init_environment(self.root))
        self.assertEqual(path.read_bytes(), before)
        with self.assertRaises(ConfigError):
            Environment(self.root, {"MRM_TASK_TIMEOUT_SECONDS": "0"})

    def test_missing_optional_tools_do_not_block_git_info(self):
        values = {"MRM_GIT_EXE": GIT, "PATH": ""}
        env = Environment(self.root, values)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(env.info(), 0)
        with self.assertRaises(OperationError):
            env.pnpm_command()

    def test_variable_expansion_and_invalid_tool_no_fallback(self):
        values = {"TEST_APP": str(self.root), "MRM_DSH_DESKTOP_DIR": "%TEST_APP%/app space"}
        env = Environment(self.root, values)
        self.assertEqual(env.path("MRM_DSH_DESKTOP_DIR"), self.root / "app space")
        env = Environment(self.root, {"MRM_NODE_EXE": str(self.root / "missing"), "PATH": os.environ.get("PATH", "")})
        with self.assertRaises(OperationError):
            env.tool("MRM_NODE_EXE", "node")
        env = Environment(self.root, {"MRM_DSH_DESKTOP_DIR": "$UNKNOWN_ENV_VAR/app"})
        with self.assertRaises(ConfigError):
            env.path("MRM_DSH_DESKTOP_DIR")

    def test_windows_pnpm_shim_resolves_node_js_without_shell(self):
        shim = self.root / "tools space/fallback/pnpm.cmd"
        script = self.root / "tools space/node/pnpm.cjs"
        shim.parent.mkdir(parents=True)
        script.parent.mkdir()
        script.write_text("// fixture")
        shim.write_text('@echo off\nset "pnpm_config_pm_on_fail=ignore"\n"%~dp0..\\node\\pnpm.cjs" %*\n')
        env = Environment(self.root, {"MRM_PNPM_EXE": str(shim), "MRM_NODE_EXE": sys.executable})
        self.assertEqual(env.pnpm_command(), [str(Path(sys.executable).resolve()), str(script)])
        self.assertEqual(env.child_env()["pnpm_config_pm_on_fail"], "ignore")

    def test_parent_git_context_does_not_redirect_child_commands(self):
        env = Environment(self.root, {"GIT_DIR": "outside", "GIT_WORK_TREE": "outside",
                                     "GIT_INDEX_FILE": "outside-index", "SSH_AUTH_SOCK": "keep-agent"})
        child = env.child_env()
        self.assertNotIn("GIT_DIR", child)
        self.assertNotIn("GIT_WORK_TREE", child)
        self.assertNotIn("GIT_INDEX_FILE", child)
        self.assertEqual(child["SSH_AUTH_SOCK"], "keep-agent")

    def test_http_credentials_rejected_and_ssh_username_preserved(self):
        path = self.root / "submodules.ini"
        for url in ("https://token@example.invalid/repo.git", "https://user:secret@example.invalid/repo.git", "ssh://git:secret@example.invalid/repo.git"):
            path.write_text(f"[repo]\npath = dsh-plugins/repo\nurl = {url}\nbranch = main\n")
            with self.subTest(url=url), self.assertRaises(ConfigError):
                Manifest.load(self.root)
        path.write_text("[repo]\npath = dsh-plugins/repo\nurl = ssh://git@example.invalid:443/repo.git\nbranch = main\n")
        self.assertEqual(Manifest.load(self.root).repos[0].remote, "origin")

    def test_runner_preserves_literal_metacharacters(self):
        runner = Runner()
        values = ['space path', 'a&b', '$HOME', 'literal;command', '"quoted"']
        result = runner.run([sys.executable, "-c", "import json,sys;print(json.dumps(sys.argv[1:]))", *values], self.root, 5, capture=True, show=False)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), values)

    def test_timeout_terminates_process_and_reports_nonzero(self):
        started = time.monotonic()
        with contextlib.redirect_stdout(io.StringIO()):
            result = Runner().run([sys.executable, "-c", "import time;time.sleep(10)"], self.root, 1, capture=True, show=False)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.returncode, 124)
        self.assertLess(time.monotonic() - started, 8)

    def test_cwd_independent_real_cli_list(self):
        entry = Path(envBuild.__file__).resolve()
        result = subprocess.run([sys.executable, str(entry), "list"], cwd=self.root, capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(MANAGER, result.stdout)
        self.assertIn(REVIEW, result.stdout)


if __name__ == "__main__":
    unittest.main()
