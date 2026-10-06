"""从环境配置解析 Desktop，提供明确的启动、安装和适配命令。"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .common import OperationError, Runner, command_text, require_success
from .env_config import Environment
from .git_manager import GitManager
from .package_artifacts import load_history, load_latest
from .plugin_tasks import package_info
from .repo_config import Manifest, RepoSpec


@contextmanager
def refresh_installed_manifests(profile: Path, names: list[str]):
    """让 hoisted 安装也重新导入同版本本地包；失败时恢复原包信息。"""
    profile = profile.resolve()
    manifests = []
    for name in names:
        manifest = profile / "node_modules" / name / "package.json"
        if not manifest.exists() and not manifest.is_symlink():
            continue
        if manifest.is_symlink() or not manifest.is_file() or not manifest.resolve().is_relative_to(profile):
            raise OperationError(f"已安装插件的包信息不是 Profile 内的普通文件: {manifest}")
        manifests.append(manifest.resolve())
    backups = []
    try:
        for manifest in manifests:
            descriptor, filename = tempfile.mkstemp(prefix=".mrm-package-", suffix=".json", dir=profile)
            os.close(descriptor)
            backup = Path(filename)
            try:
                manifest.replace(backup)
            except BaseException:
                backup.unlink(missing_ok=True)
                raise
            backups.append((manifest, backup))
        yield
    except BaseException:
        for manifest, backup in reversed(backups):
            if not manifest.parent.resolve().is_relative_to(profile):
                raise OperationError(f"恢复插件包信息时目录已逃出 Profile；备份保留在 {backup}")
            manifest.parent.mkdir(parents=True, exist_ok=True)
            try:
                backup.replace(manifest)
            except OSError as error:
                raise OperationError(f"无法恢复插件包信息；备份保留在 {backup}") from error
        raise
    else:
        for _, backup in backups:
            backup.unlink()


def desktop_processes(executable: Path) -> list[int]:
    """只读取进程列表，不结束用户应用；调用者据此拒绝安装／修改。"""
    names = {executable.name.casefold(), "deepseek harness.exe"}
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class ProcessEntry(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        for method in (kernel.Process32FirstW, kernel.Process32NextW):
            method.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
            method.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.CreateToolhelp32Snapshot(2, 0)
        if handle == ctypes.c_void_p(-1).value:
            raise OperationError(f"无法检查 Desktop 进程: Windows error {ctypes.get_last_error()}")
        found: list[int] = []
        try:
            entry = ProcessEntry()
            entry.dwSize = ctypes.sizeof(entry)
            present = kernel.Process32FirstW(handle, ctypes.byref(entry))
            while present:
                if entry.szExeFile.casefold() in names:
                    found.append(entry.th32ProcessID)
                present = kernel.Process32NextW(handle, ctypes.byref(entry))
            if ctypes.get_last_error() not in (0, 18):
                raise OperationError("Desktop 进程枚举失败，无法确认应用已退出")
            return found
        finally:
            kernel.CloseHandle(handle)
    proc = Path("/proc")
    if not proc.is_dir():
        raise OperationError("当前平台无法可靠检查 Desktop 进程")
    found = []
    for entry in proc.iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            target = (entry / "exe").resolve(strict=True)
            if target == executable.resolve() or target.name.casefold() in names:
                found.append(int(entry.name))
        except (OSError, RuntimeError):
            continue
    return found


class Desktop:
    def __init__(self, manifest: Manifest, env: Environment, runner: Runner | None = None):
        self.manifest = manifest
        self.env = env
        self.runner = runner

    def _closed(self) -> None:
        executable = self.env.desktop_executable()
        running = desktop_processes(executable)
        if running:
            raise OperationError(f"请先退出 Desktop（包含托盘进程），当前 PID: {running}")

    def start(self, dry_run: bool) -> int:
        executable = self.env.desktop_executable()
        if desktop_processes(executable):
            print("Desktop 已在运行，不重复启动。")
            return 0
        print(f"{'[预览] ' if dry_run else ''}启动 Desktop: {executable}")
        if dry_run:
            return 0
        flags = {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        process = subprocess.Popen(
            [str(executable)], cwd=str(executable.parent), env=self.env.child_env(),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **flags,
        )
        print(f"已提交启动请求，PID={process.pid}。")
        return 0

    def check_install_environment(self) -> tuple[Path, list[str]]:
        """完整流程可在编译前检查安装环境，不要求安装包已存在。"""
        self._closed()
        profile = self.env.profile_directory()
        if not profile.is_dir():
            raise OperationError(f"Desktop Profile 不存在，请先启动 Desktop 或配置 MRM_DSH_PROFILE_DIR: {profile}")
        if not (profile / "node_modules").resolve().is_relative_to(profile.resolve()):
            raise OperationError("Profile node_modules 真实路径逃出 Profile，拒绝强制刷新")
        return profile, self.env.pnpm_command()

    def install(self, repos: list[RepoSpec], dry_run: bool, archive_names: list[str] | None = None) -> int:
        profile, pnpm = self.check_install_environment()
        git = GitManager(self.manifest, self.env)
        ordered = self.manifest.ordered(repos)
        names = {package_info(repo)["name"] for repo in ordered}
        overrides = {}
        for filename in archive_names or []:
            artifact = load_history(self.manifest.root, filename)
            name = artifact.package["name"]
            if name not in names or name in overrides:
                raise OperationError(f"历史包不属于所选插件或重复指定: {name}")
            overrides[name] = artifact
        archives = []
        packages = {}
        for repo in ordered:
            git.inspect(repo)
            name = package_info(repo)["name"]
            artifact = overrides.get(name) or load_latest(self.manifest.root, name)
            package = artifact.package
            if package["name"] in packages:
                raise OperationError(f"重复插件包名称: {package['name']}")
            packages[package["name"]] = package
            archives.append(artifact)
            print(f"{repo.name}: {package['version']} ({'指定历史包' if name in overrides else 'latest'})\n  {artifact.history}")
        for package in packages.values():
            for name, required in package.get("peerDependencies", {}).items():
                if name.startswith("dsh-"):
                    dependency = packages.get(name)
                    if dependency is None:
                        raise OperationError(f"{package['name']} 安装需要配套包 {name}；请一并选择")
                    if re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.+-]+)?", required) and dependency["version"] != required:
                        raise OperationError(f"配套包 {name} 版本不匹配: 需要 {required}，实际 {dependency['version']}")
        # 同版本包会覆盖同一路径，安装时强制刷新本地包以避免旧缓存。
        targets = [f"{artifact.package['name']}@file:{artifact.history.as_posix()}" for artifact in archives]
        # 明确包名，避免 pnpm 为推断本地包身份先读取 Profile 中失效的旧 tgz。
        args = pnpm + ["--dir", str(profile), "add", "--force", "--prefer-offline", *targets]
        print(f"{'[预览] ' if dry_run else ''}安装到 {profile}\n  $ {command_text(args)}")
        if dry_run:
            print("包身份、两份副本和 SHA256 已验证；预览未修改 Profile。")
            return 0
        # 全部预检通过后，调用安装前复核历史文件及应用状态。
        self._closed()
        for artifact in archives:
            current = load_history(self.manifest.root, artifact.history)
            if current.digest != artifact.digest:
                raise OperationError(f"安装包在预检后发生变化: {artifact.history}")
        self._closed()
        runner = self.runner or Runner(self.env.child_env())
        # pnpm hoisted 模式即使 --force 也会跳过版本与路径相同的已安装包。
        # 临时移出所选包的 manifest，触发完整导入；不改共享缓存或其他插件。
        with refresh_installed_manifests(profile, list(packages)):
            require_success(runner.run(args, self.manifest.root, self.env.timeout(task=True)))
        print("安装完成；请在 Desktop 插件页确认所需插件已启用。")
        return 0

    def patch(self, dry_run: bool, check_only: bool) -> int:
        directory = self.env.desktop_directory()
        archive = directory / "resources/app.asar"
        if not archive.is_file():
            raise OperationError(f"Desktop ASAR 不存在: {archive}")
        repo = self.manifest.by_name.get("dsh-multi-git-repo-manager")
        if repo is None or not repo.enabled:
            raise OperationError("清单缺少已启用的 dsh-multi-git-repo-manager")
        GitManager(self.manifest, self.env).inspect(repo)
        script = repo.path / "scripts/patch-desktop-directory-picker.mjs"
        if not script.is_file():
            raise OperationError(f"目录选择适配脚本不存在: {script}")
        if not check_only:
            self._closed()
        args = [self.env.tool("MRM_NODE_EXE", "node"), str(script), str(archive)]
        if check_only:
            args.append("--check")
        print(f"{'[预览] ' if dry_run else ''}$ {command_text(args)}")
        if not dry_run:
            runner = self.runner or Runner(self.env.child_env())
            require_success(runner.run(args, repo.path, self.env.timeout(task=True)))
        return 0

    def run(self, action: str, repos: list[RepoSpec], dry_run: bool = False,
            archive_names: list[str] | None = None) -> int:
        if action == "info":
            return self.env.info(desktop=True)
        if action == "start":
            return self.start(dry_run)
        if action == "install":
            return self.install(repos, dry_run, archive_names)
        return self.patch(dry_run, check_only=action == "check-patch")
