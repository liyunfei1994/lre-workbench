import assert from "node:assert/strict";
import {test} from "node:test";
import {buildFlowModel} from "../webapp/static/flow-model.js";

const sample = (extra = {}) => ({pressure: 20e6, temperature: 110, mass_flow: 20,
  density: 450, enthalpy: -200e3, composition: {CH4: 1}, metadata: {}, ...extra});
const ffscIds = ["fuel_tank", "lox_tank", "fuel_boost_out", "lox_boost_out", "fuel_pump_inlet", "lox_pump_inlet",
  "lox_after_pump", "fuel_after_pump", "fuel_after_cooling", "ox_preburner_outlet", "fuel_preburner_outlet",
  "ox_turbine_exhaust", "fuel_turbine_exhaust", "main_chamber"];
const ffsc = () => Object.fromEntries(ffscIds.map(id => [id, sample({name: id})]));
const node = (model, id) => model.nodes.find(item => item.id === id);
const group = (model, id) => model.groups.find(item => item.id === id);
const freezeDeep = value => {
  if (value && typeof value === "object") { Object.values(value).forEach(freezeDeep); Object.freeze(value); }
  return value;
};

test("FFSC separates supply routes and both gas branches without invented nodes", () => {
  const model = buildFlowModel(ffsc());
  assert.equal(model.nodes.length, ffscIds.length);
  assert.deepEqual(model.groups.map(item => item.id), ["ox", "fuel", "gas", "chamber"]);
  assert.deepEqual(group(model, "gas").segments, [
    ["ox_preburner_outlet", "ox_turbine_exhaust"], ["fuel_preburner_outlet", "fuel_turbine_exhaust"],
  ]);
  assert.deepEqual(group(model, "chamber").segments, [["main_chamber"]]);
  assert.deepEqual(group(model, "fuel").segments[0].slice(-2), ["fuel_after_pump", "fuel_after_cooling"]);
  for (const entry of model.groups) assert.match(entry.caption, /离散接口状态.*不表示空间距离/);
  assert.equal(node(model, "lox_tank").code, "O01");
  assert.equal(node(model, "main_chamber").code, "C01");
});

test("GG exhaust stays distinct from the main combustion flow", () => {
  const model = buildFlowModel({gg_outlet: sample(), gg_turbine_exhaust: sample(), main_chamber: sample()}, {
    inputs: {cycle_type: "gg"}, componentIds: ["gas_generator", "gg_turbine", "gg_exhaust_nozzle", "main_chamber", "nozzle"],
  });
  assert.deepEqual(group(model, "gas").segments, [["gg_outlet", "gg_turbine_exhaust"]]);
  assert.deepEqual(node(model, "gg_turbine_exhaust").componentIds, ["gg_turbine", "gg_exhaust_nozzle"]);
  assert.ok(!node(model, "gg_turbine_exhaust").componentIds.includes("main_chamber"));
  assert.match(group(model, "gas").caption, /不进入主燃烧室/);
});

test("dual-shaft GG exposes the solver aggregate limitation and does not synthesize branch states", () => {
  const states = {gg_outlet: sample(), gg_turbine_exhaust: sample({temperature: 620, mass_flow: 5})};
  const model = buildFlowModel(states, {componentIds: ["gas_generator", "gg_turbine_ox", "gg_turbine_fuel", "gg_exhaust_nozzle"]});
  assert.equal(model.nodes.length, 2);
  assert.equal(node(model, "gg_turbine_exhaust").temperature, 620);
  assert.equal(node(model, "gg_turbine_exhaust").massFlow, 5);
  assert.deepEqual(node(model, "gg_outlet").componentIds, ["gas_generator", "gg_turbine_ox", "gg_turbine_fuel"]);
  assert.match(group(model, "gas").caption, /温度取液氧侧涡轮出口/);
  assert.match(group(model, "gas").caption, /并非混合温度/);
});

test("display units change only dimensional scalars, including negative enthalpy", () => {
  const model = buildFlowModel({lox_after_pump: sample({pressure: 69.892e6, enthalpy: -150049.1, density: 1272.97})});
  const actual = node(model, "lox_after_pump");
  assert.equal(actual.pressure, 69.892);
  assert.equal(actual.temperature, 110);
  assert.equal(actual.massFlow, 20);
  assert.equal(actual.density, 1272.97);
  assert.equal(actual.enthalpy, -150.0491);
});

test("missing and nonfinite numbers remain unavailable rather than becoming zero", () => {
  const model = buildFlowModel({fuel_tank: sample({pressure: "1000000", temperature: NaN, mass_flow: Infinity,
    density: undefined, enthalpy: -Infinity, composition: {CH4: NaN, O2: Infinity, CO2: "0.5"}})});
  const actual = node(model, "fuel_tank");
  for (const key of ["pressure", "temperature", "massFlow", "density", "enthalpy"]) assert.equal(actual[key], null);
  assert.deepEqual(actual.composition, {CH4: null, O2: null, CO2: null});
  const zero = buildFlowModel({fuel_tank: sample({pressure: 0, temperature: 0, enthalpy: 0})}).nodes[0];
  assert.equal(zero.pressure, 0); assert.equal(zero.temperature, 0); assert.equal(zero.enthalpy, 0);
});

test("a missing intermediate state breaks the displayed route without bridging", () => {
  const model = buildFlowModel({lox_tank: sample(), lox_pump_inlet: sample(), lox_after_pump: sample()});
  assert.deepEqual(group(model, "ox").segments, [["lox_tank"], ["lox_pump_inlet", "lox_after_pump"]]);
  assert.deepEqual(model.nodes.map(item => item.code), ["O01", "O03", "O04"]);
});

test("kerosene names follow saved input while retaining exact surrogate species keys", () => {
  const model = buildFlowModel({fuel_boost_out: sample({composition: {"c12h26": 1}}), gg_outlet: sample()}, {
    inputs: {propellant_pair: "lox_kerosene", cycle_type: "gg"}, labels: {fuel_boost_out: "甲烷增压泵出口"},
  });
  assert.equal(node(model, "fuel_boost_out").title, "煤油增压泵出口");
  assert.deepEqual(node(model, "fuel_boost_out").composition, {c12h26: 1});
  assert.match(group(model, "fuel").caption, /正十二烷替代物/);
  assert.equal(buildFlowModel({fuel_tank: sample()}).nodes[0].title, "甲烷贮箱");
});

test("composition stays in original mass fractions without dropping traces or normalization", () => {
  const composition = {CH4: .823, H2O: .068, CH2O: 5.264e-8, unknown_species: .02, omitted_mass: -0.01};
  const model = buildFlowModel({fuel_preburner_outlet: sample({composition})});
  assert.deepEqual(model.nodes[0].composition, composition);
  assert.notEqual(model.nodes[0].composition, composition);
  assert.notEqual(Object.values(model.nodes[0].composition).reduce((a, b) => a + b, 0), 1);
});

test("metadata distinguishes inlet mixture quantities, backend sources and unknown raw fields", () => {
  const model = buildFlowModel({gg_outlet: sample({metadata: {phi: 25, fuel_fraction: .865, gamma: 1.15,
    thermo_source: 1, t_inlet_K: 850, pi_t: 20, molar_mass: 17, cp: 3700,
    custom_diagnostic: {nested: [1, "keep me"]}}})});
  const meta = Object.fromEntries(model.nodes[0].metadata.map(item => [item.key, item]));
  assert.equal(meta.fuel_fraction.label, "入口燃料质量分数");
  assert.equal(meta.fuel_fraction.value, .865);
  assert.equal(meta.thermo_source.value, "Cantera");
  assert.equal(meta.t_inlet_K.unit, "K");
  assert.equal(meta.cp.unit, "J/(kg·K)");
  assert.equal(meta.molar_mass.unit, "kg/kmol");
  assert.match(meta.custom_diagnostic.label, /^原始字段/);
  assert.deepEqual(meta.custom_diagnostic.value, {nested: [1, "keep me"]});
  const fallback = buildFlowModel({main_chamber: sample({metadata: {thermo_source: 0}})});
  assert.equal(fallback.nodes[0].metadata[0].value, "替代热力学模型");
  const unknown = buildFlowModel({main_chamber: sample({metadata: {thermo_source: 7}})});
  assert.equal(unknown.nodes[0].metadata[0].value, 7);
});

test("component links refer only to adjacent components actually present in the result", () => {
  const available = ["ox_pump", "fuel_pump", "cooling_channel", "gas_generator", "injector", "unrelated"];
  const model = buildFlowModel({lox_after_pump: sample(), fuel_after_pump: sample(), fuel_after_cooling: sample(), lox_tank: sample()}, {
    inputs: {cycle_type: "gg"}, componentIds: available,
  });
  assert.deepEqual(node(model, "lox_after_pump").componentIds, ["ox_pump", "gas_generator", "injector"]);
  assert.deepEqual(node(model, "fuel_after_cooling").componentIds, ["cooling_channel", "injector"]);
  assert.deepEqual(node(model, "lox_tank").componentIds, []);
  for (const entry of model.nodes) assert.ok(entry.componentIds.every(id => available.includes(id) && id !== "unrelated"));
  assert.deepEqual(buildFlowModel({lox_after_pump: sample()}).nodes[0].componentIds, []);
});

test("unknown nodes stay isolated with visible original identities and text for UI escaping", () => {
  const model = buildFlowModel({new_interface: sample({name: "<custom>"}), another: sample()}, {
    labels: {another: '<script>alert("label")</script>'}, componentIds: ["new_interface"],
  });
  assert.deepEqual(model.nodes.map(entry => entry.code), ["U01", "U02"]);
  assert.deepEqual(group(model, "other").segments, [["another"], ["new_interface"]]);
  assert.equal(node(model, "new_interface").title, "<custom>");
  assert.equal(node(model, "another").title, '<script>alert("label")</script>');
  assert.deepEqual(node(model, "new_interface").componentIds, []);
  const hostileKey = buildFlowModel(JSON.parse('{"__proto__":{"pressure":1000000,"name":"original node"}}'));
  assert.equal(hostileKey.nodes[0].id, "__proto__");
  assert.equal(hostileKey.nodes[0].code, "U01");
});

test("empty inputs are supported and records are not mutated or retained by reference", () => {
  assert.deepEqual(buildFlowModel(null), {nodes: [], groups: []});
  const states = freezeDeep({fuel_tank: sample({metadata: {nested: {array: [1, 2]}}})});
  const options = freezeDeep({inputs: {propellant_pair: "lox_kerosene"}, labels: {fuel_tank: "燃料贮箱"}, componentIds: []});
  const snapshot = JSON.stringify({states, options});
  const model = buildFlowModel(states, options);
  model.nodes[0].metadata[0].value.array.push(3);
  model.nodes[0].composition.CH4 = .5;
  assert.equal(JSON.stringify({states, options}), snapshot);
});
