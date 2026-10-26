"""
nozzle.py
=========

拉瓦尔喷管（收缩-扩张喷管）热力学模型

V5 修改记录：
─────────────────────────────────────────────────────────
1. 新增 Cantera 冻结流等熵膨胀路径：
   · 当 chamber_state（含 Cantera 组分）可用时，
     直接用 Cantera 的真实状态方程做 S=const 膨胀，
     不再依赖单一 γ 的理想气体近似
   · 排气速度由焓差直接计算：Ve = √(2·(h_c - h_e))
2. 保留原有的理想气体分析公式（用于 Cantera 不可用时的 fallback，
   以及 c*、Cf 等无量纲参数的输出）
3. 构造函数新增可选参数 chamber_composition，
   由 cycle.py 从 chamber_state 传入
4. 保留 V4 的参数校验

理论说明：
─────────────────────────────────────────────────────────
为什么「真实状态方程膨胀」比「固定 γ 膨胀」更准确？

  固定 γ 假设气体比热不随温度变化，但实际燃气从 ~3600 K 膨胀到
  ~1500 K 的过程中，γ 从约 1.15 增大到约 1.25。
  单一 γ 的偏差来源：
    · 高温端 cp 偏高 → γ 偏低 → 高估膨胀做功
    · 低温端 cp 偏低 → γ 偏高 → 低估膨胀做功
  两端误差方向相反，但不完全抵消，典型偏差 2–5% Isp。

  Cantera 的冻结流膨胀在每个状态点使用局部 cp(T)，
  通过精确 S-P 关系求解出口温度，避免了上述问题。
"""

from __future__ import annotations
from math import sqrt
from typing import Dict, Optional

from core import ComponentResult, G0, R_UNIVERSAL, ValidationError, _check_range
from thermo import CANTERA_AVAILABLE, nozzle_exit_state, nozzle_performance, GasState


# ─── 内部辅助函数（理想气体公式，保留用于 fallback 和诊断） ───

def _area_mach_func(mach: float, gamma: float) -> float:
    t = 1.0 + (gamma - 1.0) / 2.0 * mach ** 2
    exponent = (gamma + 1.0) / (2.0 * (gamma - 1.0))
    return (1.0 / mach) * (2.0 / (gamma + 1.0) * t) ** exponent


def _exit_mach_from_area_ratio(area_ratio: float, gamma: float) -> float:
    lo, hi = 1.001, 25.0
    if _area_mach_func(hi, gamma) < area_ratio:
        hi = 50.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        f_mid = _area_mach_func(mid, gamma)
        if abs(f_mid - area_ratio) / max(area_ratio, 1e-9) < 1e-8:
            return mid
        if f_mid < area_ratio:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _isentropic_pressure_ratio(mach: float, gamma: float) -> float:
    t = 1.0 + (gamma - 1.0) / 2.0 * mach ** 2
    return t ** (-gamma / (gamma - 1.0))


class Nozzle:
    """
    拉瓦尔喷管热力学模型（双路径：Cantera / 理想气体）
    """

    def __init__(
        self,
        pc: float,
        expansion_ratio: float,
        gamma: float,
        molar_mass: float,
        tc: float,
        mdot: float,
        p_amb: float,
        chamber_state: Optional[GasState] = None,
    ):
        """
        参数
        ────
        chamber_state : 来自 thermo.chamber_equilibrium() 的 GasState
                        若提供且为 cantera 来源，则启用 Cantera 膨胀路径
                        若为 None，使用理想气体公式
        """
        _check_range("Nozzle.pc", pc, lo=0, lo_inclusive=False)
        _check_range("Nozzle.expansion_ratio", expansion_ratio, lo=1.0)
        _check_range("Nozzle.gamma", gamma, lo=1.0, lo_inclusive=False)
        _check_range("Nozzle.molar_mass", molar_mass, lo=0, lo_inclusive=False)
        _check_range("Nozzle.tc", tc, lo=0, lo_inclusive=False)
        _check_range("Nozzle.mdot", mdot, lo=0, lo_inclusive=False)
        _check_range("Nozzle.p_amb", p_amb, lo=0)

        self.pc              = pc
        self.expansion_ratio = expansion_ratio
        self.gamma           = gamma
        self.molar_mass      = molar_mass
        self.tc              = tc
        self.mdot            = mdot
        self.p_amb           = p_amb
        self.chamber_state   = chamber_state

    def evaluate(self) -> ComponentResult:
        """
        执行喷管热力学计算。

        优先级：
          1. 若 chamber_state 来自 Cantera → 用冻结流 S=const 膨胀
          2. 否则 → 用理想气体单 γ 公式
        """

        gamma = self.gamma
        r_gas = R_UNIVERSAL / self.molar_mass
        use_cantera = (
            self.chamber_state is not None
            and self.chamber_state.source == "cantera"
        )

        # ── 理想气体公式（始终计算，用于 c* / Cf / Me 等输出）───
        Gamma_func = (2.0 / (gamma + 1.0)) ** ((gamma + 1.0) / (2.0 * (gamma - 1.0)))
        c_star = sqrt(r_gas * self.tc / gamma) / Gamma_func
        mach_exit = _exit_mach_from_area_ratio(self.expansion_ratio, gamma)
        pe_ideal = self.pc * _isentropic_pressure_ratio(mach_exit, gamma)

        cf_momentum_sq = (
            (2.0 * gamma ** 2 / (gamma - 1.0))
            * (2.0 / (gamma + 1.0)) ** ((gamma + 1.0) / (gamma - 1.0))
            * max(1.0 - (pe_ideal / self.pc) ** ((gamma - 1.0) / gamma), 0.0)
        )
        cf_momentum = sqrt(max(cf_momentum_sq, 1e-6))
        cf_pressure = (pe_ideal - self.p_amb) * self.expansion_ratio / max(self.pc, 1e-9)
        cf = cf_momentum + cf_pressure

        throat_area = self.mdot * c_star / max(self.pc, 1e-9)
        exit_area   = throat_area * self.expansion_ratio

        # ── Cantera 路径（冻结流 + 出口压力按面积比自洽求解）──────────
        # 修正(V6.1):
        #   · 旧版借用理想 γ 的 pe_ideal 作出口压力,与真实膨胀不自洽;
        #     现改为 thermo.nozzle_performance 内部按 ρV(p_e)=ṁ/A_t/ε 二分求 p_e。
        #   · 同时输出 *真空* 与 *海平面* 两套 Isp/推力,口径分明:
        #       - 真空比冲 Isp_vac:发动机内禀属性(与高度无关)
        #       - 海平面比冲 Isp_sl:在 p_amb 下的测试结果
        #   · 冻结流:组分锁定,贴近真实喷管有限速率反应"冻结"的实际表现,
        #     且较平衡流保守;平衡流(理论上界)在"热力学对比"页给出。
        if use_cantera:
            perf = nozzle_performance(
                self.chamber_state, self.pc, self.expansion_ratio,
                p_amb=self.p_amb, equilibrium=False,
            )
            if perf is not None:
                ve       = perf["V_exit"]
                pe_real  = perf["p_exit"]
                Te       = perf["T_exit"]
                cstar_eq = perf["Cstar"]
                isp_vac  = perf["Isp_vac"]
                isp_sl   = perf["Isp_sl"]
                cf_vac   = perf["Cf_vac"]
                cf_sl    = perf["Cf_sl"]

                # 喉部面积按真实 C* 反推,再得出口面积(几何一致)
                At_real  = self.mdot * cstar_eq / max(self.pc, 1e-9)
                Ae_real  = At_real * self.expansion_ratio
                thrust_sl  = self.mdot * isp_sl  * G0
                thrust_vac = self.mdot * isp_vac * G0
                exit_state = nozzle_exit_state(self.chamber_state, pe_real)
                cf_momentum_real = ve / cstar_eq
                cf_pressure_real = (pe_real - self.p_amb) * self.expansion_ratio / self.pc

                return ComponentResult(
                    name="nozzle",
                    outputs={
                        # 主求解器/头条"实际推力"用海平面推力(与目标推力同口径)
                        "thrust_N"        : thrust_sl,
                        "Isp_s"           : isp_sl,        # 海平面比冲(测试口径)
                        "thrust_vac_N"    : thrust_vac,
                        "Isp_vac_s"       : isp_vac,       # 真空比冲(发动机内禀属性)
                        "Ve_ms"           : ve,
                        "c_star_ms"       : cstar_eq,
                        "Cf"              : cf_sl,
                        "Cf_vac"          : cf_vac,
                        "Cf_sl"           : cf_sl,
                        "Cf_momentum"     : cf_momentum_real,
                        "Cf_pressure"     : cf_pressure_real,
                        "At_m2"           : At_real,
                        "Ae_m2"           : Ae_real,
                        "mach_exit"       : ve / sqrt(exit_state.gamma * R_UNIVERSAL / exit_state.molar_mass * exit_state.temperature),
                        "p_exit_Pa"       : pe_real,
                        "p_amb_Pa"        : self.p_amb,
                        "expansion_ratio" : self.expansion_ratio,
                        "T_exit_K"        : Te,
                        "T_throat_K"      : perf.get("T_throat"),
                        "p_throat_Pa"     : perf.get("p_throat"),
                        "h_exit_Jkg"      : exit_state.enthalpy,
                        "h_chamber_Jkg"   : self.chamber_state.enthalpy,
                        "energy_residual_W": self.mdot * (exit_state.enthalpy + ve * ve / 2 - self.chamber_state.enthalpy),
                        "flow_model"      : 0.0,           # 0=冻结流, 1=平衡流
                    },
                    notes=[
                        "热力学来源: cantera 冻结流膨胀(出口压力按面积比自洽求解)",
                        f"Ve = {ve:.1f} m/s, T_exit = {Te:.0f} K, pe = {pe_real/1e3:.1f} kPa",
                        f"真空比冲 Isp_vac = {isp_vac:.1f} s(发动机内禀属性)",
                        f"海平面比冲 Isp_sl = {isp_sl:.1f} s(p_amb={self.p_amb/1e3:.1f} kPa 下测试)",
                        f"C* = {cstar_eq:.1f} m/s,Cf_vac = {cf_vac:.4f},Cf_sl = {cf_sl:.4f}",
                        "Expansion: " + (
                            "过膨胀" if pe_real < self.p_amb else
                            "欠膨胀" if pe_real > self.p_amb else "完全膨胀"
                        ),
                    ],
                )

        # ── 理想气体 Fallback ────────────────────────────
        # 海平面 Cf 含背压项;真空 Cf 去掉 p_amb(用 pe 全量压力项)
        thrust = self.mdot * c_star * cf            # 海平面推力
        isp    = thrust / max(self.mdot * G0, 1e-9)
        cf_vac = cf_momentum + pe_ideal * self.expansion_ratio / max(self.pc, 1e-9)
        thrust_vac = self.mdot * c_star * cf_vac
        isp_vac    = thrust_vac / max(self.mdot * G0, 1e-9)

        return ComponentResult(
            name="nozzle",
            outputs={
                "thrust_N"        : thrust,
                "Isp_s"           : isp,            # 海平面比冲
                "thrust_vac_N"    : thrust_vac,
                "Isp_vac_s"       : isp_vac,        # 真空比冲(内禀属性)
                "c_star_ms"       : c_star,
                "Cf"              : cf,
                "Cf_vac"          : cf_vac,
                "Cf_sl"           : cf,
                "Cf_momentum"     : cf_momentum,
                "Cf_pressure"     : cf_pressure,
                "At_m2"           : throat_area,
                "Ae_m2"           : exit_area,
                "mach_exit"       : mach_exit,
                "p_exit_Pa"       : pe_ideal,
                "p_amb_Pa"        : self.p_amb,
                "expansion_ratio" : self.expansion_ratio,
                "flow_model"      : -1.0,           # -1=理想气体 fallback
            },
            notes=[
                f"热力学来源: 理想气体（固定 γ={gamma:.4f}）",
                f"Me = {mach_exit:.3f}, pe = {pe_ideal / 1e3:.2f} kPa",
                f"真空比冲 Isp_vac = {isp_vac:.1f} s,海平面比冲 Isp_sl = {isp:.1f} s",
                "Expansion: " + (
                    "过膨胀" if pe_ideal < self.p_amb else
                    "欠膨胀" if pe_ideal > self.p_amb else "完全膨胀"
                ),
            ],
        )
