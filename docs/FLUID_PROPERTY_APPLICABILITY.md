# 纯推进剂物性适用性与 REFPROP 对照

核对日期：2026-10-23。本文仅覆盖纯氧、纯甲烷的流体物性；它不验证高压预燃室的反应混合气 EOS，也不证明 Raptor 3 实机性能。生产循环仍使用 CoolProp HEOS；此次没有更换物性后端或调整参数。

## 本机实际能力

本次运行使用 CoolProp **7.2.0**，经 CoolProp 的 REFPROP 后端成功加载本机 **REFPROP 10.0** 并实际计算氧气状态点。`ctREFPROP` 未安装，但已有可工作的 CoolProp 接口，因此无需再安装一个包装层才能执行对照。平台记录 CoolProp 版本与 git revision、REFPROP 版本、动态库及流体文件 SHA-256；不复制授权数据库或系数表。

`ctREFPROP` 是 NIST 官方提供的 Python ctypes 包装层，调用安装的 REFPROP 动态库。CoolProp 也能选择 `REFPROP` 后端，实际执行 REFPROP 的物性例程；包装层名称不决定精度。两种接口均不替代所需的 REFPROP 安装和许可。NIST 明确说明 REFPROP 的共享动态库不支持同一实例并发调用；现有对照路径增加进程内互斥锁，需要并行时应采用隔离进程。[NIST Python wrappers](https://github.com/usnistgov/REFPROP-wrappers/tree/master/wrappers/python)、[CoolProp REFPROP 接口](https://coolprop.org/coolprop/REFPROP.html)、[NIST REFPROP 产品页](https://www.nist.gov/srd/refprop)

## 具体模型与范围

下表读取本机库，不以在线文档当前版本替代安装版本证据。

| 后端 / 工质 | 最低温度 / K | 最高温度 / K | 最高压力 / MPa | HEOS EOS 引文标识 |
| --- | ---: | ---: | ---: | --- |
| HEOS / Methane | 90.6941 | 625 | 1000 | Setzmann-JPCRD-1991 |
| REFPROP / Methane | 90.6941 | 625 | 1000 | 接口未返回，不能补写成已核实相同 |
| HEOS / Oxygen | 54.361 | 2000 | 80 | Schmidt-FPE-1985, Stewart-JPCRD-1991 |
| REFPROP / Oxygen | 54.361 | 2000 | 82 | 接口未返回，不能补写成已核实相同 |

这是库公开的 EOS 范围，不能直接解释为整个矩形温压区间都有相同实验不确定度。CoolProp 的官方工质页面分别列出 EOS、黏度、导热率及熔化线来源；其中氧的低温参考论文覆盖三相点至 300 K、最高 80 MPa。讨论误差必须定位具体物性、相态和文献范围。[甲烷物性来源](https://coolprop.org/fluid_properties/fluids/Methane.html)、[氧物性来源](https://coolprop.org/fluid_properties/fluids/Oxygen.html)

已修正一个可复现的遗漏：**温压矩形范围检查不足以排除固相侧**。甲烷 100 K、70 MPa 落在 HEOS 的 Tmin/Tmax/pmax 范围内，但本机熔化线给出约 107.588 K。即使 T/P 闪蒸返回流体数值，也不能把这种亚稳流体延拓当成有效液态推进剂结果。现在物性校核会先检查各后端可用的熔化线，拒绝边界或固相侧；没有可用熔化关联式时明确标记 `unavailable`。这不是宣称所有固相转变均已完整建模。

正常状态继续报告密度、比热、声速、焓、熵、黏度和导热率。输运模型另记录 `transport_applicability.status = not_assessed`：EOS 范围通过不能自动验证黏度/导热率关联式。HEOS 自带流体 JSON 的指纹及独立 EOS/输运引文随记录保存。

## 实际 13 个供给状态的对照

源数据为上一轮已冻结的 `diagnostics/parameter_contract_20261023/temperature_mode.json`。取其中 13 个纯流体状态，包括贮箱、增压泵后、主泵入口/出口、再生冷却后及四条供给支路。本次不重新计算循环；源文件 SHA-256 与状态名称写入结果。

两端使用完全相同的工质、T、P。焓和熵比较采用各后端相对共同 `300 K、1000 Pa` 气态参考点的增量，避免参考零点不同产生假偏差。这个对照参考点只用于比较，**不改变**循环中 Cantera 化学焓与 CoolProp 差分焓对齐所使用的 `298.15 K、101325 Pa` 参考态。

13 点均能完成两端计算、EOS 范围与可用熔化线检查，且相态一致。最大相对差定义为 `|HEOS−REFPROP| / |REFPROP|`：

| 物性 | 最大相对差 | 所在状态 |
| --- | ---: | --- |
| 密度 | 1.29×10⁻¹⁵ | 氧泵入口 |
| 定压比热 | 4.77×10⁻¹⁰ | CH4 → 富氧预燃室 |
| 参考态焓增量 | 1.04×10⁻⁹ | CH4 → 富氧预燃室 |
| 声速 | 1.71×10⁻⁹ | CH4 → 富氧预燃室 |
| 黏度 | **4.2329%** | 甲烷再生冷却后，382.5994 K / 69.8885 MPa |
| 导热率 | **0.2213%** | CH4 → 富燃预燃室，384.2261 K / 59.3865 MPa |

以上是软件对照差异，**不是相对真实值的误差**。密度、比热及焓的高度一致，与两端可能采用相近/共享的热力学模型相容，但不能单凭一致就证明独立精度。黏度差异说明输运物性必须单独核查，下一步可在同一冷却模型中做关联式敏感性分析，不能直接把 4.23% 黏度差当成热流误差或推力误差。也不能据此断言任何库在所有物性和工况上更准确。

完整逐点数值、库指纹与边界反例保存在 `diagnostics/thermodynamic_applicability_20261023/fluid_backend_comparison.json`；复现命令：

```powershell
.venv-web\Scripts\python.exe -X utf8 diagnostics/thermodynamic_applicability_20261023/check_fluid_backends.py
```

## 已做修正与仍需验证的边界

`webapp/validation_checks.py` 继续作为物性校核入口，补充熔化线、EOS/输运证据区分、安装数据指纹和 REFPROP 原生调用串行化。校核的 `pass` 只表示已列出的范围和数值检查通过；`comparison.evidence_independence = not_established`，没有任意设定“软件差异小于某百分比即精度通过”。

没有把纯推进剂 REFPROP 替换成燃烧混合物模型。富氧/富燃预燃室有反应、组成变化和极端氧燃比，主燃烧室有高温解离；这些问题需要适当的化学平衡/热化学数据和高压混合物适用性证据。仅安装 REFPROP 或把流体名改成 `REFPROP::Methane` 不会解决燃气热力学问题。
