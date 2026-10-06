"""遵循清单依赖顺序调度插件已有 pnpm scripts。"""
from __future__ import annotations

import json
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from .common import CommandError, OperationError, RepoResult, Runner, command_text, require_success, summarize
from .env_config import Environment
from .git_manager import GitManager
from .package_artifacts import artifact_directory, dist_directory, latest_path, load_latest, package_stem, publish_archive, validate_package
from .repo_config import Manifest, RepoSpec


def package_info(repo: RepoSpec) -> dict:
    try:
        package = json.loads((repo.path / "package.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise OperationError(f"{repo.name} package.json 无效: {error}") from error
    return validate_package(package, f"{repo.name} package.json")


def archive_path(root: Path, repo: RepoSpec, package: dict) -> Path:
    return latest_path(root, package["name"])


def verified_archive(root: Path, repo: RepoSpec) -> tuple[Path, str, dict]:
    package = package_info(repo)
    artifact = load_latest(root, package["name"], package["version"])
    return artifact.latest, artifact.digest, artifact.package


class PluginTasks:
    def __init__(self, manifest: Manifest, env: Environment, runner: Runner | None = None):
        self.manifest = manifest
        self.env = env
        self.pnpm = env.pnpm_command()
        # .exe 版 pnpm 也要确保子脚本使用明确的 Node。
        env.tool("MRM_NODE_EXE", "node")
        task_env = env.child_env()
        # pnpm 11 通过配置环境变量控制 run 前检查；依赖安装由 deps 明确执行。
        task_env["pnpm_config_verify_deps_before_run"] = "warn"
        self.dist = dist_directory(manifest.root)
        task_env["MRM_DIST_DIR"] = str(self.dist)
        self.runner = runner or Runner(task_env)
        self.git = GitManager(manifest, env)

    def _fallback_pack(self, repo: RepoSpec, package: dict) -> None:
        staging = artifact_directory(self.manifest.root, ".staging")
        staging.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="pack-", dir=staging) as directory:
            output = Path(directory).resolve()
            if not output.is_relative_to(staging):
                raise OperationError("打包临时目录逃出 dist/.staging")
            args = self.pnpm + ["pack", "--pack-destination", str(output)]
            require_success(self.runner.run(args, repo.path, self.env.timeout(task=True)))
            source = output / f"{package_stem(package['name'])}-{package['version']}.tgz"
            compiled = repo.path / "lib/client.js"
            build_time = (datetime.fromtimestamp(compiled.stat().st_mtime, timezone.utc).isoformat(timespec="milliseconds")
                          if compiled.is_file() else None)
            publish_archive(self.manifest.root, source, package, build_time)

    def run(self, task: str, repos: list[RepoSpec], dry_run: bool = False,
            fail_fast: bool = False) -> int:
        if task == "deps" and not dry_run:
            # deps 是明确的依赖安装；允许 pnpm 在无交互环境重建过期链接。
            self.runner.env["CI"] = "true"
        ordered = self.manifest.ordered(repos)
        print("依赖顺序: " + " -> ".join(repo.name for repo in ordered))
        results: list[RepoResult] = []
        failed: set[str] = set()
        stopped = False
        for repo in ordered:
            started = time.monotonic()
            dependencies_failed = set(repo.depends_on) & failed
            if stopped or dependencies_failed:
                failed.add(repo.name)
                results.append(RepoResult(repo.name, "阻断", "前序失败／依赖失败: " + ", ".join(sorted(dependencies_failed))))
                continue
            print(f"\n[{repo.name}] {task}", flush=True)
            try:
                self.git.inspect(repo)
                package = package_info(repo)
                scripts = package.get("scripts", {})
                fallback_pack = False
                if task == "deps":
                    modules = (repo.path / "node_modules").resolve()
                    if not modules.is_relative_to(repo.path):
                        raise OperationError(f"依赖目录真实路径逃出插件仓库，拒绝重建: {modules}")
                    args = self.pnpm + ["install", "--frozen-lockfile"]
                elif task == "pack":
                    if "test:pack" in scripts:
                        args = self.pnpm + ["run", "test:pack"]
                    else:
                        args = self.pnpm + ["pack", "--pack-destination", str(self.dist / ".staging" / "<临时目录>")]
                        fallback_pack = True
                else:
                    if task not in scripts:
                        raise OperationError(f"package.json 未定义 scripts.{task}")
                    args = self.pnpm + ["run", task]
                if dry_run:
                    result = RepoResult(repo.name, "预览", f"cwd={repo.path}; {command_text(args)}")
                else:
                    if fallback_pack:
                        self._fallback_pack(repo, package)
                    else:
                        require_success(self.runner.run(args, repo.path, self.env.timeout(task=True)))
                    if task in ("pack", "test:pack"):
                        artifact = load_latest(self.manifest.root, package["name"], package["version"])
                        print(f"历史安装包: {artifact.history}\n最新安装包: {artifact.latest}")
                    result = RepoResult(repo.name, "成功", task + " 完成")
            except CommandError as error:
                result = RepoResult(repo.name, "失败", str(error))
            except OperationError as error:
                result = RepoResult(repo.name, "阻断", str(error))
            except OSError as error:
                result = RepoResult(repo.name, "失败", str(error))
            result.elapsed = time.monotonic() - started
            results.append(result)
            if result.state in ("阻断", "失败"):
                failed.add(repo.name)
                stopped = fail_fast
        return summarize(results)
