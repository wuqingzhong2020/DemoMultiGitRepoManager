# 多 Git 管理一次性交付与验证记录

日期：2026-10-06。

已修正方案第 11 节，取消分阶段交付；Git 管理、环境配置、构建调度、DSH 配置和 Desktop 接入一起实现。

最新追加交付：版本包与 `dist/latest` 双副本已生成，两个 latest JSON 均增加编译时间 `buildTime`；同版本重建覆盖，只有不同版本保留历史包。两个插件已实际安装到 Windows Desktop Profile；安装后的全部 208 个包文件逐字节匹配对应安装包，其中运行文件、声明和补丁共 163 个。

## 交付内容

- `submodules.ini`：登记两个现有仓库、实际 remote URL、main 分支，以及审查插件对管理插件的构建依赖。
- `envVar_v2.ini`：工具与超时参数，Windows/Linux 节点，可覆盖的 Desktop 安装目录、程序路径和 Profile 目录。当前 Windows 路径为 `D:/app/DeepSeekHarnessDesktop`，由配置提供。
- `envBuild.py`、`pythonProject/`：完整 CLI、路径与 Git 身份核验、分支与 remote 检查、结果汇总、dry-run、超时／中断、依赖排序、包身份与 SHA256 检查、Desktop 进程检查。
- `dsh-multi-git-repo.json`：通过 initDsh 创建两个仓库的 v2 工程配置，发现容器为 dsh-plugins，不包含主仓库。
- `tests/test_manager.py`、README 和更新后的方案文档。
- `.gitignore`：保留已有 dsh-plugins、dist 和 `.cache/` 忽略规则，增加本机覆盖和 Python 缓存忽略项；当前安装直接使用历史包，不生成第三份安装缓存。

## 自动验证

Windows 环境：Python 3.14.8、Git 2.53.0.windows.2、Node.js 24.21.0、pnpm 11.25.0。

初次交付运行原 38 个用例及 13 项定向回归，全部通过。本次统一配置文件名和安装包目录后，重新执行 `python -m unittest discover -s tests -v`：41 项全部通过，耗时约 180 秒。包含新增的根目录 dist 打包、摘要生成、安装读取以及新 JSON 文件名检查。

覆盖的行为包括：

- 使用含空格的临时目录、本地 bare 仓库进行真实 clone/fetch/pull/push/switch。
- clone 的重复执行、非空普通目录保护，以及自定义 remote 名称。
- 父 Git 仓库不被误认作子仓库；linked worktree 的 `.git` 文件正常识别。
- Windows junction 逃逸、绝对／父目录逃逸、重复／重叠路径和循环依赖拒绝。
- dirty、detached HEAD、分支不一致、fetch/push URL 不一致、未完成操作阻断。
- 分叉拉取拒绝自动 merge/rebase；推送只包含已提交内容。
- dry-run 不执行目标写操作，失败继续／fail-fast 与入口退出码正确。
- 配置覆盖不回写文件；Desktop 路径切换不需修改代码；Git 管理不依赖 Desktop/Node/pnpm。
- Windows pnpm shim 解析为 Node + JS，参数中的空格及 shell 特殊字符保留为字面值。
- 超时结束测试进程并返回非零；用户中断返回 130。
- 构建按管理插件、审查插件顺序执行，依赖失败阻断消费者。
- DSH v2 配置非覆盖创建、严格字段检查和 INI 一致性检查。
- 安装包摘要、包内身份／版本、非法元数据拒绝；Desktop 安装和适配使用测试替身调度。
- Desktop 运行时拒绝安装和 patch；install dry-run 不复制缓存、不修改测试 Profile。

## 真实工程检查

| 检查 | 结果 |
| --- | --- |
| info --desktop | Git/Node/pnpm 正常解析，Desktop 路径从 envVar_v2.ini 读取 |
| check、status | 两仓库身份、main 分支与 remote 匹配，工作区干净 |
| initDsh、checkDsh | v2 配置已创建并与启用清单一致 |
| build/deps/pack --dry-run，选择审查仓库 | 自动加入管理仓库并按正确顺序打印任务 |
| pull/push/branchReset --dry-run | 所选仓库及命令参数正确，未执行同步或切换 |
| batch --dry-run | git log 参数和工作目录正确 |
| desktop check-patch | 已配置 Desktop 的目录选择器已有起始路径支持，只读检查成功 |
| desktop install/patch --dry-run | 当前 Desktop 进程正在运行，正确返回状态阻断；未关闭应用 |

真实插件仓库 HEAD 保持：

| 仓库 | HEAD |
| --- | --- |
| dsh-multi-git-repo-manager | `3228867802aa2b815596773175d19e930c863e07` |
| dsh-file-review-tab-Multi-git-repository | `234a221856b16c987f16f3ac334e2cb5b476b438` |

初次检查时审查仓库相对已有 origin/main 引用显示 ahead 1；该信息来自缓存引用。配置命名交付修改了两个插件的文件名引用、打包脚本、文档和测试，并同步了编译产物，没有 fetch、提交或推送真实仓库。参考工程与 Desktop 安装目录保持原状；最新的 Profile 实际安装记录见下文。

## 配置命名与安装包目录复验

按后续要求，将配置文件统一为 `dsh-multi-git-repo.json`，管理脚本和两个插件仅使用新文件名，不提供旧文件名回退或迁移。根 JSON、双语界面和文档、测试及编译产物已同步；管理插件的编译模块从子仓库目录向上找到根 JSON，并正确读取两个仓库。

前次配置命名交付的安装包和 SHA256 文件已统一位于主工程根目录 `dist/`。下表保留当时的验证记录；这些同版本文件随后已覆盖，当前安装包见本文末尾：

| 安装包 | 字节数 | SHA256 |
| --- | --- | --- |
| `dsh-multi-git-repo-manager-0.1.4.tgz` | 293227 | `f16c9ea02187a192c7e00514c95f6f8545bf859a3e9e3a183d4ed4c6542154df` |
| `dsh-file-review-tab-multi-git-repository-0.3.5.tgz` | 1729796 | `ce5919eb2b20e662c68fe8637dedc45455c191ad66626349def752bb938475a4` |

每个安装包均配套同名 `.sha256`。两个插件原有 dist 中的安装包已迁出，空目录已移除；`pack`、`test:pack` 和 Desktop 安装读取路径保持一致。管理入口传入绝对 `MRM_DIST_DIR`，普通 pnpm pack 后备路径也指向根目录 dist。

| 本次执行 | 结果 |
| --- | --- |
| `deps` | frozen-lockfile 重建工程搬迁后的本地依赖链接，两仓库均从缓存取包，下载 0；package.json 和锁文件未变更 |
| `build` | 先管理插件、后审查插件，两者均成功 |
| `typecheck` | 两仓库均成功 |
| `test` | 管理插件 48 项、审查插件 176 项全部通过 |
| 审查插件 `test:docs` | 双语章节、18 张 JPEG、Host 文档与协议检查通过 |
| `pack` | 管理包 63 项、审查包 145 项归档内容、导出和 SHA256 检查通过 |
| Python 管理脚本完整回归 | 41 项全部通过 |
| 包内容及路径复核 | 两包位于根 dist、身份与摘要正确；各有 10 个文件包含新配置名，包内没有旧配置文件名 |
| `checkDsh` | 根 JSON 与启用的 INI 仓库一致 |

pnpm run 保留启动 shim 的静态配置，并将依赖状态检查设为 warn，避免普通构建隐式重装依赖；依赖重建通过明确的 `deps` 完成。

## 验证边界

配置命名交付已运行两个真实插件的完整构建、类型检查、功能测试、文档检查和安装包验证。最新追加交付实际更新了 Windows Desktop Profile，未启动 Desktop 或修改 ASAR；真实宿主启动后的界面行为不属于这次安装验收。

Linux/WSL 实际环境未运行；平台分组、跨平台路径和进程实现已提供，Windows 是本次实际验收平台。

后续开发者按 README 调用明确的 build/pack 和 desktop install 命令即可使用已交付能力；DSH 会话需重新加载已创建的工程 JSON。

## 同版本覆盖、latest 与编译时间交付复验

每次成功打包将一个安装包保存为两份相同字节：dist 直属版本包使用 `<包名>-<版本>.tgz`，latest 使用 `<包名>.tgz`，分别配套 SHA256。latest JSON 记录包名、版本、摘要、版本包路径及 `buildTime`。同版本重建覆盖该版本包，只保留不同版本的历史包；当前 dist 直属只有两个标准版本包，没有同版本的时间戳／摘要旧包。失败的包内容检查不更新 latest，临时打包目录自动清理。

`buildTime` 为 `lib/client.js` 编译产物的生成时间，使用带时区的 ISO 8601 UTC 格式；只重新打包时不改变编译时间。已核对两个字段与对应编译产物时间相符。

| 当前 latest | 字节数 | SHA256 |
| --- | --- | --- |
| `dist/latest/dsh-multi-git-repo-manager.tgz`，版本 0.1.4 | 293692 | `d0a2d98a4a5630e3d52682e96821462d6b1e112e5502501b3d27711a7e0cb7df` |
| `dist/latest/dsh-file-review-tab-multi-git-repository.tgz`，版本 0.3.5 | 1730452 | `122e9d6c881b09f6518180eef9d6d80d1ef8b22c84bc879ecda8aa5c5a11563f` |

| JSON 索引 | buildTime |
| --- | --- |
| `dist/latest/dsh-multi-git-repo-manager.json` | `2026-10-06T06:44:47.280Z` |
| `dist/latest/dsh-file-review-tab-multi-git-repository.json` | `2026-10-06T06:44:54.530Z` |

| 本次检查 | 结果 |
| --- | --- |
| 安装包规则测试 | 7 项通过：同版本覆盖／不同版本保留、失败保护、篡改拒绝、索引一致性、路径边界、SHA256 文件名和编译时间 |
| 相关管理脚本回归 | 4 项通过：包校验、普通 pack、latest 安装、历史版本回装与配套版本冲突；缓存刷新修正后两项安装用例再次通过 |
| 安装信息刷新测试 | 4 项通过：仅刷新所选包、成功清理、失败／中断恢复、非法文件预检和 Windows junction 逃逸拒绝 |
| 两插件 `build` / `test` | 构建按依赖顺序成功；管理插件 48 项、审查插件 176 项全部通过 |
| 真实两个插件 `pack` | 管理包 63 项、审查包 145 项内容校验通过；版本包与 latest 字节及摘要一致，staging 目录清空 |
| 双语文档检查 | 通过；发布文档补充编译时间说明后再次通过 |
| 临时 Profile 同版本覆盖实验 | 普通布局可用 force 更新；hoisted 布局复现只用 force 仍保留旧文档，临时移出所选包信息后成功导入重建内容 |
| 本机 `desktop install` | Desktop 已退出后成功安装到 `C:/Users/87268/.dsh/profiles/desktop`；不复制第三份安装包缓存 |
| 安装后完整文件核验 | 管理插件 63 项、审查插件 145 项全部包文件（包括文档）与安装包逐字节一致；其中运行文件／声明／补丁为 51 + 112 项 |
| Profile 与目录核验 | 两插件依赖指向本工程标准版本包，bundles 已启用；没有遗留包信息备份或插件目录下的 dist |
| 历史选择入口 | `--archive` 可重复；历史包不要求等于当前源码版本，依赖版本冲突时阻断 |

安装命令使用显式 `包名@file:版本路径`，可替换 Profile 中失效的旧 tgz 引用。宿主使用 pnpm hoisted 安装布局，该布局按包路径和版本跳过已安装包，仅传 `--force` 不足以更新同版本内容。因此安装前临时移出所选插件的普通 `package.json`，让 pnpm 完整导入；失败或中断时恢复原文件，成功后删除临时备份。路径预检拒绝修改 Profile 外的共享包信息文件。本次未执行 Desktop 启动或 ASAR 修改。

## 完整流程命令追加验证

新增 `python envBuild.py rebuild-install`，默认执行编译、打包、安装 latest 和启动 Desktop，默认不安装依赖。加 `--with-deps` 才先安装依赖；支持 `--repo` 自动加入依赖仓库和 `--dry-run` 预览。开始前检查安装环境，任何步骤失败即停止，避免编译／打包失败后安装旧包，或安装失败后启动应用。

- `tests/test_workflow.py` 的 10 项测试全部通过：默认跳过 deps、显式依赖安装、依赖排序与目标选择、各步失败停止、安装异常／非零返回值、前置检查阻断、没有旧安装包时的只读预览、组合参数、Ctrl+C 返回 130 和非法仓库选择。
- 现有 Desktop 安装、历史包选择、应用运行阻断和 CLI 退出码的 4 项定向回归通过。
- 真实工程 `rebuild-install --dry-run` 成功，输出四步流程；`rebuild-install --with-deps --dry-run --repo dsh-file-review-tab-Multi-git-repository` 成功，输出五步流程并自动加入管理插件。
- 已复核现有安装包、latest 索引及摘要一致。此次新增命令验证使用测试替身和真实工程预览，没有实际重新编译、安装或启动 Desktop。
