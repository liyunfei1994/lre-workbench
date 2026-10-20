"""
grcop_properties.py — GRCop-84 随温度变化物性（NASA 手册回归式，单一来源）

数据来源（唯一）：
  David L. Ellis, *Aerospace Structural Materials Handbook Supplement: GRCop-84*,
  Case Western Reserve University / NASA Glenn Research Center.
  （Cu-8at%Cr-4at%Nb 弥散强化铜；为再生冷却火箭推力室内衬研发。）

把"材料=一个常数"升级为"材料=随温度变化的函数"。手册给出的是带置信区间的回归式，
本模块取**回归模型均值**（best estimate，即丢弃 −t(1−α,ν)×SE 的置信带项）。设计若要
保守，可对屈服/抗拉再乘一个 knockdown（见 LOWER_95_KNOCKDOWN_*）。

⚠ 适用范围与诚实标注
  · k(T)   : 手册导热式基于**高 Fe(150–250 ppm)**粉末批；手册明确指出低 Fe(120 ppm)
            现代料在 25–350°C 区导热显著更高（RT 约 340–350 vs 此式 ~285）。本式偏
            保守(低导热→高壁温)，用作基准；现代低 Fe 料可上修 ~15–20%。
  · σy/UTS : 手册给 as-extruded 与 as-HIPed 两态闭式；默认用 **extruded**（强度上界、
            数据最全，且与本库历史 σy_hot=130 MPa@773K 自洽）。AM(L-PBF)态手册未覆盖,
            其值大致落在 extruded 与 HIPed 之间，需另行试棒标定。
  · α(T),E : 手册本版 §3.6 弹性性能为空表、热膨胀闭式 OCR 不可靠；故 CTE 取 ~16.5→19
            ppm/K 的工程近似、E 取 ~130 GPa 轻微软化的工程近似（**非手册闭式**，标注 approx）。
  · 有效温区约 −193…900°C（80…1173 K）；超域按端值夹持(clamp)并不外推回归式。
"""

from __future__ import annotations

import math

# 手册回归式的有效温度区间 [K]（超出按端值夹持）
T_MIN_K = 80.0
T_MAX_K = 1173.0     # ~900°C，手册热物性上限

# 95% 下置信限相对均值的近似 knockdown（σy/UTS，供保守设计可选乘用）
#   extruded: t(0.95,31)≈1.696, SE≈10.92 MPa → 约 −18.5 MPa（随温变化小，这里给比例近似）
LOWER_95_KNOCKDOWN_YIELD_MPa = 18.5
LOWER_95_KNOCKDOWN_UTS_MPa = 37.0     # t(0.95,32)≈1.694 × SE≈21.92


def _clamp_T(T_K: float) -> float:
    return max(T_MIN_K, min(T_MAX_K, float(T_K)))


def thermal_conductivity(T_K: float) -> float:
    """导热系数 k(T) [W/(m·K)]。手册 §2.1.3（高 Fe，回归均值）。

    k(T) = 6893 − 3466·lnT + 599.5·(lnT)² − 34.18·(lnT)³   （T in K）
    量级：~285(300K) → ~305(773K) → ~293(1000K)，受 Fe 散射而较平。
    """
    T = _clamp_T(T_K)
    lnT = math.log(T)
    return 6893.0 - 3466.0 * lnT + 599.5 * lnT * lnT - 34.18 * lnT ** 3


def specific_heat(T_K: float) -> float:
    """比热 cp(T) [J/(kg·K)]。手册 §2.1.5（回归均值，原式 J/g·K ×1000）。

    cp(T) = 0.2539 + 6.563e-4·T − 8.903e-7·T² + 4.292e-10·T³   [J/g·K]
    量级：~382(300K) → ~428(773K) → ~436(900K)。
    """
    T = _clamp_T(T_K)
    cp_JgK = 0.2539 + 6.563e-4 * T - 8.903e-7 * T * T + 4.292e-10 * T ** 3
    return cp_JgK * 1000.0


def yield_strength(T_K: float, process: str = "extruded") -> float:
    """0.2% 屈服强度 σy(T) [MPa]。手册 §3.3.1（as-processed，回归均值）。

    extruded: 297.4 − 0.312·T + 3.175e-4·T² − 2.506e-7·T³
    HIPed   : 155.3 + 0.0686·T − 1.70e-4·T²
    量级(extruded)：~226(300K) → 130(773K) → ~91(900K)。
    """
    T = _clamp_T(T_K)
    if process.lower().startswith("hip"):
        sy = 155.3 + 0.0686 * T - 1.70e-4 * T * T
    else:
        sy = 297.4 - 0.312 * T + 3.175e-4 * T * T - 2.506e-7 * T ** 3
    return max(sy, 1.0)


def ultimate_strength(T_K: float, process: str = "extruded") -> float:
    """抗拉强度 UTS(T) [MPa]。手册 §3.3.1（as-processed，回归均值）。

    extruded: 664.5 − 0.987·T + 3.850e-4·T²
    HIPed   : 454.4 − 0.420·T + 3.33e-4·T²
    """
    T = _clamp_T(T_K)
    if process.lower().startswith("hip"):
        uts = 454.4 - 0.420 * T + 3.33e-4 * T * T
    else:
        uts = 664.5 - 0.987 * T + 3.850e-4 * T * T
    return max(uts, 1.0)


def cte(T_K: float) -> float:
    """线膨胀系数 α(T) [1e-6/K]。⚠ 工程近似（手册热膨胀闭式 OCR 不可靠）。

    采用 GRCop-84/铜合金量级：~16.5(RT) 缓升到 ~19(800°C)，夹持 [15.5, 19.5]。
    """
    T = _clamp_T(T_K)
    val = 16.5 + 0.0036 * (T - 293.0)
    return max(15.5, min(19.5, val))


def youngs_modulus(T_K: float) -> float:
    """弹性模量 E(T) [GPa]。⚠ 工程近似（手册本版 §3.6 弹性性能为空表）。

    GRCop-84 RT ~130 GPa，按铜合金量级随温缓降到 ~110(800°C)，夹持 [100, 135]。
    """
    T = _clamp_T(T_K)
    val = 130.0 - 0.025 * (T - 293.0)
    return max(100.0, min(135.0, val))


def lcf_life_cycles(strain_range: float) -> float:
    """低周疲劳寿命 N_f [cycles]。手册 §3.5（as-HIPed，回归均值）。

    N_f = exp(−2.271·ln(Δε) − 2.32) = e^(−2.32) · Δε^(−2.271)
    Δε：全反向应变范围 [mm/mm]。量级：1%→~3400 cyc，2%→~700 cyc。
    可重复使用推力室内衬 doghouse/热棘轮寿命的一阶判据。
    """
    de = max(float(strain_range), 1e-6)
    return math.exp(-2.271 * math.log(de) - 2.32)


# 室温密度（手册 §2.2.1）：8.62 ± 0.08 g/cm³
DENSITY_KGM3 = 8620.0


if __name__ == "__main__":  # 自检：对照手册量级
    print("T[K]   k[W/mK]  cp[J/kgK]  σy_ext  σy_HIP  UTS_ext   α[ppm]  E[GPa]")
    for T in (200, 300, 500, 773, 923, 1073):
        print("%5d  %7.1f  %8.0f  %6.0f  %6.0f  %7.0f  %6.1f  %6.0f" % (
            T, thermal_conductivity(T), specific_heat(T),
            yield_strength(T, "extruded"), yield_strength(T, "hip"),
            ultimate_strength(T, "extruded"), cte(T), youngs_modulus(T)))
    print("LCF: 1%%->%.0f  2%%->%.0f cycles" % (lcf_life_cycles(0.01), lcf_life_cycles(0.02)))
