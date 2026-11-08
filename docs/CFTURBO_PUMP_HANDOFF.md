# 泵设计与 CFturbo 建模交接

核查日期：2026-11-07。对象为全流量补燃循环数字样机的液氧主泵和甲烷主泵。本文按 CFturbo 官方在线英文手册核查；官网当日列出的当前版本为 **CFturbo 2026 R2（2026 年 6 月）**。官方发布说明将子午轮廓导入列入 2026 R1 的改进。用户本机安装版本和许可模块尚未确认，本文不表示已在该安装环境执行导入验收。[官方发布说明](https://cfturbo.com/support/whats-new)、[在线手册](https://manual.cfturbo.com/en/cfturbo.html)

## 页面名称与职责

建议大标题使用 **“泵设计与建模交接”**，副标题使用“液氧 / 甲烷主泵 · 设计点、汽蚀条件与代表级流道”。“泵初步几何”适合已有几何子视图，但不足以覆盖设计点、部件配置、数据来源和交接要求。“详细泵模型”则超出了当前计算范围。CFturbo 把设计点、主尺寸、子午轮廓、叶片以及定子、蜗壳分别作为设计步骤；平台可以沿用这种分层组织，而无需在总体设计阶段声称完成全部部件设计。[CFturbo 设计能力](https://manual.cfturbo.com/en/cfturbo.html)、[项目结构](https://manual.cfturbo.com/en/interfaces.html)

当前平台提供保存工况与显式假设构成的代表级流道。四分之一椭圆轮毂 / 轮盖过渡是几何插值，并非 CFturbo 的完整叶型算法。页面应分别呈现“循环结果”“由记录推导”“初选假设”“待部件设计”，图形和文件均标明来源记录及坐标定义。具体本地算法边界见 [泵几何范围说明](PUMP_GEOMETRY_SCOPE.md)；CFturbo 也明确其主尺寸阶段的子午预览仅由当时的主尺寸生成。[官方主尺寸说明](https://manual.cfturbo.com/en/md_dimensions_pumpvent.html)

## 建立 CFturbo 泵项目需要哪些输入

**最小设计点是流量、能量目标与转速。** 在 `PROJECT → General → Global setup` 中，泵允许输入体积流量 `Q` 或质量流量 `ṁ`、扬程 `H` 或**总压差** `Δpt`、转速 `n`；另需选择工质。各叶轮可以单独分配能量目标和转速，多级的能量分配之和应与全局目标一致。[Global setup](https://manual.cfturbo.com/en/global_setup.html)、[多级能量分配](https://manual.cfturbo.com/en/md_multistage.html)

对于低温液氧和甲烷，交接还必须包含入口温度、压力口径与物性状态。官方液体物性字段包括密度 `ρ`、运动黏度 `ν`、导热系数 `λ`、定压比热 `cp` 和饱和蒸气压 `pv`；可用时可以采用 CoolProp。入口总压还用于组件接口信息和 CFD 导出。平台应交代温度为 K 或 °C、压力为绝压或表压、节点值为静态或总态，不能仅给出一个未解释的压力数字。[流体设置](https://manual.cfturbo.com/en/preffluids.html)、[入口状态与流体模型](https://manual.cfturbo.com/en/global_setup.html)

以下“已有”指平台保存的循环 / 几何结果中可提取的字段能力；历史记录缺失时仍应显示“未记录”，不能从当前草稿或默认值补入。

| 交接内容 | 平台数据基础与口径 | 进入详细建模前仍需明确的事项 |
| --- | --- | --- |
| `ṁ`、`Qin` | 保存流量与入口密度；`Qin = ṁ / ρin`，可并列 kg/s、m³/s、m³/h | 是整泵外部流量还是叶轮内部流量；泄漏 / 回流量如何处理 |
| 入口 / 出口压力、节点压升 | 循环保存节点值和 `Δp` | 总压 / 静压、端口速度与高度基准；确认能否用于 CFturbo 的 `Δpt` |
| 总参考扬程与代表级参考扬程 | `Href = Δp / (ρin g)`；当前代表级为等参考扬程分配 | 完整能量目标、可变密度口径、各级及诱导轮实际能量分配 |
| 转速、级数 | 保存的方案设定或求解记录 | 旋转方向、每个转子的转速、级数配置与机械约束 |
| 温度、入口 / 出口密度 | 泵组件保存的状态与物性结果 | 物性模型及版本、相态适用性、黏度、`cp`、导热系数、`pv` 的来源 |
| 泵轴功率、效率、扭矩 | 保存的 `power_W`、`eta`；扭矩可由有效轴功率和转速推导 | 系统泵效率不能直接当作 CFturbo 的水力、容积、机械各项效率 |
| `nq`、出口圆周速度、`D2` | 与当前参考扬程、级数、转速、压头系数一致的诊断 / 初估 | 适用泵型、出口叶片角、滑移、强度和效率共同校核 |
| `D1s`、`D1h`、`b2`、轴向长度及子午坐标 | 由保存设计点和速度系数、轮毂比、长度比构造 | 叶片堵塞、泄漏、局部速度分布和真实子午面优化 |
| 可用吸入压头、吸入比转速 | 部分记录已有按入口压力裕量计算的数值 | `NPSHA` 基准是否完整；`NPSHR`、所需裕度、诱导轮性能仍需独立确认 |
| 诱导轮 / 主叶轮 / 扩压器叶片数 | 当前总体循环不能唯一给出 | 需设计选择、滑移 / 堵塞计算、动静干涉与验证；缺失值显示“待设计” |
| 叶片角、包角、厚度、间隙、蜗壳 / 回流通道 | 当前代表级流道不含这些详细解 | 在 CFturbo 中完成并经 CFD / 结构分析校核，再回传设计记录 |

表中的 CFturbo 输入要求、效率分类和主尺寸依赖分别依据 [Global setup](https://manual.cfturbo.com/en/global_setup.html)、[参数与效率](https://manual.cfturbo.com/en/md_parameters_pumpvent.html)、[主尺寸](https://manual.cfturbo.com/en/md_dimensions_pumpvent.html)。本平台数据口径以 [泵几何范围说明](PUMP_GEOMETRY_SCOPE.md) 为准。

目前保存的 `npsh_available_m` 按入口节点压力裕量 `(pin − pv) / (ρin g)` 计算，未补端口速度头；由它反算的吸入比转速描述当前吸入条件下的要求，不能标成已验证的泵吸入能力。`pv`、黏度、`cp` 等在旧记录没有独立保存时应显示“未记录”；在后来版本用物性库重算，应注明重算模型与日期，不能称为原记录实存。参考液力功率是 `QΔp`；历史 `power_hydraulic_W` 字段实际包含 `/eta`，不得直接显示成该液力功率。CFturbo 区分可用吸入条件、所需 NPSH 估算与各项效率，平台也需保持这些区分。[NPSH 信息](https://manual.cfturbo.com/en/md_dimensions_pumpvent.html)、[效率定义](https://manual.cfturbo.com/en/md_parameters_pumpvent.html)

### 扬程、效率与系数不能只对齐名称

当前 `Href = Δp / (ρin g)` 是恒入口密度参考量，未包含端口速度头和位差修正。它不能直接标成“已经校正的总扬程”，也不能代替循环真实流体的焓升与轴功率。CFturbo 支持总压差输入；官方自 2023 R2 起列明液体可用 CoolProp 计算压力相关密度，因此交接时应核对两侧物性与能量定义。[设计点定义](https://manual.cfturbo.com/en/global_setup.html)、[可变液体密度版本说明](https://cfturbo.com/support/whats-new)

本平台采用 `psi = g Hstage / u2²`；现有几何交接同时给出 CFturbo 系数换算 `Psi = 2 × psi`。这个等式只在二者采用同一能量目标 `Y = g Hstage` 时成立，不能用于掩盖总压、静压、密度或效率定义不同。CFturbo 的水力、容积、叶尖间隙效率会影响尺寸设计，机械及侧面摩擦效率有不同用途；保存的系统效率 `eta` 不应被机械地复制到每个效率框。[CFturbo 参数与效率](https://manual.cfturbo.com/en/md_parameters_pumpvent.html)、[本平台系数约定](PUMP_GEOMETRY_SCOPE.md)

## CFturbo 如何由设计点生成几何

它采用透平机械方程、连续性和速度三角形，结合经验关联给出可修改的初始设计；不是只凭 `Q/H/n` 得到唯一、已经最优的泵。官方说明经验函数来自公开测量数据，并允许用户自定义。下面是离心 / 混流泵常用的平均线设计流程。[主尺寸计算依据](https://manual.cfturbo.com/en/md_dimensions_pumpvent.html)、[经验关联](https://manual.cfturbo.com/en/prefapprox.html)

1. **主尺寸。** 按设计点和选定参数估算入口眼径、出口直径、出口宽度等；欧拉方程将 `D2` 与出口叶片角联系起来。入口尺寸可以采用吸入性能相关参数，出口尺寸可以采用功系数或比直径等，用户仍需选择合理的参数及效率。[Main dimensions](https://manual.cfturbo.com/en/md_dimensions_pumpvent.html)、[设计参数](https://manual.cfturbo.com/en/md_parameters_pumpvent.html)

2. **子午面。** 可分别设计轮毂和轮盖，也可由中线及面积分布生成；曲线支持 Bézier、直线 / 圆弧、B 样条。官方 Bézier 初始设计使用主尺寸、端部倾角和轴向尺度，曲率及通流面积分布仍可人工调整。当前平台的椭圆过渡不能称为这套算法的复现。[Hub, Shroud](https://manual.cfturbo.com/en/hub_shroud.html)、[Bézier 初始构造](https://manual.cfturbo.com/en/mer_bezier_mode.html)

3. **前后缘与叶片角。** 入口速度三角形考虑入口条件、几何和堵塞；叶片角与流角之差形成入射角。出口按能量目标和欧拉方程处理，并用 Gülich/Wiesner、Aungier/Wiesner、Pfleiderer 等经验模型估计有限叶片滑移。流角不等于叶片金属角。[入口三角形](https://manual.cfturbo.com/en/bl_le.html)、[出口三角形与滑移](https://manual.cfturbo.com/en/bl_te.html)

4. **中弧线与三维叶型。** 可在 `t,m` 保角映射中用 Bézier 中弧线控制包角和端点角度，也可给定沿子午方向的 `β` 分布，积分得到包角；按多个展向截面生成三维中面。再施加厚度分布形成压力面和吸力面，设置前后缘及圆角等。[保角映射](https://manual.cfturbo.com/en/t-m-conformal-mapping.html)、[叶片角分布](https://manual.cfturbo.com/en/x-beta-blade-angle-progression.html)、[厚度与叶型](https://manual.cfturbo.com/en/prof.html)

5. **定子与蜗壳。** 定子可为有叶或无叶；有叶时还需叶片数、出流角、厚度等。蜗壳可以采用 Pfleiderer 角动量规律、Stepanoff 速度规律，或直接指定截面积发展规律，继续设计隔舌和出口段。[定子主尺寸](https://manual.cfturbo.com/en/maindimensions_stator.html)、[蜗壳计算规律](https://manual.cfturbo.com/en/voldesignrule.html)

这些步骤产生的是可评估和修改的参数化候选。详细流动、汽蚀、性能曲线和结构响应仍需要相应的仿真及实验验证；CFturbo 官方也把设计、流动仿真和有限元工具列为相互配合的流程。[CFturbo 设计能力](https://manual.cfturbo.com/en/cfturbo.html)、[官方版本中的 CFD / FEA 工作流](https://cfturbo.com/support/whats-new)

## 诱导轮、叶轮、扩压器与蜗壳如何配置

“诱导轮 → 主叶轮 → 扩压器 → 蜗壳”可以作为一种候选布置，但不是每台泵必须采用的固定序列。CFturbo 支持在主叶轮前设计诱导轮，支持有叶或无叶定子，也允许多个转子分配能量目标。页面适合显示各部件“已配置 / 候选 / 待决定”和设计状态，不应将未配置的部件画成已经求解的实体。[诱导轮](https://manual.cfturbo.com/en/inducer.html)、[有叶 / 无叶定子](https://manual.cfturbo.com/en/maindimensions_stator.html)、[多级配置](https://manual.cfturbo.com/en/md_multistage.html)

**是否需要诱导轮取决于吸入性能。** 官方描述诱导轮通常布置于离心叶轮前，以降低泵所需 NPSH；CFturbo 的诱导轮初始尺寸使用 Brumfield 曲线，通过吸入比转速估计子午流量系数，也允许直接指定入口流角或流量系数。平台仅有正的 `NPSHA` 不能证明“不需要诱导轮”，仅有较高吸入比转速也不能证明“已经设计好诱导轮”。还需 `NPSHR`、裕度、工况包线和相应验证。[Inducer](https://manual.cfturbo.com/en/inducer.html)、[NPSHR 估算信息](https://manual.cfturbo.com/en/md_dimensions_pumpvent.html)

多级方案还需要明确级间连接、回流通道和出口收集方式。当前“等扬程代表级”只是一种系统初估；应列出每级能量份额及是否计入诱导轮，不能在主叶轮已分配 100% 目标之后再额外加一段诱导轮压升。实际级数也不意味着可以直接把同一代表级几何沿轴复制成完成的总装。[多级能量分配](https://manual.cfturbo.com/en/md_multistage.html)、[组件接口与耦合](https://manual.cfturbo.com/en/interfaces.html)

## 叶片数应该怎样展示

CFturbo 手册列举常规泵 **3–7**、诱导轮 **1–3** 等常见叶片数，并给出 Pfleiderer 推荐数提示。这些是软件手册中的一般经验描述，**不是本液氧泵或甲烷泵的推荐值、求解结果或验收准则**，不应自动填入真实设计结果。叶片多寡会同时影响载荷、滑移、摩擦和堵塞，需结合具体设计权衡。[官方叶片数说明](https://manual.cfturbo.com/en/bladeprop.html)、[叶片设置与堵塞](https://manual.cfturbo.com/en/bl_setup.html)

扩压器若为无叶结构，叶片数可在已明确选择该结构后显示“无叶”；配置未决定时应显示“待设计”，不能用 0 代替缺失。有叶扩压器的叶片数需与叶轮叶片数协调；CFturbo 会检查低阶周期差对应的动静干涉，并同时考虑扩压器尺寸与通道间距。不存在适用于所有液氧 / 甲烷泵的固定扩压器叶片数。[定子配置](https://manual.cfturbo.com/en/maindimensions_stator.html)、[定子叶片数协调](https://manual.cfturbo.com/en/bladeprop_stator_blades.html)

因此页面应为诱导轮主叶片、主叶轮主叶片 / 分流叶片、扩压器叶片各设独立字段，并附来源。只有经过明确初选的值才能标“初选”，部件求解所得值标“计算”，通过外部详细设计回传的值还应附项目版本；未记录时保持“待设计”。上述状态规则是本平台的数据交接约定，不宣称是 CFturbo 自带的数据接口。[叶片设置](https://manual.cfturbo.com/en/bl_setup.html)、[定子设置](https://manual.cfturbo.com/en/maindimensions_stator.html)

## 实际建模交接步骤

1. **固定工况。** 分别选择液氧、甲烷泵，导出同一计算记录的设计点、单位、物性来源、级数 / 分配假设和几何。把 `Href` 与待确认的 `H/Δpt` 分开，确认压力口径后再录入 CFturbo 全局设计点。[Global setup](https://manual.cfturbo.com/en/global_setup.html)
2. **建立工质与部件。** 核对 Oxygen / Methane 工质身份、入口工况及物性模型；建立主叶轮和已决定的上 / 下游部件，多级时分配各转子能量目标。[流体设置](https://manual.cfturbo.com/en/preffluids.html)、[多级设置](https://manual.cfturbo.com/en/md_multistage.html)
3. **主尺寸 / 子午面两条路径。** 可以由 CFturbo 按设计点重新生成主尺寸并与平台初估对照；也可以通过 `Add component → Import geometry → 2D point data` 导入平台轮毂、轮盖各自的 TXT。官方格式每行一个 `z,r` 点，支持 `*.txt` / `*.cftcv`，只覆盖轮毂 / 轮盖轮廓。导入后复核单位、原点、旋转轴、`D2/D1s/D1h/b2` 及轴向长度。[主尺寸](https://manual.cfturbo.com/en/md_dimensions_pumpvent.html)、[二维点导入](https://manual.cfturbo.com/en/import_2dpointdata.html)
4. **完成叶片与配套部件。** 逐项设置前后缘、叶片数、厚度、入射 / 滑移、角度与包角，接着设计诱导轮、定子和蜗壳中已选择的部件；确保接口尺寸与流动条件相容。[叶片设置](https://manual.cfturbo.com/en/bl_setup.html)、[子午轮廓](https://manual.cfturbo.com/en/hub_shroud.html)、[蜗壳入口接口](https://manual.cfturbo.com/en/vol_inlet_interface.html)
5. **回传与验证。** 保留 CFturbo 项目版本、工况、最终主尺寸与叶片参数，结合 CFD / 结构结果确认性能与裕度后再更新数字样机中的部件设计状态。平台 CSV、DXF、OBJ 或 JSON 是坐标 / 参考资料；不能仅改扩展名就称为 CFturbo 原生完整参数化项目。尤其 TXT 子午轮廓导入不会自动交付已设计的叶片。[二维点导入范围](https://manual.cfturbo.com/en/import_2dpointdata.html)、[CFturbo 项目与工具定位](https://manual.cfturbo.com/en/cfturbo.html)

## 几何可视化的展示范围

现阶段可可靠显示代表级二维子午面、轮毂 / 轮盖三维旋转边界、主尺寸与各级配置示意。部件流程示意可以帮助解释诱导轮、叶轮、扩压段和蜗壳的关系，但应与有坐标来源的几何视图区分。只有叶片数、角度、包角、厚度和部件接口等已被明确设计后，才适合生成可用于后续计算的详细叶片实体；装饰性的螺旋叶片不应混入导出几何。[子午面设计](https://manual.cfturbo.com/en/hub_shroud.html)、[中弧线](https://manual.cfturbo.com/en/t-m-conformal-mapping.html)、[叶型厚度](https://manual.cfturbo.com/en/prof.html)
