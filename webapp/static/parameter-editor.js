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
  if (savedSnapshot && Object.hasOwn(defaults, "methane_thermo_dataset") && !Object.hasOwn(inputs, "methane_thermo_dataset")) result.methane_thermo_dataset = "gri30";
  return result;
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
  const actual = inputs[condition.key];
  if (condition.op === "eq") return actual === condition.value;
  if (condition.op === "gt") return typeof actual === "number" && actual > condition.value;
  if (condition.op === "lte") return typeof actual === "number" && actual <= condition.value;
  return false;
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
  const value = inputs[field.key];
  return value == null ? "" : typeof value === "number" ? Number((value * (field.factor || 1)).toPrecision(12)) : value;
}

export function parameterLayoutDependency(key, groups) {
  return [PREBURNER_MODE_KEY, "cycle_type", "propellant_pair"].includes(key) || groups.some(group => group.fields.some(field =>
    [...(field.visibility?.all || []), ...(field.visibility?.any || [])].some(condition => condition.key === key)));
}

export function parameterSearchText(field, inputs, groupTitle = "") {
  const view = parameterPresentation(field, inputs);
  return [view.label, view.full_label, view.key, view.unit, view.role_label, view.help,
    view.provenance?.label, view.provenance?.detail, groupTitle].join(" ").toLocaleLowerCase();
}

export function renderParameterField(field, inputs) {
  const view = parameterPresentation(field, inputs), value = parameterValue(view, inputs);
  const virtualMode = view.virtual && view.key === PREBURNER_MODE_KEY;
  const legacy = virtualMode && value === "legacy_free_split";
  const helpId = `parameter-help-${view.key}`;
  const help = view.help ? `<span class="field-help" id="${escape(helpId)}">${escape(view.help)}</span>` : "";
  const evidence = view.provenance?.label ? `<span class="field-provenance" title="${escape(view.provenance.detail || view.provenance.label)}">依据：${escape(view.provenance.label)}</span>` : "";
  const options = view.choices || [];
  const control = view.choices ? `<select data-input="${escape(view.key)}"${help ? ` aria-describedby="${escape(helpId)}"` : ""}>` +
    (legacy ? '<option value="legacy_free_split" selected disabled>历史模式：自由分流（保留）</option>' :
      options.some(choice => String(value) === String(choice.value)) ? "" : `<option value="" disabled selected>无效值：${escape(value)}</option>`) +
    options.map(choice => `<option value="${escape(choice.value)}"${String(value) === String(choice.value) ? " selected" : ""}>${escape(choice.label)}</option>`).join("") + "</select>" :
    `<input type="${typeof view.default === "number" ? "number" : "text"}"${typeof view.default === "number" ? ' step="any" required' : ""} data-input="${escape(view.key)}" value="${escape(value)}"${help ? ` aria-describedby="${escape(helpId)}"` : ""}>`;
  return `<label data-field="${escape(view.key)}" data-role="${escape(view.role || "")}" title="${escape(view.full_label || view.label)}"><span class="parameter-label">${escape(view.label)}${view.unit ? ` <span class="unit">[${escape(view.unit)}]</span>` : ""}</span>${view.role_label ? `<span class="parameter-role">${escape(view.role_label)}</span>` : ""}${control}${help}${evidence}${legacy ? '<span class="parameter-legacy-note" role="note">当前方案保留旧版自由分流模式与参数。选择指定温度或指定混合比后才转换；加载时不会自动转换。</span>' : ""}</label>`;
}

export function parameterGroups(groups, inputs) {
  return groups.filter(group => parameterCompatible(group, inputs)).map(group => ({...group,
    fields:group.fields.filter(field => parameterVisible(field, inputs)),
  })).filter(group => group.fields.length);
}

export function renderParameterGroups(groups, inputs, expanded = new Set()) {
  return groups.map(group => {
    const standard = [], seeds = [], numeric = [];
    for (const field of group.fields) {
      (parameterPresentation(field, inputs).role === "initial_guess" ? seeds : field.advanced ? numeric : standard).push(field);
    }
    const advanced = seeds.length ? `<details class="parameter-initials" data-initials="${escape(group.id)}"${expanded.has(group.id) ? " open" : ""}><summary>高级设置 · 迭代初值（${seeds.length}）</summary><p class="fine">初值用于启动求解。相同物理解分支充分收敛后，结果应在容差范围内一致。</p>${seeds.map(field => renderParameterField(field, inputs)).join("")}</details>` : "";
    const numericKey = group.id + "-numerics";
    const numerics = numeric.length ? `<details class="parameter-initials" data-initials="${escape(numericKey)}"${expanded.has(numericKey) ? " open" : ""}><summary>高级设置 · 数值分辨率（${numeric.length}）</summary><p class="fine">分辨率影响离散误差；应比较网格加密结果，不能用作性能调参。</p>${numeric.map(field => renderParameterField(field, inputs)).join("")}</details>` : "";
    return `<fieldset class="parameter-section" data-group="${escape(group.id)}"><legend>${escape(group.title)}</legend>${group.description ? `<p class="fine">${escape(group.description)}</p>` : ""}${standard.map(field => renderParameterField(field, inputs)).join("")}${advanced}${numerics}</fieldset>`;
  }).join("");
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
