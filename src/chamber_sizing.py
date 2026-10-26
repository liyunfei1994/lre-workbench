"""
chamber_sizing.py — 圆筒形主燃烧室几何与总和特性参数设计（教材级"设计方法"）

把《液体火箭发动机设计》(朱宁昌) / Sutton《Rocket Propulsion Elements》/
Huzel & Huang (NASA SP-125) 里燃烧室"总和特性参数 + 几何尺寸"的设计链一次算齐：

  ┌ 总和特性参数 ───────────────────────────────────────────┐
  │ 特征长度  L* = V_c / A_t            （推进剂在室内反应所需容积/喉部面积）│
  │ 停留时间  t_s = ρ_c·V_c / ṁ          （燃气平均驻留时间，决定燃烧完全度）│
  │ 流量密度  室截面 ṁ/A_c、喉部 ṁ/A_t  （单位面积质量流，反映热/结构负荷）│
  └─────────────────────────────────────────────────────────┘
  ┌ 几何尺寸 ───────────────────────────────────────────────┐
  │ 收缩比    ε_c = A_c / A_t            →  室截面 A_c、室直径 D_c          │
  │ 室容积    V_c = L*·A_t                                                   │
  │ 收缩段    半角 β → 长 L_conv、锥台体积 V_conv                           │
  │ 圆筒段    V_cyl = V_c − V_conv  →  圆筒长 L_cyl = V_cyl / A_c            │
  │ 长径比    L_cyl/D_c、(L_cyl+L_conv)/D_c                                 │
  └─────────────────────────────────────────────────────────┘

设计方法（朱宁昌 §燃烧室几何设计 / Huzel&Huang §4）：
  1) 由热力学定 A_t（喉部）与 ε_c（收缩比，按 M_c、散热、结构折中）；
  2) 选 L*（由推进剂组合的实验停留时间定，LOX/LCH4 典型 0.8–1.2 m）；
  3) V_c = L*·A_t；扣除收缩段锥台体积得圆筒段，定 L_cyl；
  4) 校核 t_s（停留时间）、L/D（长径比）、流量密度是否落在经验区间，
     不合理则回到 (2)/(1) 调 L* 或 ε_c。

本模块只做几何/特性参数（不含壁厚/冷却通道——那在 cooling.py / geometry/chamber.py）。
所有 SI 输入，返回 dict（键与 cycle.py 中 chamber_result.outputs 的 cc_* 同口径，
新增键带物理量后缀），外加 notes / validation 文本。
"""

from __future__ import annotations

import math

_R_UNIVERSAL = 8314.462618   # J/(kmol·K)

# ── LOX/LCH4 经验典型区间（软校核，仅给提示，不报错）──────────────
_L_STAR_RANGE      = (0.8, 1.2)     # 特征长度 [m]
_EPS_C_RANGE       = (2.0, 4.0)     # 收缩比 [-]（高压发动机偏下限）
_T_STAY_RANGE_MS   = (0.5, 5.0)     # 停留时间 [ms]
_LD_CYL_RANGE      = (0.5, 2.5)     # 圆筒段长径比 L_cyl/D_c [-]


def _chamber_mach_subsonic(eps_c: float, gamma: float) -> float:
    """等熵流 A/A* = ε_c 的亚声速根（二分）。
    亚声速段 A/A* 随 M 增大而减小：f > ε_c ⇒ M 偏小 ⇒ 抬 _lo。"""
    lo, hi = 1e-5, 0.9999
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        t = 1.0 + (gamma - 1.0) / 2.0 * mid * mid
        f = (1.0 / mid) * (2.0 / (gamma + 1.0) * t) ** ((gamma + 1.0) / (2.0 * (gamma - 1.0)))
        if f > eps_c:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def size_combustion_chamber(
    *,
    At: float,
    pc: float,
    mdot_total: float,
    T_c: float,
    molar_mass: float,
    gamma: float,
    l_star: float,
    contraction_ratio: float,
    conv_half_angle_deg: float = 30.0,
    c_star: float | None = None,
) -> dict:
    """
    圆筒形燃烧室几何 + 总和特性参数设计。

    参数
    ────
    At                : 喉部面积 [m²]
    pc                : 燃烧室（喷注面滞止）压力 [Pa]
    mdot_total        : 总质量流量 [kg/s]
    T_c               : 燃烧室燃气温度 [K]（真实主流温度）
    molar_mass        : 燃气平均摩尔质量 [kg/kmol]
    gamma             : 燃气比热比 [-]
    l_star            : 特征长度 L* [m]
    contraction_ratio : 收缩比 ε_c = A_c/A_t [-]
    conv_half_angle_deg : 收缩段半锥角 β [°]（典型 25–45°）
    c_star            : 特征速度 [m/s]（可选，仅用于停留时间的交叉校核）

    返回
    ────
    dict：cc_* 几何/特性参数 + "_notes"(list[str]) + "_validation"(list[str])
    """
    pi = math.pi
    if not math.isfinite(contraction_ratio) or contraction_ratio <= 1:
        raise ValueError("chamber contraction ratio must exceed one")
    if not all(math.isfinite(v) and v > 0 for v in (At, l_star)):
        raise ValueError("throat area and characteristic length must be finite and positive")
    eps_c = contraction_ratio

    # ── 燃气状态：室内燃气密度 ρ_c = pc·MW/(Ru·T_c) ──────────────
    R_spec = _R_UNIVERSAL / max(molar_mass, 1e-9)
    rho_c = pc / max(R_spec * T_c, 1e-9)

    # ── 喉部 / 室截面几何 ───────────────────────────────────────
    r_t = math.sqrt(At / pi)
    D_t = 2.0 * r_t
    Ac = eps_c * At
    r_c = math.sqrt(Ac / pi)
    D_c = 2.0 * r_c

    # ── 容积链：V_c = L*·A_t；扣收缩段（入口倒角+直锥+喉部圆弧）得圆筒段 ──────────────
    # 收缩段形状取唯一规范轮廓（入口倒角 R1 + 直锥 + 喉部圆弧 Ru=1.5·Rt，两端相切无尖角），
    # 与几何估算/喷管页/热结构云图完全同口径（chamber_contour 模块），不再用"纯尖角锥"简化。
    Vc = l_star * At
    from chamber_contour import convergent_geometry
    _cg = convergent_geometry(r_c, r_t, conv_half_angle_deg)
    L_conv = _cg["L_conv_m"]                                    # 收缩段轴向长（入口弧+直锥+喉部弧）
    V_conv = _cg["V_conv_m3"]                                   # 入口弧 + 直锥锥台 + 喉部弧 旋转体
    R1_blend = _cg.get("R1_m", 0.0)                             # 圆筒→收缩段入口倒角半径（已截断）

    V_cyl = Vc - V_conv
    cyl_clamped = False
    if V_cyl <= 0.0:
        raise ValueError("指定特征容积不足以容纳收缩段，不能将圆筒长度裁成零。")
    L_cyl = V_cyl / Ac
    L_total = L_cyl + L_conv                                    # 喷注面→喉部"表观室长"

    # ── 长径比 ──────────────────────────────────────────────────
    LD_cyl = L_cyl / D_c
    LD_total = L_total / D_c

    # ── 总和特性参数 ───────────────────────────────────────────
    # 停留时间 t_s = 室内燃气质量 / 质量流 = ρ_c·V_c / ṁ
    t_stay_s = rho_c * Vc / max(mdot_total, 1e-9)
    t_stay_ms = t_stay_s * 1000.0
    # 流量密度（单位面积质量流）：室截面 与 喉部
    mass_flux_cc = mdot_total / max(Ac, 1e-12)        # kg/(m²·s)
    mass_flux_throat = mdot_total / max(At, 1e-12)    # = G_t
    # 截面马赫数（驻压损失指标）
    Mcc = _chamber_mach_subsonic(eps_c, gamma)

    # ── 燃烧组织/稳定性筛查：声学模态频率估算 ───────────────────
    # 一阶切向 1T（高频不稳定最危险模态）：f = 1.8412·a/(π·D_c)
    # 一阶纵向 1L：f = a/(2·L_total)。喷注器/隔板/声腔设计须避开这些频率。
    a_c = math.sqrt(gamma * R_spec * T_c)
    f_1T = 1.8412 * a_c / (pi * D_c)
    f_1L = a_c / (2.0 * max(L_total, 1e-6))

    notes = [
        "── 燃烧室总和特性参数与几何（圆筒形，教材设计方法）──────",
        f"  形状：圆筒段 + 入口倒角 R1={R1_blend*1e3:.1f} mm + 直锥(半角 β={conv_half_angle_deg:.0f}°) + 喉部圆弧（两端相切无尖角）",
        f"  特征长度 L* = {l_star:.3f} m → 室容积 V_c = L*·A_t = {Vc*1e3:.2f} L",
        f"  收缩比 ε_c = {eps_c:.2f} → 室截面 A_c = {Ac*1e4:.1f} cm²，室直径 D_c = {D_c*1e3:.1f} mm",
        f"  喉部 D_t = {D_t*1e3:.1f} mm（A_t = {At*1e4:.2f} cm²）",
        f"  收缩段 L_conv = {L_conv*1e3:.1f} mm（入口弧 R1 + 直锥 β={conv_half_angle_deg:.0f}° + 实际喉部圆弧 Ru={_cg['Ru_m']*1e3:.2f} mm），"
        f"体积 V_conv = {V_conv*1e3:.2f} L（占 V_c 的 {100.0*V_conv/max(Vc,1e-12):.0f}%）",
        f"  圆筒段 L_cyl = {L_cyl*1e3:.1f} mm（V_cyl = V_c − V_conv = {V_cyl*1e3:.2f} L）"
        + ("  ⚠ 收缩段已占满 V_c，无圆筒段：建议增大 L* 或减小 ε_c/β" if cyl_clamped else ""),
        f"  表观室长 L_total = L_cyl + L_conv = {L_total*1e3:.1f} mm",
        f"  长径比 L_cyl/D_c = {LD_cyl:.2f}，(L_cyl+L_conv)/D_c = {LD_total:.2f}",
        f"  室内燃气密度 ρ_c = {rho_c:.1f} kg/m³（pc·MW/Ru·T_c，T_c={T_c:.0f} K）",
        f"  停留时间 t_s = ρ_c·V_c/ṁ = {t_stay_ms:.2f} ms",
        f"  流量密度：室截面 ṁ/A_c = {mass_flux_cc:.0f} kg/(m²·s)，"
        f"喉部 ṁ/A_t = {mass_flux_throat:.0f} kg/(m²·s)",
        f"  室截面马赫数 M_cc = {Mcc:.3f}（驻压损失指标，低→损失小但室更长）",
        f"  声学模态（燃烧组织/稳定性筛查）：声速 a_c={a_c:.0f} m/s → "
        f"1T 切向 {f_1T:.0f} Hz，1L 纵向 {f_1L:.0f} Hz"
        "（喷注耦合/声腔设计须避开；1T 是高频不稳定最危险模态）",
    ]
    if c_star is not None and c_star > 1.0:
        # 交叉校核：L* 与停留时间的解析关系 t_s = L*·ρ_c/(ṁ/A_t) = L*·ρ_c·A_t/ṁ
        # （与 ρ_c·V_c/ṁ 恒等，仅作自洽确认）
        t_alt = l_star * rho_c * At / max(mdot_total, 1e-9) * 1000.0
        notes.append(f"  （自洽校核：L*·ρ_c·A_t/ṁ = {t_alt:.2f} ms，应等于 t_s）")

    # ── 软校核（落区间打 ✓，越界给提示）─────────────────────────
    def _chk(name, val, lo, hi, unit):
        if lo <= val <= hi:
            return f"  ✓ {name} = {val:.2f} {unit}（典型 {lo:g}–{hi:g}）"
        side = "偏低" if val < lo else "偏高"
        return f"  ⚠ {name} = {val:.2f} {unit} {side}（典型 {lo:g}–{hi:g}）"

    validation = [
        "── 经验区间校核（LOX/LCH4）─────────────────────────────",
        _chk("特征长度 L*", l_star, *_L_STAR_RANGE, "m"),
        _chk("收缩比 ε_c", eps_c, *_EPS_C_RANGE, "-"),
        _chk("停留时间 t_s", t_stay_ms, *_T_STAY_RANGE_MS, "ms"),
        _chk("圆筒段长径比 L_cyl/D_c", LD_cyl, *_LD_CYL_RANGE, "-"),
    ]

    return {
        # ── 兼容旧键（cycle.py / GUI 已在用）──────────────────────
        "cc_throat_area_m2": At,
        "cc_throat_diameter_mm": D_t * 1000.0,
        "cc_area_m2": Ac,
        "cc_diameter_m": D_c,
        "cc_diameter_mm": D_c * 1000.0,
        "cc_contraction_ratio": eps_c,
        "cc_mach_number": Mcc,
        "cc_l_star_m": l_star,
        "cc_volume_L": Vc * 1000.0,
        "cc_length_m": L_cyl,                 # 圆筒段净长（已扣收缩段锥台，比旧版 V_c/A_c 更准）
        "cc_length_mm": L_cyl * 1000.0,
        # ── 新增：总和特性参数 + 完整几何 ────────────────────────
        "cc_volume_m3": Vc,
        "cc_convergent_length_mm": L_conv * 1000.0,
        "cc_convergent_volume_L": V_conv * 1000.0,
        "cc_cyl_volume_L": V_cyl * 1000.0,
        "cc_total_length_mm": L_total * 1000.0,
        "cc_LD_ratio_cyl": LD_cyl,
        "cc_LD_ratio_total": LD_total,
        "cc_residence_time_ms": t_stay_ms,
        "cc_residence_time_s": t_stay_s,
        "cc_mass_flux_chamber_kg_m2s": mass_flux_cc,
        "cc_mass_flux_throat_kg_m2s": mass_flux_throat,
        "cc_chamber_gas_density_kgm3": rho_c,
        "cc_conv_half_angle_deg": conv_half_angle_deg,
        "cc_cyl_section_clamped": 1.0 if cyl_clamped else 0.0,
        "cc_sound_speed_ms": a_c,
        "cc_acoustic_1T_Hz": f_1T,
        "cc_acoustic_1L_Hz": f_1L,
        "_notes": notes,
        "_validation": validation,
    }
