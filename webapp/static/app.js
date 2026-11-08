import {renderAnalysis, configureResultSchema} from "./results.js";
import {supportsCycleResult} from "./cycle-model.js";
import {mountSteadyWorkspace} from "./steady-workspace.js";
import {mountCoolingDesign} from "./cooling-design.js";
import {mountValidation} from "./validation.js";
import {mountThrottle,mountOperatingThrottle} from "./throttle.js";
import {mountFixedSteady} from "./fixed-steady.js";
import {renderValveRequirements} from "./valve-requirements.js";
import {HUB_VIEWS,isWorkspaceHub,workspaceGroup,hasDesignParameters,renderWorkspaceBreadcrumb,renderParameterNavigation,renderWorkspaceHub,mountWorkspaceLibrary} from "./navigation.js";
import {mountTransient} from "./transient.js";
import {mountFeedNetwork} from "./feed-network.js";
import {mountChilldown} from "./chilldown.js";
import {mountIgnition} from "./ignition.js";
import {mountShutdown} from "./shutdown.js";
import {mountComponentWorkspace} from "./component-analysis.js";
import {mountFlowWorkspace} from "./flow-analysis.js";
import {mountCycleWorkspace} from "./cycle-schematic.js";
import {renderNozzleGeometry,mountNozzleGeometry} from "./nozzle-geometry.js";
import {renderNozzleDesign,mountNozzleDesign} from "./nozzle-design.js";
import {renderChamberDesign,mountChamberDesign} from "./chamber-design.js";
import {renderPumpGeometry,mountPumpGeometry} from "./pump-geometry.js";
import {renderInjectorDesign,mountInjectorDesign} from "./injector-design.js";
import {renderTurbineGeometry,mountTurbineGeometry} from "./turbine-geometry.js";
import {renderTurbineDesign,mountTurbineDesign} from "./turbine-design.js";
import {renderResultConsistency} from "./result-consistency.js";
import {savedInputDocument,platformInputValues,mergeInputDefaults,methaneThermoSummary,upgradeMethaneDatasetDraft,upgradeCoolingModelDraft,PREBURNER_MODE_KEY,selectPreburnerMode,selectPumpSpeedMode,selectTurbineStageMode,parameterValue,parameterVisible,parameterGroups,parameterSearchText,parameterLayoutDependency,renderParameterGroups,renderParameterEvidence,renderAnalysisParameterRoles} from "./parameter-editor.js";
import {renderConceptDesignScope} from "./concept-design.js";
import {collectLocalReadiness,buildDesignReadiness,renderDesignReadiness,designReadinessStatusLabels} from "./design-readiness.js";
import {renderPreburnerInterfaces,renderPreburnerResults} from "./preburner-design.js";
import {coolingInputSummaryState,renderCoolingInputSummary} from "./cooling-input-summary.js";
import {renderCoolingChannelInputs,refreshCoolingChannelStatus,applyCoolingChannelEdit,coolingChannelPendingIssues} from "./cooling-channel-inputs.js";
import {syncPressureWorkspace,renderPressureResults} from "./pressure-design.js";
import {migratePressureReview} from "./design-review-migration.js";

const $ = id => document.getElementById(id);
const escape = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const date = value => value ? new Date(value * 1000).toLocaleString("zh-CN", {hour12:false}) : "—";
const number = value => typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("zh-CN", {maximumSignificantDigits:7}) : "—";
const clone = value => JSON.parse(JSON.stringify(value));
const active = job => job && ["queued","running"].includes(job.status);
const statusNames = {queued:"等待计算",running:"计算中",succeeded:"计算已完成",failed:"执行失败",cancelled:"已取消",timed_out:"计算超时",interrupted:"服务中断"};
const statusClass = value => ["succeeded","pass"].includes(value) ? "success" : ["failed","fail","timed_out","interrupted"].includes(value) ? "failed" : "running";
const badge = (value, label) => `<span class="badge ${statusClass(value)}">${escape(label || statusNames[value] || value)}</span>`;
const views = {overview:["概念设计","设计点循环匹配 · 部件需求与尺寸初估"],topology:["发动机循环原理图","查看推进剂流向、燃气路径与泵—涡轮驱动关系"],states:["流路状态分析","沿流路查看节点状态、压力与温度变化及组分"],components:["部件性能分析","按功能查看部件性能、进出口状态与能量转换"],log:["日志与警告","检查计算状态、警告与运行环境"],history:["方案与计算记录","查看方案版本、计算快照与历史结果"]};
let user=null, initialized=false, info=null, page="projects", analysisView="overview";
let privateWorkspace=false, personalWorkspace=false, remoteWorkspace=globalThis.location?.protocol==="https:";
let current=null, draft=null, dirty=false, resultRecord=null, modalRecord=null;
const specifiedPumpSpeeds = new Map();
const specifiedTurbineStages = new Map();
let coolingChannelEditor = null;
let fields=[], groups=[], projectJobs=[], selectedJobId=null, polling=false, editorBusy=false, connected=null;
let toastTimer;
let refreshSequence=0;
let referenceKey="", presetUndo=null;
let designReadiness=null, designReadinessTimer=null;
let designGroupReviews=new Map();
let designReviewRestore=Promise.resolve();
let pressureWorkspaceState={view:"budget",branch:"ox_to_opb"};
let validationView=null;
let throttleView=null;
let operatingThrottleView=null;
let fixedSteadyView=null;
let transientView=null;
let feedView=null;
let chilldownView=null;
let ignitionView=null;
let shutdownView=null;
let workspaceUserId=null;
let passwordBusy=false;
let nozzleGeometryCleanup=null;
let nozzleDesignCleanup=null;
let chamberDesignCleanup=null;
let pumpGeometryCleanup=null;
let injectorDesignCleanup=null;
let turbineGeometryCleanup=null;
let turbineDesignCleanup=null;
let recordLoadSequence=0;
let coolingDesignView=null;
let conceptSection="settings",libraryTab="components",libraryCleanup=null;
views.results_overview=["计算结果总览","查看所选计算记录 · 性能、系统校核与模型依据"];
views.solver_diagnostics=["收敛诊断","查看所选计算记录 · 迭代过程、残差与失败定位"];
views.geometry=["推力室内型面","燃烧室、收缩段与喷管 · 保存设计点的尺寸核对与 CAD 坐标交接"];
views.nozzle_design=["喷管设计与性能","保存设计点 · 推力分解、膨胀状态、热环境与总体设计校核"];
views.chamber_design=["主燃烧室设计与分析","总体设计 · 两股入口燃气、平衡热化学、主室几何与冷却交接"];
views.pump_geometry=["泵设计与建模交接","液氧泵与甲烷泵 · 设计点、吸入条件、部件配置与 CFturbo 建模参考"];
views.injector_design=["喷注器初步设计","FFSC 三处喷注接口 · 主喷注单元尺寸与流量一致性"];
views.turbine_geometry=["涡轮设计与建模交接","富氧与富燃涡轮 · 循环边界、级方案初选与 CFturbo 建模"];
views.shutdown=["关机与再启动分析","连续状态 · 关阀、残余库存、壁面余热、轴系惰转与再次供给"];
views.ignition=["点火事件与建压分析","气相反应容腔 · 给定外部点火热脉冲、阀门响应与压力建立"];
views.chilldown=["预冷与充填分析","壁面热惯性 · 真实流体气液库存、两相充填与初始状态快照"];
views.feed=["供给网络动态","单相液体供给子系统 · 管路惯性、容腔库存、低速泵图与阀门执行器"];
views.validation=["热力学校核","同条件热化学对比、系统守恒、真实流体物性与试验验证"];
views.transient=["瞬态响应分析","热态工作点附近的局部扰动 · 零维质量、能量与转子惯性积分"];
views.throttle=["设计匹配扫描历史","只读历史记录 · 原设计匹配口径保留，不作为固定硬件运行结果"];
views.operating_throttle=["发动机节流分析","固定设计几何 · 目标推力百分比 · 基准标定局部模型与逐点性能"];
views.fixed_steady=["固定硬件稳态","只读硬件快照 · 独立工况与待补数据接口 · 通用数值框架自检"];
Object.assign(views,HUB_VIEWS);

function designScopeIssue(inputs){
  const scope={cycle_type:"ffsc",propellant_pair:"lox_methane"};
  const cycle=inputs?.cycle_type===undefined?"ffsc":inputs.cycle_type,propellant=inputs?.propellant_pair===undefined?"lox_methane":inputs.propellant_pair;
  return cycle===scope.cycle_type&&propellant===scope.propellant_pair?"":"当前平台仅支持液氧甲烷全流量补燃循环（FFSC）。其他循环或推进剂的历史记录仅供原始数据导出，请新建 FFSC 方案。";
}
function archivedProject(){return Boolean(current&&designScopeIssue(current.inputs));}
function recordScopeIssue(record){
  return supportsCycleResult(record?.result,record?.inputs)?"":"当前平台仅支持液氧甲烷全流量补燃循环（FFSC）。此历史记录仅供原始数据导出。";
}
function requireEditableDesign(inputs=draft){
  const issue=designScopeIssue(current?.inputs)||designScopeIssue(inputs);
  if(issue)throw new Error(issue);
}
const archivedViews=new Set(["history"]);

function setNavigationOpen(open){
  $("workspace").classList.toggle("nav-open",open);
  $("navigation-backdrop").hidden=!open;
  $("toggle-navigation").dataset.open=String(open);
  $("toggle-navigation").setAttribute("aria-expanded",String(open));
  $("toggle-navigation").setAttribute("aria-label",open?"关闭工作区导航":"打开工作区导航");
}
function syncWorkspaceNavigation(){
  const group=page==="project"?workspaceGroup(analysisView):null;
  document.querySelectorAll("[data-nav-group]").forEach(node=>{
    const selected=node.dataset.navGroup===group?.id;
    node.classList.toggle("is-current",selected);
    if(group)node.open=selected;
  });
  document.querySelectorAll("[data-view]").forEach(button=>button.setAttribute("aria-current",page==="project"&&button.dataset.view===analysisView?"page":"false"));
}

// Tab-local navigation only: drafts, credentials and results remain outside storage.
function navigationKey(){return user?.id==null?null:`lre.workspace.navigation.v1:${user.id}`;}
function readNavigation(){
  try{
    const key=navigationKey(),saved=key?JSON.parse(sessionStorage.getItem(key)):null;
    if(!saved||saved.version!==1||!["project","projects","jobs","members"].includes(saved.page))return null;
    return {page:availableWorkspacePage(saved.page),
      projectId:typeof saved.projectId==="string"?saved.projectId:null,
      view:Object.hasOwn(views,saved.view)?saved.view:"overview",
      jobId:typeof saved.jobId==="string"?saved.jobId:null,
      conceptSection:["settings","results","diagnostics"].includes(saved.conceptSection)?saved.conceptSection:"settings",
      libraryTab:["components","characteristics","properties","numerics"].includes(saved.libraryTab)?saved.libraryTab:"components"};
  }catch{return null;}
}
function rememberNavigation(){
  try{
    const key=navigationKey();if(!key)return;
    sessionStorage.setItem(key,JSON.stringify({version:1,page,projectId:current?.id||null,view:analysisView,jobId:selectedJobId,conceptSection,libraryTab}));
  }catch{/* Navigation remains usable if browser storage is unavailable. */}
}

function parameterGroupKey(){return user?.id!=null&&current?.id!=null?`lre.workspace.parameter-group.v1:${user.id}:${current.id}`:null;}
function readParameterGroup(){
  try{const key=parameterGroupKey();return key?sessionStorage.getItem(key)||"":"";}
  catch{return "";}
}
function rememberParameterGroup(){
  try{
    const key=parameterGroupKey(),selected=$("parameter-group").value;
    if(key&&draft&&parameterGroups(groups,draft).some(group=>group.id===selected))sessionStorage.setItem(key,selected);
  }catch{/* Parameter editing also works when browser storage is unavailable. */}
}

function toast(message) { $("toast").textContent=message; $("toast").hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$("toast").hidden=true,5500); }
function projectsLabel(){return personalWorkspace?"我的项目":privateWorkspace?(user?.role==="admin"?"试用项目":"我的项目"):"共享项目";}
function canManageMembers(){return !personalWorkspace&&user?.role==="admin";}
function availableWorkspacePage(next){return next==="members"&&!canManageMembers()?"projects":next;}
function accessLabels(state){
  privateWorkspace=state.workspace_access==="private";
  personalWorkspace=state.workspace_access==="personal";
  remoteWorkspace=state.remote??(state.scope==="external-pilot");
  $("auth-scope-note").textContent=personalWorkspace?"个人工作区 · 本机使用":remoteWorkspace?"校外试用 · 浏览器访问":privateWorkspace?"试用工作区 · 本机初始化":"本机工作区";
  $("auth-storage-note").textContent=personalWorkspace?"此工作区仅供本人使用，方案与结果保存在本机。":privateWorkspace?"方案与结果保存在平台主机。普通账号仅访问自己的项目，管理员可管理全部试用项目。":"数据保存在本机。管理员可创建成员账号，成员共享本工作区项目。";
  for(const id of ["projects-heading","projects-nav-label"])$(id).textContent=projectsLabel();
  $("projects-description").textContent=personalWorkspace?"管理自己的方案、修订与计算记录。":privateWorkspace?(user?.role==="admin"?"管理各账号的试用方案与计算记录。":"创建自己的方案，调整参数并运行计算。"):"把方案、修订与计算记录放在一起。工作区内的成员可访问全部项目。";
  $("members-heading").textContent=personalWorkspace?"个人工作区":(privateWorkspace?(remoteWorkspace?"校外试用":"试用工作区"):"本机工作区")+" · 成员管理";
  $("members-description").textContent=personalWorkspace?"":privateWorkspace?"本页仅显示试用工作区账号，与本机个人工作区分开。普通账号仅使用自己的方案和结果。":"本页仅显示本机工作区账号，与校外试用账号分开。成员共享本工作区的项目和结果。";
  $("members-nav").hidden=!canManageMembers();
  if(!canManageMembers()){$("members-page").hidden=true;$("members-list").innerHTML="";}
  $("new-project-title").textContent=personalWorkspace||privateWorkspace?"新建我的项目":"新建共享项目";
  $("workspace-location").textContent=remoteWorkspace?"校外试用":"本机";
  $("sidebar-location").textContent=personalWorkspace?"个人工作区":privateWorkspace?"独立试用工作区":"本机工作区";
}
function connection(ok) {
  connected=ok;
  $("connection-status").dataset.state=ok?"online":"offline";
  $("connection-status").textContent=ok?"计算服务已连接":"计算服务连接中断";
  $("connection-banner").hidden=ok;
  $("workspace").classList.toggle("disconnected",!ok);
  $("connection-message").textContent=remoteWorkspace?"连接中断，当前编辑仍保留。请稍后重试，或联系平台管理员。":"连接中断，当前编辑仍保留。请双击 start_web_windows.bat 启动服务，再重新连接。";
  updateActionState();
}
async function api(path, method="GET", data) {
  const controller=new AbortController(), timeout=setTimeout(()=>controller.abort(),15000);
  const interrupted=error=>new Error(controller.signal.aborted||error.name==="AbortError"
    ?"服务响应超时，编辑已保留。保存或提交可能已完成，请重连后检查记录。"
    :remoteWorkspace?"无法连接计算服务，请稍后重试或联系平台管理员。":"无法连接本机服务。请运行 start_web_windows.bat，并保持服务窗口开启。");
  const responseError=(response,message,code)=>{
    connection(false);
    const error=new Error(message);error.status=response.status;error.code=code;return error;
  };
  const accessMessage="校外入口授权未保持或已失效。请在同一浏览器窗口重新打开平台首页，完成访问验证；若验证后立即出现，请联系管理员处理访问入口。当前编辑已保留。";
  try {
    let response;
    try { response=await fetch("/api"+path,{method,credentials:"same-origin",redirect:"manual",signal:controller.signal,
      headers:data===undefined?{}:{"Content-Type":"application/json"},body:data===undefined?undefined:JSON.stringify(data)}); }
    catch(error) {connection(false);throw interrupted(error);}
    // An access gateway may redirect even a login POST. Never forward its body or replay a write.
    if(response.type==="opaqueredirect"||(response.status>=300&&response.status<400))
      throw responseError(response,accessMessage,"access_authorization");
    const contentType=(response.headers.get("Content-Type")||"").split(";",1)[0].trim().toLowerCase();
    const mime=/^[a-z0-9!#$&^_.+-]+\/[a-z0-9!#$&^_.+-]+$/.test(contentType)&&contentType.length<=100?contentType:"未知类型";
    if(!/^application\/(?:[a-z0-9!#$&^_.+-]+\+)?json$/.test(contentType)){
      if(response.status===401||response.status===403)
        throw responseError(response,accessMessage,"access_authorization");
      const message=response.status>=500?"计算服务或访问入口返回异常":"服务响应格式异常";
      throw responseError(response,`${message}（HTTP ${response.status}；${mime}）。请稍后重试或联系平台管理员。当前编辑已保留；保存或提交结果请先检查记录。`,"response_format");
    }
    let output;
    try {output=await response.json();}
    catch(error) {
      if(controller.signal.aborted||error.name==="AbortError"){connection(false);throw interrupted(error);}
      throw responseError(response,`服务响应内容不完整或格式异常（HTTP ${response.status}；${mime}）。请稍后重试或联系平台管理员。当前编辑已保留；保存或提交结果请先检查记录。`,"response_format");
    }
    connection(true);
    if(!response.ok) {
      if(response.status===401 && user){user=null;showAuth();}
      const error=new Error(typeof output?.detail==="string"?output.detail:"操作未完成，请检查输入。");error.status=response.status;throw error;
    }
    return output;
  } finally {clearTimeout(timeout);}
}
async function action(button,errorId,task) {
  if(errorId==="editor-error")setEditorError("");else if(errorId) $(errorId).textContent="";
  if(button) button.disabled=true;
  try {await task();} catch(error){if(errorId==="editor-error")setEditorError(error.message);else if(errorId) $(errorId).textContent=error.message;else toast(error.message);}
  finally {if(button) button.disabled=false;updateActionState();}
}
function download(value,filename) {
  const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:"application/json"}));
  const link=document.createElement("a");link.href=url;link.download=filename;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);
}
function downloadResult(record) {if(record?.result) download(record,"lre-result-"+record.id+".json");}
function setEditorError(message){
  $("editor-error").textContent=message;$("project-history-error").textContent=message;
  $("workspace-error").textContent=message;$("workspace-error").hidden=!message;
}
function setDirty(value=true) {
  dirty=value;
  for(const id of ["dirty-label","project-dirty-label"]){$(id).textContent=value?"尚未保存":"已保存";$(id).className="badge"+(value?" dirty":" success");}
  if(current) $("document-title").textContent=$("project-name").value+(value?" · 已修改":"");
  updateResultContext();
  if(analysisView==="validation") validationView?.sync();
  if(analysisView==="throttle") throttleView?.sync();
  if(analysisView==="operating_throttle") operatingThrottleView?.sync();
  if(analysisView==="transient") transientView?.sync();
  if(analysisView==="feed") feedView?.sync();
  if(analysisView==="chilldown") chilldownView?.sync();
  if(analysisView==="ignition") ignitionView?.sync();
  if(analysisView==="shutdown") shutdownView?.sync();
  coolingDesignView?.sync();
}
function mayLeave(){return !dirty || confirm("当前编辑尚未保存。确定放弃这些编辑？");}
function designReadinessEnabled(){return info?.design_readiness===true&&Boolean(current&&draft)&&!archivedProject();}
function canRunDesignPoint(){
  return designReadiness?.phase==="ready"&&designReadiness.pendingReviewCount===0;
}
function readinessRunMessage(){
  if(archivedProject())return "历史方案只读 · 可查看与导出已有记录";
  if(!designReadinessEnabled())return "就绪 · 可运行当前设计点";
  if(designReadiness?.phase==="ready"&&designReadiness.pendingReviewCount>0)
    return `输入校验通过 · ${designReadiness.pendingReviewCount} 个分组待确认`;
  return {ready:"输入检查通过 · 可运行设计点",invalid:"请修正输入检查列出的参数后再运行。",
    unavailable:"输入检查未完成。请在设计输入中重试。"}[designReadiness?.phase]||"正在检查设计点输入…";
}
function updateActionState(){
  document.querySelectorAll("[data-view],[data-nav]").forEach(button=>button.disabled=editorBusy);
  document.querySelectorAll("[data-workspace-view],[data-workspace-action],[data-concept-section]").forEach(button=>button.disabled=editorBusy);
  const running=projectJobs.find(active), preflightBlocked=designReadinessEnabled()&&!canRunDesignPoint();
  for(const id of ["header-run","submit-job"]){
    $(id).disabled=!hasDesignParameters(analysisView)||!current||editorBusy||connected===false||!!running||!!propellantIssue()||preflightBlocked;
    $(id).title=preflightBlocked?readinessRunMessage():"";
  }
  const progress=running?`${statusNames[running.status]} · 方案 v${running.revision}${running.progress?` · 迭代 ${running.progress.iteration}/${running.progress.maximum} · 残差 ${number(running.progress.residual)}`:""}`:readinessRunMessage();
  if($("run-progress").textContent!==progress)$("run-progress").textContent=progress;
  for(const id of ["header-save","history-save"])$(id).disabled=(!hasDesignParameters(analysisView)&&analysisView!=="history") || !current || editorBusy || connected===false || archivedProject();
  for(const id of ["choose-import","import-inputs","export-inputs","reload-project","upgrade-methane-dataset"])$(id).disabled=!current || editorBusy;
  for(const id of ["choose-import","import-inputs","upgrade-methane-dataset","project-preset","project-name","project-description"])$(id).disabled=editorBusy||archivedProject();
  if(archivedProject())$("apply-preset").disabled=true;
  const cancellable=projectJobs.find(j=>active(j)&&(user?.role==="admin"||user?.id===j.submitted_by));
  $("header-cancel").hidden=page!=="project"||!cancellable;
  $("header-cancel").disabled=editorBusy||connected===false;
  $("header-cancel").dataset.cancel=cancellable?.id||"";
}
async function withEditorLock(task){
  if(editorBusy) return;
  editorBusy=true;const containers=[$("project-form"),$("project-history")];
  containers.forEach(el=>el.inert=true);
  const controls=containers.flatMap(el=>[...el.querySelectorAll("input,select,textarea,button")]);
  const disabled=controls.map(el=>el.disabled);controls.forEach(el=>el.disabled=true);updateActionState();
  try{return await task();}finally{controls.forEach((el,i)=>el.disabled=disabled[i]);containers.forEach(el=>el.inert=false);editorBusy=false;updateActionState();}
}
function showAuth(){
  if(nozzleDesignCleanup){nozzleDesignCleanup();nozzleDesignCleanup=null;}
  clearTimeout(designReadinessTimer);designReadinessTimer=null;designReadiness=null;
  designGroupReviews=new Map();
  if(pumpGeometryCleanup){pumpGeometryCleanup();pumpGeometryCleanup=null;}
  if(nozzleGeometryCleanup){nozzleGeometryCleanup();nozzleGeometryCleanup=null;}
  validationView?.onLeave();
  throttleView?.onLeave();
  operatingThrottleView?.onLeave();
  fixedSteadyView?.onLeave();
  transientView?.onLeave();
  feedView?.onLeave();
  chilldownView?.onLeave();
  ignitionView?.onLeave();
  shutdownView?.onLeave();
  $("auth").hidden=false;$("workspace").hidden=true;$("auth-form").hidden=false;$("display-label").hidden=initialized;
  $("auth-display").required=!initialized;$("auth-password").minLength=1;
  $("auth-password").autocomplete=initialized?"current-password":"new-password";
  $("auth-title").textContent=initialized?"欢迎回到工作台":"创建你的工作区";
  $("auth-description").textContent=initialized?"登录后进入设计工作台，继续编辑与计算。":personalWorkspace?"首次使用，请创建个人账号。密码必填，最多 128 位。":"首次使用，请创建管理员账号。密码必填，最多 128 位。";
  $("auth-submit").textContent=initialized?"进入工作台 →":personalWorkspace?"创建个人账号并进入 →":"创建管理员并进入 →";
}
async function enterWorkspace(){
  if(workspaceUserId!==null&&workspaceUserId!==user.id){current=null;draft=null;resultRecord=null;selectedJobId=null;projectJobs=[];dirty=false;analysisView="overview";page="projects";}
  workspaceUserId=user.id;
  info=await api("/info");
  accessLabels(info);
  if(!info.ui_schema?.groups) throw new Error("页面已更新，请先停止旧服务，再运行 start_web_windows.bat 载入新版工作台。");
  groups=info.ui_schema.system_groups||info.ui_schema.groups;fields=info.ui_schema.groups.flatMap(g=>g.fields);
  configureResultSchema(info.ui_schema);
  $("auth").hidden=true;$("workspace").hidden=false;$("auth-password").value="";$("auth-error").textContent="";
  $("user-label").textContent=`${user.display_name} · ${personalWorkspace?"个人账号":user.role==="admin"?"管理员":"成员"}`;$("members-nav").hidden=!canManageMembers();
  $("runtime-version").textContent=`Python ${info.python} · Cantera / CoolProp`;
  const options=info.presets.filter(p=>p.routine_design!==false).map(p=>`<option value="${escape(p.key)}">${escape(p.label)}</option>`).join("");
  const compatibilityOptions=info.presets.filter(p=>p.routine_design===false).map(p=>`<option value="${escape(p.key)}" disabled>${escape(p.label)} · 历史预算</option>`).join("");
  $("new-preset").innerHTML=options;
  const references=(info.reference_engines||[]).filter(r=>!r.applicable);
  $("project-preset").innerHTML='<option value="">自定义方案（沿用当前值）</option><optgroup label="选择即载入工程示例">'+options+'</optgroup>'+(compatibilityOptions?'<optgroup label="历史方案标识 · 不作为常规新建入口">'+compatibilityOptions+'</optgroup>':"")+(references.length?'<optgroup label="公开机型资料 · 尚不支持计算">'+references.map(r=>`<option value="${escape(r.key)}">${escape(r.name)} · 仅资料</option>`).join("")+'</optgroup>':"");
  $("reference-engine").innerHTML=(info.reference_engines||[]).map(r=>`<option value="${escape(r.key)}">${escape(r.name)}</option>`).join("");
  if(current){
    if(dirty)syncDesignReadiness(true);
    else {
      await restoreDesignGroupReviews(current.design_reviews);
      renderFields();
      if(designReadiness?.phase==="unavailable")syncDesignReadiness(true);
    }
    syncPresetSelection();
    if(page==="project"){renderView();switchPage("project");await refreshProjectSide();}
    else await navigate(page);
  }else{
    const projects=await api("/projects"),saved=readNavigation();
    if(saved){
      const project=projects.find(p=>p.id===saved.projectId);
      if(project)await openProject(project.id,saved);
      if(saved.page!=="project")await navigate(saved.page);
      else if(!project)await navigate("projects");
    }else if(projects.length)await openProject(projects[0].id);
    else await navigate("projects");
  }
}
function switchPage(next){
  next=availableWorkspacePage(next);
  if(next!=="project" && nozzleDesignCleanup){nozzleDesignCleanup();nozzleDesignCleanup=null;}
  if(next!=="project" && pumpGeometryCleanup){pumpGeometryCleanup();pumpGeometryCleanup=null;}
  if(next!=="project" && nozzleGeometryCleanup){nozzleGeometryCleanup();nozzleGeometryCleanup=null;}
  if(next!=="project") validationView?.onLeave();
  if(next!=="project") throttleView?.onLeave();
  if(next!=="project") operatingThrottleView?.onLeave();
  if(next!=="project") fixedSteadyView?.onLeave();
  if(next!=="project") transientView?.onLeave();
  if(next!=="project") feedView?.onLeave();
  if(next!=="project") chilldownView?.onLeave();
  if(next!=="project") ignitionView?.onLeave();
  if(next!=="project") shutdownView?.onLeave();
  page=next;for(const name of ["projects","project","jobs","members"]) $(name+"-page").hidden=name!==next;
  $("page-name").textContent=next==="project"?views[analysisView][0]:{projects:projectsLabel(),jobs:"计算任务",members:"成员管理"}[next];
  $("header-run").hidden=next!=="project" || analysisView!=="overview";
  $("header-save").hidden=next!=="project" || analysisView!=="overview" || conceptSection==="settings";
  document.querySelectorAll("[data-nav]").forEach(b=>b.classList.toggle("active",b.dataset.nav===next));
  document.querySelectorAll("[data-view]").forEach(b=>b.classList.toggle("active",next==="project" && b.dataset.view===analysisView));
  syncWorkspaceNavigation();setNavigationOpen(false);
  if(next!=="project"){$("document-title").textContent=personalWorkspace?"个人工作区":privateWorkspace?"试用工作区":"团队工作区";$("document-meta").textContent="方案 · 版本与计算记录";}else updateDocument();
  updateActionState();
  rememberNavigation();
}
async function navigate(next){
  next=availableWorkspacePage(next);
  if(editorBusy || (page==="project"&&!mayLeave())) return;
  if(page==="project" && dirty){specifiedPumpSpeeds.clear();specifiedTurbineStages.clear();coolingChannelEditor=null;draft=mergeInputDefaults(clone(info.ui_schema.defaults),clone(current.inputs),true);restoreDesignGroupReviews(current.design_reviews);$("project-name").value=current.name;$("project-description").value=current.description;clearPresetUndo();syncPresetSelection(true);renderFields();}
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
  const scopeIssue=designScopeIssue(draft);if(scopeIssue)return scopeIssue;
  const model=currentPropellant();if(!model||!draft)return "";
  if(!model.supported_cycles.includes(draft.cycle_type))return "当前平台仅支持液氧甲烷全流量补燃循环（FFSC）。";
  return "";
}
function updatePropellantContext(){
  const model=currentPropellant(),issue=propellantIssue();
  $("propellant-model-label").textContent=model?.label||"液氧 / 甲烷";
  if((draft?.propellant_pair||"lox_methane")==="lox_methane"){
    const view=methaneThermoSummary(draft,info?.ui_schema?.methane_thermo_datasets);
    $("propellant-model-note").innerHTML=view.summary;
    $("propellant-model-limits").innerHTML=view.details;
    $("propellant-model-details").hidden=false;
    $("upgrade-methane-dataset").hidden=!view.historical;
  }else{
    $("propellant-model-note").textContent=model?.limitations.slice(0,2).join(" ")||"推进剂模型未识别。";
    $("propellant-model-details").hidden=!model||model.limitations.length<=2;
    $("propellant-model-limits").textContent=model?.limitations.slice(2).join(" ")||"";
    $("upgrade-methane-dataset").hidden=true;
  }
  $("propellant-model-error").textContent=issue;
  syncDesignReadiness();
}
const sameInputs=(a,b)=>Object.keys({...a,...b}).every(key=>a[key]===b[key]);
const sameArchitecture=(a,b)=>a.cycle_type===b.cycle_type && (a.propellant_pair||"lox_methane")===(b.propellant_pair||"lox_methane");
function clearPresetUndo(){presetUndo=null;$("undo-preset").hidden=true;}
function syncPresetSelection(infer=false){
  if(infer)referenceKey=info.presets.find(p=>sameInputs({...info.ui_schema.defaults,...p.inputs},draft))?.key||"";
  const preset=info.presets.find(p=>p.key===referenceKey);
  if(!preset || !sameArchitecture(preset.inputs,draft))referenceKey="";
  $("project-preset").value=referenceKey;updatePresetContext();
}
function loadPreset(preset){
  if(!preset||editorBusy)return;
  const scopeIssue=designScopeIssue(current?.inputs)||designScopeIssue(preset.inputs);
  if(scopeIssue){toast(scopeIssue);return;}
  if(preset.routine_design===false){toast("该示例保留为历史预算兼容，不作为常规新设计入口。已有方案可继续从记录页载入。");return;}
  presetUndo={draft:clone(draft),dirty,referenceKey,groupReviews:new Map(designGroupReviews),pumpSpeeds:new Map(specifiedPumpSpeeds),turbineStages:new Map(specifiedTurbineStages),coolingChannelEditor:clone(coolingChannelEditor),
    group:$("parameter-group").value,search:$("parameter-search").value};
  specifiedPumpSpeeds.clear();specifiedTurbineStages.clear();coolingChannelEditor=null;
  draft={...clone(info.ui_schema.defaults),...platformInputValues(clone(preset.inputs))};referenceKey=preset.key;
  adoptPresetGroupValues();
  $("parameter-search").value="";$("parameter-group").value=groups.find(g=>g.fields.some(f=>f.key==="cycle_type"))?.id||"";
  setEditorError("");renderFields();syncPresetSelection();setDirty();$("undo-preset").hidden=false;
  toast("已用整套示例参数替换当前草稿。尚未保存，可撤销本次载入。");
}
function undoPreset(){
  if(!presetUndo||editorBusy)return;
  const previous=presetUndo;draft=previous.draft;referenceKey=previous.referenceKey;
  specifiedPumpSpeeds.clear();specifiedTurbineStages.clear();coolingChannelEditor=null;
  for(const [key,rpm] of previous.pumpSpeeds||[])specifiedPumpSpeeds.set(key,rpm);
  for(const [key,stages] of previous.turbineStages||[])specifiedTurbineStages.set(key,stages);
  designGroupReviews=previous.groupReviews||new Map();coolingChannelEditor=previous.coolingChannelEditor||null;
  $("parameter-search").value=previous.search;$("parameter-group").value=previous.group;
  renderFields();
  syncPresetSelection();setDirty(previous.dirty);clearPresetUndo();setEditorError("");
  toast("已恢复切换前的参数与编辑内容。");
}
function updatePresetContext(){
  const key=$("project-preset").value, reference=(info.reference_engines||[]).find(r=>r.key===key||r.preset_key===key);
  const preset=info.presets.find(p=>p.key===key);
  const applicable=Boolean(preset&&preset.routine_design!==false);
  $("apply-preset").disabled=!applicable;
  $("preset-context").textContent=preset?.routine_design===false?"本方案保留历史预算模型，可读取、保存和复现；该示例不作为常规新设计入口。":applicable&&preset.display_description?preset.display_description:reference?`${reference.propellants} · ${reference.cycle_label}。${reference.applicable?"":` ${reference.compatibility_reason} 当前计算方案保持不变。`}`:applicable?"可重新载入此示例的整套参数。":"自定义方案沿用当前草稿值。请逐组核对，也可以直接确认采用当前值。";
}
function renderReference(){
  const reference=(info.reference_engines||[]).find(r=>r.key===$("reference-engine").value);
  if(!reference){$("reference-content").textContent="暂无参考资料。请重新连接新版服务。";return;}
  const safeLink=(url,title)=>{
    let valid=false;
    try{const parsed=new URL(url);valid=/^https:\/\//i.test(url)&&parsed.protocol==="https:"&&Boolean(parsed.hostname)&&!parsed.username&&!parsed.password;}catch{}
    return valid?`<a href="${escape(url)}" target="_blank" rel="noopener noreferrer">${escape(title)}</a>`:escape(title);
  };
  const sourceDetails=source=>[
    source.publisher?`发布机构：${source.publisher}`:"",
    source.published_on||source.source_date?`发布日期：${source.published_on||source.source_date}`:"",
    source.source_locator||source.locator?`来源定位：${source.source_locator||source.locator}`:"",
    source.scope?`支持范围：${source.scope}`:"",
    source.checked_on?`资料核验：${source.checked_on}`:"",
    source.reviewed_on?`说明更新：${source.reviewed_on}`:"",
    source.verification_note?`核验说明：${source.verification_note}`:"",
  ].filter(Boolean).map(text=>`<p class="field-provenance">${escape(text)}</p>`).join("");
  const historicalBudget=info.presets.some(p=>p.key===reference.preset_key&&p.routine_design===false);
  const compatibility=historicalBudget?"此工程示例采用历史预算冷却模型，不作为常规新设计入口。已有方案可从“方案与计算记录”载入复现；此处保留公开资料供参考。":reference.compatibility_reason;
  const facts=(reference.reference_facts||[]).map(f=>`<tr><td>${escape(f.label)}</td><td>${escape(f.value_display??f.value)}</td><td>${safeLink(f.source_url||f.url,f.source_title||"逐条来源未登记")}${sourceDetails(f)}</td></tr>`).join("");
  const sources=(reference.sources||[]).map(s=>`<article><b>${safeLink(s.url||s.source_url,s.title||s.source_title||"来源标题未登记")}</b>${sourceDetails(s)}</article>`).join("");
  $("reference-content").innerHTML=`<div class="reference-heading"><h3>${escape(reference.name)}</h3><span class="badge">${reference.applicable&&!historicalBudget?"可加载工程示例":"仅公开资料"}</span></div><p class="reference-propellants">${escape(reference.propellants)} · ${escape(reference.cycle_label)}</p><p class="reference-compatibility">${escape(compatibility)}</p>${reference.shaft_note?`<p class="reference-shaft">${escape(reference.shaft_note)}</p>`:""}<h4>参考对象公开信息</h4><p class="fine">公开资料仅支持各条目注明的范围，不代表整套示例参数均经厂商确认。</p><div class="table-wrap"><table><thead><tr><th>资料项</th><th>公开信息</th><th>逐条来源与支持范围</th></tr></thead><tbody>${facts}</tbody></table></div><p class="fine">${escape(reference.model_note)}</p><div class="reference-sources"><b>资料清单与核验记录</b>${sources}</div>`;
  $("reference-content").innerHTML+=renderParameterEvidence(reference.parameter_evidence,fields);
}
function showReferences(){
  const key=$("project-preset").value,reference=(info.reference_engines||[]).find(r=>r.key===key||r.preset_key===key);
  $("reference-engine").value=reference?.key||info.reference_engines?.[0]?.key||"";
  renderReference();$("reference-dialog").showModal();
}
const displayValue=f=>parameterValue(f,draft);
const editorField=key=>parameterGroups(groups,draft).flatMap(g=>g.fields).find(f=>f.key===key)||fields.find(f=>f.key===key);
function designGroupSignature(group,inputs=draft){
  const values=group.fields.map(field=>[field.key,field.virtual?parameterValue(field,inputs):inputs[field.key]]);
  if(group.geometry_source_key)values.push([group.geometry_source_key,inputs[group.geometry_source_key]]);
  const context=group.configuration_context?[group.configuration_context].flat():[];
  for(const item of context)values.push([item.key,inputs[item.key]]);
  const architecture=[inputs.cycle_type,inputs.propellant_pair,null];
  return JSON.stringify([info.ui_schema.version,architecture,values]);
}
function presetGroupReviews(inputs=draft){
  return new Map(parameterGroups(groups,inputs).map(group=>[
    group.id,{kind:"preset",signature:designGroupSignature(group,inputs)},
  ]));
}
function adoptPresetGroupValues(){designGroupReviews=presetGroupReviews();}
function applicableGroupReviews(available){
  const reviews=new Map();
  for(const group of available){
    const review=designGroupReviews.get(group.id);
    if(review&&review.signature===designGroupSignature(group))reviews.set(group.id,review.kind);
  }
  for(const id of designGroupReviews.keys())if(!reviews.has(id))designGroupReviews.delete(id);
  return reviews;
}
async function designReviewDigest(signature){
  const digest=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(signature));
  return "sha256:"+Array.from(new Uint8Array(digest),byte=>byte.toString(16).padStart(2,"0")).join("");
}
async function encodeDesignGroupReviews(reviews){
  return Object.fromEntries(await Promise.all(Object.entries(reviews).map(async([id,review])=>
    [id,{kind:review.kind,signature:await designReviewDigest(review.signature)}])));
}
function restoreDesignGroupReviews(reviews){
  const target=new Map();designGroupReviews=target;
  const projectId=current?.id,revision=current?.revision,userId=user?.id;
  const pending=parameterGroups(groups,draft).map(async group=>{
    const review=reviews?.[group.id];
    if(!review||!["confirmed","preset"].includes(review.kind)||typeof review.signature!=="string")return null;
    const signature=designGroupSignature(group);
    // Accept the original uncompressed representation when opening an older record.
    const matches=review.signature===signature||review.signature===await designReviewDigest(signature);
    if(matches)return [group.id,{kind:review.kind,signature}];
    const migrated=await migratePressureReview({reviews,inputs:draft,group,
      signatureFor:(legacy,inputs)=>designGroupSignature(legacy,inputs),digest:designReviewDigest});
    return migrated?[group.id,migrated]:null;
  });
  designReviewRestore=Promise.all(pending).then(entries=>{
    if(designGroupReviews!==target||current?.id!==projectId||current?.revision!==revision||user?.id!==userId)return;
    for(const entry of entries)if(entry)target.set(...entry);
    applicableGroupReviews(parameterGroups(groups,draft));
    paintDesignReadiness();
  }).catch(()=>{
    if(designGroupReviews===target)setEditorError("参数确认记录未能校验，请重新确认相关分组。");
  });
  return designReviewRestore;
}
function savedDesignGroupReviews(){
  applicableGroupReviews(parameterGroups(groups,draft));
  return Object.fromEntries(designGroupReviews);
}
async function confirmDesignGroup(id){
  if(editorBusy||!designReadinessEnabled()||designReadiness?.phase!=="ready")return;
  requireEditableDesign();
  if($("parameter-search").value.trim()){openReadinessField(id);return;}
  const group=parameterGroups(groups,draft).find(item=>item.id===id);
  if(!group||!group.fields.some(field=>!field.read_only))return;
  if(info.design_review_persistence!==true)throw new Error("当前服务尚不支持保存参数确认记录。请重新启动工作台服务后刷新页面。");
  const review={kind:"confirmed",signature:designGroupSignature(group)};
  await withEditorLock(async()=>{
    await designReviewRestore;
    if(dirty){
      await saveProject({...savedDesignGroupReviews(),[id]:review});
    }else{
      const projectId=current.id,revision=current.revision;
      const out=await api(`/projects/${projectId}/design-reviews`,"PUT",{
        revision,design_reviews:await encodeDesignGroupReviews({[id]:review}),
      });
      if(out.project?.id!==projectId||out.project.revision!==revision)
        throw new Error("方案版本已更新，请重新载入后确认参数。当前编辑已保留。");
      current=out.project;
      await restoreDesignGroupReviews(current.design_reviews);
      paintDesignReadiness();
    }
    clearPresetUndo();
    toast(designGroupReviews.has(id)?"本组参数已确认并保存，刷新页面后保留。":"方案已保存，参数已规范化，请核对后重新确认本组。");
  });
}
function paintDesignReadiness(){
  const host=$("design-readiness"),enabled=designReadinessEnabled();
  host.hidden=!enabled;$("submit-job").hidden=!enabled;
  if(enabled&&designReadiness){
    const {issues,phase,warnings}=designReadiness;
    const available=parameterGroups(groups,draft);
    const model=buildDesignReadiness(available,issues,{phase,warnings,groupReviews:applicableGroupReviews(available)});
    designReadiness.pendingReviewCount=model.pendingReviewCount;
    const expanded=[".design-readiness-groups",".design-readiness-warnings"].filter(selector=>host.querySelector(selector)?.open);
    host.innerHTML=renderDesignReadiness(model);
    for(const selector of expanded){const details=host.querySelector(selector);if(details)details.open=true;}
    const byGroup=new Map(model.groups.map(item=>[item.id,item]));
    for(const option of $("parameter-group").querySelectorAll("option")){
      const item=byGroup.get(option.value);
      if(item)option.textContent=`${item.title} · ${designReadinessStatusLabels[item.status]}`;
    }
    for(const group of $("parameter-fields").querySelectorAll("[data-group]")){
      const item=byGroup.get(group.dataset.group),legend=group.querySelector("legend");
      if(!item||!legend)continue;
      let mark=legend.querySelector(".design-readiness-group-status");
      if(!mark){mark=document.createElement("span");mark.className="design-readiness-group-status";legend.append(" ",mark);}
      mark.dataset.status=item.status;mark.textContent=designReadinessStatusLabels[item.status];
      let confirm=group.querySelector("[data-review-design-group]");
      if(item.status==="review"&&!confirm){
        confirm=document.createElement("button");
        confirm.type="button";
        confirm.dataset.reviewDesignGroup=item.id;
        confirm.textContent="确认并保存本组参数";
        confirm.title="保存当前草稿和本组确认记录；参数发生变化后需重新确认。";
        group.append(confirm);
      }
      if(confirm)confirm.hidden=item.status!=="review";
    }
    const byField=new Map();
    for(const issue of issues)for(const key of issue.fields)if(!byField.has(key))byField.set(key,issue.message);
    for(const row of $("parameter-fields").querySelectorAll("[data-field]")){
      const message=byField.get(row.dataset.field)||"";
      const control=row.querySelector("[data-input]");
      if(!control)continue;
      control.setAttribute("aria-invalid",String(Boolean(message)));
      let note=row.querySelector(".design-readiness-field-error");
      if(message&&!note){note=document.createElement("span");note.className="design-readiness-field-error";row.append(note);}
      if(note){note.textContent=message;note.hidden=!message;}
    }
  }else host.innerHTML="";
  updateActionState();
}
async function checkDesignReadiness(entry){
  const isCurrent=()=>designReadiness===entry&&current?.id===entry.projectId&&user?.id===entry.userId;
  if(!isCurrent())return;
  try{
    const result=await api(`/projects/${entry.projectId}/design-readiness`,"POST",{inputs:JSON.parse(entry.snapshot)});
    await designReviewRestore;
    if(!isCurrent())return;
    if(typeof result?.valid!=="boolean"||!Array.isArray(result.issues)||result.issues.some(issue=>
      !Array.isArray(issue?.fields)||issue.fields.some(key=>typeof key!=="string")||typeof issue.message!=="string")||
      result.valid!==(result.issues.length===0))throw new Error("输入检查响应无效。");
    entry.issues=result.issues;entry.phase=result.valid?"ready":"invalid";
    entry.warnings=Array.isArray(result.warnings)?result.warnings.filter(message=>typeof message==="string"&&message.trim()):[];
  }catch{
    if(!isCurrent())return;
    entry.phase="unavailable";
  }
  if(isCurrent())paintDesignReadiness();
}
function syncDesignReadiness(force=false){
  if(!designReadinessEnabled()){
    clearTimeout(designReadinessTimer);designReadinessTimer=null;designReadiness=null;paintDesignReadiness();return;
  }
  const projectId=current.id,userId=user?.id,snapshot=JSON.stringify(draft);
  if(force||designReadiness?.projectId!==projectId||designReadiness?.userId!==userId||designReadiness?.snapshot!==snapshot){
    clearTimeout(designReadinessTimer);designReadinessTimer=null;
    const {issues}=collectLocalReadiness(groups,draft);
    issues.push(...coolingChannelPendingIssues(coolingChannelEditor));
    const entry={projectId,userId,snapshot,issues,phase:issues.length?"invalid":"pending"};
    designReadiness=entry;
    if(!issues.length)designReadinessTimer=setTimeout(()=>{designReadinessTimer=null;return checkDesignReadiness(entry);},300);
  }
  paintDesignReadiness();
}
function openReadinessField(groupId,fieldKey){
  if(editorBusy||!draft||!parameterGroups(groups,draft).some(group=>group.id===groupId))return;
  $("parameter-search").value="";$("parameter-group").value=groupId;
  filterParameters();rememberParameterGroup();chooseConceptSection("settings");
  const group=[...$("parameter-fields").querySelectorAll("[data-group]")].find(node=>node.dataset.group===groupId);
  const row=fieldKey&&group&&[...group.querySelectorAll("[data-field]")].find(node=>node.dataset.field===fieldKey);
  if(groupId==="pressure_ffsc" && fieldKey){
    const branch=["ox_to_opb","fuel_to_opb","ox_to_fpb","fuel_to_fpb"].find(id=>fieldKey.startsWith(id+"_"))
      || ({ox_turbine_exhaust_dp_ratio:"ox_gas",fuel_turbine_exhaust_dp_ratio:"fuel_gas"})[fieldKey];
    pressureWorkspaceState={view:"budget",branch:branch||pressureWorkspaceState.branch};
    syncPressureWorkspace($("parameter-fields"),{...pressureWorkspaceState,inputs:draft});
  }
  let details=row?.closest("details");while(details){details.open=true;details=details.parentElement?.closest("details");}
  const control=row?.querySelector("[data-input]")||$("parameter-group");
  control.focus();(row?control:group||control).scrollIntoView?.({block:"nearest"});
}
function renderFields(selected=$("parameter-group").value){
  const scope=$("concept-design-scope");
  const scopeOpen=scope.querySelector("details")?.open;
  scope.innerHTML=renderConceptDesignScope(draft);
  if(scopeOpen&&scope.querySelector("details"))scope.querySelector("details").open=true;
  const available=parameterGroups(groups,draft);
  // The old standalone thermal group now lives under numerical settings.
  if(selected==="thermo" && !available.some(g=>g.id==="thermo"))selected="solver";
  if(selected==="pressure" && draft.cycle_type==="ffsc")selected="pressure_ffsc";
  const expanded=new Set([...$("parameter-fields").querySelectorAll("[data-initials], [data-parameter-fold]")].filter(node=>node.open&&!node.dataset.searchOpened).map(node=>node.dataset.initials||node.dataset.parameterFold));
  $("parameter-group").innerHTML=renderParameterNavigation(available);
  $("parameter-group").value=available.some(g=>g.id===selected)?selected:available[0]?.id||"";
  $("parameter-fields").innerHTML=renderParameterGroups(available,draft,expanded);
  renderCoolingChannelEditor();filterParameters();updatePropellantContext();syncPreburnerContext();rememberParameterGroup();
}
function filterParameters(){
  const query=$("parameter-search").value.trim().toLocaleLowerCase(),selected=$("parameter-group").value;let count=0;$("parameter-group").disabled=!!query;
  $("parameter-fields").querySelectorAll("[data-group]").forEach(group=>{
    const title=groups.find(item=>item.id===group.dataset.group)?.title||"";
    let matches=0;group.querySelectorAll("[data-field]").forEach(row=>{const f=editorField(row.dataset.field);const found=!!f&&parameterVisible(f,draft)&&(!query||parameterSearchText(f,draft,title).includes(query));row.hidden=!found;if(found)matches++;});
    group.querySelectorAll("[data-initials], [data-parameter-fold]").forEach(details=>{const matched=[...details.querySelectorAll("[data-field]")].some(row=>!row.hidden);details.hidden=!matched;if(query&&matched&&!details.open){details.open=true;details.dataset.searchOpened="1";}else if(!query&&details.dataset.searchOpened){details.open=false;delete details.dataset.searchOpened;}});
    group.hidden=!matches||(!query&&group.dataset.group!==selected);if(!group.hidden)count+=matches;
  });$("parameter-empty").hidden=count>0;
  syncPressureWorkspace($("parameter-fields"),{...pressureWorkspaceState,search:!!query,inputs:draft});
}
function updateDocument(){if(!current)return;$("document-title").textContent=$("project-name").value+(dirty?" · 已修改":"");$("document-meta").textContent=`方案 v${current.revision} · ${current.author} · ${date(current.updated)}`;}
function fillProject(project,keepReference=false){
  if(!keepReference||current?.id!==project.id){specifiedPumpSpeeds.clear();specifiedTurbineStages.clear();coolingChannelEditor=null;}
  const changed=current?.id!==project.id;current=project;draft=mergeInputDefaults(clone(info.ui_schema.defaults),clone(project.inputs),true);
  if(info.design_review_persistence===true||changed||!keepReference)restoreDesignGroupReviews(project.design_reviews);
  if(changed){resultRecord=null;selectedJobId=null;projectJobs=[];analysisView="overview";conceptSection="settings";$("parameter-search").value="";}
  if(archivedProject())analysisView="history";
  $("project-name").value=project.name;$("project-description").value=project.description;setEditorError("");
  if(changed)$("project-more-actions").open=false;
  clearPresetUndo();syncPresetSelection(changed||!keepReference);
  renderFields(changed?readParameterGroup():$("parameter-group").value);updatePresetContext();setDirty(false);updateDocument();renderView();updateActionState();
}
async function openProject(id,savedNavigation=null){if(editorBusy||(page==="project"&&!mayLeave()))return;await withEditorLock(async()=>{
  fillProject(await api("/projects/"+id));
  if(savedNavigation){analysisView=savedNavigation.view;selectedJobId=savedNavigation.jobId;conceptSection=savedNavigation.conceptSection||"settings";libraryTab=savedNavigation.libraryTab||"components";renderView();}
  switchPage("project");await refreshProjectSide();
});}
async function chooseView(view){
  if(editorBusy || !Object.hasOwn(views,view))return;
  if(page!=="project"){
    if(current){await openProject(current.id);}
    else {const projects=await api("/projects");if(!projects.length){newProject();return;}await openProject(projects[0].id);}
  }
  analysisView=view;renderView();switchPage("project");scrollTaskTop();
}
function scrollTaskTop(){document.querySelector(".main-wrap")?.scrollTo?.({top:0,left:0});}
function chooseConceptSection(section){
  if(editorBusy || !["settings","results","diagnostics"].includes(section))return;
  conceptSection=section;analysisView="overview";renderView();switchPage("project");scrollTaskTop();
}
function openDesignParameters(){
  if(editorBusy)return;
  const target={geometry:"nozzle",nozzle_design:"nozzle",chamber_design:"chamber_chemistry",pump_geometry:"pump",injector_design:"injector",turbine_geometry:"turbine"}[analysisView];
  const group=target&&parameterGroups(groups,draft).find(g=>g.id.includes(target)||g.fields.some(f=>f.key.startsWith(target)));
  if(group){$("parameter-search").value="";$("parameter-group").value=group.id;filterParameters();rememberParameterGroup();}
  chooseConceptSection("settings");$("parameter-group").focus();
}
function renderView(){
  const archived=archivedProject();
  const archivedRecord=Boolean(resultRecord&&recordScopeIssue(resultRecord));
  if((archived||archivedRecord)&&!archivedViews.has(analysisView))analysisView="history";
  if(archived&&analysisView==="overview"&&conceptSection==="settings")conceptSection="results";
  $("project-scope-note").hidden=!archived;
  $("project-scope-note").textContent=archived?designScopeIssue(current.inputs):"";
  if(libraryCleanup){libraryCleanup();libraryCleanup=null;}
  if(nozzleDesignCleanup){nozzleDesignCleanup();nozzleDesignCleanup=null;}
  if(chamberDesignCleanup){chamberDesignCleanup();chamberDesignCleanup=null;}
  if(turbineDesignCleanup){turbineDesignCleanup();turbineDesignCleanup=null;}
  if(injectorDesignCleanup){injectorDesignCleanup();injectorDesignCleanup=null;}
  if(turbineGeometryCleanup){turbineGeometryCleanup();turbineGeometryCleanup=null;}
  if(pumpGeometryCleanup){pumpGeometryCleanup();pumpGeometryCleanup=null;}
  if(nozzleGeometryCleanup){nozzleGeometryCleanup();nozzleGeometryCleanup=null;}
  coolingDesignView?.dispose();coolingDesignView=null;
  $("analysis-title").textContent=views[analysisView][0];$("analysis-subtitle").textContent=views[analysisView][1];
  $("analysis-breadcrumb").innerHTML=renderWorkspaceBreadcrumb(analysisView,views[analysisView][0]);
  const history=analysisView==="history";
  const fixedSteady=analysisView==="fixed_steady";
  const hub=isWorkspaceHub(analysisView),designParameters=hasDesignParameters(analysisView);
  const concept=analysisView==="overview",settings=concept&&conceptSection==="settings";
  const contentView=concept?(conceptSection==="diagnostics"?"solver_diagnostics":"results_overview"):analysisView;
  $("workspace").dataset.conceptSection=conceptSection;
  $("concept-navigation").hidden=!concept;
  $("concept-settings-host").hidden=!settings;
  $("concept-settings-host").inert=!settings;
  $("concept-section-content").hidden=settings||history||fixedSteady;
  $("concept-section-content").setAttribute("role",concept?"tabpanel":"region");
  $("concept-section-content").setAttribute("aria-labelledby",concept?`concept-tab-${conceptSection==="diagnostics"?"diagnostics":"results"}`:"analysis-title");
  document.querySelectorAll("[data-concept-section]").forEach(button=>{
    const selected=button.dataset.conceptSection===conceptSection;
    button.setAttribute("aria-selected",String(selected));button.tabIndex=selected?0:-1;
  });
  document.querySelector(".workbench-layout").classList.toggle("parameters-collapsed",false);
  document.querySelector(".workbench-layout").classList.toggle("history-view",history);
  document.querySelector(".workbench-layout").classList.toggle("fixed-steady-view",fixedSteady);
  document.querySelector(".workbench-layout").classList.toggle("context-only",!designParameters);
  document.querySelector(".workbench-layout").classList.toggle("hub-view",hub);
  $("parameter-pane").hidden=!settings;
  $("parameter-pane").inert=!settings;
   $("toggle-parameters").hidden=!designParameters || concept || archived;
  $("toggle-parameters").textContent="编辑相关参数";
  for(const id of ["result-toolbar","result-context"])$(id).hidden=settings || history || fixedSteady || hub;
  $("concept-parameter-roles").innerHTML=renderAnalysisParameterRoles(info?.ui_schema?.analysis_parameter_roles,"concept_design");
  $("project-history").hidden=analysisView!=="history";$("analysis-content").hidden=["history","validation","throttle","operating_throttle","fixed_steady","transient","feed","chilldown","ignition","shutdown"].includes(analysisView);
  $("validation-content").hidden=analysisView!=="validation";
  $("throttle-content").hidden=analysisView!=="throttle";
  $("operating-throttle-content").hidden=analysisView!=="operating_throttle";
  $("fixed-steady-content").hidden=!fixedSteady;
  $("transient-content").hidden=analysisView!=="transient";
  $("feed-content").hidden=analysisView!=="feed";
  $("chilldown-content").hidden=analysisView!=="chilldown";
  $("ignition-content").hidden=analysisView!=="ignition";
  $("shutdown-content").hidden=analysisView!=="shutdown";
  if(analysisView!=="validation") validationView?.onLeave();
  if(analysisView!=="throttle") throttleView?.onLeave();
  if(analysisView!=="operating_throttle") operatingThrottleView?.onLeave();
  if(!fixedSteady) fixedSteadyView?.onLeave();
  if(analysisView!=="transient") transientView?.onLeave();
  if(analysisView!=="feed") feedView?.onLeave();
  if(analysisView!=="chilldown") chilldownView?.onLeave();
  if(analysisView!=="ignition") ignitionView?.onLeave();
  if(analysisView!=="shutdown") shutdownView?.onLeave();
  if(hub){
    $("analysis-content").innerHTML=renderWorkspaceHub(analysisView,{project:current,record:resultRecord,dirty,libraryTab});
    libraryCleanup=mountWorkspaceLibrary($("analysis-content"),{onSelect:id=>{libraryTab=id;rememberNavigation();}});
  } else if(analysisView==="geometry") {
    const geometry=resultRecord?.nozzle_geometry || resultRecord?.result?.nozzle_geometry;
    $("analysis-content").innerHTML=renderNozzleGeometry(geometry);
    nozzleGeometryCleanup=mountNozzleGeometry($("analysis-content"),geometry);
  } else if(analysisView==="chamber_design") {
    const design=resultRecord?.chamber_design || resultRecord?.result?.chamber_design;
    $("analysis-content").innerHTML=renderChamberDesign(design);
    chamberDesignCleanup=mountChamberDesign($("analysis-content"),design,{onGeometry:()=>chooseView("geometry"),onValidation:()=>chooseView("validation")});
  } else if(analysisView==="nozzle_design") {
    const design=resultRecord?.nozzle_design || resultRecord?.result?.nozzle_design;
    const nozzleSourceId=resultRecord?.id;
    $("analysis-content").innerHTML=renderNozzleDesign(design);
    nozzleDesignCleanup=mountNozzleDesign($("analysis-content"),design,{onGeometry:()=>chooseView("geometry"),onAmbient:async request=>{
      if(!nozzleSourceId||resultRecord?.id!==nozzleSourceId)throw new Error("来源记录已改变，请重新打开喷管分析。");
      const answer=await api(`/jobs/${encodeURIComponent(nozzleSourceId)}/nozzle-ambient`,"POST",request);
      if(resultRecord?.id!==nozzleSourceId)throw new Error("来源记录已改变，本次背压结果不再显示。");
      return answer;
    }});
  } else if(analysisView==="pump_geometry") {
    const geometry=resultRecord?.pump_geometry || resultRecord?.result?.pump_geometry;
    $("analysis-content").innerHTML=renderPumpGeometry(geometry);
    pumpGeometryCleanup=mountPumpGeometry($("analysis-content"),geometry);
  } else if(analysisView==="injector_design") {
    const design=resultRecord?.injector_design || resultRecord?.result?.injector_design;
    $("analysis-content").innerHTML=renderInjectorDesign(design);
    injectorDesignCleanup=mountInjectorDesign($("analysis-content"),design,{initialStation:$("analysis-content").dataset.injectorStation,onInspectComponent:id=>{
      const host=$("analysis-content");host.dataset.caRecordId=String(resultRecord?.id || "");
      host.dataset.caCategory="combustion";host.dataset.caSelected=id;chooseView("components");
    }});
    delete $("analysis-content").dataset.injectorStation;
  } else if(analysisView==="turbine_geometry") {
    const geometry=resultRecord?.turbine_geometry || resultRecord?.result?.turbine_geometry;
    const design=resultRecord?.turbine_design || resultRecord?.result?.turbine_design;
    const hasGeometry=Object.values(geometry?.turbines || {}).some(t=>t.available);
    $("analysis-content").innerHTML=renderTurbineDesign(design)+(hasGeometry?`<details class="turbine-concept-reference"><summary>概念流道参考 · 不作为 CFturbo 设计约束</summary><p>以下仅为保存假设下的环形流道和叶排包络。具体子午面和叶型由 CFturbo 设计。</p>${renderTurbineGeometry(geometry)}</details>`:"");
    const turbineSourceId=resultRecord?.id;
    turbineDesignCleanup=mountTurbineDesign($("analysis-content"),design,{initialComponent:$("analysis-content").dataset.turbineDesignComponent,
      onCalculatePreliminary:async({turbineKey,scenario})=>{
        if(!turbineSourceId||resultRecord?.id!==turbineSourceId)throw new Error("来源记录已改变，请重新打开涡轮方案。");
        const answer=await api(`/jobs/${encodeURIComponent(turbineSourceId)}/turbine-preliminary`,"POST",{turbine_key:turbineKey,scenario});
        if(resultRecord?.id!==turbineSourceId)throw new Error("来源记录已改变，本次初选结果不再显示。");
        return answer;
      },onInspectComponent:id=>{
      const host=$("analysis-content");host.dataset.caRecordId=String(resultRecord?.id || "");
      host.dataset.caCategory="shaft";host.dataset.caSelected=id;chooseView("components");
    }});
    delete $("analysis-content").dataset.turbineDesignComponent;
    if(hasGeometry)turbineGeometryCleanup=mountTurbineGeometry($("analysis-content"),geometry);
  } else if(analysisView==="validation") {
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
    transientView ||= mountTransient($("transient-content"),{api,getContext:()=>({project:current,draft,dirty,record:resultRecord}),notify:toast,onInspectSource:()=>chooseView("results_overview")});
    Promise.resolve(transientView.refresh()).catch(error=>toast(error.message));
  } else if(fixedSteady) {
    fixedSteadyView ||= mountFixedSteady($("fixed-steady-content"),{api,getContext:()=>({project:current,jobs:projectJobs,record:resultRecord,roleCatalog:info?.ui_schema?.analysis_parameter_roles}),notify:toast});
    Promise.resolve(fixedSteadyView.refresh()).catch(error=>toast(error.message));
  } else if(analysisView==="operating_throttle") {
    operatingThrottleView ||= mountOperatingThrottle($("operating-throttle-content"),{api,getContext:()=>({project:current,draft,dirty,record:resultRecord}),notify:toast});
    Promise.resolve(operatingThrottleView.refresh()).catch(error=>toast(error.message));
  } else if(analysisView==="throttle") {
    throttleView ||= mountThrottle($("throttle-content"),{api,getContext:()=>({project:current,draft,dirty,record:resultRecord}),notify:toast});
    Promise.resolve(throttleView.refresh()).catch(error=>toast(error.message));
  } else {
    if(analysisView!=="history") {
      $("analysis-content").innerHTML=settings?"":renderAnalysis(contentView,resultRecord)+(contentView==="results_overview"&&resultRecord?.result?.calculation&&!recordScopeIssue(resultRecord)?`<details class="concept-reference valve-design-evidence"><summary>阀门设计需求与待补特性</summary>${renderValveRequirements(resultRecord.valve_requirements)}</details>`:"");
      if(!settings&&contentView==="results_overview") {
        mountSteadyWorkspace($("analysis-content"),resultRecord?.id || "");
        const coolingPanel=$("analysis-content").querySelector('[data-steady-panel="cooling"]');
        if(coolingPanel && resultRecord?.status==="succeeded"&&!archived&&!recordScopeIssue(resultRecord)) {
          const host=document.createElement("section");
          host.dataset.coolingDesignHost="";coolingPanel.prepend(host);
          coolingDesignView=mountCoolingDesign(host,{api,getContext:()=>({project:current,draft,dirty,record:resultRecord}),notify:toast,onAdopt:adoptCoolingCandidate});
          Promise.resolve(coolingDesignView.refresh()).catch(error=>toast(error.message));
        }
      }
      if(analysisView==="components") mountComponentWorkspace($("analysis-content"),resultRecord?.id || "",{
        onTurbineDesign:id=>{ $("analysis-content").dataset.turbineDesignComponent=id;chooseView("turbine_geometry"); },
        onInjectorDesign:id=>{ $("analysis-content").dataset.injectorStation=({injector:"main",main_chamber:"main",ox_preburner:"ox",fuel_preburner:"fuel"})[id];chooseView("injector_design"); },
      });
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
  $("result-consistency").hidden=settings || history || fixedSteady || hub || !resultRecord?.result?.calculation;
  $("result-consistency").innerHTML=renderResultConsistency(resultRecord?.result_consistency || resultRecord?.result?.result_consistency);
}

async function adoptCoolingCandidate({studyId,candidateId}) {
  requireEditableDesign();
  if(editorBusy || !current)throw new Error("请等待当前编辑操作完成。");
  const projectId=current.id;
  const candidate=await api(`/cooling-designs/${studyId}/candidates/${candidateId}/draft`);
  if(current?.id!==projectId || candidate.project_id!==projectId)throw new Error("当前方案已改变，请重新选择候选。");
  if(editorBusy)throw new Error("方案正在保存，请稍后采纳。");
  if(draft.propellant_pair!=="lox_methane" || Number(draft.cooling_bartz_mode)<=0 || String(draft.cooling_channel_table_json||"").trim())
    throw new Error("当前草稿需采用甲烷准一维模型且没有沿程通道表；候选几何不能覆盖不同的通道定义。");
  draft={...draft,...candidate.input_patch};
  clearPresetUndo();setEditorError("");syncPresetSelection();renderFields();setDirty();
  toast("已将候选几何写入草稿，其他参数保留。请运行设计点重新闭合整机，并使用新记录复核筛选约束。");
}
function syncPreburnerContext(){
  if(!draft)return;
  $("parameter-fields").querySelectorAll("[data-preburner-interfaces]").forEach(node=>{node.innerHTML=renderPreburnerInterfaces(draft);});
  $("parameter-fields").querySelectorAll("[data-preburner-results]").forEach(node=>{node.innerHTML=renderPreburnerResults(resultRecord,draft);});
  $("parameter-fields").querySelectorAll("[data-pressure-results]").forEach(node=>{node.innerHTML=renderPressureResults(resultRecord,draft);});
  $("parameter-fields").querySelectorAll("[data-cooling-input-summary]").forEach(node=>{node.innerHTML=renderCoolingInputSummary(draft,{record:resultRecord});});
}

function coolingGeometryContext(){
  const summary=coolingInputSummaryState(draft,{record:resultRecord});
  return {profile:summary.profile,profileCurrent:summary.isCurrent===true};
}
function renderCoolingChannelEditor(){
  if(!draft)return;
  $("parameter-fields").querySelectorAll("[data-cooling-channel-inputs]").forEach(node=>{
    node.innerHTML=renderCoolingChannelInputs(draft,{editor:coolingChannelEditor,...coolingGeometryContext()});
  });
}
function editCoolingChannel(element,{repaint=true}={}){
  if(editorBusy||archivedProject()||!draft)return;
  const action={type:element.dataset.coolingChannelAction,value:element.value,
    row:Number(element.dataset.coolingChannelRow),key:element.dataset.coolingChannelKey};
  const previous=draft;
  const edited=applyCoolingChannelEdit(draft,coolingChannelEditor,action,coolingGeometryContext());
  draft=edited.inputs;coolingChannelEditor=edited.editor;
  clearPresetUndo();setEditorError("");syncPresetSelection();setDirty();
  if(previous!==draft)renderFields();
  else if(repaint)renderCoolingChannelEditor();
  else refreshCoolingChannelStatus($("parameter-fields"),draft,coolingChannelEditor,coolingGeometryContext());
  syncDesignReadiness(true);
}
function updateResultContext(){
  syncPreburnerContext();
  if(draft)refreshCoolingChannelStatus($("parameter-fields"),draft,coolingChannelEditor,coolingGeometryContext());
  if(!current)return;const old=resultRecord&&(dirty||resultRecord.revision!==current.revision);
  $("result-context").classList.toggle("result-context-stale",!!old);
  $("result-context").textContent=resultRecord?`当前显示：方案 v${resultRecord.revision} · ${date(resultRecord.created)} · ${statusNames[resultRecord.status]||resultRecord.status}${old?"。这是历史输入的结果；当前参数已变化，请重新运行设计点。":"。"}`:"尚无计算结果。选择参考工况后运行设计点。";
  $("export-current-result").hidden=!resultRecord?.result;
}
async function refreshProjectSide(){
  if(!current)return;const id=current.id,sequence=++refreshSequence;
  const [jobs,revisions]=await Promise.all([api("/jobs?project_id="+id),api(`/projects/${id}/revisions`)]);
  if(current?.id!==id||sequence!==refreshSequence)return;projectJobs=jobs;renderJobs($("project-jobs"),jobs);
  $("project-revisions").innerHTML=revisions.map(r=>`<div class="revision-row"><div><b>v${r.revision} · ${escape(r.name)}</b><p>${escape(r.author)} · ${date(r.created)}</p></div><button data-revision="${r.revision}"${archivedProject()?" disabled":""}>${archivedProject()?"历史版本只读":"载入为草稿"}</button></div>`).join("");
  if(!jobs.some(j=>j.id===selectedJobId))selectedJobId=jobs[0]?.id||null;
  $("result-selector").innerHTML=jobs.length?jobs.map(j=>`<option value="${j.id}" ${j.id===selectedJobId?"selected":""}>v${j.revision} · ${date(j.created)} · ${statusNames[j.status]}</option>`).join(""):'<option value="">尚无计算记录</option>';
  const summary=jobs.find(j=>j.id===selectedJobId);
  if(summary && (resultRecord?.id!==summary.id || resultRecord.status!==summary.status || active(summary)))await loadRecord(summary.id);
  if(current?.id!==id||sequence!==refreshSequence)return;
  updateActionState();
  rememberNavigation();
}
async function loadRecord(id){
  const projectId=current?.id,sequence=++recordLoadSequence;selectedJobId=id;
  if(resultRecord?.id!==id){resultRecord=null;renderView();$("result-context").textContent="正在读取所选计算记录，各页面将在读取完成后统一更新。";}
  let record;
  try{record=await api("/jobs/"+id);}catch(error){
    if(current?.id===projectId&&selectedJobId===id&&sequence===recordLoadSequence)$("result-context").textContent="所选计算记录读取失败，请重新选择记录或重试。";
    throw error;
  }
  if(current?.id!==projectId||selectedJobId!==id||sequence!==recordLoadSequence)return;
  resultRecord=record;renderView();rememberNavigation();
}
function renderJobs(container,jobs){container.innerHTML=jobs.length?jobs.map(j=>`<div class="job-row"><div class="job-main"><b>${escape(j.project_name)} · v${j.revision}</b><p>${escape(j.author)} · ${date(j.created)}</p>${active(j)&&j.progress?`<p>迭代 ${j.progress.iteration}/${j.progress.maximum} · 残差 ${number(j.progress.residual)}</p>`:""}</div><div class="job-actions">${badge(j.status)}<button data-job="${j.id}">查看</button>${active(j)&&(user.role==="admin"||user.id===j.submitted_by)?`<button data-cancel="${j.id}">取消</button>`:""}</div></div>`).join(""):'<div class="small-empty">暂无计算记录。点击“运行设计点”开始计算。</div>';}
function validateChoices(params){
  for(const f of fields)if(f.choices && f.key in params && !f.choices.some(c=>String(c.value)===String(params[f.key])))throw new Error(`${f.label}选项无效：${params[f.key]}`);
}
function applyInputDocument(value){
  requireEditableDesign();
  if(!value||Array.isArray(value)||typeof value!=="object")throw new Error("方案文件必须包含参数对象。");
  const sourceParams=Object.hasOwn(value,"inputs")?value.inputs:value;if(!sourceParams||Array.isArray(sourceParams)||typeof sourceParams!=="object")throw new Error("inputs 必须是参数对象。");
  const params=platformInputValues(sourceParams),defaults=info.ui_schema.defaults;
  const scopeIssue=designScopeIssue(params);if(scopeIssue)throw new Error(scopeIssue);
  for(const key of Object.keys(params))if(!Object.hasOwn(defaults,key))throw new Error("无法识别的参数："+key);
  for(const [key,v] of Object.entries(params)){const d=defaults[key],f=fields.find(item=>item.key===key);
    if(f?.nullable ? v!==null&&!Number.isFinite(v) : typeof v!==typeof d || (typeof d==="number"&&!Number.isFinite(v)))throw new Error("参数类型无效："+key);}
  if(params.cycle_type&&params.cycle_type!=="ffsc")throw new Error("循环类型无效。");
  if(params.propellant_pair&&params.propellant_pair!=="lox_methane")throw new Error("推进剂组合无效。");
  validateChoices(params);
  for(const f of fields)if(f.type==="int" && f.key in params && !Number.isInteger(params[f.key]))throw new Error(`${f.label}必须是整数。`);
  // Validate before changing the only editable draft; a failed import is inert.
  specifiedPumpSpeeds.clear();specifiedTurbineStages.clear();coolingChannelEditor=null;
  draft=mergeInputDefaults(clone(defaults),clone(params),savedInputDocument(value));designGroupReviews=new Map();clearPresetUndo();syncPresetSelection(true);renderFields();setDirty();
}
function buildInputBackup(){
  return {format:"lre.workbench.inputs",schema_version:1,inputs:clone(archivedProject()?current.inputs:draft),
    project_name:$("project-name").value,project_description:$("project-description").value,
    base_revision:current.revision,unsaved:archivedProject()?false:dirty};
}
function validateDraft(){
  requireEditableDesign();
  const pending=coolingChannelPendingIssues(coolingChannelEditor);
  if(pending.length){openReadinessField("chamber_channels");throw new Error(pending[0].message);}
  if(!$("project-name").value.trim()){
    analysisView="history";renderView();switchPage("project");$("project-name").focus();
    throw new Error("请在“方案与计算记录”中填写方案名称。");
  }
  validateChoices(draft);
  if(propellantIssue())throw new Error(propellantIssue());
  for(const f of fields){
    const value=draft[f.key];
    if(f.nullable && value==null)continue;
    if((typeof f.default==="number" || ["number","int"].includes(f.type)) && (typeof value!=="number"||!Number.isFinite(value)||(f.type==="int"&&!Number.isInteger(value)))){
      const visible=groups.some(g=>compatible(g)&&g.fields.some(item=>item.key===f.key&&parameterVisible(item,draft)));
      if(visible){$("parameter-search").value=f.label;filterParameters();chooseConceptSection("settings");}
      if(!visible&&value==null&&/^turb_(ox|fuel)_n_stages_user$/.test(f.key))
        throw new Error(`${f.label}仍处于手动待填写状态，在当前循环或轴系下已隐藏。请切回使用该涡轮的循环／轴系，填写整数级数或选择“按现有规则估算”，当前草稿已保留。`);
      if(!visible&&value==null&&/^turb_(ox|fuel)_(reaction_degree|meanline_flow_coefficient)$/.test(f.key))
        throw new Error(`${f.label}尚未填写，在当前模式下已隐藏。请在使用该涡轮的循环／轴系下重新启用均径校核并补填，当前草稿已保留。`);
      throw new Error(`${f.label}必须是${f.type==="int"?"整数":"有限数值"}。${visible?"请在参数设置中修正。":`历史参数 ${f.key} 无效；请在“方案与计算记录”载入有效版本，或导入修正后的参数备份。`}`);
    }
  }
}
async function saveProject(reviews=null){
  if(analysisView==="fixed_steady")throw new Error("请在固定硬件稳态页保存任务定义；概念设计方案需回到概念设计页保存。");
  if(["operating_throttle","throttle"].includes(analysisView))throw new Error("节流分析使用保存的设计基准；修改概念设计方案请回到概念设计页保存。");
  validateDraft();
  await designReviewRestore;
  const reviewPayload=info.design_review_persistence===true?
    {design_reviews:await encodeDesignGroupReviews(reviews??savedDesignGroupReviews())}:{};
  const out=await api("/projects/"+current.id,"PUT",{name:$("project-name").value,description:$("project-description").value,inputs:draft,revision:current.revision,
    ...reviewPayload});
  fillProject(out.project,true);await designReviewRestore;
  if(out.warnings.length)toast("已保存。参数提示："+out.warnings.join("；"));return out;
}
async function runDesign(){
  if(analysisView==="fixed_steady")throw new Error("固定硬件稳态入口不能运行概念设计或定尺寸逻辑。");
  if(["operating_throttle","throttle"].includes(analysisView))throw new Error("节流入口不能运行概念设计或定尺寸逻辑；请使用发动机节流分析页的专用运行按钮。");
  if(!current||projectJobs.some(active))return;
  if(designReadinessEnabled()){
    syncDesignReadiness();
    if(!canRunDesignPoint()){
      if(conceptSection!=="settings")chooseConceptSection("settings");
      throw new Error(readinessRunMessage());
    }
  }
  validateDraft();
  await withEditorLock(async()=>{
    if(dirty){
      await saveProject();
      if(designReadinessEnabled()&&!canRunDesignPoint())throw new Error(readinessRunMessage());
    }
    const job=await api(`/projects/${current.id}/jobs`,"POST",{mode:"concept_design",revision:current.revision});
    selectedJobId=job.id;analysisView="overview";conceptSection="results";
    renderView();switchPage("project");await refreshProjectSide();toast("设计点已提交，计算结果分区会自动更新。");
  });
}
async function refreshMembers(){if(!canManageMembers())return;const members=await api("/members");if(!canManageMembers())return;$("members-list").innerHTML=members.map(m=>`<div class="member-row"><div><b>${escape(m.display_name)}</b><p>@${escape(m.username)}</p></div><span class="badge">${m.role==="admin"?"管理员":"成员"}</span></div>`).join("");}
async function showResult(id){
  if(page==="project"){await loadRecord(id);$("result-selector").value=id;analysisView="results_overview";renderView();switchPage("project");return;}
  modalRecord=await api("/jobs/"+id);$("result-title").textContent=`计算记录 · 方案 v${modalRecord.revision}`;
  const supported=!recordScopeIssue(modalRecord);
  $("result-content").innerHTML=renderAnalysis("overview",modalRecord)+(supported&&modalRecord?.result?.calculation?renderValveRequirements(modalRecord.valve_requirements):"");
  if(supported)mountSteadyWorkspace($("result-content"),modalRecord?.id || "");
  $("download-result").hidden=!modalRecord.result;$("result-dialog").showModal();
}
function newProject(){if(editorBusy||!mayLeave())return;$("new-error").textContent="";$("new-dialog").showModal();}
function clearPasswordForm(){
  for(const id of ["password-current","password-new","password-confirm"])$(id).value="";
  $("password-error").textContent="";
}
function closePasswordDialog(){if(passwordBusy)return;clearPasswordForm();$("password-dialog").close();}
async function changeOwnPassword(){
  if(editorBusy||passwordBusy||!user)return;
  const currentPassword=$("password-current").value,newPassword=$("password-new").value;
  if(!currentPassword)throw new Error("请输入当前密码。");
  if(!newPassword)throw new Error("新密码不能为空。");
  if([...newPassword].length>128)throw new Error("新密码最多为 128 个字符。");
  if(newPassword!==$("password-confirm").value)throw new Error("两次输入的新密码不一致。");
  if(!mayLeave())return;
  passwordBusy=true;
  const controls=["password-current","password-new","password-confirm","password-close","password-cancel"];
  controls.forEach(id=>$(id).disabled=true);
  try{
    await withEditorLock(async()=>{
      await api("/auth/change-password","POST",{current_password:currentPassword,new_password:newPassword});
      clearPasswordForm();$("password-dialog").close();
      user=null;current=null;draft=null;resultRecord=null;modalRecord=null;selectedJobId=null;projectJobs=[];
      workspaceUserId=null;referenceKey="";presetUndo=null;page="projects";analysisView="overview";
      recordLoadSequence++;refreshSequence++;setDirty(false);initialized=true;showAuth();
      $("auth-password").value="";$("auth-error").textContent="";
      $("auth-description").textContent="密码已修改，请使用新密码重新登录。";
    });
  }catch(error){
    if(error.status===404)throw new Error("当前服务版本暂不支持修改密码，请联系管理员更新服务后重试。");
    if(error.status===401&&!user){clearPasswordForm();$("password-dialog").close();$("auth-error").textContent="登录已失效，请重新登录后修改密码。";return;}
    throw error;
  }finally{passwordBusy=false;controls.forEach(id=>$(id).disabled=false);}
}
$("change-password").onclick=()=>{if(editorBusy||!user)return;clearPasswordForm();$("password-dialog").showModal();$("password-current").focus();};
$("password-close").onclick=$("password-cancel").onclick=closePasswordDialog;
$("password-dialog").addEventListener("close",clearPasswordForm);
$("password-dialog").addEventListener("cancel",event=>{if(passwordBusy)event.preventDefault();else clearPasswordForm();});
$("password-form").onsubmit=event=>{event.preventDefault();if(passwordBusy)return;return action($("password-submit"),"password-error",changeOwnPassword);};
$("auth-form").onsubmit=event=>{event.preventDefault();action($("auth-submit"),"auth-error",async()=>{const data={username:$("auth-username").value,password:$("auth-password").value};if(!initialized)data.display_name=$("auth-display").value;user=await api("/auth/"+(initialized?"login":"setup"),"POST",data);initialized=true;await enterWorkspace();});};
$("logout").onclick=()=>action($("logout"),null,async()=>{if(editorBusy||!mayLeave())return;await api("/auth/logout","POST",{});user=null;current=null;draft=null;resultRecord=null;setDirty(false);showAuth();});
document.querySelectorAll("[data-nav]").forEach(b=>b.onclick=()=>action(b,null,()=>navigate(b.dataset.nav)));
document.querySelectorAll("[data-view]").forEach(b=>b.onclick=()=>action(b,null,()=>chooseView(b.dataset.view)));
document.querySelectorAll("[data-concept-section]").forEach(button=>button.onclick=()=>chooseConceptSection(button.dataset.conceptSection));
$("concept-navigation").addEventListener("keydown",event=>{
  const buttons=[...$("concept-navigation").querySelectorAll("[data-concept-section]")],index=buttons.indexOf(event.target);
  if(index<0||editorBusy)return;
  const next=event.key==="ArrowRight"?(index+1)%buttons.length:event.key==="ArrowLeft"?(index+buttons.length-1)%buttons.length:event.key==="Home"?0:event.key==="End"?buttons.length-1:null;
  if(next===null)return;event.preventDefault();chooseConceptSection(buttons[next].dataset.conceptSection);buttons[next].focus();
});
$("toggle-navigation").onclick=()=>setNavigationOpen($("toggle-navigation").dataset.open!=="true");
$("navigation-backdrop").onclick=()=>{setNavigationOpen(false);$("toggle-navigation").focus();};
window.addEventListener("keydown",event=>{if(event.key==="Escape"&&$("toggle-navigation").dataset.open==="true"){setNavigationOpen(false);$("toggle-navigation").focus();}});
document.querySelectorAll("[data-nav-group]").forEach(group=>group.addEventListener("toggle",()=>{
  if(group.open)document.querySelectorAll("[data-nav-group]").forEach(other=>{if(other!==group)other.open=false;});
}));
$("analysis-content").addEventListener("click",event=>{
  const diagnostic=event.target.closest?.('[data-steady-open="convergence"],[data-steady-target="convergence"]');
  if(diagnostic&&$("analysis-content").contains(diagnostic)){
    event.preventDefault();if(analysisView==="overview")chooseConceptSection("diagnostics");else chooseView("solver_diagnostics");return;
  }
  const destination=event.target.closest?.("[data-workspace-view]");
  if(destination&&$("analysis-content").contains(destination)){
    event.preventDefault();action(destination,null,()=>destination.dataset.workspaceView==="overview"?chooseConceptSection("settings"):chooseView(destination.dataset.workspaceView));return;
  }
  const workspaceAction=event.target.closest?.("[data-workspace-action]");
  if(workspaceAction&&$("analysis-content").contains(workspaceAction)){
    event.preventDefault();if(!editorBusy&&workspaceAction.dataset.workspaceAction==="references")showReferences();return;
  }
  const link=event.target.closest?.("[data-flow-state]");
  if(!link || !$("analysis-content").contains(link))return;
  event.preventDefault();
  const host=$("analysis-content");host.dataset.saRecordId=String(resultRecord?.id || "");
  host.dataset.saSelected=link.dataset.flowState;host.dataset.saGroup=link.dataset.flowGroup;
  chooseView("states");
});
document.querySelectorAll("[data-close]").forEach(b=>b.onclick=()=>$(b.dataset.close).close());
$("new-project").onclick=newProject;
$("new-form").onsubmit=event=>{event.preventDefault();return action(event.submitter,"new-error",async()=>{
  const data=Object.fromEntries(new FormData(event.target));
  const preset=info.presets.find(p=>p.key===data.preset);
  if(!preset)throw new Error("请选择液氧甲烷 FFSC 起始方案。");
  const inputs={...clone(info.ui_schema.defaults),...clone(preset.inputs)};
  const scopeIssue=designScopeIssue(inputs);if(scopeIssue)throw new Error(scopeIssue);
  const out=await api("/projects","POST",{name:data.name,description:data.description,inputs:preset.inputs,
    ...(info.design_review_persistence===true?{design_reviews:await encodeDesignGroupReviews(Object.fromEntries(presetGroupReviews(inputs)))}:{})});
  $("new-dialog").close();event.target.reset();fillProject(out.project);
  await designReviewRestore;
  if(info.design_review_persistence!==true)adoptPresetGroupValues();
  paintDesignReadiness();switchPage("project");await refreshProjectSide();
});};
$("project-name").oninput=$("project-description").oninput=()=>{if(editorBusy||archivedProject())return;clearPresetUndo();setEditorError("");setDirty();};
$("parameter-search").oninput=filterParameters;
$("parameter-group").onchange=()=>{filterParameters();rememberParameterGroup();};
$("design-readiness").onclick=event=>{
  if(editorBusy)return;
  if(event.target.closest?.("[data-readiness-retry]")){syncDesignReadiness(true);return;}
  const link=event.target.closest?.("[data-readiness-group]");
  if(link)openReadinessField(link.dataset.readinessGroup,link.dataset.readinessField);
};
$("parameter-fields").oninput=event=>{
  if(["edit","raw"].includes(event.target.dataset.coolingChannelAction)){
    editCoolingChannel(event.target,{repaint:false});return;
  }
  const f=editorField(event.target.dataset.input);if(!f||f.read_only||editorBusy||archivedProject())return;
  const value=event.target.value;
  if(["cycle_type","propellant_pair"].includes(f.key)&&designScopeIssue({...draft,[f.key]:value})){
    event.target.value=displayValue(f);return;
  }
  if(f.virtual){
    try{
      if(f.key===PREBURNER_MODE_KEY)draft=selectPreburnerMode(draft,value);
      else if(f.pump_speed_key){
        const key=f.pump_speed_key;
        if(Number.isFinite(draft[key])&&draft[key]>0)specifiedPumpSpeeds.set(key,draft[key]);
        draft=selectPumpSpeedMode(draft,f,value,specifiedPumpSpeeds.get(key)??info.ui_schema.defaults[key]);
      }else if(f.turbine_stages_key){
        const key=f.turbine_stages_key;
        if(Number.isInteger(draft[key])&&draft[key]>0)specifiedTurbineStages.set(key,draft[key]);
        draft=selectTurbineStageMode(draft,f,value,specifiedTurbineStages.get(key));
      }else return;
    }
    catch(error){setEditorError(error.message);event.target.value=displayValue(f);return;}
  }else{
    const canonical=fields.find(field=>field.key===f.key);if(!canonical)return;
    draft[f.key]=(typeof canonical.default==="number"||["number","int"].includes(canonical.type))?(value===""?null:Number(value)/(f.factor||1)):value;
  }
  clearPresetUndo();setEditorError("");syncPresetSelection();setDirty();
  if(f.virtual||["cycle_type","propellant_pair"].includes(f.key)||(f.choices&&parameterLayoutDependency(f.key,groups)))renderFields();
  else updatePropellantContext();
};
$("parameter-fields").onchange=event=>{
  if(editorBusy||archivedProject())return;
  if(event.target.dataset.coolingChannelAction){
    if(event.target.dataset.coolingChannelAction==="mode")editCoolingChannel(event.target);
    else if(event.target.dataset.coolingChannelKey==="anchor")renderCoolingChannelEditor();
    return;
  }
  const f=editorField(event.target.dataset.input);if(!f||f.virtual||f.choices)return;
  if(parameterLayoutDependency(f.key,groups)||groups.find(g=>g.id==="pressure_ffsc")?.fields.some(item=>item.key===f.key)||f.key.startsWith("cooling_"))renderFields();
};
$("choose-import").onclick=()=>{if(!editorBusy)$("import-inputs").click();};
$("import-inputs").onchange=event=>action(null,"editor-error",async()=>{
  const input=event.target,file=input.files[0];if(!file||editorBusy)return;
  try{
    if(file.size>180000)throw new Error("方案文件超过 180 KB。");
    if(dirty&&!confirm("导入将替换当前参数草稿，名称与说明保持不变。是否继续？"))return;
    await withEditorLock(async()=>{
      let value;try{value=JSON.parse(await file.text());}catch{throw new Error("无法读取参数备份，请选择有效的 JSON 方案文件。当前草稿已保留。");}
      applyInputDocument(value);toast("参数已导入为草稿，保存后生成新版本。");
    });
  }finally{input.value="";}
});
$("project-preset").onchange=()=>{
  if(editorBusy||archivedProject()){syncPresetSelection();return;}
  const key=$("project-preset").value,preset=info.presets.find(p=>p.key===key);
  if(preset){loadPreset(preset);return;}
  if(!key){
    referenceKey="";
    designGroupReviews=new Map();
    clearPresetUndo();
    syncDesignReadiness();
    setDirty();
  }
  updatePresetContext();
};
$("undo-preset").onclick=undoPreset;
$("open-references").onclick=showReferences;
$("reference-engine").onchange=renderReference;
$("apply-preset").onclick=()=>{const preset=info.presets.find(p=>p.key===$("project-preset").value);if(!preset){toast("该机型仅供资料查阅，当前模型尚不支持此推进剂组合。");return;}loadPreset(preset);};
$("upgrade-methane-dataset").onclick=()=>{
  if(editorBusy||!current||!draft||archivedProject())return;
  const updated=upgradeMethaneDatasetDraft(draft);if(updated===draft)return;
  draft=updated;clearPresetUndo();setEditorError("");syncPresetSelection();renderFields();setDirty();
  toast("已将当前草稿改为 GRI30 high-T。点击“保存方案”生成新版本；旧版本与已有结果保留，其他模型设置不变。");
};
$("parameter-fields").addEventListener("click",event=>{
  if(editorBusy||archivedProject())return;
  const channelAction=event.target.closest("button[data-cooling-channel-action]");
  if(channelAction){editCoolingChannel(channelAction);return;}
  const pressureView=event.target.closest("[data-pressure-view]");
  const pressureBranch=event.target.closest("[data-pressure-branch]");
  if(pressureView||pressureBranch){
    if(pressureView)pressureWorkspaceState.view=pressureView.dataset.pressureView;
    if(pressureBranch){pressureWorkspaceState.branch=pressureBranch.dataset.pressureBranch;pressureWorkspaceState.view="budget";}
    filterParameters();return;
  }
  const pressureLink=event.target.closest("[data-pressure-group]");
  if(pressureLink){openReadinessField(pressureLink.dataset.pressureGroup);return;}
  if(event.target.closest("[data-pressure-apply-shared]")){
    if(!draft)return;
    const line=draft.valve_line_dp_ratio,injector=draft.preburner_injector_dp_ratio;
    if(!Number.isFinite(line)||!Number.isFinite(injector)||line<0||injector<=0){setEditorError("请先填写有效的统一压降预算。");return;}
    for(const branch of ["ox_to_opb","fuel_to_opb","ox_to_fpb","fuel_to_fpb"]){
      draft[`${branch}_line_dp_ratio`]=line;draft[`${branch}_injector_dp_ratio`]=injector;
    }
    clearPresetUndo();setEditorError("");syncPresetSelection();renderFields();setDirty();
    toast("已将统一预算填入四条支路，请核对并确认本组参数。");return;
  }
  const interfaceLink=event.target.closest("[data-preburner-group]");
  if(interfaceLink){openReadinessField(interfaceLink.dataset.preburnerGroup);return;}
  const parameterLink=event.target.closest("[data-readiness-group]");
  if(parameterLink){openReadinessField(parameterLink.dataset.readinessGroup,parameterLink.dataset.readinessField);return;}
  const review=event.target.closest("[data-review-design-group]");
  if(review)return action(review,"editor-error",()=>confirmDesignGroup(review.dataset.reviewDesignGroup));
  if(!event.target.closest("[data-upgrade-cooling]")||editorBusy||!current||!draft)return;
  const updated=upgradeCoolingModelDraft(draft);if(updated===draft)return;
  draft=updated;clearPresetUndo();setEditorError("");syncPresetSelection();renderFields();setDirty();
  toast("已建立准一维冷却草稿。请核对型面、通道及材料；保存后生成新版本，旧记录保持不变。");
});
$("project-form").onsubmit=event=>{event.preventDefault();return action(event.submitter,"editor-error",()=>{validateDraft();return withEditorLock(async()=>{await saveProject();await refreshProjectSide();toast("方案新版本已保存。");});});};
$("reload-project").onclick=()=>action(null,"editor-error",async()=>{if(editorBusy||!mayLeave())return;await withEditorLock(async()=>{fillProject(await api("/projects/"+current.id));await refreshProjectSide();});});
$("export-inputs").onclick=()=>action(null,"editor-error",async()=>{if(editorBusy)return;if(!archivedProject())validateDraft();download(buildInputBackup(),"lre-inputs.json");});
$("submit-job").onclick=$("header-run").onclick=()=>action(null,"editor-error",runDesign);
$("result-selector").onchange=()=>action(null,null,()=>loadRecord($("result-selector").value));
$("toggle-parameters").onclick=openDesignParameters;
$("refresh-jobs").onclick=()=>action(null,null,async()=>renderJobs($("all-jobs"),await api("/jobs")));
$("member-form").onsubmit=event=>{event.preventDefault();if(!canManageMembers())return;action(event.submitter,"member-error",async()=>{await api("/members","POST",Object.fromEntries(new FormData(event.target)));event.target.reset();await refreshMembers();toast("成员账号已创建。");});};
$("download-result").onclick=()=>downloadResult(modalRecord);$("export-current-result").onclick=()=>downloadResult(resultRecord);
document.body.addEventListener("click",event=>{const b=event.target.closest("button");if(!b)return;if(b.hasAttribute("data-new"))newProject();if(b.dataset.project)action(b,null,()=>openProject(b.dataset.project));if(b.dataset.job)action(b,null,()=>showResult(b.dataset.job));if(b.dataset.cancel)action(b,null,async()=>{await api("/jobs/"+b.dataset.cancel+"/cancel","POST",{});await poll();toast("已请求取消任务。");});if(b.dataset.revision)action(b,"editor-error",async()=>{if(editorBusy||archivedProject()||!mayLeave())return;await withEditorLock(async()=>{const old=await api(`/projects/${current.id}/revisions/${b.dataset.revision}`);const scopeIssue=designScopeIssue(old.inputs);if(scopeIssue)throw new Error(scopeIssue);specifiedPumpSpeeds.clear();specifiedTurbineStages.clear();coolingChannelEditor=null;draft=mergeInputDefaults(clone(info.ui_schema.defaults),clone(old.inputs),true);designGroupReviews=new Map();clearPresetUndo();syncPresetSelection(true);$("project-name").value=old.name;$("project-description").value=old.description;renderFields();setDirty();toast(`已载入 v${old.revision} 为草稿，保存后将产生新版本。`);});});});
window.addEventListener("beforeunload",event=>{if(dirty){event.preventDefault();event.returnValue="";}});
async function poll(){if(polling||editorBusy||document.hidden)return;polling=true;try{await api("/health");if(!user)return;if(page==="projects")await refreshDashboard();if(page==="jobs")renderJobs($("all-jobs"),await api("/jobs"));if(page==="project"&&current)await refreshProjectSide();}catch(error){if(connected!==false)$("connection-status").textContent=error.message;}finally{polling=false;}}
async function initialize(){const state=await api("/auth/status");initialized=state.initialized;user=state.user;accessLabels(state);if(user)await enterWorkspace();else showAuth();}
$("retry-connection").onclick=()=>action($("retry-connection"),null,initialize);
setInterval(poll,5000);
try{await initialize();}catch(error){$("auth-title").textContent="连接未完成";$("auth-description").textContent=error.message;$("auth-retry").hidden=false;}
$("auth-retry").onclick=()=>action($("auth-retry"),"auth-error",async()=>{await initialize();$("auth-retry").hidden=true;});
