"""验证 desktop stop 的进程结束策略：先请求退出，宽限期后再强制，且不误报成功。"""
import unittest
from pathlib import Path
from unittest.mock import patch

from pythonProject.common import OperationError
from pythonProject.desktop import Desktop

EXECUTABLE = Path("C:/fake/DeepSeek Harness.exe")


class FakeEnvironment:
    def desktop_executable(self):
        return EXECUTABLE


class DesktopStopTests(unittest.TestCase):
    def setUp(self):
        self.desktop = Desktop(manifest=None, env=FakeEnvironment())

    def test_not_running_reports_success(self):
        with patch("pythonProject.desktop.desktop_processes", return_value=[]):
            self.assertEqual(self.desktop.stop(False), 0)

    def test_dry_run_never_ends_processes(self):
        with patch("pythonProject.desktop.desktop_processes", return_value=[11, 22]), \
                patch.object(Desktop, "_request_stop") as request:
            self.assertEqual(self.desktop.stop(True), 0)
        request.assert_not_called()

    def test_graceful_exit_does_not_force(self):
        with patch("pythonProject.desktop.desktop_processes", return_value=[11, 22]), \
                patch.object(Desktop, "_request_stop") as request, \
                patch.object(Desktop, "_wait_exit", return_value=[]):
            self.assertEqual(self.desktop.stop(False), 0)
        self.assertEqual([call.args[1] for call in request.call_args_list], [False])

    def test_expired_grace_period_escalates_to_force(self):
        with patch("pythonProject.desktop.desktop_processes", return_value=[11]), \
                patch.object(Desktop, "_request_stop") as request, \
                patch.object(Desktop, "_wait_exit", side_effect=[[11], []]):
            self.assertEqual(self.desktop.stop(False), 0)
        self.assertEqual([call.args[1] for call in request.call_args_list], [False, True])

    def test_surviving_process_is_reported_as_failure(self):
        with patch("pythonProject.desktop.desktop_processes", return_value=[11]), \
                patch.object(Desktop, "_request_stop"), \
                patch.object(Desktop, "_wait_exit", return_value=[11]):
            with self.assertRaises(OperationError):
                self.desktop.stop(False)


if __name__ == "__main__":
    unittest.main()
