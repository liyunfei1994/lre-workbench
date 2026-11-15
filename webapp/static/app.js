import {renderAnalysis, configureResultSchema} from "./results.js";
import {recordModelValidity} from "./model-validity.js";
import {supportsCycleResult} from "./cycle-model.js";
import {mountSteadyWorkspace} from "./steady-workspace.js";
import {mountCoolingDesign} from "./cooling-design.js";
import {mountValidation} from "./validation.js";
import {mountThrottle,mountOperatingThrottle} from "./throttle.js";
import {mountFixedSteady} from "./fixed-steady.js";
import {renderValveRequirements} from "./valve-requirements.js";
import {renderValveDesign,mountValveSelection} from "./valve-design.js";
import {WORKSPACE_GROUPS,HUB_VIEWS,isWorkspaceHub,workspaceGroup,normalizeWorkspaceView,hasDesignParameters,renderWorkspaceBreadcrumb,renderParameterNavigation,renderWorkspaceHub,mountWorkspaceLibrary,mountNavigationResize} from "./navigation.js";
import {mountTransient} from "./transient.js";
import {mountFeedNetwork} from "./feed-network.js";
import {mountChilldown} from "./chilldown.js";
import {mountIgnition} from "./ignition.js";
import {mountShutdown} from "./shutdown.js";
import {mountComponentWorkspace} from "./component-analysis.js";
import {mountFlowWorkspace} from "./flow-analysis.js";
import {mountCycleWorkspace} from "./cycle-schematic.js";
import {mountEngineeringView} from "./engineering-view.js";
import {refreshPreburnerLimitSetup} from "./preburner-design.js";
import {mountEngineeringResize} from "./engineering-resize.js";
import {applyThermochemistryBaseline,selectThermochemistryModel} from "./thermochemistry-model.js";
import {mountSolverMonitor} from "./solver-monitor.js";
import {renderNozzleGeometry,mountNozzleGeometry} from "./nozzle-geometry.js";
import {renderNozzleDesign,mountNozzleDesign} from "./nozzle-design.js";
import {renderChamberDesign,mountChamberDesign} from "./chamber-design.js";
import {renderPumpGeometry,mountPumpGeometry} from "./pump-geometry.js";
import {renderInjectorDesign,mountInjectorDesign} from "./injector-design.js";
import {renderTurbineGeometry,mountTurbineGeometry} from "./turbine-geometry.js";
import {renderTurbineDesign,mountTurbineDesign} from "./turbine-design.js";
import {renderResultConsistency} from "./result-consistency.js";
import {savedInputDocument,platformInputValues,mergeInputDefaults,upgradeMethaneDatasetDraft,upgradeCoolingModelDraft,PREBURNER_MODE_KEY,selectPreburnerMode,selectPumpSpeedMode,selectTurbineStageMode,selectDesignEnvironment,refreshDesignEnvironment,refreshParameterInheritance,parameterInheritance,parameterValue,parameterVisible,parameterGroups,parameterLayoutDependency,renderParameterGroups,renderParameterEvidence} from "./parameter-editor.js";
import {renderHelpManual,normalizeHelpChapter,helpChapterForGroup} from "./help-manual.js";
import {collectLocalReadiness,buildDesignReadiness,renderDesignReadiness,renderDesignGroupReadiness,designReadinessStatusLabels} from "./design-readiness.js";
import {coolingInputSummaryState,renderCoolingInputSummary} from "./cooling-input-summary.js";
import {renderCoolingChannelInputs,refreshCoolingChannelStatus,applyCoolingChannelEdit,coolingChannelPendingIssues} from "./cooling-channel-inputs.js";
import {syncPressureWorkspace,renderPressureResults} from "./pressure-design.js";
import {migratePressureReview} from "./design-review-migration.js";
import {applyValveConfigurationEdit,valveConfigurationIssues,refreshValveConfigurationStatus,renderValveConfiguration} from "./valve-configuration.js";
import {buildProjectWorkflow,renderProjectOverview,renderProjectContext,renderRecordProvenance} from "./project-overview.js";
import {setupNavigationGroup,setupSection,setupNodes,setupProgress,renderSetupBranch} from "./setup-tree.js";
import {migrateWorkflowReview} from "./parameter-review.js";
import {renderSolverTask,isSolverGroup} from "./solver-workspace.js";
import {findDesignParameters,renderParameterMatches} from "./parameter-finder.js";
import {designStartingInputs,componentForLocation,componentPendingPatch,renderComponentContext,renderDesignRequirements,mountEngineeringLifecycle} from "./engineering-lifecycle.js";
import {renderCooling} from "./cooling.js";
import {mountPropertyLibrary} from "./property-library.js";

const $ = id => document.getElementById(id);
const escape = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const date = value => value ? new Date(value * 1000).toLocaleString("zh-CN", {hour12:false}) : "—";
const number = value => typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("zh-CN", {maximumSignificantDigits:7}) : "—";
const clone = value => JSON.parse(JSON.stringify(value));
const active = job => job && ["queued","running"].includes(job.status);
const statusNames = {queued:"等待计算",running:"计算中",succeeded:"计算已完成",failed:"执行失败",cancelled:"已取消",timed_out:"计算超时",interrupted:"服务中断"};
const statusClass = value => ["succeeded","pass"].includes(value) ? "success" : ["failed","fail","timed_out","interrupted"].includes(value) ? "failed" : "running";
const badge = (value, label) => `<span class="badge ${statusClass(value)}">${escape(label || statusNames[value] || value)}</span>`;
const views = {overview:["总体方案与循环设计","设计点循环匹配 · 总体状态与部件需求"],topology:["保存结果 · 循环原理图","循环连接、驱动关系与流体状态均来自所选计算记录；当前方案构型见发动机定义与设计工况。"],states:["流路状态分析","沿流路查看节点状态、压力与温度变化及组分"],components:["部件性能分析","按功能查看部件性能、进出口状态与能量转换"],log:["日志与警告","检查计算状态、警告与运行环境"],history:["方案与计算记录","查看方案版本、计算快照与历史结果"]};
views.project_overview=["方案摘要","按需查看方案身份与工作入口；设计设置从任务树开始。"];
let user=null, initialized=false, info=null, page="projects", analysisView="overview";
let privateWorkspace=false, personalWorkspace=false, remoteWorkspace=globalThis.location?.protocol==="https:";
let current=null, draft=null, dirty=false, resultRecord=null, modalRecord=null;
const specifiedPumpSpeeds = new Map();
const specifiedTurbineStages = new Map();
let coolingChannelEditor = null;
let fields=[], groups=[], projectJobs=[], selectedJobId=null, polling=false, editorBusy=false, connected=null;
let toastTimer;
let locatedParameter=null;
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
let valveSelectionCleanup=null;
let recordLoadSequence=0;
let coolingDesignView=null;
let integrationView=null;
let conceptSection="settings",libraryTab="components",libraryCleanup=null;
let propertyView=null,propertyTab="data",propertyCategory="gas";
let helpChapter="workflow",helpQuery="",helpOrigin=null,helpSearchComposing=false;
let helpSuspendedView=null;
views.results_overview=["计算结果总览","查看所选计算记录 · 性能、系统校核与模型依据"];
views.solver_diagnostics=["收敛诊断","查看所选计算记录 · 迭代过程、残差与失败定位"];
views.geometry=["推力室内型面","燃烧室、收缩段与喷管 · 保存设计点的尺寸核对与 CAD 坐标交接"];
views.nozzle_design=["喷管设计与性能","保存设计点 · 推力分解、膨胀状态、热环境与总体设计校核"];
views.chamber_design=["主燃烧室设计与分析","总体设计 · 两股入口燃气、平衡热化学、主室几何与冷却交接"];
views.pump_geometry=["泵设计与建模交接","液氧泵与甲烷泵 · 设计点、吸入条件、部件配置与 CFturbo 建模参考"];
views.injector_design=["喷注器初步设计","FFSC 三处喷注接口 · 主喷注单元尺寸与流量一致性"];
views.turbine_geometry=["涡轮设计与建模交接","富氧与富燃涡轮 · 循环边界、级方案初选与 CFturbo 建模"];
views.valve_design=["阀门初步设计","MFV、OPFV、FPOV · 流路配置、有效面积需求、候选开度与多工况选型"];
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
views.design_run=["输入检查与运行","设计点求解 · 核对设计条件与数值配置，提交当前方案计算"];
views.design_monitor=["收敛监视","设计点求解 · 分层残差与关键物理量；监视所选任务的实际记录"];
views.engineering_examples=["工程示例与来源","从工程示例开始方案，核对参数来源和建模假设"];
views.property_library=["热物性与材料数据","数据来源、物种、模型分工及性质曲线；计算记录单独保留当次依据"];
views.design_requirements=["部件设计要求","从保存的总体设计点提取部件状态、负荷和设计来源"];
views.cooling_workspace=["再生冷却设计与校核","通道与材料草案、来源工况、局部冷却研究及整机反馈"];
views.integration=["整机集成与基准确认","部件反馈、总体复算与候选硬件模型快照"];

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
const archivedViews=new Set(["history","guide"]);

function setNavigationOpen(open){
  $("workspace").classList.toggle("nav-open",open);
  $("navigation-backdrop").hidden=!open;
  $("toggle-navigation").dataset.open=String(open);
  $("toggle-navigation").setAttribute("aria-expanded",String(open));
  $("toggle-navigation").setAttribute("aria-label",open?"关闭工作区导航":"打开工作区导航");
}
function syncWorkspaceNavigation(){
  $("setup-tree-tools").hidden=page!=="project"||!current;
  $("find-parameter").disabled=editorBusy||!draft||archivedProject();
  const group=page==="project"?(analysisView==="overview"&&conceptSection==="settings"?{id:setupNavigationGroup($("parameter-group").value)}:workspaceGroup(analysisView)):null;
  document.querySelectorAll("[data-nav-group]").forEach(node=>{
    const selected=node.dataset.navGroup===group?.id;
    node.classList.toggle("is-current",selected);
    if(selected)node.open=true;
  });
  for(const node of document.querySelectorAll("[data-setup-section]")){
    const selected=page==="project"&&analysisView==="overview"&&node.dataset.setupSection===setupSection($("parameter-group").value).id;
    node.classList.toggle("is-current",selected);if(selected)node.open=true;
  }
  document.querySelectorAll("[data-view]").forEach(button=>button.setAttribute("aria-current",page==="project"&&button.dataset.view===analysisView?"page":"false"));
  document.querySelectorAll(".sidebar [data-help-chapter]").forEach(button=>button.setAttribute("aria-current",page==="project"&&analysisView==="guide"&&button.dataset.helpChapter===helpChapter?"page":"false"));
}

function currentWorkflow(){
  const available=draft?parameterGroups(groups,draft):[];
  const readiness=designReadiness?buildDesignReadiness(available,designReadiness.issues,{phase:designReadiness.phase,warnings:designReadiness.warnings,groupReviews:applicableGroupReviews(available)}):null;
  return buildProjectWorkflow({project:current,draft,record:resultRecord,readiness,groups:available,jobs:projectJobs});
}
let engineeringView=null,engineeringResize=null,engineeringRecordError="";
let solverMonitor=null;
function syncSolverMonitor(){
  const visible=Boolean(user)&&page==="project"&&analysisView==="design_monitor"&&Boolean(current);
  if(visible&&!solverMonitor)solverMonitor=mountSolverMonitor($("solver-monitor"),{
    api,onSelectRecord:id=>loadRecord(id).catch(error=>toast(error.message)),
  });
  solverMonitor?.update({visible,project:current,record:resultRecord,jobs:projectJobs,selectedJobId,draftDirty:dirty});
}
function syncEngineeringView(workflow){
  engineeringView?.update({project:current,draft,dirty,record:resultRecord,jobs:projectJobs,
    selectedJobId,groupId:$("parameter-group").value,groups:parameterGroups(groups,draft||{}),
    workflow:workflow||currentWorkflow(),solverControls:info?.ui_schema?.solver_controls,
    busy:editorBusy,recordError:engineeringRecordError});
}
function configurationSelected(){return $("parameter-group").value==="cycle";}
function currentComponent(){return componentForLocation(info?.ui_schema?.engineering_workflow,analysisView,$("parameter-group").value);}
function paintComponentWorkflow(){
  const host=$("component-workflow-context"),component=currentComponent();
  host.hidden=!component;
  if(!component)return;
  const markup=renderComponentContext({component,groups,views,project:current,draft,record:resultRecord,busy:editorBusy,view:analysisView});
  if(host.innerHTML!==markup)host.innerHTML=markup;
}
function syncParameterPurpose(){
  const configuration=configurationSelected();
  const numerical=isSolverGroup($("parameter-group").value);
  $("parameter-panel-title").textContent=configuration?"构型说明":numerical?"设计点求解配置":"参数设置";
  $("parameter-panel-note").textContent=configuration?"说明当前方案的推进剂组合、循环方式与系统连接。":numerical?"配置随当前方案保存；工程视图展示任务、匹配关系和完整收敛准则。":"从左侧选择设置项；系统图说明参数所属对象及相关设置。";
  $("parameter-pane").setAttribute("aria-label",configuration?"构型说明":"参数设置");
  $("dirty-label").hidden=configuration;
  $("parameter-edit-actions").hidden=configuration;
  $("parameter-edit-actions").inert=configuration;
  $("parameter-edit-note").textContent=numerical?"数值配置随方案保存，无需额外工程确认。在“输入检查与运行”核对完整任务后提交计算。":"本页用于参数定义与确认。完成设置后，在“输入检查与运行”中检查整机输入并启动设计点计算。";
  $("configuration-actions").hidden=!configuration;
  if($("parameter-group").value==="preburner_design"){
    $("parameter-panel-note").textContent="设置参与总体循环匹配的设计工作条件；关联模型和记录摘要见右侧工程视图。";
    $("parameter-edit-note").textContent="保存并确认工作条件后，在“输入检查与运行”提交总体循环计算。工程限值按项目需要在“校核与记录”中另行管理。";
  }
  if($("parameter-group").value==="engineering_constraints"){
    $("parameter-panel-note").textContent="按项目需要登记限值和适用依据；当前工作条件在预燃室热力匹配条件中设置。";
    $("parameter-edit-note").textContent="限值随方案保存，无需额外确认。修改后计算记录仍保留原限值；重新运行设计点后查看对应校核结果。";
  }
  if(currentComponent()){
    $("parameter-panel-note").textContent="编辑当前部件草案；系统图定位所属对象，部件工作区提供来源工况、保存结果和整机反馈入口。";
    $("parameter-edit-note").textContent="保存方案保留草稿。使用上方部件反馈入口可同时记录修改与来源设计点，再进入整机复核。";
  }
}
function updateViewHeading(){
  const setting=analysisView==="overview"&&conceptSection==="settings"?groups.find(group=>group.id===$("parameter-group").value):null;
  const branch=setting?WORKSPACE_GROUPS.find(group=>group.id===setupNavigationGroup(setting.id)):null;
  const title=setting?.title||views[analysisView][0];
  $("analysis-title").textContent=title;
  const section=setting?setupSection(setting.id):null;
  const configuration=setting?.id==="cycle"||setting?.engine_definition===true;
  $("analysis-subtitle").textContent=configuration?"明确发动机构型，定义设计点目标与环境边界；系统图说明部件连接和参数归属。":setting?isSolverGroup(setting.id)?"设计点求解 · 数值配置与迭代初值；通过输入检查后保存并运行。":`${setting.workflow_stage==="components"?"部件设计输入":"总体设计输入"} · ${section.label}；本页用于参数定义与确认`:views[analysisView][1];
  $("analysis-breadcrumb").innerHTML=setting&&(["configuration","requirements","constraints"].includes(section.id)||isSolverGroup(setting.id))?renderWorkspaceBreadcrumb(analysisView,title,branch):setting?renderWorkspaceBreadcrumb(analysisView,section.label,branch)+`<i aria-hidden="true">/</i><span>${escape(title)}</span>`:renderWorkspaceBreadcrumb(analysisView,title);
  syncParameterPurpose();
  if(setting&&setupNavigationGroup(setting.id)==="components")$("analysis-subtitle").textContent=`部件设计输入 · ${section.label}；修改后的草案通过整机复核更新计算结果。`;
  if(setting?.optional_configuration)$("analysis-subtitle").textContent="按需设置方案限值，查看计算记录的工程校核；未设置预燃室温度上限仍可求解。";
  if(page==="project")$("page-name").textContent=title;
  // Record loading may redirect the current view without switching workspace pages.
  $("header-run").hidden=page!=="project"||analysisView!=="design_run";
}
function paintProjectWorkflow(){
  if(!current||!draft)return;
  const model=currentWorkflow();
  const context=$("project-context");
  const contextMarkup=renderProjectContext(model,{dirty,differencesOpen:Boolean(context.querySelector("details")?.open)});
  if(context.innerHTML!==contextMarkup)context.innerHTML=contextMarkup;
  const provenance=$("record-provenance");
  const provenanceMarkup=renderRecordProvenance(model,{open:Boolean(provenance.querySelector(".project-model-provenance")?.open),recordOpen:Boolean(provenance.querySelector(".project-record-details")?.open)});
  if(provenance.innerHTML!==provenanceMarkup)provenance.innerHTML=provenanceMarkup;
  syncEngineeringView(model);
  paintComponentWorkflow();
  const available=parameterGroups(groups,draft);
  if(analysisView==="design_run"){
    const host=$("design-run-task-summary");
    const markup=renderSolverTask({draft,groups:available,solverControls:info.ui_schema.solver_controls,compact:true});
    if(host.innerHTML!==markup)host.innerHTML=markup;
  }
  const readiness=designReadiness?buildDesignReadiness(available,designReadiness.issues,{phase:designReadiness.phase,warnings:designReadiness.warnings,groupReviews:applicableGroupReviews(available)}):null;
  const nodes=setupNodes(available,readiness,model.freshness.changedGroups||[],Boolean(resultRecord));
  paintCurrentGroupReadiness(readiness,model);
  const selected=page==="project"&&analysisView==="overview"&&conceptSection==="settings"?$("parameter-group").value:"";
  for(const host of document.querySelectorAll("[data-setup-branch]")){
    const markup=renderSetupBranch(nodes,host.dataset.setupBranch,selected);
    if(host.innerHTML!==markup)host.innerHTML=markup;
  }
  for(const badge of document.querySelectorAll("[data-setup-summary]")){
    const progress=setupProgress(nodes,badge.dataset.setupSummary);
    badge.textContent=progress.label;badge.title=progress.reason;badge.dataset.status=progress.state;
    badge.hidden=!nodes.some(node=>node.branch===badge.dataset.setupSummary);
  }
  const progress=setupProgress(nodes);
  $("setup-tree-progress").textContent=progress.label;
  $("setup-tree-progress").title=progress.reason;
  for(const button of document.querySelectorAll("[data-view]")){
    const dependent=["geometry","chamber_design","nozzle_design","pump_geometry","injector_design","turbine_geometry","valve_design","results_overview","states","components","operating_throttle","transient","feed","chilldown","ignition","shutdown"].includes(button.dataset.view);
    if(!dependent)continue;
    let mark=button.querySelector(".workflow-result-status");
    if(!mark){mark=document.createElement("small");mark.className="setup-node-status workflow-result-status";button.append(mark);}
    const stale=model.freshness.state==="stale";
    mark.hidden=!stale&&Boolean(resultRecord);mark.dataset.status=stale?"stale":"pending";mark.textContent=stale?"待更新":"待基准";
    mark.title=stale?"来源设计点与当前输入不同。原记录保留，派生结果与运行任务需重新核对来源。":"尚未选择计算记录，请先建立并检查总体设计点。";
  }
  if(analysisView==="project_overview"){
    const host=$("analysis-content"),markup=renderProjectOverview(model);if(host.innerHTML!==markup)host.innerHTML=markup;
  }
}

// Tab-local navigation only: drafts, credentials and results remain outside storage.
function navigationKey(){return user?.id==null?null:`lre.workspace.navigation.v1:${user.id}`;}
function readNavigation(){
  try{
    const key=navigationKey(),saved=key?JSON.parse(sessionStorage.getItem(key)):null;
    if(!saved||saved.version!==1||!["project","projects","jobs","members"].includes(saved.page))return null;
    const view=normalizeWorkspaceView(saved.view==="project_overview"?"overview":saved.view);
    return {page:availableWorkspacePage(saved.page),
      projectId:typeof saved.projectId==="string"?saved.projectId:null,
      view:Object.hasOwn(views,view)?view:"overview",
      parameterGroup:saved.view==="project_overview"?"design":undefined,
      jobId:typeof saved.jobId==="string"?saved.jobId:null,
      helpChapter:normalizeHelpChapter(saved.helpChapter,groups),
      conceptSection:saved.view==="project_overview"?"settings":["settings","results","diagnostics"].includes(saved.conceptSection)?saved.conceptSection:"settings",
      libraryTab:["components","characteristics","properties","numerics"].includes(saved.libraryTab)?saved.libraryTab:"components"};
  }catch{return null;}
}
function rememberNavigation(){
  try{
    const key=navigationKey();if(!key)return;
    // The optional summary does not replace the user's last working location.
    const location=analysisView==="project_overview"?(readProjectNavigation(current?.id)||{view:"overview",conceptSection:"settings",libraryTab,helpChapter}):{view:analysisView,conceptSection,libraryTab,helpChapter};
    sessionStorage.setItem(key,JSON.stringify({version:1,page,projectId:current?.id||null,...location,jobId:selectedJobId}));
    if(page==="project"&&current&&analysisView!=="project_overview")sessionStorage.setItem(`${key}:project:${current.id}`,JSON.stringify({...location,jobId:selectedJobId}));
  }catch{/* Navigation remains usable if browser storage is unavailable. */}
}
function readProjectNavigation(id){
  try{const key=navigationKey(),saved=key?JSON.parse(sessionStorage.getItem(`${key}:project:${id}`)):null;
    if(saved&&Object.hasOwn(views,normalizeWorkspaceView(saved.view)))return {...saved,view:normalizeWorkspaceView(saved.view==="project_overview"?"overview":saved.view),...(saved.view==="project_overview"?{parameterGroup:"design"}:{}),conceptSection:saved.view==="project_overview"?"settings":["settings","results","diagnostics"].includes(saved.conceptSection)?saved.conceptSection:"settings"};
  }catch{/* Open engine definition when the remembered location is unavailable. */}
  return null;
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
  paintProjectWorkflow();syncWorkspaceNavigation();
  if(current)updateViewHeading();
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
  $("design-run-error").textContent=message;
  $("editor-error").textContent=message;$("project-history-error").textContent=message;
  $("workspace-error").textContent=message;$("workspace-error").hidden=!message;
}
function setDirty(value=true) {
  dirty=value;
  for(const id of ["dirty-label","project-dirty-label","valve-dirty-label"]){$(id).textContent=value?"尚未保存":"已保存";$(id).className="badge"+(value?" dirty":" success");}
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
    unavailable:"输入检查未完成。请在本页重新检查。"}[designReadiness?.phase]||"正在检查设计点输入…";
}
function updateActionState(){
  syncSolverMonitor();
  $("engineering-view").inert=editorBusy;
  $("parameter-help").hidden=analysisView==="guide";
  $("parameter-help").disabled=editorBusy;
  document.querySelectorAll("[data-help-chapter],[data-help-return]").forEach(button=>button.disabled=editorBusy);
  $("find-parameter").disabled=editorBusy||!current||!draft||archivedProject();
  document.querySelectorAll("[data-view],[data-nav]").forEach(button=>button.disabled=editorBusy);
  document.querySelectorAll("[data-workspace-view],[data-workspace-action],[data-concept-section],[data-setup-group],[data-task-group]").forEach(button=>button.disabled=editorBusy);
  const running=projectJobs.find(active), preflightBlocked=designReadinessEnabled()&&!canRunDesignPoint();
  for(const id of ["header-run","design-run-submit"]){
    $(id).disabled=page!=="project"||analysisView!=="design_run"||!current||editorBusy||connected===false||!!running||!!propellantIssue()||preflightBlocked||archivedProject();
    $(id).title=preflightBlocked?readinessRunMessage():"";
  }
  // Navigation remains available while inputs need correction or confirmation.
  for(const [id,view] of [["open-design-run","overview"],["valve-open-design-run","valve_design"]])
    $(id).disabled=page!=="project"||analysisView!==view||!current||!draft||editorBusy||archivedProject();
  for(const id of ["design-run-back","design-run-solver"])
    $(id).disabled=!current||!draft||editorBusy||archivedProject();
  $("design-run-solver").hidden=!parameterGroups(groups,draft||{}).some(group=>group.id==="solver");
  const progress=running?`${statusNames[running.status]} · 提交输入 v${running.revision} · 分层迭代过程见收敛监控` :readinessRunMessage();
  $("design-run-progress").textContent=progress;
  $("design-run-project").textContent=current?$("project-name").value||current.name:"尚未选择方案";
  $("design-run-version").textContent=current?dirty?`当前草稿 · 基于方案 v${current.revision}`:`已保存方案 v${current.revision}`:"—";
  $("design-run-draft-note").textContent=dirty?"含未保存修改，启动计算时先保存新版本。":"使用当前已保存的方案输入。";
  $("valve-review").hidden=!designReadinessEnabled()||designGroupReviews.has("valve_configuration");
  $("valve-review").disabled=editorBusy||connected===false||designReadiness?.phase!=="ready";
  $("valve-readiness").disabled=editorBusy;
  for(const id of ["header-save","history-save","valve-save"])$(id).disabled=(!hasDesignParameters(analysisView)&&analysisView!=="history") || !current || editorBusy || connected===false || archivedProject();
  for(const id of ["choose-import","import-inputs","export-inputs","reload-project"])$(id).disabled=!current || editorBusy;
  for(const id of ["choose-import","import-inputs","project-preset","project-name","project-description"])$(id).disabled=editorBusy||archivedProject();
  if(info)updatePresetContext();
  const cancellable=projectJobs.find(j=>active(j)&&(user?.role==="admin"||user?.id===j.submitted_by));
  $("header-cancel").hidden=page!=="project"||!cancellable;
  $("header-cancel").disabled=editorBusy||connected===false;
  $("header-cancel").dataset.cancel=cancellable?.id||"";
}
async function withEditorLock(task){
  if(editorBusy) return;
  editorBusy=true;const containers=[$("project-form"),$("project-history"),$("valve-design-inputs"),$("concept-reference-panel")];
  containers.forEach(el=>el.inert=true);
  const controls=containers.flatMap(el=>[...el.querySelectorAll("input,select,textarea,button")]);
  const disabled=controls.map(el=>el.disabled);controls.forEach(el=>el.disabled=true);updateActionState();
  try{return await task();}finally{controls.forEach((el,i)=>el.disabled=disabled[i]);containers.forEach(el=>el.inert=false);editorBusy=false;updateActionState();paintComponentWorkflow();syncEngineeringView();}
}
function showAuth(){
  integrationView?.dispose();integrationView=null;
  solverMonitor?.dispose();solverMonitor=null;
  engineeringRecordError="";
  engineeringView?.dispose();engineeringView=null;
  helpOrigin=null;helpQuery="";helpChapter="workflow";helpSuspendedView=null;helpSearchComposing=false;
  resetParameterFinder();clearParameterLocation();
  if(valveSelectionCleanup){valveSelectionCleanup();valveSelectionCleanup=null;}
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
  $("project-preset").innerHTML='<option value="">选择示例以浏览</option><optgroup label="工程示例 · 选择仅浏览">'+options+'</optgroup>'+(compatibilityOptions?'<optgroup label="历史方案标识 · 不作为常规新建入口">'+compatibilityOptions+'</optgroup>':"")+(references.length?'<optgroup label="公开机型资料 · 尚不支持计算">'+references.map(r=>`<option value="${escape(r.key)}">${escape(r.name)} · 仅资料</option>`).join("")+'</optgroup>':"");
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
      else if(!project){if(saved.view==="guide")openHelp(saved.helpChapter);else await navigate("projects");}
    }else if(projects.length)await openProject(projects[0].id);
    else await navigate("projects");
  }
}
function switchPage(next){
  if(next!=="project")resetParameterFinder();
  if(next!=="project"&&valveSelectionCleanup){valveSelectionCleanup();valveSelectionCleanup=null;}
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
  $("page-name").textContent=next==="project"?$("analysis-title").textContent:{projects:projectsLabel(),jobs:"计算任务",members:"成员管理"}[next];
  $("header-run").hidden=next!=="project" || analysisView!=="design_run";
  $("open-project-summary").hidden=next!=="project";
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
function updatePropellantContext(){syncDesignReadiness();}
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
    group:$("parameter-group").value};
  specifiedPumpSpeeds.clear();specifiedTurbineStages.clear();coolingChannelEditor=null;
  draft={...clone(info.ui_schema.defaults),...platformInputValues(clone(preset.inputs))};referenceKey=preset.key;
  adoptPresetGroupValues();
  resetParameterFinder();$("parameter-group").value=groups.find(g=>g.fields.some(f=>f.key==="cycle_type"))?.id||"";
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
  resetParameterFinder();$("parameter-group").value=previous.group;
  renderFields();
  syncPresetSelection();setDirty(previous.dirty);clearPresetUndo();setEditorError("");
  toast("已恢复切换前的参数与编辑内容。");
}
function updatePresetContext(){
  const key=$("project-preset").value, reference=(info.reference_engines||[]).find(r=>r.key===key||r.preset_key===key);
  const preset=info.presets.find(p=>p.key===key);
  const applicable=Boolean(preset&&preset.routine_design!==false);
  $("apply-preset").disabled=!applicable||editorBusy||archivedProject();
  $("new-from-example").disabled=!applicable||editorBusy;
  $("preset-context").textContent=preset?.routine_design===false?"本方案保留历史预算模型，可读取、保存和复现；该示例不作为常规新设计入口。":applicable&&preset.display_description?preset.display_description:reference?`${reference.propellants} · ${reference.cycle_label}。${reference.applicable?"":` ${reference.compatibility_reason} 当前计算方案保持不变。`}`:applicable?"可从此示例新建方案；需要替换当前草稿时展开下方操作。":"请选择条目浏览，当前草稿保持原值。";
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
  // Identity already participates in architecture below. Keep pre-merge review
  // signatures valid when readonly identity fields join the design group.
  const values=group.fields.filter(field=>!(group.engine_definition&&field.read_only&&["cycle_type","propellant_pair"].includes(field.key))).map(field=>{
    const inheritance=parameterInheritance(field,inputs);
    return [field.key,field.virtual?parameterValue(field,inputs):inheritance?inheritance.value:inputs[field.key]];
  });
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
    if(migrated)return [group.id,migrated];
    const workflowReview=await migrateWorkflowReview({reviews,inputs:draft,group,
      legacyGroups:info.ui_schema.review_legacy_groups,previousGroups:info.ui_schema.review_previous_groups,
      signatureFor:designGroupSignature,digest:designReviewDigest});
    return workflowReview?[group.id,workflowReview]:null;
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
  const group=parameterGroups(groups,draft).find(item=>item.id===id);
  if(!group||group.optional_configuration||!(group.requires_review===true||group.fields.some(field=>!field.read_only)))return;
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
function paintCurrentGroupReadiness(model=null,workflow=null){
  const enabled=designReadinessEnabled()&&Boolean(designReadiness);
  if(enabled&&!model)model=buildDesignReadiness(parameterGroups(groups,draft),designReadiness.issues,
    {phase:designReadiness.phase,warnings:designReadiness.warnings,groupReviews:applicableGroupReviews(parameterGroups(groups,draft))});
  if(enabled&&!workflow)workflow=currentWorkflow();
  for(const [id,groupId] of [["design-readiness",$("parameter-group").value],["valve-group-readiness","valve_configuration"]]){
    const visible=enabled&&groupId!=="cycle";
    const host=$(id);host.hidden=!visible;
    host.innerHTML=visible?renderDesignGroupReadiness(model,groupId,{freshness:workflow.freshness,hasResult:workflow.hasResult}):"";
  }
}
function paintDesignReadiness(){
  const enabled=designReadinessEnabled();
  if(enabled&&designReadiness){
    const {issues,phase,warnings}=designReadiness;
    const available=parameterGroups(groups,draft);
    const model=buildDesignReadiness(available,issues,{phase,warnings,groupReviews:applicableGroupReviews(available)});
    designReadiness.pendingReviewCount=model.pendingReviewCount;
    const target=$("design-run-readiness");
    const expansionState=[".design-readiness-groups",".design-readiness-warnings"].map(selector=>
      [selector,target.querySelector(selector)?.open??selector===".design-readiness-groups"]);
    target.innerHTML=renderDesignReadiness(model);
    for(const [selector,open] of expansionState){const details=target.querySelector(selector);if(details)details.open=open;}
    const byGroup=new Map(model.groups.map(item=>[item.id,item]));
    for(const option of $("parameter-group").querySelectorAll("option")){
      const item=byGroup.get(option.value);
      if(item)option.textContent=`${item.title} · ${item.configurationLabel||designReadinessStatusLabels[item.status]}`;
    }
    for(const group of $("parameter-fields").querySelectorAll("[data-group]")){
      const item=byGroup.get(group.dataset.group),legend=group.querySelector("legend");
      if(!item||!legend)continue;
      let mark=legend.querySelector(".design-readiness-group-status");
      if(!mark){mark=document.createElement("span");mark.className="design-readiness-group-status";legend.append(" ",mark);}
      mark.dataset.status=item.status;mark.textContent=item.configurationLabel||designReadinessStatusLabels[item.status];
      let confirm=group.querySelector("[data-review-design-group]");
      if(item.status==="review"&&!confirm){
        confirm=document.createElement("button");
        confirm.type="button";
        confirm.dataset.reviewDesignGroup=item.id;
        confirm.textContent="确认并保存本组参数";
        confirm.title="保存当前设置和本组确认记录；参数发生变化后需重新确认。";
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
  }else {paintCurrentGroupReadiness();$("design-run-readiness").innerHTML="";}
  paintProjectWorkflow();
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
function updateParameterMatches(){
  const query=$("parameter-search").value;
  const matches=draft&&!archivedProject()?findDesignParameters(groups,draft,query):[];
  $("parameter-matches").innerHTML=renderParameterMatches(matches,query);
  $("parameter-match-status").textContent=query.trim()?`找到 ${matches.length} 个参数`:"";
}
function openParameterFinder(){
  if(editorBusy||page!=="project"||!current||!draft||archivedProject())return;
  updateParameterMatches();
  if(!$("parameter-finder").open)$("parameter-finder").showModal();
  $("find-parameter").setAttribute("aria-expanded","true");
  $("parameter-search").focus();
}
function closeParameterFinder(restoreFocus=true){
  const wasOpen=$("parameter-finder").open;
  if(wasOpen)$("parameter-finder").close();
  $("find-parameter").setAttribute("aria-expanded","false");
  if(wasOpen&&restoreFocus)$("find-parameter").focus();
}
function resetParameterFinder(){
  closeParameterFinder(false);
  $("parameter-search").value="";$("parameter-matches").innerHTML="";$("parameter-match-status").textContent="";
}
function openParameterMatch(groupId,fieldKey){
  if(editorBusy||page!=="project"||!current||!draft||archivedProject())return;
  const group=parameterGroups(groups,draft).find(item=>item.id===groupId);
  if(!group?.fields.some(field=>field.key===fieldKey&&parameterVisible(field,draft))){
    updateParameterMatches();$("parameter-match-status").textContent="该参数已不适用于当前设置，查找结果已更新。";return;
  }
  closeParameterFinder(false);
  openReadinessField(groupId,fieldKey);
}
function clearParameterLocation(){
  locatedParameter?.classList.toggle("parameter-located",false);locatedParameter=null;
}
function openReadinessField(groupId,fieldKey){
  const available=draft?parameterGroups(groups,draft):[];
  if(groupId==="thermo")groupId="initialization";
  if(groupId==="cycle"&&!available.some(group=>group.id==="cycle"))groupId="design";
  if(fieldKey)groupId=available.find(group=>group.fields.some(field=>field.key===fieldKey))?.id||groupId;
  if(editorBusy||!draft||!parameterGroups(groups,draft).some(group=>group.id===groupId))return;
  $("parameter-group").value=groupId;
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
  clearParameterLocation();
  if(row){locatedParameter=row;row.classList.toggle("parameter-located",true);}
  const selector="[data-input], [data-valve-action], [data-cooling-channel-action]";
  let input=row?.querySelector(selector)||(!fieldKey&&group?.querySelector(selector));
  // Custom editors can begin with controls inside a closed advanced section.
  // Keep that section closed and focus the visible field container instead.
  if(input?.closest?.("[hidden]"))input=null;
  let inputDetails=input?.closest?.("details");
  while(inputDetails){if(!inputDetails.open){input=null;break;}inputDetails=inputDetails.parentElement?.closest("details");}
  const control=input&&!input.disabled?input:row||group||$("analysis-title");
  if(control!==input)control.setAttribute("tabindex","-1");
  control.focus({preventScroll:true});(row||group||control).scrollIntoView?.({block:"nearest"});
}
function renderFields(selected=$("parameter-group").value){
  const available=parameterGroups(groups,draft);
  if(selected==="cycle"&&!available.some(g=>g.id==="cycle"))selected="design";
  // Saved thermal-group locations now resolve to numerical initialization.
  if(selected==="thermo" && !available.some(g=>g.id==="thermo"))selected="initialization";
  if(selected==="pressure" && draft.cycle_type==="ffsc")selected="pressure_ffsc";
  clearParameterLocation();
  const expanded=new Set([...$("parameter-fields").querySelectorAll("[data-initials], [data-parameter-fold], [data-definition-help]")].filter(node=>node.open).map(node=>node.dataset.initials||node.dataset.parameterFold||node.dataset.definitionHelp));
  $("parameter-group").innerHTML=renderParameterNavigation(available);
  $("parameter-group").value=available.some(g=>g.id===selected)?selected:available[0]?.id||"";
  $("parameter-fields").innerHTML=renderParameterGroups(available,draft,expanded);
  renderCoolingChannelEditor();filterParameters();updatePropellantContext();syncPreburnerContext();rememberParameterGroup();
}
function editValveConfiguration(element){
  if(editorBusy||archivedProject()||!draft)return;
  const catalog=editorField("valve_configuration_json")?.valve_catalog||{};
  try{
    draft=applyValveConfigurationEdit(draft,{action:element.dataset.valveAction,
      value:element.value,valveId:element.dataset.valveId,key:element.dataset.valveKey,
      index:element.dataset.valveIndex,column:element.dataset.valveColumn},catalog);
    clearPresetUndo();setEditorError("");syncPresetSelection();setDirty();
    const structural=!["field","curve","raw"].includes(element.dataset.valveAction);
    if(structural||analysisView==="valve_design")renderFields();
    else refreshValveConfigurationStatus($("parameter-fields"),draft,catalog);
    if(analysisView==="valve_design"){
      if(structural)renderValveFields(true);
      else {
        refreshValveConfigurationStatus($("valve-parameter-fields"),draft,catalog);
        $("valve-parameter-fields").dataset.snapshot=valveEditorSnapshot();
      }
    }
  }catch(error){setEditorError(error.message);}
}
function valveEditorSnapshot(){
  return JSON.stringify([current?.id,draft,editorField("valve_configuration_json")]);
}
function renderValveFields(force=false){
  const host=$("valve-parameter-fields");
  const snapshot=valveEditorSnapshot();
  // Polling updates saved results independently; keep the active input and caret.
  if(!force&&host.dataset.snapshot===snapshot)return;
  const expanded=new Set([...host.querySelectorAll("[data-parameter-fold]")].filter(node=>node.open).map(node=>node.dataset.parameterFold));
  host.innerHTML=renderValveConfiguration(draft,editorField("valve_configuration_json")||{}, {expanded});
  host.dataset.snapshot=snapshot;
}
function filterParameters(){
  const selected=$("parameter-group").value;let count=0;
  $("parameter-fields").querySelectorAll("[data-group]").forEach(group=>{
    let matches=0;group.querySelectorAll("[data-field]").forEach(row=>{const f=editorField(row.dataset.field);const found=group.dataset.group==="cycle"||!!f&&parameterVisible(f,draft);row.hidden=!found;if(found)matches++;});
    group.querySelectorAll("[data-initials], [data-parameter-fold]").forEach(details=>{const owner=details.closest?.("[data-field]");const matched=owner?!owner.hidden:[...details.querySelectorAll("[data-field]")].some(row=>!row.hidden);details.hidden=!matched;});
    group.hidden=!matches||group.dataset.group!==selected;if(!group.hidden)count+=matches;
  });$("parameter-empty").hidden=count>0;
  syncPressureWorkspace($("parameter-fields"),{...pressureWorkspaceState,search:false,inputs:draft});
  syncParameterPurpose();
  paintCurrentGroupReadiness();
}
function updateDocument(){if(!current)return;$("document-title").textContent=$("project-name").value+(dirty?" · 已修改":"");$("document-meta").textContent=`方案 v${current.revision} · ${current.author} · ${date(current.updated)}`;}
function fillProject(project,keepReference=false){
  if(current?.id!==project.id)engineeringRecordError="";
  if(!keepReference||current?.id!==project.id){specifiedPumpSpeeds.clear();specifiedTurbineStages.clear();coolingChannelEditor=null;}
  const changed=current?.id!==project.id;current=project;draft=mergeInputDefaults(clone(info.ui_schema.defaults),clone(project.inputs),true);
  if(info.design_review_persistence===true||changed||!keepReference)restoreDesignGroupReviews(project.design_reviews);
  if(changed){recordLoadSequence++;refreshSequence++;resultRecord=null;selectedJobId=null;projectJobs=[];analysisView="overview";conceptSection="settings";helpOrigin=null;helpQuery="";resetParameterFinder();}
  if(archivedProject())resetParameterFinder();
  if(archivedProject())analysisView="history";
  $("project-name").value=project.name;$("project-description").value=project.description;setEditorError("");
  if(changed)$("project-more-actions").open=false;
  clearPresetUndo();syncPresetSelection(changed||!keepReference);
  renderFields(changed?readParameterGroup():$("parameter-group").value);updatePresetContext();setDirty(false);updateDocument();renderView();updateActionState();
}
async function openProject(id,savedNavigation=null){if(editorBusy||(page==="project"&&!mayLeave()))return;await withEditorLock(async()=>{
  savedNavigation ||= readProjectNavigation(id);
  const project=await api("/projects/"+id);
  if(project?.id!==id)throw new Error("返回的方案与所选方案不一致，请重新打开方案。");
  fillProject(project);
  if(savedNavigation){analysisView=savedNavigation.view==="project_overview"?"overview":savedNavigation.view;selectedJobId=savedNavigation.jobId;conceptSection=savedNavigation.conceptSection||"settings";libraryTab=savedNavigation.libraryTab||"components";helpChapter=normalizeHelpChapter(savedNavigation.helpChapter,groups);if(savedNavigation.parameterGroup||savedNavigation.view==="project_overview")renderFields(savedNavigation.parameterGroup||"design");renderView();}
  switchPage("project");await refreshProjectSide();
});}
async function chooseView(view){
  view=normalizeWorkspaceView(view);
  if(editorBusy || !Object.hasOwn(views,view))return;
  if(view==="guide"){openHelp("workflow");return;}
  if(page!=="project"||!current){
    if(current){await openProject(current.id);}
    else {const projects=await api("/projects");if(!projects.length){newProject();return;}await openProject(projects[0].id);}
  }
  if(view==="overview")conceptSection="settings";
  analysisView=view;renderView();switchPage("project");scrollTaskTop();
}
function scrollTaskTop(){document.querySelector(".main-wrap")?.scrollTo?.({top:0,left:0});}
function helpValues(){
  return {chapter:helpChapter,query:helpQuery,groups:parameterGroups(groups,draft||{}),inputs:draft||{},catalog:info?.ui_schema?.analysis_parameter_roles,
    version:info?.ui_schema?.version?`参数定义 v${info.ui_schema.version}`:"",
    returnLabel:helpOrigin?.page==="project"?"返回原工作页":"返回工作区"};
}
function paintHelp(){
  if(helpSearchComposing)return;
  const focused=document.activeElement?.id==="help-search",start=focused?document.activeElement.selectionStart:null,end=focused?document.activeElement.selectionEnd:null;
  helpChapter=normalizeHelpChapter(helpChapter,groups);
  $("help-content").innerHTML=renderHelpManual(helpValues());
  if(focused){$("help-search")?.focus({preventScroll:true});if(start!=null)$("help-search")?.setSelectionRange?.(start,end);}
}
function openHelp(chapter=null){
  if(editorBusy)return;
  if(analysisView!=="guide")helpSuspendedView=page==="project"&&current?{view:analysisView,projectId:current.id,record:resultRecord}:null;
  if(analysisView!=="guide"||page!=="project")helpOrigin={projectId:current?.id||null,page,view:analysisView,conceptSection,group:$("parameter-group").value,scroll:document.querySelector(".main-wrap")?.scrollTop||0};
  const contextual=analysisView==="overview"?helpChapterForGroup($("parameter-group").value):analysisView==="engineering_examples"?"examples":analysisView==="property_library"?"properties":analysisView==="design_run"?"theory":"workflow";
  helpChapter=normalizeHelpChapter(chapter||contextual,groups);helpQuery="";helpSearchComposing=false;
  analysisView="guide";renderView();switchPage("project");scrollTaskTop();
  $("help-article-title")?.focus();
}
async function returnFromHelp(){
  if(editorBusy)return;
  const origin=helpOrigin;helpOrigin=null;
  if(origin?.page==="project"&&origin.projectId===current?.id&&current){
    conceptSection=origin.conceptSection;
    if(origin.view==="overview"&&groups.some(group=>group.id===origin.group)){
      $("parameter-group").value=origin.group;filterParameters();rememberParameterGroup();
    }
    await chooseView(origin.view==="guide"?"project_overview":origin.view);
    document.querySelector(".main-wrap")?.scrollTo?.({top:origin.scroll,left:0});
    $("analysis-title").setAttribute("tabindex","-1");$("analysis-title").focus({preventScroll:true});
  }else if(current&&origin?.page==="project")await chooseView("project_overview");
  else {switchPage(origin?.page&&origin.page!=="project"?origin.page:"projects");if(page==="projects")await refreshDashboard();}
}
function chooseConceptSection(section){
  if(editorBusy || !["settings","results","diagnostics"].includes(section))return;
  conceptSection=section;analysisView={settings:"overview",results:"results_overview",diagnostics:"solver_diagnostics"}[section];renderView();switchPage("project");scrollTaskTop();
}
function openDesignParameters(){
  if(editorBusy)return;
  const target=currentComponent()?.groups[0]||(analysisView==="injector_design"?"preburner_design":null);
  const group=target&&parameterGroups(groups,draft).find(g=>g.id===target);
  const selected=group?.id||$("parameter-group").value;
  if(selected)openReadinessField(selected);
  else{chooseConceptSection("settings");$("analysis-title").setAttribute("tabindex","-1");$("analysis-title").focus();}
}
async function submitComponentFeedback(componentId){
  if(editorBusy)return;
  requireEditableDesign();
  const component=info.ui_schema.engineering_workflow.components.find(item=>item.id===componentId);
  const patch=componentPendingPatch(component,groups,resultRecord?.inputs||current.inputs,platformInputValues(draft),current.inputs);
  if(!Object.keys(patch).length)throw new Error("本部件没有待保存的修改。");
  if(resultRecord?.status!=="succeeded"||(resultRecord.kind||"design")!=="design")throw new Error("请先选择已完成的总体设计点作为来源。");
  await withEditorLock(async()=>{
    const previousInputs=clone(current.inputs),previousDraft=clone(draft),reviews=new Map(designGroupReviews);
    const name=$("project-name").value,description=$("project-description").value;
    const out=await api(`/projects/${current.id}/engineering/iterations`,"POST",{revision:current.revision,source_job_id:resultRecord.id,component:componentId,patch});
    fillProject(out.project,true);
    await designReviewRestore;
    designGroupReviews=reviews;
    // A component submission must not consume unrelated unsaved edits.
    for(const [key,value] of Object.entries(previousDraft))if(!Object.hasOwn(patch,key)&&JSON.stringify(value)!==JSON.stringify(previousInputs[key]))draft[key]=value;
    $("project-name").value=name;$("project-description").value=description;
    renderFields();
    setDirty(JSON.stringify(platformInputValues(draft))!==JSON.stringify(current.inputs)||name!==current.name||description!==current.description);
    await refreshProjectSide();
  });
  await chooseView("integration");
  toast("已保存本部件修改与来源工况；请复算总体设计点并核对工程约束。");
}
function renderView(){
  syncSolverMonitor();
  const help=analysisView==="guide";
  $("help-content").hidden=!help;
  $("workbench-content").hidden=help;
  $("workbench-content").inert=help;
  document.querySelector(".workbench-layout").classList.toggle("help-view",help);
  // Documentation has its own host. Keep component forms and controllers alive
  // while reading so returning does not discard a preliminary study or filter.
  if(help){updateViewHeading();paintHelp();return;}
  const resume=helpSuspendedView?.view===analysisView&&helpSuspendedView.projectId===current?.id&&helpSuspendedView.record===resultRecord;
  helpSuspendedView=null;
  if(resume){updateViewHeading();syncEngineeringView();engineeringResize?.refresh();return;}
  clearParameterLocation();
  // Migrate old session locations: concept design now denotes inputs only.
  if(analysisView==="overview"&&conceptSection!=="settings")analysisView=conceptSection==="diagnostics"?"solver_diagnostics":"results_overview";
  if(valveSelectionCleanup){valveSelectionCleanup();valveSelectionCleanup=null;}
  const archived=archivedProject();
  const archivedRecord=Boolean(resultRecord&&recordScopeIssue(resultRecord));
  if((archived||archivedRecord)&&!archivedViews.has(analysisView))analysisView="history";
  if(archived&&analysisView==="overview"&&conceptSection==="settings")conceptSection="results";
  $("project-scope-note").hidden=!archived;
  $("project-scope-note").textContent=archived?designScopeIssue(current.inputs):"";
  if(libraryCleanup){libraryCleanup();libraryCleanup=null;}
  if(analysisView!=="property_library"){propertyView?.dispose();propertyView=null;}
  if(nozzleDesignCleanup){nozzleDesignCleanup();nozzleDesignCleanup=null;}
  if(chamberDesignCleanup){chamberDesignCleanup();chamberDesignCleanup=null;}
  if(turbineDesignCleanup){turbineDesignCleanup();turbineDesignCleanup=null;}
  if(injectorDesignCleanup){injectorDesignCleanup();injectorDesignCleanup=null;}
  if(turbineGeometryCleanup){turbineGeometryCleanup();turbineGeometryCleanup=null;}
  if(pumpGeometryCleanup){pumpGeometryCleanup();pumpGeometryCleanup=null;}
  if(nozzleGeometryCleanup){nozzleGeometryCleanup();nozzleGeometryCleanup=null;}
  coolingDesignView?.dispose();coolingDesignView=null;
  updateViewHeading();
  const history=analysisView==="history";
  const fixedSteady=analysisView==="fixed_steady";
  const designRun=analysisView==="design_run";
  const designMonitor=analysisView==="design_monitor";
  const reference=analysisView==="engineering_examples";
  const integration=analysisView==="integration";
  const hub=isWorkspaceHub(analysisView)||analysisView==="project_overview"||designRun||designMonitor||reference||integration||analysisView==="property_library",designParameters=hasDesignParameters(analysisView);
  const concept=analysisView==="overview",settings=concept&&conceptSection==="settings";
  const contentView=concept?(conceptSection==="diagnostics"?"solver_diagnostics":"results_overview"):analysisView;
  $("workspace").dataset.conceptSection=conceptSection;
  $("concept-navigation").hidden=true;
  $("design-run-panel").hidden=!designRun;
  $("design-monitor-panel").hidden=!designMonitor;
  $("concept-reference-panel").hidden=!reference;
  $("concept-reference-panel").inert=!reference||editorBusy||archived;
  $("concept-settings-host").hidden=!settings;
  $("concept-settings-host").inert=!settings;
  if(settings){ensureEngineeringView();syncEngineeringView();engineeringResize?.refresh();}
  $("concept-section-content").hidden=settings||history||fixedSteady||designRun||designMonitor||reference||integration;
  $("concept-section-content").setAttribute("role","region");
  $("concept-section-content").setAttribute("aria-labelledby","analysis-title");
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
   $("toggle-parameters").hidden=!designParameters || concept || archived || Boolean(currentComponent());
  $("toggle-parameters").textContent="打开对应设计输入";
  for(const id of ["result-toolbar","result-context"])$(id).hidden=settings || history || fixedSteady || hub;
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
  $("integration-content").hidden=!integration;
  if(!integration)integrationView?.onLeave();
  paintComponentWorkflow();
  $("valve-design-inputs").hidden=analysisView!=="valve_design";
  $("valve-design-inputs").inert=analysisView!=="valve_design"||editorBusy||archived;
  if(analysisView!=="validation") validationView?.onLeave();
  if(analysisView!=="throttle") throttleView?.onLeave();
  if(analysisView!=="operating_throttle") operatingThrottleView?.onLeave();
  if(!fixedSteady) fixedSteadyView?.onLeave();
  if(analysisView!=="transient") transientView?.onLeave();
  if(analysisView!=="feed") feedView?.onLeave();
  if(analysisView!=="chilldown") chilldownView?.onLeave();
  if(analysisView!=="ignition") ignitionView?.onLeave();
  if(analysisView!=="shutdown") shutdownView?.onLeave();
  if(integration){
    integrationView ||= mountEngineeringLifecycle($("integration-content"),{api,getContext:()=>({project:current,draft,dirty,record:resultRecord,jobs:projectJobs}),notify:toast,onSelectRecord:loadRecord,onOpenView:chooseView});
    Promise.resolve(integrationView.refresh()).catch(error=>toast(error.message));
  } else if(analysisView==="design_requirements"){
    $("analysis-content").innerHTML=renderDesignRequirements(resultRecord);
  } else if(analysisView==="cooling_workspace"){
    const conceptHeat=resultRecord?.result?.calculation?.components?.cooling_channel?.profile?.model_id==="regen_concept_heat_v1";
    $("analysis-content").innerHTML=conceptHeat
      ? renderCooling(resultRecord.result)+'<section class="panel"><h3>继续冷却部件设计</h3><p>本记录提供总体热负荷与热流分布。下一步定义通道、壁厚和材料，切换详细通道耦合并复算，再开展局部通道方案筛选。</p><button type="button" data-setup-group="chamber_channels">编辑通道草案</button> <button type="button" data-setup-group="wall_thermal">设置壁面材料</button> <button type="button" data-setup-group="cooling">选择详细耦合模型</button></section>'
      : '<section data-cooling-design-host></section>'+renderCooling(resultRecord?.result||{});
    const host=$("analysis-content").querySelector('[data-cooling-design-host]');
    if(host&&resultRecord?.status==="succeeded"){
      coolingDesignView=mountCoolingDesign(host,{api,getContext:()=>({project:current,draft,dirty,record:resultRecord}),notify:toast,onAdopt:adoptCoolingCandidate});
      Promise.resolve(coolingDesignView.refresh()).catch(error=>toast(error.message));
    }
  } else if(analysisView==="property_library"){
    if(!propertyView)propertyView=mountPropertyLibrary($("analysis-content"),{api,getContext:()=>({draft,record:resultRecord,project:current}),initialTab:propertyTab,initialCategory:propertyCategory});
    else propertyView.refresh?.();
  } else if(designRun||designMonitor||reference){
    $("analysis-content").innerHTML="";
  } else if(analysisView==="project_overview"){
    $("analysis-content").innerHTML=renderProjectOverview(currentWorkflow());
  } else if(hub){
    $("analysis-content").innerHTML=renderWorkspaceHub(analysisView,{project:current,record:resultRecord,dirty,libraryTab});
    libraryCleanup=mountWorkspaceLibrary($("analysis-content"),{onSelect:id=>{libraryTab=id;rememberNavigation();}});
  } else if(analysisView==="valve_design") {
    renderValveFields();
    $("analysis-content").innerHTML=`<section aria-label="保存记录的阀门结果"><h2>已保存计算记录 · 阀门结果</h2><p class="fine">以下内容仅来自上方选择的计算记录；当前草稿编辑不会改写历史结果。</p>${renderValveDesign(resultRecord?.valve_design)}<div data-valve-selection-host></div><details class="concept-reference"><summary>供给支路压降需求与历史证据</summary>${renderValveRequirements(resultRecord?.valve_requirements)}</details></section>`;
    const valveHost=$("analysis-content").querySelector("[data-valve-selection-host]");
    if(valveHost)valveSelectionCleanup=mountValveSelection(valveHost,{api,notify:toast,project:current,jobs:projectJobs});
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
      $("analysis-content").innerHTML=settings?"":renderAnalysis(contentView,resultRecord)+(contentView==="results_overview"&&resultRecord?.result?.calculation&&!recordScopeIssue(resultRecord)?`<details class="concept-reference valve-design-evidence"><summary>实体阀定义、开度与选型需求</summary>${renderValveDesign(resultRecord.valve_design)}</details><div data-valve-selection-host></div><details class="concept-reference valve-design-evidence"><summary>供给支路压降需求与历史证据</summary>${renderValveRequirements(resultRecord.valve_requirements)}</details>`:"");
      const valveHost=$("analysis-content").querySelector("[data-valve-selection-host]");
      if(valveHost)valveSelectionCleanup=mountValveSelection(valveHost,{api,notify:toast,project:current,jobs:projectJobs});
      if(!settings&&contentView==="results_overview") {
        mountSteadyWorkspace($("analysis-content"),resultRecord?.id || "");
        const coolingPanel=$("analysis-content").querySelector('[data-steady-panel="cooling"]');
        if(coolingPanel && resultRecord?.status==="succeeded"&&!archived&&!recordScopeIssue(resultRecord)) {
          const entry=document.createElement("div");
          entry.innerHTML='<button type="button" data-workspace-view="cooling_workspace">进入再生冷却设计与校核</button>';
          coolingPanel.prepend(entry);
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
  if(draft.propellant_pair!=="lox_methane" || Number(draft.cooling_bartz_mode)!==1 || String(draft.cooling_channel_table_json||"").trim())
    throw new Error("当前草稿需采用甲烷准一维模型且没有沿程通道表；候选几何不能覆盖不同的通道定义。");
  draft={...draft,...candidate.input_patch};
  clearPresetUndo();setEditorError("");syncPresetSelection();renderFields();setDirty();
  toast("已将候选几何写入草稿，其他参数保留。请运行设计点重新闭合整机，并使用新记录复核筛选约束。");
}
function syncPreburnerContext(){
  if(!draft)return;
  refreshPreburnerLimitSetup($("parameter-fields"),draft);
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
    node.innerHTML=renderCoolingChannelInputs(draft,{editor:coolingChannelEditor,
      componentDraft:groups.some(group=>group.id==="chamber_channels"&&group.workflow_stage==="components"),...coolingGeometryContext()});
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
  updateActionState();
  if(!current)return;const freshness=currentWorkflow().freshness,old=resultRecord&&freshness.state==="stale";
  const validity=recordModelValidity(resultRecord);
  $("result-context").classList.toggle("result-context-stale",!!old||validity.reuse_blocked);
  $("result-context").textContent=resultRecord?`当前显示：方案 v${resultRecord.revision} · ${date(resultRecord.created)} · ${statusNames[resultRecord.status]||resultRecord.status}${old?"。这是历史输入的结果；当前参数已变化，请重新运行设计点。":"。"}`:"尚无计算结果。请完成总体设计设置，在“输入检查与运行”中提交计算。";
  $("export-current-result").hidden=!resultRecord?.result;
  if(validity.reuse_blocked)$("result-context").textContent+=" 物理模型已撤回 · 结果无效。"+validity.reason;
  if(resultRecord&&freshness.state==="unknown")$("result-context").textContent+=" 输入快照不完整，无法确认是否对应当前参数。";
  paintProjectWorkflow();
}
async function refreshProjectSide(){
  if(!current)return;const id=current.id,sequence=++refreshSequence;
  const [jobs,revisions]=await Promise.all([api("/jobs?project_id="+id),api(`/projects/${id}/revisions`)]);
  if(current?.id!==id||sequence!==refreshSequence)return;
  if(jobs.some(job=>job.project_id && job.project_id!==id))throw new Error("计算列表包含其他方案的记录，请重新读取当前方案。");
  projectJobs=jobs;renderJobs($("project-jobs"),jobs);
  $("project-revisions").innerHTML=revisions.map(r=>`<div class="revision-row"><div><b>v${r.revision} · ${escape(r.name)}</b><p>${escape(r.author)} · ${date(r.created)}</p></div><button data-revision="${r.revision}"${archivedProject()?" disabled":""}>${archivedProject()?"历史版本只读":"载入为草稿"}</button></div>`).join("");
  if(!jobs.some(j=>j.id===selectedJobId))selectedJobId=jobs[0]?.id||null;
  $("result-selector").innerHTML=jobs.length?jobs.map(j=>`<option value="${j.id}" ${j.id===selectedJobId?"selected":""}>v${j.revision} · ${date(j.created)} · ${statusNames[j.status]}</option>`).join(""):'<option value="">尚无计算记录</option>';
  $("history-record-selector").innerHTML=$("result-selector").innerHTML;
  const summary=jobs.find(j=>j.id===selectedJobId);
  if(!summary){const hadRecord=Boolean(resultRecord);recordLoadSequence++;resultRecord=null;selectedJobId=null;if(hadRecord)renderView();else paintProjectWorkflow();}
  if(summary && (resultRecord?.id!==summary.id || resultRecord.status!==summary.status || active(summary)))await loadRecord(summary.id);
  if(current?.id!==id||sequence!==refreshSequence)return;
  updateActionState();
  rememberNavigation();
}
async function loadRecord(id){
  const projectId=current?.id,sequence=++recordLoadSequence;selectedJobId=id;
  engineeringRecordError="";
  if(!projectId||!id){selectedJobId=null;resultRecord=null;if(current)renderView();return false;}
  if(resultRecord?.id!==id){resultRecord=null;renderView();$("result-context").textContent="正在读取所选计算记录，各页面将在读取完成后统一更新。";}
  let record;
  try{record=await api("/jobs/"+id);}catch(error){
    if(current?.id!==projectId||selectedJobId!==id||sequence!==recordLoadSequence)return false;
    engineeringRecordError="所选计算记录读取失败，请重试或选择其他记录。";syncEngineeringView();
    $("result-context").textContent="所选计算记录读取失败，请重新选择记录或重试。";
    throw error;
  }
  if(current?.id!==projectId||selectedJobId!==id||sequence!==recordLoadSequence)return false;
  if(record?.id!==id||record?.project_id!==projectId){
    engineeringRecordError="所选计算记录与当前方案不一致，已停止载入。";
    resultRecord=null;selectedJobId=null;renderView();rememberNavigation();
    throw new Error("所选计算记录与当前方案不一致，已停止载入；请重新选择当前方案的记录。");
  }
  resultRecord=record;$("result-selector").value=id;$("history-record-selector").value=id;renderView();rememberNavigation();return true;
}
function renderJobs(container,jobs){container.innerHTML=jobs.length?jobs.map(j=>`<div class="job-row"><div class="job-main"><b>${escape(j.project_name)} · v${j.revision}</b><p>${escape(j.author)} · ${date(j.created)}</p>${active(j)&&j.progress?"<p>求解进行中 · 分层迭代过程可在“输入检查与运行”中查看。</p>":""}</div><div class="job-actions">${badge(j.status)}<button data-job="${j.id}">查看</button>${active(j)&&(user.role==="admin"||user.id===j.submitted_by)?`<button data-cancel="${j.id}">取消</button>`:""}</div></div>`).join(""):'<div class="small-empty">暂无计算记录。请前往总体方案与循环设计的“输入检查与运行”提交计算。</div>';}
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
  const valveIssues=valveConfigurationIssues(draft,editorField("valve_configuration_json")?.valve_catalog||{});
  if(valveIssues.length){openReadinessField("valve_configuration","valve_configuration_json");throw new Error(valveIssues[0].message);}
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
      const group=parameterGroups(groups,draft).find(g=>g.fields.some(item=>item.key===f.key&&parameterVisible(item,draft)));
      const visible=Boolean(group);
      if(group)openReadinessField(group.id,f.key);
      if(!visible&&value==null&&/^turb_(ox|fuel)_n_stages_user$/.test(f.key))
        throw new Error(`${f.label}仍处于手动待填写状态，在当前循环或轴系下已隐藏。请切回使用该涡轮的循环／轴系，填写整数级数或选择“按现有规则估算”，当前草稿已保留。`);
      if(!visible&&value==null&&/^turb_(ox|fuel)_(reaction_degree|meanline_flow_coefficient)$/.test(f.key))
        throw new Error(`${f.label}尚未填写，在当前模式下已隐藏。请在使用该涡轮的循环／轴系下重新启用均径校核并补填，当前草稿已保留。`);
      throw new Error(`${f.label}必须是${f.type==="int"?"整数":"有限数值"}。${visible?"请在参数设置中修正。":`历史参数 ${f.key} 无效；请在“方案与计算记录”载入有效版本，或导入修正后的参数备份。`}`);
    }
  }
}
async function saveProject(reviews=null){
  if(analysisView==="fixed_steady")throw new Error("请在固定硬件稳态页保存任务定义；当前方案需回到总体或部件参数页保存。");
  if(["operating_throttle","throttle"].includes(analysisView))throw new Error("节流分析使用保存的设计基准；修改当前方案请回到总体或部件参数页保存。");
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
  if(!current||editorBusy)return;
  requireEditableDesign();
  // A shortcut can open the review, but only an explicit action there submits a job.
  if(page!=="project"||analysisView!=="design_run"){await chooseView("design_run");return;}
  if(projectJobs.some(active))return;
  if(designReadinessEnabled()){
    syncDesignReadiness();
    if(!canRunDesignPoint()){
      throw new Error(readinessRunMessage());
    }
  }
  validateDraft();
  await withEditorLock(async()=>{
    if(dirty){
      await saveProject();
      if(designReadinessEnabled()&&!canRunDesignPoint())throw new Error(readinessRunMessage());
    }
    const projectId=current.id;
    const job=await api(`/projects/${projectId}/jobs`,"POST",{mode:"concept_design",revision:current.revision});
    if(current?.id!==projectId)return;
    if(!job?.id||(job.project_id && job.project_id!==projectId))throw new Error("提交返回的记录与当前方案不一致，请刷新当前方案。");
    recordLoadSequence++;refreshSequence++;resultRecord=null;
    selectedJobId=job.id;analysisView="design_monitor";
    renderView();switchPage("project");await refreshProjectSide();toast("设计点已提交，可在本页查看分层迭代与收敛曲线。");
  });
}
async function refreshMembers(){if(!canManageMembers())return;const members=await api("/members");if(!canManageMembers())return;$("members-list").innerHTML=members.map(m=>`<div class="member-row"><div><b>${escape(m.display_name)}</b><p>@${escape(m.username)}</p></div><span class="badge">${m.role==="admin"?"管理员":"成员"}</span></div>`).join("");}
async function showResult(id){
  if(page==="project"){if(!await loadRecord(id)||page!=="project")return;$("result-selector").value=id;analysisView="results_overview";renderView();switchPage("project");return;}
  modalRecord=await api("/jobs/"+id);$("result-title").textContent=`计算记录 · 方案 v${modalRecord.revision}`;
  const supported=!recordScopeIssue(modalRecord);
  $("result-content").innerHTML=renderAnalysis("overview",modalRecord)+(supported&&modalRecord?.result?.calculation?renderValveDesign(modalRecord.valve_design)+renderValveRequirements(modalRecord.valve_requirements):"");
  if(supported)mountSteadyWorkspace($("result-content"),modalRecord?.id || "");
  $("download-result").hidden=!modalRecord.result;$("result-dialog").showModal();
}
function updateNewPresetDescription(){const preset=info?.presets?.find(p=>p.key===$("new-preset").value);$("new-preset-description").textContent=preset?.display_description||preset?.label||"";}
function newProject(){if(editorBusy||!mayLeave())return;$("new-error").textContent="";updateNewPresetDescription();$("new-dialog").showModal();}
function openPropertyLibrary(category="gas"){
  propertyView?.dispose();propertyView=null;
  propertyTab=["record","configuration"].includes(category)?category:"data";
  propertyCategory=["gas","fluid","material"].includes(category)?category:"gas";
  chooseView("property_library");
}
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
$("logout").onclick=()=>action($("logout"),null,async()=>{if(editorBusy||!mayLeave())return;await api("/auth/logout","POST",{});recordLoadSequence++;refreshSequence++;user=null;current=null;draft=null;resultRecord=null;modalRecord=null;selectedJobId=null;projectJobs=[];setDirty(false);showAuth();});
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
window.addEventListener("keydown",event=>{if(event.key==="Escape"&&!$("parameter-finder").open&&$("toggle-navigation").dataset.open==="true"){setNavigationOpen(false);$("toggle-navigation").focus();}});
document.querySelector(".sidebar").addEventListener("click",event=>{
  if(!current||page!=="project")return;
  const expand=event.target.closest?.("[data-setup-expand]");
  if(expand){
    for(const branch of document.querySelectorAll("[data-nav-group]")){
      if(branch.dataset.navGroup==="concept")branch.open=expand.dataset.setupExpand==="true";
    }
    for(const branch of document.querySelectorAll("[data-setup-section]"))branch.open=expand.dataset.setupExpand==="true";
    return;
  }
  const reference=event.target.closest?.("[data-setup-reference]");
  if(reference){
    chooseView("engineering_examples");
    const panel=$("concept-preset-reference");panel.open=true;
    panel.querySelector("summary")?.focus();panel.scrollIntoView?.({block:"start"});return;
  }
});
document.body.addEventListener("click",event=>{
  const property=event.target.closest?.("[data-property-open]");
  if(property&&current&&page==="project"){event.preventDefault();if(!editorBusy)openPropertyLibrary(property.dataset.propertyOpen);return;}
  const upgrade=event.target.closest?.("[data-thermo-upgrade]");
  if(upgrade){event.preventDefault();upgradeThermochemistryData();return;}
  const node=event.target.closest?.("[data-setup-group],[data-task-group]");
  if(node&&current&&page==="project"){event.preventDefault();openReadinessField(node.dataset.setupGroup||node.dataset.taskGroup,node.dataset.setupField);}
});
for(const host of [$("component-workflow-context"),$("integration-content")])host.addEventListener("click",event=>{
  const feedback=event.target.closest?.("[data-component-feedback]");
  if(feedback){event.preventDefault();action(feedback,"editor-error",()=>submitComponentFeedback(feedback.dataset.componentFeedback));return;}
  const destination=event.target.closest?.("[data-workspace-view]");
  if(destination){event.preventDefault();action(destination,null,()=>chooseView(destination.dataset.workspaceView));}
});
$("parameter-help").onclick=()=>openHelp();
$("concept-reference-back").onclick=()=>action(null,null,()=>chooseView("overview"));
document.body.addEventListener("click",event=>{
  const chapter=event.target.closest?.("[data-help-chapter]");
  if(chapter){event.preventDefault();openHelp(chapter.dataset.helpChapter);return;}
  const back=event.target.closest?.("[data-help-return]");
  if(back){event.preventDefault();action(back,null,returnFromHelp);return;}
  if(event.target.closest?.("[data-help-clear]")){event.preventDefault();helpQuery="";paintHelp();$("help-search")?.focus();}
});
$("help-content").addEventListener("input",event=>{
  if(analysisView!=="guide"||event.target.id!=="help-search")return;
  helpQuery=event.target.value;
  if(!event.isComposing&&!helpSearchComposing)paintHelp();
});
$("help-content").addEventListener("compositionstart",event=>{
  if(event.target.id==="help-search")helpSearchComposing=true;
});
$("help-content").addEventListener("compositionend",event=>{
  if(event.target.id!=="help-search")return;
  helpSearchComposing=false;
  if(analysisView==="guide"){helpQuery=event.target.value;paintHelp();}
});
$("analysis-content").addEventListener("click",event=>{
  const setup=event.target.closest?.("[data-readiness-group]");
  if(setup){event.preventDefault();openReadinessField(setup.dataset.readinessGroup,setup.dataset.readinessField);return;}
  const retry=event.target.closest?.("[data-readiness-retry]");
  if(retry){syncDesignReadiness(true);return;}
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
$("open-project-summary").onclick=()=>action($("open-project-summary"),null,()=>chooseView("project_overview"));
$("project-context").addEventListener("click",event=>{
  const setup=event.target.closest?.("[data-readiness-group]");
  if(setup){event.preventDefault();openReadinessField(setup.dataset.readinessGroup,setup.dataset.readinessField);return;}
  const destination=event.target.closest?.("[data-workspace-view]");
  if(destination){event.preventDefault();action(destination,null,()=>chooseView(destination.dataset.workspaceView));}
});
document.querySelectorAll("[data-close]").forEach(b=>b.onclick=()=>$(b.dataset.close).close());
$("new-project").onclick=newProject;
$("new-form").onsubmit=event=>{event.preventDefault();return action(event.submitter,"new-error",async()=>{
  const data=Object.fromEntries(new FormData(event.target));
  const preset=info.presets.find(p=>p.key===data.preset);
  if(!preset)throw new Error("请选择液氧甲烷 FFSC 起始方案。");
  const inputs=designStartingInputs(clone(info.ui_schema.defaults),clone(preset.inputs),data.design_start);
  const scopeIssue=designScopeIssue(inputs);if(scopeIssue)throw new Error(scopeIssue);
  const initialReviews=presetGroupReviews(inputs);
  // A different cooling model changes the active assumptions and geometry use.
  // Review those groups instead of inheriting the example's confirmations.
  if(inputs.cooling_bartz_mode!==preset.inputs.cooling_bartz_mode){initialReviews.delete("cooling");initialReviews.delete("thermal_geometry");}
  const out=await api("/projects","POST",{name:data.name,description:data.description,inputs,
    ...(info.design_review_persistence===true?{design_reviews:await encodeDesignGroupReviews(Object.fromEntries(initialReviews))}:{})});
  $("new-dialog").close();event.target.reset();fillProject(out.project);
  await designReviewRestore;
  if(info.design_review_persistence!==true)designGroupReviews=initialReviews;
  paintDesignReadiness();switchPage("project");await refreshProjectSide();
  const initialGroups=parameterGroups(groups,draft);
  openReadinessField(initialGroups.some(group=>group.id==="design")?"design":initialGroups[0]?.id);
});};
$("project-name").oninput=$("project-description").oninput=()=>{if(editorBusy||archivedProject())return;clearPresetUndo();setEditorError("");setDirty();};
$("find-parameter").onclick=openParameterFinder;
$("close-parameter-finder").onclick=()=>closeParameterFinder();
$("parameter-finder").addEventListener("cancel",event=>{event.preventDefault();closeParameterFinder();});
$("parameter-finder").addEventListener("keydown",event=>{
  if(event.key==="Escape"){event.preventDefault();event.stopPropagation();closeParameterFinder();}
});
$("parameter-finder").addEventListener("close",()=>$("find-parameter").setAttribute("aria-expanded","false"));
$("parameter-search").oninput=updateParameterMatches;
$("parameter-search").addEventListener("keydown",event=>{
  if(event.key==="ArrowDown"){
    const first=$("parameter-matches").querySelector("[data-parameter-match-group]");
    if(first){event.preventDefault();first.focus();}
  }
});
$("parameter-matches").onclick=event=>{
  const match=event.target.closest?.("[data-parameter-match-group]");
  if(match)openParameterMatch(match.dataset.parameterMatchGroup,match.dataset.parameterMatchField);
};
$("parameter-group").onchange=()=>{filterParameters();rememberParameterGroup();};
$("valve-parameter-fields").oninput=event=>{
  if(event.target.dataset.valveAction)editValveConfiguration(event.target);
};
$("valve-parameter-fields").addEventListener("click",event=>{
  const button=event.target.closest("button[data-valve-action]");
  if(button)editValveConfiguration(button);
});
$("valve-save").onclick=()=>action(null,"editor-error",()=>{
  validateDraft();return withEditorLock(async()=>{await saveProject();await refreshProjectSide();toast("方案新版本已保存。");});
});
$("valve-review").onclick=()=>action(null,"editor-error",()=>{
  if(editorBusy)return;
  return confirmDesignGroup("valve_configuration");
});
$("valve-open-design-run").onclick=()=>action(null,"editor-error",()=>chooseView("design_run"));
$("valve-readiness").onclick=()=>openReadinessField("valve_configuration");
$("design-readiness").onclick=$("design-run-readiness").onclick=$("valve-group-readiness").onclick=event=>{
  if(editorBusy)return;
  const destination=event.target.closest?.('[data-workspace-view="design_run"]');
  if(destination){event.preventDefault();return action(destination,"editor-error",()=>chooseView("design_run"));}
  if(event.target.closest?.("[data-readiness-retry]")){syncDesignReadiness(true);return;}
  const link=event.target.closest?.("[data-readiness-group]");
  if(link)openReadinessField(link.dataset.readinessGroup,link.dataset.readinessField);
};
$("parameter-fields").oninput=event=>{
  if(event.target.dataset.valveAction){editValveConfiguration(event.target);return;}
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
  }else if(f.key==="gas_eos"){
    try{applyThermochemistryChoice(value);}
    catch(error){setEditorError(error.message);event.target.value=displayValue(f);return;}
    return;
  }else{
    const canonical=fields.find(field=>field.key===f.key);if(!canonical)return;
    const invalidInheritedInput=f.show_effective_value && event.target.validity?.badInput;
    draft[f.key]=(typeof canonical.default==="number"||["number","int"].includes(canonical.type))?(invalidInheritedInput?NaN:value===""?null:Number(value)/(f.factor||1)):value;
  }
  clearPresetUndo();setEditorError("");syncPresetSelection();setDirty();
  if(f.key==="ambient_pressure")refreshDesignEnvironment($("parameter-fields"),draft);
  refreshParameterInheritance($("parameter-fields"),groups.flatMap(group=>group.fields),draft);
  if(f.virtual||["cycle_type","propellant_pair"].includes(f.key)||(f.choices&&parameterLayoutDependency(f.key,groups)))renderFields();
  else updatePropellantContext();
};
$("parameter-fields").onchange=event=>{
  if(editorBusy||archivedProject())return;
  if(event.target.dataset.valveAction)return;
  if(event.target.dataset.coolingChannelAction){
    if(event.target.dataset.coolingChannelAction==="mode")editCoolingChannel(event.target);
    else if(event.target.dataset.coolingChannelKey==="anchor")renderCoolingChannelEditor();
    return;
  }
  const f=editorField(event.target.dataset.input);if(!f||f.virtual||f.choices)return;
  if(parameterLayoutDependency(f.key,groups)||groups.find(g=>g.id==="pressure_ffsc")?.fields.some(item=>item.key===f.key)||f.key.startsWith("cooling_"))renderFields();
};
$("parameter-fields").addEventListener("focusout",event=>{
  if(editorBusy||archivedProject())return;
  const f=editorField(event.target.dataset.input);if(!f||f.read_only)return;
  const inheritance=parameterInheritance(f,draft);
  // A clear means inherit, but keep the control empty while the user is typing.
  // Focus departure restores the effective display even when no change event
  // is emitted; it never writes that display value back to the input snapshot.
  if(inheritance?.inherited && inheritance.valid && event.target.value==="" && !event.target.validity?.badInput)
    event.target.value=displayValue(f);
});
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
$("project-preset").onchange=()=>{if(editorBusy||archivedProject()){syncPresetSelection();return;}updatePresetContext();};
$("new-from-example").onclick=()=>{
  const key=$("project-preset").value;
  if(!info.presets.some(p=>p.key===key&&p.routine_design!==false))return;
  $("new-preset").value=key;newProject();
};
$("new-preset").onchange=updateNewPresetDescription;
$("new-preset-reference").onclick=()=>{
  const preset=info.presets.find(p=>p.key===$("new-preset").value);
  const reference=(info.reference_engines||[]).find(r=>r.preset_key===preset?.key||r.key===preset?.key);
  if(reference)$("reference-engine").value=reference.key;
  renderReference();$("reference-dialog").showModal();
};
$("undo-preset").onclick=undoPreset;
$("open-references").onclick=showReferences;
$("reference-engine").onchange=renderReference;
$("apply-preset").onclick=()=>{const preset=info.presets.find(p=>p.key===$("project-preset").value);if(!preset){toast("该机型仅供资料查阅，当前模型尚不支持此推进剂组合。");return;}loadPreset(preset);};
function upgradeThermochemistryData(){
  if(editorBusy||!current||!draft||archivedProject())return;
  const updated=upgradeMethaneDatasetDraft(draft);if(updated===draft)return;
  draft=updated;clearPresetUndo();setEditorError("");syncPresetSelection();renderFields();setDirty();
  toast("已将当前草稿改为 GRI30 high-T。点击“保存方案”生成新版本；旧版本与已有结果保留，其他模型设置不变。");
};
function applyThermochemistryChoice(eos){
  const field=editorField("gas_eos");
  if(editorBusy||!current||!draft||archivedProject()||!field||field.read_only)return;
  const updated=selectThermochemistryModel(draft,eos);if(updated===draft)return;
  draft=updated;clearPresetUndo();setEditorError("");syncPresetSelection();renderFields();setDirty();
  $("parameter-fields").querySelector('[data-input="gas_eos"]')?.focus();
}
$("parameter-fields").addEventListener("click",event=>{
  if(editorBusy||archivedProject())return;
  const modelChoice=event.target.closest("[data-thermochemistry-apply]");
  if(modelChoice){
    try{applyThermochemistryChoice(modelChoice.dataset.thermochemistryApply);}
    catch(error){setEditorError(error.message);}
    return;
  }
  if(event.target.closest("[data-thermochemistry-baseline]")){
    if(!current||!draft)return;
    const updated=applyThermochemistryBaseline(draft);if(updated===draft)return;
    draft=updated;clearPresetUndo();setEditorError("");syncPresetSelection();renderFields();setDirty();
    toast("已采用当前设计模型。请确认并保存为新版本，再运行设计点；已有版本与计算记录保留。");
    return;
  }
  const environment=event.target.closest("button[data-design-environment]");
  if(environment){
    const field=editorField("ambient_pressure");
    if(!draft||!field||field.read_only)return;
    try{
      const next=selectDesignEnvironment(draft,environment.dataset.designEnvironment);
      if(next===draft)return;
      draft=next;
      clearPresetUndo();setEditorError("");syncPresetSelection();renderFields();setDirty();
      $("parameter-fields").querySelector(`button[data-design-environment="${environment.dataset.designEnvironment}"]`)?.focus();
    }catch(error){setEditorError(error.message);}
    return;
  }
  const valveAction=event.target.closest("button[data-valve-action]");
  if(valveAction){editValveConfiguration(valveAction);return;}
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
  const parameterLink=event.target.closest("[data-readiness-group]");
  if(parameterLink){openReadinessField(parameterLink.dataset.readinessGroup,parameterLink.dataset.readinessField);return;}
  const review=event.target.closest("[data-review-design-group]");
  if(review)return action(review,"editor-error",()=>confirmDesignGroup(review.dataset.reviewDesignGroup));
  if(!event.target.closest("[data-upgrade-cooling]")||editorBusy||!current||!draft)return;
  const updated=upgradeCoolingModelDraft(draft);if(updated===draft)return;
  draft=updated;clearPresetUndo();setEditorError("");syncPresetSelection();renderFields();setDirty();
  toast("已建立准一维冷却草稿。请核对型面、通道及材料；保存后生成新版本，旧记录保持不变。");
});
$("project-form").onsubmit=event=>{event.preventDefault();if(analysisView==="overview"&&conceptSection==="settings"&&configurationSelected())return;return action(event.submitter,"editor-error",()=>{validateDraft();return withEditorLock(async()=>{await saveProject();await refreshProjectSide();toast("方案新版本已保存。");});});};
$("reload-project").onclick=()=>action(null,"editor-error",async()=>{if(editorBusy||!mayLeave())return;await withEditorLock(async()=>{fillProject(await api("/projects/"+current.id));await refreshProjectSide();});});
$("export-inputs").onclick=()=>action(null,"editor-error",async()=>{if(editorBusy)return;if(!archivedProject())validateDraft();download(buildInputBackup(),"lre-inputs.json");});
$("open-design-run").onclick=()=>action(null,"editor-error",()=>chooseView("design_run"));
$("header-run").onclick=$("design-run-submit").onclick=()=>action(null,"editor-error",runDesign);
$("design-run-back").onclick=()=>openDesignParameters();
$("design-run-solver").onclick=()=>openReadinessField("solver");
$("design-run-panel").addEventListener("click",event=>{
  const destination=event.target.closest?.("[data-workspace-view]");
  if(destination)action(destination,null,()=>chooseView(destination.dataset.workspaceView));
});
$("result-selector").onchange=()=>action(null,null,()=>loadRecord($("result-selector").value));
$("history-record-selector").onchange=()=>action(null,null,()=>loadRecord($("history-record-selector").value));
$("toggle-parameters").onclick=openDesignParameters;
$("refresh-jobs").onclick=()=>action(null,null,async()=>renderJobs($("all-jobs"),await api("/jobs")));
$("member-form").onsubmit=event=>{event.preventDefault();if(!canManageMembers())return;action(event.submitter,"member-error",async()=>{await api("/members","POST",Object.fromEntries(new FormData(event.target)));event.target.reset();await refreshMembers();toast("成员账号已创建。");});};
$("download-result").onclick=()=>downloadResult(modalRecord);$("export-current-result").onclick=()=>downloadResult(resultRecord);
document.body.addEventListener("click",event=>{const b=event.target.closest("button");if(!b)return;if(b.hasAttribute("data-new"))newProject();if(b.dataset.project)action(b,null,()=>openProject(b.dataset.project));if(b.dataset.job)action(b,null,()=>showResult(b.dataset.job));if(b.dataset.cancel)action(b,null,async()=>{await api("/jobs/"+b.dataset.cancel+"/cancel","POST",{});await poll();toast("已请求取消任务。");});if(b.dataset.revision)action(b,"editor-error",async()=>{if(editorBusy||archivedProject()||!mayLeave())return;await withEditorLock(async()=>{const old=await api(`/projects/${current.id}/revisions/${b.dataset.revision}`);const scopeIssue=designScopeIssue(old.inputs);if(scopeIssue)throw new Error(scopeIssue);specifiedPumpSpeeds.clear();specifiedTurbineStages.clear();coolingChannelEditor=null;draft=mergeInputDefaults(clone(info.ui_schema.defaults),clone(old.inputs),true);designGroupReviews=new Map();clearPresetUndo();syncPresetSelection(true);$("project-name").value=old.name;$("project-description").value=old.description;renderFields();setDirty();toast(`已载入 v${old.revision} 为草稿，保存后将产生新版本。`);});});});
window.addEventListener("beforeunload",event=>{if(dirty){event.preventDefault();event.returnValue="";}});
async function poll(){if(polling||editorBusy||document.hidden)return;polling=true;try{await api("/health");if(!user)return;if(page==="projects")await refreshDashboard();if(page==="jobs")renderJobs($("all-jobs"),await api("/jobs"));if(page==="project"&&current)await refreshProjectSide();}catch(error){if(connected!==false)$("connection-status").textContent=error.message;}finally{polling=false;}}
async function initialize(){const state=await api("/auth/status");initialized=state.initialized;user=state.user;accessLabels(state);if(user)await enterWorkspace();else showAuth();}
$("retry-connection").onclick=()=>action($("retry-connection"),null,initialize);
mountNavigationResize($("workspace"),$("navigation-resizer"),{window});
function ensureEngineeringView(){
  if(!engineeringView)engineeringView=mountEngineeringView($("engineering-view"),{window,
    onGroup:group=>openReadinessField(group),
    onState:(id,group)=>{if(editorBusy)return;const host=$("analysis-content");
      host.dataset.saRecordId=String(resultRecord?.id||"");host.dataset.saSelected=id;host.dataset.saGroup=group;
      action(null,null,()=>chooseView("states"));},
    onComponent:id=>{if(editorBusy)return;const host=$("analysis-content");
      host.dataset.caRecordId=String(resultRecord?.id||"");host.dataset.caCategory="all";host.dataset.caSelected=id;
      action(null,null,()=>chooseView("components"));},
    onView:(view,section)=>{if(!editorBusy)action(null,null,async()=>{await chooseView(view);if(section)$(section)?.scrollIntoView({block:"start"});});},
    onRecord:id=>{if(!editorBusy)action(null,null,()=>loadRecord(id));},
    onError:message=>toast(message instanceof Error?message.message:String(message))});
}
engineeringResize=mountEngineeringResize($("concept-settings-host"),$("engineering-resizer"),{window});
setInterval(poll,5000);
try{await initialize();}catch(error){$("auth-title").textContent="连接未完成";$("auth-description").textContent=error.message;$("auth-retry").hidden=false;}
$("auth-retry").onclick=()=>action($("auth-retry"),"auth-error",async()=>{await initialize();$("auth-retry").hidden=true;});
