# 喷管总体设计的后续研究依据与适用边界

核验日期：2026-11-07。本文补充早期 NASA 喷管设计准则，服务于液氧甲烷全流量补燃发动机的总体设计、喷管分析和后续 CFD／结构分析交接。下列资料不是同一套规范的连续版本，也不能称为 NASA SP-8120 的“新版”。经典准则适合建立设计检查框架，后续研究则补充轮廓优化、非设计工况分离、瞬态侧载以及冷却制造约束。

总体设计应给出可追溯的工作点、性能分解、几何约束和工况范围；喷管内型面图只承担几何检查与坐标交接。将“喷管设计与分析”独立为页面，可以让性能、适用条件和后续验证需求同时可见，并与现有内型面页面引用同一计算记录。

## 精选资料

### 1 NASA CEA 理论与用户手册 1994 和 1996

**Sanford Gordon、Bonnie J. McBride**，*Computer Program for Calculation of Complex Chemical Equilibrium Compositions and Applications. I. Analysis*，NASA RP-1311，1994；第二部分 *II. Users Manual and Program Description*，**Bonnie J. McBride、Sanford Gordon**，1996。

来源：[第一部分 NTRS](https://ntrs.nasa.gov/citations/19950013764)、[第二部分 NTRS](https://ntrs.nasa.gov/citations/19960044559)、[NASA CEA 官方入口](https://www.nasa.gov/glenn/research/chemical-equilibrium-with-applications/)。

重点：第一部分第 6 章，印刷页 25–33，尤其 §6.1、§6.2 和 §6.5；对应 PDF 第 31–39 页。它给出一维火箭性能、有限／无限面积燃烧室和化学平衡／冻结模型的定义。用于建立热化学参考和检查数据口径，不能据此预测二维轮廓损失、边界层分离或侧载。

采用时尤其注意：§6.2.8 的 `CF=u/c*` 与任意环境下包含压力推力的总推力系数口径不同；不能仅凭字段名直接互换。比冲量的速度单位与通常使用的比冲秒数也要分清。

### 2 NASA 喷管侧载试验 2009

**Joseph H. Ruf、David M. McDaniels、Andrew M. Brown**，*Nozzle Side Load Testing and Analysis at Marshall Space Flight Center*，第 45 届 AIAA/ASME/SAE/ASEE 联合推进会议，2009。

来源：[NTRS 记录和论文](https://ntrs.nasa.gov/citations/20100017649)。NTRS 编号含 2010，但记录的出版日期是 **2009-08-02**。

重点：PDF 第 2–3 页介绍侧载、FSS／RSS 及其结构影响；后文比较截短理想型面和抛物线型面的冷流试验。该研究直接说明：起动、关机期间的非对称载荷影响喷管壳体和推力矢量执行机构，稳态轴向推力不能替代这些载荷。

总体设计可记录轮廓类型、喷管压比范围和待验证工况；具体侧向力及喉部弯矩需要相应的非定常流场、试验和结构模型，不从一条经验分离阈值推算为已验证结果。

### 3 DLR 分离机理与经验准则 2010

**Ralf Stark**，*Beitrag zum Verständnis der Strömungsablösung in Raketendüsen*，RWTH Aachen 博士论文，2010，DLR 开放存档。

来源：[DLR 记录](https://elib.dlr.de/69234/)、[全文](https://elib.dlr.de/69234/1/DissertationRalfStark.pdf)。

重点：第 4 章为冷、热流试验；第 5.1 节讨论分离起点、准则条件及文献数据对照，印刷页 110–124；第 5.1.1 节明确区分边界层状态。研究还包含冷却膜与分离行为的关联。

可借鉴的是“准则必须附带适用范围”的做法：局部壁面压力和局部马赫数不能无条件用一维出口平均值替代，冷流与热流、层流与湍流也不能混为一个阈值。平台适合给出风险筛查和来源，不应把低于某个 `pe/pa` 数值直接转成普适分离位置或安全结论。

### 4 DLR 甲烷可复用一级喷管轮廓研究 2024

**Sebastian Karl、Tamas Bykerk、Mariasole Laureti**，*Design of a truncated ideal nozzle for a re-usable first stage launcher*，HiSST，2024-04-14 至 2024-04-19。

来源：[DLR 记录](https://elib.dlr.de/197454/)、[正式 PDF](https://elib.dlr.de/197454/1/HiSST%202024%20-%2034.pdf)。存档元数据把标题写成 *Design of an ideal truncated nozzle...*，并列出不同作者顺序／页数；这里采用 PDF 首页的标题与作者顺序，页码也按实际 11 页 PDF。

重点：PDF 第 5–8 页给出从初始流场、特征线法到黏性／热化学 CFD 修正的流程；第 8–9 页展示进一步优化和性能比较。其甲烷研究对象与本平台较贴近，但论文工作点不是本发动机设计输入。

最有价值的结论是：常比热比、无黏 MOC 初型仍需要修正；长度、面积比、出口流动均匀性与性能应一起比较。本文支持检查喉后连接与曲率连续性，不支持把指定端角二次曲线直接标为已经求解的推力最优轮廓。

### 5 NASA 槽道壁喷管制造与热试验 2020

**Paul R. Gradl、Christopher S. Protz**，*Technology advancements for channel wall nozzle manufacturing in liquid rocket engines*，*Acta Astronautica* 174，148–158，2020，DOI：[10.1016/j.actaastro.2020.04.067](https://doi.org/10.1016/j.actaastro.2020.04.067)。

来源：[NASA NTRS 记录和全文](https://ntrs.nasa.gov/citations/20205002297)。

重点：印刷页 149 的设计约束、页 154–157 的试验和结论，说明热壁厚度、冷却通道压降、接头、材料和制造过程需要联合考虑。论文通过材料／工艺表征及热试验评价具体方案，而不是由内型面确认可制造性。

总体设计应提前交接冷却范围、热流和冷却剂压降预算、材料／制造路线与寿命目标。该文不能证明本项目的甲烷工况、尺寸及重复使用寿命已满足要求；材料牌号与工艺名称也不能代替本项目的许用值和鉴定证据。

### 6 NASA 大尺寸增材制造推力室研究 2024

**Darren C. Tinker、Tessa M. Fedotowsky、Rachel A. Bardsley、Paul R. Gradl**，*Development of Large-Scale Thrust Chambers using Laser Powder Directed Energy Deposition*，AIAA SciTech，2024-01-08 至 2024-01-12。

来源：[NASA NTRS 记录和全文](https://ntrs.nasa.gov/citations/20230018096)。NTRS 编号含 2023，会议论文年份为 **2024**。

重点：PDF 第 1–2 页说明尺度与特征分辨率的取舍，第 13–15 页记录双金属界面、试压问题及制造结论。对于希望向工程实体推进的平台，最值得吸收的是把沉积方向、厚薄壁过渡、接头与可检查性提前纳入几何约束。

这是一份工艺研发证据，不能把制造演示或某个样件的试验等同于任意新喷管的服役鉴定。内壁旋转曲面也不等于包含冷却槽、承力外壳和连接结构的制造模型。

### 7 CNES CERFACS 与 UT Austin 喷管出口端效应研究 2025

**F. Burdairon、G. Daviller、C. E. Tinney、S. Blanchard**，*Large-Eddy Simulation of a Rocket Nozzle Undergoing End Effects*，第 11 届 EUCASS，2025，DOI：10.13009/EUCASS2025-028。

来源：[会议官方论文 PDF](https://www.eucass.eu/component/docindexer/?id=7721&task=download)。

重点：PDF 第 1–2 页描述 FSS／RSS 转换和出口端效应，后文将 LES 与模型试验比较。论文显示侧载来源不止首次流动分离，还包括再附及末端分离泡与环境间的非定常交换；起动与关机的转换也具有滞回。

平台的稳态环境曲线应明确其附着流假设，不能用它宣称已验证起停过程。重复使用发动机的后续验证任务应包含起停压比历程和瞬态载荷，而不只校核额定点。

## 对本平台的设计约束

以下是结合上述研究形成的软件与模型要求，属于本项目的实现建议。

| 分析层级 | 应给出的信息 | 需要保留的限制 |
| --- | --- | --- |
| 当前循环工作点 | 记录 ID、计算模式、压力口径、主室入口状态、混合比、流量、喉面积、出口面积、环境压力 | 使用同一记录；历史缺失字段标为缺失，不能用当前草稿补齐 |
| 性能分解 | 特征速度、动量推力、压力推力、总推力、对应推力系数、有效排气速度与比冲 | 明确理想基准、已使用的修正和单位；区分推力室／主喷管与整机推力 |
| 几何检查 | 面积比、喉径和出口直径、膨胀段长度、钟形长度比、端角、喉部曲率、连接连续性 | 指定端角抛物线属于构型；未做流场优化时不称 MOC、TOC 或已优化 TOP |
| 环境分析 | 固定工作点和硬件的推力、比冲、压力推力随环境压力变化 | 属于喷管环境敏感性；不是重新匹配泵、涡轮和阀门的发动机节流解 |
| 非设计工况 | 膨胀状态、压比、分离风险提示、风险模型及适用域 | 一维平均出口量不能确认局部分离；风险区域应提示附着流曲线可能失效 |
| 热与结构交接 | 已计算的冷却覆盖、热流／温度、冷却压降；材料工艺、寿命和载荷待办 | 未建模的壁厚、侧载、疲劳、屈曲或接头不能显示为已校核 |

### 性能字段的统一定义

在使用相同压力基准、喉面积和质量流量的条件下：

```text
c*               = pc At / mdot
CF               = F / (pc At)
Isp              = F / (mdot g0) = c* CF / g0
```

其中 `c*` 主要表征燃烧与喉部质量流量能力，是推力室热化学和流量的关联量；它不是单纯由喷管出口型面决定的指标。压力基准必须与已有循环求解一致，有限面积燃烧室的静压／总压／等效压力不可隐式替换。

对采用均匀出口近似的单股流：

```text
Fmomentum        = mdot ve,axial
Fpressure        = (pe - pa) Ae
F                = Fmomentum + Fpressure
CFpressure       = (pe - pa) Ae / (pc At)
```

真实非均匀出口需要对出口面的动量和压力积分。若 `ve,axial` 或保存的推力已经包含效率／方向性修正，展示层不能再次乘同一损失系数。边界层、化学非平衡、发散与热损失应按明确的参考状态逐级处理；没有分项证据时，展示现有总修正及其来源，不编造损失饼图。

### 固定硬件环境曲线与发动机节流的区分

在内流状态不变、仍附着且出口状态可沿用的假设下，可以用 `F(pa)=Fvac-pa Ae` 计算环境压力修正。图表必须标明其假设，并在显著过膨胀／潜在分离区域提醒有效性限制。不能借此推出低推力工况下的泵、涡轮转速或预燃室温度。

真正的发动机节流需要固定硬件，在供给、阀门、泵／涡轮性能及轴功率约束下重新求取运行点。喷管分析可以引用这些已求得的工况进行比较，但不能通过改变喉面积或重新定型来“补足”目标推力。

### 后续验证任务

1. 采用一致的几何、气体模型和边界条件比较概念轮廓；先检查长度、端角和曲率，再引入流场优化。
2. 对需要计入的性能损失建立独立校核依据；平衡、冻结和有限速率化学结果的差异只在其模型假设下解释。
3. 对低压比、起停及复用任务分析分离与非定常载荷；将对应载荷、热边界和循环次数交接结构分析。
4. 将槽道、热壁、外壳、接口和制造要求补入工程模型后，再开展压力、热应力、疲劳与试验验证。

已有内型面坐标的数据来源与导出规则见 [推力室内型面文档](NOZZLE_GEOMETRY.md)。
