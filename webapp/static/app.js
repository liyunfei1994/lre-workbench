const $ = id => document.getElementById(id);
const escape = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const date = value => value ? new Date(value * 1000).toLocaleString("zh-CN", {hour12:false}) : "—";
const number = value => typeof value === "number" && Number.isFinite(value)
  ? (value !== 0 && (Math.abs(value)<1e-4 || Math.abs(value)>=1e7)
    ? value.toExponential(4) : value.toLocaleString("zh-CN", {maximumSignificantDigits:7})) : "—";
const clone = value => JSON.parse(JSON.stringify(value));
const statusNames = {queued:"等待计算",running:"计算中",succeeded:"计算已完成",failed:"执行失败",cancelled:"已取消",timed_out:"计算超时",interrupted:"服务中断"};
const statusClass = value => ["succeeded","pass"].includes(value) ? "success" :
  ["failed","fail","timed_out","interrupted"].includes(value) ? "failed" : "running";
const badge = (value, label) => `<span class="badge ${statusClass(value)}">${escape(label || statusNames[value] || value)}</span>`;
let user = null, initialized = false, info = null, page = "projects";
let current = null, draft = null, dirty = false, rawDirty = false, resultRecord = null, polling = false;
let toastTimer;

function toast(message) {
  $("toast").textContent = message; $("toast").hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => $("toast").hidden = true, 5500);
}
async function api(path, method="GET", data) {
  let response;
  try {
    response = await fetch("/api" + path, {method, credentials:"same-origin",
      headers: data === undefined ? {} : {"Content-Type":"application/json"},
      body: data === undefined ? undefined : JSON.stringify(data)});
  } catch { throw new Error("无法连接本机服务，请确认启动窗口仍在运行。"); }
  let output;
  try { output = await response.json(); } catch { throw new Error("服务返回了无法读取的内容。"); }
  if (!response.ok) {
    if (response.status === 401 && user) {
      user = null; toast("登录已过期。请重新登录；未保存的编辑仍保留在此页面。");
      showAuth();
    }
    throw new Error(typeof output.detail === "string" ? output.detail : "操作未完成，请检查输入。");
  }
  return output;
}
async function action(button, errorId, task) {
  if (errorId) $(errorId).textContent = "";
  if (button) button.disabled = true;
  try { await task(); }
  catch (error) { if (errorId) $(errorId).textContent = error.message; else toast(error.message); }
  finally { if (button) button.disabled = false; }
}
function download(value, filename) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], {type:"application/json"}));
  const link = document.createElement("a"); link.href = url; link.download = filename;
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}
function setDirty(value=true) {
  dirty = value;
  $("dirty-label").textContent = value ? "尚未保存" : "已保存";
  $("dirty-label").className = "badge" + (value ? " dirty" : " success");
}
function mayLeave() { return !dirty || confirm("当前编辑尚未保存。确定放弃这些编辑？"); }
function showAuth() {
  $("auth").hidden = false; $("workspace").hidden = true;
  $("auth-form").hidden = false; $("display-label").hidden = initialized;
  $("auth-display").required = !initialized;
  $("auth-password").minLength = initialized ? 1 : 10;
  $("auth-password").autocomplete = initialized ? "current-password" : "new-password";
  $("auth-title").textContent = initialized ? "欢迎回到工作台" : "创建你的工作区";
  $("auth-description").textContent = initialized ? "登录后访问共享项目与计算记录。" : "首次使用，请创建管理员账号。密码至少 10 位。";
  $("auth-submit").textContent = initialized ? "进入工作区 →" : "创建管理员并进入 →";
}
async function enterWorkspace() {
  info = await api("/info");
  $("auth").hidden = true; $("workspace").hidden = false;
  $("auth-password").value = ""; $("auth-error").textContent = "";
  $("user-label").textContent = `${user.display_name} · ${user.role === "admin" ? "管理员" : "成员"}`;
  $("members-nav").hidden = user.role !== "admin";
  $("runtime-version").textContent = `Python ${info.python}`;
  for (const id of ["new-preset","project-preset"]) {
    $(id).innerHTML = (id==="project-preset" ? '<option value="" disabled>选择要载入的参考工况</option>' : "") +
      info.presets.map(p => `<option value="${escape(p.key)}">${escape(p.label)}</option>`).join("");
  }
  if (current && dirty) { switchPage("project"); await refreshProjectSide(); }
  else await navigate("projects");
}
function switchPage(next) {
  page = next;
  for (const name of ["projects","project","jobs","members"]) $(name+"-page").hidden = name !== next;
  $("page-name").textContent = {projects:"共享项目",project:"项目方案",jobs:"计算任务",members:"成员管理"}[next];
  document.querySelectorAll("[data-nav]").forEach(button => button.classList.toggle("active", button.dataset.nav === (next === "project" ? "projects" : next)));
}
async function navigate(next) {
  if (page === "project" && !mayLeave()) return;
  setDirty(false); switchPage(next);
  if (next === "projects") await refreshDashboard();
  if (next === "jobs") renderJobs($("all-jobs"), await api("/jobs"));
  if (next === "members") await refreshMembers();
}
async function refreshDashboard() {
  const [projects, jobs] = await Promise.all([api("/projects"),api("/jobs")]);
  $("stat-projects").textContent = projects.length;
  $("stat-active").textContent = jobs.filter(j => ["queued","running"].includes(j.status)).length;
  $("stat-done").textContent = jobs.filter(j => j.status === "succeeded").length;
  $("projects-list").innerHTML = projects.length ? projects.map(p => `
    <button class="project-card" data-project="${escape(p.id)}">
    <div class="card-top"><span class="project-icon">▦</span><span class="badge">v${p.revision}</span></div>
    <h2>${escape(p.name)}</h2><p>${escape(p.description || "尚未填写项目说明")}</p>
    <div class="card-meta"><span>${escape(p.author)} · ${date(p.updated)}</span><span>${p.job_count} 次计算 →</span></div></button>`).join("")
    : '<div class="empty"><span class="empty-icon">▦</span><h2>从第一个共享项目开始</h2><p>新建项目，保存方案后提交一次设计点计算。</p><button class="primary" data-new>＋ 新建项目</button></div>';
}
const fields = [
  {key:"cycle_type",label:"循环类型",options:[["ffsc","FFSC 全流量分级燃烧"],["gg","GG 燃气发生器"]]},
  {key:"thrust",label:"目标推力 / kN",scale:1000},
  {key:"pc",label:"燃烧室压力 / MPa",scale:1e6},
  {key:"of_ratio",label:"氧燃比 O/F"},
  {key:"expansion_ratio",label:"喷管膨胀比"},
  {key:"combustion_efficiency",label:"燃烧效率"}
];
function renderFields() {
  $("parameter-fields").innerHTML = fields.map(f => `<label>${f.label}` + (f.options
    ? `<select data-input="${f.key}">${f.options.map(([v,l]) => `<option value="${v}" ${draft[f.key]===v?"selected":""}>${l}</option>`).join("")}</select>`
    : `<input type="number" step="any" required data-input="${f.key}" value="${escape(draft[f.key] / (f.scale || 1))}">`) + "</label>").join("");
  $("raw-inputs").value = JSON.stringify(draft, null, 2); rawDirty = false;
}
function fillProject(project) {
  current = project; draft = clone(project.inputs);
  $("project-preset").value = info.presets.find(p=>JSON.stringify(p.inputs)===JSON.stringify(draft))?.key || "";
  $("project-title").textContent = project.name;
  $("project-meta").textContent = `版本 v${project.revision} · ${project.author} 更新于 ${date(project.updated)}`;
  $("project-name").value = project.name; $("project-description").value = project.description;
  $("editor-error").textContent = "";
  renderFields(); setDirty(false);
}
async function openProject(id) {
  if (page === "project" && !mayLeave()) return;
  fillProject(await api("/projects/"+id)); switchPage("project"); await refreshProjectSide();
}
async function refreshProjectSide() {
  const id = current.id;
  const [jobs, revisions] = await Promise.all([api("/jobs?project_id="+id),api(`/projects/${id}/revisions`)]);
  if (current.id !== id) return;
  renderJobs($("project-jobs"), jobs);
  $("project-revisions").innerHTML = revisions.map(r => `<div class="revision-row"><div><b>v${r.revision} · ${escape(r.name)}</b><p>${escape(r.author)} · ${date(r.created)}</p></div><button data-revision="${r.revision}">载入为草稿</button></div>`).join("");
}
function renderJobs(container, jobs) {
  container.innerHTML = jobs.length ? jobs.map(j => `<div class="job-row"><div class="job-main"><b>${escape(j.project_name)} · v${j.revision}</b><p>${escape(j.author)} · ${date(j.created)}</p>${j.status === "running" && j.progress ? `<p>迭代 ${j.progress.iteration} / ${j.progress.maximum} · 残差 ${number(j.progress.residual)}</p>`:""}</div><div class="job-actions">${badge(j.status)}<button data-job="${j.id}">查看</button>${["queued","running"].includes(j.status) && (user.role==="admin" || user.id===j.submitted_by) ? `<button class="text-btn" data-cancel="${j.id}">取消</button>`:""}</div></div>`).join("")
    : '<div class="small-empty">暂无计算记录。保存方案后，可提交第一次计算。</div>';
}
function applyRaw() {
  const value = JSON.parse($("raw-inputs").value);
  if (!value || Array.isArray(value) || typeof value !== "object") throw new Error("参数必须是 JSON 对象。");
  const params = value.inputs || value;
  if (!params || Array.isArray(params) || typeof params !== "object") throw new Error("inputs 必须是 JSON 对象。");
  const keys = Object.keys(info.presets[0].inputs);
  for (const key of Object.keys(params)) if (!keys.includes(key)) throw new Error("无法识别的参数："+key);
  draft = {...clone(info.presets[0].inputs),...params}; renderFields(); setDirty();
}
async function refreshMembers() {
  const members = await api("/members");
  $("members-list").innerHTML = members.map(m => `<div class="member-row"><div><b>${escape(m.display_name)}</b><p>@${escape(m.username)}</p></div><span class="badge">${m.role==="admin"?"管理员":"成员"}</span></div>`).join("");
}
async function showResult(id) {
  const record = await api("/jobs/"+id); resultRecord = record;
  $("result-title").textContent = `计算记录 · 方案 v${record.revision}`;
  $("download-result").hidden = !record.result;
  const base = `<p class="muted">${escape(record.author)} · ${date(record.created)} · ${badge(record.status)}</p>`;
  if (!record.result) {
    $("result-content").innerHTML = base + `<div class="banner">${escape(record.error || (record.status==="queued"?"任务在队列中等待。":record.status==="running"?"任务正在计算，稍后重新打开此记录查看结果。":statusNames[record.status]))}</div>`;
  } else {
    const r = record.result, s = r.calculation.summary, a = r.assessment;
    const metrics = [["计算推力 / kN",s.thrust_N == null?null:s.thrust_N/1000],["比冲 / s",s.Isp_s],["真空比冲 / s",s.Isp_vac_s],["主室温度 / K",s.chamber_temperature_K]];
    const labels = {pass:"通过",fail:"未通过",unknown:"待确认",review:"待复核"};
    $("result-content").innerHTML = base + `
      <div class="banner ${a.numerical.status==="pass" && a.status==="pass"?"good":""}">${badge(a.numerical.status,a.numerical.label)} · 残差 ${number(a.numerical.residual)}<br>${badge(a.status,a.label)}</div>
      <div class="result-metrics">${metrics.map(([l,v])=>`<div><span>${l}</span><strong>${number(v)}</strong></div>`).join("")}</div>
      <h3>工程约束检查</h3><div class="table-wrap"><table><thead><tr><th>检查项</th><th>结果</th><th>当前值</th><th>限值</th></tr></thead><tbody>${a.checks.map(c=>`<tr><td title="${escape(c.detail)}">${escape(c.label)}</td><td>${badge(c.status,labels[c.status])}</td><td>${number(c.value)} ${escape(c.unit)}</td><td>${c.criterion==="minimum"?"≥":"≤"} ${number(c.limit)} ${escape(c.unit)}</td></tr>`).join("")}</tbody></table></div>
      <h3>计算提示</h3><ul class="warnings">${[...r.warnings,...a.limitations].map(w=>`<li>${escape(w)}</li>`).join("")}</ul>
      <details><summary>状态点</summary><div class="table-wrap"><table><thead><tr><th>状态</th><th>压力 / MPa</th><th>温度 / K</th><th>流量 / kg·s⁻¹</th></tr></thead><tbody>${Object.entries(r.calculation.states).map(([key,v])=>`<tr><td>${escape(v.name || key)}</td><td>${v.pressure==null?"—":number(v.pressure/1e6)}</td><td>${number(v.temperature)}</td><td>${number(v.mass_flow)}</td></tr>`).join("")}</tbody></table></div></details>
      <details><summary>计算环境与追溯信息</summary><p>Python ${escape(r.environment.python)} · ${escape(r.environment.platform)} · 核心版本 ${escape(r.application_version)}</p><p>${Object.entries(r.environment.dependencies).map(([k,v])=>escape(k+" "+v)).join(" / ")}</p><p class="fine">环境指纹 ${escape(r.environment.fingerprint)}<br>任务 ${escape(record.id)}<br>完整导出包含提交参数、计算有效参数、状态点与全部结果。</p></details>`;
  }
  if (!$("result-dialog").open) $("result-dialog").showModal();
}

$("auth-form").addEventListener("submit", event => {
  event.preventDefault(); action($("auth-submit"),"auth-error",async () => {
    const data = {username:$("auth-username").value,password:$("auth-password").value};
    if (!initialized) data.display_name = $("auth-display").value;
    user = await api("/auth/"+(initialized?"login":"setup"),"POST",data);
    initialized = true; await enterWorkspace();
  });
});
$("logout").onclick = () => action($("logout"),null,async () => {
  if (!mayLeave()) return;
  await api("/auth/logout","POST",{}); user=null; current=null; draft=null; setDirty(false); showAuth();
});
document.querySelectorAll("[data-nav]").forEach(button => button.onclick = () => action(button,null,()=>navigate(button.dataset.nav)));
$("back-projects").onclick = () => action(null,null,()=>navigate("projects"));
function newProject() { $("new-error").textContent=""; $("new-dialog").showModal(); }
$("new-project").onclick = newProject;
document.querySelectorAll("[data-close]").forEach(button => button.onclick = () => $(button.dataset.close).close());
$("new-form").onsubmit = event => {
  event.preventDefault(); action(event.submitter,"new-error",async () => {
    const data = Object.fromEntries(new FormData(event.target));
    const preset = info.presets.find(p => p.key === data.preset);
    const out = await api("/projects","POST",{name:data.name,description:data.description,inputs:preset.inputs});
    $("new-dialog").close(); event.target.reset(); fillProject(out.project); switchPage("project"); await refreshProjectSide();
  });
};
$("project-name").oninput = $("project-description").oninput = () => setDirty();
$("raw-inputs").oninput = () => {rawDirty=true; setDirty();};
$("parameter-fields").oninput = event => {
  const field = fields.find(f => f.key === event.target.dataset.input); if (!field) return;
  if (rawDirty) {
    $("editor-error").textContent="JSON 中有尚未应用的修改，请先应用 JSON 后再修改表单。";
    event.target.value = field.options ? draft[field.key] : draft[field.key] / (field.scale || 1); return;
  }
  draft[field.key] = field.options ? event.target.value : (event.target.value === "" ? null : Number(event.target.value) * (field.scale || 1));
  $("raw-inputs").value = JSON.stringify(draft,null,2); setDirty();
};
$("apply-json").onclick = () => action(null,"editor-error",async () => {applyRaw(); toast("JSON 已应用，请保存新版本。");});
$("import-inputs").onchange = event => action(null,"editor-error",async () => {
  const file = event.target.files[0]; if (!file) return;
  try {
    if (file.size > 180000) throw new Error("方案文件超过 180 KB。");
    if (dirty && !confirm("导入方案将替换当前参数，是否继续？")) return;
    const text = await file.text();
    JSON.parse(text); $("raw-inputs").value=text; rawDirty=true; setDirty(); applyRaw(); toast("方案已导入，请保存新版本。");
  } finally { event.target.value=""; }
});
$("apply-preset").onclick = () => {
  if (!$("project-preset").value) {toast("请先选择要载入的参考工况。");return;}
  if (!confirm("载入参考工况将替换全部参数，是否继续？")) return;
  draft=clone(info.presets.find(p=>p.key===$("project-preset").value).inputs); renderFields(); setDirty();
};
$("project-form").onsubmit = event => {
  event.preventDefault(); action(event.submitter,"editor-error",async () => {
    if (rawDirty) throw new Error("请先点击“应用 JSON”，确认完整参数。");
    const out = await api("/projects/"+current.id,"PUT",{name:$("project-name").value,description:$("project-description").value,inputs:draft,revision:current.revision});
    fillProject(out.project); await refreshProjectSide();
    toast(out.warnings.length ? "已保存。参数提示："+out.warnings.join("；") : "新版本已保存。");
  });
};
$("reload-project").onclick = () => action(null,"editor-error",async () => {
  if (!mayLeave()) return; fillProject(await api("/projects/"+current.id)); await refreshProjectSide();
});
$("export-inputs").onclick = () => action(null,"editor-error",async () => {
  if (rawDirty) throw new Error("请先应用 JSON 再导出。");
  download({inputs:draft,project_name:$("project-name").value,base_revision:current.revision,unsaved:dirty},"lre-inputs.json");
});
$("submit-job").onclick = () => action($("submit-job"),"editor-error",async () => {
  if (dirty) throw new Error("参数尚未保存，请先保存新版本再提交计算。");
  await api(`/projects/${current.id}/jobs`,"POST",{revision:current.revision}); await refreshProjectSide(); toast("任务已提交，可在计算记录中查看。");
});
$("refresh-jobs").onclick = () => action(null,null,async()=>renderJobs($("all-jobs"),await api("/jobs")));
$("member-form").onsubmit = event => {
  event.preventDefault(); action(event.submitter,"member-error",async () => {
    await api("/members","POST",Object.fromEntries(new FormData(event.target))); event.target.reset(); await refreshMembers(); toast("成员账号已创建。");
  });
};
$("download-result").onclick = () => {
  const link = document.createElement("a");
  link.href = "/api/jobs/"+resultRecord.id+"/download";
  link.download = "lre-result-"+resultRecord.id+".json";
  document.body.append(link); link.click(); link.remove();
};
document.body.addEventListener("click", event => {
  const button = event.target.closest("button"); if (!button) return;
  if (button.hasAttribute("data-new")) newProject();
  if (button.dataset.project) action(button,null,()=>openProject(button.dataset.project));
  if (button.dataset.job) action(button,null,()=>showResult(button.dataset.job));
  if (button.dataset.cancel) action(button,null,async()=>{
    await api("/jobs/"+button.dataset.cancel+"/cancel","POST",{}); await poll(); toast("任务已取消。");
  });
  if (button.dataset.revision) action(button,"editor-error",async()=>{
    if (!mayLeave()) return;
    const old = await api(`/projects/${current.id}/revisions/${button.dataset.revision}`);
    draft=clone(old.inputs); $("project-name").value=old.name; $("project-description").value=old.description;
    renderFields(); setDirty(); toast(`已载入 v${old.revision} 为草稿，保存后将产生新版本。`);
  });
});
window.addEventListener("beforeunload", event => {if(dirty){event.preventDefault();event.returnValue="";}});
async function poll() {
  if (!user || polling || document.hidden) return;
  polling=true;
  try {
    if (page==="projects") await refreshDashboard();
    if (page==="jobs") renderJobs($("all-jobs"),await api("/jobs"));
    if (page==="project" && current) await refreshProjectSide();
  } catch(error) {toast(error.message);}
  finally {polling=false;}
}
setInterval(poll,3000);
try {
  const state = await api("/auth/status"); initialized=state.initialized; user=state.user;
  if(user) await enterWorkspace(); else showAuth();
} catch(error) {$("auth-title").textContent="连接失败";$("auth-description").textContent=error.message;}
