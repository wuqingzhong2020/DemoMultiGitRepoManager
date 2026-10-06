"""平台环境覆盖、工具调用和可配置的 DeepSeek Harness Desktop 路径。"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from .common import ConfigError, OperationError
from .repo_config import read_ini


DEFAULTS = {
    "MRM_GIT_EXE": "", "MRM_NODE_EXE": "", "MRM_PNPM_EXE": "",
    "MRM_GIT_TIMEOUT_SECONDS": "30", "MRM_NETWORK_TIMEOUT_SECONDS": "300",
    "MRM_TASK_TIMEOUT_SECONDS": "900", "MRM_DSH_DESKTOP_DIR": "",
    "MRM_DSH_DESKTOP_EXE": "", "MRM_DSH_PROFILE_DIR": "",
}
TIMEOUT_KEYS = ("MRM_GIT_TIMEOUT_SECONDS", "MRM_NETWORK_TIMEOUT_SECONDS", "MRM_TASK_TIMEOUT_SECONDS")
ENV_TEMPLATE = """; 可通过 envVar_v2.local.ini 或 MRM_* 进程变量覆盖。
[envVar_all]
MRM_GIT_EXE =
MRM_NODE_EXE =
MRM_PNPM_EXE =
MRM_GIT_TIMEOUT_SECONDS = 30
MRM_NETWORK_TIMEOUT_SECONDS = 300
MRM_TASK_TIMEOUT_SECONDS = 900

[envVar_windows]
MRM_DSH_DESKTOP_DIR =
MRM_DSH_DESKTOP_EXE =
MRM_DSH_PROFILE_DIR =

[envVar_linux]
MRM_DSH_DESKTOP_DIR =
MRM_DSH_DESKTOP_EXE =
MRM_DSH_PROFILE_DIR =
"""


def init_environment(root: Path) -> bool:
    path = root / "envVar_v2.ini"
    try:
        with path.open("x", encoding="utf-8", newline="\n") as target:
            target.write(ENV_TEMPLATE)
        print(f"已创建 {path}，请填写本机 Desktop 路径。")
        return True
    except FileExistsError:
        print(f"已存在，保持原样: {path}")
        return False


class Environment:
    def __init__(self, root: Path, environ: dict[str, str] | None = None,
                 platform: str | None = None):
        self.root = root.resolve()
        self.environ = dict(os.environ if environ is None else environ)
        self.platform = platform or ("windows" if os.name == "nt" else "linux")
        self.values = dict(DEFAULTS)
        self.sources = {key: "通用默认值" for key in DEFAULTS}
        self._tools: dict[str, str] = {}
        self._tool_env: dict[str, str] = {}
        for filename in ("envVar_v2.ini", "envVar_v2.local.ini"):
            path = self.root / filename
            if not path.exists():
                continue
            config = read_ini(path)
            for section in config.sections():
                if section not in ("envVar_all", "envVar_windows", "envVar_linux"):
                    raise ConfigError(f"{filename} 不支持节点 [{section}]")
                unknown = set(config[section]) - set(DEFAULTS)
                if unknown:
                    raise ConfigError(f"{filename} [{section}] 未知参数: {', '.join(sorted(unknown))}")
            for section in ("envVar_all", f"envVar_{self.platform}"):
                if section in config:
                    for key, value in config[section].items():
                        self.values[key] = value.strip()
                        self.sources[key] = f"{filename} [{section}]"
        for key in DEFAULTS:
            if key in self.environ:
                self.values[key] = self.environ[key].strip()
                self.sources[key] = "进程环境变量"
        for key in TIMEOUT_KEYS:
            try:
                if int(self.values[key]) <= 0:
                    raise ValueError
            except ValueError as error:
                raise ConfigError(f"{key} 必须为正整数") from error

    def timeout(self, network: bool = False, task: bool = False) -> int:
        key = "MRM_TASK_TIMEOUT_SECONDS" if task else (
            "MRM_NETWORK_TIMEOUT_SECONDS" if network else "MRM_GIT_TIMEOUT_SECONDS")
        return int(self.values[key])

    def expanded(self, key: str) -> str:
        value = self.values[key]

        def replace(match: re.Match) -> str:
            name = next(part for part in match.groups() if part is not None)
            if name not in self.environ:
                raise ConfigError(f"{key} 引用了未定义环境变量: {name}")
            return self.environ[name]

        value = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)|%([A-Za-z_][A-Za-z0-9_]*)%", replace, value)
        if value.startswith("~"):
            user_home = self.environ.get("USERPROFILE" if self.platform == "windows" else "HOME")
            value = (user_home + value[1:]) if user_home and (value == "~" or value[1] in "/\\") else os.path.expanduser(value)
        return value

    def path(self, key: str) -> Path | None:
        value = self.expanded(key)
        if not value:
            return None
        path = Path(value)
        return (path if path.is_absolute() else self.root / path).resolve()

    def tool(self, key: str, default: str) -> str:
        if key in self._tools:
            return self._tools[key]
        value = self.expanded(key)
        if value and (Path(value).is_absolute() or "/" in value or "\\" in value):
            candidate = self.path(key)
        else:
            located = shutil.which(value or default, path=self.environ.get("PATH", ""))
            candidate = Path(located).resolve() if located else None
        if candidate is None or not candidate.is_file():
            raise OperationError(f"找不到 {default}，请配置 {key} 或 PATH（来源：{self.sources[key]}）")
        if os.name != "nt" and not os.access(candidate, os.X_OK) and candidate.suffix not in (".js", ".cjs", ".mjs", ".cmd"):
            raise OperationError(f"工具不可执行: {candidate}")
        self._tools[key] = str(candidate)
        return str(candidate)

    def pnpm_command(self) -> list[str]:
        shim = Path(self.tool("MRM_PNPM_EXE", "pnpm"))
        if shim.suffix.lower() in (".js", ".mjs", ".cjs"):
            return [self.tool("MRM_NODE_EXE", "node"), str(shim)]
        if shim.suffix.lower() not in (".cmd", ".bat", ".ps1"):
            return [str(shim)]
        # 用 Node 直接运行 shim 引用的 JS，避免 cmd.exe 的再解释和引号问题。
        source = shim.read_text(encoding="utf-8-sig", errors="replace")
        # 直接运行 JS 时，保留 shim 的静态 pnpm 设置（例如避免 run 隐式安装）。
        for key, value in re.findall(r'^set\s+"(pnpm_config_[A-Za-z0-9_]+)=([^"%\r\n]*)"\s*$', source, flags=re.I | re.M):
            self._tool_env[key] = value
        matches = re.findall(r'"((?:%~dp0|%dp0%)[^"\r\n]+\.(?:js|cjs|mjs))"', source, flags=re.I)
        candidates = [shim.parent / re.sub(r"^%(?:~dp0|dp0%)", "", item, flags=re.I).replace("\\", os.sep)
                      for item in matches]
        candidates += [shim.parent / "node_modules/pnpm/bin/pnpm.cjs",
                       shim.parent / "node_modules/pnpm/bin/pnpm.mjs"]
        for script in candidates:
            if script.is_file():
                return [self.tool("MRM_NODE_EXE", "node"), str(script.resolve())]
        raise OperationError(f"无法解析 pnpm shim 的 JS 入口: {shim}；可将 MRM_PNPM_EXE 设为 pnpm.cjs/pnpm.mjs 完整路径")

    def child_env(self) -> dict[str, str]:
        env = dict(self.environ)
        env.update(self._tool_env)
        # 调用者可能来自另一仓库的 Git hook；这些变量会覆盖 cwd 的仓库身份。
        # 保留 SSH/credential/代理环境，仅去掉外部仓库上下文。
        for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
                    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE"):
            env.pop(key, None)
        # 只添加实际使用工具的目录，让 pnpm scripts 也使用选定的 Node。
        directories = list(dict.fromkeys(str(Path(path).parent) for path in self._tools.values()))
        env["PATH"] = os.pathsep.join(directories + [env.get("PATH", "")])
        env.setdefault("GIT_TERMINAL_PROMPT", "0")
        env.setdefault("GIT_OPTIONAL_LOCKS", "0")
        return env

    def desktop_directory(self) -> Path:
        directory = self.path("MRM_DSH_DESKTOP_DIR")
        if directory is None or not directory.is_dir():
            raise OperationError("请在 envVar_v2.ini 或本机覆盖中配置有效的 MRM_DSH_DESKTOP_DIR")
        return directory

    def desktop_executable(self) -> Path:
        executable = self.path("MRM_DSH_DESKTOP_EXE")
        if executable is None:
            if self.platform != "windows":
                raise OperationError("Linux 请配置 MRM_DSH_DESKTOP_EXE 完整路径")
            executable = self.desktop_directory() / "DeepSeek Harness.exe"
        if not executable.is_file():
            raise OperationError(f"Desktop 程序不存在: {executable}")
        return executable

    def profile_directory(self) -> Path:
        configured = self.path("MRM_DSH_PROFILE_DIR")
        if configured:
            return configured
        home = self.environ.get("USERPROFILE" if self.platform == "windows" else "HOME")
        return (Path(home) if home else Path.home()) / ".dsh/profiles/desktop"

    def info(self, desktop: bool = False) -> int:
        print(f"工程: {self.root}\n平台: {self.platform}")
        errors = False
        for key, value in self.values.items():
            print(f"{key} = {value or '(自动/未设置)'}  [{self.sources[key]}]")
        for key, name in (("MRM_GIT_EXE", "git"), ("MRM_NODE_EXE", "node"), ("MRM_PNPM_EXE", "pnpm")):
            try:
                print(f"{name}: {self.tool(key, name)}")
            except OperationError as error:
                print(f"{name}: {error}")
                errors |= name == "git"
        if desktop:
            try:
                print(f"Desktop 程序: {self.desktop_executable()}\nDesktop Profile: {self.profile_directory()}")
            except OperationError as error:
                print(error)
                errors = True
        return int(errors)
