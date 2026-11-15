"""Record-bound main-chamber engineering review; no chemistry or hardware solve.

Only immutable saved states are consumed. Molecular/elemental conversions may
read a byte-verified mechanism, but never use current defaults or equilibrate it.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math

from webapp.design_scope import require_supported_source
from webapp.nozzle_geometry import nozzle_geometry_view


REFERENCES = [
    dict(title="NASA SP-8087 · Liquid Rocket Engine Fluid-Cooled Combustion Chambers", year=1972,
         url="https://ntrs.nasa.gov/citations/19730022965", use="再生冷却、结构与运行载荷的总体检查框架；历史经验须核对适用域。"),
    dict(title="NASA RP-1311 · Chemical Equilibrium with Applications", year=1994,
         url="https://ntrs.nasa.gov/citations/19950013764", use="HP 平衡与火箭热化学定义；独立对照需相同元素、入口焓、压力和物种政策。"),
    dict(title="Cantera · Frequently Asked Questions", year=None,
         url="https://www.cantera.org/stable/userguide/faq.html", use="平衡不依赖反应速率；焓参考和冻结组成比热定义。"),
    dict(title="DLR · Zhukov / Kong · Methane combustion kinetics", year=2018,
         url="https://elib.dlr.de/125451/", use="高压反应机理的适用性须独立验证，不能由平衡收敛推断动力学可靠。"),
]


def _map(value):
    return value if isinstance(value, dict) else {}


def _number(value, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and (not positive or value > 0) else None


def _clean(value):
    """Make copied evidence strict-JSON safe without silently creating numbers."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def _field(key, label, value, unit="", source="", note="", *, derived=False, positive=False):
    numeric = _number(value, positive)
    return dict(key=key, label=label, value=numeric, unit=unit, source=source, note=note,
                status="missing" if value is None else "invalid" if numeric is None else "derived" if derived else "saved")


def _check(key, label, actual=None, expected=None, *, unit="", note="", status=None, atol=1e-8, rtol=1e-8):
    a, b = _number(actual), _number(expected)
    delta = a-b if a is not None and b is not None else None
    tolerance = max(atol, rtol*max(abs(a), abs(b))) if delta is not None else None
    if status is None:
        status = "missing" if delta is None else "pass" if math.isfinite(delta) and abs(delta) <= tolerance else "fail"
    return dict(key=key, label=label, actual=a, expected=b, difference=delta, unit=unit,
                status=status, note=note, tolerance=tolerance)


def _composition(state, source):
    raw = state.get("composition")
    metadata = _map(state.get("metadata"))
    model = _map(metadata.get("model"))
    rows, total, valid = [], None, True
    if not isinstance(raw, dict) or not raw:
        return dict(status="missing", basis="mass_fraction", rows=[], mass_fraction_sum=None,
                    mole_fraction_status="missing", note="未保存完整组成；不以纯氧、纯甲烷或主要物种补齐。", element_mass_fractions={})
    basis = metadata.get("composition_basis")
    if basis != "mass_fraction":
        return dict(status="missing" if basis is None else "invalid", basis=basis,
            rows=[dict(species=str(species), raw_fraction=_number(fraction), mass_fraction=None,
                       mole_fraction=None, molar_mass_kg_kmol=None) for species, fraction in raw.items()],
            mass_fraction_sum=None, mole_fraction_status="missing", source=source+".composition",
            note="组成基准未保存；保留原分数，未解释为质量/摩尔分数，也未转换或核对元素。" if basis is None else
                 f"保存组成基准为 {basis!r}，不符合本接口的质量分数契约；保留原分数，未执行 Y→X 或元素核对。",
            element_mass_fractions={})
    for species, fraction in raw.items():
        y = _number(fraction)
        if not isinstance(species, str) or not species or y is None or y < 0:
            valid = False
        rows.append(dict(species=str(species), mass_fraction=y, mole_fraction=None, molar_mass_kg_kmol=None))
    if valid:
        total = math.fsum(row["mass_fraction"] for row in rows)
        valid = math.isfinite(total) and abs(total-1) <= 1e-8 and metadata.get("composition_complete") is not False
    masses = _map(metadata.get("species_molar_masses_kg_kmol"))
    elements = {}
    conversion_source, conversion_note = "", "缺少保存分子量或可核验的同身份机制，未生成摩尔分数。"
    if valid:
        try:
            from lre_workbench.integrations.gas_handoff import load_verified_gas_model
            gas = load_verified_gas_model(model)
            if any(row["species"] not in gas.species_names for row in rows):
                valid = False
                raise ValueError("保存组成含机制未识别的物种。")
            masses = {name: float(gas.molecular_weights[gas.species_index(name)]) for name in raw}
            gas.Y = raw  # elemental conversion only; no thermodynamic state/equilibrium solve
            elements = {e: float(gas.elemental_mass_fraction(e)) for e in gas.element_names}
            conversion_source = "同 SHA-256 机制的物种分子量；只转换 Y→X，不求平衡"
        except (ImportError, OSError, ValueError, RuntimeError, TypeError, KeyError):
            if valid and masses and all(_number(masses.get(row["species"]), True) for row in rows):
                conversion_source = source+".metadata.species_molar_masses_kg_kmol"
        if conversion_source:
            denominator = math.fsum(row["mass_fraction"]/masses[row["species"]] for row in rows)
            if denominator > 0 and math.isfinite(denominator):
                for row in rows:
                    row["molar_mass_kg_kmol"] = masses[row["species"]]
                    row["mole_fraction"] = row["mass_fraction"]/masses[row["species"]]/denominator
                conversion_note = "Xᵢ=(Yᵢ/Mᵢ)/Σ(Yⱼ/Mⱼ)；保留全部保存物种，不按显示阈值截断后归一化。"
        # Saved element inventories remain evidence if the mechanism is unavailable.
        if valid and not elements:
            saved = _map(metadata.get("element_mass_fractions"))
            if saved and all(_number(v) is not None and v >= 0 for v in saved.values()) and math.isclose(math.fsum(saved.values()), 1., abs_tol=1e-8):
                elements = dict(saved)
    rows.sort(key=lambda row: -(row["mass_fraction"] if row["mass_fraction"] is not None else -1))
    return dict(status="saved" if valid else "invalid", basis="mass_fraction", rows=rows,
                mass_fraction_sum=total, mole_fraction_status="derived" if any(r["mole_fraction"] is not None for r in rows) else "missing",
                source=source+".composition", mole_fraction_source=conversion_source, note=conversion_note if valid else "组成含无效值、非归一或已声明不完整；未转换摩尔分数。",
                element_mass_fractions=elements)


def _station(key, label, raw):
    state = _map(raw)
    source = "calculation.states."+key
    meta = _map(state.get("metadata"))
    return dict(id=key, label=label, metrics=[
        _field("pressure_Pa", "保存压力", state.get("pressure"), "Pa", source+".pressure", positive=True),
        _field("temperature_K", "保存温度", state.get("temperature"), "K", source+".temperature", positive=True),
        _field("mass_flow_kg_s", "质量流量", state.get("mass_flow"), "kg/s", source+".mass_flow", positive=True),
        _field("enthalpy_Jkg", "化学参考比焓", state.get("enthalpy"), "J/kg", source+".enthalpy", "允许负值；必须在同一化学参考基准下比较。"),
        _field("density_kg_m3", "保存密度", state.get("density"), "kg/m³", source+".density", positive=True)],
        composition=_composition(state, source), state_basis=deepcopy(_map(meta.get("state_basis"))), model=deepcopy(_map(meta.get("model"))))


def _balances(states, stations, out):
    checks = []
    masses = [_number(_map(states.get(s["id"])).get("mass_flow"), True) for s in stations]
    enthalpies = [_number(_map(states.get(s["id"])).get("enthalpy")) for s in stations]
    refs = [s["model"].get("enthalpy_reference_id") for s in stations]
    known = all(isinstance(ref, str) and ref for ref in refs)
    same = known and len(set(refs)) == 1
    checks.append(_check("mass_balance", "两股入口流量与主室流量", sum(masses[:2]) if all(masses[:2]) else None, masses[2], unit="kg/s"))
    checks.append(_check("enthalpy_reference", "入口与出口共同化学焓基准", status="pass" if same else "fail" if known else "missing"))
    for key in ("mechanism", "phase", "mechanism_sha256", "eos"):
        identities = [station["model"].get(key) for station in stations]
        known_ids = [value for value in identities if isinstance(value, str) and value]
        checks.append(_check("model_"+key, "入口/主室模型身份 · "+key,
            status="fail" if len(set(known_ids))>1 else "pass" if len(known_ids)==3 else "missing"))
    inventories = [s["composition"]["element_mass_fractions"] for s in stations]
    for element in sorted(set().union(*(set(e) for e in inventories))):
        complete = all(e for e in inventories) and all(masses)
        lhs = sum(masses[i]*inventories[i].get(element, 0.) for i in range(2)) if complete else None
        rhs = masses[2]*inventories[2].get(element, 0.) if complete else None
        checks.append(_check("element_"+element, element+" 元素质量流守恒", lhs, rhs, unit="kg/s", note="仅核对保存物种/相集合内的元素。"))
    if not all(inventories):
        checks.append(_check("element_inventory", "完整元素库存", note="缺少可核验机制或保存元素库存，不能声称元素闭合。"))
    q, loss = _number(out.get("heat_loss_W")), _number(out.get("combustion_loss_W"))
    complete = all(masses) and all(h is not None for h in enthalpies) and same
    power_in = sum(masses[i]*enthalpies[i] for i in range(2)) if complete else None
    power_out = masses[2]*enthalpies[2] if complete else None
    checks.append(_check("enthalpy_balance", "入口焓流 = 出口焓流 + 显式热汇", power_in,
                         power_out+q+loss if power_out is not None and q is not None and loss is not None else None,
                         unit="W", atol=1., note="未加入缺失的喷注动能/速度项；内部闭合不等于燃烧性能验证。"))
    if any(v is not None and v < 0 for v in (q, loss)):
        checks.append(_check("heat_loss_sign", "保存热汇不得为负", status="fail"))
    metrics = [_field("inlet_enthalpy_flow_W", "两股入口化学焓流", power_in, "W", "Σṁᵢhᵢ", derived=True),
               _field("outlet_enthalpy_flow_W", "主室出口化学焓流", power_out, "W", "ṁh", derived=True),
               _field("heat_loss_W", "主室零维模型显式热汇", out.get("heat_loss_W"), "W", "main_chamber.outputs.heat_loss_W", "空间扣热模式此项为零；不表示主室壁面无冷却。"),
               _field("combustion_loss_W", "历史温升折减等效热汇", out.get("combustion_loss_W"), "W", "main_chamber.outputs.combustion_loss_W")]
    status = "inconsistent" if any(c["status"] == "fail" for c in checks) else "incomplete" if any(c["status"] == "missing" for c in checks) else "consistent"
    return dict(checks=checks, metrics=metrics, status=status)


def _geometry(result, job_id, out):
    cooling = _map(_map(_map(result.get("calculation")).get("components")).get("cooling_channel"))
    saved = isinstance(result.get("nozzle_geometry"), dict) or isinstance(_map(_map(cooling.get("profile")).get("geometry")).get("full_nodes"), list)
    if not saved:
        return dict(available=False, export_allowed=False, reason="本记录未保存原始完整内型面；本页不按当前代码重建历史主室。", contour={}, dimensions={}, metrics=[], consistency={"status":"partial"})
    full = nozzle_geometry_view(result, job_id or "")
    dims = _map(full.get("dimensions"))
    xt = _number(dims.get("throat_x_m"), True)
    result_view = {key:deepcopy(full.get(key)) for key in ("available", "reason", "source", "consistency", "method")}
    result_view.update(export_allowed=False, contour={}, dimensions=deepcopy(dims), metrics=[])
    if not full.get("available") or xt is None:
        return result_view
    xs, rs = full["contour"]["x_m"], full["contour"]["r_m"]
    # Use the actual saved throat node, with the same absolute coordinate
    # tolerance as the full contour validator. A stricter half-space cutoff
    # can discard the throat and export a truncated convergent section.
    throat_index = min(range(len(xs)), key=lambda i: abs(xs[i]-xt))
    if not math.isclose(xs[throat_index], xt, rel_tol=0., abs_tol=1e-9):
        result_view.update(available=False, reason="保存母线缺少喉部节点，不能截取完整主室。")
        return result_view
    contour = dict(x_m=xs[:throat_index+1], r_m=rs[:throat_index+1], coordinate_origin="injector_face")
    result_view["contour"] = contour
    result_view["contour_sha256"] = hashlib.sha256(json.dumps(contour, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    result_view["dimensions"]["chamber_total_length_m"] = xt
    result_view["export_allowed"] = full.get("consistency", {}).get("status") == "pass"
    area = math.pi*(dims["throat_diameter_m"]/2)**2
    inputs = _map(_map(result.get("calculation")).get("inputs"))
    lstar = _number(inputs.get("chamber_l_star_m"), True)
    expected = {"cc_throat_area_m2":area, "cc_throat_diameter_mm":dims["throat_diameter_m"]*1000,
        "cc_total_length_mm":xt*1000, "cc_convergent_length_mm":(xt-dims["chamber_length_m"])*1000,
        "cc_contraction_ratio":(dims["chamber_diameter_m"]/dims["throat_diameter_m"])**2,
        "cc_volume_m3":lstar*area if lstar else None, "cc_volume_L":lstar*area*1000 if lstar else None}
    extra = [_check("chamber_"+key, "主室尺寸复核 · "+key, out[key], value, rtol=2e-6)
             for key,value in expected.items() if key in out]
    result_view["consistency"]["checks"].extend(extra)
    if any(check["status"] != "pass" for check in extra):
        result_view["export_allowed"] = False
        result_view["consistency"]["status"] = "fail" if any(check["status"]=="fail" for check in extra) else "partial"
    fields = [("cc_diameter_m", "燃烧室内径 Dc", "m"), ("cc_throat_diameter_mm", "喉部内径 Dt", "mm"),
              ("cc_length_m", "圆筒段净长", "m"), ("cc_convergent_length_mm", "收缩段长度", "mm"),
              ("cc_total_length_mm", "喷注面至喉部长度", "mm"), ("cc_volume_m3", "室容积（含收缩段）", "m³"),
              ("cc_cyl_volume_L", "圆筒段容积", "L"), ("cc_convergent_volume_L", "收缩段容积", "L"),
              ("cc_l_star_m", "指定特征长度 L*", "m"), ("cc_contraction_ratio", "收缩比 Ac/At", "—"),
              ("cc_conv_half_angle_deg", "收缩半角", "°"), ("cc_LD_ratio_cyl", "圆筒长径比", "—"),
              ("cc_residence_time_ms", "名义容积更新时间 ρV/ṁ", "ms"),
              ("cc_mach_number", "面积比反算亚声速 Mach", "—"), ("cc_acoustic_1T_Hz", "理想腔体 1T 频率估计", "Hz"),
              ("cc_acoustic_1L_Hz", "理想腔体 1L 频率估计", "Hz")]
    result_view["metrics"] = [_field(k,l,out.get(k),u,"main_chamber.outputs."+k,
        "概念尺寸/筛查量，不代表燃烧完成、声学稳定或制造合格。" if "residence" in k or "acoustic" in k or "mach" in k else "", positive=True) for k,l,u in fields]
    result_view["scope"] = "原保存母线从喷注面截取至喉部，保留原点序；只表示燃气内壁，无结构壁厚或喷注器实体。"
    return result_view


def _material_assignment_audit(profile, inputs, evidence):
    """Audit each saved cell against its recorded zone, never today's catalog."""
    assignment = _map(profile.get("material_assignment"))
    datasets = _map(profile.get("material_applicability")).get("datasets", [])
    identities = {d["dataset_id"]:d.get("material_id") for d in datasets
                  if isinstance(d, dict) and isinstance(d.get("dataset_id"), str)} if isinstance(datasets, list) else {}
    requested, default_dataset = inputs.get("chamber_inner_wall_material"), assignment.get("default_dataset_id")
    default_material = assignment.get("default_material_id") or identities.get(default_dataset)
    issues, checks, mismatch, unknown = [], [], False, 0
    if requested and default_material and requested != default_material:
        mismatch = True
        issues.append(f"方案默认内衬材料 {requested} 与保存分配的默认材料 {default_material} 不一致；分区不取消默认设置的冲突。")
    requested_dataset = inputs.get("wall_thermal_dataset_id")
    if requested_dataset and default_dataset and requested_dataset != default_dataset:
        mismatch = True
        issues.append(f"方案默认热物性数据集 {requested_dataset} 与保存分配 {default_dataset} 不一致。")
    zones_raw = assignment.get("zones", [])
    zones_valid = isinstance(zones_raw, list)
    zones = []
    for zone in zones_raw if zones_valid else []:
        if not isinstance(zone, dict) or _number(zone.get("x_start_m")) is None or _number(zone.get("x_end_m")) is None or zone["x_start_m"] < 0 or zone["x_end_m"] <= zone["x_start_m"] or not isinstance(zone.get("dataset_id"), str):
            zones_valid = False
            continue
        zones.append(zone)
    zone_ids_raw = assignment.get("zone_material_ids", [])
    zone_ids = zone_ids_raw if isinstance(zone_ids_raw, list) else []
    for cell in evidence:
        x = cell["x_m"]
        matches = [z for z in zones if z["x_start_m"] <= x < z["x_end_m"]]
        expected_dataset = default_dataset
        expected_material = default_material or requested
        scope = "default"
        unresolved = not zones_valid or len(matches)>1
        if len(matches)==1:
            zone = matches[0]
            scope = "zone"
            expected_dataset = zone["dataset_id"]
            recorded = [z for z in zone_ids if isinstance(z, dict) and z.get("dataset_id")==expected_dataset
                        and z.get("x_start_m")==zone["x_start_m"] and z.get("x_end_m")==zone["x_end_m"]]
            material_ids = {z.get("material_id") for z in recorded if isinstance(z.get("material_id"), str) and z["material_id"]}
            expected_material = next(iter(material_ids)) if len(material_ids)==1 else identities.get(expected_dataset)
            unresolved = unresolved or len(material_ids)>1 or not expected_material
        actual_material, actual_dataset = cell.get("material_id"), cell.get("dataset_id")
        failed = ((expected_dataset and actual_dataset and expected_dataset != actual_dataset)
                  or (expected_material and actual_material and expected_material != actual_material))
        incomplete = unresolved or not actual_material or not actual_dataset or not expected_dataset or not expected_material
        status = "fail" if failed else "missing" if incomplete else "pass"
        mismatch = mismatch or bool(failed)
        unknown += status == "missing"
        checks.append(dict(key=f"material_cell_{cell['cell_index']}", label="主室单元材料分配", status=status,
            x_m=x, assignment_scope=scope, expected_dataset_id=expected_dataset, expected_material_id=expected_material,
            actual_dataset_id=actual_dataset, actual_material_id=actual_material,
            note="按保存分区边界 x_start≤x<x_end 核查；不解析当前材料目录。"))
    failed_count = sum(check["status"]=="fail" for check in checks)
    if failed_count:
        issues.append(f"主室 {failed_count} 个保存单元的实际材料/数据集与该位置的默认或分区分配不一致；历史记录不改写。")
    if unknown:
        issues.append(f"主室 {unknown} 个单元缺少可解析的保存材料分配身份，标记待复核，不能认定一致。")
    if not zones_valid:
        issues.append("保存材料分区格式或坐标无效，不能确定单元适用材料。")
    return dict(mismatch=mismatch, unknown_cells=unknown, issues=issues, checks=checks, assignment=deepcopy(assignment))


def _thermal(cooling, geometry, inputs):
    profile, out = _map(cooling.get("profile")), _map(cooling.get("outputs"))
    xt = _number(_map(geometry.get("dimensions")).get("throat_x_m"), True)
    xe = _number(_map(geometry.get("dimensions")).get("total_length_m"), True)
    nodes, evidence, issues = [], [], []
    raw_cells = profile.get("cells")
    invalid = raw_cells is not None and not isinstance(raw_cells, list)
    for key in ("heat_load_chamber_W", "heat_load_W"):
        if key in out and out[key] is not None and (_number(out[key]) is None or out[key] < 0):
            invalid = True
    for index, cell in enumerate(raw_cells if isinstance(raw_cells, list) else []):
        if not isinstance(cell, dict):
            invalid = True
            continue
        if cell.get("kind") != "channel":
            continue
        x = _number(cell.get("x_m"))
        if x is None or x < 0 or (xe is not None and x > xe+1e-9):
            invalid = True
            continue
        if xt is None or x >= xt:
            continue
        hot = _map(cell.get("hot_side"))
        node = {k:_number(cell.get(k)) for k in ("x_m", "q_W_m2", "T_wall_hot_K", "T_wall_cold_K", "T_coolant_K", "p_coolant_Pa", "Q_W")}
        node.update({"T_static_K":_number(hot.get("T_static_K")), "p_gas_Pa":_number(hot.get("p_Pa")), "M":_number(hot.get("M")), "T_aw_K":_number(hot.get("T_aw_K"))})
        for k in ("q_W_m2", "T_wall_hot_K", "T_wall_cold_K", "Q_W", "T_coolant_K", "p_coolant_Pa"):
            if k in cell and cell[k] is not None and (_number(cell[k]) is None or cell[k] < 0 or ((k.startswith("T_") or k.startswith("p_")) and cell[k] == 0)):
                invalid = True
        for k in ("T_static_K", "p_Pa", "M", "T_aw_K"):
            if k in hot and hot[k] is not None and (_number(hot[k]) is None or hot[k] < 0 or (k!="M" and hot[k]==0)):
                invalid = True
        nodes.append(node)
        material = _map(cell.get("material"))
        if material:
            evidence.append(deepcopy(material) | dict(cell_index=index, x_m=x))
    nodes.sort(key=lambda n:n["x_m"])
    if any(b["x_m"] <= a["x_m"] for a,b in zip(nodes,nodes[1:])):
        invalid = True
    sample_count = len(nodes)
    missing_samples = {k:sum(node[k] is None for node in nodes) for k in
        ("q_W_m2", "T_wall_hot_K", "T_wall_cold_K", "Q_W", "T_coolant_K", "p_coolant_Pa", "T_static_K", "p_gas_Pa", "M", "T_aw_K")}
    if invalid:
        nodes = []
    def peak(k):
        return max((n[k] for n in nodes if n[k] is not None), default=None)
    fields = [
        _field("heat_load_chamber_W", "冷却模型主室分区热负荷", out.get("heat_load_chamber_W"), "W", "cooling_channel.outputs.heat_load_chamber_W", "主室=喷注面至喉部（x<xt）的冷却 channel 单元。"),
        _field("heat_load_W", "全再生回路吸热", out.get("heat_load_W"), "W", "cooling_channel.outputs.heat_load_W"),
        _field("chamber_peak_heat_flux_W_m2", "主室已保存样本最大热流密度", peak("q_W_m2"), "W/m²", "主室保存 channel 单元",
               f"{sample_count} 个主室单元中 {missing_samples['q_W_m2']} 个缺少该量；不是连续场的完整峰值。", derived=True),
        _field("chamber_peak_wall_temperature_K", "主室已保存样本最大热壁温", peak("T_wall_hot_K"), "K", "主室保存 channel 单元",
               f"{sample_count} 个主室单元中 {missing_samples['T_wall_hot_K']} 个缺少该量；不是连续场的完整峰值。", derived=True)]
    heat_sum = math.fsum(n["Q_W"] for n in nodes) if nodes and all(n["Q_W"] is not None for n in nodes) else None
    thermal_checks = [_check("chamber_heat_sum", "主室保存单元热量之和与主室分区总量", heat_sum,
                             out.get("heat_load_chamber_W"), unit="W", atol=1., rtol=2e-6)]
    actual_materials = sorted({str(m["material_id"]) for m in evidence if m.get("material_id")})
    requested = inputs.get("chamber_inner_wall_material")
    audit = _material_assignment_audit(profile,inputs,evidence)
    assignment, mismatch = audit["assignment"], audit["mismatch"]
    issues.extend(audit["issues"])
    outside = sum(m.get("within_range") is False or m.get("status") == "out_of_range" for m in evidence)
    unknown = len(nodes)-len(evidence) if not invalid else 0
    if outside:
        issues.append(f"主室 {outside} 个保存单元超出材料数据声明温区；端点延拓不是有效实验数据。")
    if not evidence or unknown:
        issues.append("部分或全部主室单元缺少材料物性证据；不能推定材料匹配或温区合格。")
    manufacture_mismatch = sum(m.get("manufacturing_match") == "mismatch" for m in evidence)
    manufacture_unknown = sum(m.get("manufacturing_match") not in ("matched", "mismatch") for m in evidence)
    if manufacture_mismatch:
        issues.append(f"主室 {manufacture_mismatch} 个单元的方案声明工艺与物性数据试样工艺不匹配；不能按该数据集认定硬件工艺。")
    if manufacture_unknown:
        issues.append(f"主室 {manufacture_unknown} 个单元的方案制造工艺未确认或未保存；物性数据的试样工艺不等于实际硬件工艺。")
    matstatus = "inconsistent" if mismatch else "review" if outside or evidence else "missing"
    materials = dict(status=matstatus, metrics=[
        _field("out_of_range_cells", "主室材料温区外单元数", outside if evidence else None, "个", "保存单元 material.within_range/status", derived=True),
        _field("audited_cells", "主室材料证据单元数", len(evidence) if evidence else None, "个", "保存主室材料证据", derived=True)],
        requested_material_id=requested, actual_material_ids=actual_materials, evidence=evidence, issues=issues,
        checks=audit["checks"], unknown_assignment_cells=audit["unknown_cells"],
        assignment=deepcopy(assignment), model_methods=deepcopy(_map(profile.get("model_methods"))),
        scope="仅审计本记录实际采用的材料、温区和制造状态；不以当前目录修正旧结果，不作强度/疲劳/寿命认证。")
    thermal = dict(metrics=fields, nodes=nodes, checks=thermal_checks,
        status="invalid" if invalid else "partial" if nodes and any(missing_samples.values()) else "saved" if nodes else "missing",
        sample_count=sample_count, missing_sample_counts=missing_samples,
        model_id=profile.get("model_id"), throat_x_m=xt,
        scope="保存的一维稳态主室热侧/冷却单元；沿程静温不是有限速率火焰温度分布，未生成 CFD 云图。",
        note="冷却单元格式、坐标或物理量无效，已停止绘图和峰值派生。" if invalid else "保留原计算值；热流关联式与高压输运仍需验证。")
    return thermal, materials


def chamber_design_view(result, job_id=None):
    """Aggregate a single saved FFSC design point, without modifying/re-solving it."""
    empty = dict(available=False, schema_version=1, source=dict(record_id=job_id or "", label="本次计算记录"))
    if not isinstance(result, dict):
        return empty | dict(reason="请先选择已完成的循环计算记录。")
    try:
        require_supported_source({"result":result})
        cal = _map(result.get("calculation"))
        components, states, inputs = _map(cal.get("components")), _map(cal.get("states")), _map(cal.get("inputs"))
        chamber = _map(components.get("main_chamber")); out = _map(chamber.get("outputs"))
        state = _map(states.get("main_chamber")); meta = _map(state.get("metadata"))
        if not out and not state:
            return empty | dict(reason="该记录未保存主燃烧室计算结果。")
        stations = [_station(k,l,states.get(k)) for k,l in [("ox_turbine_exhaust","富氧涡轮排气"),("fuel_turbine_exhaust","富燃涡轮排气"),("main_chamber","主燃烧室参考态")]]
        mode = inputs.get("combustion_loss_model")
        model_id = "hp_equilibrium" if mode == "adiabatic" else "hp_then_frozen_temperature_reduction" if mode == "legacy_temperature_rise" else "unidentified_saved_model"
        model = dict(id=model_id, label="定压定焓气相平衡参考态" if mode == "adiabatic" else "HP 平衡后冻结组成温升折减" if mode == "legacy_temperature_rise" else "记录未完整说明主室方法",
            metadata=deepcopy(_map(meta.get("model"))), state_basis=deepcopy(_map(meta.get("state_basis"))),
            property_definitions=deepcopy(_map(meta.get("property_definitions"))), temperature_range_evidence=deepcopy(_map(meta.get("temperature_range_evidence"))),
            metrics=[_field("legacy_temperature_factor", "历史温升折减系数（非 c* 效率）", out.get("combustion_efficiency") if mode=="legacy_temperature_rise" else None, "—", "main_chamber.outputs.combustion_efficiency", "仅历史温升折减模式启用；不代表燃尽率。")],
            notes=["零维储腔参考态；未求火焰长度、点火延迟、局部热释放或湍流混合。", "气相平衡含离解，CO/OH/O₂ 的存在不能单独解释为不完全燃烧。", "cp、cv、γ 与声速按保存定义解释；声明温区内不等于高压 EOS 或反应机理已验证。"])
        definitions = _map(meta.get("property_definitions"))
        frozen_cp = definitions.get("cp") == "dh_dT_at_constant_p_and_composition"
        frozen_gamma = definitions.get("gamma") == "cp_over_cv_frozen"
        cp_label = "冻结组成定压比热 cp" if frozen_cp else "保存 cp（定义未记录）" if not definitions.get("cp") else "保存 cp（见记录定义）"
        gamma_label = "冻结组成 cp/cv" if frozen_gamma else "保存比热比（定义未记录）" if not definitions.get("gamma") else "保存比热比（见记录定义）"
        metrics = list(stations[2]["metrics"])
        metrics += [_field(k,l,out.get(k),u,"main_chamber.outputs."+k,n,positive=True) for k,l,u,n in [
            ("T_chamber_K","主室模型参考温度","K","不是局部火焰实测温度。"),
            ("T_chamber_ideal_K","经验温升折减前 HP 温度","K","热汇如何扣除取决于保存模型；不能一律称绝热理论温度。"),
            ("cp_Jkgk",cp_label,"J/(kg·K)","不是沿平衡组成变化的有效热容。" if frozen_cp else "定义："+str(definitions.get("cp") or "未保存，不补写冻结/平衡假设。")),
            ("gamma",gamma_label,"—","定义："+str(definitions.get("gamma") or "未保存，不补写冻结/平衡假设。")),("molar_mass_kg_per_kmol","平均摩尔质量","kg/kmol","")]]
        balances = _balances(states,stations,out)
        geometry = _geometry(result,job_id,out)
        thermal, materials = _thermal(_map(components.get("cooling_channel")),geometry,inputs)
        nozzle = _map(_map(components.get("nozzle")).get("outputs"))
        pc, mdot, at = _number(state.get("pressure"),True), _number(state.get("mass_flow"),True), _number(nozzle.get("At_m2"),True)
        cstar = pc*at/mdot if pc and at and mdot else None
        performance = dict(metrics=[
            _field("c_star_effective_ms","本记录系统有效 c*",nozzle.get("c_star_ms"),"m/s","nozzle.outputs.c_star_ms","含本记录热化学及热侧模型的喉部流量接口量。"),
            _field("c_star_identity_ms","Pc·At/ṁ 复核值",cstar,"m/s","主室参考压力×同记录喉面积/主室流量",derived=True),
            _field("c_star_theoretical_ms","同边界理论 c* 基线",None,"m/s","未保存独立同口径基线","不在读取页面时运行平衡/冻结喷管求解。"),
            _field("eta_cstar","试验特征速度效率 ηc*",None,"—","未保存同口径试验与理论对照","不可用历史温升系数、数值闭合或当前 c* 代替。")])
        checks = list(balances["checks"])+list(thermal["checks"])
        checks += [_check("temperature_state","主室输出温度与保存状态",out.get("T_chamber_K"),state.get("temperature"),unit="K"),
                   _check("cstar_identity","系统 c* 与 Pc·At/ṁ",nozzle.get("c_star_ms"),cstar,unit="m/s",rtol=2e-6)]
        for station in stations:
            if station["composition"]["status"] == "invalid":
                checks.append(_check(station["id"]+"_composition", station["label"]+"组成有效性",status="fail"))
            if any(m["status"]=="invalid" for m in station["metrics"]):
                checks.append(_check(station["id"]+"_state",station["label"]+"状态有效性",status="fail"))
        if any(m["status"] == "invalid" for m in metrics):
            checks.append(_check("thermochemical_fields", "保存热化学参数有效性", status="fail"))
        if geometry.get("consistency",{}).get("status")=="fail":
            checks.append(_check("geometry_consistency","保存型面与循环结果一致性",status="fail"))
        if thermal["status"]=="invalid":
            checks.append(_check("thermal_profile","保存热侧单元有效性",status="fail"))
        if materials["status"]=="inconsistent":
            checks.append(_check("material_identity","方案材料与保存求解材料一致性",status="fail",note="；".join(materials["issues"])))
        scopes = [
            dict(key="equilibrium",label="主室总体热化学",status="saved" if model_id!="unidentified_saved_model" else "missing",note="两股实际涡轮排气的质量、组分和化学参考焓 → 指定压力的 HP 平衡；完整保存物种。"),
            dict(key="geometry",label="总体几何与建模交接",status="saved" if geometry.get("available") else "missing",note="同记录原始燃气内母线、关键尺寸、容积和接口；不等于含冷却槽/结构外壳的制造模型。"),
            dict(key="kinetics",label="有限速率反应动力学",status="not_modelled",note="可选后续研究；本轮总体设计不运行未验证的高压反应机理，不给出虚假反应完成时间或燃尽率。"),
            dict(key="mixing",label="局部混合、火焰与热释放",status="not_modelled",note="需要喷注器边界、混合/湍流模型及经验证的反应流分析。"),
            dict(key="stability",label="燃烧稳定性",status="screening_only",note="名义停留时间和理想声学频率不是增长率/阻尼；稳定性需热释放耦合与试验。"),
            dict(key="structure",label="结构、寿命与制造",status="not_validated",note="材料物性数据不是许用值；需要实际材料/工艺、压力及热载荷、疲劳与制造验证。")]
        status = "inconsistent" if any(c["status"]=="fail" for c in checks) else "review"
        view = dict(available=True,schema_version=1,source=dict(record_id=job_id or "",label="同一保存计算记录的主室总体分析"),status=status,
            metrics=metrics,model=model,inlets=stations[:2],composition=stations[2]["composition"],balances=balances,performance=performance,
            geometry=geometry,thermal=thermal,materials=materials,scope=scopes,checks=checks,references=deepcopy(REFERENCES),
            handoff=[dict(topic="反应流 CFD",available="两股入口完整组分、质量流率、温压/焓与状态定义；主室原始内壁坐标。",required="喷注单元实际几何与分配、总/静态和速度边界、湍流/混合假设、机理验证及热边界。"),
                     dict(topic="热结构/寿命",available="本记录主室一维保存热流/壁温及实际材料物性证据。",required="真实槽道/壁厚/壳体、工艺、热物性与强度/蠕变/疲劳许用值、稳态与起停载荷。")],
            notes=["读取页面不重算热力学，不按当前草稿补齐历史字段；缺项保留缺失。", "检查通过仅说明保存数据的内部一致性，不代表真实燃烧或工程验证。"])
        return _clean(view)
    except (ValueError, TypeError, KeyError, OverflowError, ZeroDivisionError) as exc:
        return empty | dict(reason="保存主室数据无法安全解释："+str(exc))
