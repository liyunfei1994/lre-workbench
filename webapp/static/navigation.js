/* Workspace information architecture. Navigation never changes a model or runs a job. */
const escape = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));

export const WORKSPACE_GROUPS = [
  {id:"library", label:"模型与数据", views:["model_library"]},
  {id:"design", label:"设计与配置", views:["overview","topology","geometry","chamber_design","nozzle_design","pump_geometry","injector_design","turbine_geometry"]},
  {id:"simulation", label:"工况与运行", views:["simulation_home","operating_throttle","fixed_steady","transient","feed","chilldown","ignition","shutdown"]},
  {id:"results", label:"结果与诊断", views:["results_overview","solver_diagnostics","states","components","validation","log","history","throttle"]},
  {id:"guide", label:"文档与维护", views:["guide"]},
];
export const HUB_VIEWS = {
  model_library:["模型与数据", "查看模型资源、资料来源与接入状态"],
  simulation_home:["工况与运行", "选择分析任务，确认所用模型与当前能力"],
  guide:["文档与维护", "工作流程、参数身份、验证要求与方案维护"],
};
export const LIBRARY_TABS = [
  {id:"components",label:"工程部件"},
  {id:"characteristics",label:"特性与关联模型"},
  {id:"properties",label:"物性与材料"},
  {id:"numerics",label:"数值工具"},
];
export function isWorkspaceHub(view){return Object.hasOwn(HUB_VIEWS,view);}
export function workspaceGroup(view){return WORKSPACE_GROUPS.find(group=>group.views.includes(view)) || null;}
export function hasDesignParameters(view){return ["overview","geometry","chamber_design","nozzle_design","pump_geometry","injector_design","turbine_geometry"].includes(view);}
export function workspaceCapability(view){
  if(view==="fixed_steady")return {label:"任务定义 · 计算未开放",tone:"pending"};
  if(view==="operating_throttle")return {label:"固定几何 · 局部估算",tone:"research"};
  if(["transient","feed","chilldown","ignition","shutdown"].includes(view))return {label:"研究模型 · 范围受限",tone:"research"};
  if(view==="throttle")return {label:"旧版设计扫描 · 只读",tone:"legacy"};
  if(view==="topology")return {label:"只读原理图",tone:"research"};
  if(["geometry","chamber_design","nozzle_design","pump_geometry","injector_design","turbine_geometry"].includes(view))return {label:"概念与初步设计",tone:"research"};
  return null;
}
export function renderWorkspaceBreadcrumb(view,title){
  const group=workspaceGroup(view),capability=workspaceCapability(view);
  return `<span>系统工作台</span><i aria-hidden="true">/</i><span>${escape(group?.label||"工作区")}</span>${title!==group?.label?`<i aria-hidden="true">/</i><span>${escape(title)}</span>`:""}${capability?`<span class="nav-capability" data-tone="${capability.tone}">${escape(capability.label)}</span>`:""}`;
}

const PARAMETER_SECTIONS = [
  {label:"任务与边界",ids:["cycle","design","inlet"]},
  {label:"系统与部件模型",ids:["component_efficiencies","chamber_chemistry","preburner_design","preburners","pressure_ffsc","cooling"]},
  {label:"几何与材料",ids:["pumps","turbines","chamber_contour","chamber_channels","wall_thermal"]},
  {label:"求解与初值",ids:["solver","thermo"]},
];
export function renderParameterNavigation(groups){
  const used=new Set(),section=(label,items)=>items.length?`<optgroup label="${escape(label)}">${items.map(g=>`<option value="${escape(g.id)}">${escape(g.title)}</option>`).join("")}</optgroup>`:"";
  const html=PARAMETER_SECTIONS.map(s=>{const items=groups.filter(g=>s.ids.includes(g.id));items.forEach(g=>used.add(g.id));return section(s.label,items);}).join("");
  return html+section("其他设置",groups.filter(g=>!used.has(g.id)));
}

const status=(label,tone="research")=>`<span class="hub-status" data-tone="${tone}">${escape(label)}</span>`;
const link=(view,label)=>`<button type="button" class="hub-card-link" data-workspace-view="${view}">${escape(label)} <span aria-hidden="true">→</span></button>`;
const card=(title,label,tone,description,action)=>`<article class="hub-card"><div class="hub-card-top"><h3>${escape(title)}</h3>${status(label,tone)}</div><p>${escape(description)}</p>${action||""}</article>`;
const section=(title,description,body)=>`<section class="hub-section"><h3>${escape(title)}</h3>${description?`<p>${escape(description)}</p>`:""}${body}</section>`;
const flow=()=>`<div class="hub-flow" aria-label="推荐工作流程">${[
  ["model_library","01","查看模型与数据","确认资料与适用范围"],
  ["overview","02","建立设计方案","保存设计输入与结果"],
  ["simulation_home","03","选择运行任务","区分工况、控制与初值"],
  ["history","04","复核与追溯","比较记录，检查依据"],
].map(([view,index,title,detail])=>`<button type="button" data-workspace-view="${view}"><small>${index}</small><b>${title}</b><span>${detail}</span></button>`).join("")}</div>`;

function context({project,record,dirty}){
  return `<div class="hub-context"><div class="hub-context-item"><span>当前方案</span><b>${escape(project?.name||"尚未选择方案")}</b></div><div class="hub-context-item"><span>方案版本</span><b>${project?`v${escape(project.revision)}`:"—"}${dirty?" · 有未保存编辑":""}</b></div><div class="hub-context-item"><span>当前计算记录</span><b>${record?`v${escape(record.revision)} · ${escape(record.id)}`:"尚无计算记录"}</b></div></div>`;
}
function library(selectedTab){
  const active=LIBRARY_TABS.some(tab=>tab.id===selectedTab)?selectedTab:LIBRARY_TABS[0].id;
  const categories={
    components:["工程部件","查看现有部件分析与初步设计入口；设计结果仍以所选计算记录为来源。",[
      card("部件模型与结果","已有分析入口","research","查看当前设计记录中的部件、进出口状态与能量信息。通用可执行部件库仍在建设。",link("components","查看部件分析")),
      card("循环与连接关系","只读原理图","research","查看保存结果对应的循环原理图与流路位置；当前不支持拖放部件组装可执行模型。",link("topology","查看循环原理图")),
      card("推力室内型面","初步几何","research","检查保存设计点的燃烧室、收缩段与喷管内轮廓，核对尺寸来源并导出建模坐标。",link("geometry","查看推力室内型面")),
      card("主燃烧室设计与分析","总体分析","research","查看两股入口燃气、平衡热化学、容积与几何、主室冷却及材料适用性，导出后续建模条件。",link("chamber_design","查看主燃烧室设计与分析")),
      card("喷管设计与性能","总体分析","research","核对喷管性能定义、几何一致性、轴向流动及环境背压影响，查看热环境、损失模型和后续分析要求。",link("nozzle_design","查看喷管设计与性能")),
      card("泵与涡轮","初步设计","research","查看泵设计点与吸入条件，以及涡轮级方案初选和 CFturbo 建模交接。",link("pump_geometry","查看泵设计与建模交接")+link("turbine_geometry","查看涡轮设计与建模交接")),
      card("喷注器","初步设计","research","查看喷注器初步设计及关联部件信息，保留实际流通能力的验证要求。",link("injector_design","查看喷注器设计")),
    ]],
    characteristics:["特性与关联模型","区分部件数据、计算接口和工程示例，先确认来源、适用范围与缺项。",[
      card("部件特性数据","待补数据","pending","泵、涡轮、阀门等特性尚未接入固定硬件求解。可建立任务，查看资料缺项与登记接口。",link("fixed_steady","查看数据接入要求")),
      card("高保真与降阶模型","尚未接入","pending","计算流体力学、有限元分析及降阶模型尚无统一可执行接入流程；当前页面不提供模型导入或联合仿真。",link("guide","查看模型职责与验证要求")),
      card("参考机型与工程示例","示例资料","research","查看参考资料与示例参数的来源。工程示例不代表特定实机的已验证数据。",'<button type="button" class="hub-card-link" data-workspace-action="references">查看参考资料 <span aria-hidden="true">→</span></button>'),
    ]],
    properties:["物性与材料","检查采用的热力学与材料依据；可用范围随工质、模型和温度区间变化。",[
      card("热力学与物性","已有校核入口","research","检查物性来源、组分基准、适用范围及同条件对比；保留来源记录的口径。",link("validation","进入热力学校核")),
      card("壁面材料与热物性","设计参数","research","壁面材料与热物性设置位于概念设计参数中；当前没有独立的材料版本库与寿命数据库。",link("overview","进入概念设计参数")),
      card("模型说明与验证要求","使用说明","research","了解设计输入、固定硬件、运行工况与结果的区别，以及模型接入和验收要求。",link("guide","阅读模型说明")),
    ]],
    numerics:["数值工具","查看实际求解记录与框架自检；算法验证和发动机模型验证分别判断。",[
      card("稳态求解与收敛诊断","已有求解诊断","research","查看牛顿法、雅可比近似更新和有界信赖域方法的记录，以及逐项残差、模型调用和失败位置。",link("solver_diagnostics","查看所选记录的收敛诊断")),
      card("数值求解与诊断","框架自检","research","查看独立数值框架自检及迭代记录。代数自检用于验证软件流程，不产生发动机工作点。",link("fixed_steady","查看数值框架自检")),
      card("运行过程研究","专项求解","research","现有运行研究使用各自的模型和求解设置；进入任务后确认边界、状态与模型范围。",link("simulation_home","查看运行任务")),
    ]],
  };
  return `<section class="library-workspace" aria-label="模型与数据分类"><div class="library-tabs" role="tablist" aria-label="模型资源分类">${LIBRARY_TABS.map(tab=>`<button type="button" role="tab" id="library-tab-${tab.id}" data-library-tab="${tab.id}" aria-controls="library-panel-${tab.id}" aria-selected="${tab.id===active}" tabindex="${tab.id===active?0:-1}">${tab.label}</button>`).join("")}</div>${LIBRARY_TABS.map(tab=>{const [title,description,cards]=categories[tab.id];return `<div class="library-panel" id="library-panel-${tab.id}" data-library-panel="${tab.id}" role="tabpanel" aria-labelledby="library-tab-${tab.id}" tabindex="0"${tab.id===active?"":" hidden"}>${section(title,description,`<div class="hub-card-grid">${cards.join("")}</div>`)}</div>`;}).join("")}<p class="hub-note">这里按资源类型整理现有入口与建设状态。页面分类不会创建模型、导入特性或修改当前方案。</p></section>`;
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
function simulations(){
  return section("选择运行方式","每种运行方式单独说明可用范围；新建任务前先确认来源记录。",
    `<div class="hub-card-grid">${[
      card("发动机节流分析","固定几何 · 局部估算","research","使用已保存设计点标定的局部模型，按目标推力百分比求解稳态工况。预燃室温度作为输出，逐点检查模型适用范围并绘制性能曲线。",link("operating_throttle","进入发动机节流分析")),
      card("固定硬件稳态与配平","仅任务定义","pending","已有硬件快照、工况、待补数据和目标配平定义。实际稳态工作点与推力配平尚未开放。",link("fixed_steady","配置稳态任务")),
      card("瞬态响应研究","局部研究模型","research","现有热态工作点附近的局部扰动分析。尚未与新固定硬件稳态模型统一，不代表整机全过程能力。",link("transient","进入瞬态研究")),
      card("线性化分析","尚未接入","pending","规划在经确认的工作点附近提取局部模型；当前没有可运行的线性化任务。",'<button type="button" class="hub-card-link" disabled>暂不可运行</button>'),
    ].join("")}</div>`)+section("专项运行研究","以下工具分别使用各自的模型和边界，进入后查看具体适用范围。",
    `<div class="hub-card-grid">${[
      card("供给网络动态","子系统研究","research","检查供给子系统的动态响应及状态历程。",link("feed","进入供给网络研究")),
      card("预冷与充填","专项研究","research","查看预冷、气液库存及充填过程的独立研究任务。",link("chilldown","进入预冷与充填")),
      card("点火事件与建压","专项研究","research","查看点火与建压任务的假设、输入及状态历程。",link("ignition","进入点火与建压")),
      card("关机与再启动","专项研究","research","查看关机、残余状态与再启动研究的任务记录。",link("shutdown","进入关机与再启动")),
    ].join("")}</div>`)+`<div class="hub-note"><b>旧设计扫描已归档至结果与诊断</b><p>原设计匹配扫描保留为只读历史，不再接受新任务。其预燃室温度目标与设计匹配结果不会混入发动机节流分析。</p>${link("throttle","查看归档扫描记录")}</div>`;
}
function guide(){
  return section("先确定任务，再选择计算方式","概念设计形成方案；运行分析使用明确的硬件和模型；结果分析保留来源与适用范围。",flow())+
    section("平台结构与职责","界面参考 ROCETS 的职责划分，用工作流程组织入口。",
    `<div class="hub-table"><table><thead><tr><th>平台部分</th><th>在工作台中的位置</th><th>当前说明</th></tr></thead><tbody>
    <tr><td>库系统</td><td>模型与数据</td><td>汇集现有资料与分析入口；可执行部件库和特性接入仍在建设。</td></tr>
    <tr><td>配置与运行处理</td><td>设计与配置、工况与运行</td><td>区分设计方案和运行任务；当前循环原理图为只读展示。</td></tr>
    <tr><td>执行与输出处理</td><td>计算任务、结果与诊断</td><td>管理任务状态、迭代记录、结果展示和导出；具体能力以任务说明为准。</td></tr>
    <tr><td>全套文档</td><td>本页、各参数与结果中的依据说明</td><td>记录定义、假设、适用范围和验证依据。</td></tr>
    <tr><td>维护规程</td><td>方案与计算记录、日志与警告</td><td>保留版本和输入快照；变更后重新计算并复核相关结果。</td></tr>
    </tbody></table></div>`)+
    section("参数身份与结果判读","三个状态应分别判断：数值是否收敛、模型是否在适用范围内、结果是否有验证依据。",
    `<ol class="hub-guide-steps"><li><b>设计目标与运行结果分开。</b>预燃室出口温度可用于概念设计；普通固定硬件运行中应由模型计算，允许上限另行检查。</li><li><b>硬件与控制量分开。</b>阀门尺寸、特性和开度限位属于硬件；开度属于运行设置。</li><li><b>初值与约束分开。</b>初值帮助数值迭代，不应被当作必须命中的物理目标。</li><li><b>变量名称与定义完整。</b>氧燃质量比指氧化剂质量流量除以燃料质量流量，与氧化剂质量分数不同。</li><li><b>保持来源一致。</b>当前编辑、保存方案和历史计算记录可能属于不同版本；查看结果时先确认来源。</li></ol>`)+
    section("维护与追溯","利用已有版本与记录功能维护方案。",
    `<div class="hub-card-grid">${[
      card("方案版本与备份","现有功能","research","查看历史版本、导入导出参数备份，并保存新的方案修订。参数备份不包含历史计算结果。",link("history","打开方案与计算记录")),
      card("日志与警告","现有功能","research","检查当前记录中的警告、运行环境与计算状态，保留未解决的问题。",link("log","查看日志与警告")),
      card("模型验证","分项核查","research","查看现有校核入口。软件自检通过不等于具体发动机模型已经完成验证。",link("validation","查看校核与依据")),
    ].join("")}</div>`);
}
export function renderWorkspaceHub(view,values={}){
  if(!isWorkspaceHub(view))return "";
  const descriptions={model_library:["模型资源","部件、特性与物性资料","集中查看部件、物性、参考资料与模型接入情况。"],simulation_home:["仿真任务","分析方式与当前可用能力","稳态、瞬态和线性化分别组织；选择任务前确认模型范围。"],guide:["使用与维护","模型、任务与结果的使用说明","了解工作流程，维护方案版本，追溯计算依据。"]};
  const [kicker,title,description]=descriptions[view];
  return `<div class="workspace-hub"><div class="hub-intro"><span class="hub-kicker">${kicker}</span><h2>${title}</h2><p>${description}</p></div>${context(values)}${view==="model_library"?library(values.libraryTab):view==="simulation_home"?simulations():guide()}</div>`;
}
