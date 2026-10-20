"""
cooling_marching.py — 再生冷却沿程逐站 marching 模型（V15）

把 cooling.py 的"喉部单点 + 分区积分"升级为完整的沿程一维 marching：

  推力室内轮廓（喷注面 → 圆筒段 → 收缩锥 → 喉部 → TOP 抛物线钟形喷管）离散成
  N 站，每站求解：
    · 燃气侧：局部面积比 → 局部马赫数 M(x) → 恢复温度 T_aw(x)
              → Bartz 局部换热 h_g(x) = h_g_t·(A_t/A)^0.9·σ(M, T_wg)
    · 膜冷却（可选）：T_aw_eff(x) = T_aw − η(x)·(T_aw − T_film)，
              η(x) = η₀·exp(−x/L_decay)（一阶工程模型，膜气源 = 富燃涡轮排气）
    · 壁面：1D 串联热阻 q = (T_aw_eff − T_co)/(1/h_g + t_w/k_w + 1/h_c)，
              σ 随热面壁温迭代自洽
    · 冷却剂侧：CoolProp 真实 CH4 物性 @ 局部 (T_co, p_co)，Dittus-Boelter h_c
              （喉部带 ±2 r_t 施加弯曲强化因子）
    · 能量/动量：dT_co = q·dA/(ṁ·cp)，dp = f·(ds/D_h)·½ρV²（fluids 库摩擦系数）

  冷却剂流向：counterflow（Raptor 同款）——入口在喷管末端集流环，向喷注面
  逆燃气方向流动；喉部遇到的是已被喷管段预热的冷却剂（比单点模型更真实）。

输出：T_wall_hot(x)、T_wall_cold(x)、q(x)、T_coolant(x)、p_coolant(x)、M(x)
完整剖面（挂在 ComponentResult.profile，标量进 outputs/summary），并与循环内
单点模型做一致性对照（Q/T_out/Δp 三个差值）。

定位：本模型在 **收敛设计点上后处理运行一次**（不进 Newton 迭代环），
循环闭合仍由 cooling.py 单点模型负责——保证求解器平滑稳定，剖面精度由
marching 提供，两者偏差作为单点模型的误差量化。

物理（V16 起逐步补全核心项）：
  · 燃气侧热流 = 对流（Bartz）+ 辐射。辐射 q_rad = ε_g·σ_SB·(T_g⁴ − T_wg⁴)，
    ε_g 按局部燃气静压标度（光学厚度 ∝ p·L 的一阶代理），燃烧室段占当地
    总热流 ~10–25%，喷管段随 p、T 快速衰减（Huzel & Huang §4.4.4）。
  · 冷却剂压降 = 摩擦（Darcy/Colebrook）+ 加速（动量）。超临界甲烷沿程密度
    坍塌 3–4×，加速项 dp = G²·Δ(1/ρ) 在高热流段可占总压降 10–20%。
  · 冷却侧 h_c：Dittus-Boelter × 肋片效率 × Jackson 变物性 × 喉部曲率强化。

简化（注明，后续阶段细化）：
  · 通道流通面积随局部半径变化：A_flow(x) = N·w_t·(r/r_t)·h —— 通道沿壁面
    周向等节距排布，宽度 ∝ 局部周长（输入的 channel_width 定义为喉部宽度）。
    这保证流速在喉部最高（换热最强处）、喷管大半径段自然降速（真实构型）。
  · 辐射用灰气体 ε_g（不做逐组分 H2O/CO2 带模型），不确定度量级 ~±50%。
  · 膜冷却为指数衰减效能模型，未反馈主流性能（膜流量 <3% 时 Isp 影响 <1 s）
  · 燃气物性（μ、cp、Pr）取燃烧室值沿程冻结（Bartz 标准做法）

参考：Bartz NACA TN-3997；Huzel & Huang NASA SP-125 §4.4；
      膜冷却效能：Grisson AFRPL / Sutton 9th §8.5（指数衰减一阶式）。
"""

from __future__ import annotations

from task_control import checkpoint

import math

from core import ComponentResult
from cooling import (
    ch4_props, _friction_factor, _bartz_gas_props, _throat_area,
)

_R_UNIVERSAL = 8314.462618
_SIGMA_SB = 5.670374419e-8       # Stefan-Boltzmann 常数 [W/(m²·K⁴)]


# ════════════════════════════════════════════════════════════════════
# 几何：推力室内轮廓（喷注面 x=0 → 喷管出口）
# ════════════════════════════════════════════════════════════════════

def _build_contour(r_c: float, r_t: float, r_e: float,
                   L_cyl: float, L_conv: float,
                   bell_fraction: float = 0.80,
                   conv_half_angle_deg: float = 30.0,
                   n_points: int = 300):
    """生成 (x[], r[], x_throat)，x=0 在喷注面。

    V19 口径统一：直接取 geometry.nozzle.full_hot_gas_contour（唯一规范轮廓）——
    圆筒 + 收缩段（入口倒角 R1 + 直锥 β + 喉部上游弧 Ru=1.5Rt）+ 喉部下游弧
    Rd=0.382Rt + Rao 拟合 θn/θe 的 TOP 钟形段，与几何估算页 / 2D·3D 图 / CSV
    导出完全同构。旧版此处自建的扩张段抛物线（**无 Rd 下游弧**、n_div=36）与
    几何模块不同构，是"热结构云图 vs CAD 导出扩张段坐标有差异"的根源，已删除。
    L_conv 参数保留以兼容旧签名，但收缩段长度由规范轮廓自身决定、不再使用。
    """
    from geometry.nozzle import full_hot_gas_contour
    eps_full = (r_e / max(r_t, 1e-9)) ** 2
    return full_hot_gas_contour(
        r_t, r_e, r_c, eps_full, L_cyl,
        bell_fraction=bell_fraction,
        theta_conv_deg=conv_half_angle_deg,
        n_points=n_points)


def _mach_from_area_ratio(AR: float, gamma: float, supersonic: bool) -> float:
    """等熵 A/A* → M（亚/超声速支二分）。"""
    if AR <= 1.0 + 1e-10:
        return 1.0
    lo, hi = (1.0001, 50.0) if supersonic else (1e-6, 0.9999)
    for _ in range(70):
        checkpoint()
        mid = 0.5 * (lo + hi)
        t = 1.0 + (gamma - 1.0) / 2.0 * mid * mid
        f = (1.0 / mid) * (2.0 / (gamma + 1.0) * t) ** ((gamma + 1.0) / (2.0 * (gamma - 1.0)))
        if supersonic:
            if f < AR:
                lo = mid
            else:
                hi = mid
        else:
            if f > AR:
                lo = mid
            else:
                hi = mid
    return 0.5 * (lo + hi)


def _bartz_sigma_local(gamma: float, T_w: float, T_c: float, M: float) -> float:
    """Bartz σ 修正（任意局部马赫数的完整形式）。"""
    half = 1.0 + (gamma - 1.0) / 2.0 * M * M
    inner = 0.5 * (T_w / T_c) * half + 0.5
    return max(0.20, min(inner ** (-0.68) * half ** (-0.12), 2.00))


# ════════════════════════════════════════════════════════════════════
# 主 marching 求解
# ════════════════════════════════════════════════════════════════════

def march_regen_circuit(
    *,
    # 冷却剂入口（集流环 = 燃料泵出口态）
    mdot_coolant: float, T_in: float, p_in: float,
    # 燃气/室参数
    mdot_total: float, pc: float, T_c: float, gamma: float, molar_mass: float,
    # 几何
    r_t: float, r_c: float, L_cyl: float, L_conv: float,
    expansion_ratio: float, cover_eps: float = 0.0,
    # 冷却通道（单段模式：N/深/衬厚沿程恒定，宽 ∝ 局部半径）
    n_channels: int = 300, channel_width_m: float = 1.5e-3,
    channel_height_m: float = 5.0e-3, wall_thickness_m: float = 0.5e-3,
    wall_conductivity: float = 320.0, htc_enhancement: float = 1.0,
    # 可选：内衬随温导热 k(T_wg) 回调（如 GRCop-84 手册回归式）；None 则用上面的常数
    k_wall_func=None,
    rel_roughness: float = 0.003, k_minor: float = 3.0,
    counterflow: bool = True,
    # ── V19：沿程通道分布断点表（chamber_design.ChannelRow 列表）──────────
    # None = 单段模式（与 V15 行为一致）；非空 = 每站由表解析 (N, w, h, t_liner)，
    # N 分段常数（断点即分叉/合并站），w/h/t 分段线性——真实 HARCC 变截面策略。
    channel_table=None,
    # 钟形长度比（轮廓构建用，V19 与几何模块同源）
    bell_fraction: float = 0.80,
    # 膜冷却
    film_fraction: float = 0.0, film_T_K: float = 850.0,
    film_eta0: float = 0.9, film_decay_length_m: float = 0.0,
    # 燃气辐射（H2O/CO2 高压灰气体辐射热流；0 关闭）
    gas_emissivity: float = 0.20,
    # 收缩段半锥角（喉部圆弧 Ru=1.5·Rt 的规范轮廓，与几何/部件结果同口径）
    conv_half_angle_deg: float = 30.0,
    # 校核
    wall_temp_limit_K: float = 800.0,
) -> dict:
    """沿程逐站 marching。返回剖面数组 + 标量摘要（键见函数末尾）。"""
    checkpoint()
    R_spec = _R_UNIVERSAL / max(molar_mass, 1e-9)
    mu_g, cp_g, Pr_g = _bartz_gas_props(gamma, R_spec, T_c)
    r_f = Pr_g ** (1.0 / 3.0)

    D_t = 2.0 * r_t
    A_t = math.pi * r_t * r_t
    R_curv = 0.35 * D_t
    G_t = mdot_total / A_t
    # Bartz 喉部基系数（不含 σ）
    h_g_base = ((0.026 / D_t ** 0.2) * (mu_g ** 0.2 * cp_g / Pr_g ** 0.6)
                * (G_t ** 0.8) * (D_t / R_curv) ** 0.1)

    eps_end = cover_eps if cover_eps and cover_eps > 1.0 else expansion_ratio
    r_e_full = r_t * math.sqrt(max(expansion_ratio, 1.001))
    xs, rs, x_t = _build_contour(r_c, r_t, r_e_full, L_cyl, L_conv,
                                 bell_fraction=bell_fraction,
                                 conv_half_angle_deg=conv_half_angle_deg)

    # 站点（段中点），按覆盖面积比截断
    stations = []
    for i in range(len(xs) - 1):
        checkpoint()
        x0, x1, r0, r1 = xs[i], xs[i + 1], rs[i], rs[i + 1]
        xm, rm = 0.5 * (x0 + x1), 0.5 * (r0 + r1)
        AR = (rm / r_t) ** 2
        if xm > x_t and AR > eps_end:
            break
        ds = math.hypot(x1 - x0, r1 - r0)
        stations.append({"x": xm, "r": rm, "ds": ds, "dA": 2.0 * math.pi * rm * ds,
                         "AR": AR, "supersonic": xm > x_t})
    n_st = len(stations)

    # ── V19：沿程通道分布解析（断点表 → 每站 N/w/h/t 数组）────────────────
    dist = None
    if channel_table:
        try:
            from chamber_design import resolve_channel_distribution
            # x_exit 用**全轮廓**末端（V19 审查修复）：'exit' 锚点语义是喷管出口，
            # 与工作区预览一致；若用 cover_eps 截断后的末站，覆盖比一变整张分布
            # 就被悄悄拉伸，marching 与 UI 展开视图口径分裂。
            dist = resolve_channel_distribution(
                channel_table,
                [st["x"] for st in stations], [st["r"] for st in stations],
                x_throat=x_t, L_cyl=L_cyl,
                x_exit=xs[-1] if xs else x_t)
        except Exception:
            dist = None   # 表解析失败回退单段模式（postpass 会另行告警）

    def _station_channel(i: int, st: dict):
        """站位通道几何：分布模式查表；单段模式沿用 V15 规则
        （N/深/衬厚恒定，宽 ∝ 局部半径、下限 0.3×喉部宽）。"""
        if dist is not None:
            n_loc = max(int(dist["n"][i]), 1)
            w_loc = max(float(dist["w_m"][i]), 1.0e-4)
            h_loc = max(float(dist["h_m"][i]), 3.0e-4)
            t_loc = max(float(dist["t_m"][i]), 2.0e-4)
        else:
            n_loc = max(int(n_channels), 1)
            w_loc = channel_width_m * max(st["r"] / r_t, 0.3)
            h_loc = channel_height_m
            t_loc = wall_thickness_m
        A = max(n_loc * w_loc * h_loc, 1e-9)
        Dh = 2.0 * w_loc * h_loc / max(w_loc + h_loc, 1e-9)
        pitch = 2.0 * math.pi * st["r"] / n_loc
        rib = max(pitch - w_loc, 0.2e-3)
        return n_loc, w_loc, h_loc, t_loc, A, Dh, pitch, rib

    # 喉部流通截面（分布模式取喉部站实际值；单段模式=输入标量乘积）
    if dist is not None and stations:
        _i_thr = min(range(n_st), key=lambda i: stations[i]["AR"])
        _n0, _w0, _h0, _t0, A_flow_t, _dh0, _p0, _r0 = _station_channel(_i_thr, stations[_i_thr])
        t_liner_throat = _t0
    else:
        A_flow_t = max(n_channels * channel_width_m * channel_height_m, 1e-9)
        t_liner_throat = wall_thickness_m

    # ── 膜冷却（V15.1，对抗校核 major 修复）────────────────────────────
    # 旧版 η(x)=η0·exp(−x/L_decay) 与膜流量完全无关（fraction 纯开关，1% 与 10%
    # 结果逐位相同）。现改 Grisson 型流量标度：绝热气膜的效能衰减指数
    #   ξ(x) = ∫ h_g·P dx / (ṁ_film·cp_film)，η = η0·exp(−ξ)
    # ——膜流量越大 ξ 增长越慢、保护长度越长，吹风比→0 时 η→0，标度正确。
    # film_decay_length_m>0 时退回固定衰减长的手动模式（标定用）。
    # 注意：高压气-气膜效能数据稀缺，本模型不确定度 ~±2×，notes 已标注。
    mdot_film = film_fraction * mdot_coolant
    film_on = mdot_film > 1e-6
    cp_film = 3000.0          # 富燃涡轮排气比热量级 [J/(kg·K)]
    use_fixed_decay = film_decay_length_m > 1e-6
    xi = 0.0

    # 燃气侧预计算：M、T_aw、局部静温/静压（辐射源 + 燃气侧压力）、T_aw_eff
    for st in stations:
        checkpoint()
        M = _mach_from_area_ratio(st["AR"], gamma, st["supersonic"])
        half = (gamma - 1.0) / 2.0 * M * M
        t_ratio = 1.0 + half
        T_aw = T_c * (1.0 + r_f * half) / t_ratio
        st["M"] = M
        st["T_aw"] = T_aw
        st["T_static"] = T_c / t_ratio                         # 局部燃气静温（辐射用）
        st["p_gas"] = pc * t_ratio ** (-gamma / (gamma - 1.0))  # 局部燃气静压（衬里压差用）
        if film_on:
            if use_fixed_decay:
                eta = film_eta0 * math.exp(-st["x"] / film_decay_length_m)
            else:
                sigma_f = _bartz_sigma_local(gamma, max(film_T_K, 400.0), T_c, M)
                h_g_f = h_g_base * (1.0 / st["AR"]) ** 0.9 * sigma_f
                xi += h_g_f * (2.0 * math.pi * st["r"] * st["ds"]) / (mdot_film * cp_film)
                eta = film_eta0 * math.exp(-xi)
            st["T_aw_eff"] = T_aw - eta * (T_aw - film_T_K)
            st["film_eta"] = eta
        else:
            st["T_aw_eff"] = T_aw
            st["film_eta"] = 0.0

    # 冷却剂 marching：counterflow 从喷管末端站向喷注面推进
    order = range(n_st - 1, -1, -1) if counterflow else range(n_st)
    T_co, p_co = T_in, p_in
    Q_total = 0.0
    Q_rad_total = 0.0
    dp_fric_total = 0.0
    dp_accel_total = 0.0
    rho_prev = None          # 上一站冷却剂密度，供加速压降 dp=G²·Δ(1/ρ)
    floor_hit = False
    throat_state = None      # 缓存喉部站（min AR）冷却剂态，供 dp_minor 用
    min_ar_seen = 1e9
    for idx in order:
        checkpoint()
        st = stations[idx]
        rho, mu, k_co, cp_co = ch4_props(T_co, p_co)
        n_loc, w_loc, h_loc, t_loc, A_flow, D_h, pitch, t_rib = _station_channel(idx, st)
        V = mdot_coolant / max(rho * A_flow, 1e-9)
        Re = rho * V * D_h / max(mu, 1e-12)
        Pr = mu * cp_co / max(k_co, 1e-12)
        # V15.1：曲率强化窗口收紧到 ±1·r_t（旧 ±2·r_t 覆盖了大半收缩段）
        enh = htc_enhancement if abs(st["x"] - x_t) < 1.0 * r_t else 1.0
        Nu = 0.023 * Re ** 0.8 * Pr ** 0.4
        h_c_db = max(Nu * k_co / max(D_h, 1e-9), 1.0e3)

        # V15.1（对抗校核 major 修复）：冷侧 h_c 两项修正——
        #   肋片效应（侧壁肋为扩展换热面，h_c↑，保守侧）×
        #   Jackson 变物性（强加热近壁密度坍塌使 D-B 高估，h_c↓，非保守侧）。
        # 两者随壁温迭代联立自洽；净因子典型 0.9–1.4。
        # V19：肋宽/节距/通道深来自 _station_channel（分布模式为真实局部值）；
        # 肋片效率移入下方壁温迭代内计算——与穿壁热阻共用同一 k(T) 源（审查修复：
        # 旧版肋片恒用常数 wall_conductivity，与穿壁 k_wall_func(T_wg) 不同源）。
        F_fin = 1.0

        # 壁温自洽：燃气侧热流 = 对流(Bartz) + 辐射，经穿壁+冷却侧串联导出。
        #   q_conv = h_g·(T_aw_eff − T_wg)；q_rad = ε_loc·σ_SB·(T_g⁴ − T_wg⁴)
        #   (T_wg − T_co)/R_wc = q_conv + q_rad，R_wc = t_w/k_w + 1/h_c
        # ε_loc 按局部燃气静压标度（光学厚度 ∝ p·L 的一阶代理）：喷管段压力骤降
        # → 辐射快速衰减，与 T⁴ 衰减叠加，物理趋势正确。
        T_aw_eff = st["T_aw_eff"]
        T_g_rad = st["T_static"]
        eps_loc = gas_emissivity * max(min(st["p_gas"] / max(pc, 1.0), 1.0), 0.0)
        T_wg = max(T_co + 300.0, 0.5 * (T_aw_eff + T_co))
        T_wc = T_co + 200.0
        q = q_conv = q_rad = 0.0
        h_g = h_g_base
        h_c = h_c_db * F_fin * enh
        for _ in range(20):
            # 内衬/肋导热：有随温回调则按当前热壁温 T_wg 取 k(T)（材料手册/铜族缩放），
            # 否则用常数；肋片效率与穿壁热阻**同一 k 源**、随壁温迭代自洽（V19）。
            checkpoint()
            k_wall = k_wall_func(T_wg) if k_wall_func is not None else wall_conductivity
            m_fin = math.sqrt(2.0 * h_c_db / max(k_wall * t_rib, 1e-12))
            mH = m_fin * h_loc
            eta_fin = math.tanh(mH) / max(mH, 1e-9)
            F_fin = min((w_loc + 2.0 * eta_fin * h_loc) / max(pitch, 1e-9), 2.5)
            # Jackson 型变物性因子 (ρ_w/ρ_b)^0.3；>620 K 超 CoolProp 域用理想气延拓
            T_w_props = min(T_wc, 620.0)
            rho_w = ch4_props(T_w_props, p_co)[0] * (T_w_props / max(T_wc, T_w_props))
            F_prop = min(max((rho_w / max(rho, 1e-9)) ** 0.3, 0.40), 1.0)
            h_c = max(h_c_db * F_fin * F_prop * enh, 1.0e3)

            sigma = _bartz_sigma_local(gamma, T_wg, T_c, st["M"])
            h_g = h_g_base * (1.0 / st["AR"]) ** 0.9 * sigma
            R_wc = t_loc / max(k_wall, 1e-6) + 1.0 / h_c
            q_conv = max(h_g * (T_aw_eff - T_wg), 0.0)
            q_rad = max(eps_loc * _SIGMA_SB * (T_g_rad ** 4 - T_wg ** 4), 0.0)
            q = q_conv + q_rad
            T_wg_new = T_co + q * R_wc
            T_wc = T_co + q / max(h_c, 1.0)
            if abs(T_wg_new - T_wg) < 0.3:
                T_wg = T_wg_new
                break
            T_wg = 0.5 * T_wg + 0.5 * T_wg_new

        dQ = q * st["dA"]
        Q_total += dQ
        Q_rad_total += q_rad * st["dA"]
        # 压降 = 摩擦 + 加速（动量）。加速项 dp = G²·(1/ρ − 1/ρ_prev)，
        # 超临界甲烷沿程密度坍塌 → 提速 → 不可逆动量损失。
        f = _friction_factor(Re, rel_roughness)
        dp_f = f * (st["ds"] / max(D_h, 1e-9)) * 0.5 * rho * V * V
        G_loc = mdot_coolant / max(A_flow, 1e-9)
        if rho_prev is None:
            dp_a = 0.0
        else:
            dp_a = max(G_loc * G_loc * (1.0 / max(rho, 1e-9) - 1.0 / max(rho_prev, 1e-9)), 0.0)
        rho_prev = rho
        dp = dp_f + dp_a
        dp_fric_total += dp_f
        dp_accel_total += dp_a

        st.update({"q": q, "q_conv": q_conv, "q_rad": q_rad,
                   "h_g": h_g, "h_c": h_c, "T_wg": T_wg,
                   "T_wc": T_wc, "T_co": T_co, "p_co": p_co,
                   "p_gas": st["p_gas"], "w_chan": w_loc,
                   "n_chan": n_loc, "h_chan": h_loc, "t_liner": t_loc,
                   "pitch": pitch, "rib": t_rib,
                   "V_co": V, "Re": Re, "rho_co": rho,
                   "F_fin": F_fin})
        if st["AR"] < min_ar_seen:
            min_ar_seen = st["AR"]
            throat_state = (rho, V)

        T_co = T_co + dQ / max(mdot_coolant * cp_co, 1e-9)
        p_new = p_co - dp
        if p_new < 0.05 * p_in:
            floor_hit = True
            p_new = 0.05 * p_in
        p_co = p_new

    # 局部损失（进出口集流环+弯头）：用喉部站真实冷却剂态（V15.1 修复——旧版
    # 误用出口端低密度态配喉部流通面积，混合了一个不存在的状态，高估 ~25%）
    if throat_state is not None:
        rho_thr, V_thr = throat_state
    else:
        rho_thr, _, _, _ = ch4_props(T_co, p_co)
        V_thr = mdot_coolant / max(rho_thr * A_flow_t, 1e-9)
    dp_minor = k_minor * 0.5 * rho_thr * V_thr * V_thr

    # V15.1（对抗校核 major 修复）：Δp 簿记一致化——delta_P 报"需求压降"
    # （无钳位累计），p_out 报钳位后可用值；两者不符即水力不可行。
    # V16：需求压降含加速（动量）项。
    dp_demand = dp_fric_total + dp_accel_total + dp_minor
    p_out = max(p_in - dp_demand, 0.05 * p_in)
    T_out = T_co
    dp_total = dp_demand
    V_max = max((s.get("V_co", 0.0) for s in stations), default=0.0)
    hydraulic_infeasible = bool(floor_hit or dp_demand > 0.9 * p_in or V_max > 300.0)

    # 摘要标量
    i_max = max(range(n_st), key=lambda i: stations[i]["T_wg"]) if n_st else 0
    q_max_i = max(range(n_st), key=lambda i: stations[i]["q"]) if n_st else 0
    T_wg_max = stations[i_max]["T_wg"] if n_st else 0.0
    T_wc_max = max((st["T_wc"] for st in stations), default=0.0)
    over = [st for st in stations if st["T_wg"] > wall_temp_limit_K]

    return {
        "stations": stations,
        "x_throat_m": x_t,
        "n_stations": n_st,
        "Q_total_W": Q_total,
        "Q_rad_W": Q_rad_total,
        "Q_rad_fraction": (Q_rad_total / Q_total) if Q_total > 1.0 else 0.0,
        "T_out_K": T_out,
        "p_out_Pa": p_out,
        "dp_total_Pa": dp_total,
        "dp_friction_Pa": dp_fric_total,
        "dp_accel_Pa": dp_accel_total,
        "dp_minor_Pa": dp_minor,
        "T_wall_hot_max_K": T_wg_max,
        "T_wall_cold_max_K": T_wc_max,
        "x_T_wall_max_m": stations[i_max]["x"] if n_st else 0.0,
        "q_max_MW_m2": stations[q_max_i]["q"] / 1e6 if n_st else 0.0,
        "x_q_max_m": stations[q_max_i]["x"] if n_st else 0.0,
        "wall_over_limit_fraction": len(over) / max(n_st, 1),
        "wall_temp_limit_K": wall_temp_limit_K,
        "cover_eps": eps_end,
        "film_on": film_on,
        "film_mdot_kgs": mdot_film,
        "A_flow_throat_m2": A_flow_t,
        "pressure_floor_hit": floor_hit,
        "hydraulic_infeasible": hydraulic_infeasible,
        "V_coolant_max_ms": V_max,
        "dist_mode": dist is not None,
        "t_liner_throat_m": t_liner_throat,
    }


# ════════════════════════════════════════════════════════════════════
# 循环后处理入口：从收敛 CycleResults 取参数，跑一次并注入结果
# ════════════════════════════════════════════════════════════════════

def run_marching_postpass(results, inputs) -> None:
    """在收敛设计点上运行沿程 marching，注入 results.components['cooling_marching']。
    任何失败只写 warning，不影响主结果。"""
    checkpoint()
    try:
        comps = results.components
        s = results.summary
        cool = comps["cooling_channel"].outputs
        ch = comps["main_chamber"].outputs
        noz = comps["nozzle"].outputs
        fuel_pump_out = results.states.get("fuel_after_pump")
        if fuel_pump_out is None:
            return

        At = float(noz.get("At_m2") or 0.0)
        if At <= 0.0:
            return
        r_t = math.sqrt(At / math.pi)
        r_c = float(ch.get("cc_diameter_m", 2.0 * r_t * 1.6)) / 2.0
        L_cyl = float(ch.get("cc_length_m", 0.3))
        L_conv = float(ch.get("cc_convergent_length_mm", 130.0)) / 1000.0
        gamma = float(s.get("chamber_gamma", 1.19))
        MW = float(s.get("chamber_molar_mass", 22.6))
        T_c = float(s.get("chamber_temperature_K", 3600.0))
        # V20：GG（开式循环）下主室通流/冷却剂流量 ≠ 发动机总流量——
        # 优先读专用键（GG 模型提供），缺省回退到 FFSC 口径（二者相等）。
        mdot_total = float(s.get("mdot_chamber_kg_s") or s.get("mdot_total_kg_s", 1.0))
        mdot_fuel = float(s.get("mdot_coolant_kg_s") or s.get("mdot_fuel_kg_s", 1.0))

        # 膜冷却气源：富燃涡轮排气（FFSC 气-气膜），温度取富燃预燃室涡轮排气温
        film_T = float(s.get("fuel_turbine_exhaust_T_K", 850.0) or 850.0)

        # 内衬随温导热（V19 口径统一）：材料存在即挂 k(T_wg) 回调——grcop84 走手册
        # 回归式，其它铜合金走 materials 里的通用缩放（旧版只有 temp_prop_model
        # 非空才挂，导致 GRCop-42/CuCrZr 等永远用常数 320）。
        _k_wall_func = None
        try:
            from materials import MATERIALS
            _imat = MATERIALS.get(getattr(inputs, "chamber_inner_wall_material", "grcop84"))
            if _imat is not None:
                _k_wall_func = _imat.thermal_conductivity_at
        except Exception:
            _k_wall_func = None

        # V19：冷却通道沿程分布断点表（JSON → ChannelRow 列表；失败回退单段+告警）
        _channel_rows = None
        _tbl_json = str(getattr(inputs, "cooling_channel_table_json", "") or "").strip()
        if _tbl_json:
            try:
                from chamber_design import parse_channel_table
                _channel_rows = parse_channel_table(_tbl_json)
            except Exception as _tbl_exc:
                results.warnings.append(
                    f"⚠ 冷却通道分布表解析失败（marching 回退单段模式）：{_tbl_exc}")

        prof = march_regen_circuit(
            mdot_coolant=mdot_fuel,
            T_in=fuel_pump_out.temperature, p_in=fuel_pump_out.pressure,
            mdot_total=mdot_total, pc=float(inputs.pc), T_c=T_c,
            gamma=gamma, molar_mass=MW,
            r_t=r_t, r_c=r_c, L_cyl=L_cyl, L_conv=L_conv,
            conv_half_angle_deg=float(getattr(inputs, "chamber_conv_half_angle_deg", 30.0)),
            expansion_ratio=float(inputs.expansion_ratio),
            cover_eps=float(getattr(inputs, "cooling_cover_eps", 0.0) or 0.0),
            # V19 审查修复：N 直接读 inputs（单点模型的 N 本就来自同一输入，正常流程
            # 数值相同；但「冻结边界重算沿程」时用户在工作区改的 N 必须生效——旧版
            # 优先读冻结的单点输出，改 N 无效）。通道深：auto_size 开 → 用单点模型
            # 解出的深度（物理自洽）；关 → 用用户输入值（工作区手动设计生效）。
            n_channels=int(getattr(inputs, "cooling_n_channels", 0)
                           or cool.get("n_channels", 300)),
            channel_width_m=float(getattr(inputs, "cooling_channel_width_m", 1.5e-3)),
            channel_height_m=(float(cool.get("channel_height_mm", 5.0)) / 1000.0
                              if bool(getattr(inputs, "cooling_channel_auto_size", 1.0))
                              else float(getattr(inputs, "cooling_channel_height_m", 5.0e-3))),
            wall_thickness_m=float(getattr(inputs, "cooling_wall_thickness_m", 0.5e-3)),
            wall_conductivity=float(getattr(inputs, "cooling_wall_conductivity", 320.0)),
            k_wall_func=_k_wall_func,
            htc_enhancement=float(getattr(inputs, "cooling_htc_enhancement", 1.0)),
            rel_roughness=float(getattr(inputs, "cooling_channel_rel_roughness", 0.003)),
            k_minor=float(getattr(inputs, "cooling_channel_k_minor", 3.0)),
            counterflow=True,
            film_fraction=float(getattr(inputs, "film_cooling_fraction", 0.0)),
            film_T_K=film_T,
            film_eta0=float(getattr(inputs, "film_effectiveness_initial", 0.9)),
            film_decay_length_m=float(getattr(inputs, "film_decay_length_m", 0.0)),
            gas_emissivity=float(getattr(inputs, "cooling_gas_emissivity", 0.20)),
            wall_temp_limit_K=float(getattr(inputs, "cooling_wall_temp_limit_K", 800.0)),
            channel_table=_channel_rows,
            bell_fraction=float(getattr(inputs, "nozzle_bell_fraction", 0.80)),
        )

        # 与循环内单点模型的一致性对照
        Q_point = float(cool.get("heat_load_W", 0.0))
        T_out_point = float(cool.get("T_out_K", 0.0))
        dp_point = float(cool.get("delta_P_MPa", 0.0)) * 1e6
        dQ_rel = (prof["Q_total_W"] - Q_point) / max(Q_point, 1.0)

        sts = prof["stations"]
        outputs = {
            "n_stations": float(prof["n_stations"]),
            "Q_total_MW": prof["Q_total_W"] / 1e6,
            "Q_radiation_MW": prof["Q_rad_W"] / 1e6,
            "Q_radiation_fraction": prof["Q_rad_fraction"],
            "T_coolant_out_K": prof["T_out_K"],
            "delta_P_MPa": prof["dp_total_Pa"] / 1e6,
            "delta_P_friction_MPa": prof["dp_friction_Pa"] / 1e6,
            "delta_P_accel_MPa": prof["dp_accel_Pa"] / 1e6,
            "delta_P_minor_MPa": prof["dp_minor_Pa"] / 1e6,
            "T_wall_hot_max_K": prof["T_wall_hot_max_K"],
            "x_T_wall_max_mm": prof["x_T_wall_max_m"] * 1000.0,
            "x_throat_mm": prof["x_throat_m"] * 1000.0,
            "q_max_MW_m2": prof["q_max_MW_m2"],
            "x_q_max_mm": prof["x_q_max_m"] * 1000.0,
            "wall_over_limit_fraction": prof["wall_over_limit_fraction"],
            "wall_temp_limit_K": prof["wall_temp_limit_K"],
            "cover_eps": prof["cover_eps"],
            "film_cooling_on": 1.0 if prof["film_on"] else 0.0,
            "film_cooling_fraction": float(getattr(inputs, "film_cooling_fraction", 0.0)),
            # 与单点模型（循环闭合用）的偏差量化
            "vs_point_Q_rel_percent": 100.0 * dQ_rel,
            "vs_point_T_out_delta_K": prof["T_out_K"] - T_out_point,
            "vs_point_dp_delta_MPa": (prof["dp_total_Pa"] - dp_point) / 1e6,
            "V_coolant_max_ms": prof["V_coolant_max_ms"],
            "T_wall_cold_max_K": prof["T_wall_cold_max_K"],
            "hydraulic_infeasible": 1.0 if prof["hydraulic_infeasible"] else 0.0,
            "film_mdot_kgs": prof["film_mdot_kgs"],
            "dist_mode": 1.0 if prof.get("dist_mode") else 0.0,
        }

        limit = prof["wall_temp_limit_K"]
        notes = [
            "── 沿程逐站 marching（V15，后处理于收敛设计点；counterflow）──",
            f"  站数 {prof['n_stations']}，覆盖至 ε={prof['cover_eps']:.0f}，"
            f"喉部位置 x={prof['x_throat_m']*1e3:.0f} mm",
            f"  Q_total={prof['Q_total_W']/1e6:.1f} MW（单点模型 {Q_point/1e6:.1f} MW，"
            f"差 {100.0*dQ_rel:+.0f}%）",
            f"  冷却剂 {fuel_pump_out.temperature:.0f}→{prof['T_out_K']:.0f} K；"
            f"Δp={prof['dp_total_Pa']/1e6:.1f} MPa（摩擦 {prof['dp_friction_Pa']/1e6:.1f} + "
            f"加速 {prof['dp_accel_Pa']/1e6:.1f} + 局部 {prof['dp_minor_Pa']/1e6:.1f}）",
            f"  辐射热流占比 {100.0*prof['Q_rad_fraction']:.0f}%（Q_rad={prof['Q_rad_W']/1e6:.1f} MW，"
            f"ε_g={float(getattr(inputs,'cooling_gas_emissivity',0.20)):.2f}，燃烧室段最强、喷管段衰减）",
            f"  壁温峰值 T_wg={prof['T_wall_hot_max_K']:.0f} K @ x={prof['x_T_wall_max_m']*1e3:.0f} mm"
            f"（喉部 {prof['x_throat_m']*1e3:.0f} mm 附近），"
            f"热流峰值 q={prof['q_max_MW_m2']:.0f} MW/m²",
            (f"  ✓ 全程壁温低于限值 {limit:.0f} K" if prof["wall_over_limit_fraction"] < 1e-9
             else f"  ⚠ {100.0*prof['wall_over_limit_fraction']:.0f}% 站点壁温超限值 {limit:.0f} K"
                  "（需提高流速/强化因子/减小壁厚/开膜冷却）"),
        ]
        if prof.get("hydraulic_infeasible"):
            notes.append(
                f"  ⚠⚠ 水力不可行：需求压降 {prof['dp_total_Pa']/1e6:.1f} MPa vs 入口 "
                f"{fuel_pump_out.pressure/1e6:.1f} MPa，V_max={prof['V_coolant_max_ms']:.0f} m/s"
                "——本剖面的 T_wg/h_c 数值无效（增大通道截面/通道数或降目标流速）")
        # 冷面壁温（甲烷结焦/裂解校核，铜壁阈值 ~950 K）
        _T_wc_max = prof["T_wall_cold_max_K"]
        notes.append(
            f"  冷面壁温峰值 T_wc={_T_wc_max:.0f} K"
            + ("  ⚠ 超甲烷结焦阈值 ~950 K（积碳→换热恶化正反馈，需降壁温或提流速）"
               if _T_wc_max > 950.0 else "  ✓ 低于甲烷结焦阈值 ~950 K"))
        # 通道周向可行性：N×喉部宽 + 最小肋宽 ≤ 喉部周长
        # V19：分布模式下用喉部站的真实 (N, w)；单段模式沿用输入标量。
        _circ_t = 2.0 * math.pi * r_t
        if prof.get("dist_mode") and sts:
            _i_thr = min(range(len(sts)), key=lambda i: sts[i]["AR"])
            _n_thr = int(sts[_i_thr].get("n_chan", 300) or 300)
            _w_thr = float(sts[_i_thr].get("w_chan", 1.5e-3) or 1.5e-3)
        else:
            _n_thr = int(cool.get("n_channels", 300))
            _w_thr = float(getattr(inputs, "cooling_channel_width_m", 1.5e-3))
        _w_need = _n_thr * (_w_thr + 0.2e-3)
        if _w_need > _circ_t:
            notes.append(
                f"  ⚠ 通道周向排布不可行：N×(宽+最小肋 0.2mm)={_w_need*1e3:.0f} mm > "
                f"喉部周长 {_circ_t*1e3:.0f} mm，需减少通道数或减小宽度")
        if prof.get("dist_mode"):
            notes.append("  通道几何：沿程分布表模式（N/宽/深/衬厚 逐站变化，见推力室设计工作区）")
        if prof["film_on"]:
            notes.append(
                f"  膜冷却开启：f={100.0*float(getattr(inputs,'film_cooling_fraction',0.0)):.1f}% 燃料，"
                f"T_film={film_T:.0f} K，η₀={float(getattr(inputs,'film_effectiveness_initial',0.9)):.2f}"
                f"（指数衰减，未反馈主流性能）")
        # 沿程关键站简表（5 个代表点）
        if sts:
            picks = sorted({0, len(sts)//4, len(sts)//2, 3*len(sts)//4, len(sts)-1})
            notes.append("  x[mm]   A/At   M     q[MW/m²]  T_wg[K]  T_co[K]  p_co[MPa]")
            for i in picks:
                checkpoint()
                st = sts[i]
                notes.append(
                    f"  {st['x']*1e3:6.0f} {st['AR']:6.2f} {st['M']:5.2f} "
                    f"{st['q']/1e6:8.1f} {st['T_wg']:8.0f} {st['T_co']:8.0f} {st['p_co']/1e6:8.1f}")
        notes.append("  注：剖面数组挂在 result.profile（x/r/M/q/T_wg/T_wc/T_co/p_co），可导出绘图。")

        comp = ComponentResult(name="cooling_marching", outputs=outputs, notes=notes)
        comp.profile = {
            "x_m":    [st["x"] for st in sts],
            "r_m":    [st["r"] for st in sts],
            "AR":     [st["AR"] for st in sts],
            "M":      [st["M"] for st in sts],
            "T_aw_K": [st["T_aw"] for st in sts],
            "q_W_m2": [st["q"] for st in sts],
            "q_conv_W_m2": [st.get("q_conv", st["q"]) for st in sts],
            "q_rad_W_m2":  [st.get("q_rad", 0.0) for st in sts],
            "T_wg_K": [st["T_wg"] for st in sts],
            "T_wc_K": [st["T_wc"] for st in sts],
            "T_co_K": [st["T_co"] for st in sts],
            "p_co_Pa": [st["p_co"] for st in sts],
            "p_gas_Pa": [st.get("p_gas", 0.0) for st in sts],
            "h_g_W_m2K": [st["h_g"] for st in sts],
            "h_c_W_m2K": [st["h_c"] for st in sts],
            "V_co_ms":  [st["V_co"] for st in sts],
            "rho_co":   [st["rho_co"] for st in sts],
            "Re_co":    [st["Re"] for st in sts],
            "w_chan_m": [st.get("w_chan", 0.0) for st in sts],
            # V19：沿程通道分布数组（热结构/任意站 FEM/穿壁截面图 消费）
            "n_chan":       [st.get("n_chan", 0.0) for st in sts],
            "h_chan_m":     [st.get("h_chan", 0.0) for st in sts],
            "t_liner_arr_m":[st.get("t_liner", 0.0) for st in sts],
            "pitch_m":      [st.get("pitch", 0.0) for st in sts],
            "rib_m":        [st.get("rib", 0.0) for st in sts],
            "dist_mode": bool(prof.get("dist_mode")),
            "film_eta": [st["film_eta"] for st in sts],
            "x_throat_m": prof["x_throat_m"],
            "t_liner_m": float(prof.get("t_liner_throat_m",
                               getattr(inputs, "cooling_wall_thickness_m", 0.5e-3))),
        }
        results.components["cooling_marching"] = comp

        s["cooling_marching_Twall_max_K"] = prof["T_wall_hot_max_K"]
        s["cooling_marching_Q_MW"] = prof["Q_total_W"] / 1e6
        s["cooling_marching_dp_MPa"] = prof["dp_total_Pa"] / 1e6
        s["cooling_marching_infeasible"] = 1.0 if prof["hydraulic_infeasible"] else 0.0
        # V15.1：关键校核全部进 results.warnings（机器可读路径，sweep/GUI 同源）
        if prof["hydraulic_infeasible"]:
            results.warnings.append(
                f"⚠⚠ 再生冷却 marching：通道水力不可行（需求压降 "
                f"{prof['dp_total_Pa']/1e6:.1f} MPa vs 入口 {fuel_pump_out.pressure/1e6:.1f} MPa，"
                f"V_max={prof['V_coolant_max_ms']:.0f} m/s），本剖面壁温数值无效。")
        elif prof["wall_over_limit_fraction"] > 1e-9:
            results.warnings.append(
                f"⚠ 再生冷却 marching：{100.0*prof['wall_over_limit_fraction']:.0f}% 站点热面壁温"
                f"超限（峰值 {prof['T_wall_hot_max_K']:.0f} K > {limit:.0f} K @ "
                f"x={prof['x_T_wall_max_m']*1e3:.0f} mm）。")
        if (not prof["hydraulic_infeasible"]) and prof["T_wall_cold_max_K"] > 950.0:
            results.warnings.append(
                f"⚠ 再生冷却 marching：冷面壁温峰值 {prof['T_wall_cold_max_K']:.0f} K "
                "超甲烷结焦阈值 ~950 K（积碳风险）。")
    except Exception as exc:   # 后处理失败不影响主结果
        try:
            results.warnings.append(f"⚠ 冷却 marching 后处理失败：{exc}")
        except Exception:
            pass
