"""
geometry/chamber.py

主燃烧室几何：由喉部面积按收缩比放大成室截面，用特征长度 L* 定容积，
并给出再生冷却分层壁厚与冷却通道分布。

依据：Huzel & Huang《Modern Engineering for Design of Liquid-Propellant
Rocket Engines》；NASA SP-125。
"""

from __future__ import annotations

import math

from .types import ChamberGeometry


def compute_chamber(
    At: float,
    contraction_ratio: float,
    l_star: float,
    pc: float,
    sigma_allow: float = 120e6,
    safety: float = 1.5,
    *,
    q_throat_W_m2: float = 0.0,
    coolant_pressure_pa: float = 0.0,
    k_liner_WmK: float = 303.0,
    sigma_jacket_pa: float = 950e6,
    channel_height_mm: float = 0.0,
    dT_cond_allow_K: float = 250.0,
    conv_half_angle_deg: float = 30.0,
    n_channels: int = 0,
    channel_width_mm: float = 0.0,
    liner_thickness_mm: float | None = None,
    cooling_profile: dict | None = None,
) -> ChamberGeometry:
    """
    参数
    ────
    At                : 喉部面积 [m²]
    contraction_ratio : 燃烧室/喉部面积比 εc
    l_star            : 特征长度 [m]
    pc                : 燃烧室压力 [Pa]
    sigma_allow       : （保留兼容）内壁材料许用应力 [Pa]，V12 已不用于定厚
    safety            : 壁厚安全系数（用于外壳环向应力）

    保存几何与概念设计估算
    ────
    cooling_profile    : 已解冷却剖面；提供时优先采用其实际尺寸，不再次定尺寸。
                         返回通道标量代表喷注面端截面，完整分布仍由剖面保存。
    liner_thickness_mm : 指定内衬厚度 [mm]；不裁切。没有剖面且未指定时，
                         才使用 k·ΔT/q 的历史估算，缺热流时为 1 mm。
    channel_height_mm  : 指定通道深 [mm]；不裁切。无剖面且为零时估算为 4 mm。
    q_throat_W_m2, k_liner_WmK, dT_cond_allow_K : 仅供上述未定内衬厚度的估算。
    coolant_pressure_pa, sigma_jacket_pa : 与 pc、安全系数共同估算薄壁外壳厚度。
                         该值不是已冻结的硬件厚度，也不代替结构强度或寿命校核。
    """
    pi = math.pi
    for name, value in (("At", At), ("contraction_ratio", contraction_ratio),
                        ("l_star", l_star), ("pc", pc), ("safety", safety),
                        ("sigma_jacket_pa", sigma_jacket_pa)):
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} 必须为有限正数。")
    if contraction_ratio <= 1:
        raise ValueError("燃烧室收缩比必须大于1。")

    # A solved cooling profile is physical geometry, not a suggestion for a
    # second sizing pass. Chamber scalars represent its injector-end section;
    # the full axial distribution remains in the saved profile.
    profile_geometry = None
    if cooling_profile is not None:
        if not isinstance(cooling_profile, dict):
            raise ValueError("已给冷却剖面必须为包含实际几何的记录。")
        profile_geometry = cooling_profile.get("geometry")
        if (cooling_profile.get("model_id") != "regen_quasi1d_ph_v1"
                or not isinstance(profile_geometry, dict)
                or not profile_geometry.get("nodes")):
            raise ValueError("已给冷却剖面缺少可识别的实际几何，不能回退重建。")
        node = min(profile_geometry["nodes"], key=lambda row: row["x_m"])
        for key in ("width_m", "height_m", "wall_thickness_m", "n_channels"):
            if not math.isfinite(node[key]) or node[key] <= 0:
                raise ValueError(f"冷却剖面的实际通道几何 {key} 无效，不能回退估算。")
        for key in ("r_t_m", "r_c_m", "L_cyl_m"):
            if not math.isfinite(profile_geometry[key]) or profile_geometry[key] <= 0:
                raise ValueError(f"冷却剖面几何 {key} 无效。")
        At = pi * profile_geometry["r_t_m"] ** 2
        contraction_ratio = (profile_geometry["r_c_m"] / profile_geometry["r_t_m"]) ** 2
        if contraction_ratio <= 1:
            raise ValueError("冷却剖面的燃烧室半径必须大于喉部半径。")
        conv_half_angle_deg = float(profile_geometry.get("conv_half_angle_deg", conv_half_angle_deg))
        n_channels = node["n_channels"]
        channel_width_mm = 1000 * node["width_m"]
        channel_height_mm = 1000 * node["height_m"]
        liner_thickness_mm = 1000 * node["wall_thickness_m"]

    Ac = contraction_ratio * At
    rc = math.sqrt(Ac / pi)
    Vc = l_star * At
    # V16：圆柱段长扣除收缩段（直锥 + 喉部圆弧）体积，收缩段取唯一规范轮廓
    # （chamber_contour），与 chamber_sizing.py / 2D图 / 热结构云图完全同口径。
    rt = math.sqrt(At / pi)
    from chamber_contour import convergent_geometry
    _cg = convergent_geometry(rc, rt, conv_half_angle_deg)
    V_conv = _cg["V_conv_m3"]
    if profile_geometry is not None:
        Lc = profile_geometry["L_cyl_m"]
        Vc = Ac * Lc + V_conv
        l_star = Vc / At
    else:
        if Vc <= V_conv:
            raise ValueError("指定特征容积不足以容纳收缩段，不能将圆筒长度裁成零。")
        Lc = (Vc - V_conv) / Ac

    # 内衬与通道保持已给几何；只有缺少几何时才使用历史概念设计估算。
    if liner_thickness_mm is not None:
        t_inner_mm = float(liner_thickness_mm)
    elif q_throat_W_m2 > 1.0e3:
        t_inner_mm = k_liner_WmK * dT_cond_allow_K / q_throat_W_m2 * 1000.0
    else:
        t_inner_mm = 1.0   # 无热流和厚度数据时的工程估算，不是实机参数。
    if not math.isfinite(t_inner_mm) or t_inner_mm <= 0:
        raise ValueError("内衬厚度必须为有限正数；不能隐性裁切给定几何。")

    # ② 通道深：优先用冷却模块解出的实际通道高（与再生冷却物理同口径），缺则经验 4 mm
    if not math.isfinite(channel_height_mm) or channel_height_mm < 0:
        raise ValueError("通道深度必须非负；零仅表示未提供设计估算输入。")
    t_chan_mm = channel_height_mm if channel_height_mm > 0 else 4.0

    # ③ 外壳：环向应力限（包容高压冷却剂，非 pc）
    p_struct = coolant_pressure_pa if coolant_pressure_pa > pc else pc
    t_outer_mm = safety * p_struct * rc / sigma_jacket_pa * 1000.0
    if not math.isfinite(t_outer_mm) or t_outer_mm <= 0:
        raise ValueError("外壳设计厚度必须为有限正数。")

    t_wall_mm = t_inner_mm + t_chan_mm + t_outer_mm

    # 冷却通道周向分布:优先用冷却模块的设计值（通道数 N、通道宽），保证几何估算/3D 图/
    # FEA 与再生冷却物理**同一口径**；缺失时回退到"目标节距 ~4 mm"的几何估算。
    circumference_mm = 2.0 * pi * rc * 1000.0
    if not math.isfinite(n_channels) or n_channels < 0 or int(n_channels) != n_channels:
        raise ValueError("通道数必须为非负整数；零表示未提供设计估算输入。")
    if n_channels and n_channels > 0:
        n_cool = int(n_channels)
    else:
        n_cool = max(40, int(round(circumference_mm / 4.0)))
    actual_pitch_mm = circumference_mm / max(n_cool, 1)
    if not math.isfinite(channel_width_mm) or channel_width_mm < 0:
        raise ValueError("通道宽度必须非负。")
    ch_width_mm = channel_width_mm if channel_width_mm > 0 else 0.55 * actual_pitch_mm
    if ch_width_mm >= actual_pitch_mm:
        raise ValueError("通道宽度必须小于实际节距；不能裁窄后继续展示。")
    ch_height_mm = t_chan_mm

    # 燃烧室内表面积 (圆柱段;不含 dome / 收缩段)
    A_inner_cyl = 2.0 * pi * rc * Lc

    return ChamberGeometry(
        diameter_m=2.0 * rc,
        radius_m=rc,
        length_m=Lc,
        volume_m3=Vc,
        l_star_m=l_star,
        wall_thickness_mm=t_wall_mm,
        contraction_ratio=contraction_ratio,
        cylindrical_section_length_m=Lc,
        inner_wall_thickness_mm=t_inner_mm,
        cooling_channel_height_mm=ch_height_mm,
        cooling_channel_width_mm=ch_width_mm,
        outer_wall_thickness_mm=t_outer_mm,
        num_cooling_channels=n_cool,
        inner_surface_area_m2=A_inner_cyl,
        dome_radius_m=rc,                                # 平 dome 简化:半径 = rc
        # 圆筒→收缩段入口倒角半径 R1：取自唯一规范轮廓（chamber_contour，含几何截断），
        # 与 2D/3D 图实际画出的入口弧同口径，不再用与轮廓脱节的 0.10·rc 占位值。
        chamber_to_convergent_blend_radius_m=_cg.get("R1_m", 0.0),
    )
