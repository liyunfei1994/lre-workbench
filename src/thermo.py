"""
thermo.py

热力学计算封装层（Thermodynamic Abstraction Layer）

本模块的职责：
─────────────────────────────────────────────────────────
    统一管理所有气相化学平衡 / 等熵膨胀 / 热物性查询，
    对外暴露纯 Python 数据结构，隐藏 Cantera 细节。

    两种运行模式（自动检测，无需手动切换）：

    ┌────────────────────────────────────────────────┐
    │  Cantera 可用 → 化学平衡模式                   │
    │    · 按显式选择的物种数据计算化学平衡         │
    │    · 温度/组成相关 cp、γ，受物种数据温区限制    │
    │    · 当前燃气 EOS 为理想气体混合物             │
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
import math
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional
from propellants import for_fuel, normalize_fuel_species, validate_surrogate_state
from thermo_data import METHANE_DATASETS, active_methane_dataset
from fluid_properties import (state_tp as fluid_state_tp, state_ph as fluid_state_ph,
                              ideal_reference_enthalpy, saturation_pressure, fluid_spec)

# ─── Cantera 可用性检测 ────────────────────────────────────
try:
    import cantera as ct
    _GAS_LOCAL = threading.local()

    def _get_gas(fuel_species: str = "CH4", methane_dataset=None) -> ct.Solution:
        """Lazily cache each declared data set per thread, never switch mid-flow."""
        checkpoint()
        fuel_species = normalize_fuel_species(fuel_species)
        if fuel_species == "CH4":
            dataset = methane_dataset if methane_dataset is not None else active_methane_dataset()
            if dataset not in METHANE_DATASETS:
                raise ValueError(f"不支持的甲烷热化学数据集：{dataset!r}")
            definition = METHANE_DATASETS[dataset]
            filename, phase = definition["file"], definition["phase"]
        else:
            model = for_fuel(fuel_species)
            filename, phase = model.mechanism, model.phase
        if not hasattr(_GAS_LOCAL, "gases"):
            _GAS_LOCAL.gases = {}
        key = (filename, phase)
        if key not in _GAS_LOCAL.gases:
            # Cantera's native Windows file lookup can lose non-ASCII path
            # characters. Read its unchanged bundled mechanism through Python's
            # Unicode-aware path handling and pass the exact YAML contents.
            mechanism_file = Path(ct.__file__).resolve().parent / "data" / filename
            _GAS_LOCAL.gases[key] = ct.Solution(yaml=mechanism_file.read_text(encoding="utf-8"), name=phase)
            checkpoint()
        return _GAS_LOCAL.gases[key]

    CANTERA_AVAILABLE = True
except ImportError:
    CANTERA_AVAILABLE = False


# ─── 机制名称（可配置） ─────────────────────────────────────
MECHANISM = "gri30.yaml"  # historical default only; actual identity is recorded per state
"""
GRI-Mech 3.0：53 物种、325 反应。当前调用化学平衡而非有限速率反应。
适用性取决于物种集合、热力学多项式和 EOS；不能由机制名称推断精度。
历史 GRI30 主要 CHO 物种通常止于 3500 K；gri30_highT 全物种共同
温区为 300–5000 K。两者均逐态检查并披露外推，不认证反应动力学。
高压理想气体混合物近似尚未获得本平台独立验证。
"""


# ─── CoolProp 可用性检测（用于 LOX/LCH4 真实流体焓修正） ──────────────
# GRI-Mech 的物种焓由 NASA 7 系数多项式给出，**仅适用气相**。
# 当入口处于液态/超临界稠密相（LOX@90K、LCH4@110K）时，多项式向低温外推
# 会漏掉汽化潜热（O2 ~213 kJ/kg, CH4 ~510 kJ/kg）及液-气 cp 差，从而高估
# 入口焓 → HP 平衡解出的预燃室温度系统性偏高约 +130–160 K，沿涡轮排气
# 传到主燃烧室还会再叠加 ~70–90 K Tc 偏高。
# CoolProp 提供真实流体状态方程计算；数值不是实测值，适用性须按工质检查。
try:
    from CoolProp.CoolProp import PropsSI as _CP_PropsSI
    COOLPROP_AVAILABLE = True
except ImportError:
    COOLPROP_AVAILABLE = False
    _CP_PropsSI = None

# 物种 → CoolProp 流体名映射
_COOLPROP_FLUID: Dict[str, str] = {"O2": "Oxygen", "o2": "Oxygen", "CH4": "Methane", "c12h26": "n-Dodecane"}

# CoolProp 不可用时的备用修正 (J/kg)：基于 LOX@90K/30MPa, LCH4@110K/30MPa
# 历史经验焓修正常数，未附实验来源；仅保留兼容，不用于网页科学计算。
_FALLBACK_DH_CORRECTION_JKG: Dict[str, float] = {"O2": -197.0e3, "CH4": -470.0e3}
_FALLBACK_T_THRESHOLD_K:     Dict[str, float] = {"O2": 130.0,    "CH4": 150.0}

# 兜底饱和蒸汽压 [Pa]（CoolProp 不可用时用）：过冷致密推进剂蒸汽压很低，
# 取常压沸点附近量级；用于 NPSHa 计算的保守占位。
_FALLBACK_PVAP_PA: Dict[str, float] = {"O2": 6.3e3, "CH4": 3.4e4}


def vapor_pressure(species: str, T_K: float) -> float:
    """
    饱和蒸汽压 p_sat(T) [Pa]，用于泵 NPSHa 计算。

    · CoolProp 可用：按所选纯流体关联式计算饱和压，不是实测值。
    · 超过临界温度或查询失败：明确拒绝；此时不能用固定占位值计算 NPSH。

    NPSHa = (p_pump_inlet − p_vapor) / (ρ·g)，必须在 *泵入口* 处评估（已扣管路损失）。
    """
    checkpoint()
    return saturation_pressure(species, T_K)


def enthalpy_reference_id(fuel_species="CH4") -> str:
    """Versioned identity of the chemical/pure-fluid enthalpy alignment."""
    if normalize_fuel_species(fuel_species) == "c12h26":
        return "cantera_ndodecane_coolprop_600K_ideal_reference_v2"
    return f"cantera_{active_methane_dataset()}_coolprop_298p15K_ideal_reference_v2"


def available_npsh_pressure(species, temperature, pressure):
    """Static-pressure NPSH contribution; None where saturation is undefined.

    No velocity head or elevation difference is supplied by this lumped port.
    A negative value is retained, not clipped into an apparently valid margin.
    This does not establish a pump's required NPSH or cavitation capability.
    """
    checked = fluid_state_tp(species, temperature, pressure)
    if checked["T_K"] >= checked["limits"]["Tcritical_K"]:
        return None
    return pressure - saturation_pressure(species, temperature)


def _chemical_reference_offset(species, gas):
    T_ref, p_ref = (600.0, 1000.0) if gas.name == "nDodecane_IG" else (298.15, 101325.0)
    saved = gas.state.copy()
    try:
        gas.TPX = T_ref, p_ref, {species: 1.0}
        return gas.enthalpy_mass - ideal_reference_enthalpy(species, T_ref, p_ref)
    finally:
        gas.state = saved


def _real_fluid_enthalpy(
    species: str, T_K: float, p_Pa: float, gas: "ct.Solution"
) -> float:
    """Pure-fluid EOS enthalpy aligned to the selected gas chemical reference.

    h(T,p) = h_species^ideal(T_ref) + h_HEOS(T,p) - h_HEOS^ideal(T_ref).
    Methane/oxygen use 298.15 K / 101325 Pa; the kerosene surrogate uses
    600 K / 1000 Pa. HEOS reference residual enthalpy is explicitly removed:
    h_HEOS^ideal = h_HEOS - h_HEOS,residual. The finite-pressure reference
    does not silently introduce an enthalpy offset.
    This does not change the combustion-mixture EOS to a real-gas model.
    All queries remain subject to fluid EOS ranges and phase applicability;
    a returned value alone is not experimental validation. No liquid-state
    NASA extrapolation is needed to evaluate this enthalpy difference.
    """
    checkpoint()
    if not COOLPROP_AVAILABLE:
        raise RuntimeError("统一化学基准的纯推进剂焓需要 CoolProp；不使用常数焓修正回退。")
    actual_species = ("o2" if gas.name == "nDodecane_IG" else "O2") if species in ("O2", "o2", "LOX", "Oxygen") else normalize_fuel_species(species)
    checked = fluid_state_tp(actual_species, T_K, p_Pa)
    return checked["h_Jkg"] + _chemical_reference_offset(actual_species, gas)


def reactant_enthalpy(species: str, temperature: float, pressure: float, fuel_species: str = "CH4") -> float:
    """Pure-reactant J/kg, on the selected mechanism's chemical enthalpy reference."""
    if not CANTERA_AVAILABLE:
        raise RuntimeError("反应物化学基准焓查询需要 Cantera。")
    if species in ("LOX", "Oxygen", "o2", "O2"):
        species = "o2" if normalize_fuel_species(fuel_species) == "c12h26" else "O2"
    return _real_fluid_enthalpy(species, temperature, pressure, _get_gas(fuel_species))


def reactant_temperature_from_enthalpy(
    species: str, enthalpy_Jkg: float, pressure: float, fuel_species: str = "CH4",
) -> float:
    """Invert the SAME chemical-reference real-fluid enthalpy used at reactant ports.

    Use this for adiabatic throttles (constant h) and coolant heating (h+Q/mdot).
    It requires both libraries; no cp*T or temperature-clipping substitute is used.
    """
    checkpoint()
    if not (CANTERA_AVAILABLE and COOLPROP_AVAILABLE):
        raise RuntimeError("统一基准的真实流体焓反算需要 Cantera 和 CoolProp。")
    fuel_species = normalize_fuel_species(fuel_species)
    if species in ("LOX", "Oxygen", "o2", "O2"):
        species = "o2" if fuel_species == "c12h26" else "O2"
    if not (math.isfinite(enthalpy_Jkg) and math.isfinite(pressure) and pressure > 0):
        raise ValueError("焓反算要求有限焓和正压力。")
    gas = _get_gas(fuel_species)
    offset = _chemical_reference_offset(species, gas)
    try:
        temperature = fluid_state_ph(species, pressure, enthalpy_Jkg - offset)["T_K"]
        # T/P alone cannot represent a two-phase H/P state. Do not erase quality.
        recovered = reactant_enthalpy(species, temperature, pressure, fuel_species)
        if abs(recovered - enthalpy_Jkg) > max(0.05, abs(enthalpy_Jkg) * 1e-8):
            raise ValueError("焓反算落入当前单相端口模型无法表示的状态。")
        return temperature
    except Exception as exc:
        raise ValueError(f"{species} 在 {pressure:g} Pa 的单相焓反算失败。") from exc


# =========================================================
# 输出数据结构
# =========================================================

@dataclass
class GasState:
    """
    气体热力学状态（预燃室 / 主燃烧室 / 喷管出口均用此描述）

    所有字段以 SI 单位为准。
    composition 保留全部非零物种，避免重建状态时丢失元素及焓。
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
    mechanism: str = "gri30.yaml"
    phase: str = "gri30"
    thermo_extrapolation: Dict[str, float] = field(default_factory=dict)
    thermo_range: dict = field(default_factory=dict)
    gas_model: str = "unknown"


class ThermoRangeWarning(RuntimeWarning):
    """A calculation used species polynomials outside their declared ranges."""


class SurrogateThermoRangeWarning(ThermoRangeWarning):
    """Stable warning category for the explicitly disclosed surrogate extrapolation."""


def _fuel_from_compositions(*compositions: Dict[str, float]) -> str:
    # Reitz species retain their case. GRI uses uppercase names, so chemistry
    # identity also survives legacy FlowState copies that carry composition only.
    return "c12h26" if any(name in ("c12h26", "o2", "co2", "h2o", "co", "h2", "ch4")
                           and fraction > 0 for composition in compositions
                           for name, fraction in composition.items()) else "CH4"


def _gas_for_state(state: GasState) -> "ct.Solution":
    if state.mechanism == "nDodecane_Reitz.yaml" and state.phase == "nDodecane_IG":
        return _get_gas("c12h26")
    for dataset, definition in METHANE_DATASETS.items():
        if state.mechanism == definition["file"] and state.phase == definition["phase"]:
            return _get_gas("CH4", methane_dataset=dataset)
    raise ValueError(f"未支持的气相机制身份：{state.mechanism}/{state.phase}")


def gas_data_filename(gas):
    """Get identity from the cached instance; phase names alone are ambiguous."""
    for (filename, _phase), instance in getattr(_GAS_LOCAL, "gases", {}).items():
        if instance is gas:
            return filename
    return "nDodecane_Reitz.yaml" if gas.name == "nDodecane_IG" else gas.source


# =========================================================
# 1. 预燃室 / 主燃烧室 化学平衡
# =========================================================

def combustion_equilibrium(
    mdot_fuel: float,
    mdot_ox: float,
    T_fuel: float,
    T_ox: float,
    pressure: float,
    fuel_species: str = "CH4",
    *, fuel_enthalpy: float | None = None, ox_enthalpy: float | None = None,
    heat_loss_W: float = 0.0, energy_diagnostics: dict | None = None,
) -> GasState:
    """
    计算给定端口焓及可选外部热汇后的定压（HP）化学平衡。

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
    if (not all(math.isfinite(v) and v >= 0 for v in (mdot_fuel, mdot_ox, heat_loss_W))
            or mdot_fuel + mdot_ox <= 0):
        raise ValueError("燃烧平衡要求非负有限流量/热汇及正总流量。")
    fuel_species = normalize_fuel_species(fuel_species)
    if CANTERA_AVAILABLE:
        return _cantera_combustion(mdot_fuel, mdot_ox, T_fuel, T_ox, pressure,
                                  fuel_species=fuel_species,
                                  fuel_enthalpy=fuel_enthalpy, ox_enthalpy=ox_enthalpy,
                                  heat_loss_W=heat_loss_W, energy_diagnostics=energy_diagnostics)
    else:
        if fuel_enthalpy is not None or ox_enthalpy is not None or heat_loss_W or energy_diagnostics is not None:
            raise RuntimeError("显式端口焓、热汇及能量诊断的燃烧平衡需要 Cantera。")
        if fuel_species != "CH4":
            raise RuntimeError("煤油替代模型需要 Cantera；不提供甲烷经验燃烧回退。")
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
    *, ox_exhaust_enthalpy: float | None = None,
    fuel_exhaust_enthalpy: float | None = None,
    heat_loss_W: float = 0.0,
    energy_diagnostics: dict | None = None,
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
    efficiency     : 兼容温升折减系数 ∈ (0,1]。按经验关系
                     T_model = T_mix + k·(T_equilibrium − T_mix)，
                     T_mix 为两股排气的质量加权掺混温度（燃烧前参考）。
                     1.0 = 不附加经验温升折减。折减时组分/压力不变，
                     焓差显式列为经验热汇；不是燃尽率或 c* 效率。

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
            ox_enthalpy=ox_exhaust_enthalpy, fuel_enthalpy=fuel_exhaust_enthalpy,
            heat_loss_W=heat_loss_W, energy_diagnostics=energy_diagnostics,
        )
    else:
        if heat_loss_W or ox_exhaust_enthalpy is not None or fuel_exhaust_enthalpy is not None:
            raise RuntimeError("显式端口焓及热端抽热的主燃烧室平衡需要 Cantera。")
        if _fuel_from_compositions(ox_exhaust_composition, fuel_exhaust_composition) != "CH4":
            raise RuntimeError("煤油气相混合需要 Cantera；不提供甲烷经验回退。")
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
    fuel_species: str = "CH4",
    *, fuel_enthalpy: float | None = None, ox_enthalpy: float | None = None,
    heat_loss_W: float = 0.0, energy_diagnostics: dict | None = None,
) -> GasState:
    """
    GG 主室推进剂 HP 平衡；生产调用应传入实际泵/冷却出口的共同基准焓，
    避免用同一入口温度在主室压力下重建另一状态的焓。预算模式的外部
    heat_loss_W 在 HP 目标中扣一次；空间扣热模式传零，热汇由热侧模型处理。

    efficiency < 1 仅为兼容温升折减，组分冻结后调整温度，焓差单独记账；
    不是标准燃烧效率、c* 效率或经试验验证的损失模型。
    """
    checkpoint()
    if not math.isfinite(efficiency) or not 0 < efficiency <= 1:
        raise ValueError("历史温升折减系数须在 (0, 1] 内。")
    state = combustion_equilibrium(
        mdot_fuel=mdot_fuel, mdot_ox=mdot_ox,
        T_fuel=T_fuel, T_ox=T_ox, pressure=pressure,
        fuel_species=fuel_species,
        fuel_enthalpy=fuel_enthalpy, ox_enthalpy=ox_enthalpy,
        heat_loss_W=heat_loss_W, energy_diagnostics=energy_diagnostics,
    )
    if efficiency >= 1.0:
        return state

    mdot_total = mdot_fuel + mdot_ox
    T_mix = (mdot_fuel * T_fuel + mdot_ox * T_ox) / mdot_total
    if state.temperature < T_mix:
        raise ValueError("抽热后的主室温度低于入口混合温度，不能把历史温升折减变成加热。")
    T_real = T_mix + efficiency * (state.temperature - T_mix)

    if CANTERA_AVAILABLE and state.source == "cantera":
        # 组分冻结在平衡解，把温度折减到 T_real 后由状态方程自洽重算 cp/γ/h/s
        gas = _gas_for_state(state)
        Y = _dict_to_Y(gas, state.composition)
        gas.TPY = T_real, pressure, Y
        if energy_diagnostics is not None:
            loss = mdot_total*(state.enthalpy-gas.enthalpy_mass)
            energy_diagnostics.update(h_chamber_Jkg=gas.enthalpy_mass, combustion_loss_W=loss,
                energy_residual_W=mdot_total*(gas.enthalpy_mass-energy_diagnostics["h_in_Jkg"])+heat_loss_W+loss)
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
    if (combustion_state.mechanism == "nDodecane_Reitz.yaml" or _fuel_from_compositions(combustion_state.composition) == "c12h26") and not (CANTERA_AVAILABLE and combustion_state.source == "cantera"):
        raise RuntimeError("煤油涡轮膨胀需要对应 Cantera 气相状态，不提供经验回退。")
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
    if (chamber_state.mechanism == "nDodecane_Reitz.yaml" or _fuel_from_compositions(chamber_state.composition) == "c12h26") and not (CANTERA_AVAILABLE and chamber_state.source == "cantera"):
        raise RuntimeError("煤油喷管膨胀需要对应 Cantera 气相状态，不提供经验回退。")
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
      · 从主室组成冻结是一种极限假设，不自动代表真实冻结位置。
    对照可选择连续平衡或喉部冻结；实际适用性需反应时间尺度证据。

    仅 Cantera 模式可用,经验模式返回 None(由调用方走理想气体 fallback)。
    """
    checkpoint()
    if (chamber_state.mechanism == "nDodecane_Reitz.yaml" or _fuel_from_compositions(chamber_state.composition) == "c12h26") and not (CANTERA_AVAILABLE and chamber_state.source == "cantera"):
        raise RuntimeError("煤油喷管性能需要对应 Cantera 气相状态，不提供经验回退。")
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
    fuel_species: str = "CH4",
    *, fuel_enthalpy: float | None = None, ox_enthalpy: float | None = None,
    heat_loss_W: float = 0.0, energy_diagnostics: dict | None = None,
) -> GasState:
    """
    Cantera HP 化学平衡。

    步骤
    ────
    1. 优先使用实际端口的共同基准焓；未传入时使用检查过的 HEOS 焓。
    2. 按质量加权得到混合入口焓，并扣除明确给定的外部热汇。
    3. 设置组分 → 设定 HP → 平衡

    冷推进剂不通过低温气相多项式外推代替液态入口焓。HEOS 与化学焓
    使用统一参考态，但这本身不验证高压燃气的理想混合物假设。
    """
    checkpoint()
    gas = _get_gas(fuel_species)
    ox_species = "o2" if fuel_species == "c12h26" else "O2"
    mdot_total = mdot_fuel + mdot_ox

    # ── 入口焓（已含 LOX/LCH4 真实流体焓修正） ─────────────
    h_fuel = (_real_fluid_enthalpy(fuel_species, T_fuel, pressure, gas)
              if fuel_enthalpy is None else float(fuel_enthalpy))
    h_ox = (_real_fluid_enthalpy(ox_species, T_ox, pressure, gas)
            if ox_enthalpy is None else float(ox_enthalpy))

    if not math.isfinite(h_fuel) or not math.isfinite(h_ox):
        raise ValueError("燃烧入口端口焓必须为有限值。")
    Y_fuel_frac = mdot_fuel / mdot_total
    Y_ox_frac   = 1.0 - Y_fuel_frac
    h_inlet = Y_fuel_frac * h_fuel + Y_ox_frac * h_ox

    # ── HP 平衡(快路径 + 二分回退) ──────────────────────────────
    _equilibrate_HP(
        gas, h_target=h_inlet-heat_loss_W/mdot_total, pressure=pressure,
        Y_init=f"{fuel_species}:{Y_fuel_frac}, {ox_species}:{Y_ox_frac}",
        context=f"PB(mdot_f={mdot_fuel:.3g},mdot_o={mdot_ox:.3g})",
    )

    if energy_diagnostics is not None:
        energy_diagnostics.update(h_in_Jkg=h_inlet, h_after_heat_extraction_Jkg=gas.enthalpy_mass,
            h_chamber_Jkg=gas.enthalpy_mass, heat_loss_W=heat_loss_W, combustion_loss_W=0.,
            T_chamber_ideal_K=gas.T,
            energy_residual_W=mdot_total*(gas.enthalpy_mass-h_inlet)+heat_loss_W)
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
    if not (math.isfinite(h_target) and math.isfinite(pressure) and pressure > 0):
        raise ValueError("HP 平衡要求有限目标焓和正压力。")
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
        # Fast and safeguarded paths share the same declared gas-temperature
        # envelope. Which numerical path succeeds must not change validity.
        T_ok = 300.0 <= gas.T <= (5000.0 if gas.name == "nDodecane_IG" else 5500.0)
        h_tol = max(0.05, 1e-8 * abs(h_target))
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
    tol_K: float = 1e-7,
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
    if not (math.isfinite(h_target) and math.isfinite(pressure) and pressure > 0):
        raise ValueError("HP 平衡要求有限目标焓和正压力。")
    if gas.name == "nDodecane_IG":
        T_lo, T_hi = max(T_lo, 300.0), min(T_hi, 5000.0)
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

    if h_target < h_lo or h_target > h_hi:
        raise ValueError(
            f"HP 平衡 {context}: 目标焓 {h_target:.6g} J/kg 超出 "
            f"{T_lo:g}–{T_hi:g} K 的气相平衡焓包络 [{h_lo:.6g}, {h_hi:.6g}] J/kg；不截断温度。"
        )

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
    if abs(gas.enthalpy_mass - h_target) > max(0.05, 1e-8 * abs(h_target)):
        raise ValueError(f"HP 平衡 {context} 未满足焓残差容差。")


def _cantera_chamber(
    ox_T: float, ox_mdot: float, ox_comp: Dict[str, float],
    fuel_T: float, fuel_mdot: float, fuel_comp: Dict[str, float],
    pressure: float,
    efficiency: float = 1.0,
    *, ox_enthalpy: float | None = None, fuel_enthalpy: float | None = None,
    heat_loss_W: float = 0.0, energy_diagnostics: dict | None = None,
) -> GasState:
    """
    两股涡轮排气在主燃烧室混合 → HP 平衡。

    efficiency < 1 时按燃烧温升效率折减温度（见 chamber_equilibrium 文档）。
    """
    checkpoint()
    gas = _get_gas(_fuel_from_compositions(ox_comp, fuel_comp))
    mdot_total = ox_mdot + fuel_mdot
    f_ox   = ox_mdot   / max(mdot_total, 1e-9)
    f_fuel = fuel_mdot / max(mdot_total, 1e-9)

    n_sp = gas.n_species

    # ── 富氧排气焓 ─────────────────────────────────────
    Y_ox = _dict_to_Y(gas, ox_comp)
    gas.TPY = ox_T, pressure, Y_ox
    h_ox = gas.enthalpy_mass if ox_enthalpy is None else float(ox_enthalpy)

    # ── 富燃排气焓 ─────────────────────────────────────
    Y_fuel = _dict_to_Y(gas, fuel_comp)
    gas.TPY = fuel_T, pressure, Y_fuel
    h_fuel = gas.enthalpy_mass if fuel_enthalpy is None else float(fuel_enthalpy)

    # ── 质量加权混合 ───────────────────────────────────
    import numpy as np
    Y_mix = f_ox * np.array(Y_ox) + f_fuel * np.array(Y_fuel)
    h_mix = f_ox * h_ox + f_fuel * h_fuel

    # ── HP 平衡(快路径 + 二分回退) ──────────────────────────────
    _equilibrate_HP(
        gas, h_target=h_mix - heat_loss_W / mdot_total, pressure=pressure,
        Y_init=[float(v) for v in Y_mix],
        context=f"chamber(ox_mdot={ox_mdot:.3g},fuel_mdot={fuel_mdot:.3g})",
    )

    # ── 燃烧效率：理想平衡温度 → 真实主流温度 ──────────────────
    # T_real = T_mix + η·(T_ideal − T_mix)，T_mix=两股排气质量加权掺混温(燃烧前)。
    # 仅降温、组分与压力不变 → cp/γ/h/s 由 gas.TP 自洽重算；下游喷管由本态
    # 重建，故 c*/Isp 自动跟随，无双重计数。
    T_ideal = gas.T
    h_before_efficiency = gas.enthalpy_mass
    if efficiency < 1.0:
        T_mix   = f_ox * ox_T + f_fuel * fuel_T
        if T_ideal < T_mix:
            raise ValueError("抽热后的主室平衡温度低于入口混合温度，当前燃烧温升效率模型不适用；不能把效率损失变成加热。")
        T_real  = T_mix + efficiency * (T_ideal - T_mix)
        gas.TP  = T_real, pressure

    combustion_loss_W = mdot_total * (h_before_efficiency - gas.enthalpy_mass)
    if energy_diagnostics is not None:
        energy_diagnostics.update({
            "h_in_Jkg": h_mix, "h_after_heat_extraction_Jkg": h_before_efficiency,
            "h_chamber_Jkg": gas.enthalpy_mass, "heat_loss_W": heat_loss_W,
            "combustion_loss_W": combustion_loss_W,
            "T_chamber_ideal_K": T_ideal,
            "energy_residual_W": mdot_total * (gas.enthalpy_mass - h_mix)
                                 + heat_loss_W + combustion_loss_W,
        })

    return _gas_to_state(gas, pressure)


def _cantera_turbine(
    state: GasState, p_exhaust: float, eta: float,
) -> dict:
    """
    用所选混合气体状态方程做冻结组分膨胀。当前是理想气体 EOS，
    采用温度相关热容，而非固定 γ；不意味着高压非理想性已经处理。

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
    gas = _gas_for_state(state)
    Y = _dict_to_Y(gas, state.composition)

    # 入口状态
    gas.TPY = state.temperature, state.pressure, Y
    h_in = gas.enthalpy_mass
    s_in = gas.entropy_mass

    # 等熵膨胀到排气压力（冻结组分）
    gas.SP = s_in, p_exhaust
    _check_gas_model_range(gas, report=False)
    h_out_s = gas.enthalpy_mass

    delta_h_s = h_in - h_out_s
    delta_h_actual = delta_h_s * eta
    h_exhaust = h_in - delta_h_actual

    # 由实际排气焓反查温度
    gas.HP = h_exhaust, p_exhaust
    _check_gas_model_range(gas)
    T_exhaust = gas.T

    return {
        "isentropic_enthalpy_drop": delta_h_s,
        "actual_enthalpy_drop": delta_h_actual,
        "T_exhaust": T_exhaust,
        "h_exhaust": h_exhaust,
        "composition": _Y_to_dict(gas),
        "mechanism": gas_data_filename(gas),
        "phase": gas.name,
    }


def _cantera_nozzle_expansion(
    chamber: GasState, p_exit: float,
) -> GasState:
    """
    喷管冻结流等熵膨胀（固定组分，S=const → 给定 p_exit）。
    """
    checkpoint()
    gas = _gas_for_state(chamber)
    Y = _dict_to_Y(gas, chamber.composition)

    gas.TPY = chamber.temperature, chamber.pressure, Y
    s_chamber = gas.entropy_mass

    # 冻结等熵膨胀
    gas.SP = s_chamber, p_exit

    return _gas_to_state(gas, p_exit)


# ─── Cantera 工具函数 ──────────────────────────────────────

def _check_gas_model_range(gas: "ct.Solution", *, report: bool = True) -> Dict[str, float]:
    if gas.name == "nDodecane_IG" and not 300.0 <= gas.T <= 5000.0:
        raise ValueError(f"正十二烷气相温度 {gas.T:.2f} K 超出 300–5000 K 求解范围；不继续外推。")
    if gas.min_temp <= gas.T <= gas.max_temp:
        return {}
    info = gas_temperature_applicability(gas)
    outside = {s["species"]: s["mass_fraction"] for s in info["species_outside"]}
    if outside and report:
        # Stable wording deduplicates solver trial warnings; exact final-state
        # temperatures, limits and fractions are persisted separately.
        category = SurrogateThermoRangeWarning if gas.name == "nDodecane_IG" else ThermoRangeWarning
        warnings.warn(f"{gas.name} 求解过程中存在超出声明温区的物种热力学多项式外推；"
                      "不截断温度，不据数值收敛判定热力学适用。请查看最终状态温区明细。", category)
    return outside


def gas_temperature_applicability(gas: "ct.Solution") -> dict:
    """Read-only audit of every nonzero species, including trace species.

    A zero fraction is excluded from the state coverage but not from the
    mechanism-common interval. This is a data-range check, never an error bound.
    """
    species = gas.species()
    fractions = gas.Y
    present = [(sp, float(y)) for sp, y in zip(species, fractions) if y > 0]
    outside = [{"species": sp.name, "mass_fraction": y,
                "min_temperature_K": float(sp.thermo.min_temp),
                "max_temperature_K": float(sp.thermo.max_temp)}
               for sp, y in present if not sp.thermo.min_temp <= gas.T <= sp.thermo.max_temp]
    outside.sort(key=lambda row: row["mass_fraction"], reverse=True)
    return {
        "status": "extrapolated" if outside else "within_declared_ranges",
        "temperature_K": float(gas.T), "species_outside": outside,
        "outside_mass_fraction": math.fsum(row["mass_fraction"] for row in outside),
        "valid_temperature_min_K": max((float(sp.thermo.min_temp) for sp, _ in present), default=None),
        "valid_temperature_max_K": min((float(sp.thermo.max_temp) for sp, _ in present), default=None),
        "common_temperature_min_K": max(float(sp.thermo.min_temp) for sp in species),
        "common_temperature_max_K": min(float(sp.thermo.max_temp) for sp in species),
        "mass_fraction_threshold": 0.0,
        "interpretation": "声明温区检查；外推占比不是误差百分比，温区内也不代表精度已验证。",
    }


def _gas_to_state(gas: "ct.Solution", pressure: float) -> GasState:
    """从 Cantera Solution 对象提取 GasState"""
    checkpoint()
    kerosene = gas.name == "nDodecane_IG"
    extrapolation = _check_gas_model_range(gas)
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
        mechanism=gas_data_filename(gas),
        phase=gas.name,
        thermo_extrapolation=extrapolation,
        thermo_range=gas_temperature_applicability(gas),
        gas_model=gas.thermo_model,
    )


def _Y_to_dict(gas: "ct.Solution") -> Dict[str, float]:
    """Preserve trace species internally so rebuilding a port preserves its h and atoms."""
    checkpoint()
    return {
        gas.species_name(i): float(gas.Y[i])
        for i in range(gas.n_species)
        if gas.Y[i] > 0.0
    }


def _dict_to_Y(gas: "ct.Solution", comp: Dict[str, float]) -> list:
    """Check mass fractions before Cantera; only rounding drift is normalized."""
    checkpoint()
    import numpy as np
    if not isinstance(comp, dict) or not comp:
        raise ValueError("气相质量分数组成不能为空；不能用占位气体代替。")
    Y = np.zeros(gas.n_species)
    for name, frac in comp.items():
        checkpoint()
        if isinstance(frac, bool) or not isinstance(frac, (int, float)) or not math.isfinite(frac) or frac < 0:
            raise ValueError(f"物种 {name!r} 的质量分数必须是非负有限数值；收到 {frac!r}。")
        idx = gas.species_index(name) if name in gas.species_names else -1
        if idx >= 0:
            Y[idx] = frac
        elif frac > 0:
            raise ValueError(f"气相数据未识别物种 {name!r}；不能丢弃质量后重新归一化。")
    total = Y.sum()
    if total <= 0 or abs(total - 1.0) > 1e-8:
        raise ValueError(f"气相质量分数之和为 {total:.16g}；要求 1±1e-8，不接受任意归一化。")
    Y /= total
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

_PSIA_PER_BAR = 1e5 / 6894.757293168  # exact SI conversion to psi
_FT_S_TO_M_S  = 0.3048       # ft/s -> m/s
_RANKINE_TO_K = 1.0 / 1.8    # R -> K

# CEA cards receive the same pressure-dependent inlet enthalpies as Cantera.
# Product species and phases are still different; no accuracy verdict is inferred.
_CEA_CUSTOM_REGISTERED: "dict[tuple, tuple[str, str]]" = {}

def validation_reactants(T_fuel_K, T_ox_K, pressure, fuel_species="CH4") -> dict:
    """Identical inlet chemical enthalpy for both validation solvers, no fallback.

    HEOS enthalpy is anchored to the chosen Cantera ideal species reference;
    finite-pressure residual enthalpy is removed at the reference point.
    J/kg * kg/kmol / 1000 / 4.184 = cal/mol.
    The pressure is an explicit inlet assumption, equal to chamber pressure.
    """
    if not CANTERA_AVAILABLE or not COOLPROP_AVAILABLE:
        raise RuntimeError("同入口焓对比需要 Cantera 和 CoolProp；不使用经验焓回退。")
    gas = _get_gas(fuel_species)
    fuel_species = normalize_fuel_species(fuel_species)
    ox_species = "o2" if fuel_species == "c12h26" else "O2"
    if fuel_species == "c12h26":
        validate_surrogate_state(T_fuel_K, pressure)
    reference_T, reference_p = ((600.0, 1000.0) if fuel_species == "c12h26"
                                else (298.15, 101325.0))
    result = {"fuel_species": fuel_species, "oxidizer_species": ox_species,
              "reference": {"temperature_K": reference_T, "pressure_Pa": reference_p,
                            "id": enthalpy_reference_id(fuel_species),
                            "basis": "Cantera ideal species chemical enthalpy + HEOS H(T,p) - HEOS H_ideal(T_ref)"},
              "inlet_pressure_Pa": pressure,
              "pressure_assumption": "独立燃烧室基准：两路入口物性压力均取燃烧室压力。"}
    for role, species, temperature in (("fuel", fuel_species, T_fuel_K), ("oxidizer", ox_species, T_ox_K)):
        enthalpy = _real_fluid_enthalpy(species, temperature, pressure, gas)
        molecular_weight = float(gas.molecular_weights[gas.species_index(species)])
        result[role + "_enthalpy_Jkg"] = enthalpy
        result[role + "_molecular_weight_kg_kmol"] = molecular_weight
        result[role + "_enthalpy_cal_mol"] = enthalpy * molecular_weight / 4184.0
        result[role + "_temperature_K"] = temperature
        result[role + "_phase"] = fluid_state_tp(species, temperature, pressure)["phase"]
    return result


def _ensure_cea_custom_reactants(T_ox_K: float, T_fuel_K: float, pressure: float = 30e6,
                                fuel_species: str = "CH4", reactants=None) -> "tuple[str, str]":
    """Register exact shared chemical inlet enthalpies, including pressure.

    Cards encode elemental formula and explicit h,cal (cal/mol); their names
    hash all inputs so sub-0.1 K changes cannot alias an earlier cached card.
    """
    import hashlib
    import json
    checkpoint()
    if not ROCKETCEA_AVAILABLE:
        raise RuntimeError("RocketCEA 未安装，不能计算或伪造 CEA 结果。")
    reactants = reactants or validation_reactants(T_fuel_K, T_ox_K, pressure, fuel_species)
    key = hashlib.sha256(json.dumps(reactants, sort_keys=True).encode()).hexdigest()[:20]
    cached = _CEA_CUSTOM_REGISTERED.get(key)
    if cached is not None:
        return cached

    ox_name, fuel_name = "OX_" + key, "FUEL_" + key
    h_lox, h_fuel = reactants["oxidizer_enthalpy_cal_mol"], reactants["fuel_enthalpy_cal_mol"]
    formula = "C 12 H 26" if reactants["fuel_species"] == "c12h26" else "C 1 H 4"

    _add_new_ox(
        ox_name,
        f"oxid O2_input O 2.0 wt%=100.\nh,cal={h_lox:.12g} t(k)={T_ox_K:.12g}\n",
    )
    _add_new_fuel(
        fuel_name,
        f"fuel Fuel_input {formula} wt%=100.\nh,cal={h_fuel:.12g} t(k)={T_fuel_K:.12g}\n",
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


def isentropic_sound_speed(gas, equilibrium: bool = True, relative_step: float = 1e-4) -> float:
    """Sound speed from (dp/drho)_s, with explicit chemistry constraint.

    Equilibrium derivative follows Cantera's documented sound-speed example;
    central differences reduce truncation error. The original state is restored.
    https://cantera.org/3.1/examples/python/thermo/sound_speed.html
    """
    if not equilibrium:
        return float(gas.sound_speed)
    state = gas.TDY
    entropy, pressure = gas.entropy_mass, gas.P
    try:
        densities = []
        for factor in (1.0-relative_step, 1.0+relative_step):
            checkpoint()
            gas.TDY = state
            gas.SP = entropy, pressure * factor
            gas.equilibrate("SP", rtol=1e-10)
            densities.append(gas.density_mass)
        derivative = pressure * (2*relative_step) / (densities[1]-densities[0])
        if not _math.isfinite(derivative) or derivative <= 0:
            raise ValueError("平衡等熵声速导数无有效正值。")
        return _math.sqrt(derivative)
    finally:
        gas.TDY = state


def _nozzle_station(gas, equilibrium) -> dict:
    speed = isentropic_sound_speed(gas, equilibrium)
    return {"temperature_K": float(gas.T), "pressure_Pa": float(gas.P),
            "density_kg_m3": float(gas.density_mass), "enthalpy_Jkg": float(gas.enthalpy_mass),
            "entropy_JkgK": float(gas.entropy_mass), "sound_speed_m_s": speed,
            "MW_kg_kmol": float(gas.mean_molecular_weight),
            "gamma_isentropic": speed**2 * gas.density_mass / gas.P,
            "cp_cv_frozen": float(gas.cp_mass/gas.cv_mass),
            "mass_fractions": {name: float(value) for name, value in zip(gas.species_names, gas.Y) if value > 0},
            "element_mass_fractions": {name: float(gas.elemental_mass_fraction(name)) for name in gas.element_names}}


def _cantera_nozzle_expand(
    chamber: GasState, p_chamber: float, eps: float,
    equilibrium: bool = True,
    p_amb: float = 0.0,
    n_iter: int = 40,
    freeze_at_throat: bool = False,
    diagnostics: bool = False,
    gas_override=None,
) -> "dict | None":
    """
    Cantera 喷管膨胀,**统一冻结流 / 平衡流两种口径**,出口压力按面积比自洽求解。

    equilibrium=True  → 平衡流:每截面 gas.equilibrate("SP"),组分随膨胀重组,
                        允许离解物种在膨胀时重新达到平衡。此假设可与 CEA 同口径对照。
    equilibrium=False → 冻结流:组分锁定,只 gas.SP(无 equilibrate)。快(~10ms,
                        可在 Newton 关键路径用)。主室冻结与实际冻结位置的符合程度尚未验证。

    出口压力 p_e 不再借用理想 γ 估计,而是按 ρV(p_e)=ṁ/A_t/ε 二分求解,
    与给定面积比 ε 自洽。

    算法:
    1. 燃烧室态记下 s_c, h_c(等熵 + 能量守恒不变量)
    2. 平衡喉部:最大化等熵质量通量 ρV；冻结喉部沿用 V=a 二分
    3. 出口:二分 p 找 ρV(p_e)=ρ_t·V_t/ε → 自洽 p_e, V_e
    4. Isp_vac = (V_e + p_e·ε·C*/Pc)/g₀;Isp_SL = Isp_vac − p_amb·ε·C*/(Pc·g₀)

    返回 dict(含 Cstar/Ve/pe/Te/Isp_vac/Isp_SL/Cf_vac/Cf_SL),失败返回 None。
    """
    checkpoint()
    if not CANTERA_AVAILABLE:
        return None
    if freeze_at_throat and not equilibrium:
        raise ValueError("喉部冻结必须先按平衡流计算燃烧室至喉部。")
    flow_model = "frozen_throat" if freeze_at_throat else ("equilibrium" if equilibrium else "frozen")
    # Independent validation may supply a deliberately restricted species set
    # or a different thermochemical data file. Retain that same phase throughout
    # the nozzle; reconstructing the production phase would change the problem.
    gas = gas_override if gas_override is not None else _gas_for_state(chamber)

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
        _check_gas_model_range(gas, report=False)
        T   = gas.T
        rho = gas.density_mass
        h   = gas.enthalpy_mass
        V_sq = 2.0 * (h_chamber - h)              # 能量守恒 h_c = h + V²/2
        V    = _math.sqrt(max(V_sq, 0.0))
        gamma_local = gas.cp_mass / max(gas.cv_mass, 1.0e-3)
        a = _math.sqrt(max(gamma_local * p / max(rho, 1.0e-9), 0.0))
        return T, rho, V, a

    chamber_diagnostics = _nozzle_station(gas, equilibrium) if diagnostics else None
    # Equilibrium choking maximizes rho*sqrt(2*(h0-h)) on the equilibrium
    # isentrope. cp/cv is the frozen-composition derivative and cannot locate
    # an equilibrium throat. Keep the established frozen solver unchanged.
    p_lo, p_hi = 0.05 * p_chamber, 0.95 * p_chamber
    if equilibrium:
        p_lo, p_hi = 0.0001 * p_chamber, 0.9999 * p_chamber
        ratio = (_math.sqrt(5.0) - 1.0) / 2.0
        left = p_hi - ratio * (p_hi-p_lo)
        right = p_lo + ratio * (p_hi-p_lo)
        def mass_flux(p):
            # Reset the full state so optimizer order cannot change its objective.
            gas.TPY = chamber.temperature, p_chamber, Y_chamber
            _, density, velocity, _ = state_at(p)
            return density * velocity
        f_left, f_right = mass_flux(left), mass_flux(right)
        for _ in range(max(n_iter, 50)):
            checkpoint()
            if f_left < f_right:
                p_lo, left, f_left = left, right, f_right
                right = p_lo + ratio * (p_hi-p_lo)
                f_right = mass_flux(right)
            else:
                p_hi, right, f_right = right, left, f_left
                left = p_hi - ratio * (p_hi-p_lo)
                f_left = mass_flux(left)
            if p_hi-p_lo < p_chamber * 1e-8:
                break
    else:
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
    throat_diagnostics = _nozzle_station(gas, equilibrium) if diagnostics else None
    if freeze_at_throat:
        equilibrium = False  # gas retains the equilibrium throat composition

    if V_throat <= 1.0 or rho_throat <= 1.0e-6:
        if gas.name == "nDodecane_IG":
            raise ValueError("正十二烷喷管喉部求解无有效质量通量；不回退为常比热理想气体。")
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
        if p_hi - p_lo < (max(1e-6, p_mid*1e-8) if diagnostics else 1.0):
            break
    p_exit = 0.5 * (p_lo + p_hi)
    if eps == 1.0 and diagnostics:
        p_exit = p_throat
    T_exit, _, V_exit, _ = state_at(p_exit)
    _check_gas_model_range(gas)
    exit_diagnostics = _nozzle_station(gas, equilibrium) if diagnostics else None

    # 推力系数 + 比冲(真空 & 海平面)
    # C_F = F/(Pc·A_t);Isp = C*·C_F/g₀
    cf_vac = (V_exit * mdot_per_At + p_exit * eps) / p_chamber
    cf_sl  = (V_exit * mdot_per_At + (p_exit - p_amb) * eps) / p_chamber
    Isp_vac = Cstar * cf_vac / _G0
    Isp_sl  = Cstar * cf_sl  / _G0

    result = {
        "flow_model": flow_model,
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
    if diagnostics:
        result["stations"] = {"chamber": chamber_diagnostics, "throat": throat_diagnostics,
                              "exit": exit_diagnostics}
        result["diagnostics"] = {
            "throat_method": "equilibrium_mass_flux_maximum" if flow_model != "frozen" else "frozen_sonic_condition",
            "throat_sonic_residual": V_throat / throat_diagnostics["sound_speed_m_s"] - 1.0,
            "area_relative_residual": exit_diagnostics["density_kg_m3"] * V_exit * eps / mdot_per_At - 1.0,
            "energy_residual_Jkg": exit_diagnostics["enthalpy_Jkg"] + V_exit**2/2 - h_chamber,
            "entropy_residual_JkgK": exit_diagnostics["entropy_JkgK"] - s_chamber,
        }
    return result


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
        raise RuntimeError("Cantera 未安装；无可用热力学对比结果。")
    from webapp.thermo_compare import _cantera_point, normalize_request, _comparison_reactants
    from propellants import get_propellant
    request = normalize_request({"pc_pa": pc_pa, "T_fuel_K": T_fuel_K, "T_ox_K": T_ox_K,
                                 "mode": "equilibrium", "methane_thermo_dataset": active_methane_dataset()})
    result = _cantera_point(request, of, eps, _comparison_reactants(request, get_propellant()))
    return result["metrics"] | {"source": "cantera", "flow_model": "equilibrium",
        "gamma": result["metrics"]["gamma_chamber"], "MW_kg_kmol": result["metrics"]["MW_chamber_kg_kmol"],
        "stations": result["stations"], "diagnostics": result["diagnostics"]}


def _cea_perf_point(
    of: float,
    pc_bar: float,
    eps: float,
    T_fuel_K: float = 110.0,
    T_ox_K: float   = 90.0,
) -> "dict | None":
    """
    rocketcea 单点计算 -> SI 单位结果字典，或 None（rocketcea 不可用时）。

    入口化学焓、元素配比与 Cantera 一致；产品数据库/相范围仍未统一。

    rocketcea 默认英制输出：
        Isp_vac : s       (已是 SI，无需转换)
        C*      : ft/s -> m/s (*0.3048)
        Tc      : degR  -> K  (/1.8)
        MW      : g/mol = kg/kmol (无需转换)
    """
    checkpoint()
    if not ROCKETCEA_AVAILABLE:
        raise RuntimeError("RocketCEA 未安装；无可用 CEA 对比结果。")
    from webapp.thermo_compare import _cea_point, normalize_request, _comparison_reactants
    from propellants import get_propellant
    request = normalize_request({"pc_pa": pc_bar*1e5, "T_fuel_K": T_fuel_K, "T_ox_K": T_ox_K,
                                 "mode": "equilibrium", "methane_thermo_dataset": active_methane_dataset()})
    result = _cea_point(request, of, eps, _comparison_reactants(request, get_propellant()))
    return result["metrics"] | {"source": "cea", "flow_model": "equilibrium",
        "gamma": result["metrics"]["gamma_chamber"], "MW_kg_kmol": result["metrics"]["MW_chamber_kg_kmol"],
        "stations": result["stations"], "diagnostics": result["diagnostics"]}


def cross_validate_cea_cantera(
    of_list:   "list | None" = None,
    eps_list:  "list | None" = None,
    pc_pa:      float = 30e6,
    T_fuel_K:   float = 110.0,
    T_ox_K:     float = 90.0,
    methane_thermo_dataset: str | None = None,
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
            # Preserve the desktop table contract while retaining explicit status,
            # diagnostics and reasons from the auditable comparison implementation.
            from webapp.thermo_compare import compare_thermochemistry
            report = compare_thermochemistry({"of_values": [of], "eps_values": [eps],
                "pc_pa": pc_pa, "T_fuel_K": T_fuel_K, "T_ox_K": T_ox_K,
                "methane_thermo_dataset": methane_thermo_dataset or active_methane_dataset()})
            point = report["points"][0]
            ct_res = point["cantera"]["metrics"] if point["cantera"]["status"] == "ok" else None
            cea_res = point["cea"]["metrics"] if point["cea"]["status"] == "ok" else None

            rec = {
                "of":     of,
                "eps":    eps,
                "pc_bar": pc_bar,
                "cantera": ct_res,
                "cea":     cea_res,
                "validation": point,
                "cantera_status": point["cantera"]["status"],
                "cea_status": point["cea"]["status"],
                "cantera_error": point["cantera"]["reason"],
                "cea_error": point["cea"]["reason"],
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
