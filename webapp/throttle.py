"""Steady throttle studies anchored to an immutable, completed design calculation.

The existing desktop fixed-throat solver is reused. This module adds the web
execution boundary, provenance, per-point acceptance and JSON-safe persistence.
"""
from copy import deepcopy
from dataclasses import asdict
import json
import math
import warnings

from webapp.solver import EngineInputs, input_values, plain, science_environment
from webapp.validation import fingerprint
from webapp.steady_sensitivity import throttle_response_gains
from convergence import numerical_convergence
from steady_closure import audit_energy
from steady_network import audit_network, validate_hardware

MAX_POINTS = 15
AREA_TOLERANCE = 5e-4


def finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except (ValueError, OverflowError):
        return None


def _cooling_baseline(calculation, values, throat_area, height):
    """Audit saved coolant evidence without performing a new thermal solve.

    Geometry is regenerated from the immutable design inputs. Conservation is
    recalculated from the saved nodes/cells; a stored 'pass' alone is insufficient.
    This is model bookkeeping, not validation of condensation, EOS or correlations.
    """
    if not values["cooling_bartz_mode"]:
        return {"mode": "specified_heat_and_pressure_budget", "geometry_status": "not_applicable",
                "validation_status": "no_fixed_hardware_cooling_prediction"}

    def require(condition, detail):
        if not condition:
            raise ValueError(f"来源设计点的统一准一维冷却{detail}；请重新计算设计点，不能据此建立固定硬件冷却基准。")

    def number(value, label, *, positive=False):
        parsed = finite(value)
        require(parsed is not None and (not positive or parsed > 0), f"缺少有效{label}")
        return parsed

    def close(actual, expected, label, *, atol=1e-8, rtol=1e-8):
        actual = number(actual, label)
        require(abs(actual-expected) <= max(atol, abs(expected)*rtol), f"{label}不一致")

    components = calculation.get("components") or {}
    cooling = components.get("cooling_channel") or {}
    profile = cooling.get("profile")
    require(isinstance(profile, dict) and profile.get("version") == 1 and profile.get("model_id") == "regen_quasi1d_ph_v1",
            "记录缺失或来自旧模型")
    geometry = profile.get("geometry")
    require(isinstance(geometry, dict) and geometry.get("version") == 1, "几何快照缺失")
    outputs = cooling.get("outputs") or {}
    close(outputs.get("A_throat_m2"), throat_area, "喉面积", rtol=2e-8)
    rt = number(geometry.get("r_t_m"), "几何喉半径", positive=True)
    close(math.pi*rt*rt, throat_area, "几何喉面积", rtol=2e-8)

    # Geometric reconstruction is inexpensive and involves no fluid-property or
    # engine solve. It rejects stale coverage, table geometry, flow direction or
    # scalar inputs, while retaining the saved design throat within its tolerance.
    from chamber_contour import convergent_geometry
    from regen_geometry import build_regen_geometry
    rc = rt*math.sqrt(values["chamber_contraction_ratio"])
    convergent = convergent_geometry(rc, rt, values["chamber_conv_half_angle_deg"])
    length = (values["chamber_l_star_m"]*math.pi*rt*rt-convergent["V_conv_m3"])/(math.pi*rc*rc)
    require(length > 0, "圆筒长度无效")
    try:
        expected = build_regen_geometry(r_t=rt, r_c=rc, L_cyl=length,
            expansion_ratio=values["expansion_ratio"], cover_eps=values["cooling_cover_eps"],
            bell_fraction=values["nozzle_bell_fraction"], conv_half_angle_deg=values["chamber_conv_half_angle_deg"],
            n_points=values["cooling_grid_points"], counterflow=True,
            n_channels=values["cooling_n_channels"], channel_width_m=values["cooling_channel_width_m"],
            channel_height_m=height, wall_thickness_m=values["cooling_wall_thickness_m"],
            channel_table=values["cooling_channel_table_json"] or None)
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError("来源设计点冷却几何不能从保存的输入重建，请修正并重新计算设计点。") from exc

    def compare_geometry(saved, rebuilt, path="几何"):
        if isinstance(rebuilt, dict):
            require(isinstance(saved, dict), f"{path}缺失")
            for key, value in rebuilt.items():
                compare_geometry(saved.get(key), value, path+"."+key)
        elif isinstance(rebuilt, list):
            require(isinstance(saved, list) and len(saved) == len(rebuilt), f"{path}尺寸不一致")
            for index, (a, b) in enumerate(zip(saved, rebuilt)):
                compare_geometry(a, b, f"{path}[{index}]")
        elif isinstance(rebuilt, (int, float)) and not isinstance(rebuilt, bool):
            close(saved, rebuilt, path, atol=1e-11, rtol=1e-9)
        else:
            require(saved == rebuilt, f"{path}不一致")
    compare_geometry(geometry, expected)
    require(profile.get("flow_direction") == geometry["flow_direction"], "流向不一致")

    nodes, cells = profile.get("nodes"), profile.get("cells")
    require(isinstance(nodes, list) and isinstance(cells, list)
            and len(nodes) == len(geometry["nodes"])+2 and len(cells) == len(nodes)-1,
            "沿程节点或单元不完整")
    require(all(isinstance(row,dict) for row in nodes+cells), "沿程节点或单元格式无效")
    require(cells[0].get("kind") == "inlet_manifold" and cells[-1].get("kind") == "outlet_manifold"
            and all(c.get("kind") == "channel" for c in cells[1:-1]), "集流腔/通道顺序不完整")
    for node, geometric in zip(nodes[1:-1], geometry["nodes"]):
        compare_geometry({key: node.get(key) for key in geometric}, geometric, "沿程节点几何")
    for cell, geometric in zip(cells[1:-1], geometry["cells"]):
        compare_geometry({key: cell.get(key) for key in geometric}, geometric, "沿程单元几何")

    states = calculation.get("states") or {}
    inlet, outlet = states.get("fuel_after_pump") or {}, states.get("fuel_after_cooling") or {}
    mass = number(outlet.get("mass_flow"), "冷却流量", positive=True)
    close(inlet.get("mass_flow"), mass, "冷却入口流量")
    for node in nodes:
        require(isinstance(node, dict), "节点格式无效")
        for key in ("p_Pa", "T_K", "rho_kgm3", "cp_JkgK"):
            number(node.get(key), "节点 "+key, positive=True)
        require(node.get("range_status") == "within_declared_ranges"
                and node.get("phase") in ("liquid", "gas", "supercritical", "supercritical_liquid", "supercritical_gas"),
                "节点缺少声明范围与单相证据")
        mach = number(node.get("Mach"), "冷却马赫数")
        require(0 <= mach < 1, "节点不在亚声速模型范围")
        velocity = number(node.get("velocity_ms"), "冷却速度")
        area = node.get("A_flow_m2")
        close(velocity, mass/(node["rho_kgm3"]*number(area, "通道面积", positive=True)) if area is not None else 0., "节点质量连续")
        close(node.get("h0_Jkg"), number(node.get("h_Jkg"), "节点静焓")+velocity**2/2, "节点总焓")
    for node, state, position in ((nodes[0], inlet, "入口"), (nodes[-1], outlet, "出口")):
        for key, state_key in (("p_Pa", "pressure"), ("T_K", "temperature"), ("h_Jkg", "enthalpy")):
            close(node.get(key), number(state.get(state_key), position+state_key), position+"与循环状态")

    total_heat = total_drop = 0.
    energy_errors, pressure_errors, wall_errors = [], [], []
    for a, b, cell in zip(nodes, nodes[1:], cells):
        heat = number(cell.get("Q_W"), "单元吸热")
        friction = number(cell.get("dp_friction_Pa"), "单元摩擦压降")
        local = number(cell.get("dp_local_Pa"), "单元局部压降")
        require(friction >= 0 and local >= 0, "耗散压降为负")
        ga = mass/a["A_flow_m2"] if a["A_flow_m2"] is not None else 0.
        gb = mass/b["A_flow_m2"] if b["A_flow_m2"] is not None else 0.
        momentum = .5*(ga+gb)*(b["velocity_ms"]-a["velocity_ms"])
        close(cell.get("dp_momentum_Pa"), momentum, "单元动量压差", atol=.01)
        ep = b["p_Pa"]-a["p_Pa"]+friction+local+momentum
        eh = mass*(b["h0_Jkg"]-a["h0_Jkg"])-heat
        require(abs(ep) <= .5 and abs(eh) <= max(.02,abs(heat)*1e-8), "单元能量或动量守恒失败")
        close(cell.get("energy_residual_W"), eh, "单元能量残差", atol=1e-6)
        close(cell.get("momentum_residual_Pa"), ep, "单元动量残差", atol=1e-6)
        wall = number(cell.get("wall_residual_K"), "壁面平衡残差")
        require(abs(wall) <= 1e-5, "壁面热平衡未闭合")
        total_heat += heat
        total_drop += friction+local+momentum
        energy_errors.append(abs(eh)); pressure_errors.append(abs(ep)); wall_errors.append(abs(wall))
    energy_error = mass*(nodes[-1]["h_Jkg"]-nodes[0]["h_Jkg"])-total_heat
    pressure_error = nodes[0]["p_Pa"]-nodes[-1]["p_Pa"]-total_drop
    require(abs(energy_error) <= max(.1,abs(total_heat)*1e-7) and abs(pressure_error) <= .5*len(cells), "总能量或总压差账未闭合")
    balance = profile.get("balance") or {}
    for key, value in {"energy_residual_W":energy_error,"pressure_sum_residual_Pa":pressure_error,
                       "max_cell_energy_residual_W":max(energy_errors),"max_cell_momentum_residual_Pa":max(pressure_errors),
                       "max_wall_residual_K":max(wall_errors),"mass_residual_kg_s":0.}.items():
        close(balance.get(key), value, "保存的 "+key, atol=1e-6)
    saved_summary = profile.get("summary") or {}
    close(saved_summary.get("Q_total_W"), total_heat, "总吸热汇总", atol=.1)
    close(outputs.get("heat_load_W"), total_heat, "循环总吸热", atol=.1)
    close(saved_summary.get("dp_total_Pa"), nodes[0]["p_Pa"]-nodes[-1]["p_Pa"], "总压差汇总", atol=.1)

    coupling = profile.get("hot_side_coupling") or {}
    require(coupling.get("status") == "pass", "热侧耦合未完成")
    iterations = number(coupling.get("iterations"), "热侧迭代次数", positive=True)
    history = coupling.get("history")
    require(iterations.is_integer() and isinstance(history, list) and len(history) == iterations
            and all(isinstance(row,dict) and row.get("iteration") == index+1 for index,row in enumerate(history)),
            "热侧迭代历史不完整")
    for residual, tolerance, maximum in (
        ("pressure_residual_Pa","pressure_tolerance_Pa",.5),
        ("hot_side_temperature_residual_K","temperature_tolerance_K",.001),
        ("geometry_relative_residual","geometry_relative_tolerance",2e-8),
        ("heat_relative_residual","heat_relative_tolerance",2e-8),
        ("hot_side_relative_residual","hot_side_relative_tolerance",5e-7),
        ("cell_heat_relative_residual","cell_heat_relative_tolerance",5e-7)):
        error = number(coupling.get(residual), "热侧 "+residual)
        limit = number(coupling.get(tolerance), "热侧 "+tolerance, positive=True)
        require(0 <= error <= limit <= maximum, "热侧耦合残差未达容差")
        close(history[-1].get(residual), error, "热侧历史末值", atol=1e-12)
    hot = profile.get("hot_side_profile") or {}
    nozzle_profile = (components.get("nozzle") or {}).get("profile") or {}
    require(hot.get("model_id") == "heated_frozen_quasi1d_v1" and hot == nozzle_profile,
            "缺少本次喷管热侧同源记录")
    require(isinstance(hot.get("nodes"),list) and len(hot["nodes"]) > 2
            and isinstance(hot.get("cells"),list) and len(hot["cells"]) == len(geometry["cells"]), "热侧沿程证据不完整")
    require(abs(number(hot.get("max_scaled_residual"),"热侧守恒残差")) <= 2e-6, "热侧守恒未达容差")
    critical_x = number(hot.get("critical_x_m"), "声速位置")
    require(geometry["full_nodes"][0]["x_m"] < critical_x < geometry["full_exit_x_m"], "声速位置不在热侧流路内")
    require(bool(hot.get("critical_location_model")), "声速位置求解依据缺失")
    require(abs(number(hot.get("critical_regularity_residual_per_m"), "热流临界条件残差")) <= 1e-6,
            "热流临界条件未闭合")
    close(hot.get("heat_loss_W"), total_heat, "热/冷侧总热量", atol=.1)
    launch = number(hot.get("critical_launch_relative_difference"), "临界点展开敏感性")
    require(0 <= launch <= 2e-7, "临界点展开敏感性未通过")
    momenta = hot.get("momentum_cell_residuals_Pa")
    require(isinstance(momenta,list) and len(momenta) == len(hot["nodes"])-1, "独立热侧动量积分记录不完整")
    hot_error = max(abs(number(v,"独立热侧单元动量残差")) for v in momenta)
    hot_tolerance = number(hot.get("momentum_integral_tolerance_Pa"), "独立热侧动量容差", positive=True)
    require(hot_error <= hot_tolerance <= max(.05, values["pc"]*2e-9), "独立热侧动量积分未通过")
    close(hot.get("max_momentum_integral_residual_Pa"),hot_error,"独立热侧动量残差汇总",atol=1e-8)
    channel_hot = sorted(hot["cells"], key=lambda c:c.get("x_m",float("inf")),
                         reverse=profile["flow_direction"] == "counterflow")
    temperature_error = vector_error = 0.
    for actual, cold in zip(channel_hot,cells[1:-1]):
        used = cold.get("hot_side") or {}
        close(actual.get("x_m"),cold["x_m"],"热/冷侧单元位置")
        for key in ("T_static_K","T_aw_K","T0_K","p_Pa","M","cp_JkgK","mu_Pas","Pr","gamma"):
            a = number(actual.get(key),"热侧反馈 "+key,positive=True)
            b = number(used.get(key),"冷却使用的热侧 "+key,positive=True)
            vector_error = max(vector_error,abs(a-b)/abs(a))
            if key == "T_aw_K":
                temperature_error = max(temperature_error,abs(a-b))
    close(coupling["hot_side_temperature_residual_K"],temperature_error,"重算热侧温差",atol=1e-9)
    close(coupling["hot_side_relative_residual"],vector_error,"重算热侧状态相对差",atol=1e-12)
    return {"mode":"regen_quasi1d_ph_v1", "geometry":deepcopy(geometry),
            "geometry_sha256":fingerprint(geometry), "profile_sha256":fingerprint(profile),
            "status":"saved_model_balances_checked", "validation_status":"requires_independent_validation",
            "limitations":["守恒与模型域检查不验证凝结、温区外推误差、高压燃气 EOS 或传热相关式。"]}


def source_baseline(source):
    """Read actual solved inputs and hardware; never substitute current edits."""
    original = source.get("result") or {}
    calculation = original.get("calculation") or {}
    raw_inputs = calculation.get("inputs")
    if not isinstance(raw_inputs, dict):
        raise ValueError("来源设计记录缺少实际计算输入，请先重新运行稳态设计点。")
    values, _ = input_values(deepcopy(raw_inputs), saved_snapshot=True)
    summary = calculation.get("summary") or {}
    if numerical_convergence(calculation)["status"] != "pass":
        raise ValueError("来源设计点尚未完整数值收敛或缺少分层证据，请重新计算后建立节流几何基准。")
    components = calculation.get("components") or {}
    area = finite((components.get("nozzle") or {}).get("outputs", {}).get("At_m2"))
    if area is None or area <= 0:
        raise ValueError("来源设计记录缺少有效喉部面积，不能执行固定喉部节流扫描。")
    height = None
    if values["cooling_channel_auto_size"] and values["cooling_bartz_mode"]:
        height_mm = finite((components.get("cooling_channel") or {}).get("outputs", {}).get("channel_height_mm"))
        if height_mm is None or height_mm <= 0.1:
            raise ValueError("来源设计记录缺少有效冷却通道高度，不能冻结自动定尺寸基准。")
        height = height_mm / 1000
    elif finite(values.get("cooling_channel_height_m")) is not None:
        height = values["cooling_channel_height_m"]
    baseline = {
        "thrust_N": values["thrust"], "pc_Pa": values["pc"],
        "ambient_pressure_Pa": values["ambient_pressure"], "of_ratio": values["of_ratio"],
        "expansion_ratio": values["expansion_ratio"], "throat_area_m2": area,
        "cooling_height_m": height, "cycle_type": values["cycle_type"],
        "propellant_pair": values["propellant_pair"], "gg_layout": values["gg_layout"],
    }
    baseline["cooling_hardware"] = _cooling_baseline(calculation, values, area, height)
    if values["cycle_type"] == "ffsc":
        closure = calculation.get("steady_closure")
        if not isinstance(closure, dict) or closure.get("schema_version") != 1:
            raise ValueError("来源 FFSC 设计点缺少供给网络与能量闭合记录，请先重新计算设计点后建立节流基准。")
        network = closure.get("feed_network")
        if not isinstance(network, dict):
            raise ValueError("来源 FFSC 设计点缺少四支路供给网络，请先重新计算设计点。")
        target_mr = values["use_target_preburner_mr"] >= .5 or values["use_target_preburner_temp"] >= .5
        required = ["mdot_fuel_kg_s", "mdot_ox_kg_s"]
        if target_mr:
            required.extend(("ox_preburner_mr_target", "fuel_preburner_mr_target"))
        quantities = {key: finite(summary.get(key)) for key in required}
        if any(value is None or value <= 0 for value in quantities.values()):
            raise ValueError("来源 FFSC 设计点缺少有效总流量或预燃室混合比，不能独立校核四支路。")
        checked = audit_network(network, total_fuel=quantities["mdot_fuel_kg_s"], total_ox=quantities["mdot_ox_kg_s"],
                                mr_ox=quantities.get("ox_preburner_mr_target"),
                                mr_fuel=quantities.get("fuel_preburner_mr_target"))
        if checked["status"] != "pass":
            raise ValueError("来源 FFSC 四支路供给网络重新校核未通过，请先修正并重新计算设计点。")
        energy = audit_energy(calculation.get("states"), components)
        if energy["status"] != "pass":
            raise ValueError("来源 FFSC 设计点能量守恒重新校核未通过或证据不足，请先重新计算闭合设计点。")
        try:
            baseline["feed_hardware"] = deepcopy(validate_hardware(network.get("hardware_snapshot")))
        except ValueError as exc:
            raise ValueError("来源 FFSC 记录没有完整有效的四支路阻力快照，请重新计算设计点。") from exc
        solution = (calculation.get("convergence") or {}).get("solution")
        warm_state = None
        if isinstance(solution, dict) and isinstance(solution.get("x"), list) and len(solution["x"]) == 3:
            warm_values = [*solution["x"], *(solution.get(key) for key in
                           ("chamber_temp_guess", "chamber_gamma", "molar_mass"))]
            if all(finite(value) is not None and value > 0 for value in warm_values) and solution["chamber_gamma"] > 1:
                warm_state = deepcopy(solution)
        baseline["warm_state"] = warm_state
        if warm_state is not None:
            for key, source_key in (("warm_state_thrust_N", "thrust_N"), ("warm_state_pc_Pa", "chamber_pressure_Pa")):
                actual = finite(summary.get(source_key))
                if actual is not None and actual > 0:
                    baseline[key] = actual
        baseline["preburner_mr_targets"] = {
            "ox": quantities["ox_preburner_mr_target"], "fuel": quantities["fuel_preburner_mr_target"],
        } if target_mr else None
    return values, baseline


def normalize_request(value, source):
    if not isinstance(value, dict) or set(value) - {"source_job_id", "throttle_min", "throttle_steps"}:
        raise ValueError("节流请求只接受来源记录、最低节流比例与扫描点数。")
    minimum = finite(value.get("throttle_min", 0.4))
    steps = finite(value.get("throttle_steps", 5))
    if minimum is None or not 0.05 <= minimum <= 0.99:
        raise ValueError("最低节流比例须为 0.05–0.99（5%–99%）。")
    if steps is None or not steps.is_integer() or not 2 <= steps <= MAX_POINTS:
        raise ValueError(f"节流扫描点数须为 2–{MAX_POINTS} 的整数。")
    if value.get("source_job_id") != source["id"]:
        raise ValueError("来源计算记录不匹配。")
    source_baseline(source)
    return {"source_job_id": source["id"], "throttle_min": minimum, "throttle_steps": int(steps)}


def calculate_throttle(request, source, progress=None):
    from engineering_assessment import assess_engineering
    from gg_cycle import make_cycle_model
    from version import APP_VERSION

    request = normalize_request(request, source)
    values, baseline = source_baseline(source)
    environment = science_environment(values["propellant_pair"], values.get("methane_thermo_dataset", "gri30"))
    result = {
        "schema_version": 1, "application_version": APP_VERSION,
        "analysis_type": "steady_throttle", "source": {
            "job_id": source["id"], "project_id": source["project_id"], "revision": source["revision"],
            "created": source.get("created"), "inputs_sha256": fingerprint(source["inputs"]),
            "calculation_inputs_sha256": fingerprint(source["result"]["calculation"]["inputs"]),
            "result_sha256": fingerprint(source["result"]),
            "environment": source["result"].get("environment"),
        }, "request": deepcopy(request), "baseline": baseline, "environment": environment,
        "source_inputs": deepcopy(values), "complete": False, "points": [],
        "limitations": [
            "节流比例表示目标推力占来源设计点目标推力的比例；按各独立稳态工况求解，不是时间历程。",
            "主喷管喉部面积和膨胀比固定；启用冷却通道自动定尺寸时，通道高度冻结为来源设计结果。室压由固定喉部闭合反解。",
            "GG 排气喷管仍按各工况重算，不属于完整固定整机硬件模型；推力与比冲包含 GG 排气贡献并使用来源环境压力。",
            "入口条件、总混合比、效率和压降等模型假设沿用来源设计点；尚未使用完整泵/涡轮特性图及执行机构调节规律。",
            "每点独立显示数值收敛、喉部闭合与系统约束；失败点保留且不得用插值替代。约束通过不代表已验证可用节流范围。",
            "当前喷管模型未求解深节流下的流动分离与侧向载荷；最低通过点不能视为经验证的最低节流能力。",
            "准一维模式每点重算同一冷却几何的压力与焓沿程解；预算模式只沿用给定吸热和压降，不能称为固定硬件冷却预测。",
            "不求解启动/关机、动态响应、燃烧稳定性；凝结、物性范围及高压燃气 EOS 适用性仍需独立核查。",
        ],
    }
    if values["cycle_type"] == "ffsc":
        result["limitations"] = [line for line in result["limitations"] if not line.startswith("GG 排气")]
        result["limitations"].insert(2, "FFSC 冻结来源设计点四支路的管路与喷注阻力系数，并使用当前密度求压降；主喉部及冷却通道高度同时冻结。")
        result["limitations"].insert(3, "这仍是固定阻力下的最低压头与调节阀再匹配；缺少泵/涡轮特性图和阀门行程限制，不能称为完整固定硬件节流包线。")
    values["throttle_min"] = request["throttle_min"]
    values["throttle_steps"] = request["throttle_steps"]
    current_ratio = None
    warning_offset = 0

    def publish(phase):
        points = result["points"]
        result["response_gains"] = throttle_response_gains(result)
        result["counts"] = {"total": request["throttle_steps"], "completed": len(points),
                            "converged": sum(p["converged"] for p in points),
                            **{key: sum(p["status"] == status for p in points)
                               for key, status in (("passed", "pass"), ("review", "review"), ("failed", "failed"))}}
        if progress:
            progress({"iteration": len(points), "maximum": request["throttle_steps"],
                      "completed": len(points), "phase": phase, "current_ratio": current_ratio,
                      "points": deepcopy(points)}, deepcopy(result))

    def starting(index, total, label):
        nonlocal current_ratio, warning_offset
        warning_offset = len(caught)
        current_ratio = request["throttle_min"] + (1 - request["throttle_min"]) * (index - 1) / (total - 1)
        publish(f"求解节流工况 {label}")

    def record(raw):
        solved = raw.get("results")
        point = {"index": len(result["points"]) + 1, "throttle_ratio": raw["throttle_ratio"],
                 "thrust_target_N": raw["thrust_target_N"], "pc_Pa": None,
                 "converged": False, "error": str(raw.get("error") or "")[:1600],
                 "summary": {}, "inputs": None, "assessment": None, "steady_closure": {},
                 "warnings": list(dict.fromkeys(str(w.message) for w in caught[warning_offset:])), "status": "failed",
                 "geometry": {"status": "unknown", "throat_area_m2": None,
                              "reference_area_m2": baseline["throat_area_m2"],
                              "relative_error": None, "tolerance": AREA_TOLERANCE}}
        for key in ("cycle_evaluations", "cycle_evaluation_budget"):
            count = finite(raw.get(key))
            point[key] = int(count) if count is not None and count >= 0 and count.is_integer() else None
        if solved is not None:
            point["pc_Pa"] = raw.get("pc_Pa")
            point["summary"] = deepcopy(solved.summary)
            point["convergence"] = deepcopy(solved.convergence)
            point["steady_closure"] = deepcopy(getattr(solved, "steady_closure", {}))
            point["inputs"] = asdict(solved.inputs)
            assessment = assess_engineering(solved, scope="system")
            point["assessment"] = assessment
            nozzle = solved.components.get("nozzle")
            area = finite(getattr(nozzle, "outputs", {}).get("At_m2"))
            error = area / baseline["throat_area_m2"] - 1 if area is not None and area > 0 else None
            geometry_ok = error is not None and abs(error) < AREA_TOLERANCE
            point["geometry"].update(status="pass" if geometry_ok else "fail",
                                     throat_area_m2=area, relative_error=error)
            numerical_ok = assessment["numerical"]["status"] == "pass"
            point["converged"] = bool(raw.get("converged")) and numerical_ok and geometry_ok
            point["warnings"] = list(dict.fromkeys(list(solved.warnings) + point["warnings"]))
            if not geometry_ok:
                point["warnings"].append("固定喉部面积闭合未通过；该点不能作为固定几何节流解。")
            if point["converged"]:
                point["status"] = "pass" if assessment["status"] == "pass" else "review"
        missing = []
        point = plain(point, missing, "point")
        point["nonfinite_fields"] = missing
        result["points"].append(point)
        publish(f"已完成 {len(result['points'])}/{request['throttle_steps']} 个工况")

    publish("准备节流扫描")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        make_cycle_model(EngineInputs(**values)).throttle_sweep(
            progress_callback=starting, record_callback=record,
            design_baseline=deepcopy(baseline), include_cooling_marching=False)
    result["warnings"] = list(dict.fromkeys(str(w.message) for w in caught))
    result["complete"] = True
    publish("节流扫描完成")
    json.dumps(result, allow_nan=False)
    return result
