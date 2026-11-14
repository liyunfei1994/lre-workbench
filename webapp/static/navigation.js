/* Workspace information architecture. Navigation never changes a model or runs a job. */
import {renderHelpManual} from "./help-manual.js";
const escape = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));

export const WORKSPACE_GROUPS = [
  {id:"concept", label:"总体方案与循环设计", views:["overview","design_run","design_monitor","results_overview","design_requirements","solver_diagnostics","states","components","topology"]},
  {id:"components", label:"部件设计与特性建模", views:["geometry","chamber_design","nozzle_design","cooling_workspace","pump_geometry","turbine_geometry","injector_design","valve_design"]},
  {id:"integration", label:"整机集成与基准确认", views:["integration"]},
  {id:"steady", label:"固定硬件稳态分析", views:["steady_operations","fixed_steady","operating_throttle"]},
  {id:"transient", label:"瞬态运行分析", views:["transient_operations","chilldown","ignition","transient","feed","shutdown"]},
  {id:"results", label:"校核与记录", views:["validation","log","history","throttle"], auxiliary:true},
  {id:"library", label:"模型与资料库", views:["model_library","property_library","engineering_examples"], auxiliary:true},
  {id:"guide", label:"帮助文档", views:["guide"], auxiliary:true},
];
export const HUB_VIEWS = {
  model_library:["模型与资料库", "按需查阅部件、物性与模型依据，确认来源及接入状态"],
  steady_operations:["稳态性能分析", "固定硬件 · 不同工况的平衡状态与节流性能曲线"],
  transient_operations:["瞬态过程分析", "初始状态与时间历程 · 起动、热态响应、关机与再启动"],
  guide:["帮助文档", "用户手册、模型说明与参数参考 · 随当前安装版本提供"],
};
export const LIBRARY_TABS = [
  {id:"components",label:"工程部件"},
  {id:"characteristics",label:"特性与关联模型"},
  {id:"properties",label:"物性与材料"},
  {id:"numerics",label:"数值工具"},
];
// Restore tabs saved before the steady/transient top-level split.
export function normalizeWorkspaceView(view){return view==="simulation_home"?"steady_operations":view==="concept_reference"?"engineering_examples":view;}
export function isWorkspaceHub(view){return Object.hasOwn(HUB_VIEWS,view);}
export function workspaceGroup(view){return WORKSPACE_GROUPS.find(group=>group.views.includes(view)) || null;}
export function hasDesignParameters(view){return ["overview","geometry","chamber_design","nozzle_design","pump_geometry","injector_design","turbine_geometry","valve_design","cooling_workspace"].includes(view);}
export function workspaceCapability(view){
  if(view==="fixed_steady")return {label:"任务定义 · 计算未开放",tone:"pending"};
  if(view==="operating_throttle")return {label:"固定几何 · 局部估算",tone:"research"};
  if(["transient","feed","chilldown","ignition","shutdown"].includes(view))return {label:"研究模型 · 范围受限",tone:"research"};
  if(view==="throttle")return {label:"旧版设计扫描 · 只读",tone:"legacy"};
  if(view==="topology")return {label:"保存记录 · 只读",tone:"research"};
  if(["geometry","chamber_design","nozzle_design","pump_geometry","injector_design","turbine_geometry","valve_design"].includes(view))return {label:"概念与初步设计",tone:"research"};
  return null;
}
export function renderWorkspaceBreadcrumb(view,title,groupOverride=null){
  if(view==="project_overview")return `<span>系统工作台</span><i aria-hidden="true">/</i><span>方案摘要</span>`;
  const group=groupOverride||workspaceGroup(view),capability=workspaceCapability(view);
  return `<span>系统工作台</span><i aria-hidden="true">/</i><span>${escape(group?.label||"工作区")}</span>${title!==group?.label?`<i aria-hidden="true">/</i><span>${escape(title)}</span>`:""}${capability?`<span class="nav-capability" data-tone="${capability.tone}">${escape(capability.label)}</span>`:""}`;
}

const PARAMETER_SECTIONS = [
  {label:"发动机定义与设计边界",ids:["design","inlet","cycle"]},
  {label:"物理模型与假设",ids:["component_efficiencies","chamber_chemistry","cooling"]},
  {label:"循环匹配条件",ids:["preburner_design","preburners","pressure_ffsc"]},
  {label:"总体喷管性能",ids:["nozzle_performance"]},
  {label:"部件设计输入",ids:["pumps","turbines","chamber_contour","chamber_cooling_layout","chamber_channels","wall_thermal","valve_configuration"]},
  {label:"设计点求解",ids:["solver","initialization","thermo"]},
  {label:"校核与记录",ids:["engineering_constraints"]},
];
export function renderParameterNavigation(groups){
  const used=new Set(),section=(label,items)=>items.length?`<optgroup label="${escape(label)}">${items.map(g=>`<option value="${escape(g.id)}">${escape(g.title)}</option>`).join("")}</optgroup>`:"";
  const html=PARAMETER_SECTIONS.map(s=>{const items=groups.filter(g=>s.ids.includes(g.id));items.forEach(g=>used.add(g.id));return section(s.label,items);}).join("");
  return html+section("其他设置",groups.filter(g=>!used.has(g.id)));
}

const status=(label,tone="research")=>`<span class="hub-status" data-tone="${tone}">${escape(label)}</span>`;
const link=(view,label)=>`<button type="button" class="hub-card-link" data-workspace-view="${view}">${escape(label)} <span aria-hidden="true">→</span></button>`;
const setupLink=(group,label)=>`<button type="button" class="hub-card-link" data-setup-group="${escape(group)}">${escape(label)} <span aria-hidden="true">→</span></button>`;
const card=(title,label,tone,description,action)=>`<article class="hub-card"><div class="hub-card-top"><h3>${escape(title)}</h3>${status(label,tone)}</div><p>${escape(description)}</p>${action||""}</article>`;
const section=(title,description,body)=>`<section class="hub-section"><h3>${escape(title)}</h3>${description?`<p>${escape(description)}</p>`:""}${body}</section>`;
function context({project,record,dirty}){
  return `<div class="hub-context"><div class="hub-context-item"><span>当前方案</span><b>${escape(project?.name||"尚未选择方案")}</b></div><div class="hub-context-item"><span>方案版本</span><b>${project?`v${escape(project.revision)}`:"—"}${dirty?" · 有未保存编辑":""}</b></div><div class="hub-context-item"><span>当前计算记录</span><b>${record?`v${escape(record.revision)} · ${escape(record.id)}`:"尚无计算记录"}</b></div></div>`;
}
function library(selectedTab){
  const active=LIBRARY_TABS.some(tab=>tab.id===selectedTab)?selectedTab:LIBRARY_TABS[0].id;
  const categories={
    components:["工程部件","查看现有部件分析与初步设计入口；设计结果仍以所选计算记录为来源。",[
      card("部件模型与结果","已有分析入口","research","查看保存设计点中的部件、进出口状态与能量信息。概念设计的效率与几何假设在设置树中编辑；通用可执行部件库仍在建设。",link("components","查看设计点部件性能")),
      card("发动机构型与循环原理","构型说明","research","查阅当前模型的推进剂组合、循环方式、流路连接及系统边界。当前不支持拖放部件组装可执行模型。",setupLink("design","发动机定义与设计工况")),
      card("推力室内型面","初步几何","research","检查保存设计点的燃烧室、收缩段与喷管内轮廓，核对尺寸来源并导出建模坐标。",link("geometry","查看推力室内型面")),
      card("主燃烧室设计与分析","总体分析","research","查看两股入口燃气、平衡热化学、容积与几何、主室冷却及材料适用性，导出后续建模条件。",link("chamber_design","查看主燃烧室设计与分析")),
      card("喷管设计与性能","总体分析","research","核对喷管性能定义、几何一致性、轴向流动及环境背压影响，查看热环境、损失模型和后续分析要求。",link("nozzle_design","查看喷管设计与性能")),
      card("泵与涡轮","初步设计","research","查看泵设计点与吸入条件，以及涡轮级方案初选和 CFturbo 建模交接。",link("pump_geometry","查看泵设计与建模交接")+link("turbine_geometry","查看涡轮设计与建模交接")),
      card("喷注器","初步设计","research","查看喷注器初步设计及关联部件信息，保留实际流通能力的验证要求。",link("injector_design","查看喷注器设计")),
      card("阀门","初步设计","research","定义三阀流路与设计需求，核对保存工况的有效面积、开度及多工况选型包。",link("valve_design","进入阀门初步设计")),
    ]],
    characteristics:["特性与关联模型","区分部件数据、计算接口和工程示例，先确认来源、适用范围与缺项。",[
      card("部件特性数据","待补数据","pending","泵、涡轮、阀门等特性尚未接入固定硬件求解。可建立任务，查看资料缺项与登记接口。",link("fixed_steady","查看数据接入要求")),
      card("高保真与降阶模型","尚未接入","pending","计算流体力学、有限元分析及降阶模型尚无统一可执行接入流程；当前页面不提供模型导入或联合仿真。",link("guide","查看模型职责与验证要求")),
      card("参考机型与工程示例","方案起点","research","查看参数来源，从示例新建方案。选择示例只浏览，明确执行替换后才更新已有草稿。",link("engineering_examples","查看工程示例与来源")),
    ]],
    properties:["物性与材料","检查采用的热力学与材料依据；可用范围随工质、模型和温度区间变化。",[
      card("热物性与材料数据","数据资料","research","查阅实际热化学文件、逐物种温区与系数、性质曲线、纯流体 EOS 文献及材料试样来源。当前配置与计算记录分别展示。",link("property_library","浏览物性与材料数据")),
      card("独立热力学校核","同条件比较","research","核对物种、参考态与边界条件后，开展热化学及 HEOS / REFPROP 对照。",link("validation","进入热力学校核")),
      card("壁面材料与热物性","设计参数","research","壁面材料与热物性设置位于“部件设计与特性建模 → 再生冷却系统”；当前没有独立的材料版本库与寿命数据库。",setupLink("wall_thermal","设置壁面材料与热物性")),
      card("模型说明与验证要求","使用说明","research","了解设计输入、固定硬件、运行工况与结果的区别，以及模型接入和验收要求。",link("guide","阅读模型说明")),
    ]],
    numerics:["数值工具","查看实际求解记录与框架自检；算法验证和发动机模型验证分别判断。",[
      card("稳态求解与收敛诊断","已有求解诊断","research","查看牛顿法、雅可比近似更新和有界信赖域方法的记录，以及逐项残差、模型调用和失败位置。",link("solver_diagnostics","查看所选记录的收敛诊断")),
      card("数值求解与诊断","框架自检","research","查看独立数值框架自检及迭代记录。代数自检用于验证软件流程，不产生发动机工作点。",link("fixed_steady","查看数值框架自检")),
      card("运行分析任务","分项研究","research","稳态性能与瞬态过程分别组织；进入任务后确认边界、状态与模型范围。",link("steady_operations","查看稳态任务")+link("transient_operations","查看瞬态任务")),
    ]],
  };
  return `<section class="library-workspace" aria-label="模型与资料分类"><div class="library-tabs" role="tablist" aria-label="模型资源分类">${LIBRARY_TABS.map(tab=>`<button type="button" role="tab" id="library-tab-${tab.id}" data-library-tab="${tab.id}" aria-controls="library-panel-${tab.id}" aria-selected="${tab.id===active}" tabindex="${tab.id===active?0:-1}">${tab.label}</button>`).join("")}</div>${LIBRARY_TABS.map(tab=>{const [title,description,cards]=categories[tab.id];return `<div class="library-panel" id="library-panel-${tab.id}" data-library-panel="${tab.id}" role="tabpanel" aria-labelledby="library-tab-${tab.id}" tabindex="0"${tab.id===active?"":" hidden"}>${section(title,description,`<div class="hub-card-grid">${cards.join("")}</div>`)}</div>`;}).join("")}<p class="hub-note">这里按资源类型整理现有入口与建设状态，供设置具体节点时按需查阅。页面分类不会创建模型、导入特性或修改当前方案。</p></section>`;
}

/** Local, accessible category switching. No model edits, network calls or job submissions. */
export function mountWorkspaceLibrary(host,{onSelect}={}){
  const tabs=[...host.querySelectorAll("[data-library-tab]")],panels=[...host.querySelectorAll("[data-library-panel]")];
  if(!tabs.length)return ()=>{};
  const activate=(id,focus=false)=>{
    const selected=tabs.find(tab=>tab.dataset.libraryTab===id);if(!selected)return;
    for(const tab of tabs){const current=tab===selected;tab.setAttribute("aria-selected",String(current));tab.tabIndex=current?0:-1;}
    for(const panel of panels)panel.hidden=panel.dataset.libraryPanel!==id;
    if(focus)selected.focus();
    if(onSelect)onSelect(id);
  };
  const click=event=>{const tab=event.target.closest("[data-library-tab]");if(tabs.includes(tab))activate(tab.dataset.libraryTab);};
  const keydown=event=>{
    const tab=event.target.closest("[data-library-tab]"),index=tabs.indexOf(tab);if(index<0)return;
    let next;
    if(event.key==="ArrowRight")next=(index+1)%tabs.length;
    else if(event.key==="ArrowLeft")next=(index+tabs.length-1)%tabs.length;
    else if(event.key==="Home")next=0;
    else if(event.key==="End")next=tabs.length-1;
    else return;
    event.preventDefault();activate(tabs[next].dataset.libraryTab,true);
  };
  host.addEventListener("click",click);host.addEventListener("keydown",keydown);
  return ()=>{host.removeEventListener("click",click);host.removeEventListener("keydown",keydown);};
}
function steadyOperations(){
  return section("工况与节流任务","稳态计算求解每个工况的平衡状态；节流扫描比较多个稳态点，横坐标采用目标或实际推力百分比。",
    `<div class="hub-card-grid operation-card-grid">${[
      card("固定硬件稳态与配平","仅任务定义","pending","已有硬件快照、工况、待补数据和目标配平定义。实际稳态工作点与推力配平尚未开放。",link("fixed_steady","配置稳态任务")),
      card("发动机节流分析","固定几何 · 局部估算","research","使用已保存设计点标定的局部模型，按目标推力百分比求解稳态工况。预燃室温度作为输出，逐点检查模型适用范围并绘制性能曲线。",link("operating_throttle","进入节流扫描")),
    ].join("")}</div>`)+section("结果检查重点","各工况沿用同一硬件基准，并分别记录计算是否收敛、是否越界和模型适用范围。",
    `<ul class="hub-guide-steps"><li><b>工况边界：</b>确认来源设计记录、入口状态、环境背压与控制设置。</li><li><b>性能变化：</b>对比推力、比冲、室压、氧燃比、流量、轴速及关键温度。</li><li><b>节流定义：</b>记录额定推力基准和环境条件，分别展示目标与实际推力百分比。</li></ul>`);
}
function transientOperations(){
  return section("起动准备与建压","按具体过程检查初始状态、供给边界和事件时序。各任务使用独立模型，状态交接以工具实际支持为准。",
    `<div class="hub-card-grid operation-card-grid">${[
      card("预冷与充填","专项研究","research","查看预冷、气液库存及充填过程的独立研究任务。",link("chilldown","进入预冷与充填")),
      card("点火事件与建压","专项研究","research","查看点火与建压任务的假设、输入及状态历程。",link("ignition","进入点火与建压")),
    ].join("")}</div>`)+section("热态响应与子系统动态","关注压力、温度、流量和轴速的时间响应；稳态点序列不能代替过程积分。",
    `<div class="hub-card-grid operation-card-grid">${[
      card("瞬态响应研究","局部研究模型","research","现有热态工作点附近的局部扰动分析。尚未与新固定硬件稳态模型统一，不代表整机全过程能力。",link("transient","进入瞬态研究")),
      card("供给网络动态","子系统研究","research","检查供给子系统的动态响应及状态历程。",link("feed","进入供给网络研究")),
    ].join("")}</div>`)+section("关机与后续研究","保留关机后的残余状态，分别查看再启动和局部模型分析的可用范围。",
    `<div class="hub-card-grid operation-card-grid">${[
      card("关机与再启动","专项研究","research","查看关机、残余状态与再启动研究的任务记录。",link("shutdown","进入关机与再启动")),
      card("线性化分析","尚未接入","pending","规划在经确认的工作点附近提取局部模型；当前没有可运行的线性化任务。",'<button type="button" class="hub-card-link" disabled>暂不可运行</button>'),
    ].join("")}</div>`);
}
export function renderWorkspaceHub(view,values={}){
  if(!isWorkspaceHub(view))return "";
  if(view==="guide")return renderHelpManual(values);
  const descriptions={model_library:["模型资源","部件、特性与物性资料","集中查看部件、物性、参考资料与模型接入情况。"],steady_operations:["STEADY PERFORMANCE","固定硬件的工况性能","先确认硬件与工况边界，再选择稳态任务或节流扫描。"],transient_operations:["TRANSIENT ANALYSIS","发动机运行的时间过程","按起动、热态运行和关机组织独立研究任务，检查状态历程与过程约束。"]};
  const [kicker,title,description]=descriptions[view];
  const content=view==="model_library"?library(values.libraryTab):view==="steady_operations"?steadyOperations():view==="transient_operations"?transientOperations():"";
  return `<div class="workspace-hub"><div class="hub-intro"><span class="hub-kicker">${kicker}</span><h2>${title}</h2><p>${description}</p></div>${context(values)}${content}</div>`;
}

// Browser-local layout preference; it never reads or writes a project.
export const NAVIGATION_DEFAULT_WIDTH = 340;
export const NAVIGATION_MIN_WIDTH = 280;
export const NAVIGATION_MAX_WIDTH = 560;
const STORAGE_KEY = "lre.workspace.navigation-width.v1";

export function mountNavigationResize(workspace, handle, {window: win = globalThis.window, storage} = {}) {
  if (!workspace || !handle || !win) return () => {};
  let savedStorage = storage;
  try { if (savedStorage === undefined) savedStorage = win.localStorage; } catch { savedStorage = null; }
  const bounded = value => Math.min(NAVIGATION_MAX_WIDTH, Math.max(NAVIGATION_MIN_WIDTH, Math.round(value)));
  let preferred = NAVIGATION_DEFAULT_WIDTH;
  try {
    const saved = savedStorage?.getItem(STORAGE_KEY);
    if (saved != null && /^\d+(?:\.\d+)?$/.test(String(saved)) && Number.isFinite(Number(saved))) preferred = bounded(Number(saved));
  } catch { /* Layout remains adjustable when storage is unavailable. */ }
  let width = preferred, drag = null;
  const listeners = [];
  const listen = (target, event, handler) => { target.addEventListener(event, handler); listeners.push(() => target.removeEventListener(event, handler)); };
  const viewport = () => Number(win.innerWidth) || 1280;
  const desktop = () => viewport() > 700;
  // At the narrowest desktop sizes the content may be below 480 px; mobile
  // uses its existing overlay drawer and therefore needs no content reserve.
  const maximum = () => desktop() ? Math.min(NAVIGATION_MAX_WIDTH, Math.max(NAVIGATION_MIN_WIDTH, viewport() - 480)) : NAVIGATION_MAX_WIDTH;
  const clamp = value => Math.min(maximum(), Math.max(NAVIGATION_MIN_WIDTH, Math.round(value)));
  function paint(value = preferred) {
    width = clamp(value);
    workspace.style.setProperty("--navigation-width", `${width}px`);
    handle.hidden = !desktop();
    handle.tabIndex = desktop() ? 0 : -1;
    handle.setAttribute("aria-valuemin", String(NAVIGATION_MIN_WIDTH));
    handle.setAttribute("aria-valuemax", String(maximum()));
    handle.setAttribute("aria-valuenow", String(width));
    handle.setAttribute("aria-valuetext", `${width} 像素`);
  }
  function save(value) {
    preferred = value;
    try { savedStorage?.setItem(STORAGE_KEY, String(preferred)); } catch { /* An unavailable preference store must not block resizing. */ }
  }
  function finish(commit) {
    if (!drag) return;
    const previous = drag;
    drag = null;
    workspace.classList.toggle("navigation-resizing", false);
    if (commit && width !== previous.width) save(width); else preferred = previous.preferred;
    paint();
    try { handle.releasePointerCapture(previous.id); } catch { /* Capture may already be lost on blur or cancellation. */ }
  }
  listen(handle, "pointerdown", event => {
    if (!desktop() || drag || event.button !== 0 || event.isPrimary === false || !Number.isFinite(event.clientX)) return;
    event.preventDefault();
    drag = {id:event.pointerId, x:event.clientX, width, preferred};
    try { handle.setPointerCapture(event.pointerId); } catch { drag = null; return; }
    handle.focus({preventScroll:true});
    workspace.classList.toggle("navigation-resizing", true);
  });
  listen(handle, "pointermove", event => {
    if (!drag || event.pointerId !== drag.id || !Number.isFinite(event.clientX)) return;
    paint(drag.width + event.clientX - drag.x);
  });
  listen(handle, "pointerup", event => {
    if (!drag || event.pointerId !== drag.id) return;
    if (Number.isFinite(event.clientX)) paint(drag.width + event.clientX - drag.x);
    finish(true);
  });
  for (const name of ["pointercancel", "lostpointercapture"]) listen(handle, name, event => {
    if (drag && event.pointerId === drag.id) finish(false);
  });
  listen(handle, "keydown", event => {
    if (!desktop()) return;
    if (event.key === "Escape" && drag) { event.preventDefault(); finish(false); return; }
    if (drag) return;
    const step = event.shiftKey ? 32 : 16;
    const values = {ArrowLeft:width-step, ArrowRight:width+step, Home:NAVIGATION_MIN_WIDTH, End:maximum(), Enter:NAVIGATION_DEFAULT_WIDTH};
    if (!Object.hasOwn(values, event.key)) return;
    event.preventDefault(); paint(values[event.key]); save(event.key === "Enter" ? NAVIGATION_DEFAULT_WIDTH : width);
  });
  listen(handle, "dblclick", event => {
    if (!desktop()) return;
    event.preventDefault(); finish(false); save(NAVIGATION_DEFAULT_WIDTH); paint();
  });
  listen(win, "resize", () => { finish(false); paint(); });
  listen(win, "blur", () => finish(false));
  paint();
  return () => { finish(false); for (const remove of listeners) remove(); };
}
