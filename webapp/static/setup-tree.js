// Task locations are independent of the order of fields in the input schema.
const escape = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const branches = {
  configuration: ["design", "cycle"],
  requirements: ["inlet"],
  models: ["chamber_chemistry", "component_efficiencies", "cooling", "thermal_geometry"],
  design_point: ["pressure", "pressure_ffsc", "preburner_design", "preburners", "nozzle_performance"],
  pumps: ["pumps"],
  turbines: ["turbines"],
  chamber: ["chamber_contour"],
  cooling_component: ["chamber_cooling_layout", "chamber_channels", "wall_thermal"],
  valves: ["valve_configuration"],
  solution: ["solver", "initialization", "thermo"],
  constraints: ["engineering_constraints"],
};
export function setupBranch(id) {
  return Object.keys(branches).find(branch => branches[branch].includes(id)) || "models";
}
export function setupNavigationGroup(id) { return setupBranch(id)==="constraints"?"results":["pumps","turbines","chamber","cooling_component","valves","component_inputs"].includes(setupBranch(id))?"components":"concept"; }
export function setupSection(id) {
  const branch=setupBranch(id);
  return {id:branch,label:{configuration:"发动机定义与设计工况",requirements:"推进剂供给条件",models:"总体模型与假设",design_point:"循环匹配条件",component_inputs:"部件设计输入",pumps:"泵与轴系",turbines:"涡轮",chamber:"推力室与喷管",cooling_component:"再生冷却系统",valves:"阀门、管路与供给部件",solution:"总体设计点计算",constraints:"工程限值与校核"}[branch]};
}
const labels = {ready:"可计算",invalid:"存在问题",filled:"待检查",pending:"检查中",unavailable:"待检查",preset:"采用示例",confirmed:"已确认",review:"待确认",readonly:"说明",missing:"未设置"};
export function setupNodes(groups, readiness, changedGroups = [], hasRecord = false) {
  const statuses = new Map((readiness?.groups || []).map(group => [group.id,group]));
  const changed = new Set(changedGroups.map(group => typeof group === "string" ? group : group.id));
  return groups.map(group => {
    const check = statuses.get(group.id);
    const state = check?.status || "pending";
    // Input confirmation and the age of a selected result are independent.
    const resultChanged = hasRecord && changed.has(group.id);
    const resultReason = resultChanged ? "本组输入与所选计算记录不同；确认参数不会改变该记录的输入。" : "";
    const label=check?.configurationLabel || labels[state] || "待检查";
    const inputReason = check?.issues?.map(issue => issue.message).join("；") || label;
    const reason = [inputReason, resultReason].filter(Boolean).join("；");
    return {id:group.id,title:group.title,branch:setupBranch(group.id),state,label,reason,deferredDesign:check?.deferredDesign===true,changed:resultChanged,resultReason,field:check?.issues?.[0]?.fields?.find(key=>group.fields?.some(field=>field.key===key))};
  });
}
// Summaries remain visible when a branch is collapsed. These are input states,
// not evidence of solver convergence or model validation.
export function setupProgress(nodes, branch = null) {
  const scoped = nodes.filter(node => !branch || node.branch === branch);
  const editable = scoped.filter(node => node.state !== "readonly");
  const attention = editable.filter(node => !["ready", "confirmed", "preset"].includes(node.state));
  const priority = ["invalid", "missing", "unavailable", "pending", "review", "filled"];
  const state = priority.find(value => attention.some(node => node.state === value)) || (attention.length ? "pending" : "ready");
  const states = [...new Set(attention.map(node => node.state))];
  const action = attention.length ? `${attention.length} ${states.length === 1 ? labels[states[0]] || "待处理" : "待处理"}` : editable.length ? editable.every(node=>node.deferredDesign)?"部件草案":"输入已就绪" : "只读说明";
  const counts = [...new Set(scoped.map(node => node.state))].map(value => `${scoped.filter(node => node.state === value).length} 组${labels[value] || "待检查"}`);
  return {label: branch ? action : `${editable.length} 组设置 · ${action}`, state,
    reason: `${counts.join("；")}。此处汇总输入准备状态，计算、工程约束与验证证据分别核对。`};
}
export function renderSetupBranch(nodes, branch, selected = "") {
  return nodes.filter(node => node.branch === branch).map(node => `<button type="button" class="setup-tree-node${node.id===selected?" active":""}" data-setup-group="${escape(node.id)}"${node.field?` data-setup-field="${escape(node.field)}"`:""} aria-current="${node.id===selected?"page":"false"}" title="${escape(node.reason)}"><span class="nav-item-label">${escape(node.title)}</span><small class="setup-node-status" data-status="${escape(node.state)}">${escape(node.label)}</small></button>`).join("");
}
