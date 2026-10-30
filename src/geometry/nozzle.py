"""Canonical axisymmetric gas-wall geometry for system-level calculations.

The divergent contour joins a circular throat blend to a quadratic Bezier
parabola using the intersection of specified endpoint tangents. This is an
engineering construction, not a verified thrust-optimized (TOP/TOC) design.
The optional historical logarithmic angle rule has no traced fit dataset and
is retained solely to reproduce older parameter sets.

Nozzle coordinates put the throat at x=0. Full geometry coordinates put the
injector face at x=0; the cylinder, convergent and divergent are one contour.
"""

from __future__ import annotations

import math
from typing import List, Tuple

from .types import NozzleGeometry


CONTOUR_METHODS = ("parabolic_bell_explicit_v1", "legacy_log_v1")


def top_bell_angles(expansion_ratio: float, bell_fraction: float = 0.80) -> Tuple[float, float]:
    """Historical API name: unvalidated logarithmic angles [deg], not a Rao fit."""
    ln_eps = math.log(max(expansion_ratio, 1.0001))
    d_bell = 0.80 - bell_fraction
    return (max(20.0, min(42.0, 24.0 + 2.9 * ln_eps + 25.0 * d_bell)),
            max(5.0, min(15.0, 16.5 - 2.1 * ln_eps + 15.0 * d_bell)))


def contour_definition(expansion_ratio, bell_fraction=0.80, *,
                       nozzle_contour_method="legacy_log_v1",
                       nozzle_initial_angle_deg=35.0, nozzle_exit_angle_deg=10.0):
    """Resolve actual angles and their provenance without substituting defaults."""
    if nozzle_contour_method not in CONTOUR_METHODS:
        raise ValueError("Unknown nozzle contour method")
    if nozzle_contour_method == "legacy_log_v1":
        tn, te = top_bell_angles(expansion_ratio, bell_fraction)
        source = "historical_log_rule_unvalidated"
        note = "历史对数角度规则；无可追溯拟合数据，仅用于旧方案复现。"
    else:
        tn, te = float(nozzle_initial_angle_deg), float(nozzle_exit_angle_deg)
        source = "specified_design_angles"
        note = "显式给定设计角度；圆弧与二次 Bézier 相切构造，未进行推力优化。"
    if not all(math.isfinite(v) for v in (tn, te)) or not (0 <= te < tn < 90):
        raise ValueError("喷管角度必须满足 0 ≤ 出口角 < 起始角 < 90°。")
    return dict(nozzle_contour_method=nozzle_contour_method,
                initial_expansion_angle_deg=tn, exit_angle_deg=te,
                angle_source=source, method_note=note,
                nozzle_type="parabolic_bell_engineering")


def _bezier_definition(rt, re, expansion_ratio, bell_fraction, **definition):
    """Validate tangent intersection before sampling; no midpoint fallback."""
    meta = contour_definition(expansion_ratio, bell_fraction, **definition)
    tn, te = map(math.radians, (meta["initial_expansion_angle_deg"], meta["exit_angle_deg"]))
    rd = .382 * rt  # Declared construction assumption, not measured hardware.
    length = bell_fraction * (re - rt) / math.tan(math.radians(15))
    xn, rn = rd * math.sin(tn), rt + rd * (1 - math.cos(tn))
    dx, dr = length - xn, re - rn
    sn, se = math.tan(tn), math.tan(te)
    if dx <= 0 or dr <= 0 or not se < dr / dx < sn:
        raise ValueError("喷管长度、面积比与角度不相容：出口弦斜率必须位于两端切线斜率之间；请调整长度比或角度。")
    qx = (re - rn + sn * xn - se * length) / (sn - se)
    qr = rn + sn * (qx - xn)
    if not xn < qx < length or not rn < qr <= re:
        raise ValueError("喷管切线交点导致型面反折，不能生成有效钟形轮廓。")
    return meta, (xn, rn), (qx, qr), (length, re), rd


def _rao_bell_contour(
    rt: float,
    re: float,
    rc: float,
    expansion_ratio: float,
    n_points: int = 200,
    bell_fraction: float = 0.80,
    theta_conv_deg: float = 30.0,
    *, nozzle_contour_method="legacy_log_v1",
    nozzle_initial_angle_deg=35.0, nozzle_exit_angle_deg=10.0,
) -> Tuple[List[float], List[float], float, float, float, float, float, float]:
    """
    兼容函数名：计算工程抛物线钟形轮廓（含收缩段），不声明 TOP 优化。

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
    if not all(math.isfinite(v) and v > 0 for v in (rt, re, rc, expansion_ratio, bell_fraction)):
        raise ValueError("nozzle dimensions must be finite and positive")
    if rc <= rt or expansion_ratio <= 1 or not math.isclose((re/rt)**2, expansion_ratio, rel_tol=1e-10):
        raise ValueError("nozzle radii and area ratios are inconsistent")
    if isinstance(n_points, bool) or int(n_points) != n_points or n_points < 24:
        raise ValueError("nozzle n_points must be an integer >= 24")
    # 喉部上游半径必须读取规范轮廓的实际值；低收缩比时会按几何容许量缩小。
    from chamber_contour import convergent_contour, convergent_geometry
    convergent = convergent_geometry(rc, rt, theta_conv_deg)
    Ru = convergent["Ru_m"]
    if nozzle_contour_method == "parabolic_bell_explicit_v1" and convergent["R1_m"] <= 1e-12:
        raise ValueError("所选收缩比与收缩角使圆筒入口过渡圆角退化为尖角；请增大收缩比或减小收缩角。")
    meta, P0, Q, P2, Rd = _bezier_definition(rt, re, expansion_ratio, bell_fraction,
        nozzle_contour_method=nozzle_contour_method,
        nozzle_initial_angle_deg=nozzle_initial_angle_deg,
        nozzle_exit_angle_deg=nozzle_exit_angle_deg)
    L_nozzle = P2[0]
    theta_n_deg, theta_e_deg = meta["initial_expansion_angle_deg"], meta["exit_angle_deg"]
    theta_n = math.radians(theta_n_deg)

    # ── 收缩段：取自唯一规范轮廓 chamber_contour ────────────────────
    # 含「圆筒→收缩段入口倒角 R1」+「喉部上游圆弧 Ru」；新方法拒绝退化圆角。
    # 与主燃烧室页 / 2D/3D 图 / 热结构云图完全同构（不再本地重复实现一套"直锥尖角"
    # 收缩段——那是 V16 以前两套定义并存、且圆筒接口处有 C¹ 折点的根源）。
    # 坐标系：本模块喉部在 x=0、收缩段位于负 x；chamber_contour 给出 x∈[0,L_conv]
    # （rc→rt），整体减 L_conv 即把喉部平移到 x=0。
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

    # Quadratic Bezier: endpoints N/E and their validated tangent intersection Q.
    n_bell = max(n_points - len(xs), 10)

    for i in range(1, n_bell + 1):
        t = i / n_bell
        mt = 1.0 - t
        x = mt * mt * P0[0] + 2.0 * mt * t * Q[0] + t * t * P2[0]
        r = mt * mt * P0[1] + 2.0 * mt * t * Q[1] + t * t * P2[1]
        xs.append(x)
        rs.append(r)

    return (xs, rs, L_nozzle, L_conv, theta_n_deg, theta_e_deg, Ru, Rd)


def full_hot_gas_geometry(
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
    nozzle_contour_method="legacy_log_v1",
    nozzle_initial_angle_deg=35.0, nozzle_exit_angle_deg=10.0,
):
    """全发动机燃气侧内壁轮廓（喷注面 → 喷管出口）——**唯一规范来源**（V19）。

    圆筒段（直线）+ 收缩段（chamber_contour 规范轮廓：入口倒角 R1 + 直锥 β +
    喉部上游弧 Ru（实际构造值））+ 喉部下游弧 Rd=0.382Rt + 工程抛物线钟形段，
    与 compute_nozzle 的轮廓完全同构（同一 _rao_bell_contour）。

    cooling_marching / 推力室设计工作区 / CSV 导出都应取此轮廓，消除历史上
    "marching 自建轮廓（无 Rd 弧）vs 几何模块规范轮廓"两套并存的口径分裂。

    坐标系：x=0 在喷注面，x 单调增至喷管出口。
    返回轮廓及其实际几何元数据；上游喉部曲率从构造圆弧读取，不从网格差分估计。
    """
    xs_r, rs_r, _L_noz, L_conv, _tn, _te, _Ru, _Rd = _rao_bell_contour(
        rt, re, rc, expansion_ratio,
        n_points=max(n_points - n_cyl, 60),
        bell_fraction=bell_fraction,
        theta_conv_deg=theta_conv_deg,
        nozzle_contour_method=nozzle_contour_method,
        nozzle_initial_angle_deg=nozzle_initial_angle_deg, nozzle_exit_angle_deg=nozzle_exit_angle_deg,
    )
    if not math.isfinite(L_cyl) or L_cyl <= 0:
        raise ValueError("L_cyl must be finite and positive")
    xs: List[float] = []
    rs: List[float] = []
    # 圆筒段（不含末点——收缩段首点即圆筒末端 (x=L_cyl, rc)）
    for i in range(max(n_cyl, 1)):
        xs.append(L_cyl * i / max(n_cyl, 1))
        rs.append(rc)
    # 喷管轮廓：喉部在 x=0、收缩段在负 x → 平移到喷注面坐标系
    shift = L_cyl + L_conv
    for xr, rr in zip(xs_r, rs_r):
        xs.append(xr + shift)
        rs.append(rr)
    from chamber_contour import convergent_geometry
    cg = convergent_geometry(rc, rt, theta_conv_deg)
    return dict(contour_x=xs, contour_r=rs, x_throat_m=L_cyl + L_conv,
                chamber_blend_radius_m=cg["R1_m"],
                convergent_tangent_continuity="C1" if cg["R1_m"] > 1e-12 else "legacy_cylinder_corner",
                L_conv_m=L_conv, L_nozzle_m=_L_noz,
                throat_upstream_radius_m=_Ru, throat_downstream_radius_m=_Rd,
                conv_half_angle_deg=theta_conv_deg, bell_fraction=bell_fraction,
                source="geometry.nozzle.full_hot_gas_geometry",
                coordinate_origin="injector_face",
                **contour_definition(expansion_ratio, bell_fraction,
                    nozzle_contour_method=nozzle_contour_method,
                    nozzle_initial_angle_deg=nozzle_initial_angle_deg, nozzle_exit_angle_deg=nozzle_exit_angle_deg))


def full_hot_gas_contour(rt, re, rc, expansion_ratio, L_cyl, *,
                         bell_fraction=0.80, theta_conv_deg=30.0,
                         n_points=300, n_cyl=14,
                         nozzle_contour_method="legacy_log_v1",
                         nozzle_initial_angle_deg=35.0, nozzle_exit_angle_deg=10.0):
    """Compatibility tuple view of the canonical geometry, without recomputing a contour."""
    geom = full_hot_gas_geometry(rt, re, rc, expansion_ratio, L_cyl,
        bell_fraction=bell_fraction, theta_conv_deg=theta_conv_deg,
        n_points=n_points, n_cyl=n_cyl, nozzle_contour_method=nozzle_contour_method,
        nozzle_initial_angle_deg=nozzle_initial_angle_deg, nozzle_exit_angle_deg=nozzle_exit_angle_deg)
    return geom["contour_x"], geom["contour_r"], geom["x_throat_m"]


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
    *, nozzle_contour_method="legacy_log_v1",
    nozzle_initial_angle_deg=35.0, nozzle_exit_angle_deg=10.0,
) -> NozzleGeometry:
    """由喉部/出口/室半径推导完整喷管几何（含轮廓与内表面积）。"""
    if not math.isfinite(chamber_length_m) or chamber_length_m <= 0:
        raise ValueError("chamber length must be finite and positive")
    if not math.isfinite(contraction_ratio) or not math.isclose((rc / rt)**2, contraction_ratio, rel_tol=1e-10):
        raise ValueError("nozzle contraction ratio conflicts with radii")
    pi = math.pi
    xs, rs, L_noz, L_conv, theta_n, theta_e, Ru, Rd = _rao_bell_contour(
        rt, re, rc, expansion_ratio,
        n_points=300,
        bell_fraction=bell_fraction,
        theta_conv_deg=theta_conv_deg,
        nozzle_contour_method=nozzle_contour_method,
        nozzle_initial_angle_deg=nozzle_initial_angle_deg, nozzle_exit_angle_deg=nozzle_exit_angle_deg,
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
        nozzle_type="parabolic_bell_engineering",
        nozzle_contour_method=nozzle_contour_method,
        angle_source=contour_definition(expansion_ratio, bell_fraction,
            nozzle_contour_method=nozzle_contour_method, nozzle_initial_angle_deg=nozzle_initial_angle_deg,
            nozzle_exit_angle_deg=nozzle_exit_angle_deg)["angle_source"],
        wall_thickness_mm=wall_thickness_mm,                  # 同 chamber 总壁厚
        inner_surface_area_m2=A_inner_noz,
        throat_axial_position_m=chamber_length_m + L_conv,    # 从喷注面算起
        num_contour_points=len(xs),
    )
