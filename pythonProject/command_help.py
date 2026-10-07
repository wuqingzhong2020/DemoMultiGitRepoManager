"""`envBuild.py` 的完整命令帮助。

空命令、`-h`/`--help` 和参数错误共用同一份文本：先按分组列出每个命令的用法
与具体含义，再给出公共选项和退出码，避免 argparse 默认的简略 usage。
"""
from __future__ import annotations

import sys
import unicodedata
from typing import Sequence, TextIO

PROGRAM = "python envBuild.py"
_INDENT = "    "
_GAP = " : "

# (分组标题, 分组补充说明, ((用法, 含义), ...))
_SECTIONS: Sequence[tuple[str, str, Sequence[tuple[str, str]]]] = (
    (
        "环境与信息",
        "",
        (
            ("init", "只创建缺失的 envVar_v2.ini 模板，已有配置保持原样"),
            ("info [--desktop]", "打印当前生效的环境变量与工具路径；--desktop 同时校验 Desktop 配置路径"),
            ("list [--repo 名称]", "显示仓库清单（含 enabled=false 的禁用项）、分支与依赖关系"),
        ),
    ),
    (
        "日常 Git 管理",
        "目标目录都位于 dsh-plugins/ 下；新增仓库在 submodules.ini 添加 section。",
        (
            ("check", "只读检查各仓身份与工作区状态，不访问网络"),
            ("clone [--dry-run]", "克隆缺失的启用仓库，跳过已存在且身份正确的目录"),
            ("fetch [--dry-run]", "拉取远程引用但不合并，供 status 显示 ahead/behind"),
            ("pull [--dry-run]", "按 --no-rebase --ff-only 快进拉取，有未提交改动时拒绝执行"),
            ("push [--dry-run]", "显式推送当前分支，不 add/commit、不 force、不带标签"),
            ("switch [--dry-run]", "切换到清单声明的分支，要求工作区干净"),
            ("branchReset [--dry-run]", "switch 的别名，实际执行普通 git switch"),
            ("status", "查看各仓工作区状态；ahead/behind 来自已缓存的远程引用"),
            ("branch", "查看各仓当前所在分支"),
            ("showUrl", "查看各仓远程仓库地址（git remote -v）"),
            ("batch [--dry-run] -- <命令...>", "在各仓依次执行原样子进程命令，不展开通配符、管道和重定向"),
        ),
    ),
    (
        "插件依赖与构建",
        "执行顺序为管理插件、审查插件；构建不会自动安装到 Desktop。",
        (
            ("deps [--dry-run]", "在各仓执行 pnpm install --frozen-lockfile"),
            ("typecheck [--dry-run]", "委托各仓已有的 scripts.typecheck"),
            ("build [--dry-run]", "委托各仓已有的 scripts.build"),
            ("test [--dry-run]", "委托各仓已有的 scripts.test"),
            ("pack [--dry-run]", "先 build 再打包，产物统一写入工程根目录 dist/"),
            ("test:e2e [--dry-run]", "运行各仓的 scripts.test:e2e（仓库必须已定义）"),
            ("test:docs [--dry-run]", "运行各仓的 scripts.test:docs（仓库必须已定义）"),
            ("test:pack [--dry-run]", "运行各仓的 scripts.test:pack（仓库必须已定义）"),
            ("test:install [--dry-run]", "运行各仓的 scripts.test:install（仓库必须已定义）"),
        ),
    ),
    (
        "DSH 工程配置",
        "INI 负责仓库来源和分支，DSH JSON 负责界面管理和文件审查范围。",
        (
            ("initDsh [--dry-run]", "为启用仓库创建缺失的 dsh-multi-git-repo.json v2 配置，不覆盖已有文件"),
            ("checkDsh", "只读校验 DSH JSON 与仓库清单的名称、路径、类型一致性"),
        ),
    ),
    (
        "Desktop 启动、关闭、安装与适配",
        "启动、关闭、安装与 patch 均为独立命令；Git 命令和 build 不会隐式触发，只有显式传入 rebuild-install --stop-desktop 时流程才会先关闭 Desktop。",
        (
            ("desktop info", "显示 Desktop 安装目录、可执行文件与 Profile 路径"),
            ("desktop start [--dry-run]", "启动 Desktop"),
            ("desktop stop [--dry-run]", "关闭 Desktop（含托盘进程）；先请求退出，未退出再强制结束"),
            ("desktop install [--dry-run]", "安装两个插件的 latest 包，校验索引、包内身份与 SHA256"),
            ("desktop install --archive <历史包.tgz>", "以 dist 内历史包安装指定构建，可重复；未指定的配套插件仍用 latest"),
            ("desktop check-patch [--dry-run]", "只检查配置安装目录下 resources/app.asar 的适配状态"),
            ("desktop patch [--dry-run]", "调用插件适配脚本修改 app.asar，由脚本负责核验与备份"),
            ("rebuild-install [--stop-desktop] [--with-deps] [--dry-run]", "依次编译、打包、安装最新包并启动 Desktop，失败即停止"),
        ),
    ),
)

_OPTIONS: Sequence[tuple[str, str]] = (
    ("--repo 名称", "可重复，默认只操作启用的清单仓库；deps/build/pack/rebuild-install/desktop install 会自动加入依赖"),
    ("--fail-fast", "某个独立仓库失败后停止后续仓库；默认继续执行其他仓库并在汇总中保留原因"),
    ("--dry-run", "对写操作只做本地预检并打印命令，不发起 fetch/pull/push，也不切换分支或安装"),
    ("--stop-desktop", "仅 rebuild-install：编译前关闭 Desktop（包含托盘），结束时重新启动；默认不关闭，要求用户先退出 Desktop"),
    ("--", "batch 之后的内容作为原样子进程参数传递，不经过 shell 展开"),
)

_EXIT_CODES = "0 成功；1 运行失败或状态阻断；2 参数或配置无效；130 用户中断。"

# (示例分组, ((命令, 说明), ...))；命令前置 PROGRAM，可直接复制执行。
_EXAMPLES: Sequence[tuple[str, Sequence[tuple[str, str]]]] = (
    (
        "环境与信息",
        (
            ("init", "首次使用，只创建缺失的 envVar_v2.ini 模板"),
            ("info --desktop", "打印生效环境并校验 Desktop 配置路径"),
            ("list --repo dsh-multi-git-repo-manager", "只看单个仓库的清单条目"),
        ),
    ),
    (
        "日常 Git 管理",
        (
            ("clone --dry-run", "先预览将要克隆的仓库和命令"),
            ("clone --fail-fast", "任一仓库失败即停止后续仓库"),
            ("status", "查看各仓工作区状态，不访问网络"),
            ("fetch", "更新远程引用，供 status 显示 ahead/behind"),
            ("pull --repo dsh-multi-git-repo-manager", "快进拉取管理插件"),
            ("push --repo dsh-multi-git-repo-manager --dry-run", "预览推送，不实际推送"),
            ("switch --repo dsh-multi-git-repo-manager --dry-run", "预览分支切换"),
            ("batch --repo dsh-multi-git-repo-manager -- git log -1 --oneline", "在各仓执行任意 git 命令"),
            ("batch -- git status --short", "省略 --repo 时作用于全部启用仓库"),
        ),
    ),
    (
        "插件依赖与构建",
        (
            ("deps", "首次构建或依赖变更前安装锁定依赖"),
            ("build --dry-run", "只打印将要执行的 pnpm 命令"),
            ("build --fail-fast", "任一仓库构建失败即停止"),
            ("test --repo dsh-file-review-tab-Multi-git-repository", "只测审查插件"),
            ("pack", "编译后打包到工程根目录 dist/"),
        ),
    ),
    (
        "DSH 工程配置",
        (
            ("initDsh --dry-run", "预览将为启用仓库创建的 DSH 配置"),
            ("checkDsh", "只读校验 DSH JSON 与清单一致性"),
        ),
    ),
    (
        "Desktop 启动、关闭、安装与适配",
        (
            ("desktop info", "查看 Desktop 目录、可执行文件与 Profile 路径"),
            ("desktop start --dry-run", "预览启动 Desktop"),
            ("desktop stop --dry-run", "预览将结束的 Desktop 进程"),
            ("desktop install --dry-run", "预览安装两个插件的 latest 包"),
            ("desktop install --archive <历史包.tgz> --dry-run", "回装 dist 内的指定历史构建"),
            ("desktop patch --dry-run", "预览 app.asar 适配，不修改文件"),
            ("rebuild-install --with-deps", "编译、打包、安装并启动 Desktop"),
            ("rebuild-install --stop-desktop", "先关闭 Desktop，再编译、打包、安装并重新启动"),
            ("rebuild-install --stop-desktop --with-deps --dry-run", "预览关闭 Desktop、安装依赖与完整交付流程"),
        ),
    ),
)

# 示例命令的说明列宽上限：更长的命令把说明换行缩进，避免整行过宽。
_EXAMPLE_NOTE_MAX_WIDTH = 60


def _display_width(text: str) -> int:
    """按终端显示列数计算宽度：中文等全角字符占两列。"""
    return sum(2 if unicodedata.east_asian_width(char) in ("W", "F") else 1 for char in text)


def _column_width() -> int:
    """所有分组共用同一列宽（最宽的用法后仍留一列），让分隔符对齐。"""
    return max(_display_width(usage) for _, _, rows in _SECTIONS for usage, _ in rows) + 1


def _row(usage: str, meaning: str, width: int) -> str:
    padding = " " * max(1, width - _display_width(usage))
    return f"{_INDENT}{usage}{padding}{_GAP}{meaning}"


def _example_width() -> int:
    """示例说明列宽：不超过上限，保证整行不会过宽。"""
    widest = max(_display_width(f"{PROGRAM} {command}") for _, rows in _EXAMPLES for command, _ in rows)
    return min(widest, _EXAMPLE_NOTE_MAX_WIDTH) + 1


def _example_lines() -> list[str]:
    """按分组渲染示例；过长的命令把说明换到下一行缩进。"""
    width = _example_width()
    lines: list[str] = []
    for title, rows in _EXAMPLES:
        lines.append(f"{_INDENT}{title}")
        for command, note in rows:
            full = f"{PROGRAM} {command}"
            if _display_width(full) < width:
                padding = " " * (width - _display_width(full))
                lines.append(f"{_INDENT}{_INDENT}{full}{padding}# {note}")
            else:
                lines.append(f"{_INDENT}{_INDENT}{full}")
                lines.append(f"{_INDENT}{_INDENT}{' ' * 4}# {note}")
        lines.append("")
    return lines[:-1]


def help_text() -> str:
    """返回完整帮助文本（不含结尾空行）。"""
    width = _column_width()
    lines = [
        f"用法: {PROGRAM} <命令> [选项]",
        f"      {PROGRAM} -h | --help",
        "",
    ]
    for title, note, rows in _SECTIONS:
        lines.append(f"[{title}]")
        if note:
            lines.append(f"{_INDENT}{note}")
        lines.extend(_row(usage, meaning, width) for usage, meaning in rows)
        lines.append("")
    lines.append("[公共选项]")
    lines.extend(_row(usage, meaning, width) for usage, meaning in _OPTIONS)
    lines.extend((
        "",
        "[退出码]",
        f"{_INDENT}{_EXIT_CODES}",
        "",
        "[示例]",
    ))
    lines.extend(_example_lines())
    return "\n".join(lines)


def print_help(stream: TextIO | None = None) -> None:
    """打印完整帮助；出错路径传入 stderr。"""
    print(help_text(), file=stream or sys.stdout)
