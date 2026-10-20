"""
sweep.py

预燃室混合比 (O/F) 扫描 —— 用于涡轮设计点选取

功能：
─────────────────────────────────────────────────────────
* sweep_ox_preburner_mr(...)   ：富氧侧 O/F 扫描，固定富燃侧目标
* sweep_fuel_preburner_mr(...) ：富燃侧 O/F 扫描，固定富氧侧目标
* sweep_to_csv(records, path)  ：导出 CFTurbo 输入备选表

每次扫描的内部逻辑：
─────────────────────────────────────────────────────────
1. 深拷贝原始 EngineInputs，覆写目标预燃室 O/F；
2. 运行完整 FFSCCycleModel.evaluate()（求解器闭合三个残差）；
3. 从 summary / components 抽出涡轮设计需要的全部字段；
4. "T_PB_real_estimate" 现等于 T_PB（真实流体焓修正已内置于
   thermo._real_fluid_enthalpy，T_PB 即真实物理温度，不再额外减 150 K）。

设计动机：
─────────────────────────────────────────────────────────
* O/F 扫描不直接修改任何原始 inputs，扫描完成后的设计点可以
  通过 "应用此设计点" 把对应的 O/F 写回主面板，再正式跑一次。
* 富氧/富燃两侧的扫描函数对称设计，将来很容易扩成 2D 联合扫描。

V9 新增。
"""

from __future__ import annotations

import copy
import csv
from typing import Callable, List, Optional

from core import EngineInputs
from cycle import FFSCCycleModel
from task_control import checkpoint


# 【已废弃】历史经验修正项。早期 Cantera 用气相 NASA 多项式查 LOX 入口焓，
# 漏掉汽化潜热（~213 kJ/kg @ 90K）→ 富氧预燃室温度高估 ~150 K，故曾手动减回去。
# 现该修正已内置于 thermo._real_fluid_enthalpy()（CoolProp 真实流体焓），
# T_PB 本身即修正后的物理温度，**不应再减**；故偏移量置 0，保留常量仅为向后兼容。
T_PB_GAS_TO_REAL_OFFSET_K = 0.0


def _extract_record(of_target: float, results, side: str) -> dict:
    """从 CycleResults 中抽取一行扫描记录。side ∈ {'ox','fuel'}"""
    s = results.summary if results is not None else {}
    comp = (results.components if results is not None else {}) or {}

    if side == "ox":
        T_PB    = s.get("ox_preburner_T_combustion_K", 0.0)
        pi_t    = s.get("ox_turbine_pi_t", 0.0)
        p_in    = s.get("lox_pump_out_pressure_Pa", 0.0)   # 备用
        p_pre   = (comp.get("ox_preburner").outputs.get("preburner_pressure_Pa", 0.0)
                   if "ox_preburner" in comp else 0.0)
        p_exh   = s.get("ox_turbine_exhaust_pressure_Pa", 0.0)
        mdot_t  = comp.get("ox_preburner_turbine").outputs.get("mass_flow_kgs", 0.0) if "ox_preburner_turbine" in comp else 0.0
        cp      = comp.get("ox_preburner_turbine").outputs.get("cp_in_Jkgk", 0.0) if "ox_preburner_turbine" in comp else 0.0
        gamma   = comp.get("ox_preburner_turbine").outputs.get("gamma_in", 0.0) if "ox_preburner_turbine" in comp else 0.0
        MW      = comp.get("ox_preburner_turbine").outputs.get("molar_mass_in", 0.0) if "ox_preburner_turbine" in comp else 0.0
        power_W = s.get("ox_turbine_power_MW", 0.0) * 1e6
        dh_a    = comp.get("ox_preburner_turbine").outputs.get("actual_drop_Jkg", 0.0) if "ox_preburner_turbine" in comp else 0.0
        dh_s    = comp.get("ox_preburner_turbine").outputs.get("isentropic_drop_Jkg", 0.0) if "ox_preburner_turbine" in comp else 0.0
        T_exh   = comp.get("ox_preburner_turbine").outputs.get("T_exhaust_K", 0.0) if "ox_preburner_turbine" in comp else 0.0
        mr      = s.get("ox_preburner_mr", 0.0)
        eta_t   = comp.get("ox_preburner_turbine").outputs.get("eta_turbine", 0.0) if "ox_preburner_turbine" in comp else 0.0
    else:
        T_PB    = s.get("fuel_preburner_T_combustion_K", 0.0)
        pi_t    = s.get("fuel_turbine_pi_t", 0.0)
        p_pre   = (comp.get("fuel_preburner").outputs.get("preburner_pressure_Pa", 0.0)
                   if "fuel_preburner" in comp else 0.0)
        p_exh   = s.get("fuel_turbine_exhaust_pressure_Pa", 0.0)
        mdot_t  = comp.get("fuel_preburner_turbine").outputs.get("mass_flow_kgs", 0.0) if "fuel_preburner_turbine" in comp else 0.0
        cp      = comp.get("fuel_preburner_turbine").outputs.get("cp_in_Jkgk", 0.0) if "fuel_preburner_turbine" in comp else 0.0
        gamma   = comp.get("fuel_preburner_turbine").outputs.get("gamma_in", 0.0) if "fuel_preburner_turbine" in comp else 0.0
        MW      = comp.get("fuel_preburner_turbine").outputs.get("molar_mass_in", 0.0) if "fuel_preburner_turbine" in comp else 0.0
        power_W = s.get("fuel_turbine_power_MW", 0.0) * 1e6
        dh_a    = comp.get("fuel_preburner_turbine").outputs.get("actual_drop_Jkg", 0.0) if "fuel_preburner_turbine" in comp else 0.0
        dh_s    = comp.get("fuel_preburner_turbine").outputs.get("isentropic_drop_Jkg", 0.0) if "fuel_preburner_turbine" in comp else 0.0
        T_exh   = comp.get("fuel_preburner_turbine").outputs.get("T_exhaust_K", 0.0) if "fuel_preburner_turbine" in comp else 0.0
        mr      = s.get("fuel_preburner_mr", 0.0)
        eta_t   = comp.get("fuel_preburner_turbine").outputs.get("eta_turbine", 0.0) if "fuel_preburner_turbine" in comp else 0.0

    R_spec = (cp * (gamma - 1.0) / max(gamma, 1.001)) if gamma > 1.001 else 0.0

    # T_PB 已是真实物理温度：LOX/CH4 的真实流体焓修正现已内置于
    # thermo._real_fluid_enthalpy()（CoolProp），故 combustion_equilibrium 解出的
    # T_PB 本身就含修正，**不再额外减 150 K**（旧逻辑会重复修正、使富氧侧偏低 ~150 K，
    # 即 GUI 表里"预燃室温度"与"真实温度估算"差 150 K 的来源）。
    # 本字段保留 = T_PB，仅为兼容 CSV 导出与历史引用。
    T_real_est = T_PB

    residual = float(s.get("solver_residual_norm", 1.0))
    converged = residual < 1e-3 and not bool(s.get("solver_warning", 0.0))

    return {
        "side": side,
        "of_target": of_target,
        "of_actual": mr,
        "converged": converged,
        "residual": residual,
        # CFTurbo 直接可用字段：
        "T_PB_K": T_PB,
        "T_PB_real_estimate_K": T_real_est,
        "T_exhaust_K": T_exh,
        "pi_t": pi_t,
        "p_in_Pa":  p_pre,
        "p_in_MPa": p_pre / 1e6,
        "p_out_Pa": p_exh,
        "p_out_MPa": p_exh / 1e6,
        "mass_flow_kgs": mdot_t,
        "cp_Jkgk": cp,
        "gamma": gamma,
        "molar_mass_kg_kmol": MW,
        "R_specific_Jkgk": R_spec,
        "isentropic_dh_kJkg": dh_s / 1e3,
        "actual_dh_kJkg": dh_a / 1e3,
        "turbine_power_MW": power_W / 1e6,
        "eta_turbine": eta_t,
        # 系统级一致性：
        "mdot_total_kgs": s.get("mdot_total_kg_s", 0.0),
        "thrust_N":       s.get("thrust_N", 0.0),
        "Isp_s":          s.get("Isp_s", 0.0),
        # summary 里只有 Pa 版键（chamber_pressure_Pa），此处换算成 MPa 供 CSV/CFTurbo 用
        "pc_MPa":         s.get("chamber_pressure_Pa", 0.0) / 1e6,
    }


def sweep_ox_preburner_mr(
    inputs: EngineInputs,
    of_list: List[float],
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    record_callback: Optional[Callable[[dict], None]] = None,
) -> List[dict]:
    """
    富氧预燃室 O/F 扫描。
    每个 O/F 跑一次完整闭合求解器；富燃侧目标 O/F 保持原值。

    返回：list[dict]，每行包含 CFTurbo 输入所需的全部字段，
          以及收敛标志、残差、O/F 实际值（应与 target 一致）。
    """
    if inputs.cycle_type != "ffsc":
        raise ValueError("预燃室混合比扫描仅支持 FFSC 循环，不能使用 GG 工况。")
    records: List[dict] = []
    n = len(of_list)
    for i, of_val in enumerate(of_list):
        checkpoint()
        pt_inputs = copy.deepcopy(inputs)
        pt_inputs.use_target_preburner_mr = 1.0   # 强制目标 MR 模式
        # 关键：本扫描就是"给定 O/F → 算温度"的前向计算,必须关掉温度目标外环,
        # 否则外环会反求 MR 把温度钉在目标值、覆盖掉扫描的 O/F → 输出全平(与主
        # 求解的全局模式无关,扫描内部自洽)。
        pt_inputs.use_target_preburner_temp = 0.0
        pt_inputs.ox_preburner_mr_target  = float(of_val)
        # 富燃侧保持原值

        if progress_callback is not None:
            try:
                progress_callback(i + 1, n, f"富氧 O/F = {of_val:.1f}")
            except Exception:
                pass

        try:
            results = FFSCCycleModel(pt_inputs).evaluate()
            rec = _extract_record(of_val, results, side="ox")
            rec["error"] = ""
        except Exception as exc:
            rec = {
                "side": "ox",
                "of_target": of_val,
                "converged": False,
                "residual": float("nan"),
                "error": str(exc),
            }
        records.append(rec)
        if record_callback is not None:
            record_callback(rec)
        checkpoint()
    return records


def sweep_fuel_preburner_mr(
    inputs: EngineInputs,
    of_list: List[float],
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    record_callback: Optional[Callable[[dict], None]] = None,
) -> List[dict]:
    """富燃预燃室 O/F 扫描。富氧侧目标 O/F 保持原值。"""
    if inputs.cycle_type != "ffsc":
        raise ValueError("预燃室混合比扫描仅支持 FFSC 循环，不能使用 GG 工况。")
    records: List[dict] = []
    n = len(of_list)
    for i, of_val in enumerate(of_list):
        checkpoint()
        pt_inputs = copy.deepcopy(inputs)
        pt_inputs.use_target_preburner_mr   = 1.0
        # 同 ox 侧：扫描走前向"给定 O/F → 算温度",关掉温度目标外环避免覆盖。
        pt_inputs.use_target_preburner_temp = 0.0
        pt_inputs.fuel_preburner_mr_target  = float(of_val)

        if progress_callback is not None:
            try:
                progress_callback(i + 1, n, f"富燃 O/F = {of_val:.3f}")
            except Exception:
                pass

        try:
            results = FFSCCycleModel(pt_inputs).evaluate()
            rec = _extract_record(of_val, results, side="fuel")
            rec["error"] = ""
        except Exception as exc:
            rec = {
                "side": "fuel",
                "of_target": of_val,
                "converged": False,
                "residual": float("nan"),
                "error": str(exc),
            }
        records.append(rec)
        if record_callback is not None:
            record_callback(rec)
        checkpoint()
    return records


def sweep_to_csv(records: List[dict], file_path: str) -> str:
    """把扫描结果导出 CSV（可直接复制进 CFTurbo Global Setup）。"""
    if not records:
        raise ValueError("扫描结果为空，无法导出 CSV。")
    # 取所有键的并集，保证不同 side 行也能写
    keys: List[str] = []
    seen = set()
    for r in records:
        for k in r.keys():
            if k not in seen:
                keys.append(k); seen.add(k)
    with open(file_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in records:
            w.writerow(r)
    return file_path
