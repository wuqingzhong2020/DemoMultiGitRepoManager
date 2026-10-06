"""仓库清单解析、真实路径边界及构建依赖排序。"""
from __future__ import annotations

import configparser
import os
import re
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from urllib.parse import urlsplit

from .common import ConfigError


def read_ini(path: Path) -> configparser.ConfigParser:
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    try:
        with path.open(encoding="utf-8-sig") as source:
            config.read_file(source)
    except (OSError, UnicodeError, configparser.Error) as error:
        raise ConfigError(f"无法读取 {path}: {error}") from error
    if config.defaults():
        raise ConfigError(f"{path.name} 不支持 [DEFAULT] 隐式字段")
    return config


def valid_branch(branch: str) -> bool:
    return bool(branch and branch != "HEAD" and not branch.startswith("-")
                and not branch.endswith(".") and ".." not in branch and "@{" not in branch
                and not re.search(r"[\x00-\x20\x7f~^:?*\[\\]", branch)
                and all(p and not p.startswith(".") and not p.endswith(".lock")
                        for p in branch.split("/")))


def repo_path(root: Path, relative: str) -> Path:
    normalized = relative.replace("\\", "/")
    parts = normalized.split("/")
    if (Path(normalized).is_absolute() or PureWindowsPath(normalized).drive
            or parts[0] != "dsh-plugins" or len(parts) < 2
            or any(p in ("", ".", "..", ".git") for p in parts)):
        raise ConfigError(f"仓库路径必须是 dsh-plugins 下的相对子路径: {relative}")
    resolved_root = root.resolve()
    container = (resolved_root / "dsh-plugins").resolve()
    target = (resolved_root / normalized).resolve()
    if (not container.is_relative_to(resolved_root)
            or target == container or not target.is_relative_to(container)):
        raise ConfigError(f"仓库真实路径逃出 dsh-plugins: {relative}")
    return target


@dataclass(frozen=True)
class RepoSpec:
    name: str
    relative_path: str
    path: Path
    url: str
    branch: str
    remote: str = "origin"
    enabled: bool = True
    depends_on: tuple[str, ...] = ()


class Manifest:
    def __init__(self, root: Path, repos: list[RepoSpec]):
        self.root = root.resolve()
        self.repos = repos
        self.by_name = {repo.name: repo for repo in repos}
        for repo in repos:
            for dependency in repo.depends_on:
                if dependency not in self.by_name:
                    raise ConfigError(f"{repo.name} 的 depends_on 仓库不存在: {dependency}")
        self.ordered(repos)

    @classmethod
    def load(cls, root: Path) -> "Manifest":
        config = read_ini(root / "submodules.ini")
        if not config.sections():
            raise ConfigError("submodules.ini 没有仓库配置")
        repos: list[RepoSpec] = []
        allowed = {"path", "url", "branch", "remote", "enabled", "depends_on"}
        for name in config.sections():
            section = config[name]
            if name != name.strip() or not name:
                raise ConfigError("仓库名称不能为空或含首尾空格")
            unknown = set(section) - allowed
            if unknown:
                raise ConfigError(f"[{name}] 不支持的字段: {', '.join(sorted(unknown))}")
            for field in ("path", "url", "branch"):
                if not section.get(field, "").strip():
                    raise ConfigError(f"[{name}] 缺少 {field}")
            relative = section["path"].strip().replace("\\", "/")
            path = repo_path(root, relative)
            url, branch = section["url"].strip(), section["branch"].strip()
            remote = section.get("remote", "origin").strip()
            if url.startswith("-") or any(ord(ch) < 32 for ch in url):
                raise ConfigError(f"[{name}] URL 无效")
            if "://" in url:
                try:
                    parsed_url = urlsplit(url)
                except ValueError as error:
                    raise ConfigError(f"[{name}] URL 无效") from error
                if (parsed_url.password is not None
                        or parsed_url.scheme in ("http", "https") and parsed_url.username is not None):
                    raise ConfigError(f"[{name}] URL 不能嵌入密码/token；HTTP(S) 认证请使用 Git credential 工具")
            if not valid_branch(branch):
                raise ConfigError(f"[{name}] Git 分支名称无效: {branch}")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", remote) or not valid_branch(remote):
                raise ConfigError(f"[{name}] remote 名称无效: {remote}")
            try:
                enabled = section.getboolean("enabled", fallback=True)
            except ValueError as error:
                raise ConfigError(f"[{name}] enabled 必须为布尔值") from error
            dependencies = tuple(p.strip() for p in section.get("depends_on", "").split(",") if p.strip())
            for previous in repos:
                if (path == previous.path or path.is_relative_to(previous.path)
                        or previous.path.is_relative_to(path)):
                    raise ConfigError(f"[{name}] 路径与 [{previous.name}] 重复或重叠")
            repos.append(RepoSpec(name, relative, path, url, branch, remote, enabled, dependencies))
        return cls(root, repos)

    def ordered(self, repos: list[RepoSpec]) -> list[RepoSpec]:
        selected = {repo.name for repo in repos}
        visiting: set[str] = set()
        visited: set[str] = set()
        ordered: list[RepoSpec] = []

        def visit(repo: RepoSpec) -> None:
            if repo.name in visiting:
                raise ConfigError(f"depends_on 存在循环依赖: {repo.name}")
            if repo.name in visited:
                return
            visiting.add(repo.name)
            for name in repo.depends_on:
                if name in selected:
                    visit(self.by_name[name])
            visiting.remove(repo.name)
            visited.add(repo.name)
            ordered.append(repo)

        for repo in repos:
            visit(repo)
        return ordered

    def select(self, names: list[str] | None, include_dependencies: bool = False) -> list[RepoSpec]:
        selected = set(names or [repo.name for repo in self.repos if repo.enabled])
        for name in selected:
            if name not in self.by_name:
                raise ConfigError(f"未知仓库: {name}")
            if not self.by_name[name].enabled:
                raise ConfigError(f"仓库已禁用: {name}")
        if include_dependencies:
            pending = list(selected)
            while pending:
                repo = self.by_name[pending.pop()]
                for name in repo.depends_on:
                    if not self.by_name[name].enabled:
                        raise ConfigError(f"{repo.name} 依赖已禁用的仓库: {name}")
                    if name not in selected:
                        selected.add(name)
                        pending.append(name)
        return [repo for repo in self.repos if repo.name in selected]

    def recheck_path(self, repo: RepoSpec) -> None:
        if repo_path(self.root, repo.relative_path) != repo.path:
            raise ConfigError(f"{repo.name} 的真实路径发生变化，请重新读取配置")

    def unregistered(self) -> list[str]:
        container = self.root / "dsh-plugins"
        known = {os.path.normcase(str(repo.path)) for repo in self.repos}
        if not container.is_dir():
            return []
        return sorted(child.name for child in container.iterdir()
                      if child.is_dir() and (child / ".git").exists()
                      and os.path.normcase(str(child.resolve())) not in known)
