import assert from "node:assert/strict";
import {test} from "node:test";
import {coolingInputSummaryState, renderCoolingInputSummary} from "../webapp/static/cooling-input-summary.js";

function fixture() {
  const inputs = {cycle_type:"ffsc", propellant_pair:"lox_methane", pc:33e6,
    cooling_n_channels:300, cooling_channel_height_m:.008, cooling_bartz_mode:1,
    wall_thermal_dataset_id:"grcop84_reference"};
  const throat = {x_m:.5, r_m:.1, n_channels:320, width_m:.0016, height_m:.007,
    wall_thickness_m:.0006, pitch_m:.002, rib_m:.0004, D_h_m:.0026};
  const geometry = {flow_direction:"counterflow", x_throat_m:.5,
    nodes:[{...throat,x_m:1,r_m:.2}, throat, {...throat,x_m:0,r_m:.15}],
    channel_distribution:"width_proportional_to_radius"};
  const profile = {version:1, model_id:"regen_quasi1d_ph_v1", flow_direction:"counterflow", geometry,
    nodes:[{kind:"inlet_port",x_m:1},{kind:"channel",x_m:1}, {kind:"channel",x_m:.5}, {kind:"channel",x_m:0}, {kind:"outlet_port",x_m:0}],
    material_assignment:{default_dataset_id:"grcop84_reference",zones:[]},
    hydraulic_parameters:{version:1,roughness_mode:"absolute",channel_roughness_m:4e-6,k_minor_inlet:0,k_minor_outlet:2},
    heat_transfer_correction:{factor:1,basis:""}};
  const record = {status:"succeeded",revision:12,result:{calculation:{inputs, states:{
    fuel_after_pump:{mass_flow:90,pressure:50e6,temperature:120,enthalpy:-100000}},
    components:{cooling_channel:{profile}}}}};
  return {inputs,record,profile};
}

test("sources without a result contain no fabricated numeric boundaries or editable copies", () => {
  const {inputs} = fixture();
  const state = coolingInputSummaryState(inputs);
  assert.equal(state.status,"empty");
  assert.equal(state.isCurrent,false);
  assert.equal(state.profile,null);
  const html = renderCoolingInputSummary(inputs);
  assert.match(html,/无需在此重复填写/);
  assert.doesNotMatch(html, /<table|<(input|select|textarea)\b|300|33000000/);
  assert.match(html,/Dittus–Boelter/);
});

test("matched FFSC methane record exposes only its saved boundary and geometry", () => {
  const {inputs,record,profile} = fixture(), before = JSON.stringify({inputs,record});
  const state = coolingInputSummaryState({...inputs},{record});
  assert.equal(state.status,"current");
  assert.equal(state.isCurrent,true);
  assert.equal(state.profile,profile);
  assert.equal(state.boundary.enthalpy,-100000);
  const html = renderCoolingInputSummary(inputs,{record});
  for (const value of ["90","50","120","-100","320","1.6","7","0.6","2","0.4","2.6"]) {
    assert.ok(html.includes(`<td>${value}</td>`),value);
  }
  assert.doesNotMatch(html, /<td>300<\/td>|<td>8<\/td>/);
  assert.match(html,/ks = 4 μm/);
  assert.match(html,/入口 K = 0，出口 K = 2/);
  assert.equal(JSON.stringify({inputs,record}),before);
});

test("changes anywhere in the draft hide stale numeric conditions and sampling profile", () => {
  const {inputs,record} = fixture();
  const state = coolingInputSummaryState({...inputs,pc:34e6},{record});
  assert.equal(state.status,"stale");
  assert.equal(state.profile,null);
  assert.equal(state.isCurrent,false);
  const html = renderCoolingInputSummary({...inputs,pc:34e6},{record});
  assert.match(html,/当前参数与已保存计算不同/);
  assert.doesNotMatch(html,/<table|<td>|ks = 4/);
});

test("every available identity source independently rejects old GG or kerosene records", () => {
  for (const path of ["wrapper","submitted","calculation"]) {
    for (const override of [{cycle_type:"gg"},{propellant_pair:"lox_kerosene"}]) {
      const {inputs,record} = fixture();
      const old = {...inputs,...override};
      if (path === "wrapper") record.inputs=old;
      if (path === "submitted") record.result.submitted_inputs=old;
      if (path === "calculation") record.result.calculation.inputs=old;
      const state = coolingInputSummaryState(inputs,{record});
      assert.equal(state.status,"unsupported",path);
      assert.equal(state.profile,null);
      assert.doesNotMatch(renderCoolingInputSummary(inputs,{record}),/<table/);
    }
  }
});

test("unknown snapshots and unsuccessful records cannot be labeled current", () => {
  const {inputs,record} = fixture();
  delete record.result.calculation.inputs;
  record.result.submitted_inputs=inputs;
  assert.equal(coolingInputSummaryState(inputs,{record}).status,"unknown");
  record.result.calculation.inputs=inputs;
  record.inputs="invalid snapshot";
  assert.equal(coolingInputSummaryState(inputs,{record}).status,"unknown");
  delete record.inputs;
  record.status="failed";
  assert.equal(coolingInputSummaryState(inputs,{record}).status,"incomplete");
});

test("coflow and reversed historical coordinates never masquerade as current reverse flow", () => {
  for (const mutate of [p=>{p.flow_direction="coflow";}, p=>{p.geometry.flow_direction="coflow";},
    p=>p.geometry.nodes.reverse(), p=>p.nodes.reverse()]) {
    const {inputs,record,profile} = fixture();
    mutate(profile);
    const state = coolingInputSummaryState(inputs,{record});
    assert.equal(state.status,"flow_mismatch");
    assert.equal(state.isCurrent,false);
    assert.equal(state.profile,null);
  }
});

test("new disabled optional fields retain old result identity without masking meaningful edits", () => {
  const {inputs,record} = fixture();
  const defaults = {cooling_channel_roughness_m:null,cooling_k_minor_inlet:null,cooling_k_minor_outlet:null,
    cooling_htc_enhancement_basis:"",ox_to_opb_line_dp_ratio:null};
  assert.equal(coolingInputSummaryState({...inputs,...defaults},{record}).status,"current");
  for (const change of [{cooling_k_minor_inlet:0},{cooling_channel_roughness_m:0},
    {cooling_htc_enhancement_basis:"calibration report"},{unrelated:null}]) {
    assert.equal(coolingInputSummaryState({...inputs,...defaults,...change},{record}).status,"stale");
  }
});

test("missing or nonfinite data are not replaced by defaults or misleading zero", () => {
  const {inputs,record,profile} = fixture();
  const pump=record.result.calculation.states.fuel_after_pump;
  pump.mass_flow=null; pump.pressure=Infinity; pump.temperature=NaN; pump.enthalpy=0;
  profile.geometry.nodes[1].wall_thickness_m=-1;
  profile.geometry.nodes[1].n_channels=320.5;
  const html = renderCoolingInputSummary(inputs,{record});
  assert.match(html,/冷却入口比焓<\/th><td>0<\/td>/);
  for (const label of ["甲烷总质量流量","冷却入口压力","冷却入口温度","热壁厚度 t","通道数 N"]) {
    assert.ok(html.includes(`${label}</th><td>未记录</td>`),label);
  }
  assert.doesNotMatch(html,/Infinity|NaN|<td>-1000<\/td>/);
});

test("unsupported profiles and missing exact throat nodes never generate draft geometry", () => {
  const {inputs,record,profile} = fixture();
  profile.geometry.x_throat_m=.499;
  assert.equal(coolingInputSummaryState(inputs,{record}).throat,null);
  assert.match(renderCoolingInputSummary(inputs,{record}),/通道数 N<\/th><td>未记录<\/td>/);
  profile.model_id="legacy_budget";
  assert.equal(coolingInputSummaryState(inputs,{record}).status,"unavailable");
});

test("all source and record strings are escaped", () => {
  const {inputs,record,profile} = fixture();
  inputs.wall_thermal_dataset_id='<img src=x onerror="bad()">';
  record.revision="<script>bad()</script>";
  profile.material_assignment.default_dataset_id="<dataset>";
  profile.heat_transfer_correction.basis="<b>test</b>";
  const html = renderCoolingInputSummary(inputs,{record});
  assert.doesNotMatch(html,/<script|<img|<dataset>|<b>test/);
  assert.match(html,/&lt;img/);
  assert.match(html,/&lt;dataset&gt;/);
  assert.match(html,/&lt;b&gt;test/);
});

function valveFixture(topology="paper_split_before_cooling_v1") {
  const value=fixture(), {inputs,record,profile}=value;
  const paper=topology==="paper_split_before_cooling_v1", mass=paper?70:90;
  const config={schema_version:1,enabled:true,topology_id:topology,mode:"requirement_sizing",valves:{
    MFV:{id:"MFV",fluid:"CH4",upstream_node:"fuel_after_pump",downstream_node:"cooling_inlet"},
    OPFV:{id:"OPFV",fluid:"CH4",upstream_node:paper?"fuel_after_pump":"fuel_after_cooling",downstream_node:"opfv_outlet"},
    FPOV:{id:"FPOV",fluid:"O2",upstream_node:"lox_after_pump",downstream_node:"fpov_outlet"}}};
  inputs.valve_configuration_json=JSON.stringify(config);
  record.result.calculation.steady_closure={valve_design:{configuration:config}};
  Object.assign(record.result.calculation.states,{cooling_inlet:{mass_flow:mass,pressure:45e6,temperature:118,enthalpy:-100000},
    fuel_after_cooling:{mass_flow:mass},branch_fuel_to_fpb:{mass_flow:70}});
  Object.assign(profile.nodes[0],{p_Pa:45e6,T_K:118});
  Object.assign(profile.nodes[1],{rho_kgm3:500,velocity_ms:mass/5,A_flow_m2:.01});
  return value;
}

test("explicit valve summaries use post-MFV state and actual coolant flow for both layouts",()=>{
  for(const topology of ["paper_split_before_cooling_v1","all_fuel_cooled_then_split_valves_v1"]){
    const {inputs,record}=valveFixture(topology), before=JSON.stringify(record);
    const state=coolingInputSummaryState(inputs,{record});
    assert.equal(state.status,"current");
    assert.equal(state.boundary.mdot,topology==="paper_split_before_cooling_v1"?70:90);
    assert.equal(state.boundary.pressure,45e6);assert.equal(state.boundary.temperature,118);
    const html=renderCoolingInputSummary(inputs,{record});
    assert.match(html,/MFV 出口 \/ 冷却入口/);assert.match(html,/冷却支路质量流量/);
    assert.doesNotMatch(html,/甲烷总质量流量|已保存入口条件 · 燃料主泵出口/);
    if(topology==="paper_split_before_cooling_v1")assert.match(html,/OPFV 旁路直接供给富氧预燃室/);
    assert.equal(JSON.stringify(record),before);
  }
});

test("missing valve port and conflicting saved coolant boundary never fall back to total pump flow",()=>{
  const mutations=[r=>delete r.result.calculation.states.cooling_inlet,
    r=>r.result.calculation.components.cooling_channel.profile.nodes[0].p_Pa=50e6,
    r=>r.result.calculation.components.cooling_channel.profile.nodes[1].velocity_ms=18,
    r=>r.result.calculation.states.cooling_inlet.mass_flow=90,
    r=>delete r.result.calculation.steady_closure.valve_design];
  for(const mutate of mutations){
    const {inputs,record}=valveFixture();mutate(record);
    const state=coolingInputSummaryState(inputs,{record});
    assert.equal(state.isCurrent,false);assert.equal(state.boundary,null);
    assert.doesNotMatch(renderCoolingInputSummary(inputs,{record}),/<table/);
  }
});

test("disabled default configuration preserves legacy identity while unknown valves cannot infer a port",()=>{
  const {inputs,record}=fixture();
  assert.equal(coolingInputSummaryState({...inputs,valve_configuration_json:""},{record}).status,"current");
  inputs.valve_configuration_json=JSON.stringify({schema_version:1,enabled:true,topology_id:"unknown",valves:{}});
  assert.equal(coolingInputSummaryState(inputs,{record}).status,"valve_mismatch");
});
