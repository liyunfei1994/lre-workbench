"""Read-only audit of duplicated values in one saved design-point record.

This is a provenance/units check, not a fresh cycle solve or a physics approval.
Only explicitly mapped stations and saved geometry are compared. Draft inputs,
transient studies and unrelated metadata are intentionally outside its scope.
"""
import hashlib
import json
import math


_ABS_TOL = 1e-8
_REL_TOL = 2e-7
_TURBINES = ("ox_preburner_turbine", "fuel_preburner_turbine",
             "gg_turbine", "gg_turbine_ox", "gg_turbine_fuel")
_COMPONENTS = ("ox_pump", "fuel_pump", "ox_preburner", "fuel_preburner",
               "gas_generator", "main_chamber", "nozzle", "cooling_channel") + _TURBINES


def _dict(value):
    return value if isinstance(value, dict) else {}


def _finite(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _equal(a, b):
    if _finite(a) and _finite(b):
        return math.isclose(a, b, rel_tol=_REL_TOL, abs_tol=_ABS_TOL)
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_equal(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b


def _json_value(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        if len(value) > 8:
            return {"count": len(value), "first": _json_value(value[0]), "last": _json_value(value[-1])}
        return [_json_value(item) for item in value]
    return value if isinstance(value, (str, int, float, bool)) or value is None else str(value)


class _Audit:
    def __init__(self, result):
        self.result = result
        self.checks = []

    def get(self, path):
        value = self.result
        for key in path.split("."):
            if not isinstance(value, dict) or key not in value:
                return None
            value = value[key]
        return value

    def add(self, check_id, label, status, sources, note):
        self.checks.append({"id": check_id, "label": label, "status": status,
                            "source_paths": [row["path"] for row in sources],
                            "values": sources, "note": note})

    def compare(self, check_id, label, specs, unit="", *, structural=False, note=""):
        """A spec is (saved path, scale, optional transform), always read-only."""
        rows, valid = [], []
        missing = False
        for spec in specs:
            path, scale = spec[:2]
            value = self.get(path)
            ok = (value is not None and not (isinstance(value, float) and not _finite(value))) if structural else _finite(value)
            normalized = value if structural else value * scale if ok else None
            if ok and len(spec) > 2:
                try:
                    normalized = spec[2](normalized)
                except (TypeError, ValueError, KeyError, IndexError, OverflowError, ZeroDivisionError):
                    ok = False
            if not structural and not _finite(normalized):
                ok = False
            missing |= not ok
            if ok:
                valid.append(normalized)
            rows.append({"path": path, "value": _json_value(value),
                         "normalized_value": _json_value(normalized) if ok else None, "unit": unit})
        conflict = len(valid) >= 2 and any(not _equal(valid[0], other) for other in valid[1:])
        status = "conflict" if conflict else "partial" if missing or len(valid) < 2 else "pass"
        message = {"pass": "已保存的同一物理量一致。", "conflict": "同一记录的重复数据不一致；未覆盖任何来源。",
                   "partial": "缺少可比较的保存数据，不能判定一致。"}[status]
        self.add(check_id, label, status, rows, (note + " " + message).strip())

    def options(self, base, options):
        """Use every stored alias; one missing canonical entry represents no data."""
        present = [(base + key, scale) for key, scale in options if self.get(base + key) is not None]
        return present or [(base + options[0][0], options[0][1])]


def _component_base(key):
    return f"calculation.components.{key}.outputs."


def _pressure_options(end):
    return [(f"p_{end}_Pa", 1), (f"p_{end}_MPa", 1e6), (f"p_{end}_bar", 1e5)]


def _audit_aliases(audit, components):
    for key in _COMPONENTS:
        outputs = _dict(_dict(components.get(key)).get("outputs"))
        families = {}
        for field in outputs:
            for suffix, scale, unit in (("_MPa", 1e6, "Pa"), ("_bar", 1e5, "Pa"), ("_Pa", 1, "Pa"),
                                        ("_MW", 1e6, "W"), ("_kW", 1e3, "W"), ("_W", 1, "W")):
                if field.endswith(suffix):
                    families.setdefault((field[:-len(suffix)], unit), []).append((field, scale))
                    break
        for (family, unit), fields in families.items():
            if len(fields) >= 2:
                audit.compare(f"alias.{key}.{family}", f"{key}：{family} 单位别名", [
                    (_component_base(key) + field, scale) for field, scale in fields], unit)
        if key in _TURBINES and ("T_out_K" in outputs):
            audit.compare(f"alias.{key}.outlet_temperature", f"{key}：出口温度别名", [
                (_component_base(key) + "T_exhaust_K", 1), (_component_base(key) + "T_out_K", 1)], "K")


def _station(audit, key, state, end, *, mass=False):
    base, node = _component_base(key), f"calculation.states.{state}."
    audit.compare(f"station.{key}.{end}.pressure", f"{key} {end}：节点压力", audit.options(base, _pressure_options(end)) + [(node + "pressure", 1)], "Pa")
    temperature = "T_exhaust_K" if end == "out" and key in _TURBINES else f"T_{end}_K"
    options = [(temperature, 1)]
    if end == "out" and key in _TURBINES:
        options.append(("T_out_K", 1))
    audit.compare(f"station.{key}.{end}.temperature", f"{key} {end}：节点温度", audit.options(base, options) + [(node + "temperature", 1)], "K")
    if mass:
        audit.compare(f"station.{key}.{end}.flow", f"{key} {end}：支路质量流量", [(base + "mass_flow_kgs", 1), (node + "mass_flow", 1)], "kg/s")


def _summary(audit, field, specs, unit="", scale=1, note=""):
    audit.compare("summary." + field, "汇总：" + field,
                  [("calculation.summary." + field, scale)] + specs, unit, note=note)


def _power_total(audit, field, component_keys, power_field):
    """Check an explicitly defined total; do not compare one branch to a total."""
    summary_path = "calculation.summary." + field
    if audit.get(summary_path) is None:
        return
    specs = [(summary_path, 1e6)] + [(_component_base(key) + power_field, 1) for key in component_keys]
    rows, values = [], []
    for path, scale in specs:
        value = audit.get(path)
        normalized = value * scale if _finite(value) else None
        if not _finite(normalized):
            normalized = None
        values.append(normalized)
        rows.append({"path": path, "value": _json_value(value), "normalized_value": normalized, "unit": "W"})
    status = ("partial" if any(v is None for v in values) else
              "pass" if _equal(values[0], sum(values[1:])) else "conflict")
    audit.add("summary." + field + ".total", "汇总功率与部件功率合计：" + field, status, rows,
              "按保存的独立部件求和；GG 共轴涡轮仅计一次，不将机械传动后功率混入原始涡轮功率。")


def _audit_system(audit, calculation):
    components, inputs = _dict(calculation.get("components")), _dict(calculation.get("inputs"))
    for key, inlet, outlet, summary_prefix in (
        ("ox_pump", "lox_pump_inlet", "lox_after_pump", "lox"),
        ("fuel_pump", "fuel_pump_inlet", "fuel_after_pump", "fuel"),
    ):
        _station(audit, key, inlet, "in")
        _station(audit, key, outlet, "out")
        base = _component_base(key)
        _summary(audit, f"{summary_prefix}_pump_out_pressure_Pa", audit.options(base, _pressure_options("out")), "Pa")
        # Check optional historical display aliases only if actually recorded.
        alias = f"{summary_prefix}_pump_out_pressure_MPa"
        if alias in _dict(calculation.get("summary")):
            _summary(audit, alias, audit.options(base, _pressure_options("out")), "Pa", 1e6)
        _summary(audit, f"{key}_power_MW", audit.options(base, [("power_W", 1), ("power_MW", 1e6)]), "W", 1e6)

    if "gg_turbine" in components or (inputs.get("cycle_type") == "gg" and inputs.get("gg_layout") == "single_shaft"):
        turbines = [("gg_turbine", "gg_outlet", "gg_turbine_exhaust", "gg")]
    elif any(key in components for key in ("gg_turbine_ox", "gg_turbine_fuel")) or inputs.get("cycle_type") == "gg":
        turbines = [("gg_turbine_ox", "gg_outlet", "gg_turbine_ox_exhaust", "ox"),
                    ("gg_turbine_fuel", "gg_outlet", "gg_turbine_fuel_exhaust", "fuel")]
    else:
        turbines = [("ox_preburner_turbine", "ox_preburner_outlet", "ox_turbine_exhaust", "ox"),
                    ("fuel_preburner_turbine", "fuel_preburner_outlet", "fuel_turbine_exhaust", "fuel")]
    for key, inlet, outlet, side in turbines:
        _station(audit, key, inlet, "in")
        _station(audit, key, outlet, "out", mass=True)
        base = _component_base(key)
        _summary(audit, f"{side}_turbine_power_MW", audit.options(base, [("turbine_power_W", 1), ("turbine_power_MW", 1e6)]), "W", 1e6)
        if key in ("ox_preburner_turbine", "fuel_preburner_turbine"):
            _summary(audit, f"{side}_turbine_exhaust_T_K", [(base + "T_exhaust_K", 1)], "K")
            _summary(audit, f"{side}_turbine_exhaust_pressure_Pa", [(base + "p_out_Pa", 1)], "Pa")
            _summary(audit, f"{side}_preburner_pressure_Pa", [(base + "p_in_Pa", 1)], "Pa")
    combustion = [("gas_generator", "gg_outlet", "gg_T_combustion_K", "gg_pressure_Pa")] if inputs.get("cycle_type") == "gg" or any(k.startswith("gg_") for k, *_ in turbines) else [
        ("ox_preburner", "ox_preburner_outlet", "ox_preburner_T_combustion_K", "ox_preburner_pressure_Pa"),
        ("fuel_preburner", "fuel_preburner_outlet", "fuel_preburner_T_combustion_K", "fuel_preburner_pressure_Pa")]
    for key, state, temp_summary, pressure_summary in combustion:
        base, node = _component_base(key), f"calculation.states.{state}."
        _summary(audit, temp_summary, [(base + "T_combustion_K", 1), (node + "temperature", 1)], "K")
        audit.compare("summary." + pressure_summary + ".node", "燃烧器出口压力：" + key,
                      [("calculation.summary." + pressure_summary, 1), (base + "preburner_pressure_Pa", 1), (node + "pressure", 1)], "Pa")
    _summary(audit, "chamber_temperature_K", [("calculation.states.main_chamber.temperature", 1)], "K")
    _summary(audit, "chamber_pressure_Pa", [("calculation.states.main_chamber.pressure", 1)], "Pa")
    if any(key.startswith("gg_") for key, *_ in turbines):
        _summary(audit, "gg_turbine_exhaust_T_K", [("calculation.states.gg_turbine_exhaust.temperature", 1)], "K",
                 note="旧 GG 双轴合并排气温度沿用氧支路；不据此推断燃料支路温度。")
        _summary(audit, "gg_turbine_backpressure_Pa", [("calculation.states.gg_turbine_exhaust.pressure", 1)], "Pa")
        _power_total(audit, "gg_turbine_power_MW", [key for key, *_ in turbines], "turbine_power_W")
        if len(turbines) == 1 and audit.get("calculation.summary.ox_turbine_power_MW") is not None:
            _summary(audit, "ox_turbine_power_MW", [(_component_base("gg_turbine") + "turbine_power_W", 1)], "W", 1e6,
                     note="GG 单轴旧汇总中氧侧功率键代表唯一共享涡轮。")
    _power_total(audit, "turbine_power_MW", [key for key, *_ in turbines], "turbine_power_W")
    _power_total(audit, "pump_power_MW", ["ox_pump", "fuel_pump"], "power_W")
    return turbines


def _audit_inputs(audit, calculation):
    saved, submitted = calculation.get("inputs"), audit.result.get("submitted_inputs")
    if not isinstance(saved, dict) or not isinstance(submitted, dict) or not set(saved).intersection(submitted):
        audit.add("inputs.sources", "计算与提交参数来源", "partial", [], "未同时保存可比较的两份参数；未使用当前草稿补齐。")
        return
    for key in sorted(set(saved).intersection(submitted)):
        audit.compare("inputs." + key, "保存参数：" + key,
                      [("calculation.inputs." + key, 1), ("submitted_inputs." + key, 1)],
                      structural=True, note="仅比较两份历史输入的共同字段；新增字段不作为冲突。")


def _geometry_reason_status(reason):
    # Explicitly unsupported total-state annulus sizing and old missing
    # assumptions are incomplete evidence, not contradictory measurements.
    if any(word in reason for word in ("总态接口", "旧记录", "未保存", "缺少", "缺失", "未完整保存", "无法识别")):
        return "partial"
    return "conflict" if any(word in reason for word in ("不一致", "无效", "反折", "严格递增", "不完整")) else "partial"


def _geometry_integrity(audit, family, view, original, path):
    available = view.get("available") is True
    reason = str(view.get("reason") or "")
    status = "pass" if available else _geometry_reason_status(reason)
    audit.add("geometry.integrity." + path, family + "保存几何内部校验", status,
              [{"path": path, "value": {"available": original.get("available")},
                "normalized_value": {"available": available}, "unit": ""}],
              "保存几何内部校验通过；不代表气动性能验证。" if available else reason or "未保存可校验的完整几何。")


def _audit_geometry(audit, record_id):
    from webapp.pump_geometry import pump_geometry_view
    from webapp.turbine_geometry import turbine_geometry_view
    from webapp.nozzle_geometry import nozzle_geometry_view

    pump_fields = {"pressure_in_pa": _pressure_options("in"), "pressure_out_pa": _pressure_options("out"),
                   "pressure_rise_pa": [("delta_p_Pa", 1), ("delta_p_MPa", 1e6), ("delta_p_bar", 1e5)],
                   "volume_flow_m3_s": [("volume_flow_m3s", 1)], "density_in_kg_m3": [("rho_in_kgm3", 1)],
                   "shaft_speed_rpm": [("nr_rpm", 1)], "stages": [("n_stages", 1)],
                   "total_head_m": [("head_m", 1)], "inlet_temperature_k": [("T_in_K", 1)],
                   "outlet_temperature_k": [("T_out_K", 1)]}
    turbine_fields = {"pressure_in_pa": [("p_in_Pa", 1)], "pressure_out_pa": [("p_out_Pa", 1)],
                      "temperature_in_k": [("T_in_K", 1)], "temperature_out_k": [("T_exhaust_K", 1)],
                      "mass_flow_kg_s": [("mass_flow_kgs", 1)], "shaft_speed_rpm": [("shaft_speed_rpm", 1)],
                      "mean_diameter_m": [("D_mean_m", 1)], "power_w": [("turbine_power_W", 1)],
                      "stages": [("n_stages_recommended", 1)], "pressure_ratio": [("turbine_pressure_ratio", 1)]}
    for family, collection, view_fn, fields in (("pump_geometry", "pumps", pump_geometry_view, pump_fields),
                                               ("turbine_geometry", "turbines", turbine_geometry_view, turbine_fields)):
        saved = audit.result.get(family)
        if not isinstance(saved, dict):
            audit.add("geometry." + family, family + "快照", "partial", [], "未保存几何快照，未重建或用当前输入补图。")
            continue
        validated = _dict(view_fn(audit.result, record_id).get(collection))
        entries = _dict(saved.get(collection))
        if not entries:
            audit.add("geometry." + family, family + "快照", "partial", [], "快照缺少部件数据。")
        for side, entry in entries.items():
            entry = _dict(entry)
            path = f"{family}.{collection}.{side}"
            _geometry_integrity(audit, family, _dict(validated.get(side)), entry, path)
            if not _dict(entry.get("design_point")):
                # An intentionally unavailable annulus is one incomplete
                # geometry check, not a missing-data alert for every dimension.
                continue
            key = _dict(entry.get("source")).get("component_key")
            if family == "pump_geometry":
                key = {"ox": "ox_pump", "fuel": "fuel_pump"}.get(side)
            if not key or key not in _COMPONENTS:
                continue
            for field, options in fields.items():
                audit.compare(f"geometry.{family}.{side}.{field}", f"{side} 几何／系统：{field}",
                              [(path + ".design_point." + field, 1)] + audit.options(_component_base(key), options))
    saved = audit.result.get("nozzle_geometry")
    profile_path = "calculation.components.cooling_channel.profile.geometry"
    profile = audit.get(profile_path)
    if not isinstance(saved, dict):
        audit.add("geometry.nozzle_geometry", "喷管几何快照", "partial", [], "未保存喷管几何快照；本审计不重建型面。")
        return
    _geometry_integrity(audit, "nozzle_geometry", nozzle_geometry_view(audit.result, record_id), saved, "nozzle_geometry")
    for field, source, transform in (("throat_diameter_m", "At_m2", lambda d: math.pi * d * d / 4),
                                     ("expansion_ratio", "expansion_ratio", lambda x: x)):
        audit.compare("geometry.nozzle." + field, "喷管几何／系统：" + field,
                      [("nozzle_geometry.dimensions." + field, 1, transform), (_component_base("nozzle") + source, 1)])
    contour = _dict(saved.get("contour"))
    for field, coords, transform in (("throat_diameter_m", "r_m", lambda rs: 2 * min(rs)),
                                      ("exit_diameter_m", "r_m", lambda rs: 2 * rs[-1]),
                                      ("total_length_m", "x_m", lambda xs: xs[-1])):
        audit.compare("geometry.nozzle.contour." + field, "喷管保存轮廓／尺寸：" + field,
                      [("nozzle_geometry.dimensions." + field, 1),
                       ("nozzle_geometry.contour." + coords, 1, transform)], structural=True)
    if isinstance(contour.get("x_m"), list) and isinstance(contour.get("r_m"), list):
        try:
            canonical = {k: contour[k] for k in ("x_m", "r_m", "coordinate_origin")}
            digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
            status = "pass" if saved.get("contour_sha256") == digest else "conflict" if saved.get("contour_sha256") else "partial"
        except (KeyError, ValueError, TypeError, OverflowError):
            status, digest = "partial", None
        audit.add("geometry.nozzle.contour_hash", "喷管保存坐标校验值", status,
                  [{"path": "nozzle_geometry.contour_sha256", "value": saved.get("contour_sha256"), "normalized_value": digest, "unit": ""}],
                  "仅校验保存型面，来源说明及其他 metadata 不参与校验。")
    if isinstance(profile, dict):
        # This path validates only an existing saved full profile: never invokes
        # the no-profile reconstruction branch and never passes current inputs.
        result = {"calculation": {"components": {"cooling_channel": {"profile": {"geometry": profile}}}}}
        profile_view = nozzle_geometry_view(result, record_id)
        _geometry_integrity(audit, "冷却记录完整喷管", profile_view, profile, profile_path)
        for field, source, scale in (("throat_diameter_m", "r_t_m", 2), ("chamber_diameter_m", "r_c_m", 2),
                                     ("chamber_length_m", "L_cyl_m", 1), ("expansion_ratio", "expansion_ratio", 1)):
            audit.compare("geometry.nozzle.profile." + field, "喷管快照／冷却几何：" + field,
                          [("nozzle_geometry.dimensions." + field, 1), (profile_path + "." + source, scale)])
        for coordinate in ("x_m", "r_m"):
            audit.compare("geometry.nozzle.profile." + coordinate, "喷管快照／完整冷却轮廓：" + coordinate,
                          [("nozzle_geometry.contour." + coordinate, 1),
                           (profile_path + ".full_nodes", 1, lambda nodes, key=coordinate: [n[key] for n in nodes])],
                          structural=True)


def result_consistency_view(result, record_id=""):
    """Return pass/conflict/partial evidence without mutating a saved result."""
    result = _dict(result)
    audit = _Audit(result)
    calculation = _dict(result.get("calculation"))
    _audit_inputs(audit, calculation)
    _audit_aliases(audit, _dict(calculation.get("components")))
    _audit_system(audit, calculation)
    _audit_geometry(audit, record_id)
    conflicts = [row for row in audit.checks if row["status"] == "conflict"]
    partial = sum(row["status"] == "partial" for row in audit.checks)
    return {"schema_version": 1, "record_id": record_id,
            "status": "conflict" if conflicts else "partial" if partial else "pass",
            "checks": audit.checks, "checked_count": len(audit.checks) - partial,
            "conflicts": conflicts, "conflict_count": len(conflicts), "partial_count": partial,
            "scope": "同一保存记录内明确映射的重复物理量与几何；不等同于物理模型或收敛验证。"}
