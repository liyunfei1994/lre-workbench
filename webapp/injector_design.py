"""Saved FFSC injector interfaces and preliminary coaxial-port geometry.

This view never sizes new holes or recomputes thermodynamics. Historical records
retain their stored dimensions and cannot borrow the current geometry contract.
"""
import math


def _obj(value):
    return value if isinstance(value, dict) else {}


def _num(value, positive=False):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) and (not positive or number > 0) else None
    except (ValueError, OverflowError):
        return None


def _calc(function):
    """Damaged historical numbers must produce missing evidence, never NaN/500."""
    try:
        return _num(function())
    except (ArithmeticError, ValueError, TypeError):
        return None


def _clean(value, depth=0):
    """Detach saved metadata and make nested values strictly JSON serializable."""
    if depth > 20:
        return None
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, (int, float)):
        return value if _num(value) is not None else None
    if isinstance(value, dict):
        return {k: _clean(v, depth + 1) for k, v in value.items() if isinstance(k, str)}
    if isinstance(value, (list, tuple)):
        return [_clean(v, depth + 1) for v in value]
    return None


def _check(label, actual, expected, unit="", rtol=2e-7, atol=1e-10):
    a, e = _num(actual), _num(expected)
    return {"label": label, "actual": a, "expected": e, "unit": unit,
            "status": "unavailable" if a is None or e is None else
            "pass" if math.isclose(a, e, rel_tol=rtol, abs_tol=atol) else "fail"}


def _state(state):
    state = _obj(state)
    return {key: _num(state.get(raw), True) for key, raw in (
        ("pressure_pa", "pressure"), ("temperature_k", "temperature"),
        ("mass_flow_kg_s", "mass_flow"))}


def _main(calc, result):
    comps, states = _obj(calc.get("components")), _obj(calc.get("states"))
    component = _obj(comps.get("injector"))
    o = _obj(component.get("outputs"))
    contract = _obj(_obj(component.get("profile")).get("geometry_contract"))
    dimensions = {key: _num(o.get(key), True) for key in (
        "face_diameter_m", "face_area_m2", "num_elements", "element_spacing_mm",
        "d_ox_mm", "d_post_mm", "delta_fuel_mm", "d_outer_mm", "post_wall_thickness_mm")}
    available = all(dimensions[key] is not None for key in (
        "face_diameter_m", "num_elements", "d_ox_mm", "d_post_mm", "delta_fuel_mm", "d_outer_mm"))
    inputs = _obj(calc.get("inputs"))
    main_state = _state(states.get("main_chamber"))
    chamber_pressure = main_state["pressure_pa"]
    checks = [_check("主室状态压力与保存设计输入", chamber_pressure, inputs.get("pc"), "Pa", atol=1.)]
    ports = []
    n = dimensions["num_elements"]
    shape_valid = (available and n.is_integer()
                   and dimensions["d_ox_mm"] < dimensions["d_post_mm"] < dimensions["d_outer_mm"])
    if available:
        checks.append({"label": "单元数与同轴流道尺寸关系", "status": "pass" if shape_valid else "fail"})
    d = dimensions
    areas = [None, None]
    if shape_valid:
        areas = [_calc(lambda: math.pi/4*(d["d_ox_mm"]*1e-3)**2),
                 _calc(lambda: math.pi/4*(d["d_outer_mm"]-d["d_post_mm"])*
                       (d["d_outer_mm"]+d["d_post_mm"])*1e-6)]
    checks += [
        _check("外环径与径向缝宽", _calc(lambda: (d["d_outer_mm"]-d["d_post_mm"])/2), d["delta_fuel_mm"], "mm"),
        _check("喷注面面积与直径", d["face_area_m2"], _calc(lambda: math.pi*d["face_diameter_m"]**2/4), "m²"),
        _check("中心管壁厚", _calc(lambda: (d["d_post_mm"]-d["d_ox_mm"])/2), d["post_wall_thickness_mm"], "mm"),
        _check("等效单元间距", d["element_spacing_mm"], _calc(lambda: 1000*math.sqrt(d["face_area_m2"]/n)), "mm"),
    ]
    chamber = _obj(_obj(comps.get("main_chamber")).get("outputs"))
    # The primary chamber diameter is required for a current complete record.
    checks.append(_check("喷注面与主室内径", d["face_diameter_m"], chamber.get("cc_diameter_m"), "m"))
    if "cc_area_m2" in chamber:
        checks.append(_check("喷注面与主室截面积", d["face_area_m2"], chamber.get("cc_area_m2"), "m²"))
    cooling_geometry = _obj(_obj(_obj(comps.get("cooling_channel")).get("profile")).get("geometry"))
    if "r_c_m" in cooling_geometry:
        checks.append(_check("喷注面与冷却热壁主室内径", d["face_diameter_m"],
                             _calc(lambda: 2*_num(cooling_geometry.get("r_c_m"), True)), "m"))
    nozzle_dimensions = _obj(_obj(result.get("nozzle_geometry")).get("dimensions"))
    if "chamber_diameter_m" in nozzle_dimensions:
        checks.append(_check("喷注面与推力室几何快照内径", d["face_diameter_m"], nozzle_dimensions.get("chamber_diameter_m"), "m"))
    for side, label in (("ox", "富氧燃气 · 中心圆孔"), ("fuel", "富燃燃气 · 外环缝")):
        state = _state(states.get(side + "_turbine_exhaust"))
        port = {"id": side, "label": label, **state,
                "pressure_drop_pa": _num(o.get("dp_" + side + "_Pa"), True),
                "density_kg_m3": _num(o.get("rho_" + side + "_kgm3"), True),
                "cd": _num(o.get("cd_" + side), True),
                "effective_velocity_m_s": _num(o.get("v_" + side + "_ms"), True),
                "required_area_m2": _num(o.get("a_" + side + "_required_per_element_m2"), True),
                "geometry_area_m2": areas[0 if side == "ox" else 1],
                "required_flow_kg_s": _num(o.get("mdot_" + side + "_kgs"), True),
                "geometry_mass_flow_kg_s": None, "relative_flow_error": None}
        per = _num(o.get("mdot_" + side + "_per_element_kgs"), True)
        demand, area = port["required_flow_kg_s"], port["geometry_area_m2"]
        rho, velocity, cd, dp = (port[k] for k in ("density_kg_m3", "effective_velocity_m_s", "cd", "pressure_drop_pa"))
        flow = _calc(lambda: n * rho * velocity * area)
        error = _calc(lambda: flow / demand - 1)
        port.update(geometry_mass_flow_kg_s=flow, relative_flow_error=error)
        # Do not skip required comparisons when a field is missing: a partial
        # current contract must remain incomplete, not accidentally pass.
        checks += [
            _check(label + "单元数 × 单元流量", _calc(lambda: n * per), demand, "kg/s"),
            _check(label + "与涡轮排气流量", demand, state["mass_flow_kg_s"], "kg/s"),
            _check(label + "压差与系统状态", dp, _calc(lambda: state["pressure_pa"] - chamber_pressure), "Pa", atol=1.),
            _check(label + "压差与保存压降比", dp, _calc(lambda: _num(o.get("dp_" + side + "_ratio"), True)*chamber_pressure), "Pa", atol=1.),
            _check(label + "孔口模型速度", velocity, _calc(lambda: cd*math.sqrt(2*dp/rho)), "m/s"),
            _check(label + "需求面积与流量", port["required_area_m2"], _calc(lambda: demand/n/rho/velocity), "m²", atol=1e-14),
            _check(label + "面积闭合", area, port["required_area_m2"], "m²", atol=1e-14),
            _check(label + "保存几何面积与返回尺寸", o.get("a_" + side + "_geometry_per_element_m2"), area, "m²", atol=1e-14),
            _check(label + "几何回算质量流量", flow, demand, "kg/s"),
            _check(label + "几何流量与源状态", flow, state["mass_flow_kg_s"], "kg/s"),
            _check(label + "保存几何流量与独立回算", o.get("mdot_" + side + "_geometry_kgs"), flow, "kg/s"),
            _check(label + "保存流量误差与独立回算", o.get("mass_flow_relative_error_" + side), error),
        ]
        if "dp_" + side + "_MPa" in o:
            checks.append(_check(label + "压差单位别名", dp, _calc(lambda: _num(o.get("dp_" + side + "_MPa"))*1e6), "Pa", atol=1.))
        source_density = _obj(states.get(side + "_turbine_exhaust")).get("density")
        if source_density is not None:
            checks.append(_check(label + "密度与源状态", rho, source_density, "kg/m³"))
        ports.append(port)
    checks.append(_check("主室入口总质量流量", _calc(lambda: sum(p["required_flow_kg_s"] for p in ports)),
                         main_state["mass_flow_kg_s"], "kg/s"))
    checks.append(_check("保存几何闭合标识", o.get("geometry_closure_passed"), 1.0))
    current = (_num(contract.get("version")) == 1 and contract.get("type") == "coaxial_circular_annular"
               and contract.get("flow_model") == "incompressible_orifice")
    status = ("unavailable" if not available else "inconsistent" if any(c["status"] == "fail" for c in checks)
              else "legacy" if not current else "incomplete" if any(c["status"] == "unavailable" for c in checks)
              else "consistent")
    warnings = [w for w in contract.get("warnings", []) if isinstance(w, str)] if isinstance(contract.get("warnings"), list) else []
    return {"id": "main", "component_id": "injector", "label": "主燃烧室喷注器", "available": available,
            "status": status, "shape_valid": bool(shape_valid), "dimensions": dimensions, "ports": ports,
            "checks": checks, "warnings": warnings, "geometry_contract": _clean(contract),
            "chamber_pressure_pa": chamber_pressure,
            "location": "富氧涡轮排气与富燃涡轮排气汇入主燃烧室的入口喷注面。",
            "type": "同轴中心圆孔＋外环缝（概念构型）",
            "limitations": ["本轮保留不可压缩孔口关系；气体可压缩性、临界流及高压燃气物性适用性尚未校核。",
                "单元数量、流量系数和中心管壁厚为概念假设；面积等效间距不代表已经完成孔位排布。",
                "旋流为原模型的经验假设；尚未定义切向孔、旋流槽、喷注柱长度、缩进量或外筒壁厚。",
                "同一模型下的几何连续性通过，不代表喷注性能、燃烧效率、强度或制造可行性已验证。"]}


def _preburner(calc, side):
    target = side + "_preburner"
    network = _obj(_obj(calc.get("steady_closure")).get("feed_network"))
    rows = network.get("branches", [])
    states = _obj(calc.get("states"))
    component = _obj(_obj(_obj(calc.get("components")).get(target)).get("outputs"))
    ports, checks = [], []
    outlet = _state(states.get(side + "_preburner_outlet"))
    for propellant in ("ox", "fuel"):
        suffix = "opb" if side == "ox" else "fpb"
        branch_id = propellant + "_to_" + suffix
        matches = [_obj(r) for r in rows if _obj(r).get("id") == branch_id] if isinstance(rows, list) else []
        b = matches[0] if matches else {}
        state = _state(states.get("branch_" + branch_id))
        port = {"id": propellant, "label": "氧化剂支路" if propellant == "ox" else "燃料支路",
                      "mass_flow_kg_s": _num(b.get("mass_flow_kg_s"), True),
                      "chamber_pressure_pa": _num(b.get("destination_pressure_Pa"), True),
                      "pressure_drop_pa": _num(b.get("injector_drop_Pa"), True),
                      "branch_temperature_k": state["temperature_k"],
                      "temperature_basis": "支路集总损失后的状态，不是已解析的喷孔入口静温"}
        prefix = port["label"]
        checks += [
            _check(prefix + "支路记录唯一性", len(matches) if matches else None, 1),
            _check(prefix + "网络流量与支路状态", port["mass_flow_kg_s"], state["mass_flow_kg_s"], "kg/s"),
            _check(prefix + "目标压力与预燃室状态", port["chamber_pressure_pa"], outlet["pressure_pa"], "Pa", atol=1.),
            _check(prefix + "集总支路压力与预燃室压力", state["pressure_pa"], outlet["pressure_pa"], "Pa", atol=1.),
            _check(prefix + "网络温度与支路状态", b.get("temperature_K"), port["branch_temperature_k"], "K"),
            # Recompute from the saved individual pressures, rather than trust
            # an old row status/residual or merely require a positive drop.
            _check(prefix + "喷注压降与独立压力预算", port["pressure_drop_pa"],
                   _calc(lambda: _num(b.get("source_pressure_Pa"), True)
                         - _num(b.get("line_drop_Pa")) - _num(b.get("valve_drop_Pa"))
                         - port["chamber_pressure_pa"]), "Pa", atol=1.),
        ]
        if network.get("mode") == "design":
            ratio = _num(_obj(network.get("design_loss_inputs")).get("injector_ratio"), True)
            checks.append(_check(prefix + "喷注压降与保存设计压降比", port["pressure_drop_pa"],
                                 _calc(lambda: ratio * port["chamber_pressure_pa"]), "Pa", atol=1.))
            if "injector_dp_Pa" in component:
                checks.append(_check(prefix + "喷注压降与预燃室部件", port["pressure_drop_pa"],
                                     component.get("injector_dp_Pa"), "Pa", atol=1.))
        elif network.get("mode") == "fixed_resistance":
            # Off-design branch drops need not match the design-ratio value
            # still reported by the lumped preburner component.
            coefficient = _obj(_obj(_obj(network.get("hardware_snapshot")).get("branches")).get(branch_id))
            checks.append(_check(prefix + "喷注压降与保存固定阻力", port["pressure_drop_pa"],
                                 _calc(lambda: _num(coefficient.get("K_injector"), True)
                                       * port["mass_flow_kg_s"] ** 2 / _num(b.get("density_kg_m3"), True)),
                                 "Pa", atol=1.))
        ports.append(port)
    checks.append(_check("预燃室入口总质量流量", _calc(lambda: sum(p["mass_flow_kg_s"] for p in ports)),
                         outlet["mass_flow_kg_s"], "kg/s"))
    boundary_available = all(p[k] is not None for p in ports for k in (
        "mass_flow_kg_s", "chamber_pressure_pa", "pressure_drop_pa", "branch_temperature_k"))
    boundary_status = ("inconsistent" if any(c["status"] == "fail" for c in checks)
                       else "incomplete" if not boundary_available or any(c["status"] == "unavailable" for c in checks)
                       else "consistent")
    label = "富氧预燃室喷注器" if side == "ox" else "富燃预燃室喷注器"
    return {"id": side, "component_id": target, "label": label, "status": "boundary_only",
            "boundary_status": boundary_status, "boundary_available": boundary_available, "checks": checks,
            "location": "两股推进剂进入" + ("富氧" if side == "ox" else "富燃") + "预燃室的入口喷注面，位于对应涡轮上游。",
            "type": "结构未选型；当前模型仅记录支路流量与喷注压降", "ports": ports,
            "missing_geometry": ["喷注结构与单元数", "各孔口流量系数", "喷孔入口局部状态与流动模型", "壁厚及布置约束"],
            "note": "此处入口为供给支路的氧化剂和燃料；富氧／富燃描述燃烧后的组成。相态需按局部压力、温度与物性判断，不能直接套用主室气—气喷注几何。"}


def injector_design_view(result, record_id=None):
    result = _obj(result)
    calc = _obj(result.get("calculation"))
    inputs = _obj(calc.get("inputs"))
    view = {"schema_version": 1, "record_id": _clean(record_id), "available": False, "stations": [],
            "source": "所选计算记录；不读取当前草稿，不重算热力学或覆盖历史尺寸"}
    if inputs.get("cycle_type") != "ffsc":
        view["reason"] = "本页当前提供液氧／甲烷 FFSC 的三处喷注接口；请选取对应计算记录。"
        return view
    if inputs.get("propellant_pair") not in (None, "lox_methane"):
        view["reason"] = "本页尚未定义此推进剂组合的喷注接口。"
        return view
    view.update(available=True, stations=[_main(calc, result), _preburner(calc, "ox"), _preburner(calc, "fuel")])
    return view
