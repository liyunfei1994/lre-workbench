// Explicit channel-table editing. Editor state is transient; only Apply changes
// cooling_channel_table_json. Reading old, sparse or unknown JSON is lossless.
const escape = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const clone = value => JSON.parse(JSON.stringify(value));
export const COOLING_CHANNEL_TABLE_KEY = "cooling_channel_table_json";
const ANCHORS = {injector:"喷注面", conv_start:"收敛起点", throat:"喉部", exit:"主喷管出口", x_mm:"轴向坐标"};
const ROW_KEYS = new Set(["anchor", "x_mm", "n", "w_mm", "h_mm", "t_mm"]);
const LIMITS = {n:[10,2000], w_mm:[0.3,12], h_mm:[0.5,20], t_mm:[0.2,3]};
const LABELS = {n:"通道数量", w_mm:"宽度", h_mm:"深度", t_mm:"热壁厚度", x_mm:"轴向坐标"};
const rawOf = inputs => String(inputs?.[COOLING_CHANNEL_TABLE_KEY] ?? "");
const finite = value => typeof value === "number" && Number.isFinite(value);

export function validateCoolingChannelRows(rows, geometry = null) {
  const issues = [];
  if (!Array.isArray(rows) || !rows.length) return [{row:null,key:null,message:"沿程表至少需要一个断点。"}];
  const positions = new Map();
  const anchorX = geometry ? {injector:0, conv_start:geometry.L_cyl_m, throat:geometry.x_throat_m, exit:geometry.full_exit_x_m} : {injector:0};
  rows.forEach((row,index) => {
    const issue = (key,message) => issues.push({row:index,key,message:`第 ${index+1} 行：${message}`});
    if (!row || typeof row !== "object" || Array.isArray(row)) {issue(null,"断点必须为对象。");return;}
    if (!Object.hasOwn(ANCHORS,row.anchor)) issue("anchor","请选择有效的物理站位或轴向坐标。");
    for (const [key,[lo,hi]] of Object.entries(LIMITS)) {
      const value = row[key];
      if (!finite(value) || value < lo || value > hi || (key === "n" && !Number.isInteger(value)))
        issue(key,`${LABELS[key]}须为 ${lo}～${hi} 范围内的${key === "n" ? "整数" : "数值（mm）"}。`);
    }
    if ((row.anchor === "x_mm" || Object.hasOwn(row,"x_mm")) && (!finite(row.x_mm) || row.x_mm < 0)) issue("x_mm","轴向坐标须为有限非负数（从喷注面起算，mm）。");
    if (index && finite(row.n) && finite(rows[0]?.n) && row.n !== rows[0].n) issue("n","当前模型要求各断点通道数量相同，尚不支持分叉或合并。");
    const x = row.anchor === "x_mm" && finite(row.x_mm) ? row.x_mm / 1000 : anchorX[row.anchor];
    const identity = finite(x) ? `x:${x}` : Object.hasOwn(ANCHORS,row.anchor) && row.anchor !== "x_mm" ? `anchor:${row.anchor}` : null;
    if (identity) {
      if (positions.has(identity)) issue("anchor",`与第 ${positions.get(identity)+1} 行站位重复。`);
      else positions.set(identity,index);
    }
    if (finite(x) && finite(geometry?.full_exit_x_m) && x > geometry.full_exit_x_m + 1e-12) issue("x_mm","断点超出当前主喷管出口。");
  });
  return issues;
}

export function inspectCoolingChannelTable(raw) {
  if (raw == null || String(raw).trim() === "") return {kind:"scalar",rows:null,issues:[]};
  let data;
  try {data = JSON.parse(String(raw));}
  catch {return {kind:"invalid",rows:null,issues:[{row:null,key:null,message:"沿程表不是有效 JSON；保留原内容，修复前不会回退为标量几何。"}]};}
  const rows = Array.isArray(data) ? data : data?.rows;
  if (!Array.isArray(rows) || !rows.length || rows.some(row => !row || typeof row !== "object" || Array.isArray(row)))
    return {kind:"invalid",data,rows:null,issues:[{row:null,key:null,message:"沿程表须包含非空的断点对象数组。"}]};
  const unknown = (!Array.isArray(data) && (Object.keys(data).some(key => !["version","rows"].includes(key)) || (data.version != null && data.version !== 1))) || rows.some(row => Object.keys(row).some(key => !ROW_KEYS.has(key)));
  const missing = rows.some(row => !["anchor","n","w_mm","h_mm","t_mm"].every(key => Object.hasOwn(row,key)) || (row.anchor === "x_mm" && !Object.hasOwn(row,"x_mm")));
  return {kind:unknown || missing ? "legacy" : "editable",data,rows,issues:validateCoolingChannelRows(rows),unknown,missing};
}

function currentGeometry(options) {
  return options?.profileCurrent === true && options.profile?.model_id === "regen_quasi1d_ph_v1" ? options.profile.geometry : null;
}

function baseEditor(inputs, editor) {
  // A different saved/imported document invalidates an outstanding draft. The
  // caller should also clear editor caches when switching projects.
  return editor?.sourceRaw === rawOf(inputs) ? {...editor} : {sourceRaw:rawOf(inputs),candidate:null,savedTableRaw:null,error:""};
}

function blankRows(inputs) {
  const n = Number.isInteger(inputs.cooling_n_channels) ? inputs.cooling_n_channels : null;
  return ["injector","throat","exit"].map(anchor => ({anchor,x_mm:0,n,w_mm:null,h_mm:null,t_mm:null}));
}

function sampleRows(options) {
  const nodes = currentGeometry(options)?.nodes;
  if (!Array.isArray(nodes) || nodes.length < 2) throw new Error("需要与当前输入一致的完整已算冷却几何，才能生成节点采样表。");
  const rows = [...nodes].sort((a,b) => a.x_m-b.x_m).map(node => ({anchor:"x_mm",x_mm:node.x_m*1000,n:node.n_channels,w_mm:node.width_m*1000,h_mm:node.height_m*1000,t_mm:node.wall_thickness_m*1000}));
  const issues = validateCoolingChannelRows(rows,currentGeometry(options));
  if (issues.length) throw new Error(issues[0].message);
  return rows;
}

/**
 * Pure edit reducer. action: mode(value scalar/table), new, sample, raw-edit,
 * raw(value), edit(row,key,value), add, remove(row), apply, cancel.
 * All table edits stay in editor.candidate until explicit apply. N edits apply
 * to every row because the solver cannot resolve a branching channel network.
 */
export function applyCoolingChannelEdit(inputs, editor, action, options = {}) {
  const next = baseEditor(inputs,editor);
  next.error = "";
  let updated = inputs;
  const inspected = inspectCoolingChannelTable(rawOf(inputs));
  try {
    switch (action.type) {
      case "mode":
        if (!["scalar","table"].includes(action.value)) throw new Error("未知的通道几何方式。");
        if (action.value === "scalar") {
          if (rawOf(inputs).trim()) next.savedTableRaw = rawOf(inputs);
          if (next.candidate) next.savedCandidate = next.candidate;
          updated = {...inputs,[COOLING_CHANNEL_TABLE_KEY]:""};
          next.candidate = null;
        } else if (!rawOf(inputs).trim()) {
          if (next.savedTableRaw != null) updated = {...inputs,[COOLING_CHANNEL_TABLE_KEY]:next.savedTableRaw};
          if (next.savedCandidate) next.candidate = next.savedCandidate;
          else if (next.savedTableRaw == null) next.candidate = {kind:"rows",rows:blankRows(inputs),origin:"new"};
        }
        break;
      case "new":
        next.candidate = {kind:"rows",rows:blankRows(inputs),origin:"new"};
        break;
      case "sample":
        next.candidate = {kind:"rows",rows:sampleRows(options),origin:"sample"};
        break;
      case "raw-edit":
        next.candidate = {kind:"raw",raw:rawOf(inputs),origin:"raw"};
        break;
      case "raw":
        if (next.candidate?.kind !== "raw") throw new Error("请先进入原始表编辑。");
        next.candidate = {...next.candidate,raw:String(action.value)};
        break;
      case "edit": {
        if (!ROW_KEYS.has(action.key)) throw new Error("未知的断点字段。");
        if (!next.candidate) {
          if (inspected.kind !== "editable") throw new Error("此表不能直接逐格编辑，请显式编辑原始表或新建草稿。");
          next.candidate = {kind:"rows",rows:clone(inspected.rows),origin:"existing",container:clone(inspected.data)};
        }
        if (next.candidate.kind !== "rows") throw new Error("原始表编辑中不能修改断点单元格。");
        const rows = clone(next.candidate.rows);
        if (!Number.isInteger(action.row) || !rows[action.row]) throw new Error("断点行不存在。");
        const value = action.key === "anchor" ? String(action.value) : String(action.value).trim() === "" ? null : Number(action.value);
        if (action.key === "n") rows.forEach(row => {row.n=value;});
        else rows[action.row][action.key] = value;
        if (action.key === "anchor" && value !== "x_mm") rows[action.row].x_mm = 0;
        next.candidate = {...next.candidate,rows};
        break;
      }
      case "add":
      case "remove": {
        if (!next.candidate) {
          if (inspected.kind !== "editable") throw new Error("请先新建或编辑沿程表。");
          next.candidate = {kind:"rows",rows:clone(inspected.rows),origin:"existing",container:clone(inspected.data)};
        }
        if (next.candidate.kind !== "rows") throw new Error("请在断点表模式中增删行。");
        const rows = clone(next.candidate.rows);
        if (action.type === "add") rows.push({anchor:"x_mm",x_mm:null,n:rows[0]?.n ?? null,w_mm:null,h_mm:null,t_mm:null});
        else {
          if (!Number.isInteger(action.row) || !rows[action.row]) throw new Error("断点行不存在。");
          if (rows.length === 1) throw new Error("沿程表至少保留一个断点；停用表请选择标量规则。");
          rows.splice(action.row,1);
        }
        next.candidate = {...next.candidate,rows};
        break;
      }
      case "apply": {
        if (!next.candidate) break;
        const pending = next.candidate;
        let raw;
        if (pending.kind === "raw") {
          const parsed = inspectCoolingChannelTable(pending.raw);
          if (["scalar","invalid"].includes(parsed.kind)) throw new Error(parsed.issues[0]?.message || "停用表请使用标量规则选项。");
          const issues = validateCoolingChannelRows(parsed.rows,currentGeometry(options));
          if (issues.length) throw new Error(issues[0].message);
          raw = pending.raw; // Preserve unknown metadata and exact text.
        } else {
          const issues = validateCoolingChannelRows(pending.rows,currentGeometry(options));
          if (issues.length) throw new Error(issues[0].message);
          const data = Array.isArray(pending.container) ? pending.rows : {...(pending.container || {version:1}),rows:pending.rows};
          raw = JSON.stringify(data);
        }
        updated = {...inputs,[COOLING_CHANNEL_TABLE_KEY]:raw};
        next.savedTableRaw = raw;
        next.candidate = null;
        next.savedCandidate = null;
        break;
      }
      case "cancel": next.candidate = null; next.savedCandidate = null; break;
      default: throw new Error("未知的通道编辑操作。");
    }
  } catch(error) {next.error = error.message;}
  next.sourceRaw = rawOf(updated);
  return {inputs:updated,editor:next};
}

export function coolingChannelPendingIssues(editor) {
  return editor?.candidate ? [{fields:[COOLING_CHANNEL_TABLE_KEY],message:"沿程通道表仍有未应用的编辑，请应用或取消草稿后继续。"}] : [];
}

function button(action,label,extra="") {
  return `<button type="button" data-cooling-channel-action="${action}" ${extra}>${label}</button>`;
}

function issueList(issues) {
  return issues.length ? `<ul class="cooling-channel-issues" role="status">${issues.map(issue => `<li>${escape(issue.message)}</li>`).join("")}</ul>` : "";
}

function editingIssues(inspected, pending, options) {
  if (pending?.kind === "raw") {
    const parsed = inspectCoolingChannelTable(pending.raw);
    return parsed.kind === "scalar" ? [{message:"停用沿程表请使用标量规则选项。"}]
      : parsed.rows ? validateCoolingChannelRows(parsed.rows,currentGeometry(options)) : parsed.issues;
  }
  return pending?.kind === "rows" || inspected.rows
    ? validateCoolingChannelRows(pending?.rows || inspected.rows,currentGeometry(options)) : inspected.issues;
}

// Refresh status without replacing focused inputs or action buttons. Replacing
// the table during an input's change/blur event can swallow the following click
// or Tab focus. This helper never changes a control's disabled state.
export function refreshCoolingChannelStatus(root, inputs, editor, options = {}) {
  const state = baseEditor(inputs,editor), pending = state.candidate;
  const inspected = inspectCoolingChannelTable(rawOf(inputs));
  const issues = editingIssues(inspected,pending,options);
  const rows = pending?.kind === "rows" ? pending.rows : !pending ? inspected.rows : null;
  const hosts = root?.matches?.("[data-cooling-channel-editor]") ? [root]
    : [...(root?.querySelectorAll?.("[data-cooling-channel-editor]") || [])];
  for (const host of hosts) {
    const active = host.ownerDocument?.activeElement;
    const pendingHost = host.querySelector("[data-cooling-channel-pending]");
    if (pendingHost) pendingHost.hidden = !pending;
    const sources = host.querySelector("[data-cooling-channel-sources]");
    if (sources) sources.hidden = Boolean(pending);
    const sample = host.querySelector('[data-cooling-channel-action="sample"]');
    if (sample) sample.hidden = !currentGeometry(options);
    const validation = host.querySelector("[data-cooling-channel-validation]");
    if (validation) validation.innerHTML = issueList(issues);
    const error = host.querySelector("[data-cooling-channel-error]");
    if (error) {error.textContent=state.error || "";error.hidden=!state.error;}
    const derived = host.querySelector("[data-cooling-channel-derived]");
    if (derived) derived.innerHTML = renderCoolingChannelGeometrySummary(options.profile,!pending && options.profileCurrent);
    for (const field of host.querySelectorAll("[data-cooling-channel-key]")) {
      const key=field.dataset.coolingChannelKey, index=Number(field.dataset.coolingChannelRow);
      field.setAttribute("aria-invalid",String(issues.some(issue => issue.key === key && (key === "n" || issue.row === index))));
      if (field === active || !rows?.[index]) continue;
      const value = rows[index][key];
      field.value = key === "anchor" ? String(value) : finite(value) ? String(value) : "";
    }
  }
}

export function renderCoolingChannelGeometrySummary(profile, profileCurrent = false) {
  if (!profileCurrent || profile?.model_id !== "regen_quasi1d_ph_v1" || !Array.isArray(profile.geometry?.nodes))
    return '<p class="fine cooling-channel-derived">派生几何：重算当前设计点后显示实际水力直径、节距和肋宽；不从历史结果推定当前草稿尺寸。</p>';
  const nodes = profile.geometry.nodes;
  const range = (key,factor=1) => {
    const values = nodes.map(node => node[key]).filter(finite).map(value => value*factor);
    if (!values.length) return "未记录";
    const lo = Math.min(...values),hi = Math.max(...values);
    return Math.abs(hi-lo)<1e-9 ? lo.toPrecision(4) : `${lo.toPrecision(4)}～${hi.toPrecision(4)}`;
  };
  return `<div class="cooling-channel-derived"><p class="fine">当前已算通道几何（沿程范围）</p><dl><dt>水力直径 / mm</dt><dd>${range("D_h_m",1000)}</dd><dt>周向节距 / mm</dt><dd>${range("pitch_m",1000)}</dd><dt>肋宽 / mm</dt><dd>${range("rib_m",1000)}</dd><dt>总流通面积 / mm²</dt><dd>${range("A_flow_m2",1e6)}</dd></dl><p class="fine">肋宽由节距减去通道宽度得到；尺寸生成与热工计算不等于制造或寿命校核通过。</p></div>`;
}

export function renderCoolingChannelInputs(inputs, options = {}) {
  if (!(inputs.cooling_bartz_mode > 0)) return "";
  const editor = baseEditor(inputs,options.editor);
  const raw = rawOf(inputs), inspected = inspectCoolingChannelTable(raw), pending = editor.candidate;
  const tableMode = Boolean(raw.trim() || pending);
  const mode = `<label class="cooling-channel-mode">通道几何定义方式<select data-cooling-channel-action="mode"><option value="scalar"${!tableMode?" selected":""}>标量规则：矩形纵向通道</option><option value="table"${tableMode?" selected":""}>沿程尺寸表：分段线性插值</option></select></label>`;
  const rules = '<p class="fine">当前模型采用等数量、周向均匀分流的矩形通道，沿子午线布置；尚不求解螺旋路径、分叉或合并、集流腔内分配。水力直径、节距、肋宽由几何推导。</p>';
  const scalar = '<p class="fine">标量规则：通道宽度按当地型面半径与喉部半径之比变化；深度和热壁厚度沿程保持不变。按入口速度定深只确定尺寸，不保证壁温或压降达标。</p>';
  let content = "";
  if (pending?.kind === "raw") {
    content = `<p class="notice">原始表编辑：保留未知属性；应用前需显式填写每行的数量、宽度、深度与热壁厚度。</p><label>沿程通道表 JSON<textarea rows="10" data-cooling-channel-action="raw">${escape(pending.raw)}</textarea></label>`;
  } else if (pending?.kind === "rows" || inspected.kind === "editable") {
    const rows = pending?.rows || inspected.rows;
    const input = (value,row,key) => `<input type="number" step="${key==="n"?"1":"any"}" aria-label="${key==="n"?"全部断点通道数量":`第 ${row+1} 行${LABELS[key]}`}" data-cooling-channel-action="edit" data-cooling-channel-row="${row}" data-cooling-channel-key="${key}" value="${finite(value)?escape(value):""}">`;
    content = `<p class="fine">各断点的宽、深、热壁厚沿轴向 x 线性插值，首末断点之外沿用端值。x 从喷注面起算；“出口”指主喷管出口。每行尺寸须明确填写，所有断点共用同一通道数量。</p>${pending?.origin==="new"?'<p class="notice">新方案草稿：仅沿用当前通道数量，其余尺寸待填写；它不是标量几何的等效转换。</p>':""}${pending?.origin==="sample"?'<p class="notice">由当前已算节点采样生成草稿。绝对 x 坐标及尺寸保留；改变型面或网格后，不再代表原半径比例规则。</p>':""}<label>全部断点通道数量 N${input(rows[0]?.n,0,"n")}</label><div class="table-wrap"><table class="cooling-channel-table"><thead><tr><th>定位</th><th>x / mm</th><th>宽 w / mm</th><th>深 h / mm</th><th>热壁厚 t / mm</th><th>操作</th></tr></thead><tbody>${rows.map((row,i)=>`<tr><td><select aria-label="第 ${i+1} 行定位" data-cooling-channel-action="edit" data-cooling-channel-row="${i}" data-cooling-channel-key="anchor">${!Object.hasOwn(ANCHORS,row.anchor)?`<option value="${escape(row.anchor)}" selected>未识别：${escape(row.anchor)}</option>`:""}${Object.entries(ANCHORS).map(([value,label])=>`<option value="${value}"${row.anchor===value?" selected":""}>${label}</option>`).join("")}</select></td><td>${row.anchor==="x_mm"?input(row.x_mm,i,"x_mm"):'<span class="fine">随型面定位</span>'}</td>${["w_mm","h_mm","t_mm"].map(key=>`<td>${input(row[key],i,key)}</td>`).join("")}<td>${button("remove","删除",`data-cooling-channel-row="${i}"`)}</td></tr>`).join("")}</tbody></table></div>${button("add","添加断点")}`;
  } else if (raw.trim()) {
    content = `<p class="notice warning">${inspected.kind==="invalid"?"当前沿程表无法作为有效断点表读取。":"当前沿程表含未显式填写的列、扩展属性或版本；逐格编辑可能改变旧值含义，因此保留原表。"} 可显式编辑原始表，或另建新方案草稿。</p><details><summary>查看原始保存内容</summary><pre>${escape(raw)}</pre></details>${button("raw-edit","编辑原始表（保留属性）")}${button("new","新建完整断点表草稿")}`;
  }
  const sourceActions = `<div class="cooling-channel-actions" data-cooling-channel-sources${pending?" hidden":""}>${!tableMode?button("new","新建沿程表草稿"):""}${button("sample","从当前已算节点生成草稿",currentGeometry(options)?"":"hidden")}</div>`;
  const applyActions = `<div class="cooling-channel-actions" data-cooling-channel-pending${!pending?" hidden":""}><span class="fine">尚未应用；保存或运行前须应用或取消。</span>${button("apply","应用沿程表")}${button("cancel","取消编辑")}</div>`;
  const validation = `<div data-cooling-channel-validation>${issueList(editingIssues(inspected,pending,options))}</div>`;
  const error = `<p class="notice warning" role="alert" data-cooling-channel-error${!editor.error?" hidden":""}>${escape(editor.error)}</p>`;
  const derived = `<div data-cooling-channel-derived>${renderCoolingChannelGeometrySummary(options.profile,!pending && options.profileCurrent)}</div>`;
  return `<section class="cooling-channel-inputs" data-cooling-channel-editor>${mode}${rules}${!tableMode?scalar:""}${content}${validation}${error}${applyActions}${sourceActions}${derived}</section>`;
}
