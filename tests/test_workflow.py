"""验证完整命令的默认流程、显式依赖安装、失败停止及只读预览。"""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import envBuild
from pythonProject.common import OperationError
from pythonProject.desktop import Desktop
from pythonProject.plugin_tasks import PluginTasks


MANAGER = "dsh-multi-git-repo-manager"
REVIEW = "dsh-file-review-tab-Multi-git-repository"


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mrm workflow ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        # 消费者在清单中排在前面，用于验证流程仍按依赖顺序执行。
        (self.root / "submodules.ini").write_text(
            f"[{REVIEW}]\npath = dsh-plugins/{REVIEW}\nurl = review.git\n"
            f"branch = main\ndepends_on = {MANAGER}\n\n"
            f"[{MANAGER}]\npath = dsh-plugins/{MANAGER}\nurl = manager.git\nbranch = main\n",
            encoding="utf-8",
        )
        self.events = []
        self.desktop = Mock(spec=Desktop)
        self.tasks = Mock(spec=PluginTasks)
        self.failures = {}
        self.task_calls = []
        self.desktop.check_install_environment.side_effect = (
            lambda require_closed=True: self.events.append("preflight")
        )

        def stop(dry_run):
            self.events.append("stop-preview" if dry_run else "stop")
            return 0

        def task(phase, repos, dry_run, fail_fast):
            self.events.append(phase)
            self.task_calls.append((phase, [repo.name for repo in repos], dry_run, fail_fast))
            return self.failures.get(phase, 0)

        def install(repos, dry_run):
            self.events.append("install")
            self.assertEqual([repo.name for repo in repos], [MANAGER, REVIEW])
            self.assertFalse(dry_run)
            return 0

        def start(dry_run):
            self.events.append("start-preview" if dry_run else "start")
            return 0

        self.tasks.run.side_effect = task
        self.desktop.stop.side_effect = stop
        self.desktop.install.side_effect = install
        self.desktop.start.side_effect = start
        desktop_patch = patch("pythonProject.workflow.Desktop", return_value=self.desktop)
        tasks_patch = patch("pythonProject.workflow.PluginTasks", return_value=self.tasks)
        root_patch = patch("envBuild.project_root", return_value=self.root)
        self.desktop_class = desktop_patch.start()
        self.tasks_class = tasks_patch.start()
        root_patch.start()
        for patcher in (desktop_patch, tasks_patch, root_patch):
            self.addCleanup(patcher.stop)

    def invoke(self, *arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = envBuild.main(["rebuild-install", *arguments])
        return result, output.getvalue()

    def test_default_build_pack_install_start_without_deps(self):
        result, _ = self.invoke()
        self.assertEqual(result, 0)
        self.assertEqual(self.events, ["preflight", "build", "pack", "install", "start"])
        for _, names, dry_run, fail_fast in self.task_calls:
            self.assertEqual(names, [MANAGER, REVIEW])
            self.assertFalse(dry_run)
            self.assertTrue(fail_fast)

    def test_default_run_never_stops_desktop(self):
        result, _ = self.invoke()
        self.assertEqual(result, 0)
        self.desktop.stop.assert_not_called()
        self.desktop.check_install_environment.assert_called_once_with(require_closed=True)

    def test_stop_desktop_closes_application_before_build_and_starts_it_again(self):
        result, _ = self.invoke("--stop-desktop")
        self.assertEqual(result, 0)
        self.assertEqual(self.events, ["stop", "preflight", "build", "pack", "install", "start"])
        self.desktop.stop.assert_called_once_with(dry_run=False)

    def test_dry_run_stop_desktop_previews_closing_without_requiring_exit(self):
        result, output = self.invoke("--dry-run", "--stop-desktop")
        self.assertEqual(result, 0)
        self.assertEqual(self.events, ["stop-preview", "preflight", "build", "pack", "start-preview"])
        self.desktop.stop.assert_called_once_with(dry_run=True)
        self.desktop.check_install_environment.assert_called_once_with(require_closed=False)
        self.desktop.install.assert_not_called()
        self.assertIn("desktop install", output)

    def test_stop_failure_prevents_build_and_installation(self):
        self.desktop.stop.side_effect = OperationError("cannot close Desktop")
        result, output = self.invoke("--stop-desktop")
        self.assertEqual(result, 1)
        self.assertEqual(self.events, [])
        self.tasks_class.assert_not_called()
        self.desktop.install.assert_not_called()
        self.desktop.start.assert_not_called()
        self.assertIn("cannot close Desktop", output)

    def test_with_deps_and_consumer_selection_includes_dependency(self):
        result, _ = self.invoke("--with-deps", "--repo", REVIEW)
        self.assertEqual(result, 0)
        self.assertEqual(self.events, ["preflight", "deps", "build", "pack", "install", "start"])
        self.assertTrue(all(names == [MANAGER, REVIEW] for _, names, _, _ in self.task_calls))

    def test_each_task_failure_stops_remaining_work_and_installation(self):
        for phase in ["deps", "build", "pack"]:
            with self.subTest(phase=phase):
                self.events.clear()
                self.failures = {phase: 1}
                result, output = self.invoke("--with-deps")
                self.assertEqual(result, 1)
                self.assertEqual(self.events[-1], phase)
                self.assertNotIn("install", self.events)
                self.assertNotIn("start", self.events)
                self.assertIn("流程停止", output)

    def test_install_exception_prevents_start(self):
        self.desktop.install.side_effect = OperationError("install failed")
        result, output = self.invoke()
        self.assertEqual(result, 1)
        self.desktop.start.assert_not_called()
        self.assertIn("install failed", output)

    def test_install_nonzero_result_prevents_start(self):
        self.desktop.install.side_effect = None
        self.desktop.install.return_value = 1
        result, output = self.invoke()
        self.assertEqual(result, 1)
        self.desktop.start.assert_not_called()
        self.assertIn("未启动 Desktop", output)

    def test_preflight_failure_prevents_any_build_task(self):
        self.desktop.check_install_environment.side_effect = OperationError("Desktop running")
        result, _ = self.invoke()
        self.assertEqual(result, 1)
        self.tasks_class.assert_not_called()
        self.desktop.install.assert_not_called()
        self.desktop.start.assert_not_called()

    def test_dry_run_needs_no_existing_archives_and_does_not_install(self):
        before = (self.root / "submodules.ini").read_bytes()
        result, output = self.invoke("--dry-run")
        self.assertEqual(result, 0)
        self.assertEqual(self.events, ["preflight", "build", "pack", "start-preview"])
        self.assertTrue(all(dry_run for _, _, dry_run, _ in self.task_calls))
        self.desktop.install.assert_not_called()
        self.assertFalse((self.root / "dist").exists())
        self.assertEqual((self.root / "submodules.ini").read_bytes(), before)
        self.assertIn("desktop install", output)
        self.assertIn("实际安装时校验版本和 SHA256", output)

    def test_dry_run_with_deps_previews_deps_explicitly(self):
        result, _ = self.invoke("--dry-run", "--with-deps", "--repo", REVIEW)
        self.assertEqual(result, 0)
        self.assertEqual(self.events, ["preflight", "deps", "build", "pack", "start-preview"])
        self.assertTrue(all(dry_run and fail_fast for _, _, dry_run, fail_fast in self.task_calls))
        self.desktop.install.assert_not_called()

    def test_interrupt_stops_workflow_with_exit_code_130(self):
        self.tasks.run.side_effect = KeyboardInterrupt()
        result, _ = self.invoke()
        self.assertEqual(result, 130)
        self.desktop.install.assert_not_called()
        self.desktop.start.assert_not_called()

    def test_invalid_repository_is_rejected_before_preflight(self):
        result, _ = self.invoke("--repo", "unknown")
        self.assertEqual(result, 2)
        self.desktop_class.assert_not_called()


if __name__ == "__main__":
    unittest.main()
