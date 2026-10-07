"""一次执行插件编译、打包、Desktop 安装和启动。"""
from __future__ import annotations

import sys

from .common import command_text
from .desktop import Desktop
from .env_config import Environment
from .plugin_tasks import PluginTasks
from .repo_config import Manifest, RepoSpec


def rebuild_install(manifest: Manifest, env: Environment, repos: list[RepoSpec],
                    dry_run: bool = False, with_deps: bool = False,
                    stop_desktop: bool = False) -> int:
    desktop = Desktop(manifest, env)
    # 默认要求用户已退出 Desktop；显式选择时由本流程结束它（含托盘），预览只打印将结束的进程。
    if stop_desktop:
        desktop.stop(dry_run=dry_run)
    # 开始前验证应用已退出、Profile 和工具可用；新工程不需要已有 tgz。
    desktop.check_install_environment(require_closed=not (dry_run and stop_desktop))
    ordered = manifest.ordered(repos)
    tasks = PluginTasks(manifest, env)
    phases = (["deps"] if with_deps else []) + ["build", "pack"]
    total = len(phases) + 2
    for index, phase in enumerate(phases, 1):
        print(f"\n[流程 {index}/{total}] {phase}", flush=True)
        result = tasks.run(phase, ordered, dry_run=dry_run, fail_fast=True)
        if result:
            print(f"流程停止：{phase} 未成功，未执行后续步骤。", flush=True)
            return result

    print(f"\n[流程 {total - 1}/{total}] desktop install", flush=True)
    if dry_run:
        # 前面的 pack 还没有生成包；预览不能校验旧包或要求已有 latest。
        args = [sys.executable, str(manifest.root / "envBuild.py"), "desktop", "install"]
        for repo in ordered:
            args.extend(["--repo", repo.name])
        print(f"[预览] 安装本次打包生成的 latest；实际安装时校验版本和 SHA256。\n  $ {command_text(args)}")
    else:
        result = desktop.install(ordered, dry_run=False)
        if result:
            print("流程停止：安装未成功，未启动 Desktop。", flush=True)
            return result

    print(f"\n[流程 {total}/{total}] desktop start", flush=True)
    result = desktop.start(dry_run=dry_run)
    if result == 0:
        print("流程预览完成；未执行写操作或启动应用。" if dry_run else "编译、打包、安装完成，已提交 Desktop 启动请求。", flush=True)
    return result
