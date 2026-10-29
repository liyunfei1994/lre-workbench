"""
cfturbo_export.py

把数字样机求解结果里的四个叶轮机械（液氧泵 / 甲烷泵 / 富氧涡轮 / 富燃涡轮）
设计点，整理成一份 *CFTurbo 录入参考* 的 Markdown 文档。

所有量纲已换算到 CFTurbo 的常用显示单位：
  · 体积流量 Q [m³/h]   · 温度 [°C]   · 压力 [bar]   · 功率 [kW]
  · 摩尔质量 M [kg/mol]（注意：CFTurbo 是 kg/mol，不是 kg/kmol！）
  · 涡轮总态口径只采用组件保存的显式 state_basis 声明

泵的流体物性（μ/ν/λ/pv）在导出时用 CoolProp 在入口工况实算（若可用）。
"""

from __future__ import annotations

import math
import re
from typing import Optional
from propellants import get_propellant

try:
    from CoolProp.CoolProp import PropsSI as _PROPSSI
    _CP_OK = True
except Exception:
    _CP_OK = False

_R_UNIV = 8314.462  # J/(kmol·K)
_K = 273.15


def _fluid_props(coolprop_name: str, T_K: float, p_Pa: float) -> Optional[dict]:
    """入口流体物性（CoolProp），失败返回 None。"""
    if not _CP_OK or not coolprop_name or not math.isfinite(T_K) or not math.isfinite(p_Pa):
        return None
    try:
        rho = _PROPSSI("D", "T", T_K, "P", p_Pa, coolprop_name)
        mu = _PROPSSI("V", "T", T_K, "P", p_Pa, coolprop_name)
        lam = _PROPSSI("L", "T", T_K, "P", p_Pa, coolprop_name)
        cp = _PROPSSI("C", "T", T_K, "P", p_Pa, coolprop_name)
        try:
            pv = _PROPSSI("P", "T", T_K, "Q", 0, coolprop_name)
        except Exception:
            pv = float("nan")
        return {"rho": rho, "mu": mu, "nu": mu / rho * 1e6, "lam": lam, "cp": cp, "pv": pv}
    except Exception:
        return None


def _g(d: dict, k: str, default: float = float("nan")) -> float:
    v = d.get(k, default)
    try:
        value = float(v)
        return value if math.isfinite(value) and not isinstance(v, bool) else default
    except (TypeError, ValueError, OverflowError):
        return default


def _show_missing(text: str) -> str:
    """Keep missing numerical cells explicit after formatting/conversion."""
    return re.sub(r"(?<![\w.])nan(?![\w.])", "未记录／待确认", text)


def _pump_block(title: str, fluid_cn: str, coolprop_name: str, molar_kg_per_mol: float,
                o: dict, T_in_K: float, mdot_kg_s: float) -> str:
    Q_m3h = _g(o, "volume_flow_m3h")
    if not math.isfinite(Q_m3h):
        Q_m3h = _g(o, "volume_flow_m3s") * 3600.0
    p_in_bar = _g(o, "p_in_bar")
    p_out_bar = _g(o, "p_out_bar")
    T_out_K = _g(o, "T_out_K")
    npsha_m = _g(o, "npsh_available_m")   # pump.py 已按 (p_in-p_vap)/(ρ·g) 报出压头[m]
    pr = _fluid_props(coolprop_name, T_in_K, p_in_bar * 1e5)

    lines = [
        f"### {title}",
        "",
        f"**CFTurbo 机器类型**：Pump　**工质**：{fluid_cn}；CoolProp 名称：{coolprop_name or '待确认'}",
        "入口 P/T 为保存的系统节点状态；总压／静压口径及诱导轮方案待确认。",
        "",
        "| CFTurbo 字段 | 数值 | 单位 |",
        "|---|---|---|",
        f"| Mass flow ṁ | {mdot_kg_s:.1f} | kg/s |",
        f"| Volume flow Q | {Q_m3h:.0f} | m³/h |",
        f"| Head H | {_g(o,'head_m'):.0f} | m |",
        f"| 节点压升 Δp | {_g(o,'delta_p_bar'):.1f} | bar |",
        f"| Revolutions n | {_g(o,'nr_rpm'):.0f} | /min |",
        f"| Stages（级数） | {_g(o,'n_stages'):.0f} | — |",
        f"| 入口节点压力 p | {p_in_bar:.2f} | bar |",
        f"| 入口节点温度 T | {T_in_K - _K:.2f} | °C |",
        f"| 系统泵效率 η（口径待核对） | {_g(o,'eta'):.3f} | — |",
        "",
        "**入口/出口流动状态**",
        "",
        "| | 入口 | 出口 |",
        "|---|---|---|",
        f"| 压力 [bar] | {p_in_bar:.2f} | {p_out_bar:.1f} |",
        f"| 温度 [°C] | {T_in_K - _K:.2f} | {T_out_K - _K:.2f} |",
        f"| 温度 [K] | {T_in_K:.1f} | {T_out_K:.1f} |",
        f"| 密度 [kg/m³] | {_g(o,'rho_in_kgm3'):.1f} | {_g(o,'rho_out_kgm3'):.1f} |",
        "",
        "**比转速 / 汽蚀 / 叶尖**",
        "",
        "| 量 | 值 |",
        "|---|---|",
        f"| 比转速 nq(EU)（对标 CFTurbo） | {_g(o,'specific_speed_Ns'):.1f} |",
        f"| 叶尖速度 u_tip [m/s] | {_g(o,'u_tip_est_ms'):.0f}（上限 {_g(o,'tip_speed_limit_ms'):.0f}） |",
        f"| 叶轮直径估算 D_imp [mm] | {_g(o,'d_impeller_est_m')*1000:.0f} |",
        f"| 压头系数 ψ | {_g(o,'psi_head_coefficient'):.2f} |",
        f"| NPSHa [m] | {npsha_m:.1f} |",
        f"| 吸入比转速 nss(EU)（对标 CFTurbo 150–400，诱导轮级） | {_g(o,'suction_specific_speed_nss_eu'):.0f} |",
        f"| 吸入比转速 Nss(US) | {_g(o,'suction_specific_speed_Nss'):.0f} |",
    ]
    # 流体物性（CFTurbo Fluid 录入）
    lines += [
        "",
        f"**{fluid_cn} 流体物性（CFTurbo Fluid 录入，@ 入口 {T_in_K - _K:.2f} °C / {p_in_bar:.2f} bar）**",
        "",
        "| 字段 | 值 | 单位 |",
        "|---|---|---|",
    ]
    if pr:
        lines += [
            f"| Density ρ | {pr['rho']:.1f} | kg/m³ |",
            f"| Dynamic viscosity μ | {pr['mu']:.6f} | Pa·s |",
            f"| Kinematic viscosity ν | {pr['nu']:.5f} | mm²/s |",
            f"| Heat capacity cp | {pr['cp']:.1f} | J/(kg·K) |",
            f"| Thermal conductivity λ | {pr['lam']:.5f} | W/(m·K) |",
            f"| **Molar weight M** | {molar_kg_per_mol:.6f} | **kg/mol** |",
            f"| Vapor pressure pv | {pr['pv']/1e5:.5f} | bar |",
        ]
    else:
        lines.append("| 物性 | 未记录／待确认（物性库、工质或入口工况不可用） | — |")
    lines.append("")
    return _show_missing("\n".join(lines))


def _turbine_block(title: str, gas_cn: str, o: dict) -> str:
    Tt_in_C = _g(o, "T_in_K") - _K
    pt_in_bar = _g(o, "p_in_Pa") / 1e5
    p_out_bar = _g(o, "p_out_Pa") / 1e5
    mw_kmol = _g(o, "molar_mass_in")
    if mw_kmol <= 0:
        mw_kmol = float("nan")
    R = _R_UNIV / mw_kmol
    M_kg_mol = mw_kmol / 1000.0
    tt = o.get("state_basis") == "total_to_total"
    ratio_label = "Total-to-total pressure ratio πtt" if tt else "节点压力比 p_in/p_out（口径待确认）"
    pin_label = "Inlet total pressure pt（入口总压）" if tt else "入口节点压力 p（口径待确认）"
    tin_label = "Inlet total temperature Tt（入口总温）" if tt else "入口节点温度 T（口径待确认）"
    pout_label = "Outlet total pressure pt（出口总压）" if tt else "出口节点压力 p（口径待确认）"
    eta_label = "Total-to-total efficiency ηtt（系统设定）" if tt else "系统等熵效率 η（口径待确认）"
    lines = [
        f"### {title}",
        "",
        f"**CFTurbo 机器类型**：Turbine（燃气涡轮）　**工质**：{gas_cn}",
        "保存记录明确声明 total_to_total；总态和总—总口径来自该声明。" if tt else
        "总态／静态口径待确认；以下为系统节点量，不能直接作为 CFTurbo 的总态或总—静边界。",
        "",
        "| CFTurbo 字段 | 数值 | 单位 |",
        "|---|---|---|",
        f"| Mass flow ṁ（本涡轮实际流量） | {_g(o,'mass_flow_kgs'):.3f} | kg/s |",
        f"| {ratio_label} | {_g(o,'turbine_pressure_ratio'):.4f} | — |",
        f"| Revolutions n | {_g(o,'shaft_speed_rpm'):.0f} | /min |",
        f"| {pin_label} | {pt_in_bar:.1f} | bar |",
        f"| {tin_label} | {Tt_in_C:.2f} | °C |",
        f"| {pout_label} | {p_out_bar:.1f} | bar |",
        f"| {eta_label} | {_g(o,'eta_turbine'):.3f} | — |",
        "",
        "**保存的入口工质参数（Perfect gas 常物性近似需另行确认）**",
        "cp、γ、M 按原记录展示；R 仅由有效 M 换算。未强制三者自洽，也未代替变比热计算。",
        "",
        "| 字段 | 值 | 单位 |",
        "|---|---|---|",
        f"| Heat capacity cp | {_g(o,'cp_in_Jkgk'):.1f} | J/(kg·K) |",
        f"| κ (gamma) | {_g(o,'gamma_in'):.4f} | — |",
        f"| Gas constant R | {R:.1f} | J/(kg·K) |",
        f"| **Molar weight M** | {M_kg_mol:.6f} | **kg/mol** |",
        "",
        "**气动 / 几何（参考）**",
        "",
        "| 量 | 值 |",
        "|---|---|",
        f"| 等熵焓降 Δh_s [kJ/kg] | {_g(o,'isentropic_drop_Jkg')/1e3:.1f} |",
        f"| 涡轮功率 [kW] | {_g(o,'turbine_power_W')/1e3:.0f} |",
        f"| 速比 U/C₀ | {_g(o,'u_over_c_actual'):.2f} |",
        f"| 反动度 R | {_g(o,'reaction_degree'):.2f} |",
        f"| 节圆速度 U_mean [m/s] | {_g(o,'U_mean_actual_ms'):.0f} |",
        f"| 转子中径 D_mean [mm] | {_g(o,'D_mean_m')*1000:.0f} |",
        f"| 记录的建议级数 | {_g(o,'n_stages_recommended'):.0f} |",
        f"| 出口{'总温' if tt else '节点温度'} T_exh [K] | {_g(o,'T_exhaust_K'):.0f} |",
    ]
    lines.append("")
    return _show_missing("\n".join(lines))


def _composition_block(state, state_key: str) -> str:
    lines = [f"**入口组分来源：{state_key}；质量分数 Y（非摩尔分数）**", ""]
    composition = getattr(state, "composition", None)
    if not isinstance(composition, dict) or not composition:
        return "\n".join(lines + ["未记录／待确认", ""])
    lines += ["| 物种 | 质量分数 Y |", "|---|---|"]
    values = [_g(composition, species) for species in composition]
    for species in sorted(composition):
        lines.append(f"| {species} | {_g(composition, species):.10g} |")
    if not all(math.isfinite(v) and 0 <= v <= 1 for v in values) or not math.isclose(sum(values), 1, abs_tol=1e-6):
        lines += ["", "组分质量分数未通过非负性／归一化检查，待确认；未补齐或归一化。"]
    return _show_missing("\n".join(lines + [""]))


def build_cfturbo_markdown(results) -> str:
    """由保存的拓扑、组件和入口组分生成录入参考；不补造旧记录数据。"""
    inp, comp, s = results.inputs, results.components, results.summary
    states = getattr(results, "states", {}) or {}
    cycle = str(getattr(inp, "cycle_type", ""))
    try:
        propellant = get_propellant(getattr(inp, "propellant_pair", None))
    except ValueError:
        propellant = None
    fuel_label = propellant.fuel_label if propellant else "燃料（身份待确认）"
    fuel_fluid = propellant.coolprop_fluid if propellant else ""
    fuel_molar = {"Methane": 0.0160428, "n-Dodecane": 0.17033484}.get(fuel_fluid, float("nan"))
    if cycle == "gg":
        dual = getattr(inp, "gg_layout", None) == "dual_shaft"
        turbine_specs = ([
            ("gg_turbine_ox", "GG 涡轮（氧泵轴，并联支路）", "gg_outlet", "GG 燃气"),
            ("gg_turbine_fuel", "GG 涡轮（燃料泵轴，并联支路）", "gg_outlet", "GG 燃气"),
        ] if dual else [("gg_turbine", "GG 涡轮（单轴共驱两泵）", "gg_outlet", "GG 燃气")])
        cycle_label = "GG 双轴并联" if dual else "GG 单轴"
    else:
        turbine_specs = [
            ("ox_preburner_turbine", "富氧涡轮", "ox_preburner_outlet", "富氧预燃室出口燃气"),
            ("fuel_preburner_turbine", "富燃涡轮", "fuel_preburner_outlet", "富燃预燃室出口燃气"),
        ]
        cycle_label = "FFSC" if cycle == "ffsc" else "循环待确认"
    parts = [
        f"# {cycle_label} 涡轮泵 CFTurbo 设计点参考\n",
        f"- 推进剂：{propellant.label if propellant else '未记录／待确认'}",
        f"- 发动机：推力 {_g(s,'thrust_N')/1e6:.3f} MN，Isp {_g(s,'Isp_s'):.1f}/{_g(s,'Isp_vac_s'):.1f} s，"
        f"Pc {_g(vars(inp), 'pc')/1e6:.3f} MPa，O/F {_g(vars(inp), 'of_ratio'):.2f}，总流量 {_g(s,'mdot_total_kg_s'):.1f} kg/s",
        f"- 求解残差 {_g(s,'solver_residual_norm'):.3e}\n",
        "> 单位：体积流量 m³/h、摩尔质量 kg/mol（非 kg/kmol）、温度 °C、压力 bar。"
        "缺失数据保留为待确认；边界口径以各组件保存记录为准。\n",
        "## 泵设计点\n",
    ]
    for key, title, fluid, cp_fluid, molar, state_key, mdot_key in (
        ("ox_pump", "液氧泵", "液氧 LOX(O₂)", "Oxygen", 0.0319988, "lox_pump_inlet", "mdot_ox_kg_s"),
        ("fuel_pump", f"{fuel_label}泵", fuel_label, fuel_fluid, fuel_molar, "fuel_pump_inlet", "mdot_fuel_kg_s"),
    ):
        component = comp.get(key)
        outputs = component.outputs if component else {}
        inlet = states.get(state_key)
        temperature = _g(outputs, "T_in_K")
        if not math.isfinite(temperature):
            temperature = _g({"value": getattr(inlet, "temperature", None)}, "value")
        flow = _g({"value": getattr(inlet, "mass_flow", None)}, "value")
        if not math.isfinite(flow):
            flow = _g(s, mdot_key)
        parts += [_pump_block(title, fluid, cp_fluid, molar, outputs, temperature, flow), "---\n"]
    parts.append("## 涡轮设计点\n")
    for key, title, state_key, gas_label in turbine_specs:
        component = comp.get(key)
        outputs = component.outputs if component else {}
        parts += [_turbine_block(title, gas_label, outputs),
                  _composition_block(states.get(state_key), state_key), "---\n"]
    parts += ["## 备注\n",
              "- 泵工质采用记录对应的流体身份；煤油按正十二烷替代模型，未标定为真实 RP-1。",
              "- 未声明 total_to_total 的旧涡轮记录仅导出节点量；确认速度和边界定义后，"
              "才能转换为 CFTurbo 所需的总态／静态参数。",
              "- 组分为求解器保存的质量分数，工质模型及适用温压范围需另行核对。",
              "- 级数和几何为系统初选参考；叶型、损失及强度需要后续设计与校核。\n"]
    return _show_missing("\n".join(parts))


def export_cfturbo_markdown(results, path: str) -> str:
    """生成并写入 Markdown 文件，返回写入的文本。"""
    md = build_cfturbo_markdown(results)
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    return md
