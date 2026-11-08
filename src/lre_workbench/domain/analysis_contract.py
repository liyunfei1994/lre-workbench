"""Versioned task boundaries; a concept task is never an operating-point task.

The flat EngineInputs format remains the legacy concept solver's adapter.
Version 2 separates physical input roles, including conditional preburner
initials. Version 1 records retain their original partition for exact replay.
"""
from copy import deepcopy
from dataclasses import fields

from lre_workbench.domain.models import EngineInputs
from lre_workbench.domain import parameters as parameter_contract
from lre_workbench.systems.pressure_budget import BRANCH_BUDGET_FIELDS


SCHEMA_VERSION = 2
FFSC_SCHEMA_VERSION = 3
CONCEPT_DESIGN = "concept_design"
FIXED_HARDWARE_STEADY = "fixed_hardware_steady"
GROUPS = ("design_targets", "design_choices", "boundaries", "model_configuration",
          "constraints", "initial_guess", "solver_settings")
_V1_GROUPS = ("design_targets", "boundaries", "model_configuration", "initial_guess")

_V1_TARGETS = frozenset({
    "thrust", "pc", "of_ratio", "inlet_pressure",
    "use_target_preburner_temp", "use_target_preburner_mr",
    "ox_preburner_pressure_ratio", "fuel_preburner_pressure_ratio",
    "ox_preburner_temp_target", "fuel_preburner_temp_target",
    "ox_preburner_mr_target", "fuel_preburner_mr_target",
    "use_target_gg_temp", "gg_temp_target", "gg_mr_target",
})
_V1_BOUNDARIES = frozenset({
    "ambient_pressure", "tank_pressure_pa", "fuel_inlet_temp", "ox_inlet_temp",
})
_V1_INITIAL = frozenset({
    "target_isp", "chamber_temp_guess", "chamber_gamma", "molar_mass",
    "fuel_split_to_ox_preburner", "ox_split_to_ox_preburner", "gg_fraction_guess",
})
_FIELDS = frozenset(field.name for field in fields(EngineInputs))
# The digital prototype has an explicit FFSC-only contract. Legacy desktop
# V1/V2 records keep their original fields for reading and exact replay.
RETIRED_PLATFORM_FIELDS = frozenset(key for key in _FIELDS if key.startswith('gg_')) | {
    'use_target_gg_temp', 'injector_dp_ratio_ox', 'injector_dp_ratio_fuel',
}
FFSC_INPUT_FIELDS = _FIELDS - RETIRED_PLATFORM_FIELDS
# Earlier V1/V2 task snapshots predate explicit shaft-power acceptance settings.
# Accept that exact older shape without filling or mutating saved task evidence.
_ADDED_POWER_TOLERANCES = frozenset({"steady_ox_power_tolerance_W", "steady_fuel_power_tolerance_W"})
_ADDED_BRANCH_BUDGETS = frozenset(key for keys in BRANCH_BUDGET_FIELDS.values() for key in keys.values())
_ADDED_COOLING_PARAMETERS = frozenset({
    "cooling_channel_roughness_m", "cooling_k_minor_inlet", "cooling_k_minor_outlet",
    "cooling_htc_enhancement_basis",
})
# Older V2 records classified these turbine assumptions together as design choices.
# Accept only that complete historic partition, without rewriting its saved evidence.
_V2_OLD_TURBINE_CHOICES = frozenset({
    "turb_ox_u_max_ms", "turb_fuel_u_max_ms",
    "ox_turbine_geometry_state_basis", "fuel_turbine_geometry_state_basis",
})
_V1_GROUP_FIELDS = {
    "design_targets": _V1_TARGETS,
    "boundaries": _V1_BOUNDARIES,
    "initial_guess": _V1_INITIAL,
    "model_configuration": _FIELDS - _V1_TARGETS - _V1_BOUNDARIES - _V1_INITIAL,
}
_TARGET_FIELDS = _V1_TARGETS - {
    "use_target_preburner_temp", "use_target_preburner_mr", "use_target_gg_temp",
}
_MODE_FLAGS = frozenset({"use_target_preburner_temp", "use_target_preburner_mr", "use_target_gg_temp"})


def _v2_group_fields(values, input_fields=_FIELDS):
    groups = {name: set() for name in GROUPS}
    for key in input_fields:
        role = parameter_contract.concept_parameter_role(key, values)
        if key in _MODE_FLAGS:
            group = "model_configuration"
        elif role == "design_input":
            group = "design_targets" if key in _TARGET_FIELDS else "design_choices"
        else:
            group = {"component_sizing": "design_choices", "boundary": "boundaries",
                     "constraint": "constraints", "initial_guess": "initial_guess",
                     "solver_setting": "solver_settings"}.get(role, "model_configuration")
        groups[group].add(key)
    return groups


def require_concept_mode(mode):
    """Guard every legacy design/sizing entry before creating a cycle model."""
    if mode != CONCEPT_DESIGN:
        raise ValueError("此入口仅允许 concept_design 概念设计；固定硬件稳态不得调用设计或定尺寸求解。")


def _require_fields(value, expected, label):
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"{label} 字段不完整、未知或分组错误；必须使用对应版本的完整任务契约。")


def concept_design_task(values):
    """Partition normalized EngineInputs without defaults, mutation or loss."""
    ffsc = isinstance(values, dict) and set(values) == FFSC_INPUT_FIELDS
    if ffsc and (values.get('cycle_type') != 'ffsc' or values.get('propellant_pair') != 'lox_methane'):
        raise ValueError('当前数字样机仅支持液氧甲烷全流量补燃循环。')
    input_fields = FFSC_INPUT_FIELDS if ffsc else _FIELDS
    _require_fields(values, input_fields, "概念设计输入")
    group_fields = _v2_group_fields(values, input_fields)
    return {"schema_version": FFSC_SCHEMA_VERSION if ffsc else SCHEMA_VERSION, "mode": CONCEPT_DESIGN,
            **{group: {key: deepcopy(value) for key, value in values.items()
                       if key in group_fields[group]} for group in GROUPS}}


def concept_inputs(task):
    """Strictly adapt a concept task to flat inputs; reject cross-mode reuse."""
    if not isinstance(task, dict):
        raise ValueError("概念设计任务必须为 JSON 对象。")
    version = task.get("schema_version")
    if type(version) is not int or version not in (1, SCHEMA_VERSION, FFSC_SCHEMA_VERSION):
        raise ValueError("不支持的概念设计任务 schema_version。")
    groups = _V1_GROUPS if version == 1 else GROUPS
    _require_fields(task, {"schema_version", "mode", *groups}, "概念设计任务")
    require_concept_mode(task["mode"])
    values = {}
    for group in groups:
        if not isinstance(task[group], dict) or set(task[group]) & set(values):
            raise ValueError(f"{group} 必须是对象，且参数不能跨组重复。")
        values.update(deepcopy(task[group]))
    input_fields = FFSC_INPUT_FIELDS if version == FFSC_SCHEMA_VERSION else _FIELDS
    if version == FFSC_SCHEMA_VERSION and (values.get('cycle_type') != 'ffsc' or values.get('propellant_pair') != 'lox_methane'):
        raise ValueError('当前数字样机仅支持液氧甲烷全流量补燃循环。')
    missing = input_fields - set(values)
    legacy_shapes = (_ADDED_POWER_TOLERANCES, _ADDED_BRANCH_BUDGETS,
                     _ADDED_POWER_TOLERANCES | _ADDED_BRANCH_BUDGETS)
    # Only complete historic additive groups may be absent, never arbitrary
    # missing fields within a new group. Replay returns the original evidence.
    legacy_shapes += tuple(shape | _ADDED_COOLING_PARAMETERS
                           for shape in (frozenset(),) + legacy_shapes)
    legacy_missing = missing if version != FFSC_SCHEMA_VERSION and missing in legacy_shapes else frozenset()
    _require_fields(values, input_fields - legacy_missing, "概念设计输入")
    expected = _V1_GROUP_FIELDS if version == 1 else _v2_group_fields(values, input_fields)
    if version == 2 and _V2_OLD_TURBINE_CHOICES <= set(task["design_choices"]):
        expected = {group: keys - _V2_OLD_TURBINE_CHOICES for group, keys in expected.items()}
        expected["design_choices"] |= _V2_OLD_TURBINE_CHOICES
    for group in groups:
        _require_fields(task[group], expected[group] - legacy_missing, group)
    return values
