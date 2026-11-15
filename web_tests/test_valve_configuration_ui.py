"""The browser receives the same registered topology definitions as the core."""
import json
from pathlib import Path
import subprocess
import unittest

from lre_workbench.components.valves import parse_configuration, valve_configuration_catalog
from webapp.ui_schema import design_ui_schema


ROOT = Path(__file__).resolve().parent.parent


class ValveConfigurationUiTests(unittest.TestCase):
    def test_browser_initializes_current_pressure_and_preserves_saved_requirements(self):
        script = """
import fs from 'node:fs';
import {applyValveConfigurationEdit as edit,valveConfigurationIssues as issues} from './webapp/static/valve-configuration.js';
const catalog=JSON.parse(fs.readFileSync(0,'utf8'));
const started=edit({pc:20e6},{action:'topology',value:catalog.recommended_topology_id},catalog);
let changed=edit(started,{action:'field',valveId:'MFV',key:'design_pressure_drop_Pa',value:'0.75'},catalog);
changed=edit(changed,{action:'field',valveId:'OPFV',key:'design_pressure_drop_Pa',value:''},catalog);
const filled=edit({...changed,pc:33e6},{action:'pressure_fill'},catalog);
const reset=edit(filled,{action:'pressure_defaults'},catalog);
process.stdout.write(JSON.stringify([started,filled,reset].map(inputs=>({inputs,issues:issues(inputs,catalog)}))));
"""
        run = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT,
                             input=json.dumps(valve_configuration_catalog()), text=True,
                             encoding="utf-8", capture_output=True, check=True)
        rows = json.loads(run.stdout)
        for row in rows:
            self.assertEqual(row["issues"], [])
        parsed = [parse_configuration(row["inputs"]["valve_configuration_json"])["valves"] for row in rows]
        self.assertEqual([parsed[0][key]["design_pressure_drop_Pa"] for key in ("MFV", "OPFV", "FPOV")],
                         [200000, 200000, 200000])
        self.assertEqual([parsed[1][key]["design_pressure_drop_Pa"] for key in ("MFV", "OPFV", "FPOV")],
                         [750000, 330000, 200000])
        self.assertEqual([parsed[2][key]["design_pressure_drop_Pa"] for key in ("MFV", "OPFV", "FPOV")],
                         [330000, 330000, 330000])

    def test_one_custom_field_has_disabled_default_and_authoritative_catalog(self):
        schema = design_ui_schema()
        self.assertEqual(schema["defaults"]["valve_configuration_json"], "")
        for section in ("groups", "system_groups"):
            owners = [(group, field) for group in schema[section] for field in group["fields"]
                      if field["key"] == "valve_configuration_json"]
            self.assertEqual(len(owners), 1)
            group, field = owners[0]
            self.assertEqual(group["id"], "valve_configuration")
            self.assertEqual(field["custom_editor"], "valve_configuration")
            self.assertEqual(field["valve_catalog"], valve_configuration_catalog())

    def test_browser_edits_registered_templates_into_core_valid_si_snapshots(self):
        script = """
import fs from 'node:fs';
import {applyValveConfigurationEdit as edit,valveConfigurationIssues as issues,renderValveConfiguration} from './webapp/static/valve-configuration.js';
const catalog=JSON.parse(fs.readFileSync(0,'utf8'));
const results=[];
for(const topology_id of Object.keys(catalog.templates)){
 let inputs=edit({}, {action:'topology',value:topology_id},catalog);
 for(const valveId of ['MFV','OPFV','FPOV'])inputs=edit(inputs,{action:'field',valveId,key:'design_pressure_drop_Pa',value:'0.75'},catalog);
 results.push({inputs,issues:issues(inputs,catalog),html:renderValveConfiguration(inputs,{valve_catalog:catalog})});
}
process.stdout.write(JSON.stringify(results));
"""
        run = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT,
                             input=json.dumps(valve_configuration_catalog()), text=True,
                             encoding="utf-8", capture_output=True, check=True)
        for item in json.loads(run.stdout):
            self.assertEqual(item["issues"], [])
            config = parse_configuration(item["inputs"]["valve_configuration_json"])
            self.assertEqual(config["valves"]["MFV"]["design_pressure_drop_Pa"], 750000)
            self.assertIn(config["valves"]["OPFV"]["upstream_node"], item["html"])
            self.assertNotIn('data-valve-key="upstream_node"', item["html"])


if __name__ == "__main__":
    unittest.main()
