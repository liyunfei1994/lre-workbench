from copy import deepcopy
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from webapp.chamber_design import chamber_design_view
from web_tests.test_nozzle_geometry import fixture as geometry_fixture


def fixture():
    result = geometry_fixture()
    cal = result["calculation"]
    cal["inputs"].update(combustion_loss_model="adiabatic", combustion_efficiency=.91,
                         chamber_inner_wall_material="grcop84", wall_thermal_dataset_id="saved-dataset")
    metadata = dict(composition_complete=True, composition_basis="mass_fraction",
                    model={"enthalpy_reference_id":"saved-test-reference"},
                    species_molar_masses_kg_kmol={"H2O":18.01528},
                    element_mass_fractions={"H":.1119,"O":.8881},
                    state_basis={"kind":"reservoir_approximation"})
    cal["states"] = {
        "ox_turbine_exhaust":dict(pressure=12e6,temperature=700,mass_flow=100,enthalpy=100,
                                  density=40,composition={"H2O":1.},metadata=deepcopy(metadata)),
        "fuel_turbine_exhaust":dict(pressure=11e6,temperature=800,mass_flow=100,enthalpy=200,
                                    density=35,composition={"H2O":1.},metadata=deepcopy(metadata)),
        "main_chamber":dict(pressure=10e6,temperature=3000,mass_flow=200,enthalpy=150,
                            density=7,composition={"H2O":1.},metadata=deepcopy(metadata)),
    }
    cal["components"]["main_chamber"] = {"outputs":dict(T_chamber_K=3000,T_chamber_ideal_K=3000,
        combustion_efficiency=1.,cp_Jkgk=2200,gamma=1.2,molar_mass_kg_per_kmol=18.01528,
        heat_loss_W=0.,combustion_loss_W=0.,cc_diameter_m=.3,cc_length_m=.3,
        cc_l_star_m=cal["inputs"]["chamber_l_star_m"])}
    nozzle = cal["components"]["nozzle"]["outputs"]
    nozzle["c_star_ms"] = 10e6*nozzle["At_m2"]/200
    geom = cal["components"]["cooling_channel"]["profile"]["geometry"]
    xt = geom["x_throat_m"]
    cell = dict(kind="channel",x_m=xt/2,q_W_m2=100.,Q_W=5.,T_wall_hot_K=800.,T_wall_cold_K=700.,
                T_coolant_K=300.,p_coolant_Pa=15e6,
                hot_side=dict(T_static_K=2900.,p_Pa=9.5e6,M=.25,T_aw_K=2950.),
                material=dict(material_id="grcop84",dataset_id="saved-dataset",within_range=True,
                              status="within_range",x_m=xt/2))
    cool = cal["components"]["cooling_channel"]
    cool["outputs"] = dict(A_throat_m2=nozzle["At_m2"],heat_load_chamber_W=5.,heat_load_W=15.)
    cool["profile"].update(model_id="regen_quasi1d_ph_v1", cells=[cell,
        dict(kind="channel",x_m=xt+.1,q_W_m2=10000.,Q_W=10.,T_wall_hot_K=5000.,T_wall_cold_K=4000.),
        dict(kind="inlet_manifold",x_m=0,q_W_m2=1e9,T_wall_hot_K=1e9)],
        material_assignment={"default_dataset_id":"saved-dataset","zones":[]})
    return result


class ChamberDesignTests(unittest.TestCase):
    def test_immutable_record_only_and_explicit_model_scope(self):
        result = fixture()
        result["submitted_inputs"] = dict(pc=1,chamber_inner_wall_material="unrelated")
        before = deepcopy(result)
        view = chamber_design_view(result,"saved-record")
        self.assertTrue(view["available"],view)
        self.assertEqual(view["source"]["record_id"],"saved-record")
        self.assertEqual(view["status"],"review")
        self.assertEqual(result,before)
        metrics = {m["key"]:m for m in view["metrics"]}
        self.assertEqual(metrics["pressure_Pa"]["value"],10e6)
        self.assertIsNone(view["model"]["metrics"][0]["value"])
        self.assertEqual(view["model"]["id"],"hp_equilibrium")
        self.assertEqual(next(s["status"] for s in view["scope"] if s["key"]=="kinetics"),"not_modelled")
        self.assertEqual(view["balances"]["status"],"incomplete",view["checks"])
        self.assertEqual(next(c["status"] for c in view["checks"] if c["key"]=="mass_balance"),"pass")
        self.assertEqual(next(c["status"] for c in view["checks"] if c["key"]=="model_mechanism_sha256"),"missing")
        json.dumps(view,allow_nan=False)

    def test_complete_species_and_mole_fractions_from_saved_weights(self):
        view = chamber_design_view(fixture())
        comp = view["composition"]
        self.assertEqual(comp["status"],"saved")
        self.assertEqual(comp["mole_fraction_status"],"derived")
        self.assertEqual(comp["rows"][0]["mole_fraction"],1.)
        r = fixture()
        for state in r["calculation"]["states"].values():
            state["metadata"].pop("species_molar_masses_kg_kmol")
        view = chamber_design_view(r)
        self.assertEqual(view["composition"]["mole_fraction_status"],"missing")
        self.assertIsNone(view["composition"]["rows"][0]["mole_fraction"])

    def test_bad_fractions_are_not_normalized_or_declared_valid(self):
        for comp in ({"H2O":.5},{"H2O":-1.},{"H2O":float("nan")},{"H2O":True}):
            r = fixture();r["calculation"]["states"]["main_chamber"]["composition"] = comp
            view = chamber_design_view(r)
            self.assertEqual(view["composition"]["status"],"invalid")
            self.assertEqual(view["status"],"inconsistent")
            self.assertEqual(view["composition"]["mole_fraction_status"],"missing")
            json.dumps(view,allow_nan=False)

    def test_nonmass_or_missing_basis_preserves_raw_values_without_conversion(self):
        for basis in ("mole_fraction", None):
            r=fixture();meta=r["calculation"]["states"]["main_chamber"]["metadata"]
            if basis is None:
                meta.pop("composition_basis")
            else:
                meta["composition_basis"]=basis
            before=deepcopy(r);v=chamber_design_view(r);comp=v["composition"]
            self.assertEqual(comp["status"],"missing" if basis is None else "invalid")
            self.assertEqual(comp["basis"],basis)
            self.assertEqual(comp["rows"][0]["raw_fraction"],1.)
            self.assertIsNone(comp["rows"][0]["mass_fraction"])
            self.assertIsNone(comp["rows"][0]["mole_fraction"])
            self.assertEqual(comp["element_mass_fractions"],{})
            self.assertEqual(next(c["status"] for c in v["checks"] if c["key"]=="element_inventory"),"missing")
            self.assertEqual(r,before)

    def test_mass_element_reference_and_energy_inconsistency_are_independent(self):
        mutations = [
            ("mass_balance",lambda s:s["main_chamber"].update(mass_flow=201)),
            ("enthalpy_balance",lambda s:s["main_chamber"].update(enthalpy=151)),
            ("enthalpy_reference",lambda s:s["main_chamber"]["metadata"]["model"].update(enthalpy_reference_id="different")),
            ("element_H",lambda s:s["main_chamber"]["metadata"].update(element_mass_fractions={"H":.2,"O":.8})),
        ]
        for key,mutate in mutations:
            r=fixture();mutate(r["calculation"]["states"])
            v=chamber_design_view(r)
            with self.subTest(key=key):
                self.assertEqual(next(c["status"] for c in v["checks"] if c["key"]==key),"fail")
                self.assertEqual(v["status"],"inconsistent")

    def test_missing_budget_is_not_implicitly_zero(self):
        r=fixture();r["calculation"]["components"]["main_chamber"]["outputs"].pop("heat_loss_W")
        v=chamber_design_view(r)
        self.assertEqual(v["balances"]["status"],"incomplete")
        self.assertEqual(next(c["status"] for c in v["checks"] if c["key"]=="enthalpy_balance"),"missing")
        self.assertIsNone(next(m["value"] for m in v["balances"]["metrics"] if m["key"]=="heat_loss_W"))

    def test_effective_cstar_not_fabricated_theory_or_efficiency(self):
        v=chamber_design_view(fixture())
        p={m["key"]:m["value"] for m in v["performance"]["metrics"]}
        self.assertAlmostEqual(p["c_star_effective_ms"],p["c_star_identity_ms"])
        self.assertIsNone(p["c_star_theoretical_ms"]);self.assertIsNone(p["eta_cstar"])
        r=fixture();r["calculation"]["components"]["nozzle"]["outputs"]["c_star_ms"]*=2
        self.assertEqual(chamber_design_view(r)["status"],"inconsistent")

    def test_geometry_is_original_chamber_slice_and_export_needs_full_consistency(self):
        r=fixture();v=chamber_design_view(r);g=v["geometry"]
        raw=r["calculation"]["components"]["cooling_channel"]["profile"]["geometry"]
        self.assertTrue(g["export_allowed"],g.get("consistency"))
        expected=[n for n in raw["full_nodes"] if n["x_m"]<=raw["x_throat_m"]+1e-10]
        self.assertEqual(g["contour"]["x_m"],[n["x_m"] for n in expected])
        self.assertEqual(g["contour"]["r_m"],[n["r_m"] for n in expected])
        self.assertAlmostEqual(g["contour"]["x_m"][-1],raw["x_throat_m"])
        r["calculation"]["components"]["main_chamber"]["outputs"].pop("cc_length_m")
        self.assertFalse(chamber_design_view(r)["geometry"]["export_allowed"])
        r["calculation"]["components"]["main_chamber"]["outputs"]["cc_length_m"]=1.
        self.assertFalse(chamber_design_view(r)["geometry"]["export_allowed"])
        r=fixture();r["calculation"]["components"]["main_chamber"]["outputs"]["cc_volume_m3"]=1.
        self.assertFalse(chamber_design_view(r)["geometry"]["export_allowed"])

    def test_no_saved_contour_does_not_rebuild_history(self):
        r=fixture();r["calculation"]["components"]["cooling_channel"]["profile"].pop("geometry")
        with patch("webapp.chamber_design.nozzle_geometry_view",side_effect=AssertionError("must not reconstruct")):
            v=chamber_design_view(r)
        self.assertFalse(v["geometry"]["available"])
        self.assertFalse(v["geometry"]["export_allowed"])
        self.assertFalse(v["thermal"]["nodes"])

    def test_main_chamber_slice_retains_throat_within_saved_coordinate_tolerance(self):
        for shift in (-5e-10,5e-10):
            r=fixture();raw=r["calculation"]["components"]["cooling_channel"]["profile"]["geometry"]
            throat=raw["x_throat_m"]
            expected=[n for n in raw["full_nodes"] if n["x_m"]<=throat+1e-10]
            raw["x_throat_m"]+=shift
            before=deepcopy(r);g=chamber_design_view(r)["geometry"]
            self.assertTrue(g["export_allowed"],g)
            self.assertEqual(g["contour"]["x_m"],[n["x_m"] for n in expected])
            self.assertEqual(g["contour"]["r_m"],[n["r_m"] for n in expected])
            self.assertEqual(r,before)

    def test_saved_manufacturing_and_gas_range_evidence_are_not_hardware_qualification(self):
        for state,match,word in (("unspecified","unknown","未确认"),("different-process","mismatch","不匹配")):
            r=fixture();material=r["calculation"]["components"]["cooling_channel"]["profile"]["cells"][0]["material"]
            material.update(manufacturing_state_id="dataset-process",declared_manufacturing_state=state,manufacturing_match=match)
            evidence=dict(status="extrapolated",temperature_K=3000,valid_temperature_min_K=200,
                          valid_temperature_max_K=2000,outside_mass_fraction=.01,species_outside=[])
            r["calculation"]["states"]["main_chamber"]["metadata"]["temperature_range_evidence"]=evidence
            before=deepcopy(r);v=chamber_design_view(r)
            self.assertTrue(any(word in issue for issue in v["materials"]["issues"]))
            self.assertEqual(v["materials"]["evidence"][0]["declared_manufacturing_state"],state)
            self.assertEqual(v["model"]["temperature_range_evidence"],evidence)
            self.assertEqual(r,before)

    def test_chamber_thermal_excludes_nozzle_and_manifold_and_audits_material(self):
        r=fixture();v=chamber_design_view(r)
        self.assertEqual(len(v["thermal"]["nodes"]),1)
        self.assertEqual(v["thermal"]["nodes"][0]["T_static_K"],2900.)
        m={m["key"]:m["value"] for m in v["thermal"]["metrics"]}
        self.assertEqual(m["chamber_peak_heat_flux_W_m2"],100.)
        self.assertEqual(m["chamber_peak_wall_temperature_K"],800.)
        cell=r["calculation"]["components"]["cooling_channel"]["profile"]["cells"][0]
        cell["material"].update(material_id="grcop42",within_range=False,status="out_of_range")
        v=chamber_design_view(r)
        self.assertEqual(v["materials"]["status"],"inconsistent")
        self.assertEqual(v["status"],"inconsistent")
        self.assertEqual(len(v["materials"]["issues"]),3)
        self.assertTrue(any("制造工艺未确认" in issue for issue in v["materials"]["issues"]))
        self.assertEqual(v["thermal"]["nodes"][0]["T_wall_hot_K"],800.)

    def test_invalid_thermal_coordinates_and_duplicate_nodes_block_curve(self):
        for bad in (float("nan"),-1.,1000.):
            r=fixture();r["calculation"]["components"]["cooling_channel"]["profile"]["cells"][0]["x_m"]=bad
            v=chamber_design_view(r)
            self.assertEqual(v["thermal"]["status"],"invalid")
            self.assertFalse(v["thermal"]["nodes"])
            self.assertEqual(v["status"],"inconsistent")
            json.dumps(v,allow_nan=False)
        r=fixture();cells=r["calculation"]["components"]["cooling_channel"]["profile"]["cells"]
        cells.append(deepcopy(cells[0]))
        self.assertEqual(chamber_design_view(r)["thermal"]["status"],"invalid")

    def test_mismatched_heat_sum_and_known_model_identity_are_not_green(self):
        r=fixture();r["calculation"]["components"]["cooling_channel"]["outputs"]["heat_load_chamber_W"]=100.
        v=chamber_design_view(r)
        self.assertEqual(next(c["status"] for c in v["checks"] if c["key"]=="chamber_heat_sum"),"fail")
        self.assertEqual(v["status"],"inconsistent")
        r=fixture()
        r["calculation"]["states"]["main_chamber"]["metadata"]["model"]["eos"]="different"
        r["calculation"]["states"]["ox_turbine_exhaust"]["metadata"]["model"]["eos"]="ideal-gas"
        v=chamber_design_view(r)
        self.assertEqual(next(c["status"] for c in v["checks"] if c["key"]=="model_eos"),"fail")

    def test_nozzle_zone_does_not_hide_default_chamber_material_mismatch(self):
        r=fixture();profile=r["calculation"]["components"]["cooling_channel"]["profile"]
        xt=profile["geometry"]["x_throat_m"]
        profile["material_assignment"].update(zones=[dict(x_start_m=xt,x_end_m=xt+.2,dataset_id="nozzle-only")])
        profile["cells"][0]["material"]["material_id"]="grcop42"
        v=chamber_design_view(r)
        self.assertEqual(v["materials"]["status"],"inconsistent")
        self.assertEqual(v["materials"]["checks"][0]["assignment_scope"],"default")
        self.assertEqual(v["materials"]["checks"][0]["status"],"fail")

    def test_explicit_zone_is_audited_against_its_recorded_material(self):
        r=fixture();profile=r["calculation"]["components"]["cooling_channel"]["profile"]
        xt=profile["geometry"]["x_throat_m"]
        zone=dict(x_start_m=0.,x_end_m=xt,dataset_id="zone42")
        profile["material_assignment"].update(default_material_id="grcop84",zones=[zone],
            zone_material_ids=[zone|dict(material_id="grcop42")])
        profile["cells"][0]["material"].update(material_id="grcop42",dataset_id="zone42")
        v=chamber_design_view(r)
        self.assertEqual(v["materials"]["checks"][0]["status"],"pass")
        self.assertEqual(v["materials"]["checks"][0]["assignment_scope"],"zone")
        self.assertNotEqual(v["materials"]["status"],"inconsistent")
        profile["cells"][0]["material"]["material_id"]="grcop84"
        self.assertEqual(chamber_design_view(r)["materials"]["checks"][0]["status"],"fail")

    def test_global_default_identity_conflict_survives_matching_zone(self):
        r=fixture();profile=r["calculation"]["components"]["cooling_channel"]["profile"]
        xt=profile["geometry"]["x_throat_m"]
        zone=dict(x_start_m=0.,x_end_m=xt,dataset_id="zone84")
        profile["material_assignment"].update(default_material_id="grcop42",zones=[zone],
            zone_material_ids=[zone|dict(material_id="grcop84")])
        profile["cells"][0]["material"]["dataset_id"]="zone84"
        v=chamber_design_view(r)
        self.assertEqual(v["materials"]["checks"][0]["status"],"pass")
        self.assertEqual(v["materials"]["status"],"inconsistent")
        self.assertTrue(any("默认" in message for message in v["materials"]["issues"]))
        profile["material_assignment"]["default_material_id"]="grcop84"
        profile["material_assignment"]["default_dataset_id"]="wrong-default-dataset"
        self.assertEqual(chamber_design_view(r)["materials"]["status"],"inconsistent")

    def test_legacy_zone_without_material_identity_is_unknown_not_matched(self):
        r=fixture();profile=r["calculation"]["components"]["cooling_channel"]["profile"]
        xt=profile["geometry"]["x_throat_m"]
        zone=dict(x_start_m=0.,x_end_m=xt,dataset_id="old-zone")
        profile["material_assignment"]["zones"]=[zone]
        profile["cells"][0]["material"]["dataset_id"]="old-zone"
        v=chamber_design_view(r)
        self.assertEqual(v["materials"]["checks"][0]["status"],"missing")
        self.assertEqual(v["materials"]["unknown_assignment_cells"],1)
        self.assertEqual(v["materials"]["status"],"review")
        profile["material_applicability"]={"datasets":[dict(dataset_id="old-zone",material_id="grcop84")]}
        self.assertEqual(chamber_design_view(r)["materials"]["checks"][0]["status"],"pass")

    def test_cp_gamma_definitions_are_never_inferred_for_old_records(self):
        r=fixture();v=chamber_design_view(r)
        fields={field["key"]:field for field in v["metrics"]}
        self.assertIn("定义未记录",fields["cp_Jkgk"]["label"])
        self.assertIn("定义未记录",fields["gamma"]["label"])
        r["calculation"]["states"]["main_chamber"]["metadata"]["property_definitions"]={
            "cp":"dh_dT_at_constant_p_and_composition","gamma":"cp_over_cv_frozen"}
        fields={field["key"]:field for field in chamber_design_view(r)["metrics"]}
        self.assertIn("冻结",fields["cp_Jkgk"]["label"])
        self.assertIn("冻结",fields["gamma"]["label"])

    def test_partial_thermal_samples_report_missing_count_and_sample_maximum(self):
        r=fixture();cool=r["calculation"]["components"]["cooling_channel"];profile=cool["profile"]
        xt=profile["geometry"]["x_throat_m"];base=deepcopy(profile["cells"][0])
        profile["cells"]=[deepcopy(base) for _ in range(3)]
        for i,cell in enumerate(profile["cells"]):cell["x_m"]=xt*(i+1)/4
        profile["cells"][1].pop("T_wall_hot_K")
        profile["cells"][1].pop("q_W_m2")
        cool["outputs"]["heat_load_chamber_W"]=15.
        v=chamber_design_view(r);thermal=v["thermal"]
        self.assertEqual(thermal["status"],"partial")
        self.assertEqual(thermal["sample_count"],3)
        self.assertEqual(thermal["missing_sample_counts"]["T_wall_hot_K"],1)
        self.assertIsNone(thermal["nodes"][1]["T_wall_hot_K"])
        metric=next(m for m in thermal["metrics"] if m["key"]=="chamber_peak_wall_temperature_K")
        self.assertEqual(metric["value"],800.)
        self.assertIn("样本",metric["label"])
        self.assertIn("1 个缺少",metric["note"])
        self.assertEqual(len(thermal["nodes"]),3)

    def test_nonfinite_hot_side_and_coolant_values_block_curves(self):
        for key in ("T_static_K","p_Pa","M","T_aw_K"):
            r=fixture();r["calculation"]["components"]["cooling_channel"]["profile"]["cells"][0]["hot_side"][key]=float("inf")
            v=chamber_design_view(r)
            self.assertEqual(v["thermal"]["status"],"invalid",key)
            self.assertFalse(v["thermal"]["nodes"])
            json.dumps(v,allow_nan=False)
        r=fixture();r["calculation"]["components"]["cooling_channel"]["profile"]["cells"][0]["p_coolant_Pa"]=True
        self.assertEqual(chamber_design_view(r)["thermal"]["status"],"invalid")
        for key in ("heat_load_chamber_W","heat_load_W"):
            r=fixture();r["calculation"]["components"]["cooling_channel"]["outputs"][key]=float("nan")
            self.assertEqual(chamber_design_view(r)["thermal"]["status"],"invalid")

    def test_legacy_mode_is_named_and_nonfinite_states_are_not_green(self):
        r=fixture();r["calculation"]["inputs"]["combustion_loss_model"]="legacy_temperature_rise"
        r["calculation"]["components"]["main_chamber"]["outputs"]["combustion_efficiency"]=.91
        self.assertEqual(chamber_design_view(r)["model"]["metrics"][0]["value"],.91)
        r["calculation"]["states"]["main_chamber"]["pressure"]=float("inf")
        v=chamber_design_view(r)
        self.assertEqual(v["status"],"inconsistent")
        json.dumps(v,allow_nan=False)

    def test_foreign_and_malformed_results_fail_without_crashing(self):
        self.assertFalse(chamber_design_view(None)["available"])
        self.assertFalse(chamber_design_view({"calculation":[]})["available"])
        r=fixture();r["calculation"]["inputs"]["cycle_type"]="gg"
        self.assertFalse(chamber_design_view(r)["available"])
        r=fixture();r["calculation"]["components"]["main_chamber"] = []
        r["calculation"]["states"]["main_chamber"] = []
        self.assertFalse(chamber_design_view(r)["available"])


class ChamberDesignAPITests(unittest.TestCase):
    def test_authenticated_read_only_job_view_and_registered_assets(self):
        from webapp.app import create_app
        from webapp.solver import presets
        from web_tests.test_workspace import client, TEST_PASSWORD
        with tempfile.TemporaryDirectory() as temporary:
            app = create_app(Path(temporary)/"workspace.sqlite3",queue_enabled=False)
            with client(app) as api:
                self.assertEqual(api.get("/api/jobs/unknown").status_code,401)
                api.post("/api/auth/setup",json=dict(username="admin",display_name="测试",password=TEST_PASSWORD))
                project = api.post("/api/projects",json=dict(name="主室分析",inputs=presets()[0]["inputs"])).json()["project"]
                job = api.post(f"/api/projects/{project['id']}/jobs",json={"revision":1}).json()["id"]
                self.assertFalse(api.get("/api/jobs/"+job).json()["chamber_design"]["available"])
                raw = json.dumps(fixture(),ensure_ascii=False)
                app.state.store.execute("UPDATE jobs SET status='succeeded',result=? WHERE id=?",(raw,job))
                job_inputs = app.state.store.one("SELECT inputs FROM jobs WHERE id=?",(job,))["inputs"]
                project_inputs = app.state.store.one("SELECT inputs FROM projects WHERE id=?",(project["id"],))["inputs"]
                with patch("lre_workbench.properties.thermochemistry.chamber_equilibrium",side_effect=AssertionError("GET must not solve chemistry")):
                    response = api.get("/api/jobs/"+job)
                    self.assertEqual(response.status_code,200,response.text)
                    view = response.json()["chamber_design"]
                    self.assertTrue(view["available"])
                    self.assertEqual(view["source"]["record_id"],job)
                    self.assertEqual(view["inlets"][0]["id"],"ox_turbine_exhaust")
                    self.assertEqual(api.get("/api/jobs/"+job).json()["chamber_design"],view)
                self.assertEqual(app.state.store.one("SELECT result FROM jobs WHERE id=?",(job,))["result"],raw)
                self.assertEqual(app.state.store.one("SELECT inputs FROM jobs WHERE id=?",(job,))["inputs"],job_inputs)
                self.assertEqual(app.state.store.one("SELECT inputs FROM projects WHERE id=?",(project["id"],))["inputs"],project_inputs)
                self.assertEqual(api.get("/api/jobs/unknown").status_code,404)
                for asset,content_type in (("chamber-design.js","javascript"),("chamber-design.css","text/css")):
                    asset_response = api.get("/static/"+asset)
                    self.assertEqual(asset_response.status_code,200)
                    self.assertIn(content_type,asset_response.headers["content-type"])
                    self.assertTrue(asset_response.text.strip())
                with client(app) as anonymous:
                    self.assertEqual(anonymous.get("/api/jobs/"+job).status_code,401)

    def test_archived_foreign_cycle_is_returned_verbatim_without_chamber_derivation(self):
        from webapp.app import create_app
        from webapp.solver import presets
        from web_tests.test_workspace import client, TEST_PASSWORD
        with tempfile.TemporaryDirectory() as temporary:
            app=create_app(Path(temporary)/"workspace.sqlite3",queue_enabled=False)
            with client(app) as api:
                api.post("/api/auth/setup",json=dict(username="admin",display_name="测试",password=TEST_PASSWORD))
                project=api.post("/api/projects",json=dict(name="历史记录",inputs=presets()[0]["inputs"])).json()["project"]
                job=api.post(f"/api/projects/{project['id']}/jobs",json={"revision":1}).json()["id"]
                result=fixture();result["calculation"]["inputs"]["cycle_type"]="gg"
                raw=json.dumps(result,ensure_ascii=False)
                app.state.store.execute("UPDATE jobs SET status='succeeded',result=? WHERE id=?",(raw,job))
                response=api.get("/api/jobs/"+job)
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(response.json()["result"],result)
                self.assertIn("scope_issue",response.json())
                self.assertNotIn("chamber_design",response.json())
                self.assertEqual(app.state.store.one("SELECT result FROM jobs WHERE id=?",(job,))["result"],raw)


if __name__ == "__main__":
    unittest.main()
