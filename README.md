# 火箭动力工作台 V7.1.0 · 协作维护版

正式名称：**液体火箭发动机数字样机设计与分析平台**。英文名称：**LRE Workbench（Liquid Rocket Engine Workbench）**。

本次维护修复跨电脑启动，并补齐团队开发和发布流程。安装路径由当前文件夹决定，
不再依赖原作者的用户名、桌面目录或基础 Python。计算版本及方案格式保持 V7.1.0 兼容。

## 打开新版

**现在直接双击 `start_windows.bat`。** 第一次运行会检查并准备本机环境，完成后打开工作台。
需要仅安装 / 修复时双击 `setup_windows.bat`，完成后会重新生成当前目录的图标。

截图里的“无法找到脚本文件”是旧快捷方式指向另一台电脑造成的。搬动文件夹后，
请使用文件夹内的 `start_windows.bat`，或重新运行安装脚本生成图标。不要复制 `.lnk` 给其他成员。

| 入口 | 用途 |
| --- | --- |
| `start_windows.bat` | 检查环境并启动应用 |
| `setup_windows.bat` | 准备环境，修复当前文件夹的快捷方式 |
| `diagnose_windows.bat` | 生成 `diagnostics/` 下的环境报告，便于定位问题 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 团队代码仓库、分支、审查、测试和发布流程 |
| [平台建设方案](docs/PLATFORM_PLAN.md) | 桌面版向内网网页平台逐步迁移的架构与边界 |

**多人开发使用统一公开仓库：[liyunfei1994/lre-workbench](https://github.com/liyunfei1994/lre-workbench)。**
任何人都可查看、下载和 Fork；外部贡献者从自己的 Fork 发起 Pull Request，
受邀协作者可向仓库推送功能分支。具体步骤见 [协作指南](CONTRIBUTING.md)。
主分支提交与 Pull Request 的安装、测试和打包结果见 [GitHub Actions](https://github.com/liyunfei1994/lre-workbench/actions)。
分支保护的启用情况以仓库设置为准。当前仍是桌面程序，尚未实现浏览器使用、
共享数据库、多人同时编辑或服务器端计算队列。

桌面程序当前仍专注 Windows。通用 JSON / CSV 任务包可传给其他电脑的 CFD 适配程序；没有添加 Linux 桌面启动器。

## V7.1.0 负责什么

工作台保留系统循环匹配、初步尺寸和工程约束检查，新增 **CFD 协同** 页面。

- **内部初算** 保留 V7.0.0 的分析流程，旧方案默认按此路线打开。
- **外部 CFD 协同** 保留循环初算，关闭内置沿程详细冷却与本地二维 FEM 入口，把详细流场、冷却换热及涡轮流动交给外部工具。
- 导出任务包包含原提交输入、求解器有效参数、状态点、工程警告、环境信息、边界 CSV、轴对称参考型面和结果模板。
- 外部适配器可由界面显式选择，支持运行日志、取消和超时；也可手动在另一电脑计算后导入结果。
- 回传结果单独展示，检查任务身份、指纹、单位、数值范围和分析域；支持指标对比与壁温曲线。
- 已确认来源的真实冷却 CFD 压降、热负荷可用于循环复算；同时关闭对应的自动估算，保证外部值实际参与计算。

本版实现通用数据接口和适配器运行框架。**尚未绑定 Fluent / OpenFOAM 的原生 API，也没有内置 CFD 求解器或三维自动网格生成器。** 确定具体软件后，需要按其工程、网格和物理模型实现适配程序。

## 第一次使用 CFD 协同

1. 打开 **CFD 协同**，选择 **外部 CFD 协同** 和任务类型：推力室、冷却通道或涡轮；涡轮可选择富氧 / 富燃侧。
2. 设定原方案参数，运行设计点，检查数值收敛和工程警告。这里的结果仍是循环初算。
3. 点击 **导出通用任务包**，选择存放文件夹。未明确压力 / 温度口径的包可用于准备外部建模。
4. 在外部软件确认分析域、真实几何、网格、组分、壁面、湍流 / 燃烧模型及边界映射。压力参考采用绝压 Pa；不能把模型状态直接当作 CFD 总压 / 静压边界。
5. 完成外部确认后，在工作台选择相应 P/T 口径、勾选复核，再导出新的任务包。每个任务都有独立身份，适配器必须读取本次 `manifest.json`。
6. 用真实 CFD 软件计算，把输出按 `result_template.json` 的契约整理为 `result.json`。可在本机选择适配器程序运行，也可手动传回文件。
7. 点击 **导入结果 JSON**，检查来源、求解器报告的状态、指标和可选壁温曲线。
8. 对冷却任务，符合条件时可点击 **采用冷却压降 / 热负荷并复算**；核对新的循环匹配和工程约束，再保存方案。复算改变了工况，下一轮 CFD 应创建新任务。

单通道或周期扇区 CFD 的热负荷 / 流量需由外部适配器正确汇总为全发动机总量。工作台不自动把任意局部结果乘以通道数。测试数据、未收敛数据或不匹配工况的结果不会用于反馈。

## 自动运行外部适配器（可选）

选择真实适配器的可执行程序。若适配器是 Python 脚本，应选择其 Python 解释器，再把脚本路径作为第一个参数。

参数是字符串数组，每一项独立传入，支持 `{manifest}`、`{result}`、`{job_dir}`。例如：

```json
["C:\\CFD tools\\my_adapter.py", "--manifest", "{manifest}", "--result", "{result}"]
```

任务运行期间可以查看日志并取消，超时范围为 1–86400 秒。取消只停止本任务启动的进程树。已有 `result.json` 的任务不会被自动重跑覆盖，应新建任务。

`scripts/cfd_adapter_example.py` 是接口开发示例。默认明确拒绝生成真实 CFD 结果；只有显式 `--self-test` 才输出标记为 **测试数据** 的固定值，用来验证文件交接，不能用于设计判断或反馈。

协议、指标、Python API 和适配器说明见 [CFD 接口文档](docs/CFD_INTERFACE.md)。

## 新电脑安装

1. 从团队发布渠道取得 `火箭动力工作台_V7.1.0-team-source.zip`，解压到可写目录。
2. 双击 `setup_windows.bat`。需要 Windows x64；首次安装需要联网。
3. 启动器使用标准版 Python 3.14 x64。没有时自动下载经摘要校验的 uv，
   并在项目 `.runtime/` 内准备 Python 3.14，不修改系统 PATH 或其他项目。
4. 安装完整科学依赖，校验版本、Cantera 机制与 CoolProp 功能，生成当前目录快捷方式。
5. 双击 `start_windows.bat` 或重新生成的 **火箭动力工作台 V7.1.0** 图标。

每次启动都会验证真实环境。健康环境不重复下载；旧 Python 版本或失效的 `.venv` 会先改名为
`.venv.backup-日期-标识` 再重建。检查新版本可用后可自行归档这些备份。
同一时间只能有一个安装 / 修复流程，避免多个启动窗口同时改写依赖。

依赖来源为 PyPI；自动准备 Python 还需要访问 GitHub。网络受限时，可自行安装标准版 Python 3.14 x64，
再双击安装脚本。也可以在 PowerShell 中通过 `LRE_PYTHON` 指定已有解释器的完整路径：

```powershell
$env:LRE_PYTHON = 'C:\Python314\python.exe'  # 替换为本机真实路径
.\setup_windows.bat
```

离线安装需要提前在相同系统、架构和 Python 次版本上准备 wheel 文件，
同时提供可用的基础 Python。准备机器执行：

```powershell
python -m pip download -r requirements.txt -r requirements-science.txt -c constraints.txt -d wheelhouse
```

目标机器设置 `PIP_NO_INDEX=1` 和 `PIP_FIND_LINKS` 为 wheelhouse 的真实目录后再运行安装。
`--basic` 仅用于明确选择基础界面环境的调试，不等价于完整科学环境。

不要跨电脑复制 `.venv`、`.runtime` 或快捷方式。发布 ZIP 是源码安装包，启动器负责准备运行环境；
外部 CFD 软件及其许可证不在安装包中。Python 虚拟环境应在目标机器重建，参见
[Python 官方说明](https://docs.python.org/3.14/library/venv.html)。

当前依赖锁定为 Cantera 3.2.0、CoolProp 7.2.0 等支持 Python 3.14 的版本，
不使用 free-threaded Python 构建。支持范围及迁移说明见 [Python 3.14 迁移记录](docs/PYTHON314.md)。

## 保存、恢复与诊断

可打开 V7.0.0 方案。保存 / 放弃 / 取消保护和自动恢复保留；CFD 本地配置、未完成的参数文本、内部计算偏好和反馈记录随方案 / 草稿保存。

CFD 任务进行中不能替换方案、恢复另一份草稿或直接关闭窗口，先取消并等待停止。边界条件或原工况改变后，旧任务可诊断查看，不能继续用于当前工况反馈。

- 草稿：`%LOCALAPPDATA%\lre-workbench\7.1.0\recovery`。
- 应用日志：`%LOCALAPPDATA%\lre-workbench\7.1.0\logs\workbench.log`。
- 外部任务日志与终态：任务目录中的 `adapter.log`、`run_status.json`。
- 安装环境诊断：双击 `diagnose_windows.bat`；报告含本机路径、依赖和 Git 状态，不读取个人方案。

“数值收敛”和“工程约束”继续独立显示。默认 FFSC 初算仍可能欠压或超温，CFD 求解器声明收敛也不证明网格独立性、守恒或整机可行性。材料库数据及局部模型用于初步分析。

## 验证与打包

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts\build_release.py
```

验证记录见 [docs/VALIDATION.md](docs/VALIDATION.md)。发布包不含本机 `.venv`、个人方案、CFD 任务和运行日志。
新包使用 `*-team-source.zip` 文件名，附 `.sha256` 校验文件及包内 `SOURCE_MANIFEST.json`。
旧的 `*-windows.zip` 不会被本次打包覆盖。

内置 Noto Sans SC 使用 SIL Open Font License 1.1；授权见 `assets/fonts/OFL.txt`。图标沿用工作台矢量资源。
