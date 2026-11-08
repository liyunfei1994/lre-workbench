# 团队协作开发

本项目采用「统一 Git 仓库 → 功能分支 → Pull Request → 自动测试 → 审查合并 → 发布版本」。
开发者同步源码；使用者取得经过验证的发布包。不要把某个人的完整工作目录当作最新版传递。

## 首次加入

团队使用公开仓库 [liyunfei1994/lre-workbench](https://github.com/liyunfei1994/lre-workbench)。
任何人都可直接下载源码。维护者在 Settings → Collaborators 中邀请需要直接推送功能分支的成员。
外部贡献者先在 GitHub 点击 Fork，将下列克隆地址替换为自己的 Fork 地址：

```powershell
git clone https://github.com/liyunfei1994/lre-workbench.git
cd lre-workbench
```

进入克隆目录，双击 `setup_windows.bat`。启动器选择标准版 Python 3.14 x64；
如果没有，会联网下载项目专用的 Python 3.14。无需改动其他项目的 Python。
PyCharm 的解释器选择本项目的 `.venv\Scripts\python.exe`。

## 日常修改

```powershell
git switch main
git pull --ff-only
git switch -c feature/your-change
```

修改后验证：

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
git diff --check
git status
```

只添加与该功能有关的文件，提交并推送自己的分支，再创建 Pull Request：

```powershell
git add <本次修改的文件>
git commit -m "说明本次改动"
git push -u origin feature/your-change
```

在仓库的 Pull requests 页面选择 New pull request，目标分支为 `main`，
来源分支为自己的功能分支（外部贡献者选择自己的 Fork），说明改动目的与验证结果。
由另一位成员审查，测试通过后合并。不要在未检查 `git status` 的情况下覆盖目录或强行同步。
更新代码后双击 `start_windows.bat`，启动器会重新检查依赖；不需要手动重装整个项目。

在 GitHub 中启用 `main` 分支规则，要求至少一人审查，并将以下检查设为合并条件：

- `Windows Python 3.14`

工作流位于 `.github/workflows/validate.yml`，
实际执行状态与下载产物见 [Actions](https://github.com/liyunfei1994/lre-workbench/actions)。
主分支提交、Pull Request 和 `v*` 标签触发检查，也可手动运行。
分支保护在 GitHub 远端设置；实际生效情况以 Settings → Branches 为准。
公开可见不代表所有访问者都能修改主分支。维护者审核 PR 后才合并代码。
其他 Git 服务需要按该平台的 CI 格式移植同样的安装、测试和打包命令。

## 文件边界

| 内容 | 存放方式 |
| --- | --- |
| 源码、测试、依赖约束、操作文档 | Git |
| Python 解释器、`.venv`、安装缓存、快捷方式 | 每台电脑自行生成，Git 忽略 |
| 个人方案、实验数据、CFD 任务和结果 | `user_data/` 或团队数据存储，另行备份 |
| 经过审查的版本 | CI 构建产物，附版本、源码清单与 SHA-256 |

代码仓库不代替实验数据管理。桌面方案仍按文件保存；Web 工作区已有账号、项目、版本、任务队列和本机数据库，
也没有因此成为多人同时编辑同一个方案的协同系统。工作区数据库和个人结果需另行备份，不能随源码提交。

## 依赖与计算模块

`requirements.txt`、`requirements-science.txt` 定义直接依赖，
`constraints.txt` 固定其传递依赖。修改依赖必须同步这些文件并在标准版 Python 3.14 x64 上检查。
不要在本地安装最新依赖后直接把虚拟环境打包给别人。

规范计算包为 `src/lre_workbench/`，入口统一使用 `lre_workbench.*` 导入。IDE 将 `src` 标记为源码根；
不要把某个部件子目录单独加入 `sys.path`，也不要继续使用旧的短模块名或 `src.*` 导入。
包初始化显式拒绝 `src.lre_workbench`，避免同一个模型以两套类身份和缓存加载。

| 修改内容 | 位置 |
| --- | --- |
| 泵、涡轮、燃烧室、喷管、喷注器、预燃室、冷却 | `src/lre_workbench/components/` 对应子包 |
| FFSC 循环、支路、压降预算和系统耦合 | `src/lre_workbench/systems/` |
| 通用迭代、初值、收敛和求解状态 | `src/lre_workbench/solvers/` |
| 数据合同、物性、材料 | `src/lre_workbench/domain/`、`properties/`、`materials/` |
| 动态运行研究和工程分析 | `src/lre_workbench/operations/`、`analysis/` |
| Qt 窗口、绘图与桌面交互 | `src/lre_workbench/desktop/`，工作台入口为 `desktop/workbench.py` |
| Web 接口、存储、任务与浏览器展示 | `webapp/` 和 `webapp/static/` |
| 外部软件交接和开发工具 | `src/lre_workbench/integrations/` 和 `scripts/` |

部件与物性模块不能新增对 Qt、HTTP、数据库或浏览器状态的依赖。包初始化保持轻量，避免纯计算导入触发界面启动。
共享热化学比较使用 `lre_workbench.properties.comparison`；`webapp.thermo_compare` 仅通过 `sys.modules` 别名兼容已有 Web 调用与测试补丁，不能反过来成为内核依赖。
桌面与研究入口通过 `lre_workbench.application.cycle_factory` 延迟选择循环，不能为运行 FFSC 提前导入历史循环实现。
历史代码仍有需要逐步拆分的局部依赖；本轮目录迁移不代表完全消除了这些耦合。详细职责和扩展规则见
[源码架构说明](docs/SOURCE_ARCHITECTURE.md)。

移动文件时同步普通/局部/动态导入、子进程命令、测试替换目标、资源路径及打包清单。
共享资源通过 `src/lre_workbench/resources.py` 定位，不按模块的新目录层数各自猜测项目根。
目录重组应保持物理公式、容差和保存数据合同；算法修改另行说明，并增加能证明行为变化的测试。
数值收敛、工程约束通过、真实 CFD 验证是不同结论。

计算回归使用 `tests/`，Web 适配与前端回归使用 `web_tests/`。结构变动同时验证两个入口和纯计算导入：

```powershell
.\.venv-web\Scripts\python.exe -m unittest discover -s web_tests -p "test_*.py" -v
# 使用已配置完整桌面依赖的环境检查桌面入口。
.\.venv\Scripts\python.exe run_app.py
```

网页开发入口为 `python -m webapp.run --personal-workspace`，已运行同一端口时先按正常停止流程关闭服务。
上述命令从项目根目录执行；不要直接运行某个包内界面文件。

## 发布与回退

### GitHub 更新完成标准

“已推送功能分支”“PR 待审核”和“已进入主分支”是不同交付状态。
用户要求把最新程序更新到 GitHub 时，默认完成目标是经验证的修改进入远端默认分支 `main`；
若明确要求仅提交 PR 或暂不合并，则按该要求执行。

- 推送后核对提交作者在 GitHub 上的账号归属。保留真实作者、时间及历史，不靠空提交或改日期补贡献。
- 将准备交付的 PR 从草稿转为可审核状态，等待当前头提交的 CI 与要求的审核通过。
- 按仓库规则合并，优先采用保留原始提交历史的普通合并。不得为了贡献统计关闭分支保护或调整默认分支；管理员合并例外需单独明确授权。
- 合并后确认 PR 为 `MERGED`，并核验目标提交已包含在远端 `main`，记录合并提交号。
- 缺少审批、检查失败或冲突时，明确报告“功能分支已推送，主分支尚未更新”和具体原因，不将其称为发布完成。

提交贡献需要符合 GitHub 的账号关联与默认分支等条件，统计可能延迟最多 24 小时。
合并历史提交后应按原始提交日期查看贡献图，不保证全部显示在合并当天。
参见 [GitHub：排查缺失贡献](https://docs.github.com/en/account-and-profile/how-tos/contribution-settings/troubleshooting-missing-contributions)。

### 构建与回退

先通过全部测试，再运行：

```powershell
.\.venv\Scripts\python.exe scripts\build_release.py
```

发布包名称为 `release/火箭动力工作台_V7.1.0-team-source.zip`。
它仍是源码安装包，首次运行需要网络或预先准备的依赖。
不要把旧的 `*-windows.zip` 当作本次协作维护版。

包内 `SOURCE_MANIFEST.json` 保存实际文件摘要、构建时间、可用时的 Git 提交号及工作区状态。
包外 `.sha256` 用于校验传输完整性；它不是数字签名。
正式发布应从干净的 Git 提交构建，并设置版本标签。

回退时优先把上一版本解压到另一个目录，在其中重新安装环境，
再打开方案副本验证兼容性。不要把新旧源码和环境混在同一目录。
