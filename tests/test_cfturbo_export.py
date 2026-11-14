"""A CFturbo reference must preserve identity, topology and boundary evidence."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lre_workbench.integrations.cfturbo import build_cfturbo_markdown, export_cfturbo_markdown
from lre_workbench.domain.models import ComponentResult, CycleResults, EngineInputs, FlowState


def result_fixture(cycle="ffsc", layout="single_shaft", pair="lox_methane"):
    inputs = EngineInputs(cycle_type=cycle, gg_layout=layout, propellant_pair=pair,
                          cooling_bartz_mode=0,
                          fuel_inlet_temp=300 if pair == "lox_kerosene" else 110)
    turbine = {"mass_flow_kgs": 12.5, "p_in_Pa": 9e6, "p_out_Pa": 0.5e6,
               "T_in_K": 950, "T_exhaust_K": 700, "turbine_pressure_ratio": 18,
               "eta_turbine": .71, "molar_mass_in": 24,
               "n_stages_recommended": 3, "shaft_speed_rpm": 24000}
    pump = {"p_in_bar": 8, "p_out_bar": 400, "delta_p_bar": 392,
            "volume_flow_m3s": .1, "T_in_K": 95, "T_out_K": 115}
    components = {
        "ox_pump": ComponentResult("ox_pump", dict(pump)),
        "fuel_pump": ComponentResult("fuel_pump", {**pump, "T_in_K": inputs.fuel_inlet_temp + 5}),
    }
    if cycle == "ffsc":
        components.update({key: ComponentResult(key, dict(turbine)) for key in
                           ("ox_preburner_turbine", "fuel_preburner_turbine")})
    else:
        keys = ("gg_turbine_ox", "gg_turbine_fuel") if layout == "dual_shaft" else ("gg_turbine",)
        components.update({key: ComponentResult(key, {**turbine, "mass_flow_kgs": flow})
                           for key, flow in zip(keys, (7.25, 5.25) if len(keys) == 2 else (12.5,))})
    states = {
        "ox_preburner_outlet": FlowState("ox", 9e6, 950, 12.5, composition={"O2": .63, "H2O": .37}),
        "fuel_preburner_outlet": FlowState("fuel", 9e6, 950, 12.5, composition={"H2": .2, "CO": .8}),
        "gg_outlet": FlowState("gg", 9e6, 950, 12.5, composition={"CO": .75, "H2": .25}),
    }
    summary = {"mdot_ox_kg_s": 90, "mdot_fuel_kg_s": 25, "mdot_total_kg_s": 115}
    return CycleResults(inputs, summary, states, components, [])


class CFturboExportTests(unittest.TestCase):
    def setUp(self):
        self.props = patch('lre_workbench.integrations.cfturbo._fluid_props', return_value=None).start()
        self.addCleanup(patch.stopall)

    def test_legacy_nodes_are_not_presented_as_total_or_static_boundaries(self):
        result = result_fixture()
        # A newer input panel must not relabel outputs of an older calculation.
        result.inputs.turbine_boundary_basis = "total_to_total"
        text = build_cfturbo_markdown(result)
        for misleading in ("Total-to-static", "πts", "ηts", "πtt", "ηtt",
                           "Inlet total pressure", "Outlet static pressure", "Pressure (total)"):
            self.assertNotIn(misleading, text)
        self.assertIn("节点压力比 p_in/p_out（口径待确认） | 18.0000", text)
        self.assertIn("系统等熵效率 η（口径待确认） | 0.710", text)

    def test_only_explicit_saved_total_to_total_declaration_labels_total_states(self):
        result = result_fixture()
        result.components["ox_preburner_turbine"].outputs["state_basis"] = "total_to_total"
        text = build_cfturbo_markdown(result)
        self.assertEqual(text.count("Total-to-total pressure ratio πtt"), 1)
        self.assertEqual(text.count("Inlet total pressure pt"), 1)
        self.assertEqual(text.count("Outlet total pressure pt"), 1)
        self.assertEqual(text.count("Total-to-total efficiency ηtt"), 1)
        self.assertEqual(text.count("节点压力比 p_in/p_out（口径待确认）"), 1)
        self.assertNotIn("πts", text)

    def test_single_shaft_gg_has_one_turbine_and_saved_composition(self):
        result = result_fixture("gg")
        # Ignore stale branches inconsistent with the saved single-shaft layout.
        result.components["gg_turbine_ox"] = ComponentResult("stale", {"mass_flow_kgs": 999})
        text = build_cfturbo_markdown(result)
        self.assertIn("# GG 单轴", text)
        self.assertEqual(text.count("### GG 涡轮"), 1)
        self.assertIn("本涡轮实际流量） | 12.500 | kg/s", text)
        self.assertIn("组分来源：gg_outlet", text)
        self.assertIn("| CO | 0.75 |", text)
        self.assertNotIn("999", text)

    def test_parallel_gg_uses_each_component_flow_and_never_generator_total(self):
        result = result_fixture("gg", "dual_shaft")
        result.components["gg_turbine"] = ComponentResult("stale", {"mass_flow_kgs": 999})
        text = build_cfturbo_markdown(result)
        self.assertEqual(text.count("### GG 涡轮"), 2)
        self.assertIn("本涡轮实际流量） | 7.250 | kg/s", text)
        self.assertIn("本涡轮实际流量） | 5.250 | kg/s", text)
        self.assertNotIn("本涡轮实际流量） | 12.500", text)
        del result.components["gg_turbine_fuel"].outputs["mass_flow_kgs"]
        text = build_cfturbo_markdown(result)
        self.assertIn("本涡轮实际流量） | 未记录／待确认 | kg/s", text)

    def test_kerosene_uses_dodecane_not_methane_and_saved_pump_inlet(self):
        result = result_fixture("gg", pair="lox_kerosene")
        text = build_cfturbo_markdown(result)
        self.assertIn("煤油（正十二烷替代）泵", text)
        self.assertIn("n-Dodecane", text)
        self.assertNotIn("Methane", text)
        self.assertNotIn("甲烷", text)
        fuel_call = self.props.call_args_list[1]
        self.assertEqual(fuel_call.args, ("n-Dodecane", 305., 8e5))
        self.assertIn("入口节点温度 T | 31.85 | °C", text)

    def test_saved_species_and_stages_replace_unsupported_fixed_advice(self):
        text = build_cfturbo_markdown(result_fixture())
        self.assertIn("质量分数 Y（非摩尔分数）", text)
        self.assertIn("| O2 | 0.63 |", text)
        self.assertIn("| H2 | 0.2 |", text)
        self.assertIn("记录的候选级数 | 3 |", text)
        for invented in ("92%", "78%", "CH₄ 为主", "**2 级**", "安全系数回到", "三者自洽 cp="):
            self.assertNotIn(invented, text)

    def test_missing_invalid_values_are_not_zero_or_default_molecular_weight(self):
        result = result_fixture()
        for component in result.components.values():
            component.outputs.clear()
        result.components["ox_preburner_turbine"].outputs.update(
            {"molar_mass_in": 0, "T_in_K": None, "turbine_pressure_ratio": float("inf")})
        result.states.clear()
        result.summary.clear()
        text = build_cfturbo_markdown(result)
        for fabricated in ("nan", " inf ", "0.030000", "-273.15", "| 0.0 |", "| 0 |"):
            self.assertNotIn(fabricated, text)
        self.assertIn("Molar weight M** | 未记录／待确认", text)
        self.assertIn("Gas constant R | 未记录／待确认", text)
        self.assertIn("Volume flow Q | 未记录／待确认", text)

    def test_zero_is_retained_and_invalid_composition_is_reported_without_normalizing(self):
        result = result_fixture()
        result.components["ox_preburner_turbine"].outputs["reaction_degree"] = 0
        result.states["ox_preburner_outlet"].composition = {"O2": .2, "H2O": .3}
        text = build_cfturbo_markdown(result)
        self.assertIn("反动度初选值 R | 0.00 |", text)
        self.assertIn("| O2 | 0.2 |", text)
        self.assertIn("未通过非负性／归一化检查", text)

    def test_volume_flow_conversion_molar_units_and_public_file_export(self):
        result = result_fixture()
        text = build_cfturbo_markdown(result)
        self.assertIn("Volume flow Q | 360 | m³/h", text)
        self.assertIn("Molar weight M** | 0.024000 | **kg/mol**", text)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "涡轮参考.md"
            returned = export_cfturbo_markdown(result, str(destination))
            self.assertEqual(returned, destination.read_text(encoding="utf-8"))
            self.assertEqual(returned, text)

    def test_legacy_bar_pressures_and_si_turbine_pressures_display_in_mpa(self):
        text = build_cfturbo_markdown(result_fixture())
        self.assertIn("节点压升 Δp | 39.200 | MPa", text)
        self.assertIn("入口节点压力 p | 0.800 | MPa", text)
        self.assertIn("| 压力 [MPa] | 0.800 | 40.000 |", text)
        self.assertIn("入口节点压力 p（口径待确认） | 9.000 | MPa", text)
        self.assertIn("出口节点压力 p（口径待确认） | 0.500 | MPa", text)
        self.assertNotIn("bar", text)
        self.assertEqual(self.props.call_args_list[0].args, ("Oxygen", 95., 8e5))

    def test_si_pressure_wins_over_inconsistent_engineering_unit_copies(self):
        result = result_fixture()
        result.components["ox_pump"].outputs.update(
            p_in_Pa=1.25e6, p_out_Pa=42e6, delta_p_Pa=40.75e6,
            p_in_MPa=7, p_out_MPa=8, delta_p_MPa=9)
        before = dict(result.components["ox_pump"].outputs)
        text = build_cfturbo_markdown(result).split("### 甲烷")[0]
        self.assertIn("| 压力 [MPa] | 1.250 | 42.000 |", text)
        self.assertIn("节点压升 Δp | 40.750 | MPa", text)
        self.assertEqual(self.props.call_args_list[0].args, ("Oxygen", 95., 1.25e6))
        self.assertEqual(result.components["ox_pump"].outputs, before)

    def test_mpa_fallback_and_legacy_turbine_zero_preserve_units(self):
        result = result_fixture()
        result.components["ox_pump"].outputs.update(
            p_in_Pa=None, p_in_MPa=1.2, p_out_MPa=41, delta_p_MPa=39.8)
        turbine = result.components["ox_preburner_turbine"].outputs
        del turbine["p_in_Pa"], turbine["p_out_Pa"]
        turbine.update(p_in_bar=90, p_out_bar=0)
        text = build_cfturbo_markdown(result)
        self.assertIn("| 压力 [MPa] | 1.200 | 41.000 |", text)
        self.assertIn("节点压升 Δp | 39.800 | MPa", text)
        self.assertIn("出口节点压力 p（口径待确认） | 0.000 | MPa", text)
        self.assertEqual(self.props.call_args_list[0].args, ("Oxygen", 95., 1.2e6))

    def test_vapor_pressure_is_converted_from_property_si_to_mpa(self):
        self.props.return_value = {"rho": 1100, "mu": .0002, "nu": .2, "lam": .1,
                                   "cp": 1700, "pv": 101325}
        text = build_cfturbo_markdown(result_fixture())
        self.assertIn("Vapor pressure pv | 0.101325 | MPa", text)

    def test_pump_reference_head_and_available_suction_are_not_design_certification(self):
        result = result_fixture()
        result.components["ox_pump"].outputs.update(head_m=4000, npsh_available_m=25,
            suction_specific_speed_nss_eu=500, suction_specific_speed_Nss=25800)
        text = build_cfturbo_markdown(result)
        self.assertIn("入口密度参考扬程 Href（非已校正总扬程） | 4000", text)
        self.assertIn("吸入比转速 nss(EU)（按可用 NPSHa） | 500", text)
        self.assertIn("系统效率不能直接替代液力效率和容积效率", text)
        self.assertIn("不是原计算记录保存的物性快照", text)
        self.assertNotIn("150–400", text)
        self.assertNotIn("| Head H |", text)


if __name__ == "__main__":
    unittest.main()
