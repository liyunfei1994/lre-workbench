"""Bounded channel trade study at a saved design point's thermal boundaries.

This is a one-way boundary study, not an engine-cycle optimization. Selected
hardware must be saved as a new design and reclosed in the full cycle.
"""
from copy import deepcopy
import itertools
import math

MAX_CANDIDATES = 64
DIMENSIONS = {
    "channel_counts": (10, 2000, True),
    "widths_mm": (0.3, 12, False),
    "heights_mm": (0.5, 20, False),
    "wall_thicknesses_mm": (0.2, 3, False),
}


def number(value, label, low=0, high=float("inf"), integer=False):
    try:
        valid = not isinstance(value, bool) and isinstance(value, (float, int)) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{label}必须为有限数值")
    if not low <= value <= high or (integer and int(value) != value):
        raise ValueError(f"{label}超出允许输入范围或不是整数")
    return int(value) if integer else float(value)


def source_data(source):
    from webapp.solver import input_values
    if source.get("status") != "succeeded" or source.get("kind", "design") != "design":
        raise ValueError("请选择已完成的稳态设计记录")
    result = source.get("result") or {}
    calc = result.get("calculation") or {}
    values, _ = input_values(calc.get("inputs") or source["inputs"], saved_snapshot=True)
    component = (calc.get("components") or {}).get("cooling_channel") or {}
    profile = component.get("profile") or {}
    geom = profile.get("geometry") or {}
    if (values["propellant_pair"] != "lox_methane" or not values["cooling_bartz_mode"]
            or profile.get("model_id") != "regen_quasi1d_ph_v1" or geom.get("version") != 3):
        raise ValueError("通道筛选需要含规范几何和沿程热侧状态的甲烷准一维设计记录")
    if values.get("cooling_channel_table_json", "").strip():
        raise ValueError("当前筛选使用恒定通道数、等高且宽度随半径变化的通道；已有沿程表不能静默替换")
    channels = [c for c in profile.get("cells", []) if c.get("kind") == "channel"]
    if len(channels) != len(geom.get("cells", [])) or not channels:
        raise ValueError("来源记录缺少完整热单元")
    if any(not isinstance(c.get("hot_side"), dict) for c in channels):
        raise ValueError("来源记录未保存沿程热侧状态，请重新运行设计点")
    if calc.get("summary", {}).get("numerical_converged") != 1 or profile.get("hot_side_coupling", {}).get("status") != "pass":
        raise ValueError("来源设计点尚无完整数值收敛与热侧耦合证据，请先重新运行设计点")
    from webapp.throttle import _wall_material_binding
    _wall_material_binding(profile, values)
    return values, calc, component, profile, geom, channels


def normalize_request(request, source):
    source_data(source)
    allowed = {"source_job_id", *DIMENSIONS, "constraints"}
    if not isinstance(request, dict) or set(request) - allowed:
        raise ValueError("通道筛选请求包含未知字段")
    if request.get("source_job_id") != source["id"]:
        raise ValueError("来源记录不一致")
    clean = {"source_job_id": source["id"]}
    size = 1
    for name, (lo, hi, integer) in DIMENSIONS.items():
        vals = request.get(name)
        if not isinstance(vals, list) or not 1 <= len(vals) <= 16:
            raise ValueError(f"{name}需填写1至16个候选值")
        clean[name] = sorted(set(number(v, name, lo, hi, integer) for v in vals))
        size *= len(clean[name])
    if size > MAX_CANDIDATES:
        raise ValueError(f"组合数{size}超过{MAX_CANDIDATES}，请缩小搜索范围")
    raw = request.get("constraints", {})
    limits = {"wall_temp_limit_K": (1, 5000), "max_pressure_drop_MPa": (0.001, 500),
              "min_rib_mm": (0.001, 20), "max_aspect_ratio": (0.1, 200),
              "max_velocity_ms": (0.01, 2000)}
    if not isinstance(raw, dict) or set(raw) - {*limits, "wall_limit_status", "wall_limit_basis", "basis"}:
        raise ValueError("筛选约束包含未知字段")
    constraints = {k: None if raw.get(k) is None else number(raw[k], k, *bounds) for k, bounds in limits.items()}
    status = raw.get("wall_limit_status", "unset")
    if status not in ("unset", "provisional", "documented"):
        raise ValueError("壁温约束状态无效")
    for key in ("wall_limit_basis", "basis"):
        text = raw.get(key, "")
        if not isinstance(text, str) or len(text) > 2000:
            raise ValueError("约束依据须为不超过2000字的文本")
        constraints[key] = text.strip()
    if status != "unset" and constraints["wall_temp_limit_K"] is None:
        raise ValueError("启用壁温约束时需填写限值")
    if status == "documented" and not constraints["wall_limit_basis"]:
        raise ValueError("已有依据的壁温约束需填写依据")
    constraints["wall_limit_status"] = status
    clean["constraints"] = constraints
    return clean


def configuration(source):
    values, calc, component, profile, geom, channels = source_data(source)
    throat = min(geom["nodes"], key=lambda n: abs(n["x_m"] - geom["x_throat_m"]))
    port = profile["nodes"][0]
    inlet = next(n for n in profile["nodes"] if n.get("kind") == "channel")
    return {"source_job_id": source["id"], "source_revision": source["revision"],
            "channel_counts": [throat["n_channels"]], "widths_mm": [throat["width_m"]*1000],
            "heights_mm": [throat["height_m"]*1000], "wall_thicknesses_mm": [throat["wall_thickness_m"]*1000],
            "constraints": {"wall_temp_limit_K": values["cooling_wall_temp_limit_K"],
                "wall_limit_status": values.get("cooling_wall_temp_limit_status", "provisional"),
                "wall_limit_basis": values.get("cooling_wall_temp_limit_basis", ""),
                "max_pressure_drop_MPa": None, "min_rib_mm": None, "max_aspect_ratio": None,
                "max_velocity_ms": None, "basis": ""},
            "boundary": {"T_in_K": port["T_K"], "p_in_MPa": port["p_Pa"]/1e6,
                "mdot_coolant_kg_s": inlet["rho_kgm3"]*inlet["velocity_ms"]*inlet["A_flow_m2"],
                "source_wall_max_K": profile["summary"]["T_wall_hot_max_K"],
                "source_dp_MPa": profile["summary"]["dp_total_Pa"]/1e6},
            "maximum_candidates": MAX_CANDIDATES}


def metrics(profile):
    geom, summary = profile["geometry"], profile["summary"]
    fluid = [n for n in profile["nodes"] if n.get("kind") == "channel"]
    throat = min(fluid, key=lambda n: abs(n["x_m"]-geom["x_throat_m"]))
    return {"wall_max_K": summary["T_wall_hot_max_K"], "dp_MPa": summary["dp_total_Pa"]/1e6,
        "Q_MW": summary["Q_total_W"]/1e6, "T_out_K": summary["T_out_K"], "p_out_MPa": summary["p_out_Pa"]/1e6,
        "velocity_inlet_ms": fluid[0]["velocity_ms"], "velocity_throat_ms": throat["velocity_ms"],
        "velocity_max_ms": max(n["velocity_ms"] for n in fluid),
        "min_rib_mm": min(n["rib_m"] for n in geom["nodes"])*1000,
        "throat_pitch_mm": throat["pitch_m"]*1000, "throat_rib_mm": throat["rib_m"]*1000,
        "max_aspect_ratio": max(n["height_m"]/n["width_m"] for n in geom["nodes"]),
        "energy_residual_W": profile["balance"]["energy_residual_W"]}


def assess(values, constraints, material):
    specs = [("wall_temperature", "wall_max_K", "wall_temp_limit_K", False),
             ("pressure_drop", "dp_MPa", "max_pressure_drop_MPa", False),
             ("rib_width", "min_rib_mm", "min_rib_mm", True),
             ("aspect_ratio", "max_aspect_ratio", "max_aspect_ratio", False),
             ("velocity", "velocity_max_ms", "max_velocity_ms", False)]
    checks = []
    for name, metric, limit, lower in specs:
        target = constraints[limit]
        if name == "wall_temperature" and constraints["wall_limit_status"] == "unset":
            target = None
        ok = target is not None and (values[metric] >= target if lower else values[metric] <= target)
        checks.append({"id": name, "actual": values[metric], "limit": target,
                       "status": "unset" if target is None else "pass" if ok else "fail"})
    failed = any(c["status"] == "fail" for c in checks)
    complete = all(c["status"] != "unset" for c in checks[:4])
    verified = (constraints["wall_limit_status"] == "documented" and bool(constraints["basis"])
                and material.get("status") == "within_range" and material.get("manufacturing_match") == "matched")
    status = "rejected" if failed else "incomplete" if not complete else "meets_declared" if verified else "provisional"
    return {"status": status, "checks": checks, "material_status": material.get("status", "missing_evidence"),
            "material_out_of_range_cells": material.get("out_of_range_cell_count"),
            "manufacturing_match": material.get("manufacturing_match", "unknown")}


def pareto(rows):
    eligible = [r for r in rows if r.get("status") == "computed" and r["assessment"]["status"] != "rejected"]
    for row in rows:
        row["pareto"] = False
    for row in eligible:
        a = row["metrics"]
        row["pareto"] = not any(
            b["metrics"]["wall_max_K"] <= a["wall_max_K"] and b["metrics"]["dp_MPa"] <= a["dp_MPa"]
            and (b["metrics"]["wall_max_K"] < a["wall_max_K"] or b["metrics"]["dp_MPa"] < a["dp_MPa"])
            for b in eligible if b is not row)


def _check_fixed_contour(original, candidate):
    """Bind saved gas states to the same wall contour, not merely axial x.

    Channel count, width, height and wall thickness deliberately vary here.
    Their flow areas, hydraulic diameters, pitches and ribs must therefore not
    be part of this check. The gas contour and cooled surface stay fixed.
    """
    def require(ok, field):
        if not ok:
            raise ValueError(f"候选固定热侧轮廓与来源记录不一致（{field}），请重新运行来源设计点")

    def numbers(a, b, fields, label):
        for field in fields:
            left, right = a.get(field), b.get(field)
            valid = all(isinstance(value, (float, int)) and not isinstance(value, bool)
                        and math.isfinite(value) for value in (left, right))
            require(valid and math.isclose(left, right, rel_tol=1e-11, abs_tol=1e-12), label+"."+field)

    numbers(original, candidate, (
        "initial_expansion_angle_deg", "exit_angle_deg", "L_nozzle_m", "chamber_blend_radius_m",
        "x_throat_m", "r_t_m", "r_c_m", "L_cyl_m", "L_conv_m",
        "throat_upstream_radius_m", "throat_downstream_radius_m", "conv_half_angle_deg",
        "bell_fraction", "full_exit_x_m", "coverage_end_x_m", "cover_eps", "expansion_ratio",
        "path_length_m", "hot_area_m2"), "geometry")
    for field in ("source", "nozzle_contour_method", "coordinate_origin", "flow_direction",
                  "full_nodes_direction"):
        require(original.get(field) is not None and original.get(field) == candidate.get(field), field)
    for field in ("material_boundaries_m",):
        left, right = original.get(field), candidate.get(field)
        require(isinstance(left, list) and isinstance(right, list) and len(left) == len(right), field)
        for index, (a, b) in enumerate(zip(left, right)):
            numbers({"x_m":a}, {"x_m":b}, ("x_m",), f"{field}[{index}]")
    groups = (
        ("full_nodes", ("x_m", "r_m", "s_m", "area_ratio", "area_m2")),
        ("nodes", ("x_m", "r_m", "s_m", "area_ratio")),
        ("cells", ("x_m", "r_m", "s_m", "ds_m", "dA_hot_m2", "area_ratio")),
    )
    for name, fields in groups:
        left, right = original.get(name), candidate.get(name)
        require(isinstance(left, list) and isinstance(right, list) and bool(left) and len(left) == len(right), name)
        for index, (a, b) in enumerate(zip(left, right)):
            require(isinstance(a, dict) and isinstance(b, dict), f"{name}[{index}]")
            numbers(a, b, fields, f"{name}[{index}]")
            if name == "full_nodes":
                require(a.get("cooling_covered") == b.get("cooling_covered"), f"{name}[{index}].cooling_covered")


def calculate_cooling_design(request, source, progress=None):
    from cooling_marching import march_regen_circuit
    from task_control import checkpoint
    from webapp.throttle import _wall_material_binding
    clean = normalize_request(request, source)
    values, calc, component, profile, geom, cells = source_data(source)
    summary, port, limits = calc["summary"], profile["nodes"][0], clean["constraints"]
    # Use saved inlet mass flow. For all supported cycles it is also recoverable
    # from any saved channel node's rho*u*A, without assuming total fuel flow.
    inlet = next(n for n in profile["nodes"] if n.get("kind") == "channel")
    mdot = inlet["rho_kgm3"]*inlet["velocity_ms"]*inlet["A_flow_m2"]
    base = dict(mdot_coolant=mdot, T_in=port["T_K"], p_in=port["p_Pa"],
        mdot_total=summary["mdot_total_kg_s"], pc=summary["chamber_pressure_Pa"],
        T_c=summary["chamber_temperature_K"], gamma=summary["chamber_gamma"],
        molar_mass=summary["chamber_molar_mass"], r_t=geom["r_t_m"], r_c=geom["r_c_m"], L_cyl=geom["L_cyl_m"],
        expansion_ratio=values["expansion_ratio"], cover_eps=values["cooling_cover_eps"],
        bell_fraction=geom["bell_fraction"], conv_half_angle_deg=geom["conv_half_angle_deg"],
        nozzle_contour_method=geom["nozzle_contour_method"],
        nozzle_initial_angle_deg=geom["initial_expansion_angle_deg"], nozzle_exit_angle_deg=geom["exit_angle_deg"],
        n_points=int(values["cooling_grid_points"]), counterflow=profile["flow_direction"] == "counterflow",
        hot_side_profile=[dict(c["hot_side"], x_m=c["x_m"]) for c in cells],
        inlet_enthalpy_Jkg=port["h_Jkg"], wall_thermal_dataset_id=values["wall_thermal_dataset_id"],
        wall_manufacturing_state=values["wall_manufacturing_state"], wall_material_zones=values["wall_material_zones_json"],
        wall_conductivity=values["cooling_wall_conductivity"], htc_enhancement=values["cooling_htc_enhancement"],
        rel_roughness=values["cooling_channel_rel_roughness"], k_minor=values["cooling_channel_k_minor"],
        gas_emissivity=values["cooling_gas_emissivity"],
        wall_temp_limit_K=limits["wall_temp_limit_K"] or values["cooling_wall_temp_limit_K"],
        wall_temp_limit_status=limits["wall_limit_status"], wall_temp_limit_basis=limits["wall_limit_basis"])
    candidates = list(itertools.product(*(clean[name] for name in DIMENSIONS)))
    result = {"schema_version": 1, "kind": "cooling_design", "request": clean,
        "source_job_id": source["id"], "source_revision": source["revision"],
        "material_binding": _wall_material_binding(profile, values),
        "scope": "fixed_saved_hot_side_and_coolant_inlet_boundary_screening",
        "scope_note": "沿用来源记录的热气沿程状态及冷却入口压力、温度、流量；候选吸热尚未反馈整机。采纳几何后必须重新运行设计点。",
        "boundary": configuration(source)["boundary"], "total": len(candidates), "candidates": []}
    for index, (count, width, height, wall) in enumerate(candidates):
        checkpoint()
        geometry = {"count": count, "width_mm": width, "height_mm": height, "wall_mm": wall}
        row = {"id": index+1, "geometry": geometry, "status": "failed", "pareto": False}
        try:
            solved = march_regen_circuit(**base, n_channels=count, channel_width_m=width/1000,
                channel_height_m=height/1000, wall_thickness_m=wall/1000)
            candidate = solved["profile"]
            _check_fixed_contour(geom, candidate["geometry"])
            original_x = [c["x_m"] for c in cells]
            actual_x = [c["x_m"] for c in candidate["cells"] if c["kind"] == "channel"]
            if original_x != actual_x:
                raise ValueError("候选热单元坐标与来源热边界不一致")
            row.update(status="computed", metrics=metrics(candidate), profile=candidate)
            row["assessment"] = assess(row["metrics"], limits, candidate["material_applicability"])
        except (ValueError, ArithmeticError) as exc:
            row["error"] = str(exc)[:1200]
        result["candidates"].append(row)
        pareto(result["candidates"])
        if progress:
            progress({"iteration": index+1, "maximum": len(candidates), "phase": "通道候选筛选"}, deepcopy(result))
    return result
