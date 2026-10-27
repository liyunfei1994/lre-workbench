"""
core.py

系统级数据结构定义（System Data Layer）

EngineInputs 的默认值构成工程设计示例，不是一套经过验证的 Raptor 3 实机数据。
公开机型性能及其来源由 engine_presets / ENGINE_REFERENCES 单独管理。
设计边界、部件假设、运行限值与数值初值必须按当前模式区分；参数名称和
历史序列化键保持兼容，不表示所有字段都会参与当前计算。
"""

from __future__ import annotations

import math
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


PREBURNER_LIMIT_STATUSES = ("unset", "provisional", "documented")


def preburner_temperature_constraint(inputs, side: str) -> dict:
    """Resolve an optional gas-temperature constraint without inventing evidence.

    Missing status is the historical, provisional numeric constraint. Saved
    snapshots are not rewritten. An unset value remains stored but is inactive.
    """
    if side not in ("ox", "fuel"):
        raise ValueError("preburner side must be ox or fuel")
    prefix = f"{side}_preburner_temp_limit"
    read = inputs.get if isinstance(inputs, dict) else lambda key, default=None: getattr(inputs, key, default)
    status = read(f"{prefix}_status", "provisional")
    basis = read(f"{prefix}_basis", "")
    basis = basis.strip() if isinstance(basis, str) else ""
    active = status in ("provisional", "documented")
    return {"status": status, "basis": basis,
            "limit_K": read(prefix) if active else None}


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

    - 温度目标模式反求预燃室 O/F；混合比模式将 O/F 作为设计约束。
    - 两个 split 字段仅用于历史自由分流模式的初值，目标模式不使用。
    - 温度上限是用户给定的运行约束，不能代替材料与寿命验证。
    """

    # ---------- 基本性能（工程设计输入）----------
    thrust: float = 2.45e6            # 当前环境下的目标推力 [N]；通用历史默认值
    pc: float = 33.0e6               # 燃烧室压力 [Pa]，工程假设
    of_ratio: float = 3.6            # 总氧化剂/燃料质量流量比，工程假设
    expansion_ratio: float = 34.34   # 喷管出口/喉部面积比，工程假设
    # 历史键 combustion_efficiency 实现的是主室温升折减系数：先求化学平衡，
    # 再保持组成不变折减温升，并将焓差列为损失。不是 c* 效率或燃尽率。
    # 1.0 表示不施加该经验折减；0.99 是示例假设，不是实机测量值。
    combustion_efficiency: float = 0.99
    # 缺失字段的旧方案沿用旧温升模型；新参考示例显式选用焓平衡。
    # adiabatic 表示不附加经验燃烧损失，不会取消单独记账的再生冷却抽热。
    combustion_loss_model: str = "legacy_temperature_rise"
    # Old snapshots keep GRI30. High-temperature species data are an explicit
    # model choice, not an EOS change or a finite-rate chemistry certification.
    methane_thermo_dataset: str = "gri30_highT"

    # ---------- 贮箱出口边界（历史键名 inlet_temp）----------
    fuel_inlet_temp: float = 100.0   # 燃料贮箱出口温度 [K]，工程边界假设
    ox_inlet_temp: float = 70.0      # 液氧贮箱出口温度 [K]，工程边界假设
    # ════════ 增压输送系统（贮箱 → 增压泵 → 主泵）════════════════════
    # 本示例供给链假设；三个压力节点分开建模：
    #
    #   贮箱(tank_pressure_pa)  → 增压泵(boost)  → 短管(feed_line_dp_pa) → 主泵入口
    #        ~3 bar               抬到 ~10 bar        −0.5 bar              9.5 bar
    # 增压泵在当前模型中由外部功源供能，不计入两根主轴残差，但计入总能量账。
    # 未据此验证任何实机增压泵驱动方式或汽蚀裕度。

    # ① 贮箱压力边界（不模拟自生增压过程）
    tank_pressure_pa: float = 3.0e5   # 贮箱压力 [Pa]，工程假设

    # ② 增压泵出口压力 = 主泵供给压力（沿用历史键名 inlet_pressure）
    #    主泵入口压力还需扣除 feed_line_dp_pa。
    inlet_pressure: float = 1.0e6     # 增压泵出口压力 [Pa]，工程假设

    # ③ 增压泵常效率假设
    boost_pump_efficiency: float = 0.70

    # ---------- 增压泵→主泵 短管压降 ----------
    feed_line_dp_pa: float = 0.5e5    # 增压泵→主泵短管压降预算 [Pa]

    # ---------- 部件常效率假设（非 Raptor 实测值）----------
    pump_efficiency_ox:   float = 0.86
    pump_efficiency_fuel: float = 0.85
    turbine_efficiency_ox: float = 0.85
    turbine_efficiency_fuel: float = 0.88

    # ---------- 冷却系统 ----------
    # V8：默认启用 Bartz 热流估算模式（cooling_bartz_mode=True）
    # Bartz 模式：从发动机参数（mdot、pc、Tc、γ、Mm）自动推算喉部面积和热载荷
    # 固定模式（cooling_bartz_mode=False）：直接用 cooling_heat_load（V7 兼容）
    cooling_bartz_mode: float = 1.0      # 1=Bartz估算（默认），0=固定热载荷（V7兼容）
    cooling_heat_load: float = 90.0e6   # 再生冷却吸热量 [W]（固定模式用；Bartz模式忽略）
    coolant_cp: float = 2.4e3           # 历史无物性库回退用常数 [J/(kg·K)]；正常焓路径不用

    # ---------- 再生冷却通道几何（V14：正式纳入，之前用 getattr 读但未定义 → 锁死）----------
    # 几何、换热系数和材料常数均为设计假设。自动定尺寸与水力估算是低阶模型，
    # 启用后也不代表通道几何、热量积分范围与压降模型已获得试验验证。
    cooling_n_channels: float = 300.0        # 冷却通道数（周向，受室周长约束；典型 150–400）
    cooling_channel_width_m: float = 1.5e-3  # 通道周向宽 [m]
    cooling_channel_height_m: float = 5.0e-3 # 通道径向深 [m]（auto_size=1 时被反算覆盖）
    cooling_wall_thickness_m: float = 0.5e-3 # 内衬热壁厚 [m]（GRCop ~0.5–1 mm）
    cooling_wall_conductivity: float = 320.0 # 内衬导热 [W/(m·K)]（GRCop-84 @600K）
    cooling_htc_enhancement: float = 1.0     # 喉部弯曲/入口段换热强化因子（1.0–1.5）
    # 固定通道数与宽度，用目标流速作概念尺寸约束，反算通道深度。
    cooling_channel_auto_size: float = 1.0   # 1=按目标流速反算通道深度（默认），0=用上面固定几何
    cooling_target_velocity_ms: float = 100.0  # 目标冷却剂流速 [m/s]（再生通道典型 40–150）
    # 旧方案兼容字段，不再控制物理模型。准一维模式同时求解热量与压降。
    cooling_dp_auto: float = 0.0
    cooling_channel_rel_roughness: float = 0.003  # 通道相对粗糙度 ε/D_h（AM 打印态 ~3e-3）
    cooling_channel_k_minor: float = 3.0     # 进出口+集流环+弯头局部损失（动压头数）

    # ---------- 唯一准一维冷却模型 ----------
    cooling_marching_enable: float = 1.0     # 旧方案兼容字段；统一内核始终返回沿程结果
    cooling_grid_points: float = 120.0      # 唯一准一维网格分辨率；不改变冷却物理模式
    cooling_cover_eps: float = 0.0           # 再生冷却覆盖到的面积比（0=全喷管 ε；如填 8 表示只冷到 ε=8）
    cooling_wall_temp_limit_K: float = 800.0 # 用户给定热面壁温限值 [K]；不替代材料寿命校核
    # 燃气辐射发射率 ε_g（灰气体一阶代理，marching 辐射热流用；V19 起为正式输入字段，
    # 之前 marching 用 getattr 读取但字段不存在 → 永远只能用 0.20 默认值）
    cooling_gas_emissivity: float = 0.20
    # ---------- 冷却通道沿程分布表（V19：推力室设计工作区）----------
    # JSON 断点表定义宽/深/衬厚沿轴向分布；当前未建集流分支，通道数 N 必须恒定。
    # "" = 使用标量通道几何假设，仍调用同一个准一维内核。
    # 格式见 chamber_design.py：{"version":1,"rows":[{"anchor":"throat","n":300,
    #   "w_mm":1.5,"h_mm":5.0,"t_mm":0.5}, ...]}，anchor ∈ injector/conv_start/throat/exit/x_mm
    cooling_channel_table_json: str = ""

    # 历史膜冷却字段只供旧方案读取；未建质量/焓支路，物理沿程内核拒绝非零流量。
    film_cooling_fraction: float = 0.0
    film_effectiveness_initial: float = 0.9  # 兼容值，当前不生效
    film_decay_length_m: float = 0.0         # 兼容值，当前不生效

    # ---------- 主室热反馈初值（不是焓参考态，也不是实机测量值）----------
    # 启用热反馈时由求解结果更新；关闭该反馈时成为固定的性能近似输入。
    chamber_temp_guess: float = 3550.0  # 主室温度初值 [K]
    chamber_gamma: float = 1.18         # 比热比初值 [-]
    molar_mass: float = 21.0            # 平均摩尔质量初值 [kg/kmol]
    # 化学焓与真实流体焓的参考态对齐由 thermo.py 管理，与这些初值无关。

    # ---------- 总流量初值 ----------
    target_isp: float = 327.0   # 比冲初值 [s]；仅用于 mdot 初猜，不是最终比冲约束

    # ---------- 预燃室压力比（p_pb/p_c，不是真正的涡轮入口/出口压比）----------
    # 目标模式用于构造涡轮压比初值；历史自由分流模式中为固定压力关系。
    ox_preburner_pressure_ratio: float = 1.15
    fuel_preburner_pressure_ratio: float = 1.18
    # 涡轮排气至主燃烧室的热气段压降预算，包含当前主喷注器口径：
    # p_turbine_out = Pc*(1 + dp_ratio)。数值为工程假设，非实机测量。
    ox_turbine_exhaust_dp_ratio: float = 0.243
    fuel_turbine_exhaust_dp_ratio: float = 0.093

    # ---------- 用户指定预燃室出口/涡轮入口温度上限 ----------
    # 本模型将两者视为同一温度节点；限值不由选定材料自动推导。
    # 实际允许值还需材料、环境、冷却和寿命证据，不能称为通用材料耐温极限。
    ox_preburner_temp_limit: float = 800.0    # [K] 富氧侧运行限值假设
    fuel_preburner_temp_limit: float = 900.0  # [K] 富燃侧运行限值假设
    # 缺少状态的历史方案仍按暂定数值检查；不据此宣称材料/寿命已验证。
    # unset 不使用保存的数值；documented 仅表示用户已填写该方案的依据。
    ox_preburner_temp_limit_status: str = "provisional"
    fuel_preburner_temp_limit_status: str = "provisional"
    ox_preburner_temp_limit_basis: str = ""
    fuel_preburner_temp_limit_basis: str = ""

    # ---------- 压力网络（V7 新增，FFSC 必需）----------
    # ⚠ FFSC 模式已废弃（V13）：主燃烧室喷注器压降统一由 ox/fuel_turbine_exhaust_dp_ratio
    #   表示（= 涡轮排气压力 − Pc，与压力网络同口径）。
    # ★ V20 复活：GG（燃气发生器）模式下主燃烧室是 **液-液喷注**，这两个参数
    #   即为液路主喷注器压降比 ΔP/Pc（典型 0.15–0.25），由 gg_cycle.py 使用。
    injector_dp_ratio_ox:   float = 0.20   # FFSC 不用；GG=主燃室液氧喷注压降比
    injector_dp_ratio_fuel: float = 0.20   # FFSC 不用；GG=主燃室甲烷喷注压降比
    # 预燃室喷注器压降 / 预燃室压力（两个预燃室各自一套喷注器）。
    #   设计点按对应预燃室压力计算，形成所需供给压力预算；当前 0.12 为
    #   示例假设，不构成喷注器稳定性验证，也不是已核实的 Raptor 内部压降。
    preburner_injector_dp_ratio: float = 0.12   # 预燃室喷注器 ΔP / 预燃室压力
    # 给定冷却压降 [Pa]；仅 Q、压降预算模式生效。准一维模式中仅作耦合初值。
    cooling_dp_pa: float = 5.0e6
    # 支路阀门/管路基础压降预算与 pc 之比；另求的调节压降单独记账。
    valve_line_dp_ratio: float = 0.03

    # ---------- 材料 / 几何（V7 新增）----------
    # 壁面强度与安全系数为概念估算输入，不声明任何实机材料或寿命能力。
    chamber_wall_allow_pa: float = 120.0e6
    chamber_wall_safety_factor: float = 1.5
    # 主燃烧室几何初估：圆柱燃烧段 + 收缩段 + 喉部圆弧过渡
    # L* = Vcc / At；LOX/LCH4 初步设计常取约 0.8-1.2 m。
    chamber_contraction_ratio: float = 2.5
    chamber_l_star_m: float = 1.0
    chamber_conv_half_angle_deg: float = 30.0   # 收缩段半锥角 β [°]（典型 25–45°）
    # 钟形喷管长度比（相对同喉部/出口 15° 锥形喷管长度；
    # 全链同口径：geometry 轮廓 / cooling_marching / 2D图 / CSV 导出）
    nozzle_bell_fraction: float = 0.80
    nozzle_contour_method: str = "parabolic_bell_explicit_v1"
    nozzle_initial_angle_deg: float = 35.0  # 设计角度示例，非优化/实机数据
    nozzle_exit_angle_deg: float = 10.0
    # 具体材料选择与温度依赖物性由下面的材料字段管理。

    # ---------- 预燃室混合比模式与历史序列化开关 ----------
    # 温度开关优先：temp>=0.5 时由外环反求 MR，下列 MR 字段仅为其初值。
    # temp<0.5 且 mr>=0.5：指定 MR，反求分流与涡轮压比。
    # 两个开关均<0.5：历史自由分流模式，保留兼容，不作为新设计默认。
    use_target_preburner_mr: float = 1.0
    ox_preburner_mr_target: float = 60.0
    fuel_preburner_mr_target: float = 0.13

    # ---------- 预燃室温度目标模式（V11 新增）──────────────────
    # 温度模式适用于给定预燃室出口温度的概念设计；外环在当前入口焓与压力下
    # 反求 MR，并重新检查耦合后的目标误差。它不是比固定 MR 更真实的物理模型。
    # 启用上限时目标必须不超过上限；默认目标与上限均为假设，不声称材料裕度已验证。
    use_target_preburner_temp: float = 1.0
    ox_preburner_temp_target: float = 750.0    # [K] 富氧预燃室出口设计目标
    fuel_preburner_temp_target: float = 850.0  # [K] 富燃预燃室出口设计目标

    # ---------- 流量分配（自由分流模式初始猜测）----------
    # 仅在温度和混合比目标模式均关闭时作为求解初值。
    fuel_split_to_ox_preburner: float = 0.06
    ox_split_to_ox_preburner: float = 0.97

    # ---------- 节流参数（V6 新增）----------
    throttle_min: float = 0.40      # 申请扫描的最低推力比例 [-]，不代表已验证节流能力
    throttle_steps: int = 5         # 节流扫描点数

    # ---------- 涡轮泵转速参数（V10 新增）──────────────────
    # 概念选型规则，不是部件特性图联立匹配：
    #   1) override_rpm > 0 时直接采用给定转速。
    #   2) override_rpm = 0 时按 Ns_target 与每级扬程、体积流量反推转速。
    #   3) 涡轮转速 = 同轴泵转速；ψ 参与几何估算，不参与该转速反推公式。
    #
    # 比转速 Ns（量纲式，N 用 rpm，Q 用 m³/s，H 用 m，per-stage）：
    #   Ns = N·sqrt(Q)/H_per_stage^0.75，依赖单位约定，不是无量纲比转速。
    #
    # 压头系数 ψ = g·H / u_tip²：
    #   高压离心泵典型 0.45–0.55，更小越接近轴流
    #
    # LOX 泵示例设计设定，未以实机转速、叶轮或特性图验证。
    lox_pump_speed_rpm_override: float = 26500.0   # [rpm] 锁定；置 0 改回按 nq 反推
    lox_pump_specific_speed_target: float = 30.0   # nq(EU)=N·√Q/H^0.75（与 CFTurbo nq(EU) 同口径）
    lox_pump_n_stages: int = 1                  # 用户指定级数假设
    lox_pump_head_coefficient_psi: float = 0.50

    # 燃料泵示例设计设定；不据此声称机械裕度或效率随转速变化已验证。
    fuel_pump_speed_rpm_override: float = 38000.0
    fuel_pump_specific_speed_target: float = 30.0
    fuel_pump_n_stages: int = 2                 # 用户指定级数假设
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
    # 用户给定的速度筛查限值；不由材料名自动得到，也不等于转子强度验证。
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
    # 历史结构筛查配置：内衬与外套。外套并不必然为镍基合金。
    # 材料 key 必须存在于 materials.MATERIALS 且 parts 含 chamber_inner_wall /
    # chamber_outer_wall。默认沿用 materials.DEFAULT_CHAMBER_WALL_MATERIALS。
    #
    # 冷却导热改由 wall_thermal_dataset_id / 轴向分区驱动。历史结构库的
    # 简化强度数据独立管理；未匹配热物性材料/状态时不生成内衬安全系数。
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
    # Fuel choice is part of the immutable design input. Kerosene uses an
    # explicitly identified n-dodecane surrogate, initially in GG cycles only.
    propellant_pair: str = "lox_methane"

    # ---------- 燃气发生器循环参数（V20 新增，cycle_type="gg" 时生效）────
    # 轴系布局：
    #   single_shaft = 单涡轮同轴驱动双泵（Merlin/F-1 经典布局，默认）
    #   dual_shaft   = GG 燃气并联分流至两台涡轮，各自驱动一侧泵（Vulcain 2 类）
    gg_layout: str = "single_shaft"
    # GG 混合比（富燃）：典型 0.2–0.45，对应出口温度 ~700–1000 K。
    # use_target_gg_temp=1 时此值仅作外环初值，实际 MR 由目标温度反求。
    gg_mr_target: float = 0.30
    use_target_gg_temp: float = 1.0     # 1=按用户给定的 GG 出口温度目标反求 MR
    gg_temp_target: float = 850.0       # GG 出口(=涡轮入口)目标温度 [K]（富燃甲烷 GG 典型 800–950）
    gg_temp_limit: float = 950.0        # 用户指定 GG 温度上限 [K]
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

    # Numerical method only: both backends solve the same physical residuals.
    # Appended to preserve the positional order of existing serialized inputs.
    nonlinear_solver: str = "newton"

    # Solid thermal data are separate from legacy structural material records.
    # These appended fields preserve old positional input ordering. Empty zones
    # apply the default dataset to the entire cooled liner; state is not assumed.
    wall_thermal_dataset_id: str = ""
    wall_manufacturing_state: str = "unspecified"
    wall_material_zones_json: str = ""

    @classmethod
    def from_dict(cls, values):
        """Load saved input documents without changing the historical contour."""
        copied = dict(values)
        copied.setdefault("nozzle_contour_method", "legacy_log_v1")
        return cls(**copied)

    def __post_init__(self):
        """输入参数物理范围校验。"""

        if self.nonlinear_solver not in ("newton", "trf"):
            raise ValidationError("nonlinear_solver 必须是 newton 或 trf。")

        from wall_materials import get_wall_dataset, default_dataset_for_material
        from wall_thermal import parse_material_zones, validate_manufacturing_state
        try:
            if self.wall_thermal_dataset_id == "" and self.cooling_bartz_mode:
                self.wall_thermal_dataset_id = default_dataset_for_material(self.chamber_inner_wall_material)
            if self.wall_thermal_dataset_id:
                get_wall_dataset(self.wall_thermal_dataset_id)
            validate_manufacturing_state(self.wall_manufacturing_state)
            parse_material_zones(self.wall_material_zones_json)
        except (ValueError, TypeError, KeyError) as exc:
            raise ValidationError(f"壁面热物性设置无效：{exc}") from exc

        if self.propellant_pair not in ("lox_methane", "lox_kerosene"):
            raise ValidationError("propellant_pair 必须是 lox_methane 或 lox_kerosene。")
        if self.propellant_pair == "lox_kerosene":
            if self.cycle_type != "gg":
                raise ValidationError("液氧/煤油代理模型目前支持 GG 燃气发生器循环，尚不支持 FFSC。")
            if self.cooling_bartz_mode >= 0.5:
                raise ValidationError("液氧/煤油代理模型目前采用给定吸热量的再生冷却；请将 cooling_bartz_mode 设为 0，不能使用甲烷 Bartz 关联式。")
            _check_range("fuel_inlet_temp（正十二烷代理物性范围）",
                         self.fuel_inlet_temp, lo=263.6, hi=700.0)

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
        if self.combustion_loss_model not in ("legacy_temperature_rise", "adiabatic"):
            raise ValidationError("combustion_loss_model 必须是 adiabatic 或 legacy_temperature_rise。")
        if self.methane_thermo_dataset not in ("gri30", "gri30_highT"):
            raise ValidationError("methane_thermo_dataset 必须是 gri30 或 gri30_highT。")
        _check_range("combustion_efficiency（主室温升折减系数）",
                     self.combustion_efficiency, lo=0, hi=1.0, lo_inclusive=False)
        _warn_range("combustion_efficiency（主室温升折减系数）",
                    self.combustion_efficiency, 0.95, 1.0)

        # ── 膨胀比（必须 ≥ 1）──────────────────────────────
        _check_range("expansion_ratio（膨胀比）", self.expansion_ratio, lo=1.0)
        _warn_range("expansion_ratio（膨胀比）",  self.expansion_ratio, 2.0, 300.0)

        # ── 温度（必须 > 0 K）─────────────────────────────
        _check_range("fuel_inlet_temp（燃料贮箱出口温度）", self.fuel_inlet_temp, lo=0, lo_inclusive=False)
        _check_range("ox_inlet_temp（氧化剂贮箱出口温度）", self.ox_inlet_temp,   lo=0, lo_inclusive=False)

        # ── 泵效率（硬约束：0 < η ≤ 1）──────────────────
        _check_range("pump_efficiency_ox（LOX泵效率）",
                     self.pump_efficiency_ox,   lo=0, hi=1.0, lo_inclusive=False)
        _check_range("pump_efficiency_fuel（燃料泵效率）",
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
        from geometry.nozzle import CONTOUR_METHODS, _bezier_definition
        if self.nozzle_contour_method not in CONTOUR_METHODS:
            raise ValidationError("未知的喷管型面方法。")
        # Geometry compatibility is dimensionless and independent of throat sizing.
        try:
            _bezier_definition(1., math.sqrt(self.expansion_ratio), self.expansion_ratio,
                self.nozzle_bell_fraction, nozzle_contour_method=self.nozzle_contour_method,
                nozzle_initial_angle_deg=self.nozzle_initial_angle_deg,
                nozzle_exit_angle_deg=self.nozzle_exit_angle_deg)
            if self.nozzle_contour_method == "parabolic_bell_explicit_v1":
                from chamber_contour import convergent_geometry
                cg = convergent_geometry(math.sqrt(self.chamber_contraction_ratio), 1.,
                                         self.chamber_conv_half_angle_deg)
                if cg["R1_m"] <= 1e-12:
                    raise ValueError("圆筒入口过渡圆角退化为尖角；请增大收缩比或减小收缩角。")
        except (ValueError, TypeError, OverflowError) as exc:
            raise ValidationError(str(exc)) from exc
        _check_range("cooling_gas_emissivity（燃气辐射发射率）", self.cooling_gas_emissivity, lo=0.0, hi=0.60)
        # 通道分布表：仅做 JSON 可解析性校验（结构校验在 chamber_design.parse_channel_table）
        if isinstance(self.cooling_channel_table_json, str) and self.cooling_channel_table_json.strip():
            import json as _json
            try:
                _json.loads(self.cooling_channel_table_json)
            except Exception as exc:
                raise ValidationError(f"cooling_channel_table_json 不是有效 JSON：{exc}")
        _check_range("cooling_marching_enable（marching 开关）", self.cooling_marching_enable, lo=0.0, hi=1.0)
        _check_range("cooling_grid_points（冷却网格点数）", self.cooling_grid_points, lo=24, hi=1000)
        if int(self.cooling_grid_points) != self.cooling_grid_points:
            raise ValidationError("cooling_grid_points 必须为整数。")
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

        # ── 可选的 FFSC 预燃室气体温度约束 ──────────────
        # 数值范围不代表材料许可范围；GG 不使用这两侧隐藏约束。
        if self.cycle_type == "ffsc":
            for side in ("ox", "fuel"):
                constraint = preburner_temperature_constraint(self, side)
                key = f"{side}_preburner_temp_limit"
                if constraint["status"] not in PREBURNER_LIMIT_STATUSES:
                    raise ValidationError(f"{key}_status 必须是 unset、provisional 或 documented。")
                if constraint["status"] == "documented" and not constraint["basis"]:
                    raise ValidationError(f"{key}_basis：已有依据状态必须填写该限值的适用条件和来源。")
                if constraint["status"] != "unset":
                    limit = constraint["limit_K"]
                    if not isinstance(limit, (int, float)) or not math.isfinite(limit) or limit <= 0:
                        raise ValidationError(f"{key} 必须是大于 0 K 的有限数值。")

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
        # 下列宽范围是当前求解器支持域，不是工程推荐值或材料温度依据。
        # 温度模式实际使用 MR 初始化循环；GG 与历史自由分流不使用这两项。
        if self.cycle_type == "ffsc" and (
                self.use_target_preburner_temp >= 0.5 or self.use_target_preburner_mr >= 0.5):
            role = "O/F 初值" if self.use_target_preburner_temp >= 0.5 else "指定 O/F"
            for side, label, lower, upper in (("ox", "富氧", 5.0, 200.0),
                                               ("fuel", "富燃", 0.01, 2.0)):
                value = getattr(self, f"{side}_preburner_mr_target")
                name = f"{side}_preburner_mr_target（{label}预燃室{role}；当前求解器支持范围）"
                if not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValidationError(f"{name} 必须是有限数值。")
                _check_range(name, value, lo=lower, hi=upper)

        # ── 预燃室温度目标模式（V11 新增）──────────────────
        _check_range("use_target_preburner_temp（温度目标模式开关）",
                     self.use_target_preburner_temp, lo=0.0, hi=1.0)
        if self.cycle_type == "ffsc" and self.use_target_preburner_temp >= 0.5:
            for side, label in (("ox", "富氧"), ("fuel", "富燃")):
                target = getattr(self, f"{side}_preburner_temp_target")
                if not isinstance(target, (int, float)) or not math.isfinite(target) or target <= 0:
                    raise ValidationError(f"{side}_preburner_temp_target 必须是大于 0 K 的有限数值。")
                limit = preburner_temperature_constraint(self, side)["limit_K"]
                if limit is not None and target > limit:
                    raise ValidationError(
                        f"{side}_preburner_temp_target = {target} K "
                        f"> {side}_preburner_temp_limit = {limit} K，"
                        f"目标温度不能超过{label}侧已启用的温度上限。"
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
            ("fuel_pump_speed_rpm_override（燃料泵转速锁定）", self.fuel_pump_speed_rpm_override),
        ]:
            _check_range(nm, val, lo=0.0)
            if val > 0:
                _warn_range(nm, val, 5000.0, 80000.0)
        _check_range("lox_pump_specific_speed_target（LOX Ns 目标）",
                     self.lox_pump_specific_speed_target, lo=5.0, hi=200.0)
        _check_range("fuel_pump_specific_speed_target（燃料 Ns 目标）",
                     self.fuel_pump_specific_speed_target, lo=5.0, hi=200.0)
        _warn_range("lox_pump_specific_speed_target（LOX Ns 目标）",
                    self.lox_pump_specific_speed_target, 15.0, 80.0)
        _warn_range("fuel_pump_specific_speed_target（燃料 Ns 目标）",
                    self.fuel_pump_specific_speed_target, 15.0, 80.0)
        if not isinstance(self.lox_pump_n_stages, int) or self.lox_pump_n_stages < 1:
            self.lox_pump_n_stages = max(1, int(self.lox_pump_n_stages))
        if not isinstance(self.fuel_pump_n_stages, int) or self.fuel_pump_n_stages < 1:
            self.fuel_pump_n_stages = max(1, int(self.fuel_pump_n_stages))
        _check_range("lox_pump_n_stages（LOX 泵级数）",
                     float(self.lox_pump_n_stages), lo=1, hi=6)
        _check_range("fuel_pump_n_stages（燃料泵级数）",
                     float(self.fuel_pump_n_stages), lo=1, hi=6)
        _check_range("lox_pump_head_coefficient_psi（LOX 压头系数 ψ）",
                     self.lox_pump_head_coefficient_psi, lo=0.2, hi=0.8)
        _check_range("fuel_pump_head_coefficient_psi（燃料压头系数 ψ）",
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
                f"{self.gg_temp_limit} K，GG 目标温度不能超过指定温度上限"
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
    metadata: Dict[str, object] = field(default_factory=dict)


# =========================================
# 3️⃣ 部件结果层（Component Layer）
# =========================================

@dataclass
class ComponentResult:
    """单个部件的计算结果"""

    name: str
    outputs: Dict[str, float] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    profile: Dict[str, object] | None = None


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
    # Versioned solver evidence; empty on legacy records (never implies success).
    convergence:     dict = field(default_factory=dict)
    # Versioned boundary/equation evidence. Legacy records have no closure proof.
    steady_closure:  dict = field(default_factory=dict)
    thermodynamic_applicability: dict = field(default_factory=dict)
