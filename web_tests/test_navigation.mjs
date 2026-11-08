/** Navigation contracts: route coverage, honest capabilities and inert hub content. */
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";
import {
  WORKSPACE_GROUPS, HUB_VIEWS, workspaceGroup, workspaceCapability,
  isWorkspaceHub, hasDesignParameters, renderWorkspaceBreadcrumb,
  renderWorkspaceHub, renderParameterNavigation, LIBRARY_TABS, mountWorkspaceLibrary,
} from "../webapp/static/navigation.js";

const entry = readFileSync(new URL("../webapp/static/index.html", import.meta.url), "utf8");
const legacyViews = [
  "overview", "topology", "states", "components", "geometry", "chamber_design", "nozzle_design", "pump_geometry",
  "injector_design", "turbine_geometry", "log", "history", "shutdown", "ignition",
  "chilldown", "feed", "validation", "transient", "throttle", "operating_throttle", "fixed_steady",
];
const hubViews = ["model_library", "simulation_home", "guide"];
const knownViews = new Set([...legacyViews, ...hubViews,"results_overview","solver_diagnostics"]);

function attributes(source) {
  return Object.fromEntries([...source.matchAll(/([\w-]+)(?:="([^"]*)")?/g)]
    .map(([, name, value]) => [name, value ?? true]));
}
function elements(markup, tag) {
  return [...markup.matchAll(new RegExp(`<${tag}\\b([^>]*)>([\\s\\S]*?)<\\/${tag}>`, "g"))]
    .map(([, attrs, body]) => ({attrs: attributes(attrs), body}));
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
  assert.equal(WORKSPACE_GROUPS.length, 5);
  assert.equal(new Set(WORKSPACE_GROUPS.map(group => group.id)).size, 5);
  const assigned = WORKSPACE_GROUPS.flatMap(group => group.views);
  assert.equal(assigned.length, new Set(assigned).size, "a page must not belong to two groups");
  assert.deepEqual(new Set(assigned), knownViews);
  assert.deepEqual(new Set(Object.keys(HUB_VIEWS)), new Set(hubViews));

  const buttons = elements(entry, "button").filter(node => "data-view" in node.attrs);
  assert.equal(buttons.length, knownViews.size);
  assert.deepEqual(new Set(buttons.map(node => node.attrs["data-view"])), knownViews);
  assert.ok(buttons.every(node => !("disabled" in node.attrs)), "existing pages stay reachable");

  const groups = elements(entry, "details").filter(node => "data-nav-group" in node.attrs);
  assert.equal(groups.length, WORKSPACE_GROUPS.length);
  for (const group of WORKSPACE_GROUPS) {
    const node = groups.find(candidate => candidate.attrs["data-nav-group"] === group.id);
    assert.ok(node, `Missing group ${group.id}`);
    assert.deepEqual(elements(node.body, "button").filter(b => "data-view" in b.attrs)
      .map(b => b.attrs["data-view"]), group.views);
    for (const view of group.views) assert.equal(workspaceGroup(view)?.id, group.id);
  }
  assert.equal(workspaceGroup("missing-route"), null);
});

test("design scans and operating studies remain distinct, with explicit capability limits", () => {
  assert.equal(workspaceGroup("throttle").id, "results");
  assert.ok(!WORKSPACE_GROUPS.find(group=>group.id==="design").views.includes("throttle"));
  assert.match(renderWorkspaceBreadcrumb("throttle","设计匹配扫描历史"), /结果与诊断/);
  assert.doesNotMatch(entry, /历史设计工具/);
  assert.equal(workspaceGroup("fixed_steady").id, "simulation");
  assert.equal(workspaceGroup("operating_throttle").id, "simulation");
  assert.match(workspaceCapability("operating_throttle").label, /固定几何.*局部估算/);
  assert.equal(workspaceCapability("throttle").tone, "legacy");
  assert.match(workspaceCapability("throttle").label, /设计扫描/);
  assert.equal(workspaceCapability("fixed_steady").tone, "pending");
  assert.match(workspaceCapability("fixed_steady").label, /任务定义/);
  assert.match(workspaceCapability("fixed_steady").label, /未开放/);
  for (const view of ["transient", "feed", "chilldown", "ignition", "shutdown"]) {
    assert.equal(workspaceGroup(view).id, "simulation");
    assert.equal(workspaceCapability(view).tone, "research");
    assert.match(workspaceCapability(view).label, /研究.*受限/);
  }
  assert.match(workspaceCapability("topology").label, /只读/);
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
    assert.doesNotMatch(markup, /<(?:form|input|script|iframe)\b/i);
    const buttons = elements(markup, "button");
    assert.ok(buttons.length > 0);
    for (const {attrs} of buttons) {
      assert.equal(attrs.type, "button", "hub actions must not submit a form");
      assert.ok(Object.keys(attrs).every(name => !/^on/i.test(name)), "no inline execution handlers");
      const viewTarget = attrs["data-workspace-view"];
      const action = attrs["data-workspace-action"];
      const libraryTab=attrs["data-library-tab"];
      if (viewTarget) assert.ok(knownViews.has(viewTarget), `Unknown target ${viewTarget}`);
      if (action) assert.equal(action, "references");
      if (libraryTab)assert.ok(LIBRARY_TABS.some(tab=>tab.id===libraryTab));
      assert.ok([viewTarget,action,libraryTab].filter(Boolean).length === 1 || "disabled" in attrs,
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
  const card = article(renderWorkspaceHub("simulation_home"), "线性化分析");
  assert.match(card.body, /尚未接入/);
  const buttons = elements(card.body, "button");
  assert.equal(buttons.length, 1);
  assert.ok("disabled" in buttons[0].attrs);
  assert.ok(!("data-workspace-view" in buttons[0].attrs));
  assert.ok(!("data-workspace-action" in buttons[0].attrs));
  const sidebar = elements(entry, "button").filter(node => node.body.includes("线性化分析"));
  assert.equal(sidebar.length, 1);
  assert.ok("disabled" in sidebar[0].attrs);
  assert.ok(!("data-view" in sidebar[0].attrs));
  assert.ok(!("data-nav" in sidebar[0].attrs));
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
  const steady = article(renderWorkspaceHub("simulation_home"), "固定硬件稳态与配平");
  assert.match(steady.body, /仅任务定义/);
  assert.match(steady.body, /实际稳态工作点与推力配平尚未开放/);
  const transient = article(renderWorkspaceHub("simulation_home"), "瞬态响应研究");
  assert.match(transient.body, /局部研究模型/);
  assert.match(transient.body, /尚未与新固定硬件稳态模型统一/);
});

test("project, record and breadcrumb text is escaped rather than executable markup", () => {
  const payload = '<script>alert(1)</script>&"\'';
  const encoded = "&lt;script&gt;alert(1)&lt;/script&gt;&amp;&quot;&#39;";
  for (const view of hubViews) {
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
  assert.match(breadcrumb, /工况与运行/);
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

test("concept parameters are hidden for operating studies, results and read-only hubs", () => {
  const editable = new Set(["overview", "geometry", "chamber_design", "nozzle_design", "pump_geometry", "injector_design", "turbine_geometry"]);
  for (const view of knownViews) assert.equal(hasDesignParameters(view), editable.has(view), view);
  for (const view of ["fixed_steady", "transient", "feed", "chilldown", "ignition", "shutdown",
    "states", "components", "validation", "log", "history", ...hubViews, "unknown"]) {
    assert.equal(hasDesignParameters(view), false, view);
  }
});
