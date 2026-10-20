"""
thermal_structural.py — 推力室/喷管壁 热-结构云图(L2 第一块)
================================================================

把数字样机从 L1(系统级性能)推进到 L2(部件结构响应):基于已有的
  · Bartz 喉部热流 / 换热系数(cooling.py)
  · 喷管真实轮廓 contour_x/contour_r(geometry.py)
  · 材料热-力物性 k/E/α/ν/σy(materials.py 已补全)
沿轴向逐站做 **一维穿壁串联热阻** 求解,得到:
  · 燃气侧壁温 T_wg、冷却侧壁温 T_wc、穿壁温差 ΔT
  · 热应力 σ_th(穿壁线性梯度产生的弯曲热应力)
  · 压力环向应力 σ_hoop(Barlow)
  · 等效应力 σ_eq 与安全系数 SF
并渲染成发动机剖面 **温度云图 + 应力云图**。

为什么用一维穿壁(而不是 2D FEM):
  推力室壁很薄(~1–3 mm)而轴向尺度 ~米级,轴向导热相对穿壁可忽略,
  逐站一维串联热阻是 NASA SP-125 / Huzel&Huang 的标准工程做法,
  精度足够 L2,且无需任何重型有限元库,鲁棒、秒级。
  (将来要 2D/3D 精细场,可把每站结果作为边界导出给 CalculiX。)

串联热阻(稳态)
────────────────
    q = (T_aw - T_coolant) / (1/h_g + t/k + 1/h_c)
    T_wg = T_aw - q/h_g           # 燃气侧壁温
    T_wc = T_coolant + q/h_c      # 冷却侧壁温
    ΔT_wall = T_wg - T_wc = q·t/k # 穿壁温差

热应力(薄壁、线性穿壁梯度,Timoshenko)
    σ_th = E·α·ΔT_wall / (2(1-ν))   # 两侧等值反号,内壁压/外壁拉
环向压力应力(Barlow 薄壁)
    σ_hoop = ΔP·r / t
等效应力(一阶叠加;保守取代数和)
    σ_eq = |σ_th| + σ_hoop
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


# ─────────────────────────────────────────────────────────
# Bartz 局部换热缩放
# ─────────────────────────────────────────────────────────
# h_g(x) / h_g_throat ≈ (A_t / A(x))^0.9   (Bartz 面积比指数 ≈ 0.9)
# 喉部 A=A_t → 比值 1(峰值);燃烧室/喷管 A>A_t → h_g 更小。
_BARTZ_AREA_EXP = 0.9
_RECOVERY = 0.91   # 湍流恢复系数 r ≈ Pr^(1/3)


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
    peak_sigma_eq: float = 0.0
    min_SF: float = 0.0
    coolant_out_K: float = 0.0                            # 冷却剂出口温度 [K]
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
    sigma_th_peak_MPa: float = 0.0
    # 压力载荷
    p_gas_MPa: float = 0.0
    p_coolant_max_MPa: float = 0.0
    dP_liner_peak_MPa: float = 0.0
    sigma_p_peak_MPa: float = 0.0
    # 衬里综合
    sigma_eq_peak_MPa: float = 0.0
    liner_min_SF: float = 0.0
    liner_thickness_mm: float = 0.0
    # 结构夹套
    jacket_thickness_mm: float = 0.0
    jacket_hoop_MPa: float = 0.0
    jacket_axial_MPa: float = 0.0
    jacket_SF: float = 0.0
    thrust_kN: float = 0.0

    def to_rows(self):
        """返回载荷清单表行：(载荷, 作用对象, 量值, 产生的应力/裕度)。"""
        return [
            ("① 热载荷 ΔT_wall（穿壁温差）", f"热衬里 {self.liner_material}",
             f"{self.dT_wall_peak_K:.0f} K（热流峰 {self.q_peak_MW_m2:.0f} MW/m²）",
             f"σ_th = {self.sigma_th_peak_MPa:.0f} MPa"),
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
             f"σ_eq = {self.sigma_eq_peak_MPa:.0f} MPa",
             f"min SF = {self.liner_min_SF:.2f}"),
        ]


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
    """
    计算推力室+喷管内壁沿轴向的热-结构场。

    参数
    ────
    summary          : cycle.summary(取 chamber_pressure/temperature)
    geometry         : geometry_to_dict 输出(取 nozzle.contour_x/r、chamber/壁厚)
    cooling_outputs  : cooling_channel.outputs(取 Bartz 喉部 h_g/q/T_aw/At)
    inner_material_key: 内壁材料 key(默认 GRCop-84)
    coolant_temp_K   : 冷却剂代表温度 [K]
    h_coolant_W_m2K  : 冷却侧换热系数 [W/m²K](甲烷再生冷却典型 2–5e4)
    n_axial          : 轴向离散点数

    返回 WallField;数据不足时返回 None。
    """
    from materials import get_material

    g_nz = geometry.get("nozzle") or {}
    g_ch = geometry.get("chamber") or {}
    cx = list(g_nz.get("contour_x") or [])
    cr = list(g_nz.get("contour_r") or [])
    if len(cx) < 8 or len(cr) != len(cx):
        return None

    rt = float(g_nz.get("throat_radius_m", 0.0) or 0.0)
    if rt <= 0:
        rt = min(cr)
    At = math.pi * rt * rt

    # 热衬里厚度:用传入的 t_liner_m(热分析的是薄铜内壁,~1mm,
    # 不是结构总壁厚 3.6mm;后者含通道壁 + Inconel 外壳)
    t_wall = max(t_liner_m, 0.3e-3)

    Tc = float(summary.get("chamber_temperature_K", 3500.0) or 3500.0)
    pc = float(summary.get("chamber_pressure_Pa", 30e6) or 30e6)
    gamma = float(summary.get("chamber_gamma", 1.19) or 1.19)

    # Bartz 喉部基准(来自 cooling)
    co = cooling_outputs or {}
    h_g_throat = float(co.get("h_g_throat_W_m2_K", 0.0) or 0.0)
    T_aw_throat = float(co.get("T_aw_throat_K", 0.0) or 0.0)
    if h_g_throat <= 0:
        # 退化:用 q_throat / (T_aw - 800) 反推,再不行给个量级值
        q_t = float(co.get("q_throat_MW_m2", 0.0) or 0.0) * 1e6
        T_aw_throat = T_aw_throat or 0.91 * Tc
        h_g_throat = (q_t / max(T_aw_throat - 800.0, 100.0)) if q_t > 0 else 5.0e4
    if T_aw_throat <= 0:
        T_aw_throat = 0.91 * Tc

    mat = get_material(inner_material_key)

    # ── 冷却剂入口 + 通道几何：优先取 cooling_outputs（与 cooling.py V9 一致）──
    coolant_T_in = float(co.get("T_in_K", coolant_temp_K) or coolant_temp_K)
    coolant_p_in = float(co.get("P_in_Pa", pc * 1.5) or pc * 1.5)
    if mdot_coolant is None or mdot_coolant <= 0:
        mdot_coolant = float(summary.get("mdot_fuel_kg_s", 100.0) or 100.0)
    A_flow = float(co.get("channel_A_flow_cm2", 11.25) or 11.25) * 1e-4
    D_h = float(co.get("channel_D_h_mm", 2.3) or 2.3) * 1e-3
    h_c_fallback = float(co.get("h_coolant_W_m2K", h_coolant_W_m2K) or h_coolant_W_m2K)
    # 衬里厚度：优先取 cooling.py 的值（统一两模块；否则用传入 t_liner_m）
    t_wall = max(float(co.get("liner_thickness_m", t_liner_m) or t_liner_m), 0.3e-3)

    # 复用 cooling.py 的超临界甲烷物性 + Dittus-Boelter（保证与系统级模型同一套物理）
    _have_cool = True
    try:
        from cooling import (_ch4_density_supercritical, _ch4_viscosity,
                             _ch4_thermal_conductivity, _ch4_cp_supercritical,
                             _dittus_boelter_htc)
    except Exception:
        _have_cool = False
        march_coolant = False

    x0, x1 = cx[0], cx[-1]
    wf = WallField(material_name=mat.display_name, t_wall_m=t_wall)

    def _interp_r(xq: float) -> float:
        if xq <= cx[0]:
            return cr[0]
        if xq >= cx[-1]:
            return cr[-1]
        for i in range(len(cx) - 1):
            if cx[i] <= xq <= cx[i + 1]:
                f = (xq - cx[i]) / max(cx[i + 1] - cx[i], 1e-12)
                return cr[i] + f * (cr[i + 1] - cr[i])
        return cr[-1]

    # 站点几何 + 步长
    xs = [x0 + (x1 - x0) * j / (n_axial - 1) for j in range(n_axial)]
    rs = [_interp_r(xq) for xq in xs]
    dxs = []
    for j in range(n_axial):
        if 0 < j < n_axial - 1:
            dxs.append(abs(xs[j + 1] - xs[j - 1]) / 2.0)
        elif j == 0:
            dxs.append(abs(xs[1] - xs[0]))
        else:
            dxs.append(abs(xs[-1] - xs[-2]))

    # 预算燃气侧 h_g(x) 与绝热壁温 T_aw(x) + 局部静压 p_gas(x)（V19：任意站 FEM 用，
    # 取代旧版 0.55·pc 硬编码；亚声速段解 M_sub、扩张段解 M_sup，等熵静压）
    h_g_arr = []; T_aw_arr = []; p_gas_arr = []
    pgas_exp = -gamma / max(gamma - 1.0, 1e-9)
    for j in range(n_axial):
        A = math.pi * rs[j] * rs[j]
        area_ratio = At / max(A, 1e-12)
        h_g = bartz_correction * h_g_throat * area_ratio ** _BARTZ_AREA_EXP
        is_div = (xs[j] > 0.0 and rs[j] > rt * 1.02)
        if is_div:
            M = _mach_supersonic(A / At, gamma)
            t_ratio = 1.0 + (gamma - 1.0) / 2.0 * M * M
            T_static = Tc / t_ratio
            T_aw = T_static * (1.0 + _RECOVERY * (gamma - 1.0) / 2.0 * M * M)
        else:
            M = _mach_subsonic(A / At, gamma)
            t_ratio = 1.0 + (gamma - 1.0) / 2.0 * M * M
            T_aw = T_aw_throat
        h_g_arr.append(h_g); T_aw_arr.append(T_aw)
        p_gas_arr.append(pc * t_ratio ** pgas_exp)

    # 冷却剂 marching 顺序：counterflow 默认从喷管出口端进、向喷注器端流
    nozzle_is_last = (xs[-1] >= xs[0])
    if march_coolant:
        if coolant_inlet_end == "nozzle":
            order = list(range(n_axial - 1, -1, -1)) if nozzle_is_last else list(range(n_axial))
        else:
            order = list(range(n_axial)) if nozzle_is_last else list(range(n_axial - 1, -1, -1))
    else:
        order = list(range(n_axial))

    N = n_axial
    h_c_arr = [h_c_fallback] * N
    T_co_arr = [coolant_T_in] * N
    p_co_arr = [coolant_p_in] * N
    q_arr = [0.0] * N; T_wg_arr = [0.0] * N; T_wc_arr = [0.0] * N
    rho_arr = [0.0] * N; V_arr = [0.0] * N

    # 沿冷却剂流向逐站推进：每站算 h_c → k(T) 自洽求壁温/热流 → 把热量加进冷却剂
    T_co = coolant_T_in; p_co = coolant_p_in
    for idx in order:
        if march_coolant and _have_cool:
            rho = _ch4_density_supercritical(T_co, p_co)
            mu_c = _ch4_viscosity(T_co)
            k_c = _ch4_thermal_conductivity(T_co)
            cp_c = _ch4_cp_supercritical(T_co)
            h_c, V_c, Re_c, Pr_c, Nu_c = _dittus_boelter_htc(
                mdot_coolant, rho, mu_c, k_c, cp_c, A_flow, D_h)
            h_c = max(h_c, 1.0e3)
        else:
            h_c = h_c_fallback; rho = 100.0; V_c = 0.0; Re_c = 1.0e6

        h_g = h_g_arr[idx]; T_aw = T_aw_arr[idx]
        T_wg = T_co + 300.0
        q = 0.0
        for _ in range(6):                       # k(T) 自洽（h_g 固定，含喉部壁温修正）
            k_w = _k_of_T(mat, 0.5 * (T_wg + T_co))
            R_tot = 1.0 / max(h_g, 1.0) + t_wall / max(k_w, 1e-6) + 1.0 / h_c
            q = max((T_aw - T_co) / max(R_tot, 1e-9), 0.0)
            T_wg_new = T_aw - q / max(h_g, 1.0)
            if abs(T_wg_new - T_wg) < 0.3:
                T_wg = T_wg_new; break
            T_wg = 0.5 * T_wg + 0.5 * T_wg_new
        T_wc = T_co + q / max(h_c, 1.0)

        h_c_arr[idx] = h_c; T_co_arr[idx] = T_co; p_co_arr[idx] = p_co
        q_arr[idx] = q; T_wg_arr[idx] = T_wg; T_wc_arr[idx] = T_wc
        rho_arr[idx] = rho; V_arr[idx] = V_c

        if march_coolant and _have_cool:         # 冷却剂吸热升温 + 摩擦压降 → 下一站
            dQ = q * (2.0 * math.pi * rs[idx] * max(dxs[idx], 1e-4))
            cp_c = _ch4_cp_supercritical(T_co)
            T_co = T_co + dQ / max(mdot_coolant * cp_c, 1e-6)
            f_fric = 0.046 / max(Re_c, 1.0) ** 0.2
            p_co = max(p_co - f_fric * (max(dxs[idx], 1e-4) / max(D_h, 1e-4))
                       * 0.5 * rho * V_c * V_c, 1.0e5)

    coolant_out_K = T_co

    # 按 contour 顺序填 WallField + 逐站应力（回退路径无逐站燃气压力/通道宽，
    # 故衬里压差弯曲 σ_p=0；只含热应力。辐射项不在此简化路径，q_rad=0）。
    peak_T = 0.0; peak_s = 0.0; min_sf = 1e9; throat_i = 0; min_r = 1e9
    for j in range(N):
        T_wg = T_wg_arr[j]; T_wc = T_wc_arr[j]; dT = T_wg - T_wc; q = q_arr[j]
        sigma_th, sigma_p, sigma_eq, sigma_y, SF = _wall_stress(
            mat, T_wg, dT, 0.0, 0.0, t_wall)
        wf.x.append(xs[j]); wf.r.append(rs[j]); wf.h_g.append(h_g_arr[j])
        wf.T_aw.append(T_aw_arr[j]); wf.q.append(q)
        wf.q_conv.append(q); wf.q_rad.append(0.0)
        wf.T_wg.append(T_wg); wf.T_wc.append(T_wc); wf.dT_wall.append(dT)
        wf.sigma_th.append(sigma_th); wf.sigma_p.append(sigma_p); wf.sigma_hoop.append(0.0)
        wf.sigma_eq.append(sigma_eq); wf.sigma_y.append(sigma_y); wf.SF.append(SF)
        wf.h_c.append(h_c_arr[j]); wf.T_coolant.append(T_co_arr[j]); wf.p_coolant.append(p_co_arr[j])
        wf.rho_coolant.append(rho_arr[j]); wf.v_coolant.append(V_arr[j])
        wf.p_gas.append(p_gas_arr[j])
        if T_wg > peak_T: peak_T = T_wg
        if sigma_eq > peak_s: peak_s = sigma_eq
        if SF < min_sf: min_sf = SF
        if rs[j] < min_r: min_r = rs[j]; throat_i = j

    wf.throat_index = throat_i
    wf.peak_T_wg = peak_T
    wf.peak_sigma_eq = peak_s
    wf.min_SF = min_sf
    wf.coolant_out_K = coolant_out_K
    wf.mat_T_max_use_K = mat.T_max_use_K

    # 结构外壳(Inconel 夹套)环向压力应力 —— 单独标量校核
    # V19 审查修复：从分层总厚里扣内衬**和通道层**（与 wall_field_from_profile 同口径）；
    # 优先直接取几何模块的外壳层厚 outer_wall_thickness_mm。
    rc = float(g_ch.get("radius_m", max(cr)) or max(cr))
    t_jacket_geo = float(g_ch.get("outer_wall_thickness_mm", 0.0) or 0.0) / 1000.0
    if t_jacket_geo > 1e-4:
        t_jacket = t_jacket_geo
    else:
        t_total = float(g_ch.get("wall_thickness_mm", 6.0) or 6.0) / 1000.0
        t_chan = float(g_ch.get("cooling_channel_height_mm", 0.0) or 0.0) / 1000.0
        t_jacket = max(t_total - t_wall - t_chan, 2.0e-3)
    sigma_jacket_hoop = pc * rc / t_jacket / 1e6   # MPa
    try:
        jacket_mat = get_material(outer_material_key)
        sf_jacket = jacket_mat.sigma_yield_at(700.0) / max(sigma_jacket_hoop, 1e-6)
        jacket_name_fallback = jacket_mat.display_name
    except Exception:
        sf_jacket = float("nan")
        jacket_name_fallback = "Inconel-718"

    # D：峰值热流与公开数据对标（标定/不确定度）
    q_peak_MW = (max(wf.q) / 1e6) if wf.q else 0.0
    h_c_throat = wf.h_c[throat_i] if wf.h_c else 0.0
    calib = "✓ 在公开量级内" if 60.0 <= q_peak_MW <= 200.0 else "⚠ 偏离常见量级,核查工况/几何"

    wf.notes = [
        f"内壁(热衬里):{mat.display_name},厚 {t_wall*1000:.2f} mm,"
        f"k(T)≈{_k_of_T(mat, peak_T):.0f} W/(m·K) @ {peak_T:.0f}K",
        f"峰值燃气侧壁温 T_wg = {peak_T:.0f} K(喉部);峰值热流 q = {q_peak_MW:.0f} MW/m²;"
        f"峰值热应力 σ_th = {peak_s:.0f} MPa",
        f"内壁最小安全系数 SF = {min_sf:.2f}"
        + ("  ⚠ <1 → 衬里塑性,每次点火-关机累积塑性应变,LCF 寿命受限"
           "(再生冷却铜室核心寿命瓶颈)" if min_sf < 1.0 else ""),
        (f"冷却剂(沿程 marching):入口 {coolant_T_in:.0f} K → 出口 {coolant_out_K:.0f} K;"
         f"喉部 h_c≈{h_c_throat:.2e} W/(m²·K)(由通道几何+超临界甲烷物性算出)"
         if (march_coolant and _have_cool) else
         f"冷却侧(固定回退):T={coolant_T_in:.0f} K,h_c={h_c_fallback:.1e} W/(m²·K)"),
        f"标定:峰值热流 {q_peak_MW:.0f} MW/m² —— 公开 Raptor/RS-25 喉部量级 "
        f"~80–160 MW/m²(±30% 工程不确定度)  {calib}",
        f"结构外壳({jacket_name_fallback} 夹套,厚 {t_jacket*1000:.1f} mm)环向应力 "
        f"σ_hoop = {sigma_jacket_hoop:.0f} MPa,SF = {sf_jacket:.2f}"
        + ("  (室压载荷由夹套承担,不压在铜衬里)" if sf_jacket >= 1.0 else "  ⚠ 夹套偏薄"),
        "说明:h_g(x) 取 cooling.py 自洽喉部值按面积比缩放;冷却剂逐站 marching"
        "(假设通道截面沿程不变);衬里应力只含热应力(LCF 驱动)。",
    ]
    return wf


def wall_field_from_profile(
    profile: Dict[str, Any],
    summary: Dict[str, Any],
    geometry: Dict[str, Any],
    inner_material_key: str = "grcop84",
    outer_material_key: str = "inconel718_wall",     # V18：结构夹套材料（用户在材料库可选）
    domain: str = "chamber",
) -> Optional[WallField]:
    """从 cooling_marching 的完整剖面构建 WallField（首选路径）。

    传热/流动场（q、T_wg/T_wc/T_co、p_co、h_g/h_c、ρ、V、辐射分解）直接取自
    系统级 marching 模型（CoolProp 真实物性 + 肋效率 + Jackson 变物性 + 辐射 +
    加速压降），本函数只在其上**叠加结构层**（热应力 σ_th、衬里压差弯曲 σ_p、
    SF）+ **载荷清单**（ChamberLoadSet）。数据不足时返回 None。

    domain
    ──────
    "chamber" : 只取喷注面→喉部（主燃烧室段）。此段衬里材料统一为 GRCop-84、
                外壳为 Inconel-718，单一材料的结构判据才成立——喷管下游内壁
                材料/构型不同（可能另一牌号或辐射冷却延伸段），不在此域。
    "full"    : 全程（喷注面→喷管出口），用于纯查看（材料按 chamber 牌号近似）。
    """
    from materials import get_material

    x_full = list(profile.get("x_m") or [])
    if len(x_full) < 4:
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

    # V19：沿程通道分布数组（cooling_marching 分布模式时提供；旧剖面缺省回退）
    h_ch_arr = _col("h_chan_m")
    n_ch_arr = _col("n_chan")
    t_ln_arr = _col("t_liner_arr_m")
    dist_mode = bool(profile.get("dist_mode"))

    mat = get_material(inner_material_key)
    t_liner = max(float(profile.get("t_liner_m", 1e-3) or 1e-3), 0.3e-3)
    wf = WallField(material_name=mat.display_name, t_wall_m=t_liner, source="marching-profile")

    # 衬里压差弯曲的有效跨距：
    #   · 分布模式（V19，用户给了沿程通道表）：几何是真实设计，σ_p 直接用**局部**
    #     通道宽 + 局部衬厚——分叉/变宽的结构后果如实反映到应力上。
    #   · 旧单段模式：marching 的通道宽 ∝ 局部半径只是"流通面积"假设，喷管大半径段
    #     会涨到喉部宽的 5–6×，令 σ_p=½ΔP(w/t)² 非物理虚高。真实构型沿喷管分叉细分
    #     使肋跨距不超喉部量级，故结构跨距统一取喉部通道宽（最窄、设计驱动构型）。
    w_pos = [w for w in w_ch if w > 1e-5]
    w_struct_span = min(w_pos) if w_pos else 3.0 * t_liner

    peak_T = 0.0; peak_s = 0.0; min_sf = 1e9; throat_i = 0; min_r = 1e9
    Q_sum = 0.0; Q_rad = 0.0
    for j in range(n):
        dT = T_wg[j] - T_wc[j]
        dP = p_co[j] - p_gas[j]                    # 冷却剂压向衬里的净压差
        if dist_mode:
            w_span_j = w_ch[j] if w_ch[j] > 1e-5 else w_struct_span
            t_ln_j = t_ln_arr[j] if t_ln_arr[j] > 1e-5 else t_liner
        else:
            w_span_j, t_ln_j = w_struct_span, t_liner
        s_th, s_p, s_eq, s_y, SF = _wall_stress(mat, T_wg[j], dT, dP, w_span_j, t_ln_j)
        wf.x.append(x[j]); wf.r.append(r[j]); wf.h_g.append(h_g[j]); wf.T_aw.append(T_aw[j])
        wf.q.append(q[j]); wf.q_conv.append(q_conv[j]); wf.q_rad.append(q_rad[j])
        wf.T_wg.append(T_wg[j]); wf.T_wc.append(T_wc[j]); wf.dT_wall.append(dT)
        wf.sigma_th.append(s_th); wf.sigma_p.append(s_p); wf.sigma_hoop.append(0.0)
        wf.sigma_eq.append(s_eq); wf.sigma_y.append(s_y); wf.SF.append(SF)
        wf.h_c.append(h_c[j]); wf.T_coolant.append(T_co[j]); wf.p_coolant.append(p_co[j])
        wf.rho_coolant.append(rho_co[j]); wf.v_coolant.append(V_co[j])
        wf.p_gas.append(p_gas[j]); wf.w_chan.append(w_ch[j])
        wf.h_chan.append(h_ch_arr[j]); wf.n_chan.append(n_ch_arr[j])
        wf.t_liner.append(t_ln_j)
        Q_sum += q[j]; Q_rad += q_rad[j]
        if T_wg[j] > peak_T: peak_T = T_wg[j]
        if s_eq > peak_s: peak_s = s_eq
        if SF < min_sf: min_sf = SF
        if r[j] < min_r: min_r = r[j]; throat_i = j

    wf.throat_index = throat_i
    wf.peak_T_wg = peak_T
    wf.peak_sigma_eq = peak_s
    wf.min_SF = min_sf
    wf.coolant_out_K = max(T_co) if T_co else 0.0
    wf.mat_T_max_use_K = mat.T_max_use_K
    wf.q_rad_fraction = Q_rad / max(Q_sum, 1e-9)

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
    s_th_peak = (max(abs(v) for v in wf.sigma_th)) if wf.sigma_th else 0.0
    dT_peak = (max(wf.dT_wall)) if wf.dT_wall else 0.0
    dP_peak = (max(p_co[j] - p_gas[j] for j in range(n)) / 1e6) if n else 0.0

    # ── 载荷清单（结构分析/后续 FEA 的边界条件输入）──
    wf.loads = ChamberLoadSet(
        domain=domain,
        liner_material=mat.display_name,
        jacket_material=jacket_name,
        q_peak_MW_m2=q_peak_MW,
        dT_wall_peak_K=dT_peak,
        T_wg_peak_K=peak_T,
        sigma_th_peak_MPa=s_th_peak,
        p_gas_MPa=pc / 1e6,
        p_coolant_max_MPa=p_co_chamber / 1e6,
        dP_liner_peak_MPa=dP_peak,
        sigma_p_peak_MPa=s_p_peak,
        sigma_eq_peak_MPa=peak_s,
        liner_min_SF=min_sf,
        liner_thickness_mm=t_liner * 1000.0,
        jacket_thickness_mm=t_jacket * 1000.0,
        jacket_hoop_MPa=sigma_jacket_hoop,
        jacket_axial_MPa=sigma_jacket_axial,
        jacket_SF=sf_jacket,
        thrust_kN=thrust_N / 1e3,
    )

    calib = "✓ 量级合理" if 60.0 <= q_peak_MW <= 200.0 else "⚠ 偏离常见量级"
    sf_flag = "  ⚠ 衬里塑性(SF<1)" if min_sf < 1.0 else ""
    wf.notes = [
        f"峰值：T_wg={peak_T:.0f} K · q={q_peak_MW:.0f} MW/m² · σ_eq={peak_s:.0f} MPa · "
        f"min SF={min_sf:.2f}{sf_flag}",
        f"标定：喉部热流 {q_peak_MW:.0f} MW/m²（公开 Raptor/RS-25 ~80–160）{calib}；"
        f"数据源 cooling_marching 剖面（CoolProp+Bartz+辐射）。",
    ]
    return wf


def _mach_supersonic(area_ratio: float, gamma: float) -> float:
    """超声速区由 A/A* 二分求马赫数(A/A*≥1)。"""
    if area_ratio <= 1.0:
        return 1.0

    def _ar(M):
        base = (2.0 / (gamma + 1.0)) * (1.0 + (gamma - 1.0) / 2.0 * M * M)
        return (1.0 / M) * base ** ((gamma + 1.0) / (2.0 * (gamma - 1.0)))

    lo, hi = 1.0 + 1e-6, 60.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if _ar(mid) > area_ratio:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def _mach_subsonic(area_ratio: float, gamma: float) -> float:
    """亚声速支由 A/A* 二分求马赫数（A/A*≥1，圆柱/收敛段静压用）。"""
    if area_ratio <= 1.0:
        return 1.0

    def _ar(M):
        base = (2.0 / (gamma + 1.0)) * (1.0 + (gamma - 1.0) / 2.0 * M * M)
        return (1.0 / M) * base ** ((gamma + 1.0) / (2.0 * (gamma - 1.0)))

    lo, hi = 1e-6, 1.0 - 1e-9
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if _ar(mid) > area_ratio:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# ─────────────────────────────────────────────────────────
# 绘图:发动机剖面温度云图 + 应力云图
# ─────────────────────────────────────────────────────────

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
    _draw(ax_S, wf.sigma_eq, "② 等效应力 σ_eq [MPa]", "viridis", "σ_eq [MPa]", wf.peak_sigma_eq)

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
    _minsf = wf.min_SF if wf.SF else 0.0
    _plastic = _minsf < 1.0
    _exp = (
        "③ 内壁安全系数 SF(x) 是什么？\n\n"
        "SF = σ_y(T_wg) / σ_eq\n"
        "  = 材料在该处壁温的屈服强度\n"
        "    ÷ 该处等效应力(热应力+压差弯曲)\n\n"
        "SF ≥ 1：应力低于屈服 → 弹性工作(安全)\n"
        "SF < 1：应力超过屈服 → 衬里塑性\n"
        "    每次点火-关机循环都塑性变形,\n"
        "    低周疲劳(LCF)寿命受限(铜室核心瓶颈)。\n\n"
        f"本工况 min SF = {_minsf:.2f} @ 喉部"
        + ("   ⚠ <1 衬里塑性" if _plastic else "   ✓ ≥1 弹性")
    )
    ax_exp.text(0.02, 0.98, _exp, transform=ax_exp.transAxes, va="top", ha="left",
                fontsize=8.5, color="#374151", linespacing=1.5,
                bbox=dict(boxstyle="round,pad=0.6",
                          fc="#fef2f2" if _plastic else "#f0fdf4", ec="#cbd5e1"))

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
    src = "marching 剖面" if wf.source == "marching-profile" else "1D 简化"
    region = ("主燃烧室段" if (wf.loads and getattr(wf.loads, "domain", "") == "chamber")
              else "推力室全程")
    ax_geo.set_title(f"再生冷却沿程剖面  ·  {region}(喷注器→喷管出口)  ·  数据源:{src}",
                     fontsize=11, fontweight="bold", loc="left", color="#0f172a")
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
                  label=f"辐射(占{100.0*wf.q_rad_fraction:.0f}%)")
        ax_q.legend(fontsize=7, loc="upper left", framealpha=0.8)
    ax_q.set_ylabel("q [MW/m²]", fontsize=8)
    ax_q.set_title("① 热流密度 q(x) — 哪里最烫", fontsize=9, loc="left",
                   color="#111827", fontweight="bold")
    ax_q.annotate(f"峰值 {q.max():.0f}", (xt, q.max()), textcoords="offset points",
                  xytext=(6, -2), fontsize=8, color="#dc2626", fontweight="bold")

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
    if p.size and float(p.max()) > 0:
        ax_p.text(0.98, 0.1, f"Δp ≈ {p.max()-p.min():.1f} MPa  (入口{p.max():.0f}→出口{p.min():.0f})",
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
    dp = float(p.max() - p.min()) if p.size else 0.0
    Vmax = float(v.max()) if v.size else 0.0
    if wf.loads is not None and getattr(wf.loads, "liner_min_SF", 0.0):
        minSF = float(wf.loads.liner_min_SF)
    else:
        minSF = float(min(wf.SF)) if wf.SF else 0.0
    def _m(ok): return "✓" if ok else "⚠"
    rows = [
        (_m(float(q.max()) <= 160.0), "峰值热流 q", f"{q.max():.0f} MW/m²"),
        (_m(not over), "峰值壁温 T_wg", f"{Twg.max():.0f} K / 上限{T_max:.0f}"),
        ("·", "冷却剂升温", f"{Tco.min():.0f}→{Tco.max():.0f} K"),
        (_m(dp < 0.45 * p.max() if p.max() > 0 else True), "压降 Δp", f"{dp:.1f} MPa"),
        (_m(Vmax < 120.0), "最大流速 V", f"{Vmax:.0f} m/s"),
        (_m(minSF >= 1.0), "内壁 min SF", f"{minSF:.2f}"),
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

    fig.suptitle("")
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
    co = cool.outputs if cool else {}

    wf = compute_chamber_wall_field(
        r.summary, geom, cooling_outputs=co,
        inner_material_key="grcop84",
        coolant_temp_K=r.states["fuel_after_cooling"].temperature if "fuel_after_cooling" in r.states else 300.0,
    )
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
