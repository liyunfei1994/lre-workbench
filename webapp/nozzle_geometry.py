"""Read-only geometric view of a frozen design record, never of the live draft."""
from copy import deepcopy
import hashlib
import json
import math


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _positive(value, name):
    if not _finite(value) or value <= 0:
        raise ValueError(f"缺少有效的{name}。")
    return float(value)


def _mapping(value, name):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{name}格式无效。")
    return value


def _coordinates(nodes):
    if not isinstance(nodes, list) or len(nodes) < 4:
        raise ValueError("记录未保存完整内轮廓坐标。")
    xs, rs = [], []
    for node in nodes:
        if not isinstance(node, dict) or not _finite(node.get("x_m")):
            raise ValueError("轮廓轴向坐标缺失或无效。")
        xs.append(float(node["x_m"]))
        rs.append(_positive(node.get("r_m"), "轮廓半径"))
    if any(b <= a for a, b in zip(xs, xs[1:])):
        raise ValueError("完整轮廓必须按燃气流向严格递增，不能用冷却逆流坐标替代。")
    if not math.isclose(xs[0], 0.0, abs_tol=1e-10):
        raise ValueError("完整轮廓的坐标原点必须是喷注面。")
    return xs, rs


def nozzle_geometry_view(result, record_id=""):
    """Use saved full nodes first; reconstruct only records without a profile.

    Reconstructed budget-mode contours use frozen input/size data and are labelled.
    Invalid or partial saved geometry is never repaired by the latest defaults.
    """
    empty = {"available": False, "export_allowed": False,
             "reason": "选择已完成的设计点记录后查看喷管几何。"}
    if not isinstance(result, dict):
        return empty
    try:
        from webapp.design_scope import require_supported_source
        require_supported_source({"result": result})
        if "nozzle_geometry" in result:
            if not isinstance(result["nozzle_geometry"], dict):
                raise ValueError("保存的几何快照格式无效，不能从其他数据补图。")
            saved = deepcopy(result["nozzle_geometry"])
            if not isinstance(saved.get("source"), dict):
                raise ValueError("几何快照缺少有效的数据来源。")
            saved["source"]["record_id"] = record_id
            if not saved.get("available"):
                return saved | {"export_allowed": False}
            _validate_snapshot(saved)
            return _attach_consistency(saved, result)
        return _attach_consistency(_build(result, record_id), result)
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        return {"available": False, "export_allowed": False, "reason": str(exc),
                "consistency": {"status": "fail", "checks": [], "messages": [str(exc)]},
                "source": {"record_id": record_id, "label": "本次计算记录"}}


def _validate_snapshot(view):
    """Validate frozen coordinates and annotations without regenerating them."""
    contour, dimensions = view.get("contour"), view.get("dimensions")
    if not isinstance(contour, dict) or not isinstance(dimensions, dict):
        raise ValueError("几何快照缺少完整坐标或尺寸。")
    if not isinstance(view.get("method"), dict):
        raise ValueError("几何快照缺少有效的构造方法记录。")
    xs, rs = contour.get("x_m"), contour.get("r_m")
    if not isinstance(xs, list) or not isinstance(rs, list) or len(xs) != len(rs):
        raise ValueError("几何快照坐标不完整。")
    if contour.get("coordinate_origin") != "injector_face":
        raise ValueError("几何快照坐标原点无效，不能猜测坐标系。")
    _coordinates([{"x_m": x, "r_m": r} for x, r in zip(xs, rs)])
    required = ("throat_diameter_m", "chamber_diameter_m", "exit_diameter_m",
                "chamber_length_m", "throat_x_m", "total_length_m",
                "convergent_length_m", "nozzle_length_m", "expansion_ratio")
    for key in required:
        _positive(dimensions.get(key), key)
    rt, rc = dimensions["throat_diameter_m"] / 2, dimensions["chamber_diameter_m"] / 2
    lc, xt = dimensions["chamber_length_m"], dimensions["throat_x_m"]
    if not (0 < lc < xt < xs[-1]):
        raise ValueError("几何快照的燃烧室、喉部和出口位置不一致。")
    comparisons = ((xs[-1], dimensions["total_length_m"]),
                   (2 * rs[-1], dimensions["exit_diameter_m"]),
                   (min(rs), rt), (xt - lc, dimensions["convergent_length_m"]),
                   (xs[-1] - xt, dimensions["nozzle_length_m"]),
                   ((rs[-1] / rt) ** 2, dimensions["expansion_ratio"]))
    if any(not math.isclose(a, b, rel_tol=1e-8, abs_tol=1e-10) for a, b in comparisons):
        raise ValueError("几何快照的坐标与尺寸不一致。")
    ci, ti = (min(range(len(xs)), key=lambda i: abs(xs[i] - x)) for x in (lc, xt))
    if (not math.isclose(xs[ci], lc, abs_tol=1e-9) or not math.isclose(xs[ti], xt, abs_tol=1e-9)
            or not math.isclose(rs[ti], rt, rel_tol=1e-8, abs_tol=1e-10)
            or any(not math.isclose(r, rc, rel_tol=1e-8, abs_tol=1e-10) for r in rs[:ci + 1])
            or any(rs[i + 1] > rs[i] + 1e-10 for i in range(ti))
            or any(rs[i + 1] < rs[i] - 1e-10 for i in range(ti, len(rs) - 1))):
        raise ValueError("几何快照的分段位置或半径不一致，或型面出现反折。")
    cooling = _mapping(view.get("cooling"), "冷却覆盖")
    start, end = cooling.get("start_x_m"), cooling.get("end_x_m")
    if (start is not None or end is not None) and not (
            _finite(start) and _finite(end) and 0 <= start < end <= xs[-1] + 1e-9):
        raise ValueError("几何快照的冷却覆盖范围与完整轮廓不一致。")
    digest = hashlib.sha256(json.dumps(contour, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if view.get("contour_sha256") and digest != view["contour_sha256"]:
        raise ValueError("几何快照坐标校验值不一致，不能导出未经确认的型面。")


def _attach_consistency(view, result):
    """Compare saved evidence; this audit never changes the chosen contour."""
    calculation = _mapping(result.get("calculation"), "计算记录")
    components = _mapping(calculation.get("components"), "部件记录")
    inputs = _mapping(calculation.get("inputs") or result.get("submitted_inputs"), "设计输入")
    dimensions, contour = view["dimensions"], view["contour"]
    checks = []

    def compare(key, label, actual, expected, source_path, unit="", tolerance=2e-8):
        if actual is None or expected is None:
            status = "partial"
        elif _finite(actual) and _finite(expected):
            status = "pass" if math.isclose(actual, expected, rel_tol=tolerance, abs_tol=1e-12) else "fail"
        else:
            status = "pass" if actual == expected else "fail"
        checks.append({"id": key, "label": label, "status": status,
                       "geometry_value": actual, "record_value": expected,
                       "source_path": source_path, "unit": unit, "relative_tolerance": tolerance})

    def output(component, field):
        entry = _mapping(components.get(component), "部件")
        return _mapping(entry.get("outputs"), "部件输出").get(field)

    area = math.pi * (dimensions["throat_diameter_m"] / 2) ** 2
    for key, label, actual, component, field, unit in (
        ("nozzle_area", "型面喉面积 / 循环喷管喉面积", area, "nozzle", "At_m2", "m²"),
        ("nozzle_ratio", "型面面积比 / 循环喷管面积比", dimensions["expansion_ratio"], "nozzle", "expansion_ratio", ""),
        ("chamber_diameter", "燃烧室内径 / 总体尺寸", dimensions["chamber_diameter_m"], "main_chamber", "cc_diameter_m", "m"),
        ("chamber_length", "直筒段长度 / 总体尺寸", dimensions["chamber_length_m"], "main_chamber", "cc_length_m", "m"),
    ):
        compare(key, label, actual, output(component, field), f"calculation.components.{component}.outputs.{field}", unit)
    cooling = _mapping(components.get("cooling_channel"), "冷却部件")
    profile = _mapping(cooling.get("profile"), "冷却剖面")
    profile_geometry = profile.get("geometry") if isinstance(profile, dict) else None
    if isinstance(profile_geometry, dict):
        if "A_throat_m2" in (cooling.get("outputs") or {}):
            compare("cooling_area", "型面喉面积 / 冷却计算喉面积", area, output("cooling_channel", "A_throat_m2"),
                    "calculation.components.cooling_channel.outputs.A_throat_m2", "m²")
        nodes = profile_geometry.get("full_nodes")
        try:
            xs, rs = _coordinates(nodes)
            matches = xs == contour["x_m"] and rs == contour["r_m"]
        except (ValueError, TypeError):
            matches = False
        compare("saved_profile", "显示型面 / 保存的完整冷却型面", True, matches,
                "calculation.components.cooling_channel.profile.geometry.full_nodes")
        for field, saved_key, label in (
            ("bell_fraction", "bell_fraction", "钟形长度比"),
            ("initial_angle_deg", "initial_expansion_angle_deg", "膨胀起始角"),
            ("exit_angle_deg", "exit_angle_deg", "出口角"),
        ):
            if saved_key in profile_geometry:
                compare("profile_" + field, "型面标注 / 冷却几何：" + label,
                        dimensions.get(field), profile_geometry[saved_key],
                        "calculation.components.cooling_channel.profile.geometry." + saved_key)
    elif view["source"].get("kind") == "saved_full_contour":
        compare("saved_profile", "显示型面 / 保存的完整冷却型面", True, None,
                "calculation.components.cooling_channel.profile.geometry.full_nodes")

    derived = {"expansion_ratio": dimensions["expansion_ratio"],
               "chamber_contraction_ratio": (dimensions["chamber_diameter_m"] / dimensions["throat_diameter_m"]) ** 2,
               "nozzle_bell_fraction": dimensions.get("bell_fraction"),
               "nozzle_contour_method": view.get("method", {}).get("id")}
    beta = dimensions.get("convergent_half_angle_deg")
    if beta is None and isinstance(profile_geometry, dict):
        beta = profile_geometry.get("conv_half_angle_deg")
    derived["chamber_conv_half_angle_deg"] = beta
    if _finite(beta) and 0 < beta < 90:
        from lre_workbench.components.chambers.contour import convergent_geometry
        rc, rt = dimensions["chamber_diameter_m"] / 2, dimensions["throat_diameter_m"] / 2
        volume = math.pi * rc * rc * dimensions["chamber_length_m"] + convergent_geometry(rc, rt, beta)["V_conv_m3"]
        derived["chamber_l_star_m"] = volume / area
    else:
        derived["chamber_l_star_m"] = None
    if derived["nozzle_contour_method"] == "parabolic_bell_explicit_v1":
        derived.update(nozzle_initial_angle_deg=dimensions.get("initial_angle_deg"),
                       nozzle_exit_angle_deg=dimensions.get("exit_angle_deg"))
    labels = {"expansion_ratio": "喷管面积比", "chamber_contraction_ratio": "燃烧室收缩比",
              "nozzle_bell_fraction": "钟形长度比", "nozzle_contour_method": "型面构造方法",
              "nozzle_initial_angle_deg": "膨胀起始角", "nozzle_exit_angle_deg": "出口角",
              "chamber_conv_half_angle_deg": "收缩半角", "chamber_l_star_m": "特征长度 L*"}
    for key, value in derived.items():
        unit = "°" if key.endswith("_deg") else "m" if key == "chamber_l_star_m" else ""
        compare("input_" + key, labels[key] + " / 本记录设计输入", value, inputs.get(key), "calculation.inputs." + key, unit)
    digest = hashlib.sha256(json.dumps(contour, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    compare("contour_hash", "保存坐标校验值", digest, view.get("contour_sha256"), "nozzle_geometry.contour_sha256")
    status = "fail" if any(row["status"] == "fail" for row in checks) else (
        "partial" if any(row["status"] == "partial" for row in checks) else "pass")
    messages = (["保存型面与本条计算记录存在矛盾；保留原坐标供核查，暂停坐标导出。"] if status == "fail" else
                ["部分历史证据未记录；已验证保存坐标，但不能据此宣称与全部循环尺寸完全一致。"] if status == "partial" else
                ["型面与本记录的循环尺寸及设计输入在数值容差内一致。"])
    view["consistency"] = {"status": status, "checks": checks, "messages": messages,
                           "scope": "同一保存计算记录的几何数据一致性；不是气动优化或制造验证"}
    view["export_allowed"] = status != "fail"
    return view


def _build(result, record_id):
    from lre_workbench.components.nozzles.geometry import full_hot_gas_geometry, top_bell_angles
    from lre_workbench.components.chambers.contour import convergent_geometry

    calculation = _mapping(result.get("calculation"), "计算记录")
    inputs = _mapping(calculation.get("inputs") or result.get("submitted_inputs"), "设计输入")
    components = _mapping(calculation.get("components"), "部件记录")
    cooling = _mapping(components.get("cooling_channel"), "冷却部件")
    profile = _mapping(cooling.get("profile"), "冷却剖面")
    saved_geometry = profile.get("geometry") if isinstance(profile, dict) else None
    notes = ["显示燃气侧内轮廓的轴对称几何；三维曲面不含壁厚、冷却槽或结构外壳。",
             "几何有效不等于推力最优，尚未包含二维流动损失、分离或结构验证。"]
    if isinstance(profile, dict) and "geometry" in profile:
        if not isinstance(saved_geometry, dict):
            raise ValueError("保存的冷却几何格式无效，不能改用当前参数补图。")
        g = saved_geometry
        xs, rs = _coordinates(g.get("full_nodes"))
        source = {"label": "计算记录保存的完整内轮廓", "kind": "saved_full_contour", "record_id": record_id}
        coverage_end = g.get("coverage_end_x_m")
        cooled = g.get("nodes") or []
        if not isinstance(cooled, list) or any(not isinstance(n, dict) or not _finite(n.get("x_m")) for n in cooled):
            raise ValueError("保存的冷却覆盖节点无效。")
        coverage_start = min((n["x_m"] for n in cooled), default=None)
        if coverage_end is not None and (not _finite(coverage_end) or coverage_start is None
                or not (0 <= coverage_start < coverage_end <= xs[-1] + 1e-9)):
            raise ValueError("保存的冷却覆盖范围与完整轮廓不一致。")
        rt = _positive(g.get("r_t_m"), "喉半径")
        rc = _positive(g.get("r_c_m"), "燃烧室半径")
        Lc = _positive(g.get("L_cyl_m"), "燃烧室直段长度")
        xt = _positive(g.get("x_throat_m"), "喉部位置")
        eps = _positive(g.get("expansion_ratio"), "喷管面积比")
        bf = _positive(g.get("bell_fraction"), "喷管长度比")
        method_id = g.get("nozzle_contour_method", "legacy_log_v1")
    else:
        nozzle_component = _mapping(components.get("nozzle"), "喷管部件")
        nozzle = _mapping(nozzle_component.get("outputs"), "喷管输出")
        area = _positive(nozzle.get("At_m2"), "本次计算的喉部面积")
        eps = _positive(nozzle.get("expansion_ratio", inputs.get("expansion_ratio")), "喷管面积比")
        contraction = _positive(inputs.get("chamber_contraction_ratio"), "燃烧室收缩比")
        lstar = _positive(inputs.get("chamber_l_star_m"), "特征长度")
        beta = _positive(inputs.get("chamber_conv_half_angle_deg"), "收缩半角")
        bf = _positive(inputs.get("nozzle_bell_fraction"), "喷管长度比")
        method_id = inputs.get("nozzle_contour_method", "legacy_log_v1")
        rt = math.sqrt(area / math.pi)
        rc = rt * math.sqrt(contraction)
        Lc = (lstar * area - convergent_geometry(rc, rt, beta)["V_conv_m3"]) / (math.pi * rc * rc)
        _positive(Lc, "燃烧室直段长度")
        options = dict(bell_fraction=bf, theta_conv_deg=beta, n_points=300)
        if method_id != "legacy_log_v1":
            options.update(nozzle_contour_method=method_id,
                           nozzle_initial_angle_deg=inputs.get("nozzle_initial_angle_deg"),
                           nozzle_exit_angle_deg=inputs.get("nozzle_exit_angle_deg"))
        g = full_hot_gas_geometry(rt, rt * math.sqrt(eps), rc, eps, Lc, **options)
        xs, rs = _coordinates([{"x_m": x, "r_m": r} for x, r in zip(g["contour_x"], g["contour_r"])])
        xt = g["x_throat_m"]
        source = {"label": "由本记录尺寸与输入重建的型面", "kind": "reconstructed_from_record", "record_id": record_id}
        coverage_start = coverage_end = None
        notes.append("原记录未保存冷却完整轮廓；此图只按该记录的设计尺寸构造，不补造冷却结果。")
    if not (0 < Lc < xt < xs[-1]):
        raise ValueError("燃烧室、喉部和出口的轴向尺寸不一致。")
    ci = min(range(len(xs)), key=lambda i: abs(xs[i] - Lc))
    if (not math.isclose(xs[ci], Lc, abs_tol=1e-9)
            or any(not math.isclose(r, rc, rel_tol=1e-8, abs_tol=1e-10) for r in rs[:ci + 1])
            or rs[ci + 1] >= rc):
        raise ValueError("保存轮廓与燃烧室直段半径或长度不一致。")
    if not math.isclose(min(rs), rt, rel_tol=1e-8, abs_tol=1e-10):
        raise ValueError("保存轮廓与喉部尺寸不一致。")
    if not math.isclose((rs[-1] / rt) ** 2, eps, rel_tol=1e-8):
        raise ValueError("保存轮廓与出口面积比不一致。")
    ti = min(range(len(xs)), key=lambda i: abs(xs[i] - xt))
    if not math.isclose(xs[ti], xt, abs_tol=1e-9) or not math.isclose(rs[ti], rt, rel_tol=1e-8):
        raise ValueError("完整轮廓中缺少与尺寸一致的喉部节点。")
    if any(rs[i + 1] > rs[i] + 1e-10 for i in range(ti)) or any(rs[i + 1] < rs[i] - 1e-10 for i in range(ti, len(rs) - 1)):
        raise ValueError("内轮廓在收缩或膨胀方向出现反折。")
    if method_id == "legacy_log_v1":
        tn, te = top_bell_angles(eps, bf)
        angle_source = "历史经验角度表达式；未建立拟合误差与适用域证据"
        method_label = "抛物线钟形喷管 · 历史经验角度"
    elif method_id == "parabolic_bell_explicit_v1":
        tn, te = g.get("initial_expansion_angle_deg"), g.get("exit_angle_deg")
        if not (_finite(tn) and _finite(te)):
            raise ValueError("显式角度型面缺少随几何保存的角度。")
        angle_source = "方案指定的起始角与出口角；不代表完成推力优化"
        method_label = "抛物线钟形喷管 · 指定端点角度"
    else:
        tn = te = None
        angle_source = "记录未提供可识别的方法证据；仅展示保存坐标"
        method_label = "保存的轴对称内轮廓"
    contour = {"x_m": xs, "r_m": rs, "coordinate_origin": "injector_face"}
    fingerprint = hashlib.sha256(json.dumps(contour, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"available": True, "schema_version": 1, "source": source,
            "method": {"id": method_id, "label": method_label, "angle_source": angle_source, "notes": notes},
            "contour": contour, "contour_sha256": fingerprint,
            "dimensions": {"throat_diameter_m": 2 * rt, "exit_diameter_m": 2 * rs[-1],
                "chamber_diameter_m": 2 * rc, "chamber_length_m": Lc,
                "convergent_length_m": xt - Lc, "nozzle_length_m": xs[-1] - xt,
                "convergent_half_angle_deg": g.get("conv_half_angle_deg"),
                "total_length_m": xs[-1], "throat_x_m": xt,
                "initial_angle_deg": tn, "exit_angle_deg": te,
                "throat_upstream_radius_m": g.get("throat_upstream_radius_m"),
                "throat_downstream_radius_m": g.get("throat_downstream_radius_m"),
                "expansion_ratio": eps, "bell_fraction": bf,
                "inner_surface_area_m2": sum(math.pi * (a + b) * math.hypot(y - x, b - a)
                    for x, y, a, b in zip(xs, xs[1:], rs, rs[1:]))},
            "cooling": {"start_x_m": coverage_start, "end_x_m": coverage_end},
            "validation": {"status": "geometry_valid", "node_count": len(xs),
                "checks": ["轴向坐标递增", "燃烧室直段尺寸一致", "收缩与膨胀段无反折", "喉部与出口面积比一致"],
                "scope": "几何一致性检查"}}
