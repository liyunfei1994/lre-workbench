"""
turbine.py

涡轮模型（V9 — 从 preburner.py 中独立出来）

V9 修改记录：
─────────────────────────────────────────────────────────
1. 把 V5 时合并在 preburner.Preburner 内的"涡轮等熵膨胀"逻辑拆出，
   形成独立 Turbine 类。
2. preburner.Preburner 只负责 HP 化学平衡 → 产出 GasState（涡轮入口状态）。
3. Turbine 类接收 GasState + 排气压力 + 等熵效率，调用
   thermo.turbine_expansion()（当前为 Cantera 理想气体混合物 / 经验回退）
   产出涡轮排气 FlowState 与 ComponentResult。
4. 行为与 V8 完全等价（cycle.py 的求解残差和数值结果不变），
   只是物理对象的边界更清晰，为后续做 CFTurbo 联调留出接口。

设计动机：
─────────────────────────────────────────────────────────
* 预燃室是一个"化学反应器"（HP 平衡），涡轮是一个"气动机器"（等熵膨胀）；
  两者物理本质不同，应当对应不同的代码对象。
* 拆开之后，下一步要做"涡轮单级 / 多级 / 变工况 / CFD 接口"时，
  只需扩展 turbine.py，不会触碰预燃室与系统级求解器。
* CFTurbo 输入提取 (cp, γ, R, MW, π_t, T_in, mdot, 功率) 全部从
  Turbine.evaluate() 的返回结果中读取，集中维护。
"""

from __future__ import annotations

import math
from typing import Literal

from core import ComponentResult, FlowState, ValidationError, _check_range
from thermo import GasState, turbine_expansion


class Turbine:
    """
    单/多级等熵涡轮模型（V10：贯通转速、U/C0、材料校核）。

    V10 修改：
    ────
      · 新增 shaft_speed_rpm（必传）：与同轴泵共用；这是把"涡轮转速"
        从一个"几何模块猜的值"提升为"系统级一等公民"的关键改动。
      · 新增 u_over_c_target / reaction_degree / n_stages_user：
        气动设计目标；之前硬编码在 geometry.py，现在交给 InputParameters。
      · 新增 side / rotor_material / t_in_limit_K：
        触发 materials.py 中的氧兼容性 + 温度上限 + 离心应力校核。
      · evaluate() 输出大量新键，专供 CFTurbo 导出和机械审计层使用。

    输入：
    ────
        name              ：涡轮名
        inlet_state       ：涡轮入口热燃气状态
        exhaust_pressure  ：涡轮排气压力 [Pa]
        eta_turbine       ：等熵效率 [-]
        mass_flow         ：通过涡轮的质量流量 [kg/s]
        shaft_speed_rpm   ：轴转速 [rpm]（与同轴泵共用）
        u_over_c_target   ：目标速比（反动级 0.45–0.65）
        reaction_degree   ：目标反动度（反动级 0.30–0.50）
        n_stages_user     ：用户指定级数；0 = 自动按 U/C0 选
        rotor_material    ：动叶材料 key（materials.MATERIALS 中）
        side              ："ox" 或 "fuel"，决定氧兼容性校核
        t_in_limit_K      ：入口温度硬上限 [K]，超出报警

    输出：
    ────
        FlowState         ：涡轮排气流动状态
        ComponentResult   ：含 π_t、Δh_s、功率、U/C0、级数建议、材料安全系数
    """

    def __init__(
        self,
        name: str,
        inlet_state: GasState,
        exhaust_pressure: float,
        eta_turbine: float,
        mass_flow: float,
        # ── V10 新增（默认 None / 0 → 走向后兼容路径）────
        shaft_speed_rpm: float | None = None,
        u_over_c_target: float = 0.55,
        reaction_degree: float = 0.45,
        n_stages_user: int = 0,
        rotor_material: str | None = None,
        side: Literal["ox", "fuel"] | None = None,
        t_in_limit_K: float | None = None,
        u_max_ms: float = 450.0,
    ):
        _check_range(f"{name}.eta_turbine（涡轮等熵效率）",
                     eta_turbine, lo=0, hi=1.0, lo_inclusive=False)
        _check_range(f"{name}.exhaust_pressure（涡轮排气压力）",
                     exhaust_pressure, lo=0, lo_inclusive=False)
        _check_range(f"{name}.mass_flow（涡轮质量流量）",
                     mass_flow, lo=0, lo_inclusive=False)
        if exhaust_pressure >= inlet_state.pressure:
            raise ValidationError(
                f"{name}: exhaust_pressure={exhaust_pressure:.0f} Pa "
                f">= inlet_pressure={inlet_state.pressure:.0f} Pa，"
                f"涡轮排气压力必须严格低于入口压力才能做功"
            )
        if shaft_speed_rpm is not None:
            _check_range(f"{name}.shaft_speed_rpm（涡轮转速）",
                         shaft_speed_rpm, lo=1000.0, hi=200000.0)
        _check_range(f"{name}.u_over_c_target（目标速比）",
                     u_over_c_target, lo=0.05, hi=0.95)
        _check_range(f"{name}.reaction_degree（反动度）",
                     reaction_degree, lo=0.0, hi=0.8)

        self.name             = name
        self.inlet_state      = inlet_state
        self.exhaust_pressure = exhaust_pressure
        self.eta_turbine      = eta_turbine
        self.mass_flow        = mass_flow
        # V10
        self.shaft_speed_rpm  = shaft_speed_rpm
        self.u_over_c_target  = u_over_c_target
        self.reaction_degree  = reaction_degree
        self.n_stages_user    = int(n_stages_user)
        self.rotor_material   = rotor_material
        self.side             = side
        self.t_in_limit_K     = t_in_limit_K
        self.u_max_ms         = u_max_ms

    def evaluate(self) -> tuple[FlowState, ComponentResult]:
        """
        执行等熵膨胀计算 + V10 几何/材料校核。

        步骤：
        1. 调用 thermo.turbine_expansion() 计算 Δh_s, Δh_actual, T_exhaust, h_exhaust
        2. 计算涡轮功率 P = ṁ · Δh_actual
        3. （V10）按 shaft_speed_rpm + U/C0_target 估算 D_mean、U_mean
        4. （V10）按 U/C0_actual 推荐级数
        5. （V10）查 materials.py 算离心应力、对比材料 σy(T) → 安全系数
        6. （V10）富氧侧硬校验 oxygen_compatible + T_in vs T_max_use_K
        """
        turb = turbine_expansion(
            combustion_state=self.inlet_state,
            exhaust_pressure=self.exhaust_pressure,
            eta_turbine=self.eta_turbine,
        )

        delta_h_s      = turb["isentropic_enthalpy_drop"]
        delta_h_actual = turb["actual_enthalpy_drop"]
        t_exhaust      = turb["T_exhaust"]
        h_exhaust      = turb["h_exhaust"]
        composition    = turb["composition"]
        turbine_power  = self.mass_flow * delta_h_actual

        T_in    = self.inlet_state.temperature
        p_in    = self.inlet_state.pressure
        pi_t    = p_in / max(self.exhaust_pressure, 1.0)
        rec_ratio = self.exhaust_pressure / max(p_in, 1.0)
        actual_temp_drop = T_in - t_exhaust

        # ── V10：几何与速度三角形 ─────────────────────────
        # 等熵速度 C0 = sqrt(2·Δh_s)（spouting velocity）
        C0 = math.sqrt(max(2.0 * delta_h_s, 1.0))
        geom_notes: list[str] = []
        geom_outputs: dict[str, float] = {}

        if self.shaft_speed_rpm is not None:
            omega = self.shaft_speed_rpm * 2.0 * math.pi / 60.0
            U_mean_target = self.u_over_c_target * C0
            D_mean_ideal  = 2.0 * U_mean_target / max(omega, 1.0)
            D_mean        = max(0.05, min(0.80, D_mean_ideal))
            U_mean        = 0.5 * D_mean * omega
            u_over_c_actual = U_mean / max(C0, 1.0)

            # 级数推荐（SP-8110 §4.4 + 图 14）
            if self.n_stages_user >= 1:
                n_stages = self.n_stages_user
                stage_src = "用户指定"
            else:
                if u_over_c_actual < 0.20:
                    n_stages, stage_src = 3, "U/C0<0.20 → 速度复合 3 级"
                elif u_over_c_actual < 0.35:
                    n_stages, stage_src = 2, "0.20≤U/C0<0.35 → 双列冲动 2 级"
                elif u_over_c_actual < 0.50:
                    n_stages, stage_src = 2, "0.35≤U/C0<0.50 → 单列冲动 / 2 级反动"
                else:
                    n_stages, stage_src = 1, "U/C0≥0.50 → 单级反动足够"

            tip_ok = U_mean <= self.u_max_ms

            geom_outputs.update({
                "C0_spouting_ms":   C0,
                "U_mean_target_ms": U_mean_target,
                "U_mean_actual_ms": U_mean,
                "D_mean_m":         D_mean,
                "u_over_c_target":  self.u_over_c_target,
                "u_over_c_actual":  u_over_c_actual,
                "n_stages_recommended": float(n_stages),
                "reaction_degree":  self.reaction_degree,
                "shaft_speed_rpm":  self.shaft_speed_rpm,
                "u_max_ms":         self.u_max_ms,
                "u_mean_ok":        1.0 if tip_ok else 0.0,
            })
            geom_notes += [
                "── 速度三角形 & 几何（V10）─────────────────",
                f"  C0(等熵喷出速度) = sqrt(2·Δh_s) = {C0:.0f} m/s",
                f"  转速 {self.shaft_speed_rpm:.0f} rpm，目标 U/C0 = {self.u_over_c_target:.2f}",
                f"  D_mean ≈ {D_mean*1000:.0f} mm，U_mean = {U_mean:.0f} m/s "
                    f"({'✓' if tip_ok else f'⚠ 超过 u_max={self.u_max_ms:.0f} m/s'})",
                f"  实际 U/C0 = {u_over_c_actual:.3f}",
                f"  级数建议 = {n_stages}（{stage_src}）",
            ]

            # ── V10：材料校核 ─────────────────────────────
            if self.rotor_material is not None:
                try:
                    from materials import material_audit_row, get_material  # noqa
                    audit = material_audit_row(
                        material_key=self.rotor_material,
                        T_K=T_in,
                        U_ms=U_mean,
                        side=(self.side or "fuel"),
                    )
                    # 注：材料名是字符串，不能进 outputs（ComponentResult 限 float）。
                    # 改用 result.notes 末尾"材料 key="行 + cycle.inputs 引用，
                    # UI 渲染时从两边都能拿到。
                    geom_outputs.update({
                        "rotor_sigma_yield_T_MPa": audit["sigma_yield_T_MPa"],
                        "rotor_sigma_centrif_MPa": audit["sigma_centrifugal_MPa"],
                        "rotor_safety_factor":     audit["safety_factor"],
                    })
                    geom_notes.append(
                        f"── 动叶材料：{audit['material_name']} ─────"
                    )
                    geom_notes.append(
                        f"  σy(T_in={T_in:.0f}K) = {audit['sigma_yield_T_MPa']:.0f} MPa； "
                        f"σ_离心(ρ·U²) = {audit['sigma_centrifugal_MPa']:.0f} MPa； "
                        f"SF = {audit['safety_factor']:.2f}"
                    )
                    for iss in audit["issues"]:
                        geom_notes.append(f"  {iss}")
                except (ImportError, KeyError, ValueError) as ex:
                    geom_notes.append(f"  ⚠ 材料校核跳过：{ex}")

            # ── V10：入口温度硬约束 ───────────────────────
            if self.t_in_limit_K is not None and T_in > self.t_in_limit_K:
                geom_notes.append(
                    f"  ⚠ 入口温度 T_in={T_in:.0f}K 超过用户设定上限 "
                    f"{self.t_in_limit_K:.0f}K"
                )
                geom_outputs["t_in_over_limit"] = T_in - self.t_in_limit_K

        # 涡轮排气流动状态（供主燃烧室混合用）
        state = FlowState(
            name=f"{self.name}_exhaust",
            pressure=self.exhaust_pressure,
            temperature=t_exhaust,
            mass_flow=self.mass_flow,
            enthalpy=h_exhaust,
            composition=composition,
            metadata={
                "t_inlet_K": T_in,
                "pi_t":      pi_t,
                "thermo_source": 1.0 if self.inlet_state.source == "cantera" else 0.0,
            },
        )

        outputs = {
            "p_in_Pa": p_in,
            "p_out_Pa": self.exhaust_pressure,
            "turbine_pressure_ratio":  pi_t,
            "pressure_recovery_ratio": rec_ratio,
            "T_in_K":      T_in,
            "T_exhaust_K": t_exhaust,
            "isentropic_drop_Jkg":  delta_h_s,
            "actual_drop_Jkg":      delta_h_actual,
            "actual_temp_drop_K":   actual_temp_drop,
            "turbine_power_W":   turbine_power,
            "turbine_power_MW":  turbine_power / 1e6,
            "eta_turbine":       self.eta_turbine,
            "mass_flow_kgs":     self.mass_flow,
            # —— 为 CFTurbo 输入预先暴露的工质参数（V9）——
            "cp_in_Jkgk":     self.inlet_state.cp,
            "gamma_in":       self.inlet_state.gamma,
            "molar_mass_in":  self.inlet_state.molar_mass,
        }
        outputs.update(geom_outputs)

        notes = [
            f"涡轮压比 π_t = p_in/p_out = {pi_t:.4f}",
            f"压力恢复比 p_out/p_in = {rec_ratio:.4f}",
            f"等熵焓降 Δh_s = {delta_h_s / 1e3:.1f} kJ/kg",
            f"实际焓降 Δh_a = {delta_h_actual / 1e3:.1f} kJ/kg",
            f"实际温降 ΔT = {actual_temp_drop:.1f} K (T_in={T_in:.0f} K -> T_out={t_exhaust:.0f} K)",
            f"涡轮功率 P = {turbine_power / 1e6:.3f} MW",
        ] + geom_notes

        result = ComponentResult(
            name=self.name,
            outputs=outputs,
            notes=notes,
        )
        return state, result
