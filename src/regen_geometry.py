"""One geometric mesh for system-level coolant heat and momentum balances.

The hot-wall contour is the same formal contour used by chamber/nozzle geometry.
Channel width is the throat width scaled with radius unless an explicit table is
provided. The table is never silently replaced by another geometry.
"""
from __future__ import annotations

import math
from bisect import bisect_left


def _positive(name, value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return value


def build_regen_geometry(*, r_t, r_c, L_cyl, expansion_ratio, cover_eps=0.0,
                         bell_fraction=0.8, conv_half_angle_deg=30.0,
                         n_points=120, counterflow=True, n_channels=300,
                         channel_width_m=1.5e-3, channel_height_m=5e-3,
                         wall_thickness_m=0.5e-3, channel_table=None,
                         material_boundaries_m=(),
                         nozzle_contour_method="legacy_log_v1",
                         nozzle_initial_angle_deg=35.0, nozzle_exit_angle_deg=10.0):
    """Return channel-boundary nodes and cells in coolant-flow order.

    ``s_m`` is distance along the coolant path; ``x_m`` remains the common gas
    coordinate (injector face is zero). Coverage ends exactly at the requested
    divergent area ratio, interpolated on the common contour. Inlet/outlet
    manifold transitions are added by the flow solver, not this geometry.
    Material boundaries are axial coordinates on that same covered contour.
    They split the mesh before either the gas or coolant nodes are built, so
    the two sides share every material interface without changing the contour.
    """
    from geometry.nozzle import full_hot_gas_geometry

    r_t, r_c = _positive("r_t", r_t), _positive("r_c", r_c)
    L_cyl = _positive("L_cyl", L_cyl)
    eps = _positive("expansion_ratio", expansion_ratio)
    if r_c <= r_t or eps <= 1:
        raise ValueError("chamber radius and nozzle expansion must exceed the throat")
    if int(n_points) != n_points or n_points < 24:
        raise ValueError("n_points must be an integer >= 24")
    if not (0 < bell_fraction <= 1.5 and 0 < conv_half_angle_deg < 80):
        raise ValueError("invalid bell fraction or contraction angle")
    cover = eps if cover_eps == 0 else float(cover_eps)
    if not math.isfinite(cover) or not 1 < cover <= eps:
        raise ValueError("cooling coverage must lie in (1, expansion_ratio]")
    if int(n_channels) != n_channels or n_channels < 1:
        raise ValueError("channel count must be a positive integer")
    for name, val in (("channel_width_m", channel_width_m),
                      ("channel_height_m", channel_height_m),
                      ("wall_thickness_m", wall_thickness_m)):
        _positive(name, val)

    # n_cyl changes with refinement; the former fixed 14 cells prevented a
    # meaningful complete-domain grid-convergence check.
    contour = full_hot_gas_geometry(
        r_t, r_t * math.sqrt(eps), r_c, eps, L_cyl,
        bell_fraction=bell_fraction, theta_conv_deg=conv_half_angle_deg,
        n_points=int(n_points), n_cyl=max(6, int(n_points) // 8),
        nozzle_contour_method=nozzle_contour_method, nozzle_initial_angle_deg=nozzle_initial_angle_deg,
        nozzle_exit_angle_deg=nozzle_exit_angle_deg)
    xs, rs, x_throat = (contour[k] for k in ("contour_x", "contour_r", "x_throat_m"))
    full_exit = xs[-1]
    clipped = []
    r_end = r_t * math.sqrt(cover)
    for x, r in zip(xs, rs):
        if clipped and x > x_throat and r > r_end:
            xa, ra = clipped[-1]
            f = (r_end - ra) / (r - ra)
            clipped.append((xa + f * (x - xa), r_end))
            break
        clipped.append((x, r))
    if len(clipped) < 3:
        raise ValueError("cooling mesh contains too few nodes")
    # Drop exact shared contour vertices, never a finite geometric segment.
    points = [clipped[0]]
    for xy in clipped[1:]:
        if math.hypot(xy[0] - points[-1][0], xy[1] - points[-1][1]) > 1e-14:
            points.append(xy)
    try:
        material_boundaries = sorted(float(x) for x in material_boundaries_m)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("material boundaries must be finite axial coordinates") from exc
    if any(not math.isfinite(x) for x in material_boundaries):
        raise ValueError("material boundaries must be finite axial coordinates")
    if any(a == b for a, b in zip(material_boundaries, material_boundaries[1:])):
        raise ValueError("material boundaries must be unique")
    if any(x < points[0][0] or x > points[-1][0] for x in material_boundaries):
        raise ValueError("material boundaries must lie within the cooled extent")
    point_xs = [x for x, _ in points]
    for boundary in material_boundaries:
        index = bisect_left(point_xs, boundary)
        if index < len(points) and point_xs[index] == boundary:
            continue
        xa, ra = points[index - 1]
        xb, rb = points[index]
        fraction = (boundary - xa) / (xb - xa)
        points.insert(index, (boundary, ra + fraction * (rb - ra)))
        point_xs.insert(index, boundary)
    # Full hot-side mesh retains the exact coverage boundary and the uncooled
    # extension. Gas coordinates always run injector -> nozzle exit.
    full_points = points + [(x, r) for x, r in zip(xs, rs) if x > points[-1][0] + 1e-14]
    if not any(abs(x-x_throat) < 1e-12 and abs(r-r_t) < 1e-12 for x, r in full_points):
        raise ValueError("formal contour did not provide an exact throat boundary")
    full_nodes = []
    gas_s = 0.0
    for i, (x, r) in enumerate(full_points):
        if i:
            gas_s += math.hypot(x-full_points[i-1][0], r-full_points[i-1][1])
        full_nodes.append(dict(x_m=x, r_m=r, s_m=gas_s,
                               area_ratio=(r/r_t)**2, area_m2=math.pi*r*r,
                               cooling_covered=x <= points[-1][0]+1e-12))

    if channel_table:
        from chamber_design import parse_channel_table, resolve_channel_distribution
        rows = parse_channel_table(channel_table) if isinstance(channel_table, str) else channel_table
        if not rows:
            raise ValueError("nonempty channel table produced no geometry")
        for row in rows:
            row.validate()
        # Changing channel count is a manifold/branching problem, not smooth
        # one-dimensional area variation. Do not invent an unrecorded loss.
        if len({row.n for row in rows}) != 1:
            raise ValueError("variable channel count needs explicit branch/manifold losses")
        distribution = resolve_channel_distribution(
            rows, [x for x, _ in points], [r for _, r in points],
            x_throat=x_throat, L_cyl=L_cyl, x_exit=full_exit)
    else:
        distribution = None

    nodes = []
    for i, (x, r) in enumerate(points):
        n = int(distribution["n"][i]) if distribution else int(n_channels)
        w = float(distribution["w_m"][i]) if distribution else channel_width_m * r / r_t
        h = float(distribution["h_m"][i]) if distribution else channel_height_m
        t = float(distribution["t_m"][i]) if distribution else wall_thickness_m
        pitch = 2 * math.pi * r / n
        if not all(math.isfinite(v) and v > 0 for v in (w, h, t)) or w >= pitch:
            raise ValueError(f"invalid channel geometry at x={x:g} m: width must be below pitch")
        nodes.append(dict(x_m=x, r_m=r, A_flow_m2=n*w*h,
                          D_h_m=2*w*h/(w+h), n_channels=n, width_m=w,
                          height_m=h, wall_thickness_m=t,
                          pitch_m=pitch, rib_m=pitch-w, area_ratio=(r/r_t)**2))
    if counterflow:
        nodes.reverse()
    nodes[0]["s_m"] = 0.0
    cells = []
    for a, b in zip(nodes, nodes[1:]):
        ds = math.hypot(b["x_m"]-a["x_m"], b["r_m"]-a["r_m"])
        b["s_m"] = a["s_m"] + ds
        cell = {key: 0.5*(a[key]+b[key]) for key in (
            "x_m", "r_m", "s_m", "A_flow_m2", "D_h_m", "width_m",
            "height_m", "wall_thickness_m", "pitch_m", "rib_m")}
        cell.update(ds_m=ds, dA_hot_m2=math.pi*(a["r_m"]+b["r_m"])*ds,
                    area_ratio=(cell["r_m"]/r_t)**2,
                    n_channels=a["n_channels"], supersonic=cell["x_m"] > x_throat)
        cells.append(cell)
    return dict(version=3, source=contour["source"],
                nozzle_contour_method=contour["nozzle_contour_method"],
                initial_expansion_angle_deg=contour["initial_expansion_angle_deg"],
                exit_angle_deg=contour["exit_angle_deg"], angle_source=contour["angle_source"],
                method_note=contour["method_note"], coordinate_origin="injector_face",
                L_nozzle_m=contour["L_nozzle_m"],
                chamber_blend_radius_m=contour["chamber_blend_radius_m"],
                convergent_tangent_continuity=contour["convergent_tangent_continuity"],
                flow_direction="counterflow" if counterflow else "coflow",
                nodes=nodes, cells=cells, x_throat_m=x_throat,
                r_t_m=r_t, r_c_m=r_c, L_cyl_m=L_cyl,
                L_conv_m=contour["L_conv_m"],
                throat_upstream_radius_m=contour["throat_upstream_radius_m"],
                throat_downstream_radius_m=contour["throat_downstream_radius_m"],
                conv_half_angle_deg=contour["conv_half_angle_deg"],
                bell_fraction=contour["bell_fraction"],
                full_nodes=full_nodes, full_nodes_direction="gas_flow",
                full_exit_x_m=full_exit, coverage_end_x_m=points[-1][0],
                material_boundaries_m=material_boundaries,
                cover_eps=cover, expansion_ratio=eps,
                path_length_m=nodes[-1]["s_m"],
                hot_area_m2=sum(cell["dA_hot_m2"] for cell in cells),
                channel_distribution="table" if distribution else "width_proportional_to_radius")
