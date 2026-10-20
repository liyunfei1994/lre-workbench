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

    V12 新增（按各层真实失效模式分别定厚，对标 Raptor 通道壁构造）
    ────
    q_throat_W_m2       : 喉部热流 [W/m²]（来自冷却模块）；定内衬传热限厚度
    coolant_pressure_pa : 冷却剂压力 [Pa]（≈燃料泵出口，约 2×pc）；定外壳环向应力
    k_liner_WmK         : 内衬导热系数 [W/(m·K)]（GRCop-84 ≈303）
    sigma_jacket_pa     : 外壳屈服强度 [Pa]（Inconel 718 热态 ≈950 MPa）
    channel_height_mm   : 冷却通道径向深 [mm]；0=经验默认
    dT_cond_allow_K     : 内衬允许导热温降 [K]（铜衬典型 200–300 K）
    """
    pi = math.pi

    Ac = contraction_ratio * At
    rc = math.sqrt(Ac / pi)
    Vc = l_star * At
    # V16：圆柱段长扣除收缩段（直锥 + 喉部圆弧）体积，收缩段取唯一规范轮廓
    # （chamber_contour），与 chamber_sizing.py / 2D图 / 热结构云图完全同口径。
    rt = math.sqrt(At / pi)
    from chamber_contour import convergent_geometry
    _cg = convergent_geometry(rc, rt, conv_half_angle_deg)
    L_conv = _cg["L_conv_m"]
    V_conv = _cg["V_conv_m3"]
    Lc = max(Vc - V_conv, 0.0) / Ac

    # ── 分层壁厚（V12：按各层真实失效模式分别定厚，对标 Raptor 通道壁）──
    #
    # 真实再生冷却"通道壁"是三明治：薄铜内衬 + 铣槽冷却通道 + 镍基承力外壳，
    # 三层定厚判据完全不同：
    #   ① 内衬(铜)   —— 传热限：热流要"穿过"内衬进冷却剂，越厚热侧越烫。
    #        ΔT_导 = q·t/k  →  t = k·ΔT_允许 / q     （Raptor 喉部 ≈0.5–1 mm）
    #   ② 通道深     —— 冷却需求（冷却剂流速/吸热），取冷却模块值或经验 4 mm。
    #   ③ 外壳(镍基) —— 环向应力限，包容的是 *高压冷却剂*（≈泵出口压，约 2×pc），
    #        不是 pc！   t = SF·P_冷却剂·rc / σ_外壳   （薄壁 Barlow）
    #
    # 旧版(V7)用"单一 Barlow(0.25pc, 铜的弱强度) + 15/25/60% 切分"——载荷与材料
    # 都张冠李戴：巧合凑出 ~24 mm 总厚，但内衬虚高 3–4 倍、外壳对冷却剂压力反而偏薄。

    # ① 内衬：传热限  t = k·ΔT / q
    if q_throat_W_m2 > 1.0e3:
        t_inner_mm = k_liner_WmK * dT_cond_allow_K / q_throat_W_m2 * 1000.0
    else:
        t_inner_mm = 1.0   # 无热流数据时的 Raptor 量级默认
    t_inner_mm = max(0.5, min(1.5, t_inner_mm))

    # ② 通道深：优先用冷却模块解出的实际通道高（与再生冷却物理同口径），缺则经验 4 mm
    t_chan_mm = channel_height_mm if channel_height_mm > 0.1 else 4.0
    t_chan_mm = max(2.0, min(15.0, t_chan_mm))

    # ③ 外壳：环向应力限（包容高压冷却剂，非 pc）
    p_struct = coolant_pressure_pa if coolant_pressure_pa > pc else pc
    t_outer_mm = safety * p_struct * rc / sigma_jacket_pa * 1000.0
    t_outer_mm = max(3.0, min(40.0, t_outer_mm))

    t_wall_mm = t_inner_mm + t_chan_mm + t_outer_mm

    # 冷却通道周向分布:优先用冷却模块的设计值（通道数 N、通道宽），保证几何估算/3D 图/
    # FEA 与再生冷却物理**同一口径**；缺失时回退到"目标节距 ~4 mm"的几何估算。
    circumference_mm = 2.0 * pi * rc * 1000.0
    if n_channels and n_channels > 0:
        n_cool = int(n_channels)
    else:
        n_cool = max(40, int(round(circumference_mm / 4.0)))
    actual_pitch_mm = circumference_mm / max(n_cool, 1)
    ch_width_mm = channel_width_mm if channel_width_mm > 0.05 else 0.55 * actual_pitch_mm
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
