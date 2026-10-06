# DemoMultiGitRepoManager

通过 `submodules.ini`、`envVar_v2.ini` 和 `envBuild.py` 管理 `dsh-plugins/` 下的独立 Git 仓库，并按依赖顺序调度插件构建和 DeepSeek Harness Desktop 接入。

目前纳管：

| 仓库 | 分支 | 构建依赖 |
| --- | --- | --- |
| `dsh-multi-git-repo-manager` | `main` | 无 |
| `dsh-file-review-tab-Multi-git-repository` | `main` | 管理插件 |

两个目录保留自身 Git 历史；主仓库忽略 `dsh-plugins/` 内容。主仓库操作在根目录单独进行。

## 环境配置

Git 管理需要 Python 3.10+、支持 `git switch` 的 Git。插件构建按现有插件文档使用 Node.js 24 和 pnpm 11。工具路径留空时从 PATH 获取；配置的非空工具路径无效时会报错。

`envVar_v2.ini` 包含通用参数和 Windows/Linux 平台节点。**DeepSeek Harness Desktop 安装路径从配置读取**，按实际位置修改：

```ini
[envVar_windows]
MRM_DSH_DESKTOP_DIR = D:/app/DeepSeekHarnessDesktop
MRM_DSH_DESKTOP_EXE =
MRM_DSH_PROFILE_DIR =
```

另一台机器也可以创建不提交的 `envVar_v2.local.ini`：

```ini
[envVar_windows]
MRM_DSH_DESKTOP_DIR = E:/tools/DeepSeekHarnessDesktop
```

Windows 的程序默认是安装目录下的 `DeepSeek Harness.exe`；程序名称或布局不同时填写 `MRM_DSH_DESKTOP_EXE` 完整路径。Linux 须明确填写可执行文件路径。Profile 目录与安装目录独立，默认是当前用户的 `.dsh/profiles/desktop`。

优先级为进程 `MRM_*` 变量、本机文件、共享文件、通用默认值；每个文件的平台节点覆盖通用节点。相对路径以本工程根目录为基准，支持 `~`、`$VAR`、`${VAR}` 和 `%VAR%`。读取配置不回写文件、不修改永久环境变量。

| 参数 | 默认／用途 |
| --- | --- |
| `MRM_GIT_EXE`、`MRM_NODE_EXE`、`MRM_PNPM_EXE` | 为空时查找 PATH，非空时使用配置工具 |
| `MRM_GIT_TIMEOUT_SECONDS` | 本地 Git 命令，30 秒 |
| `MRM_NETWORK_TIMEOUT_SECONDS` | clone/fetch/pull/push/batch，300 秒 |
| `MRM_TASK_TIMEOUT_SECONDS` | pnpm 任务、Desktop 安装／适配，900 秒 |
| `MRM_DSH_DESKTOP_DIR` | Desktop 安装目录，Python 无固定安装路径 |
| `MRM_DSH_DESKTOP_EXE` | 可选的 Desktop 可执行文件完整路径 |
| `MRM_DSH_PROFILE_DIR` | 可选的 Desktop Profile 目录 |

```powershell
python envBuild.py init
python envBuild.py info
python envBuild.py info --desktop
python envBuild.py list
python envBuild.py check
python envBuild.py status
```

`init` 只创建缺失的环境模板；已有配置保持原样。普通 Git 管理不要求安装 Desktop、Node 或 pnpm。

## 日常 Git 管理

```powershell
python envBuild.py clone --dry-run
python envBuild.py clone
python envBuild.py fetch
python envBuild.py pull --repo dsh-multi-git-repo-manager --dry-run
python envBuild.py pull --repo dsh-multi-git-repo-manager
python envBuild.py push --repo dsh-multi-git-repo-manager --dry-run
python envBuild.py branch
python envBuild.py showUrl
python envBuild.py switch --repo dsh-multi-git-repo-manager --dry-run
python envBuild.py batch --repo dsh-multi-git-repo-manager -- git log -1 --oneline
```

- `--repo` 可重复，默认只操作启用的清单仓库。目录扫描不会自动增加目标。
- `--dry-run` 对写操作只做本地预检并打印命令，不发起 fetch/pull/push、不切换分支。
- 默认某个独立仓库失败后继续其他仓库；`--fail-fast` 停止后续仓库，汇总中保留失败和跳过原因。
- clone 跳过已存在且身份正确的仓库；非空普通目录、错误仓库、路径逃逸不会被覆盖。
- pull 使用 `--no-rebase --ff-only`；pull/switch 拒绝有未提交或未跟踪改动的工作区，不自动 stash。
- push 要求当前分支与清单匹配，显式推送单一分支，不 add/commit、不 force、不附带标签。工作区未提交改动不会推送。
- `branchReset` 是 `switch` 的别名，实际执行普通 `git switch`。
- status 的 ahead/behind 来自已缓存远程引用，显示“未自动 fetch”。
- batch 的 `--` 后为原样子进程参数，不展开 shell 通配符、管道或重定向；自定义命令的副作用由调用者明确选择。

新增仓库在 `submodules.ini` 添加 section，必填 `path/url/branch`，可选 `remote/enabled/depends_on`。`depends_on` 使用逗号分隔清单名称，配置会检查未知依赖与循环依赖。所有目标路径必须位于 `dsh-plugins/` 内。

## 插件依赖与构建

```powershell
python envBuild.py deps --dry-run
python envBuild.py deps
python envBuild.py build --dry-run
python envBuild.py build
python envBuild.py typecheck
python envBuild.py test
python envBuild.py pack
```

`deps` 使用 `pnpm install --frozen-lockfile`；其余命令委托各仓库已有 scripts。pnpm run 的依赖状态检查使用 warn 模式，依赖安装由 deps 明确执行。build/deps/pack 选择审查插件时自动加入管理插件；执行顺序为管理插件、审查插件，依赖失败时阻断依赖它的任务。构建不会自动安装到 Desktop。

也可以用一个命令依次编译、打包、安装最新包并启动 Desktop。运行前完整退出 Desktop（包括托盘）：

```powershell
python envBuild.py rebuild-install
python envBuild.py rebuild-install --with-deps
python envBuild.py rebuild-install --dry-run
```

`rebuild-install` 默认不安装依赖；首次构建或依赖变更时加 `--with-deps`，在编译前执行 `deps`。流程始终失败即停止，编译或打包失败不会继续安装旧包，安装失败不会启动 Desktop。开始前检查 Desktop 已退出、Profile 和工具有效；安装时再次检查应用状态。支持 `--repo` 选择仓库并自动加入其依赖。`--dry-run` 预览所有步骤，不要求已有安装包；与 `--with-deps` 可以组合使用。

`pack` 优先运行已有 `test:pack`，验证安装包及 SHA256；没有该 script 的新仓库使用普通 pnpm pack 并执行相同的归档规则。先执行 build，再执行 pack。所有安装包统一放在本工程根目录 `dist/`，管理入口通过 `MRM_DIST_DIR` 把绝对输出路径传给插件脚本。包名和版本从各仓库 `package.json` 读取，不写死版本号。

每次成功打包生成两份内容完全一致的安装包：

```text
dist/
  <包名>-<版本>.tgz                            # 按版本归档，同版本覆盖
  <同名历史包>.tgz.sha256
  latest/
    <包名>.tgz                                  # 固定文件名，更新为最近一次成功打包
    <包名>.tgz.sha256
    <包名>.json                                 # 版本、编译时间、摘要及对应版本包文件名
```

例如最新管理包为 `dist/latest/dsh-multi-git-repo-manager.tgz`，最新审查包为 `dist/latest/dsh-file-review-tab-multi-git-repository.tgz`。同版本重新构建覆盖该版本的包，只保留不同版本；这里的 latest 表示最近一次成功打包，不按版本号或文件修改时间猜测。校验失败不会更新 latest；临时打包目录自动清理。

两个 latest JSON 均包含 `buildTime`，使用带时区的 ISO 8601 格式，记录 `lib/client.js` 编译产物的生成时间。例如 `2026-10-06T06:30:00.000Z` 中 `Z` 表示 UTC；只重新打包而未编译时保留原编译时间。

其他定向验证命令为 `test:e2e`、`test:docs`、`test:pack`、`test:install`，具体仓库必须已定义对应 script。默认 test 只运行各仓库的 `scripts.test`，不会自动运行全部集成测试。

## DSH 工程配置

```powershell
python envBuild.py initDsh --dry-run
python envBuild.py initDsh
python envBuild.py checkDsh
```

`initDsh` 为启用仓库创建缺失的 `dsh-multi-git-repo.json` v2 配置，默认不包含主仓库，发现容器为 `dsh-plugins`；已有 JSON 不覆盖。`checkDsh` 校验格式及启用仓库的名称／路径／类型一致性，保留用户在 DSH 中添加的其他目标。外部改变 JSON 后，在 DSH 管理页重新加载配置。

INI 负责仓库来源和分支；DSH JSON 负责界面管理和文件审查范围。修改 INI 后，不会静默覆盖 JSON。

## Desktop 启动、安装与目录选择适配

```powershell
python envBuild.py desktop info
python envBuild.py desktop start --dry-run
python envBuild.py desktop start
python envBuild.py desktop install --dry-run
python envBuild.py desktop install
Get-ChildItem .\dist -Filter *.tgz
python envBuild.py desktop install --archive "<管理包历史文件名.tgz>" --archive "<审查包历史文件名.tgz>" --dry-run
python envBuild.py desktop check-patch
python envBuild.py desktop patch --dry-run
python envBuild.py desktop patch
```

安装前先完成插件 build/pack，并退出 Desktop（包括托盘）。默认一起安装两个插件的 latest：读取索引，验证最新包与历史包内容一致、包内身份及 SHA256，然后以对应版本包路径调用 pnpm，并强制刷新同版本本地包缓存，不额外复制第三份安装缓存。为确保宿主的 hoisted 安装布局也重新导入同版本包，安装前临时移出所选插件的 `package.json`；失败或中断时恢复原文件，成功后清理备份。命令显式指定包名和 file 路径，可更新 Profile 中指向已失效旧 tgz 的同名插件引用。安装位置仍从 `envVar_v2.ini` 读取；安装后在插件页确认需要的插件已启用。

`--archive` 可重复，填写 `dist` 直属历史包文件名或完整路径，以安装指定构建。未指定的配套插件仍使用 latest；安装按包内 peerDependencies 检查配套版本，版本不匹配时阻断。历史包允许早于当前源码版本，便于回装已有构建。`--archive` 仅适用于 `desktop install`。

check-patch 使用管理插件已有适配脚本的 `--check`，只检查配置安装目录下的 `resources/app.asar`。patch 明确调用同一脚本；脚本负责兼容性核验和备份。启动、安装和适配均为独立命令，Git 命令或 build 不会隐式触发。

Desktop 安装与 patch 会检查进程状态，有应用进程时拒绝执行，不自动结束用户进程。对应命令的 dry-run 仍会验证路径、进程和已存在的安装包，但不复制文件、修改 Profile 或 Desktop。

## 验证与退出码

```powershell
python -m unittest discover -s tests -v
```

测试使用临时 Git 工作区、本地 bare 仓库和调度替身，不向真实插件远程推送，也不安装到实际 Desktop。

退出码：`0` 成功，`1` 运行失败或状态阻断，`2` 参数／配置无效，`130` 用户中断。超时终止本次子进程树，批量操作不自动回滚已成功的仓库。
