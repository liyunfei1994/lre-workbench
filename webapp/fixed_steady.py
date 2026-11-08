"""Prepare fixed-hardware tasks without falling back to concept sizing.

Tasks freeze geometry/resistance evidence and separate input roles. The data
manifest is a declaration interface, not pump/turbine maps or an engine model.
The independent numerical framework check cannot make these tasks executable.
"""
from copy import deepcopy
import math

from lre_workbench.domain.analysis_contract import FIXED_HARDWARE_STEADY
from lre_workbench.domain.analysis_roles import analysis_parameter_roles
from lre_workbench.domain.models import preburner_temperature_constraint, wall_temperature_constraint
from webapp.throttle import source_baseline
from webapp.validation import fingerprint
from webapp.steady_model_contract import model_data_manifest
from webapp.valve_requirements import valve_requirements_view


BOUNDARY_FIELDS = frozenset({
    "ambient_pressure", "tank_pressure_pa",
    "fuel_inlet_temp", "ox_inlet_temp",
})
# Only actual, named output quantities may seed an operating-point task. Legacy
# x vectors change meaning with the concept mode and are intentionally excluded.
INITIAL_GUESS_FIELDS = frozenset({
    "mdot_total_kg_s", "mdot_fuel_kg_s", "mdot_ox_kg_s",
    "mdot_fuel_to_ox_preburner_kg_s", "mdot_fuel_to_fuel_preburner_kg_s",
    "mdot_ox_to_ox_preburner_kg_s", "mdot_ox_to_fuel_preburner_kg_s",
    "chamber_pressure_Pa", "chamber_temperature_K",
    "ox_preburner_pressure_Pa", "fuel_preburner_pressure_Pa",
    "ox_preburner_mr", "fuel_preburner_mr",
    "ox_preburner_T_combustion_K", "fuel_preburner_T_combustion_K",
})


def _number(value, label, *, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} 必须是有限数值。")
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{label} 必须是有限数值。") from exc
    if not math.isfinite(result) or (result < 0 if nonnegative else result <= 0):
        domain = "非负" if nonnegative else "正"
        raise ValueError(f"{label} 必须是有限{domain}数值。")
    return result


def _object(value, allowed, label):
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError(f"{label} 含未知或不允许的字段。")
    return value


def _blockers(baseline):
    blockers = [
        {"code": "fixed_steady_solver_unavailable", "label": "发动机方程与部件模型尚未接入",
         "detail": "通用迭代接口和数值自检不构成发动机工作点求解；不得转用概念设计、目标温度反求或定尺寸入口。"},
        {"code": "pump_turbine_maps_missing", "label": "缺少泵与涡轮固定硬件特性",
         "detail": "已有概念转速、尺寸及常效率不构成性能图；尚未建立流量、压升/压比、效率与轴功匹配。"},
        {"code": "valve_characteristics_missing", "label": "缺少阀门开度与流量特性",
         "detail": "阀门尺寸、特性和开度限位属于固定硬件，开度属于可调运行设定；当前尚无可用阀门定义，四支路阻力不能替代这些特性。"},
        {"code": "hot_gas_injector_characteristics_missing", "label": "缺少热气喷注器固定流通特性",
         "detail": "涡轮排气至主室仍为设计压降预算，尚不能由固定通流能力闭合工作点。"},
        {"code": "feed_hardware_missing", "label": "前置供给硬件尚未定义",
         "detail": "来源增压出口设计要求和短管压降预算仅保留作来源假设，不作为运行边界；需建立相应硬件或显式定义模型外的供给接口。"},
    ]
    if baseline.get("cycle_type") != "ffsc":
        blockers.append({"code": "cycle_not_supported", "label": "固定硬件稳态当前仅规划 FFSC",
                         "detail": "此来源循环尚无对应的固定硬件任务执行器。"})
    if baseline.get("cooling_hardware", {}).get("mode") != "regen_quasi1d_ph_v1":
        blockers.append({"code": "cooling_budget_only", "label": "冷却来源仅有热量与压降预算",
                         "detail": "预算值不是固定通道随工况变化的热水力能力；需实际冻结几何及相应模型。"})
    return blockers


def configuration(source):
    """Build a detached template from the independently re-audited result."""
    values, baseline = source_baseline(source)
    payload = {
        "cycle_type": baseline["cycle_type"], "propellant_pair": baseline["propellant_pair"],
        "nozzle": {"throat_area_m2": baseline["throat_area_m2"],
                   "expansion_ratio": baseline["expansion_ratio"],
                   "contour_definition": deepcopy(baseline["contour_inputs"])},
        "feed": deepcopy(baseline.get("feed_hardware")),
        "cooling": deepcopy(baseline["cooling_hardware"]),
        # Missing valve definitions are explicit. Openings never belong here.
        "valves": {},
    }
    summary = source["result"]["calculation"].get("summary") or {}
    guesses = {}
    for key in sorted(INITIAL_GUESS_FIELDS):
        if key in summary:
            try:
                guesses[key] = _number(summary[key], key)
            except ValueError:
                # An absent/unusable output is not replaced by a design target.
                pass
    boundaries = {key: _number(values[key], key, nonnegative=key == "ambient_pressure")
                  for key in sorted(BOUNDARY_FIELDS)}
    hardware = {"schema_version": 2, "id": fingerprint(payload), "payload": payload}
    return {
        "schema_version": 2, "mode": FIXED_HARDWARE_STEADY,
        "parameter_roles": analysis_parameter_roles(),
        "source": {"job_id": source["id"], "project_id": source.get("project_id"),
                   "revision": source.get("revision"), "result_sha256": fingerprint(source["result"])},
        "hardware": hardware,
        "model_data": model_data_manifest(hardware),
        "valve_requirements": valve_requirements_view(source["result"], source["id"]),
        "operating_conditions": {
            "boundaries": boundaries,
            "controls": {"valve_openings": {}},
            "control_targets": {},
        },
        "legacy_design_assumptions": {
            "inlet_pressure": {"value": values["inlet_pressure"], "unit": "Pa", "role": "design_target",
                               "adopted_as_boundary": False,
                               "description": "来源前置增压泵出口设计要求；未作为固定硬件运行边界。"},
            "feed_line_dp_pa": {"value": values["feed_line_dp_pa"], "unit": "Pa", "role": "model_assumption",
                                "adopted_as_boundary": False,
                                "description": "来源短管设计压降预算；未作为固定硬件运行边界或硬件特性。"},
        },
        "initial_guess": {"mode": FIXED_HARDWARE_STEADY, "variables": guesses},
        "constraints": {
            "ox_preburner_temperature": preburner_temperature_constraint(values, "ox"),
            "fuel_preburner_temperature": preburner_temperature_constraint(values, "fuel"),
            "cooling_wall_temperature": wall_temperature_constraint(values),
        },
        "readiness": {"can_run": False, "status": "blocked", "blockers": _blockers(baseline)},
    }


def normalize_request(value, source):
    """Accept operating changes, never client-supplied hardware or equalities."""
    _object(value, {"source_job_id", "operating_conditions", "initial_guess"}, "固定硬件稳态请求")
    if value.get("source_job_id") != source.get("id"):
        raise ValueError("来源计算记录不匹配。")
    task = configuration(source)
    conditions = _object(value.get("operating_conditions", {}), {"boundaries", "controls", "control_targets"}, "运行工况")
    boundaries = _object(conditions.get("boundaries", {}), BOUNDARY_FIELDS, "运行边界")
    for key, number in boundaries.items():
        task["operating_conditions"]["boundaries"][key] = _number(
            number, key, nonnegative=key == "ambient_pressure")
    controls = _object(conditions.get("controls", {}), {"valve_openings"}, "运行控制")
    # No valve identities/characteristics exist yet. Never interpret a supplied
    # ratio or percentage as an actuator that can realize that command.
    _object(controls.get("valve_openings", {}), set(), "阀门开度可调，但尚无已定义阀门及其特性")
    _object(conditions.get("control_targets", {}), set(),
            "推力或整机 氧燃质量比 控制目标需明确操纵量、范围和饱和处理，当前尚未接入")
    initial = _object(value.get("initial_guess", {}), {"mode", "variables"}, "固定硬件初值")
    if initial.get("mode", FIXED_HARDWARE_STEADY) != FIXED_HARDWARE_STEADY:
        raise ValueError("初值 mode 必须是 fixed_hardware_steady，不能复用概念设计未知量向量。")
    variables = _object(initial.get("variables", {}), INITIAL_GUESS_FIELDS, "命名初值")
    for key, number in variables.items():
        task["initial_guess"]["variables"][key] = _number(number, key)
    return task


def assert_runnable(task):
    """Fail closed even if a caller forges readiness.can_run or drops blockers."""
    if not isinstance(task, dict) or task.get("mode") != FIXED_HARDWARE_STEADY:
        raise ValueError("固定硬件稳态入口仅接受 fixed_hardware_steady 任务。")
    raise ValueError("固定硬件稳态暂不可运行：发动机方程与部件模型尚未接入，且缺少泵/涡轮特性图、"
                     "阀门开度特性与热气喷注器模型；不会调用概念设计或定尺寸求解。")
