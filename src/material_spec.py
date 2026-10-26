"""
material_spec.py — 反向材料指标（工况 → 所需材料性能 → 选材/研发判定）

设计目的
═════════════════════════════════════════════════════════════════
数字样机此前只能做 *正向校核*：
    "我选了 GRCop-84，在这个热流下安全系数够不够？"
本模块补上反方向，也就是导师真正要的那一件事 —— *反向定指标*：
    "要扛住这个工况，理想材料的性能指标应该是多少？
     现有材料里有没有能满足的？没有的话，研发目标是什么？"

输入  ：一次 FFSCCycleModel.evaluate() 得到的 CycleResults（你已算出的工况）。
输出  ：每个关键部件一行《材料需求规格》ComponentSpec，内含
        · 主导失效模式（离心 / 热流-LCF / 承压 / 温度-氧化）
        · 工况点（T、p、q、U、ΔP …）
        · 反推出的"所需材料指标"（与具体材料无关的硬指标）
        · 对当前选用材料逐条判定（满足 / 临界 / 不满足）
        · 在材料库里筛出的候选材料
        · 若不满足 → 直接给出"研发指标"（缺口多少）

反向公式（全部来自现有正向模型的解析反解，量纲自洽）
═════════════════════════════════════════════════════════════════
旋转件（动叶 / 叶轮）—— 离心应力主导
    正向：σ_离心 = ρ·U²              （与 materials.material_audit_row 同口径）
    反向：σy(T) ≥ SF·ρ·U²   ⇔   比强度 σy/ρ ≥ SF·U²
          （比强度形式与材料无关，是最干净的"指标"）

冷却壁（推力室 / 喷管内壁）—— 热流 + 低周疲劳主导
    正向：T_wg = T_wc + q·t/k        （穿壁一维热阻，见 thermal_structural.py）
    反向：k ≥ q·t/(T_allow - T_wc)    （把热面温度压到许用值所需的最低热导率）
          选材品质因子 FoM = k·σy/(E·α)（越大越好；抗热应力 + 抗软化）
          热应力 σ_th = E·α·ΔT/(2(1-ν)) 通常 > σy → 每循环塑性 → 寿命由 LCF 定。

承压件（外壳 / 夹套 / 歧管）—— 环向应力主导
    正向：σ_hoop = p·r/t = p·(r/t)
    反向：σy(T) ≥ SF·p·(r/t)         （r/t 为设计长细比，缺省 15）

高温静子（静叶 / 喷嘴）—— 温度 + 抗氧化主导
    反向：T_max ≥ T_工况 + 裕度；富氧侧须氧兼容、Co<5%。

引用
═════════════════════════════════════════════════════════════════
  · NASA SP-125 Huzel & Huang；NASA SP-8110 涡轮；
  · Bartz NACA TN-3997（热流）；Coffin–Manson / Larson–Miller（寿命）；
  · ASTM G124（富氧自燃）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

from materials import (
    MATERIALS, get_material, TurbineMaterial,
    DEFAULT_OX_PUMP_MATERIALS, DEFAULT_FUEL_PUMP_MATERIALS,
    DEFAULT_CHAMBER_WALL_MATERIALS, DEFAULT_NOZZLE_WALL_MATERIALS,
)


# ════════════════════════════════════════════════════════════════
# 设计目标（可由调用方覆盖）
# ════════════════════════════════════════════════════════════════

@dataclass
class SpecTargets:
    """反向定指标用到的设计目标值。"""
    sf_rotating:     float = 1.5     # 旋转件离心应力安全系数（设计目标；最低 1.2）
    sf_pressure:     float = 1.5     # 承压件安全系数（= chamber_wall_safety_factor 缺省）
    sf_creep:        float = 1.5     # 持久强度安全系数（对蠕变）
    life_cycles:     int   = 100     # 复用寿命目标（点火-关机循环数）
    duty_per_cycle_s: float = 360.0  # 单次工作时长 [s]（估算累计高温持久时数）
    temp_margin_K:   float = 50.0    # 温度裕度（T_max 须高出工况这么多）
    cobalt_max_ox:   float = 0.05    # 富氧侧 Co 上限（ASTM G124 自燃判据）
    blade_taper_factor: float = 1.0  # σ=K·ρ·U² 的 K（与现有 audit 一致取 1.0；锥削叶可取 0.5）
    wall_T_allow_K:  float = 850.0   # 冷却壁热面许用温度（保 LCF 寿命；强度上限更高，约 973 K）
    wall_liner_t_m:  float = 0.5e-3  # 冷却壁热衬里厚度（= cooling.py _WALL_THICKNESS_M）
    bartz_correction: float = 1.0    # 喉部热流修正：1.0=用 cooling.py 原始 Bartz 峰值（与材料库 UI 一致）；
                                     #              0.6=与 thermal_structural 云图一致（边界层修正后）
    pressure_r_over_t: float = 15.0  # 承压壳长细比 r/t 设计假设
    marginal_band:   float = 0.15    # 裕度 < 15% 判为"临界"


# 布尔型指标（恰好达标即视为满足，不参与"临界"判定）
_BOOLEAN_PROPS = {"oxygen_compatible"}


# ════════════════════════════════════════════════════════════════
# 数据结构
# ════════════════════════════════════════════════════════════════

@dataclass
class RequiredProp:
    """一条"所需材料指标"。"""
    prop_id:   str            # 机器键（screening 用）：sigma_yield_MPa / specific_strength_kJkg / ...
    label:     str            # 人读名
    required:  float          # 阈值
    unit:      str
    cmp:       str = ">="     # ">=" 越大越好 / "<=" 越小越好
    T_eval:    float = 0.0    # 在此温度处取材料属性
    rationale: str = ""       # 反推依据（公式 + 代入值）
    hard:      bool = True    # 硬指标（参与判定）；False = 仅信息/数据缺


@dataclass
class ComponentSpec:
    """一个部件的《材料需求规格》。"""
    component:  str
    part_key:   str
    side:       str                                # "ox" / "fuel" / ""
    governing:  str
    op_point:   List[Tuple[str, float, str]] = field(default_factory=list)
    requirements: List[RequiredProp] = field(default_factory=list)
    current_material_key: str = ""
    # —— finalize() 填充 ——
    current_checks: List[Tuple[RequiredProp, float, Optional[bool], float]] = field(default_factory=list)
    candidates:     List[str] = field(default_factory=list)
    rd_targets:     List[str] = field(default_factory=list)
    verdict:        str = ""
    verdict_level:  str = ""                        # "ok" / "marginal" / "gap"
    notes:          List[str] = field(default_factory=list)


# ════════════════════════════════════════════════════════════════
# 材料实际值取数（screening 与判定共用）
# ════════════════════════════════════════════════════════════════

def _fom_cooled(m: TurbineMaterial, T_K: float) -> float:
    """冷却壁选材品质因子 FoM = k·σy(T)/(E·α)（越大越好）。"""
    E = max(m.youngs_modulus_GPa, 1e-6)          # GPa
    a = max(m.cte_1e6_per_K, 1e-6)               # 1e-6/K
    return m.thermal_conductivity_WmK * m.sigma_yield_at(T_K) / (E * a)


def material_actual(m: TurbineMaterial, prop_id: str, T_eval: float) -> float:
    """返回材料 m 在 prop_id 指标上的实际值；无数据返回 NaN。"""
    if prop_id == "sigma_yield_MPa":
        return m.sigma_yield_at(T_eval)
    if prop_id == "specific_strength_kJkg":
        return m.sigma_yield_at(T_eval) * 1.0e6 / max(m.density_kgm3, 1e-6) / 1.0e3
    if prop_id == "thermal_conductivity_WmK":
        return m.thermal_conductivity_WmK
    if prop_id == "T_max_use_K":
        return m.T_max_use_K
    if prop_id == "oxygen_compatible":
        return 1.0 if m.oxygen_compatible else 0.0
    if prop_id == "cobalt_frac":
        return m.cobalt_fraction
    if prop_id == "fom_cooled":
        return _fom_cooled(m, T_eval)
    if prop_id == "creep_rupture_MPa":
        return float("nan")            # 库中暂无持久强度曲线 → 标记数据缺
    return float("nan")


def _check_one(m: TurbineMaterial, rp: RequiredProp) -> Tuple[float, Optional[bool], float]:
    """对单条指标判定。返回 (实际值, 是否通过|None, 裕度%)。"""
    actual = material_actual(m, rp.prop_id, rp.T_eval)
    if math.isnan(actual):
        return actual, None, float("nan")           # 数据缺，不计入通过/失败
    if rp.cmp == ">=":
        passes = actual >= rp.required
        margin = (actual - rp.required) / abs(rp.required) * 100.0 if rp.required else float("inf")
    else:  # "<="
        passes = actual <= rp.required
        margin = (rp.required - actual) / abs(rp.required) * 100.0 if rp.required else float("inf")
    return actual, passes, margin


def _rd_line(rp: RequiredProp, actual: float) -> str:
    """把一条未满足的指标翻译成"研发目标"语句。"""
    if rp.prop_id == "oxygen_compatible":
        return f"【硬约束】{rp.label}：当前材料不满足，富氧流道接触件必须氧兼容（ASTM G124）"
    if math.isnan(actual):
        return f"{rp.label}：需 {rp.cmp} {rp.required:.3g} {rp.unit}（当前材料缺该项数据，建议补测）"
    if rp.cmp == ">=":
        gap = (rp.required - actual) / abs(actual) * 100.0 if actual else float("inf")
        return (f"{rp.label}：需 ≥ {rp.required:.4g} {rp.unit}"
                f"（现 {actual:.4g}，缺口 {gap:+.0f}%）")
    return (f"{rp.label}：需 ≤ {rp.required:.4g} {rp.unit}"
            f"（现 {actual:.4g}）")


# ════════════════════════════════════════════════════════════════
# 反向指标构造器（每类失效模式一个）
# ════════════════════════════════════════════════════════════════

def _spec_rotating(component: str, part_key: str, side: str,
                   T_op: float, U_ms: float, current_key: str,
                   tgt: SpecTargets, with_creep: bool = False) -> ComponentSpec:
    """旋转件（动叶 / 叶轮）：离心应力主导。"""
    K = tgt.blade_taper_factor
    # 比强度（与材料无关的核心指标）：σy/ρ ≥ SF·K·U²   [J/kg → kJ/kg]
    req_spec_kJkg = tgt.sf_rotating * K * U_ms * U_ms / 1.0e3
    reqs = [
        RequiredProp(
            "specific_strength_kJkg", "比强度 σy/ρ", req_spec_kJkg, "kJ/kg", ">=", T_op,
            rationale=(f"σ_离心 = K·ρ·U²，要求 σy ≥ SF·K·ρ·U² ⇔ "
                       f"σy/ρ ≥ SF·K·U² = {tgt.sf_rotating:.2f}×{K:.2f}×{U_ms:.0f}²"),
        ),
        RequiredProp(
            "T_max_use_K", "使用温度上限 T_max", T_op + tgt.temp_margin_K, "K", ">=", T_op,
            rationale=f"需 T_max ≥ T_工况({T_op:.0f}K) + 裕度{tgt.temp_margin_K:.0f}K",
        ),
    ]
    if side == "ox":
        reqs += [
            RequiredProp("oxygen_compatible", "富氧自燃兼容", 1.0, "(是/否)", ">=", T_op,
                         rationale="富氧流道接触件必须氧兼容（ASTM G124）"),
            RequiredProp("cobalt_frac", "钴含量 Co", tgt.cobalt_max_ox, "质量分数", "<=", T_op,
                         rationale="富氧侧 Co<5% 防自燃（ASTM G124）"),
        ]
    gov = "离心应力 + 高温" + ("（+蠕变持久）" if with_creep else "")
    spec = ComponentSpec(
        component=component, part_key=part_key, side=side, governing=gov,
        op_point=[("工况温度 T", T_op, "K"), ("叶片速度 U", U_ms, "m/s")],
        requirements=reqs, current_material_key=current_key,
    )
    if with_creep:
        # 蠕变/持久：用 σ_离心（取当前材料密度的量级）给出持久强度目标 + Larson-Miller 提示
        try:
            rho = get_material(current_key).density_kgm3
        except Exception:
            rho = 8400.0
        sigma_c = rho * U_ms * U_ms / 1.0e6
        t_hours = tgt.life_cycles * tgt.duty_per_cycle_s / 3600.0
        sigma_rupture_req = tgt.sf_creep * sigma_c
        lmp = T_op * (20.0 + math.log10(max(t_hours, 1e-3)))
        spec.requirements.append(RequiredProp(
            "creep_rupture_MPa", f"{t_hours:.1f}h 持久强度 σ_rupture", sigma_rupture_req,
            "MPa", ">=", T_op, hard=False,
            rationale=(f"复用 {tgt.life_cycles} 次×{tgt.duty_per_cycle_s:.0f}s ≈ {t_hours:.1f}h 高温持载；"
                       f"需 σ_rupture(T_in,{t_hours:.1f}h) ≥ SF·σ_离心 = {tgt.sf_creep:.1f}×{sigma_c:.0f}；"
                       f"Larson-Miller 参数 LMP≈{lmp:.0f}（C=20）作选材门槛")))
        spec.notes.append("持久/蠕变项：材料库暂无 Larson-Miller 曲线 → 建议补该数据后自动判定。")
    return spec


def _spec_cooled_wall(component: str, part_key: str,
                      q_W_m2: float, T_wc: float, current_key: str,
                      tgt: SpecTargets) -> ComponentSpec:
    """冷却壁（推力室 / 喷管内壁）：热流 + 低周疲劳主导。"""
    t = tgt.wall_liner_t_m
    T_allow = tgt.wall_T_allow_K
    head = T_allow - T_wc                          # 可用于穿壁的温差预算
    reqs: List[RequiredProp] = []
    notes: List[str] = []

    if head > 1.0:
        k_req = q_W_m2 * t / head
        reqs.append(RequiredProp(
            "thermal_conductivity_WmK", "热导率 k", k_req, "W/(m·K)", ">=", T_allow,
            rationale=(f"穿壁温差 ΔT=q·t/k；要把热面压到 T_allow={T_allow:.0f}K，"
                       f"需 k ≥ q·t/(T_allow−T_wc) = {q_W_m2/1e6:.0f}MW/m²×{t*1e3:.1f}mm/"
                       f"({T_allow:.0f}−{T_wc:.0f})K")))
    else:
        # 冷却侧背壁已逼近/超过许用温度 → 任何材料（哪怕 k=∞）都救不了
        notes.append(f"⚠ 冷却侧背壁 T_wc={T_wc:.0f}K ≥ 许用 T_allow={T_allow:.0f}K："
                     "再高的热导率也无法把热面压到许用值 —— 这是冷却设计不足，"
                     "须先加强冷却（提高 h_c / 降 T_coolant / 减薄壁），而非换材料。")
        reqs.append(RequiredProp(
            "thermal_conductivity_WmK", "热导率 k", 400.0, "W/(m·K)", ">=", T_allow, hard=False,
            rationale="冷却预算已耗尽，列纯铜级 k≈400 作参考门槛（实际须改冷却设计）"))

    reqs.append(RequiredProp(
        "T_max_use_K", "使用温度上限 T_max", T_allow, "K", ">=", T_allow,
        rationale=f"热面长期运行于 T_allow={T_allow:.0f}K，材料 T_max 须不低于此值"))
    reqs.append(RequiredProp(
        "fom_cooled", "选材品质因子 k·σy/(E·α)", _fom_cooled(get_material(DEFAULT_CHAMBER_WALL_MATERIALS["chamber_inner_wall"]), T_allow),
        "(相对)", ">=", T_allow, hard=False,
        rationale="抗热应力+抗软化综合指标（越大越好，门槛取 GRCop-84 当前水平作基准）"))

    spec = ComponentSpec(
        component=component, part_key=part_key, side="", governing="热流 + 低周疲劳(LCF)",
        op_point=[("喉部热流 q", q_W_m2 / 1e6, "MW/m²"),
                  ("冷却侧壁温 T_wc", T_wc, "K"),
                  ("热面许用 T_allow", T_allow, "K")],
        requirements=reqs, current_material_key=current_key, notes=notes,
    )

    # 对当前材料补算热应力与 LCF 量级（信息性，写入 notes）
    try:
        m = get_material(current_key)
        k = max(m.thermal_conductivity_WmK, 1e-6)
        dT = q_W_m2 * t / k
        T_wg = T_wc + dT
        E = m.youngs_modulus_GPa * 1e9
        alpha = m.cte_1e6_per_K * 1e-6
        nu = m.poisson_ratio
        sigma_th = E * alpha * dT / (2.0 * max(1.0 - nu, 1e-6)) / 1e6     # MPa
        sy = m.sigma_yield_at(T_wg)
        sf_static = sy / max(sigma_th, 1e-6)
        # 受约束热应变幅与塑性应变幅
        eps_mech = alpha * dT / max(1.0 - nu, 1e-6)
        eps_pl = max(0.0, eps_mech - 2.0 * sy * 1e6 / E)
        spec.op_point.append(("当前热面壁温 T_wg", T_wg, "K"))
        spec.notes.append(
            f"当前材料 {m.display_name}：ΔT_壁={dT:.0f}K，热面 T_wg={T_wg:.0f}K，"
            f"热应力 σ_th≈{sigma_th:.0f} MPa vs σy({T_wg:.0f}K)={sy:.0f} MPa → "
            f"静态 SF={sf_static:.2f}"
            + ("（<1：每次点火热面塑性，寿命由 LCF 决定，这是铜室的正常物理）"
               if sf_static < 1.0 else "（>1：静态弹性，寿命主要看 HCF/blanching）"))
        if eps_pl > 0:
            n_est = _coffin_manson_life(eps_pl, current_key)
            spec.notes.append(
                f"塑性应变幅 Δε_p≈{eps_pl*100:.2f}% → LCF 寿命估算 N_f≈{n_est:.0f} 次"
                f"（目标 {tgt.life_cycles} 次；Coffin-Manson 量级估算，需实测标定）。"
                f"研发抓手：提高 k·σy/(E·α)、抗 blanching、抗甲烷侧结焦。")
    except Exception as ex:
        spec.notes.append(f"当前材料热应力补算跳过：{ex}")
    return spec


def _spec_pressure(component: str, part_key: str, side: str,
                   p_Pa: float, T_op: float, current_key: str,
                   tgt: SpecTargets, r_over_t: Optional[float] = None) -> ComponentSpec:
    """承压件（外壳 / 夹套 / 歧管）：环向应力主导。"""
    rt = r_over_t if r_over_t is not None else tgt.pressure_r_over_t
    sigma_hoop = p_Pa * rt / 1e6                    # MPa
    req_sy = tgt.sf_pressure * sigma_hoop
    reqs = [
        RequiredProp("sigma_yield_MPa", "屈服强度 σy@工况", req_sy, "MPa", ">=", T_op,
                     rationale=(f"σ_hoop=p·(r/t)={p_Pa/1e6:.0f}MPa×{rt:.0f}={sigma_hoop:.0f}MPa；"
                                f"需 σy(T) ≥ SF·σ_hoop = {tgt.sf_pressure:.2f}×{sigma_hoop:.0f}")),
        RequiredProp("T_max_use_K", "使用温度上限 T_max", T_op + tgt.temp_margin_K, "K", ">=", T_op,
                     rationale=f"需 T_max ≥ T_工况({T_op:.0f}K)+裕度{tgt.temp_margin_K:.0f}K"),
    ]
    if side == "ox":
        reqs += [
            RequiredProp("oxygen_compatible", "富氧自燃兼容", 1.0, "(是/否)", ">=", T_op,
                         rationale="富氧承压壳须氧兼容（ASTM G124）"),
            RequiredProp("cobalt_frac", "钴含量 Co", tgt.cobalt_max_ox, "质量分数", "<=", T_op,
                         rationale="富氧侧 Co<5%"),
        ]
    return ComponentSpec(
        component=component, part_key=part_key, side=side, governing="承压环向应力",
        op_point=[("内压 p", p_Pa / 1e6, "MPa"), ("工况温度 T", T_op, "K"),
                  ("假设 r/t", rt, "")],
        requirements=reqs, current_material_key=current_key,
    )


def _spec_hot_static(component: str, part_key: str, side: str,
                     T_op: float, current_key: str, tgt: SpecTargets) -> ComponentSpec:
    """高温静子（静叶 / 喷嘴）：温度 + 抗氧化主导。"""
    reqs = [
        RequiredProp("T_max_use_K", "使用温度上限 T_max", T_op + tgt.temp_margin_K, "K", ">=", T_op,
                     rationale=f"高温燃气冲刷、无离心载荷；需 T_max ≥ T_in({T_op:.0f}K)+裕度{tgt.temp_margin_K:.0f}K"),
    ]
    if side == "ox":
        reqs += [
            RequiredProp("oxygen_compatible", "富氧自燃兼容", 1.0, "(是/否)", ">=", T_op,
                         rationale="富氧流道静子须氧兼容（ASTM G124）"),
            RequiredProp("cobalt_frac", "钴含量 Co", tgt.cobalt_max_ox, "质量分数", "<=", T_op,
                         rationale="富氧侧 Co<5%"),
        ]
    return ComponentSpec(
        component=component, part_key=part_key, side=side, governing="温度 + 抗氧化",
        op_point=[("涡轮入口温度 T_in", T_op, "K")],
        requirements=reqs, current_material_key=current_key,
    )


# Coffin-Manson 疲劳延性参数（按材料族给量级缺省；可日后移入 materials.py 精确化）
_CM_PARAMS = {
    "copper": (0.30, -0.50),       # (ε_f', c)：铜合金延性高
    "superalloy": (0.10, -0.60),   # 镍基超合金
    "steel": (0.20, -0.55),
}


def _cm_family(material_key: str) -> str:
    k = material_key.lower()
    if any(s in k for s in ("grcop", "narloy", "copper", "ofhc")):
        return "copper"
    if any(s in k for s in ("ti6al4v", "al70", "al22")):
        return "steel"
    return "superalloy"


def _coffin_manson_life(eps_pl: float, material_key: str) -> float:
    """Δε_p = ε_f'·(2N_f)^c  →  N_f。量级估算，需实测标定。"""
    ef, c = _CM_PARAMS[_cm_family(material_key)]
    if eps_pl <= 0:
        return 1e9
    return 0.5 * (eps_pl / ef) ** (1.0 / c)


# ════════════════════════════════════════════════════════════════
# 判定 + 选材筛选
# ════════════════════════════════════════════════════════════════

def _finalize(spec: ComponentSpec, tgt: SpecTargets) -> ComponentSpec:
    """对当前材料逐条判定，筛选候选材料，给出 verdict 与研发指标。"""
    try:
        m = get_material(spec.current_material_key)
    except Exception:
        spec.verdict = f"❌ 未知材料 {spec.current_material_key}"
        spec.verdict_level = "gap"
        return spec

    level = "ok"
    for rp in spec.requirements:
        actual, passes, margin = _check_one(m, rp)
        spec.current_checks.append((rp, actual, passes, margin))
        if not rp.hard:
            continue
        if passes is False:
            level = "gap"
            spec.rd_targets.append(_rd_line(rp, actual))
        elif (passes is True and rp.prop_id not in _BOOLEAN_PROPS
              and not math.isnan(margin) and margin < tgt.marginal_band * 100.0
              and level != "gap"):
            level = "marginal"

    spec.verdict_level = level
    spec.verdict = {
        "ok":       "✅ 现有材料满足设计目标",
        "marginal": "⚠ 临界（部分指标裕度 < 15%，建议升级或降载）",
        "gap":      "❌ 现有材料不满足 → 见研发指标",
    }[level]

    # 在材料库里筛出"硬指标全过"的候选（排除当前材料）
    for key, mm in MATERIALS.items():
        if key == spec.current_material_key:
            continue
        if spec.part_key not in mm.parts:
            continue
        ok = True
        for rp in spec.requirements:
            if not rp.hard:
                continue
            _, passes, _ = _check_one(mm, rp)
            if passes is False:
                ok = False
                break
        if ok:
            spec.candidates.append(key)
    return spec


# ════════════════════════════════════════════════════════════════
# 取工况（从 CycleResults 提取各部件工况点，带回退缺省）
# ════════════════════════════════════════════════════════════════

def _co(results, comp: str, key: str, default: float = float("nan")) -> float:
    c = results.components.get(comp)
    if not c:
        return default
    v = c.outputs.get(key, default)
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _sm(results, key: str, default: float = float("nan")) -> float:
    try:
        return float(results.summary.get(key, default))
    except (TypeError, ValueError):
        return default


# ════════════════════════════════════════════════════════════════
# 主入口：生成整机《材料需求规格书》
# ════════════════════════════════════════════════════════════════

def generate_material_spec_sheet(results, targets: Optional[SpecTargets] = None
                                 ) -> List[ComponentSpec]:
    """
    从一次 CycleResults（你已算出的工况）反推每个关键部件的材料需求规格。

    返回 ComponentSpec 列表，可直接喂给 format_report / export_markdown / export_csv。
    """
    tgt = targets or SpecTargets()
    inp = results.inputs
    specs: List[ComponentSpec] = []

    # —— 1/2. 富氧 / 富燃 涡轮动叶（离心 + 蠕变）——
    T_ox_t = _co(results, "ox_preburner_turbine", "T_in_K", 750.0)
    U_ox_t = _co(results, "ox_preburner_turbine", "U_mean_actual_ms", 350.0)
    specs.append(_spec_rotating("富氧涡轮 动叶", "rotor_blade", "ox",
                                T_ox_t, U_ox_t, inp.turb_ox_rotor_material, tgt, with_creep=True))
    T_fu_t = _co(results, "fuel_preburner_turbine", "T_in_K", 850.0)
    U_fu_t = _co(results, "fuel_preburner_turbine", "U_mean_actual_ms", 400.0)
    specs.append(_spec_rotating("富燃涡轮 动叶", "rotor_blade", "fuel",
                                T_fu_t, U_fu_t, inp.turb_fuel_rotor_material, tgt, with_creep=True))

    # —— 3/4. 液氧 / 甲烷 泵叶轮（离心）——
    T_ox_p = _co(results, "ox_pump", "T_out_K", 95.0)
    U_ox_p = _co(results, "ox_pump", "u_tip_est_ms", 350.0)
    specs.append(_spec_rotating("液氧泵 叶轮", "pump_impeller", "ox",
                                T_ox_p, U_ox_p, DEFAULT_OX_PUMP_MATERIALS["pump_impeller"], tgt))
    T_fu_p = _co(results, "fuel_pump", "T_out_K", 130.0)
    U_fu_p = _co(results, "fuel_pump", "u_tip_est_ms", 400.0)
    specs.append(_spec_rotating("甲烷泵 叶轮", "pump_impeller", "fuel",
                                T_fu_p, U_fu_p, DEFAULT_FUEL_PUMP_MATERIALS["pump_impeller"], tgt))

    # —— 5. 主燃烧室喉部内壁（热流 + LCF）——
    q_raw = _co(results, "cooling_channel", "q_throat_MW_m2", 80.0) * 1e6
    q_throat = q_raw * tgt.bartz_correction
    T_wc = _co(results, "cooling_channel", "T_wall_cold_K", 500.0)
    cw = _spec_cooled_wall("主燃烧室喉部内壁", "chamber_inner_wall",
                           q_throat, T_wc,
                           DEFAULT_CHAMBER_WALL_MATERIALS["chamber_inner_wall"], tgt)
    cw.notes.insert(0, (
        f"喉部热流 q={q_throat/1e6:.0f} MW/m²（cooling.py V9 自洽壁温解，已耦合 h_c 与壁温迭代"
        + (f"；×额外修正 {tgt.bartz_correction:.2f}" if abs(tgt.bartz_correction - 1.0) > 1e-6 else "")
        + "）。V9 已计入真实壁温（取代旧版冷壁假设的 Bartz 原始峰值），"
        "故 bartz_correction 保持 1.0 即可，无需再叠加 0.6。"))
    specs.append(cw)

    # —— 6. 主燃烧室外壳 / 结构夹套（承压）——
    pc = _sm(results, "chamber_pressure_Pa", inp.pc)
    rc = _co(results, "main_chamber", "cc_diameter_m", 0.3) / 2.0
    # V19：优先用分层壁厚的外壳分量（cycle 与几何模块同口径）；旧结果回退
    # "总厚 − 衬里" 估算（会把通道深也算进外壳、偏厚）。
    t_jacket = _co(results, "main_chamber", "cc_wall_jacket_mm", 0.0) / 1000.0
    if t_jacket <= 1e-4:
        t_total = _co(results, "main_chamber", "cc_wall_thickness_mm", 6.0) / 1000.0
        t_jacket = max(t_total - tgt.wall_liner_t_m, 2.0e-3)
    rt_jacket = rc / t_jacket
    specs.append(_spec_pressure("主燃烧室外壳/夹套", "chamber_outer_wall", "",
                                pc, 700.0, DEFAULT_CHAMBER_WALL_MATERIALS["chamber_outer_wall"],
                                tgt, r_over_t=rt_jacket))

    # —— 7/8. 富氧 / 富燃 涡轮静叶（温度 + 抗氧化）——
    specs.append(_spec_hot_static("富氧涡轮 静叶", "stator_vane", "ox",
                                  T_ox_t, inp.turb_ox_stator_material, tgt))
    specs.append(_spec_hot_static("富燃涡轮 静叶", "stator_vane", "fuel",
                                  T_fu_t, inp.turb_fuel_stator_material, tgt))

    return [_finalize(s, tgt) for s in specs]


# ════════════════════════════════════════════════════════════════
# 报告渲染（控制台 / Markdown / CSV）
# ════════════════════════════════════════════════════════════════

def _verdict_mark(level: str) -> str:
    return {"ok": "✅", "marginal": "⚠", "gap": "❌"}.get(level, "·")


def format_report(specs: List[ComponentSpec], targets: Optional[SpecTargets] = None) -> str:
    """生成可读文本报告（控制台用）。"""
    tgt = targets or SpecTargets()
    L: List[str] = []
    n_ok = sum(1 for s in specs if s.verdict_level == "ok")
    n_mg = sum(1 for s in specs if s.verdict_level == "marginal")
    n_gp = sum(1 for s in specs if s.verdict_level == "gap")
    L.append("═" * 78)
    L.append("  FFSC 数字样机 · 关键部件《材料需求规格书》（反向定指标）")
    L.append("═" * 78)
    L.append(f"  设计目标：旋转件SF≥{tgt.sf_rotating}　承压件SF≥{tgt.sf_pressure}　"
             f"复用寿命≥{tgt.life_cycles}次　温度裕度≥{tgt.temp_margin_K:.0f}K　富氧Co<{tgt.cobalt_max_ox*100:.0f}%")
    L.append(f"  汇总：{len(specs)} 个部件 → ✅满足 {n_ok}　⚠临界 {n_mg}　❌需研发 {n_gp}")
    L.append("")

    for s in specs:
        L.append("─" * 78)
        L.append(f"▌{s.component}　[{s.part_key}{('/'+s.side) if s.side else ''}]　主导：{s.governing}")
        L.append(f"  选用材料：{_safe_name(s.current_material_key)}　→　{s.verdict}")
        op = "　".join(f"{lab}={val:.4g}{u}" for lab, val, u in s.op_point)
        L.append(f"  工况点：{op}")
        L.append(f"  {'指标':<22}{'要求':<18}{'当前材料':<18}判定")
        for rp, actual, passes, margin in s.current_checks:
            req_s = f"{rp.cmp}{rp.required:.4g}{rp.unit}"
            if math.isnan(actual):
                act_s, mark = "数据缺", "·"
            else:
                act_s = f"{actual:.4g}{rp.unit}"
                mark = "✅" if passes else "❌"
                if (passes and rp.prop_id not in _BOOLEAN_PROPS
                        and not math.isnan(margin) and margin < tgt.marginal_band * 100):
                    mark = "⚠"
            tag = "" if rp.hard else "(参考)"
            L.append(f"    {rp.label:<20}{req_s:<18}{act_s:<18}{mark} {tag}")
        if s.rd_targets:
            L.append("  ▶ 研发指标（现有材料缺口 → 交材料组）：")
            for rd in s.rd_targets:
                L.append(f"      • {rd}")
        if s.candidates:
            cand = "、".join(_safe_name(k) for k in s.candidates[:5])
            L.append(f"  ▶ 库内候选（硬指标全过）：{cand}")
        elif s.verdict_level == "gap":
            L.append("  ▶ 库内候选：无 —— 需研发新材料或调整工况/冷却")
        for nt in s.notes:
            L.append(f"  · {nt}")
        L.append("")
    L.append("═" * 78)
    L.append("  注：本表为系统级一阶反推（NASA SP-125 / SP-8110 工程口径）。")
    L.append("      σy(T)/k 取自材料库；持久(蠕变)与 LCF 寿命为量级估算，详设计需实测曲线。")
    L.append("═" * 78)
    return "\n".join(L)


def _safe_name(key: str) -> str:
    try:
        return get_material(key).display_name
    except Exception:
        return key


def export_markdown(specs: List[ComponentSpec], path: str,
                    targets: Optional[SpecTargets] = None) -> str:
    """导出 Markdown 规格书（适合贴论文/交导师）。返回写入路径。"""
    tgt = targets or SpecTargets()
    L: List[str] = []
    L.append("# FFSC 数字样机 · 关键部件材料需求规格书（反向定指标）\n")
    L.append(f"**设计目标**：旋转件 SF≥{tgt.sf_rotating}；承压件 SF≥{tgt.sf_pressure}；"
             f"复用寿命≥{tgt.life_cycles} 次；温度裕度≥{tgt.temp_margin_K:.0f} K；"
             f"富氧侧 Co<{tgt.cobalt_max_ox*100:.0f}%。\n")
    L.append("| 部件 | 主导模式 | 工况 | 当前材料 | 判定 | 关键研发指标 |")
    L.append("|---|---|---|---|---|---|")
    for s in specs:
        op = "；".join(f"{lab}={val:.4g}{u}" for lab, val, u in s.op_point)
        rd = "；".join(s.rd_targets) if s.rd_targets else "—"
        L.append(f"| {s.component} | {s.governing} | {op} | {_safe_name(s.current_material_key)} "
                 f"| {_verdict_mark(s.verdict_level)} | {rd} |")
    L.append("\n## 逐部件详表\n")
    for s in specs:
        L.append(f"### {s.component}　{s.verdict}\n")
        L.append("| 指标 | 要求 | 当前材料 | 判定 | 反推依据 |")
        L.append("|---|---|---|---|---|")
        for rp, actual, passes, margin in s.current_checks:
            act_s = "数据缺" if math.isnan(actual) else f"{actual:.4g}{rp.unit}"
            mark = "·" if passes is None else ("✅" if passes else "❌")
            L.append(f"| {rp.label}{'' if rp.hard else '(参考)'} | {rp.cmp}{rp.required:.4g}{rp.unit} "
                     f"| {act_s} | {mark} | {rp.rationale} |")
        if s.candidates:
            L.append(f"\n**库内候选**：{('、'.join(_safe_name(k) for k in s.candidates)) }\n")
        for nt in s.notes:
            L.append(f"> {nt}\n")
    text = "\n".join(L)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def export_csv(specs: List[ComponentSpec], path: str) -> str:
    """导出扁平 CSV（每条指标一行），便于 Excel/进一步处理。返回写入路径。"""
    import csv
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["部件", "part_key", "side", "主导模式", "判定",
                    "指标", "比较", "要求值", "单位", "当前材料值", "通过", "裕度%", "反推依据"])
        for s in specs:
            for rp, actual, passes, margin in s.current_checks:
                w.writerow([
                    s.component, s.part_key, s.side, s.governing, s.verdict_level,
                    rp.label, rp.cmp, f"{rp.required:.6g}", rp.unit,
                    "" if math.isnan(actual) else f"{actual:.6g}",
                    "" if passes is None else ("Y" if passes else "N"),
                    "" if math.isnan(margin) else f"{margin:.1f}",
                    rp.rationale,
                ])
    return path


# ════════════════════════════════════════════════════════════════
# 燃烧室铜合金对比（耦合冷却）：同一工况下各铜合金能不能扛住
# ════════════════════════════════════════════════════════════════

def _fmt_life(n: float) -> str:
    if n >= 1e8:
        return "充裕"
    if n >= 1e4:
        return f"{n/1e3:.0f}k"
    return f"{n:.0f}"


def compare_chamber_liner_materials(results, candidate_keys: Optional[List[str]] = None,
                                    targets: Optional[SpecTargets] = None,
                                    coolant_T_throat: Optional[float] = None,
                                    h_c_throat: Optional[float] = None):
    """Compare candidate responses to saved throat thermal loads, without a thermal solve.

    Changing material conductivity changes the coupled thermal state and therefore
    requires a complete cycle rerun. This table is only a frozen-load screening.
    Legacy boundary overrides are rejected rather than replacing saved evidence.
    """
    if coolant_T_throat is not None or h_c_throat is not None:
        raise ValueError("材料比较只读取保存的冷却载荷；改变热边界需重算完整循环。")
    from chamber_design import saved_cooling_result
    profile = saved_cooling_result(results)["profile"]
    cells = [c for c in profile.get("cells", []) if c.get("kind") == "channel"]
    if not cells:
        raise ValueError("统一冷却剖面未保存通道单元；请重算完整循环。")
    throat_x = float(profile.get("x_throat_m", profile.get("summary", {}).get("x_throat_m", float("nan"))))
    if not math.isfinite(throat_x):
        raise ValueError("统一冷却剖面缺少喉部坐标。")
    cell = min(cells, key=lambda c: abs(c["x_m"] - throat_x))
    keys = ("h_g_W_m2K", "h_c_W_m2K", "T_coolant_K", "wall_thickness_m",
            "T_wall_hot_K", "T_wall_cold_K", "q_W_m2")
    if any(not isinstance(cell.get(k), (int, float)) or not math.isfinite(cell[k]) for k in keys):
        raise ValueError("保存的喉部冷却载荷不完整，不能使用猜测值代替。")
    tgt = targets or SpecTargets()
    h_g, h_c, T_co, t, T_wg, T_wc, q = (float(cell[k]) for k in keys)
    T_aw = float((cell.get("hot_side") or {}).get("T_aw_K", float("nan")))

    try:
        from thermal_structural import _k_of_T
    except Exception:
        def _k_of_T(m, T):
            return m.thermal_conductivity_WmK or 25.0

    if candidate_keys is None:
        candidate_keys = [k for k, m in MATERIALS.items()
                          if "chamber_inner_wall" in m.parts]

    rows = []
    for key in candidate_keys:
        try:
            m = get_material(key)
        except Exception:
            continue
        # All candidates use the same recorded temperatures and flux. No k(T) heat solve.
        dT = T_wg - T_wc
        E = m.youngs_modulus_GPa * 1e9
        al = m.cte_1e6_per_K * 1e-6
        nu = m.poisson_ratio
        sigma_th = E * al * dT / (2.0 * max(1.0 - nu, 1e-6)) / 1e6
        sy = m.sigma_yield_at(T_wg)
        sf = sy / max(sigma_th, 1e-6)
        eps_mech = al * dT / max(1.0 - nu, 1e-6)
        eps_pl = max(0.0, eps_mech - 2.0 * sy * 1e6 / E)
        N_f = _coffin_manson_life(eps_pl, key) if eps_pl > 0 else 1e9
        k_op = _k_of_T(m, T_wg)

        if T_wg > m.T_max_short_K:
            verdict = "记录温度超过短时材料上限"; level = "gap"
        elif T_wg > m.T_max_use_K:
            verdict = "记录温度超过长期材料上限"; level = "marginal"
        elif N_f < tgt.life_cycles:
            verdict = f"简化 LCF 估计低于目标({_fmt_life(N_f)}<{tgt.life_cycles})"; level = "marginal"
        else:
            verdict = "未触发本表筛查限值（未经验证）"; level = "ok"

        rows.append({
            "key": key, "name": m.display_name,
            "name_short": m.name.split("（")[0].split(" (")[0].strip(),
            "k_op": k_op, "q_MW": q / 1e6, "T_wg": T_wg, "T_wc": T_wc, "dT": dT,
            "T_max_use": m.T_max_use_K, "T_max_short": m.T_max_short_K,
            "T_margin": m.T_max_use_K - T_wg,
            "sigma_th": sigma_th, "sigma_y": sy, "SF": sf, "N_f": N_f,
            "verdict": verdict, "level": level,
        })

    order = {"ok": 0, "marginal": 1, "gap": 2}
    rows.sort(key=lambda r: (order[r["level"]], -r["T_margin"]))
    op = {"q_throat_MW": q / 1e6, "source": "saved_cooling_throat_load",
          "h_c": h_c, "T_co": T_co, "T_aw": T_aw, "h_g": h_g, "t_mm": t * 1000.0,
          "life_target": tgt.life_cycles}
    return rows, op


def format_liner_comparison(rows: List[dict], op: dict) -> str:
    """铜合金对比文本表。"""
    L: List[str] = []
    L.append("═" * 90)
    L.append("  燃烧室内壁材料筛查（保存的喉部热负荷，未经结构验证）")
    L.append("═" * 90)
    L.append(f"  当前设计保存的喉部工况：q≈{op['q_throat_MW']:.0f} MW/m²　T_aw={op['T_aw']:.0f}K　"
             f"h_g={op['h_g']:.1e}　h_c={op['h_c']:.1e}　冷却剂T={op['T_co']:.0f}K　衬里厚={op['t_mm']:.2f}mm")
    L.append("  所有候选材料使用相同的保存壁温与热流。更换材料后必须重算完整循环，本表不预测新的壁温。")
    L.append("")
    L.append(f"  {'材料':<13}{'k@工况':>7}{'热面T_wg':>9}{'温度上限':>9}{'温度余量':>9}{'静态SF':>8}{'LCF寿命':>8}  判定")
    L.append("  " + "─" * 86)
    for r in rows:
        L.append(f"  {r['name_short']:<13}{r['k_op']:>7.0f}{r['T_wg']:>9.0f}{r['T_max_use']:>9.0f}"
                 f"{r['T_margin']:>+9.0f}{r['SF']:>8.2f}{_fmt_life(r['N_f']):>8}  {r['verdict']}")
    L.append("")
    L.append("  静态 SF 和 LCF 仅为简化筛查估计，不构成材料选型、疲劳寿命或结构验证结论。")
    L.append("  材料温度限值及模型仍需核实；改变材料后的导热反馈需完整循环重算。")
    L.append("═" * 90)
    return "\n".join(L)


def plot_liner_comparison(fig, rows: List[dict], op: dict) -> None:
    """铜合金对比柱状图：各合金热面壁温 T_wg(柱) vs 各自温度上限(黑帽线)。

    柱高 = 热面壁温；黑实线帽 = 长期 T_max；灰点线帽 = 短时 T_max。
    柱顶低于黑帽 → 扛得住(绿)；高于黑帽 → 超限(红)。一眼看清能不能扛。
    """
    import numpy as np
    from matplotlib.patches import Patch
    from matplotlib.lines import Line2D

    fig.clear()
    fig.patch.set_facecolor("#FAFAFA")
    if not rows:
        ax = fig.add_subplot(111); ax.axis("off")
        ax.text(0.5, 0.5, "无对比数据", ha="center", va="center",
                transform=ax.transAxes, color="#9CA3AF")
        return
    ax = fig.add_subplot(111)
    colors = {"ok": "#15803D", "marginal": "#B45309", "gap": "#B91C1C"}
    x = np.arange(len(rows))
    twg = [r["T_wg"] for r in rows]
    ax.bar(x, twg, width=0.6, color=[colors[r["level"]] for r in rows], alpha=0.85, zorder=3)
    for i, r in enumerate(rows):
        ax.hlines(r["T_max_use"], i - 0.36, i + 0.36, color="#111827", lw=2.4, zorder=5)
        ax.hlines(r["T_max_short"], i - 0.30, i + 0.30, color="#6B7280", lw=1.2, ls=":", zorder=5)
        ax.annotate(f"k={r['k_op']:.0f}\nSF={r['SF']:.2f}\nLCF {_fmt_life(r['N_f'])}",
                    (i, twg[i]), textcoords="offset points", xytext=(0, 6),
                    ha="center", fontsize=7.8, color="#374151")
    ax.axhline(op["T_co"], color="#2563eb", ls="--", lw=1.0, zorder=2)
    ax.annotate(f"冷却剂 {op['T_co']:.0f}K", (len(rows) - 0.5, op["T_co"]),
                fontsize=8, color="#2563eb", va="bottom", ha="right")
    ax.set_xticks(x)
    ax.set_xticklabels([r["name_short"] for r in rows], fontsize=9)
    ax.set_ylabel("温度 [K]", fontsize=9.5)
    ax.set_title("保存的同一热负荷下材料筛查：壁温 T_wg vs 各自温度上限\n"
                 f"(喉部 q≈{op['q_throat_MW']:.0f} MW/m²  ·  h_c={op['h_c']:.1e}  ·  "
                 f"冷却剂 {op['T_co']:.0f}K  ·  衬里 {op['t_mm']:.2f}mm)",
                 fontsize=11, fontweight="bold", color="#0f172a")
    ax.set_ylim(0, max(max(twg), max(r["T_max_short"] for r in rows)) * 1.18)
    legend = [
        Patch(facecolor="#15803D", label="未触发筛查限值"),
        Patch(facecolor="#B45309", label="触发筛查限值"),
        Patch(facecolor="#B91C1C", label="超短时温度限值"),
        Line2D([0], [0], color="#111827", lw=2.4, label="长期 T_max"),
        Line2D([0], [0], color="#6B7280", ls=":", lw=1.2, label="短时 T_max"),
    ]
    ax.legend(handles=legend, fontsize=8, ncol=3, loc="upper center", framealpha=0.9)
    ax.grid(True, axis="y", alpha=0.25)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.subplots_adjust(left=0.09, right=0.97, top=0.86, bottom=0.09)


# ════════════════════════════════════════════════════════════════
# 独立调试入口
# ════════════════════════════════════════════════════════════════

def demo():
    """求解一次循环 → 反推材料指标 → 打印报告 + 导出 txt/md/csv。"""
    import sys
    import warnings
    warnings.simplefilter("ignore")
    try:                                   # Windows 控制台默认 GBK，切到 UTF-8 才能显示 ✅/❌
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import core
    import cycle

    inp = core.EngineInputs()
    results = cycle.FFSCCycleModel(inp).evaluate()
    specs = generate_material_spec_sheet(results)
    report = format_report(specs)
    with open("material_spec_report.txt", "w", encoding="utf-8") as f:
        f.write(report)
    try:
        print(report)
    except UnicodeEncodeError:
        print("(控制台编码受限，完整报告见 material_spec_report.txt)")
    md = export_markdown(specs, "material_spec_sheet.md")
    cs = export_csv(specs, "material_spec_sheet.csv")
    print(f"\n已导出：material_spec_report.txt\n已导出：{md}\n已导出：{cs}")


if __name__ == "__main__":
    demo()
