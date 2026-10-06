"""DSH v2 工程配置的非覆盖创建与清单一致性检查。"""
from __future__ import annotations

import json
from pathlib import Path, PureWindowsPath

from .common import ConfigError, OperationError
from .repo_config import Manifest


PROJECT_FILE = "dsh-multi-git-repo.json"


def expected_config(manifest: Manifest) -> dict:
    return {
        "version": 2, "enabled": True, "includeProjectRoot": False,
        "repositories": [{"name": repo.name, "path": repo.relative_path}
                         for repo in manifest.repos if repo.enabled],
        "directories": [], "discovery": {"containers": ["dsh-plugins"]},
    }


def init_dsh(manifest: Manifest, dry_run: bool = False) -> int:
    path = manifest.root / PROJECT_FILE
    if path.exists() or path.is_symlink():
        print(f"已存在，保持原样: {path}")
        return 0
    if dry_run:
        print(f"[预览] 创建 {path}\n{json.dumps(expected_config(manifest), ensure_ascii=False, indent=2)}")
        return 0
    try:
        with path.open("x", encoding="utf-8", newline="\n") as target:
            target.write(json.dumps(expected_config(manifest), ensure_ascii=False, indent=2) + "\n")
    except FileExistsError:
        print(f"已存在，保持原样: {path}")
        return 0
    print(f"已创建 {path}；DSH 管理界面请重新加载配置。")
    return 0


def _relative(root: Path, value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ConfigError("DSH 目标路径必须是非空字符串，且不超过 4096 字符")
    path = value.strip().replace("\\", "/")
    if (Path(path).is_absolute() or PureWindowsPath(path).drive
            or any(part in ("", ".", "..", ".git") for part in path.split("/"))
            or not (root / path).resolve().is_relative_to(root)):
        raise ConfigError(f"DSH 持久路径必须位于工程内: {value}")
    return path


def check_dsh(manifest: Manifest) -> int:
    path = manifest.root / PROJECT_FILE
    if not path.is_file() or path.is_symlink():
        raise OperationError(f"缺少普通 DSH v2 配置文件，请运行 initDsh: {path}")
    if path.stat().st_size > 1024 * 1024:
        raise ConfigError("DSH 配置超过 1 MiB")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise ConfigError(f"无法解析 DSH JSON: {error}") from error
    required = {"version", "includeProjectRoot", "repositories", "directories"}
    allowed = required | {"enabled", "discovery"}
    if not isinstance(config, dict) or not required <= set(config) or set(config) - allowed:
        raise ConfigError("DSH v2 配置字段缺失或包含未知字段")
    if type(config["version"]) is not int or config["version"] != 2:
        raise ConfigError("DSH 配置必须是 version 2")
    if type(config["includeProjectRoot"]) is not bool or type(config.get("enabled", True)) is not bool:
        raise ConfigError("DSH 启用和根目录开关必须是布尔值")
    entries: dict[str, dict[str, str]] = {}
    total = 0
    for key in ("repositories", "directories"):
        rows = config[key]
        if not isinstance(rows, list):
            raise ConfigError(f"DSH {key} 必须是数组")
        total += len(rows)
        for row in rows:
            if (not isinstance(row, dict) or set(row) != {"name", "path"}
                    or not isinstance(row["name"], str) or not row["name"].strip()
                    or len(row["name"].strip()) > 120):
                raise ConfigError("DSH 目标必须只包含非空 name 和 path")
            relative = _relative(manifest.root, row["path"])
            identity = str((manifest.root / relative).resolve())
            if identity in entries:
                raise ConfigError(f"DSH 重复目标路径: {relative}")
            entries[identity] = {"name": row["name"].strip(), "path": relative, "kind": key}
    if total > 512:
        raise ConfigError("DSH 配置超过 512 个目标")
    discovery = config.get("discovery", {"containers": []})
    if not isinstance(discovery, dict) or set(discovery) != {"containers"}:
        raise ConfigError("DSH discovery 只支持 containers")
    containers = discovery["containers"]
    if not isinstance(containers, list) or len(containers) > 32:
        raise ConfigError("DSH containers 必须是最多 32 项的数组")
    for value in containers:
        _relative(manifest.root, value)
    failures: list[str] = []
    for repo in manifest.repos:
        if not repo.enabled:
            continue
        row = entries.get(str(repo.path))
        if row is None or row["kind"] != "repositories" or row["name"] != repo.name:
            failures.append(f"{repo.name}: DSH 仓库名称／路径／类型与 INI 不一致")
    declared = {str(repo.path) for repo in manifest.repos if repo.enabled}
    extras = [row["path"] for key, row in entries.items() if key not in declared]
    if extras:
        print(f"保留 DSH 额外目标: {', '.join(extras)}")
    if not config.get("enabled", True):
        failures.append("DSH 多目标配置当前被禁用")
    print(f"DSH includeProjectRoot={config['includeProjectRoot']}; 目标 {total}")
    for failure in failures:
        print(f"[不一致] {failure}")
    if not failures:
        print("DSH v2 配置与启用的 INI 仓库一致。")
    return int(bool(failures))
