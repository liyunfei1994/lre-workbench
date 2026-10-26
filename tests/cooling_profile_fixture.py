"""Small complete common-mesh evidence for assessment control-flow tests."""
def cooling_profile(peak=750.0):
    geometry_nodes = [{"s_m": float(i), "x_m": float(2-i)} for i in range(3)]
    nodes = [dict(point, kind=kind, p_Pa=10e6, T_K=200., rho_kgm3=400., Mach=mach,
                  range_status="within_declared_ranges")
             for point, kind, mach in [(geometry_nodes[0], "inlet_port", 0.),
                                       *[(point, "channel", .1) for point in geometry_nodes],
                                       (geometry_nodes[-1], "outlet_port", 0.)]]
    cells = [dict(kind=kind, dp_friction_Pa=0., dp_momentum_Pa=0., dp_local_Pa=0.,
                  T_wall_hot_K=temperature, wall_residual_K=0.)
             for kind, temperature in [("inlet_manifold", None), ("channel", peak),
                                       ("channel", peak-10), ("outlet_manifold", None)]]
    return {"version": 1, "model_id": "regen_quasi1d_ph_v1", "nodes": nodes, "cells": cells,
            "geometry": {"nodes": geometry_nodes, "cells": [{}, {}]},
            "summary": {"T_wall_hot_max_K": peak}, "validity": {"status": "pass"}}
