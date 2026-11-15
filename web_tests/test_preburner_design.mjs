import assert from "node:assert/strict";
import {test} from "node:test";
import {renderPreburnerInterfaces, renderPreburnerResults,preburnerSavedSummary,renderPreburnerSummary,renderPreburnerRequirements,preburnerLimitSetupState,refreshPreburnerLimitSetup} from "../webapp/static/preburner-design.js";

test("limit guidance identifies the actual missing information",()=>{
  const inputs={ox_preburner_temp_limit_status:"documented",ox_preburner_temp_limit:800};
  assert.match(preburnerLimitSetupState(inputs,"ox").message,/尚未登记限值来源/);
  inputs.ox_preburner_temp_limit_basis="试验 A，设计工况";
  assert.match(preburnerLimitSetupState(inputs,"ox").message,/已登记来源/);
  assert.equal(preburnerLimitSetupState(inputs,"ox").needsBasis,false);
  inputs.ox_preburner_temp_limit=null;
  assert.match(preburnerLimitSetupState(inputs,"ox").message,/大于零/);
  inputs.ox_preburner_temp_limit_status="unset";
  assert.match(preburnerLimitSetupState(inputs,"ox").message,/未评估/);
});

test("typing a limit basis refreshes its guidance without replacing form controls or collapsing it",()=>{
  const note={dataset:{}},label={textContent:""},basis={open:false,querySelector:()=>label};
  const section={dataset:{preburnerLimitSide:"ox"},querySelector:selector=>selector===".preburner-basis"?basis:note};
  const root={querySelectorAll:()=>[section],set innerHTML(value){throw new Error("must preserve controls and caret");}};
  const inputs={ox_preburner_temp_limit_status:"documented",ox_preburner_temp_limit:800};
  refreshPreburnerLimitSetup(root,inputs);
  assert.equal(basis.open,true);assert.match(label.textContent,/未登记（必填）/);
  inputs.ox_preburner_temp_limit_basis="  已录入依据  ";
  refreshPreburnerLimitSetup(root,inputs);
  assert.equal(basis.open,true);assert.match(label.textContent,/已填写（必填）/);
  assert.match(note.textContent,/已登记来源/);
  inputs.ox_preburner_temp_limit_status="provisional";
  refreshPreburnerLimitSetup(root,inputs);
  assert.equal(note.dataset.preburnerLimitState,"provisional");assert.doesNotMatch(label.textContent,/必填/);
  assert.match(note.textContent,/依据待核实/);
});

function fixture() {
  const inputs = {cycle_type:"ffsc", pc:33e6, ox_preburner_temp_target:750, cooling_bartz_mode:1};
  return {
    id:"saved-point", revision:12, status:"succeeded", created:1793614514,
    result:{calculation:{inputs, summary:{
      mdot_ox_to_ox_preburner_kg_s:590, mdot_fuel_to_ox_preburner_kg_s:10,
      mdot_ox_to_fuel_preburner_kg_s:30, mdot_fuel_to_fuel_preburner_kg_s:150,
      ox_preburner_mr:59, fuel_preburner_mr:.2,
      ox_preburner_pressure_Pa:55e6, fuel_preburner_pressure_Pa:60e6,
      ox_preburner_T_combustion_K:749, fuel_preburner_T_combustion_K:851,
      ox_turbine_power_MW:40.001, ox_pump_power_MW:40,
      fuel_turbine_power_MW:20, fuel_pump_power_MW:20,
    }, components:{}, states:{}}},
  };
}

test("interfaces link to the single parameter owners without editable copies", () => {
  const html = renderPreburnerInterfaces({cooling_bartz_mode:1});
  for (const id of ["inlet", "pressure_ffsc", "cooling", "component_efficiencies", "chamber_chemistry", "initialization", "engineering_constraints"]) {
    assert.ok(html.includes(`data-ev-related="${id}"`));
  }
  assert.match(html,/data-ev-origin="preburner_design"/);
  assert.doesNotMatch(renderPreburnerInterfaces({},[{id:"inlet"}]),/data-ev-related="cooling"/);
  assert.doesNotMatch(html, /<(input|select|textarea)\b/);
  assert.match(html, /沿程模型/);
  assert.match(renderPreburnerInterfaces({cooling_bartz_mode:0}), /给定吸热与压降预算/);
  assert.match(renderPreburnerInterfaces(), /冷却模型尚未确定/);
});

function convergedFixture(){
  const record=fixture(),calc=record.result.calculation;
  Object.assign(calc.inputs,{use_target_preburner_temp:1,use_target_preburner_mr:1,cooling_bartz_mode:0,
    ox_preburner_temp_limit_status:"provisional",ox_preburner_temp_limit:800,
    fuel_preburner_temp_limit_status:"documented",fuel_preburner_temp_limit:900,fuel_preburner_temp_limit_basis:"试验 A，仅适用该工况"});
  const layer=()=>({enabled:true,status:"pass",residual:0,tolerance:1});
  calc.convergence={schema_version:1,layers:{inner:layer(),preburner:layer(),thermal:{enabled:false,status:"disabled"},continuation:{...layer(),reached_ratio:1,termination_reason:"target_reached"}}};
  calc.summary.solver_residual_norm=0;
  record.result.assessment={numerical:{status:"pass"}};
  return record;
}

test("unset limits keep the saved temperatures but hide inactive values and empty evidence rows",()=>{
  const record=convergedFixture(),inputs=record.result.calculation.inputs;
  inputs.ox_preburner_temp_limit_status="unset";inputs.fuel_preburner_temp_limit_status="unset";
  const before=JSON.stringify(record),html=renderPreburnerSummary(record);
  assert.match(html,/>749</);assert.match(html,/>851</);assert.match(html,/未评估/);
  assert.doesNotMatch(html,/记录温度上限|限值依据状态|温度裕量|记录中的限值依据|>800<|>900<|试验 A/);
  assert.equal(JSON.stringify(record),before);
});

test("saved target comparison respects mode and never fills historical holes with defaults",()=>{
  const record=convergedFixture(),before=JSON.stringify(record);
  const row=preburnerSavedSummary(record,"ox");
  assert.equal(row.target,750);assert.equal(row.actual,749);assert.equal(row.deviation,-1);assert.equal(row.margin,51);
  assert.equal(row.tone,"provisional");assert.match(row.verdict,/满足暂定限值，依据待核实/);
  assert.equal(preburnerSavedSummary(record,"fuel").tone,"within");
  assert.equal(preburnerSavedSummary(record,"fuel").target,undefined);
  const html=renderPreburnerSummary(record);assert.match(html,/记录目标温度/);assert.match(html,/试验 A/);
  assert.equal(JSON.stringify(record),before);
  record.result.calculation.inputs.use_target_preburner_temp=0;
  record.result.calculation.inputs.ox_preburner_mr_target=60;
  const mr=preburnerSavedSummary(record,"ox");assert.equal(mr.mode,"mixture_ratio");assert.equal(mr.target,60);assert.equal(mr.actual,59);assert.equal(mr.deviation,-1);
  delete record.result.calculation.inputs;
  assert.equal(preburnerSavedSummary(record,"ox").target,undefined);
  assert.doesNotMatch(renderPreburnerSummary(record),/>750<|>800<|>900</);
});

test("limit establishment, numerical evidence and exceedance are distinct",()=>{
  const record=convergedFixture(),inputs=record.result.calculation.inputs;
  inputs.ox_preburner_temp_limit_status="unset";
  let row=preburnerSavedSummary(record,"ox");assert.equal(row.margin,undefined);assert.match(row.verdict,/未评估/);
  inputs.ox_preburner_temp_limit_status="provisional";inputs.ox_preburner_temp_limit=740;
  row=preburnerSavedSummary(record,"ox");assert.equal(row.tone,"exceeded");assert.equal(row.margin,-9);assert.match(row.verdict,/超出暂定限值/);
  inputs.ox_preburner_temp_limit=800;inputs.ox_preburner_temp_limit_status="documented";
  assert.equal(preburnerSavedSummary(record,"ox").tone,"unknown");
  inputs.ox_preburner_temp_limit_basis="<script>source</script>";
  assert.equal(preburnerSavedSummary(record,"ox").tone,"within");
  assert.doesNotMatch(renderPreburnerSummary(record),/<script>/);
  assert.match(renderPreburnerSummary(record),/&lt;script&gt;/);
  delete record.result.calculation.convergence;
  row=preburnerSavedSummary(record,"ox");assert.equal(row.tone,"unknown");assert.match(row.verdict,/数值收敛待核对/);
  assert.doesNotMatch(renderPreburnerSummary(record),/满足已登记限值/);
  record.status="failed";
  assert.match(renderPreburnerSummary(record),/保存值仅供诊断/);
});

test("historical limits stay provisional and missing values remain unassessed",()=>{
  const record=fixture(),inputs=record.result.calculation.inputs;
  inputs.ox_preburner_temp_limit=800;
  let row=preburnerSavedSummary(record,"ox");assert.equal(row.evidence.status,"provisional");assert(row.evidence.historical);
  delete inputs.ox_preburner_temp_limit;
  assert.match(preburnerSavedSummary(record,"ox").verdict,/未评估/);
  record.result.calculation.summary.ox_preburner_T_combustion_K=null;
  inputs.ox_preburner_temp_limit=800;
  row=preburnerSavedSummary(record,"ox");assert.equal(row.margin,undefined);assert.match(row.verdict,/温度未记录/);
});

test("handoff preserves branch states, station conventions and signed power balance",()=>{
  const record=convergedFixture();
  record.result.calculation.states.branch_ox_to_opb={mass_flow:590,pressure:55e6,temperature:123,enthalpy:-2e5};
  const before=JSON.stringify(record),html=renderPreburnerRequirements(record);
  assert.match(html,/id="preburner-requirements"/);
  assert.match(html,/<td>590<\/td><td>55<\/td><td>123<\/td><td>-200<\/td>/);
  assert.match(html,/不能直接解释为喷注器上游总压/);
  assert.match(html,/未记录/);assert.match(html,/方案 v12/);
  assert.equal(JSON.stringify(record),before);
  record.result.calculation.inputs.cycle_type="gg";
  assert.equal(renderPreburnerRequirements(record),"");
});

test("no result asks for a calculation without showing design targets as results", () => {
  const html = renderPreburnerResults(null, fixture().result.calculation.inputs);
  assert.match(html, /尚无计算记录/);
  assert.doesNotMatch(html, /<table|750|已匹配|已通过/);
  assert.match(renderPreburnerResults({status:"failed"}, {}), /未保存预燃室计算结果/);
});

test("matching inputs are compared by content and saved side values are displayed", () => {
  const record = fixture();
  const draft = Object.fromEntries(Object.entries(record.result.calculation.inputs).reverse());
  const html = renderPreburnerResults(record, draft);
  assert.match(html, /data-preburner-result-state="current"/);
  assert.ok(html.includes(`结果来源：方案 v12 · 提交时间：${new Date(record.created*1000).toLocaleString("zh-CN",{hour12:false})}`));
  assert.doesNotMatch(html, /saved-point/);
  assert.match(html, /<td>59<\/td><td>0.2<\/td>/);
  assert.match(html, /<td>55<\/td><td>60<\/td>/);
  assert.match(html, /<td>749<\/td><td>851<\/td>/);
  assert.match(html, /<td>600<\/td><td>180<\/td>/);
  assert.match(html, /<td>1<\/td><td>0<\/td>/);
  assert.doesNotMatch(html, /750|匹配通过|约束通过/);
});

test("modified draft is stale while all displayed numbers stay bound to the record", () => {
  const record = fixture();
  const before = JSON.stringify(record);
  const html = renderPreburnerResults(record, {...record.result.calculation.inputs, pc:99e6, ox_preburner_temp_target:999});
  assert.match(html, /data-preburner-result-state="stale"/);
  assert.match(html, /当前参数已修改/);
  assert.match(html, /<td>749<\/td><td>851<\/td>/);
  assert.doesNotMatch(html, /999|99,000,000/);
  assert.equal(JSON.stringify(record), before);
});

test("new null pressure overrides preserve old result identity without mutating either snapshot", () => {
  const record = fixture();
  const overrides = Object.fromEntries(["ox_to_opb", "ox_to_fpb", "fuel_to_opb", "fuel_to_fpb"]
    .flatMap(branch => ["line_dp_ratio", "injector_dp_ratio"].map(suffix => [`${branch}_${suffix}`, null])));
  const draft = {...record.result.calculation.inputs, ...overrides};
  const recordBefore = JSON.stringify(record), draftBefore = JSON.stringify(draft);
  assert.match(renderPreburnerResults(record, draft), /data-preburner-result-state="current"/);
  assert.equal(JSON.stringify(record), recordBefore);
  assert.equal(JSON.stringify(draft), draftBefore);
  const olderDraft = {...record.result.calculation.inputs};
  record.result.calculation.inputs = {...record.result.calculation.inputs, ...overrides};
  assert.match(renderPreburnerResults(record, olderDraft), /data-preburner-result-state="current"/);
});

test("explicit branch budgets and unrelated null fields still invalidate saved results", () => {
  const record = fixture(), inputs = record.result.calculation.inputs;
  for (const branch of ["ox_to_opb", "ox_to_fpb", "fuel_to_opb", "fuel_to_fpb"]) {
    for (const suffix of ["line_dp_ratio", "injector_dp_ratio"]) {
      const key = `${branch}_${suffix}`;
      assert.match(renderPreburnerResults(record, {...inputs, [key]:.03}), /data-preburner-result-state="stale"/);
    }
  }
  assert.match(renderPreburnerResults(record, {...inputs, ox_to_opb_line_dp_ratio:0}), /data-preburner-result-state="stale"/);
  assert.match(renderPreburnerResults(record, {...inputs, unrelated_option:null}), /data-preburner-result-state="stale"/);
  assert.match(renderPreburnerResults(record, {...inputs, pc:null}), /data-preburner-result-state="stale"/);
});

test("absent snapshots and absent values cannot be called current or replaced by zero", () => {
  const record = fixture();
  delete record.result.calculation.inputs;
  delete record.created;
  record.result.calculation.summary = {ox_preburner_T_combustion_K:null, ox_pump_power_MW:0};
  const html = renderPreburnerResults(record, {cycle_type:"ffsc", ox_preburner_temp_target:750});
  assert.match(html, /data-preburner-result-state="unknown"/);
  assert.match(html, /无法核对/);
  assert.match(html, /提交时间未记录/);
  assert.match(html, /轴功率差（涡轮 − 主泵）<\/th><td>kW<\/td><td>未记录<\/td><td>未记录<\/td>/);
  assert.match(html, /对应主泵轴功率<\/th><td>MW<\/td><td>0<\/td><td>未记录<\/td>/);
  assert.doesNotMatch(html, /750/);
});

test("canonical component SI values are used and negative shaft differences survive", () => {
  const record = fixture();
  record.result.calculation.components = {
    ox_preburner:{outputs:{mdot_total_kgs:605, mdot_ox_kgs:595, mdot_fuel_kgs:10, preburner_pressure_Pa:56e6, T_combustion_K:752}},
    ox_pump:{outputs:{power_W:40e6}},
    ox_preburner_turbine:{outputs:{turbine_power_W:39998000}},
  };
  const html = renderPreburnerResults(record, record.result.calculation.inputs);
  assert.match(html, /<td>605<\/td><td>180<\/td>/);
  assert.match(html, /<td>56<\/td><td>60<\/td>/);
  assert.match(html, /<td>752<\/td><td>851<\/td>/);
  assert.match(html, /<td>-2<\/td><td>0<\/td>/);
});

test("failed records remain diagnostic even when the input snapshots agree", () => {
  const record = fixture();
  record.status = "failed";
  assert.match(renderPreburnerResults(record, record.result.calculation.inputs), /已保存数值仅供诊断/);
  record.result.calculation.inputs.cycle_type = "gg";
  assert.match(renderPreburnerResults(record, record.result.calculation.inputs), /不属于全流量补燃循环/);
});

test("record labels are escaped and malformed numerical values are not interpolated", () => {
  const record = fixture();
  record.id = '<img src=x onerror="run()">';
  record.revision = '<script>run()</script>';
  record.created = Infinity;
  record.result.calculation.summary.ox_preburner_mr = '<b>1</b>';
  record.result.calculation.summary.fuel_preburner_T_combustion_K = Infinity;
  const html = renderPreburnerResults(record, record.result.calculation.inputs);
  assert.doesNotMatch(html, /&lt;img/);
  assert.match(html, /提交时间未记录/);
  assert.match(html, /&lt;script&gt;run\(\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script|<img|<b>1<\/b>|Infinity|NaN/);
  assert.match(html, /<td>749<\/td><td>未记录<\/td>/);
});
