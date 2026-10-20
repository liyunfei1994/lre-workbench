"""
cooling.py  -  再生冷却通道模型 V8

V8 修改记录：
─────────────────────────────────────────────────────────
1. 新增 Bartz 模式（bartz_mode=True，默认）：
   · Bartz 关联式估算喉部热流密度（h_g 和 q_throat）
   · σ 修正系数（Bartz-Hatch 边界层温度校正，M=1 喉部）
   · 三区积分：圆柱燃烧室 + 收缩段 + 喉部区 + 喷管尖端
     各区热流按 (D_t/D_local)^1.8 缩放（与质量通量幂次一致）
   · 超临界 CH4 变 cp（NIST 拟合，100–450 K）
   · 1D 热阻模型：T_wall_hot = T_coolant + q/h_coolant + q·t_w/k_wall
   · 从质量流量、pc、Tc、γ、Mm 自动推算喉部几何尺寸

2. 保留固定热载荷模式（bartz_mode=False）：
   · 与 V7 完全向后兼容（固定 Q 和 dp）

3. 新增输出量（Bartz 模式）：
   · q_throat_MW_m2  喉部热流密度
   · Q_zone_* MW     各区贡献热载荷
   · T_wall_hot_K    燃气侧壁温（GRCop-84 热面）
   · r_throat_m / D_throat_m  喉部几何

参考：
  Bartz (1957) NACA TN-3997；Huzel & Huang NASA SP-125；
  Piunti (2022) 论文附录表 A1-A3
"""

from __future__ import annotations

import math
from functools import lru_cache

from core import ComponentResult, FlowState, ValidationError, _check_range

# ── V15：CoolProp 真实甲烷物性（优先）+ fluids 摩擦系数（优先）──────────
# 审查指出 _ch4_density_supercritical 的 Z 线性式在稠密/伪临界区失真
# （71 MPa/175 K 处 ρ 偏差 ~+70% 靠硬截断兜底）。现统一走 CoolProp
# Setzmann-Wagner 真实 EOS，失败时回退原工程拟合。
try:
    from CoolProp.CoolProp import PropsSI as _PROPSSI_CH4
    _COOLPROP_CH4_OK = True
except Exception:
    _COOLPROP_CH4_OK = False

try:
    from fluids.friction import friction_factor as _fluids_friction_factor
    _FLUIDS_OK = True
except Exception:
    _FLUIDS_OK = False

# ── 物理常数 ─────────────────────────────────────────────
_R_UNIVERSAL  = 8314.462618   # J/(kmol·K)
_G0           = 9.80665       # m/s²

# ── GRCop-84 铜铬铌合金壁面默认参数 ───────────────────────
_WALL_THICKNESS_M    = 0.0005   # 壁厚 [m]  0.5 mm — Raptor 内衬典型，薄以降低 ΔT_cond
_WALL_CONDUCTIVITY   = 320.0    # 导热系数 [W/(m·K)]  GRCop-84 @ 600 K
# 冷却剂侧传热系数：超临界 CH4（30 MPa，2 mm 通道，高流速）湍流强制对流
# Dittus-Boelter 估算：Nu ≈ 0.023 Re^0.8 Pr^0.4
# Raptor 高纵横比通道（HARCC）实测可达 500,000–800,000 W/(m²·K)
# V5.3.6：300000 → 500000（更贴近 Raptor HARCC 实测；可降低燃气侧壁温估算）
_COOLANT_HTC         = 500000.0 # 冷却剂侧传热系数 [W/(m²·K)]

# ── 收缩段半锥角（用于估算收缩段面积）────────────────────
_CONV_HALF_ANGLE_DEG = 40.0    # 度，典型值 35–45°


def _ch4_cp_supercritical(T: float) -> float:
    """
    超临界 CH4（p > 10 MPa）定压比热拟合 [J/(kg·K)]
    基于 NIST Webbook 30 MPa 等压线数据（100–450 K）

    主要特征：
      · 亚临界区（<120 K）约 3500 J/kg/K（液态超冷）
      · 伪临界峰值（~200 K @ 30 MPa）约 4800 J/kg/K
      · 超临界气态区（>300 K）稳定在 3000–3500 J/kg/K
    """
    if T < 110.0:
        return 3500.0
    elif T < 180.0:
        # 向伪临界峰爬升
        t = (T - 110.0) / (180.0 - 110.0)
        return 3500.0 + t * (4800.0 - 3500.0)
    elif T < 220.0:
        # 伪临界峰附近
        t = (T - 180.0) / (220.0 - 180.0)
        return 4800.0 - t * (4800.0 - 4200.0)
    elif T < 300.0:
        # 下降至超临界气态
        t = (T - 220.0) / (300.0 - 220.0)
        return 4200.0 - t * (4200.0 - 3200.0)
    elif T < 450.0:
        # 高温气态：缓慢上升（甲烷振动激发）
        t = (T - 300.0) / (450.0 - 300.0)
        return 3200.0 + t * (3600.0 - 3200.0)
    else:
        return 3600.0


# ── 超临界甲烷输运物性（A+B 新增：算 h_c 用）──────────────────
# 冷却通道工况 T~150–800 K、p~30–80 MPa（远超 CH4 临界点 190.6 K / 4.6 MPa）。
# 以下为工程量级拟合（NIST Webbook 量级），用于 Dittus-Boelter 估算冷却剂换热。
# ⚠ 详细设计应替换为 NIST/CoolProp 实表，尤其伪临界区（~200 K @ 高压）。

def _ch4_density_supercritical(T: float, p: float) -> float:
    """超临界 CH4 密度 [kg/m³]：理想气体 + 弱压缩修正 ρ=pM/(ZRT)，M=16.04 g/mol。

    在 Tr>1.3 的类气态区精度尚可；近伪临界区偏差大（该处密度对 T 极敏感）。
    """
    M = 16.04e-3
    Z = 1.0 + 1.2e-9 * max(p, 0.0)          # 高压甲烷 Z 略 >1 的极简修正
    rho = p * M / (Z * 8.314462 * max(T, 50.0))
    return max(5.0, min(rho, 470.0))


def _ch4_viscosity(T: float) -> float:
    """超临界 CH4 动力黏度 [Pa·s]：幂律拟合（300 K≈1.1e-5；压力依赖弱，忽略）。"""
    return 1.1e-5 * (max(T, 100.0) / 300.0) ** 0.69


def _ch4_thermal_conductivity(T: float) -> float:
    """超临界 CH4 导热系数 [W/(m·K)]：幂律拟合（300 K≈0.034）。"""
    return 0.034 * (max(T, 100.0) / 300.0) ** 1.16


@lru_cache(maxsize=16384)
def _ch4_props_coolprop(T_deciK: int, p_50kPa: int):
    """量化缓存（0.1 K / 0.05 MPa）的 CH4 真实物性：(ρ, μ, k, cp)。"""
    T = T_deciK / 10.0
    p = p_50kPa * 5.0e4
    rho = _PROPSSI_CH4("D", "T", T, "P", p, "Methane")
    mu  = _PROPSSI_CH4("V", "T", T, "P", p, "Methane")
    k   = _PROPSSI_CH4("L", "T", T, "P", p, "Methane")
    cp  = _PROPSSI_CH4("C", "T", T, "P", p, "Methane")
    return float(rho), float(mu), float(k), float(cp)


def ch4_props(T: float, p: float):
    """液态/超临界 CH4 物性 (ρ[kg/m³], μ[Pa·s], k[W/mK], cp[J/kgK])。
    CoolProp（NIST 级 EOS）优先；不可用/越界回退工程拟合。"""
    if _COOLPROP_CH4_OK:
        try:
            T_q = max(92.0, min(T, 620.0))    # Setzmann-Wagner 有效域内夹取
            p_q = max(1.0e5, min(p, 9.0e8))
            return _ch4_props_coolprop(int(round(T_q * 10.0)), int(round(p_q / 5.0e4)))
        except Exception:
            pass
    return (_ch4_density_supercritical(T, p), _ch4_viscosity(T),
            _ch4_thermal_conductivity(T), _ch4_cp_supercritical(T))


def _mach_subsonic_from_area_ratio(eps_c: float, gamma: float) -> float:
    """等熵流 A/A* = ε_c 的亚声速根（二分）。"""
    lo, hi = 1e-5, 0.9999
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        t = 1.0 + (gamma - 1.0) / 2.0 * mid * mid
        f = (1.0 / mid) * (2.0 / (gamma + 1.0) * t) ** ((gamma + 1.0) / (2.0 * (gamma - 1.0)))
        if f > eps_c:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _dittus_boelter_htc(mdot: float, rho: float, mu: float, k_co: float,
                        cp_co: float, A_flow: float, D_h: float,
                        enhancement: float = 1.0):
    """Dittus-Boelter 强制对流：返回 (h_c, V, Re, Pr, Nu)。

    Nu = 0.023·Re^0.8·Pr^0.4（冷却剂被加热取指数 0.4）；
    enhancement 为喉部弯曲/入口段强化因子（缺省 1.0，典型 1.1–1.5）。
    """
    V  = mdot / max(rho * A_flow, 1e-9)
    Re = rho * V * D_h / max(mu, 1e-12)
    Pr = mu * cp_co / max(k_co, 1e-12)
    Nu = 0.023 * Re ** 0.8 * Pr ** 0.4 * enhancement
    h_c = Nu * k_co / max(D_h, 1e-9)
    return h_c, V, Re, Pr, Nu


def _bartz_gas_props(gamma: float, R_spec: float, T_c: float):
    """
    燃烧产物气体属性估算（Bartz 关联式所需量）

    Returns
    -------
    mu   : 动力黏度 [Pa·s]，Sutherland 标定到 LOX/CH4 混合产物
    cp_g : 比热容 [J/(kg·K)]，量热完全气体近似
    Pr   : Prandtl 数，Eucken 关系
    """
    # cp_g（量热完全气体）
    cp_g = gamma / (gamma - 1.0) * R_spec

    # 动力黏度：参考值 μ_ref ≈ 8e-5 Pa·s @ 3000 K，T^0.7 缩放
    # （LOX/LCH4 平衡燃烧产物 NIST 拟合范围 3000–3800 K）
    T_ref = 3000.0
    mu_ref = 8.0e-5
    mu = mu_ref * (T_c / T_ref) ** 0.7

    # Eucken 关系：Pr ≈ 4γ/(9γ - 5)
    Pr = 4.0 * gamma / (9.0 * gamma - 5.0)

    return mu, cp_g, Pr


def _bartz_sigma(gamma: float, T_w: float, T_c: float) -> float:
    """
    Bartz 修正系数 σ（喉部 M=1）

    σ = { 0.5 × (T_w/T_c) × [(γ+1)/2] + 0.5 }^(-0.68)
        × { (γ+1)/2 }^(-0.12)
    """
    factor_M = (gamma + 1.0) / 2.0
    inner    = 0.5 * (T_w / T_c) * factor_M + 0.5
    sigma    = inner ** (-0.68) * factor_M ** (-0.12)
    # σ > 1 在 T_wall << T_c 时物理正确（冷壁增强热流）；上限 2.0 对应极端超冷壁
    return max(0.20, min(sigma, 2.00))


def _throat_area(mdot_total: float, pc: float, T_c: float,
                 gamma: float, Mm: float) -> float:
    """
    从质量流量和燃烧室条件推算喉部面积 A* [m²]

    ṁ = p_c · A* · Γ / √(R_spec · T_c)
    Γ = √γ · (2/(γ+1))^((γ+1)/(2(γ-1)))
    """
    R_spec = _R_UNIVERSAL / Mm
    exp    = (gamma + 1.0) / (2.0 * (gamma - 1.0))
    Gamma  = math.sqrt(gamma) * (2.0 / (gamma + 1.0)) ** exp
    A_star = mdot_total * math.sqrt(R_spec * T_c) / (pc * Gamma)
    return A_star


def _mach_supersonic_from_area_ratio(AR: float, gamma: float) -> float:
    """等熵流 A/A* = AR 的超声速根（二分）。"""
    if AR <= 1.0 + 1e-10:
        return 1.0
    lo, hi = 1.0001, 50.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        t = 1.0 + (gamma - 1.0) / 2.0 * mid * mid
        f = (1.0 / mid) * (2.0 / (gamma + 1.0) * t) ** ((gamma + 1.0) / (2.0 * (gamma - 1.0)))
        if f < AR:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _integrate_heat_load(h_g_t, r_t, CR, L_star, T_aw_t,
                          T_aw_cc, T_w, gamma,
                          T_c: float = 0.0, r_f: float = 0.94,
                          cover_eps: float = 0.0,
                          conv_half_angle_deg: float = 30.0):
    """
    分区热载荷积分（燃烧室 + 收缩段 + 喉部带 + 喷管头部 + 喷管延伸段）

    热流密度缩放：h_g_local / h_g_t ≈ (D_t/D_local)^1.8

    V15.1（对抗校核 major 修复）：
      · 新增喷管延伸段（1.5 r_t 下游 → cover_eps），消除"循环闭合只覆盖
        ε≈8、marching 剖面覆盖全喷管"的系统性口径差（全喷管再生时旧版
        Q 低估 ~40%、冷却剂出口温低 ~80 K、连带预燃室入口焓偏低）。
      · L_cc 扣除收缩段锥台体积（与 chamber_sizing 同口径，旧 L*/CR 偏长 25%）。
      · 收缩半角参数化（默认 30°，与 chamber_sizing 一致；旧硬编码 40°）。
    """
    import math as _math
    A_t    = _math.pi * r_t * r_t
    Ac     = CR * A_t
    r_cc   = r_t * _math.sqrt(CR)

    half_ang = _math.radians(max(min(conv_half_angle_deg, 80.0), 5.0))
    L_conv   = (r_cc - r_t) / _math.tan(half_ang)
    V_conv   = (_math.pi * L_conv / 3.0) * (r_cc * r_cc + r_cc * r_t + r_t * r_t)
    L_cc     = max(L_star * A_t - V_conv, 0.0) / Ac
    A_cyl    = 2.0 * _math.pi * r_cc * L_cc
    A_conv   = _math.pi * (r_cc + r_t) * _math.sqrt(
        (r_cc - r_t)**2 + L_conv**2
    )

    # 喉部带：喉部前后各 r_t（总轴向跨度 = 2 r_t）
    L_throat_band = 2.0 * r_t          # 总跨度（±r_t）
    r_band_avg    = r_t * 1.05         # 微曲过渡使平均半径略大
    A_throat_band = 2.0 * _math.pi * r_band_avg * L_throat_band   # 侧面积

    L_noz_eff  = 1.5 * r_t            # 喷管头部有效冷却长度（前 1.5 r_t）
    r_noz_avg  = r_t * 1.15
    A_noz_band = 2.0 * _math.pi * r_noz_avg * L_noz_eff

    h_g_cc  = h_g_t * (CR ** (-0.9))
    q_cc    = h_g_cc * (T_aw_cc - T_w)

    diam_conv_eff = 2.0 * r_t * CR ** 0.25
    ratio_conv    = (2.0 * r_t / diam_conv_eff) ** 1.8
    h_g_conv      = h_g_t * ratio_conv
    T_aw_conv     = 0.5 * (T_aw_cc + T_aw_t)
    q_conv        = h_g_conv * (T_aw_conv - T_w)

    q_throat_band = h_g_t * (T_aw_t - T_w)

    h_g_noz_eff = h_g_t * 0.55
    q_noz       = h_g_noz_eff * (T_aw_t * 0.85 - T_w)

    # ── 喷管延伸段（V15.1）：r=1.3·r_t → √cover_eps·r_t，15° 等效锥 12 条带 ──
    # h_g ∝ (1/AR)^0.9；T_aw 按局部超声速 M 用完整恢复温度式（需 T_c）。
    Q_noz_ext = 0.0
    A_noz_ext = 0.0
    if cover_eps and cover_eps > 1.8 and T_c > 100.0:
        r_end = r_t * _math.sqrt(cover_eps)
        r_start = 1.3 * r_t            # 喷管头部带（轴向 1.5 r_t）的近似末端半径
        if r_end > r_start:
            sin15 = _math.sin(_math.radians(15.0))
            n_strip = 12
            for _i in range(n_strip):
                r0 = r_start + (r_end - r_start) * _i / n_strip
                r1 = r_start + (r_end - r_start) * (_i + 1) / n_strip
                rm = 0.5 * (r0 + r1)
                ds = (r1 - r0) / sin15
                dA = 2.0 * _math.pi * rm * ds
                AR = (rm / r_t) ** 2
                M_loc = _mach_supersonic_from_area_ratio(AR, gamma)
                half = (gamma - 1.0) / 2.0 * M_loc * M_loc
                T_aw_loc = T_c * (1.0 + r_f * half) / (1.0 + half)
                q_loc = h_g_t * (1.0 / AR) ** 0.9 * (T_aw_loc - T_w)
                if q_loc > 0.0:
                    Q_noz_ext += q_loc * dA
                A_noz_ext += dA

    Q_cc     = max(0.0, q_cc)    * A_cyl
    Q_conv   = max(0.0, q_conv)  * A_conv
    Q_throat = max(0.0, q_throat_band) * A_throat_band
    Q_noz    = max(0.0, q_noz)   * A_noz_band
    Q_total  = Q_cc + Q_conv + Q_throat + Q_noz + Q_noz_ext

    return {
        "Q_cc_W":       Q_cc,
        "Q_conv_W":     Q_conv,
        "Q_throat_W":   Q_throat,
        "Q_noz_W":      Q_noz,
        "Q_noz_ext_W":  Q_noz_ext,
        "Q_total_W":    Q_total,
        "A_cyl_m2":     A_cyl,
        "A_conv_m2":    A_conv,
        "A_throat_band_m2": A_throat_band,
        "A_noz_m2":     A_noz_band,
        "A_noz_ext_m2": A_noz_ext,
        "h_g_cc":       h_g_cc,
        "h_g_conv":     h_g_conv,
        "q_cc_MW_m2":   q_cc   / 1e6,
        "q_conv_MW_m2": q_conv / 1e6,
        "q_throat_MW_m2": q_throat_band / 1e6,
        "q_noz_MW_m2":  q_noz  / 1e6,
        "r_cc_m":       r_cc,
        "L_cc_m":       L_cc,
        "L_conv_m":     L_conv,
    }


# ── V14：再生冷却通道水力学（修 Major 1：几何/流速/压降自洽）──────────────
def _friction_factor(Re: float, rel_roughness: float) -> float:
    """Darcy 摩擦系数：层流 64/Re；湍流优先 fluids 库（Clamond/Colebrook 精确解），
    fluids 不可用时回退 Haaland 显式式（两者差 <2%）。"""
    Re = max(Re, 1.0)
    if Re < 2300.0:
        return 64.0 / Re
    if _FLUIDS_OK:
        try:
            return float(_fluids_friction_factor(Re=Re, eD=rel_roughness))
        except Exception:
            pass
    inv = -1.8 * math.log10((rel_roughness / 3.7) ** 1.11 + 6.9 / Re)
    return 1.0 / max(inv, 1e-6) ** 2


def resolve_channel_height(mdot_fuel: float, rho_co: float, target_velocity: float,
                           width_m: float, n_channels: int):
    """固定通道数+宽，反算通道「深度」命中目标流速（真实再生通道的做法：
    周向通道数受室周长约束、宽度受铣槽工艺约束，故调深度而非数量）。
    返回 (height_m, clamped)；深度物理钳位 [1, 15] mm。"""
    A_req = mdot_fuel / max(rho_co * target_velocity, 1e-9)
    h = A_req / max(n_channels * width_m, 1e-12)
    h_cl = min(max(h, 1.0e-3), 15.0e-3)
    return h_cl, (abs(h_cl - h) > 1e-12)


def regen_pressure_drop(mdot_fuel: float, rho_co: float, mu_co: float,
                        A_flow: float, D_h: float, L_channel: float,
                        rel_roughness: float = 0.003, k_minor: float = 3.0):
    """通道水力压降 Δp = (f·L/D_h + K_minor)·½ρV²。返回 (dp, V, Re, f)。"""
    V = mdot_fuel / max(rho_co * A_flow, 1e-9)
    Re = rho_co * V * D_h / max(mu_co, 1e-12)
    f = _friction_factor(Re, rel_roughness)
    dp = (f * L_channel / max(D_h, 1e-9) + k_minor) * 0.5 * rho_co * V * V
    return dp, V, Re, f


def _channel_axial_length(r_t: float, CR: float, L_star: float) -> float:
    """冷却通道沿程长度估算：圆筒段 L_cc + 收缩段 L_conv + 喉部带(~2 r_t)。"""
    r_cc = r_t * math.sqrt(CR)
    L_cc = L_star / CR
    L_conv = (r_cc - r_t) / math.tan(math.radians(_CONV_HALF_ANGLE_DEG))
    return L_cc + L_conv + 2.0 * r_t


def estimate_regen_dp(mdot_fuel: float, t_proxy: float, p_proxy: float,
                      mdot_total: float, pc: float, t_chamber: float,
                      gamma_c: float, molar_mass_c: float,
                      contraction_ratio: float, l_star_m: float,
                      n_channels: int, width_m: float, height_m: float,
                      auto_size: bool, target_velocity: float,
                      rel_roughness: float = 0.003, k_minor: float = 3.0) -> dict:
    """循环主链 pre-pass：在主泵之前估算再生冷却水力压降（供燃料泵出口压力预算）。
    用代表性冷却剂密度，几何/公式与 CoolingChannel 内部一致 → 与卡片自洽。
    返回 dict(dp_pa, velocity_ms, n_channels, height_m, D_h_m, A_flow_m2, L_channel_m)。"""
    A_t = _throat_area(mdot_total, pc, t_chamber, gamma_c, molar_mass_c)
    r_t = math.sqrt(A_t / math.pi)
    L_ch = _channel_axial_length(r_t, contraction_ratio, l_star_m)
    rho_co, mu_co, _, _ = ch4_props(t_proxy, p_proxy)
    n = max(int(n_channels), 1)
    if auto_size:
        h, _ = resolve_channel_height(mdot_fuel, rho_co, target_velocity, width_m, n)
    else:
        h = height_m
    A_flow = n * width_m * h
    D_h = 2.0 * width_m * h / max(width_m + h, 1e-12)
    dp, V, Re, f = regen_pressure_drop(mdot_fuel, rho_co, mu_co, A_flow, D_h, L_ch,
                                       rel_roughness, k_minor)
    return {"dp_pa": dp, "velocity_ms": V, "n_channels": float(n),
            "height_m": h, "D_h_m": D_h, "A_flow_m2": A_flow, "L_channel_m": L_ch}


class CoolingChannel:
    """
    再生冷却通道模型

    两种工作模式
    bartz_mode=True（默认）：Bartz 关联式估算热载荷
    bartz_mode=False（固定模式）：直接用 heat_load 和 cp（V7 兼容）
    """

    def __init__(
        self,
        mdot_fuel: float,
        t_inlet: float,
        p_inlet: float,
        dp_pa: float = 8.0e6,
        heat_load: float = 90.0e6,
        cp: float = 2.4e3,
        bartz_mode: bool = True,
        mdot_total: float = 0.0,
        pc: float = 0.0,
        t_chamber: float = 3550.0,
        gamma_c: float = 1.18,
        molar_mass_c: float = 21.0,
        contraction_ratio: float = 2.5,
        l_star_m: float = 1.0,
        # ── A+B 新增：再生冷却通道几何（算 h_c 用；缺省 = Raptor 喉部量级）──
        n_channels: int = 150,
        channel_width_m: float = 1.5e-3,
        channel_height_m: float = 5.0e-3,
        wall_thickness_m: float = _WALL_THICKNESS_M,
        wall_conductivity: float = _WALL_CONDUCTIVITY,
        htc_enhancement: float = 1.0,
        # ── V14：自动定尺寸 + 水力压降（修 Major 1）──
        auto_size: bool = True,
        target_velocity_ms: float = 100.0,
        dp_auto: bool = False,
        channel_rel_roughness: float = 0.003,
        channel_k_minor: float = 3.0,
        # ── V15.1：热载荷覆盖范围（0=只到喷管头 1.5r_t，>1.8=积到该面积比）──
        cover_eps: float = 0.0,
        conv_half_angle_deg: float = 30.0,
    ):
        _check_range("CoolingChannel.mdot_fuel", mdot_fuel, lo=0, lo_inclusive=False)
        _check_range("CoolingChannel.t_inlet",   t_inlet,   lo=0, lo_inclusive=False)
        _check_range("CoolingChannel.p_inlet",   p_inlet,   lo=0, lo_inclusive=False)
        _check_range("CoolingChannel.dp_pa",     dp_pa,     lo=0)
        if dp_pa >= p_inlet:
            raise ValidationError(
                f"CoolingChannel: 通道压降 {dp_pa:.0f} Pa >= 入口压力 {p_inlet:.0f} Pa"
            )

        if bartz_mode:
            _check_range("CoolingChannel.mdot_total",    mdot_total,    lo=0, lo_inclusive=False)
            _check_range("CoolingChannel.pc",            pc,            lo=0, lo_inclusive=False)
            _check_range("CoolingChannel.t_chamber",     t_chamber,     lo=500, lo_inclusive=False)
            _check_range("CoolingChannel.gamma_c",       gamma_c,       lo=1.0, lo_inclusive=False)
            _check_range("CoolingChannel.molar_mass_c",  molar_mass_c,  lo=0,   lo_inclusive=False)
            _check_range("CoolingChannel.contraction_ratio", contraction_ratio, lo=1.5)
            _check_range("CoolingChannel.l_star_m",      l_star_m,      lo=0.1)
        else:
            _check_range("CoolingChannel.heat_load", heat_load, lo=0)
            _check_range("CoolingChannel.cp",        cp,        lo=0, lo_inclusive=False)

        self.mdot_fuel        = mdot_fuel
        self.t_inlet          = t_inlet
        self.p_inlet          = p_inlet
        self.dp_pa            = dp_pa
        self.heat_load        = heat_load
        self.cp               = cp
        self.bartz_mode       = bartz_mode
        self.mdot_total       = mdot_total
        self.pc               = pc
        self.t_chamber        = t_chamber
        self.gamma_c          = gamma_c
        self.molar_mass_c     = molar_mass_c
        self.contraction_ratio = contraction_ratio
        self.l_star_m         = l_star_m
        self.n_channels        = max(int(n_channels), 1)
        self.channel_width_m   = channel_width_m
        self.channel_height_m  = channel_height_m
        self.wall_thickness_m  = wall_thickness_m
        self.wall_conductivity = wall_conductivity
        self.htc_enhancement   = htc_enhancement
        self.auto_size            = bool(auto_size)
        self.target_velocity_ms   = target_velocity_ms
        self.dp_auto              = bool(dp_auto)
        self.channel_rel_roughness = channel_rel_roughness
        self.channel_k_minor       = channel_k_minor
        self.cover_eps             = cover_eps
        self.conv_half_angle_deg   = conv_half_angle_deg

    def _evaluate_fixed(self):
        delta_t = self.heat_load / (self.mdot_fuel * self.cp)
        t_out   = self.t_inlet + delta_t
        p_out   = self.p_inlet - self.dp_pa
        state = FlowState(
            name="fuel_after_cooling",
            pressure=p_out, temperature=t_out,
            mass_flow=self.mdot_fuel,
            enthalpy=self.cp * t_out,
            composition={"CH4": 1.0},
        )
        result = ComponentResult(
            name="cooling_channel",
            outputs={
                "heat_load_W":   self.heat_load,
                "heat_load_MW":  self.heat_load / 1e6,
                "delta_T_K":     delta_t,
                "T_in_K":        self.t_inlet,
                "T_out_K":       t_out,
                "P_in_Pa":       self.p_inlet,
                "P_out_Pa":      p_out,
                "delta_P_MPa":   self.dp_pa / 1e6,
                "mode":          0.0,
            },
            notes=[
                f"[固定模式] 吸热 {self.heat_load/1e6:.1f} MW → ΔT = {delta_t:.1f} K",
                f"压降 {self.dp_pa/1e6:.2f} MPa",
                "cp 常数，不含 Bartz 热流分析。",
            ],
        )
        return state, result

    def _evaluate_bartz(self):
        gamma  = self.gamma_c
        Mm     = self.molar_mass_c
        T_c    = self.t_chamber
        pc     = self.pc

        R_spec = _R_UNIVERSAL / Mm

        # 1. 喉部几何
        A_t   = _throat_area(self.mdot_total, pc, T_c, gamma, Mm)
        r_t   = math.sqrt(A_t / math.pi)
        D_t   = 2.0 * r_t
        R_c   = 0.35 * D_t   # 喉部曲率半径 ≈ 0.35 D_t

        # 2. 气体属性
        mu, cp_g, Pr = _bartz_gas_props(gamma, R_spec, T_c)

        # 3. 质量通量
        G_t = self.mdot_total / A_t

        # 4. 绝热壁温
        r_f = Pr ** (1.0 / 3.0)
        T_aw_throat = T_c * (2.0 + r_f * (gamma - 1.0)) / (gamma + 1.0)
        # V15 修正（审查 minor）：恢复温度公式 T_aw = T_c·(1+r·(γ−1)/2·M²)/(1+(γ−1)/2·M²)，
        # r<1 时必须低于滞止温度（旧式 1+r(γ−1)/2·0.04 给出 1.0034·T_c，方向错 +12 K）。
        # M_cc 由收缩比反算（与 cycle/chamber_sizing 同口径）。
        M_cc_aw = _mach_subsonic_from_area_ratio(self.contraction_ratio, gamma)
        _half_cc = (gamma - 1.0) / 2.0 * M_cc_aw * M_cc_aw
        T_aw_cc = T_c * (1.0 + r_f * _half_cc) / (1.0 + _half_cc)

        # ── 5–9. 三侧耦合自洽求解（A+B：算 h_c + 壁温迭代）──────────────
        # 旧版问题：① 冷却剂换热 h_c 写死常数；② 壁温用 T_w=t_inlet+200 固定猜测，
        #           算出的 q 与该猜测不自洽（冷壁假设 → q 系统性偏高）。
        # 现在：① h_c 由超临界甲烷物性 + 通道几何经 Dittus-Boelter 算出；
        #       ② 喉部壁温内层迭代到自洽（Bartz σ 随热面壁温变 → h_g、q、T_wg 收敛）；
        #       ③ 外层用收敛壁温重算三区热载荷 → 冷却剂温升 → 更新喉部冷却剂温。
        # 冷却剂沿程逐站 marching 留待后续阶段；此处喉部点用平均冷却剂温近似。
        # ── V14：自动定通道深度命中目标流速（修 Major 1：避免非物理流速）──
        #   固定通道数+宽（受室周长/铣槽工艺约束），反算深度使 V≈V_target。
        chan_h = self.channel_height_m
        sizing_clamped = False
        if self.auto_size:
            _rho_size = ch4_props(self.t_inlet, self.p_inlet)[0]
            chan_h, sizing_clamped = resolve_channel_height(
                self.mdot_fuel, _rho_size, self.target_velocity_ms,
                self.channel_width_m, self.n_channels)
        A_flow = max(self.n_channels * self.channel_width_m * chan_h, 1e-9)
        D_h = (2.0 * self.channel_width_m * chan_h
               / max(self.channel_width_m + chan_h, 1e-9))
        L_channel = _channel_axial_length(r_t, self.contraction_ratio, self.l_star_m)
        t_wall = self.wall_thickness_m
        k_wall = self.wall_conductivity

        def _h_g_of(T_wg_local):
            s = _bartz_sigma(gamma, T_wg_local, T_c)
            h = ((0.026 / D_t ** 0.2) * (mu ** 0.2 * cp_g / Pr ** 0.6)
                 * (G_t ** 0.8) * (D_t / R_c) ** 0.1 * s)
            return h, s

        T_co = self.t_inlet + 50.0          # 喉部冷却剂温度初值
        T_wg = T_co + 350.0                 # 热面壁温初值
        h_c = _COOLANT_HTC                  # 兜底值（计算失败时回退）
        V_co = Re_co = Pr_co = Nu_co = 0.0
        sigma = 1.0
        h_g_t = 5.0e4
        q_peak_W_m2 = 0.0
        zones = {}
        Q_total = 0.0
        t_out = self.t_inlet
        delta_t = 0.0

        for _outer in range(8):
            # 冷却剂物性 @ 当前喉部冷却剂温度、通道压力（V15：CoolProp 真实物性优先）
            rho_co, mu_co, kth_co, cp_co = ch4_props(T_co, self.p_inlet)
            h_c, V_co, Re_co, Pr_co, Nu_co = _dittus_boelter_htc(
                self.mdot_fuel, rho_co, mu_co, kth_co, cp_co,
                A_flow, D_h, self.htc_enhancement)
            h_c = max(h_c, 1.0e3)

            # 内层：壁温自洽（h_g 随 T_wg 经 Bartz σ 变化；串联热阻求 q）
            for _inner in range(25):
                h_g_t, sigma = _h_g_of(T_wg)
                R_tot = 1.0 / max(h_g_t, 1.0) + t_wall / max(k_wall, 1e-6) + 1.0 / h_c
                q_peak_W_m2 = max((T_aw_throat - T_co) / max(R_tot, 1e-9), 0.0)
                T_wg_new = T_aw_throat - q_peak_W_m2 / max(h_g_t, 1.0)
                if abs(T_wg_new - T_wg) < 0.3:
                    T_wg = T_wg_new
                    break
                T_wg = 0.5 * T_wg + 0.5 * T_wg_new

            # 用收敛壁温重算三区热载荷 → 冷却剂总温升 → 更新喉部冷却剂温
            zones = _integrate_heat_load(
                h_g_t=h_g_t, r_t=r_t, CR=self.contraction_ratio,
                L_star=self.l_star_m, T_aw_t=T_aw_throat,
                T_aw_cc=T_aw_cc, T_w=T_wg, gamma=gamma,
                T_c=T_c, r_f=r_f,
                cover_eps=self.cover_eps,
                conv_half_angle_deg=self.conv_half_angle_deg,
            )
            Q_total = zones["Q_total_W"]
            T_cur = self.t_inlet
            Q_step = Q_total / 5.0
            for _ in range(5):
                T_cur += Q_step / (self.mdot_fuel * ch4_props(T_cur, self.p_inlet)[3])
            t_out = T_cur
            delta_t = t_out - self.t_inlet
            T_co_new = 0.5 * (self.t_inlet + t_out)   # 喉部点用平均冷却剂温（逐站 marching 见后续阶段）
            if abs(T_co_new - T_co) < 1.0:
                T_co = T_co_new
                break
            T_co = 0.6 * T_co + 0.4 * T_co_new

        q_throat_MW_m2 = q_peak_W_m2 / 1e6
        cp_avg = Q_total / (self.mdot_fuel * delta_t) if delta_t > 1e-6 else 3500.0

        A_total_wet = (zones["A_cyl_m2"] + zones["A_conv_m2"]
                       + zones["A_throat_band_m2"] + zones["A_noz_m2"])
        T_coolant_avg = T_co
        T_wall_cold   = T_co + q_peak_W_m2 / h_c
        T_wall_hot    = T_wg

        # ── V14：水力压降（Darcy + 局部损失），与通道几何/流速自洽（修 Major 1）──
        rho_co_final, mu_co_final, _, _ = ch4_props(T_co, self.p_inlet)
        dp_hydraulic, V_dp, Re_dp, f_dp = regen_pressure_drop(
            self.mdot_fuel, rho_co_final, mu_co_final, A_flow, D_h, L_channel,
            self.channel_rel_roughness, self.channel_k_minor)
        dp_used = dp_hydraulic if self.dp_auto else self.dp_pa
        dp_used = min(max(dp_used, 0.0), 0.9 * self.p_inlet)   # 物理保护：≤90% 入口压
        p_out = self.p_inlet - dp_used

        # 一致性诊断：固定压降模式下若水力实算与固定值差 >30%，提示用户对齐
        dp_mismatch = (not self.dp_auto and self.dp_pa > 1e3
                       and abs(dp_hydraulic - self.dp_pa) / self.dp_pa > 0.30)

        state = FlowState(
            name="fuel_after_cooling",
            pressure=p_out, temperature=t_out,
            mass_flow=self.mdot_fuel,
            enthalpy=cp_avg * t_out,
            composition={"CH4": 1.0},
        )

        notes = [
            "── Bartz 再生冷却模型（V9：算 h_c + 壁温自洽）──────────",
            f"  喉部几何：D_t={D_t*100:.1f} cm，R_c={R_c*100:.2f} cm，A_t={A_t*1e4:.1f} cm²",
            f"  气体属性：mu={mu:.2e} Pa·s，cp_g={cp_g:.0f} J/(kg·K)，Pr={Pr:.3f}",
            f"  G_t={G_t:.0f} kg/(m²·s)，sigma={sigma:.3f}",
            f"  h_g(喉部)={h_g_t:.0f} W/(m²·K)，q_throat={q_throat_MW_m2:.1f} MW/m²（自洽壁温下）",
            f"  T_aw(喉部)={T_aw_throat:.0f} K，T_aw(燃烧室)={T_aw_cc:.0f} K",
            "── 三区热载荷分布 ──────────────────────────────────",
            f"  燃烧室段  A={zones['A_cyl_m2']:.3f} m²  q={zones['q_cc_MW_m2']:.1f} MW/m²  Q={zones['Q_cc_W']/1e6:.2f} MW",
            f"  收缩段    A={zones['A_conv_m2']:.3f} m²  q={zones['q_conv_MW_m2']:.1f} MW/m²  Q={zones['Q_conv_W']/1e6:.2f} MW",
            f"  喉部带    A={zones['A_throat_band_m2']:.3f} m²  q={zones['q_throat_MW_m2']:.1f} MW/m²  Q={zones['Q_throat_W']/1e6:.2f} MW",
            f"  喷管头    A={zones['A_noz_m2']:.3f} m²  q={zones['q_noz_MW_m2']:.1f} MW/m²  Q={zones['Q_noz_W']/1e6:.2f} MW",
            (f"  喷管延伸  A={zones.get('A_noz_ext_m2', 0.0):.3f} m²  Q={zones.get('Q_noz_ext_W', 0.0)/1e6:.2f} MW"
             f"（V15.1 积到 ε={self.cover_eps:.0f}）" if zones.get("Q_noz_ext_W", 0.0) > 1e3
             else "  喷管延伸  （未启用，覆盖仅到喷管头 1.5 r_t）"),
            f"  总热载荷  Q_total = {Q_total/1e6:.2f} MW",
            "── 冷却剂侧（Dittus-Boelter，由通道几何+超临界CH4物性算出）──",
            f"  通道：N={self.n_channels}，{self.channel_width_m*1e3:.1f}×{chan_h*1e3:.2f} mm"
            + ("（深度按目标流速自动反算）" if self.auto_size else "（固定几何）")
            + (" ⚠深度触钳位[1–15mm]，实际流速偏离目标" if sizing_clamped else "")
            + f"，D_h={D_h*1e3:.2f} mm，A_flow={A_flow*1e4:.1f} cm²，L_ch≈{L_channel*1e3:.0f} mm",
            f"  冷却剂：V={V_co:.0f} m/s，Re={Re_co:.2e}，Pr={Pr_co:.2f} → "
            f"h_c={h_c:.3e} W/(m²·K)"
            + ("" if abs(self.htc_enhancement - 1.0) < 1e-6
               else f"（含弯曲强化×{self.htc_enhancement:.2f}）")
            + ("  ⚠流速 >200 m/s，偏高（增通道数/深度或换大通道）" if V_co > 200.0 else ""),
            f"  m_coolant={self.mdot_fuel:.1f} kg/s，cp_avg={cp_avg:.0f} J/(kg·K)",
            f"  dT_coolant = {delta_t:.1f} K  ({self.t_inlet:.0f} K -> {t_out:.0f} K)",
            (f"  压降 {dp_used/1e6:.2f} MPa（水力实算 Darcy，f={f_dp:.4f}）" if self.dp_auto
             else f"  压降 {dp_used/1e6:.2f} MPa（固定输入）；水力实算应为 {dp_hydraulic/1e6:.2f} MPa"
                  + ("  ⚠两者差 >30%，建议把 cooling_dp_pa 调到该实算值或置 cooling_dp_auto=1"
                     if dp_mismatch else " ✓与几何自洽")),
            "── 壁温（喉部最高点，三侧串联热阻自洽解）──────────────",
            f"  T_coolant(喉部)={T_coolant_avg:.0f} K",
            f"  T_wall_cold={T_wall_cold:.0f} K（冷却侧面）",
            f"  T_wall_hot ={T_wall_hot:.0f} K（燃气侧面，GRCop-84 热面）",
            ("  OK 壁温正常，低于 GRCop-84 使用上限 ~800 K"
             if T_wall_hot < 800 else
             "  !! 壁温偏高，须加强冷却（增通道数/减小通道高/降壁厚/提流速）"),
            "  注：h_c 现按通道几何+物性计算（非写死常数）；喉部冷却剂温取平均（逐站 marching 见后续阶段）。",
        ]

        result = ComponentResult(
            name="cooling_channel",
            outputs={
                "mode":                 1.0,
                "heat_load_W":          Q_total,
                "heat_load_MW":         Q_total / 1e6,
                "Q_cc_MW":              zones["Q_cc_W"]     / 1e6,
                "Q_conv_MW":            zones["Q_conv_W"]   / 1e6,
                "Q_throat_MW":          zones["Q_throat_W"] / 1e6,
                "Q_noz_MW":             zones["Q_noz_W"]    / 1e6,
                "Q_noz_ext_MW":         zones.get("Q_noz_ext_W", 0.0) / 1e6,
                "cover_eps":            self.cover_eps,
                "q_throat_MW_m2":       q_throat_MW_m2,
                "h_g_throat_W_m2_K":    h_g_t,
                "sigma":                sigma,
                "D_throat_m":           D_t,
                "r_throat_m":           r_t,
                "A_throat_m2":          A_t,
                "r_cc_m":               zones["r_cc_m"],
                "L_cc_m":               zones["L_cc_m"],
                "A_wetted_total_m2":    A_total_wet,
                "T_aw_throat_K":        T_aw_throat,
                "T_wall_cold_K":        T_wall_cold,
                "T_wall_hot_K":         T_wall_hot,
                "delta_T_K":            delta_t,
                "T_in_K":               self.t_inlet,
                "T_out_K":              t_out,
                "cp_avg_J_kg_K":        cp_avg,
                "P_in_Pa":              self.p_inlet,
                "P_out_Pa":             p_out,
                "delta_P_MPa":          dp_used / 1e6,
                "delta_P_hydraulic_MPa": dp_hydraulic / 1e6,
                "delta_P_source":       1.0 if self.dp_auto else 0.0,   # 1=水力实算 0=固定输入
                "channel_height_mm":    chan_h * 1000.0,
                "channel_length_m":     L_channel,
                "channel_friction_factor": f_dp,
                "G_throat_kg_m2_s":     G_t,
                "mu_Pa_s":              mu,
                "Pr":                   Pr,
                # ── A+B 新增：算出来的冷却剂侧换热 + 通道几何 ──
                "h_coolant_W_m2K":      h_c,
                "coolant_velocity_ms":  V_co,
                "coolant_Re":           Re_co,
                "coolant_Pr":           Pr_co,
                "channel_D_h_mm":       D_h * 1000.0,
                "channel_A_flow_cm2":   A_flow * 1e4,
                "n_channels":           float(self.n_channels),
                "T_coolant_throat_K":   T_coolant_avg,
                "liner_thickness_m":    t_wall,
                "wall_conductivity_WmK": k_wall,
            },
            notes=notes,
        )
        return state, result

    def evaluate(self):
        if self.bartz_mode:
            return self._evaluate_bartz()
        else:
            return self._evaluate_fixed()
