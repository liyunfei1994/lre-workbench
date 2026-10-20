"""
chamber_contour.py — 主燃烧室收缩段的【唯一规范轮廓】（入口倒角 + 直锥 + 喉部圆弧）

全程统一口径：部件结果(chamber_sizing) / 几何估算(geometry.chamber + geometry.nozzle +
2D/3D 图 + CSV + 拓扑) / 热结构云图(cooling_marching) 都从本模块取收缩段形状与
L_conv / V_conv，消除历史上"两套收缩段定义并存"导致的不一致。

形状（Huzel & Huang / Rao 惯例；两端都用相切圆弧，全程 C¹ 连续，无尖角）：

  圆筒(rc) ──┐                                         ┌── 喉部(rt)
            │  入口倒角弧 R1   直锥 半锥角 β   喉部上游弧 Ru
            └─────╮          ╲              ╭─────────┘
                   ╰──相切──→ ╲ ──相切──→ ╭──
                   (圆筒↔锥)   直锥段     (锥↔喉部)

关键修正（V17）：旧版圆筒直接尖角接直锥（dr/dx 从 0 突跳到 −tanβ，C¹ 间断），在
该折点会出现 ① 局部热流尖峰 ② 应力集中 ③ 冷却通道转折变形 ④ 疲劳裂纹萌生。真实可
加工推力室在圆筒→收缩段入口用一段**相切圆弧 R1**平滑过渡。本版补上该入口倒角：
  · 入口弧 R1 相切于圆筒（起点 dr/dx=0，与圆筒斜率一致）并相切于直锥；
  · 喉部上游弧 Ru=1.5·Rt（行业惯例，旧版已有）相切于直锥并相切于喉部。
R1 取 R1_factor·Rt（默认 1.5，与 Ru 同量级的"圆弧收缩段"构型），并按几何容许量
截断，保证直锥段长 ≥ 0（高 β/低收缩比时两弧不互相侵入）。
"""

from __future__ import annotations

import math


def _beta_rad(conv_half_angle_deg: float) -> float:
    return math.radians(max(min(conv_half_angle_deg, 80.0), 5.0))


def _resolve_radii(rc: float, rt: float, beta: float,
                   Ru_factor: float, R1_factor: float):
    """解析收缩段两段相切圆弧的半径与切点（含 R1 几何截断）。

    返回 (Ru, R1, r_tan1, r_tan2, L_straight)：
      Ru        喉部上游弧半径 = Ru_factor·rt
      R1        入口倒角弧半径（已截断到几何容许量，保证 L_straight ≥ 0）
      r_tan1    圆筒弧↔直锥 上切点半径
      r_tan2    直锥↔喉部弧 下切点半径
      L_straight 直锥段轴向长
    """
    cosb = math.cos(beta)
    omc = 1.0 - cosb
    Ru = Ru_factor * rt
    # V19 审查修复：喉部上游弧也按几何容许量截断——低收缩比+大 β 时
    # rt + Ru(1−cosβ) 可能超过 rc（喉部弧自己就放不下），旧版只截 R1 不截 Ru，
    # 导致 r_tan2 > rc：轮廓凸出燃烧室半径并出现重复 x 的竖直跳变。
    Ru = min(Ru, max((rc - rt) / max(omc, 1e-9), 0.0))
    r_tan2 = rt + Ru * omc
    # R1 上限：令 r_tan1 = r_tan2（直锥段长压到 0）时的最大入口弧半径。
    # 超过它两弧会互相侵入；不足 0 则连喉部弧都放不下（极端高 β/低 εc），R1 取 0 回退尖角。
    R1_max = ((rc - rt) - Ru * omc) / max(omc, 1e-9)
    R1 = max(0.0, min(R1_factor * rt, R1_max))
    r_tan1 = rc - R1 * omc
    L_straight = (r_tan1 - r_tan2) / math.tan(beta) if r_tan1 > r_tan2 else 0.0
    return Ru, R1, r_tan1, r_tan2, L_straight


def convergent_geometry(rc: float, rt: float,
                        conv_half_angle_deg: float = 30.0,
                        Ru_factor: float = 1.5,
                        R1_factor: float = 1.5) -> dict:
    """收缩段（圆筒末端→喉部）规范几何标量。

    形状：圆筒(rc) → 入口倒角弧 R1（相切圆筒，C¹ 连续）→ 半锥角 β 直锥
          → 喉部上游圆弧 Ru → 喉部(rt)。

    返回 dict：
      L_conv_m     收缩段总轴向长（入口弧 + 直锥 + 喉部弧）
      V_conv_m3    收缩段旋转体体积（三段 ∫π r² dx 之和）
      L_straight_m 直锥段轴向长
      Ru_m         喉部上游圆弧半径
      R1_m         圆筒→收缩段入口倒角半径（已按几何容许量截断；0=回退尖角）
      r_tangent_m  直锥↔喉部弧 切点半径（下切点）
      r_tangent1_m 圆筒弧↔直锥 切点半径（上切点）
    """
    beta = _beta_rad(conv_half_angle_deg)
    sinb = math.sin(beta)
    Ru, R1, r_tan1, r_tan2, L_straight = _resolve_radii(
        rc, rt, beta, Ru_factor, R1_factor)
    L_conv = R1 * sinb + L_straight + Ru * sinb

    # 体积：入口弧旋转体 + 直锥锥台 + 喉部弧旋转体（各段 ∫π r² dx）
    n = 64
    # 入口弧：x=R1·sinφ, r=rc−R1(1−cosφ), φ:0→β; dV=π r² R1 cosφ dφ
    V_in = 0.0
    for i in range(n):
        phi = beta * (i + 0.5) / n
        r = rc - R1 * (1.0 - math.cos(phi))
        V_in += math.pi * r * r * R1 * math.cos(phi) * (beta / n)
    # 直锥锥台
    V_frustum = (math.pi * L_straight / 3.0) * (
        r_tan1 * r_tan1 + r_tan1 * r_tan2 + r_tan2 * r_tan2)
    # 喉部弧：x=…, r=rt+Ru(1−cosφ), φ:0→β; dV=π r² Ru cosφ dφ
    V_th = 0.0
    for i in range(n):
        phi = beta * (i + 0.5) / n
        r = rt + Ru * (1.0 - math.cos(phi))
        V_th += math.pi * r * r * Ru * math.cos(phi) * (beta / n)

    return dict(L_conv_m=L_conv, V_conv_m3=V_in + V_frustum + V_th,
                L_straight_m=L_straight, Ru_m=Ru, R1_m=R1,
                r_tangent_m=r_tan2, r_tangent1_m=r_tan1,
                beta_deg=conv_half_angle_deg)


def convergent_contour(rc: float, rt: float,
                       conv_half_angle_deg: float = 30.0,
                       Ru_factor: float = 1.5, R1_factor: float = 1.5,
                       x0: float = 0.0,
                       n_in: int = 14, n_straight: int = 20, n_arc: int = 18):
    """收缩段轮廓点 (xs, rs, L_conv)，x0 为圆筒末端轴向坐标，末点落在喉部。

    形状：入口倒角弧 R1（相切圆筒）→ 直锥 β → 喉部上游弧 Ru → 喉部。
    xs 单调增（x0 → x0+L_conv），rs 单调减（rc → rt）；首点=(x0, rc) 起始斜率 0
    （与圆筒相切），末点=(x0+L_conv, rt)，全程 C¹ 连续无尖角。
    """
    beta = _beta_rad(conv_half_angle_deg)
    sinb = math.sin(beta)
    Ru, R1, r_tan1, r_tan2, L_straight = _resolve_radii(
        rc, rt, beta, Ru_factor, R1_factor)
    L_in = R1 * sinb
    L_conv = L_in + L_straight + Ru * sinb

    xs: list[float] = []
    rs: list[float] = []
    # ① 入口倒角弧（φ:0→β，rc→r_tan1），不含末点（由后段补）；R1=0 时跳过，
    #    起点由直锥段 i=0 提供（r_tan1=rc，L_in=0），避免与直锥首点重复。
    if R1 > 1e-12:
        for i in range(n_in):
            phi = beta * (i / n_in)
            xs.append(x0 + R1 * math.sin(phi))
            rs.append(rc - R1 * (1.0 - math.cos(phi)))
    # ② 直锥段（r_tan1→r_tan2），不含末点（由喉部弧补）
    if L_straight > 1e-12:
        for i in range(n_straight):
            f = i / n_straight
            xs.append(x0 + L_in + L_straight * f)
            rs.append(r_tan1 + (r_tan2 - r_tan1) * f)
    # ③ 喉部上游圆弧（φ:β→0，r_tan2→rt），含末点（喉部）
    for i in range(n_arc + 1):
        phi = beta * (1.0 - i / n_arc)
        xs.append(x0 + L_conv - Ru * math.sin(phi))
        rs.append(rt + Ru * (1.0 - math.cos(phi)))
    # 极端退化（R1=0 且 L_straight=0，高 β/低 εc）下首点会落在 r_tan2 而非 rc，补圆筒末端点
    if abs(xs[0] - x0) > 1e-12 or abs(rs[0] - rc) > 1e-9:
        xs.insert(0, x0)
        rs.insert(0, rc)
    return xs, rs, L_conv
