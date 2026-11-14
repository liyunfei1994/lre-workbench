"""Read-only property data inspection, separate from engine model evaluation.

Only bundled, allowlisted datasets are inspected. Curves use fresh states and
never change a solver's cached state, a reference zero, or a project input.
"""
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path

from lre_workbench.properties.datasets import METHANE_DATASETS, methane_dataset_catalog


def _source_gas(dataset):
    import cantera as ct
    if dataset not in METHANE_DATASETS:
        raise ValueError("请选择已登记的热化学数据集。")
    item = METHANE_DATASETS[dataset]
    raw = (Path(ct.__file__).resolve().parent / "data" / item["file"]).read_bytes()
    return ct.Solution(yaml=raw.decode("utf-8"), name=item["phase"])


@lru_cache(maxsize=2)
def _gas_details(dataset):
    gas = _source_gas(dataset)
    meta = next(item for item in methane_dataset_catalog() if item["id"] == dataset)
    header = dict(gas.input_header)
    meta.update(
        engine="Cantera", generator=header.get("generator"),
        generator_version=header.get("cantera-version"), generation_date=header.get("date"),
        elements=list(gas.element_names), source_reaction_count=gas.n_reactions,
        source_transport_model=gas.transport_model,
        source_kinetics_model=gas.kinetics_model,
        thermodynamic_role="物种标准态比热、焓、熵；与状态方程及组成共同确定混合物性质。",
        kinetics_role="源文件含反应速率，但总体设计点采用化学平衡与冻结组成，不积分有限速率反应。",
        species=[{
            "name": s.name, "composition": dict(s.composition),
            "source_note": s.input_data.get("note"),
            "molecular_weight_kg_kmol": float(s.molecular_weight),
            "reference_pressure_Pa": float(s.thermo.reference_pressure),
            "thermo": dict(s.thermo.input_data),
            "transport": dict(s.transport.input_data) if s.transport else None,
        } for s in gas.species()],
    )
    # Cantera AnyMap is converted to ordinary JSON data before caching.
    return json.loads(json.dumps(meta, allow_nan=False))


def gas_dataset_details(dataset):
    return deepcopy(_gas_details(dataset))


@lru_cache(maxsize=2)
def _fluid_details(fluid):
    import CoolProp
    from CoolProp.CoolProp import get_fluid_param_string
    from lre_workbench.properties.fluids import fluid_spec
    if fluid not in ("Methane", "Oxygen"):
        raise ValueError("资料库仅登记甲烷与氧的纯流体物性。")
    raw = get_fluid_param_string(fluid, "JSON")
    document = json.loads(raw)[0]
    eos = document["EOS"][0]
    return {
        "id": fluid, "label": {"Methane": "甲烷", "Oxygen": "氧"}[fluid],
        "engine": "CoolProp", "version": CoolProp.__version__, "backend": "HEOS",
        "eos_family": "多参数 Helmholtz 自由能状态方程",
        "limits": fluid_spec(fluid),
        "eos_references": eos.get("BibTeX_EOS"),
        "transport_references": {key: value.get("BibTeX") for key, value in document.get("TRANSPORT", {}).items()
                                 if isinstance(value, dict)},
        "source_url": f"https://coolprop.org/fluid_properties/fluids/{fluid}.html",
        "data_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "fingerprint_basis": "CoolProp 当前导出的流体 JSON（包括状态方程与输运声明）",
        "scope": "贮箱、供给、泵及冷却剂纯流体状态；不用于反应燃气混合物。",
        "reference_note": "曲线保留 HEOS 原始焓熵参考。进入化学能量计算前另行对齐生成焓；不可直接与物种标准态焓作差。",
        "range_note": "范围是状态方程声明域；不等于输运精度、相态、汽蚀或近临界换热已验证。",
    }


def pure_fluid_catalog():
    return [deepcopy(_fluid_details(name)) for name in ("Methane", "Oxygen")]


def property_catalog():
    from lre_workbench.materials.wall import list_wall_datasets
    from lre_workbench.properties.gas_eos import PR_SUPPORTED_V1
    return {
        "schema_version": 1, "scope": "installed_reference_data",
        "gas_datasets": [gas_dataset_details(key) for key in METHANE_DATASETS],
        "pure_fluids": pure_fluid_catalog(),
        "wall_datasets": [item.metadata() for item in list_wall_datasets()],
        "species_policies": {"pr_supported_v1": list(PR_SUPPORTED_V1)},
    }


def sample_property_curve(*, kind, dataset, member="", start_K, end_K,
                          points=61, pressure_Pa=None, expected_sha256=None):
    """Bounded inspection; no extrapolation, equilibrium or engine calculation."""
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
           for v in (start_K, end_K)) or not 0 < start_K < end_K:
        raise ValueError("温度区间须为有限正数，且终点大于起点。")
    if isinstance(points, bool) or not isinstance(points, int) or not 2 <= points <= 121:
        raise ValueError("曲线点数必须为 2–121 的整数。")
    if kind != "fluid" and pressure_Pa is not None:
        raise ValueError("仅纯流体曲线接受压力条件；物种曲线使用其声明的标准态参考压力。")
    if kind == "species":
        detail = gas_dataset_details(dataset)
        spec = next((s for s in detail["species"] if s["name"] == member), None)
        if spec is None:
            raise ValueError("该数据集未登记所选物种。")
        ranges = spec["thermo"]["temperature-ranges"]
        limits, fingerprint = (ranges[0], ranges[-1]), detail["sha256"]
        thermo = _source_gas(dataset).species(member).thermo
        units = {"cp_JmolK": "J/(mol·K)", "h_Jmol": "J/mol", "s_JmolK": "J/(mol·K)"}
        evaluate = lambda t: {"cp_JmolK": thermo.cp(t)/1000, "h_Jmol": thermo.h(t)/1000, "s_JmolK": thermo.s(t)/1000}
        definition = f"{member} 纯物种标准态性质；参考压力 {spec['reference_pressure_Pa']:g} Pa。不是反应混合物的平衡曲线。"
    elif kind == "fluid":
        from lre_workbench.properties.fluids import state_tp, validate_pressure
        detail = deepcopy(_fluid_details(dataset))
        if isinstance(pressure_Pa, bool) or not isinstance(pressure_Pa, (float, int)) or not math.isfinite(pressure_Pa):
            raise ValueError("纯流体曲线须给定有限绝对压力。")
        validate_pressure(dataset, pressure_Pa)
        limits, fingerprint = (detail["limits"]["Tmin_K"], detail["limits"]["Tmax_K"]), detail["data_sha256"]
        units = {"rho_kgm3": "kg/m³", "cp_JkgK": "J/(kg·K)", "h_Jkg": "J/kg", "s_JkgK": "J/(kg·K)", "mu_Pas": "Pa·s", "k_WmK": "W/(m·K)"}
        def evaluate(t):
            state = state_tp(dataset, t, pressure_Pa, transport=True)
            return {**{key: state[key] for key in units}, "phase": state["phase"]}
        definition = f"HEOS 纯 {dataset}，恒定绝对压力 {pressure_Pa:g} Pa；焓熵为 HEOS 原始参考，跨相态不连线。"
    elif kind == "material":
        from lre_workbench.materials.wall import get_wall_dataset
        try:
            wall = get_wall_dataset(dataset)
        except KeyError as exc:
            raise ValueError("未登记的壁面热物性数据集。") from exc
        curves = {"conductivity": wall.conductivity_property, "heat_capacity": wall.heat_capacity_property}
        if member not in curves:
            raise ValueError("请选择导热率或比热。")
        curve = curves[member]
        limits, fingerprint = curve.range_K, wall.metadata()["data_sha256"]
        units = {member: curve.units}
        evaluate = lambda t: {member: curve.value(t, policy="strict")}
        definition = f"{wall.label} · {member}；按登记试样制造状态及文献温区求值，不作端点外延。"
    else:
        raise ValueError("未登记的物性曲线类型。")
    if expected_sha256 and expected_sha256 != fingerprint:
        raise ValueError("数据指纹与当前安装数据不一致，请刷新资料；不以当前数据重绘旧数据的曲线。")
    if not limits[0] <= start_K < end_K <= limits[1]:
        raise ValueError(f"请求温区超出该性质声明范围 [{limits[0]:g}, {limits[1]:g}] K；资料曲线不作外推。")
    rows = []
    for i in range(points):
        temperature = start_K + (end_K-start_K)*i/(points-1)
        try:
            values = evaluate(temperature)
            if any(isinstance(v, (float, int)) and not math.isfinite(v) for v in values.values()):
                raise ValueError("返回的物性不是有限数值。")
            rows.append({"T_K": temperature, "status": "available", **values})
        except (ValueError, RuntimeError) as exc:
            rows.append({"T_K": temperature, "status": "unavailable", "reason": str(exc)})
    return {"schema_version": 1, "kind": kind, "dataset": dataset, "member": member,
            "data_sha256": fingerprint, "definition": definition, "units": units,
            "pressure_Pa": pressure_Pa, "range_K": list(limits), "rows": rows}
