import assert from "node:assert/strict";
import {test} from "node:test";
import {renderPreburnerInterfaces, renderPreburnerResults} from "../webapp/static/preburner-design.js";

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
  for (const id of ["inlet", "pressure_ffsc", "cooling", "component_efficiencies"]) {
    assert.ok(html.includes(`data-preburner-group="${id}"`));
  }
  assert.doesNotMatch(html, /<(input|select|textarea)\b/);
  assert.match(html, /沿程模型/);
  assert.match(renderPreburnerInterfaces({cooling_bartz_mode:0}), /给定吸热与压降预算/);
  assert.match(renderPreburnerInterfaces(), /冷却模型尚未确定/);
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
