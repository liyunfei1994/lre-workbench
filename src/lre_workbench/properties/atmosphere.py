# -*- coding: utf-8 -*-
"""
标准大气模型 (U.S. Standard Atmosphere 1976) —— 供高度特性曲线使用。

提供：给定几何高度 z[m] → 环境压力 P_a[Pa]、温度 T[K]、密度 ρ[kg/m³]。
覆盖 0 ~ 86 km（geopotential 0~84.852 km）共 7 个分层；86 km 以上压力已趋近真空，
统一外推/截断到极小值，足够火箭发动机高度特性（推力/比冲随高度）使用。

参考：U.S. Standard Atmosphere, 1976, NOAA/NASA/USAF.
公式（分层重力位势高度 h）：
    L≠0:  P = Pb · [Tb / (Tb + L·(h−hb))] ^ ( g0·M / (R*·L) )
    L=0:  P = Pb · exp[ −g0·M·(h−hb) / (R*·Tb) ]
"""
from __future__ import annotations
import math
from typing import List, Tuple

# ── 物理常数（US Std Atm 1976 取值）──────────────────────────────────────
_G0 = 9.80665           # 标准重力加速度 [m/s²]
_M  = 0.0289644         # 干空气摩尔质量 [kg/mol]
_R  = 8.3144598         # 通用气体常数 [J/(mol·K)]
_RE = 6356766.0         # 地球有效半径（重力位势用）[m]
_GMR = _G0 * _M / _R    # ≈ 0.034163  [K/m 的指数系数基]

# ── 分层表：(基准重力位势高度 hb[m], 基准温度 Tb[K], 温度梯度 L[K/m], 基准压力 Pb[Pa]) ──
_LAYERS: List[Tuple[float, float, float, float]] = [
    (0.0,      288.15,  -0.0065,  101325.0),
    (11000.0,  216.65,   0.0,      22632.06),
    (20000.0,  216.65,   0.001,     5474.889),
    (32000.0,  228.65,   0.0028,     868.0187),
    (47000.0,  270.65,   0.0,        110.9063),
    (51000.0,  270.65,  -0.0028,      66.93887),
    (71000.0,  214.65,  -0.002,        3.956420),
]
_H_TOP = 84852.0        # 顶层重力位势高度上限 [m]


def _geopotential(z_m: float) -> float:
    """几何高度 z[m] → 重力位势高度 h[m]。"""
    return _RE * z_m / (_RE + z_m)


def _layer_for(h: float) -> Tuple[float, float, float, float]:
    layer = _LAYERS[0]
    for L in _LAYERS:
        if h >= L[0]:
            layer = L
        else:
            break
    return layer


def temperature_at_geopotential(h: float) -> float:
    hb, Tb, L, _Pb = _layer_for(h)
    return Tb + L * (h - hb)


def pressure_at_altitude(z_m: float) -> float:
    """几何高度 z[m] → 环境压力 P_a[Pa]。z≤0 取海平面；超出 86km 截断到极小值。"""
    if z_m <= 0.0:
        return _LAYERS[0][3]
    h = _geopotential(z_m)
    if h >= _H_TOP:
        # 86 km 以上：用顶层等温外推，封顶到极小压力（≈真空）
        hb, Tb, L, Pb = _LAYERS[-1]
        T_top = Tb + L * (_H_TOP - hb)
        P_top = _pressure_in_layer(_H_TOP, hb, Tb, L, Pb)
        P = P_top * math.exp(-_G0 * _M * (h - _H_TOP) / (_R * T_top))
        return max(P, 1e-3)
    hb, Tb, L, Pb = _layer_for(h)
    return _pressure_in_layer(h, hb, Tb, L, Pb)


def _pressure_in_layer(h: float, hb: float, Tb: float, L: float, Pb: float) -> float:
    if abs(L) < 1e-12:
        return Pb * math.exp(-_G0 * _M * (h - hb) / (_R * Tb))
    return Pb * (Tb / (Tb + L * (h - hb))) ** (_GMR / L)


def temperature_at_altitude(z_m: float) -> float:
    """几何高度 z[m] → 温度 T[K]。"""
    if z_m <= 0.0:
        return _LAYERS[0][1]
    h = min(_geopotential(z_m), _H_TOP)
    return temperature_at_geopotential(h)


def density_at_altitude(z_m: float) -> float:
    """几何高度 z[m] → 密度 ρ[kg/m³]（理想气体）。"""
    P = pressure_at_altitude(z_m)
    T = max(temperature_at_altitude(z_m), 1.0)
    return P * _M / (_R * T)


def altitude_for_pressure(p_pa: float) -> float:
    """反查：给定环境压力 P_a[Pa] → 几何高度 z[m]（用于"最佳膨胀高度"标注）。
    单调二分，0~86km 范围。超界返回边界。"""
    p_pa = float(p_pa)
    if p_pa >= _LAYERS[0][3]:
        return 0.0
    if p_pa <= pressure_at_altitude(86000.0):
        return 86000.0
    lo, hi = 0.0, 86000.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if pressure_at_altitude(mid) > p_pa:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def altitude_grid(z_max_m: float = 80000.0, n: int = 60) -> List[float]:
    """生成 0~z_max 的高度采样点 [m]。"""
    n = max(2, int(n))
    return [z_max_m * i / (n - 1) for i in range(n)]


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    # 与 US Std Atm 1976 表对照（粗校）
    checks = [
        (0,      101325.0),
        (5000,    54048.0),
        (11000,   22632.0),
        (20000,    5474.9),
        (32000,     868.0),
        (47000,     110.9),
        (71000,       3.956),
    ]
    print(f"{'z[m]':>8} {'P_calc[Pa]':>14} {'P_ref[Pa]':>12} {'err%':>8}")
    for z, pref in checks:
        pc = pressure_at_altitude(float(z))
        err = (pc - pref) / pref * 100.0
        print(f"{z:8d} {pc:14.4f} {pref:12.4f} {err:8.3f}")
    print(f"\n反查 1 atm → z = {altitude_for_pressure(101325.0):.1f} m")
    print(f"反查 0.5 atm → z = {altitude_for_pressure(50662.5):.1f} m")
