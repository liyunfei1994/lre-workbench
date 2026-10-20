"""
geometry/nozzle.py

喷管几何：TOP（Thrust-Optimized Parabola，推力优化抛物线）钟形喷管轮廓
+ 喉部/出口尺寸。

TOP 是现役大多数液体火箭发动机（SpaceX Raptor、Merlin，SSME/RS-25，
Vulcain，RD-180 …）采用的标准喷管型面：它用一条**二次抛物线**逼近 Rao 用
特征线法（MOC）求得的最优推力型面（TOC），精度接近 TOC 而构造简单。

构造（Sutton《Rocket Propulsion Elements》Fig.3-14；Huzel & Huang SP-125）：
  · 喉部上游圆弧 Ru = 1.5·Rt，下游圆弧 Rd = 0.382·Rt；
  · 下游圆弧从喉部转到初始抛物线角 θn 的切点 N；
  · 从 N 到出口 E 用一条**抛物线**（二次 Bézier）连接，控制点 Q = 过 N 的
    θn 切线与过 E 的 θe 切线之交点；
  · 长度 = 钟形长度比 Lf ×（15° 锥形喷管等效长度），Lf=0.80 即 80% bell；
  · θn、θe 由膨胀比 ε 与 Lf 按 Rao 设计图拟合。

理论依据：G.V.R. Rao, "Exhaust Nozzle Contour for Optimum Thrust",
Jet Propulsion, 1958；Rao, "Approximation of Optimum Thrust Nozzle Contour",
ARS Journal, 1960（抛物线近似法本体）。
"""

from __future__ import annotations

import math
from typing import List, Tuple

from .types import NozzleGeometry


def top_bell_angles(expansion_ratio: float, bell_fraction: float = 0.80) -> Tuple[float, float]:
    """TOP 抛物线钟形喷管的喉后初始角 θn 与出口角 θe [deg]。

    拟合 Rao 设计图 θn/θe = f(ε, 长度比 Lf)（Sutton RPE Fig.3-12、Huzel&Huang）。
    基准为 80% bell；更短的钟形（Lf↓）起始更陡（θn↑）、出口残余发散更大（θe↑）。
    典型量级：ε=20 → θn≈33°/θe≈10°；ε=35 → θn≈34°/θe≈9°；ε=80 → θn≈37°/θe≈7°。
    """
    ln_eps = math.log(max(expansion_ratio, 1.0001))
    d_bell = 0.80 - bell_fraction            # 相对 80% 基准的长度差
    theta_n = 24.0 + 2.9 * ln_eps + 25.0 * d_bell
    theta_e = 16.5 - 2.1 * ln_eps + 15.0 * d_bell
    theta_n = max(20.0, min(42.0, theta_n))
    theta_e = max(5.0, min(15.0, theta_e))
    return theta_n, theta_e


def _rao_bell_contour(
    rt: float,
    re: float,
    rc: float,
    expansion_ratio: float,
    n_points: int = 200,
    bell_fraction: float = 0.80,
    theta_conv_deg: float = 30.0,
) -> Tuple[List[float], List[float], float, float, float, float, float, float]:
    """
    计算 TOP 抛物线钟形喷管完整轮廓（含收缩段）。

    参数
    ────
    rt      : 喉部半径 [m]
    re      : 出口半径 [m]
    rc      : 燃烧室半径 [m]
    expansion_ratio : 面积膨胀比
    n_points: 轮廓点数
    bell_fraction: 钟形长度比（0.80 = 80% bell）
    theta_conv_deg: 收缩段半角 [°]

    返回
    ────
    (xs, rs, L_nozzle, L_conv, theta_n_deg, theta_e_deg, Ru, Rd)
    """
    theta_conv = math.radians(theta_conv_deg)

    # 喉部圆弧半径
    Ru = 1.5 * rt      # 上游（收缩侧）
    Rd = 0.382 * rt     # 下游（膨胀侧，Rao TOP 标准值）

    # 15° 锥形喷管长度（参考基准）
    L_conical = (re - rt) / math.tan(math.radians(15.0))
    L_nozzle = bell_fraction * L_conical

    # 初始抛物线角 θn、出口角 θe —— TOP 按 Rao 设计图拟合（见 top_bell_angles）
    theta_n_deg, theta_e_deg = top_bell_angles(expansion_ratio, bell_fraction)
    theta_n = math.radians(theta_n_deg)
    theta_e = math.radians(theta_e_deg)

    # ── 收缩段：取自唯一规范轮廓 chamber_contour ────────────────────
    # 含「圆筒→收缩段入口倒角 R1」+「喉部上游圆弧 Ru」，两端相切、C¹ 连续无尖角，
    # 与主燃烧室页 / 2D/3D 图 / 热结构云图完全同构（不再本地重复实现一套"直锥尖角"
    # 收缩段——那是 V16 以前两套定义并存、且圆筒接口处有 C¹ 折点的根源）。
    # 坐标系：本模块喉部在 x=0、收缩段位于负 x；chamber_contour 给出 x∈[0,L_conv]
    # （rc→rt），整体减 L_conv 即把喉部平移到 x=0。
    from chamber_contour import convergent_contour
    n_conv = max(n_points // 4, 18)
    cvx, cvr, L_conv = convergent_contour(
        rc, rt, conv_half_angle_deg=theta_conv_deg, x0=0.0,
        n_in=max(n_conv // 3, 6), n_straight=max(n_conv // 3, 6),
        n_arc=max(n_conv // 3, 8))

    xs = []
    rs = []
    for xc, rcl in zip(cvx, cvr):
        xs.append(xc - L_conv)              # 平移：喉部(rt) 落在 x=0，rc 在 x=−L_conv
        rs.append(rcl)

    # ── 下游圆弧（喉部到初始膨胀角）───────────────────
    n_throat = n_points // 8
    for i in range(1, n_throat + 1):
        frac = i / max(n_throat, 1)
        angle = frac * theta_n
        x = Rd * math.sin(angle)
        r = rt + Rd * (1.0 - math.cos(angle))
        xs.append(x)
        rs.append(r)

    # 下游圆弧终点
    x_n = Rd * math.sin(theta_n)
    r_n = rt + Rd * (1.0 - math.cos(theta_n))

    # ── 钟形段：TOP 抛物线（二次 Bézier，从 N=(x_n,r_n) 到 E=(L_nozzle,re)）──
    # 控制点 Q = 过 N 的 θn 切线 与 过 E 的 θe 切线 之交点（这正是 Rao 抛物线
    # 近似法的定义；二次 Bézier 的轨迹恰为抛物线段，区别于旧版三次 Bézier）。
    n_bell = n_points - len(xs)
    if n_bell < 10:
        n_bell = 10

    P0 = (x_n, r_n)
    P2 = (L_nozzle, re)
    tan_n = math.tan(theta_n)
    tan_e = math.tan(theta_e)
    denom = tan_n - tan_e
    if abs(denom) < 1e-9:
        Qx = 0.5 * (P0[0] + P2[0])
        Qr = 0.5 * (P0[1] + P2[1])
    else:
        # 解 r0 + tan_n·(x−x0) = re + tan_e·(x−xe)
        Qx = (P2[1] - P0[1] + tan_n * P0[0] - tan_e * P2[0]) / denom
        Qr = P0[1] + tan_n * (Qx - P0[0])
    Q = (Qx, Qr)

    for i in range(1, n_bell + 1):
        t = i / n_bell
        mt = 1.0 - t
        x = mt * mt * P0[0] + 2.0 * mt * t * Q[0] + t * t * P2[0]
        r = mt * mt * P0[1] + 2.0 * mt * t * Q[1] + t * t * P2[1]
        xs.append(x)
        rs.append(r)

    return (xs, rs, L_nozzle, L_conv, theta_n_deg, theta_e_deg, Ru, Rd)


def full_hot_gas_contour(
    rt: float,
    re: float,
    rc: float,
    expansion_ratio: float,
    L_cyl: float,
    *,
    bell_fraction: float = 0.80,
    theta_conv_deg: float = 30.0,
    n_points: int = 300,
    n_cyl: int = 14,
):
    """全发动机燃气侧内壁轮廓（喷注面 → 喷管出口）——**唯一规范来源**（V19）。

    圆筒段（直线）+ 收缩段（chamber_contour 规范轮廓：入口倒角 R1 + 直锥 β +
    喉部上游弧 Ru=1.5Rt）+ 喉部下游弧 Rd=0.382Rt + TOP 抛物线钟形段，
    与 compute_nozzle 的轮廓完全同构（同一 _rao_bell_contour）。

    cooling_marching / 推力室设计工作区 / CSV 导出都应取此轮廓，消除历史上
    "marching 自建轮廓（无 Rd 弧）vs 几何模块 Rao 轮廓"两套并存的口径分裂。

    坐标系：x=0 在喷注面，x 单调增至喷管出口。
    返回 (xs, rs, x_throat)。
    """
    xs_r, rs_r, _L_noz, L_conv, _tn, _te, _Ru, _Rd = _rao_bell_contour(
        rt, re, rc, expansion_ratio,
        n_points=max(n_points - n_cyl, 60),
        bell_fraction=bell_fraction,
        theta_conv_deg=theta_conv_deg,
    )
    xs: List[float] = []
    rs: List[float] = []
    # 圆筒段（不含末点——收缩段首点即圆筒末端 (x=L_cyl, rc)）
    for i in range(max(n_cyl, 1)):
        xs.append(L_cyl * i / max(n_cyl, 1))
        rs.append(rc)
    # Rao 轮廓：喉部在 x=0、收缩段在负 x → 平移到喷注面坐标系
    shift = L_cyl + L_conv
    for xr, rr in zip(xs_r, rs_r):
        xs.append(xr + shift)
        rs.append(rr)
    return xs, rs, L_cyl + L_conv


def compute_nozzle(
    rt: float,
    re: float,
    rc: float,
    expansion_ratio: float,
    contraction_ratio: float,
    chamber_length_m: float,
    bell_fraction: float = 0.80,
    theta_conv_deg: float = 30.0,
    wall_thickness_mm: float = 0.0,
) -> NozzleGeometry:
    """由喉部/出口/室半径推导完整喷管几何（含轮廓与内表面积）。"""
    pi = math.pi
    xs, rs, L_noz, L_conv, theta_n, theta_e, Ru, Rd = _rao_bell_contour(
        rt, re, rc, expansion_ratio,
        n_points=300,
        bell_fraction=bell_fraction,
        theta_conv_deg=theta_conv_deg,
    )

    # 喷管内表面积 (用轮廓离散积分)：A = ∫ 2π r · √(dr² + dx²)
    A_inner_noz = 0.0
    if len(xs) >= 2:
        for i in range(1, len(xs)):
            dx = xs[i] - xs[i - 1]
            dr = rs[i] - rs[i - 1]
            ds = math.sqrt(dx * dx + dr * dr)
            r_avg = 0.5 * (rs[i] + rs[i - 1])
            A_inner_noz += 2.0 * pi * r_avg * ds

    return NozzleGeometry(
        throat_diameter_m=2.0 * rt,
        throat_radius_m=rt,
        exit_diameter_m=2.0 * re,
        exit_radius_m=re,
        expansion_ratio=expansion_ratio,
        contraction_ratio=contraction_ratio,
        nozzle_length_m=L_noz,
        convergent_length_m=L_conv,
        total_length_m=chamber_length_m + L_conv + L_noz,
        convergent_half_angle_deg=theta_conv_deg,
        throat_upstream_radius_m=Ru,
        throat_downstream_radius_m=Rd,
        initial_expansion_angle_deg=theta_n,
        exit_angle_deg=theta_e,
        contour_x=xs,
        contour_r=rs,
        bell_fraction=bell_fraction,
        nozzle_type="TOP",                                    # 推力优化抛物线
        wall_thickness_mm=wall_thickness_mm,                  # 同 chamber 总壁厚
        inner_surface_area_m2=A_inner_noz,
        throat_axial_position_m=chamber_length_m + L_conv,    # 从喷注面算起
        num_contour_points=len(xs),
    )
