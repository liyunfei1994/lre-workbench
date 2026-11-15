# 源码组织与模块职责

本文对应 2026-11-07 的源码包重组。规范 Python 包为 `src/lre_workbench/`，程序入口、Web 适配层、测试和开发工具分别保留在项目根目录、`webapp/`、`tests/` / `web_tests/` 和 `scripts/`。

## 为什么不能只按文件数量或零件名称分类

原 `src/` 中同时存在部件物理模型、循环迭代、物性数据、Qt 窗口、外部软件接口和图标生成工具。文件都位于同一目录时，很难判断一个修改是否会影响计算、界面或外部数据契约；短名导入与 `src.*` 导入混用，也使模块身份和测试替换更难追踪。

泵、涡轮、燃烧室、喷管、喷注器和预燃室适合按部件组织。循环连接、轴功匹配和再生冷却耦合跨越多个部件，需要系统层；通用非线性求解、物性和材料也需要共享层。部件模型的目录表示代码职责，不代表该部件已达到详细设计或工程验证的成熟度。

`src/` 中也不都是每次启动都要加载的文件：Qt 桌面模块只服务桌面入口，历史模型服务兼容与回归，图标属于资源，`__pycache__/` 是 Python 自动生成的缓存。图标生成器现放在 `scripts/make_icon.py`，不属于运行时计算模型。

## 当前目录

```text
项目根目录/
├─ run_app.py                         桌面启动入口
├─ src/
│  └─ lre_workbench/
│     ├─ domain/                      输入、状态、结果、几何类型和参数契约
│     ├─ components/
│     │  ├─ pumps/                    泵模型、几何与转速交叉核算
│     │  ├─ turbines/                 涡轮模型与概念几何
│     │  ├─ chambers/                 主燃烧室、尺寸、连续内型面与设计数据
│     │  ├─ nozzles/                  喷管性能、几何、分离和受热流动
│     │  ├─ injectors/                喷注器模型与几何
│     │  ├─ preburners/               预燃室模型与几何
│     │  └─ cooling/                  冷却、沿程计算、通道与壁面热数据
│     ├─ systems/                     FFSC、稳态网络、压降预算及耦合
│     │  └─ geometry/                 各部件几何结果的系统聚合入口
│     ├─ solvers/                     非线性求解、迭代、初值、状态与收敛
│     ├─ properties/                  热化学、物性、推进剂、适用性与独立比较
│     ├─ materials/                   材料目录、数据集及材料规格
│     ├─ analysis/                    工程评估、扫描与热结构分析
│     ├─ operations/                  热态扰动、供给、预冷、点火和关机研究
│     ├─ application/                 循环选择工厂、工程预设与方案恢复
│     ├─ integrations/                CFturbo、CFD 和燃气数据交接
│     ├─ desktop/                     Qt 窗口、图表、绘图和交互
│     │  ├─ drawings/                 部件示意与三维参考显示
│     │  ├─ cfd/                      Qt 外部进程与 CFD 页面适配
│     │  └─ assets/                   既有桌面图标
│     ├─ runtime/                     任务取消和环境诊断
│     ├─ legacy/                      历史循环兼容实现
│     ├─ resources.py                 与工作目录无关的资源定位
│     └─ version.py                   版本与应用标识
├─ webapp/                            HTTP、账号、项目、队列及保存结果视图
│  └─ static/                         网页 HTML、CSS 与 JavaScript
├─ assets/                            共享字体、图标与样式资源
├─ scripts/                           启动、环境、诊断、打包和开发工具
├─ tests/                             计算内核、桌面与架构测试
├─ web_tests/                         Web 接口、投影视图和前端测试
├─ docs/                              方法、契约、验证与维护文档
└─ user_data/                         个人工作区与运行数据，不随源码发布
```

## 各层如何协作

### 部件与系统

部件包管理自身模型、相关几何和局部设计定义。例如涡轮的循环做功模型位于 `components/turbines/model.py`，几何估算位于同包 `geometry.py`；它们不负责数据库、HTTP 或网页绘制。

`systems/ffsc.py` 编排部件并匹配全流量补燃循环，`systems/steady_network.py` 管理稳态支路，`systems/pressure_budget.py` 定义压降预算，`systems/regenerative.py` 负责再生冷却与循环的耦合。`systems/geometry/` 汇总已保存结果的各部件几何，部件自身的几何函数已经回到对应部件包。

主燃烧室和喷管在代码中分别组织，完整推力室内型面仍需要两者保持坐标、喉部和接口一致。目录拆分不允许分别生成互不一致的两套型面。

### 共享数据、物性和数值方法

- `domain/models.py` 保留 `EngineInputs`、`FlowState`、`ComponentResult`、`CycleResults` 等现有数据合同；`domain/geometry.py` 保存共享几何类型。
- `domain/metadata.py` 是无 Qt 的共享标签与说明。物性适用性报告读取状态名称时不再指向一个桌面界面模块。
- `properties/` 管理 Cantera、CoolProp 等物性适配、数据标识、推进剂定义和适用性证据；计算调用方不能用展示文案替代物性模型。
- `properties/comparison.py` 管理共享的独立热化学比较实现。物性层不再为比较服务反向导入 `webapp`；入口与数据来源仍需按比较协议核对。
- `materials/` 管理固体材料与温度相关数据。`components/cooling/wall_thermal.py` 管理这些材料数据在冷却壁面上的绑定和校核。
- `solvers/` 管理求解过程与数值诊断。数值收敛判定、循环守恒和工程约束仍是不同的检查。

### 界面和外部软件

`desktop/` 是 PySide6 适配层，拥有窗口、Qt 信号、图表和 QProcess。尤其 `desktop/cfd/runner.py` 是 Qt 进程控制器，不能因名称包含 CFD 就归入无界面的计算层。

`integrations/` 管理与外部软件交换的数值、文件和验证契约。CFturbo 交接文件不是原生 `.cft` 工程；通用 CFD 接口也不表示内置了 Fluent、OpenFOAM 或自动网格求解能力。

`webapp/` 仍位于规范计算包之外：它负责认证、权限、项目和版本、数据库、任务队列、HTTP 路由及浏览器数据视图。`webapp/solver.py` 是输入/输出序列化边界，通过 `lre_workbench.*` 调用共享内核；`webapp/__init__.py` 负责源码发行布局的导入准备。

部分 Web 设计交接代码目前仍包含独立的初选和数据整理逻辑，例如 `webapp/turbine_preliminary.py`、`webapp/pump_design.py`。本次重组没有声称这些能力已全部下沉至部件内核。后续若桌面或批处理需要复用，应先明确无界面计算合同，再迁到对应部件包，Web 保留请求校验、来源记录和响应序列化。

热化学比较已完成这一方向的迁移：`webapp/thermo_compare.py` 只保留兼容别名，通过 `sys.modules` 指向 `lre_workbench.properties.comparison` 的同一个模块对象。已有 Web 调用和测试替换目标继续有效，函数、缓存和测试补丁不会因两份模块实现而分裂。新内核代码直接使用规范物性路径，不通过这个 Web 别名调用。

### 依赖方向与本轮边界

新增代码应遵循以下方向：

```text
Web / 桌面 / 脚本入口
        ↓
应用流程 / 系统编排 / 分析
        ↓
部件模型 → 共享物性、材料、数据合同
        ↓
数值与运行支持
```

这是维护约束，而不是宣称所有历史耦合已经消除。现有代码仍有局部导入和职责交叉，例如输入校验读取通道设计规则、收敛汇总读取冷却耦合证据、部分分析模块保留示例和绘图函数，以及既有桌面窗口与推力室面板相互调用。本轮以规范包、领域归属、导入和资源路径为主，保留这些算法及调用行为；后续拆分需要相应行为测试，不能为了“层次纯净”改变物理口径。

禁止在部件或物性包中新增对 `webapp`、数据库、Qt 窗口或浏览器状态的依赖。包的 `__init__.py` 默认保持轻量，避免导入一个纯数据类型时连带加载整个界面或科学求解环境。已有 `systems.geometry` 等聚合入口属于显式例外，应由应用按需调用。

## 阀门、起动器现在在哪里

当前没有为了目录齐全而新增空的阀门或起动器详细设计模块。

| 对象 | 当前实现位置 | 已有能力及限制 |
| --- | --- | --- |
| 液路阀门与执行器 | `operations/feed_network.py` | 单相液路压降、有效面积、行程滞后与速率约束；不含真实阀体详细几何设计 |
| 热态模型中的节流 | `operations/transient.py` | 稳态点附近的有效阀口面积扰动；不等同全工况阀特性 |
| 点火供给阀门 | `operations/ignition.py` | 给定气体边界与阀动作下的容腔响应 |
| 概念阀门需求 | `webapp/valve_requirements.py` | 保存记录的附加节流压降和数据缺项；不反推硬件开度、Cv 或尺寸 |
| 驱动与轴系再启动研究 | `operations/shutdown_rotor.py` | 给定外部驱动力矩、泵负载和惯量下的转子动力学；不预测真实起动器性能 |
| 点火热源 | `operations/ignition.py` | 给定热脉冲及气相有限速率化学；不设计真实点火器，也不判定整机点火成功 |

后续若建立独立阀门模型，应先定义端口、介质与热力状态、流量特性、气蚀/壅塞范围、泄漏和执行器接口，再建立 `components/valves/`。独立起动器则需确定能量源、力矩/功率规律、耦合接口和有效工况后再建立 `components/starters/`。这些名称不应提前暗示不存在的设计能力。

## 历史循环与 FFSC 范围

`legacy/gg_cycle.py` 保存历史循环和已有桌面兼容实现，便于读取旧方案、维持旧回归和追踪既有结果。该目录不为当前 FFSC 网页新增其他循环入口，不把旧循环结果重新标记为 FFSC。网页范围仍由 `webapp/design_scope.py` 和输入契约检查。

`application/cycle_factory.py` 是桌面和研究入口的循环选择工厂：按所选循环延迟导入对应模型，选择 FFSC 时不需要先加载历史循环。桌面调用此应用层工厂，历史模块中的旧工厂仅转发，避免由历史循环模块承担当前系统的选择入口。

移动历史代码到 `legacy/` 不等同删除其历史数据，也不表示旧桌面能力获得新的工程认证。当前产品范围、历史兼容和未来模型扩展应分别记录。

## 启动和开发导入

普通使用继续通过项目根目录的 `start_web_windows.bat` 或 `start_windows.bat`。开发调试在项目根目录执行：

```powershell
# 网页个人工作区；已在运行时不要重复启动同一端口。
.\.venv-web\Scripts\python.exe -m webapp.run --personal-workspace

# 桌面界面，使用已准备完整桌面依赖的解释器。
.\.venv\Scripts\python.exe run_app.py
```

不要直接运行 `src/lre_workbench/desktop/workbench.py`，也不要将某个部件目录单独加入 `sys.path`。入口负责加入项目的 `src` 根目录；新代码统一使用完整模块路径：

```python
from lre_workbench.domain.models import EngineInputs
from lre_workbench.components.pumps.model import Pump
from lre_workbench.components.turbines.model import Turbine
from lre_workbench.systems.ffsc import FFSCCycleModel
```

IDE 可将项目的 `src` 标记为源码根。单独的开发命令若不经过现有入口，需要显式配置该源码根；不能依赖另一个模块恰好修改过导入路径。`core`、`pump`、`src.core` 等旧导入名不再作为新代码的接口；仓库内入口、测试及动态导入应同步迁到规范路径。包初始化会拒绝 `src.lre_workbench` 这一重复命名空间，防止相同文件以两种名称加载，产生不同的类身份和缓存。数据字段名称和 JSON 结果契约不因 Python 模块位置改变而重命名。

`resources.py` 从包位置定位 `PROJECT_ROOT`、`ASSET_ROOT` 和 `DESKTOP_ASSET_ROOT`，不依赖进程当前目录。源码发行包须保持项目、`src/lre_workbench` 和共享 `assets` 的相对布局；本次没有引入新的 wheel 安装或单文件打包承诺。

## 修改和验收约定

1. 新增物理模型先选择部件或系统归属，再定义输入、输出、单位、来源和适用范围。不要继续向 `src/` 根部增加散落模块，也不要为尚未实现的能力建占位包。
2. 单独的目录重构保持公式、求解容差、预设值和数据键不变。若同时修正算法，应拆开描述并增加能证明数值变化的测试。
3. 移动模块须检查普通导入、局部导入、动态导入、子进程字符串、测试替换目标、资源定位和打包清单。
4. 计算改动运行对应部件与循环回归；界面改动运行相应 Web 或 Qt 测试；结构变动还要验证从非项目工作目录启动、纯计算导入不加载 Qt、静态资源发布和源码包可运行性。
5. 不在生产模块中为迁就测试保留两份实现。测试应导入规范模块；外部脚本按新的完整路径迁移。确有已发布的适配层调用需要兼容时，可像 `webapp.thermo_compare` 一样显式别名到同一模块对象，并验证对象身份；不能复制函数实现或默默建立第二套缓存。
6. 个人数据库、历史方案和计算结果属于运行数据；源码迁移不重算、不覆盖这些记录。回归证据应区分代码组织正确、数值结果一致与物理模型已经验证。

完整开发和发布流程见 [CONTRIBUTING.md](../CONTRIBUTING.md)。

## 本次迁移验证（2026-11-07）

- 已解析生产代码的 430 处规范包导入，目标模块均存在；新增 8 项架构守卫通过。
- Web 后端全量 668 项：667 项首轮通过，1 项 RocketCEA 用例因测试临时目录含中文和空格失败，改用系统 ASCII 临时目录复测通过。
- Web 前端全量 861 项通过。桌面入口、资源、打包和 Windows 相关 38 项通过。
- 桌面/科学计算全量首轮运行 712 项；迁移遗漏的动态补丁路径、日志名称和源码读取路径已修正，相关 36 项复测通过。统计有重叠，不把这些数字累加为总测试数。
- 对迁移前源码进行独立 AST 保真审查：部件公式、数值求解、默认参数和数据字段未发生意外改变。资源路径、导入名称、源码来源说明和工厂职责的调整属于本次架构变更。
- 本机个人工作区已在无活动计算时重载；健康检查和前端资源返回正常。

桌面全量仍有以下 6 个迁移前已存在的问题，均已在隔离的原始源码副本复现，不能把本次验收写为“所有测试通过”：

| 既有问题 | 影响的测试范围 |
| --- | --- |
| 预燃室暂定上限标签与断言不一致 | `test_assessment_ui` |
| 原生循环夹具未收敛，类初始化失败 | `test_cfd_panel` |
| GG 喷管节流用例被当前 FFSC-only Web 边界拒绝 | `test_nozzle_contour_methods` |
| 显式 unset 旧参数字典与 FFSC 过滤结果不相等 | `test_preburner_constraints` |
| 旧材料分区回读的完整字典与过滤结果不相等 | `test_wall_material_defaults` |
| 替换材料数据集后，默认输入构造先报数据集缺失 | `test_wall_throttle_binding` |

本地迁移备份、全量日志、原始代码复现日志和保真审查记录位于 `tmp/src-refactor/`，不作为源码发行内容。
