"""
cfturbo_export.py

把数字样机求解结果里的四个叶轮机械（液氧泵 / 甲烷泵 / 富氧涡轮 / 富燃涡轮）
设计点，整理成一份 *CFTurbo 录入参考* 的 Markdown 文档。

所有量纲已换算到 CFTurbo 的常用显示单位：
  · 体积流量 Q [m³/h]   · 温度 [°C]   · 压力 [bar]   · 功率 [kW]
  · 摩尔质量 M [kg/mol]（注意：CFTurbo 是 kg/mol，不是 kg/kmol！）
  · 涡轮压比按「总-静(total-to-static)」口径给出

泵的流体物性（μ/ν/λ/pv）在导出时用 CoolProp 在入口工况实算（若可用）。
"""

from __future__ import annotations

from typing import Optional

try:
    from CoolProp.CoolProp import PropsSI as _PROPSSI
    _CP_OK = True
except Exception:
    _CP_OK = False

_R_UNIV = 8314.462  # J/(kmol·K)
_K = 273.15


def _fluid_props(coolprop_name: str, T_K: float, p_Pa: float) -> Optional[dict]:
    """入口流体物性（CoolProp），失败返回 None。"""
    if not _CP_OK:
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


def _g(d: dict, k: str, default: float = 0.0) -> float:
    v = d.get(k, default)
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _pump_block(title: str, fluid_cn: str, coolprop_name: str, molar_kg_per_mol: float,
                o: dict, T_in_K: float, mdot_kg_s: float) -> str:
    Q_m3h = _g(o, "volume_flow_m3h") or _g(o, "volume_flow_m3s") * 3600.0  # 与 GUI/CFTurbo 同源
    p_in_bar = _g(o, "p_in_bar")
    p_out_bar = _g(o, "p_out_bar")
    T_out_K = _g(o, "T_out_K")
    npsha_m = _g(o, "npsh_available_m")   # pump.py 已按 (p_in-p_vap)/(ρ·g) 报出压头[m]
    pr = _fluid_props(coolprop_name, T_in_K, p_in_bar * 1e5)

    lines = [
        f"### {title}",
        "",
        "**CFTurbo 机器类型**：Pump（离心泵，带诱导轮 inducer）　**流体模型**：CoolProp 真实流体（液相）",
        "",
        "| CFTurbo 字段 | 数值 | 单位 |",
        "|---|---|---|",
        f"| Mass flow ṁ | {mdot_kg_s:.1f} | kg/s |",
        f"| Volume flow Q | {Q_m3h:.0f} | m³/h |",
        f"| Head H | {_g(o,'head_m'):.0f} | m |",
        f"| Total pressure rise Δp | {_g(o,'delta_p_bar'):.1f} | bar |",
        f"| Revolutions n | {_g(o,'nr_rpm'):.0f} | /min |",
        f"| Stages（级数） | {_g(o,'n_stages'):.0f} | — |",
        f"| **Inlet** Pressure (total) pt | {p_in_bar:.2f} | bar |",
        f"| **Inlet** Temperature T | {T_in_K - _K:.2f} | °C |",
        f"| Efficiency η（参考） | {_g(o,'eta'):.3f} | — |",
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
        lines.append("| （CoolProp 不可用，请在 CFTurbo 内用 CoolProp 模型按入口工况自动取物性） | | |")
    lines.append("")
    return "\n".join(lines)


def _turbine_block(title: str, gas_cn: str, o: dict) -> str:
    Tt_in_C = _g(o, "T_in_K") - _K
    pt_in_bar = _g(o, "p_in_Pa") / 1e5
    p_out_bar = _g(o, "p_out_Pa") / 1e5
    mw_kmol = _g(o, "molar_mass_in", 30.0)
    R = _R_UNIV / max(mw_kmol, 1.0)
    M_kg_mol = mw_kmol / 1000.0
    lines = [
        f"### {title}",
        "",
        f"**CFTurbo 机器类型**：Turbine（燃气涡轮）　**气体模型**：Perfect gas（{gas_cn}）",
        "",
        "| CFTurbo 字段 | 数值 | 单位 |",
        "|---|---|---|",
        f"| Mass flow ṁ | {_g(o,'mass_flow_kgs'):.1f} | kg/s |",
        f"| **Total-to-static pressure ratio** πts | {_g(o,'turbine_pressure_ratio'):.4f} | — |",
        f"| Revolutions n | {_g(o,'shaft_speed_rpm'):.0f} | /min |",
        f"| Inlet total pressure pt | {pt_in_bar:.1f} | bar |",
        f"| Inlet total temperature Tt | {Tt_in_C:.2f} | °C |",
        f"| Outlet static pressure（=πts 反算） | {p_out_bar:.1f} | bar |",
        f"| Total-to-static efficiency ηts（参考） | {_g(o,'eta_turbine'):.3f} | — |",
        "",
        "**Perfect-gas 工质（三者自洽 cp=γ/(γ−1)·R）**",
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
        f"| 推荐级数 | {_g(o,'n_stages_recommended'):.0f} |",
        f"| 出口温度 T_exh [K] | {_g(o,'T_exhaust_K'):.0f} |",
    ]
    lines.append("")
    return "\n".join(lines)


def build_cfturbo_markdown(results) -> str:
    """由一次 CycleResults 生成 CFTurbo 录入参考 Markdown 文本。

    V20：兼容 GG（燃气发生器）循环——两侧涡轮键回退到 gg_turbine_*/gg_turbine
    （单轴布局时两个"涡轮端"块显示同一台 GG 涡轮，标题会注明）。
    """
    inp = results.inputs
    comp = results.components
    s = results.summary

    _is_gg = str(getattr(inp, "cycle_type", "ffsc")) == "gg"
    op = comp["ox_pump"].outputs
    fp = comp["fuel_pump"].outputs
    _ot_comp = (comp.get("ox_preburner_turbine")
                or comp.get("gg_turbine_ox") or comp.get("gg_turbine"))
    _ft_comp = (comp.get("fuel_preburner_turbine")
                or comp.get("gg_turbine_fuel") or comp.get("gg_turbine"))
    if _ot_comp is None or _ft_comp is None:
        raise KeyError("结果中找不到涡轮组件（ox/fuel_preburner_turbine 或 gg_turbine*）")
    ot = _ot_comp.outputs
    ft = _ft_comp.outputs
    _ot_title = "富氧涡轮（涡轮端）" if not _is_gg else f"GG 涡轮（{_ot_comp.name}，富燃燃气）"
    _ft_title = "富燃涡轮（涡轮端）" if not _is_gg else f"GG 涡轮（{_ft_comp.name}，富燃燃气）"
    _ot_gas = "富氧燃气 ~92% O₂" if not _is_gg else "GG 富燃燃气（CH₄ 为主）"
    _ft_gas = "富燃燃气 ~78% CH₄" if not _is_gg else "GG 富燃燃气（CH₄ 为主）"

    mdot_ox = _g(s, "mdot_ox_kg_s")
    mdot_fuel = _g(s, "mdot_fuel_kg_s")

    head = [
        "# FFSC 涡轮泵 CFTurbo 设计点参考",
        "",
        f"- 发动机：推力 {_g(s,'thrust_N')/1e6:.3f} MN(SL)，Isp {_g(s,'Isp_s'):.1f}/{_g(s,'Isp_vac_s'):.1f} s，"
        f"Pc {inp.pc/1e6:.0f} MPa，O/F {inp.of_ratio:.2f}，总流量 {_g(s,'mdot_total_kg_s'):.1f} kg/s",
        f"- 供给链：贮箱 {_g(s,'tank_pressure_bar'):.1f} bar → 增压泵 → {_g(s,'boost_out_pressure_bar'):.1f} bar "
        f"→ 主泵入口 {_g(s,'main_pump_inlet_pressure_bar'):.2f} bar",
        f"- 求解残差 {_g(s,'solver_residual_norm'):.1e}，两轴功率平衡 "
        f"氧 {_g(s,'ox_side_power_balance_ratio'):.4f} / 燃 {_g(s,'fuel_side_power_balance_ratio'):.4f}",
        "",
        "> ⚠ CFTurbo 单位提醒：体积流量 **m³/h**、摩尔质量 **kg/mol**（非 kg/kmol）、温度 **°C**、压力 **bar**、"
        "涡轮压比用 **总-静(total-to-static)**。",
        "",
        "---",
        "",
        "## 一、液氧涡轮泵（1 号轴）",
        "",
    ]
    parts = [
        "\n".join(head),
        _pump_block("液氧泵（泵端）", "液氧 LOX(O₂)", "Oxygen", 0.0319988, op, inp.ox_inlet_temp, mdot_ox),
        "---\n",
        _turbine_block(_ot_title, _ot_gas, ot),
        "---\n",
        "## 二、甲烷涡轮泵（2 号轴）\n" if not _is_gg else
        "## 二、甲烷泵（GG 循环：单轴时与液氧泵同轴）\n",
        _pump_block("甲烷泵（泵端）", "液甲烷 LCH4", "Methane", 0.0160428, fp, inp.fuel_inlet_temp, mdot_fuel),
        "---\n",
        _turbine_block(_ft_title, _ft_gas, ft),
        "---\n",
        "## 备注\n",
        "- 泵流体在 CFTurbo 里建议直接用 **CoolProp / Oxygen、Methane** 模型，按上表入口 pt/T 录入，"
        "由 CFTurbo 在工况点自算物性；上表物性供核对。\n"
        "- 涡轮 πts 是「预燃室静压 / 排气静压」口径（入口动能可忽略），录入 CFTurbo 的"
        "「Total-to-static pressure ratio」字段。\n"
        "- 富燃涡轮建议在 CFTurbo 内按 **2 级** 设计（每级载荷减半，转子安全系数回到 ~2）。\n",
    ]
    return "\n".join(parts)


def export_cfturbo_markdown(results, path: str) -> str:
    """生成并写入 Markdown 文件，返回写入的文本。"""
    md = build_cfturbo_markdown(results)
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    return md
