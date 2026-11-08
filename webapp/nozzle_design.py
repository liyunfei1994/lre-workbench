"""Read-only nozzle engineering review of one immutable calculation record."""
from __future__ import annotations

from copy import deepcopy
import math

from lre_workbench.components.nozzles.performance import ambient_characteristic, finite_number
from lre_workbench.domain.models import G0
from webapp.design_scope import require_supported_source
from webapp.nozzle_geometry import nozzle_geometry_view

REFERENCES = [
    dict(title="NASA SP-8120 · Liquid Rocket Engine Nozzles", year=1976,
         url="https://ntrs.nasa.gov/citations/19770009165",
         use="型面、损失、热/结构和试验的总体设计框架；旧经验相关式须核对适用域。"),
    dict(title="NASA Glenn · Rocket Thrust Equation", year=None,
         url="https://www1.grc.nasa.gov/beginners-guide-to-aeronautics/rocket-thrust-equation/",
         use="动量推力与压力推力的分解。"),
    dict(title="DLR · Design of an ideal truncated nozzle for a re-usable first stage launcher", year=2024,
         url="https://elib.dlr.de/197454/", use="甲烷可复用一级 TIC 型面与 Rao 型面对照；羽流和底部热环境需联合评估。"),
    dict(title="NASA · Technology Advancements for Channel Wall Nozzle Manufacturing", year=2020,
         url="https://ntrs.nasa.gov/citations/20205002297", use="槽道壁制造、薄壁公差、材料和热试验；补充 SP-8120 未覆盖的现代槽道壁工艺。"),
]


def _mapping(value, name):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(name + "格式无效。")
    return value


def _num(value, positive=False):
    return float(value) if finite_number(value) and (not positive or value > 0) else None


def _metric(key, label, value, unit, basis, note="", derived=False):
    value = _num(value)
    return dict(key=key, label=label, value=value, unit=unit, basis=basis, note=note,
                status="missing" if value is None else "derived" if derived else "saved")


def _check(key, label, actual, expected, note="", tolerance=2e-6):
    a, e = _num(actual), _num(expected)
    error = abs(a-e)/max(abs(a), abs(e), 1e-12) if a is not None and e is not None else None
    return dict(key=key, label=label, actual=a, expected=e, relative_error=error,
                relative_tolerance=tolerance,
                status="missing" if error is None else "pass" if error <= tolerance else "fail", note=note)


def _flow(profile, geometry):
    raw = profile.get("nodes")
    empty = dict(available=False, status="missing", nodes=[], reason="本记录未保存经过校核的轴向燃气状态；不由外形插造流场。")
    if raw is None:
        return empty
    empty["status"] = "invalid"
    if not isinstance(raw, list) or len(raw) < 3:
        return empty | dict(reason="已保存的轴向燃气剖面为空或格式无效。")
    keys = ("x_m", "area_ratio", "M", "p_Pa", "T_K", "velocity_ms")
    nodes = []
    for row in raw:
        if not isinstance(row, dict) or any(_num(row.get(k)) is None for k in keys):
            return empty | dict(reason="保存的轴向燃气节点不完整或含非有限值。")
        node = {k:float(row[k]) for k in keys}
        if node["x_m"] < 0 or any(node[k] <= 0 for k in keys[1:]):
            return empty | dict(reason="保存的轴向燃气节点超出物理范围。")
        if node["area_ratio"] < 1 - 1e-8:
            return empty | dict(reason="保存的 A/At 小于 1，与最小喉面积定义不一致。")
        nodes.append(node)
    if any(b["x_m"] <= a["x_m"] for a, b in zip(nodes, nodes[1:])):
        return empty | dict(reason="燃气轴向节点非递增；不能绘制为有效流场。")
    if abs(nodes[0]["x_m"]) > 1e-9:
        return empty | dict(reason="燃气剖面缺少喷注面 x=0 的起点。")
    dims = geometry.get("dimensions", {})
    if geometry.get("available") and not math.isclose(nodes[-1]["x_m"], dims["total_length_m"], rel_tol=2e-6):
        return empty | dict(reason="燃气末端坐标与本记录型面不一致。")
    return dict(available=True, status="saved", model_id=profile.get("model_id"), nodes=nodes,
                throat_x_m=dims.get("throat_x_m"), sonic_x_m=_num(profile.get("critical_x_m")),
                scope="同一记录保存的燃气静态 p、T 与截面平均速度/Mach；不是壁面压力或 CFD 流场。")


def _thermal(cooling, geometry):
    outputs = _mapping(cooling.get("outputs"), "冷却输出")
    profile = _mapping(cooling.get("profile"), "冷却剖面")
    xt = geometry.get("dimensions", {}).get("throat_x_m")
    xe = geometry.get("dimensions", {}).get("total_length_m")
    nodes, material_outside, invalid_cells = [], 0, 0
    for cell in profile.get("cells", []) if isinstance(profile.get("cells", []), list) else []:
        if not isinstance(cell, dict) or cell.get("kind") != "channel":
            continue
        x = _num(cell.get("x_m"))
        if x is None or (xe is not None and not 0 <= x <= xe + 1e-9):
            invalid_cells += 1
            continue
        if xt is None or x < xt:
            continue
        node = {k:_num(cell.get(k)) for k in ("x_m", "q_W_m2", "T_wall_hot_K", "T_wall_cold_K")}
        nodes.append(node)
        if isinstance(cell.get("material"), dict) and cell["material"].get("within_range") is False:
            material_outside += 1
    nodes.sort(key=lambda n:n["x_m"])
    if invalid_cells:
        nodes = []
    def peak(key):
        return max((n[key] for n in nodes if n[key] is not None), default=None)
    metrics = [
        _metric("heat_load_nozzle_W", "冷却模型喷管分区热负荷", outputs.get("heat_load_nozzle_W"), "W",
                "cooling_channel.outputs.heat_load_nozzle_W", "沿用冷却模型分区口径，不能据此推定整段壁温。"),
        _metric("heat_load_W", "全再生回路吸热", outputs.get("heat_load_W"), "W", "cooling_channel.outputs.heat_load_W"),
        _metric("nozzle_peak_heat_flux_W_m2", "扩张段最大保存热流密度", peak("q_W_m2"), "W/m²", "冷却 channel 单元，x≥喉部", derived=True),
        _metric("nozzle_peak_wall_temperature_K", "扩张段最高保存热壁温", peak("T_wall_hot_K"), "K", "冷却 channel 单元，x≥喉部", derived=True),
        _metric("coolant_pressure_drop_Pa", "全再生回路压降", outputs.get("delta_P_MPa")*1e6 if _num(outputs.get("delta_P_MPa")) is not None else None,
                "Pa", "cooling_channel.outputs.delta_P_MPa × 10⁶", "整条回路压降，不是独立喷管段压降。", derived=True),
    ]
    return dict(metrics=metrics, nodes=nodes, status="invalid" if invalid_cells else "saved" if nodes else "missing",
                invalid_cell_count=invalid_cells, scope="已保存的稳态热流/壁温模型；无应力、疲劳寿命或制造验收结论。",
                material_outside_range_cells=material_outside,
                note=(f"{invalid_cells} 个冷却单元坐标无效或超出完整型面范围，未绘制热分布或报告峰值。" if invalid_cells else
                      f"扩张段 {material_outside} 个单元的材料物性超出声明温区，需复核。" if material_outside else
                      "材料制造状态、物性范围和热应力需在冷却/结构分析中进一步核对。"))


def nozzle_design_view(result, record_id=""):
    empty = dict(available=False, schema_version=1, source=dict(record_id=record_id, label="本次计算记录"))
    if not isinstance(result, dict):
        return empty | dict(reason="请先选择已完成的循环计算记录。")
    try:
        require_supported_source({"result": result})
        calculation = _mapping(result.get("calculation"), "循环结果")
        summary = _mapping(calculation.get("summary"), "循环汇总")
        components = _mapping(calculation.get("components"), "部件结果")
        nozzle = _mapping(components.get("nozzle"), "喷管部件")
        out = _mapping(nozzle.get("outputs"), "喷管输出")
        profile = _mapping(nozzle.get("profile"), "喷管剖面")
        if not out:
            return empty | dict(reason="该记录尚无喷管计算结果。")
        pc = _num(summary.get("chamber_pressure_Pa"), True)
        mdot = _num(summary.get("mdot_total_kg_s"), True)
        at, ae = _num(out.get("At_m2"), True), _num(out.get("Ae_m2"), True)
        ae_derived = "Ae_m2" not in out
        eps, pa, pe, ve = (_num(out.get(k)) for k in ("expansion_ratio", "p_amb_Pa", "p_exit_Pa", "Ve_ms"))
        if ae_derived and at and eps and eps >= 1:
            ae = at*eps
        scale = pc*at if pc and at else None
        cstar = scale/mdot if scale and mdot else None
        force = _num(out.get("thrust_N"))
        fv = _num(out.get("thrust_vac_N"))
        cf = force/scale if force is not None and scale else None
        mom = mdot*ve if mdot and ve and ve > 0 else None
        fp = (pe-pa)*ae if ae and pe is not None and pa is not None else None
        fields = [
            ("pc_Pa", "主室模型参考压力 Pc", pc, "Pa", "calculation.summary.chamber_pressure_Pa", "沿用本循环储室基准；不可把未知基准的喷注面静压直接当喷管总压。"),
            ("mdot_kg_s", "主喷管质量流量", mdot, "kg/s", "calculation.summary.mdot_total_kg_s", "FFSC 同一记录总流量；不使用目标流量。"),
            ("p_amb_Pa", "当前环境压力", pa, "Pa", "nozzle.outputs.p_amb_Pa", ""),
            ("At_m2", "喉部面积 At", at, "m²", "nozzle.outputs.At_m2", ""),
            ("Ae_m2", "出口面积 Ae", ae, "m²", "nozzle.outputs.Ae_m2 / At·ε", ""),
            ("expansion_ratio", "膨胀面积比 ε", eps, "—", "nozzle.outputs.expansion_ratio", ""),
            ("c_star_ms", "特征速度 c*", cstar, "m/s", "Pc·At/ṁ", "燃烧/热化学与喉部流量的接口量；不是喷管效率，亦非已测燃烧效率。"),
            ("Cf", "当前环境推力系数 CF", cf, "—", "F/(Pc·At)", "包含本记录已建模的效应；未建模损失不自动补偿。"),
            ("Cf_vac", "真空推力系数 CF,vac", fv/scale if fv is not None and scale else None, "—", "Fvac/(Pc·At)", ""),
            ("Isp_s", "当前环境比冲", _num(out.get("Isp_s")), "s", "nozzle.outputs.Isp_s", ""),
            ("Isp_vac_s", "真空比冲", _num(out.get("Isp_vac_s")), "s", "nozzle.outputs.Isp_vac_s", ""),
            ("thrust_N", "当前环境推力", force, "N", "nozzle.outputs.thrust_N", ""),
            ("thrust_vac_N", "真空推力", fv, "N", "nozzle.outputs.thrust_vac_N", ""),
            ("momentum_thrust_N", "动量推力", mom, "N", "ṁ·Ve", "截面平均的一维轴向动量；未显式计入二维发散。"),
            ("pressure_thrust_N", "压力推力", fp, "N", "(pe−pa)·Ae", "过膨胀时可为负值；这不是喷管壳体载荷。"),
            ("Ve_ms", "出口速度 Ve", ve, "m/s", "nozzle.outputs.Ve_ms", ""),
            ("p_exit_Pa", "出口静压 pe", pe, "Pa", "nozzle.outputs.p_exit_Pa", "截面平均值，不替代局部壁面压力。"),
            ("T_exit_K", "出口静温", _num(out.get("T_exit_K")), "K", "nozzle.outputs.T_exit_K", ""),
            ("mach_exit", "出口 Mach", _num(out.get("mach_exit")), "—", "nozzle.outputs.mach_exit", ""),
            ("pe_pa_ratio", "出口/环境压力比", pe/pa if pe is not None and pa and pa > 0 else None, "—", "pe/pa", "真空时不定义有限压力比。"),
            ("npr", "室压/环境压力比 NPR", pc/pa if pc and pa and pa > 0 else None, "—", "Pc/pa", ""),
            ("dF_dpa_m2", "环境背压推力灵敏度 dF/dpa", -ae if ae else None, "N/Pa", "−Ae", "仅对固定内部状态、满流假设成立。"),
        ]
        derived_keys = {"c_star_ms", "Cf", "Cf_vac", "momentum_thrust_N", "pressure_thrust_N", "pe_pa_ratio", "npr", "dF_dpa_m2"}
        if ae_derived:
            derived_keys.add("Ae_m2")
        metrics = [_metric(*row, derived=row[0] in derived_keys) for row in fields]
        geometry = nozzle_geometry_view(result, record_id)
        dims = geometry.get("dimensions", {})
        gview = {k:deepcopy(geometry.get(k)) for k in ("available", "reason", "dimensions", "method", "source", "consistency", "contour_sha256")}
        cone = ((dims["exit_diameter_m"]-dims["throat_diameter_m"])/2/math.tan(math.radians(15))
                if geometry.get("available") else None)
        gview["derived"] = [
            _metric("reference_cone_length_m", "同面积比 15°半锥角参考长度", cone, "m", "(re−rt)/tan(15°)", derived=True),
            _metric("bell_length_fraction", "扩张段 / 参考圆锥长度", dims.get("nozzle_length_m", 0)/cone if cone else None,
                    "—", "Ln/Lcone", "只比较喉部至出口长度，不是整个推力室长度。", derived=True),
        ]
        checks = [
            _check("area", "Ae = ε·At", ae, eps*at if eps and at else None),
            _check("cstar", "保存 c* 与 Pc·At/ṁ", out.get("c_star_ms"), cstar),
            _check("cf", "保存 CF 与 F/(Pc·At)", out.get("Cf"), cf),
            _check("cf_vac", "保存 CF,vac 与 Fvac/(Pc·At)", out.get("Cf_vac"), fv/scale if fv is not None and scale else None),
            _check("isp_vac", "保存真空比冲与 Fvac/(ṁ·g₀)", out.get("Isp_vac_s"), fv/(mdot*G0) if fv is not None and mdot else None),
            _check("thrust", "推力 = 动量项 + 压力项", force, mom+fp if mom is not None and fp is not None else None),
            _check("vacuum", "Fvac−F = pa·Ae", fv-force if fv is not None and force is not None else None, pa*ae if pa is not None and ae else None),
            _check("isp", "Isp·g₀ = c*·CF", out.get("Isp_s")*G0 if _num(out.get("Isp_s")) is not None else None, cstar*cf if cstar and cf is not None else None),
            _check("summary_thrust", "喷管推力与循环汇总", force, summary.get("thrust_N")),
            _check("geometry_area", "内型面喉面积与喷管计算", math.pi*(dims["throat_diameter_m"]/2)**2 if dims.get("throat_diameter_m") else None, at),
        ]
        flow = _flow(profile, geometry)
        if flow["available"]:
            end = flow["nodes"][-1]
            for key, target in (("p_Pa", pe), ("T_K", out.get("T_exit_K")), ("M", out.get("mach_exit")), ("velocity_ms", ve), ("area_ratio", eps)):
                checks.append(_check("exit_"+key, "轴向末端 / 出口结果 · "+key, end[key], target))
        heated = profile.get("model_id") == "heated_frozen_quasi1d_v1"
        mode = out.get("flow_model")
        model_id = profile.get("model_id") or {0:"frozen_ideal_gas_isentropic", -1:"constant_gamma_isentropic", 1:"equilibrium_isentropic"}.get(mode, "unidentified_saved_model")
        thermal = _thermal(_mapping(components.get("cooling_channel"), "冷却部件"), geometry)
        invalid_ae = not ae_derived and ae is None
        if invalid_ae:
            checks.append(dict(key="saved_exit_area", label="保存的出口面积无效", status="fail", actual=None, expected=None,
                               relative_error=None, note="已有 Ae 非正、缺值或非有限；不能用派生值替换。"))
        if flow.get("status") == "invalid":
            checks.append(dict(key="flow_profile", label="保存的燃气剖面无效", status="fail", actual=None, expected=None,
                               relative_error=None, note=flow["reason"]))
        if thermal.get("status") == "invalid":
            checks.append(dict(key="thermal_profile", label="保存的冷却单元坐标无效", status="fail", actual=None, expected=None,
                               relative_error=None, note=thermal["note"]))
        status = "inconsistent" if any(c["status"] == "fail" for c in checks) or geometry.get("consistency", {}).get("status") == "fail" else "review"
        ambient = dict(available=False, points=[], reason="缺少独立出口状态或保存值不一致，未生成环境曲线。")
        if status != "inconsistent":
            try:
                ambient = ambient_characteristic(pc=pc, mdot=mdot, at=at, ae=ae, ve=ve, pe=pe, design_pa=pa)
            except ValueError:
                pass
        losses = [
            dict(key="heat", label="壁面换热与气体能量损失", status="included" if heated else "not_modelled",
                 note="已在耦合能量方程扣热，不应再乘一个经验热损效率。" if heated else "本记录喷管求解未识别为逐段扣热耦合模型。"),
            dict(key="chemistry", label="化学反应模型", status="model_assumption", note="冻结组成，不含有限速率化学；与平衡膨胀的差别未分解。" if mode == 0 else "按保存模型解释，不凭本页推算化学损失系数。"),
            dict(key="divergence", label="二维发散 / 非均匀流", status="not_modelled", note="钟形端点角不能直接代入圆锥发散效率；需 MOC/二维流动分析。"),
            dict(key="friction", label="燃气侧摩擦 / 边界层位移", status="not_modelled", note="冷却剂摩擦压降不等于燃气侧摩擦损失。"),
            dict(key="separation", label="激波、分离与侧向载荷", status="not_modelled", note="经验筛查只提示风险，不能输出可靠侧载幅值。"),
            dict(key="efficiency", label="综合喷管效率 / c*效率", status="not_modelled", note="缺少同基准理想对照与试验，不填默认 0.98 或将当前结果当实测效率。"),
        ]
        return dict(available=True, schema_version=1, source=dict(record_id=record_id, label="同一计算记录的喷管总体分析", model_id=model_id,
                    scope="保存值与派生核算；检查通过仅表示数据一致，不代表工程验证。"), status=status,
                    metrics=metrics, geometry=gview, checks=checks, flow_profile=flow, thermal=thermal,
                    ambient=ambient, losses=losses, references=deepcopy(REFERENCES),
                    handoff=[
                        dict(topic="总体设计", available="Pc、ṁ、At/Ae、性能分解、轴向已保存状态与热负荷。", required="权衡飞行环境、长度/质量、冷却能力；改变面积比或型面后重新求解循环。"),
                        dict(topic="型面设计", available="指定角度的圆弧 + 二次 Bézier 工程钟形。", required="独立 MOC/TIC/TOP 优化与边界层修正；不能称作已优化 Rao。"),
                        dict(topic="CFD", available="原始内型面坐标及同记录气体状态。", required="组分/物性、入口总态、出口背压包线、粗糙度与热边界；评估激波、分离、非均匀流及启动/关机。"),
                        dict(topic="有限元与寿命", available="保存的一维热载荷可作为初步交接。", required="内外压力分布、壁厚/槽道/加强结构、材料制造态、支承、瞬态侧载、热循环疲劳及屈曲。推力不能直接代替壳体载荷。"),
                    ])
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        return empty | dict(reason=str(exc) or "喷管保存数据无效。")


def nozzle_ambient_view(result, request, record_id=""):
    if not isinstance(request, dict) or set(request) != {"maximum_pressure_Pa", "point_count"}:
        raise ValueError("环境特性请求须包含压力上限 maximum_pressure_Pa 和点数 point_count。")
    view = nozzle_design_view(result, record_id)
    if not view.get("available") or not view.get("ambient", {}).get("available"):
        raise ValueError("本记录缺少可核对的喷管状态，或存在数据不一致；不能生成环境曲线。")
    values = {m["key"]:m["value"] for m in view["metrics"]}
    curve = ambient_characteristic(pc=values["pc_Pa"], mdot=values["mdot_kg_s"], at=values["At_m2"],
        ae=values["Ae_m2"], ve=values["Ve_ms"], pe=values["p_exit_Pa"], design_pa=values["p_amb_Pa"], **request)
    curve["source"] = deepcopy(view["source"])
    return curve
