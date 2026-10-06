"""显式清单范围内的 Git 检查与批量操作，不隐式修复仓库。"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from .common import CommandError, OperationError, RepoResult, Runner, command_text, require_success, summarize
from .env_config import Environment
from .repo_config import Manifest, RepoSpec


@dataclass
class GitState:
    branch: str
    head: str
    changes: str
    git_dir: Path


class GitManager:
    def __init__(self, manifest: Manifest, env: Environment, runner: Runner | None = None):
        self.manifest = manifest
        self.env = env
        self.git = env.tool("MRM_GIT_EXE", "git")
        self.runner = runner or Runner(env.child_env())

    def query(self, repo: RepoSpec, *args: str, allow_failure: bool = False):
        result = self.runner.run([self.git, *args], repo.path, self.env.timeout(), capture=True, show=False)
        if not allow_failure:
            require_success(result)
        return result

    def inspect(self, repo: RepoSpec) -> GitState:
        self.manifest.recheck_path(repo)
        if not repo.path.is_dir() or not (repo.path / ".git").exists():
            raise OperationError(f"缺少自身 Git 元数据: {repo.path}")
        top = Path(self.query(repo, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
        if top != repo.path:
            raise OperationError(f"Git 根目录不匹配: 期望 {repo.path}，实际 {top}")
        branch = self.query(repo, "symbolic-ref", "--quiet", "--short", "HEAD", allow_failure=True)
        if branch.returncode not in (0, 1):
            require_success(branch)
        head = self.query(repo, "rev-parse", "--short", "--verify", "HEAD", allow_failure=True)
        changes = self.query(repo, "-c", "core.quotepath=false", "status", "--porcelain=v1", "--untracked-files=normal").stdout.rstrip()
        git_dir = Path(self.query(repo, "rev-parse", "--absolute-git-dir").stdout.strip()).resolve()
        return GitState(branch.stdout.strip(), head.stdout.strip() if head.returncode == 0 else "(无提交)", changes, git_dir)

    def remote_urls(self, repo: RepoSpec, push: bool = False) -> list[str]:
        args = ["remote", "get-url", "--all"]
        if push:
            args.append("--push")
        args.append(repo.remote)
        return self.query(repo, *args).stdout.strip().splitlines()

    def validate_remote(self, repo: RepoSpec, push: bool = False) -> None:
        actual = self.remote_urls(repo, push)
        if actual != [repo.url]:
            kind = "push" if push else "fetch"
            raise OperationError(f"{repo.remote} {kind} URL 不匹配: 配置 {repo.url}，实际 {actual}")

    def write_ready(self, repo: RepoSpec, clean: bool = False,
                    branch: bool = False, push: bool = False) -> GitState:
        state = self.inspect(repo)
        self.validate_remote(repo)
        if push:
            self.validate_remote(repo, push=True)
        # git-path 能正确处理 linked worktree 的工作树专属元数据。
        for marker in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"):
            filename = self.query(repo, "rev-parse", "--git-path", marker).stdout.strip()
            path = Path(filename)
            if (path if path.is_absolute() else repo.path / path).exists():
                raise OperationError(f"存在未完成 Git 操作: {marker}")
        if branch and state.branch != repo.branch:
            raise OperationError(f"当前分支 {state.branch or '(detached HEAD)'} 不等于配置分支 {repo.branch}；请先 switch")
        if clean and state.changes:
            raise OperationError(f"工作区存在改动，请先处理：\n{state.changes}")
        return state

    def ahead_behind(self, repo: RepoSpec) -> str:
        reference = f"refs/remotes/{repo.remote}/{repo.branch}"
        result = self.query(repo, "rev-list", "--left-right", "--count", f"HEAD...{reference}", allow_failure=True)
        if result.returncode != 0:
            return "ahead/behind 未知（无可用远程跟踪引用；未自动 fetch）"
        values = result.stdout.split()
        return f"ahead {values[0]} / behind {values[1]}（相对 {reference}，未自动 fetch）"

    def execute(self, repo: RepoSpec, args: list[str], dry_run: bool,
                network: bool = False, cwd: Path | None = None) -> str:
        if dry_run:
            return f"cwd={cwd or repo.path}; {command_text(args)}"
        require_success(self.runner.run(args, cwd or repo.path, self.env.timeout(network=network)))
        return "命令完成"

    def operate(self, command: str, repo: RepoSpec, dry_run: bool = False,
                batch: list[str] | None = None) -> RepoResult:
        self.manifest.recheck_path(repo)
        if command == "clone":
            if repo.path.exists():
                if not repo.path.is_dir():
                    raise OperationError(f"目标不是目录: {repo.path}")
                if (repo.path / ".git").exists():
                    state = self.inspect(repo)
                    self.validate_remote(repo)
                    return RepoResult(repo.name, "跳过", f"仓库已存在，保留当前分支 {state.branch or '(detached)'}；目标 {repo.branch}")
                if any(repo.path.iterdir()):
                    raise OperationError(f"目标是非空普通目录，拒绝覆盖: {repo.path}")
            args = [self.git, "clone", "--origin", repo.remote, "--branch", repo.branch, "--", repo.url, str(repo.path)]
            if not dry_run:
                repo.path.parent.mkdir(parents=True, exist_ok=True)
            message = self.execute(repo, args, dry_run, network=True, cwd=self.manifest.root)
        elif command == "status":
            state = self.inspect(repo)
            message = (f"当前={state.branch or '(detached HEAD)'}; 目标={repo.branch}; HEAD={state.head}\n"
                       f"  {self.ahead_behind(repo)}\n  {state.changes or '工作区干净'}")
            return RepoResult(repo.name, "成功", message)
        elif command == "check":
            state = self.inspect(repo)
            self.validate_remote(repo)
            self.validate_remote(repo, push=True)
            if state.branch != repo.branch:
                raise OperationError(f"当前分支 {state.branch or '(detached)'} 与目标 {repo.branch} 不一致")
            return RepoResult(repo.name, "成功", f"Git 根、分支和 fetch/push remote 匹配；{'有未提交改动' if state.changes else '工作区干净'}")
        elif command in ("branch", "showUrl"):
            state = self.inspect(repo)
            if command == "branch":
                message = f"当前={state.branch or '(detached)'}; 目标={repo.branch}\n" + self.query(repo, "branch", "--list").stdout.strip()
            else:
                fetch, push = self.remote_urls(repo), self.remote_urls(repo, push=True)
                message = (f"配置 {repo.url}\n  fetch={fetch}\n  push={push}\n"
                           f"  配置匹配={fetch == [repo.url] and push == [repo.url]}")
            return RepoResult(repo.name, "成功", message)
        elif command == "batch":
            self.inspect(repo)
            args = list(batch or [])
            if not args:
                raise OperationError("batch 缺少命令，请在 -- 后指定命令")
            if args[0] == "git":
                args[0] = self.git
            message = self.execute(repo, args, dry_run, network=True)
        elif command in ("fetch", "pull", "push", "switch"):
            state = self.write_ready(repo, clean=command in ("pull", "switch"),
                                     branch=command in ("pull", "push"), push=command == "push")
            if command == "fetch":
                args = [self.git, "fetch", repo.remote]
            elif command == "pull":
                args = [self.git, "pull", "--no-rebase", "--ff-only", repo.remote, repo.branch]
            elif command == "push":
                if state.head == "(无提交)":
                    raise OperationError("仓库没有可推送提交")
                if state.changes:
                    print(f"  [提示] 未提交改动不包含在推送中:\n{state.changes}")
                args = [self.git, "push", "--no-force", "--no-follow-tags", repo.remote,
                        f"refs/heads/{repo.branch}:refs/heads/{repo.branch}"]
            else:
                local = self.query(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{repo.branch}", allow_failure=True)
                if local.returncode == 0:
                    args = [self.git, "switch", "--no-guess", repo.branch]
                else:
                    remote_ref = f"refs/remotes/{repo.remote}/{repo.branch}"
                    remote = self.query(repo, "show-ref", "--verify", "--quiet", remote_ref, allow_failure=True)
                    if remote.returncode != 0:
                        raise OperationError(f"目标分支 {repo.branch} 未缓存，请先 fetch 或核实配置")
                    args = [self.git, "switch", "--track", "-c", repo.branch, remote_ref]
            message = self.execute(repo, args, dry_run, network=command != "switch")
        else:
            raise OperationError(f"未实现的 Git 操作: {command}")
        return RepoResult(repo.name, "预览" if dry_run else "成功", message)

    def run(self, command: str, repos: list[RepoSpec], dry_run: bool = False,
            fail_fast: bool = False, batch: list[str] | None = None) -> int:
        results: list[RepoResult] = []
        for repo in repos:
            started = time.monotonic()
            print(f"\n[{repo.name}]\n路径: {repo.path}", flush=True)
            try:
                result = self.operate(command, repo, dry_run, batch)
            except CommandError as error:
                result = RepoResult(repo.name, "失败", str(error))
            except OperationError as error:
                result = RepoResult(repo.name, "阻断", str(error))
            result.elapsed = time.monotonic() - started
            results.append(result)
            if fail_fast and result.state in ("阻断", "失败"):
                results.extend(RepoResult(rest.name, "跳过", "--fail-fast 停止后续仓库")
                               for rest in repos[len(results):])
                break
        if command == "check":
            unknown = self.manifest.unregistered()
            if unknown:
                print(f"\n未登记的直接子仓库（未自动纳管）: {', '.join(unknown)}")
        return summarize(results)
