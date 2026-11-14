"""Property inspection must preserve source identity, units and solver state."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import cantera as ct
from CoolProp.CoolProp import PropsSI
from lre_workbench.properties.library import gas_dataset_details, property_catalog, sample_property_curve
from lre_workbench.properties import thermochemistry as thermo
from lre_workbench.properties.applicability import build_thermodynamic_applicability
from lre_workbench.domain.models import EngineInputs, FlowState, CycleResults


class PropertyLibraryTests(unittest.TestCase):
    def test_catalog_preserves_file_identity_species_notes_and_separate_versions(self):
        catalog = property_catalog()
        self.assertEqual(catalog["scope"], "installed_reference_data")
        for data in catalog["gas_datasets"]:
            raw = (Path(ct.__file__).parent / "data" / data["file"]).read_bytes()
            gas = ct.Solution(yaml=raw.decode(), name=data["phase"])
            self.assertEqual(data["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(data["cantera_version"], ct.__version__)
            self.assertEqual(data["generator_version"], gas.input_header["cantera-version"])
            self.assertEqual(len(data["species"]), gas.n_species)
            for source, saved in zip(gas.species(), data["species"]):
                self.assertEqual(saved["thermo"], dict(source.thermo.input_data))
                self.assertEqual(saved["source_note"], source.input_data.get("note"))
        catalog["gas_datasets"][0]["species"][0]["thermo"]["data"][0][0] = -999
        self.assertNotEqual(property_catalog()["gas_datasets"][0]["species"][0]["thermo"]["data"][0][0], -999)
        json.dumps(property_catalog(), allow_nan=False)

    def test_species_curve_units_reference_and_enthalpy_derivative(self):
        result = sample_property_curve(kind="species", dataset="gri30_highT", member="H2O",
                                       start_K=799.99, end_K=800.01, points=3)
        gas = ct.Solution(yaml=(Path(ct.__file__).parent / "data" / "gri30_highT.yaml").read_text(encoding="utf8"))
        gas.TPX = 800, gas.species("H2O").thermo.reference_pressure, "H2O:1"
        mid = result["rows"][1]
        self.assertAlmostEqual(mid["cp_JmolK"], gas.cp_mole / 1000, places=9)
        self.assertAlmostEqual(mid["h_Jmol"], gas.enthalpy_mole / 1000, places=8)
        self.assertAlmostEqual(mid["s_JmolK"], gas.entropy_mole / 1000, places=9)
        derivative = (result["rows"][2]["h_Jmol"] - result["rows"][0]["h_Jmol"]) / .02
        self.assertAlmostEqual(derivative, mid["cp_JmolK"], places=6)
        self.assertIn("标准态", result["definition"])

    def test_inspection_never_changes_cached_solver_state_or_reference(self):
        gas = thermo._get_gas()
        gas.TPX = 850, 35e6, "O2:1"
        before = gas.state.copy()
        enthalpy = PropsSI("H", "T", 140, "P", 5e6, "HEOS::Methane")
        sample_property_curve(kind="species", dataset="gri30_highT", member="OH", start_K=300, end_K=4000)
        curve = sample_property_curve(kind="fluid", dataset="Methane", start_K=130, end_K=150, points=3, pressure_Pa=5e6)
        self.assertTrue((gas.state == before).all())
        self.assertAlmostEqual(curve["rows"][1]["h_Jkg"], enthalpy, places=6)
        self.assertEqual(PropsSI("H", "T", 140, "P", 5e6, "HEOS::Methane"), enthalpy)

    def test_invalid_identifiers_extrapolation_and_changed_hash_are_rejected(self):
        base = dict(kind="species", dataset="gri30_highT", member="H2O", start_K=300., end_K=2000.)
        for change in ({"dataset": "../../secret"}, {"member": "unknown"}, {"start_K": 100.},
                       {"end_K": 7000.}, {"end_K": float("nan")}, {"points": True}, {"points": 122},
                       {"pressure_Pa": 1e6}, {"expected_sha256": "0" * 64}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                sample_property_curve(**(base | change))

    def test_material_curve_uses_its_own_source_range_and_does_not_extend_endpoints(self):
        from lre_workbench.materials.wall import get_wall_dataset
        for data in property_catalog()["wall_datasets"]:
            wall = get_wall_dataset(data["dataset_id"])
            for member in ("conductivity", "heat_capacity"):
                prop = getattr(wall, member + "_property")
                low, high = prop.range_K
                curve = sample_property_curve(kind="material", dataset=data["dataset_id"], member=member,
                                               start_K=low, end_K=high, points=3)
                self.assertEqual(curve["rows"][1][member], prop.value((low + high) / 2, policy="strict"))
                with self.assertRaisesRegex(ValueError, "不作外推"):
                    sample_property_curve(kind="material", dataset=data["dataset_id"], member=member,
                                          start_K=low - 1, end_K=high)

    def test_pure_fluid_unavailable_points_are_not_replaced_by_nearby_values(self):
        saturation = PropsSI("T", "P", 1e5, "Q", 0, "Methane")
        curve = sample_property_curve(kind="fluid", dataset="Methane", start_K=saturation - 5,
                                       end_K=saturation + 5, pressure_Pa=1e5, points=3)
        self.assertEqual([r["status"] for r in curve["rows"]], ["available", "unavailable", "available"])
        self.assertNotIn("h_Jkg", curve["rows"][1])
        self.assertNotEqual(curve["rows"][0]["phase"], curve["rows"][2]["phase"])

    def test_new_result_snapshot_preserves_actual_candidates_and_dormant_material_role(self):
        inputs = EngineInputs(cooling_bartz_mode=2, combustion_loss_model="adiabatic", methane_thermo_dataset="gri30_highT")
        result = CycleResults(inputs, {"thermo_source": 1.}, {
            "main_chamber": FlowState("main_chamber", 33e6, 3700, 1, composition={"H2O": .7, "CO2": .3}),
            "fuel_after_cooling": FlowState("fuel_after_cooling", 30e6, 400, 1, composition={"CH4": 1}),
        }, {}, [])
        before = asdict(result)
        report = build_thermodynamic_applicability(result)
        manifest = report["data_manifest"]
        self.assertEqual(asdict(result), before)
        self.assertEqual(manifest["scope"], "calculation_snapshot")
        self.assertEqual(manifest["wall_thermal"]["use"], "not_evaluated")
        self.assertEqual(manifest["pure_fluids"][0]["id"], "Methane")
        self.assertEqual(manifest["gas_dataset"]["sha256"], gas_dataset_details("gri30_highT")["sha256"])
        self.assertFalse(manifest["chemistry"]["finite_rate_reactions_used"])
        self.assertEqual(report["states"][0]["mass_fractions"], {"H2O": .7, "CO2": .3})
        manifest["candidate_species"].clear()
        self.assertTrue(build_thermodynamic_applicability(result)["data_manifest"]["candidate_species"])
