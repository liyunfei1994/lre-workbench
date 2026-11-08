import assert from "node:assert/strict";
import {test} from "node:test";
import {
  pressureBranches, pressureBranchForField, renderPressureTopology,
  renderPressureWorkspace, renderPressureResults, syncPressureWorkspace,
} from "../webapp/static/pressure-design.js";

const overrideKeys = pressureBranches.flatMap(branch => [`${branch.id}_line_dp_ratio`, `${branch.id}_injector_dp_ratio`]);
const fieldKeys = ["valve_line_dp_ratio", "preburner_injector_dp_ratio", ...overrideKeys, "ox_turbine_exhaust_dp_ratio", "fuel_turbine_exhaust_dp_ratio"];

function fixture() {
  const inputs = {cycle_type:"ffsc", pc:33e6, valve_line_dp_ratio:.03, preburner_injector_dp_ratio:.12};
  const branch = (id, status="pass") => ({id, status, mass_flow_kg_s:12.5,
    source_pressure_Pa:62e6, line_drop_Pa:1e6, valve_drop_Pa:2e6,
    injector_drop_Pa:9e6, destination_pressure_Pa:50e6, pressure_residual_Pa:-.25});
  const pump = id => ({id, inlet_pressure_Pa:1e6, required_outlet_pressure_Pa:62e6,
    required_rise_Pa:61e6, cooling_drop_Pa:id === "fuel" ? 2e6 : 0,
    pressure_residual_Pa:0, pressure_link_status:"pass", capacity_status:"not_verified"});
  const gas = id => ({id, turbine_inlet_pressure_Pa:50e6, turbine_outlet_pressure_Pa:36e6,
    turbine_pressure_ratio:50/36, hot_gas_drop_Pa:3e6, chamber_pressure_Pa:33e6,
    pressure_residual_Pa:.05, status:"pass"});
  return {id:"raw-uuid-do-not-show", revision:13, status:"succeeded", created:1793614514,
    result:{calculation:{inputs, summary:{ox_pump_power_MW:40, ox_turbine_power_MW:39.998,
      fuel_pump_power_MW:30, fuel_turbine_power_MW:30},
      steady_closure:{feed_network:{mode:"design", status:"pass", max_pressure_residual_Pa:.25,
        max_mass_residual_kg_s:0, pressure_tolerance_Pa:1, mass_tolerance_kg_s:1e-8,
        branches:pressureBranches.map(row => branch(row.id)),
        pressure_contract:{pressure_convention:{label:"集总绝对压力 / Pa"},
          pump_capacity:{pumps:[pump("ox"), pump("fuel")]}, gas_paths:[gas("ox"), gas("fuel")]}}}}}};
}

test("workspace owns no duplicate fields and exposes all six physical paths", () => {
  const calls = [], fields = fieldKeys.map(key => ({key}));
  const html = renderPressureWorkspace({fields}, fixture().result.calculation.inputs,
    field => {calls.push(field.key); return `<label data-field="${field.key}"><input data-input="${field.key}"></label>`;});
  assert.deepEqual([...calls].sort(), [...fieldKeys].sort());
  assert.equal(new Set(calls).size, fieldKeys.length);
  for (const view of ["topology", "budget", "results"]) assert.match(html, new RegExp(`data-pressure-panel="${view}"`));
  for (const id of [...pressureBranches.map(row => row.id), "ox_gas", "fuel_gas"]) assert.match(html, new RegExp(`data-pressure-branch-panel="${id}"`));
  assert.match(html, /data-pressure-apply-shared/);
  assert.match(html, /支路字段留空时采用本组缺省值/);
  assert.match(html, /本段从再生冷却出口起计/);
  assert.match(html, /data-pressure-results/);
});

test("unknown future fields remain editable rather than disappearing", () => {
  const html = renderPressureWorkspace({fields:[{key:"future_loss"}]}, {}, field => `<input data-input="${field.key}">`);
  assert.match(html, /data-input="future_loss"/);
  assert.doesNotMatch(html, /data-pressure-apply-shared/);
});

test("branch navigation resolves field ownership including gas paths", () => {
  assert.equal(pressureBranchForField("fuel_to_fpb_line_dp_ratio"), "fuel_to_fpb");
  assert.equal(pressureBranchForField("ox_turbine_exhaust_dp_ratio"), "ox_gas");
  assert.equal(pressureBranchForField("preburner_injector_dp_ratio"), null);
  assert.equal(pressureBranchForField("pc"), null);
});

test("topology reads design boundaries without introducing editable copies or solved states", () => {
  const html = renderPressureTopology({pc:33e6, tank_pressure_pa:.5e6, ox_inlet_temp:90, fuel_inlet_temp:111, cooling_bartz_mode:1});
  assert.match(html, /data-pressure-reference="pc"[^>]*>33 /);
  assert.match(html, /data-pressure-reference="tank_pressure_pa"[^>]*>0.5 /);
  assert.match(html, /氧化剂经主泵后分流，燃料经再生冷却后分流/);
  assert.match(html, /当前沿程模型/);
  assert.doesNotMatch(html, /<(input|select|textarea)\b/);
  assert.match(renderPressureTopology(), /未记录/);
});

test("saved branch pressure and power results retain SI signs and never imply capacity verification", () => {
  const record = fixture();
  const html = renderPressureResults(record, record.result.calculation.inputs);
  assert.match(html, /data-pressure-result-state="current"/);
  assert.match(html, /结果来源：方案 v13 · 提交时间：/);
  assert.doesNotMatch(html, /raw-uuid-do-not-show/);
  assert.match(html, /<td>12.5<\/td><td>62<\/td><td>1<\/td><td>2<\/td><td>9<\/td><td>50<\/td><td>-0.25<\/td>/);
  assert.match(html, /<td>39.998<\/td><td>40<\/td><td>-2<\/td>/);
  assert.match(html, /机组供给能力<\/h4><span class="pressure-design-status">待验证/);
  assert.match(html, /所选历史记录采用统一支路预算/);
  assert.doesNotMatch(html, /能力已验证|能力通过|可用压力裕度/);
});

test("historical omitted overrides and new null fields have identical result identity", () => {
  const record = fixture(), inputs = record.result.calculation.inputs;
  const draft = {...inputs, ...Object.fromEntries(overrideKeys.map(key => [key, null]))};
  assert.match(renderPressureResults(record, draft), /data-pressure-result-state="current"/);
  draft.ox_to_fpb_line_dp_ratio = .04;
  assert.match(renderPressureResults(record, draft), /data-pressure-result-state="stale"/);
  const before = JSON.stringify(record);
  const html = renderPressureResults(record, {...inputs, pc:99e6});
  assert.match(html, /当前参数已修改/);
  assert.match(html, /<td>33<\/td>/);
  assert.doesNotMatch(html, /<td>99<\/td>/);
  assert.equal(JSON.stringify(record), before);
});

test("absent records, absent contract, and absent values remain explicitly unavailable", () => {
  assert.match(renderPressureResults(null, {pc:33e6}), /尚无计算记录/);
  assert.doesNotMatch(renderPressureResults(null, {pc:33e6}), /<table|已闭合|33/);
  const record = fixture();
  delete record.result.calculation.inputs;
  delete record.result.calculation.steady_closure.feed_network.pressure_contract;
  record.result.calculation.steady_closure.feed_network.branches = [{id:"ox_to_opb", source_pressure_Pa:null}];
  const html = renderPressureResults(record, {cycle_type:"ffsc"});
  assert.match(html, /data-pressure-result-state="unknown"/);
  assert.match(html, /氧化剂主泵<\/th><td>未记录<\/td><td>未记录<\/td>/);
  assert.match(html, /未记录/);
  delete record.result.calculation.steady_closure;
  assert.match(renderPressureResults(record, {}), /未保存逐支路压力分配数据/);
});

test("budget provenance distinguishes per-branch budgets from frozen resistance", () => {
  const record = fixture(), network = record.result.calculation.steady_closure.feed_network;
  network.design_loss_inputs = {branches:{ox_to_opb:{line_dp_ratio:.03, injector_ratio:.12}}};
  assert.match(renderPressureResults(record, record.result.calculation.inputs), /分支路预算定义不完整/);
  network.design_loss_inputs.branches = Object.fromEntries(pressureBranches.map(branch => [branch.id, {line_dp_ratio:.03, injector_ratio:.12}]));
  assert.match(renderPressureResults(record, record.result.calculation.inputs), /已保存四条支路各自采用的设计压降预算/);
  network.mode = "fixed_resistance";
  assert.match(renderPressureResults(record, record.result.calculation.inputs), /采用冻结支路阻力/);
});

test("failed records and raw diagnostic text are not promoted to valid results or HTML", () => {
  const record = fixture();
  record.status = "failed";
  record.revision = '<img src=x onerror="bad()">';
  record.result.calculation.steady_closure.feed_network.errors = ['<script>bad()</script>'];
  record.result.calculation.steady_closure.feed_network.branches[0].source_pressure_Pa = Infinity;
  const html = renderPressureResults(record, record.result.calculation.inputs);
  assert.match(html, /已保存数值仅供诊断/);
  assert.match(html, /&lt;script&gt;bad\(\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script|<img|Infinity|NaN/);
  record.result.calculation.inputs.cycle_type = "gg";
  assert.match(renderPressureResults(record, record.result.calculation.inputs), /不属于全流量补燃循环/);
});

// A minimal DOM adapter exercises the view state contract without loading the
// application, submitting a calculation, or requiring a browser dependency.
function node(dataset) {
  return {dataset, hidden:false, attributes:{}, setAttribute(key,value) { this.attributes[key]=value; }};
}
function workspaceFixture() {
  const panels = ["topology", "budget", "results"].map(value => node({pressurePanel:value}));
  const buttons = ["topology", "budget", "results"].map(value => node({pressureView:value}));
  const branches = [...pressureBranches.map(row => row.id), "ox_gas", "fuel_gas"];
  const branchPanels = branches.map(value => node({pressureBranchPanel:value}));
  const branchButtons = branches.map(value => node({pressureBranch:value}));
  const shared = {open:false}, reference = node({pressureReference:"pc", pressureUnit:"MPa"});
  const selectors = {"[data-pressure-panel]":panels, "[data-pressure-view]":buttons,
    "[data-pressure-branch-panel]":branchPanels, "[data-pressure-branch]":branchButtons,
    "[data-pressure-shared]":[shared], "[data-pressure-reference]":[reference]};
  const root = {matches:() => true, querySelectorAll:selector => selectors[selector] || [],
    classList:{toggle(){}}};
  return {root, panels, buttons, branchPanels, branchButtons, shared, reference};
}

test("switching tabs, selected branches, and search reveals fields without changing inputs", () => {
  const dom = workspaceFixture();
  syncPressureWorkspace(dom.root, {view:"results", branch:"fuel_gas", inputs:{pc:44e6}});
  assert.deepEqual(dom.panels.map(row => row.hidden), [true,true,false]);
  assert.equal(dom.branchPanels.find(row => row.dataset.pressureBranchPanel === "fuel_gas").hidden, false);
  assert.equal(dom.buttons[2].attributes["aria-pressed"], "true");
  assert.equal(dom.reference.textContent, "44 MPa");
  syncPressureWorkspace(dom.root, {view:"results", branch:"fuel_gas", search:true});
  assert.deepEqual(dom.panels.map(row => row.hidden), [true,false,true]);
  assert.ok(dom.branchPanels.every(row => !row.hidden));
  assert.equal(dom.shared.open, true);
  syncPressureWorkspace(dom.root, {view:"budget", branch:"ox_to_fpb"});
  assert.equal(dom.branchPanels.filter(row => !row.hidden).length, 1);
  assert.equal(dom.branchPanels.find(row => !row.hidden).dataset.pressureBranchPanel, "ox_to_fpb");
});
