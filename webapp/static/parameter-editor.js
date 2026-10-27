// Presentation rules never migrate stored EngineInputs. Only an explicit mode
// change writes the two legacy flags, keeping drafts, imports and undo lossless.
const escape = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
export const PREBURNER_MODE_KEY = "preburner_design_mode";

export function savedInputDocument(value) {
  // Recognized desktop files and previous web exports have snapshot identity.
  // A bare object or an arbitrary {inputs: ...} parameter patch does not.
  return Boolean(value && typeof value === "object" && value.inputs &&
    ((["ffsc.workbench.case", "lre.workbench.inputs"].includes(value.format) && value.schema_version === 1) ||
      (!value.format && typeof value.project_name === "string" && Number.isInteger(value.base_revision) &&
        value.base_revision > 0 && typeof value.unsaved === "boolean")));
}

export function mergeInputDefaults(defaults, inputs, savedSnapshot = false) {
  const result = {...defaults, ...inputs};
  if(Object.hasOwn(defaults,"nozzle_contour_method") && !Object.hasOwn(inputs,"nozzle_contour_method")) result.nozzle_contour_method="legacy_log_v1";
  if (savedSnapshot && Object.hasOwn(defaults, "methane_thermo_dataset") && !Object.hasOwn(inputs, "methane_thermo_dataset")) result.methane_thermo_dataset = "gri30";
  if(Object.hasOwn(defaults,"wall_thermal_dataset_id") && !Object.hasOwn(inputs,"wall_thermal_dataset_id") &&
      (savedSnapshot || Object.hasOwn(inputs,"chamber_inner_wall_material"))) {
    const supported={grcop84:"grcop84_ellis2000_extruded_high_fe_v1",cuzr:"cuzr_qi2024_heat_treated_v1"};
    result.wall_thermal_dataset_id=supported[inputs.chamber_inner_wall_material || defaults.chamber_inner_wall_material] || "";
  }
  return result;
}

export function methaneThermoSummary(inputs, catalog = []) {
  const id = inputs.methane_thermo_dataset ?? "gri30";
  const dataset = catalog.find(item => item.id === id);
  const names = {gri30:"GRI30（历史数据）", gri30_highT:"GRI30 high-T"};
  const legacyLoss = (inputs.combustion_loss_model ?? "legacy_temperature_rise") === "legacy_temperature_rise";
  const spatialCooling = Boolean(inputs.cooling_bartz_mode);
  const chamber = legacyLoss ? "焓平衡后温升折减（历史模型）" :
    inputs.combustion_loss_model === "adiabatic" ?
      (spatialCooling ? "焓约束化学平衡" : "焓约束化学平衡（扣除给定冷却吸热）") : "未识别的燃烧损失模型";
  const rows = [["甲烷热化学", names[id] || `未识别：${id}`], ["燃烧室", chamber],
    ["主喷管热侧", spatialCooling ? "冻结组成 · 沿程冷却" : "冻结组成 · 绝热膨胀"],
    ["燃气状态方程", dataset?.gas_eos === "ideal-gas" ? "理想气体混合物" : dataset?.gas_eos || "数据未读取"]];
  const range = dataset?.common_temperature_range_K;
  const rangeText = Array.isArray(range) && range.length === 2 && range.every(Number.isFinite) ? `${range[0]}–${range[1]} K` : "未读取";
  const source = /^https:\/\//i.test(dataset?.source_url || "") ?
    `<a href="${escape(dataset.source_url)}" target="_blank" rel="noopener noreferrer">${escape(dataset.source_label || "数据来源")}</a>` : "来源未读取";
  const update = id === "gri30_highT" && dataset?.file_description?.includes("NASA TM-4513") ?
    "文件说明：Joseph Shepherd 依据 NASA TM-4513 更新高温热力学多项式，并非仅放宽温区。" : "";
  const historical = id === "gri30";
  const details = `<p class="fine">当前参数所用文件：${escape(dataset?.file || id)}；Cantera ${escape(dataset?.cantera_version || "版本未读取")}。${source}</p>` +
    `<p class="fine">${escape(dataset?.species_count ?? "未读取")} 种物种声明温区的交集：${escape(rangeText)}。交集外需逐物种检查，不代表所有物种都已越界。${escape(update)}</p>` +
    '<p class="fine">高温热数据提供物种焓、熵和比热；不能据此认定高压状态方程、反应速率、输运或积碳模型已验证。系统纯流体物性使用 CoolProp 的 HEOS 模型；REFPROP 对照在“热力学校核”中单独进行。</p>' +
    (legacyLoss ? '<p class="fine">此方案保留历史温升折减；升级热化学数据不会自动改变该模型。</p>' : "") +
    (historical ? '<p class="fine">旧方案继续使用其原数据复现。使用 high-T 时先建立草稿，点击“保存方案”生成新版本；旧版本和已保存结果保留。</p>' : '<p class="fine">此摘要对应当前参数。已保存结果使用各自记录的数据版本，修改后需重新计算。</p>');
  return {summary: rows.map(([label,value]) => `<p class="fine"><b>${escape(label)}：</b>${escape(value)}</p>`).join(""), details, historical};
}

export function upgradeMethaneDatasetDraft(inputs) {
  if ((inputs.propellant_pair || "lox_methane") !== "lox_methane" || (inputs.methane_thermo_dataset ?? "gri30") !== "gri30") return inputs;
  return {...inputs, methane_thermo_dataset:"gri30_highT"};
}

export function upgradeCoolingModelDraft(inputs) {
  if ((inputs.propellant_pair || "lox_methane") !== "lox_methane" || inputs.cooling_bartz_mode !== 0) return inputs;
  return {...inputs, cooling_bartz_mode:1};
}

function renderCoolingModel(inputs) {
  const spatial=typeof inputs.cooling_bartz_mode === "number" && Number.isFinite(inputs.cooling_bartz_mode) && inputs.cooling_bartz_mode>0;
  const budget=inputs.cooling_bartz_mode===0;
  if(spatial)return '<div class="cooling-model-summary"><b>统一准一维冷却</b><p class="field-help">吸热与压降由模型计算；型面、通道和材料分别在对应分组设置。输出沿程状态，并与热侧和循环迭代闭合。</p><p class="field-help">结果区的模型摘要对应所选计算记录；修改参数后需运行新设计点。</p></div>';
  if(!budget)return '<p class="notice warning">冷却模型标识缺失或无效；请检查保存的方案。</p>';
  const methane=(inputs.propellant_pair || "lox_methane")==="lox_methane";
  return '<div class="cooling-model-summary"><b>历史兼容：给定吸热与压降预算</b><p class="field-help">本方案保留原计算方式和预算参数，没有沿程冷却解；加载、保存和查看记录不会自动转换模型。</p>'+
    (methane?'<button type="button" data-upgrade-cooling>建立准一维冷却草稿</button><p class="field-help">切换后请核对型面、通道与材料，保存为新版本并重新计算。既有记录保留。</p>':'<p class="field-help">煤油替代工质尚未接入统一准一维冷却；当前仅保留预算模型兼容能力。</p>')+'</div>';
}
const ROLE_LABELS = {
  design_input:"设计输入", boundary:"边界条件", model_assumption:"模型假设",
  constraint:"约束", initial_guess:"迭代初值", component_sizing:"部件选型",
  solver_setting:"求解设置", legacy:"历史兼容", result:"计算结果",
};

export function preburnerMode(inputs) {
  if (Number(inputs.use_target_preburner_temp ?? 1) >= .5) return "temperature";
  if (Number(inputs.use_target_preburner_mr ?? 1) >= .5) return "mixture_ratio";
  return "legacy_free_split";
}

export function selectPreburnerMode(inputs, mode) {
  if (!["temperature", "mixture_ratio"].includes(mode)) throw new Error("请选择指定预燃室温度或指定预燃室混合比。");
  return {...inputs, use_target_preburner_temp:mode === "temperature" ? 1 : 0,
    use_target_preburner_mr:1};
}

export function parameterCompatible(item, inputs) {
  return (!item.cycles || item.cycles.includes(inputs.cycle_type)) &&
    (!item.propellants || item.propellants.includes(inputs.propellant_pair || "lox_methane"));
}

function conditionMatches(condition, inputs) {
  const actual = preburnerStatusValue(condition.key, inputs[condition.key]);
  // Do not trim: even whitespace or "[]" selects the table path in the solver
  // and must be surfaced for correction, never silently treated as scalar input.
  if (condition.op === "empty") return actual == null || actual === "";
  if (condition.op === "eq") return actual === condition.value;
  if (condition.op === "gt") return typeof actual === "number" && actual > condition.value;
  if (condition.op === "lte") return typeof actual === "number" && actual <= condition.value;
  return false;
}

function preburnerStatusValue(key, value) {
  // Old snapshots had numeric limits but no evidence classification. Rendering
  // their controls as provisional preserves the saved values without a migration.
  return /^(ox|fuel)_preburner_temp_limit_status$/.test(key) && value == null ? "provisional" : value;
}

export function parameterVisible(field, inputs) {
  if (field.hidden || !parameterCompatible(field, inputs)) return false;
  const rules = field.visibility;
  if (!rules) return true;
  if (rules.preburner_modes && !rules.preburner_modes.includes(preburnerMode(inputs))) return false;
  if (rules.all && !rules.all.every(condition => conditionMatches(condition, inputs))) return false;
  if (rules.any && !rules.any.some(condition => conditionMatches(condition, inputs))) return false;
  return true;
}

export function parameterPresentation(field, inputs) {
  const mode = preburnerMode(inputs), override = field.roles_by_mode?.[mode];
  const role = typeof override === "string" ? override : override?.role || field.role;
  const label = field.labels_by_mode?.[mode] || field.label;
  const roleLabel = (typeof override === "object" && override?.role_label) ||
    (override ? ROLE_LABELS[role] : field.role_label) || ROLE_LABELS[role] || "";
  return {...field, role, role_label:roleLabel, label,
    help:field.help_by_mode?.[mode] || field.help || field.tooltip || ""};
}

export function parameterValue(field, inputs) {
  if (field.key === PREBURNER_MODE_KEY && field.virtual) return preburnerMode(inputs);
  const value = preburnerStatusValue(field.key, inputs[field.key]);
  return value == null ? "" : typeof value === "number" ? Number((value * (field.factor || 1)).toPrecision(12)) : value;
}

export function parameterLayoutDependency(key, groups) {
  return [PREBURNER_MODE_KEY, "cycle_type", "propellant_pair", "wall_thermal_dataset_id", "wall_manufacturing_state"].includes(key) || groups.some(group => group.fields.some(field =>
    [...(field.visibility?.all || []), ...(field.visibility?.any || [])].some(condition => condition.key === key)));
}

function renderWallDatasetInfo(field, inputs) {
  if(field.key!=="wall_thermal_dataset_id" || !Array.isArray(field.dataset_catalog))return "";
  const dataset=field.dataset_catalog.find(item=>item.dataset_id===inputs.wall_thermal_dataset_id);
  if(!dataset)return '<p class="field-help">当前数据集未识别；不会自动换成另一种材料。</p>';
  const state=inputs.wall_manufacturing_state || "unspecified";
  const manufacturing=dataset.manufacturing_state || {},expected=manufacturing.state_id;
  const match=state==="unspecified" ? "方案制造状态未确认" : state===expected ? "方案制造状态与数据匹配" : "方案制造状态与数据不匹配";
  const conductivity=dataset.conductivity_property || {};
  const range=conductivity.range_K;
  const rangeText=Array.isArray(range) && range.length===2 && range.every(Number.isFinite) ? `${range[0]}–${range[1]} K` : "未登记";
  const source=dataset.sources?.[0] || {};
  const sourceLabel=source.title || dataset.source_title || "未登记";
  const url=source.url || dataset.source_url || "";
  const sourceHtml=/^https:\/\//i.test(url) ? `<a href="${escape(url)}" target="_blank" rel="noopener noreferrer">${escape(sourceLabel)}</a>` : escape(sourceLabel);
  return `<p class="field-help wall-dataset-summary">导热率数据温区：${escape(rangeText)}。${escape(match)}。稳态计算使用 k(T)。</p><details class="wall-dataset-evidence"><summary>数据来源与制造状态</summary><p class="fine">${escape(manufacturing.label || "制造状态未登记")}</p><p class="fine">${sourceHtml}</p><p class="fine">数据版本：${escape(dataset.dataset_id)}。比热与密度的登记状态见计算记录；登记并不代表已启用壁体储热瞬态。数据温区不是材料允许使用温度。</p></details>`;
}

export function parameterSearchText(field, inputs, groupTitle = "") {
  const view = parameterPresentation(field, inputs);
  return [view.label, view.full_label, view.key, view.unit, view.role_label, view.help,
    view.provenance?.label, view.provenance?.detail, groupTitle].join(" ").toLocaleLowerCase();
}

export function renderParameterField(field, inputs) {
  const view = parameterPresentation(field, inputs), value = parameterValue(view, inputs);
  if(view.read_only && view.key==="cooling_bartz_mode")return `<div data-field="cooling_bartz_mode" data-role="model_assumption"><span class="parameter-label">${escape(view.label)}</span>${renderCoolingModel(inputs)}</div>`;
  const virtualMode = view.virtual && view.key === PREBURNER_MODE_KEY;
  const legacy = virtualMode && value === "legacy_free_split";
  const helpId = `parameter-help-${view.key}`;
  const help = view.help ? `<span class="field-help" id="${escape(helpId)}">${escape(view.help)}</span>` : "";
  const evidence = view.provenance?.label ? `<span class="field-provenance" title="${escape(view.provenance.detail || view.provenance.label)}">依据：${escape(view.provenance.label)}</span>` : "";
  const options = view.choices || [];
  const control = view.choices ? `<select data-input="${escape(view.key)}"${help ? ` aria-describedby="${escape(helpId)}"` : ""}>` +
    (legacy ? '<option value="legacy_free_split" selected disabled>历史模式：自由分流（保留）</option>' :
      options.some(choice => String(value) === String(choice.value)) ? "" : `<option value="" disabled selected>${escape(value==="" && view.empty_choice_label ? view.empty_choice_label : `无效值：${value}`)}</option>`) +
    options.map(choice => `<option value="${escape(choice.value)}"${String(value) === String(choice.value) ? " selected" : ""}>${escape(choice.label)}</option>`).join("") + "</select>" :
    view.multiline ? `<textarea rows="5" data-input="${escape(view.key)}"${help ? ` aria-describedby="${escape(helpId)}"` : ""}>${escape(value)}</textarea>` :
    `<input type="${typeof view.default === "number" ? "number" : "text"}"${typeof view.default === "number" ? ' step="any" required' : ""} data-input="${escape(view.key)}" value="${escape(value)}"${help ? ` aria-describedby="${escape(helpId)}"` : ""}>`;
  return `<label data-field="${escape(view.key)}" data-role="${escape(view.role || "")}" title="${escape(view.full_label || view.label)}"><span class="parameter-label">${escape(view.label)}${view.unit ? ` <span class="unit">[${escape(view.unit)}]</span>` : ""}</span>${view.role_label ? `<span class="parameter-role">${escape(view.role_label)}</span>` : ""}${control}${help}${evidence}${legacy ? '<span class="parameter-legacy-note" role="note">当前方案保留旧版自由分流模式与参数。选择指定温度或指定混合比后才转换；加载时不会自动转换。</span>' : ""}</label>${renderWallDatasetInfo(view,inputs)}`;
}

export function parameterGroups(groups, inputs) {
  return groups.filter(group => parameterCompatible(group, inputs)).map(group => ({...group,
    fields:group.fields.filter(field => parameterVisible(field, inputs)),
  })).filter(group => group.fields.length);
}

function renderChannelGeometryInfo(group, inputs) {
  if (!group.geometry_source_key || !(inputs.cooling_bartz_mode > 0)) return "";
  const raw = inputs[group.geometry_source_key];
  if (raw == null || raw === "") {
    const depth = inputs.cooling_channel_auto_size > 0 ? "入口速度估算深度" : "指定深度";
    return `<p class="fine channel-geometry-source">几何来源：标量规则（通道宽随型面半径变化，${depth}）。</p>`;
  }
  const note = '<p class="fine">当前保存的沿程表覆盖通道数、宽度、深度和热壁厚，自动定深与目标流速不生效。被覆盖的标量仍保留在方案中。此处只读展示原表；保存不会改写它。</p>';
  let rows;
  try {
    const data = JSON.parse(raw);
    rows = Array.isArray(data) ? data : data?.rows;
    if (!Array.isArray(rows) || !rows.length || rows.some(row => !row || typeof row !== "object" || Array.isArray(row))) throw new Error("invalid table");
  } catch {
    return `<div class="channel-geometry-source"><p class="notice warning">几何来源：沿程通道表，但保存内容无法展示为非空断点表。需修复原表后计算；不会自动回退为标量几何。</p>${note}</div>`;
  }
  const anchors = {injector:"喷注面", conv_start:"收敛起点", throat:"喉部", exit:"主喷管出口", x_mm:"轴向坐标"};
  const cell = (row, key) => Object.hasOwn(row, key) ? escape(row[key]) : "未填写";
  const body = rows.map(row => `<tr><td>${escape(anchors[row.anchor] || row.anchor || "未填写")}</td><td>${cell(row,"x_mm")}</td><td>${cell(row,"n")}</td><td>${cell(row,"w_mm")}</td><td>${cell(row,"h_mm")}</td><td>${cell(row,"t_mm")}</td></tr>`).join("");
  return `<div class="channel-geometry-source"><p class="fine">几何来源：沿程通道表（${rows.length} 个断点）。</p>${note}<details><summary>查看保存的通道几何</summary><p class="fine">x 从喷注面起算；物理锚点随型面定位。宽、深、壁厚沿 x 线性插值，表两端之外沿用端值。未填列采用解析器默认值，不继承被覆盖的标量；当前内核要求各断点通道数相同。几何适用性由运行时统一校验。</p><div class="table-wrap"><table><thead><tr><th>定位</th><th>x / mm</th><th>N</th><th>宽 / mm</th><th>深 / mm</th><th>壁厚 / mm</th></tr></thead><tbody>${body}</tbody></table></div></details></div>`;
}

export function renderParameterGroups(groups, inputs, expanded = new Set()) {
  return groups.map(group => {
    const standard = [], seeds = [], numeric = [];
    for (const field of group.fields) {
      (parameterPresentation(field, inputs).role === "initial_guess" ? seeds : field.advanced ? numeric : standard).push(field);
    }
    const advanced = seeds.length ? `<details class="parameter-initials" data-initials="${escape(group.id)}"${expanded.has(group.id) ? " open" : ""}><summary>高级设置 · 迭代初值（${seeds.length}）</summary><p class="fine">初值用于启动求解。相同物理解分支充分收敛后，结果应在容差范围内一致。</p>${seeds.map(field => renderParameterField(field, inputs)).join("")}</details>` : "";
    const numericKey = group.id + "-numerics";
    const numericOnly=numeric.every(field=>field.role==="solver_setting");
    const advancedTitle=group.advanced_title || (numericOnly ? "数值分辨率" : "补充设置");
    const advancedHelp=group.advanced_help || (numericOnly ? "分辨率影响离散误差；应比较网格加密结果，不能用作性能调参。" : "这些输入参与模型配置；完整方案与计算记录保留实际取值。");
    const numerics = numeric.length ? `<details class="parameter-initials" data-initials="${escape(numericKey)}"${expanded.has(numericKey) ? " open" : ""}><summary>高级设置 · ${escape(advancedTitle)}（${numeric.length}）</summary><p class="fine">${escape(advancedHelp)}</p>${numeric.map(field => renderParameterField(field, inputs)).join("")}</details>` : "";
    const contexts=Array.isArray(group.configuration_context) ? group.configuration_context : group.configuration_context ? [group.configuration_context] : [];
    const contextText=contexts.map(context=>`<p class="fine wall-configuration-context">${escape(context.label)}：${escape(context.labels?.[inputs[context.key]] || inputs[context.key] || "未指定")}。${escape(context.note)}</p>`).join("");
    return `<fieldset class="parameter-section" data-group="${escape(group.id)}"><legend>${escape(group.title)}</legend>${group.description ? `<p class="fine">${escape(group.description)}</p>` : ""}${renderChannelGeometryInfo(group, inputs)}${renderParameterSections(group, standard, inputs)}${contextText}${advanced}${numerics}</fieldset>`;
  }).join("");
}

function renderParameterSections(group, fields, inputs) {
  if (!Array.isArray(group.sections)) return fields.map(field=>renderParameterField(field,inputs)).join("");
  const rendered=new Set(), sections=[];
  for (const section of group.sections) {
    const keys=new Set(Array.isArray(section.keys)?section.keys:[]);
    const selected=fields.filter(field=>keys.has(field.key)&&!rendered.has(field.key));
    if (!selected.length) continue;
    selected.forEach(field=>rendered.add(field.key));
    sections.push(`<section class="parameter-subsection" data-parameter-section="${escape(section.id)}"><h3>${escape(section.title)}</h3>${section.description?`<p class="fine">${escape(section.description)}</p>`:""}${selected.map(field=>renderParameterField(field,inputs)).join("")}</section>`);
  }
  // Search filtering and future schema additions must never drop an otherwise
  // visible control merely because it is not assigned to a subsection yet.
  return sections.join("")+fields.filter(field=>!rendered.has(field.key)).map(field=>renderParameterField(field,inputs)).join("");
}

export function renderParameterEvidence(evidence, fields = []) {
  if (!Array.isArray(evidence) || !evidence.length) return "";
  const names = {published:"厂商公开值",engineering_assumption:"工程假设",numerical_initial_guess:"数值初值"};
  const rows = evidence.map(item => {
    const field = fields.find(candidate => candidate.key === item.name);
    const value = typeof item.value_si === "number" && Number.isFinite(item.value_si) ?
      (item.value_si * (field?.factor || 1)).toLocaleString("zh-CN",{maximumSignificantDigits:10}) : "—";
    const source = /^https:\/\//i.test(item.source_url || "") ? `<a href="${escape(item.source_url)}" target="_blank" rel="noopener noreferrer">原始来源</a>` : "";
    const dates = [item.source_date ? `发布 ${item.source_date}` : "", item.checked_on ? `核对 ${item.checked_on}` : ""].filter(Boolean).join(" · ");
    return `<tr><td>${escape(field?.label || item.name)}</td><td>${escape(value)}${field?.unit ? ` ${escape(field.unit)}` : ""}</td><td>${escape(names[item.classification] || "待核实")}${source ? `<br>${source}` : ""}${dates ? `<small class="field-provenance">${escape(dates)}</small>` : ""}</td><td>${escape(item.note || "")}</td></tr>`;
  }).join("");
  return `<details class="reference-evidence"><summary>默认示例参数依据（${evidence.length}）</summary><p class="fine">以下依据对应可加载的示例；当前方案经过修改的值以保存的输入为准。</p><div class="table-wrap"><table><thead><tr><th>参数</th><th>示例值</th><th>性质与来源</th><th>说明</th></tr></thead><tbody>${rows}</tbody></table></div></details>`;
}
