"""
core.py

系统级数据结构定义（System Data Layer）

V7 修改记录（对标 Raptor 3）：
─────────────────────────────────────────────────────────
1. 默认参数全部对标 SpaceX Raptor 3（公开资料 + 工程估算）：
   · target_isp 327 → 363 s（海平面 ~330，真空 ~380，二者加权）
   · inlet_pressure 0.2 → 0.6 MPa（含 boost pump 增压后）
   · cooling_heat_load 25 → 90 MW（更接近实际再生冷却热载荷）
   · fuel_preburner_temp_limit 1100 → 950 K（Raptor 富燃涡轮入口实测 ~870 K）
2. 新增压力网络参数（V7）：
   · injector_dp_ratio_ox / fuel：喷注器压降比例（默认 0.20 / 0.18）
   · cooling_dp_pa：冷却通道压降（默认 8 MPa）
   · valve_line_dp_ratio：阀门+管路压降（默认 0.03）
3. 新增材料/几何参数（V7）：
   · chamber_wall_material：燃烧室内壁材料（默认 GRCop-84）
   · chamber_wall_allow_pa：许用应力（GRCop ~120 MPa @ 800 K）
   · 这样几何模块能算出 mm 级的真实壁厚，而不是 V6 的 48 mm

V6 历史记录：
─────────────────────────────────────────────────────────
1. EngineInputs 新增预燃室温度上限参数。
2. EngineInputs 新增节流范围参数。
3. 新增目标预燃室混合比模式。
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Dict, List, Tuple


# =========================================
# 基本常数（统一管理）
# =========================================

G0 = 9.80665
"""标准重力加速度 [m/s²]"""

R_UNIVERSAL = 8314.462618
"""通用气体常数 [J/(kmol·K)]"""


# =========================================
# 校验工具
# =========================================

class ValidationError(Exception):
    """参数校验失败时抛出的异常。"""
    pass


def _check_range(
    name: str,
    value: float,
    lo: float | None = None,
    hi: float | None = None,
    lo_inclusive: bool = True,
    hi_inclusive: bool = True,
) -> None:
    """通用范围校验辅助函数（硬校验）。"""
    if lo is not None:
        if lo_inclusive and value < lo:
            raise ValidationError(
                f"{name} = {value}，低于允许下限 {lo}（含）"
            )
        if not lo_inclusive and value <= lo:
            raise ValidationError(
                f"{name} = {value}，必须严格大于 {lo}"
            )
    if hi is not None:
        if hi_inclusive and value > hi:
            raise ValidationError(
                f"{name} = {value}，超过允许上限 {hi}（含）"
            )
        if not hi_inclusive and value >= hi:
            raise ValidationError(
                f"{name} = {value}，必须严格小于 {hi}"
            )


def _warn_range(
    name: str,
    value: float,
    lo: float,
    hi: float,
) -> None:
    """软校验：值在物理可行但工程上不典型时发出警告。"""
    if value < lo or value > hi:
        warnings.warn(
            f"⚠ {name} = {value} 超出典型工程范围 [{lo}, {hi}]，"
            f"计算可以继续但请确认输入是否合理。",
            stacklevel=3,
        )


# =========================================
# 1️⃣ 设计输入层（Design Layer）
# =========================================

@dataclass
class EngineInputs:
    """
    发动机设计输入参数（Design Point Definition）

    ⚠️ V6 重要说明：
    - fuel_split_to_ox_preburner 和 ox_split_to_ox_preburner
      仅作为求解器的初始猜测值，实际分流比由功率平衡决定
    - 预燃室温度上限是硬约束，用于验证求解结果的物理合理性
    """

    # ---------- 基本性能（对标 Raptor 3）----------
    thrust: float = 2.45e6            # 推力 [N]  (Raptor 3 海平面 2.45 MN = 250 tf；真空 2.70 MN = 275 tf)
    pc: float = 33.0e6                # 燃烧室压力 [Pa] (Raptor 3 ~33 MPa)
    of_ratio: float = 3.6             # 氧燃比 (Raptor 优化点 3.55–3.65)
    expansion_ratio: float = 34.34    # 喷管膨胀比 (Raptor 3 海平面型号 34.34；真空 RVac=80 是另一台发动机)
    # 主燃烧室燃烧效率 η_comb：实际达到的"燃烧温升 / 理想温升"比例(能量释放效率)。
    #   1.0 = 理想完全燃烧(化学平衡上限,即旧行为)；
    #   气-气 FFSC 主燃室掺混/反应充分,典型 0.98–0.995,默认 0.99。
    # 作用：把理想平衡 Tc 折减为真实主流温度 →  c*/Isp 与喉部热流随之一致降低
    #       (单点注入、不双重计数；喷管由 chamber_state 重建,故性能自动跟随)。
    combustion_efficiency: float = 0.99

    # ---------- 入口条件 ----------
    fuel_inlet_temp: float = 100.0    # 液态甲烷温度 [K]（过冷LCH4 @ 100 K，NIST/Piunti 2022）
    ox_inlet_temp: float = 70.0       # 液氧温度 [K]（过冷LOX @ 70 K，NIST/Piunti 2022）
    # ════════ 增压输送系统（贮箱 → 增压泵 → 主泵）════════════════════
    # 真实 FFSC 供给链，三个压力节点分开建模，避免把「贮箱压力」与
    # 「主泵入口压力」混为一谈：
    #
    #   贮箱(tank_pressure_pa)  → 增压泵(boost)  → 短管(feed_line_dp_pa) → 主泵入口
    #        ~3 bar               抬到 ~10 bar        −0.5 bar              9.5 bar
    #   (自生增压,为省壁重很低)  (LPP,给主泵诱导轮供 NPSH)
    #
    # 工程依据：SSME/RD-170/国内补燃发动机均有独立低压增压泵(前置泵)；其作用
    # 是把低贮箱压力抬到主泵入口所需值，抑制主泵汽蚀。增压泵扬程低、功率小
    # （主泵的~1–3%，常由主泵排出口抽流的小液力涡轮驱动，闭环回收），系统级
    # 功率平衡里不计入主轴残差，仅作信息量显示。

    # ① 贮箱(自生增压)压力——真实值很低，不是 10 bar！
    tank_pressure_pa: float = 3.0e5   # 贮箱自生增压压力 [Pa]（3 bar；methalox 典型 2–4 bar）

    # ② 增压泵出口压力 = 主泵供给压力（沿用历史键名 inlet_pressure）
    #    取 10 bar：扣 0.5 bar 短管 → 主泵入口 9.5 bar，使液氧诱导轮 nss(EU)≈750
    #    （优良诱导轮可达）、甲烷≈440（宽裕）。SSME 主氧泵入口约 29 bar，此处更低
    #    （致密化深过冷降低 NPSH 需求）。
    inlet_pressure: float = 1.0e6     # 增压泵(boost)出口压力 = 主泵供给压力 [Pa]（10 bar）

    # ③ 增压泵效率（低压前置泵，效率低于主泵）
    boost_pump_efficiency: float = 0.70

    # ---------- 增压泵→主泵 短管压降 ----------
    # 增压泵紧贴主泵，中间只有短导管 + 弯头，损失 ~0.3–0.5 bar。
    feed_line_dp_pa: float = 0.5e5    # 增压泵→主泵短管压降 [Pa]（0.5 bar）

    # ---------- 效率参数 ----------
    pump_efficiency_ox:   float = 0.86   # V8: 0.84→0.86（Raptor 实测高效）
    pump_efficiency_fuel: float = 0.85   # V8: 0.81→0.85
    turbine_efficiency_ox: float = 0.85   # V8: 0.80→0.85
    turbine_efficiency_fuel: float = 0.88   # V8: 0.83→0.88

    # ---------- 冷却系统 ----------
    # V8：默认启用 Bartz 热流估算模式（cooling_bartz_mode=True）
    # Bartz 模式：从发动机参数（mdot、pc、Tc、γ、Mm）自动推算喉部面积和热载荷
    # 固定模式（cooling_bartz_mode=False）：直接用 cooling_heat_load（V7 兼容）
    cooling_bartz_mode: float = 1.0      # 1=Bartz估算（默认），0=固定热载荷（V7兼容）
    cooling_heat_load: float = 90.0e6   # 再生冷却吸热量 [W]（固定模式用；Bartz模式忽略）
    coolant_cp: float = 2.4e3           # 高压超临界 CH4 比热 [J/(kg·K)]（固定模式用）

    # ---------- 再生冷却通道几何（V14：正式纳入，之前用 getattr 读但未定义 → 锁死）----------
    # 修 Major 1（冷却几何 vs 固定压降自相矛盾）：通道几何现可配置，并支持
    # 「按目标流速自动定通道深度」+「压降由水力学(Darcy)实算」，避免 354 m/s 的
    # 非物理流速与固定 5 MPa 压降并存。
    cooling_n_channels: float = 300.0        # 冷却通道数（周向，受室周长约束；典型 150–400）
    cooling_channel_width_m: float = 1.5e-3  # 通道周向宽 [m]
    cooling_channel_height_m: float = 5.0e-3 # 通道径向深 [m]（auto_size=1 时被反算覆盖）
    cooling_wall_thickness_m: float = 0.5e-3 # 内衬热壁厚 [m]（GRCop ~0.5–1 mm）
    cooling_wall_conductivity: float = 320.0 # 内衬导热 [W/(m·K)]（GRCop-84 @600K）
    cooling_htc_enhancement: float = 1.0     # 喉部弯曲/入口段换热强化因子（1.0–1.5）
    # ★ 自动定尺寸：固定通道数+宽，反算通道「深度」命中目标流速（真实再生通道做法）
    cooling_channel_auto_size: float = 1.0   # 1=按目标流速反算通道深度（默认），0=用上面固定几何
    cooling_target_velocity_ms: float = 100.0  # 目标冷却剂流速 [m/s]（再生通道典型 40–150）
    # ★ 压降来源：1=水力学(Darcy+局部损失)实算并反馈进泵压预算；0=用固定 cooling_dp_pa
    cooling_dp_auto: float = 0.0             # 默认 0（保持泵压预算稳定；置 1 启用水力压降耦合）
    cooling_channel_rel_roughness: float = 0.003  # 通道相对粗糙度 ε/D_h（AM 打印态 ~3e-3）
    cooling_channel_k_minor: float = 3.0     # 进出口+集流环+弯头局部损失（动压头数）

    # ---------- 沿程逐站 marching（V15：收敛设计点后处理，出 T_wall(x)/p_co(x) 剖面）----------
    cooling_marching_enable: float = 1.0     # 1=求解后自动跑 marching 剖面（默认），0=关闭
    cooling_cover_eps: float = 0.0           # 再生冷却覆盖到的面积比（0=全喷管 ε；如填 8 表示只冷到 ε=8）
    cooling_wall_temp_limit_K: float = 800.0 # 热面壁温限值（GRCop 系 ~800 K），marching 超限即告警
    # 燃气辐射发射率 ε_g（灰气体一阶代理，marching 辐射热流用；V19 起为正式输入字段，
    # 之前 marching 用 getattr 读取但字段不存在 → 永远只能用 0.20 默认值）
    cooling_gas_emissivity: float = 0.20
    # ---------- 冷却通道沿程分布表（V19：推力室设计工作区）----------
    # JSON 断点表，定义 N/宽/深/衬厚 沿轴向的分布（分段线性，N 在断点处分叉/合并）。
    # "" = 不启用（沿用上面的单段标量，行为与 V15 完全一致）。
    # 格式见 chamber_design.py：{"version":1,"rows":[{"anchor":"throat","n":300,
    #   "w_mm":1.5,"h_mm":5.0,"t_mm":0.5}, ...]}，anchor ∈ injector/conv_start/throat/exit/x_mm
    cooling_channel_table_json: str = ""

    # ---------- 膜冷却（V15：一阶效能模型；FFSC 气-气膜，气源=富燃涡轮排气）----------
    # T_aw_eff = T_aw − η(x)·(T_aw − T_film)，η(x)=η₀·exp(−x/L_decay)。
    # 默认关闭（fraction=0）；开启后仅作用于热载荷，不反馈主流性能（<3% 时 Isp 影响 <1 s）。
    film_cooling_fraction: float = 0.0       # 膜冷却流量占燃料流量比例（0=关；典型 0.01–0.05）
    film_effectiveness_initial: float = 0.9  # 喷注面处初始效能 η₀
    film_decay_length_m: float = 0.0         # 效能衰减特征长 [m]（0=自动取 L_cyl+L_conv）

    # ---------- 燃烧室估计 ----------
    chamber_temp_guess: float = 3550.0  # Raptor 主燃烧室实测约 3500–3600 K
    chamber_gamma: float = 1.18         # 高压富燃产物 γ 偏低
    molar_mass: float = 21.0            # 高压平衡平均 MW

    # ---------- 系统目标 ----------
    # V7：Raptor 3 公开真空 Isp ~380 s，海平面 ~330 s。
    # 这里给海平面参考（与 thrust 海平面值一致），用户可以改。
    target_isp: float = 327.0   # 目标比冲 [s] (Raptor 3 海平面 327 s；仅作 mdot 初猜，真实 Isp 由热化学算出)

    # ---------- 预燃室参数 ----------
    ox_preburner_pressure_ratio: float = 1.15
    fuel_preburner_pressure_ratio: float = 1.18
    # 涡轮排气进入主燃烧室须经过主喷注器，出口压力 = Pc*(1 + dp_ratio)。
    # 两侧压降不同，原因：
    #   1. 富氧排气（主要O2）密度高、流速快 → 较大压降（Raptor 约 24% Pc）
    #   2. 富燃排气（主要CH4）密度低、流速慢 → 较小压降（Raptor 约 9% Pc）
    # 参考：Piunti 2022 Table 14/15（Pc=300 bar → LOX 373 bar，CH4 328 bar）
    ox_turbine_exhaust_dp_ratio: float = 0.243    # LOX侧主喷注器压降/Pc ≈ 24.3%
    fuel_turbine_exhaust_dp_ratio: float = 0.093  # CH4侧主喷注器压降/Pc ≈  9.3%

    # ---------- 预燃室温度上限（V6, V7 修正）----------
    #
    # 全流量分级燃烧循环中，预燃室出口温度直接决定涡轮入口温度。
    # 涡轮叶片材料的耐温性是硬约束：
    #   - 富氧侧：高温富氧环境极其苛刻，特殊合金 (Monel/铜合金) 耐温约 750–800 K
    #     Raptor 富氧涡轮入口实测约 ~730 K
    #   - 富燃侧：富甲烷环境相对温和，镍基高温合金可达 ~900–1000 K
    #     Raptor 富燃涡轮入口实测约 ~870 K → V7 下调至 950 K
    #
    ox_preburner_temp_limit: float = 800.0    # [K] 富氧侧涡轮入口温度上限
    # V5.3.6：980→900K（O/F_fuel 由 0.16 降到 0.13 后实际工况 ~870K，留 30K 余量；
    #          Raptor 富燃涡轮镍基合金真实耐温上限约 870–950K）
    fuel_preburner_temp_limit: float = 900.0  # [K] 富燃侧涡轮入口温度上限

    # ---------- 压力网络（V7 新增，FFSC 必需）----------
    # ⚠ FFSC 模式已废弃（V13）：主燃烧室喷注器压降统一由 ox/fuel_turbine_exhaust_dp_ratio
    #   表示（= 涡轮排气压力 − Pc，与压力网络同口径，Piunti 富氧24.3%/富燃9.3%）。
    # ★ V20 复活：GG（燃气发生器）模式下主燃烧室是 **液-液喷注**，这两个参数
    #   即为液路主喷注器压降比 ΔP/Pc（典型 0.15–0.25），由 gg_cycle.py 使用。
    injector_dp_ratio_ox:   float = 0.20   # FFSC 不用；GG=主燃室液氧喷注压降比
    injector_dp_ratio_fuel: float = 0.20   # FFSC 不用；GG=主燃室甲烷喷注压降比
    # 预燃室喷注器压降 / 预燃室压力（两个预燃室各自一套喷注器）。
    #   参考 Schoenman 1981(NASA TR)：富氧/富燃高压预燃室喷注器 ΔP/Pc_pb=5.7–20%，典型 ~14%。
    #   这是主泵出口压力预算里的关键一项：p_pump_out = p_pre + ΔP_inj_pb + ΔP_valve(+冷却)。
    #   引用「预燃室压力」而非 Pc，物理更对，且与 preburner.py 卡片显示同口径。
    #   取 0.12（现代高压发动机偏精简端，兼顾燃烧稳定裕度）→ 液氧泵出口约 2.17×Pc≈717 bar，
    #   与 Raptor 主氧泵出口 ~2.0–2.2×Pc 量级吻合。
    preburner_injector_dp_ratio: float = 0.12   # 预燃室喷注器 ΔP / 预燃室压力
    # 再生冷却通道压降：高压 CH4 经过细密通道阻力大，典型 5–12 MPa
    # V5.3.6：3→5 MPa（更贴近 Raptor 真实 HARCC，迫使 CH4 泵出口抬到 ~78 MPa，
    #         为 CH4 涡轮提供更大压比，富燃 PB 温度可自然降到 ~870 K）
    cooling_dp_pa: float = 5.0e6
    # 阀门 + 管路 + 喷注器面板冗余压降比 pc
    valve_line_dp_ratio: float = 0.03

    # ---------- 材料 / 几何（V7 新增）----------
    # 燃烧室内壁是 GRCop-84 / GRCop-42 (Cu-Cr-Nb 合金)
    # σ_allow ≈ 100–140 MPa @ 800 K
    chamber_wall_allow_pa: float = 120.0e6
    chamber_wall_safety_factor: float = 1.5
    # 主燃烧室几何初估：圆柱燃烧段 + 收缩段 + 喉部圆弧过渡
    # L* = Vcc / At；LOX/LCH4 初步设计常取约 0.8-1.2 m。
    chamber_contraction_ratio: float = 2.5
    chamber_l_star_m: float = 1.0
    chamber_conv_half_angle_deg: float = 30.0   # 收缩段半锥角 β [°]（典型 25–45°）
    # 钟形喷管长度比（0.80 = 80% bell，TOP 的核心定义参数；V19 起为正式输入，
    # 全链同口径：geometry 轮廓 / cooling_marching / 2D图 / CSV 导出）
    nozzle_bell_fraction: float = 0.80
    # 主壳体（外结构）用 Inconel 718, σ_allow ≈ 600 MPa @ 800 K

    # ---------- 预燃室混合比目标（V3 新增）----------
    # 推荐先使用目标混合比模式：
    #   富氧预燃室 O/F ≈ 40–80，对应 phi ≈ 0.05–0.10
    #   富燃预燃室 O/F ≈ 0.05–0.30，对应 phi ≈ 13–80
    # use_target_preburner_mr = 1：由目标 O/F 反求分流量，求解器调节两侧预燃室压力比。
    # use_target_preburner_mr = 0：保留旧的自由分流功率闭合模式。
    use_target_preburner_mr: float = 1.0
    ox_preburner_mr_target: float = 60.0
    # V5.3.6：0.16 → 0.13。配合 cooling_dp 抬到 5 MPa，CH4 涡轮可用焓降更大，
    #         同样 34 MW 功率只需更低燃烧温度，T_PB_fuel 自然落到 ~870 K（贴近 Raptor 实测）。
    fuel_preburner_mr_target: float = 0.13

    # ---------- 预燃室温度目标模式（V11 新增）──────────────────
    #
    # 架构修正：涡轮入口温度（=预燃室出口温度）是材料硬约束，应"顶着材料
    # 极限设计"。旧逻辑把混合比当输入、温度当输出（事后告警），需手动试凑
    # MR 才能命中温度。本模式把因果方向摆正：
    #
    #   use_target_preburner_temp = 1：
    #     在 MR 目标模式之上加一层一维外环，自动反求 MR 使两侧预燃室出口温度
    #     = 目标温度。预燃室火焰温度几乎只由 MR 决定（强度量）且对 MR 单调，
    #     故为干净的一维反求，与已有的 Tc Picard 外环同构。
    #   use_target_preburner_temp = 0（默认）：
    #     沿用 use_target_preburner_mr 指定的固定 MR（向后兼容，已验证工作点）。
    #
    # 目标温度独立于材料上限（*_temp_limit）：目标是"你选择跑多热"，上限是
    # "材料能扛多热"。默认目标取 Raptor 实测量级，各留 ≥50 K 裕度。
    # 硬约束：目标温度必须 ≤ 对应材料上限。
    # V11.1：默认开启温度目标模式 —— "顶着材料极限设计"成为默认行为。
    # 工作点从旧的 737/762 K 迁移到 750/850 K（更贴 Raptor 富燃侧 ~870 K 实测）。
    # 需要旧的固定 MR 行为时置 0。
    use_target_preburner_temp: float = 1.0
    ox_preburner_temp_target: float = 750.0    # [K] 富氧涡轮入口目标温度（Raptor ~730–750）
    fuel_preburner_temp_target: float = 850.0  # [K] 富燃涡轮入口目标温度（Raptor ~870，留裕度）

    # ---------- 流量分配（自由分流模式初始猜测）----------
    # 仅在 use_target_preburner_mr = 0 时作为求解初值。
    fuel_split_to_ox_preburner: float = 0.06
    ox_split_to_ox_preburner: float = 0.97

    # ---------- 节流参数（V6 新增）----------
    throttle_min: float = 0.40      # 最低节流比例 [-]（Raptor 约 40%）
    throttle_steps: int = 5         # 节流扫描点数

    # ---------- 涡轮泵转速参数（V10 新增）──────────────────
    # 设计原则（参考 NASA SP-125 §6, Huzel & Huang）：
    #   1) override_rpm > 0 时直接锁定该转速（用于 CFTurbo 设计点指定）
    #   2) override_rpm = 0 时按 (Ns_target, n_stages, ψ) 反推
    #   3) 涡轮转速 = 同轴泵转速（FFSC 真实工程实现）
    #
    # 比转速 Ns（量纲式，N 用 rpm，Q 用 m³/s，H 用 m，per-stage）：
    #   火箭泵典型 Ns ∈ [15, 80]，离心泵最优区间 [25, 50]
    #   Raptor-3 公开估算：LOX ≈ 22-25k rpm，LCH4 ≈ 28-32k rpm
    #
    # 压头系数 ψ = g·H / u_tip²：
    #   高压离心泵典型 0.45–0.55，更小越接近轴流
    #
    # ★ LOX 泵 —— 设计点已锁定（CFTurbo 冻结点）
    #   N=26500 rpm 由 nq(EU)≈30 反推后取整锁定（CFTurbo 离心区 10–40 的中段，
    #   u_tip=333 m/s < 550 限值；与同轴富氧涡轮共用此转速）。
    lox_pump_speed_rpm_override: float = 26500.0   # [rpm] 锁定；置 0 改回按 nq 反推
    lox_pump_specific_speed_target: float = 30.0   # nq(EU)=N·√Q/H^0.75（与 CFTurbo nq(EU) 同口径）
    lox_pump_n_stages: int = 1                  # Raptor LOX 单级离心
    lox_pump_head_coefficient_psi: float = 0.50

    # ★ LCH4 泵 —— 设计点已锁定（CFTurbo 冻结点）
    #   N=38000 rpm（nq(EU)≈30，落在 CFTurbo 离心区中段、留机械裕度；比 nq=35 的
    #   44288 rpm 低 14%，效率仅降~1%，避开离心/混流边界 40 与高轴承 DN）。
    fuel_pump_speed_rpm_override: float = 38000.0
    fuel_pump_specific_speed_target: float = 30.0
    fuel_pump_n_stages: int = 2                 # Raptor LCH4 双级离心(主流公开估算)
    fuel_pump_head_coefficient_psi: float = 0.50

    # ---------- 涡轮气动设计目标（V10 新增）──────────────
    # U/C0 = 节圆速度 / 等熵喷出速度，反动度涡轮最优 0.45–0.60
    # （SP-8110 §4.4 + 图 14）。FFSC 低压比涡轮取 0.50–0.55。
    turb_ox_u_over_c_target:   float = 0.52
    turb_fuel_u_over_c_target: float = 0.55
    # 反动度 R = (h_2 - h_3)/(h_1 - h_3)；0.4–0.5 即反动级。
    turb_ox_reaction_degree:   float = 0.45
    turb_fuel_reaction_degree: float = 0.45
    # 用户指定级数（0 = 自动按 U/C0 选）
    turb_ox_n_stages_user:   int = 0
    turb_fuel_n_stages_user: int = 0

    # ---------- 涡轮叶尖速度上限（材料强度约束）──────────
    # 火箭涡轮 U_mean 典型 200–400 m/s。超过 450 m/s 多数镍基合金
    # 在工况温度下会失稳；富氧侧因 LowCo 合金强度低，更严。
    turb_ox_u_max_ms:   float = 380.0
    turb_fuel_u_max_ms: float = 450.0

    # ---------- 涡轮材料选择（V10 新增）─────────────────
    # 每根轴一套（4 个部位）；材料 key 必须存在于 materials.MATERIALS。
    # 默认采用 materials.DEFAULT_OX_TURBINE_MATERIALS / DEFAULT_FUEL_TURBINE_MATERIALS。
    #
    # 富氧侧：所有部位都必须 oxygen_compatible=True（assert in 校核层）。
    # 富燃侧：可以用高 Co 合金（Mar-M247 / Stellite 等）。
    turb_ox_rotor_material:   str = "inconel718_lowco"
    turb_ox_stator_material:  str = "inconel625"
    turb_ox_disk_material:    str = "nasa_hr1"
    turb_ox_housing_material: str = "inconel625"

    turb_fuel_rotor_material:   str = "marm247_am"
    turb_fuel_stator_material:  str = "hastelloyx"
    turb_fuel_disk_material:    str = "inconel718"
    turb_fuel_housing_material: str = "hastelloyx"

    # ---------- 推力室壁材料选择（V18 新增）─────────────────
    # 主燃烧室内壁(铜合金，燃气侧，高热流) + 外壳(镍基，结构层，承高压冷却剂)，
    # 材料 key 必须存在于 materials.MATERIALS 且 parts 含 chamber_inner_wall /
    # chamber_outer_wall。默认沿用 materials.DEFAULT_CHAMBER_WALL_MATERIALS。
    #
    # 选择驱动：① 再生冷却 marching k(T) 取此材料(若挂随温模型,如 grcop84)；
    #          ② 热结构云图/SF 校核取此材料 σy(T)；③ 材料库卡片显示此材料详情。
    #
    # 内壁候选(LOX/CH4 高压再生冷却 chamber inner)：
    #   grcop84    NASA Cu-8Cr-4Nb 弥散强化，Tmax 1023K，Raptor/RS-25E 主选(有手册随温物性)
    #   grcop42    NASA Cu-4Cr-2Nb，导热更高(310)，AM 友好，Raptor 2/Vulcan BE-4
    #   cucrzr     传统 Cu-Cr-Zr，导热 330 最高但 Tmax 仅 623K，中低热流/早期对照
    #   glidcop_al15 Al2O3 弥散强化铜(ODS)，Tmax 1123K，研究型候选
    #   narloy_z   SSME 经典 Cu-3Ag-0.5Zr，导热 316，Tmax 811K
    chamber_inner_wall_material: str = "grcop84"
    chamber_outer_wall_material: str = "inconel718_wall"

    # ---------- 涡轮泵叶尖速度上限（泵侧）──────────────
    # 离心泵叶尖 u_tip 典型 200–500 m/s；超 550 m/s 钛/铝叶轮失效风险高。
    pump_tip_speed_limit_ms: float = 550.0

    # ---------- 泵叶轮材料（新增：叶轮离心应力 + 氧兼容性校核）──────
    # 富氧侧（液氧泵）：必须 oxygen_compatible（**禁 Ti**，ASTM G93/G124）；
    #   默认 Inconel 718 富氧低钴版（叶轮）。
    # 富燃侧（甲烷泵）：可用 Ti-6Al-4V（轻、低温强度好，仅燃料侧）。
    # 校核：叶轮离心/轮缘应力 σ≈ρ_材料·u_tip² 对比 σy(T_入口)，给安全系数 SF。
    lox_pump_impeller_material:  str = "inconel718_pump_ox"
    fuel_pump_impeller_material: str = "ti6al4v"

    # ---------- 外界条件 ----------
    ambient_pressure: float = 101325.0

    # ---------- 循环方式（V20 新增）─────────────────────────────
    # "ffsc" = 全流量分级燃烧循环（Raptor 类，默认，行为与历史版本完全一致）
    # "gg"   = 燃气发生器开式循环（蓝箭 TQ-12 类甲烷机：少量推进剂在富燃燃气
    #          发生器燃烧驱动涡轮，涡轮排气经小膨胀比排气喷管直排，不进主燃室）
    cycle_type: str = "ffsc"

    # ---------- 燃气发生器循环参数（V20 新增，cycle_type="gg" 时生效）────
    # 轴系布局：
    #   single_shaft = 单涡轮同轴驱动双泵（Merlin/F-1 经典布局，默认）
    #   dual_shaft   = GG 燃气并联分流至两台涡轮，各自驱动一侧泵（Vulcain 2 类）
    gg_layout: str = "single_shaft"
    # GG 混合比（富燃）：典型 0.2–0.45，对应出口温度 ~700–1000 K。
    # use_target_gg_temp=1 时此值仅作外环初值，实际 MR 由目标温度反求。
    gg_mr_target: float = 0.30
    use_target_gg_temp: float = 1.0     # 1=按 GG 目标出口温度反求 MR（顶着材料极限设计）
    gg_temp_target: float = 850.0       # GG 出口(=涡轮入口)目标温度 [K]（富燃甲烷 GG 典型 800–950）
    gg_temp_limit: float = 950.0        # GG 温度上限 [K]（涡轮材料硬约束）
    # GG 喷注器压降 / p_gg（液-液喷注，Schoenman 同源口径）
    gg_injector_dp_ratio: float = 0.15
    # 涡轮排气背压 = 排气喷管入口压力 [Pa]（开式循环涡轮压比大的来源；典型 2–6 bar）
    gg_turbine_backpressure_pa: float = 0.40e6
    # 排气喷管膨胀比（小锥形/钟形直排喷管，产生少量推力；典型 3–6）
    gg_exhaust_expansion_ratio: float = 4.0
    # 轴系机械/传动效率（轴承+密封+盘阻；单轴直连 ~0.97）
    gg_mech_efficiency: float = 0.97
    # GG 流量分数初猜（占总流量比例；GG 循环典型 2–5%）
    gg_fraction_guess: float = 0.03

    def __post_init__(self):
        """输入参数物理范围校验。"""

        # ── 推力 & 压力（必须 > 0）─────────────────────────
        _check_range("thrust（推力）",          self.thrust,     lo=0, lo_inclusive=False)
        _check_range("pc（燃烧室压力）",         self.pc,         lo=0, lo_inclusive=False)
        _check_range("tank_pressure_pa（贮箱压力）", self.tank_pressure_pa, lo=0, lo_inclusive=False)
        _check_range("inlet_pressure（增压泵出口/主泵供给压力）", self.inlet_pressure, lo=0, lo_inclusive=False)
        _check_range("feed_line_dp_pa（增压泵→主泵短管压降）", self.feed_line_dp_pa, lo=0)
        _check_range("boost_pump_efficiency（增压泵效率）",
                     self.boost_pump_efficiency, lo=0, hi=1.0, lo_inclusive=False)
        # 增压泵出口必须高于贮箱压力（否则不是「增压」）
        if self.inlet_pressure <= self.tank_pressure_pa:
            raise ValidationError(
                f"inlet_pressure（增压泵出口 {self.inlet_pressure/1e5:.2f} bar）必须高于 "
                f"tank_pressure_pa（贮箱 {self.tank_pressure_pa/1e5:.2f} bar）"
            )
        _warn_range("tank_pressure_pa（贮箱压力）", self.tank_pressure_pa, 1.5e5, 6.0e5)
        _check_range("ambient_pressure（环境压力）", self.ambient_pressure, lo=0)

        # ── 氧燃比（必须 > 0）──────────────────────────────
        _check_range("of_ratio（氧燃比）",       self.of_ratio,   lo=0, lo_inclusive=False)
        _warn_range("of_ratio（氧燃比）",        self.of_ratio,   1.0, 10.0)

        # ── 主燃烧室燃烧效率（硬约束：0 < η ≤ 1）────────────
        _check_range("combustion_efficiency（主燃室燃烧效率）",
                     self.combustion_efficiency, lo=0, hi=1.0, lo_inclusive=False)
        _warn_range("combustion_efficiency（主燃室燃烧效率）",
                    self.combustion_efficiency, 0.95, 1.0)

        # ── 膨胀比（必须 ≥ 1）──────────────────────────────
        _check_range("expansion_ratio（膨胀比）", self.expansion_ratio, lo=1.0)
        _warn_range("expansion_ratio（膨胀比）",  self.expansion_ratio, 2.0, 300.0)

        # ── 温度（必须 > 0 K）─────────────────────────────
        _check_range("fuel_inlet_temp（燃料入口温度）", self.fuel_inlet_temp, lo=0, lo_inclusive=False)
        _check_range("ox_inlet_temp（氧化剂入口温度）", self.ox_inlet_temp,   lo=0, lo_inclusive=False)

        # ── 泵效率（硬约束：0 < η ≤ 1）──────────────────
        _check_range("pump_efficiency_ox（LOX泵效率）",
                     self.pump_efficiency_ox,   lo=0, hi=1.0, lo_inclusive=False)
        _check_range("pump_efficiency_fuel（CH₄泵效率）",
                     self.pump_efficiency_fuel, lo=0, hi=1.0, lo_inclusive=False)

        # ── 涡轮效率（硬约束：0 < η ≤ 1）────────────────
        _check_range("turbine_efficiency_ox（富氧涡轮效率）",
                     self.turbine_efficiency_ox,   lo=0, hi=1.0, lo_inclusive=False)
        _check_range("turbine_efficiency_fuel（富燃涡轮效率）",
                     self.turbine_efficiency_fuel, lo=0, hi=1.0, lo_inclusive=False)

        # ── 效率工程范围软校验 ─────────────────────────────
        _warn_range("pump_efficiency_ox",   self.pump_efficiency_ox,   0.60, 0.92)
        _warn_range("pump_efficiency_fuel", self.pump_efficiency_fuel, 0.55, 0.90)
        _warn_range("turbine_efficiency_ox",   self.turbine_efficiency_ox,   0.60, 0.92)
        _warn_range("turbine_efficiency_fuel", self.turbine_efficiency_fuel, 0.60, 0.92)

        # ── 冷却系统（必须 ≥ 0 / > 0）────────────────────
        _check_range("cooling_bartz_mode（Bartz模式开关）", self.cooling_bartz_mode, lo=0.0, hi=1.0)
        _check_range("cooling_heat_load（冷却热载荷）", self.cooling_heat_load, lo=0)
        _check_range("coolant_cp（冷却剂比热）",        self.coolant_cp,        lo=0, lo_inclusive=False)

        # ── 再生冷却通道几何（V14）────────────────────────
        _check_range("cooling_n_channels（冷却通道数）", self.cooling_n_channels, lo=10, hi=2000)
        _check_range("cooling_channel_width_m（通道宽）", self.cooling_channel_width_m, lo=0.3e-3, hi=12e-3)
        _check_range("cooling_channel_height_m（通道深）", self.cooling_channel_height_m, lo=0.5e-3, hi=20e-3)
        _check_range("cooling_wall_thickness_m（内衬热壁厚）", self.cooling_wall_thickness_m, lo=0.2e-3, hi=3e-3)
        _check_range("cooling_wall_conductivity（内衬导热）", self.cooling_wall_conductivity, lo=10.0, hi=450.0)
        _check_range("cooling_htc_enhancement（换热强化因子）", self.cooling_htc_enhancement, lo=0.5, hi=2.0)
        _check_range("cooling_channel_auto_size（自动定尺寸开关）", self.cooling_channel_auto_size, lo=0.0, hi=1.0)
        _check_range("cooling_target_velocity_ms（目标冷却剂流速）", self.cooling_target_velocity_ms, lo=10.0, hi=300.0)
        _warn_range("cooling_target_velocity_ms（目标冷却剂流速）", self.cooling_target_velocity_ms, 40.0, 150.0)
        _check_range("cooling_dp_auto（水力压降耦合开关）", self.cooling_dp_auto, lo=0.0, hi=1.0)
        _check_range("cooling_channel_rel_roughness（通道相对粗糙度）", self.cooling_channel_rel_roughness, lo=0.0, hi=0.05)
        _check_range("cooling_channel_k_minor（局部损失动压头数）", self.cooling_channel_k_minor, lo=0.0, hi=20.0)
        _check_range("chamber_conv_half_angle_deg（收缩段半锥角）", self.chamber_conv_half_angle_deg, lo=10.0, hi=60.0)
        _check_range("nozzle_bell_fraction（钟形长度比）", self.nozzle_bell_fraction, lo=0.60, hi=1.00)
        _check_range("cooling_gas_emissivity（燃气辐射发射率）", self.cooling_gas_emissivity, lo=0.0, hi=0.60)
        # 通道分布表：仅做 JSON 可解析性校验（结构校验在 chamber_design.parse_channel_table）
        if isinstance(self.cooling_channel_table_json, str) and self.cooling_channel_table_json.strip():
            import json as _json
            try:
                _json.loads(self.cooling_channel_table_json)
            except Exception as exc:
                raise ValidationError(f"cooling_channel_table_json 不是有效 JSON：{exc}")
        _check_range("cooling_marching_enable（marching 开关）", self.cooling_marching_enable, lo=0.0, hi=1.0)
        if self.cooling_cover_eps != 0.0:
            _check_range("cooling_cover_eps（再生冷却覆盖面积比）", self.cooling_cover_eps, lo=1.5, hi=400.0)
        _check_range("cooling_wall_temp_limit_K（热面壁温限值）", self.cooling_wall_temp_limit_K, lo=400.0, hi=1500.0)
        _check_range("film_cooling_fraction（膜冷却流量比例）", self.film_cooling_fraction, lo=0.0, hi=0.15)
        _check_range("film_effectiveness_initial（膜初始效能）", self.film_effectiveness_initial, lo=0.1, hi=1.0)
        _check_range("film_decay_length_m（膜效能衰减长）", self.film_decay_length_m, lo=0.0, hi=3.0)

        # ── 燃烧室猜测值 ──────────────────────────────────
        _check_range("chamber_temp_guess（燃烧室温度猜测值）", self.chamber_temp_guess, lo=0, lo_inclusive=False)
        _check_range("chamber_gamma（燃烧室比热比）",          self.chamber_gamma,      lo=1.0, lo_inclusive=False)
        _check_range("molar_mass（摩尔质量）",                 self.molar_mass,         lo=0, lo_inclusive=False)

        # ── 比冲目标 ──────────────────────────────────────
        _check_range("target_isp（目标比冲）", self.target_isp, lo=0, lo_inclusive=False)
        _warn_range("target_isp（目标比冲）",  self.target_isp, 200.0, 500.0)

        # ── 预燃室压力比（必须 ≥ 1）────────────────────
        _check_range("ox_preburner_pressure_ratio（氧侧预燃室压力比）",
                     self.ox_preburner_pressure_ratio, lo=1.0)
        _check_range("fuel_preburner_pressure_ratio（燃侧预燃室压力比）",
                     self.fuel_preburner_pressure_ratio, lo=1.0)
        _check_range("ox_turbine_exhaust_dp_ratio（富氧涡轮出口至主燃烧室压降比）",
                     self.ox_turbine_exhaust_dp_ratio, lo=0.0, hi=0.8)
        _check_range("fuel_turbine_exhaust_dp_ratio（富燃涡轮出口至主燃烧室压降比）",
                     self.fuel_turbine_exhaust_dp_ratio, lo=0.0, hi=0.8)

        # ── 预燃室温度上限（V6 新增）───────────────────
        _check_range("ox_preburner_temp_limit（富氧预燃室温度上限）",
                     self.ox_preburner_temp_limit, lo=400, hi=1200)
        _check_range("fuel_preburner_temp_limit（富燃预燃室温度上限）",
                     self.fuel_preburner_temp_limit, lo=600, hi=1500)

        # ── 压力网络参数（V7 新增）──────────────────────
        _check_range("injector_dp_ratio_ox（氧喷注器压降比）",
                     self.injector_dp_ratio_ox, lo=0.05, hi=0.50)
        _check_range("injector_dp_ratio_fuel（燃喷注器压降比）",
                     self.injector_dp_ratio_fuel, lo=0.05, hi=0.50)
        _check_range("preburner_injector_dp_ratio（预燃室喷注器压降/预燃室压力）",
                     self.preburner_injector_dp_ratio, lo=0.03, hi=0.30)
        _warn_range("preburner_injector_dp_ratio（预燃室喷注器压降/预燃室压力）",
                    self.preburner_injector_dp_ratio, 0.08, 0.20)
        _check_range("cooling_dp_pa（冷却通道压降）",
                     self.cooling_dp_pa, lo=0)
        _check_range("valve_line_dp_ratio（阀门管路压降比）",
                     self.valve_line_dp_ratio, lo=0, hi=0.20)
        _check_range("chamber_wall_allow_pa（壁面许用应力）",
                     self.chamber_wall_allow_pa, lo=1e6, hi=2e9)
        _check_range("chamber_wall_safety_factor（壁厚安全系数）",
                     self.chamber_wall_safety_factor, lo=1.0, hi=5.0)
        _check_range("chamber_contraction_ratio（燃烧室收缩比）",
                     self.chamber_contraction_ratio, lo=1.5, hi=8.0)
        _check_range("chamber_l_star_m（燃烧室特征长度）",
                     self.chamber_l_star_m, lo=0.2, hi=3.0)
        _warn_range("chamber_contraction_ratio（燃烧室收缩比）",
                    self.chamber_contraction_ratio, 2.5, 4.0)
        _warn_range("chamber_l_star_m（燃烧室特征长度）",
                    self.chamber_l_star_m, 0.8, 1.2)

        # ── 预燃室目标混合比（V3 新增）──────────────────
        _check_range("use_target_preburner_mr（是否采用目标预燃室混合比模式）",
                     self.use_target_preburner_mr, lo=0.0, hi=1.0)
        _check_range("ox_preburner_mr_target（富氧预燃室目标 O/F）",
                     self.ox_preburner_mr_target, lo=5.0, hi=200.0)
        _check_range("fuel_preburner_mr_target（富燃预燃室目标 O/F）",
                     self.fuel_preburner_mr_target, lo=0.01, hi=2.0)
        _warn_range("ox_preburner_mr_target（富氧预燃室目标 O/F）",
                    self.ox_preburner_mr_target, 40.0, 80.0)
        _warn_range("fuel_preburner_mr_target（富燃预燃室目标 O/F）",
                    self.fuel_preburner_mr_target, 0.05, 0.30)

        # ── 预燃室温度目标模式（V11 新增）──────────────────
        _check_range("use_target_preburner_temp（温度目标模式开关）",
                     self.use_target_preburner_temp, lo=0.0, hi=1.0)
        _check_range("ox_preburner_temp_target（富氧预燃室目标温度）",
                     self.ox_preburner_temp_target, lo=400.0, hi=1200.0)
        _check_range("fuel_preburner_temp_target（富燃预燃室目标温度）",
                     self.fuel_preburner_temp_target, lo=600.0, hi=1500.0)
        if self.use_target_preburner_temp >= 0.5:
            # 目标温度不能超过材料温度上限（顶着极限但不能越界）
            if self.ox_preburner_temp_target > self.ox_preburner_temp_limit:
                raise ValidationError(
                    f"ox_preburner_temp_target = {self.ox_preburner_temp_target} K "
                    f"> ox_preburner_temp_limit = {self.ox_preburner_temp_limit} K，"
                    f"目标温度不能超过富氧涡轮材料上限"
                )
            if self.fuel_preburner_temp_target > self.fuel_preburner_temp_limit:
                raise ValidationError(
                    f"fuel_preburner_temp_target = {self.fuel_preburner_temp_target} K "
                    f"> fuel_preburner_temp_limit = {self.fuel_preburner_temp_limit} K，"
                    f"目标温度不能超过富燃涡轮材料上限"
                )

        # ── 自由分流模式初始猜测 ─────────────────────────
        _check_range("fuel_split_to_ox_preburner（燃料→富氧预燃室比例）",
                     self.fuel_split_to_ox_preburner, lo=0.005, hi=0.80,
                     lo_inclusive=True, hi_inclusive=False)
        _check_range("ox_split_to_ox_preburner（氧化剂→富氧预燃室比例）",
                     self.ox_split_to_ox_preburner, lo=0.20, hi=0.999,
                     lo_inclusive=False, hi_inclusive=False)

        _warn_range("fuel_split_to_ox_preburner（燃料→富氧预燃室比例）",
                    self.fuel_split_to_ox_preburner, 0.03, 0.15)
        _warn_range("ox_split_to_ox_preburner（氧化剂→富氧预燃室比例）",
                    self.ox_split_to_ox_preburner, 0.90, 0.995)

        # ── 节流参数（V6 新增）────────────────────────
        _check_range("throttle_min（最低节流比例）",
                     self.throttle_min, lo=0.05, hi=0.99)
        if not isinstance(self.throttle_steps, int) or self.throttle_steps < 2:
            self.throttle_steps = max(2, int(self.throttle_steps))

        # ── 涡轮泵转速参数（V10 新增）─────────────────
        # override_rpm 允许 0（=自动反推）或 [1000, 200000] 之间
        for nm, val in [
            ("lox_pump_speed_rpm_override（LOX 泵转速锁定）",  self.lox_pump_speed_rpm_override),
            ("fuel_pump_speed_rpm_override（CH4 泵转速锁定）", self.fuel_pump_speed_rpm_override),
        ]:
            _check_range(nm, val, lo=0.0)
            if val > 0:
                _warn_range(nm, val, 5000.0, 80000.0)
        _check_range("lox_pump_specific_speed_target（LOX Ns 目标）",
                     self.lox_pump_specific_speed_target, lo=5.0, hi=200.0)
        _check_range("fuel_pump_specific_speed_target（CH4 Ns 目标）",
                     self.fuel_pump_specific_speed_target, lo=5.0, hi=200.0)
        _warn_range("lox_pump_specific_speed_target（LOX Ns 目标）",
                    self.lox_pump_specific_speed_target, 15.0, 80.0)
        _warn_range("fuel_pump_specific_speed_target（CH4 Ns 目标）",
                    self.fuel_pump_specific_speed_target, 15.0, 80.0)
        if not isinstance(self.lox_pump_n_stages, int) or self.lox_pump_n_stages < 1:
            self.lox_pump_n_stages = max(1, int(self.lox_pump_n_stages))
        if not isinstance(self.fuel_pump_n_stages, int) or self.fuel_pump_n_stages < 1:
            self.fuel_pump_n_stages = max(1, int(self.fuel_pump_n_stages))
        _check_range("lox_pump_n_stages（LOX 泵级数）",
                     float(self.lox_pump_n_stages), lo=1, hi=6)
        _check_range("fuel_pump_n_stages（CH4 泵级数）",
                     float(self.fuel_pump_n_stages), lo=1, hi=6)
        _check_range("lox_pump_head_coefficient_psi（LOX 压头系数 ψ）",
                     self.lox_pump_head_coefficient_psi, lo=0.2, hi=0.8)
        _check_range("fuel_pump_head_coefficient_psi（CH4 压头系数 ψ）",
                     self.fuel_pump_head_coefficient_psi, lo=0.2, hi=0.8)

        # ── 涡轮气动设计目标（V10 新增）─────────────────
        for nm, val in [
            ("turb_ox_u_over_c_target（富氧涡轮 U/C0）",   self.turb_ox_u_over_c_target),
            ("turb_fuel_u_over_c_target（富燃涡轮 U/C0）", self.turb_fuel_u_over_c_target),
        ]:
            _check_range(nm, val, lo=0.10, hi=0.80)
            _warn_range(nm, val, 0.40, 0.65)
        for nm, val in [
            ("turb_ox_reaction_degree（富氧反动度）",   self.turb_ox_reaction_degree),
            ("turb_fuel_reaction_degree（富燃反动度）", self.turb_fuel_reaction_degree),
        ]:
            _check_range(nm, val, lo=0.0, hi=0.8)
        for nm, val in [
            ("turb_ox_u_max_ms（富氧涡轮 U_max）",   self.turb_ox_u_max_ms),
            ("turb_fuel_u_max_ms（富燃涡轮 U_max）", self.turb_fuel_u_max_ms),
        ]:
            _check_range(nm, val, lo=100.0, hi=700.0)
        if not isinstance(self.turb_ox_n_stages_user, int):
            self.turb_ox_n_stages_user = int(self.turb_ox_n_stages_user)
        if not isinstance(self.turb_fuel_n_stages_user, int):
            self.turb_fuel_n_stages_user = int(self.turb_fuel_n_stages_user)
        _check_range("turb_ox_n_stages_user（富氧级数，0=自动）",
                     float(self.turb_ox_n_stages_user), lo=0, hi=6)
        _check_range("turb_fuel_n_stages_user（富燃级数，0=自动）",
                     float(self.turb_fuel_n_stages_user), lo=0, hi=6)

        # ── 涡轮材料 key 必须可在 materials.MATERIALS 中找到 ─
        # 富氧侧 4 个部位必须全部 oxygen_compatible
        try:
            from materials import MATERIALS, assert_oxygen_compatible  # noqa: WPS433
        except ImportError:
            MATERIALS = None  # 旧环境兼容
            assert_oxygen_compatible = None
        if MATERIALS is not None:
            for nm, key in [
                ("turb_ox_rotor_material",   self.turb_ox_rotor_material),
                ("turb_ox_stator_material",  self.turb_ox_stator_material),
                ("turb_ox_disk_material",    self.turb_ox_disk_material),
                ("turb_ox_housing_material", self.turb_ox_housing_material),
                ("turb_fuel_rotor_material",   self.turb_fuel_rotor_material),
                ("turb_fuel_stator_material",  self.turb_fuel_stator_material),
                ("turb_fuel_disk_material",    self.turb_fuel_disk_material),
                ("turb_fuel_housing_material", self.turb_fuel_housing_material),
                ("lox_pump_impeller_material",  self.lox_pump_impeller_material),
                ("fuel_pump_impeller_material", self.fuel_pump_impeller_material),
                # 推力室壁材料（V18）：同样要求 key 存在于 MATERIALS
                ("chamber_inner_wall_material", self.chamber_inner_wall_material),
                ("chamber_outer_wall_material", self.chamber_outer_wall_material),
            ]:
                if key not in MATERIALS:
                    raise ValidationError(
                        f"{nm}='{key}' 不在 materials.MATERIALS 中。"
                        f"可用 key：{sorted(MATERIALS.keys())}"
                    )
            # 推力室壁材料必须支持对应部位（parts 列表里有 chamber_inner_wall/outer_wall）
            for nm, key, part in [
                ("chamber_inner_wall_material", self.chamber_inner_wall_material, "chamber_inner_wall"),
                ("chamber_outer_wall_material", self.chamber_outer_wall_material, "chamber_outer_wall"),
            ]:
                if part not in MATERIALS[key].parts:
                    raise ValidationError(
                        f"{nm}='{key}' 的 parts={MATERIALS[key].parts} 不含 '{part}'。"
                        f"请改选支持该部位的材料。"
                    )
            # 富氧侧硬校验：涡轮 4 部位 + 液氧泵叶轮 都必须 oxygen_compatible（禁 Ti）
            for part, key in [
                ("ox_turbine.rotor_blade", self.turb_ox_rotor_material),
                ("ox_turbine.stator_vane", self.turb_ox_stator_material),
                ("ox_turbine.disk",        self.turb_ox_disk_material),
                ("ox_turbine.housing",     self.turb_ox_housing_material),
                ("lox_pump.impeller",      self.lox_pump_impeller_material),
            ]:
                assert_oxygen_compatible(key, where=part)

        # ── 泵叶尖速度上限 ───────────────────────────────
        _check_range("pump_tip_speed_limit_ms（泵叶尖速度上限）",
                     self.pump_tip_speed_limit_ms, lo=100.0, hi=800.0)

        # ── 循环方式 & 燃气发生器参数（V20 新增）──────────────
        if self.cycle_type not in ("ffsc", "gg"):
            raise ValidationError(
                f"cycle_type='{self.cycle_type}' 无效，必须是 'ffsc'（全流量分级燃烧）"
                f"或 'gg'（燃气发生器开式循环）"
            )
        if self.gg_layout not in ("single_shaft", "dual_shaft"):
            raise ValidationError(
                f"gg_layout='{self.gg_layout}' 无效，必须是 'single_shaft'（单涡轮双泵）"
                f"或 'dual_shaft'（燃气分流双涡轮泵）"
            )
        _check_range("gg_mr_target（GG 目标混合比）", self.gg_mr_target, lo=0.05, hi=1.0)
        _warn_range("gg_mr_target（GG 目标混合比）", self.gg_mr_target, 0.20, 0.45)
        _check_range("use_target_gg_temp（GG 温度目标模式开关）",
                     self.use_target_gg_temp, lo=0.0, hi=1.0)
        _check_range("gg_temp_target（GG 目标出口温度）", self.gg_temp_target, lo=600.0, hi=1500.0)
        _check_range("gg_temp_limit（GG 温度上限）", self.gg_temp_limit, lo=600.0, hi=1500.0)
        if self.use_target_gg_temp >= 0.5 and self.gg_temp_target > self.gg_temp_limit:
            raise ValidationError(
                f"gg_temp_target = {self.gg_temp_target} K > gg_temp_limit = "
                f"{self.gg_temp_limit} K，GG 目标温度不能超过涡轮材料上限"
            )
        _check_range("gg_injector_dp_ratio（GG 喷注器压降比）",
                     self.gg_injector_dp_ratio, lo=0.03, hi=0.40)
        _check_range("gg_turbine_backpressure_pa（GG 涡轮排气背压）",
                     self.gg_turbine_backpressure_pa, lo=0.05e6, hi=5.0e6)
        _warn_range("gg_turbine_backpressure_pa（GG 涡轮排气背压）",
                    self.gg_turbine_backpressure_pa, 0.15e6, 1.0e6)
        _check_range("gg_exhaust_expansion_ratio（GG 排气喷管膨胀比）",
                     self.gg_exhaust_expansion_ratio, lo=1.0, hi=50.0)
        _check_range("gg_mech_efficiency（GG 轴系机械效率）",
                     self.gg_mech_efficiency, lo=0.5, hi=1.0)
        _check_range("gg_fraction_guess（GG 流量分数初猜）",
                     self.gg_fraction_guess, lo=0.002, hi=0.20)


# =========================================
# 2️⃣ 流动状态层（Flow State）
# =========================================

@dataclass
class FlowState:
    """单个"流动状态点"（Flow State）"""

    name: str
    pressure: float           # 压力 [Pa]
    temperature: float        # 温度 [K]
    mass_flow: float          # 质量流量 [kg/s]

    enthalpy: float | None = None
    density: float | None = None

    composition: Dict[str, float] = field(default_factory=dict)
    metadata: Dict[str, float] = field(default_factory=dict)


# =========================================
# 3️⃣ 部件结果层（Component Layer）
# =========================================

@dataclass
class ComponentResult:
    """单个部件的计算结果"""

    name: str
    outputs: Dict[str, float] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)


# =========================================
# =========================================
# 4. 拓扑层（流程图节点 / 边）
# =========================================

@dataclass
class TopologyNode:
    key:    str
    label:  str
    pos:    tuple  # (x, y) 像素坐标

@dataclass
class TopologyEdge:
    source: str
    target: str
    label:  str = ""


# =========================================
# 5. CycleResults 汇总
# =========================================

@dataclass
class CycleResults:
    """一次完整 FFSC 循环求解的全部输出"""
    inputs:          EngineInputs
    summary:         Dict[str, float]
    states:          Dict[str, FlowState]
    components:      Dict[str, ComponentResult]
    warnings:        List[str]
    topology_nodes:  List = field(default_factory=list)
    topology_edges:  List = field(default_factory=list)
    geometry:        Dict[str, float] = field(default_factory=dict)
