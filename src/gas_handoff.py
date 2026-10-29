"""Record-bound gas handoff and independent elemental/enthalpy accounting.

This diagnostic is explicit work, not a GET-time property reconstruction. It
never mutates a saved record, selects current input defaults, drops species or
claims that internal conservation validates the high-pressure gas EOS.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import math
from pathlib import Path


STATIONS = (
    ("branch_ox_to_opb", "富氧预燃室氧入口"), ("branch_fuel_to_opb", "富氧预燃室燃料入口"),
    ("branch_ox_to_fpb", "富燃预燃室氧入口"), ("branch_fuel_to_fpb", "富燃预燃室燃料入口"),
    ("ox_preburner_outlet", "富氧预燃室出口／涡轮入口"),
    ("fuel_preburner_outlet", "富燃预燃室出口／涡轮入口"),
    ("ox_turbine_exhaust", "富氧涡轮排气／主室入口"),
    ("fuel_turbine_exhaust", "富燃涡轮排气／主室入口"),
    ("main_chamber", "主燃烧室储腔"),
)


def _dict(value):
    return value if isinstance(value, dict) else {}


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except (OverflowError, ValueError):
        return None


def _check(key, label, left=None, right=None, *, unit="", reason="", abs_tol=1e-8, rel_tol=1e-8, status=None):
    left, right = _number(left), _number(right)
    difference = left - right if left is not None and right is not None else None
    if status is None:
        status = ("unavailable" if difference is None else
                  "pass" if math.isclose(left, right, abs_tol=abs_tol, rel_tol=rel_tol) else "fail")
    return {"id": key, "label": label, "status": status,
            "left_si": left, "right_si": right, "difference_si": difference,
            "unit": unit, "reason": reason,
            "absolute_tolerance": abs_tol if difference is not None else None,
            "relative_tolerance": rel_tol if difference is not None else None,
            "tolerance_si": max(abs_tol, rel_tol * max(abs(left), abs(right))) if difference is not None else None}


def load_verified_gas_model(model: dict):
    """Load only the mechanism whose exact identity/hash was saved in a record.

    A new independent Solution is returned; no thread-local production state is
    mutated. Missing hashes, changed bytes, unknown paths and EOS mismatches fail.
    HEOS reactant ports use this phase only for their chemical elemental data.
    """
    import cantera as ct
    model = _dict(model)
    filename, phase, digest = (model.get(key) for key in ("mechanism", "phase", "mechanism_sha256"))
    allowed = {"gri30.yaml", "gri30_highT.yaml", "nDodecane_Reitz.yaml"}
    if filename not in allowed or not isinstance(phase, str) or not phase or not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("缺少可信的保存机制／相／SHA-256；不能借用当前默认物性。")
    raw = (Path(ct.__file__).resolve().parent / "data" / filename).read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("当前热化学文件 SHA-256 与计算记录不符；不能重算该记录。")
    gas = ct.Solution(yaml=raw.decode("utf-8"), name=phase)
    expected_eos = model.get("chemical_reference_eos") if model.get("eos") == "HEOS" else model.get("eos")
    if expected_eos and expected_eos != gas.thermo_model:
        raise ValueError("保存的燃气 EOS 与热化学相定义不符。")
    expected_reference = ("cantera_ndodecane_coolprop_600K_ideal_reference_v2" if filename == "nDodecane_Reitz.yaml"
                          else f"cantera_{Path(filename).stem}_coolprop_298p15K_ideal_reference_v2")
    if model.get("enthalpy_reference_id") and model["enthalpy_reference_id"] != expected_reference:
        raise ValueError("保存的化学焓基准与已核对的物种热化学文件不兼容。")
    return gas


def _record_model(state, environment, *, pure=False):
    metadata = _dict(state.get("metadata"))
    if _dict(metadata.get("model")):
        return deepcopy(metadata["model"])
    physics = _dict(environment.get("physics"))
    scope = _dict(environment.get("thermodynamic_scope"))
    filename = metadata.get("thermochemical_data") or physics.get("mechanism")
    # A record-level fingerprint applies only to the exact same named file.
    digest = environment.get("mechanism_sha256") if filename == physics.get("mechanism") else None
    return {"mechanism": filename, "phase": physics.get("phase"),
            "mechanism_sha256": digest, "cantera_version": _dict(environment.get("dependencies")).get("cantera"),
            "eos": scope.get("pure_fluid_backend") if pure else scope.get("gas_eos"),
            "chemical_reference_eos": scope.get("gas_eos") if pure else None,
            "enthalpy_reference_id": metadata.get("reference_id"),
            "composition_basis": metadata.get("composition_basis", "mass_fraction"),
            "identity_source": "saved_record_environment", "phase_set": [physics.get("phase")],
            "equilibrium_scope": "pure_reactant" if pure else "gas_phase_only",
            "high_pressure_validation": "not_validated"}


def _composition_elements(gas, composition):
    from thermo import _dict_to_Y
    gas.Y = _dict_to_Y(gas, composition)
    return {name: float(gas.elemental_mass_fraction(name)) for name in gas.element_names}


def _station(key, label, state, environment, gas_cache):
    state = _dict(state)
    metadata = _dict(state.get("metadata"))
    pure = key.startswith("branch_")
    model = _record_model(state, environment, pure=pure)
    composition = deepcopy(_dict(state.get("composition")))
    basis = deepcopy(_dict(metadata.get("state_basis"))) or {
        "kind": "unspecified", "reason": "保存记录未提供逐端口总／静态定义；未推定或转换。"}
    row = {"id": key, "label": label, "p_Pa": _number(state.get("pressure")),
           "T_K": _number(state.get("temperature")), "mass_flow_kg_s": _number(state.get("mass_flow")),
           "h_Jkg": _number(state.get("enthalpy")), "entropy_JkgK": _number(metadata.get("entropy_JkgK")),
           "density_kg_m3": _number(state.get("density")), "composition_mass_fractions": composition,
           "element_mass_fractions": {}, "element_fluxes_kg_s": {}, "model": model,
           "state_basis": basis, "source_ports": deepcopy(metadata.get("source_ports", [])), "checks": []}
    row["property_definitions"] = deepcopy(_dict(metadata.get("property_definitions")))
    checks = row["checks"]
    for value_key in ("p_Pa", "T_K", "mass_flow_kg_s", "h_Jkg"):
        value = row[value_key]
        ok = value is not None and (value_key == "h_Jkg" or value > 0)
        checks.append(_check(f"{key}.{value_key}", f"{label}：{value_key}", status="pass" if ok else "unavailable",
                             reason="要求有限值，压力、温度及流量必须为正。"))
    checks.append(_check(f"{key}.state_basis", f"{label}：总静态口径",
                         status="unavailable" if basis.get("kind") == "unspecified" else "pass",
                         reason=basis.get("reason", "保存的显式端口约定。")))
    ref = model.get("enthalpy_reference_id")
    checks.append(_check(f"{key}.enthalpy_reference", f"{label}：化学焓参考身份",
                         status="pass" if isinstance(ref, str) and ref else "unavailable"))
    if metadata.get("reference_id") and ref and metadata["reference_id"] != ref:
        checks.append(_check(f"{key}.reference_identity_consistency", f"{label}：参考身份一致性",
                             status="fail", reason="端口 reference_id 与其保存的物性模型身份冲突。"))
    try:
        cache_key = (model.get("mechanism"), model.get("phase"), model.get("mechanism_sha256"), model.get("eos"))
        if cache_key not in gas_cache:
            gas_cache[cache_key] = load_verified_gas_model(model)
        gas = gas_cache[cache_key]
        checks.append(_check(f"{key}.model_identity", f"{label}：保存物性文件身份", status="pass",
                             reason="已核对保存的机制名、相、文件 SHA-256 和 EOS。"))
    except Exception as exc:
        checks.append(_check(f"{key}.model_identity", f"{label}：保存物性文件身份",
                             status="unavailable" if not model.get("mechanism_sha256") else "fail", reason=str(exc)))
        gas = None
    if gas is not None:
        try:
            elements = _composition_elements(gas, composition)
            row["element_mass_fractions"] = elements
            mdot = row["mass_flow_kg_s"]
            if mdot is not None and mdot > 0:
                row["element_fluxes_kg_s"] = {name: mdot * y for name, y in elements.items()}
            checks.append(_check(f"{key}.composition", f"{label}：保存组成和归一性",
                math.fsum(composition.values()), 1.0, reason="保留所有非零物种；不丢弃未知物种，不做任意归一化。"))
            saved_elements = _dict(metadata.get("element_mass_fractions"))
            for name in sorted(set(saved_elements) | (set(elements) if saved_elements else set())):
                checks.append(_check(f"{key}.saved_element.{name}", f"{label}：保存与组分回算的 {name} 元素",
                                      _number(saved_elements.get(name)), elements.get(name)))
            if not pure and all(row[name] is not None and row[name] > 0 for name in ("p_Pa", "T_K")):
                gas.TPY = row["T_K"], row["p_Pa"], composition
                from thermo import gas_temperature_applicability
                row["temperature_range_evidence"] = gas_temperature_applicability(gas)
                # Keep domain applicability separate from contract completeness.
                row["thermochemical_range_status"] = row["temperature_range_evidence"]["status"]
                checks.append(_check(f"{key}.state_enthalpy", f"{label}：保存焓与 T/P/Y 回算",
                    row["h_Jkg"], float(gas.enthalpy_mass), unit="J/kg", abs_tol=0.05,
                    reason="同一保存气体模型的状态一致性，不是外部物性准确度验证。"))
        except Exception as exc:
            checks.append(_check(f"{key}.composition", f"{label}：完整组成", status="fail" if composition else "unavailable", reason=str(exc)))
    if metadata.get("composition_complete") is False:
        checks.append(_check(f"{key}.composition_completeness", f"{label}：完整组成声明", status="fail",
                             reason="来源明确标记组成不完整，不能用于完整燃气交接。"))
    row["status"] = _status(checks)
    return row


def _status(checks):
    if any(check["status"] == "fail" for check in checks):
        return "inconsistent"
    return "incomplete" if any(check["status"] == "unavailable" for check in checks) else "complete"


def _balance(key, label, inlet_ids, outlet_id, stations, outputs, kind):
    inlet, outlet = [stations[s] for s in inlet_ids], stations[outlet_id]
    rows = inlet + [outlet]
    checks = []
    flows = [row["mass_flow_kg_s"] for row in rows]
    good_flow = all(value is not None and value > 0 for value in flows)
    checks.append(_check(f"{key}.mass", f"{label}质量守恒", math.fsum(flows[:-1]) if good_flow else None,
                         flows[-1] if good_flow else None, unit="kg/s"))
    elements = sorted(set().union(*(row["element_fluxes_kg_s"].keys() for row in rows)))
    have_elements = all(row["element_fluxes_kg_s"] for row in rows)
    element_checks = []
    for element in elements or ["all"]:
        check = _check(f"{key}.element.{element}", f"{label} {element} 元素守恒",
            math.fsum(row["element_fluxes_kg_s"].get(element, 0.0) for row in inlet) if have_elements else None,
            outlet["element_fluxes_kg_s"].get(element, 0.0) if have_elements else None, unit="kg/s",
            reason="所保存物种／相集合内的元素收支；未把未建凝聚相当成已验证能力。")
        checks.append(check)
        element_checks.append(check)
    refs = [row["model"].get("enthalpy_reference_id") for row in rows]
    have_refs = all(isinstance(ref, str) and ref for ref in refs)
    same_ref = have_refs and len(set(refs)) == 1
    checks.append(_check(f"{key}.reference", f"{label}入口／出口化学焓基准",
        status="pass" if same_ref else "fail" if have_refs else "unavailable"))
    power_in = math.fsum(row["mass_flow_kg_s"] * row["h_Jkg"] for row in inlet) if good_flow and all(row["h_Jkg"] is not None for row in inlet) else None
    power_out = outlet["mass_flow_kg_s"] * outlet["h_Jkg"] if good_flow and outlet["h_Jkg"] is not None else None
    if kind == "turbine":
        loss = _number(outputs.get("turbine_power_W"))
    elif kind == "chamber":
        losses = [_number(outputs.get(field)) for field in ("heat_loss_W", "combustion_loss_W")]
        loss = math.fsum(losses) if all(value is not None for value in losses) else None
    else:
        loss = 0.0  # The saved preburner model is explicitly adiabatic HP equilibrium.
    energy = _check(f"{key}.enthalpy", f"{label}焓流守恒",
        power_in if same_ref else None,
        power_out + loss if same_ref and power_out is not None and loss is not None else None,
        unit="W", abs_tol=1.0, rel_tol=1e-8,
        reason="入口焓流 = 出口焓流 + 已保存轴功／热汇；未加入缺失的速度项。")
    checks.append(energy)
    return {"id": key, "label": label, "inlet_station_ids": inlet_ids, "outlet_station_id": outlet_id,
            "status": _status(checks), "checks": checks, "element_checks": element_checks,
            "enthalpy_residual_W": energy["difference_si"]}


def capture_gas_handoff(source_result: dict) -> dict:
    """Audit a saved/new standard web result without altering it or its inputs."""
    source_result = _dict(source_result)
    calculation = _dict(source_result.get("calculation"))
    if _dict(calculation.get("inputs")).get("cycle_type") == "gg":
        return {"schema_version": 1, "scope": "ffsc_saved_gas_and_reactant_ports", "status": "not_applicable",
                "stations": [], "component_balances": [], "element_checks": [],
                "notes": ["此交接审计针对 FFSC 两预燃室／两涡轮结构；GG 记录不使用该接口。"]}
    saved = _dict(calculation.get("states"))
    environment = _dict(source_result.get("environment"))
    components = _dict(calculation.get("components"))
    cache = {}
    stations = [_station(key, label, saved.get(key), environment, cache) for key, label in STATIONS]
    lookup = {row["id"]: row for row in stations}
    specs = [
        ("ox_preburner", "富氧预燃室", ["branch_ox_to_opb", "branch_fuel_to_opb"], "ox_preburner_outlet", "preburner"),
        ("fuel_preburner", "富燃预燃室", ["branch_ox_to_fpb", "branch_fuel_to_fpb"], "fuel_preburner_outlet", "preburner"),
        ("ox_preburner_turbine", "富氧涡轮", ["ox_preburner_outlet"], "ox_turbine_exhaust", "turbine"),
        ("fuel_preburner_turbine", "富燃涡轮", ["fuel_preburner_outlet"], "fuel_turbine_exhaust", "turbine"),
        ("main_chamber", "主燃烧室", ["ox_turbine_exhaust", "fuel_turbine_exhaust"], "main_chamber", "chamber"),
    ]
    balances = [_balance(key, label, inlet, outlet, lookup, _dict(_dict(components.get(key)).get("outputs")), kind)
                for key, label, inlet, outlet, kind in specs]
    all_checks = [check for row in stations + balances for check in row["checks"]]
    return {"schema_version": 1, "scope": "ffsc_saved_gas_and_reactant_ports", "status": _status(all_checks),
            "stations": stations, "component_balances": balances,
            "element_checks": [check for balance in balances for check in balance["element_checks"]],
            "notes": ["完整组成和化学焓随真实循环保存；主室入口为两股涡轮排气。",
                      "只按保存机制与 SHA-256 复核；历史缺失证据保留缺失，不借用当前默认值。",
                      "守恒通过表示所声明模型内部闭合，不代表高压 EOS、凝聚相、积碳或燃烧动力学已验证。"]}
