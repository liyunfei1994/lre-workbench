# 系统仿真校核与验证

核对日期：2026-10-27。校核针对冻结的计算记录，另存检查结果，不修改原设计点、模型参数或计算数值。需要区分三种证据：同一结果的守恒/一致性检查、不同实现的交叉对照、独立试验数据验证。两套软件一致并不证明模型复现了实际发动机。

## 热化学交叉对照

`webapp/thermo_compare.py` 在实际依赖可用时分别运行 Cantera 与 RocketCEA；缺失依赖不能生成替代数值。应比较同一推进剂元素组成、质量 O/F、入口化学焓、燃烧室压力、喷管面积比和膨胀模式下的理想性能，并记录版本、物种数据库与文件指纹。接口兼容默认 `native_databases` 保留两端原生产品范围；网页新建甲烷校核默认选择 `matched_cho_gas`，将两端限制为同一组 16 个中性 CHO 气体物种。后者只对齐允许物种，不声称热力学系数、数据库版本或求解误差相同。差异用于定位问题，不以任意百分比阈值宣告哪套软件“更准确”。

CEA 的火箭模型包含等压绝热燃烧和等熵喷管膨胀，可选择连续平衡、在指定位置冻结，以及有限/无限面积燃烧室。不能将平衡膨胀与燃烧室冻结膨胀直接当成相同问题。[NASA CEARUN 方法说明](https://cearun.grc.nasa.gov/intro.html)

同样需要核对物种的标准生成焓、熵参考压力与热容参数。Cantera 支持 NASA7、NASA9 等模型，标准参考压力随数据来源而异；同名物种不意味着两端采用相同数据。[Cantera 物种热力学模型](https://cantera.org/stable/reference/thermo/species-thermo.html)

入口液相焓应包含液体到气体的相态焓差，不能直接把液氧/液态烃温度填入理想气体焓函数。喷管平衡声速与冻结声速也需区分，尤其在寻找喉部条件时。[Cantera 冻结与平衡声速示例](https://cantera.org/stable/examples/python/thermo/sound_speed.html)

### 2026-10-23 独立对照升级

- `product_policy=native_databases`、`cantera_dataset=production` 保留原生物种范围/热力学数据对照；两端产品集合仍未对齐。
- `product_policy=matched_cho_gas` 使用 `C, CH, CH2, CH3, CH4, CO, CO2, H, H2, H2O, H2O2, HCO, HO2, O, O2, OH`。CEA 输入 `ONLY` 卡必须原样回显，且输出不得出现集合之外的物种。限制为液氧/甲烷基准，不适用于完整富燃预燃室、凝聚/积碳、电离或有限速率验证。[NASA RP-1311 Part II，ONLY 数据集](https://ntrs.nasa.gov/citations/19960044559)
- 只有共同物种模式可选择 `cantera_dataset=nasa_gas_candidate`：使用 Cantera 随附 `nasa_gas.yaml` 中对应的 16 个物种。这是源于 NASA TM-4513 (1993) 的 **NASA7** 数据，本机所选物种声明范围均为 200–6000 K；不能称为最新 CEA NASA9 数据，不能仅因范围更宽就称其更准确，更不会切换生产循环的机制。[Cantera 数据文件及来源](https://github.com/Cantera/cantera/blob/main/data/nasa_gas.yaml)
- 两端基准统一采用 CEA 的 C/H/O 原子量 `12.0107 / 1.00794 / 15.9994 kg/kmol`，仅作用于独立对照。入口化学焓数值固定于同一生产参考态，CEA 的 cal/mol 卡按这些分子量换算，并检查独立返回的燃烧室焓。否则 Cantera 默认原子量略有不同，会使“相同质量 O/F 与 J/kg 入口焓”的说法不严格成立。
- CEA 旧版输入换算采用 `14.696006 psi/atm`，本对照按该常数编码目标压力；避免现代精确 psi 换算与旧程序常数之间约 3.9 ppm 的差别。完整原始 CEA 文本保留在输出中。
- 本机 RocketCEA 1.2.3 的 `get_SpeciesMassFractions` 遍历 `range(ngc)`，而字符名称数组有效项为 `1..ngc`，会漏掉最后一个产物。本对照读取完整真实 common block，并独立检查质量分数和；不通过归一化隐藏缺失。例如共同物种模式下最后一个产品 O₂ 曾使室内报告质量分数之和缺失约 0.0322。读取布局变化时应显式报错，不能生成猜测组成。
- 两端逐站保存燃烧室、喉部、出口温度与参与物种的多项式声明范围；CEA 数据范围从当前安装的 `thermo.inp` 提取，缺失项标为未核实。版本、数据文件哈希、入口焓基准、物种政策和原始输出均保留。

在本机 Cantera 3.2.0 / RocketCEA 1.2.3 / CoolProp 7.2.0 环境中，运行了 54 个独立对照点：压力 10、33 MPa，质量 O/F 3.2、3.6、4.0，面积比 34.34，入口温度甲烷 100 K / 氧 70 K；三种膨胀口径 × 三种数据库/物种组合。54 点均得到两端结果。这是**绝热独立燃烧室与理想喷管基准**，不包含 FFSC 泵、涡轮、冷却回热与其他循环损失，不能拿这里的温度或比冲直接替代整机结果。

在 33 MPa、O/F=3.6、燃烧室冻结的共同物种示例中：

| 热化学数据/实现 | 燃烧室温度 K | 真空 Isp s | 燃烧室温区 |
| --- | ---: | ---: | --- |
| Cantera / GRI30 共同物种 | 3777.926 | 346.63979 | 发生声明温区外推 |
| Cantera / NASA gas 候选共同物种 | 3777.669 | 346.64262 | 在所选物种声明温区内 |
| CEA / 当前安装数据库共同物种 | 3774.301 | 346.49894 | 在当前库所报物种声明温区内 |

这些接近结果**不能证明 GRI30 的外推普遍可靠**。两端仍共同采用理想气体假设，NASA gas 与 CEA 也存在历史数据来源重叠；因此独立求解实现并非完全独立物理证据，尤其不能据此验证高压预燃室非理想性。所选扫描范围内，共同物种对照的温度、c*、真空 Isp 最大相对差分别约为 0.1144%、0.03983%、0.05081%；这是本组案例观察值，不是软件精度或不确定度。

可复算脚本：`diagnostics/thermodynamic_applicability_20261023/independent_reference/run_benchmark.py`。完整记录、原始 CEA 文本与逐点差异在同目录 `benchmark_results.json`、`benchmark_summary.json`。运行 `.venv-web/Scripts/python.exe -X utf8 diagnostics/thermodynamic_applicability_20261023/independent_reference/run_benchmark.py` 不会修改保存的设计方案。

### 2026-10-27 界面与组分读取校核

- 稳态参数区显示只读热化学摘要；新方案采用 `gri30_highT.yaml`。来源、Cantera 版本与全部物种声明温区交集读取本机随附文件。旧 GRI30 方案可显式建立 high-T 草稿，保存时生成新版本，不修改旧结果或同时切换历史燃烧损失模型。
- “截面状态与产物组成对照”读取保存的燃烧室、喉部、出口状态，展示静温、静压、密度、等熵声速、平均分子量、等熵指数和静焓。没有保存的量留空；焓差需有共同参考态证据；等熵指数不一律视为定组成 cp/cv。组分按质量分数展示，未记录不补零，不将异构物或不同相态合并。
- 在补充组分展示时，发现 RocketCEA `ONLY` 路径重复调用可能把名称重置为输入次序，而数组仍为数据库次序。旧的质量分数总和检查不能捕捉此问题。新版受限调用强制重新读取数据库，并检查名称/分子量及逐截面元素守恒，通过后记录 `composition_verification.reader_version=2`。这修复独立校核的数据读取，不改变系统循环的物理方程。
- 旧记录不回写。缺少新版组分读取和元素守恒证据的 CEA 组成及其派生温区诊断暂停展示，提示重新运行；历史宏观性能、截面状态仍可查阅。上文历史基准的 CEA 组成与温区证据也需要重新核查，不能因宏观性能接近而认定物种读取正确。
- “本次校核使用的软件与数据”展示记录当时的软件版本和数据文件。“数据文件校验码”只用于识别文件内容是否相同，不是精度分数；RocketCEA 版本是 Python 接口版本，不是 CEA 数据库版本。顶部另列本机当前可用环境。
- 完整原始字段保留在 JSON 导出中；日常页面使用可读的来源设计记录、物种范围及检查说明，不再重复展示整块诊断 JSON。
- 当前生产循环的纯流体物性后端为 CoolProp HEOS。REFPROP 可通过 CoolProp 接口执行纯流体物性对照；不代替 Cantera/CEA 的反应平衡，也不因安装可用就自动切换循环后端。HEOS 和 REFPROP 可能采用相同原始数据，对照一致性不等于独立实验验证。

## 守恒与记录一致性

本节描述旧 `conservation_checks` 校核器的证据范围。新 FFSC 设计点另外保存 `steady_closure.energy_balance` 的完整端口焓账，并以 `thermodynamic_applicability` 单列热力学适用性；不能将下文旧校核器的 `unavailable` 当作新账目缺失，也不能用新账目通过替代热化学验证。参见 [本轮热力学修正记录](THERMODYNAMIC_APPLICABILITY.md)。

`conservation_checks(source_result)` 接收 `webapp.solver.calculate()` 的标准序列化结果：

```json
{
  "calculation": {"inputs": {}, "summary": {}, "components": {}, "states": {}},
  "assessment": {"status": "pass"},
  "nonfinite_fields": []
}
```

检查从实际数值重新计算，不把汇总中的 `power_balance_ratio=1` 当成证据。

| 项目 | 计算依据 | 绝对 + 相对容差 |
| --- | --- | --- |
| 总流量 | 总流量与燃料、氧化剂流量之和 | `1e-6 kg/s + 1e-6 × max(|左|, |右|)` |
| GG 分流 | GG 两推进剂、主室/GG 分流、主路燃料/GG 燃料 | 同上 |
| FFSC 分流 | 两预燃室的燃料、氧化剂及两路热气流量 | 同上 |
| 主轴/双轴功率 | 涡轮可用功率与主泵功率 | `1 W + 1e-3 × max(|左|, |右|)` |
| 泵功率汇总 | 部件 `power_W` 与汇总 MW 的单位换算 | `1 W + 1e-6 × max(|左|, |右|)` |

这些阈值是本平台明确选取的数值检查政策，不是实验不确定度或厂家验收标准。GG 采用本次记录的机械效率乘涡轮毛功率；FFSC 沿用当前模型的涡轮轴功率定义。GG 双轴分别检查两侧，避免一侧欠功率被另一侧多余功率抵消。外部增压/补压泵不属于当前主轴平衡。

源结果还必须包含收敛信息和工程约束状态。求解残差不低于 `1e-3`、求解警告或已失败的工程约束会阻止完整通过；缺失或非有限数据不会补成零。

目前没有完整、统一基准的全部边界物种组成、凝聚相流量、焓、排气动能、壁面热损失与外部功。因此**完整元素守恒、完整系统能量守恒仍为 `unavailable`**。即使所有可计算项目通过，这一组的总体状态通常仍为 `partial`。不能把质量流量或轴功率闭合标记成完整能量验证。

## 真实流体对比与扫描

`validate_property_request(request)` 严格验证请求并返回规范化副本；`property_checks(request)` 执行检查。最多 20 个状态点：

```json
{
  "points": [
    {"fluid": "Oxygen", "temperature_K": 90, "pressure_Pa": 1000000},
    {"fluid": "Methane", "temperature_K": 110, "pressure_Pa": 1000000},
    {"fluid": "n-Dodecane", "temperature_K": 300, "pressure_Pa": 1000000}
  ],
  "compare_refprop": true
}
```

允许的流体仅为 `Oxygen`、`Methane`、`n-Dodecane`。温度、压力必须是有限数值，不接收字符串、布尔值、任意流体表达式或任意后端名称。通用请求边界为 `0 < T ≤ 10000 K`、`0 < P ≤ 10 GPa`；执行时再依据所选后端的真实 `Tmin/Tmax/pmax` 拒绝超界，不能把请求边界当成物性有效范围。

`properties_si` 返回：

| 字段 | 单位 |
| --- | --- |
| `density_kg_m3` | kg/m³ |
| `enthalpy_J_kg` | J/kg |
| `cp_J_kg_K` | J/(kg·K)，定压比热 |
| `cv_J_kg_K` | J/(kg·K)，定容比热 |
| `speed_sound_m_s` | m/s，声速 |
| `entropy_J_kg_K` | J/(kg·K) |
| `viscosity_Pa_s` | Pa·s |
| `conductivity_W_m_K` | W/(m·K) |
| `enthalpy_delta_J_kg` | J/kg，相对共同参考态 |
| `entropy_delta_J_kg_K` | J/(kg·K)，相对共同参考态 |

每点附带 `phase`、`limits`、`reference_state`、缺失物性名单和状态。单相数值成功返回表示可计算，不宣称精度已被实验验证；T/P 不能唯一确定的两相状态不强行选相。状态方程范围也不代表每项输运关联式在所有区域具有相同精度。

`property_capabilities()` 返回实际 CoolProp 版本及本机 REFPROP 可用性。REFPROP 必须先找到真实的本机动态库，再通过真实氧气点计算探测。可读取已有 `COOLPROP_REFPROP_ROOT`、CoolProp 路径配置、标准安装目录及 PATH；Windows 自动选择与 Python 位数匹配的库。标准安装目录（包括 `C:\Program Files (x86)\REFPROP`）发现匹配库后，平台会在首次加载前将库的完整路径与流体数据库目录传给 CoolProp，保留显式配置的优先权，无需修改系统 PATH。不会下载、安装、购买 REFPROP，也不会在运行中改全局参考态。安装变化后须重新启动服务，刷新进程级能力缓存。[CoolProp 官方 REFPROP 接口与路径说明](https://coolprop.org/coolprop/REFPROP.html)

REFPROP 可用时，使用**同一 T/P 和同一纯流体**分别计算。由于不同数据库的焓、熵零点可能不同，不直接比较原始绝对值：氧/甲烷采用 `300 K, 1000 Pa`，正十二烷采用 `600 K, 1000 Pa` 的共同稀薄气参考点，分别计算本后端的 `h(T,P)−h(ref)`、`s(T,P)−s(ref)`。参考态必须被识别为气态。差异报告 `HEOS − REFPROP` 与除以 REFPROP 绝对值的相对差；参考值近零时相对差为空，不除以零。

不预设物性精度验收阈值；REFPROP 差异的 `agreement_status` 为 `not_assessed`。HEOS 与 REFPROP 可能共享状态方程或原始实验数据，数值一致不能当作两个完全独立的实验证据。CoolProp 的正十二烷资料也分别给出 EOS、输运关联式和后端一致性图。[CoolProp 正十二烷来源与范围](https://coolprop.org/fluid_properties/fluids/n-Dodecane.html)

网页在同一个分析区提供三种输入方式：系统状态点、定压温度扫描、定温压力扫描。扫描将区间等分为 2–20 点，以显式的温度和压力列表保存到独立校核记录中。切换扫描方式或工质后，初始区间取所填状态点的 ±10%，只是便于编辑的初值，不能视为物性有效区间；找不到该工质的来源状态时留空，不猜测工况。

对比界面按工质分组，同时绘制密度、定压比热、动力黏度、导热系数、参考态焓增量与熵增量，并显示逐点两端值、差值和相对差。只有同工质且固定另一坐标的扫描才连成温度或压力趋势；混合工况保留独立状态点，计算缺失或相态变化处断线。声速、定容比热及原始焓/熵放入详细数据中。历史记录缺少的新字段显示空缺，不补算后写回旧记录。

差值均采用后端保存的 `HEOS − REFPROP`，相对差为该差值除以 `|REFPROP|` 再乘 100%。相态不一致时不进行直接差值比较；参考态不一致时不比较焓/熵增量，其他满足条件的指标仍可对照。图中的最大偏差是定位差异的统计量，不是误差或精度验收结论。每个状态点同时显示相态、温压适用上限、参考态和后端可获得的模型来源。图表支持 PNG/SVG，所有工质的数值支持 CSV，完整记录继续支持 JSON。

## 实测数据核对

`validate_experiment_request(request)` 校验并将量纲规范化为 SI，保持输入不变，重复规范化不会二次换算。`experiment_checks(source_result, experiment)` 只读取冻结计算记录。`None`、`{}` 或没有测量行时为 `not_provided`，不能通过。

```json
{
  "role": "holdout",
  "conditions": {
    "propellant_pair": "lox_methane", "cycle_type": "gg", "gg_layout": "single_shaft",
    "pc": {"value": 10.1, "unit": "MPa", "tolerance": 0.01},
    "of_ratio": {"value": 3.4, "unit": "1", "tolerance": 0.01},
    "fuel_inlet_temp": {"value": 100, "unit": "K", "tolerance": 1},
    "ox_inlet_temp": {"value": 70, "unit": "K", "tolerance": 1},
    "ambient_pressure": {"value": 101325, "unit": "Pa", "tolerance": 100}
  },
  "measurements": [
    {"metric": "thrust_N", "value": 830, "unit": "kN", "tolerance": 10, "uncertainty": 5},
    {"metric": "Isp_s", "value": 290, "unit": "s", "tolerance": 3}
  ]
}
```

以上数据仅为接口说明，不是实机试验记录。最多 20 项，指标不能重复。必须确认推进剂、循环、燃烧室压力、全机 O/F、两推进剂入口温度；GG 还需轴系。环境压力下的推力/比冲还需环境压力。每个数值工况需显式绝对容差。工况不匹配、遗漏或源记录失效时，测量差值可显示供诊断，但测量检查为 `unavailable`，不能通过。

`role=calibration` 仅表示标定数据一致性；`role=holdout` 表示提交者声明为未参与调参的独立数据。程序不能自动证明数据独立性。不能将计算值复制到测量字段后称为实测验证，也不能仅因表单由计算工况预填就默认它是试验记录。

支持推力 `thrust_N/thrust_vac_N`、比冲 `Isp_s/Isp_vac_s`、总流量/推进剂流量/GG 流量、主室/GG/排气温度、氧泵/燃料泵出口压力、GG 压力和主泵/涡轮功率。可用单位为同量纲的 `N/kN`、`s`、`kg/s`、`K`、`Pa/kPa/MPa/bar`、`W/kW/MW`，无量纲为 `1`。功率输出从计算记录的 MW 换算为 W。

测量的 `tolerance` 与 `uncertainty` 都是所填单位下的绝对量。给出 tolerance 时，以 `|计算−实测| ≤ tolerance` 判定；仅给 uncertainty 时，只判定是否在所报区间内，不推定其置信水平或覆盖因子，也没有合成模型不确定度。每项返回 `predicted_si/measured_si/difference_si/relative_difference/tolerance_si/uncertainty_si/unit`。

## 后续提高可信度的重点

1. 建立可重复的理想燃烧/喷管基准，统一物种、相、参考焓和膨胀假设，再讨论软件实现差异；损失模型与理想性能分开验证。
2. 补齐元素与能量边界记录，记录泵/涡轮效率图、冷却吸热、压降来源和不确定度，再执行真正的系统能量闭合。
3. 用测量数据及明确有效范围检查真实流体 EOS/输运模型；在接近相变、临界点及超范围区域避免只看两软件相差百分比。
4. 煤油使用有证据的替代物。NIST 的 RP-1 物性模型是四组分混合物，并非纯正十二烷；它的实验精度不能套给本平台。富燃 GG 可另做含凝聚碳相的平衡敏感性研究，但凝聚碳平衡不等同于烟炱生成速率或壁面结焦预测。[NIST RP-1 替代物研究](https://www.nist.gov/publications/preliminary-surrogate-mixture-models-thermophysical-properties-rocket-propellants-rp-1)、[Cantera 气相/固体碳平衡示例](https://cantera.org/stable/examples/python/thermo/adiabatic.html)
5. 只有当停留时间、混合、热损失使平衡假设不足时，再增加有适用范围证据的 0D/1D 有限速率模型，并以独立试验数据验证；复杂程度本身不是更准确的证据。系统级研究无需因此引入 FEA/CFD。[Cantera 停留时间反应器示例](https://cantera.org/stable/examples/python/reactors/combustor.html)

## 检查状态与自动化验证

三个检查组共有 `{kind, status, checks, notes}`，每项至少有 `{id, label, status}`。`pass` 只适用于明确列出的检查判据，`fail` 表示判据不满足，`partial` 表示仅部分证据可用，`unavailable` 表示没有足够依据执行，`not_provided` 表示未提交可选数据。完整试验验证不能由内部守恒检查或交叉软件一致性替代。

运行 `.venv-web\Scripts\python.exe -m unittest web_tests.test_validation_checks -v`（命令中的路径按当前项目根目录使用）。测试包含真实 HEOS 三流体物性、超界拒绝、无 REFPROP 的诚实缺失、参考焓/熵零点消除、单/双轴功率抵消错误、单位归一、工况不匹配及源记录不可变性。测试中的 REFPROP 接口替身仅用于证明参考零点处理逻辑，不充当实际 REFPROP 验证数据。
