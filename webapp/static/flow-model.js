// Read-only presentation of the solver's discrete FlowState records.
// The routes below follow src/cycle.py and src/gg_cycle.py. They describe
// recorded flow interfaces, never spatial samples or transient time steps.
const finite = value => typeof value === "number" && Number.isFinite(value);
const numeric = (value, scale = 1) => finite(value) && Number.isFinite(value * scale) ? value * scale : null;
const object = value => value !== null && typeof value === "object" && !Array.isArray(value) ? value : {};
const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);

const NODE_SPECS = {
  lox_tank: ["O01", "ox", "液氧贮箱"],
  lox_boost_out: ["O02", "ox", "液氧增压泵出口"],
  lox_pump_inlet: ["O03", "ox", "液氧泵入口（管路后）"],
  lox_after_pump: ["O04", "ox", "液氧泵出口"],
  fuel_tank: ["F01", "fuel", "燃料贮箱"],
  fuel_boost_out: ["F02", "fuel", "燃料增压泵出口"],
  fuel_pump_inlet: ["F03", "fuel", "燃料泵入口（管路后）"],
  fuel_after_pump: ["F04", "fuel", "燃料泵出口"],
  fuel_after_cooling: ["F05", "fuel", "燃料再生冷却出口"],
  ox_preburner_outlet: ["G01", "gas", "富氧预燃室出口（涡轮入口）"],
  ox_turbine_exhaust: ["G02", "gas", "富氧涡轮出口"],
  fuel_preburner_outlet: ["G03", "gas", "富燃预燃室出口（涡轮入口）"],
  fuel_turbine_exhaust: ["G04", "gas", "富燃涡轮出口"],
  gg_outlet: ["G05", "gas", "燃气发生器出口（涡轮入口）"],
  gg_turbine_exhaust: ["G06", "gas", "GG 涡轮排气"],
  main_chamber: ["C01", "chamber", "主燃烧室"],
};

const ROUTES = {
  ox: [["lox_tank", "lox_boost_out", "lox_pump_inlet", "lox_after_pump"]],
  fuel: [["fuel_tank", "fuel_boost_out", "fuel_pump_inlet", "fuel_after_pump", "fuel_after_cooling"]],
  gas: [["ox_preburner_outlet", "ox_turbine_exhaust"], ["fuel_preburner_outlet", "fuel_turbine_exhaust"], ["gg_outlet", "gg_turbine_exhaust"]],
  chamber: [["main_chamber"]],
};

const META = {
  phi: ["入口当量比 φ", "—"],
  fuel_fraction: ["入口燃料质量分数", "—"],
  gamma: ["比热比 γ", "—"],
  thermo_source: ["热力学计算来源", "—"],
  t_inlet_K: ["上游涡轮入口温度", "K"],
  pi_t: ["涡轮压比 p入口 / p出口", "—"],
  molar_mass: ["平均摩尔质量", "kg/kmol"],
  cp: ["定压比热 cp", "J/(kg·K)"],
  thermo_extrapolated_mass_fraction: ["热力学数据外推组分质量分数合计", "—"],
  thermo_common_max_temperature_K: ["组分热力学数据共同温度上限", "K"],
  thermo_extrapolated_species_mass_fractions: ["热力学数据外推组分及质量分数", "—"],
};

// Cloning also keeps nested, formerly raw diagnostic fields independent from
// the immutable calculation record. Rendering and escaping belong to the UI.
function copyValue(value) {
  if (typeof value === "number") return numeric(value);
  if (Array.isArray(value)) return value.map(copyValue);
  if (value !== null && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, copyValue(item)]));
  return value ?? null;
}

function metadataRows(metadata) {
  return Object.entries(object(metadata)).map(([key, original]) => {
    const [label, unit] = own(META, key) ? META[key] : [`原始字段 · ${key}`, "—"];
    const value = key === "thermo_source" && original === 1 ? "Cantera"
      : key === "thermo_source" && original === 0 ? "替代热力学模型"
      : copyValue(original);
    return {key, label, value, unit};
  });
}

function componentsFor(id, available, gg) {
  const candidates = {
    lox_pump_inlet: ["ox_pump"],
    fuel_pump_inlet: ["fuel_pump"],
    lox_after_pump: gg ? ["ox_pump", "gas_generator", "injector"] : ["ox_pump", "ox_preburner", "fuel_preburner"],
    fuel_after_pump: gg ? ["fuel_pump", "cooling_channel", "gas_generator"] : ["fuel_pump", "cooling_channel"],
    fuel_after_cooling: gg ? ["cooling_channel", "injector"] : ["cooling_channel", "ox_preburner", "fuel_preburner"],
    ox_preburner_outlet: ["ox_preburner", "ox_preburner_turbine"],
    fuel_preburner_outlet: ["fuel_preburner", "fuel_preburner_turbine"],
    ox_turbine_exhaust: ["ox_preburner_turbine", "injector"],
    fuel_turbine_exhaust: ["fuel_preburner_turbine", "injector"],
    gg_outlet: ["gas_generator", "gg_turbine", "gg_turbine_ox", "gg_turbine_fuel"],
    gg_turbine_exhaust: ["gg_turbine", "gg_turbine_ox", "gg_turbine_fuel", "gg_exhaust_nozzle"],
    main_chamber: ["injector", "main_chamber", "nozzle"],
  };
  return (own(candidates, id) ? candidates[id] : []).filter(key => available.has(key));
}

// Missing records break a route: filtering the list alone would misleadingly
// draw a direct connection across an unrecorded intermediate interface.
function presentSegments(route, present) {
  const segments = [];
  let segment = [];
  for (const id of route) {
    if (present.has(id)) segment.push(id);
    else if (segment.length) { segments.push(segment); segment = []; }
  }
  if (segment.length) segments.push(segment);
  return segments;
}

/**
 * Build engineering display data exclusively from a saved calculation.
 * Scalars: MPa, K, kg/s, kg/m³, kJ/kg. Missing/nonfinite values are null.
 * Composition retains original mass fractions, including incomplete sums;
 * it is never normalized, converted to mole fractions, or inferred.
 */
export function buildFlowModel(states, {labels = {}, inputs = {}, componentIds = []} = {}) {
  const source = object(states), names = object(labels), parameters = object(inputs);
  const available = new Set(Array.isArray(componentIds) ? componentIds.filter(id => typeof id === "string") : []);
  const fuel = parameters.propellant_pair === "lox_kerosene" ? "煤油" : "甲烷";
  const adaptLabel = value => fuel === "煤油"
    ? value.replace(/甲烷|燃料/g, "煤油").replace(/CH₄|CH4/g, "C₁₂H₂₆")
    : value.replace(/燃料/g, "甲烷");
  const gg = parameters.cycle_type === "gg" || own(source, "gg_outlet") || available.has("gas_generator");
  const splitGG = available.has("gg_turbine_ox") && available.has("gg_turbine_fuel");
  const known = Object.keys(NODE_SPECS).filter(id => own(source, id));
  const unknown = Object.keys(source).filter(id => !own(NODE_SPECS, id)).sort();
  const nodes = [...known, ...unknown].map(id => {
    const state = object(source[id]);
    const [code, group, fallback] = own(NODE_SPECS, id)
      ? NODE_SPECS[id] : [`U${String(unknown.indexOf(id) + 1).padStart(2, "0")}`, "other", typeof state.name === "string" && state.name ? state.name : id];
    const title = own(names, id) && typeof names[id] === "string" && names[id] ? names[id] : fallback;
    return {
      id, code, title: adaptLabel(title), group,
      pressure: numeric(state.pressure, 1e-6), temperature: numeric(state.temperature),
      massFlow: numeric(state.mass_flow), density: numeric(state.density), enthalpy: numeric(state.enthalpy, 1e-3),
      composition: Object.fromEntries(Object.entries(object(state.composition)).map(([key, value]) => [key, numeric(value)])),
      metadata: metadataRows(state.metadata), componentIds: componentsFor(id, available, gg),
    };
  });
  const discrete = "离散接口状态；节点间距不表示空间距离。";
  const definitions = [
    {id: "ox", title: "液氧供给", color: "#427ca4", caption: `${discrete}贮箱 → 增压泵出口 → 主泵入口 → 主泵出口。`},
    {id: "fuel", title: "燃料供给与冷却", color: "#b28b46", caption: `${discrete}${fuel}${fuel === "煤油" ? "（正十二烷替代物）" : ""}供给及再生冷却；分流前后质量流量可能不同。`},
    {id: "gas", title: "燃气流路", color: "#9270a8", caption: `${discrete}${gg
      ? splitGG ? "GG 燃气并联驱动双涡轮。排气记录为总质量流量，温度取液氧侧涡轮出口；并非混合温度，未提供两支路独立出口状态。"
        : "燃气发生器 → 涡轮排气；排气进入独立排气喷管，不进入主燃烧室。"
      : "富氧、富燃预燃室各自驱动对应涡轮；两条燃气支路分开展示，之后进入主燃烧室。"}`},
    {id: "chamber", title: "主燃烧室", color: "#b96359", caption: `${discrete}主燃烧室集总状态；不表示室内温度场或喷管沿程分布。`},
    {id: "other", title: "其他记录节点", color: "#778596", caption: `${discrete}连接关系未定义，各记录独立展示。`},
  ];
  const present = new Set(nodes.map(node => node.id));
  const groups = definitions.filter(group => nodes.some(node => node.group === group.id)).map(group => ({
    ...group, segments: group.id === "other" ? unknown.map(id => [id])
      : ROUTES[group.id].flatMap(route => presentSegments(route, present)),
  }));
  return {nodes, groups};
}
