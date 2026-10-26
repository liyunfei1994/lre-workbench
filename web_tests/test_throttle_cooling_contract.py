"""Immutable cooling hardware needs complete, independently checked records.

The fixture is an analytic conservation ledger over the production geometry;
its declared property states are synthetic. It is not an engine simulation.
"""
from copy import deepcopy
import json
import math
import unittest

from webapp.throttle import source_baseline
from web_tests.test_throttle_analysis import source_fixture
from chamber_contour import convergent_geometry
from regen_geometry import build_regen_geometry
from wall_thermal import material_for_cell, audit_cell, material_summary
from wall_materials import GRCOP84_DATASET_ID


def physical_source_fixture(*, material_zones=(), cover_eps=0.):
    source = source_fixture()
    c = source["result"]["calculation"]
    v = c["inputs"]
    v.update(cooling_bartz_mode=1., cooling_grid_points=24., cooling_cover_eps=cover_eps,
             wall_material_zones_json=json.dumps(list(material_zones)),
             wall_thermal_dataset_id=GRCOP84_DATASET_ID)
    boundaries = sorted({z[key] for z in material_zones for key in ("x_start_m", "x_end_m")})
    rt = math.sqrt(.05/math.pi)
    rc = rt*math.sqrt(v["chamber_contraction_ratio"])
    convergent = convergent_geometry(rc,rt,v["chamber_conv_half_angle_deg"])
    length = (v["chamber_l_star_m"]*.05-convergent["V_conv_m3"])/(math.pi*rc*rc)
    geometry = build_regen_geometry(r_t=rt,r_c=rc,L_cyl=length,
        expansion_ratio=v["expansion_ratio"],n_points=24,
        n_channels=v["cooling_n_channels"],channel_width_m=v["cooling_channel_width_m"],
        channel_height_m=v["cooling_channel_height_m"],wall_thickness_m=v["cooling_wall_thickness_m"],
        cover_eps=cover_eps, material_boundaries_m=boundaries)
    geom_nodes = [dict(geometry["nodes"][0],A_flow_m2=None),*geometry["nodes"],
                  dict(geometry["nodes"][-1],A_flow_m2=None)]
    geom_cells = [dict(kind="inlet_manifold"),
                  *(dict(cell,kind="channel") for cell in geometry["cells"]),dict(kind="outlet_manifold")]
    nodes, cells = [], []
    heat = 600./len(geometry["cells"])
    p, h0 = 70e6, -750.
    for index, geom in enumerate(geom_nodes):
        u = 2./(500.*geom["A_flow_m2"]) if geom["A_flow_m2"] else 0.
        if index:
            cell = deepcopy(geom_cells[index-1])
            previous = nodes[-1]
            ga = 2./previous["A_flow_m2"] if previous["A_flow_m2"] else 0.
            gb = 2./geom["A_flow_m2"] if geom["A_flow_m2"] else 0.
            momentum = .5*(ga+gb)*(u-previous["velocity_ms"])
            friction = 1e4 if cell["kind"] == "channel" else 0.
            local = 1000. if cell["kind"] != "channel" else 0.
            q = heat if cell["kind"] == "channel" else 0.
            p -= momentum+friction+local
            h0 += q/2.
            cell.update(Q_W=q,dp_friction_Pa=friction,dp_local_Pa=local,dp_momentum_Pa=momentum,
                energy_residual_W=0.,momentum_residual_Pa=0.,wall_residual_K=0.)
            cells.append(cell)
        nodes.append(dict(geom,p_Pa=p,T_K=120.+index,rho_kgm3=500.,cp_JkgK=2400.,
            velocity_ms=u,Mach=u/1000.,h0_Jkg=h0,h_Jkg=h0-u*u/2,
            phase="liquid",range_status="within_declared_ranges"))
    for node, key in ((nodes[0],"fuel_after_pump"),(nodes[-1],"fuel_after_cooling")):
        c["states"][key].update(pressure=node["p_Pa"],temperature=node["T_K"],enthalpy=node["h_Jkg"])
    residuals = dict(pressure_residual_Pa=0.,hot_side_temperature_residual_K=0.,geometry_relative_residual=0.,
        heat_relative_residual=0.,hot_side_relative_residual=0.,cell_heat_relative_residual=0.)
    coupling = dict(status="pass",iterations=2,pressure_tolerance_Pa=.5,temperature_tolerance_K=.001,
        geometry_relative_tolerance=2e-8,heat_relative_tolerance=2e-8,hot_side_relative_tolerance=5e-7,
        cell_heat_relative_tolerance=5e-7,**residuals,history=[dict(iteration=1,**residuals),dict(iteration=2,**residuals)])
    hot_cells = [dict(x_m=c["x_m"],T_static_K=3000.,T_aw_K=3200.,T0_K=3500.,
                     p_Pa=3e6,M=.5,cp_JkgK=2000.,mu_Pas=.0001,Pr=.7,gamma=1.2)
                 for c in geometry["cells"]]
    for cold, state in zip(cells[1:-1],hot_cells):
        cold["hot_side"] = deepcopy(state)
        # Synthetic wall states support the material identity gate only; this
        # fixture remains an analytic ledger, not a heat-transfer solution.
        cold.update(T_wall_hot_K=600., T_wall_cold_K=500., fin_reference_temperature_K=400.,
                    wall_conductivity_WmK=320.)
        dataset, declared = material_for_cell(cold["x_m"], v["wall_thermal_dataset_id"],
                                              v["wall_manufacturing_state"], list(material_zones))
        cold["material"] = audit_cell(cold, dataset, declared)
    hot = dict(model_id="heated_frozen_quasi1d_v1",nodes=[{}, {}, {}],
        cells=sorted(hot_cells,key=lambda c:c["x_m"]),max_scaled_residual=0.,heat_loss_W=600.,
        critical_x_m=geometry["x_throat_m"]-.001,critical_regularity_residual_per_m=0.,
        critical_launch_relative_difference=0.,momentum_cell_residuals_Pa=[0.,0.],
        momentum_integral_tolerance_Pa=max(.05,v["pc"]*2e-9),max_momentum_integral_residual_Pa=0.,
        critical_location_model="conservative_pchip_heat_and_log_area_regularity")
    profile = dict(version=1,model_id="regen_quasi1d_ph_v1",geometry=geometry,flow_direction="counterflow",
        gas_heat_transfer=dict(geometry_model_id="bartz_upstream_contour_curvature_v2",
            curvature_source="geometry.throat_upstream_radius_m",throat_diameter_m=2*rt,
            curvature_radius_m=geometry["throat_upstream_radius_m"],
            curvature_factor=(2*rt/geometry["throat_upstream_radius_m"])**.1),
        nodes=nodes,cells=cells,summary=dict(Q_total_W=600.,dp_total_Pa=nodes[0]["p_Pa"]-nodes[-1]["p_Pa"]),
        balance=dict(energy_residual_W=0.,pressure_sum_residual_Pa=0.,max_cell_energy_residual_W=0.,
                     max_cell_momentum_residual_Pa=0.,max_wall_residual_K=0.,mass_residual_kg_s=0.),
        hot_side_coupling=coupling,hot_side_profile=deepcopy(hot),
        material_applicability=material_summary(cells[1:-1], v["cooling_wall_temp_limit_K"]),
        material_assignment=dict(default_dataset_id=v["wall_thermal_dataset_id"],
            default_manufacturing_state=v["wall_manufacturing_state"], zones=list(material_zones),
            coordinate="axial_x_from_injector_m"))
    c["components"]["cooling_channel"].update(profile=profile)
    c["components"]["cooling_channel"]["outputs"].update(A_throat_m2=.05,channel_height_mm=5.)
    c["components"]["nozzle"].update(profile=hot)
    c["components"]["nozzle"]["outputs"].update(heat_loss_W=600.)
    c["components"]["main_chamber"]["outputs"]["heat_loss_W"] = 0.
    c["states"]["main_chamber"]["enthalpy"] = -75.
    return source


class ThrottleCoolingContractTests(unittest.TestCase):
    def test_checked_spatial_record_retains_geometry_without_certifying_accuracy(self):
        source = physical_source_fixture()
        before = deepcopy(source)
        values, baseline = source_baseline(source)
        hardware = baseline["cooling_hardware"]
        self.assertEqual(hardware["mode"],"regen_quasi1d_ph_v1")
        self.assertEqual(hardware["status"],"saved_model_balances_checked")
        self.assertEqual(hardware["validation_status"],"requires_independent_validation")
        self.assertIn("凝结",hardware["limitations"][0])
        self.assertEqual(source,before)
        hardware["geometry"]["nodes"][0]["height_m"] = 999.
        self.assertEqual(source,before)

    def test_old_physical_records_must_be_recalculated_but_budget_remains_honest(self):
        source = source_fixture()
        baseline = source_baseline(source)[1]
        self.assertEqual(baseline["cooling_hardware"]["validation_status"],"no_fixed_hardware_cooling_prediction")
        source["result"]["calculation"]["inputs"]["cooling_bartz_mode"] = 1.
        with self.assertRaisesRegex(ValueError,"旧模型.*重新计算"):
            source_baseline(source)

    def test_saved_pass_cannot_mask_missing_or_inconsistent_cooling_evidence(self):
        def profile(c):
            return c["components"]["cooling_channel"]["profile"]
        mutations = [
            lambda c: profile(c).pop("gas_heat_transfer"),
            lambda c: profile(c)["gas_heat_transfer"].update(curvature_radius_m=.35*2*profile(c)["geometry"]["r_t_m"]),
            lambda c: profile(c)["gas_heat_transfer"].update(curvature_factor=1.1),
            lambda c: profile(c)["geometry"].update(version=1),
            lambda c: profile(c).pop("geometry"),
            lambda c: profile(c)["geometry"].update(cover_eps=2),
            lambda c: c["inputs"].update(cooling_channel_width_m=.0016),
            lambda c: profile(c)["nodes"].pop(),
            lambda c: profile(c)["nodes"][2].update(phase="twophase"),
            lambda c: profile(c)["nodes"][2].update(range_status="extrapolated"),
            lambda c: profile(c)["cells"][2].update(Q_W=999.),
            lambda c: profile(c)["cells"][2].update(dp_friction_Pa=999.),
            lambda c: profile(c)["balance"].update(energy_residual_W=999.),
            lambda c: profile(c).pop("hot_side_coupling"),
            lambda c: profile(c)["hot_side_coupling"].pop("history"),
            lambda c: profile(c)["hot_side_coupling"].update(heat_relative_residual=.01),
            lambda c: profile(c)["hot_side_coupling"].update(heat_relative_tolerance=.01),
            lambda c: profile(c)["hot_side_coupling"].update(pressure_residual_Pa=float("nan")),
            lambda c: profile(c)["cells"][2]["hot_side"].update(T_aw_K=999.),
            lambda c: profile(c)["hot_side_profile"].update(heat_loss_W=500.),
            lambda c: c["components"]["nozzle"]["profile"].update(critical_x_m=999.),
            lambda c: profile(c)["hot_side_profile"].pop("critical_location_model"),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                source = physical_source_fixture()
                mutation(source["result"]["calculation"])
                with self.assertRaisesRegex(ValueError,"重新计算"):
                    source_baseline(source)

    def test_gg_physical_source_needs_same_cooling_evidence(self):
        source = physical_source_fixture()
        c = source["result"]["calculation"]
        c["inputs"]["cycle_type"] = "gg"
        c.pop("steady_closure")
        self.assertEqual(source_baseline(source)[1]["cooling_hardware"]["mode"],"regen_quasi1d_ph_v1")
        c["components"]["cooling_channel"]["profile"].pop("hot_side_coupling")
        with self.assertRaisesRegex(ValueError,"热侧耦合"):
            source_baseline(source)
