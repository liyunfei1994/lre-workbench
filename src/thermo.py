"""
thermo.py

热力学计算封装层（Thermodynamic Abstraction Layer）

本模块的职责：
─────────────────────────────────────────────────────────
    统一管理所有气相化学平衡 / 等熵膨胀 / 热物性查询，
    对外暴露纯 Python 数据结构，隐藏 Cantera 细节。

    两种运行模式（自动检测，无需手动切换）：

    ┌────────────────────────────────────────────────┐
    │  Cantera 可用 → 高保真模式                     │
    │    · 基于 GRI-Mech 3.0 的化学平衡              │
    │    · 真实 cp(T,p,Y)、γ(T,p,Y)                 │
    │    · 等熵膨胀基于真实状态方程                   │
    ├────────────────────────────────────────────────┤
    │  Cantera 不可用 → 经验 fallback 模式           │
    │    · 分段多项式拟合温度                         │
    │    · 固定 cp、γ 常数                           │
    │    · 理想气体等熵公式                           │
    └────────────────────────────────────────────────┘

依赖：
    · cantera（可选）：pip install cantera
    · core.FlowState：用于主燃烧室混合计算的输入

接口保持稳定：下游 preburner / chamber / nozzle 只调用本模块函数，
不直接 import cantera，便于未来替换为其他求解器（如 CEA、Mutation++）。
"""

from __future__ import annotations

from task_control import checkpoint

import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

# ─── Cantera 可用性检测 ────────────────────────────────────
try:
    import cantera as ct
    _GAS: Optional[ct.Solution] = None

    def _get_gas() -> ct.Solution:
        """获取模块级 Cantera Solution 对象（懒初始化，避免重复解析机制文件）"""
        checkpoint()
        global _GAS
        if _GAS is None:
            # Cantera's native Windows file lookup can lose non-ASCII path
            # characters. Read its unchanged bundled mechanism through Python's
            # Unicode-aware path handling and pass the exact YAML contents.
            mechanism_file = Path(ct.__file__).resolve().parent / "data" / "gri30.yaml"
            _GAS = ct.Solution(yaml=mechanism_file.read_text(encoding="utf-8"))
            checkpoint()
        return _GAS

    CANTERA_AVAILABLE = True
except ImportError:
    CANTERA_AVAILABLE = False


# ─── 机制名称（可配置） ─────────────────────────────────────
MECHANISM = "gri30.yaml"
"""
GRI-Mech 3.0：53 物种、325 反应
优点：Cantera 自带、CH₄/O₂ 覆盖完整
局限：高压（>10 MPa）下验证有限，但平衡计算受影响小
后续升级建议：FFCM-1 或 AramcoMech
"""


# ─── CoolProp 可用性检测（用于 LOX/LCH4 真实流体焓修正） ──────────────
# GRI-Mech 的物种焓由 NASA 7 系数多项式给出，**仅适用气相**。
# 当入口处于液态/超临界稠密相（LOX@90K、LCH4@110K）时，多项式向低温外推
# 会漏掉汽化潜热（O2 ~213 kJ/kg, CH4 ~510 kJ/kg）及液-气 cp 差，从而高估
# 入口焓 → HP 平衡解出的预燃室温度系统性偏高约 +130–160 K，沿涡轮排气
# 传到主燃烧室还会再叠加 ~70–90 K Tc 偏高。
# CoolProp 提供 NIST 级别的真实流体物性，用来给入口焓打个修正项即可。
try:
    from CoolProp.CoolProp import PropsSI as _CP_PropsSI
    COOLPROP_AVAILABLE = True
except ImportError:
    COOLPROP_AVAILABLE = False
    _CP_PropsSI = None

# 物种 → CoolProp 流体名映射
_COOLPROP_FLUID: Dict[str, str] = {"O2": "Oxygen", "CH4": "Methane"}

# CoolProp 不可用时的备用修正 (J/kg)：基于 LOX@90K/30MPa, LCH4@110K/30MPa
# 实测的 (h_real − h_NASA_extrap)，作为常数兜底。低于阈值才生效，高于即气相。
_FALLBACK_DH_CORRECTION_JKG: Dict[str, float] = {"O2": -197.0e3, "CH4": -470.0e3}
_FALLBACK_T_THRESHOLD_K:     Dict[str, float] = {"O2": 130.0,    "CH4": 150.0}

# 兜底饱和蒸汽压 [Pa]（CoolProp 不可用时用）：过冷致密推进剂蒸汽压很低，
# 取常压沸点附近量级；用于 NPSHa 计算的保守占位。
_FALLBACK_PVAP_PA: Dict[str, float] = {"O2": 6.3e3, "CH4": 3.4e4}


def vapor_pressure(species: str, T_K: float) -> float:
    """
    饱和蒸汽压 p_sat(T) [Pa]，用于泵 NPSHa 计算。

    · CoolProp 可用：返回 NIST 级真实饱和压（O2@70K≈6.3 kPa, CH4@100K≈34 kPa）。
    · 超过临界温度或查询失败：退回固定兜底常数（深过冷推进剂蒸汽压本就很低，
      取常压沸点量级即可，对 NPSHa 影响极小）。

    NPSHa = (p_pump_inlet − p_vapor) / (ρ·g)，必须在 *泵入口* 处评估（已扣管路损失）。
    """
    checkpoint()
    fluid_key = "O2" if species in ("O2", "LOX", "Oxygen") else (
        "CH4" if species in ("CH4", "Methane") else species
    )
    if COOLPROP_AVAILABLE and fluid_key in _COOLPROP_FLUID:
        try:
            return float(_CP_PropsSI("P", "T", T_K, "Q", 0, _COOLPROP_FLUID[fluid_key]))
        except Exception:
            pass
    return _FALLBACK_PVAP_PA.get(fluid_key, 1.0e3)


def _real_fluid_enthalpy(
    species: str, T_K: float, p_Pa: float, gas: "ct.Solution"
) -> float:
    """
    返回入口推进剂的"真实流体焓" [J/kg]，**参考态对齐到 Cantera NASA 基准**。

    用法
    ────
        h = _real_fluid_enthalpy("O2", 90.0, 30e6, gas)
    等价替代:
        gas.TPX = 90.0, 30e6, "O2:1"; h = gas.enthalpy_mass
    但自动包含液相 / 超临界稠密相的真实焓修正。

    原理
    ────
    令参考态 (T_ref, p_ref) = (298.15 K, 1 atm)，该工况下 NASA 多项式
    与 CoolProp 都给出准确气相焓，两者参考零点差可在差分中消除：

        Δh_real = h_CP(T,p)   − h_CP(T_ref,p_ref)        ← CoolProp 真实路径
        Δh_NASA = h_NASA(T,p) − h_NASA(T_ref,p_ref)      ← Cantera 气相外推
        correction = Δh_real − Δh_NASA                   ← 应叠加到 NASA 焓上
        h_real(T,p) ≈ h_NASA(T,p) + correction

    关键修正（V12，修复 TH-01/TH-03）：CoolProp 真实焓 departure 在 NASA 适用区
    （低压/高温气相）会自然趋零，故**对所有 T 都安全计算**，不再用硬温度阈值早退。
    这同时修复了两件事：
      ① 再生冷却后的超临界稠密甲烷（~226 K, 74 MPa）原先因 T>150K 被当气相、
         漏掉约 -285 kJ/kg 真实焓差，使富燃预燃室温度系统性高估 ~+65 K；
      ② 旧阈值在 130K/150K 处造成的 ~155 kJ/kg 焓阶跃（破坏 Newton-Jacobian 光滑性）。
    硬温度阈值现仅用于 CoolProp 不可用时的"固定常数兜底"——该常数只在稠密相标定，
    高温气相区不可外推，故气相区返回纯 NASA 焓。
    """
    # 先把 gas 置于入口态，读出 NASA 多项式焓（无修正）
    checkpoint()
    gas.TPX = T_K, p_Pa, f"{species}:1"
    h_nasa_in = gas.enthalpy_mass

    # ── 高保真路径：CoolProp 真实流体 departure（对所有 T 计算，连续无阶跃）──────
    # departure = (h_CP(T,p) − h_CP(ref)) − (h_NASA(T,p) − h_NASA(ref))
    # 稠密相（液/超临界）为显著负值；低压高温气相自然趋零。
    if COOLPROP_AVAILABLE and species in _COOLPROP_FLUID:
        try:
            fluid = _COOLPROP_FLUID[species]
            T_ref, p_ref = 298.15, 101_325.0

            h_cp_in  = _CP_PropsSI("H", "T", T_K,   "P", p_Pa,  fluid)
            h_cp_ref = _CP_PropsSI("H", "T", T_ref, "P", p_ref, fluid)

            gas.TPX = T_ref, p_ref, f"{species}:1"
            h_nasa_ref = gas.enthalpy_mass

            # 还原 gas 到入口态，免得调用方依赖 gas 的"上次状态"
            gas.TPX = T_K, p_Pa, f"{species}:1"

            correction = (h_cp_in - h_cp_ref) - (h_nasa_in - h_nasa_ref)
            return h_nasa_in + correction
        except Exception as ex:
            # CoolProp 出错时记录一次并回落到固定修正
            warnings.warn(
                f"CoolProp 计算 {species}@({T_K}K,{p_Pa/1e6:.1f}MPa) 失败:{ex}; "
                f"回落到固定修正 {_FALLBACK_DH_CORRECTION_JKG.get(species,0)/1e3:.0f} kJ/kg。",
                RuntimeWarning,
            )

    # ── 回退路径（CoolProp 不可用）：固定修正常数，仅在稠密相阈值以下生效 ──────
    # 常数只在稠密相标定，高温气相不可外推，故此处保留温度阈值门控。
    if T_K <= _FALLBACK_T_THRESHOLD_K.get(species, 1.0e9):
        return h_nasa_in + _FALLBACK_DH_CORRECTION_JKG.get(species, 0.0)
    return h_nasa_in


# =========================================================
# 输出数据结构
# =========================================================

@dataclass
class GasState:
    """
    气体热力学状态（预燃室 / 主燃烧室 / 喷管出口均用此描述）

    所有字段以 SI 单位为准。
    composition 中仅保留质量分数 > 1e-10 的物种，以减少序列化开销。
    """
    temperature: float              # [K]
    pressure: float                 # [Pa]
    cp: float                       # 定压比热 [J/(kg·K)]
    gamma: float                    # 比热比 [-]
    molar_mass: float               # 平均摩尔质量 [kg/kmol]
    enthalpy: float                 # 质量比焓 [J/kg]
    entropy: float = 0.0            # 质量比熵 [J/(kg·K)]，Cantera 模式可用
    composition: Dict[str, float] = field(default_factory=dict)
    source: str = "empirical"       # "cantera" 或 "empirical"


# =========================================================
# 1. 预燃室 / 主燃烧室 化学平衡
# =========================================================

def combustion_equilibrium(
    mdot_fuel: float,
    mdot_ox: float,
    T_fuel: float,
    T_ox: float,
    pressure: float,
) -> GasState:
    """
    计算 LOX/CH₄ 预燃室绝热定压（HP）化学平衡。

    参数
    ────
    mdot_fuel : 燃料（CH₄）质量流量 [kg/s]
    mdot_ox   : 氧化剂（O₂）质量流量 [kg/s]
    T_fuel    : 燃料入口温度 [K]
    T_ox      : 氧化剂入口温度 [K]
    pressure  : 燃烧压力 [Pa]

    返回
    ────
    GasState  : 平衡状态
    """
    checkpoint()
    if CANTERA_AVAILABLE:
        return _cantera_combustion(mdot_fuel, mdot_ox, T_fuel, T_ox, pressure)
    else:
        return _empirical_combustion(mdot_fuel, mdot_ox, T_fuel, T_ox, pressure)


def chamber_equilibrium(
    ox_exhaust_T: float,
    ox_exhaust_mdot: float,
    ox_exhaust_composition: Dict[str, float],
    fuel_exhaust_T: float,
    fuel_exhaust_mdot: float,
    fuel_exhaust_composition: Dict[str, float],
    pressure: float,
    gamma_fallback: float = 1.22,
    molar_mass_fallback: float = 22.0,
    temp_guess: float = 3600.0,
    efficiency: float = 1.0,
) -> GasState:
    """
    计算主燃烧室两股排气混合后的化学平衡。

    参数
    ────
    ox_exhaust_*   : 富氧涡轮排气（温度、流量、质量分数组成）
    fuel_exhaust_* : 富燃涡轮排气（温度、流量、质量分数组成）
    pressure       : 主燃烧室压力 [Pa]
    gamma_fallback : Cantera 不可用时的 γ 估计值
    molar_mass_fallback : Cantera 不可用时的 MW 估计值
    temp_guess     : Cantera 不可用时的温度猜测值
    efficiency     : 燃烧效率 η_comb ∈ (0,1]。把理想平衡温度按"燃烧温升效率"
                     折减为真实主流温度：T_real = T_mix + η·(T_ideal − T_mix)，
                     T_mix 为两股排气的质量加权掺混温度（燃烧前参考）。
                     1.0 = 理想完全燃烧（旧行为）。仅作用于温度，组分/压力不变，
                     故下游 c*/Isp（由本态重建）与热流自动一致跟随，无双重计数。

    返回
    ────
    GasState  : 主燃烧室平衡状态（已按 efficiency 折减温度）
    """
    checkpoint()
    if CANTERA_AVAILABLE:
        return _cantera_chamber(
            ox_exhaust_T, ox_exhaust_mdot, ox_exhaust_composition,
            fuel_exhaust_T, fuel_exhaust_mdot, fuel_exhaust_composition,
            pressure, efficiency=efficiency,
        )
    else:
        return _empirical_chamber(
            ox_exhaust_T, ox_exhaust_mdot,
            fuel_exhaust_T, fuel_exhaust_mdot,
            pressure, gamma_fallback, molar_mass_fallback, temp_guess,
            efficiency=efficiency,
        )


def gg_chamber_equilibrium(
    mdot_fuel: float,
    mdot_ox: float,
    T_fuel: float,
    T_ox: float,
    pressure: float,
    efficiency: float = 1.0,
) -> GasState:
    """
    燃气发生器循环主燃烧室：**液-液** LOX/CH₄ HP 化学平衡（V20 新增）。

    与 chamber_equilibrium（FFSC 气-气掺混）的区别
    ─────────────────────────────────────────────
    FFSC 主燃室入口是两股热排气，组分/焓走气相 NASA 多项式没有问题；
    GG 主燃室入口是 **低温液体推进剂**（LOX ~85 K，CH4 ~120–130 K），
    必须走与预燃室同源的 combustion_equilibrium() —— 其 Cantera 路径
    内置 CoolProp 真实流体焓修正（漏掉汽化潜热会把 Tc 系统性抬高 ~100 K+）。

    efficiency < 1 时按燃烧温升效率折减温度（与 _cantera_chamber 同口径）：
        T_real = T_mix + η·(T_ideal − T_mix)
    T_mix 取两股入口的质量加权温度（燃烧前参考）；仅降温、组分/压力不变。
    """
    checkpoint()
    state = combustion_equilibrium(
        mdot_fuel=mdot_fuel, mdot_ox=mdot_ox,
        T_fuel=T_fuel, T_ox=T_ox, pressure=pressure,
    )
    if efficiency >= 1.0:
        return state

    mdot_total = max(mdot_fuel + mdot_ox, 1e-9)
    T_mix = (mdot_fuel * T_fuel + mdot_ox * T_ox) / mdot_total
    T_real = T_mix + efficiency * (state.temperature - T_mix)

    if CANTERA_AVAILABLE and state.source == "cantera":
        # 组分冻结在平衡解，把温度折减到 T_real 后由状态方程自洽重算 cp/γ/h/s
        gas = _get_gas()
        Y = _dict_to_Y(gas, state.composition)
        gas.TPY = T_real, pressure, Y
        return _gas_to_state(gas, pressure)

    # 经验回退：只改温度（cp/γ 经验值本就近似）
    from dataclasses import replace as _dc_replace
    return _dc_replace(state, temperature=T_real)


# =========================================================
# 2. 涡轮等熵膨胀
# =========================================================

def turbine_expansion(
    combustion_state: GasState,
    exhaust_pressure: float,
    eta_turbine: float,
) -> dict:
    """
    计算涡轮等熵膨胀过程。

    参数
    ────
    combustion_state : 预燃室出口（涡轮入口）气体状态
    exhaust_pressure : 涡轮排气压力 [Pa]
    eta_turbine      : 涡轮等熵效率 [-]

    返回
    ────
    dict:
        isentropic_enthalpy_drop [J/kg]
        actual_enthalpy_drop     [J/kg]
        T_exhaust                [K]
        h_exhaust                [J/kg]
        composition              {species: Y}
    """
    checkpoint()
    if CANTERA_AVAILABLE and combustion_state.source == "cantera":
        return _cantera_turbine(combustion_state, exhaust_pressure, eta_turbine)
    else:
        return _empirical_turbine(combustion_state, exhaust_pressure, eta_turbine)


# =========================================================
# 3. 喷管等熵膨胀
# =========================================================

def nozzle_exit_state(
    chamber_state: GasState,
    exit_pressure: float,
) -> GasState:
    """
    计算喷管出口等熵膨胀后的气体状态（冻结流）。

    用于配合 nozzle.py 计算精确的 Ve 和推力。
    仅 Cantera 模式可用，经验模式返回 None。
    """
    checkpoint()
    if CANTERA_AVAILABLE and chamber_state.source == "cantera":
        return _cantera_nozzle_expansion(chamber_state, exit_pressure)
    return None


def nozzle_performance(
    chamber_state: GasState,
    p_chamber: float,
    eps: float,
    p_amb: float = 0.0,
    equilibrium: bool = False,
) -> "dict | None":
    """
    喷管性能(出口压力按面积比自洽求解),返回 C*、Ve、pe、Te、真空/海平面 Isp。

    主引擎默认用 equilibrium=False(冻结流):
      · 出口压力 p_e 由 ρV(p_e)=ṁ/A_t/ε 自洽确定,不再借用理想 γ 估计;
      · 冻结流速度快(~10ms),可放在 Newton 关键路径;
      · 物理上更接近真实喷管(有限速率反应会冻结),且更保守。
    交叉验证 / 理论上界用 equilibrium=True(平衡流,与 NASA CEA 同口径)。

    仅 Cantera 模式可用,经验模式返回 None(由调用方走理想气体 fallback)。
    """
    checkpoint()
    if CANTERA_AVAILABLE and chamber_state.source == "cantera":
        return _cantera_nozzle_expand(
            chamber_state, p_chamber, eps,
            equilibrium=equilibrium, p_amb=p_amb,
            n_iter=(60 if equilibrium else 40),
        )
    return None


# =========================================================
# Cantera 实现
# =========================================================

def _cantera_combustion(
    mdot_fuel: float, mdot_ox: float,
    T_fuel: float, T_ox: float, pressure: float,
) -> GasState:
    """
    Cantera HP 化学平衡。

    步骤
    ────
    1. 用 _real_fluid_enthalpy() 算 CH₄ 和 O₂ 在各自入口温度下的真实焓
       （CoolProp 可用时含液相/超临界稠密相焓修正，否则用固定修正常数）
    2. 按质量加权得到混合入口焓
    3. 设置组分 → 设定 HP → 平衡

    与历史版本的差异
    ──────────────────
    旧版直接用 gas.TPX + gas.enthalpy_mass 读 NASA 多项式焓，把 LOX@90K
    当成"气相 O2 外推到 90K"，遗漏汽化潜热 ~213 kJ/kg；LCH4 同理漏掉
    ~510 kJ/kg。HP 平衡的入口焓因此偏高，预燃室 T 系统性偏高 130–160 K。
    本版用 CoolProp 真实流体物性修正这一项，T_PB 应回落到合理量级。
    """
    checkpoint()
    gas = _get_gas()
    mdot_total = mdot_fuel + mdot_ox

    # ── 入口焓（已含 LOX/LCH4 真实流体焓修正） ─────────────
    h_fuel = _real_fluid_enthalpy("CH4", T_fuel, pressure, gas)
    h_ox   = _real_fluid_enthalpy("O2",  T_ox,   pressure, gas)

    Y_fuel_frac = mdot_fuel / max(mdot_total, 1e-9)
    Y_ox_frac   = 1.0 - Y_fuel_frac
    h_inlet = Y_fuel_frac * h_fuel + Y_ox_frac * h_ox

    # ── HP 平衡(快路径 + 二分回退) ──────────────────────────────
    _equilibrate_HP(
        gas, h_target=h_inlet, pressure=pressure,
        Y_init=f"CH4:{Y_fuel_frac}, O2:{Y_ox_frac}",
        context=f"PB(mdot_f={mdot_fuel:.3g},mdot_o={mdot_ox:.3g})",
    )

    return _gas_to_state(gas, pressure)


def _equilibrate_HP(
    gas: "ct.Solution",
    h_target: float,
    pressure: float,
    Y_init,
    context: str = "",
) -> None:
    """
    在给定 (h, p) 下做化学平衡,**快路径优先,失败回退到二分**。

    快路径:先把组分预平衡到合理产物,再调 Cantera 内置 setState_HP +
    equilibrate("HP")。绝大多数工况(包括 Raptor 类 FFSC 默认参数)能直接
    在 ~1ms 内完成。仅当 setState_HP 的 Newton 飞过头(极富燃 / h 触底)
    或结果不合理(T 越界 / 焓不匹配)时,才走 _equilibrate_HP_bisection,
    多花 ~10ms 但保证收敛。

    副作用:gas 最终停在收敛态。
    """
    # ── 快路径 ─────────────────────────────────────────────
    checkpoint()
    try:
        # 1) 用 3000 K 把组分先 TP 平衡到产物,setState_HP 才能稳定收敛
        gas.TPY = 3000.0, pressure, Y_init
        checkpoint()
        gas.equilibrate("TP")
        checkpoint()

        # 2) 设目标 (h, p) 并做最终 HP 化学平衡
        gas.HP = h_target, pressure
        checkpoint()
        gas.equilibrate("HP")
        checkpoint()

        # 3) 健全性校验:T 合理 + 焓与目标一致
        T_ok = 200.0 < gas.T < 6000.0
        h_tol = max(1.0e3, 1e-4 * abs(h_target))
        h_ok = abs(gas.enthalpy_mass - h_target) < h_tol
        if T_ok and h_ok:
            return
    except Exception:
        pass  # 任何异常都走回退路径

    # ── 回退:T 域二分 ─────────────────────────────────────
    _equilibrate_HP_bisection(
        gas, h_target=h_target, pressure=pressure, Y_init=Y_init,
        context=context,
    )


def _equilibrate_HP_bisection(
    gas: "ct.Solution",
    h_target: float,
    pressure: float,
    Y_init,
    T_lo: float = 300.0,
    T_hi: float = 5500.0,
    tol_K: float = 1e-2,
    max_iter: int = 60,
    context: str = "",
) -> None:
    """
    在 T 域内对"内层 TP 化学平衡"做二分,直到 h_eq(T,p) = h_target ± tol。

    为什么不用 Cantera 自带 setState_HP:
    ────────────────────────────────
    · 极富燃 (φ>4) / 含液相修正的低 h_target 下,反应物组分的气相 NASA 焓
      达不到 h_target,setState_HP 内置 Newton 在缩温时一步飞负(T→0)。
    · 峰值 Tc 附近(~3500K),自由基离解(H, O, OH)使 d(h)/dT 不光滑,Newton
      震荡不收敛。
    · 二分法只要求 h_eq(T) 单调(物理上一定满足:cp_eq > 0),无须 cp 估计,
      ~30 步即可收敛到 1e-6 K 量级。

    副作用:gas 最终停在收敛态。
    """
    # 1) 建立 Y 的原子约束(equilibrate 会重分配原子,但原子总数守恒)
    checkpoint()
    gas.TPY = 0.5 * (T_lo + T_hi), pressure, Y_init

    # 2) 检查上下界包络 h_target
    gas.TP = T_lo, pressure
    checkpoint()
    gas.equilibrate("TP")
    checkpoint()
    h_lo = gas.enthalpy_mass

    gas.TP = T_hi, pressure
    checkpoint()
    gas.equilibrate("TP")
    checkpoint()
    h_hi = gas.enthalpy_mass

    if h_target <= h_lo:
        warnings.warn(
            f"HP 平衡 {context}: h_target={h_target:.0f} J/kg 低于 T={T_lo}K "
            f"的平衡焓 {h_lo:.0f} J/kg。结果截断到 T={T_lo}K。",
            RuntimeWarning,
        )
        gas.TP = T_lo, pressure
        checkpoint()
        gas.equilibrate("TP")
        checkpoint()
        return
    if h_target >= h_hi:
        warnings.warn(
            f"HP 平衡 {context}: h_target={h_target:.0f} J/kg 高于 T={T_hi}K "
            f"的平衡焓 {h_hi:.0f} J/kg。结果截断到 T={T_hi}K。",
            RuntimeWarning,
        )
        return  # gas 已停在 T_hi

    # 3) 二分
    for _ in range(max_iter):
        checkpoint()
        T_mid = 0.5 * (T_lo + T_hi)
        gas.TP = T_mid, pressure
        checkpoint()
        gas.equilibrate("TP")
        checkpoint()
        if gas.enthalpy_mass < h_target:
            T_lo = T_mid
        else:
            T_hi = T_mid
        if T_hi - T_lo < tol_K:
            break

    # 最后一次 TP 平衡固定到中点
    gas.TP = 0.5 * (T_lo + T_hi), pressure
    checkpoint()
    gas.equilibrate("TP")
    checkpoint()


def _cantera_chamber(
    ox_T: float, ox_mdot: float, ox_comp: Dict[str, float],
    fuel_T: float, fuel_mdot: float, fuel_comp: Dict[str, float],
    pressure: float,
    efficiency: float = 1.0,
) -> GasState:
    """
    两股涡轮排气在主燃烧室混合 → HP 平衡。

    efficiency < 1 时按燃烧温升效率折减温度（见 chamber_equilibrium 文档）。
    """
    checkpoint()
    gas = _get_gas()
    mdot_total = ox_mdot + fuel_mdot
    f_ox   = ox_mdot   / max(mdot_total, 1e-9)
    f_fuel = fuel_mdot / max(mdot_total, 1e-9)

    n_sp = gas.n_species

    # ── 富氧排气焓 ─────────────────────────────────────
    Y_ox = _dict_to_Y(gas, ox_comp)
    gas.TPY = ox_T, pressure, Y_ox
    h_ox = gas.enthalpy_mass

    # ── 富燃排气焓 ─────────────────────────────────────
    Y_fuel = _dict_to_Y(gas, fuel_comp)
    gas.TPY = fuel_T, pressure, Y_fuel
    h_fuel = gas.enthalpy_mass

    # ── 质量加权混合 ───────────────────────────────────
    import numpy as np
    Y_mix = f_ox * np.array(Y_ox) + f_fuel * np.array(Y_fuel)
    h_mix = f_ox * h_ox + f_fuel * h_fuel

    # ── HP 平衡(快路径 + 二分回退) ──────────────────────────────
    _equilibrate_HP(
        gas, h_target=h_mix, pressure=pressure,
        Y_init=[float(v) for v in Y_mix],
        context=f"chamber(ox_mdot={ox_mdot:.3g},fuel_mdot={fuel_mdot:.3g})",
    )

    # ── 燃烧效率：理想平衡温度 → 真实主流温度 ──────────────────
    # T_real = T_mix + η·(T_ideal − T_mix)，T_mix=两股排气质量加权掺混温(燃烧前)。
    # 仅降温、组分与压力不变 → cp/γ/h/s 由 gas.TP 自洽重算；下游喷管由本态
    # 重建，故 c*/Isp 自动跟随，无双重计数。
    if efficiency < 1.0:
        T_ideal = gas.T
        T_mix   = f_ox * ox_T + f_fuel * fuel_T
        T_real  = T_mix + efficiency * (T_ideal - T_mix)
        gas.TP  = T_real, pressure

    return _gas_to_state(gas, pressure)


def _cantera_turbine(
    state: GasState, p_exhaust: float, eta: float,
) -> dict:
    """
    用真实状态方程做等熵膨胀，不依赖理想气体 γ 近似。

    步骤
    ────
    1. 从燃烧状态恢复 Cantera 气体对象
    2. 记录入口焓 h_in 和熵 s_in
    3. 等熵膨胀：固定 S, P → 得到 h_out_isentropic
    4. Δh_s = h_in - h_out_s
    5. Δh_actual = η · Δh_s
    6. h_exhaust = h_in - Δh_actual
    7. 用 HP 反查排气温度
    """
    checkpoint()
    gas = _get_gas()
    Y = _dict_to_Y(gas, state.composition)

    # 入口状态
    gas.TPY = state.temperature, state.pressure, Y
    h_in = gas.enthalpy_mass
    s_in = gas.entropy_mass

    # 等熵膨胀到排气压力（冻结组分）
    gas.SP = s_in, p_exhaust
    h_out_s = gas.enthalpy_mass

    delta_h_s = h_in - h_out_s
    delta_h_actual = delta_h_s * eta
    h_exhaust = h_in - delta_h_actual

    # 由实际排气焓反查温度
    gas.HP = h_exhaust, p_exhaust
    T_exhaust = gas.T

    return {
        "isentropic_enthalpy_drop": delta_h_s,
        "actual_enthalpy_drop": delta_h_actual,
        "T_exhaust": T_exhaust,
        "h_exhaust": h_exhaust,
        "composition": _Y_to_dict(gas),
    }


def _cantera_nozzle_expansion(
    chamber: GasState, p_exit: float,
) -> GasState:
    """
    喷管冻结流等熵膨胀（固定组分，S=const → 给定 p_exit）。
    """
    checkpoint()
    gas = _get_gas()
    Y = _dict_to_Y(gas, chamber.composition)

    gas.TPY = chamber.temperature, chamber.pressure, Y
    s_chamber = gas.entropy_mass

    # 冻结等熵膨胀
    gas.SP = s_chamber, p_exit

    return _gas_to_state(gas, p_exit)


# ─── Cantera 工具函数 ──────────────────────────────────────

def _gas_to_state(gas: "ct.Solution", pressure: float) -> GasState:
    """从 Cantera Solution 对象提取 GasState"""
    checkpoint()
    return GasState(
        temperature=gas.T,
        pressure=pressure,
        cp=gas.cp_mass,
        gamma=gas.cp_mass / max(gas.cv_mass, 1e-3),
        molar_mass=gas.mean_molecular_weight,  # Cantera 单位已是 kg/kmol
        enthalpy=gas.enthalpy_mass,
        entropy=gas.entropy_mass,
        composition=_Y_to_dict(gas),
        source="cantera",
    )


def _Y_to_dict(gas: "ct.Solution") -> Dict[str, float]:
    """Cantera Y 数组 → 精简字典（忽略微量物种）"""
    checkpoint()
    return {
        gas.species_name(i): float(gas.Y[i])
        for i in range(gas.n_species)
        if gas.Y[i] > 1e-10
    }


def _dict_to_Y(gas: "ct.Solution", comp: Dict[str, float]) -> list:
    """质量分数字典 → Cantera Y 数组（未识别物种忽略）"""
    checkpoint()
    import numpy as np
    Y = np.zeros(gas.n_species)
    for name, frac in comp.items():
        checkpoint()
        idx = gas.species_index(name) if name in gas.species_names else -1
        if idx >= 0:
            Y[idx] = frac
    total = Y.sum()
    if total > 0:
        Y /= total   # 归一化
    else:
        # fallback：纯 N2 占位（不应发生）
        Y[gas.species_index("N2")] = 1.0
    return Y.tolist()


# =========================================================
# 经验 Fallback 实现（V7.2 — 质量守恒版）
# =========================================================
#
# 改进说明（对照原 V3 经验版）：
#
# 1. 温度计算改为绝热燃烧温升模型：
#      ΔT = Q_released / (ṁ_total · c̄p_mix)
#    富氧侧（φ < 1）：CH4 完全燃烧，过量 O2 吸热；
#    富燃侧（φ > 1）：O2 全部消耗，大量未反应 CH4 吸热。
#    参考：Sutton §3.3, NASA SP-125 §2.
#
# 2. 组分计算改为基于质量守恒的精确推算：
#    富氧：CH4 + 2O2 → CO2 + 2H2O（按化学计量）
#      质量比：1 kg CH4 + 4 kg O2 → 2.75 kg CO2 + 2.25 kg H2O
#      原始版本的组分分数之和 ≠ 1，已修正。
#    富燃：O2 全部用于燃烧少量 CH4，其余 CH4 原样通过涡轮；
#      原始版本使用了与 O/F 完全无关的固定组分，已修正。
#
# 3. 主燃烧室组分改为 LOX/CH4 在 O/F≈3.5 下
#    Cantera/NASA CEA 参考值（质量分数），并注明来源。
#
# LOX/LCH4 化学计量 O/F = 4.0 （by mass）
_STOICH_OF = 4.0           # kg O2 / kg CH4
_LHV_CH4   = 50.1e6        # J/kg  低热值（甲烷）
_CP_O2_GAS = 1050.0        # J/(kg·K)  气态 O2 高温平均
_CP_CH4_GAS = 2200.0       # J/(kg·K)  气态 CH4 平均


def _empirical_combustion(
    mdot_fuel: float, mdot_ox: float,
    T_fuel: float, T_ox: float, pressure: float,
) -> GasState:
    """
    预燃室绝热燃烧经验模型（V7.2，基于质量守恒）。

    物理假设
    ────────
    · 富氧侧（φ < 1）：CH4 完全燃烧（CO₂ + H₂O），O₂ 过量
    · 富燃侧（φ ≥ 1）：O₂ 全部消耗，多余 CH4 不参与反应
    · 绝热近似：温升 ΔT = Q / (ṁ_total · c̄p_mix)
    · 组分质量分数由化学计量精确计算，之和 = 1
    """
    checkpoint()
    mdot_total = mdot_fuel + mdot_ox
    f_fuel = mdot_fuel / max(mdot_total, 1e-9)   # CH4 质量分数（进口）
    f_ox   = 1.0 - f_fuel                         # O2 质量分数（进口）

    # 质量加权混合进口温度（液态比热）
    CP_LO2  = 1700.0   # J/(kg·K) 液氧低温段
    CP_LCH4 = 3500.0   # J/(kg·K) 液甲烷低温段
    T_mix = (mdot_fuel * CP_LCH4 * T_fuel + mdot_ox * CP_LO2 * T_ox) /             max(mdot_fuel * CP_LCH4 + mdot_ox * CP_LO2, 1e-9)

    # 当量比 φ = (F/Ox)_actual / (F/Ox)_stoich = (mdot_CH4/mdot_O2) / (1/4)
    phi = (mdot_fuel / max(mdot_ox, 1e-9)) * _STOICH_OF

    if phi < 1.0:
        # ── 富氧：CH4 完全燃烧 ──────────────────────────────
        # 化学计量：1 kg CH4 + 4 kg O2 → 2.75 kg CO2 + 2.25 kg H2O
        Y_CO2 = f_fuel * 2.75
        Y_H2O = f_fuel * 2.25
        Y_O2  = max(f_ox - f_fuel * 4.0, 0.0)
        # 归一化（浮点误差保护）
        _s = Y_CO2 + Y_H2O + Y_O2
        if _s > 1e-9:
            Y_CO2 /= _s; Y_H2O /= _s; Y_O2 /= _s
        composition = {"O2": Y_O2, "CO2": Y_CO2, "H2O": Y_H2O}

        # 热容（燃烧后高温气体，O2 为主）
        cp    = _CP_O2_GAS * Y_O2 + 1800.0 * (Y_CO2 + Y_H2O)
        gamma = 1.30 - 0.04 * (1.0 - Y_O2)   # 富氧气体 γ ≈ 1.26–1.30
        mw    = 1.0 / max(Y_O2/32.0 + Y_CO2/44.0 + Y_H2O/18.0, 1e-9)

        # 绝热温升：燃烧放热加热过量 O2
        Q         = f_fuel * _LHV_CH4              # J/kg_total
        cp_mix    = Y_O2 * _CP_O2_GAS + (Y_CO2 + Y_H2O) * 1500.0
        delta_T   = Q / max(cp_mix, 1.0) * 0.88   # 0.88 修正非完全绝热
        T_comb    = max(T_mix + delta_T, 350.0)

    else:
        # ── 富燃：O2 全部消耗，大量 CH4 不参与反应 ──────────
        # 每 1 kg 混合物中：f_ox kg O2 消耗 f_ox/4 kg CH4
        ch4_burned     = f_ox / _STOICH_OF           # 参与燃烧的 CH4 [kg/kg_mix]
        ch4_remaining  = max(f_fuel - ch4_burned, 0.0)
        Y_CO2 = ch4_burned * 2.75
        Y_H2O = ch4_burned * 2.25
        Y_CH4 = ch4_remaining
        # 归一化
        _s = Y_CH4 + Y_CO2 + Y_H2O
        if _s > 1e-9:
            Y_CH4 /= _s; Y_CO2 /= _s; Y_H2O /= _s
        composition = {"CH4": Y_CH4, "CO2": Y_CO2, "H2O": Y_H2O}

        # 热容（富燃高温，CH4 为主）
        cp    = _CP_CH4_GAS * Y_CH4 + 1800.0 * (Y_CO2 + Y_H2O)
        gamma = 1.22 + 0.04 * (1.0 - Y_CH4)   # 越富燃 γ 越接近 CH4 的 1.30
        mw    = 1.0 / max(Y_CH4/16.0 + Y_CO2/44.0 + Y_H2O/18.0, 1e-9)

        # 绝热温升：少量 CH4 燃烧放热被大量 CH4 吸收
        Q         = (f_ox / _STOICH_OF) * _LHV_CH4
        cp_mix    = Y_CH4 * _CP_CH4_GAS + (Y_CO2 + Y_H2O) * 1500.0
        delta_T   = Q / max(cp_mix, 1.0) * 0.85
        T_comb    = max(T_mix + delta_T, 350.0)

    return GasState(
        temperature=T_comb,
        pressure=pressure,
        cp=cp,
        gamma=gamma,
        molar_mass=mw,
        enthalpy=cp * T_comb,
        composition=composition,
        source="empirical",
    )


def _empirical_chamber(
    ox_T: float, ox_mdot: float,
    fuel_T: float, fuel_mdot: float,
    pressure: float,
    gamma: float, molar_mass: float, temp_guess: float,
    efficiency: float = 1.0,
) -> GasState:
    """
    主燃烧室经验模型（V7.2）。

    LOX/LCH4 在 O/F ≈ 3.5，Pc ≈ 30 MPa 下的参考组分（质量分数）
    来源：NASA CEA / Cantera GRI-Mech 3.0 计算结果交叉验证
    ──────────────────────────────────────────────────────
    物种    质量分数    说明
    H2O     0.367       主要产物，高压下更稳定
    CO2     0.118       完全氧化产物
    CO      0.198       不完全燃烧（略偏燃料侧）
    H2      0.082       离解平衡
    OH      0.054       中间体，高温下显著
    O       0.024       高温离解
    H       0.020       高温离解
    O2      0.008       微量过剩
    NO      0.003       热力学 NOx（高温）
    其他    ~0.126      N₂ 等（GRI-Mech 含氮，少量）
    ──────────────────────────────────────────────────────
    注：此为近化学计量混合（O/F = 3.5，φ ≈ 0.875）的平衡组分估算。
    实际组分随 O/F 和压力变化；安装 Cantera 可获得高保真结果。
    """
    checkpoint()
    mdot_total = ox_mdot + fuel_mdot
    f_ox   = ox_mdot   / max(mdot_total, 1e-9)
    f_fuel = fuel_mdot / max(mdot_total, 1e-9)

    # 质量加权混合焓估算（两股排气均为高温气体）
    cp_ox_exhaust   = 1050.0   # 富氧排气（主要O2）
    cp_fuel_exhaust = 2200.0   # 富燃排气（主要CH4）
    T_mix = (ox_mdot * cp_ox_exhaust * ox_T + fuel_mdot * cp_fuel_exhaust * fuel_T) /             max(ox_mdot * cp_ox_exhaust + fuel_mdot * cp_fuel_exhaust, 1e-9)

    # LOX/LCH4 绝热火焰温度：O/F=3.5 约 3640K（Pc=30 MPa，NASA CEA）
    # 简化模型：在 T_mix 基础上加燃烧温升
    Q_released = 0.0
    # 估算有效 O2 和 CH4 进入主燃烧室的质量流量
    # （近似：富氧排气 ~90% O2，富燃排气 ~87% CH4，其余为预燃烧产物）
    eff_O2  = ox_mdot  * 0.90
    eff_CH4 = fuel_mdot * 0.87
    if eff_CH4 > 0 and eff_O2 > 0:
        # 限制性反应物
        ch4_limited = min(eff_CH4, eff_O2 / _STOICH_OF)
        Q_released  = ch4_limited * _LHV_CH4
    cp_products  = 3500.0    # J/(kg·K) 高温平衡燃气
    delta_T      = Q_released / max(mdot_total * cp_products, 1.0) * 0.92
    T_chamber    = max(T_mix + delta_T, temp_guess)
    T_chamber    = min(T_chamber, 4200.0)   # 物理上限
    # 燃烧效率：按燃烧温升效率折减（与 Cantera 路径同一口径）
    if efficiency < 1.0:
        T_chamber = T_mix + efficiency * (T_chamber - T_mix)

    # 主燃烧室平衡组分（近化学计量 O/F≈3.5 参考值）
    composition = {
        "H2O": 0.367,
        "CO":  0.198,
        "CO2": 0.118,
        "H2":  0.082,
        "OH":  0.054,
        "O":   0.024,
        "H":   0.020,
        "O2":  0.008,
        "NO":  0.003,
    }
    # 归一化（确保和为1）
    _total = sum(composition.values())
    composition = {k: v / _total for k, v in composition.items()}

    return GasState(
        temperature=T_chamber,
        pressure=pressure,
        cp=cp_products,
        gamma=gamma,
        molar_mass=molar_mass,
        enthalpy=cp_products * T_chamber,
        composition=composition,
        source="empirical",
    )


def _empirical_turbine(
    state: GasState, p_exhaust: float, eta: float,
) -> dict:
    """理想气体等熵膨胀（V3 兼容）"""
    checkpoint()
    pressure_ratio = p_exhaust / max(state.pressure, 1.0)
    cp = state.cp
    gamma = state.gamma
    t_comb = state.temperature

    delta_h_s = cp * t_comb * max(
        1.0 - pressure_ratio ** ((gamma - 1.0) / gamma),
        0.0,
    )
    delta_h_actual = delta_h_s * eta
    t_exhaust = max(t_comb - delta_h_actual / max(cp, 1.0), 400.0)

    return {
        "isentropic_enthalpy_drop": delta_h_s,
        "actual_enthalpy_drop": delta_h_actual,
        "T_exhaust": t_exhaust,
        "h_exhaust": cp * t_exhaust,
        "composition": dict(state.composition),
    }


# =========================================================
# NASA CEA x Cantera 交叉验证
# =========================================================

import math as _math

# rocketcea 可用性检测（可选依赖）
try:
    from rocketcea.cea_obj import (
        CEA_Obj as _CEA_Obj,
        add_new_oxidizer as _add_new_ox,
        add_new_fuel as _add_new_fuel,
    )
    ROCKETCEA_AVAILABLE = True
except ImportError:
    ROCKETCEA_AVAILABLE = False

_PSIA_PER_BAR = 14.5038      # 1 bar = 14.5038 psia
_FT_S_TO_M_S  = 0.3048       # ft/s -> m/s
_RANKINE_TO_K = 1.0 / 1.8    # R -> K

# ── 第三步修正:CEA 反应物入口温度与 Cantera 基准锁同 ─────────────────
# rocketcea 默认 "LOX/CH4" 实际已经是液态(LOX@90.17K / 液 CH4@~111.65K),
# 但 Cantera 在本程序里用的是 LOX@90K / CH4@110K,基准差 ~1-2 K。
# 这里通过 add_new_oxidizer/add_new_fuel 注册"温度精确对齐到 Cantera 入口"的
# 自定义反应物,使 CEA vs Cantera 的差异只来自模型(CEA 数据库 vs GRI-3.0)
# 与平衡/冻结模式,而非入口温度基准。注册是懒式且按 (T_ox, T_fuel) 缓存的。
#
# 焓的算法:
#   LOX:  h_ref(O2 液,90.17K) = -3102 cal/mol(NIST 反算,与 rocketcea 默认值匹配)
#         dh/dT (液相 cp_p) = 13.0 cal/(mol·K)  (NIST WebBook @ NBP:
#                                                 cp_l=1.6964 kJ/(kg·K)·32 g/mol
#                                                     = 54.27 J/(mol·K) = 12.97 cal/(mol·K))
#         注:V6.1 修正 — 旧版误用气相 cp=6.5,造成低/高温 LOX 工况下 CEA 入口
#         焓系统偏高,Tc 偏高 ~0.1%(对 T_LOX=70K 密度化工况 ~4 K)。
#   LCH4: h_ref(CH4 液,111.65K) ≈ -21490 cal/mol(由气态 298K 基准 -17896,
#         加 298→111.65 显热 -1607 cal/mol,减汽化潜热 1985 cal/mol)
#         dh/dT (液相 cp_p) ≈ 13 cal/(mol·K)(NIST 实测 13.3,~2% 误差)
_CEA_CUSTOM_REGISTERED: "dict[tuple, tuple[str, str]]" = {}

def _ensure_cea_custom_reactants(T_ox_K: float, T_fuel_K: float) -> "tuple[str, str]":
    """注册一对 (LOX, LCH4) 自定义反应物,入口温度对齐到 Cantera 基准。

    返回 (ox_name, fuel_name) 可直接传给 _CEA_Obj。温度按 0.1 K 量化,
    相同温度对只注册一次。rocketcea 不可用时返回默认名(调用方应已先做可用性检查)。
    """
    checkpoint()
    if not ROCKETCEA_AVAILABLE:
        return ("LOX", "CH4")

    key = (round(float(T_ox_K), 1), round(float(T_fuel_K), 1))
    cached = _CEA_CUSTOM_REGISTERED.get(key)
    if cached is not None:
        return cached

    # 名称里的小数点替换为 'p',避免被 CEA 名称解析当成分隔符
    ox_name   = f"LOX_{key[0]:.1f}K".replace(".", "p")
    fuel_name = f"LCH4_{key[1]:.1f}K".replace(".", "p")

    h_lox  = -3102.0  + (T_ox_K   - 90.17)  * 13.0   # NIST 液相 cp(原 6.5 误用气相值)
    h_lch4 = -21490.0 + (T_fuel_K - 111.65) * 13.0

    _add_new_ox(
        ox_name,
        f"oxid O2(L) O 2.0\nh,cal={h_lox:.1f}  t(k)={T_ox_K:.2f}\n",
    )
    _add_new_fuel(
        fuel_name,
        f"fuel CH4(L) C 1.0 H 4.0\nh,cal={h_lch4:.1f}  t(k)={T_fuel_K:.2f}\n",
    )

    _CEA_CUSTOM_REGISTERED[key] = (ox_name, fuel_name)
    return (ox_name, fuel_name)
_G0           = 9.80665      # m/s^2


def _mach_from_area_ratio(eps: float, gamma: float) -> float:
    """
    超音速区二分法：由面积比 eps=A/A* 求马赫数 M (M>1)。
    A/A* = (1/M)*[(2/(gamma+1))*(1+(gamma-1)/2*M^2)]^((gamma+1)/(2*(gamma-1)))
    """
    checkpoint()
    def _ar(M, g):
        checkpoint()
        base = (2.0 / (g + 1.0)) * (1.0 + (g - 1.0) / 2.0 * M * M)
        return (1.0 / M) * base ** ((g + 1.0) / (2.0 * (g - 1.0)))

    lo, hi = 1.0 + 1e-6, 60.0
    for _ in range(100):
        checkpoint()
        mid = (lo + hi) * 0.5
        # In supersonic regime A/A* increases with M:
        # _ar(mid) > eps  →  M is too HIGH → search lower half
        if _ar(mid, gamma) > eps:
            hi = mid
        else:
            lo = mid
    return (lo + hi) * 0.5


def _cantera_nozzle_expand(
    chamber: GasState, p_chamber: float, eps: float,
    equilibrium: bool = True,
    p_amb: float = 0.0,
    n_iter: int = 40,
) -> "dict | None":
    """
    Cantera 喷管膨胀,**统一冻结流 / 平衡流两种口径**,出口压力按面积比自洽求解。

    equilibrium=True  → 平衡流:每截面 gas.equilibrate("SP"),组分随膨胀重组,
                        离解物种(H/OH/O)在低温段复合放热 → Isp 偏高(理论上界,
                        与 NASA CEA 默认同口径)。慢(~600ms,只在交叉验证/最终报告用)。
    equilibrium=False → 冻结流:组分锁定,只 gas.SP(无 equilibrate)。快(~10ms,
                        可在 Newton 关键路径用),更接近真实喷管(有限速率反应会冻结)。

    出口压力 p_e 不再借用理想 γ 估计,而是按 ρV(p_e)=ṁ/A_t/ε 二分求解,
    与给定面积比 ε 自洽。

    算法:
    1. 燃烧室态记下 s_c, h_c(等熵 + 能量守恒不变量)
    2. 喉部:二分 p 找 V=a(M=1) → ρ_t·V_t = ṁ/A_t, C*=Pc/(ρ_t·V_t)
    3. 出口:二分 p 找 ρV(p_e)=ρ_t·V_t/ε → 自洽 p_e, V_e
    4. Isp_vac = (V_e + p_e·ε·C*/Pc)/g₀;Isp_SL = Isp_vac − p_amb·ε·C*/(Pc·g₀)

    返回 dict(含 Cstar/Ve/pe/Te/Isp_vac/Isp_SL/Cf_vac/Cf_SL),失败返回 None。
    """
    checkpoint()
    if not CANTERA_AVAILABLE:
        return None
    gas = _get_gas()

    Y_chamber = _dict_to_Y(gas, chamber.composition)
    gas.TPY = chamber.temperature, p_chamber, Y_chamber
    s_chamber = gas.entropy_mass
    h_chamber = gas.enthalpy_mass

    def state_at(p: float):
        """沿等熵线膨胀到压力 p,返回 (T, ρ, V, a)。"""
        checkpoint()
        gas.SP = s_chamber, p
        if equilibrium:
            checkpoint()
            gas.equilibrate("SP")
            checkpoint()
        T   = gas.T
        rho = gas.density_mass
        h   = gas.enthalpy_mass
        V_sq = 2.0 * (h_chamber - h)              # 能量守恒 h_c = h + V²/2
        V    = _math.sqrt(max(V_sq, 0.0))
        gamma_local = gas.cp_mass / max(gas.cv_mass, 1.0e-3)
        a = _math.sqrt(max(gamma_local * p / max(rho, 1.0e-9), 0.0))
        return T, rho, V, a

    # ── 喉部 V = a (M = 1) ────────────────────────────
    p_lo, p_hi = 0.05 * p_chamber, 0.95 * p_chamber
    for _ in range(n_iter):
        checkpoint()
        p_mid = 0.5 * (p_lo + p_hi)
        _, _, V, a = state_at(p_mid)
        if V > a:
            p_lo = p_mid
        else:
            p_hi = p_mid
        if p_hi - p_lo < p_chamber * 1.0e-6:
            break
    p_throat = 0.5 * (p_lo + p_hi)
    T_throat, rho_throat, V_throat, _ = state_at(p_throat)

    if V_throat <= 1.0 or rho_throat <= 1.0e-6:
        return None

    mdot_per_At = rho_throat * V_throat
    Cstar       = p_chamber / mdot_per_At
    target_rv   = mdot_per_At / max(eps, 1.0)

    # ── 出口 ρV(p_e) = ṁ/A_t / ε ───────────────────────
    p_lo, p_hi = 1.0, p_throat * 0.999
    for _ in range(n_iter):
        checkpoint()
        p_mid = 0.5 * (p_lo + p_hi)
        _, rho, V, _ = state_at(p_mid)
        if rho * V < target_rv:
            p_lo = p_mid
        else:
            p_hi = p_mid
        if p_hi - p_lo < 1.0:
            break
    p_exit = 0.5 * (p_lo + p_hi)
    T_exit, _, V_exit, _ = state_at(p_exit)

    # 推力系数 + 比冲(真空 & 海平面)
    # C_F = F/(Pc·A_t);Isp = C*·C_F/g₀
    cf_vac = (V_exit * mdot_per_At + p_exit * eps) / p_chamber
    cf_sl  = (V_exit * mdot_per_At + (p_exit - p_amb) * eps) / p_chamber
    Isp_vac = Cstar * cf_vac / _G0
    Isp_sl  = Cstar * cf_sl  / _G0

    return {
        "flow_model": "equilibrium" if equilibrium else "frozen",
        "Tc":        chamber.temperature,
        "p_chamber": p_chamber,
        "p_throat":  p_throat,
        "T_throat":  T_throat,
        "V_throat":  V_throat,
        "p_exit":    p_exit,
        "T_exit":    T_exit,
        "V_exit":    V_exit,
        "Cstar":     Cstar,
        "Cf_vac":    cf_vac,
        "Cf_sl":     cf_sl,
        "Isp_vac":   Isp_vac,
        "Isp_sl":    Isp_sl,
    }


def _cantera_equilibrium_nozzle(chamber: GasState, p_chamber: float, eps: float):
    """向后兼容包装:平衡流口径(交叉验证用)。"""
    checkpoint()
    return _cantera_nozzle_expand(chamber, p_chamber, eps, equilibrium=True, n_iter=60)


def _cantera_perf_point(
    of: float,
    pc_pa: float,
    eps: float,
    T_fuel_K: float = 110.0,
    T_ox_K: float   = 90.0,
) -> "dict | None":
    """
    Cantera 单点主燃烧室性能(**平衡流口径**,与 NASA CEA 默认模式同口径)。

    流程:
      1. HP 化学平衡 → 燃烧室 (Tc, Pc, Y_chamber)
      2. 平衡流喷管膨胀(SP 平衡逐点) → 喉部 + 出口状态
      3. C* 由真实喉部条件给出;Isp_vac 含压力项

    参数
    ────
    of      : O/F 质量混合比
    pc_pa   : 燃烧室压力 [Pa]
    eps     : 喷管面积比 Ae/At
    T_fuel_K: CH4 入口温度 [K]
    T_ox_K  : LOX 入口温度 [K]
    """
    checkpoint()
    if not CANTERA_AVAILABLE:
        return None
    try:
        mdot_fuel = 1.0 / (1.0 + of)
        mdot_ox   = of  / (1.0 + of)
        state = _cantera_combustion(mdot_fuel, mdot_ox, T_fuel_K, T_ox_K, pc_pa)

        # 平衡流喷管展开
        nozzle = _cantera_equilibrium_nozzle(state, pc_pa, eps)
        if nozzle is None:
            return None

        return {
            "source":     "cantera",
            "Isp_vac_s":  nozzle["Isp_vac"],
            "Cstar_m_s":  nozzle["Cstar"],
            "Tc_K":       state.temperature,
            "gamma":      state.gamma,
            "MW_kg_kmol": state.molar_mass,
            "flow_model": "equilibrium",   # 标识口径
            "p_throat":   nozzle["p_throat"],
            "T_throat":   nozzle["T_throat"],
            "p_exit":     nozzle["p_exit"],
            "T_exit":     nozzle["T_exit"],
            "V_exit":     nozzle["V_exit"],
        }
    except Exception:
        return None


def _cea_perf_point(
    of: float,
    pc_bar: float,
    eps: float,
    T_fuel_K: float = 110.0,
    T_ox_K: float   = 90.0,
) -> "dict | None":
    """
    rocketcea 单点计算 -> SI 单位结果字典，或 None（rocketcea 不可用时）。

    第三步修正:通过 _ensure_cea_custom_reactants() 注册温度对齐到
    Cantera 基准的 LOX/LCH4 反应物,使 CEA-Cantera 差异只来自模型层,
    不再混入入口温度基准差。

    rocketcea 默认英制输出：
        Isp_vac : s       (已是 SI，无需转换)
        C*      : ft/s -> m/s (*0.3048)
        Tc      : degR  -> K  (/1.8)
        MW      : g/mol = kg/kmol (无需转换)
    """
    checkpoint()
    if not ROCKETCEA_AVAILABLE:
        return None
    try:
        ox_name, fuel_name = _ensure_cea_custom_reactants(T_ox_K, T_fuel_K)
        cea     = _CEA_Obj(oxName=ox_name, fuelName=fuel_name)
        pc_psia = pc_bar * _PSIA_PER_BAR

        # get_IvacCstrTc_exitMwGam -> (Isp_vac_s, Cstar_ft_s, Tc_R, MW, gamma)
        isp_vac, cstar_ft_s, Tc_R, MW, gamma = cea.get_IvacCstrTc_exitMwGam(
            Pc=pc_psia, MR=of, eps=eps
        )
        return {
            "source":     "cea",
            "Isp_vac_s":  isp_vac,
            "Cstar_m_s":  cstar_ft_s * _FT_S_TO_M_S,
            "Tc_K":       Tc_R * _RANKINE_TO_K,
            "gamma":      gamma,
            "MW_kg_kmol": float(MW),
            "T_fuel_K":   T_fuel_K,
            "T_ox_K":     T_ox_K,
            "ox_name":    ox_name,
            "fuel_name":  fuel_name,
        }
    except Exception:
        return None


def cross_validate_cea_cantera(
    of_list:   "list | None" = None,
    eps_list:  "list | None" = None,
    pc_pa:      float = 30e6,
    T_fuel_K:   float = 110.0,
    T_ox_K:     float = 90.0,
) -> list:
    """
    在 O/F x eps 网格上并排对比 Cantera 和 NASA CEA 的燃烧室性能。

    参数
    ────
    of_list   : O/F 扫描点，默认 [2.5, 3.0, 3.4, 3.6, 4.0, 4.5, 5.0]
    eps_list  : 面积比扫描点，默认 [40, 80, 120]
    pc_pa     : 燃烧室压力 [Pa]，默认 30 MPa
    T_fuel_K  : CH4 入口温度 [K]
    T_ox_K    : LOX 入口温度 [K]

    返回
    ────
    list of dict，每条包含：
        of, eps, pc_bar,
        cantera (dict 或 None), cea (dict 或 None),
        delta_Isp_vac_s_pct, delta_Cstar_m_s_pct, delta_Tc_K_pct
        (delta = (CEA - Cantera) / |Cantera| * 100，Cantera 为基准)
    """
    checkpoint()
    if of_list  is None:
        of_list  = [2.5, 3.0, 3.4, 3.6, 4.0, 4.5, 5.0]
    if eps_list is None:
        eps_list = [40, 80, 120]

    pc_bar  = pc_pa / 1e5
    records = []

    for of in of_list:
        checkpoint()
        for eps in eps_list:
            checkpoint()
            ct_res  = _cantera_perf_point(of, pc_pa, eps, T_fuel_K, T_ox_K)
            cea_res = _cea_perf_point(of, pc_bar, eps, T_fuel_K, T_ox_K)

            rec = {
                "of":     of,
                "eps":    eps,
                "pc_bar": pc_bar,
                "cantera": ct_res,
                "cea":     cea_res,
            }

            for key in ("Isp_vac_s", "Cstar_m_s", "Tc_K"):
                checkpoint()
                if ct_res and cea_res:
                    ct_val  = ct_res[key]
                    cea_val = cea_res[key]
                    rec[f"delta_{key}_pct"] = (
                        (cea_val - ct_val) / max(abs(ct_val), 1e-9) * 100.0
                    )
                else:
                    rec[f"delta_{key}_pct"] = None

            records.append(rec)

    return records
