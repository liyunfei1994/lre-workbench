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

代码仓库不代替实验数据管理。现阶段方案 JSON 仍是单人编辑文件，
没有多人同时编辑、账户权限、服务器任务队列或数据库。

## 依赖与计算模块

`requirements.txt`、`requirements-science.txt` 定义直接依赖，
`constraints.txt` 固定其传递依赖。修改依赖必须同步这些文件并在标准版 Python 3.14 x64 上检查。
不要在本地安装最新依赖后直接把虚拟环境打包给别人。

UI 修改尽量放在 `workbench.py` 和相关界面文件；计算改动放在对应计算模块，
并增加能证明行为变化的测试。数值收敛、工程约束通过、真实 CFD 验证是不同结论。

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
