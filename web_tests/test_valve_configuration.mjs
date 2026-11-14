import assert from "node:assert/strict";
import test from "node:test";
import {applyValveConfigurationEdit as edit,renderValveConfiguration as render,valveConfigurationIssues as issues} from "../webapp/static/valve-configuration.js";
import {mergeInputDefaults,renderParameterGroups} from "../webapp/static/parameter-editor.js";

const KEY="valve_configuration_json";
function template(topology){return {schema_version:1,enabled:true,topology_id:topology,mode:"requirement_sizing",valves:Object.fromEntries(["MFV","OPFV","FPOV"].map(id=>[id,{
  id,fluid:id==="FPOV"?"O2":"CH4",upstream_node:`${topology}_${id}_inlet`,downstream_node:`${id}_outlet`,
  characteristic_version:"concept_linear_v1",rated_cda_m2:null,area_curve:[[0,0],[1,1]],
  opening_min:0,opening_max:1,reference_opening:.7,design_pressure_drop_Pa:null,leak_fraction:0,
  max_pressure_drop_ratio:.1,actuator_tau_s:.1,actuator_rate_per_s:1,
}]))};}
const catalog={templates:{paper_split_before_cooling_v1:template("paper_split_before_cooling_v1"),all_fuel_cooled_then_split_valves_v1:template("all_fuel_cooled_then_split_valves_v1")},topology_labels:{paper_split_before_cooling_v1:"先分流再冷却",all_fuel_cooled_then_split_valves_v1:"先冷却再分流"}};
const field={key:KEY,custom_editor:"valve_configuration",valve_catalog:catalog,default:"",label:"阀门定义"};
const config=inputs=>JSON.parse(inputs[KEY]);
const enabled=()=>edit({[KEY]:"",pc:33e6},{action:"topology",value:"paper_split_before_cooling_v1"},catalog);
function filled(){let inputs=enabled();for(const valveId of ["MFV","OPFV","FPOV"])inputs=edit(inputs,{action:"field",valveId,key:"design_pressure_drop_Pa",value:"1.2"},catalog);return inputs;}

test("old projects stay disabled and rendering/import default merges never activate valves",()=>{
  const old={pc:33e6},before=structuredClone(old);
  assert.deepEqual(mergeInputDefaults({[KEY]:""},old,true),{...old,[KEY]:""});
  assert.deepEqual(issues(old,catalog),[]);
  const html=render(old,field);
  assert.match(html,/未启用实体阀/);assert.doesNotMatch(html,/data-valve-id=/);
  assert.deepEqual(old,before);
});

test("activation copies registered nodes and leaves unknown sizing requirements empty",()=>{
  const before=structuredClone(catalog),inputs=enabled();
  assert.equal(config(inputs).valves.OPFV.upstream_node,"paper_split_before_cooling_v1_OPFV_inlet");
  assert.equal(config(inputs).valves.MFV.rated_cda_m2,null);
  assert.equal(issues(inputs,catalog).filter(item=>item.path.endsWith("design_pressure_drop_Pa")).length,3);
  assert.deepEqual(catalog,before);
  assert.throws(()=>edit(inputs,{action:"field",valveId:"MFV",key:"upstream_node",value:"foreign"},catalog),/不能单独编辑/);
});

test("topology changes reset physical candidate binding without mutating saved data",()=>{
  let inputs=filled();
  inputs=edit(inputs,{action:"field",valveId:"OPFV",key:"rated_cda_m2",value:"120"},catalog);
  inputs=edit(inputs,{action:"field",valveId:"OPFV",key:"characteristic_version",value:"hardware_A_v2"},catalog);
  const before=structuredClone(inputs),next=edit(inputs,{action:"topology",value:"all_fuel_cooled_then_split_valves_v1"},catalog);
  assert.equal(config(next).valves.OPFV.rated_cda_m2,null);
  assert.equal(config(next).valves.OPFV.characteristic_version,"concept_linear_v1");
  assert.equal(config(next).valves.OPFV.upstream_node,"all_fuel_cooled_then_split_valves_v1_OPFV_inlet");
  assert.deepEqual(inputs,before);
  assert.equal(edit(next,{action:"topology",value:""},catalog)[KEY],"");
});

test("display units round-trip to SI and candidate mode requires defined capacity",()=>{
  let inputs=filled();assert.deepEqual(issues(inputs,catalog),[]);
  inputs=edit(inputs,{action:"mode",value:"candidate_check"},catalog);
  assert.equal(issues(inputs,catalog).filter(item=>item.message.includes("候选校核需要")).length,3);
  for(const valveId of ["MFV","OPFV","FPOV"])inputs=edit(inputs,{action:"field",valveId,key:"rated_cda_m2",value:"123"},catalog);
  inputs=edit(inputs,{action:"field",valveId:"MFV",key:"reference_opening",value:"75"},catalog);
  assert.equal(config(inputs).valves.MFV.rated_cda_m2,123e-6);
  assert.equal(config(inputs).valves.MFV.design_pressure_drop_Pa,1.2e6);
  assert.equal(config(inputs).valves.MFV.reference_opening,.75);
  assert.deepEqual(issues(inputs,catalog),[]);
  assert.doesNotMatch(render(inputs,field),/data-valve-key="design_pressure_drop_Pa"/);
  inputs=edit(inputs,{action:"mode",value:"requirement_sizing"},catalog);
  assert.equal(config(inputs).valves.MFV.rated_cda_m2,null);
});

test("invalid numeric edits remain in the draft and block saving instead of clamping",()=>{
  const inputs=edit(filled(),{action:"field",valveId:"MFV",key:"opening_max",value:"60"},catalog);
  assert.equal(config(inputs).valves.MFV.opening_max,.6);
  assert.ok(issues(inputs,catalog).some(item=>item.path==="MFV.reference_opening"));
  const blank=edit(inputs,{action:"field",valveId:"MFV",key:"actuator_tau_s",value:""},catalog);
  assert.equal(config(blank).valves.MFV.actuator_tau_s,null);
  assert.ok(issues(blank,catalog).some(item=>item.path==="MFV.actuator_tau_s"));
});

test("curve editing retains endpoints and rejects nonmonotonic data",()=>{
  let inputs=edit(filled(),{action:"curve_add",valveId:"MFV"},catalog);
  assert.deepEqual(config(inputs).valves.MFV.area_curve,[[0,0],[.5,.5],[1,1]]);
  inputs=edit(inputs,{action:"curve",valveId:"MFV",index:1,column:1,value:"25"},catalog);
  assert.deepEqual(config(inputs).valves.MFV.area_curve[1],[.5,.25]);
  assert.deepEqual(issues(inputs,catalog),[]);
  const invalid=edit(inputs,{action:"curve",valveId:"MFV",index:1,column:0,value:"100"},catalog);
  assert.ok(issues(invalid,catalog).some(item=>item.path==="MFV.area_curve"));
  assert.throws(()=>edit(inputs,{action:"curve_remove",valveId:"MFV",index:0},catalog),/首尾断点/);
  assert.equal(config(edit(inputs,{action:"curve_remove",valveId:"MFV",index:1},catalog)).valves.MFV.area_curve.length,2);
});

test("unknown schema content is retained for explicit repair, and strings are escaped",()=>{
  const inputs=filled(),broken={...inputs,[KEY]:'{"schema_version":99,"enabled":true,"unsafe":"<script>"}'};
  const before=structuredClone(broken);
  assert.ok(issues(broken,catalog).length);
  const html=render(broken,field);
  assert.match(html,/data-valve-action="raw"/);assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>/);
  assert.deepEqual(broken,before);
  const foreign=config(inputs);foreign.valves.OPFV.upstream_node="foreign_project_port";
  assert.ok(issues({[KEY]:JSON.stringify(foreign)},catalog).some(item=>item.path==="OPFV.upstream_node"));
});

test("custom field renders one searchable owner and reports scope accurately",()=>{
  const html=renderParameterGroups([{id:"valve_configuration",title:"阀门与流路",fields:[field]}],filled(),new Set(["valve_configuration:MFV"]));
  assert.equal(html.split('data-field="valve_configuration_json"').length,2);
  assert.match(html,/data-valve-action="mode"/);assert.match(html,/固定硬件离设计求解仍需完整部件特性/);
  for(const id of ["MFV","OPFV","FPOV"])assert.match(html,new RegExp(`${id} · `));
  assert.match(html,/<details data-parameter-fold="valve_configuration:MFV" open>/);
});

test("candidate matching accepts blank unused pressure budgets",()=>{
  let inputs=edit(enabled(),{action:"mode",value:"candidate_check"},catalog);
  for(const valveId of ["MFV","OPFV","FPOV"])inputs=edit(inputs,{action:"field",valveId,key:"rated_cda_m2",value:"150"},catalog);
  assert.deepEqual(issues(inputs,catalog),[]);
  assert.equal(config(inputs).valves.MFV.design_pressure_drop_Pa,null);
  assert.equal(issues(edit(inputs,{action:"mode",value:"requirement_sizing"},catalog),catalog).filter(item=>item.path.endsWith("design_pressure_drop_Pa")).length,3);
});

const recommendedCatalog={...catalog,pressure_drop_recommendation:{id:"concept_pc_fraction_v1",chamber_pressure_ratio:.01,reference_pressure_Pa:33e6}};
test("activation scales explicit concept pressure defaults to the current chamber pressure",()=>{
  const inputs=edit({pc:20e6},{action:"topology",value:"paper_split_before_cooling_v1"},recommendedCatalog);
  for(const row of Object.values(config(inputs).valves))assert.equal(row.design_pressure_drop_Pa,.2e6);
  assert.deepEqual(issues(inputs,recommendedCatalog),[]);
  const before=structuredClone(inputs),changed={...inputs,pc:40e6};
  assert.match(render(changed,{valve_catalog:recommendedCatalog}),/0.4 MPa \/ 阀/);
  assert.equal(config(changed).valves.MFV.design_pressure_drop_Pa,.2e6);
  assert.deepEqual(inputs,before);
});
test("filling missing budgets preserves manual values; explicit reset uses the current pressure",()=>{
  const original=edit(enabled(),{action:"field",valveId:"MFV",key:"design_pressure_drop_Pa",value:"1.2"},catalog);
  const filled=edit(original,{action:"pressure_fill"},recommendedCatalog);
  assert.equal(config(filled).valves.MFV.design_pressure_drop_Pa,1.2e6);
  assert.equal(config(filled).valves.OPFV.design_pressure_drop_Pa,.33e6);
  assert.equal(config(original).valves.OPFV.design_pressure_drop_Pa,null);
  const reset=edit({...filled,pc:25e6},{action:"pressure_defaults"},recommendedCatalog);
  for(const row of Object.values(config(reset).valves))assert.equal(row.design_pressure_drop_Pa,.25e6);
  const candidate=edit(reset,{action:"mode",value:"candidate_check"},recommendedCatalog);
  assert.throws(()=>edit(candidate,{action:"pressure_defaults"},recommendedCatalog),/不使用最小设计压降/);
});
test("activation and topology are separate and alternative topology is in advanced settings",()=>{
  const disabled=render({},field);
  assert.match(disabled,/<button[^>]+data-valve-action="topology"[^>]+value="paper_split_before_cooling_v1"/);
  assert.doesNotMatch(disabled,/<select[^>]+data-valve-action="topology"/);
  const html=render(filled(),field);
  assert.match(html,/<details[^>]+data-parameter-fold="valve_configuration:topology"><summary>高级/);
  assert.doesNotMatch(html,/<option value=""/);
  assert.match(html,/调节主燃料支路流量/);
  assert.match(html,/计量送入富氧预燃室的少量燃料/);
  assert.match(html,/计量送入富燃预燃室的少量氧化剂/);
});
