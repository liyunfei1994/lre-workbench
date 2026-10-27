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
    empty = {"available": False, "reason": "选择已完成的设计点记录后查看喷管几何。"}
    if not isinstance(result, dict):
        return empty
    try:
        if isinstance(result.get("nozzle_geometry"), dict):
            saved = deepcopy(result["nozzle_geometry"])
            if not isinstance(saved.get("source"), dict):
                raise ValueError("几何快照缺少有效的数据来源。")
            saved["source"]["record_id"] = record_id
            return saved
        return _build(result, record_id)
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        return {"available": False, "reason": str(exc),
                "source": {"record_id": record_id, "label": "本次计算记录"}}


def _build(result, record_id):
    from geometry.nozzle import full_hot_gas_geometry, top_bell_angles
    from chamber_contour import convergent_geometry

    calculation = result.get("calculation") or {}
    inputs = calculation.get("inputs") or result.get("submitted_inputs") or {}
    components = calculation.get("components") or {}
    profile = (components.get("cooling_channel") or {}).get("profile")
    saved_geometry = profile.get("geometry") if isinstance(profile, dict) else None
    notes = ["显示燃气侧内轮廓的轴对称几何；三维曲面不含壁厚、冷却槽或结构外壳。",
             "几何有效不等于推力最优，尚未包含二维流动损失、分离或结构验证。"]
    if saved_geometry is not None:
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
        nozzle = (components.get("nozzle") or {}).get("outputs") or {}
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
