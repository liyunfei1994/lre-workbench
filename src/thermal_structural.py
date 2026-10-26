"""Display the cycle's saved cooling thermal loads and estimate structural response.

The cooling solver lives in cooling.py. This module never recomputes heat flux,
pressure loss or coolant outlet state. Simple stress/material estimates are
screening aids, not CFD/FEA, fatigue qualification or structural validation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


def _k_of_T(mat, T_K: float) -> float:
    """内壁材料热导率 k(T) [W/(m·K)]。

    V19 口径统一：委托给 materials.TurbineMaterial.thermal_conductivity_at
    （grcop84 手册回归式 / 铜族通用缩放 / 常数，全项目唯一 k(T) 入口）。
    保留本包装函数是因为 fea_chamber 等旧调用点引用它。
    """
    if hasattr(mat, "thermal_conductivity_at"):
        return mat.thermal_conductivity_at(T_K) or 25.0
    return mat.thermal_conductivity_WmK or 25.0


def _is_copper(mat) -> bool:
    if hasattr(mat, "_is_copper_family"):
        return mat._is_copper_family()
    k0 = mat.thermal_conductivity_WmK or 25.0
    key = (getattr(mat, "key", "") or "").lower()
    return (k0 > 150.0) or any(s in key for s in ("grcop", "narloy", "copper", "ofhc"))


def _E_of_T(mat, T_K: float) -> float:
    """弹性模量 E(T) [Pa]。V19 口径统一：委托 materials.youngs_modulus_at。"""
    if hasattr(mat, "youngs_modulus_at"):
        return mat.youngs_modulus_at(T_K)
    return mat.youngs_modulus_GPa * 1e9


def _wall_stress(mat, T_wg: float, dT_wall: float,
                 dP_liner_Pa: float, w_chan_m: float, t_liner_m: float):
    """单站衬里应力分解 [MPa]。

    返回 (σ_th, σ_p, σ_eq, σ_y, SF)：
      σ_th : 穿壁线性温度梯度的弯曲热应力 = E(T)·α·ΔT / (2(1−ν))  （Timoshenko）
      σ_p  : 冷却剂-燃气压差把薄衬里压向通道（夹支板/梁弯曲）
             = ½·ΔP·(w/t)²  （clamped-clamped 梁最大弯曲应力，LCF/doghouse 驱动）
      σ_eq : 一阶保守叠加 |σ_th| + σ_p
      σ_y  : T_wg 处屈服；SF = σ_y/σ_eq
    """
    E = _E_of_T(mat, T_wg)
    alpha = mat.cte_1e6_per_K * 1e-6
    nu = mat.poisson_ratio
    sigma_th = E * alpha * dT_wall / (2.0 * max(1.0 - nu, 1e-6)) / 1e6
    dP = max(dP_liner_Pa, 0.0)
    wt = w_chan_m / max(t_liner_m, 1e-4)
    sigma_p = 0.5 * dP * wt * wt / 1e6
    sigma_eq = abs(sigma_th) + sigma_p
    sigma_y = mat.sigma_yield_at(T_wg)
    SF = sigma_y / max(sigma_eq, 1e-6)
    return sigma_th, sigma_p, sigma_eq, sigma_y, SF


@dataclass
class WallField:
    """沿轴向的壁面热-结构场 + 2D 穿壁场,供绘图。"""
    x:        List[float] = field(default_factory=list)   # 轴向坐标 [m]
    r:        List[float] = field(default_factory=list)   # 内壁半径 [m]
    h_g:      List[float] = field(default_factory=list)   # 燃气侧换热 [W/m²K]
    T_aw:     List[float] = field(default_factory=list)   # 绝热壁温 [K]
    q:        List[float] = field(default_factory=list)   # 总热流密度 [W/m²]
    q_conv:   List[float] = field(default_factory=list)   # 对流热流 [W/m²]
    q_rad:    List[float] = field(default_factory=list)   # 辐射热流 [W/m²]
    T_wg:     List[float] = field(default_factory=list)   # 燃气侧壁温 [K]
    T_wc:     List[float] = field(default_factory=list)   # 冷却侧壁温 [K]
    dT_wall:  List[float] = field(default_factory=list)   # 穿壁温差 [K]
    sigma_th: List[float] = field(default_factory=list)   # 热应力 [MPa]
    sigma_p:  List[float] = field(default_factory=list)   # 衬里压差弯曲应力 [MPa]
    sigma_hoop: List[float] = field(default_factory=list) # 环向压力应力 [MPa]
    sigma_eq: List[float] = field(default_factory=list)   # 等效应力 [MPa]
    sigma_y:  List[float] = field(default_factory=list)   # 该温度屈服 [MPa]
    SF:       List[float] = field(default_factory=list)   # 安全系数
    # ── C 新增：冷却剂沿程 marching 场 ──
    h_c:       List[float] = field(default_factory=list)  # 冷却侧换热 [W/m²K]（逐站算出）
    T_coolant: List[float] = field(default_factory=list)  # 局部冷却剂体温度 [K]（沿程累加）
    p_coolant: List[float] = field(default_factory=list)  # 局部冷却剂压力 [Pa]（沿程摩擦降）
    rho_coolant: List[float] = field(default_factory=list) # 局部冷却剂密度 [kg/m³]
    v_coolant: List[float] = field(default_factory=list)  # 局部冷却剂流速 [m/s]
    # ── V19 新增：沿程通道几何 + 燃气静压（推力室设计工作区 / 任意站位 FEM 用）──
    p_gas:     List[float] = field(default_factory=list)  # 局部燃气静压 [Pa]
    w_chan:    List[float] = field(default_factory=list)  # 局部通道宽 [m]
    h_chan:    List[float] = field(default_factory=list)  # 局部通道深 [m]
    n_chan:    List[float] = field(default_factory=list)  # 局部通道数（分叉后变化）
    t_liner:   List[float] = field(default_factory=list)  # 局部内衬厚 [m]
    # 标量诊断
    material_name: str = ""
    t_wall_m: float = 0.0
    throat_index: int = 0
    peak_T_wg: float = 0.0
    peak_sigma_eq: Optional[float] = 0.0
    min_SF: Optional[float] = 0.0
    structural_status: str = "legacy_screening"
    structural_reason: str = "历史材料简化筛查，未经结构验证"
    coolant_out_K: float = 0.0                            # 冷却剂出口温度 [K]
    coolant_in_K: float = float("nan")                   # 保存的入口端口温度
    coolant_in_Pa: float = float("nan")                  # 保存的入口端口压力
    coolant_out_Pa: float = float("nan")                 # 保存的出口端口压力
    coolant_dp_Pa: float = float("nan")                  # 保存的端口压差，非沿程极差
    coolant_flow_direction: str = ""
    mat_T_max_use_K: float = 0.0                          # 内壁材料长期温度上限 [K]
    q_rad_fraction: float = 0.0                           # 辐射占总热载比例
    source: str = "marching-1d"                           # 数据来源标识
    notes: List[str] = field(default_factory=list)
    loads: Any = None                                     # ChamberLoadSet 载荷清单


@dataclass
class ChamberLoadSet:
    """主燃烧室壁载荷清单（L2 稳态机械+热载荷，不含疲劳/裂纹）。

    把"作用在壁上的载荷"显式列清楚——这是结构分析（解析或后续 FEA）的输入
    边界条件，必须先确定。区分两类承载件：
      · 热衬里 GRCop-84（薄铜内壁，~0.5 mm）—— 承热载荷 + 衬里压差；
      · 结构夹套 Inconel-718（外壳）—— 承环向压力 + 轴向推力。
    """
    domain: str = "chamber"
    liner_material: str = ""
    jacket_material: str = ""
    # 热载荷（驱动热衬里）
    q_peak_MW_m2: float = 0.0
    dT_wall_peak_K: float = 0.0
    T_wg_peak_K: float = 0.0
    sigma_th_peak_MPa: Optional[float] = 0.0
    # 压力载荷
    p_gas_MPa: float = 0.0
    p_coolant_max_MPa: float = 0.0
    dP_liner_peak_MPa: float = 0.0
    sigma_p_peak_MPa: float = 0.0
    # 衬里综合
    sigma_eq_peak_MPa: Optional[float] = 0.0
    liner_min_SF: Optional[float] = 0.0
    liner_structural_status: str = "legacy_screening"
    liner_structural_reason: str = "历史材料简化筛查，未经结构验证"
    liner_thickness_mm: float = 0.0
    # 结构夹套
    jacket_thickness_mm: float = 0.0
    jacket_hoop_MPa: float = 0.0
    jacket_axial_MPa: float = 0.0
    jacket_SF: float = 0.0
    thrust_kN: float = 0.0

    def to_rows(self):
        """返回载荷清单表行：(载荷, 作用对象, 量值, 产生的应力/裕度)。"""
        def assessed(value, pattern):
            return pattern.format(value) if value is not None else "未评估（材料结构数据未对应）"
        return [
            ("① 热载荷 ΔT_wall（穿壁温差）", f"热衬里 {self.liner_material}",
             f"{self.dT_wall_peak_K:.0f} K（热流峰 {self.q_peak_MW_m2:.0f} MW/m²）",
             assessed(self.sigma_th_peak_MPa, "σ_th = {:.0f} MPa")),
            ("② 衬里压差 ΔP=p_coolant−p_gas", f"热衬里 {self.liner_material}",
             f"{self.dP_liner_peak_MPa:.0f} MPa（肋跨弯曲）",
             f"σ_p = {self.sigma_p_peak_MPa:.0f} MPa"),
            ("③ 环向压力（夹套包容冷却剂）", f"结构夹套 {self.jacket_material}",
             f"p_coolant = {self.p_coolant_max_MPa:.0f} MPa",
             f"σ_hoop = {self.jacket_hoop_MPa:.0f} MPa（SF {self.jacket_SF:.2f}）"),
            ("④ 轴向推力", f"结构夹套 {self.jacket_material}",
             f"F = {self.thrust_kN:.0f} kN",
             f"σ_axial ≈ {self.jacket_axial_MPa:.0f} MPa"),
            ("⑤ 衬里综合（①+②）", f"热衬里 {self.liner_material}",
             assessed(self.sigma_eq_peak_MPa, "σ_eq = {:.0f} MPa"),
             assessed(self.liner_min_SF, "min SF = {:.2f}")),
        ]


def _liner_structural_scope(profile, inner_material_key):
    """Prevent a new thermal assignment from silently inheriting old strength.

    The only retained explicit compatibility is the historical GRCop-84
    extruded data category with its corresponding legacy material model. Even
    that remains an approximate screening calculation, not qualification.
    Saved snapshots are inspected; the current data catalog is not substituted.
    """
    channels = [cell for cell in profile.get("cells", []) if cell.get("kind") == "channel"]
    explicit = ("material_applicability" in profile or "material_assignment" in profile
                or any("material" in cell for cell in channels))
    if not explicit:
        return "legacy_screening", "历史材料简化筛查；记录未保存独立材料热物性身份", ""
    evidence = profile.get("material_applicability") or {}
    snapshots = evidence.get("datasets") or []
    snapshots = [row for row in snapshots if isinstance(row, dict)]
    name = " / ".join(str(row.get("label") or row.get("material_id") or "未记录材料")
                      for row in snapshots) or "保存的内衬材料（数据不完整）"
    records = [cell.get("material") for cell in channels]
    if not records or any(not isinstance(row, dict) for row in records):
        return "not_assessed", "保存的逐单元材料证据不完整，未叠加历史材料强度", name
    by_id = {row.get("dataset_id"): row for row in snapshots}
    ids = {row.get("dataset_id") for row in records}
    if None in ids or any(key not in by_id for key in ids):
        return "not_assessed", "缺少与单元数据集对应的材料快照，未叠加历史材料强度", name
    expected = "grcop84_ellis2000_extruded_high_fe_v1"
    state = "powder_metallurgy_extruded_high_fe"
    if inner_material_key != "grcop84" or ids != {expected} or any(
            row.get("material_id") != "grcop84" or row.get("manufacturing_state_id") != state
            for row in records) or by_id[expected].get("material_id") != "grcop84" or (
                by_id[expected].get("manufacturing_state") or {}).get("state_id") != state:
        return "not_assessed", "热物性材料或分区与历史结构材料不对应；缺少相应强度数据", name
    if any(row.get("manufacturing_match") != "matched" for row in records):
        return "not_assessed", "方案制造状态未对应文献试样状态，未叠加历史材料强度", name
    if any(row.get("status") != "within_range" for row in records):
        return "not_assessed", "材料热物性越界或记录不完整，未据此给出结构裕度", name
    return ("screening_only", "仅对应历史 GRCop-84 挤压态类别的简化筛查；"
            "制造类别相同与热物性温区内均不代表结构或寿命资格已验证", name)


def compute_chamber_wall_field(
    summary: Dict[str, Any],
    geometry: Dict[str, Any],
    cooling_outputs: Optional[Dict[str, Any]] = None,
    inner_material_key: str = "grcop84",
    outer_material_key: str = "inconel718_wall",      # V18：结构夹套材料(用户在材料库可选)
    coolant_temp_K: float = 300.0,       # 冷却剂入口温回退值(优先用 cooling_outputs.T_in_K)
    h_coolant_W_m2K: float = 2.0e5,      # h_c 回退值(marching 关闭/失败时用)
    t_liner_m: float = 1.0e-3,           # 热衬里厚度(铜内壁),非结构总壁厚;典型 0.8–1.5 mm
    bartz_correction: float = 1.0,       # h_g 缩放(V9 喉部已自洽含壁温修正,默认 1.0 不再叠加)
    n_axial: int = 120,
    mdot_coolant: Optional[float] = None,    # C：冷却剂流量(缺则取 summary.mdot_fuel_kg_s)
    coolant_inlet_end: str = "nozzle",       # C：冷却剂入口端 "nozzle"(逆流)/"injector"(顺流)
    march_coolant: bool = True,              # C：沿程逐站 marching 开关
) -> Optional[WallField]:
    """Removed duplicate thermal solve; consume the cycle's saved profile instead."""
    raise RuntimeError(
        "独立冷却后处理求解已停用；请重算完整循环，并用 "
        "wall_field_from_profile(cooling_channel.profile, ...) 读取同源热负荷。")


def wall_field_from_profile(
    profile: Dict[str, Any],
    summary: Dict[str, Any],
    geometry: Dict[str, Any],
    inner_material_key: str = "grcop84",
    outer_material_key: str = "inconel718_wall",     # V18：结构夹套材料（用户在材料库可选）
    domain: str = "chamber",
) -> Optional[WallField]:
    """从循环保存的 cooling_channel.profile 构建 WallField。

    传热/流动场（q、T_wg/T_wc/T_co、p_co、h_g/h_c、ρ、V、辐射分解）直接取自
    同一冷却沿程结果；本函数不重新求解换热或流动，只叠加结构估计和载荷清单。
    简化应力、材料与寿命估计不代表结构验证。数据不足时返回 None。

    domain
    ──────
    "chamber" : 只取喷注面→喉部（主燃烧室段）。
    "full"    : 保存的全冷却覆盖区域。显式材料数据与历史结构材料不对应时，
                保留热流与流动载荷，结构材料相关指标显示未评估。
    """
    from materials import get_material

    x_full = list(profile.get("x_m") or [])
    if len(x_full) < 4:
        return None
    required = ("r_m", "q_W_m2", "T_wg_K", "T_wc_K", "T_co_K", "p_co_Pa",
                "p_gas_Pa", "h_g_W_m2K", "h_c_W_m2K", "V_co_ms", "rho_co")
    if any(len(profile.get(key) or []) != len(x_full) for key in required):
        return None
    if any(not isinstance(v, (int, float)) or not math.isfinite(v)
           for key in ("x_m", *required) for v in profile[key]):
        return None
    x_throat = float(profile.get("x_throat_m", 0.0) or 0.0)
    # 分析域截断：主燃烧室 = 喷注面 → 喉部（含喉部峰值，~2% 余量过喉确保峰不被切）
    if domain == "chamber" and x_throat > 0:
        keep = [i for i, xx in enumerate(x_full) if xx <= x_throat * 1.02]
        if len(keep) < 4:
            keep = list(range(len(x_full)))
    else:
        keep = list(range(len(x_full)))
    n = len(keep)

    def _col(key, default=0.0):
        v = list(profile.get(key) or [])
        if len(v) != len(x_full):
            return [default] * n
        return [v[i] for i in keep]

    x      = _col("x_m")
    r      = _col("r_m")
    q      = _col("q_W_m2")
    q_conv = _col("q_conv_W_m2")
    q_rad  = _col("q_rad_W_m2")
    T_wg   = _col("T_wg_K")
    T_wc   = _col("T_wc_K")
    T_co   = _col("T_co_K")
    p_co   = _col("p_co_Pa")
    p_gas  = _col("p_gas_Pa")
    h_g    = _col("h_g_W_m2K")
    h_c    = _col("h_c_W_m2K")
    V_co   = _col("V_co_ms")
    rho_co = _col("rho_co")
    T_aw   = _col("T_aw_K")
    w_ch   = _col("w_chan_m")

    # 保存的沿程通道几何；不重新生成通道或计算冷却。
    h_ch_arr = _col("h_chan_m")
    n_ch_arr = _col("n_chan")
    t_ln_arr = _col("t_liner_arr_m")
    dist_mode = bool(profile.get("dist_mode"))

    mat = get_material(inner_material_key)
    structural_status, structural_reason, thermal_material_name = _liner_structural_scope(
        profile, inner_material_key)
    assess_liner = structural_status != "not_assessed"
    t_liner = max(float(profile.get("t_liner_m", 1e-3) or 1e-3), 0.3e-3)
    wf = WallField(material_name=thermal_material_name or mat.display_name, t_wall_m=t_liner,
                   source="saved-cooling-profile", structural_status=structural_status,
                   structural_reason=structural_reason)

    # 衬里压差弯曲的有效跨距：
    #   · 分布模式（V19，用户给了沿程通道表）：几何是真实设计，σ_p 直接用**局部**
    #     通道宽 + 局部衬厚——分叉/变宽的结构后果如实反映到应力上。
    #   · 旧单段模式：marching 的通道宽 ∝ 局部半径只是"流通面积"假设，喷管大半径段
    #     会涨到喉部宽的 5–6×，令 σ_p=½ΔP(w/t)² 非物理虚高。真实构型沿喷管分叉细分
    #     使肋跨距不超喉部量级，故结构跨距统一取喉部通道宽（最窄、设计驱动构型）。
    w_pos = [w for w in w_ch if w > 1e-5]
    w_struct_span = min(w_pos) if w_pos else 3.0 * t_liner

    peak_T = 0.0; peak_s = 0.0; min_sf = 1e9; throat_i = 0; min_r = 1e9
    for j in range(n):
        dT = T_wg[j] - T_wc[j]
        dP = p_co[j] - p_gas[j]                    # 冷却剂压向衬里的净压差
        if dist_mode:
            w_span_j = w_ch[j] if w_ch[j] > 1e-5 else w_struct_span
            t_ln_j = t_ln_arr[j] if t_ln_arr[j] > 1e-5 else t_liner
        else:
            w_span_j, t_ln_j = w_struct_span, t_liner
        if assess_liner:
            s_th, s_p, s_eq, s_y, SF = _wall_stress(mat, T_wg[j], dT, dP, w_span_j, t_ln_j)
            wf.sigma_th.append(s_th); wf.sigma_eq.append(s_eq)
            wf.sigma_y.append(s_y); wf.SF.append(SF)
            if s_eq > peak_s: peak_s = s_eq
            if SF < min_sf: min_sf = SF
        else:
            # This pressure-bending load depends only on pressure and geometry;
            # do not invent E, expansion or yield strength for the saved alloy.
            s_p = 0.5 * max(dP, 0.0) * (w_span_j/max(t_ln_j, 1e-4))**2 / 1e6
        wf.x.append(x[j]); wf.r.append(r[j]); wf.h_g.append(h_g[j]); wf.T_aw.append(T_aw[j])
        wf.q.append(q[j]); wf.q_conv.append(q_conv[j]); wf.q_rad.append(q_rad[j])
        wf.T_wg.append(T_wg[j]); wf.T_wc.append(T_wc[j]); wf.dT_wall.append(dT)
        wf.sigma_p.append(s_p); wf.sigma_hoop.append(0.0)
        wf.h_c.append(h_c[j]); wf.T_coolant.append(T_co[j]); wf.p_coolant.append(p_co[j])
        wf.rho_coolant.append(rho_co[j]); wf.v_coolant.append(V_co[j])
        wf.p_gas.append(p_gas[j]); wf.w_chan.append(w_ch[j])
        wf.h_chan.append(h_ch_arr[j]); wf.n_chan.append(n_ch_arr[j])
        wf.t_liner.append(t_ln_j)
        if T_wg[j] > peak_T: peak_T = T_wg[j]
        if r[j] < min_r: min_r = r[j]; throat_i = j

    wf.throat_index = throat_i
    wf.peak_T_wg = peak_T
    wf.peak_sigma_eq = peak_s if assess_liner else None
    wf.min_SF = min_sf if assess_liner else None
    recorded = profile.get("summary") or {}
    ports = profile.get("nodes") or []
    def saved_number(value):
        return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else float("nan")
    wf.coolant_out_K = saved_number(recorded.get("T_out_K"))
    wf.coolant_dp_Pa = saved_number(recorded.get("dp_total_Pa"))
    wf.coolant_out_Pa = saved_number(recorded.get("p_out_Pa"))
    wf.coolant_in_Pa = saved_number(ports[0].get("p_Pa")) if ports else float("nan")
    wf.coolant_in_K = saved_number(ports[0].get("T_K")) if ports else float("nan")
    wf.coolant_flow_direction = str(profile.get("flow_direction") or "未记录")
    # New thermal records supply data intervals, not allowable-use temperatures.
    wf.mat_T_max_use_K = mat.T_max_use_K if structural_status == "legacy_screening" else 0.0
    wf.q_rad_fraction = saved_number(recorded.get("Q_rad_fraction"))

    # ── 结构夹套（Inconel-718 外壳）载荷 ──────────────────────────────
    # 载荷确定的关键修正：夹套包容的是衬里与夹套之间的**高压冷却剂**(~60 MPa)，
    # 不是燃气室压(~33 MPa)。故环向载荷按冷却剂压力算（旧版用室压，低估 ~2×）。
    g_ch = geometry.get("chamber") or {}
    pc = float(summary.get("chamber_pressure_Pa", 30e6) or 30e6)
    rc = float(g_ch.get("radius_m", max(r) if r else 0.2) or 0.2)
    t_total = float(g_ch.get("wall_thickness_mm", 6.0) or 6.0) / 1000.0
    t_chan = float(g_ch.get("cooling_channel_height_mm", 0.0) or 0.0) / 1000.0
    t_jacket = max(t_total - t_liner - t_chan, 2.0e-3)
    p_co_chamber = max(p_co) if p_co else pc      # 主燃烧室段冷却剂压（夹套包容压）
    sigma_jacket_hoop = p_co_chamber * rc / t_jacket / 1e6                 # MPa
    thrust_N = float(summary.get("thrust_N", 0.0) or 0.0)
    sigma_jacket_axial = pc * rc / (2.0 * t_jacket) / 1e6                  # MPa（薄壳轴向≈半环向）
    try:
        jm = get_material(outer_material_key)
        sf_jacket = jm.sigma_yield_at(700.0) / max(sigma_jacket_hoop, 1e-6)
        jacket_name = jm.display_name
    except Exception:
        sf_jacket = float("nan"); jacket_name = "Inconel-718"

    q_peak_MW = (max(wf.q) / 1e6) if wf.q else 0.0
    s_p_peak = (max(wf.sigma_p)) if wf.sigma_p else 0.0
    s_th_peak = (max(abs(v) for v in wf.sigma_th)) if wf.sigma_th else None
    dT_peak = (max(wf.dT_wall)) if wf.dT_wall else 0.0
    dP_peak = (max(p_co[j] - p_gas[j] for j in range(n)) / 1e6) if n else 0.0

    # ── 载荷清单（结构分析/后续 FEA 的边界条件输入）──
    wf.loads = ChamberLoadSet(
        domain=domain,
        liner_material=wf.material_name,
        jacket_material=jacket_name,
        q_peak_MW_m2=q_peak_MW,
        dT_wall_peak_K=dT_peak,
        T_wg_peak_K=peak_T,
        sigma_th_peak_MPa=s_th_peak,
        p_gas_MPa=pc / 1e6,
        p_coolant_max_MPa=p_co_chamber / 1e6,
        dP_liner_peak_MPa=dP_peak,
        sigma_p_peak_MPa=s_p_peak,
        sigma_eq_peak_MPa=wf.peak_sigma_eq,
        liner_min_SF=wf.min_SF,
        liner_structural_status=structural_status,
        liner_structural_reason=structural_reason,
        liner_thickness_mm=t_liner * 1000.0,
        jacket_thickness_mm=t_jacket * 1000.0,
        jacket_hoop_MPa=sigma_jacket_hoop,
        jacket_axial_MPa=sigma_jacket_axial,
        jacket_SF=sf_jacket,
        thrust_kN=thrust_N / 1e3,
    )

    sf_flag = "  ⚠ 衬里塑性(SF<1)" if assess_liner and min_sf < 1.0 else ""
    structural_peak = (f"σ_eq={peak_s:.0f} MPa · min SF={min_sf:.2f}{sf_flag}"
                       if assess_liner else "内衬结构指标未评估")
    wf.notes = [
        f"峰值：T_wg={peak_T:.0f} K · q={q_peak_MW:.0f} MW/m² · {structural_peak}",
        structural_reason,
        "数据源：当前循环保存的统一冷却沿程；这里不重新求解换热、压降或出口状态。",
        f"流向 {wf.coolant_flow_direction}；横轴为发动机轴向坐标 x。结构估计仅供载荷筛查，未经结构验证。",
    ]
    wf.notes.extend(str(w) for w in (profile.get("validity") or {}).get("warnings", []))
    return wf


def plot_wall_field(fig, wf: WallField) -> None:
    """主燃烧室内壁 沿程热-结构概览：
       上排 ① 壁温 T_wg、② 等效应力 σ_eq 两幅剖面云图（真实比例并排）；
       下排 ③ 安全系数 SF(x) 沿程曲线。细节见「喉部 2D 有限元」。
    """
    import numpy as np
    from matplotlib.collections import LineCollection

    fig.clear()
    fig.patch.set_facecolor("#FAFAFA")
    gs = fig.add_gridspec(2, 2, height_ratios=[1.0, 0.72], hspace=0.42, wspace=0.22)
    ax_T = fig.add_subplot(gs[0, 0])
    ax_S = fig.add_subplot(gs[0, 1])
    ax_sf = fig.add_subplot(gs[1, 0])
    ax_exp = fig.add_subplot(gs[1, 1])      # SF 是什么 + 判定（不再让 SF 线拉满全宽）

    x = np.array(wf.x) * 1000.0      # mm
    r = np.array(wf.r) * 1000.0      # mm
    ti = wf.throat_index
    x_thr = x[ti]

    def _draw(ax, values, label, cmap, unit, peak):
        vals = np.array(values)
        segs_up, segs_dn = [], []
        for i in range(len(x) - 1):
            segs_up.append([(x[i], r[i]), (x[i + 1], r[i + 1])])
            segs_dn.append([(x[i], -r[i]), (x[i + 1], -r[i + 1])])
        c = 0.5 * (vals[:-1] + vals[1:])
        vmin, vmax = float(vals.min()), float(vals.max())
        ax.fill_between(x, r, -r, color="#EEF2F7", zorder=0)
        lc_last = None
        for segs in (segs_up, segs_dn):
            lc = LineCollection(segs, cmap=cmap, linewidth=8,
                                array=c, capstyle="round", zorder=3)
            lc.set_clim(vmin, vmax)
            ax.add_collection(lc); lc_last = lc
        cb = fig.colorbar(lc_last, ax=ax, pad=0.02, fraction=0.05)
        cb.set_label(f"{unit}  峰值 {peak:.0f}", fontsize=8)
        ax.set_title(label, fontsize=10, fontweight="bold", loc="left", color="#111827")
        ax.set_xlim(x.min() - 8, x.max() + 8)
        ax.set_ylim(-r.max() * 1.28, r.max() * 1.28)
        ax.set_aspect("equal")                                   # 真实比例，不拉伸
        ax.set_xlabel("x [mm]", fontsize=8, color="#6B7280")
        ax.set_ylabel("r [mm]", fontsize=8, color="#6B7280")
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.tick_params(labelsize=7.5, colors="#6B7280")
        ax.axvline(x_thr, color="#7c2d12", ls=":", lw=0.9, alpha=0.7, zorder=2)
        ax.annotate("喉部", (x_thr, r.max() * 1.04), fontsize=7.5,
                    color="#7c2d12", ha="center", va="bottom")

    _draw(ax_T, wf.T_wg, "① 燃气侧壁温 T_wg [K]", "inferno", "T_wg [K]", wf.peak_T_wg)
    if wf.sigma_eq:
        _draw(ax_S, wf.sigma_eq, "② 等效应力 σ_eq [MPa]", "viridis", "σ_eq [MPa]", wf.peak_sigma_eq)
    else:
        ax_S.axis("off")
        ax_S.text(.03, .9, "② 内衬结构指标未评估\n\n" + wf.structural_reason,
                  transform=ax_S.transAxes, va="top", wrap=True, fontsize=9)

    # ③ 沿程安全系数 SF(x)
    sf = np.array(wf.SF) if wf.SF else np.array([])
    if sf.size:
        ax_sf.plot(x, sf, color="#0f766e", lw=1.8)
        ax_sf.axhline(1.0, color="#b91c1c", ls="--", lw=1.1)
        ax_sf.annotate("SF=1 屈服临界", (x[0], 1.0), textcoords="offset points",
                       xytext=(4, 3), fontsize=7.5, color="#b91c1c")
        j_min = int(np.argmin(sf))
        ax_sf.scatter([x[j_min]], [sf[j_min]], s=30, color="#dc2626", zorder=5)
        ax_sf.annotate(f"min SF={sf[j_min]:.2f}", (x[j_min], sf[j_min]),
                       textcoords="offset points", xytext=(-8, 10), ha="right",
                       fontsize=8.5, color="#dc2626", fontweight="bold")
        ax_sf.fill_between(x, sf, 1.0, where=(sf < 1.0), color="#fecaca", alpha=0.6)
        ax_sf.set_ylim(0, max(2.0, float(np.nanmin([sf.max() * 1.1, 6.0]))))
    ax_sf.axvline(x_thr, color="#7c2d12", ls=":", lw=0.9, alpha=0.7)
    _xend = "喉部" if (wf.loads and getattr(wf.loads, "domain", "") == "chamber") else "喷管出口"
    ax_sf.set_title("③ 内壁安全系数 SF(x) = σ_y(T_wg) / σ_eq", fontsize=10,
                    fontweight="bold", loc="left", color="#111827")
    ax_sf.set_xlabel(f"轴向位置 x [mm]（喷注器 → {_xend}）", fontsize=8, color="#6B7280")
    ax_sf.set_ylabel("SF [-]", fontsize=8, color="#6B7280")
    for s in ("top", "right"):
        ax_sf.spines[s].set_visible(False)
    ax_sf.tick_params(labelsize=7.5, colors="#6B7280")
    ax_sf.grid(True, alpha=0.22)

    # SF 是什么 —— 文字说明 + 判定（替代原来拉满全宽的细长 SF 条）
    ax_exp.axis("off")
    _minsf = wf.min_SF if wf.SF else None
    _plastic = _minsf is not None and _minsf < 1.0
    _exp = (
        "③ 内壁安全系数 SF(x) 是什么？\n\n"
        "SF = σ_y(T_wg) / σ_eq\n"
        "  = 材料在该处壁温的屈服强度\n"
        "    ÷ 该处等效应力(热应力+压差弯曲)\n\n"
        "SF ≥ 1：简化模型未触及屈服\n"
        "SF < 1：简化模型提示塑性风险\n"
        "    该比值不构成结构、疲劳或寿命验证。\n\n"
        + (f"本工况 min SF = {_minsf:.2f}" if _minsf is not None else
           "本工况未评估：没有对应的结构材料数据。")
    )
    ax_exp.text(0.02, 0.98, _exp, transform=ax_exp.transAxes, va="top", ha="left",
                fontsize=8.5, color="#374151", linespacing=1.5,
                bbox=dict(boxstyle="round,pad=0.6",
                          fc="#fef2f2" if _plastic else "#f8fafc", ec="#cbd5e1"))

    _region = "主燃烧室内壁" if (wf.loads and getattr(wf.loads, "domain", "") == "chamber") else "推力室/喷管内壁"
    fig.suptitle(f"{_region} 沿程热-结构概览  ·  {wf.material_name}",
                 fontsize=11.5, fontweight="bold", color="#0f172a")
    fig.subplots_adjust(left=0.07, right=0.97, top=0.90, bottom=0.10)


def plot_cooling_profiles(fig, wf: WallField) -> None:
    """再生冷却沿程剖面（2 列紧凑排版，不再是 6 条细长条）。

    顶部：发动机轮廓（空间参照，薄）。
    左列（传热侧）：① 热流 q(x) ③ 换热系数 h_g/h_c ⑤ 冷却剂 ρ/V。
    右列（冷却剂与判定）：② 三侧温度 vs 材料上限 ④ 冷却剂压力 Δp ⑥ 诊断小结。
    重点：先看「哪里最烫(q)」「烫不烫得住(T_wg vs 上限)」「水力可行吗(Δp/V)」。
    """
    import numpy as np

    fig.clear()
    fig.patch.set_facecolor("#FAFAFA")
    if not wf.x:
        ax = fig.add_subplot(111); ax.axis("off")
        ax.text(0.5, 0.5, "无冷却剖面数据（先运行设计点）", ha="center", va="center",
                transform=ax.transAxes, color="#9CA3AF", fontsize=11, style="italic")
        return

    x = np.array(wf.x) * 1000.0          # mm
    ti = wf.throat_index
    xt = x[ti]
    r = np.array(wf.r) * 1000.0
    q = np.array(wf.q) / 1e6
    q_rad = np.array(wf.q_rad) / 1e6 if wf.q_rad else np.zeros_like(q)
    p = np.array(wf.p_coolant) / 1e6 if wf.p_coolant else np.zeros_like(q)
    rho = np.array(wf.rho_coolant) if wf.rho_coolant else np.zeros_like(q)
    v = np.array(wf.v_coolant) if wf.v_coolant else np.zeros_like(q)
    Tco = np.array(wf.T_coolant) if wf.T_coolant else np.zeros_like(q)
    Twg = np.array(wf.T_wg)

    gs = fig.add_gridspec(4, 2, height_ratios=[1.45, 1.0, 1.0, 1.0],
                          hspace=0.32, wspace=0.26)
    ax_geo = fig.add_subplot(gs[0, :])
    ax_q = fig.add_subplot(gs[1, 0], sharex=ax_geo)
    ax_T = fig.add_subplot(gs[1, 1], sharex=ax_geo)
    ax_h = fig.add_subplot(gs[2, 0], sharex=ax_geo)
    ax_p = fig.add_subplot(gs[2, 1], sharex=ax_geo)
    ax_rv = fig.add_subplot(gs[3, 0], sharex=ax_geo)
    ax_diag = fig.add_subplot(gs[3, 1])

    # 顶：轮廓
    ax_geo.fill_between(x, r, -r, color="#E5EAF1", zorder=0)
    ax_geo.plot(x, r, color="#475569", lw=1.0); ax_geo.plot(x, -r, color="#475569", lw=1.0)
    ax_geo.set_ylabel("r [mm]", fontsize=8)
    src = "循环保存的冷却沿程" if wf.source == "saved-cooling-profile" else "历史记录"
    region = ("主燃烧室段" if (wf.loads and getattr(wf.loads, "domain", "") == "chamber")
              else "推力室全程")
    fig.suptitle(f"再生冷却沿程 · {region} · {src}",
                 fontsize=11, fontweight="bold", x=.03, ha="left", color="#0f172a")
    ax_geo.set_title("轴向型面（喷注器 → 喷管出口）", fontsize=9, loc="left", color="#0f172a")
    ax_geo.annotate("喉部", (xt, r.max()), textcoords="offset points",
                    xytext=(0, 1), ha="center", fontsize=7.5, color="#7c2d12")
    # 按**真实比例**显示轮廓（等纵横比、居中）。本机长 ~2.15 m、最大直径 ~1.36 m，比例约
    # 1.58:1，本不算长；之前"拉得太长"只是把它塞进又宽又扁的条里、纵向被压扁 ~6× 的视觉错觉。
    # 等比例后是一台正常比例的钟形喷管发动机（代价：轮廓条比下方面板窄、居中，喉部竖线不强求像素对齐）。
    ax_geo.set_ylim(-r.max() * 1.06, r.max() * 1.06)
    ax_geo.set_aspect("equal", adjustable="box", anchor="C")
    ax_geo.margins(x=0.0)

    # ① 热流 q
    ax_q.plot(x, q, color="#dc2626", lw=1.6)
    ax_q.fill_between(x, q, color="#dc2626", alpha=0.10)
    if float(q_rad.max() if len(q_rad) else 0.0) > 0.5:
        ax_q.plot(x, q_rad, color="#7c3aed", lw=1.1, ls="--",
                  label=(f"辐射(全回路占{100.0*wf.q_rad_fraction:.1f}%)"
                         if math.isfinite(wf.q_rad_fraction) else "辐射"))
        ax_q.legend(fontsize=7, loc="upper left", framealpha=0.8)
    ax_q.set_ylabel("q [MW/m²]", fontsize=8)
    ax_q.set_title("① 热流密度 q(x) — 哪里最烫", fontsize=9, loc="left",
                   color="#111827", fontweight="bold")

    # ② 三侧温度 vs 材料上限（安全核心）
    ax_T.plot(x, Twg, color="#ea580c", lw=1.6, label="T_wg 燃气壁")
    ax_T.plot(x, wf.T_wc, color="#0891b2", lw=1.3, label="T_wc 冷却壁")
    ax_T.plot(x, Tco, color="#2563eb", lw=1.3, ls="--", label="T_co 冷却剂")
    if wf.mat_T_max_use_K > 0:
        ax_T.axhline(wf.mat_T_max_use_K, color="#b91c1c", ls=":", lw=1.2)
        ax_T.annotate(f"材料上限 {wf.mat_T_max_use_K:.0f}K", (x[-1], wf.mat_T_max_use_K),
                      textcoords="offset points", xytext=(-4, 3), ha="right",
                      fontsize=7, color="#b91c1c")
    ax_T.set_ylabel("T [K]", fontsize=8)
    ax_T.set_title("② 三侧温度 vs 材料上限 — 烫不烫得住", fontsize=9, loc="left",
                   color="#111827", fontweight="bold")
    ax_T.legend(fontsize=6.5, ncol=1, loc="upper left", framealpha=0.8)

    # ③ 换热系数 h（对数）
    ax_h.semilogy(x, wf.h_g, color="#b45309", lw=1.4, label="h_g 燃气侧")
    ax_h.semilogy(x, wf.h_c, color="#0e7490", lw=1.4, label="h_c 冷却侧")
    ax_h.set_ylabel("h [W/m²K]", fontsize=8)
    ax_h.set_title("③ 换热系数 — 加热 vs 带走", fontsize=9, loc="left",
                   color="#111827", fontweight="bold")
    ax_h.legend(fontsize=7, ncol=2, loc="lower left", framealpha=0.8)

    # ④ 冷却剂压力 Δp
    ax_p.plot(x, p, color="#15803d", lw=1.5)
    ax_p.fill_between(x, p, color="#15803d", alpha=0.10)
    ax_p.set_ylabel("p_co [MPa]", fontsize=8)
    ax_p.set_title("④ 冷却剂压力 — 沿程压降 Δp", fontsize=9, loc="left",
                   color="#111827", fontweight="bold")
    if math.isfinite(wf.coolant_dp_Pa):
        ax_p.text(0.98, 0.1, f"保存的端口 Δp = {wf.coolant_dp_Pa/1e6:.1f} MPa"
                  f" (入口{wf.coolant_in_Pa/1e6:.1f}→出口{wf.coolant_out_Pa/1e6:.1f})",
                  transform=ax_p.transAxes, ha="right", va="bottom", fontsize=7.3,
                  color="#15803d", bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.7))

    # ⑤ 冷却剂 ρ/V（双轴）
    ax_rv.plot(x, rho, color="#9333ea", lw=1.4)
    ax_rv.set_ylabel("ρ [kg/m³]", fontsize=8, color="#9333ea")
    ax_rv.tick_params(axis="y", labelcolor="#9333ea", labelsize=7.5)
    ax_v = ax_rv.twinx()
    ax_v.plot(x, v, color="#c2410c", lw=1.2, ls="--")
    ax_v.set_ylabel("V [m/s]", fontsize=8, color="#c2410c")
    ax_v.tick_params(axis="y", labelcolor="#c2410c", labelsize=7.5)
    ax_v.spines["top"].set_visible(False)
    ax_rv.set_title("⑤ 冷却剂密度 ρ / 流速 V — 超临界状态", fontsize=9, loc="left",
                    color="#111827", fontweight="bold")

    # ⑥ 诊断小结（右下，一眼看懂行不行）
    ax_diag.axis("off")
    T_max = wf.mat_T_max_use_K
    over = (float(Twg.max()) > T_max) if T_max > 0 else False
    dp = wf.coolant_dp_Pa / 1e6
    Vmax = float(v.max()) if v.size else 0.0
    if wf.loads is not None and getattr(wf.loads, "liner_min_SF", None) is not None:
        minSF = float(wf.loads.liner_min_SF)
    else:
        minSF = float(min(wf.SF)) if wf.SF else None
    def _m(ok): return "+" if ok else "!"
    rows = [
        ("·", "峰值热流 q", f"{q.max():.0f} MW/m²"),
        (_m(not over) if T_max > 0 else "·", "峰值壁温 T_wg",
         f"{Twg.max():.0f} K / 上限{T_max:.0f}" if T_max > 0 else
         f"{Twg.max():.0f} K（未提供材料使用温度资格）"),
        ("·", "入口→出口温度", f"{wf.coolant_in_K:.0f}→{wf.coolant_out_K:.0f} K"),
        ("·", "保存的端口 Δp", f"{dp:.1f} MPa" if math.isfinite(dp) else "未记录"),
        ("·", "最大流速 V", f"{Vmax:.0f} m/s"),
        (_m(minSF >= 1.0) if minSF is not None else "·", "内壁 min SF 估计",
         f"{minSF:.2f}（非结构验证）" if minSF is not None else "未评估（材料数据未对应）"),
    ]
    txt = "⑥ 诊断小结 / Scorecard\n" + "\n".join(
        f"{mk}  {name}：{val}" for mk, name, val in rows)
    # 不指定 family="monospace"：等宽字体无中日韩字形会把中文显示成 □（字体失效）。
    # 改用图全局 sans-serif（已含中文字形），列对齐改靠「名：值」分隔而非等宽补空格。
    ax_diag.text(0.02, 0.98, txt, transform=ax_diag.transAxes, va="top", ha="left",
                 fontsize=9, color="#374151", linespacing=1.6,
                 bbox=dict(boxstyle="round,pad=0.6", fc="#f8fafc", ec="#cbd5e1"))

    for ax in (ax_geo, ax_q, ax_T, ax_h, ax_p, ax_rv):
        ax.axvline(xt, color="#7c2d12", ls=":", lw=0.8, alpha=0.55)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.tick_params(labelsize=7.5, colors="#6B7280")
        ax.grid(True, alpha=0.20)
    # 仅底部绘图面板显示 x 轴标签
    for ax in (ax_geo, ax_q, ax_T, ax_h):
        ax.tick_params(labelbottom=False)
    ax_rv.set_xlabel("轴向位置 x [mm]（喷注器 → 喷管出口）", fontsize=8.5)
    ax_p.set_xlabel("轴向位置 x [mm]", fontsize=8.5)

    fig.subplots_adjust(left=0.075, right=0.93, top=0.94, bottom=0.07)


# ─────────────────────────────────────────────────────────
# 独立调试入口
# ─────────────────────────────────────────────────────────

def demo(out_path: str = "thermal_structural_demo.png"):
    """用 cycle 求解一次,生成热-结构云图,单独验证。"""
    import warnings; warnings.simplefilter("ignore")
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "sans-serif"]
    matplotlib.rcParams["axes.unicode_minus"] = False
    from matplotlib.figure import Figure
    import core, cycle
    from geometry import compute_geometry, geometry_to_dict

    inp = core.EngineInputs()
    r = cycle.FFSCCycleModel(inp).evaluate()
    geom = geometry_to_dict(compute_geometry(
        r, contraction_ratio=inp.chamber_contraction_ratio, l_star=inp.chamber_l_star_m))
    cool = r.components.get("cooling_channel")
    profile = getattr(cool, "profile", None)
    if not profile:
        raise RuntimeError("当前循环未保存统一冷却沿程，请完整重算。")
    wf = wall_field_from_profile(profile, r.summary, geom,
                                 inner_material_key="grcop84")
    if wf is None:
        print("数据不足,无法生成云图")
        return
    for n in wf.notes:
        print("  ", n)
    fig = Figure(figsize=(11, 8), dpi=110)
    plot_wall_field(fig, wf)
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    FigureCanvasAgg(fig).print_png(out_path)
    print(f"saved: {out_path}")


if __name__ == "__main__":
    demo()
