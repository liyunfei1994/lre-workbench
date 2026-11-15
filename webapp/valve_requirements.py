"""Read saved concept feed requirements without inventing physical valves.

The extra throttling loss is a design-matching result. It is neither an actual
valve characteristic nor a valve-position result. No property lookup, sizing,
inverse capacity calculation or cycle evaluation occurs in this projection.
"""
import math

from lre_workbench.systems.steady_network import BRANCHES


MISSING_HARDWARE_FIELDS = (
    "valve_id", "position_and_port_ids", "geometry_and_hardware_revision",
    "opening_range", "opening_flow_characteristic", "characteristic_domain_and_evidence",
    "leakage_characteristic", "fail_position", "material_compatibility",
    "actuator_and_control_assignment", "saturation_policy",
)

_LIMITATIONS = (
    "附加调节压降是所选概念设计点的匹配需求，不是由实际阀门特性求得的能力或开度。",
    "基础管路/全开阀损失与附加调节损失分别记录；不能把同一损失重复分配给阀门。",
    "附加调节压降为零只表示该来源工况没有额外节流需求，不证明没有阀门或阀门处于全开。",
    "取流点与供给支路末端不是已定义的阀门安装节点；本报告不推导实际阀门上下游压力和温度。",
    "温度、密度和焓按保存端口分别读取，不将不同位置的量拼接成阀门入口状态；未保存的量保持缺失。",
    "需要补充实际阀门定义与试验/部件模型依据后，才能交付固定硬件计算；该报告不生成尺寸、流量系数或开度。",
)


def _dict(value):
    return value if isinstance(value, dict) else {}


def _quantity(value, unit, source_path, *, domain="positive", reason=None):
    missing = {"value": None, "unit": unit, "source_path": source_path, "status": "missing"}
    if reason:
        return missing | {"reason": reason}
    if value is None:
        return missing | {"reason": "来源记录未保存该量。"}
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return missing | {"reason": "来源量不是有效数值。"}
    try:
        valid = math.isfinite(value)
    except (TypeError, OverflowError):
        valid = False
    if not valid:
        return missing | {"reason": "来源量不是有限数值。"}
    if (domain == "positive" and value <= 0) or (domain == "nonnegative" and value < 0):
        return missing | {"reason": "来源量超出本字段允许范围；未截断或替换。"}
    return {"value": value, "unit": unit, "source_path": source_path, "status": "recorded"}


def _composition(value):
    """Copy valid saved fractions only; never normalize or repair a mixture."""
    if not isinstance(value, dict) or not value:
        return {}
    for name, fraction in value.items():
        if not isinstance(name, str) or _quantity(fraction, "1", "", domain="nonnegative")["status"] != "recorded":
            return {}
        if fraction > 1:
            return {}
    return dict(value)


def valve_requirements_view(result, record_id=""):
    """Return an independent projection of the selected saved result only."""
    result = _dict(result)
    calculation = _dict(result.get("calculation"))
    inputs = _dict(calculation.get("inputs"))
    network = _dict(_dict(calculation.get("steady_closure")).get("feed_network"))
    report = {
        "schema_version": 1, "record_id": record_id, "scope": "concept_valve_requirements",
        "source": "saved_calculation_only", "status": "unavailable", "rows": [],
        "missing_fields": list(MISSING_HARDWARE_FIELDS), "warnings": [],
        "limitations": list(_LIMITATIONS),
    }
    if inputs.get("cycle_type") and inputs["cycle_type"] != "ffsc":
        return report | {"status": "not_applicable"}
    if inputs.get("cycle_type") != "ffsc" or not network:
        report["warnings"].append("所选记录未保存可识别的 FFSC 四支路供给数据。")
        return report
    states = _dict(calculation.get("states"))
    branches = network.get("branches")
    branches = branches if isinstance(branches, list) else []
    if network.get("status") != "pass":
        report["warnings"].append("来源供给网络未记录为校核通过；读取需求不代表该设计点可采用。")
    known_ids = {row[0] for row in BRANCHES}
    if any(not isinstance(branch, dict) or not isinstance(branch.get("id"), str)
           or branch["id"] not in known_ids for branch in branches):
        report["warnings"].append("来源含未识别的支路记录，未将其当作新增阀门。")
    for branch_id, label, propellant, expected_source, expected_target in BRANCHES:
        matches = [(i, row) for i, row in enumerate(branches)
                   if isinstance(row, dict) and row.get("id") == branch_id]
        branch = matches[0][1] if len(matches) == 1 else {}
        prefix = (f"calculation.steady_closure.feed_network.branches[{matches[0][0]}]"
                  if len(matches) == 1 else f"calculation.steady_closure.feed_network.branches[id={branch_id}]")
        identity_ok = (branch.get("propellant") == propellant and branch.get("source") == expected_source
                       and branch.get("target") == expected_target)
        reason = ("来源支路未保存。" if not matches else
                  "来源支路 ID 重复，不能选择其中一条。" if len(matches) > 1 else
                  "来源支路的流体或连接位置与已登记拓扑不一致。" if not identity_ok else None)
        source = branch.get("source") if identity_ok else None
        target = branch.get("target") if identity_ok else None
        tap_state = _dict(states.get(source)) if source else {}
        supply_state = _dict(states.get("branch_" + branch_id)) if identity_ok else {}
        quantities = {}
        for key, saved_key, unit, domain in (
            ("mass_flow", "mass_flow_kg_s", "kg/s", "positive"),
            ("source_pressure", "source_pressure_Pa", "Pa", "positive"),
            ("destination_pressure", "destination_pressure_Pa", "Pa", "positive"),
            ("line_drop", "line_drop_Pa", "Pa", "nonnegative"),
            ("injector_drop", "injector_drop_Pa", "Pa", "nonnegative"),
            ("extra_valve_drop", "valve_drop_Pa", "Pa", "nonnegative"),
        ):
            quantities[key] = _quantity(branch.get(saved_key), unit, prefix + "." + saved_key,
                                         domain=domain, reason=reason)
        tap_path = f"calculation.states.{source or expected_source}"
        for key, saved_key, unit, domain in (
            ("source_tap_temperature", "temperature", "K", "positive"),
            ("density", "density", "kg/m³", "positive"),
            ("source_tap_enthalpy", "enthalpy", "J/kg", "finite"),
        ):
            quantities[key] = _quantity(tap_state.get(saved_key), unit, tap_path + "." + saved_key,
                                         domain=domain, reason=reason)
        # Supply-end temperature is explicitly distinct from the source tap.
        # Do not fall back to branch.temperature_K, which changed location in
        # historical formats and may have no location evidence.
        quantities["supply_end_temperature"] = _quantity(
            supply_state.get("temperature"), "K", f"calculation.states.branch_{branch_id}.temperature",
            reason=reason)
        missing = [key for key, value in quantities.items() if value["status"] == "missing"]
        row = {
            "id": branch_id, "label": label, "propellant": propellant if identity_ok else None,
            "source": source, "target": target, "temperature_location": "separate_saved_ports",
            "quantities": quantities, "missing_fields": missing,
            "status": "incomplete" if missing else "recorded",
            "loss_source": branch.get("loss_source") if isinstance(branch.get("loss_source"), str) else None,
            "source_composition": _composition(tap_state.get("composition")),
            "source_reference_id": (_dict(tap_state.get("metadata")).get("reference_id")
                                    if isinstance(_dict(tap_state.get("metadata")).get("reference_id"), str)
                                    else None),
        }
        report["rows"].append(row)
    report["status"] = ("blocked" if network.get("status") == "fail" else
                        "incomplete" if any(row["status"] == "incomplete" for row in report["rows"]) else
                        "review_required")
    return report
