# 通用 CFD 数据接口（V7.1.0）

本接口负责模型与外部 CFD 工具之间的 JSON / CSV 文件交接。它没有 Fluent、OpenFOAM 或其他软件的原生适配器，也不会生成可直接运行的求解器工程、三维网格、湍流或燃烧模型配置。外部工具或用户自己的适配程序负责这些步骤，并把输出整理成本协议。

结构应力分析需要独立的结构求解流程。本协议当前交换流动、换热及壁温数据，没有结构应力场的接口；外部协同路线暂停本地 FEM，不表示 CFD 自动替代结构求解。

## 使用顺序

1. 运行工作台设计点，检查数值状态及工程警告。初算数据是外部建模的参考，不是经过 CFD 验证的边界。
2. 明确分析域、实际入口、压力和温度的总量 / 静量口径，再审阅 CFD 设置。默认口径为 `unresolved`，默认 `setup_reviewed=false`。
3. 用 `create_cfd_job` 导出独立任务目录。目录含输入、初算结果、环境、参考几何和边界快照。
4. 在外部工具建立真实几何 / 网格 / 物理模型，执行计算，检查质量及能量守恒、网格敏感性和适当的收敛标准。
5. 适配程序读取 `manifest.json` 中的任务 ID 与指纹，把实际输出写为 `result.json`。也可以通过界面显式选择另一个结果文件导入。
6. 导入时检查协议、身份、单位、数值范围和型面数据，并生成原子保存的 `imported_result.json`。测试或未收敛结果可诊断查看，不能用于设计回写。

`converged=true` 仅代表外部求解器的声明。接口无法用一个布尔字段证明解的精度、守恒、网格独立性或整机工程可行性。

## Python API

```python
from cfd_exchange import (
    create_cfd_job, load_cfd_job,
    import_cfd_result, load_imported_result,
)

job = create_cfd_job(
    results,                 # core.CycleResults
    geometry,                # geometry.geometry_to_dict(...) 的字典
    parent_directory,
    task="cooling_flow",
    settings={
        "pressure_basis": "static",
        "temperature_basis": "total",
        "setup_reviewed": True,
        "turbine_side": "ox",
    },
    environment=runtime_report,
    source_inputs=submitted_input_snapshot,  # 可选，求解时提交的原工况
)
manifest = load_cfd_job(job)
result = import_cfd_result(job)             # 默认读取 job/result.json
result = import_cfd_result(job, explicit_result_path)
last_result = load_imported_result(job)     # 未导入时返回 None
```

`create_cfd_job` 返回新任务目录的 `pathlib.Path`；另外三个函数返回普通字典或 `None`。所有导入异常会抛出 `ValueError` 或文件系统异常，失败不会覆盖此前已导入的结果。

支持的任务类型：`chamber_flow`、`cooling_flow`、`turbine_flow`。`turbine_side` 仅取 `ox` 或 `fuel`；GG 单轴使用实际 `gg_turbine`，GG 双轴使用所选支路的专用流量与出口温度。

接口只把 `pressure_basis`、`temperature_basis`、`setup_reviewed`、`turbine_side` 写进任务设置。UI 中的执行路径、命令参数和超时属于本地运行配置，不是数据协议，也不会在本模块中执行。

可选关键字 `source_inputs` 接受提交时的 `EngineInputs` 或输入字典，并执行有限数值及 `EngineInputs` 校验。传入时，顶层 `inputs` 保存原始提交快照，`initial_results.effective_inputs` 保存求解器实际使用的参数；内部温度外环或反馈可能使两者不同。没有传入时，顶层 `inputs` 仍采用 `results.inputs`。边界、几何派生和域范围使用实际求解参数，不把提交参数假称实际状态。两份输入均随指纹绑定。

## 任务目录与 manifest

| 文件 | 内容 |
|---|---|
| `manifest.json` | 输入、参考结果、环境、任务身份、设置和数据范围 |
| `boundaries.csv` | 分析域入口及下游 / 内部参考状态，全部采用 SI |
| `geometry_contour.csv` | 有足够几何参数时为喷注面到喷管出口的全热气型面 |
| `nozzle_contour.csv` | 原几何字典中的喷管型面，喉部为轴向零点 |
| `result_template.json` | 带正确任务身份的空结果模板，直接导入会被拒绝 |
| `SI_UNITS.md` | 单位、口径和范围说明 |
| `result.json` | 外部适配程序生成的结果；工作台导出任务时不伪造此文件 |
| `imported_result.json` | 校验后的持久副本及回写资格说明 |

`manifest.json` 的固定字段：

- `format="lre.cfd.job"`、整数 `schema_version=1`。
- `job_id`：新的规范 UUID，每次创建独立任务。
- `input_fingerprint`：任务、输入、初算结果、状态、几何、环境、审阅设置、分析域和边界快照的规范 JSON SHA256。
- `task`、`domain`、`inputs`、`initial_results`、`states`、`environment`。
- `initial_results.effective_inputs` 保存实际求解参数；`initial_results.summary` 保留原模型 SI 汇总；`initial_results.components` 保留部件输出；`warnings` 和 `engineering_assessment` 保留初算工程依据。
- `settings.pressure_basis` 和 `settings.temperature_basis` 取 `unresolved` / `static` / `total`；`setup_reviewed` 必须为真正的布尔值。
- `geometry_reference.snapshot` 保存原几何；`contour` 保存实际导出的全长或局部型面及坐标系。
- `boundary_review.complete` 表示所需模型参考是否齐全；缺失项列在 `missing_references` 中。
- `files.result` 固定为 `result.json`；所有协议文件名固定，读取时拒绝通过 manifest 重定向到其他路径。

任务创建后修改上述快照、设置或域会使指纹校验失败。调整工况或 P/T 口径时应重新创建任务，不能只把旧结果的 ID 或指纹改成新值。

### 边界与流量

`pressure_absolute_Pa` 一律表示绝压，不能填表压。P/T 是否为总量或静量不能由模型站位的名字自动推断；在设置未明确前，CSV 也明确记录 `unresolved`。

`role=inlet` 行才记录该域实际参与计算的质量流量。`outlet_reference` 和 `domain_reference` 是下游 / 内部状态参考，其流量列留空，不能把它们累加成新的入口。例如 FFSC 主室两条涡轮排气入口流量相加后，主室内部总流量不再重复添加。

GG 的主室域采用 `mdot_chamber_kg_s` 与 `chamber_mixture_ratio` 拆分主室推进剂流量，排除燃气发生器旁路。冷却域采用真正的冷却通流，不使用包含 GG 分流的泵总流量。GG 双轴涡轮域采用所选涡轮部件的支路流量，不把发生器总流量作为每台涡轮的流量。

这些 CSV 行是模型参考，不是可直接套用的完整求解器边界配置。喷注器结构、速度方向、组分、壁面热条件和外部远场等仍需建模者设置。

### 冷却域必须为全发动机汇总

冷却任务的固定范围为：

```json
{
  "flow_scope": "full_engine",
  "heat_load_scope": "full_engine",
  "cooling_channel_count": 300,
  "no_implicit_multiplication": true
}
```

`cooling_channel_count` 取实际输入，并随任务指纹绑定。`mass_flow_kg_s` 表示全发动机冷却流量；`heat_load_W` 表示全发动机全部冷却通道的热负荷。

如果外部 CFD 使用单通道、周期扇区或其他局部域，外部适配程序必须根据真实域范围、周期关系和通道差异正确汇总。工作台不会把任意局部输出乘以通道数，也不会声称局部域与全发动机等价。

冷却结果必须显式回传与 `manifest.domain` **字段和值及类型完全相同**的 `domain`，才能获得回写资格。缺少域、单通道域、通道数不符或布尔值被写成 `1` 的结果仍可诊断导入，但不能回写。模板已附带正确域，适配程序只能在完成真实全域计算或汇总后保留该声明。

### 参考型面不是 CFD 网格

几何字典具有燃烧室 `cylindrical_section_length_m` / `radius_m`、喷管喉部 / 出口半径及面积比时，调用程序已有的 `geometry.nozzle.full_hot_gas_contour`，导出圆筒、收缩、喉部和钟形段，`coordinate_origin="injection_face_origin"`，喷注面轴向坐标为零。

缺少完整参数时，只导出原 `contour_x` / `contour_r`，`coordinate_origin="nozzle_throat_origin"`，明确 `includes_cylindrical_chamber=false`。此时负轴向坐标表示喉部上游收缩段，不能伪称已包含燃烧室圆筒。

型面只有轴向长度与半径，单位为米。它没有喷注器孔、叶片、冷却通道实体、体网格、边界面分组或旋转域。冷却和涡轮 CFD 仍需相应的真实三维结构。

## 结果协议

外部结果的最小结构示例：

```json
{
  "format": "lre.cfd.result",
  "schema_version": 1,
  "job_id": "从 manifest 原样复制",
  "input_fingerprint": "从 manifest 原样复制",
  "task": "cooling_flow",
  "source_kind": "cfd",
  "solver": {"name": "实际求解器名称", "version": "实际版本"},
  "converged": true,
  "domain": {
    "flow_scope": "full_engine",
    "heat_load_scope": "full_engine",
    "cooling_channel_count": 300,
    "no_implicit_multiplication": true
  },
  "metrics": {
    "pressure_drop_Pa": {"value": 200000, "unit": "Pa"},
    "heat_load_W": {"value": 20000000, "unit": "W"},
    "max_wall_temperature_K": {"value": 780, "unit": "K"}
  },
  "wall_profile": {
    "x_m": [0, 0.1, 0.3],
    "wall_temperature_K": [740, 760, 780]
  }
}
```

这是协议格式示例，其数值不是已计算的 CFD 结果。ID、指纹、任务、域和指标必须来自相应的真实任务及输出。

`source_kind` 只允许 `cfd` 或 `test`。测试文件应写 `test`，即使 `converged=true` 也不会允许回写。`solver.name/version` 不得为空或保留 `REPLACE_WITH_...` 占位；`converged` 只接受 JSON `true` / `false`，不接受 `0`、`1` 或字符串。

至少需要一个支持的指标，每项只允许 `value`、`unit` 两个字段。单位必须精确匹配，不能以 MPa、摄氏度、kW 等替代。

| 指标 | 单位 | 接口数值范围 |
|---|---|---|
| `pressure_drop_Pa` | `Pa` | 0 ～ 1e10，正值表示压降 |
| `outlet_temperature_K` | `K` | 大于 0，且不超过 20000 |
| `max_wall_temperature_K` | `K` | 大于 0，且不超过 20000 |
| `heat_load_W` | `W` | 0 ～ 1e13，正值表示进入壁面 / 冷却系统的热负荷 |
| `thrust_N` | `N` | 0 ～ 1e10，正向轴向推力 |
| `mass_flow_kg_s` | `kg/s` | 0 ～ 1e9 |
| `efficiency` | `1` | 0 ～ 1 |
| `shaft_power_W` | `W` | 0 ～ 1e13，正值表示涡轮对轴输出功率 |

这些是接口异常数据检查范围，不是推荐设计范围，也不证明结果物理合理。工作台的具体设计回写操作还需检查任务类型、当前输入一致性及相应参数约束。

可选 `wall_profile` 必须包含至少两个点的 `x_m` 和 `wall_temperature_K`；所有数组等长，`x_m` 严格递增，所有数值有限。额外支持 `coolant_temperature_K`、`pressure_Pa`、`heat_flux_W_m2`、`radius_m`。温度、半径必须为正，压力及热流密度不能为负；所有名称自带 SI 单位。型面的坐标系应与任务导出的型面一致，不能混用喷注面原点和喉部原点。

## 导入结果与安全边界

导入后增加：

- `feedback_eligible`：只有真实 CFD、报告收敛、人工审阅完成、P/T 口径明确、边界参考齐全，以及冷却域声明一致时才为 `true`。
- `diagnostic_only`：以上条件有任一不满足时为 `true`。
- `eligibility_reasons`：不允许回写的具体原因。
- `convergence_notice`：求解器声明的适用限制。
- `imported_at`：导入时间。

重新加载持久副本时，接口重新计算资格，不能通过手工把 `feedback_eligible` 改成 `true` 获得回写资格。接口本身不修改设计输入、不认证工程可行，也不验证外部求解器许可证或可信身份。

每份文件最多 20 MiB。JSON 拒绝 NaN、Infinity、超大指数形成的非有限值、重复字段、损坏结构和错误身份。写入采用同目录临时文件及原子替换；失败会保留旧持久副本。读取不会接受 manifest 中的任意文件路径；显式选择的结果文件可位于任务目录外，但仍执行同样的大小、身份和数值校验。

## 适配器开发与反馈

适配器应显式读取 `--manifest` 指向的当前任务，创建或更新实际 CFD 工程，执行求解并检查输出，再按上文格式写入 `--result`。已有结果不会被工作台运行器删除重跑。

界面采用独立参数数组运行所选程序，不解析任务文件中的命令。Python / PowerShell 等脚本应显式选择解释器，并把脚本路径作为参数。`adapter.log` 与 `run_status.json` 保存日志及终态，取消 / 超时只停止本任务启动的进程树。

冷却反馈只采用已校验的 `pressure_drop_Pa` 与 `heat_load_W`。采用压降时关闭 `cooling_dp_auto`；采用热负荷时关闭 `cooling_bartz_mode`，避免外部值被自动估算覆盖。更新模式和值均保留在方案的 `cfd_feedback_history`。温度、效率、推力和壁温曲线用于查看 / 对比，目前不会直接覆盖循环状态或结构场。

每次反馈后需重新运行循环、检查约束；下一轮 CFD 使用新任务及新指纹。`scripts/cfd_adapter_example.py --describe` 可检查示例接口；其默认返回错误，只有 `--self-test` 会生成明确标为测试的固定数据，不是真实求解器。

## 边界口径参考

不同求解器对边界变量有具体要求，适配器须负责映射和转换。参考官方文档：[Fluent 可压缩流入口 / 出口条件](https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/flu_ug/flu_ug_sec_compressible.html)、[OpenFOAM 派生边界条件](https://doc.cfd.direct/openfoam/user-guide-v13/derived-boundary-conditions)。本接口不从 0D 状态变量自动生成这些求解器设置。
