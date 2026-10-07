"""多 Git 仓库、插件构建和 DeepSeek Harness Desktop 管理的统一入口。"""
from __future__ import annotations

import argparse
import sys
import time

from pythonProject import command_help
from pythonProject.common import ConfigError, OperationError, project_root
from pythonProject.desktop import Desktop
from pythonProject.dsh_config import check_dsh, init_dsh
from pythonProject.env_config import Environment, init_environment
from pythonProject.git_manager import GitManager
from pythonProject.plugin_tasks import PluginTasks
from pythonProject.repo_config import Manifest
from pythonProject.workflow import rebuild_install


GIT_COMMANDS = ("check", "clone", "status", "branch", "showUrl", "fetch", "pull", "push", "switch", "branchReset", "batch")
TASKS = ("deps", "typecheck", "build", "test", "test:e2e", "test:docs", "test:pack", "test:install", "pack")
COMMANDS = ("init", "info", "list", *GIT_COMMANDS, *TASKS, "initDsh", "checkDsh", "rebuild-install", "desktop")
HELP_FLAGS = ("-h", "--help")


class CommandHelpParser(argparse.ArgumentParser):
    """把 `-h`/`--help` 和参数错误统一导向完整命令帮助。

    子命令同样使用本类，因此 `envBuild.py <命令> -h` 也打印同一份完整帮助。
    """

    def format_help(self) -> str:
        return command_help.help_text()

    def format_usage(self) -> str:
        return f"用法: {command_help.PROGRAM} <命令> [选项]"

    def error(self, message: str) -> None:
        print(f"参数错误: {message}", file=sys.stderr)
        print(file=sys.stderr)
        command_help.print_help(sys.stderr)
        raise SystemExit(2)


def parser() -> argparse.ArgumentParser:
    root = CommandHelpParser(description="显式管理 dsh-plugins 下的独立 Git 仓库、插件构建和 Desktop。")
    commands = root.add_subparsers(dest="command", required=True, parser_class=CommandHelpParser)
    commands.add_parser("init", help="仅创建缺失的 envVar_v2.ini，不覆盖已有文件")
    info = commands.add_parser("info", help="查看生效环境与工具路径")
    info.add_argument("--desktop", action="store_true", help="同时校验 Desktop 配置路径")
    listing = commands.add_parser("list", help="显示仓库清单（包含禁用项）")
    listing.add_argument("--repo", action="append", metavar="名称")
    for name in (*GIT_COMMANDS, *TASKS):
        command = commands.add_parser(name, help=f"批量执行 {name}")
        command.add_argument("--repo", action="append", metavar="名称", help="可重复，默认选择所有启用仓库")
        command.add_argument("--fail-fast", action="store_true", help="失败后停止后续仓库")
        if name not in ("check", "status", "branch", "showUrl"):
            command.add_argument("--dry-run", action="store_true", help="仅本地预检和打印，不执行目标操作")
        if name == "batch":
            command.add_argument("arguments", nargs=argparse.REMAINDER, help="-- 后的子进程命令及参数")
    dsh_init = commands.add_parser("initDsh", help="创建缺失的 DSH v2 工程配置，不覆盖已有配置")
    dsh_init.add_argument("--dry-run", action="store_true")
    commands.add_parser("checkDsh", help="只读检查 DSH v2 JSON 与仓库清单一致性")
    workflow = commands.add_parser("rebuild-install", help="依次编译、打包、安装并启动 Desktop，失败即停止")
    workflow.add_argument("--repo", action="append", metavar="名称", help="可重复，自动加入依赖，默认选择所有启用仓库")
    workflow.add_argument("--with-deps", action="store_true", help="编译前安装依赖，默认不安装依赖")
    workflow.add_argument("--dry-run", action="store_true", help="预览完整流程，不编译、打包、安装或启动")
    desktop = commands.add_parser("desktop", help="Desktop 启动、关闭、安装及目录选择适配")
    desktop.add_argument("action", choices=("info", "start", "stop", "install", "patch", "check-patch"))
    desktop.add_argument("--repo", action="append", metavar="名称", help="install 的插件选择，自动加入依赖")
    desktop.add_argument("--archive", action="append", metavar="历史包文件名", help="install 指定 dist 内历史 tgz，可重复；未指定的插件使用 latest")
    desktop.add_argument("--dry-run", action="store_true")
    return root


def _dispatch(arguments: list[str]) -> int:
    """分发一次命令；整体耗时由 `main` 统一统计并打印。"""
    if arguments[0] in HELP_FLAGS:
        command_help.print_help()
        return 0
    if arguments[0] not in COMMANDS:
        print(f"未知命令: {arguments[0]}", file=sys.stderr)
        print(file=sys.stderr)
        command_help.print_help(sys.stderr)
        return 2
    cli = parser()
    args = cli.parse_args(arguments)
    root = project_root()
    try:
        if args.command == "init":
            init_environment(root)
            return 0
        env = Environment(root)
        if args.command == "info":
            return env.info(args.desktop)
        if args.command == "desktop" and args.action != "install" and (args.repo or args.archive):
            raise ConfigError("desktop --repo/--archive 仅适用于 install")
        # Desktop info/start/stop 不依赖仓库清单，便于环境诊断、独立启动和关闭。
        if args.command == "desktop" and args.action in ("info", "start", "stop"):
            return Desktop(Manifest(root, []), env).run(args.action, [], args.dry_run)
        manifest = Manifest.load(root)
        if args.command == "initDsh":
            return init_dsh(manifest, args.dry_run)
        if args.command == "checkDsh":
            return check_dsh(manifest)
        if args.command == "list":
            repos = manifest.select(args.repo) if args.repo else manifest.repos
            for repo in repos:
                print(f"[{repo.name}] enabled={repo.enabled}\n  path={repo.relative_path}\n"
                      f"  url={repo.url}\n  branch={repo.branch}; remote={repo.remote}\n"
                      f"  depends_on={', '.join(repo.depends_on) or '(无)'}")
            return 0
        include_deps = args.command in ("deps", "build", "pack", "rebuild-install") or (args.command == "desktop" and args.action == "install")
        repos = manifest.select(args.repo, include_dependencies=include_deps)
        if not repos:
            raise ConfigError("未选择任何启用仓库")
        if args.command == "rebuild-install":
            return rebuild_install(manifest, env, repos, args.dry_run, args.with_deps)
        if args.command == "desktop":
            return Desktop(manifest, env).run(args.action, repos, args.dry_run, args.archive)
        dry_run = getattr(args, "dry_run", False)
        if args.command in TASKS:
            return PluginTasks(manifest, env).run(args.command, repos, dry_run, args.fail_fast)
        batch = None
        if args.command == "batch":
            batch = args.arguments
            if batch and batch[0] == "--":
                batch = batch[1:]
            if not batch:
                raise ConfigError("batch 缺少命令，示例: batch -- git log -1 --oneline")
        command = "switch" if args.command == "branchReset" else args.command
        return GitManager(manifest, env).run(command, repos, dry_run, args.fail_fast, batch)
    except ConfigError as error:
        print(f"配置/参数错误: {error}", file=sys.stderr)
        print(f"运行 {command_help.PROGRAM} -h 查看全部命令用法。", file=sys.stderr)
        return 2
    except (OperationError, OSError) as error:
        print(f"执行失败: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已中断，停止后续任务。", file=sys.stderr)
        return 130


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    # 空命令、-h/--help 和未知命令都给出完整帮助，而不是 argparse 的简略 usage。
    if not arguments:
        command_help.print_help(sys.stderr)
        return 2
    started = time.monotonic()
    try:
        return _dispatch(arguments)
    finally:
        # 无论成功、受阻、报错还是中断，都在结束前打印一行耗时统计。
        elapsed = time.monotonic() - started
        print(f"\n[耗时统计] 命令 '{arguments[0]}' 总执行耗时: {elapsed:.2f} 秒")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
