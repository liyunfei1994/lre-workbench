import {renderAnalysis, configureResultSchema} from "./results.js";
import {mountValidation} from "./validation.js";
import {mountThrottle} from "./throttle.js";
import {mountTransient} from "./transient.js";
import {mountFeedNetwork} from "./feed-network.js";
import {mountChilldown} from "./chilldown.js";
import {mountIgnition} from "./ignition.js";
import {mountShutdown} from "./shutdown.js";
import {mountComponentWorkspace} from "./component-analysis.js";
import {mountFlowWorkspace} from "./flow-analysis.js";
import {mountCycleWorkspace} from "./cycle-schematic.js";
import {savedInputDocument,mergeInputDefaults,PREBURNER_MODE_KEY,selectPreburnerMode,parameterValue,parameterVisible,parameterGroups,parameterSearchText,parameterLayoutDependency,renderParameterGroups,renderParameterEvidence} from "./parameter-editor.js";

const $ = id => document.getElementById(id);
const escape = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const date = value => value ? new Date(value * 1000).toLocaleString("zh-CN", {hour12:false}) : "—";
const number = value => typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("zh-CN", {maximumSignificantDigits:7}) : "—";
const clone = value => JSON.parse(JSON.stringify(value));
const active = job => job && ["queued","running"].includes(job.status);
const statusNames = {queued:"等待计算",running:"计算中",succeeded:"计算已完成",failed:"执行失败",cancelled:"已取消",timed_out:"计算超时",interrupted:"服务中断"};
const statusClass = value => ["succeeded","pass"].includes(value) ? "success" : ["failed","fail","timed_out","interrupted"].includes(value) ? "failed" : "running";
const badge = (value, label) => `<span class="badge ${statusClass(value)}">${escape(label || statusNames[value] || value)}</span>`;
const views = {overview:["稳态性能分析","单工况设计点 · 循环匹配、功率平衡与系统约束"],topology:["发动机循环原理图","查看推进剂流向、燃气路径与泵—涡轮驱动关系"],states:["流路状态分析","沿流路查看节点状态、压力与温度变化及组分"],components:["部件性能分析","按功能查看部件性能、进出口状态与能量转换"],log:["日志与警告","检查计算状态、警告与运行环境"],history:["方案与计算记录","查看方案版本、计算快照与历史结果"]};
let user=null, initialized=false, info=null, page="projects", analysisView="overview";
let current=null, draft=null, dirty=false, rawDirty=false, resultRecord=null, modalRecord=null;
let fields=[], groups=[], projectJobs=[], selectedJobId=null, polling=false, editorBusy=false, connected=null;
let toastTimer;
let refreshSequence=0;
let referenceKey="", presetUndo=null;
let validationView=null;
let throttleView=null;
let transientView=null;
let feedView=null;
let chilldownView=null;
let ignitionView=null;
let shutdownView=null;
views.shutdown=["关机与再启动分析","连续状态 · 关阀、残余库存、壁面余热、轴系惰转与再次供给"];
views.ignition=["点火事件与建压分析","气相反应容腔 · 给定外部点火热脉冲、阀门响应与压力建立"];
views.chilldown=["预冷与充填分析","壁面热惯性 · 真实流体气液库存、两相充填与初始状态快照"];
views.feed=["供给网络动态","单相液体供给子系统 · 管路惯性、容腔库存、低速泵图与阀门执行器"];
views.validation=["热力学校核","同条件热化学对比、系统守恒、真实流体物性与试验验证"];
views.transient=["瞬态响应分析","热态工作点扰动 · 零维质量、能量与转子惯性积分"];
views.throttle=["节流稳态性能分析","固定主喷管喉部 · 不同推力指令下的稳态工况扫描"];

function toast(message) { $("toast").textContent=message; $("toast").hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$("toast").hidden=true,5500); }
function connection(ok) {
  connected=ok;
  $("connection-status").dataset.state=ok?"online":"offline";
  $("connection-status").textContent=ok?"本机服务已连接":"本机服务连接中断";
  $("connection-banner").hidden=ok;
  $("workspace").classList.toggle("disconnected",!ok);
  $("connection-message").textContent="连接中断，当前编辑仍保留。请双击 start_web_windows.bat 启动服务，再重新连接。";
  updateActionState();
}
async function api(path, method="GET", data) {
  const controller=new AbortController(), timeout=setTimeout(()=>controller.abort(),15000);
  let response;
  try { response=await fetch("/api"+path,{method,credentials:"same-origin",signal:controller.signal,
    headers:data===undefined?{}:{"Content-Type":"application/json"},body:data===undefined?undefined:JSON.stringify(data)}); }
  catch(error) { connection(false); throw new Error(error.name==="AbortError" ? "服务响应超时，编辑已保留。保存或提交可能已完成，请重连后检查记录。" : "无法连接本机服务。请运行 start_web_windows.bat，并保持服务窗口开启。"); }
  finally {clearTimeout(timeout);}
  connection(true);
  let output;
  try {output=await response.json();} catch {throw new Error("服务返回了无法读取的内容。");}
  if(!response.ok) {
    if(response.status===401 && user){user=null;showAuth();}
    const error=new Error(typeof output.detail==="string"?output.detail:"操作未完成，请检查输入。");error.status=response.status;throw error;
  }
  return output;
}
async function action(button,errorId,task) {
  if(errorId) $(errorId).textContent="";
  if(button) button.disabled=true;
  try {await task();} catch(error){if(errorId) $(errorId).textContent=error.message;else toast(error.message);}
  finally {if(button) button.disabled=false;updateActionState();}
}
function download(value,filename) {
  const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:"application/json"}));
  const link=document.createElement("a");link.href=url;link.download=filename;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);
}
function downloadResult(record) {if(record?.result) download(record,"lre-result-"+record.id+".json");}
function setDirty(value=true) {
  dirty=value;$("dirty-label").textContent=value?"尚未保存":"已保存";$("dirty-label").className="badge"+(value?" dirty":" success");
  if(current) $("document-title").textContent=$("project-name").value+(value?" · 已修改":"");
  updateResultContext();
  if(analysisView==="validation") validationView?.sync();
  if(analysisView==="throttle") throttleView?.sync();
  if(analysisView==="transient") transientView?.sync();
  if(analysisView==="feed") feedView?.sync();
  if(analysisView==="chilldown") chilldownView?.sync();
  if(analysisView==="ignition") ignitionView?.sync();
  if(analysisView==="shutdown") shutdownView?.sync();
}
function mayLeave(){return !dirty || confirm("当前编辑尚未保存。确定放弃这些编辑？");}
function updateActionState(){
  for(const id of ["header-run","submit-job"]) $(id).disabled=!current || editorBusy || connected===false || projectJobs.some(active) || !!propellantIssue();
  $("header-save").disabled=!current || editorBusy || connected===false;
  const cancellable=projectJobs.find(j=>active(j)&&(user?.role==="admin"||user?.id===j.submitted_by));
  $("header-cancel").hidden=page!=="project"||!cancellable;
  $("header-cancel").disabled=editorBusy||connected===false;
  $("header-cancel").dataset.cancel=cancellable?.id||"";
}
async function withEditorLock(task){
  if(editorBusy) return;
  editorBusy=true;$("project-form").inert=true;const controls=[...$("project-form").querySelectorAll("input,select,textarea,button")];
  const disabled=controls.map(el=>el.disabled);controls.forEach(el=>el.disabled=true);updateActionState();
  try{return await task();}finally{controls.forEach((el,i)=>el.disabled=disabled[i]);$("project-form").inert=false;editorBusy=false;updateActionState();}
}
function showAuth(){
  validationView?.onLeave();
  throttleView?.onLeave();
  transientView?.onLeave();
  feedView?.onLeave();
  chilldownView?.onLeave();
  ignitionView?.onLeave();
  shutdownView?.onLeave();
  $("auth").hidden=false;$("workspace").hidden=true;$("auth-form").hidden=false;$("display-label").hidden=initialized;
  $("auth-display").required=!initialized;$("auth-password").minLength=initialized?1:10;
  $("auth-password").autocomplete=initialized?"current-password":"new-password";
  $("auth-title").textContent=initialized?"欢迎回到工作台":"创建你的工作区";
  $("auth-description").textContent=initialized?"登录后进入设计工作台，继续编辑与计算。":"首次使用，请创建管理员账号。密码至少 10 位。";
  $("auth-submit").textContent=initialized?"进入工作台 →":"创建管理员并进入 →";
}
async function enterWorkspace(){
  info=await api("/info");
  if(!info.ui_schema?.groups) throw new Error("页面已更新，请先停止旧服务，再运行 start_web_windows.bat 载入新版工作台。");
  groups=info.ui_schema.system_groups||info.ui_schema.groups;fields=info.ui_schema.groups.flatMap(g=>g.fields);
  configureResultSchema(info.ui_schema);
  $("auth").hidden=true;$("workspace").hidden=false;$("auth-password").value="";$("auth-error").textContent="";
  $("user-label").textContent=`${user.display_name} · ${user.role==="admin"?"管理员":"成员"}`;$("members-nav").hidden=user.role!=="admin";
  $("runtime-version").textContent=`Python ${info.python} · Cantera / CoolProp`;
  const options=info.presets.map(p=>`<option value="${escape(p.key)}">${escape(p.label)}</option>`).join("");
  $("new-preset").innerHTML=options;
  const references=(info.reference_engines||[]).filter(r=>!r.applicable);
  $("project-preset").innerHTML='<option value="">自定义方案</option><optgroup label="选择即载入工程示例">'+options+'</optgroup>'+(references.length?'<optgroup label="公开机型资料 · 尚不支持计算">'+references.map(r=>`<option value="${escape(r.key)}">${escape(r.name)} · 仅资料</option>`).join("")+'</optgroup>':"");
  $("reference-engine").innerHTML=(info.reference_engines||[]).map(r=>`<option value="${escape(r.key)}">${escape(r.name)}</option>`).join("");
  if(current){if(!dirty)renderFields();syncPresetSelection();switchPage("project");await refreshProjectSide();}
  else {const projects=await api("/projects");if(projects.length) await openProject(projects[0].id);else await navigate("projects");}
}
function switchPage(next){
  if(next!=="project") validationView?.onLeave();
  if(next!=="project") throttleView?.onLeave();
  if(next!=="project") transientView?.onLeave();
  if(next!=="project") feedView?.onLeave();
  if(next!=="project") chilldownView?.onLeave();
  if(next!=="project") ignitionView?.onLeave();
  if(next!=="project") shutdownView?.onLeave();
  page=next;for(const name of ["projects","project","jobs","members"]) $(name+"-page").hidden=name!==next;
  $("page-name").textContent=next==="project"?views[analysisView][0]:{projects:"共享项目",jobs:"计算任务",members:"成员管理"}[next];
  for(const id of ["header-save","header-run","export-inputs"]) $(id).hidden=next!=="project";
  document.querySelectorAll("[data-nav]").forEach(b=>b.classList.toggle("active",b.dataset.nav===next));
  document.querySelectorAll("[data-view]").forEach(b=>b.classList.toggle("active",next==="project" && b.dataset.view===analysisView));
  if(next!=="project"){$("document-title").textContent="团队工作区";$("document-meta").textContent="共享方案 · 版本与计算记录";}else updateDocument();
  updateActionState();
}
async function navigate(next){
  if(editorBusy || (page==="project"&&!mayLeave())) return;
  if(page==="project" && dirty){draft=mergeInputDefaults(clone(info.ui_schema.defaults),clone(current.inputs),true);$("project-name").value=current.name;$("project-description").value=current.description;clearPresetUndo();syncPresetSelection(true);renderFields();}
  setDirty(false);switchPage(next);
  if(next==="projects") await refreshDashboard();
  if(next==="jobs") renderJobs($("all-jobs"),await api("/jobs"));
  if(next==="members") await refreshMembers();
}
async function refreshDashboard(){
  const [projects,jobs]=await Promise.all([api("/projects"),api("/jobs")]);if(page!=="projects") return;
  $("stat-projects").textContent=projects.length;$("stat-active").textContent=jobs.filter(active).length;$("stat-done").textContent=jobs.filter(j=>j.status==="succeeded").length;
  $("projects-list").innerHTML=projects.length?projects.map(p=>`<button class="project-card" data-project="${escape(p.id)}"><div class="card-top"><span class="project-icon">▦</span><span class="badge">v${p.revision}</span></div><h2>${escape(p.name)}</h2><p>${escape(p.description||"尚未填写项目说明")}</p><div class="card-meta"><span>${escape(p.author)} · ${date(p.updated)}</span><span>打开设计工作台 →</span></div></button>`).join(""):'<div class="empty"><h2>从第一个设计方案开始</h2><p>选择参考工况，进入与桌面版相同布局的设计工作台。</p><button class="primary" data-new>＋ 新建项目</button></div>';
}
const compatible=item=>(!item.cycles || item.cycles.includes(draft.cycle_type)) && (!item.propellants || item.propellants.includes(draft.propellant_pair||"lox_methane"));
function currentPropellant(){
  return info?.ui_schema?.propellant_models?.find(m=>m.id===(draft?.propellant_pair||"lox_methane"));
}
function propellantIssue(){
  const model=currentPropellant();if(!model||!draft)return "";
  if(!model.supported_cycles.includes(draft.cycle_type))return "该推进剂模型当前支持 GG 循环，请选择燃气发生器循环，或加载配套工程示例。";
  if(draft.propellant_pair!=="lox_kerosene")return "";
  if(draft.cooling_bartz_mode!==0||draft.cooling_dp_auto!==0)return "煤油替代模型需使用指定吸热量、指定系统压降；请在再生冷却分组修改，或加载配套工程示例。";
  if(!(draft.fuel_inlet_temp>=model.inlet_temperature_min_K&&draft.fuel_inlet_temp<=model.inlet_temperature_max_K))return `煤油替代工质的入口温度需为 ${model.inlet_temperature_min_K}–${model.inlet_temperature_max_K} K；当前值 ${draft.fuel_inlet_temp} K。`;
  return "";
}
function updatePropellantContext(){
  const model=currentPropellant(),issue=propellantIssue();
  $("propellant-model-label").textContent=model?.label||"液氧 / 甲烷";
  $("propellant-model-note").textContent=model?(model.id==="lox_methane"?`气相数据：${draft.methane_thermo_dataset==="gri30_highT"?"gri30_highT.yaml（高温热化学）":"gri30.yaml（历史数据）"}。仍采用理想气体混合物；纯推进剂使用 CoolProp。`:model.limitations.slice(0,2).join(" ")):"旧方案默认采用液氧 / 甲烷。";
  $("propellant-model-details").hidden=!model||model.limitations.length<=2;
  $("propellant-model-limits").textContent=model?.limitations.slice(2).join(" ")||"";
  $("propellant-model-error").textContent=issue;
  $("load-propellant-example").hidden=!issue;
  updateActionState();
}
const sameInputs=(a,b)=>Object.keys({...a,...b}).every(key=>a[key]===b[key]);
const sameArchitecture=(a,b)=>a.cycle_type===b.cycle_type && (a.propellant_pair||"lox_methane")===(b.propellant_pair||"lox_methane") && (a.cycle_type!=="gg" || a.gg_layout===b.gg_layout);
function clearPresetUndo(){presetUndo=null;$("undo-preset").hidden=true;}
function syncPresetSelection(infer=false){
  if(infer)referenceKey=info.presets.find(p=>sameInputs({...info.ui_schema.defaults,...p.inputs},draft))?.key||"";
  const preset=info.presets.find(p=>p.key===referenceKey);
  if(!preset || !sameArchitecture(preset.inputs,draft))referenceKey="";
  $("project-preset").value=referenceKey;updatePresetContext();
}
function loadPreset(preset){
  if(!preset||editorBusy)return;
  presetUndo={draft:clone(draft),dirty,rawDirty,raw:$("raw-inputs").value,referenceKey,
    group:$("parameter-group").value,search:$("parameter-search").value};
  draft={...clone(info.ui_schema.defaults),...clone(preset.inputs)};referenceKey=preset.key;
  $("parameter-search").value="";$("parameter-group").value=groups.find(g=>g.fields.some(f=>f.key==="cycle_type"))?.id||"";
  $("editor-error").textContent="";renderFields();syncPresetSelection();setDirty();$("undo-preset").hidden=false;
  toast("已载入整套参考参数，循环、推进剂与轴系已同步。尚未保存，可撤销切换。");
}
function undoPreset(){
  if(!presetUndo||editorBusy)return;
  const previous=presetUndo;draft=previous.draft;referenceKey=previous.referenceKey;
  $("parameter-search").value=previous.search;$("parameter-group").value=previous.group;
  renderFields();rawDirty=previous.rawDirty;$("raw-inputs").value=previous.raw;
  syncPresetSelection();setDirty(previous.dirty);clearPresetUndo();$("editor-error").textContent="";
  toast("已恢复切换前的参数与编辑内容。");
}
function updatePresetContext(){
  const key=$("project-preset").value, reference=(info.reference_engines||[]).find(r=>r.key===key||r.preset_key===key);
  const applicable=info.presets.some(p=>p.key===key);
  $("apply-preset").disabled=!applicable;
  $("preset-context").textContent=reference?`${reference.propellants} · ${reference.cycle_label}。${reference.shaft_note||""}${reference.applicable?"":` ${reference.compatibility_reason} 当前计算方案保持不变。`}`:applicable?"已载入参考参数；修改循环、推进剂或轴系后转为自定义方案。":"当前为自定义方案。选择参考机型会立即替换整套参数，并提供一次撤销。";
}
function renderReference(){
  const reference=(info.reference_engines||[]).find(r=>r.key===$("reference-engine").value);
  if(!reference){$("reference-content").textContent="暂无参考资料。请重新连接新版服务。";return;}
  const safeLink=(url,title)=>/^https:\/\//i.test(url||"")?`<a href="${escape(url)}" target="_blank" rel="noopener noreferrer">${escape(title)}</a>`:escape(title);
  $("reference-content").innerHTML=`<div class="reference-heading"><h3>${escape(reference.name)}</h3><span class="badge">${reference.applicable?"可加载工程示例":"仅公开资料"}</span></div><p class="reference-propellants">${escape(reference.propellants)} · ${escape(reference.cycle_label)}</p><p class="reference-compatibility">${escape(reference.compatibility_reason)}</p>${reference.shaft_note?`<p class="reference-shaft">${escape(reference.shaft_note)}</p>`:""}<div class="table-wrap"><table><thead><tr><th>资料项</th><th>公开信息</th></tr></thead><tbody>${(reference.reference_facts||[]).map(f=>`<tr><td>${escape(f.label)}</td><td>${escape(f.value)}</td></tr>`).join("")}</tbody></table></div><p class="fine">${escape(reference.model_note)}</p><div class="reference-sources"><b>资料来源</b>${(reference.sources||[]).map(s=>safeLink(s.url,s.title)).join("")}</div>`;
  $("reference-content").innerHTML+=renderParameterEvidence(reference.parameter_evidence,fields);
}
function showReferences(){
  const key=$("project-preset").value,reference=(info.reference_engines||[]).find(r=>r.key===key||r.preset_key===key);
  $("reference-engine").value=reference?.key||info.reference_engines?.[0]?.key||"";
  renderReference();$("reference-dialog").showModal();
}
const displayValue=f=>parameterValue(f,draft);
const editorField=key=>groups.flatMap(g=>g.fields).find(f=>f.key===key)||fields.find(f=>f.key===key);
function renderFields(){
  const selected=$("parameter-group").value,available=parameterGroups(groups,draft);
  const expanded=new Set([...$("parameter-fields").querySelectorAll("[data-initials]")].filter(node=>node.open&&!node.dataset.searchOpened).map(node=>node.dataset.initials));
  $("parameter-group").innerHTML=available.map(g=>`<option value="${escape(g.id)}">${escape(g.title)}</option>`).join("");
  if(available.some(g=>g.id===selected)) $("parameter-group").value=selected;
  $("parameter-fields").innerHTML=renderParameterGroups(available,draft,expanded);
  $("raw-inputs").value=JSON.stringify(draft,null,2);rawDirty=false;filterParameters();updatePropellantContext();
}
function filterParameters(){
  const query=$("parameter-search").value.trim().toLocaleLowerCase(),selected=$("parameter-group").value;let count=0;$("parameter-group").disabled=!!query;
  $("parameter-fields").querySelectorAll("[data-group]").forEach(group=>{
    let matches=0;group.querySelectorAll("[data-field]").forEach(row=>{const f=editorField(row.dataset.field);const found=!!f&&parameterVisible(f,draft)&&(!query||parameterSearchText(f,draft,group.querySelector("legend").textContent).includes(query));row.hidden=!found;if(found)matches++;});
    group.querySelectorAll("[data-initials]").forEach(details=>{const matched=[...details.querySelectorAll("[data-field]")].some(row=>!row.hidden);details.hidden=!matched;if(query&&matched&&!details.open){details.open=true;details.dataset.searchOpened="1";}else if(!query&&details.dataset.searchOpened){details.open=false;delete details.dataset.searchOpened;}});
    group.hidden=!matches||(!query&&group.dataset.group!==selected);if(!group.hidden)count+=matches;
  });$("parameter-empty").hidden=count>0;
}
function updateDocument(){if(!current)return;$("document-title").textContent=$("project-name").value+(dirty?" · 已修改":"");$("document-meta").textContent=`方案 v${current.revision} · ${current.author} · ${date(current.updated)}`;}
function fillProject(project,keepReference=false){
  const changed=current?.id!==project.id;current=project;draft=mergeInputDefaults(clone(info.ui_schema.defaults),clone(project.inputs),true);
  if(changed){resultRecord=null;selectedJobId=null;projectJobs=[];analysisView="overview";$("parameter-search").value="";}
  $("project-name").value=project.name;$("project-description").value=project.description;$("editor-error").textContent="";
  clearPresetUndo();syncPresetSelection(changed||!keepReference);
  renderFields();updatePresetContext();setDirty(false);updateDocument();renderView();updateActionState();
}
async function openProject(id){if(editorBusy||(page==="project"&&!mayLeave()))return;await withEditorLock(async()=>{fillProject(await api("/projects/"+id));switchPage("project");await refreshProjectSide();});}
async function chooseView(view){
  if(editorBusy)return;
  if(page!=="project"){
    if(current){await openProject(current.id);}
    else {const projects=await api("/projects");if(!projects.length){newProject();return;}await openProject(projects[0].id);}
  }
  analysisView=view;renderView();switchPage("project");
}
function renderView(){
  $("analysis-title").textContent=views[analysisView][0];$("analysis-subtitle").textContent=views[analysisView][1];
  $("project-history").hidden=analysisView!=="history";$("analysis-content").hidden=["history","validation","throttle","transient","feed","chilldown","ignition","shutdown"].includes(analysisView);
  $("validation-content").hidden=analysisView!=="validation";
  $("throttle-content").hidden=analysisView!=="throttle";
  $("transient-content").hidden=analysisView!=="transient";
  $("feed-content").hidden=analysisView!=="feed";
  $("chilldown-content").hidden=analysisView!=="chilldown";
  $("ignition-content").hidden=analysisView!=="ignition";
  $("shutdown-content").hidden=analysisView!=="shutdown";
  if(analysisView!=="validation") validationView?.onLeave();
  if(analysisView!=="throttle") throttleView?.onLeave();
  if(analysisView!=="transient") transientView?.onLeave();
  if(analysisView!=="feed") feedView?.onLeave();
  if(analysisView!=="chilldown") chilldownView?.onLeave();
  if(analysisView!=="ignition") ignitionView?.onLeave();
  if(analysisView!=="shutdown") shutdownView?.onLeave();
  if(analysisView==="validation") {
    validationView ||= mountValidation($("validation-content"),{api,getContext:()=>({project:current,draft,dirty,record:resultRecord}),notify:toast});
    Promise.resolve(validationView.refresh()).catch(error=>toast(error.message));
  } else if(analysisView==="shutdown") {
    shutdownView ||= mountShutdown($("shutdown-content"),{api,getContext:()=>({project:current,draft,dirty,record:resultRecord}),notify:toast});
    Promise.resolve(shutdownView.refresh()).catch(error=>toast(error.message));
  } else if(analysisView==="ignition") {
    ignitionView ||= mountIgnition($("ignition-content"),{api,getContext:()=>({project:current,draft,dirty,record:resultRecord}),notify:toast});
    Promise.resolve(ignitionView.refresh()).catch(error=>toast(error.message));
  } else if(analysisView==="chilldown") {
    chilldownView ||= mountChilldown($("chilldown-content"),{api,getContext:()=>({project:current,draft,dirty,record:resultRecord}),notify:toast});
    Promise.resolve(chilldownView.refresh()).catch(error=>toast(error.message));
  } else if(analysisView==="feed") {
    feedView ||= mountFeedNetwork($("feed-content"),{api,getContext:()=>({project:current,draft,dirty,record:resultRecord}),notify:toast});
    Promise.resolve(feedView.refresh()).catch(error=>toast(error.message));
  } else if(analysisView==="transient") {
    transientView ||= mountTransient($("transient-content"),{api,getContext:()=>({project:current,draft,dirty,record:resultRecord}),notify:toast,onInspectSource:()=>chooseView("overview")});
    Promise.resolve(transientView.refresh()).catch(error=>toast(error.message));
  } else if(analysisView==="throttle") {
    throttleView ||= mountThrottle($("throttle-content"),{api,getContext:()=>({project:current,draft,dirty,record:resultRecord}),notify:toast});
    Promise.resolve(throttleView.refresh()).catch(error=>toast(error.message));
  } else {
    if(analysisView!=="history") {
      $("analysis-content").innerHTML=renderAnalysis(analysisView,resultRecord);
      if(analysisView==="components") mountComponentWorkspace($("analysis-content"),resultRecord?.id || "");
      if(analysisView==="states") mountFlowWorkspace($("analysis-content"),resultRecord?.id || "",{
        onComponent:id=>{
          const host=$("analysis-content");
          host.dataset.caRecordId=String(resultRecord?.id || "");
          host.dataset.caCategory="all";host.dataset.caSelected=id;
          chooseView("components");
        },
        onTopology:id=>{
          $("analysis-content").dataset.flowLocation=id || "";
          $("analysis-content").dataset.flowLocationRecord=String(resultRecord?.id || "");
          chooseView("topology");
        },
      });
      if(analysisView==="topology") mountCycleWorkspace($("analysis-content"),resultRecord?.id || "",{
        onComponent:id=>{
          const host=$("analysis-content");host.dataset.caRecordId=String(resultRecord?.id || "");
          host.dataset.caCategory="all";host.dataset.caSelected=id;chooseView("components");
        },onError:toast,
      });
    }
  }
  updateResultContext();
}
function updateResultContext(){
  if(!current)return;const old=resultRecord&&(dirty||resultRecord.revision!==current.revision);
  $("result-context").classList.toggle("result-context-stale",!!old);
  $("result-context").textContent=resultRecord?`当前显示：方案 v${resultRecord.revision} · ${date(resultRecord.created)} · ${statusNames[resultRecord.status]||resultRecord.status}${old?"。这是历史输入的结果；当前参数已变化，请重新运行设计点。":"。"}`:"尚无计算结果。选择参考工况后运行设计点。";
  $("export-current-result").hidden=!resultRecord?.result;
}
async function refreshProjectSide(){
  if(!current)return;const id=current.id,sequence=++refreshSequence;
  const [jobs,revisions]=await Promise.all([api("/jobs?project_id="+id),api(`/projects/${id}/revisions`)]);
  if(current?.id!==id||sequence!==refreshSequence)return;projectJobs=jobs;renderJobs($("project-jobs"),jobs);
  $("project-revisions").innerHTML=revisions.map(r=>`<div class="revision-row"><div><b>v${r.revision} · ${escape(r.name)}</b><p>${escape(r.author)} · ${date(r.created)}</p></div><button data-revision="${r.revision}">载入为草稿</button></div>`).join("");
  if(!jobs.some(j=>j.id===selectedJobId))selectedJobId=jobs[0]?.id||null;
  $("result-selector").innerHTML=jobs.length?jobs.map(j=>`<option value="${j.id}" ${j.id===selectedJobId?"selected":""}>v${j.revision} · ${date(j.created)} · ${statusNames[j.status]}</option>`).join(""):'<option value="">尚无计算记录</option>';
  const summary=jobs.find(j=>j.id===selectedJobId);
  if(summary && (resultRecord?.id!==summary.id || resultRecord.status!==summary.status || active(summary)))await loadRecord(summary.id);
  if(current?.id!==id||sequence!==refreshSequence)return;
  const running=jobs.find(active);$("run-progress").textContent=running?`${statusNames[running.status]} · 方案 v${running.revision}${running.progress?` · 迭代 ${running.progress.iteration}/${running.progress.maximum} · 残差 ${number(running.progress.residual)}`:""}`:"就绪 · 可运行当前设计点";
  updateActionState();
}
async function loadRecord(id){const projectId=current?.id;selectedJobId=id;const record=await api("/jobs/"+id);if(current?.id!==projectId||selectedJobId!==id)return;resultRecord=record;renderView();}
function renderJobs(container,jobs){container.innerHTML=jobs.length?jobs.map(j=>`<div class="job-row"><div class="job-main"><b>${escape(j.project_name)} · v${j.revision}</b><p>${escape(j.author)} · ${date(j.created)}</p>${active(j)&&j.progress?`<p>迭代 ${j.progress.iteration}/${j.progress.maximum} · 残差 ${number(j.progress.residual)}</p>`:""}</div><div class="job-actions">${badge(j.status)}<button data-job="${j.id}">查看</button>${active(j)&&(user.role==="admin"||user.id===j.submitted_by)?`<button data-cancel="${j.id}">取消</button>`:""}</div></div>`).join(""):'<div class="small-empty">暂无计算记录。点击“运行设计点”开始计算。</div>';}
function validateChoices(params){
  for(const f of fields)if(f.choices && f.key in params && !f.choices.some(c=>String(c.value)===String(params[f.key])))throw new Error(`${f.label}选项无效：${params[f.key]}`);
}
function applyRaw(){
  const value=JSON.parse($("raw-inputs").value);if(!value||Array.isArray(value)||typeof value!=="object")throw new Error("参数必须是 JSON 对象。");
  const params=value.inputs||value;if(!params||Array.isArray(params)||typeof params!=="object")throw new Error("inputs 必须是 JSON 对象。");
  const defaults=info.ui_schema.defaults;for(const key of Object.keys(params))if(!(key in defaults))throw new Error("无法识别的参数："+key);
  for(const [key,v] of Object.entries(params)){const d=defaults[key];if(typeof v!==typeof d || (typeof d==="number"&&!Number.isFinite(v)))throw new Error("参数类型无效："+key);}
  if(params.cycle_type&&!['ffsc','gg'].includes(params.cycle_type))throw new Error("循环类型无效。");
  if(params.propellant_pair&&!['lox_methane','lox_kerosene'].includes(params.propellant_pair))throw new Error("推进剂组合无效。");
  validateChoices(params);
  draft=mergeInputDefaults(clone(defaults),params,savedInputDocument(value));clearPresetUndo();syncPresetSelection(true);renderFields();setDirty();
}
function validateDraft(){
  if(!$("project-name").value.trim())throw new Error("请在“方案信息与文件”中填写项目名称。");
  validateChoices(draft);
  if(propellantIssue())throw new Error(propellantIssue());
  for(const f of fields){
    const value=draft[f.key];
    if(typeof f.default==="number" && (typeof value!=="number"||!Number.isFinite(value)||(f.type==="int"&&!Number.isInteger(value)))){
      const visible=groups.some(g=>compatible(g)&&g.fields.some(item=>item.key===f.key&&parameterVisible(item,draft)));
      if(visible){$("parameter-search").value=f.label;filterParameters();}
      throw new Error(`${f.label}必须是${f.type==="int"?"整数":"有限数值"}，请检查${visible?"参数":"完整参数 JSON 中的 "+f.key}。`);
    }
  }
}
async function saveProject(){
  if(rawDirty)throw new Error("请先点击“应用 JSON”，确认完整参数。");
  validateDraft();
  const out=await api("/projects/"+current.id,"PUT",{name:$("project-name").value,description:$("project-description").value,inputs:draft,revision:current.revision});
  fillProject(out.project,true);if(out.warnings.length)toast("已保存。参数提示："+out.warnings.join("；"));return out;
}
async function runDesign(){
  if(!current||projectJobs.some(active))return;
  if(rawDirty)throw new Error("请先应用 JSON，再运行设计点。");
  validateDraft();
  await withEditorLock(async()=>{if(dirty)await saveProject();const job=await api(`/projects/${current.id}/jobs`,"POST",{revision:current.revision});selectedJobId=job.id;analysisView="overview";switchPage("project");await refreshProjectSide();toast("设计点已提交，结果会自动显示在右侧。");});
}
async function refreshMembers(){const members=await api("/members");$("members-list").innerHTML=members.map(m=>`<div class="member-row"><div><b>${escape(m.display_name)}</b><p>@${escape(m.username)}</p></div><span class="badge">${m.role==="admin"?"管理员":"成员"}</span></div>`).join("");}
async function showResult(id){
  if(page==="project"){await loadRecord(id);$("result-selector").value=id;analysisView="overview";renderView();switchPage("project");return;}
  modalRecord=await api("/jobs/"+id);$("result-title").textContent=`计算记录 · 方案 v${modalRecord.revision}`;$("result-content").innerHTML=renderAnalysis("overview",modalRecord);$("download-result").hidden=!modalRecord.result;$("result-dialog").showModal();
}
function newProject(){if(editorBusy||!mayLeave())return;$("new-error").textContent="";$("new-dialog").showModal();}
$("auth-form").onsubmit=event=>{event.preventDefault();action($("auth-submit"),"auth-error",async()=>{const data={username:$("auth-username").value,password:$("auth-password").value};if(!initialized)data.display_name=$("auth-display").value;user=await api("/auth/"+(initialized?"login":"setup"),"POST",data);initialized=true;await enterWorkspace();});};
$("logout").onclick=()=>action($("logout"),null,async()=>{if(editorBusy||!mayLeave())return;await api("/auth/logout","POST",{});user=null;current=null;draft=null;resultRecord=null;setDirty(false);showAuth();});
document.querySelectorAll("[data-nav]").forEach(b=>b.onclick=()=>action(b,null,()=>navigate(b.dataset.nav)));
document.querySelectorAll("[data-view]").forEach(b=>b.onclick=()=>action(b,null,()=>chooseView(b.dataset.view)));
$("analysis-content").addEventListener("click",event=>{
  const link=event.target.closest?.("[data-flow-state]");
  if(!link || !$("analysis-content").contains(link))return;
  event.preventDefault();
  const host=$("analysis-content");host.dataset.saRecordId=String(resultRecord?.id || "");
  host.dataset.saSelected=link.dataset.flowState;host.dataset.saGroup=link.dataset.flowGroup;
  chooseView("states");
});
document.querySelectorAll("[data-close]").forEach(b=>b.onclick=()=>$(b.dataset.close).close());
$("new-project").onclick=newProject;
$("new-form").onsubmit=event=>{event.preventDefault();action(event.submitter,"new-error",async()=>{const data=Object.fromEntries(new FormData(event.target));const preset=info.presets.find(p=>p.key===data.preset);const out=await api("/projects","POST",{name:data.name,description:data.description,inputs:preset.inputs});$("new-dialog").close();event.target.reset();fillProject(out.project);switchPage("project");await refreshProjectSide();});};
$("project-name").oninput=$("project-description").oninput=()=>{clearPresetUndo();setDirty();};
$("parameter-search").oninput=$("parameter-group").onchange=filterParameters;
$("raw-inputs").oninput=()=>{clearPresetUndo();rawDirty=true;setDirty();};
$("parameter-fields").oninput=event=>{
  const f=editorField(event.target.dataset.input);if(!f||editorBusy)return;
  if(rawDirty){$("editor-error").textContent="JSON 中有尚未应用的修改，请先应用 JSON 后再修改表单。";event.target.value=displayValue(f);return;}
  const value=event.target.value;
  if(f.virtual){
    if(f.key!==PREBURNER_MODE_KEY)return;
    try{draft=selectPreburnerMode(draft,value);}
    catch(error){$("editor-error").textContent=error.message;event.target.value=displayValue(f);return;}
  }else{
    const canonical=fields.find(field=>field.key===f.key);if(!canonical)return;
    draft[f.key]=typeof canonical.default==="number"?(value===""?null:Number(value)/(canonical.factor||1)):value;
  }
  clearPresetUndo();$("editor-error").textContent="";$("raw-inputs").value=JSON.stringify(draft,null,2);syncPresetSelection();setDirty();
  if(f.virtual||["cycle_type","propellant_pair"].includes(f.key)||(f.choices&&parameterLayoutDependency(f.key,groups)))renderFields();
  else updatePropellantContext();
};
$("parameter-fields").onchange=event=>{const f=editorField(event.target.dataset.input);if(!f||editorBusy||rawDirty||f.virtual||f.choices)return;if(parameterLayoutDependency(f.key,groups))renderFields();};
$("apply-json").onclick=()=>action(null,"editor-error",async()=>{applyRaw();toast("JSON 已应用。");});
$("import-inputs").onchange=event=>action(null,"editor-error",async()=>{const file=event.target.files[0];if(!file)return;try{if(file.size>180000)throw new Error("方案文件超过 180 KB。");if(dirty&&!confirm("导入方案将替换当前参数，是否继续？"))return;const text=await file.text();JSON.parse(text);$("raw-inputs").value=text;rawDirty=true;setDirty();applyRaw();toast("方案已导入，可保存或运行。");}finally{event.target.value="";}});
$("project-preset").onchange=()=>{if(editorBusy){syncPresetSelection();return;}const key=$("project-preset").value,preset=info.presets.find(p=>p.key===key);if(preset)loadPreset(preset);else {if(!key){referenceKey="";clearPresetUndo();}updatePresetContext();}};
$("undo-preset").onclick=undoPreset;
$("open-references").onclick=showReferences;
$("reference-engine").onchange=renderReference;
$("apply-preset").onclick=()=>{const preset=info.presets.find(p=>p.key===$("project-preset").value);if(!preset){toast("该机型仅供资料查阅，当前模型尚不支持此推进剂组合。");return;}loadPreset(preset);};
$("load-propellant-example").onclick=()=>loadPreset(info.presets.find(p=>p.inputs.propellant_pair===draft.propellant_pair&&p.inputs.cycle_type==="gg"));
$("project-form").onsubmit=event=>{event.preventDefault();action(event.submitter,"editor-error",()=>withEditorLock(async()=>{await saveProject();await refreshProjectSide();toast("方案新版本已保存。");}));};
$("reload-project").onclick=()=>action(null,"editor-error",async()=>{if(editorBusy||!mayLeave())return;await withEditorLock(async()=>{fillProject(await api("/projects/"+current.id));await refreshProjectSide();});});
$("export-inputs").onclick=()=>action(null,"editor-error",async()=>{if(rawDirty)throw new Error("请先应用 JSON 再导出。");download({format:"lre.workbench.inputs",schema_version:1,inputs:draft,project_name:$("project-name").value,base_revision:current.revision,unsaved:dirty},"lre-inputs.json");});
$("submit-job").onclick=$("header-run").onclick=()=>action(null,"editor-error",runDesign);
$("result-selector").onchange=()=>action(null,null,()=>loadRecord($("result-selector").value));
$("toggle-parameters").onclick=()=>{const collapsed=document.querySelector(".workbench-layout").classList.toggle("parameters-collapsed");$("toggle-parameters").textContent=collapsed?"展开参数":"收起参数";$("toggle-parameters").setAttribute("aria-expanded",String(!collapsed));};
$("refresh-jobs").onclick=()=>action(null,null,async()=>renderJobs($("all-jobs"),await api("/jobs")));
$("member-form").onsubmit=event=>{event.preventDefault();action(event.submitter,"member-error",async()=>{await api("/members","POST",Object.fromEntries(new FormData(event.target)));event.target.reset();await refreshMembers();toast("成员账号已创建。");});};
$("download-result").onclick=()=>downloadResult(modalRecord);$("export-current-result").onclick=()=>downloadResult(resultRecord);
document.body.addEventListener("click",event=>{const b=event.target.closest("button");if(!b)return;if(b.hasAttribute("data-new"))newProject();if(b.dataset.project)action(b,null,()=>openProject(b.dataset.project));if(b.dataset.job)action(b,null,()=>showResult(b.dataset.job));if(b.dataset.cancel)action(b,null,async()=>{await api("/jobs/"+b.dataset.cancel+"/cancel","POST",{});await poll();toast("已请求取消任务。");});if(b.dataset.revision)action(b,"editor-error",async()=>{if(editorBusy||!mayLeave())return;await withEditorLock(async()=>{const old=await api(`/projects/${current.id}/revisions/${b.dataset.revision}`);draft=mergeInputDefaults(clone(info.ui_schema.defaults),clone(old.inputs),true);clearPresetUndo();syncPresetSelection(true);$("project-name").value=old.name;$("project-description").value=old.description;renderFields();setDirty();toast(`已载入 v${old.revision} 为草稿，保存后将产生新版本。`);});});});
window.addEventListener("beforeunload",event=>{if(dirty){event.preventDefault();event.returnValue="";}});
async function poll(){if(polling||editorBusy||document.hidden)return;polling=true;try{await api("/health");if(!user)return;if(page==="projects")await refreshDashboard();if(page==="jobs")renderJobs($("all-jobs"),await api("/jobs"));if(page==="project"&&current)await refreshProjectSide();}catch(error){if(connected!==false)$("connection-status").textContent=error.message;}finally{polling=false;}}
async function initialize(){const state=await api("/auth/status");initialized=state.initialized;user=state.user;if(user)await enterWorkspace();else showAuth();}
$("retry-connection").onclick=()=>action($("retry-connection"),null,initialize);
setInterval(poll,5000);
try{await initialize();}catch(error){$("auth-title").textContent="连接未完成";$("auth-description").textContent=error.message;$("auth-retry").hidden=false;}
$("auth-retry").onclick=()=>action($("auth-retry"),"auth-error",async()=>{await initialize();$("auth-retry").hidden=true;});
