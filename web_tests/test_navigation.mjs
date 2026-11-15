/** Navigation contracts: route coverage, honest capabilities and inert hub content. */
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";
import {
  WORKSPACE_GROUPS, HUB_VIEWS, workspaceGroup, workspaceCapability,
  isWorkspaceHub, hasDesignParameters, renderWorkspaceBreadcrumb,
  renderWorkspaceHub, renderParameterNavigation, LIBRARY_TABS, mountWorkspaceLibrary,
  normalizeWorkspaceView,
} from "../webapp/static/navigation.js";

const entry = readFileSync(new URL("../webapp/static/index.html", import.meta.url), "utf8");
const legacyViews = [
  "overview", "topology", "states", "components", "geometry", "chamber_design", "nozzle_design", "pump_geometry",
  "injector_design", "turbine_geometry", "valve_design", "log", "history", "shutdown", "ignition",
  "chilldown", "feed", "validation", "transient", "throttle", "operating_throttle", "fixed_steady",
];
const hubViews = ["model_library", "steady_operations", "transient_operations", "guide"];
const knownViews = new Set([...legacyViews, ...hubViews,"engineering_examples","property_library","design_run","design_monitor","results_overview","solver_diagnostics","design_requirements","cooling_workspace","integration"]);
// These routes are reached through generated nodes, context links or saved-record details.
const generatedViews = new Set(["overview", "guide"]);

function attributes(source) {
  return Object.fromEntries([...source.matchAll(/([\w-]+)(?:="([^"]*)")?/g)]
    .map(([, name, value]) => [name, value ?? true]));
}
function elements(markup, tag) {
  const stack=[],found=[];
  for(const match of markup.matchAll(new RegExp(`<(/?)${tag}\\b([^>]*)>`,"g"))){
    if(!match[1])stack.push({attrs:attributes(match[2]),start:match.index,bodyStart:match.index+match[0].length});
    else {
      const opened=stack.pop();
      if(opened)found.push({...opened,body:markup.slice(opened.bodyStart,match.index)});
    }
  }
  return found.sort((a,b)=>a.start-b.start);
}
function article(markup, title) {
  const found = elements(markup, "article").find(node => node.body.includes(`<h3>${title}</h3>`));
  assert.ok(found, `Missing capability card: ${title}`);
  return found;
}
function decode(value) {
  return value.replace(/&(amp|lt|gt|quot|#39);/g,
    (_, entity) => ({amp: "&", lt: "<", gt: ">", quot: '"', "#39": "'"}[entity]));
}

test("every existing page and each new hub has one reachable navigation location", () => {
  assert.equal(WORKSPACE_GROUPS.length, 8);
  assert.equal(new Set(WORKSPACE_GROUPS.map(group => group.id)).size, 8);
  const assigned = WORKSPACE_GROUPS.flatMap(group => group.views);
  assert.equal(assigned.length, new Set(assigned).size, "a page must not belong to two groups");
  assert.deepEqual(new Set(assigned), knownViews);
  assert.deepEqual(new Set(Object.keys(HUB_VIEWS)), new Set(hubViews));

  const sidebar=elements(entry,"aside").find(node=>node.attrs.id==="workspace-sidebar");
  const buttons = elements(sidebar.body, "button").filter(node => "data-view" in node.attrs);
  const staticViews=new Set([...knownViews].filter(view=>!generatedViews.has(view)));
  assert.equal(buttons.length, staticViews.size);
  assert.deepEqual(new Set(buttons.map(node => node.attrs["data-view"])), staticViews);
  assert.ok(buttons.every(node => !("disabled" in node.attrs)), "existing pages stay reachable");

  const groups = elements(entry, "details").filter(node => "data-nav-group" in node.attrs);
  assert.equal(groups.length, WORKSPACE_GROUPS.length);
  for (const group of WORKSPACE_GROUPS) {
    const node = groups.find(candidate => candidate.attrs["data-nav-group"] === group.id);
    assert.ok(node, `Missing group ${group.id}`);
    assert.deepEqual(elements(node.body, "button").filter(b => "data-view" in b.attrs)
      .map(b => b.attrs["data-view"]), group.views.filter(view=>!generatedViews.has(view)));
    for (const view of group.views) assert.equal(workspaceGroup(view)?.id, group.id);
  }
  assert.equal(workspaceGroup("missing-route"), null);
});

test("design scans and operating studies remain distinct, with explicit capability limits", () => {
  assert.equal(workspaceGroup("throttle").id, "results");
  assert.ok(!WORKSPACE_GROUPS.find(group=>group.id==="concept").views.includes("throttle"));
  assert.match(renderWorkspaceBreadcrumb("throttle","设计匹配扫描历史"), /校核与记录/);
  assert.doesNotMatch(entry, /历史设计工具/);
  assert.equal(workspaceGroup("fixed_steady").id, "steady");
  assert.equal(workspaceGroup("operating_throttle").id, "steady");
  assert.match(workspaceCapability("operating_throttle").label, /固定几何.*局部估算/);
  assert.equal(workspaceCapability("throttle").tone, "legacy");
  assert.match(workspaceCapability("throttle").label, /设计扫描/);
  assert.equal(workspaceCapability("fixed_steady").tone, "pending");
  assert.match(workspaceCapability("fixed_steady").label, /任务定义/);
  assert.match(workspaceCapability("fixed_steady").label, /未开放/);
  for (const view of ["transient", "feed", "chilldown", "ignition", "shutdown"]) {
    assert.equal(workspaceGroup(view).id, "transient");
    assert.equal(workspaceCapability(view).tone, "research");
    assert.match(workspaceCapability(view).label, /研究.*受限/);
  }
  assert.match(workspaceCapability("topology").label, /只读/);
});

test("task tree begins with the current case and keeps resources auxiliary",()=>{
  const expected=[["steady",["steady_operations","fixed_steady","operating_throttle"]],
    ["transient",["transient_operations","chilldown","ignition","transient","feed","shutdown"]]];
  assert.deepEqual(WORKSPACE_GROUPS.map(group=>group.label),["总体方案与循环设计","部件设计与特性建模","整机集成与基准确认","固定硬件稳态分析","瞬态运行分析","校核与记录","模型与资料库","帮助文档"]);
  const groups=elements(entry,"details").filter(node=>node.attrs["data-nav-group"]);
  assert.deepEqual(groups.slice(0,5).map(node=>elements(node.body,"span").find(span=>span.attrs.class==="nav-group-number").body),["01","02","03","04","05"]);
  for(const group of groups.slice(5))assert.doesNotMatch(group.body,/nav-group-number/);
  assert.deepEqual(WORKSPACE_GROUPS.filter(group=>group.auxiliary).map(group=>group.id),["results","library","guide"]);
  assert.equal(workspaceGroup("project_overview"),null);
  assert.equal(isWorkspaceHub("project_overview"),false,"overview is case state, not an inert capability hub");
  assert.equal(workspaceGroup("topology").id,"concept");
  assert.equal(workspaceGroup("overview").id,"concept");
  assert.equal(workspaceGroup("design_run").id,"concept");
  assert.equal(workspaceGroup("design_monitor").id,"concept");
  assert.equal(workspaceGroup("pump_geometry").id,"components");
  const branches=[...entry.matchAll(/data-setup-branch="([^"]+)"/g)].map(match=>match[1]);
  assert.deepEqual(branches,["configuration","requirements","models","design_point","solution","chamber","cooling_component","pumps","turbines","valves","constraints"]);
  const checks=groups.find(group=>group.attrs["data-nav-group"]==="results");
  assert.match(checks.body,/data-setup-branch="constraints"/);
  assert.doesNotMatch(entry,/data-nav-group="(?:operations|simulation)"|data-view="simulation_home"/);
  for(const [groupId,routes] of expected){
    const group=WORKSPACE_GROUPS.find(group=>group.id===groupId);
    assert.deepEqual(group.views,routes);
    const hub=renderWorkspaceHub(routes[0]);
    const destinations=elements(hub,"button").filter(node=>node.attrs["data-workspace-view"])
      .map(node=>node.attrs["data-workspace-view"]);
    assert.deepEqual(destinations,routes.slice(1),"each workspace exposes only its own tasks");
    for(const view of routes){
      assert.equal(workspaceGroup(view),group);
      const breadcrumb=renderWorkspaceBreadcrumb(view,"当前任务");
      assert.ok(breadcrumb.includes(`<span>${group.label}</span>`));
      assert.match(breadcrumb,/分析/);
    }
  }
  assert.equal(normalizeWorkspaceView("simulation_home"),"steady_operations");
  assert.equal(normalizeWorkspaceView("concept_reference"),"engineering_examples");
  for(const view of knownViews)assert.equal(normalizeWorkspaceView(view),view);
  assert.equal(normalizeWorkspaceView("unknown"),"unknown");
  assert.equal(isWorkspaceHub("simulation_home"),false);
});

test("guide is a chaptered read-only manual with contextual return",()=>{
  const guide=renderWorkspaceHub("guide");
  assert.match(guide,/data-help-return/);
  const targets=elements(guide,"button").map(node=>node.attrs["data-help-chapter"]).filter(Boolean);
  for(const chapter of ["workflow","theory","parameters","modes","examples","troubleshooting"])assert.ok(targets.includes(chapter));
  assert.doesNotMatch(guide,/data-workspace-view=|type="submit"/);
  assert.match(guide,/确认与保存不启动计算/);
  assert.match(guide,/页面导航不自动提交任务/);
  assert.match(guide,/输入完整性、数值收敛、工程约束和模型验证证据/);
  assert.match(guide,/概略冷却模型根据工况、参考壁温与受热面估算热负荷，无需先完成通道设计/);
  assert.match(renderWorkspaceHub("guide",{chapter:"troubleshooting"}),/旧计算记录继续保留其输入快照与版本/);
  assert.match(entry,/资料与帮助/);
  assert.doesNotMatch(entry,/辅助入口/);
});

test("setup branches are discoverable by default and editing precedes supporting explanations",()=>{
  const concept=elements(entry,"details").find(node=>node.attrs["data-nav-group"]==="concept");
  assert.ok(concept && "open" in concept.attrs);
  const groups=elements(concept.body,"details").filter(node=>node.attrs["data-setup-section"]);
  for(const id of ["models","design_point"]){
    const group=groups.find(node=>node.attrs["data-setup-section"]===id);
    assert.ok(group && "open" in group.attrs,`${id} settings must be visible on first entry`);
    assert.match(group.body,/data-setup-summary=/,"collapsed branches retain an input progress summary");
  }
  const controls=elements(entry,"button").filter(node=>"data-setup-expand" in node.attrs);
  assert.deepEqual(controls.map(node=>node.attrs["data-setup-expand"]).sort(),["false","true"]);
  assert.ok(controls.every(node=>node.attrs.type==="button" && !("disabled" in node.attrs)));
  const selector=elements(entry,"select").find(node=>node.attrs.id==="parameter-group");
  assert.ok(selector && "hidden" in selector.attrs,"the internal group selection is no longer the visible navigation");
  const fieldsAt=entry.indexOf('id="parameter-fields"'),checksAt=entry.indexOf('id="design-readiness"');
  assert.ok(fieldsAt>=0 && fieldsAt<checksAt);
  const form=elements(entry,"form").find(node=>node.attrs.id==="project-form");
  assert.doesNotMatch(form.body,/parameter-references|concept-design-scope|concept-parameter-roles|concept-preset-reference/);
  const reference=elements(entry,"section").find(node=>node.attrs.id==="concept-reference-panel");
  assert.ok(reference && "hidden" in reference.attrs);
  assert.match(reference.body,/id="concept-preset-reference"/);
  assert.match(entry,/id="parameter-help"/);
});

test("each engineering stage owns both its inputs and task results",()=>{
  const stages=elements(entry,"details").filter(node=>node.attrs["data-nav-group"]);
  const concept=stages.find(node=>node.attrs["data-nav-group"]==="concept");
  const components=stages.find(node=>node.attrs["data-nav-group"]==="components");
  assert.match(concept.body,/data-view="design_run"/);
  assert.match(concept.body,/data-view="results_overview"/);
  assert.match(concept.body,/data-view="design_requirements"/);
  assert.doesNotMatch(concept.body,/data-setup-branch="(?:pumps|turbines|chamber|cooling_component)"/);
  for(const branch of ["pumps","turbines","chamber","cooling_component","valves"])
    assert.ok(components.body.includes(`data-setup-branch="${branch}"`));
  for(const view of ["pump_geometry","turbine_geometry","cooling_workspace"])assert.equal(workspaceGroup(view).id,"components");
  assert.equal(workspaceGroup("integration").id,"integration");
  assert.equal(hasDesignParameters("design_run"),false);
  assert.equal(hasDesignParameters("integration"),false);
  const runPanel=elements(entry,"section").find(node=>node.attrs.id==="design-run-panel");
  assert.match(runPanel.body,/id="design-run-readiness"/);
  assert.equal(elements(runPanel.body,"button").find(node=>node.attrs.id==="design-run-submit")?.attrs.type,"button");
});

test("parameter editing offers save and review navigation instead of direct design-point submission",()=>{
  const form=elements(entry,"form").find(node=>node.attrs.id==="project-form");
  assert.ok(form);
  assert.doesNotMatch(form.body,/id="submit-job"|运行设计点/);
  const openReview=elements(form.body,"button").find(node=>node.attrs.id==="open-design-run");
  assert.equal(openReview?.attrs.type,"button","review navigation must not implicitly submit the save form");
  assert.match(openReview.body,/输入检查/);
  assert.ok(elements(form.body,"button").some(node=>node.attrs.type==="submit" && /保存方案/.test(node.body)));
});

test("configuration precedes requirements while the saved-record cycle diagram keeps its detail route",()=>{
  const groups=elements(entry,"details").filter(node=>node.attrs["data-nav-group"]);
  const concept=groups.find(node=>node.attrs["data-nav-group"]==="concept");
  assert.match(concept.body,/<div class="nav-children">\s*<div data-setup-branch="configuration"><\/div>\s*<div data-setup-branch="requirements"/);
  assert.equal((entry.match(/data-view="topology"/g)||[]).length,1,"saved system diagram has one result entry");
  assert.equal((entry.match(/data-setup-branch="configuration"/g)||[]).length,1);
  assert.equal(workspaceGroup("topology").id,"concept");
  assert.equal(hasDesignParameters("topology"),false);
  assert.match(renderWorkspaceBreadcrumb("topology","保存记录循环原理图"),/总体方案与循环设计.*保存记录循环原理图.*保存记录 · 只读/);
  assert.equal(workspaceGroup("states").id,"concept");
  assert.equal(workspaceGroup("components").id,"concept");
});

test("hubs expose only valid navigation or the reference action, never a submission", () => {
  const values = Object.freeze({
    project: Object.freeze({name: "审查方案", revision: 3}),
    record: Object.freeze({id: "saved-record", revision: 2}), dirty: true,
  });
  const before = JSON.stringify(values);
  for (const view of hubViews) {
    assert.equal(isWorkspaceHub(view), true);
    const markup = renderWorkspaceHub(view, values);
    assert.ok(markup);
    assert.doesNotMatch(markup, /<(?:form|script|iframe)\b/i);
    const inputs=[...markup.matchAll(/<input\b([^>]*)>/g)].map(match=>attributes(match[1]));
    assert.deepEqual(inputs.map(input=>[input.id,input.type]),view==="guide"?[["help-search","search"]]:[]);
    const buttons = elements(markup, "button");
    assert.ok(buttons.length > 0);
    for (const {attrs} of buttons) {
      assert.equal(attrs.type, "button", "hub actions must not submit a form");
      assert.ok(Object.keys(attrs).every(name => !/^on/i.test(name)), "no inline execution handlers");
      const viewTarget = attrs["data-workspace-view"];
      const action = attrs["data-workspace-action"];
      const libraryTab=attrs["data-library-tab"];
      const setupGroup=attrs["data-setup-group"];
      if (viewTarget) assert.ok(knownViews.has(viewTarget), `Unknown target ${viewTarget}`);
      if (action) assert.equal(action, "references");
      if (libraryTab)assert.ok(LIBRARY_TABS.some(tab=>tab.id===libraryTab));
      if (setupGroup)assert.ok(["design","wall_thermal"].includes(setupGroup));
      const helpAction=view==="guide"&&["data-help-chapter","data-help-return","data-help-clear"].some(name=>name in attrs);
      assert.ok([viewTarget,action,libraryTab,setupGroup,helpAction].filter(Boolean).length === 1 || "disabled" in attrs,
        "an enabled hub action has exactly one permitted navigation target");
    }
  }
  assert.equal(JSON.stringify(values), before, "rendering cannot change the selected case or record");
  assert.equal(isWorkspaceHub("fixed_steady"), false);
  assert.equal(isWorkspaceHub("toString"), false);
  assert.equal(renderWorkspaceHub("unknown"), "");
});

test("resource library has four local accessible categories with honest data and model boundaries",()=>{
  assert.deepEqual(LIBRARY_TABS.map(tab=>tab.label),["工程部件","特性与关联模型","物性与材料","数值工具"]);
  const html=renderWorkspaceHub("model_library",{libraryTab:"properties"});
  const tabs=elements(html,"button").filter(node=>node.attrs["data-library-tab"]);
  assert.equal(tabs.length,4);
  for(const tab of tabs){
    const id=tab.attrs["data-library-tab"],selected=id==="properties";
    assert.equal(tab.attrs.role,"tab");assert.equal(tab.attrs["aria-selected"],String(selected));
    assert.equal(tab.attrs.tabindex,selected?"0":"-1");
    assert.equal(tab.attrs["aria-controls"],`library-panel-${id}`);
    assert.match(html,new RegExp(`id="library-panel-${id}"[^>]*role="tabpanel"[^>]*aria-labelledby="library-tab-${id}"`));
  }
  assert.match(html,/不支持拖放部件组装可执行模型/);
  assert.match(html,/不提供模型导入或联合仿真/);
  assert.match(html,/没有独立的材料版本库与寿命数据库/);
  assert.match(html,/data-workspace-view="solver_diagnostics"/);
  const configuration=article(html,"发动机构型与循环原理");
  assert.match(configuration.body,/data-setup-group="design"/);
  assert.doesNotMatch(configuration.body,/data-workspace-view="topology"/);
  assert.match(configuration.body,/推进剂组合、循环方式、流路连接及系统边界/);
  assert.doesNotMatch(html,/data-workspace-view="(?:linearization|import_model|import_map)"/);
  const fallback=renderWorkspaceHub("model_library",{libraryTab:'<script>alert(1)</script>'});
  assert.match(fallback,/data-library-tab="components"[^>]*aria-selected="true"/);
  assert.doesNotMatch(fallback,/<script/);
});

test("library category controls switch only their own panels with keyboard focus and cleanup",()=>{
  const tabs=LIBRARY_TABS.map(({id},i)=>({dataset:{libraryTab:id},attributes:{"aria-selected":String(i===0)},tabIndex:i===0?0:-1,
    setAttribute(key,value){this.attributes[key]=value;},focus(){this.focused=true;}}));
  const panels=LIBRARY_TABS.map(({id},i)=>({dataset:{libraryPanel:id},hidden:i!==0}));
  const listeners=new Map(),chosen=[];
  const host={querySelectorAll:selector=>selector==="[data-library-tab]"?tabs:panels,
    addEventListener:(name,handler)=>listeners.set(name,handler),removeEventListener:(name,handler)=>{assert.equal(listeners.get(name),handler);listeners.delete(name);}};
  const dispose=mountWorkspaceLibrary(host,{onSelect:id=>chosen.push(id)});
  const target=tab=>({closest:()=>tab});
  listeners.get("click")({target:target(tabs[2])});
  assert.deepEqual(panels.map(panel=>panel.hidden),[true,true,false,true]);
  assert.deepEqual(tabs.map(tab=>tab.tabIndex),[-1,-1,0,-1]);
  let prevented=false;
  listeners.get("keydown")({target:target(tabs[2]),key:"End",preventDefault(){prevented=true;}});
  assert.equal(prevented,true);assert.equal(tabs[3].focused,true);assert.equal(chosen.at(-1),"numerics");
  listeners.get("keydown")({target:target(tabs[3]),key:"ArrowRight",preventDefault(){}});
  assert.equal(chosen.at(-1),"components");
  listeners.get("keydown")({target:target(tabs[0]),key:"ArrowLeft",preventDefault(){}});
  assert.equal(chosen.at(-1),"numerics");
  const count=chosen.length;
  listeners.get("click")({target:target({dataset:{libraryTab:"components"}})});
  listeners.get("keydown")({target:target(tabs[0]),key:"Escape",preventDefault(){throw new Error("unhandled key");}});
  assert.equal(chosen.length,count,"unrelated controls do not switch library pages");
  dispose();assert.equal(listeners.size,0);
  assert.doesNotThrow(()=>mountWorkspaceLibrary({querySelectorAll:()=>[]})());
});

test("linearization is described as unavailable and has no runnable navigation target", () => {
  const card = article(renderWorkspaceHub("transient_operations"), "线性化分析");
  assert.match(card.body, /尚未接入/);
  const buttons = elements(card.body, "button");
  assert.equal(buttons.length, 1);
  assert.ok("disabled" in buttons[0].attrs);
  assert.ok(!("data-workspace-view" in buttons[0].attrs));
  assert.ok(!("data-workspace-action" in buttons[0].attrs));
  const sidebar = elements(entry, "button").filter(node => node.body.includes("线性化分析"));
  assert.equal(sidebar.length, 0,"unimplemented analysis remains documented in the hub, outside the task tree");
});

test("resource cards distinguish existing analysis and numerical self-checks from engine models", () => {
  const library = renderWorkspaceHub("model_library");
  const component = article(library, "部件模型与结果");
  assert.match(component.body, /已有分析入口/);
  assert.match(component.body, /通用可执行部件库.*仍在建设/);
  const numerical = article(library, "数值求解与诊断");
  assert.match(numerical.body, /框架自检/);
  assert.match(numerical.body, /代数自检/);
  assert.match(numerical.body, /不产生发动机工作点/);
  const data = article(library, "部件特性数据");
  assert.match(data.body, /尚未接入固定硬件求解/);
  const steady = article(renderWorkspaceHub("steady_operations"), "固定硬件稳态与配平");
  assert.match(steady.body, /仅任务定义/);
  assert.match(steady.body, /实际稳态工作点与推力配平尚未开放/);
  const transient = article(renderWorkspaceHub("transient_operations"), "瞬态响应研究");
  assert.match(transient.body, /局部研究模型/);
  assert.match(transient.body, /尚未与新固定硬件稳态模型统一/);
});

test("project, record and breadcrumb text is escaped rather than executable markup", () => {
  const payload = '<script>alert(1)</script>&"\'';
  const encoded = "&lt;script&gt;alert(1)&lt;/script&gt;&amp;&quot;&#39;";
  for (const view of hubViews.filter(view=>view!=="guide")) {
    const markup = renderWorkspaceHub(view, {
      project: {name: `project-${payload}`, revision: payload},
      record: {id: `record-${payload}`, revision: payload}, dirty: true,
    });
    assert.ok(markup.includes(`project-${encoded}`));
    assert.ok(markup.includes(`record-${encoded}`));
    assert.doesNotMatch(markup, /<script\b|<img\b/i);
    assert.match(markup, /有未保存编辑/);
  }
  const breadcrumb = renderWorkspaceBreadcrumb("fixed_steady", payload);
  assert.ok(breadcrumb.includes(encoded));
  assert.doesNotMatch(breadcrumb, /<script\b/i);
  assert.match(breadcrumb, /固定硬件稳态分析/);
  assert.match(breadcrumb, /计算未开放/);
});

test("parameter sections preserve each available group, including unknown future groups", () => {
  const groups = [
    {id: "future<&\"", title: "未来组 <新增>"},
    {id: "solver", title: "数值求解"},
    {id: "cycle", title: "循环方式"},
    {id: "wall_thermal", title: "壁面材料"},
    {id: "component_efficiencies", title: "部件假设"},
    {id: "extension", title: "扩展组"},
  ].map(Object.freeze);
  Object.freeze(groups);
  const markup = renderParameterNavigation(groups);
  const options = elements(markup, "option");
  assert.equal(options.length, groups.length);
  assert.deepEqual(new Set(options.map(node => decode(node.attrs.value))), new Set(groups.map(g => g.id)));
  for (const group of groups) {
    const selected = options.filter(node => decode(node.attrs.value) === group.id);
    assert.equal(selected.length, 1);
    assert.equal(decode(selected[0].body), group.title);
  }
  const other = elements(markup, "optgroup").find(node => node.attrs.label === "其他设置");
  assert.ok(other);
  assert.deepEqual(elements(other.body, "option").map(node => decode(node.attrs.value)),
    ["future<&\"", "extension"]);
  assert.doesNotMatch(markup, /<新增>/);
  assert.equal(renderParameterNavigation([]), "");
});

test("parameter breadcrumb follows its task branch with escaped labels and unchanged defaults",()=>{
  const requirements=WORKSPACE_GROUPS.find(group=>group.id==="concept");
  const breadcrumb=renderWorkspaceBreadcrumb("overview","推力与设计目标",requirements);
  assert.match(breadcrumb,/<span>总体方案与循环设计<\/span>/);
  assert.match(breadcrumb,/<span>推力与设计目标<\/span>/);
  assert.doesNotMatch(breadcrumb,/总体设计点/);
  assert.equal(renderWorkspaceBreadcrumb("overview","概念设计",null),renderWorkspaceBreadcrumb("overview","概念设计"));
  const payload='<script>alert(1)</script>&"';
  const unsafe=renderWorkspaceBreadcrumb("overview",payload,{id:"requirements",label:payload});
  assert.doesNotMatch(unsafe,/<script/);
  assert.match(unsafe,/&lt;script&gt;alert\(1\)&lt;\/script&gt;&amp;&quot;/);
  assert.equal((unsafe.match(/&lt;script&gt;/g)||[]).length,1,"duplicate branch and title are collapsed");
  assert.match(renderWorkspaceBreadcrumb("fixed_steady","任务",requirements),/计算未开放/);
});

test("concept parameters are hidden for operating studies, results and read-only hubs", () => {
  const editable = new Set(["overview", "geometry", "chamber_design", "nozzle_design", "pump_geometry", "injector_design", "turbine_geometry", "valve_design", "cooling_workspace"]);
  for (const view of knownViews) assert.equal(hasDesignParameters(view), editable.has(view), view);
  for (const view of ["fixed_steady", "transient", "feed", "chilldown", "ignition", "shutdown",
    "states", "components", "validation", "log", "history", ...hubViews, "unknown"]) {
    assert.equal(hasDesignParameters(view), false, view);
  }
});
