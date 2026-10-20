# 团队协作开发

本项目采用「统一 Git 仓库 → 功能分支 → Pull Request → 自动测试 → 审查合并 → 发布版本」。
开发者同步源码；使用者取得经过验证的发布包。不要把某个人的完整工作目录当作最新版传递。

## 首次加入

维护者在团队选定的 GitHub、GitLab 或内网 Git 服务上建立**私有仓库**，邀请成员。
仓库地址由团队提供；当前本地工程不会自动上传。首次推送前确认组织归属和代码的共享范围。
仓库准备好后：

```powershell
git clone <团队仓库地址>
```

进入克隆目录，双击 `setup_windows.bat`。启动器选择 Python 3.11/3.12 x64；
如果没有，会联网下载项目私有的 Python 3.11。无需改动其他项目的 Python。
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

只添加与该功能有关的文件，提交并推送自己的分支，再创建 Pull Request。
由另一位成员审查，测试通过后合并。不要在未检查 `git status` 的情况下覆盖目录或强行同步。
更新代码后双击 `start_windows.bat`，启动器会重新检查依赖；不需要手动重装整个项目。

在 GitHub 中启用 `main` 分支规则，要求至少一人审查，并将以下检查设为合并条件：

- `Windows Python 3.11`
- `Windows Python 3.12`

工作流位于 `.github/workflows/validate.yml`。这是仓库配置文件；
上传到 GitHub、启用 Actions 后才会在远端执行。分支保护需要管理员在远端设置。
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
`constraints.txt` 固定其传递依赖。修改依赖必须同步这些文件并在两种 Python 版本上检查。
不要在本地安装最新依赖后直接把虚拟环境打包给别人。

UI 修改尽量放在 `workbench.py` 和相关界面文件；计算改动放在对应计算模块，
并增加能证明行为变化的测试。数值收敛、工程约束通过、真实 CFD 验证是不同结论。

## 发布与回退

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
