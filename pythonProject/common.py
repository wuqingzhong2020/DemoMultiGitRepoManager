"""工程根定位、子进程生命周期和逐仓结果汇总。"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


class ConfigError(ValueError):
    """参数或配置结构错误，入口返回 2。"""


class OperationError(RuntimeError):
    """工具、路径或仓库状态阻断，入口返回 1。"""


@dataclass
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


class CommandError(OperationError):
    def __init__(self, result: CommandResult):
        self.result = result
        reason = "命令超时" if result.timed_out else f"命令失败，返回码 {result.returncode}"
        super().__init__(f"{reason}: {(result.stderr or result.stdout).strip()}")


@dataclass
class RepoResult:
    name: str
    state: str
    message: str
    elapsed: float = 0.0


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def command_text(args: Sequence[str]) -> str:
    return subprocess.list2cmdline([str(arg) for arg in args])


class Runner:
    """使用参数数组执行；超时或 Ctrl+C 时结束本次整个进程组。"""

    def __init__(self, env: Mapping[str, str] | None = None):
        self.env = dict(os.environ if env is None else env)

    @staticmethod
    def _stop(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def run(self, args: Sequence[str], cwd: Path, timeout: int,
            capture: bool = False, show: bool = True) -> CommandResult:
        command = [str(arg) for arg in args]
        if show:
            print(f"  cwd: {cwd}\n  $ {command_text(command)}", flush=True)
        options = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                   if os.name == "nt" else {"start_new_session": True})
        try:
            process = subprocess.Popen(
                command, cwd=str(cwd), env=self.env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE if capture else None,
                stderr=subprocess.PIPE if capture else None,
                encoding="utf-8", errors="replace", **options,
            )
        except OSError as error:
            raise OperationError(f"无法启动 {command[0]}: {error}") from error
        try:
            if capture:
                stdout, stderr = process.communicate(timeout=timeout)
            else:
                started = time.monotonic()
                while True:
                    remaining = timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, timeout)
                    try:
                        process.wait(timeout=min(15, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        if time.monotonic() - started >= timeout:
                            raise
                        print(f"  [运行中] 已耗时 {int(time.monotonic() - started)} 秒", flush=True)
                stdout, stderr = "", ""
            return CommandResult(process.returncode, stdout or "", stderr or "")
        except subprocess.TimeoutExpired:
            self._stop(process)
            stdout, stderr = process.communicate() if capture else ("", "")
            print(f"  [超时] {timeout} 秒", flush=True)
            return CommandResult(124, stdout or "", stderr or "", True)
        except KeyboardInterrupt:
            self._stop(process)
            raise


def require_success(result: CommandResult) -> CommandResult:
    if result.returncode != 0:
        raise CommandError(result)
    return result


def summarize(results: Sequence[RepoResult]) -> int:
    print("\n执行汇总：")
    for result in results:
        print(f"[{result.state}] {result.name} ({result.elapsed:.2f}s): {result.message}")
    counts = {state: sum(r.state == state for r in results)
              for state in ("成功", "预览", "跳过", "阻断", "失败")}
    print("；".join(f"{state} {count}" for state, count in counts.items()))
    return int(any(r.state in ("阻断", "失败") for r in results))
