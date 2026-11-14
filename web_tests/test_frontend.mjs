/** State/API regression tests with a small DOM double; no browser automation. */
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {spawnSync} from "node:child_process";
import {fileURLToPath} from "node:url";
import vm from "node:vm";
import {webcrypto} from "node:crypto";
import test from "node:test";
import * as parameterEditor from "../webapp/static/parameter-editor.js";
import * as parameterFinder from "../webapp/static/parameter-finder.js";
import * as conceptDesign from "../webapp/static/concept-design.js";
import {mountSteadyWorkspace} from "../webapp/static/steady-workspace.js";
import {renderValveRequirements} from "../webapp/static/valve-requirements.js";
import {renderValveDesign} from "../webapp/static/valve-design.js";
import * as valveConfiguration from "../webapp/static/valve-configuration.js";
import {recordModelValidity} from "../webapp/static/model-validity.js";
import {supportsCycleResult} from "../webapp/static/cycle-model.js";
import * as workspaceNavigation from "../webapp/static/navigation.js";
import * as helpManual from "../webapp/static/help-manual.js";
import {mountNavigationResize} from "../webapp/static/navigation.js";
import * as projectOverview from "../webapp/static/project-overview.js";
import * as setupTree from "../webapp/static/setup-tree.js";
import * as designReadiness from "../webapp/static/design-readiness.js";
import * as preburnerDesign from "../webapp/static/preburner-design.js";
import * as coolingInputSummary from "../webapp/static/cooling-input-summary.js";
import * as coolingChannelInputs from "../webapp/static/cooling-channel-inputs.js";
import * as pressureDesign from "../webapp/static/pressure-design.js";
import {migratePressureReview} from "../webapp/static/design-review-migration.js";
import {migrateWorkflowReview} from "../webapp/static/parameter-review.js";
import * as solverWorkspace from "../webapp/static/solver-workspace.js";
import * as lifecycle from "../webapp/static/engineering-lifecycle.js";
import {renderCooling} from "../webapp/static/cooling.js";

const root = new URL("../", import.meta.url);
const html = readFileSync(new URL("webapp/static/index.html", root), "utf8");
const source = readFileSync(new URL("webapp/static/app.js", root), "utf8")
  .replace(/^import .*;\r?\n/gm, "")
  .replace(/^try\{await initialize\(\);\}catch\(error\)\{.*\}\r?$/m, "");
const defaults = {cycle_type: "ffsc", thrust: 829640, pc: 10100000, throttle_steps: 5};
const fields = [
  {key: "cycle_type", default: "ffsc", label: "循环类型", factor: 1},
  {key: "thrust", default: 829640, label: "目标推力", type: "number", factor: 0.001},
  {key: "pc", default: 10100000, label: "燃烧室压力", type: "number", factor: 0.000001},
  {key: "throttle_steps", default: 5, label: "步数", type: "int", factor: 1},
];
const schema = {defaults, groups: [{id: "design", title: "设计", fields}]};
const project = {id: "p1", name: "测试方案", description: "", author: "测试", revision: 1,
  updated: 1, inputs: defaults};
const info = {python: "3.14.4", ui_schema: schema, presets: [{key: "test", label: "参考机型", inputs: defaults}]};
const user = {id: "u1", display_name: "测试", role: "admin"};
const copy = value => JSON.parse(JSON.stringify(value));

function element() {
  const classes = new Set();
  const listeners = new Map();
  const callbacks = new Map();
  const attributes = new Map();
  const styles = new Map();
  return {value: "", textContent: "", innerHTML: "", hidden: false, disabled: false,
    dataset: {}, className: "", inert: false, querySelectorAll: () => [], querySelector: () => null,
    listeners, attributes, focusCount: 0, selectCount: 0, scrollCount: 0,
    style: {setProperty:(name,value)=>styles.set(name,String(value)),getPropertyValue:name=>styles.get(name)||""},
    classList: {contains: name => classes.has(name), add(...names) {names.forEach(name=>classes.add(name));}, remove(...names) {names.forEach(name=>classes.delete(name));}, toggle(name, force) {const on = force ?? !classes.has(name); on ? classes.add(name) : classes.delete(name); return on;}},
    reportValidity: () => true, checkValidity: () => true, addEventListener(name, callback) {
      if(!callbacks.has(name))callbacks.set(name,new Set());callbacks.get(name).add(callback);
      listeners.set(name,event=>{let result;for(const listener of callbacks.get(name))result=listener(event);return result;});
    },
    removeEventListener(name,callback) {callbacks.get(name)?.delete(callback);if(!callbacks.get(name)?.size)listeners.delete(name);},
    setAttribute(name,value) {attributes.set(name,String(value));}, getAttribute(name) {return attributes.get(name)??null;}, hasAttribute:name=>attributes.has(name), removeAttribute(name) {attributes.delete(name);},
    setSelectionRange(start,end) {this.selectionStart=start;this.selectionEnd=end;},
    contains(node) {return node===this;}, select() {this.selectCount++;}, scrollIntoView() {this.scrollCount++;},
    showModal() {this.open=true;}, close() {this.open=false;listeners.get("close")?.({target:this});}, reset() {}, append() {}, click() {}, remove() {}, focus() {this.focusCount++;}};
}

function memoryStorage() {
  const items=new Map();
  return {getItem:key=>items.get(key)??null,setItem:(key,value)=>items.set(key,String(value)),
    removeItem:key=>items.delete(key)};
}

function harness(fetcher,{storage=memoryStorage(),timers={},confirmResult=true}={}) {
  let activeElement=null;
  const makeNode=id=>{const node=element();node.id=id;node.focus=()=>{node.focusCount++;activeElement=node;};return node;};
  const nodes = new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(match => [match[1], makeNode(match[1])]));
  // Help is rendered dynamically. Replacing its markup replaces its inputs too,
  // so focus/caret assertions detect restoration to the new search control.
  let helpMarkup="";
  Object.defineProperty(nodes.get("help-content"),"innerHTML",{get:()=>helpMarkup,set(value){
    helpMarkup=value;
    for(const id of ["help-search","help-article-title"]){
      nodes.delete(id);if(value.includes(`id="${id}"`))nodes.set(id,makeNode(id));
    }
    const search=nodes.get("help-search");if(search)search.value=/id="help-search"[^>]*value="([^"]*)"/.exec(value)?.[1]||"";
  }});
  const conceptButtons=["settings","results","diagnostics"].map(section=>{
    const button=nodes.get(`concept-tab-${section}`);button.dataset.conceptSection=section;return button;
  });
  nodes.get("concept-navigation").querySelectorAll=selector=>selector==="[data-concept-section]"?conceptButtons:[];
  const navGroups=[...html.matchAll(/<details\b([^>]*data-nav-group="([^"]+)"[^>]*)>/g)].map(([,attrs,id])=>{
    const node=element();node.dataset.navGroup=id;node.open=/\bopen(?:\s|$)/.test(attrs);return node;
  });
  const setupSections=[...html.matchAll(/<details\b([^>]*data-setup-section="([^"]+)"[^>]*)>/g)].map(([,attrs,id])=>{
    const node=element();node.dataset.setupSection=id;node.open=/\bopen(?:\s|$)/.test(attrs);return node;
  });
  const navButtons=[...html.matchAll(/<button\b[^>]*data-view="([^"]+)"[^>]*>/g)].map(([,view])=>{
    const node=element();node.dataset.view=view;node.focus=()=>{node.focusCount++;activeElement=node;};return node;
  });
  const helpButtons=[...html.matchAll(/<button\b[^>]*data-help-chapter="([^"]+)"[^>]*>/g)].map(([,chapter])=>{
    const node=element();node.dataset.helpChapter=chapter;return node;
  });
  const setupBranches=[...html.matchAll(/data-setup-branch="([^"]+)"/g)].map(([,branch])=>{
    const node=element();node.dataset.setupBranch=branch;return node;
  });
  const setupSummaries=[...html.matchAll(/data-setup-summary="([^"]+)"/g)].map(([,branch])=>{
    const node=element();node.dataset.setupSummary=branch;return node;
  });
  const setupExpandButtons=[...html.matchAll(/<button\b[^>]*data-setup-expand="([^"]+)"[^>]*>/g)].map(([,value])=>{
    const node=element();node.dataset.setupExpand=value;return node;
  });
  if(nodes.has("setup-tree-tools"))nodes.get("setup-tree-tools").querySelectorAll=selector=>selector==="[data-setup-expand]"?setupExpandButtons:[];
  const navigationNodes=selector=>selector.split(",").flatMap(part=>({
    "[data-concept-section]":conceptButtons,
    "[data-nav-group]":navGroups,
    "[data-setup-section]":setupSections,
    "[data-view]":navButtons,
    "[data-help-chapter]":helpButtons,
    ".sidebar [data-help-chapter]":helpButtons,
    "[data-help-return]":[],
    "[data-setup-branch]":setupBranches,
    "[data-setup-summary]":setupSummaries,
    "[data-setup-expand]":setupExpandButtons,
  }[part.trim()]||[]));
  const body = element();
  let confirmations = 0;
  const throttleLifecycle = {mounts:0,refreshes:0,leaves:0};
  const operatingThrottleLifecycle = {mounts:0,refreshes:0,leaves:0};
  const transientLifecycle = {mounts:0,refreshes:0,leaves:0};
  const analysisRenders=[];
  const analysisMounts={cycle:[],flow:[],components:[]};
  const turbineLifecycle={mounts:0,cleanups:0};
  const engineeringLifecycle={mounts:0,cleanups:0,updates:[],options:null,resizeRefreshes:0};
  const monitorLifecycle={mounts:0,cleanups:0,updates:[],options:null};
  const context = vm.createContext({
    ...parameterEditor,
    ...lifecycle,renderCooling,
    ...parameterFinder,
    ...conceptDesign,
    ...workspaceNavigation,
    ...helpManual,
    mountNavigationResize,
    mountSolverMonitor(host,options){
      monitorLifecycle.mounts++;monitorLifecycle.options=options;
      return {update(value){monitorLifecycle.updates.push(copy(value));},dispose(){monitorLifecycle.cleanups++;}};
    },
    mountEngineeringView(host,options){
      engineeringLifecycle.mounts++;engineeringLifecycle.options=options;
      return {update(value){engineeringLifecycle.updates.push(copy(value));},dispose(){engineeringLifecycle.cleanups++;}};
    },
    mountEngineeringResize(){return {refresh(){engineeringLifecycle.resizeRefreshes++;},dispose(){}};},
    ...projectOverview,
    ...setupTree,
    ...designReadiness,
    ...preburnerDesign,
    ...coolingInputSummary,
    ...coolingChannelInputs,
    ...valveConfiguration, recordModelValidity,
    ...pressureDesign, migratePressureReview, migrateWorkflowReview, ...solverWorkspace,
    document: {hidden: false, get activeElement(){return activeElement;}, getElementById: id => {
      assert.ok(nodes.has(id), `Unexpected DOM id ${id}`);return nodes.get(id);
    }, querySelectorAll: navigationNodes, querySelector: selector => {
      if(selector===".sidebar")return nodes.get("workspace-sidebar");
      const group=/^\[data-nav-group="([^"]+)"\]$/.exec(selector);
      if(group)return navGroups.find(node=>node.dataset.navGroup===group[1])||null;
      const section=/^\[data-setup-section="([^"]+)"\]$/.exec(selector);
      if(section)return setupSections.find(node=>node.dataset.setupSection===section[1])||null;
      return element();
    }, body, createElement: () => element()},
    window: {innerWidth:1280,localStorage:memoryStorage(),addEventListener() {},removeEventListener() {}}, sessionStorage:storage,
    renderAnalysis: (view,record) => {analysisRenders.push({view,record});return "analysis";}, renderValveRequirements, renderValveDesign, mountValveSelection:()=>()=>{}, supportsCycleResult, configureResultSchema() {}, fetch: fetcher, AbortController, Blob, URL,
    mountShutdown:()=>({refresh(){},sync(){},onLeave(){}}),mountIgnition:()=>({refresh(){},sync(){},onLeave(){}}),mountChilldown:()=>({refresh(){},sync(){},onLeave(){}}),mountFeedNetwork:()=>({refresh(){},sync(){},onLeave(){}}),
    mountTransient: () => {transientLifecycle.mounts++;return {refresh(){transientLifecycle.refreshes++;},onLeave(){transientLifecycle.leaves++;},sync(){}};},
    mountValidation: () => ({refresh(){},onLeave(){},sync(){}}),
    mountPropertyLibrary:()=>({refresh(){},dispose(){}}),
    mountFixedSteady: () => ({refresh(){},onLeave(){},sync(){}}),
    mountOperatingThrottle: () => {operatingThrottleLifecycle.mounts++;return {refresh(){operatingThrottleLifecycle.refreshes++;},onLeave(){operatingThrottleLifecycle.leaves++;},sync(){}};},
    mountThrottle: () => {throttleLifecycle.mounts++;return {refresh(){throttleLifecycle.refreshes++;},onLeave(){throttleLifecycle.leaves++;},sync(){}};},
    mountComponentWorkspace(host,recordId,options) {analysisMounts.components.push({host,recordId,options});},
    mountFlowWorkspace(host,recordId,options) {analysisMounts.flow.push({host,recordId,options});},
    mountCycleWorkspace(host,recordId,options) {analysisMounts.cycle.push({host,recordId,options});},
    renderNozzleGeometry:()=>"geometry",mountNozzleGeometry:()=>()=>{},
    renderNozzleDesign:()=>"nozzle_design",mountNozzleDesign:()=>()=>{},
    renderPumpGeometry:()=>"pump_geometry",mountPumpGeometry:()=>()=>{},
    renderInjectorDesign:()=>"injector_design",mountInjectorDesign:()=>()=>{},
    renderTurbineGeometry:()=>"turbine_geometry",mountTurbineGeometry:()=>()=>{},
    renderTurbineDesign:()=>"turbine_design",mountTurbineDesign:()=>{turbineLifecycle.mounts++;return ()=>{turbineLifecycle.cleanups++;};},
    renderResultConsistency:()=>"consistency",
    mountSteadyWorkspace,
    setTimeout: timers.setTimeout||(()=>1), clearTimeout:timers.clearTimeout||(()=>{}), setInterval() {}, confirm: () => {confirmations++;return confirmResult;},
    FormData: class {constructor(form){this.entries=form.formData||[];}*[Symbol.iterator](){yield* this.entries;}}, console, crypto:webcrypto, TextEncoder,
  });
  vm.runInContext(source + `\n;globalThis.app={api,upgradeThermochemistryData,runDesign,saveProject,initialize,poll,refreshProjectSide,refreshMembers,switchPage,withEditorLock,fillProject,applyInputDocument,buildInputBackup,validateDraft,loadRecord,showResult,chooseView,chooseConceptSection,navigate,openProject,adoptCoolingCandidate,submitComponentFeedback,editCoolingChannel,filterParameters,currentWorkflow,openReadinessField,openParameterFinder,closeParameterFinder,updateParameterMatches,openParameterMatch,openHelp,returnFromHelp,
    reset(){user=null;initialized=false;info=null;page="projects";analysisView="overview";conceptSection="settings";libraryTab="components";helpChapter="workflow";helpQuery="";helpOrigin=null;current=null;draft=null;dirty=false;resultRecord=null;selectedJobId=null;projectJobs=[];workspaceUserId=null;privateWorkspace=false;personalWorkspace=false;coolingChannelEditor=null;},
    seed(value){({current,draft,dirty,user,info,groups,fields,page,projectJobs,connected}=value);referenceKey=value.referenceKey||"";presetUndo=value.presetUndo||null;},
    state(){return JSON.parse(JSON.stringify({current,draft,dirty,user,page,analysisView,conceptSection,libraryTab,helpChapter,helpQuery,helpOrigin,projectJobs,connected,editorBusy,resultRecord,selectedJobId,referenceKey,presetUndo,coolingChannelEditor,groupReviews:[...designGroupReviews]}));}};`, context,
    {filename: fileURLToPath(new URL("webapp/static/app.js", root))});
  nodes.get("project-name").value = project.name;
  nodes.get("project-description").value = "";
  context.app.seed(copy({current: project, draft: defaults, dirty: false, user, info,
    groups: schema.groups, fields, page: "project", projectJobs: [], connected: true}));
  return {app: context.app, nodes, body, storage, navGroups, navButtons, helpButtons, setupSections, setupBranches, setupSummaries, setupExpandButtons,
    throttleLifecycle, operatingThrottleLifecycle, transientLifecycle, turbineLifecycle, engineeringLifecycle, monitorLifecycle, analysisRenders, analysisMounts, confirmations:()=>confirmations,
    activeElement:()=>activeElement};
}

const response = (data, status = 200) => ({ok: status>=200&&status<300, status, type:"basic",
  headers:new Headers({"Content-Type":"application/json"}), json: async () => copy(data)});

function supplyHarness(fetcher) {
  const old={...defaults,tank_pressure_pa:425000,feed_line_dp_pa:65000,inlet_pressure:1170000,boost_pump_efficiency:.68};
  const supplyFields=[
    ["fuel_tank_pressure_pa","tank_pressure_pa"],["ox_tank_pressure_pa","tank_pressure_pa"],
    ["fuel_feed_line_dp_pa","feed_line_dp_pa"],["ox_feed_line_dp_pa","feed_line_dp_pa"],
  ].map(([key,fallback_key])=>({key,fallback_key,label:key,type:"number",default:null,nullable:true,
    factor:1e-6,unit:"MPa",show_effective_value:true,show_role:true,role:"boundary"}));
  const commonFields=Object.keys(old).filter(key=>!(key in defaults)).map(key=>({key,label:key,type:"number",default:old[key],hidden:true}));
  // The real schema adds presentation flags only to system_groups. Canonical
  // definitions still drive type checking and storage.
  const allFields=[...fields,...commonFields,...supplyFields.map(({show_effective_value,show_role,...field})=>field)];
  const allDefaults={...old,...Object.fromEntries(supplyFields.map(f=>[f.key,null]))};
  const supplyGroup={id:"inlet",title:"推进剂供给条件",compact:true,fields:supplyFields};
  const groups=[schema.groups[0],supplyGroup];
  const currentInfo={...info,ui_schema:{defaults:allDefaults,groups}};
  const saved={...project,inputs:old};
  const h=harness(fetcher);
  h.app.seed(copy({current:saved,draft:allDefaults,dirty:false,user,info:currentInfo,
    groups,fields:allFields,page:"project",projectJobs:[],connected:true}));
  h.app.fillProject(copy(saved));
  return {...h,old,supplyFields,saved};
}

test("historical supply values display and survive unrelated saves without materializing per-side overrides",async()=>{
  let payload;
  const h=supplyHarness(async(url,options)=>{
    assert.equal(url,"/api/projects/p1");assert.equal(options.method,"PUT");
    payload=JSON.parse(options.body);
    return response({project:{...h.saved,...payload,revision:2},warnings:[]});
  });
  h.app.openReadinessField("inlet");
  const markup=h.nodes.get("parameter-fields").innerHTML;
  assert.match(markup,/data-input="fuel_tank_pressure_pa" value="0\.425"/);
  assert.match(markup,/data-input="ox_feed_line_dp_pa" value="0\.065"/);
  assert.match(markup,/沿用共用设定/);
  editField(h,"thrust","950");
  await h.app.saveProject();
  for(const field of h.supplyFields)assert.equal(payload.inputs[field.key],null);
  for(const key of ["tank_pressure_pa","feed_line_dp_pa","inlet_pressure","boost_pump_efficiency"])
    assert.equal(payload.inputs[key],h.old[key]);
  assert.equal(payload.inputs.thrust,950000);
});

test("supply edits update the engineering draft and clearing restores inheritance instead of zero",async()=>{
  const h=supplyHarness(async()=>{throw Error("Editing is local");});
  h.app.openReadinessField("inlet");
  const host=h.nodes.get("parameter-fields");
  const note={dataset:{parameterInheritance:"fuel_tank_pressure_pa"},textContent:""};
  host.querySelectorAll=selector=>selector==="[data-parameter-inheritance]"?[note]:[];
  const target={dataset:{input:"fuel_tank_pressure_pa"},value:"0.52"};
  host.oninput({target});
  assert.ok(Math.abs(h.app.state().draft.fuel_tank_pressure_pa-520000)<1e-6);
  assert.equal(h.app.state().draft.ox_tank_pressure_pa,null);
  assert.equal(note.dataset.inherited,"false");assert.match(note.textContent,/本路独立设定/);
  assert.ok(Math.abs(h.engineeringLifecycle.updates.at(-1).draft.fuel_tank_pressure_pa-520000)<1e-6);
  target.value="";host.oninput({target});
  assert.equal(h.app.state().draft.fuel_tank_pressure_pa,null);
  assert.equal(note.dataset.inherited,"true");assert.match(note.textContent,/0\.425 MPa/);
  assert.equal(target.value,"");
  host.listeners.get("focusout")({target});
  assert.equal(target.value,.425);
  assert.equal(h.app.state().draft.fuel_tank_pressure_pa,null);
  host.onchange({target});
  assert.match(host.innerHTML,/data-input="fuel_tank_pressure_pa" value="0\.425"/);
  assert.equal(h.app.state().draft.tank_pressure_pa,425000);
  const loss={dataset:{input:"fuel_feed_line_dp_pa"},value:"0"};
  host.oninput({target:loss});host.onchange({target:loss});
  assert.equal(h.app.state().draft.fuel_feed_line_dp_pa,0);
  assert.equal(h.app.state().draft.ox_feed_line_dp_pa,null);
  assert.equal(h.engineeringLifecycle.updates.at(-1).draft.fuel_feed_line_dp_pa,0);
  // Number controls report an invalid partial token as value="". That is not
  // an intentional clear and must not silently activate the fallback value.
  target.value="";target.validity={badInput:true};host.oninput({target});
  assert.equal(note.dataset.inherited,"false");assert.equal(note.dataset.valid,"false");
  host.listeners.get("focusout")({target});assert.equal(target.value,"");
  await assert.rejects(h.app.saveProject(),/有限数值/);
  target.validity.badInput=false;host.oninput({target});host.onchange({target});
  assert.equal(h.app.state().draft.fuel_tank_pressure_pa,null);
  assert.match(host.innerHTML,/data-input="fuel_tank_pressure_pa" value="0\.425"/);
});

test("live monitor follows the selected failed record without replacing the draft or submitting work",async()=>{
  const record={id:"failed-monitor",project_id:project.id,revision:1,status:"failed",created:1,inputs:defaults,result:null};
  const requests=[];
  const h=harness(async(url,options)=>{requests.push({url,method:options.method});return response(record);});
  await h.app.chooseView("design_monitor");
  assert.equal(h.monitorLifecycle.mounts,1);
  h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"pc"},value:"12.25"}});
  const draftBefore=h.app.state().draft;
  await h.monitorLifecycle.options.onSelectRecord(record.id);
  const monitor=h.monitorLifecycle.updates.at(-1);
  assert.equal(monitor.visible,true);assert.equal(monitor.selectedJobId,record.id);
  assert.equal(monitor.record.status,"failed");assert.equal(monitor.draftDirty,true);
  assert.deepEqual(h.app.state().draft,draftBefore);
  assert.deepEqual(requests,[{url:"/api/jobs/failed-monitor",method:"GET"}]);
  await h.app.chooseView("overview");assert.equal(h.monitorLifecycle.updates.at(-1).visible,false);
  await h.app.chooseView("design_monitor");assert.equal(h.monitorLifecycle.mounts,1);
  h.app.switchPage("projects");assert.equal(h.monitorLifecycle.updates.at(-1).visible,false);
});

test("help suspends monitor polling and returning restores the selected job",async()=>{
  const h=harness(async()=>{throw Error("Navigation must not submit work");});
  await h.app.chooseView("design_monitor");
  await h.app.openHelp("workflow");
  assert.equal(h.monitorLifecycle.updates.at(-1).visible,false);
  await h.app.returnFromHelp();
  assert.equal(h.monitorLifecycle.updates.at(-1).visible,true);
  assert.equal(h.monitorLifecycle.mounts,1);
});

test("engineering object navigation preserves the draft and updates selected group without saving or solving",async()=>{
  const h=harness(async()=>{throw Error("Engineering selection must remain local");});
  const panelGroups=[schema.groups[0],{id:"inlet",title:"推进剂供给边界",fields:[fields[2]]}];
  h.app.seed(copy({current:project,draft:defaults,dirty:false,user,info,groups:panelGroups,fields,page:"project",projectJobs:[],connected:true}));
  await h.app.chooseView("overview");
  editField(h,"thrust","975");
  const before=h.app.state();
  h.engineeringLifecycle.options.onGroup("inlet");
  assert.equal(h.engineeringLifecycle.mounts,1);assert.equal(h.engineeringLifecycle.cleanups,0);
  assert.equal(h.nodes.get("parameter-group").value,"inlet");
  assert.equal(h.engineeringLifecycle.updates.at(-1).groupId,"inlet");
  assert.equal(h.engineeringLifecycle.updates.at(-1).draft.thrust,975000);
  for(const key of ["draft","current","dirty","resultRecord","groupReviews"])assert.deepEqual(h.app.state()[key],before[key],key);
  assert.equal(h.confirmations(),0);
});

test("engineering metadata refresh distinguishes saved evidence from modified draft",async()=>{
  const record={id:"engineering-saved",project_id:project.id,revision:1,status:"succeeded",created:1,inputs:defaults,result:{calculation:{inputs:defaults}}};
  const h=harness(async(path,options)=>{assert.equal(path,"/api/jobs/engineering-saved");assert.equal(options.method,"GET");return response(record);});
  await h.app.chooseView("overview");await h.app.loadRecord(record.id);
  assert.equal(h.engineeringLifecycle.updates.at(-1).workflow.freshness.state,"current");
  editField(h,"thrust","975");
  const view=h.engineeringLifecycle.updates.at(-1);
  assert.equal(view.dirty,true);assert.equal(view.workflow.freshness.state,"stale");
  assert.equal(view.draft.thrust,975000);assert.equal(view.record.inputs.thrust,defaults.thrust);
  assert.deepEqual(view.record,record);assert.equal(h.engineeringLifecycle.mounts,1);
});

test("engineering panel and split survive contextual help without resetting the mounted view",async()=>{
  const h=harness(async()=>{throw Error("Help must not save or solve");});
  await h.app.chooseView("overview");editField(h,"thrust","975");
  const before=h.app.state(),host=h.nodes.get("engineering-view"),resize=h.engineeringLifecycle.resizeRefreshes;
  host.innerHTML='<canvas data-retained-camera="saved-view"></canvas>';
  h.app.openHelp("theory");await h.app.returnFromHelp();
  assert.equal(h.engineeringLifecycle.mounts,1);assert.equal(h.engineeringLifecycle.cleanups,0);
  assert.match(host.innerHTML,/data-retained-camera/);
  assert.ok(h.engineeringLifecycle.resizeRefreshes>resize);
  for(const key of ["draft","current","dirty","resultRecord","groupReviews"])assert.deepEqual(h.app.state()[key],before[key],key);
});

test("engineering panel clears previous project evidence before showing a different project",async()=>{
  const record={id:"engineering-old-project",project_id:project.id,revision:1,status:"succeeded",inputs:defaults,result:{calculation:{inputs:defaults}}};
  const h=harness(async()=>response(record));
  await h.app.chooseView("overview");await h.app.loadRecord(record.id);
  const before=h.engineeringLifecycle.updates.length;
  h.app.fillProject({...project,id:"p2",name:"另一方案",inputs:{...defaults,pc:20000000}});
  await h.app.chooseView("overview");
  const updates=h.engineeringLifecycle.updates.slice(before);
  assert.ok(updates.length>0);
  for(const update of updates){assert.equal(update.project.id,"p2");assert.equal(update.record,null);assert.equal(update.selectedJobId,null);}
  assert.equal(updates.at(-1).draft.pc,20000000);
});

test("engineering source switching rejects foreign records and preserves unsaved inputs",async()=>{
  const h=harness(async()=>response({id:"foreign-source",project_id:"other",inputs:defaults,result:{calculation:{}}}));
  await h.app.chooseView("overview");editField(h,"thrust","975");
  const before=h.app.state();
  await assert.rejects(h.app.loadRecord("foreign-source"),/当前方案不一致/);
  const view=h.engineeringLifecycle.updates.at(-1);
  assert.equal(view.record,null);assert.equal(view.selectedJobId,null);
  assert.deepEqual(h.app.state().draft,before.draft);assert.equal(h.app.state().dirty,true);
  assert.equal(h.engineeringLifecycle.mounts,1);
});

test("engineering object and source actions remain inert during an editor transaction",async()=>{
  const h=harness(async()=>{throw Error("Busy engineering controls must not issue requests");});
  await h.app.chooseView("overview");
  const group=h.nodes.get("parameter-group").value,before=h.app.state();
  await h.app.withEditorLock(async()=>{
    assert.equal(h.nodes.get("engineering-view").inert,true);
    h.engineeringLifecycle.options.onGroup("design");
    h.engineeringLifecycle.options.onRecord("some-job");
    h.engineeringLifecycle.options.onView("geometry");
    assert.equal(h.nodes.get("parameter-group").value,group);
  });
  assert.equal(h.nodes.get("engineering-view").inert,false);
  assert.deepEqual(h.app.state(),before);
});

test("engineering controls unlock after saved inputs refresh inside a transaction",async()=>{
  for(const fails of [false,true]){
    const h=harness(async()=>{throw Error("This transaction does not need network access");});
    await h.app.chooseView("overview");
    const transaction=h.app.withEditorLock(async()=>{
      h.app.fillProject({...project,revision:2});
      assert.equal(h.engineeringLifecycle.updates.at(-1).busy,true);
      if(fails)throw Error("refresh failed after saving");
    });
    if(fails)await assert.rejects(transaction,/refresh failed/);else await transaction;
    const view=h.engineeringLifecycle.updates.at(-1);
    assert.equal(view.project.revision,2);
    assert.equal(view.busy,false);
    assert.equal(h.nodes.get("engineering-view").inert,false);
    assert.equal(h.engineeringLifecycle.mounts,1);
  }
});

test("engineering record loading failure is persistent and clears after a successful retry",async()=>{
  let fail=true;
  const record={id:"engineering-retry",project_id:project.id,revision:1,status:"succeeded",inputs:defaults,result:{calculation:{inputs:defaults}}};
  const h=harness(async()=>{if(fail)throw Error("temporary disconnect");return response(record);});
  await h.app.chooseView("overview");editField(h,"thrust","975");
  const before=h.app.state();
  await assert.rejects(h.app.loadRecord(record.id),/无法连接本机服务/);
  const failed=h.engineeringLifecycle.updates.at(-1);
  assert.equal(failed.record,null);assert.equal(failed.selectedJobId,record.id);
  assert.match(failed.recordError,/读取失败/);
  fail=false;await h.app.loadRecord(record.id);
  const retried=h.engineeringLifecycle.updates.at(-1);
  assert.equal(retried.recordError,"");assert.equal(retried.record.id,record.id);
  assert.equal(retried.workflow.freshness.state,"stale");
  assert.deepEqual(h.app.state().draft,before.draft);assert.equal(h.app.state().dirty,true);
});

// Readiness is a property of the dedicated run page, not of the parameter editor.
// Return to the original page so edit/confirmation regressions exercise their own context.
async function assertRunGate(h,disabled,message){
  const previous=h.app.state().analysisView;
  await h.app.chooseView("design_run");
  assert.equal(h.app.state().analysisView,"design_run");
  assert.equal(h.nodes.get("design-run-submit").disabled,disabled,message);
  assert.equal(h.nodes.get("header-run").disabled,disabled,message);
  await h.app.chooseView(previous);
}

test("explicit model selection binds its species configuration without rewriting saved input",()=>{
  const {app,nodes}=harness(()=>{throw Error("unexpected request");});
  const eos={key:"gas_eos",label:"燃气状态方程",default:"ideal-gas",type:"select",choices:[{value:"ideal-gas",label:"理想气体"},{value:"Peng-Robinson",label:"PR"}]};
  const policy={key:"gas_species_policy",default:"auto",type:"text",hidden:true};
  const allFields=[...fields,eos,policy],groups=[...schema.groups,{id:"chamber_chemistry",fields:[eos,policy]}];
  const original={...defaults,gas_eos:"ideal-gas",gas_species_policy:"full",combustion_loss_model:"adiabatic",methane_thermo_dataset:"gri30_highT"};
  const saved={...project,inputs:original};
  app.seed(copy({current:saved,draft:original,dirty:false,user,info:{...info,ui_schema:{defaults:{...defaults,gas_eos:"ideal-gas",gas_species_policy:"auto"},groups}},groups,fields:allFields,page:"project",projectJobs:[],connected:true}));
  assert.equal(app.state().draft.gas_species_policy,"full");
  nodes.get("parameter-fields").oninput({target:{dataset:{input:"gas_eos"},value:"Peng-Robinson"}});
  assert.deepEqual(copy(app.state().draft),{...original,gas_eos:"Peng-Robinson",gas_species_policy:"pr_supported_v1"});
  assert.deepEqual(copy(app.state().current.inputs),original);assert.equal(app.state().dirty,true);
  assert.match(nodes.get("parameter-fields").innerHTML,/PR/);
  nodes.get("parameter-fields").oninput({target:{dataset:{input:"gas_eos"},value:"ideal-gas"}});
  assert.deepEqual(copy(app.state().draft),original);
});

test("historical automatic species remains valid with the actual Python presentation schema",()=>{
  const result=spawnSync("python",["-X","utf8","-c",[
    "import sys,json",
    "sys.path.insert(0,'src')",
    "from webapp.ui_schema import design_ui_schema",
    "print(json.dumps(design_ui_schema()))",
  ].join("\n")],{cwd:fileURLToPath(root),encoding:"utf8"});
  assert.equal(result.status,0,result.stderr);
  const actualSchema=JSON.parse(result.stdout);
  const groups=actualSchema.system_groups,allFields=actualSchema.groups.flatMap(group=>group.fields);
  const model=groups.find(group=>group.id==="chamber_chemistry");
  const species=model.fields.find(field=>field.key==="gas_species_policy");
  assert.ok(species.choices.some(choice=>choice.value==="auto"),"validation metadata must accept saved auto values");
  for(const eos of ["ideal-gas","Peng-Robinson","Peng-Robinson-Boston-Mathias"]){
    const original={...actualSchema.defaults,gas_eos:eos,gas_species_policy:"auto"};
    const {app}=harness(()=>{throw Error("unexpected request");});
    app.seed(copy({current:{...project,inputs:original},draft:original,dirty:false,user,
      info:{...info,ui_schema:actualSchema},groups,fields:allFields,page:"project",projectJobs:[],connected:true}));
    assert.doesNotThrow(()=>app.validateDraft());
    const issues=designReadiness.collectLocalReadiness(groups,app.state().draft).issues;
    assert.deepEqual(issues.filter(issue=>issue.fields.includes("gas_species_policy")),[]);
    const rendered=parameterEditor.renderParameterGroups([model],app.state().draft);
    assert.match(rendered,eos==="ideal-gas"?/53 种气相物种/:/16 种气相物种/);
    assert.match(rendered,/<select[^>]+data-input="gas_eos"/);
    assert.doesNotMatch(rendered,/<input|data-input="gas_species_policy"|data-input="combustion_loss_model"/);
    if(eos!=="ideal-gas")assert.match(rendered,/研究模型/);
    assert.deepEqual(copy(app.state().draft),original);
    assert.deepEqual(copy(app.state().current.inputs),original);
  }
  for(const policy of ["full","pr_supported_v1"]){
    const rendered=parameterEditor.renderParameterGroups([model],{...actualSchema.defaults,gas_species_policy:policy});
    assert.doesNotMatch(rendered,/<option value="auto"/);
  }
});

test("applying a saved model configuration is explicit, atomic and respects editor guards",async()=>{
  for(const guard of ["none","busy","readonly","invalid"]){
    const eos={key:"gas_eos",label:"燃气物性模型",type:"text",default:"ideal-gas",read_only:guard==="readonly"};
    const original={...defaults,gas_eos:"Peng-Robinson",gas_species_policy:"full",combustion_loss_model:"legacy_temperature_rise",methane_thermo_dataset:"gri30",combustion_efficiency:.95};
    const groups=[...schema.groups,{id:"chamber_chemistry",title:"燃烧热化学与燃气物性",fields:[eos],requires_review:true}];
    const h=harness(()=>{throw Error("Model selection must not save or submit a calculation");});
    h.app.seed(copy({current:{...project,inputs:original},draft:original,dirty:false,user,
      info:{...info,ui_schema:{defaults:original,groups}},groups,fields:[...fields,eos],page:"project",projectJobs:[],connected:true}));
    const button={dataset:{thermochemistryApply:guard==="invalid"?"unknown-model":"Peng-Robinson"}};
    const click=()=>h.nodes.get("parameter-fields").listeners.get("click")({target:{closest:selector=>selector==="[data-thermochemistry-apply]"?button:null}});
    assert.deepEqual(copy(h.app.state().draft),original,"opening a saved model must not repair it implicitly");
    if(guard==="busy")await h.app.withEditorLock(click);else click();
    const expected=guard==="none"?{...original,gas_species_policy:"pr_supported_v1",combustion_loss_model:"adiabatic"}:original;
    assert.deepEqual(copy(h.app.state().draft),expected,guard);
    assert.deepEqual(copy(h.app.state().current.inputs),original,guard);
    assert.equal(h.app.state().dirty,guard==="none");
    if(guard==="invalid")assert.match(h.nodes.get("editor-error").textContent,/请选择支持的燃气物性模型/);
  }
});

test("manual liner choice changes only that draft field and preserves dataset manufacturing and history",()=>{
  const {app,nodes}=harness(()=>{throw Error("unexpected request");});
  const liner={key:"chamber_inner_wall_material",label:"燃气侧内衬材料",default:"grcop42",type:"select",
    choices:[{value:"grcop84",label:"GRCop-84"},{value:"grcop42",label:"GRCop-42"}]};
  const groups=[...schema.groups,{id:"wall_thermal",fields:[liner]}],allFields=[...fields,liner];
  const original={...defaults,chamber_inner_wall_material:"grcop84",wall_thermal_dataset_id:"d42",
    wall_manufacturing_state:"unspecified",chamber_outer_wall_material:"i625"};
  app.seed(copy({current:{...project,inputs:original},draft:original,dirty:false,user,
    info:{...info,ui_schema:{defaults:original,groups}},groups,fields:allFields,page:"project",projectJobs:[],connected:true}));
  nodes.get("parameter-fields").oninput({target:{dataset:{input:"chamber_inner_wall_material"},value:"grcop42"}});
  assert.deepEqual(copy(app.state().draft),{...original,chamber_inner_wall_material:"grcop42"});
  assert.deepEqual(copy(app.state().current.inputs),original);
  assert.equal(app.state().dirty,true);
});

function seedChannelEditor(app){
  const inputs={...defaults,cooling_channel_table_json:""};
  const channelField={key:"cooling_channel_table_json",default:"",type:"text",custom_editor:"channel_distribution"};
  app.seed(copy({current:{...project,inputs},draft:inputs,dirty:false,user,
    info:{...info,ui_schema:{...schema,defaults:inputs}},
    groups:[...schema.groups,{id:"chamber_channels",fields:[channelField]}],fields:[...fields,channelField],page:"project",projectJobs:[],connected:true}));
}

function seedValveEditor(app){
  const valves=Object.fromEntries(["MFV","OPFV","FPOV"].map(id=>[id,{
    id,fluid:id==="FPOV"?"O2":"CH4",upstream_node:`${id}_upstream`,downstream_node:`${id}_downstream`,
    characteristic_version:"concept_linear_v1",rated_cda_m2:null,area_curve:[[0,0],[1,1]],opening_min:0,
    opening_max:1,reference_opening:.7,design_pressure_drop_Pa:null,leak_fraction:0,max_pressure_drop_ratio:.1,
    actuator_tau_s:.1,actuator_rate_per_s:1,
  }]));
  const template={schema_version:1,enabled:true,topology_id:"paper_split_before_cooling_v1",mode:"requirement_sizing",valves};
  const valveField={key:"valve_configuration_json",default:"",type:"text",custom_editor:"valve_configuration",
    valve_catalog:{templates:{paper_split_before_cooling_v1:template}}};
  const inputs={...defaults,valve_configuration_json:""},groups=[...schema.groups,{id:"valve_configuration",fields:[valveField]}];
  app.seed(copy({current:{...project,inputs},draft:inputs,dirty:false,user,
    info:{...info,ui_schema:{...schema,defaults:inputs,groups}},groups,fields:[...fields,valveField],
    page:"project",projectJobs:[],connected:true}));
}

test("valve form edits only the current draft and refuses incomplete hardware before any save",async()=>{
  const calls=[];
  const {app,nodes}=harness(async(path,options)=>{
    const body=JSON.parse(options.body);calls.push({path,body});
    return response({project:{...project,revision:2,inputs:body.inputs},warnings:[]});
  });
  seedValveEditor(app);
  const input=(dataset,value)=>nodes.get("parameter-fields").oninput({target:{dataset,value}});
  input({valveAction:"topology"},"paper_split_before_cooling_v1");
  assert.equal(app.state().dirty,true);assert.equal(app.state().current.inputs.valve_configuration_json,"");
  await assert.rejects(app.saveProject(),/设计压降/);assert.equal(calls.length,0);
  for(const valveId of ["MFV","OPFV","FPOV"])input({valveAction:"field",valveId,valveKey:"design_pressure_drop_Pa"},"0.75");
  await app.saveProject();
  assert.equal(calls.length,1);assert.equal(calls[0].path,"/api/projects/p1");
  assert.equal(JSON.parse(calls[0].body.inputs.valve_configuration_json).valves.MFV.design_pressure_drop_Pa,750000);
  assert.equal(app.state().dirty,false);
  input({valveAction:"mode"},"candidate_check");
  await assert.rejects(app.saveProject(),/候选校核/);assert.equal(calls.length,1);
});

test("changing scheme never carries a valve draft into another scheme",()=>{
  const {app,nodes}=harness(()=>{throw Error("unexpected request");});
  seedValveEditor(app);
  nodes.get("parameter-fields").oninput({target:{dataset:{valveAction:"topology"},value:"paper_split_before_cooling_v1"}});
  assert.ok(app.state().draft.valve_configuration_json);
  app.fillProject({...project,id:"p2",name:"另一方案",inputs:{...defaults,valve_configuration_json:""}});
  assert.equal(app.state().draft.valve_configuration_json,"");
  assert.equal(app.state().current.id,"p2");assert.equal(app.state().dirty,false);
});

test("valve preliminary design is an editable independent route with the same draft and saved source",async()=>{
  const calls=[];
  const {app,nodes}=harness(async(path,options)=>{
    calls.push({path,options});
    if(path==="/api/jobs/saved-valves")return response({id:"saved-valves",project_id:"p1",revision:1,status:"succeeded",
      inputs:defaults,result:{calculation:{}},valve_design:{status:"missing",message:"原记录未启用实体阀"}});
    if(path==="/api/projects/p1"&&options.method==="PUT"){
      const body=JSON.parse(options.body);return response({project:{...project,revision:2,inputs:body.inputs},warnings:[]});
    }
    if(path.startsWith("/api/jobs?"))return response([{id:"saved-valves",project_id:"p1",revision:1,status:"succeeded"}]);
    if(path.endsWith("/revisions"))return response([]);
    throw Error(`unexpected request ${path}`);
  });
  seedValveEditor(app);await app.loadRecord("saved-valves");
  const saved=copy(app.state().resultRecord);calls.length=0;
  await app.chooseView("valve_design");
  assert.equal(app.state().analysisView,"valve_design");
  assert.equal(nodes.get("valve-design-inputs").hidden,false);
  assert.equal(nodes.get("parameter-pane").hidden,true);
  assert.equal(nodes.get("concept-settings-host").inert,true);
  assert.match(nodes.get("valve-parameter-fields").innerHTML,/valve-configuration/);
  assert.match(nodes.get("analysis-content").innerHTML,/已保存计算记录.*阀门结果/);
  assert.equal(calls.length,0,"navigation cannot save or run");
  const input=(dataset,value)=>nodes.get("valve-parameter-fields").oninput({target:{dataset,value}});
  input({valveAction:"topology"},"paper_split_before_cooling_v1");
  for(const valveId of ["MFV","OPFV","FPOV"])input({valveAction:"field",valveId,valveKey:"design_pressure_drop_Pa"},"0.75");
  assert.equal(app.state().current.inputs.valve_configuration_json,"");
  assert.equal(app.state().dirty,true);
  assert.equal(nodes.get("valve-dirty-label").textContent,"尚未保存");
  assert.equal(JSON.parse(app.state().draft.valve_configuration_json).valves.OPFV.design_pressure_drop_Pa,750000);
  assert.match(nodes.get("parameter-fields").innerHTML,/value="0.75"/);
  assert.deepEqual(copy(app.state().resultRecord),saved);
  await app.chooseView("overview");await app.chooseView("valve_design");
  assert.equal(app.state().dirty,true);assert.equal(calls.length,0);
  await nodes.get("valve-save").onclick();
  assert.equal(calls.filter(call=>call.options.method==="PUT").length,1);
  assert.equal(app.state().analysisView,"valve_design");
  assert.equal(app.state().dirty,false);
  assert.equal(nodes.get("valve-design-inputs").inert,false);
  assert.equal(nodes.get("valve-dirty-label").textContent,"已保存");
  assert.deepEqual(copy(app.state().resultRecord),saved,"saving never rewrites old valve results");
});

test("valve editor respects save locks and preserves active input markup during record polling",async()=>{
  const {app,nodes}=harness(async(path)=>{
    if(path.startsWith("/api/jobs?"))return response([]);
    if(path.endsWith("/revisions"))return response([]);
    throw Error("unexpected request");
  });
  seedValveEditor(app);await app.chooseView("valve_design");
  nodes.get("valve-parameter-fields").oninput({target:{dataset:{valveAction:"topology"},value:"paper_split_before_cooling_v1"}});
  const draft=copy(app.state().draft);
  await app.withEditorLock(async()=>{
    assert.equal(nodes.get("valve-design-inputs").inert,true);
    nodes.get("valve-parameter-fields").oninput({target:{dataset:{valveAction:"field",valveId:"MFV",valveKey:"reference_opening"},value:"40"}});
    assert.deepEqual(copy(app.state().draft),draft);
  });
  assert.equal(nodes.get("valve-design-inputs").inert,false);
  const host=nodes.get("valve-parameter-fields");
  host.innerHTML="active editor remains mounted";
  await app.refreshProjectSide();
  assert.equal(host.innerHTML,"active editor remains mounted");
  assert.deepEqual(copy(app.state().draft),draft);
});

test("finder queries do not filter the selected group or its nested valve controls",()=>{
  const {app,nodes}=harness(()=>{throw Error("unexpected request");});seedValveEditor(app);
  const owner={dataset:{field:"valve_configuration_json"},hidden:false};
  const details={...element(),closest:()=>owner};
  const group={dataset:{group:"valve_configuration"},querySelectorAll:selector=>selector==="[data-field]"?[owner]:[details]};
  nodes.get("parameter-fields").querySelectorAll=selector=>selector==="[data-group]"?[group]:[];
  nodes.get("parameter-group").value="valve_configuration";
  app.filterParameters();
  assert.equal(group.hidden,false);assert.equal(details.hidden,false);
  nodes.get("parameter-search").value="unmatched text";app.filterParameters();
  assert.equal(group.hidden,false);assert.equal(details.hidden,false);
  assert.equal(nodes.get("parameter-group").disabled,false);
});

function parameterFinderFixture() {
  const modelFields=[
    {key:"gas_eos",label:"燃气状态方程",default:"ideal-gas",type:"select",choices:[{value:"ideal-gas",label:"理想气体"},{value:"Peng-Robinson",label:"PR"}]},
    {key:"model_note",label:"只读模型说明",default:1,type:"number",factor:1,read_only:true},
    {key:"conditional_input",label:"条件参数",default:2,type:"number",factor:1,visibility:{all:[{key:"gas_eos",op:"eq",value:"Peng-Robinson"}]}},
  ];
  const groups=[{...schema.groups[0],title:"设计目标与工况"},{id:"chamber_chemistry",title:"工质与燃烧模型",fields:modelFields}];
  const inputs={...defaults,...Object.fromEntries(modelFields.map(field=>[field.key,field.default]))};
  return {groups,fields:[...fields,...modelFields],inputs,info:{...info,ui_schema:{defaults:inputs,groups}}};
}

function mountFinderFields(h,groups) {
  const rows=new Map(),controls=new Map(),groupNodes=new Map(),folds=[];
  for(const definition of groups){
    const group=element(),groupRows=[];group.dataset.group=definition.id;
    for(const field of definition.fields){
      const row=element(),control=field.read_only?null:element();row.dataset.field=field.key;
      if(control){control.dataset.input=field.key;controls.set(field.key,control);}
      row.querySelector=selector=>selector.startsWith("[data-input]")?control:null;
      row.parentElement=group;row.closest=()=>null;rows.set(field.key,row);groupRows.push(row);
    }
    const groupFolds=[];
    if(definition.id==="chamber_chemistry"){
      const outer=element(),inner=element();outer.open=false;inner.open=false;
      outer.dataset.parameterFold="model-details";inner.dataset.parameterFold="gas-model";
      outer.parentElement=group;inner.parentElement=outer;
      outer.closest=selector=>selector==="details"?outer:null;
      inner.closest=selector=>selector==="details"?inner:null;
      group.closest=()=>null;
      outer.querySelectorAll=inner.querySelectorAll=selector=>selector==="[data-field]"?groupRows:[];
      for(const row of groupRows){row.closest=selector=>selector==="details"?inner:selector==="[data-group]"?group:null;row.parentElement=inner;}
      groupFolds.push(outer,inner);folds.push(outer,inner);
    }
    group.querySelectorAll=selector=>selector==="[data-field]"?groupRows:selector==="[data-initials], [data-parameter-fold]"?groupFolds:[];
    group.querySelector=selector=>selector.startsWith("[data-input]")?groupRows.map(row=>controls.get(row.dataset.field)).find(Boolean)||null:null;
    groupNodes.set(definition.id,group);
  }
  h.nodes.get("parameter-fields").querySelectorAll=selector=>selector==="[data-group]"?[...groupNodes.values()]:selector==="[data-field]"?[...rows.values()]:selector==="[data-initials], [data-parameter-fold]"?folds:[];
  return {rows,controls,groups:groupNodes,folds};
}

function seedParameterFinder(h,fixture=parameterFinderFixture(),saved={...project,inputs:fixture.inputs}) {
  h.app.seed(copy({current:saved,draft:fixture.inputs,dirty:false,user,info:fixture.info,groups:fixture.groups,fields:fixture.fields,page:"project",projectJobs:[],connected:true}));
  h.app.fillProject(copy(saved));
  return fixture;
}

function clickParameterMatch(h,groupId,fieldKey) {
  const button=element();button.dataset.parameterMatchGroup=groupId;button.dataset.parameterMatchField=fieldKey;
  button.closest=selector=>selector.includes("data-parameter-match")?button:null;
  const host=h.nodes.get("parameter-matches");host.contains=node=>node===button;
  host.onclick({target:button,preventDefault(){}});
}

test("parameter finder searches separately without filtering inputs or changing the active setting",async()=>{
  const h=harness(()=>{throw Error("Finding a setting must not request or save data");});
  const fixture=seedParameterFinder(h),dom=mountFinderFields(h,fixture.groups);
  await h.app.chooseView("overview");h.app.filterParameters();
  const before=h.app.state(),selected=h.nodes.get("parameter-group").value;
  const title=h.nodes.get("analysis-title").textContent,markup=h.nodes.get("parameter-fields").innerHTML;
  h.nodes.get("find-parameter").onclick();
  assert.equal(h.nodes.get("parameter-finder").open,true);
  assert.equal(h.activeElement(),h.nodes.get("parameter-search"));
  assert.equal(h.nodes.get("find-parameter").getAttribute("aria-expanded"),"true");
  for(const query of ["燃气状态方程","not-present-anywhere",""]){
    h.nodes.get("parameter-search").value=query;h.nodes.get("parameter-search").oninput();
    assert.equal(h.nodes.get("parameter-group").value,selected);
    assert.equal(h.nodes.get("parameter-group").disabled,false);
    assert.equal(dom.groups.get("design").hidden,false);assert.equal(dom.groups.get("chamber_chemistry").hidden,true);
    assert.equal(h.nodes.get("analysis-title").textContent,title);assert.equal(h.nodes.get("parameter-fields").innerHTML,markup);
    assert.deepEqual(h.app.state(),before);
    if(query==="燃气状态方程"){
      assert.match(h.nodes.get("parameter-matches").innerHTML,/data-parameter-match-group="chamber_chemistry".*data-parameter-match-field="gas_eos"/);
      assert.match(h.nodes.get("parameter-matches").innerHTML,/总体方案与循环设计 \/ .* \/ 工质与燃烧模型/);
    }else assert.doesNotMatch(h.nodes.get("parameter-matches").innerHTML,/data-parameter-match-field=/);
  }
  assert.equal(h.nodes.get("parameter-match-status").textContent,"");
});

test("selecting a finder path updates the setting tree and heading, opens folds, and retains the draft and result",async()=>{
  const fixture=parameterFinderFixture();
  const record={id:"finder-record",project_id:project.id,revision:1,status:"succeeded",result:{calculation:{inputs:fixture.inputs}}};
  const server=navigationServer({uiInfo:fixture.info,records:[record]}),h=harness(server.fetcher);
  seedParameterFinder(h,fixture);await h.app.loadRecord(record.id);await h.app.chooseView("results_overview");
  const dom=mountFinderFields(h,fixture.groups),before=h.app.state(),requests=server.calls.length;
  h.nodes.get("find-parameter").onclick();h.nodes.get("parameter-search").value="燃气状态方程";h.nodes.get("parameter-search").oninput();
  clickParameterMatch(h,"chamber_chemistry","gas_eos");
  assert.equal(h.nodes.get("parameter-finder").open,false);
  assert.equal(h.nodes.get("find-parameter").getAttribute("aria-expanded"),"false");
  assert.equal(h.app.state().analysisView,"overview");assert.equal(h.app.state().conceptSection,"settings");
  assert.equal(h.nodes.get("parameter-group").value,"chamber_chemistry");
  assert.equal(h.nodes.get("analysis-title").textContent,"工质与燃烧模型");
  assert.equal(h.nodes.get("page-name").textContent,"工质与燃烧模型");
  assert.match(h.nodes.get("analysis-breadcrumb").innerHTML,/总体方案与循环设计.*工质与燃烧模型/);
  assertPrimaryNavigation(h,"overview","concept");
  assert.equal(h.setupSections.find(node=>node.dataset.setupSection==="models").classList.contains("is-current"),true);
  assert.equal(dom.groups.get("design").hidden,true);assert.equal(dom.groups.get("chamber_chemistry").hidden,false);
  assert.ok(dom.folds.every(fold=>fold.open));assert.equal(dom.controls.get("gas_eos").focusCount,1);
  assert.equal(dom.rows.get("gas_eos").classList.contains("parameter-located"),true);
  assert.equal(dom.rows.get("gas_eos").scrollCount,1);assert.equal(h.nodes.get("find-parameter").focusCount,0);
  const after=h.app.state();
  assert.deepEqual(after.draft,before.draft);assert.equal(after.dirty,before.dirty);
  assert.deepEqual(after.resultRecord,before.resultRecord);assert.equal(after.selectedJobId,record.id);
  assert.equal(server.calls.length,requests);
});

test("stale finder matches cannot navigate to a removed or newly hidden setting",async()=>{
  const h=harness(()=>{throw Error("Finding a setting cannot request data");});
  const fixture=parameterFinderFixture();fixture.inputs.gas_eos="Peng-Robinson";
  seedParameterFinder(h,fixture);await h.app.chooseView("overview");
  h.nodes.get("find-parameter").onclick();h.nodes.get("parameter-search").value="条件参数";h.nodes.get("parameter-search").oninput();
  assert.match(h.nodes.get("parameter-matches").innerHTML,/data-parameter-match-field="conditional_input"/);
  h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"gas_eos"},value:"ideal-gas"}});
  const before=h.app.state(),selected=h.nodes.get("parameter-group").value;
  for(const [group,key] of [["chamber_chemistry","conditional_input"],["chamber_chemistry","deleted_field"],["deleted_group","gas_eos"]]){
    clickParameterMatch(h,group,key);
    assert.deepEqual(h.app.state(),before);assert.equal(h.nodes.get("parameter-group").value,selected);
    assert.equal(h.nodes.get("parameter-finder").open,true);
    assert.doesNotMatch(h.nodes.get("parameter-matches").innerHTML,/data-parameter-match-field="conditional_input"/);
    assert.match(h.nodes.get("parameter-match-status").textContent,/不适用/);
  }
});

test("read-only finder results focus their own row and replace the previous highlight",async()=>{
  const h=harness(()=>{throw Error("Finding a setting cannot request data");});
  const fixture=seedParameterFinder(h),dom=mountFinderFields(h,fixture.groups);
  h.nodes.get("find-parameter").onclick();clickParameterMatch(h,"chamber_chemistry","gas_eos");
  h.nodes.get("find-parameter").onclick();h.nodes.get("parameter-search").value="只读模型说明";h.nodes.get("parameter-search").oninput();
  assert.match(h.nodes.get("parameter-matches").innerHTML,/data-parameter-match-field="model_note"/);
  const before=h.app.state();clickParameterMatch(h,"chamber_chemistry","model_note");
  assert.equal(dom.rows.get("model_note").focusCount,1);assert.equal(dom.rows.get("model_note").getAttribute("tabindex"),"-1");
  assert.equal(dom.controls.get("gas_eos").focusCount,1,"read-only matches cannot focus a different editable field");
  assert.equal(dom.rows.get("gas_eos").classList.contains("parameter-located"),false);
  assert.equal(dom.rows.get("model_note").classList.contains("parameter-located"),true);
  assert.deepEqual(h.app.state().draft,before.draft);assert.equal(h.app.state().dirty,false);
  await h.app.chooseView("results_overview");
  assert.equal(dom.rows.get("model_note").classList.contains("parameter-located"),false);
});

test("custom valve matches focus the visible field row when their first control is hidden or inside closed details",()=>{
  for(const hiddenBy of ["hidden-ancestor","closed-details","closed-outer-details"]){
    const h=harness(()=>{throw Error("Locating a valve setting cannot request data");});seedValveEditor(h.app);
    const dom=mountFinderFields(h,[...schema.groups,{id:"valve_configuration",fields:[{key:"valve_configuration_json"}]}]);
    const row=dom.rows.get("valve_configuration_json"),control=dom.controls.get("valve_configuration_json");
    const advanced=element(),inner=element();advanced.open=false;inner.open=true;
    advanced.parentElement=row;advanced.closest=selector=>selector==="details"?advanced:null;
    inner.parentElement=advanced;
    const hidden=element();hidden.hidden=true;
    control.dataset={valveAction:"raw"};
    control.closest=selector=>selector==="[hidden]"?(hiddenBy==="hidden-ancestor"?hidden:null):
      selector==="details"?(hiddenBy==="closed-outer-details"?inner:advanced):null;
    const before=h.app.state();h.nodes.get("find-parameter").onclick();clickParameterMatch(h,"valve_configuration","valve_configuration_json");
    assert.equal(control.focusCount,0,hiddenBy);assert.equal(row.focusCount,1,hiddenBy);
    assert.equal(row.getAttribute("tabindex"),"-1");assert.equal(row.classList.contains("parameter-located"),true);
    assert.equal(dom.groups.get("valve_configuration").hidden,false);assert.equal(row.hidden,false);
    assert.equal(advanced.open,false,"locating the field must not force open its advanced controls");
    assert.equal(row.scrollCount,1);assert.equal(h.nodes.get("parameter-finder").open,false);
    assert.deepEqual(h.app.state().draft,before.draft);assert.equal(h.app.state().dirty,before.dirty);
  }
});

test("closing or cancelling the finder restores its tree-entry focus without changing input state",()=>{
  const h=harness(()=>{throw Error("Closing the finder cannot request data");});seedParameterFinder(h);
  for(const cancel of [false,true]){
    const before=h.app.state();h.nodes.get("find-parameter").onclick();
    h.nodes.get("parameter-search").value="压力";h.nodes.get("parameter-search").oninput();
    if(cancel){let prevented=false;h.nodes.get("parameter-finder").listeners.get("cancel")({preventDefault(){prevented=true;}});assert.equal(prevented,true);}
    else h.nodes.get("close-parameter-finder").onclick();
    assert.equal(h.nodes.get("parameter-finder").open,false);assert.equal(h.activeElement(),h.nodes.get("find-parameter"));
    assert.equal(h.nodes.get("find-parameter").getAttribute("aria-expanded"),"false");
    assert.deepEqual(h.app.state(),before);
  }
});

test("Escape closes a nonempty finder on the first keydown before native searchbox clearing",()=>{
  const h=harness(()=>{throw Error("Closing the finder cannot request data");});seedParameterFinder(h);
  h.nodes.get("find-parameter").onclick();h.nodes.get("parameter-search").value="燃烧室压力";h.nodes.get("parameter-search").oninput();
  const before=h.app.state();let prevented=false,stopped=false;
  h.nodes.get("parameter-finder").listeners.get("keydown")({key:"Escape",target:h.nodes.get("parameter-search"),
    preventDefault(){prevented=true;},stopPropagation(){stopped=true;}});
  assert.equal(prevented,true,"the browser's searchbox clear action must be suppressed");assert.equal(stopped,true);
  assert.equal(h.nodes.get("parameter-finder").open,false);assert.equal(h.activeElement(),h.nodes.get("find-parameter"));
  assert.equal(h.nodes.get("find-parameter").getAttribute("aria-expanded"),"false");
  assert.equal(h.nodes.get("parameter-search").value,"燃烧室压力");assert.deepEqual(h.app.state(),before);
});

test("switching or archiving a project and leaving its workspace clears the finder",()=>{
  for(const action of ["switch","archive","leave"]){
    const h=harness(()=>{throw Error("Local workspace transitions cannot request data");}),fixture=seedParameterFinder(h);
    h.nodes.get("find-parameter").onclick();h.nodes.get("parameter-search").value="压力";h.nodes.get("parameter-search").oninput();
    assert.match(h.nodes.get("parameter-matches").innerHTML,/data-parameter-match-field=/);
    if(action==="leave")h.app.switchPage("projects");
    else h.app.fillProject({...project,inputs:{...fixture.inputs,...(action==="archive"?{cycle_type:"gg"}:{})},...(action==="switch"?{id:"p2"}:{})});
    assert.equal(h.nodes.get("parameter-finder").open,false);assert.equal(h.nodes.get("parameter-search").value,"");
    assert.equal(h.nodes.get("parameter-matches").innerHTML,"");assert.equal(h.nodes.get("parameter-match-status").textContent,"");
    assert.equal(h.nodes.get("find-parameter").getAttribute("aria-expanded"),"false");
    if(action!=="switch"){h.nodes.get("find-parameter").onclick();assert.equal(h.nodes.get("parameter-finder").open,false);}
    assert.deepEqual(copy(h.app.state().draft),{...fixture.inputs,...(action==="archive"?{cycle_type:"gg"}:{})});assert.equal(h.app.state().dirty,false);
  }
});

test("busy and missing-project states cannot open or follow finder results",async()=>{
  const h=harness(()=>{throw Error("Blocked finder actions cannot request data");});seedParameterFinder(h);
  await h.app.withEditorLock(async()=>{
    const before=h.app.state();h.nodes.get("find-parameter").onclick();clickParameterMatch(h,"chamber_chemistry","gas_eos");
    assert.notEqual(h.nodes.get("parameter-finder").open,true);assert.deepEqual(h.app.state(),before);
  });
  h.app.reset();const before=h.app.state();h.nodes.get("find-parameter").onclick();clickParameterMatch(h,"chamber_chemistry","gas_eos");
  assert.notEqual(h.nodes.get("parameter-finder").open,true);assert.deepEqual(h.app.state(),before);
});

test("finder keyboard navigation enters available matches and leaves an empty query in the search input",()=>{
  const h=harness(()=>{throw Error("Finding a setting cannot request data");});seedParameterFinder(h);
  h.nodes.get("find-parameter").onclick();
  const keydown=h.nodes.get("parameter-search").listeners.get("keydown");
  let prevented=false;keydown({key:"ArrowDown",preventDefault(){prevented=true;}});
  assert.equal(prevented,false);assert.equal(h.activeElement(),h.nodes.get("parameter-search"));
  const first=element();h.nodes.get("parameter-matches").querySelector=selector=>selector==="[data-parameter-match-group]"?first:null;
  h.nodes.get("parameter-search").value="压力";h.nodes.get("parameter-search").oninput();
  keydown({key:"ArrowDown",preventDefault(){prevented=true;}});
  assert.equal(prevented,true);assert.equal(first.focusCount,1);
});

test("invalid inputs navigate to their owning group and field without using the finder query",async()=>{
  const h=harness(()=>{throw Error("Validation failure must precede all writes and calculations");}),fixture=parameterFinderFixture();
  fixture.inputs.gas_eos="Peng-Robinson";seedParameterFinder(h,fixture);
  h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"conditional_input"},value:""}});
  const dom=mountFinderFields(h,fixture.groups);await h.app.chooseView("solver_diagnostics");
  h.nodes.get("parameter-search").value="上次查询";const before=h.app.state();
  assert.throws(()=>h.app.validateDraft(),/条件参数必须是有限数值/);
  assert.equal(h.nodes.get("parameter-group").value,"chamber_chemistry");
  assert.equal(h.nodes.get("analysis-title").textContent,"工质与燃烧模型");
  assert.equal(h.nodes.get("parameter-search").value,"上次查询");assert.notEqual(h.nodes.get("parameter-finder").open,true);
  assert.equal(dom.controls.get("conditional_input").focusCount,1);assert.ok(dom.folds.every(fold=>fold.open));
  assert.equal(dom.rows.get("conditional_input").classList.contains("parameter-located"),true);
  assert.deepEqual(h.app.state().draft,before.draft);assert.equal(h.app.state().dirty,true);
});

test("a previous finder query does not prevent confirming all parameters in the selected group",async()=>{
  const storage=reviewStorage(),h=reviewPage(storage);await h.check();
  h.nodes.get("find-parameter").onclick();h.nodes.get("parameter-search").value="燃烧室压力";h.nodes.get("parameter-search").oninput();
  h.nodes.get("close-parameter-finder").onclick();await confirmGroup(h.nodes,"design");
  assert.ok(storage.project.design_reviews.design.signature);
  await assertRunGate(h,false);
  assert.deepEqual(copy(h.app.state().draft),defaults);assert.equal(h.app.state().dirty,false);
});

test("unapplied channel edits block saving and running, and cannot leak across imported projects",async()=>{
  const calls=[];
  const {app,nodes}=harness(async(path)=>{calls.push(path);return response({});});
  seedChannelEditor(app);
  app.editCoolingChannel({dataset:{coolingChannelAction:"mode"},value:"table"});
  assert.equal(app.state().dirty,true);
  assert.equal(app.state().draft.cooling_channel_table_json,"");
  await assert.rejects(app.saveProject(),/未应用/);
  await app.chooseView("design_run");
  await assert.rejects(app.runDesign(),/未应用/);
  assert.equal(calls.length,0);
  app.applyInputDocument({inputs:{...defaults,cooling_channel_table_json:""}});
  assert.doesNotThrow(()=>app.validateDraft());
  assert.equal(nodes.get("editor-error").textContent,"");
});

test("channel table application is explicit and normal save preserves the complete JSON",async()=>{
  const raw=JSON.stringify({version:1,rows:[{anchor:"throat",n:300,w_mm:1.5,h_mm:8,t_mm:.5}]});
  const calls=[];
  const {app}=harness(async(path,options)=>{
    calls.push({path,body:options.body&&JSON.parse(options.body)});
    return response({project:{...project,revision:2,inputs:JSON.parse(options.body).inputs},warnings:[]});
  });
  seedChannelEditor(app);
  app.applyInputDocument({inputs:{...defaults,cooling_channel_table_json:raw}});
  app.editCoolingChannel({dataset:{coolingChannelAction:"edit",coolingChannelRow:"0",coolingChannelKey:"h_mm"},value:"7"});
  assert.equal(app.state().draft.cooling_channel_table_json,raw);
  await assert.rejects(app.saveProject(),/未应用/);
  app.editCoolingChannel({dataset:{coolingChannelAction:"apply"}});
  assert.equal(JSON.parse(app.state().draft.cooling_channel_table_json).rows[0].h_mm,7);
  await app.saveProject();
  assert.equal(JSON.parse(calls[0].body.inputs.cooling_channel_table_json).rows[0].h_mm,7);
  assert.equal(app.state().dirty,false);
});

test("absolute roughness input converts micrometres to SI and explicit zero remains active",()=>{
  const {app,nodes}=harness(()=>{throw new Error("unexpected request");});
  const rough={key:"cooling_channel_roughness_m",default:null,type:"number",nullable:true,factor:1e6,label:"ks"};
  app.seed(copy({current:project,draft:{...defaults,cooling_channel_roughness_m:null},dirty:false,user,
    info:{...info,ui_schema:{...schema,defaults:{...defaults,cooling_channel_roughness_m:null}}},
    groups:[{id:"chamber_channels",fields:[rough]}],fields:[rough],page:"project",projectJobs:[],connected:true}));
  const target={dataset:{input:rough.key},value:"12"};
  nodes.get("parameter-fields").oninput({target});
  assert.equal(app.state().draft.cooling_channel_roughness_m,12e-6);
  target.value="0";nodes.get("parameter-fields").oninput({target});
  assert.equal(app.state().draft.cooling_channel_roughness_m,0);
  target.value="";nodes.get("parameter-fields").oninput({target});
  assert.equal(app.state().draft.cooling_channel_roughness_m,null);
});

function confirmGroup(nodes,id){
  const button={dataset:{reviewDesignGroup:id}};
  return nodes.get("parameter-fields").listeners.get("click")({target:{closest:selector=>selector==="[data-review-design-group]"?button:null}});
}

test("adopting the baseline invalidates model confirmation and keeps stored inputs and other confirmations",async()=>{
  // Only the dataset differs, so this exercises the hidden configuration dependency.
  const inputs={...defaults,propellant_pair:"lox_methane",gas_eos:"ideal-gas",gas_species_policy:"full",methane_thermo_dataset:"gri30",combustion_loss_model:"adiabatic"};
  const modelFields=["gas_eos","gas_species_policy","combustion_loss_model"].map(key=>({key,type:"text",label:key,default:inputs[key]}));
  const modelGroup={id:"chamber_chemistry",title:"燃烧热化学与燃气物性",fields:modelFields,configuration_context:[{key:"methane_thermo_dataset"}]};
  const groups=[schema.groups[0],modelGroup],allFields=[...fields,...modelFields];
  const storage=reviewStorage({...project,inputs});
  const h=reviewPage(storage,{groups,fields:allFields,extraInfo:{ui_schema:{version:20,defaults:inputs,groups}}});
  await h.check();await confirmGroup(h.nodes,"design");await confirmGroup(h.nodes,"chamber_chemistry");
  assert(h.app.state().groupReviews.some(([id])=>id==="chamber_chemistry"));
  const before=copy(storage.project);
  h.nodes.get("parameter-fields").listeners.get("click")({target:{closest:selector=>selector==="[data-thermochemistry-baseline]"?{}:null}});
  const state=h.app.state();
  assert.equal(state.draft.methane_thermo_dataset,"gri30_highT");
  assert.equal(state.dirty,true);assert.deepEqual(copy(state.current.inputs),inputs);
  assert(!state.groupReviews.some(([id])=>id==="chamber_chemistry"));
  assert(state.groupReviews.some(([id])=>id==="design"));
  assert.deepEqual(storage.project,before);
});

// Models revision-scoped server storage, independent of each page's memory.
function reviewStorage(initial=project){
  let saved=copy(initial);
  const requests=[];
  return {requests,get project(){return copy(saved);},async fetch(url,options){
    requests.push([url,options.method]);
    const payload=options.body?JSON.parse(options.body):null;
    if(url===`/api/projects/${saved.id}`&&options.method==="PUT"){
      if(payload.revision!==saved.revision)return response({detail:"方案已更新，请重新载入。"},409);
      saved={...saved,...payload,revision:saved.revision+1};
      return response({project:saved,warnings:[]});
    }
    if(url===`/api/projects/${saved.id}/design-reviews`&&options.method==="PUT"){
      if(payload.revision!==saved.revision)return response({detail:"方案已更新，请重新载入。"},409);
      saved={...saved,design_reviews:{...saved.design_reviews,...payload.design_reviews}};
      return response({project:saved});
    }
    throw new Error(`Unexpected review storage request ${url}`);
  }};
}

function reviewPage(storage,{groups=schema.groups,fields:fieldDefinitions=fields,extraInfo={},fetcher=null}={}){
  const checks=new Map();let timer=0;
  const h=harness(fetcher||((url,options)=>url.endsWith("/design-readiness")?
    response({valid:true,issues:[]}):storage.fetch(url,options)),{
    timers:{setTimeout(callback,delay){const id=++timer;if(delay===300)checks.set(id,callback);return id;},
      clearTimeout:id=>checks.delete(id)},
  });
  h.app.seed(copy({current:storage.project,draft:storage.project.inputs,dirty:false,user,
    info:{...info,design_readiness:true,design_review_persistence:true,...extraInfo},
    groups,fields:fieldDefinitions,page:"project",projectJobs:[],connected:true}));
  h.app.fillProject(storage.project);
  return {...h,async check(){
    for(const [id,callback] of [...checks]){checks.delete(id);await callback();}
  }};
}

test("supply confirmation follows SI effective values and ignores inactive common settings",async()=>{
  for(const independent of [false,true]){
    const legacy={key:"tank_pressure_pa",type:"number",default:425000,label:"共用压力",factor:1e-6,hidden:true};
    const perSide=["fuel","ox"].map(side=>({key:`${side}_tank_pressure_pa`,label:side,type:"number",default:null,
      factor:1e-6,nullable:true,fallback_key:"tank_pressure_pa",show_effective_value:true,role:"boundary"}));
    const inputs={...defaults,propellant_pair:"lox_methane",tank_pressure_pa:425000,
      fuel_tank_pressure_pa:independent?425000:null,ox_tank_pressure_pa:independent?425000:null};
    const groups=[{id:"inlet",title:"推进剂供给条件",fields:[legacy,...perSide]}];
    const fixture={version:27,defaults:inputs,groups};
    const storage=reviewStorage({...project,inputs});
    const h=reviewPage(storage,{groups,fields:[...fields,legacy,...perSide],extraInfo:{ui_schema:fixture}});
    await h.check();await confirmGroup(h.nodes,"inlet");
    assert.ok(h.app.state().groupReviews.some(([id])=>id==="inlet"));
    const signature=storage.project.design_reviews.inlet.signature;
    editField(h,"tank_pressure_pa","0.6");await h.check();
    assert.equal(h.app.state().groupReviews.some(([id])=>id==="inlet"),independent);
    await h.app.saveProject();
    if(independent){
      assert.equal(storage.project.design_reviews.inlet.signature,signature);
      const restored=reviewPage(storage,{groups,fields:[...fields,legacy,...perSide],extraInfo:{ui_schema:fixture}});
      await restored.check();assert.ok(restored.app.state().groupReviews.some(([id])=>id==="inlet"));
    }else assert.equal(storage.project.design_reviews.inlet,undefined);
  }
});

function environmentFixture(ambientPressure=101325){
  const inputs={...defaults,ambient_pressure:ambientPressure,target_isp:321};
  const definitions=[...fields,
    {key:"ambient_pressure",label:"环境压力",type:"number",default:101325,factor:1e-6,role:"boundary"},
    {key:"target_isp",label:"比冲初值",type:"number",default:321,factor:1,role:"initial_guess",inline_help:"仅用于初始化"},
  ];
  const groups=[{id:"design",title:"设计目标与工况",primary_initials:["target_isp"],fields:definitions}];
  return {inputs,fields:definitions,groups,schema:{version:20,defaults:{...inputs,ambient_pressure:101325},groups,system_groups:groups}};
}
function seedEnvironment(h,fixture,saved={...project,inputs:fixture.inputs}){
  h.app.seed(copy({current:saved,draft:saved.inputs,dirty:false,user,
    info:{...info,ui_schema:fixture.schema},groups:fixture.groups,fields:fixture.fields,page:"project",projectJobs:[],connected:true}));
  h.app.fillProject(saved);
}
function environmentControls(host){
  const label=element(),buttons=["sea_level","vacuum"].map(mode=>{const button=element();button.dataset.designEnvironment=mode;return button;});
  const previousQuery=host.querySelectorAll;
  host.querySelectorAll=selector=>selector==="[data-design-environment-label]"?[label]:
    selector==="[data-design-environment]"?buttons:previousQuery(selector);
  host.contains=node=>buttons.includes(node);
  return {label,buttons,async click(mode){
    const button=buttons.find(node=>node.dataset.designEnvironment===mode);
    assert.ok(button);
    return host.listeners.get("click")({target:{closest:selector=>selector.includes("data-design-environment")?button:null},preventDefault(){}});
  }};
}

test("loading historical custom pressure and vacuum retains exact values without dirtying the scheme",()=>{
  for(const [pressure,key] of [[100000,"custom"],[0,"vacuum"]]){
    const fixture=environmentFixture(pressure),saved={...project,inputs:fixture.inputs},before=copy(saved);
    const h=harness(()=>{throw new Error("loading a scheme unexpectedly requested data");});
    seedEnvironment(h,fixture,saved);
    assert.equal(h.app.state().draft.ambient_pressure,pressure);assert.equal(h.app.state().dirty,false);
    assert.equal(parameterEditor.designEnvironment(h.app.state().draft).key,key);
    assert.deepEqual(copy(h.app.state().draft),fixture.inputs);assert.deepEqual(saved,before);
    assert.match(h.nodes.get("parameter-fields").innerHTML,new RegExp(`data-input="ambient_pressure"[^>]*value="${pressure===0?"0":"0\\.1"}"`));
  }
});

test("environment shortcuts change only the pressure draft and retain the selected historical result",async()=>{
  const fixture=environmentFixture(100000),record={id:"environment-source",project_id:project.id,revision:1,status:"succeeded",inputs:fixture.inputs};
  const server=navigationServer({records:[record]}),h=harness(server.fetcher);
  seedEnvironment(h,fixture);await h.app.loadRecord(record.id);
  const controls=environmentControls(h.nodes.get("parameter-fields")),before=h.app.state(),requests=server.calls.length;
  for(const [mode,pressure] of [["sea_level",101325],["vacuum",0]]){
    await controls.click(mode);
    assert.deepEqual(copy(h.app.state().draft),{...fixture.inputs,ambient_pressure:pressure});
    assert.equal(h.app.state().draft.thrust,fixture.inputs.thrust);assert.equal(h.app.state().draft.target_isp,321);
    assert.equal(h.app.state().dirty,true);assert.equal(h.app.state().selectedJobId,record.id);
    assert.deepEqual(h.app.state().resultRecord,before.resultRecord);
    assert.equal(h.app.currentWorkflow().freshness.state,"stale");
    assert.deepEqual(Object.keys(h.app.state().draft).sort(),Object.keys(fixture.inputs).sort());
  }
  assert.equal(server.calls.length,requests);
});

test("manual environment edits refresh status without rebuilding the focused form and blank remains invalid",()=>{
  const fixture=environmentFixture(),h=harness(()=>{throw new Error("unexpected request");});seedEnvironment(h,fixture);
  const host=h.nodes.get("parameter-fields"),controls=environmentControls(host);
  let markup=host.innerHTML,writes=0;
  Object.defineProperty(host,"innerHTML",{get(){return markup;},set(value){markup=value;writes++;}});
  for(const [value,pressure,key] of [["0.1",100000,"custom"],["",null,"invalid"],["0",0,"vacuum"]]){
    host.oninput({target:{dataset:{input:"ambient_pressure"},value}});
    if(pressure===null)assert.equal(h.app.state().draft.ambient_pressure,null);
    else assert.ok(Math.abs(h.app.state().draft.ambient_pressure-pressure)<1e-9);
    assert.equal(controls.label.textContent,parameterEditor.designEnvironment({ambient_pressure:pressure}).label);
    for(const button of controls.buttons)assert.equal(button.getAttribute("aria-pressed"),String(button.dataset.designEnvironment===key));
    assert.equal(h.app.state().draft.target_isp,321);assert.equal(writes,0);
  }
});

test("environment shortcuts obey busy, archive and pressure-editability guards",async()=>{
  for(const guard of ["busy","archive","readonly","missing"]){
    const fixture=environmentFixture();
    if(guard==="readonly")fixture.fields.find(field=>field.key==="ambient_pressure").read_only=true;
    if(guard==="missing")fixture.groups[0].fields=fixture.fields=fixture.fields.filter(field=>field.key!=="ambient_pressure");
    const saved={...project,inputs:{...fixture.inputs,...(guard==="archive"?{cycle_type:"gg"}:{})}},h=harness(()=>{throw new Error("guard issued a request");});
    seedEnvironment(h,fixture,saved);
    const controls=environmentControls(h.nodes.get("parameter-fields")),before=h.app.state();
    if(guard==="busy")await h.app.withEditorLock(()=>controls.click("vacuum"));
    else await controls.click("vacuum");
    assert.deepEqual(h.app.state().draft,before.draft,guard);assert.equal(h.app.state().dirty,false,guard);
  }
});

test("environment confirmation survives a no-op shortcut and expires only when its pressure changes",async()=>{
  const fixture=environmentFixture(),storage=reviewStorage({...project,inputs:fixture.inputs});
  const h=reviewPage(storage,{groups:fixture.groups,fields:fixture.fields,extraInfo:{ui_schema:fixture.schema}});
  await h.check();await confirmGroup(h.nodes,"design");
  await assertRunGate(h,false);
  const controls=environmentControls(h.nodes.get("parameter-fields")),signature=storage.project.design_reviews.design.signature,requests=storage.requests.length;
  await controls.click("sea_level");
  assert.equal(h.app.state().dirty,false);await assertRunGate(h,false);
  assert.equal(storage.requests.length,requests);
  await controls.click("vacuum");await h.check();
  await assertRunGate(h,true);
  assert.match(h.nodes.get("design-run-readiness").innerHTML,/待确认/);
  await confirmGroup(h.nodes,"design");
  assert.equal(storage.project.inputs.ambient_pressure,0);assert.equal(storage.project.inputs.target_isp,321);
  assert.notEqual(storage.project.design_reviews.design.signature,signature);
  const restored=reviewPage(storage,{groups:fixture.groups,fields:fixture.fields,extraInfo:{ui_schema:fixture.schema}});await restored.check();
  assert.equal(restored.app.state().draft.ambient_pressure,0);await assertRunGate(restored,false);
});

function pressureEditorFixture(cycle="ffsc"){
  const ratios={preburner_injector_dp_ratio:0.12,ox_turbine_exhaust_dp_ratio:0.243,
    fuel_turbine_exhaust_dp_ratio:0.093,valve_line_dp_ratio:0.03};
  const overrideKeys=["fuel_to_opb","fuel_to_fpb","ox_to_opb","ox_to_fpb"]
    .flatMap(branch=>[`${branch}_line_dp_ratio`,`${branch}_injector_dp_ratio`]);
  const pressureFields=Object.entries(ratios).map(([key,value])=>({key,default:value,label:key,type:"number",factor:1}));
  const overrides=overrideKeys.map(key=>({key,default:null,label:key,type:"number",factor:1,nullable:true,
    fallback_key:key.includes("_line_")?"valve_line_dp_ratio":"preburner_injector_dp_ratio"}));
  const designFields=[...fields,{key:"propellant_pair",default:"lox_methane",label:"推进剂组合",factor:1}];
  const canonical=[...designFields,...pressureFields,...overrides];
  const values={...defaults,cycle_type:cycle,propellant_pair:"lox_methane",...ratios,
    ...Object.fromEntries(overrideKeys.map(key=>[key,null]))};
  const groups=[{id:"design",title:"设计目标",fields:designFields},
    {id:"pressure_ffsc",title:"压力分配与压降预算",cycles:["ffsc"],fields:[...pressureFields,...overrides]
      .map(field=>({...field,factor:100,unit:"%",cycles:["ffsc"]}))}];
  return {fields:canonical,groups,overrideKeys,inputs:values,
    schema:{version:16,defaults:values,groups:[{id:"canonical",fields:canonical}],system_groups:groups}};
}

for(const cycle of ["ffsc"])test(`pressure field visibility, display and saved ratio follow the ${cycle} editor group`,async()=>{
  const fixture=pressureEditorFixture(cycle),storage=reviewStorage({...project,inputs:fixture.inputs});
  const h=harness((url,options)=>storage.fetch(url,options));
  h.app.seed(copy({current:storage.project,draft:fixture.inputs,dirty:false,user,
    info:{...info,ui_schema:fixture.schema},groups:fixture.groups,fields:fixture.fields,
    page:"project",projectJobs:[],connected:true}));
  h.app.fillProject(storage.project);
  const key="valve_line_dp_ratio",groupId=cycle==="ffsc"?"pressure_ffsc":"pressure";
  assert.match(h.nodes.get("parameter-fields").innerHTML,
    new RegExp(`data-input="${key}" value="${cycle==="ffsc"?"3":"0\\.03"}"`));
  const row=element(),group=element();row.dataset.field=key;group.dataset.group=groupId;
  group.querySelectorAll=selector=>selector==="[data-field]"?[row]:[];
  h.nodes.get("parameter-fields").querySelectorAll=selector=>selector==="[data-group]"?[group]:[];
  h.nodes.get("parameter-group").value=groupId;h.nodes.get("parameter-group").onchange();
  assert.equal(row.hidden,false,"the active cycle's pressure field must remain visible");
  assert.equal(group.hidden,false);
  h.nodes.get("parameter-fields").oninput({target:{dataset:{input:key},value:cycle==="ffsc"?"4":"0.04"}});
  assert.equal(h.app.state().draft[key],0.04,"input uses the active group's display factor");
  await h.app.saveProject();
  assert.equal(storage.project.inputs[key],0.04,"the stored ratio is dimensionless in both cycles");
});

test("both legacy pressure confirmations migrate, persist and restore without changing saved budgets",async()=>{
  const fixture=pressureEditorFixture(),inputs={...fixture.inputs};
  for(const key of fixture.overrideKeys)delete inputs[key];
  const signature=keys=>JSON.stringify([16,[inputs.cycle_type,inputs.propellant_pair,null],keys.map(key=>[key,inputs[key]])]);
  const digest=async value=>"sha256:"+Array.from(new Uint8Array(await webcrypto.subtle.digest("SHA-256",new TextEncoder().encode(value))))
    .map(byte=>byte.toString(16).padStart(2,"0")).join("");
  const reviews={design:{kind:"confirmed",signature:await digest(signature(fixture.groups[0].fields.map(field=>field.key)))},
    pressure_ffsc:{kind:"confirmed",signature:await digest(signature(["preburner_injector_dp_ratio","ox_turbine_exhaust_dp_ratio","fuel_turbine_exhaust_dp_ratio"]))},
    pressure:{kind:"preset",signature:await digest(signature(["valve_line_dp_ratio"]))}};
  const storage=reviewStorage({...project,inputs,design_reviews:reviews});
  const options={groups:fixture.groups,fields:fixture.fields,extraInfo:{ui_schema:fixture.schema}};
  const first=reviewPage(storage,options);await first.check();
  await assertRunGate(first,false,"both old confirmations cover the inherited merged group");
  assert.equal(storage.requests.length,0,"opening the old revision must not rewrite it");
  for(const key of fixture.overrideKeys)assert.equal(first.app.state().draft[key],null);
  await first.app.saveProject();
  assert.deepEqual(Object.keys(storage.project.design_reviews).sort(),["design","pressure_ffsc"]);
  assert.equal(storage.project.design_reviews.design.signature,reviews.design.signature,"unrelated confirmation survives unchanged");
  assert.equal(storage.project.design_reviews.pressure_ffsc.kind,"confirmed");
  assert.notEqual(storage.project.design_reviews.pressure_ffsc.signature,reviews.pressure_ffsc.signature);
  for(const [key,value] of Object.entries(inputs))assert.deepEqual(storage.project.inputs[key],value);
  for(const key of fixture.overrideKeys)assert.equal(storage.project.inputs[key],null);
  const refreshed=reviewPage(storage,options);await refreshed.check();
  await assertRunGate(refreshed,false);
  assert.match(refreshed.nodes.get("design-run-readiness").innerHTML,/压力分配与压降预算<\/button><span[^>]+data-status="confirmed"/);
  assert.equal(storage.requests.filter(([,method])=>method==="PUT").length,1);

  for(const invalidation of ["changed_shared_ratio","explicit_branch_ratio"]){
    const changed={...inputs,...(invalidation==="changed_shared_ratio"?{valve_line_dp_ratio:0.04}:{ox_to_opb_line_dp_ratio:0.03})};
    const invalid=reviewPage(reviewStorage({...project,inputs:changed,design_reviews:reviews}),options);await invalid.check();
    await assertRunGate(invalid,true,invalidation);
    assert.match(invalid.nodes.get("design-run-readiness").innerHTML,/1 组参数待确认/);
    assert.match(invalid.nodes.get("design-run-readiness").innerHTML,/设计目标<\/button><span[^>]+data-status="confirmed"/);
  }
});

test("confirmed groups survive a new page instance and still require fresh input validation",async()=>{
  const storage=reviewStorage(),first=reviewPage(storage);
  await first.check();
  await assertRunGate(first,true);
  await confirmGroup(first.nodes,"design");
  await assertRunGate(first,false);
  assert.equal(storage.project.revision,1,"confirmation alone must not create a parameter revision");
  assert.equal(storage.project.design_reviews.design.kind,"confirmed");
  const refreshed=reviewPage(storage);
  await assertRunGate(refreshed,true,"saved confirmation cannot bypass validation");
  await refreshed.check();
  assert.match(refreshed.nodes.get("design-run-readiness").innerHTML,/已确认/);
  await assertRunGate(refreshed,false);
  assert.equal(storage.requests.filter(([,method])=>method==="PUT").length,1);
  const other=reviewPage(reviewStorage({...project,id:"p2"}));
  await other.check();
  assert.match(other.nodes.get("design-run-readiness").innerHTML,/待确认/);
});

test("read-only cooling methods do not require a saved review or invalidate editable confirmations after refresh",async()=>{
  const cooling={id:"cooling",title:"再生冷却计算方法",fields:[{key:"cooling_bartz_mode",label:"当前计算方法",read_only:true,default:1}]};
  const groups=[...schema.groups,cooling],storage=reviewStorage({...project,inputs:{...defaults,cooling_bartz_mode:1}});
  const h=reviewPage(storage,{groups});await h.check();
  assert.match(h.nodes.get("design-run-readiness").innerHTML,/1 组参数待确认/);
  await confirmGroup(h.nodes,"cooling");
  assert.equal(storage.requests.length,0,"methods-only group cannot write confirmation metadata");
  await confirmGroup(h.nodes,"design");
  await assertRunGate(h,false);
  const refreshed=reviewPage(storage,{groups});
  await assertRunGate(refreshed,true,"refresh still requires fresh input validation");
  await refreshed.check();
  await assertRunGate(refreshed,false);
  assert.match(refreshed.nodes.get("design-run-readiness").innerHTML,/data-status="readonly">只读说明/);
  assert.match(refreshed.nodes.get("design-run-readiness").innerHTML,/1 个适用分组已就绪/);
  assert.deepEqual(Object.keys(storage.project.design_reviews),["design"]);
});

test("cooling method links navigate to existing geometry and material groups without modifying the draft",async()=>{
  const extra=["chamber_contour","chamber_channels","wall_thermal"].map(id=>({id,title:id,fields:[{key:"pc",label:"test"}]}));
  const storage=reviewStorage(),h=reviewPage(storage,{groups:[...schema.groups,...extra]});
  const before=h.app.state();
  for(const id of extra.map(group=>group.id)){
    const button={dataset:{readinessGroup:id}};
    h.nodes.get("parameter-search").value="旧搜索";
    h.nodes.get("parameter-fields").listeners.get("click")({target:{closest:selector=>selector==="[data-readiness-group]"?button:null}});
    assert.equal(h.nodes.get("parameter-group").value,id);
    assert.equal(h.nodes.get("parameter-search").value,"旧搜索");
    assert.equal(h.app.state().conceptSection,"settings");
    assert.deepEqual(h.app.state().draft,before.draft);
    assert.equal(h.app.state().dirty,before.dirty);
  }
  assert.equal(storage.requests.length,0);
});

test("changed group stays unconfirmed after save and refresh while unchanged groups remain confirmed",async()=>{
  const groups=[{id:"design",title:"设计目标",fields:fields.filter(f=>f.key!=="pc")},
    {id:"pressure",title:"压力设定",fields:fields.filter(f=>f.key==="pc")}];
  const storage=reviewStorage(),h=reviewPage(storage,{groups});
  await h.check();await confirmGroup(h.nodes,"design");await confirmGroup(h.nodes,"pressure");
  h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"pc"},value:"12"}});
  await h.check();await h.app.saveProject();
  assert.deepEqual(Object.keys(storage.project.design_reviews),["design"]);
  const refreshed=reviewPage(storage,{groups});await refreshed.check();
  assert.match(refreshed.nodes.get("design-run-readiness").innerHTML,/1 组参数待确认/);
  assert.equal(refreshed.app.state().draft.pc,12e6);
  await confirmGroup(refreshed.nodes,"pressure");
  await assertRunGate(refreshed,false);
  assert.equal(storage.project.revision,2);
});

test("confirming an edited draft saves its exact values and review together",async()=>{
  const storage=reviewStorage(),h=reviewPage(storage);await h.check();
  h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"pc"},value:"12"}});
  await h.check();await confirmGroup(h.nodes,"design");
  assert.equal(storage.project.inputs.pc,12e6);
  assert.equal(storage.project.revision,2);
  assert.equal(h.app.state().dirty,false);
  assert.match(storage.project.design_reviews.design.signature,/^sha256:[a-f0-9]{64}$/);
  const refreshed=reviewPage(storage);await refreshed.check();
  assert.equal(refreshed.app.state().draft.pc,12e6);
  await assertRunGate(refreshed,false);
});

test("failed confirmation preserves the draft and never marks it saved or confirmed",async()=>{
  for(const dirty of [false,true]){
    const storage=reviewStorage();let writes=0;
    const h=reviewPage(storage,{fetcher:async(url,options)=>{
      if(url.endsWith("/design-readiness"))return response({valid:true,issues:[]});
      writes++;return response({detail:"方案版本已更新，请重新载入。"},409);
    }});await h.check();
    if(dirty){h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"pc"},value:"12"}});await h.check();}
    await confirmGroup(h.nodes,"design");
    assert.equal(writes,1);
    assert.equal(h.app.state().dirty,dirty);
    assert.equal(h.app.state().draft.pc,dirty?12e6:defaults.pc);
    await assertRunGate(h,true);
    assert.match(h.nodes.get("editor-error").textContent,/版本已更新/);
    assert.equal(storage.project.design_reviews,undefined);
    assert.equal(h.app.state().editorBusy,false);
  }
});

test("browsing examples preserves saved adoption while schema changes invalidate old confirmations",async()=>{
  const storage=reviewStorage(),h=reviewPage(storage);await h.check();
  selectModel(h,"test");await h.app.saveProject();
  const adopted=reviewPage(storage);await adopted.check();
  assert.match(adopted.nodes.get("design-run-readiness").innerHTML,/采用示例/);
  const before=adopted.app.state();
  browseModel(adopted,"");
  assert.deepEqual(adopted.app.state(),before,"browsing changes neither inputs nor review evidence");
  assert.match(adopted.nodes.get("design-run-readiness").innerHTML,/采用示例/);
  await confirmGroup(adopted.nodes,"design");
  const changedSchema=reviewPage(storage,{extraInfo:{ui_schema:{...schema,version:999}}});
  await changedSchema.check();
  await assertRunGate(changedSchema,true);
  assert.match(changedSchema.nodes.get("design-run-readiness").innerHTML,/待确认/);
});

test("an older server explicitly reports unsupported confirmation persistence",async()=>{
  const storage=reviewStorage(),h=reviewPage(storage,{extraInfo:{design_review_persistence:false}});
  await h.check();await confirmGroup(h.nodes,"design");
  assert.match(h.nodes.get("editor-error").textContent,/重新启动工作台服务/);
  await assertRunGate(h,true);
  assert.equal(storage.requests.length,0);
});

test("confirmation rejects a response from a newer revision without replacing the draft",async()=>{
  const storage=reviewStorage(),h=reviewPage(storage,{fetcher:async url=>{
    if(url.endsWith("/design-readiness"))return response({valid:true,issues:[]});
    return response({project:{...storage.project,revision:2,inputs:{...defaults,pc:20e6}}});
  }});await h.check();await confirmGroup(h.nodes,"design");
  assert.equal(h.app.state().current.revision,1);
  assert.equal(h.app.state().draft.pc,defaults.pc);
  await assertRunGate(h,true);
  assert.match(h.nodes.get("editor-error").textContent,/版本已更新/);
});

test("large table values appear only once in a save while confirmation uses a compact digest",async()=>{
  const inputs={...defaults,table_json:"x".repeat(120000)};
  const largeGroup={id:"table",title:"数据表",fields:[{key:"table_json",default:"",label:"数据表"}]};
  const storage=reviewStorage({...project,inputs});let size=0;
  const h=reviewPage(storage,{groups:[...schema.groups,largeGroup],fetcher:async(url,options)=>{
    if(url.endsWith("/design-readiness"))return response({valid:true,issues:[]});
    size=Math.max(size,new TextEncoder().encode(options.body).length);
    return storage.fetch(url,options);
  }});await h.check();
  h.nodes.get("project-description").value="数据表测试";h.nodes.get("project-description").oninput();
  await confirmGroup(h.nodes,"table");
  assert.ok(size<125000,`Review metadata must not duplicate the table: ${size} bytes`);
  assert.equal(storage.project.design_reviews.table.signature.length,71);
  const refreshed=reviewPage(storage,{groups:[...schema.groups,largeGroup]});await refreshed.check();
  assert.match(refreshed.nodes.get("design-run-readiness").innerHTML,/数据表<\/button><span[^>]+data-status="confirmed"/);
});

test("same-user reauthentication restores saved reviews without reviving an unconfirmed custom draft",async()=>{
  const storage=reviewStorage(),first=reviewPage(storage);await first.check();await confirmGroup(first.nodes,"design");
  for(const custom of [false,true]){
    const h=reviewPage(storage,{fetcher:async(url,options)=>{
      if(url.endsWith("/design-readiness"))return response({valid:true,issues:[]});
      if(url==="/api/auth/status")return response({initialized:true,user});
      if(url==="/api/info")return response({...info,design_readiness:true,design_review_persistence:true});
      if(url==="/api/projects/p1/revisions"||url==="/api/jobs?project_id=p1")return response([]);
      if(url==="/api/expired")return response({detail:"登录已失效"},401);
      return storage.fetch(url,options);
    }});await h.check();
    if(custom){h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"pc"},value:"12"}});}
    await assert.rejects(h.app.api("/expired"));
    await h.app.initialize();await h.check();
    await assertRunGate(h,custom);
    assert.equal(h.app.state().dirty,custom);
  }
});

test("design-point readiness blocks missing inputs and ignores results for an older draft", async()=>{
  const checks=new Map(),requests=[],storage=reviewStorage();
  let timer=0;
  const h=harness((url,options)=>{
    if(url==="/api/projects/p1"&&options.method==="PUT")return storage.fetch(url,options);
    assert.equal(url,"/api/projects/p1/design-readiness");
    assert.equal(options.method,"POST");
    return new Promise(resolve=>requests.push({resolve,inputs:JSON.parse(options.body).inputs}));
  },{timers:{setTimeout(callback,delay){const id=++timer;if(delay===300)checks.set(id,callback);return id;},clearTimeout:id=>checks.delete(id)}});
  h.app.seed(copy({current:project,draft:defaults,dirty:false,user,info:{...info,design_readiness:true,design_review_persistence:true},
    groups:schema.groups,fields,page:"project",projectJobs:[],connected:true}));
  h.app.fillProject(copy(project));
  const edit=value=>h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"pc"},value}});
  const startCheck=()=>{const [id,callback]=[...checks][0];checks.delete(id);return callback();};
  await assertRunGate(h,true);
  const original=startCheck();
  assert.equal(requests[0].inputs.pc,10100000);
  edit("");
  await assertRunGate(h,true);
  assert.equal(checks.size,0);
  assert.match(h.nodes.get("design-run-readiness").innerHTML,/燃烧室压力/);
  await h.app.chooseView("design_run");
  await assert.rejects(h.app.runDesign(),/修正输入检查/);
  edit("-2");
  const edited=startCheck();
  requests[1].resolve(response({valid:false,issues:[{fields:["pc"],message:"燃烧室压力必须大于 0。"}]}));
  await edited;
  requests[0].resolve(response({valid:true,issues:[]}));
  await original;
  await assertRunGate(h,true);
  assert.match(h.nodes.get("design-run-readiness").innerHTML,/燃烧室压力必须大于 0/);
  edit("10.1");
  const corrected=startCheck();
  requests[2].resolve(response({valid:true,issues:[]}));
  await corrected;
  await assertRunGate(h,true);
  assert.match(h.nodes.get("design-run-readiness").innerHTML,/待确认/);
  await confirmGroup(h.nodes,"design");
  await assertRunGate(h,false);
  assert.match(h.nodes.get("design-run-readiness").innerHTML,/已确认/);
});

test("imported inputs require group confirmation while browsing examples does not alter them",async()=>{
  const checks=new Map(),storage=reviewStorage();let timer=0,submittedRevision=null;
  const groups=[{id:"design",title:"设计目标",fields:fields.filter(field=>field.key!=="pc")},
    {id:"pressure",title:"压力设定",fields:fields.filter(field=>field.key==="pc")}];
  const h=harness(async (url,options)=>{
    if(url==="/api/projects/p1/design-readiness")return response({valid:true,issues:[]});
    if(options.method==="PUT")return storage.fetch(url,options);
    if(url==="/api/projects/p1/jobs"){
      submittedRevision=JSON.parse(options.body).revision;
      return response({id:"reviewed-job"});
    }
    if(url==="/api/jobs?project_id=p1"||url==="/api/projects/p1/revisions")return response([]);
    throw new Error(`Unexpected request ${url}`);
  },{timers:{setTimeout(callback,delay){const id=++timer;if(delay===300)checks.set(id,callback);return id;},clearTimeout:id=>checks.delete(id)}});
  h.app.seed(copy({current:project,draft:defaults,dirty:false,user,info:{...info,design_readiness:true,design_review_persistence:true},
    groups,fields,page:"project",projectJobs:[],connected:true}));
  const panel=h.nodes.get("design-run-readiness"),select=h.nodes.get("project-preset");
  const finishCheck=async()=>{const [id,callback]=[...checks][0];checks.delete(id);await callback();};
  select.value="test";select.onchange();h.nodes.get("apply-preset").onclick();
  await finishCheck();
  await assertRunGate(h,false);
  assert.match(panel.innerHTML,/采用示例/);
  const previous=h.app.state().draft;
  select.value="";select.onchange();
  assert.deepEqual(h.app.state().draft,previous);
  assert.equal(checks.size,0);
  await assertRunGate(h,false);
  h.app.applyInputDocument({inputs:copy(previous)});if(checks.size)await finishCheck();
  await assertRunGate(h,true);
  await h.app.chooseView("design_run");
  await assert.rejects(h.app.runDesign(),/2 个分组待确认/);
  await confirmGroup(h.nodes,"design");
  await assertRunGate(h,true);
  await confirmGroup(h.nodes,"pressure");
  await assertRunGate(h,false);
  h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"pc"},value:"12"}});
  await finishCheck();
  await assertRunGate(h,true);
  await h.app.chooseView("design_run");
  await assert.rejects(h.app.runDesign(),/1 个分组待确认/);
  assert.match(panel.innerHTML,/设计目标<\/button><span[^>]+data-status="confirmed"/);
  await confirmGroup(h.nodes,"pressure");
  await assertRunGate(h,false);
  await h.app.chooseView("design_run");
  await h.app.runDesign();
  assert.equal(submittedRevision,3);
  assert.equal(h.app.state().analysisView,"design_monitor","submitted work opens the live monitor page");
  assert.equal(storage.project.inputs.pc,12e6);
});

test("the help manual renders the shared parameter identity catalog without changing the draft",async()=>{
  const h=harness(async()=>{throw Error("Mode explanation must not submit a calculation");});
  const catalog={rows:[{id:"valve_opening",label:"阀门开度",concept_design:{description:"设计参考"},fixed_hardware_steady:{description:"运行设定，可调节"}}]};
  h.app.seed(copy({current:project,draft:defaults,dirty:false,user,info:{...info,ui_schema:{...schema,analysis_parameter_roles:catalog}},
    groups:schema.groups,fields,page:"project",projectJobs:[],connected:true}));
  await h.app.chooseView("overview");
  assert.equal(h.nodes.has("concept-parameter-roles"),false);
  assert.equal(h.nodes.get("concept-settings-host").hidden,false);
  h.app.openHelp("modes");
  const content=h.nodes.get("help-content").innerHTML;
  assert.match(content,/<summary>分析模式参数对照<\/summary>/);
  assert.match(content,/概念设计 · 当前模式/);
  assert.match(content,/阀门开度/);assert.match(content,/运行设定，可调节/);
  assert.equal(h.nodes.get("workbench-content").hidden,true);
  assert.deepEqual(copy(h.app.state().draft),defaults);
  await h.app.chooseView("states");assert.doesNotMatch(h.nodes.get("analysis-content").innerHTML,/分析模式参数对照/);
  assert.equal(h.nodes.get("concept-settings-host").hidden,true);
  assert.equal(h.nodes.get("concept-settings-host").inert,true);
});

test("access gateway redirects and plain denials preserve drafts and never replay login writes",async()=>{
  for(const [status,type,contentType] of [[0,"opaqueredirect",""],[302,"basic","text/html"],[307,"basic","text/html"],[401,"basic","text/plain"],[403,"basic","text/html"]]){
    const calls=[];
    const h=harness(async(url,options)=>{
      calls.push({url,options});
      return {...response(null,status),type,headers:new Headers({"Content-Type":contentType}),
        json:()=>{throw new Error("Gateway content must not be read as JSON");}};
    });
    h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"thrust"},value:"975"}});
    const before=h.app.state();
    await assert.rejects(h.app.api("/auth/login","POST",{username:"test",password:"test-only"}),error=>{
      assert.equal(error.code,"access_authorization");assert.equal(error.status,status);
      assert.match(error.message,/同一浏览器窗口.*平台首页.*访问验证/);
      assert.match(error.message,/验证后立即出现.*管理员/);return true;
    });
    assert.equal(calls.length,1);assert.equal(calls[0].url,"/api/auth/login");
    assert.equal(calls[0].options.redirect,"manual");assert.equal(calls[0].options.credentials,"same-origin");
    assert.deepEqual(h.app.state().draft,before.draft);assert.equal(h.app.state().dirty,true);
    assert.deepEqual(h.app.state().user,before.user);
    assert.equal(h.app.state().connected,false);
  }
});

test("JSON authentication and permission failures retain their application meaning",async()=>{
  for(const status of [401,403]){
    const message=status===401?"用户名或密码不正确。":"没有该操作的权限。";
    const h=harness(async()=>response({detail:message},status));
    const before=h.app.state();
    await assert.rejects(h.app.api("/auth/login","POST",{}),error=>{
      assert.equal(error.message,message);assert.equal(error.status,status);assert.equal(error.code,undefined);return true;
    });
    assert.equal(h.app.state().connected,true);
    assert.deepEqual(h.app.state().draft,before.draft);
    assert.deepEqual(h.app.state().user,status===401?null:before.user);
  }
});

test("non-JSON proxy and invalid JSON responses report safe metadata without rendering their bodies",async()=>{
  for(const [status,contentType,invalidJSON] of [[502,"text/html; charset=utf-8",false],[200,"text/html",false],[200,"application/json",true],[503,"<script>private-content</script>",false]]){
    let readBody=false;
    const h=harness(async()=>({...response(null,status),headers:new Headers({"Content-Type":contentType}),
      json:async()=>{readBody=true;throw new SyntaxError("<script>secret-error-body</script>");}}));
    await assert.rejects(h.app.api("/health"),error=>{
      assert.equal(error.code,"response_format");assert.match(error.message,new RegExp(`HTTP ${status}`));
      assert.doesNotMatch(error.message,/script|private-content|secret-error-body|验证邮箱/);
      assert.match(error.message,status>=500?/计算服务或访问入口返回异常/:/格式异常/);return true;
    });
    assert.equal(readBody,invalidJSON);assert.equal(h.app.state().connected,false);
  }
});

test("API timeout includes reading the response body and never retries a possibly accepted save",async()=>{
  let deadline,cleared=false,calls=0,bodyStarted;
  const reading=new Promise(resolve=>{bodyStarted=resolve;});
  const h=harness(async(url,options)=>{
    calls++;
    return {...response({}),json:()=>new Promise((resolve,reject)=>{
      assert.equal(cleared,false,"Deadline must still protect body reading");
      options.signal.addEventListener("abort",()=>{const error=new Error("aborted");error.name="AbortError";reject(error);},{once:true});
      bodyStarted();
    })};
  },{timers:{setTimeout(callback,ms){assert.equal(ms,15000);deadline=callback;return 123;},clearTimeout(id){assert.equal(id,123);cleared=true;}}});
  const before=h.app.state(),pending=h.app.api("/projects/p1","PUT",{});
  const rejected=assert.rejects(pending,/服务响应超时.*保存或提交可能已完成.*检查记录/);
  await reading;deadline();await rejected;
  assert.equal(calls,1);assert.equal(cleared,true);assert.equal(h.app.state().connected,false);
  assert.deepEqual(h.app.state().draft,before.draft);
});

test("browser pilot labels ownership and sends visitors to the administrator on disconnect",async()=>{
  for(const role of ["member","admin"]){
    let offline=false;
    const account={...user,role};
    const h=harness(async url=>{
      if(offline)throw new TypeError("network down");
      if(url==="/api/auth/status")return response({initialized:true,user:account,remote:true,workspace_access:"private"});
      if(url==="/api/info")return response({...info,scope:"external-pilot",workspace_access:"private"});
      if(url==="/api/projects"||url==="/api/jobs")return response([]);
      throw new Error(url);
    });
    h.app.reset();await h.app.initialize();
    assert.equal(h.nodes.get("projects-heading").textContent,role==="admin"?"试用项目":"我的项目");
    assert.equal(h.nodes.get("members-nav").hidden,role!=="admin");
    assert.equal(h.nodes.get("members-heading").textContent,"校外试用 · 成员管理");
    assert.match(h.nodes.get("auth-storage-note").textContent,/保存在平台主机/);
    assert.match(h.nodes.get("members-description").textContent,/仅使用自己的/);
    assert.equal(h.nodes.get("workspace-location").textContent,"校外试用");
    offline=true;
    await assert.rejects(h.app.api("/health"),/联系平台管理员/);
    assert.doesNotMatch(h.nodes.get("connection-message").textContent,/start_web|本机/);
  }
});

test("pilot login describes server storage before authentication",async()=>{
  const h=harness(async()=>response({initialized:true,user:null,remote:true,workspace_access:"private"}));
  h.app.reset();await h.app.initialize();
  assert.equal(h.nodes.get("auth").hidden,false);
  assert.equal(h.nodes.get("display-label").hidden,true);
  assert.match(h.nodes.get("auth-scope-note").textContent,/浏览器访问/);
  assert.match(h.nodes.get("auth-storage-note").textContent,/仅访问自己的项目/);
});

function passwordDialog(h,{current="existing-test-password",next="replacement-test-password",confirm=next}={}){
  h.nodes.get("change-password").onclick();
  h.nodes.get("password-current").value=current;
  h.nodes.get("password-new").value=next;
  h.nodes.get("password-confirm").value=confirm;
  return ()=>h.nodes.get("password-form").onsubmit({preventDefault(){}});
}

test("password mismatch and length errors cannot submit or discard an edited design",async()=>{
  for(const [settings,message] of [[{confirm:"different-test-password"},/两次输入/],[{next:""},/新密码不能为空/],[{next:"x".repeat(129)},/最多为 128/],[{current:""},/当前密码/]]){
    const h=harness(async()=>{throw new Error("Invalid password input must not send a request");});
    h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"thrust"},value:"975"}});
    const before=h.app.state(),submit=passwordDialog(h,settings);
    await submit();
    assert.match(h.nodes.get("password-error").textContent,message);
    assert.equal(h.confirmations(),0);assert.deepEqual(h.app.state(),before);
    assert.equal(h.nodes.get("password-dialog").open,true);
    assert.equal(h.nodes.get("password-submit").disabled,false);
  }
});

test("one-character and eleven-character new passwords can be submitted",async()=>{
  for(const next of ["q","qa-password"]){
    const calls=[];
    const h=harness(async(url,options)=>{calls.push({url,body:JSON.parse(options.body)});return response({ok:true});});
    await passwordDialog(h,{next})();
    assert.deepEqual(calls,[{url:"/api/auth/change-password",body:{current_password:"existing-test-password",new_password:next}}]);
    assert.equal(h.nodes.get("password-new").value,"");
  }
});

test("declining to discard a dirty draft cancels password change and closing clears password fields",async()=>{
  let calls=0;
  const h=harness(async()=>{calls++;return response({ok:true});},{confirmResult:false});
  h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"thrust"},value:"975"}});
  const before=h.app.state(),submit=passwordDialog(h);
  await submit();
  assert.equal(h.confirmations(),1);assert.equal(calls,0);assert.deepEqual(h.app.state(),before);
  h.nodes.get("password-cancel").onclick();
  assert.equal(h.nodes.get("password-dialog").open,false);
  for(const id of ["password-current","password-new","password-confirm"])assert.equal(h.nodes.get(id).value,"");
});

test("successful own-password change submits only the contract and clears credentials and workspace state",async()=>{
  const calls=[];
  const h=harness(async(url,options)=>{
    calls.push({url,method:options.method,body:options.body&&JSON.parse(options.body)});
    if(url==="/api/jobs/saved")return response({id:"saved",project_id:"p1",result:{calculation:{summary:{temperature:900}}}});
    assert.equal(url,"/api/auth/change-password");return response({ok:true});
  });
  await h.app.loadRecord("saved");
  h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"thrust"},value:"975"}});
  h.nodes.get("auth-password").value="stale-test-password";
  const submit=passwordDialog(h);await submit();
  assert.equal(h.confirmations(),1);
  assert.deepEqual(calls.at(-1),{url:"/api/auth/change-password",method:"POST",body:{current_password:"existing-test-password",new_password:"replacement-test-password"}});
  assert.equal(calls.length,2);
  const state=h.app.state();
  for(const key of ["user","current","draft","resultRecord","selectedJobId"])assert.equal(state[key],null);
  assert.equal(state.dirty,false);assert.equal(state.editorBusy,false);assert.equal(state.projectJobs.length,0);
  for(const id of ["password-current","password-new","password-confirm","auth-password"])assert.equal(h.nodes.get(id).value,"");
  assert.equal(h.nodes.get("password-dialog").open,false);
  assert.equal(h.nodes.get("auth").hidden,false);assert.equal(h.nodes.get("workspace").hidden,true);
  assert.match(h.nodes.get("auth-description").textContent,/密码已修改.*新密码重新登录/);
});

test("rejected password changes retain user and dirty draft including on an older backend",async()=>{
  for(const [status,message] of [[403,"当前密码不正确。"],[404,"Not Found"]]){
    let calls=0;
    const h=harness(async()=>{calls++;return response({detail:message},status);});
    h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"thrust"},value:"975"}});
    const before=h.app.state(),submit=passwordDialog(h);await submit();
    assert.equal(calls,1);assert.deepEqual(h.app.state(),before);
    assert.match(h.nodes.get("password-error").textContent,status===404?/当前服务版本暂不支持.*管理员更新/:/当前密码不正确/);
    assert.equal(h.nodes.get("password-dialog").open,true);assert.equal(h.nodes.get("password-submit").disabled,false);
    for(const id of ["password-current","password-new","password-confirm","password-cancel"])assert.equal(h.nodes.get(id).disabled,false);
  }
});

test("pending password change cannot be duplicated or dismissed until its result is known",async()=>{
  let finish,calls=0;
  const h=harness(()=>{calls++;return new Promise(resolve=>{finish=resolve;});});
  const submit=passwordDialog(h),pending=submit();
  assert.equal(calls,1);assert.equal(h.nodes.get("password-submit").disabled,true);
  await submit();h.nodes.get("password-cancel").onclick();
  let cancelled=false;
  h.nodes.get("password-dialog").listeners.get("cancel")({preventDefault(){cancelled=true;}});
  assert.equal(cancelled,true);assert.equal(calls,1);assert.equal(h.nodes.get("password-dialog").open,true);
  assert.equal(h.nodes.get("password-submit").disabled,true);
  finish(response({detail:"当前密码不正确。"},403));await pending;
  assert.equal(h.nodes.get("password-submit").disabled,false);
  h.nodes.get("password-close").onclick();
  for(const id of ["password-current","password-new","password-confirm"])assert.equal(h.nodes.get(id).value,"");
});

test("switching records clears previous quantities and a late response cannot overwrite the selected record",async()=>{
  const pending=new Map();
  const h=harness(url=>new Promise(resolve=>pending.set(url,resolve)));
  const old=h.app.loadRecord("old");
  pending.get("/api/jobs/old")(response({id:"old",project_id:"p1",result:{calculation:{summary:{temperature:500}}}}));await old;
  const slow=h.app.loadRecord("slow");
  assert.equal(h.app.state().resultRecord,null);
  assert.match(h.nodes.get("result-context").textContent,/正在读取/);
  const selected=h.app.loadRecord("selected");
  pending.get("/api/jobs/selected")(response({id:"selected",project_id:"p1",result:{calculation:{summary:{temperature:800}}}}));await selected;
  pending.get("/api/jobs/slow")(response({id:"slow",project_id:"p1",result:{calculation:{summary:{temperature:600}}}}));await slow;
  assert.equal(h.app.state().resultRecord.id,"selected");
  assert.equal(h.app.state().resultRecord.result.calculation.summary.temperature,800);
});

test("failed record selection leaves previous values cleared and explains the failed load",async()=>{
  let fail=false;
  const h=harness(async()=>{if(fail)throw new Error("offline");return response({id:"old",project_id:"p1",result:{calculation:{}}});});
  await h.app.loadRecord("old");fail=true;
  await assert.rejects(h.app.loadRecord("new"),/offline|服务/);
  assert.equal(h.app.state().resultRecord,null);
  assert.match(h.nodes.get("result-context").textContent,/读取失败/);
});

function navigationServer({account=user,projects=[project],records=[],uiInfo=info}={}) {
  const calls=[];
  const fetcher=async(url,options)=>{
    calls.push(url);assert.equal(options.method,"GET","Navigation must never save or run a calculation");
    if(url==="/api/auth/status")return response({initialized:true,user:account});
    if(url==="/api/info")return response(uiInfo);
    if(url==="/api/projects")return response(projects);
    if(url==="/api/members")return response([]);
    if(url==="/api/jobs")return response(records);
    const projectDetail=projects.find(p=>url===`/api/projects/${p.id}`);
    if(projectDetail)return response(projectDetail);
    if(projects.some(p=>url===`/api/projects/${p.id}/revisions`))return response([]);
    const projectJobs=projects.find(p=>url===`/api/jobs?project_id=${p.id}`);
    if(projectJobs)return response(records.filter(r=>r.project_id===projectJobs.id));
    const record=records.find(r=>url===`/api/jobs/${r.id}`);
    if(record)return response(record);
    throw new Error(`Unexpected navigation request ${url}`);
  };
  return {fetcher,calls};
}

test("personal workspace blocks saved and direct member navigation without requesting member data",async()=>{
  for(const projectId of [null,project.id]){
    const storage=memoryStorage(),server=navigationServer({uiInfo:{...info,workspace_access:"personal"}});
    const key=`lre.workspace.navigation.v1:${user.id}`;
    storage.setItem(key,JSON.stringify({version:1,page:"members",projectId,view:"overview"}));
    const h=harness(server.fetcher,{storage});h.app.reset();
    h.nodes.get("members-list").innerHTML="old member list";
    await h.app.initialize();
    assert.equal(h.app.state().page,"projects");assert.equal(h.nodes.get("members-nav").hidden,true);
    assert.equal(h.nodes.get("members-page").hidden,true);assert.equal(h.nodes.get("members-list").innerHTML,"");
    assert.equal(h.nodes.get("projects-heading").textContent,"我的项目");
    assert.equal(h.nodes.get("projects-nav-label").textContent,"我的项目");
    assert.equal(h.nodes.get("document-title").textContent,"个人工作区");
    assert.equal(h.nodes.get("sidebar-location").textContent,"个人工作区");
    assert.match(h.nodes.get("user-label").textContent,/个人账号/);
    assert.match(h.nodes.get("auth-storage-note").textContent,/仅供本人使用.*本机/);
    assert.doesNotMatch(h.nodes.get("projects-description").textContent,/共享|成员/);
    assert.equal(JSON.parse(storage.getItem(key)).page,"projects");
    await h.app.navigate("members");h.app.switchPage("members");await h.app.refreshMembers();
    h.nodes.get("member-form").onsubmit({preventDefault(){},target:h.nodes.get("member-form")});
    assert.equal(h.app.state().page,"projects");
    assert.ok(!server.calls.some(url=>url==="/api/members"));
  }
});

test("personal workspace member navigation preserves the existing unsaved-draft guard",async()=>{
  const server=navigationServer({uiInfo:{...info,workspace_access:"personal"}}),h=harness(server.fetcher,{confirmResult:false});
  h.app.reset();await h.app.initialize();
  h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"thrust"},value:"975"}});
  const before=h.app.state(),requests=server.calls.length;
  await h.app.navigate("members");
  assert.equal(h.confirmations(),1);assert.deepEqual(h.app.state(),before);
  assert.equal(h.app.state().dirty,true);assert.equal(server.calls.length,requests);
});

test("personal login and first setup describe a personal account without member sharing",async()=>{
  for(const initialized of [true,false]){
    const h=harness(async()=>response({initialized,user:null,remote:false,workspace_access:"personal"}));
    h.app.reset();await h.app.initialize();
    assert.equal(h.nodes.get("auth-scope-note").textContent,"个人工作区 · 本机使用");
    assert.match(h.nodes.get("auth-storage-note").textContent,/仅供本人使用/);
    assert.doesNotMatch(h.nodes.get("auth-storage-note").textContent,/共享|成员|管理员/);
    assert.equal(h.nodes.get("members-nav").hidden,true);
    if(!initialized){assert.match(h.nodes.get("auth-description").textContent,/创建个人账号/);assert.match(h.nodes.get("auth-submit").textContent,/创建个人账号/);}
  }
});

test("private pilot administrators retain member management and its saved page",async()=>{
  const storage=memoryStorage(),server=navigationServer({uiInfo:{...info,workspace_access:"private",scope:"external-pilot"}});
  storage.setItem(`lre.workspace.navigation.v1:${user.id}`,JSON.stringify({version:1,page:"members",projectId:null,view:"overview"}));
  const h=harness(server.fetcher,{storage});h.app.reset();await h.app.initialize();
  assert.equal(h.app.state().page,"members");assert.equal(h.nodes.get("members-nav").hidden,false);
  assert.equal(h.nodes.get("members-page").hidden,false);assert.equal(h.nodes.get("members-heading").textContent,"校外试用 · 成员管理");
  assert.equal(server.calls.filter(url=>url==="/api/members").length,1);
  assert.equal(h.nodes.get("projects-heading").textContent,"试用项目");
});

const navigationViews=["project_overview","overview","engineering_examples","design_run","results_overview","solver_diagnostics","topology","states","components","geometry","nozzle_design","pump_geometry","injector_design","turbine_geometry","log","history","shutdown","ignition","chilldown","feed","validation","transient","throttle","operating_throttle","fixed_steady","model_library","property_library","steady_operations","transient_operations","guide"];

test("legacy concept section links enter the appropriate stage and preserve the dirty draft and selected record",async()=>{
  const record={id:"historical-design",project_id:project.id,revision:1,status:"succeeded",created:1,result:{calculation:{inputs:defaults}}};
  const server=navigationServer({records:[record]}),h=harness(server.fetcher);
  await h.app.loadRecord(record.id);
  h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"pc"},value:"12.25"}});
  h.nodes.get("project-name").value="未保存的工作方案";h.nodes.get("project-name").oninput();
  const before=h.app.state(),requestCount=server.calls.length;
  let renderedRecord;
  for(const section of ["settings","results","diagnostics","settings","results"]){
    const rendersBefore=h.analysisRenders.length;
    h.nodes.get(`concept-tab-${section}`).onclick();
    const state=h.app.state(),settings=section==="settings";
    assert.equal(state.analysisView,{settings:"overview",results:"results_overview",diagnostics:"solver_diagnostics"}[section]);assert.equal(state.conceptSection,section);
    assert.deepEqual(state.draft,before.draft);assert.equal(state.dirty,true);
    assert.deepEqual(state.resultRecord,before.resultRecord);assert.equal(state.selectedJobId,record.id);
    assert.equal(h.nodes.get("project-name").value,"未保存的工作方案");
    for(const id of ["concept-settings-host","parameter-pane"]){
      assert.equal(h.nodes.get(id).hidden,!settings,`${section}: ${id} visibility`);
      assert.equal(h.nodes.get(id).inert,!settings,`${section}: ${id} input isolation`);
    }
    assert.equal(h.nodes.get("concept-section-content").hidden,settings);
    assert.equal(h.nodes.get("concept-navigation").hidden,true,"legacy section controls are not the visible stage navigation");
    assert.equal(h.nodes.get("result-toolbar").hidden,settings);
    for(const choice of ["settings","results","diagnostics"]){
      const button=h.nodes.get(`concept-tab-${choice}`);
      assert.equal(button.getAttribute("aria-selected"),String(choice===section));
      assert.equal(button.tabIndex,choice===section?0:-1);
    }
    if(settings){assert.equal(h.analysisRenders.length,rendersBefore);assert.equal(h.nodes.get("analysis-content").innerHTML,"");}
    else{
      const rendered=h.analysisRenders.at(-1);
      assert.equal(rendered.view,section==="results"?"results_overview":"solver_diagnostics");
      if(renderedRecord)assert.equal(rendered.record,renderedRecord,"Tabs must share the original record object");
      renderedRecord=rendered.record;
      assert.equal(h.nodes.get("concept-section-content").getAttribute("aria-labelledby"),"analysis-title");
    }
  }
  assert.equal(server.calls.length,requestCount);assert.equal(h.confirmations(),0);
});

test("direct result and diagnostic routes render the selected record and returning to concept opens inputs",async()=>{
  const record={id:"selected-design",project_id:project.id,revision:1,status:"failed",created:1,result:{calculation:{inputs:defaults}}};
  const server=navigationServer({records:[record]}),h=harness(server.fetcher);
  await h.app.loadRecord(record.id);h.app.chooseConceptSection("diagnostics");
  h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"thrust"},value:"975"}});
  const before=h.app.state(),requestCount=server.calls.length,selectedRecord=h.analysisRenders.at(-1).record;
  for(const view of ["results_overview","solver_diagnostics"]){
    await h.app.chooseView(view);
    assert.equal(h.analysisRenders.at(-1).view,view);assert.equal(h.analysisRenders.at(-1).record,selectedRecord);
    assert.equal(h.nodes.get("concept-navigation").hidden,true);
    assert.equal(h.nodes.get("concept-settings-host").hidden,true);assert.equal(h.nodes.get("concept-settings-host").inert,true);
    assert.equal(h.nodes.get("parameter-pane").hidden,true);assert.equal(h.nodes.get("parameter-pane").inert,true);
    assert.equal(h.nodes.get("concept-section-content").getAttribute("role"),"region");
    assert.equal(h.nodes.get("header-run").hidden,true);assert.equal(h.nodes.get("design-run-submit").disabled,true);
    assert.deepEqual(h.app.state().draft,before.draft);assert.equal(h.app.state().dirty,true);
    assert.equal(h.app.state().selectedJobId,record.id);assert.equal(h.app.state().conceptSection,"diagnostics");
  }
  await h.app.chooseView("overview");
  assert.equal(h.app.state().conceptSection,"settings");
  assert.equal(h.nodes.get("concept-settings-host").hidden,false);
  assert.equal(h.nodes.get("concept-navigation").hidden,true);
  assert.equal(h.analysisRenders.at(-1).view,"solver_diagnostics");assert.equal(h.analysisRenders.at(-1).record,selectedRecord);
  assert.equal(server.calls.length,requestCount);
});

test("the library concept parameter entry opens task settings while preserving a dirty draft and result",async()=>{
  const record={id:"library-design-source",project_id:project.id,revision:1,status:"succeeded",created:1};
  const server=navigationServer({records:[record]}),h=harness(server.fetcher);
  await h.app.loadRecord(record.id);h.app.chooseConceptSection("results");
  h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"pc"},value:"12.25"}});
  await h.app.chooseView("model_library");
  const before=h.app.state(),requestCount=server.calls.length;
  const link={dataset:{workspaceView:"overview"},closest:selector=>selector==="[data-workspace-view]"?link:null};
  const host=h.nodes.get("analysis-content");host.contains=node=>node===link;
  let prevented=false;host.listeners.get("click")({target:link,preventDefault(){prevented=true;}});
  assert.equal(prevented,true);assert.equal(h.app.state().analysisView,"overview");
  assert.equal(h.app.state().conceptSection,"settings");
  assert.equal(h.nodes.get("concept-settings-host").hidden,false);assert.equal(h.nodes.get("concept-settings-host").inert,false);
  assert.equal(h.nodes.get("parameter-pane").hidden,false);assert.equal(h.nodes.get("parameter-pane").inert,false);
  assert.deepEqual(h.app.state().draft,before.draft);assert.equal(h.app.state().dirty,true);
  assert.deepEqual(h.app.state().resultRecord,before.resultRecord);assert.equal(h.app.state().selectedJobId,record.id);
  assert.equal(server.calls.length,requestCount);assert.equal(h.confirmations(),0);
});

test("concept keyboard navigation follows the tabs and ignores invalid sections or a locked editor",async()=>{
  const h=harness(async()=>{throw Error("Tab navigation must not request data");});
  await h.app.chooseView("overview");
  const keydown=h.nodes.get("concept-navigation").listeners.get("keydown");
  for(const [from,key,to] of [["settings","ArrowRight","results"],["results","End","diagnostics"],["diagnostics","ArrowRight","settings"],["settings","ArrowLeft","diagnostics"],["diagnostics","Home","settings"]]){
    let prevented=false;
    keydown({target:h.nodes.get(`concept-tab-${from}`),key,preventDefault(){prevented=true;}});
    assert.equal(prevented,true);assert.equal(h.app.state().conceptSection,to);
    assert.ok(h.nodes.get(`concept-tab-${to}`).focusCount>0);
  }
  const before=h.app.state();
  for(const invalid of ["future",null,{},"constructor"])h.app.chooseConceptSection(invalid);
  assert.deepEqual(h.app.state(),before);
  await h.app.withEditorLock(async()=>{
    for(const section of ["settings","results","diagnostics"])assert.equal(h.nodes.get(`concept-tab-${section}`).disabled,true);
    h.nodes.get("concept-tab-results").onclick();
    keydown({target:h.nodes.get("concept-tab-settings"),key:"End",preventDefault(){throw Error("Locked keyboard input should be ignored");}});
    assert.equal(h.app.state().conceptSection,"settings");
  });
  assert.deepEqual(h.app.state(),before);
});

test("legacy diagnostic links open the appropriate outer page without replacing the selected record",async()=>{
  const record={id:"diagnostic-source",project_id:project.id,revision:1,status:"failed",created:1};
  const server=navigationServer({records:[record]}),h=harness(server.fetcher);
  await h.app.loadRecord(record.id);
  const requestCount=server.calls.length;
  for(const view of ["overview","results_overview"]){
    await h.app.chooseView(view);if(view==="overview")h.app.chooseConceptSection("results");
    const original=h.app.state().resultRecord;
    for(const key of ["steadyOpen","steadyTarget"]){
      const link={dataset:{[key]:"convergence"},closest:selector=>selector==='[data-steady-open="convergence"],[data-steady-target="convergence"]'?link:null};
      const host=h.nodes.get("analysis-content");host.contains=node=>node===link;
      let prevented=false;host.listeners.get("click")({target:link,preventDefault(){prevented=true;}});
      assert.equal(prevented,true);
      assert.equal(h.app.state().analysisView,"solver_diagnostics");
      assert.equal(h.nodes.get("concept-navigation").hidden,true);
      assert.equal(h.analysisRenders.at(-1).view,"solver_diagnostics");
      assert.deepEqual(h.app.state().resultRecord,original);assert.equal(h.app.state().selectedJobId,record.id);
    }
  }
  assert.equal(server.calls.length,requestCount);
});

test("invalid draft validation returns from diagnostics to editable task settings before any request",async()=>{
  const h=harness(async()=>{throw Error("Invalid inputs must not be saved or calculated");});
  h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"pc"},value:""}});
  h.app.chooseConceptSection("diagnostics");
  assert.equal(h.nodes.get("parameter-pane").inert,true);
  await h.app.chooseView("design_run");
  await assert.rejects(h.app.runDesign(),/燃烧室压力必须是有限数值/);
  assert.equal(h.app.state().conceptSection,"settings");
  assert.equal(h.nodes.get("parameter-pane").hidden,false);assert.equal(h.nodes.get("parameter-pane").inert,false);
  assert.equal(h.nodes.get("parameter-search").value,"");
  assert.equal(h.nodes.get("parameter-group").value,"design");
  assert.equal(h.app.state().draft.pc,null);assert.equal(h.app.state().dirty,true);
});

for(const [location,submitterId] of [["results","header-save"],["diagnostics","header-save"],["history","history-save"]])test(`submitting an invalid draft from ${location} opens unlocked task settings without losing its record`,async()=>{
  const record={id:"saved-design",project_id:project.id,revision:1,status:"succeeded",created:1,result:{calculation:{inputs:defaults}}};
  const server=navigationServer({records:[record]}),h=harness(server.fetcher);
  await h.app.loadRecord(record.id);
  h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"pc"},value:""}});
  h.nodes.get("project-name").value="待修正的方案名称";h.nodes.get("project-name").oninput();
  if(location==="history")await h.app.chooseView("history");else h.app.chooseConceptSection(location);
  assert.equal(h.nodes.get("parameter-pane").inert,true);
  const before=h.app.state(),requestCount=server.calls.length;
  let prevented=false;
  await h.nodes.get("project-form").onsubmit({submitter:h.nodes.get(submitterId),preventDefault(){prevented=true;}});
  assert.equal(prevented,true);
  assert.equal(h.app.state().analysisView,"overview");assert.equal(h.app.state().conceptSection,"settings");
  assert.equal(h.nodes.get("concept-settings-host").hidden,false);assert.equal(h.nodes.get("concept-settings-host").inert,false);
  assert.equal(h.nodes.get("parameter-pane").hidden,false);assert.equal(h.nodes.get("parameter-pane").inert,false);
  assert.equal(h.nodes.get("project-form").inert,false);assert.equal(h.app.state().editorBusy,false);
  assert.equal(h.nodes.get("parameter-search").value,"");
  assert.equal(h.nodes.get("parameter-group").value,"design");
  assert.match(h.nodes.get("editor-error").textContent,/燃烧室压力必须是有限数值/);
  assert.equal(h.nodes.get("workspace-error").hidden,false);
  assert.match(h.nodes.get("workspace-error").textContent,/燃烧室压力必须是有限数值/);
  assert.deepEqual(h.app.state().draft,before.draft);assert.equal(h.app.state().draft.pc,null);assert.equal(h.app.state().dirty,true);
  assert.equal(h.nodes.get("project-name").value,"待修正的方案名称");
  assert.deepEqual(h.app.state().resultRecord,before.resultRecord);assert.equal(h.app.state().selectedJobId,record.id);
  assert.equal(h.app.state().current.revision,before.current.revision);
  assert.equal(server.calls.length,requestCount);assert.equal(h.confirmations(),0);
});

for(const section of ["settings","results","diagnostics"])test(`reload restores the ${section} stage and its selected record`,async()=>{
  const record={id:"historical-design",project_id:project.id,revision:1,status:"succeeded",created:1};
  const server=navigationServer({records:[record]}),first=harness(server.fetcher);
  await first.app.loadRecord(record.id);first.app.chooseConceptSection(section);
  const saved=JSON.parse(first.storage.getItem(`lre.workspace.navigation.v1:${user.id}`));
  assert.equal(saved.conceptSection,section);assert.equal(saved.view,{settings:"overview",results:"results_overview",diagnostics:"solver_diagnostics"}[section]);assert.equal(saved.jobId,record.id);
  assert.equal(Object.hasOwn(saved,"draft"),false);assert.equal(Object.hasOwn(saved,"resultRecord"),false);
  const reloaded=harness(server.fetcher,{storage:first.storage});reloaded.app.reset();await reloaded.app.initialize();
  assert.equal(reloaded.app.state().conceptSection,section);assert.equal(reloaded.app.state().selectedJobId,record.id);
  assert.equal(reloaded.app.state().resultRecord.id,record.id);assert.equal(reloaded.app.state().dirty,false);
  assert.equal(reloaded.nodes.get("concept-settings-host").hidden,section!=="settings");
  assert.deepEqual(copy(reloaded.app.state().draft),defaults);
});

test("legacy overview result locations restore as separate design-point stages",async()=>{
  for(const [section,view] of [["results","results_overview"],["diagnostics","solver_diagnostics"]]){
    const storage=memoryStorage(),record={id:"legacy-stage-source",project_id:project.id,revision:1,status:"succeeded",inputs:defaults};
    storage.setItem(`lre.workspace.navigation.v1:${user.id}`,JSON.stringify({version:1,page:"project",projectId:project.id,view:"overview",conceptSection:section,jobId:record.id}));
    const server=navigationServer({records:[record]}),h=harness(server.fetcher,{storage});
    h.app.reset();await h.app.initialize();
    assert.equal(h.app.state().analysisView,view);assert.equal(h.nodes.get("concept-navigation").hidden,true);
    assert.equal(h.nodes.get("parameter-pane").hidden,true);assert.equal(h.nodes.get("design-run-panel").hidden,true);
    assert.deepEqual(copy(h.app.state().resultRecord),record);
    assertPrimaryNavigation(h,view,"concept");
  }
});

test("parameter and valve entry points navigate to input review without saving or submitting a job",async()=>{
  for(const [view,buttonId] of [["overview","open-design-run"],["valve_design","valve-open-design-run"]]){
    for(const entry of ["button","direct"]){
      const calls=[],record={id:"navigation-evidence",project_id:project.id,revision:1,status:"succeeded",inputs:defaults};
      const h=harness(async(url,options)=>{
        calls.push([url,options.method]);
        if(url===`/api/jobs/${record.id}`)return response(record);
        throw new Error(`Navigation must not save or calculate: ${url}`);
      });
      seedValveEditor(h.app);await h.app.loadRecord(record.id);
      h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"pc"},value:"12.5"}});
      await h.app.chooseView(view);
      const before=h.app.state(),requestCount=calls.length;
      assert.equal(h.nodes.get("header-run").hidden,true);
      assert.equal(h.nodes.get("header-run").disabled,true);
      assert.equal(h.nodes.get("design-run-submit").disabled,true);
      assert.equal(h.nodes.get(buttonId).disabled,false);
      if(entry==="button")await h.nodes.get(buttonId).onclick();else await h.app.runDesign();
      assert.equal(h.app.state().analysisView,"design_run");
      assert.equal(h.nodes.get("header-run").hidden,false);
      assert.equal(h.nodes.get("design-run-panel").hidden,false);
      assert.equal(h.nodes.get("parameter-pane").hidden,true);
      assert.deepEqual(h.app.state().draft,before.draft);
      assert.deepEqual(h.app.state().current,before.current);
      assert.deepEqual(h.app.state().resultRecord,before.resultRecord);
      assert.equal(h.app.state().selectedJobId,record.id);assert.equal(h.app.state().dirty,true);
      assert.equal(calls.length,requestCount);
      assert.equal(h.confirmations(),0);
    }
  }
  assert.doesNotMatch(html,/id="(?:submit-job|valve-run|run-progress|valve-run-status)"/);
  assert.match(html,/<button id="open-design-run" type="button">前往输入检查<\/button>/);
});

test("confirmed parameter groups still expose navigation instead of an executable run control",async()=>{
  const storage=reviewStorage(),h=reviewPage(storage);await h.check();await confirmGroup(h.nodes,"design");
  await h.app.chooseView("overview");
  assert.equal(h.nodes.get("header-run").hidden,true);assert.equal(h.nodes.get("header-run").disabled,true);
  assert.equal(h.nodes.get("design-run-submit").disabled,true);
  assert.equal(h.nodes.get("open-design-run").disabled,false);
  const writes=storage.requests.length;
  await h.nodes.get("open-design-run").onclick();
  assert.equal(h.app.state().analysisView,"design_run");
  assert.equal(h.nodes.get("design-run-submit").disabled,false);
  assert.equal(h.nodes.get("header-run").hidden,false);
  assert.equal(storage.requests.length,writes);
  await h.app.chooseView("overview");
  h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"pc"},value:""}});
  assert.equal(h.nodes.get("open-design-run").disabled,false,"invalid inputs must not block access to their review");
  await h.nodes.get("open-design-run").onclick();
  assert.equal(h.nodes.get("design-run-submit").disabled,true);
  assert.match(h.nodes.get("design-run-readiness").innerHTML,/燃烧室压力/);
  assert.equal(storage.requests.length,writes);
});

test("parameter status follows the selected group while the run page retains the complete checklist",async()=>{
  const groups=[{id:"design",title:"设计目标",fields:fields.filter(field=>field.key!=="pc")},
    {id:"pressure",title:"压力设定",fields:fields.filter(field=>field.key==="pc")}];
  const storage=reviewStorage(),h=reviewPage(storage,{groups});await h.check();
  h.app.openReadinessField("design");await confirmGroup(h.nodes,"design");
  const local=h.nodes.get("design-readiness"),global=h.nodes.get("design-run-readiness");
  assert.match(local.innerHTML,/本组参数状态/);assert.match(local.innerHTML,/data-status="confirmed"/);
  assert.doesNotMatch(local.innerHTML,/计算前检查|分组状态|压力设定|data-readiness-group="pressure"/);
  assert.match(global.innerHTML,/压力设定/);assert.match(global.innerHTML,/1 组参数待确认/);
  h.app.openReadinessField("pressure");
  assert.match(local.innerHTML,/data-status="review"/);assert.doesNotMatch(local.innerHTML,/data-status="confirmed"/);
  h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"pc"},value:""}});
  assert.match(local.innerHTML,/data-status="invalid"/);assert.match(local.innerHTML,/燃烧室压力/);
  h.app.openReadinessField("design");
  assert.doesNotMatch(local.innerHTML,/燃烧室压力必须|data-readiness-group="pressure"/);
  await h.nodes.get("open-design-run").onclick();
  assert.equal(h.nodes.get("design-run-submit").disabled,true);
  assert.match(global.innerHTML,/燃烧室压力/);assert.match(global.innerHTML,/设计目标/);assert.match(global.innerHTML,/压力设定/);
});

test("confirming changed thermochemistry keeps input confirmation and stale result evidence separate",async()=>{
  const gasField={key:"gas_eos",type:"select",label:"燃气物性模型",default:"Peng-Robinson-Boston-Mathias",factor:1,
    choices:[{value:"ideal-gas",label:"理想气体混合物"},{value:"Peng-Robinson-Boston-Mathias",label:"PR–BM"}]};
  const inputs={...defaults,gas_eos:gasField.default,gas_species_policy:"pr_supported_v1",methane_thermo_dataset:"gri30_highT",combustion_loss_model:"adiabatic"};
  const groups=[schema.groups[0],{id:"chamber_chemistry",title:"燃烧热化学与燃气物性",fields:[gasField],requires_review:true,
    configuration_context:[{key:"methane_thermo_dataset"},{key:"gas_species_policy"},{key:"combustion_loss_model"}]}];
  const oldRecord={id:"before-model-change",project_id:project.id,revision:1,status:"succeeded",inputs,result:{calculation:{inputs,summary:{}}}};
  const newRecord={id:"after-model-change",project_id:project.id,revision:2,status:"succeeded",inputs:{...inputs,gas_eos:"ideal-gas"},result:{}};
  const storage=reviewStorage({...project,inputs}),requests=[];
  const h=reviewPage(storage,{groups,fields:[...fields,gasField],extraInfo:{ui_schema:{version:20,defaults:inputs,groups}},fetcher:async(url,options)=>{
    requests.push([url,options.method]);
    if(url.endsWith("/design-readiness"))return response({valid:true,issues:[]});
    if(url===`/api/jobs/${oldRecord.id}`)return response(oldRecord);
    if(url===`/api/jobs/${newRecord.id}`)return response(newRecord);
    return storage.fetch(url,options);
  }});
  await h.check();await confirmGroup(h.nodes,"design");await confirmGroup(h.nodes,"chamber_chemistry");
  h.app.openReadinessField("chamber_chemistry");await h.app.loadRecord(oldRecord.id);
  const writes=requests.length;
  h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"gas_eos"},value:"ideal-gas"}});
  assert.equal(requests.length,writes,"model adoption is local until explicitly saved");
  assert.equal(h.app.state().dirty,true);
  assert.deepEqual(copy(h.app.state().current.inputs),inputs);
  await h.check();await confirmGroup(h.nodes,"chamber_chemistry");await h.check();
  const local=h.nodes.get("design-readiness"),branch=h.setupBranches.find(node=>node.dataset.setupBranch==="models");
  assert.match(local.innerHTML,/data-status="confirmed">✓ 已确认/);
  assert.match(local.innerHTML,/所选结果需重算/);
  assert.match(local.innerHTML,/本组输入与所选计算记录不同/);
  assert.match(branch.innerHTML,/data-status="confirmed">已确认/);
  assert.doesNotMatch(branch.innerHTML,/data-status="stale"|待更新/);
  assert.equal(h.setupSummaries.find(node=>node.dataset.setupSummary==="models").textContent,"输入已就绪");
  assert.match(h.nodes.get("setup-tree-progress").textContent,/2 组设置 · 输入已就绪/);
  assert.equal(h.app.currentWorkflow().freshness.state,"stale");
  assert.deepEqual(copy(h.app.state().resultRecord),oldRecord);
  assert.equal(storage.project.inputs.gas_eos,"ideal-gas");
  assert.equal(storage.project.inputs.gas_species_policy,"full");
  assert.equal(storage.project.revision,2);
  const refreshed=reviewPage(storage,{groups,fields:[...fields,gasField],extraInfo:{ui_schema:{version:20,defaults:inputs,groups}}});
  await refreshed.check();
  assert.equal(new Map(refreshed.app.state().groupReviews).get("chamber_chemistry").kind,"confirmed");
  const before=h.app.state(),count=requests.length;
  const button={dataset:{workspaceView:"design_run"}};
  await local.onclick({target:{closest:selector=>selector==='[data-workspace-view="design_run"]'?button:null},preventDefault(){}});
  assert.equal(h.app.state().analysisView,"design_run");
  assert.equal(requests.length,count,"the result reminder navigates without submitting or saving");
  assert.deepEqual(h.app.state().draft,before.draft);assert.deepEqual(h.app.state().groupReviews,before.groupReviews);
  assert.deepEqual(h.app.state().resultRecord,before.resultRecord);
  newRecord.inputs=copy(h.app.state().draft);
  h.app.openReadinessField("chamber_chemistry");await h.app.loadRecord(newRecord.id);
  assert.equal(h.app.currentWorkflow().freshness.state,"current");
  assert.match(local.innerHTML,/data-status="confirmed"/);
  assert.doesNotMatch(local.innerHTML,/所选结果需重算/);
  await h.app.loadRecord(oldRecord.id);
  assert.match(local.innerHTML,/data-status="confirmed"/);
  assert.match(local.innerHTML,/所选结果需重算/);
  assert.equal(requests.filter(([url,method])=>url.endsWith("/jobs")&&method==="POST").length,0);
});

test("run review shows the current draft revision and returns to the relevant input or solver settings",async()=>{
  const solver={key:"solver_tolerance",label:"收敛容差",type:"number",default:0.001,factor:1};
  const inputs={...defaults,solver_tolerance:0.001},groups=[...schema.groups,{id:"solver",title:"数值求解",fields:[solver]}];
  const h=harness(async()=>{throw new Error("Opening run settings must not write or submit");});
  h.app.seed(copy({current:{...project,inputs},draft:inputs,dirty:false,user,info:{...info,ui_schema:{...schema,defaults:inputs,groups}},
    groups,fields:[...fields,solver],page:"project",projectJobs:[],connected:true}));
  h.app.fillProject({...project,inputs});h.app.openReadinessField("design");
  h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"pc"},value:"12"}});
  const before=h.app.state();await h.nodes.get("open-design-run").onclick();
  assert.equal(h.nodes.get("design-run-project").textContent,project.name);
  assert.match(h.nodes.get("design-run-version").textContent,/当前草稿.*v1/);
  assert.match(h.nodes.get("design-run-draft-note").textContent,/未保存修改/);
  assert.equal(h.nodes.get("design-run-solver").hidden,false);
  h.nodes.get("design-run-back").onclick();
  assert.equal(h.app.state().analysisView,"overview");assert.equal(h.nodes.get("parameter-group").value,"design");
  await h.nodes.get("open-design-run").onclick();h.nodes.get("design-run-solver").onclick();
  assert.equal(h.app.state().analysisView,"overview");assert.equal(h.nodes.get("parameter-group").value,"solver");
  assert.deepEqual(h.app.state().draft,before.draft);assert.deepEqual(h.app.state().current,before.current);
  assert.equal(h.app.state().dirty,true);
});

test("valve input review shortcut returns to its concept group without changing the draft",async()=>{
  const h=harness(async()=>{throw new Error("Valve settings navigation must not submit data");});
  seedValveEditor(h.app);await h.app.chooseView("valve_design");const before=h.app.state();
  h.nodes.get("valve-readiness").onclick();
  assert.equal(h.app.state().analysisView,"overview");
  assert.equal(h.nodes.get("parameter-group").value,"valve_configuration");
  assert.deepEqual(h.app.state().draft,before.draft);assert.equal(h.app.state().dirty,false);
});

test("run navigation separates input review from result and component stages without changing the draft",async()=>{
  const record={id:"stage-source",project_id:project.id,revision:1,status:"succeeded",inputs:defaults};
  const server=navigationServer({records:[record]}),h=harness(server.fetcher);
  await h.app.loadRecord(record.id);h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"pc"},value:"12.5"}});
  const before=h.app.state(),requests=server.calls.length;
  for(const view of ["design_run","results_overview","pump_geometry","overview","design_run"]){
    await h.app.chooseView(view);
    assert.equal(h.nodes.get("design-run-panel").hidden,view!=="design_run");
    assert.equal(h.nodes.get("concept-settings-host").hidden,view!=="overview");
    assert.equal(h.nodes.get("concept-navigation").hidden,true);
    if(view==="design_run"){
      assert.equal(h.nodes.get("result-toolbar").hidden,true);assert.equal(h.nodes.get("design-run-submit").disabled,false);
      assertPrimaryNavigation(h,view,"concept");
    }
    assert.deepEqual(h.app.state().draft,before.draft);assert.deepEqual(h.app.state().resultRecord,before.resultRecord);
  }
  assert.equal(server.calls.length,requests);assert.equal(h.app.state().dirty,true);
});

test("run page shares input confirmation gates and sends its issue links to concept settings",async()=>{
  const storage=reviewStorage(project),h=reviewPage(storage);await h.check();
  await h.app.chooseView("design_run");
  assert.match(h.nodes.get("design-run-readiness").innerHTML,/待确认/);
  assert.equal(h.nodes.get("design-run-submit").disabled,true);
  await h.nodes.get("design-run-submit").onclick();
  assert.equal(h.app.state().analysisView,"design_run");assert.match(h.nodes.get("design-run-error").textContent,/待确认/);
  const link={dataset:{readinessGroup:"design"}};
  h.nodes.get("design-run-readiness").onclick({target:{closest:selector=>selector==="[data-readiness-group]"?link:null}});
  assert.equal(h.app.state().analysisView,"overview");assert.equal(h.nodes.get("parameter-group").value,"design");
  await confirmGroup(h.nodes,"design");await h.app.chooseView("design_run");
  assert.equal(h.nodes.get("design-run-submit").disabled,false);
  await h.app.withEditorLock(async()=>assert.equal(h.nodes.get("design-run-submit").disabled,true));
  assert.equal(h.nodes.get("design-run-submit").disabled,false);
  h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"pc"},value:""}});
  assert.equal(h.nodes.get("design-run-submit").disabled,true);
  assert.match(h.nodes.get("design-run-readiness").innerHTML,/燃烧室压力/);
  assert.ok(!storage.requests.some(([url])=>url.endsWith("/jobs")),"blocked or navigational actions never submit a calculation");
});

test("failed submission from the run page exposes the error there and preserves previous evidence",async()=>{
  const record={id:"before-submit",project_id:project.id,revision:1,status:"succeeded",inputs:defaults};
  const h=harness(async(url,options)=>{
    if(url===`/api/jobs/${record.id}`)return response(record);
    assert.equal(url,`/api/projects/${project.id}/jobs`);assert.equal(options.method,"POST");
    return response({detail:"服务器拒绝本次运行"},422);
  });
  await h.app.loadRecord(record.id);await h.app.chooseView("design_run");
  await h.nodes.get("design-run-submit").onclick();
  assert.equal(h.app.state().analysisView,"design_run");
  assert.match(h.nodes.get("design-run-error").textContent,/服务器拒绝本次运行/);
  assert.deepEqual(copy(h.app.state().resultRecord),record);
  assert.deepEqual(copy(h.app.state().draft),defaults);
});

test("older navigation storage and invalid tab names fall back while valid library tabs remain restorable",async()=>{
  for(const invalid of [undefined,null,"removed-tab",42,{},["results"]]){
    const storage=memoryStorage(),server=navigationServer();
    storage.setItem(`lre.workspace.navigation.v1:${user.id}`,JSON.stringify({version:1,page:"project",projectId:project.id,view:"overview",conceptSection:invalid,libraryTab:invalid}));
    const h=harness(server.fetcher,{storage});h.app.reset();await h.app.initialize();
    assert.equal(h.app.state().conceptSection,"settings");assert.equal(h.app.state().libraryTab,"components");
    assert.equal(h.nodes.get("concept-settings-host").hidden,false);assert.equal(h.nodes.get("concept-settings-host").inert,false);
    assert.deepEqual(copy(h.app.state().draft),defaults);assert.equal(h.app.state().dirty,false);
  }
  for(const libraryTab of ["components","characteristics","properties","numerics"]){
    const storage=memoryStorage(),server=navigationServer();
    storage.setItem(`lre.workspace.navigation.v1:${user.id}`,JSON.stringify({version:1,page:"project",projectId:project.id,view:"model_library",conceptSection:"results",libraryTab}));
    const h=harness(server.fetcher,{storage});h.app.reset();await h.app.initialize();
    assert.equal(h.app.state().libraryTab,libraryTab);assert.equal(h.app.state().conceptSection,"results");
    assert.equal(JSON.parse(storage.getItem(`lre.workspace.navigation.v1:${user.id}`)).libraryTab,libraryTab);
  }
});

test("component parameter editing returns to task settings and the relevant group without changing a result",async()=>{
  const extraFields=["nozzle","pump","injector","turbine"].map(name=>({key:`${name}_test`,default:1,label:`${name} 参数`,type:"number",factor:1}));
  const componentSchema={engineering_workflow:{components:[
    {id:"chamber",groups:["nozzle_test-geometry"],views:["geometry","nozzle_design"]},
    {id:"pumps",groups:["pump_test-geometry"],views:["pump_geometry"]},
    {id:"injectors",groups:["injector_test-geometry"],views:["injector_design"]},
    {id:"turbines",groups:["turbine_test-geometry"],views:["turbine_geometry"]},
  ]},defaults:{...defaults,...Object.fromEntries(extraFields.map(f=>[f.key,f.default]))},groups:[...schema.groups,
    ...extraFields.map(f=>({id:`${f.key}-geometry`,title:f.label,fields:[f]}))]};
  const record={id:"geometry-source",project_id:project.id,revision:1,status:"succeeded",created:1};
  const server=navigationServer({uiInfo:{...info,ui_schema:componentSchema},records:[record]}),h=harness(server.fetcher);
  h.app.reset();await h.app.initialize();
  h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"pc"},value:"12.25"}});
  const before=h.app.state(),requestCount=server.calls.length;
  for(const [view,prefix] of [["geometry","nozzle"],["nozzle_design","nozzle"],["pump_geometry","pump"],["injector_design","injector"],["turbine_geometry","turbine"]]){
    h.app.chooseConceptSection("diagnostics");await h.app.chooseView(view);
    h.nodes.get("parameter-search").value="unrelated search";
    assert.equal(h.nodes.get("component-workflow-context").hidden,false);
    assert.equal(h.nodes.get("toggle-parameters").textContent,"打开对应设计输入");
    h.nodes.get("toggle-parameters").onclick();
    assert.equal(h.app.state().analysisView,"overview");assert.equal(h.app.state().conceptSection,"settings");
    assert.equal(h.nodes.get("parameter-group").value,`${prefix}_test-geometry`);assert.equal(h.nodes.get("parameter-search").value,"unrelated search");
    assert.equal(h.nodes.get("parameter-group").disabled,false);
    assert.equal(h.nodes.get("parameter-group").focusCount,0,"the hidden internal selector cannot receive editing focus");
    assert.ok(h.nodes.get("analysis-title").focusCount>0,"missing DOM controls fall back to the current settings heading");
    assert.equal(h.nodes.get("parameter-search").focusCount,0,"closed finder input cannot receive editing focus");
    assert.equal(h.nodes.get("parameter-pane").hidden,false);assert.equal(h.nodes.get("parameter-pane").inert,false);
    assert.deepEqual(h.app.state().draft,before.draft);assert.deepEqual(h.app.state().resultRecord,before.resultRecord);
    assert.equal(h.app.state().selectedJobId,record.id);assert.equal(h.app.state().dirty,true);
  }
  assert.equal(server.calls.length,requestCount);assert.equal(h.confirmations(),0);
});

for(const view of ["model_library","steady_operations","transient_operations","guide"])test(`workflow hub ${view} preserves the draft and never offers concept execution`,async()=>{
  const h=harness(async(url)=>{throw new Error(`Read-only hub unexpectedly requested ${url}`);});
  h.nodes.get("project-name").value="未保存的方案名称";
  h.nodes.get("project-name").oninput();
  const before=h.app.state();
  await h.app.chooseView(view);
  const editorContainer=view==="guide"?"workbench-content":"parameter-pane";
  assert.equal(h.nodes.get(editorContainer).hidden,true);
  assert.equal(h.nodes.get(editorContainer).inert,true);
  for(const id of (view==="guide"?["header-run","header-save"]:["header-run","header-save","toggle-parameters","result-toolbar","result-consistency"]))assert.equal(h.nodes.get(id).hidden,true,id);
  assert.equal(h.nodes.get("design-run-submit").disabled,true);
  assert.match(h.nodes.get(view==="guide"?"help-content":"analysis-content").innerHTML,view==="guide"?/help-manual/:/workspace-hub/);
  assert.deepEqual(h.app.state().draft,before.draft);
  assert.equal(h.app.state().dirty,true);
  assert.equal(h.nodes.get("project-name").value,"未保存的方案名称");
  await h.app.chooseView("overview");
  assert.equal(h.nodes.get("header-run").hidden,true);
  assert.equal(h.nodes.get("parameter-pane").inert,false);
  assert.equal(h.app.state().dirty,true);
});

function assertPrimaryNavigation(h,view,group){
  assert.deepEqual(h.navGroups.map(node=>node.dataset.navGroup),["concept","components","integration","steady","transient","results","library","guide"]);
  for(const node of h.navGroups){
    if(node.dataset.navGroup===group)assert.equal(node.open,true);
    assert.equal(node.classList.contains("is-current"),node.dataset.navGroup===group);
  }
  for(const button of h.navButtons){
    assert.equal(button.getAttribute("aria-current"),button.dataset.view===view?"page":"false");
    assert.equal(button.classList.contains("active"),button.dataset.view===view);
  }
}

function clickHelpControl(h,attribute,value=""){
  const button={dataset:{[attribute.replace(/-([a-z])/g,(_,letter)=>letter.toUpperCase())]:value}};
  h.body.listeners.get("click")({preventDefault(){},target:{closest:selector=>selector===`[data-${attribute}]`?button:null}});
}

test("contextual help returns to the original parameter group with draft and reviews intact",async()=>{
  const storage=reviewStorage(),h=reviewPage(storage);await h.check();await confirmGroup(h.nodes,"design");
  h.nodes.get("project-name").value="帮助查阅中的未保存方案";h.nodes.get("project-name").oninput();
  const before=h.app.state(),requests=storage.requests.length;
  assert.ok(before.groupReviews.length>0);assert.equal(before.dirty,true);
  h.nodes.get("parameter-help").onclick();
  assert.equal(h.app.state().analysisView,"guide");assert.equal(h.app.state().helpChapter,"parameter:design");
  assert.equal(h.app.state().helpOrigin.group,"design");
  assert.equal(h.nodes.get("workbench-content").inert,true);
  assert.match(h.nodes.get("help-content").innerHTML,/data-help-current="parameter:design"/);
  clickHelpControl(h,"help-chapter","workflow");clickHelpControl(h,"help-chapter","modes");
  assert.equal(h.app.state().helpOrigin.view,"overview");
  assert.equal(h.nodes.get("parameter-help").hidden,true);
  clickHelpControl(h,"help-return");await new Promise(resolve=>setImmediate(resolve));
  assert.equal(h.app.state().analysisView,"overview");assert.equal(h.nodes.get("parameter-group").value,"design");
  assert.equal(h.nodes.get("parameter-pane").inert,false);assert.equal(h.nodes.get("parameter-help").hidden,false);
  for(const key of ["current","draft","groupReviews","resultRecord","selectedJobId","dirty"])
    assert.deepEqual(h.app.state()[key],before[key],key);
  assert.equal(h.nodes.get("project-name").value,"帮助查阅中的未保存方案");
  assert.equal(storage.requests.length,requests);
});

test("help navigation preserves an unapplied cooling channel table candidate",async()=>{
  const h=ffscHarness();
  h.app.editCoolingChannel({dataset:{coolingChannelAction:"new"}});
  h.app.editCoolingChannel({dataset:{coolingChannelAction:"edit",coolingChannelRow:"0",coolingChannelKey:"w_mm"},value:"2.7"});
  const before=h.app.state();assert.equal(before.coolingChannelEditor.candidate.rows[0].w_mm,2.7);
  h.app.openHelp("theory");clickHelpControl(h,"help-chapter","examples");
  await h.app.returnFromHelp();
  assert.equal(h.app.state().analysisView,before.analysisView);
  assert.deepEqual(h.app.state().coolingChannelEditor,before.coolingChannelEditor);
  assert.deepEqual(h.app.state().draft,before.draft);assert.deepEqual(h.app.state().groupReviews,before.groupReviews);
  assert.equal(h.app.state().dirty,before.dirty);
});

test("help preserves the mounted component study and original DOM until another work view is selected",async()=>{
  const record={id:"turbine-help-source",project_id:project.id,status:"succeeded",inputs:defaults,turbine_design:{available:true}};
  const server=navigationServer({records:[record]}),h=harness(server.fetcher);
  await h.app.loadRecord(record.id);await h.app.chooseView("turbine_geometry");
  assert.deepEqual(h.turbineLifecycle,{mounts:1,cleanups:0});
  const host=h.nodes.get("analysis-content");
  host.innerHTML='<div class="local-study"><input value="尚未提交的初选设置"><p>当前候选方案</p></div>';
  const markup=host.innerHTML,before=h.app.state(),requests=server.calls.length;
  h.app.openHelp("theory");
  assert.equal(h.nodes.get("help-content").hidden,false);
  assert.equal(h.nodes.get("workbench-content").hidden,true);assert.equal(h.nodes.get("workbench-content").inert,true);
  assert.equal(host.innerHTML,markup);assert.deepEqual(h.turbineLifecycle,{mounts:1,cleanups:0});
  clickHelpControl(h,"help-chapter","parameters");
  const search=h.nodes.get("help-search");search.value="推力";
  h.nodes.get("help-content").listeners.get("input")({target:search});
  assert.equal(host.innerHTML,markup);assert.deepEqual(h.turbineLifecycle,{mounts:1,cleanups:0});
  await h.app.returnFromHelp();
  assert.equal(h.app.state().analysisView,"turbine_geometry");
  assert.equal(h.nodes.get("help-content").hidden,true);
  assert.equal(h.nodes.get("workbench-content").hidden,false);assert.equal(h.nodes.get("workbench-content").inert,false);
  assert.equal(host.innerHTML,markup);assert.deepEqual(h.turbineLifecycle,{mounts:1,cleanups:0});
  for(const key of ["draft","current","resultRecord","selectedJobId","dirty"])assert.deepEqual(h.app.state()[key],before[key],key);
  assert.equal(server.calls.length,requests);
  await h.app.chooseView("overview");
  assert.deepEqual(h.turbineLifecycle,{mounts:1,cleanups:1},"leaving the component work page still cleans up its controller");
});

test("help search restores focus and caret after replacing the search input",()=>{
  const h=harness(async()=>{throw Error("Help search must not access the API");});h.app.fillProject(copy(project));
  h.app.openHelp("parameters");const origin=h.app.state().helpOrigin;
  const search=h.nodes.get("help-search");search.value="燃烧室压力";search.focus();search.setSelectionRange(2,4);
  h.nodes.get("help-content").listeners.get("input")({target:search});
  const replaced=h.nodes.get("help-search");assert.notEqual(replaced,search);assert.equal(h.activeElement(),replaced);
  assert.equal(replaced.selectionStart,2);assert.equal(replaced.selectionEnd,4);assert.equal(replaced.value,"燃烧室压力");
  assert.equal(h.app.state().helpQuery,"燃烧室压力");
  assert.match(h.nodes.get("help-content").innerHTML,/help-search-results/);
  assert.match(h.nodes.get("help-content").innerHTML,/data-help-chapter="parameter:design"/);
  clickHelpControl(h,"help-clear");assert.equal(h.app.state().helpQuery,"");
  assert.equal(h.activeElement(),h.nodes.get("help-search"));
  clickHelpControl(h,"help-chapter","parameter:design");
  assert.equal(h.app.state().helpChapter,"parameter:design");assert.deepEqual(h.app.state().helpOrigin,origin);
  assert.equal(h.activeElement(),h.nodes.get("help-article-title"));
});

test("help search keeps the composition input mounted until the Chinese text is committed",()=>{
  const h=harness(async()=>{throw Error("Help search must not access the API");});h.app.fillProject(copy(project));
  h.app.openHelp("parameters");
  const host=h.nodes.get("help-content"),search=h.nodes.get("help-search"),initialMarkup=host.innerHTML;
  search.focus();host.listeners.get("compositionstart")({target:search});
  for(const value of ["ranshao","燃烧室压力"]){
    search.value=value;host.listeners.get("input")({target:search,isComposing:true});
    assert.equal(h.nodes.get("help-search"),search);assert.equal(h.activeElement(),search);
    assert.equal(host.innerHTML,initialMarkup,"IME input must not trigger an intermediate DOM replacement");
  }
  search.setSelectionRange(5,5);host.listeners.get("compositionend")({target:search});
  const committed=h.nodes.get("help-search");assert.notEqual(committed,search);
  assert.equal(h.app.state().helpQuery,"燃烧室压力");assert.equal(committed.value,"燃烧室压力");
  assert.equal(h.activeElement(),committed);assert.equal(committed.selectionStart,5);assert.equal(committed.selectionEnd,5);
  assert.match(host.innerHTML,/help-search-results/);
});

test("help is available without a project and survives reload without creating a case",async()=>{
  const server=navigationServer({projects:[]}),h=harness(server.fetcher);h.app.reset();await h.app.initialize();
  assert.equal(h.app.state().current,null);assert.equal(h.app.state().page,"projects");
  const requests=server.calls.length;
  for(const chapter of ["workflow","theory","parameters","modes","examples","troubleshooting"]){
    assert.ok(h.helpButtons.some(button=>button.dataset.helpChapter===chapter));
    clickHelpControl(h,"help-chapter",chapter);
    assert.equal(h.app.state().analysisView,"guide");assert.equal(h.app.state().helpChapter,chapter);
    assert.equal(h.app.state().page,"project");assert.equal(h.app.state().current,null);
    assert.match(h.nodes.get("help-content").innerHTML,/help-manual/);
    assert.equal(h.nodes.get("header-run").hidden,true);assert.equal(h.nodes.get("design-run-submit").disabled,true);
  }
  assert.equal(server.calls.length,requests);
  const reloaded=harness(server.fetcher,{storage:h.storage});reloaded.app.reset();await reloaded.app.initialize();
  assert.equal(reloaded.app.state().current,null);assert.equal(reloaded.app.state().analysisView,"guide");
  assert.equal(reloaded.app.state().helpChapter,"troubleshooting");
  await h.app.returnFromHelp();assert.equal(h.app.state().page,"projects");assert.equal(h.app.state().current,null);
});

test("work routes opened from projectless help require an existing or explicitly created project",async()=>{
  for(const projectAvailable of [false,true]){
    const projects=[],server=navigationServer({projects}),h=harness(server.fetcher);h.app.reset();await h.app.initialize();
    h.app.openHelp("workflow");
    assert.equal(h.app.state().page,"project");assert.equal(h.app.state().current,null);
    if(projectAvailable)projects.push(copy(project));
    const before=server.calls.length;
    await h.navButtons.find(button=>button.dataset.view==="design_run").onclick();
    assert.ok(server.calls.slice(before).includes("/api/projects"),"work navigation must resolve a project even from the help workspace");
    if(projectAvailable){
      assert.equal(h.app.state().current.id,project.id);assert.equal(h.app.state().analysisView,"design_run");
      assert.equal(Boolean(h.nodes.get("new-dialog").open),false);
      assert.deepEqual(copy(h.app.state().draft),defaults);
    }else{
      assert.equal(h.app.state().current,null);assert.equal(h.app.state().analysisView,"guide");
      assert.equal(h.nodes.get("new-dialog").open,true);
      assert.equal(h.nodes.get("workbench-content").hidden,true);
    }
  }
});

test("help chapter memory restores parameter references and rejects unavailable chapters",async()=>{
  const server=navigationServer({uiInfo:parameterNavigationInfo}),first=harness(server.fetcher);
  first.app.reset();await first.app.initialize();
  first.nodes.get("parameter-group").value="design";first.nodes.get("parameter-group").onchange();
  await first.app.chooseView("overview");
  first.app.openHelp();assert.equal(first.app.state().helpChapter,"parameter:design");
  const reloaded=harness(server.fetcher,{storage:first.storage});reloaded.app.reset();await reloaded.app.initialize();
  assert.equal(reloaded.app.state().analysisView,"guide");assert.equal(reloaded.app.state().helpChapter,"parameter:design");
  assert.equal(reloaded.nodes.get("parameter-group").value,"design");
  for(const helpChapter of ["constructor","__proto__","parameter:removed",{},null]){
    const storage=memoryStorage();storage.setItem(`lre.workspace.navigation.v1:${user.id}`,JSON.stringify({version:1,page:"project",projectId:project.id,view:"guide",helpChapter}));
    const h=harness(server.fetcher,{storage});h.app.reset();await h.app.initialize();
    assert.equal(h.app.state().analysisView,"guide");assert.equal(h.app.state().helpChapter,"workflow");
    assert.deepEqual(copy(h.app.state().draft),defaults);
  }
});

test("run and example pages link to their documentation and respect editor locks",async()=>{
  const h=harness(async()=>{throw Error("Documentation must not submit work");});h.app.fillProject(copy(project));
  for(const [view,chapter] of [["design_run","theory"],["engineering_examples","examples"]]){
    await h.app.chooseView(view);const before=h.app.state();
    h.nodes.get("parameter-help").onclick();assert.equal(h.app.state().helpChapter,chapter);
    const helpBefore=h.app.state();
    await h.app.withEditorLock(async()=>{
      assert.equal(h.nodes.get("parameter-help").disabled,true);assert.ok(h.helpButtons.every(button=>button.disabled));
      h.app.openHelp("workflow");await h.app.returnFromHelp();
      assert.equal(h.app.state().helpChapter,chapter);assert.deepEqual(h.app.state().helpOrigin,helpBefore.helpOrigin);
    });
    await h.app.returnFromHelp();assert.equal(h.app.state().analysisView,view);
    assert.deepEqual(h.app.state().draft,before.draft);assert.equal(h.app.state().dirty,before.dirty);
  }
});

const operationNavigationGroups={
  steady:["steady_operations","fixed_steady","operating_throttle"],
  transient:["transient_operations","chilldown","ignition","transient","feed","shutdown"],
};

test("steady and transient routes activate their own top-level task groups",async()=>{
  const h=harness(async url=>{throw Error(`Navigation unexpectedly requested ${url}`);});
  assert.equal(h.nodes.has("operation-navigation"),false);
  for(const [group,routes] of Object.entries(operationNavigationGroups)){
    for(const route of routes){
      await h.app.chooseView(route);assertPrimaryNavigation(h,route,group);
      assert.match(h.nodes.get("analysis-breadcrumb").innerHTML,group==="steady"?/固定硬件稳态分析/:/瞬态运行分析/);
    }
  }
  h.nodes.get("parameter-group").value="design";
  await h.app.chooseView("overview");assertPrimaryNavigation(h,"overview","concept");
});

test("multiple task branches remain expanded without changing the active analysis",async()=>{
  const h=harness(async url=>{throw Error(`Expanding navigation unexpectedly requested ${url}`);});
  await h.app.chooseView("fixed_steady");
  const before=h.app.state(),steady=h.navGroups.find(node=>node.dataset.navGroup==="steady"),transient=h.navGroups.find(node=>node.dataset.navGroup==="transient"),models=h.setupSections.find(node=>node.dataset.setupSection==="models");
  assert.equal(steady.open,true);
  models.open=true;models.listeners.get("toggle")?.();
  assert.equal(models.open,true);assert.equal(steady.open,true);
  assert.equal(h.app.state().analysisView,"fixed_steady");
  assert.deepEqual(h.app.state().draft,before.draft);
  await h.navButtons.find(node=>node.dataset.view==="chilldown").onclick();
  assertPrimaryNavigation(h,"chilldown","transient");
  assert.equal(models.open,true);assert.equal(steady.open,true);assert.equal(transient.open,true);
});

test("setup expansion controls affect only settings branches and are hidden outside a project",async()=>{
  const record={id:"expansion-source",project_id:project.id,revision:1,status:"succeeded",inputs:defaults};
  const server=navigationServer({records:[record]}),h=harness(server.fetcher);
  await h.app.loadRecord(record.id);await h.app.chooseView("project_overview");
  h.nodes.get("project-name").value="导航展开中的草稿";h.nodes.get("project-name").oninput();
  assert.equal(h.nodes.get("setup-tree-tools").hidden,false);
  const before=h.app.state(),requests=server.calls.length;
  const settings=["concept"];
  const unrelated=h.navGroups.filter(node=>!settings.includes(node.dataset.navGroup)).map(node=>[node,node.open]);
  const click=value=>{
    const button=h.setupExpandButtons.find(node=>node.dataset.setupExpand===value);
    assert.ok(button);
    h.nodes.get("workspace-sidebar").listeners.get("click")({target:{closest:selector=>selector==="[data-setup-expand]"?button:null}});
  };
  for(const value of ["false","true"]){
    click(value);
    assert.ok(h.navGroups.filter(node=>settings.includes(node.dataset.navGroup)).every(node=>node.open===(value==="true")));
    assert.ok(h.setupSections.every(node=>node.open===(value==="true")));
    for(const [node,open] of unrelated)assert.equal(node.open,open);
    assert.deepEqual(h.app.state(),before);
  }
  assert.equal(server.calls.length,requests);
  h.app.switchPage("projects");assert.equal(h.nodes.get("setup-tree-tools").hidden,true);
  const open=h.navGroups.map(node=>node.open);click("false");assert.deepEqual(h.navGroups.map(node=>node.open),open);
});

test("direct design entry updates from confirmation to invalid input without a redundant wrapper",async()=>{
  const storage=reviewStorage(project),h=reviewPage(storage);await h.check();
  const branch=h.setupBranches.find(node=>node.dataset.setupBranch==="configuration");
  assert.match(branch.innerHTML,/data-status="review"/);
  assert.match(h.nodes.get("setup-tree-progress").textContent,/1 组设置/);
  await confirmGroup(h.nodes,"design");
  assert.match(branch.innerHTML,/data-status="confirmed"/);
  h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"pc"},value:""}});
  assert.match(branch.innerHTML,/data-status="invalid"/);
  assert.ok(h.setupSummaries.every(node=>node.hidden),"empty schema branches do not advertise phantom settings");
});

test("the reference tree entry opens a dedicated example page without loading a preset",async()=>{
  const record={id:"reference-source",project_id:project.id,revision:1,status:"succeeded",inputs:defaults};
  const server=navigationServer({records:[record]}),h=harness(server.fetcher);
  h.app.reset();await h.app.initialize();
  h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"pc"},value:"12.5"}});
  await h.app.chooseView("project_overview");
  const before=h.app.state(),requests=server.calls.length;
  const panel=h.nodes.get("concept-preset-reference"),summary=element();let scrolls=0;
  panel.querySelector=selector=>selector==="summary"?summary:null;panel.scrollIntoView=()=>scrolls++;
  const button={dataset:{setupReference:"presets"}};
  h.nodes.get("workspace-sidebar").listeners.get("click")({target:{closest:selector=>selector==="[data-setup-reference]"?button:null}});
  assert.equal(panel.open,true);assert.equal(summary.focusCount,1);assert.equal(scrolls,1);
  assert.equal(h.app.state().analysisView,"engineering_examples");assert.equal(h.nodes.get("concept-reference-panel").hidden,false);
  assert.equal(h.nodes.get("parameter-pane").hidden,true);assert.equal(h.nodes.get("concept-reference-panel").inert,false);
  assert.deepEqual(h.app.state().draft,before.draft);assert.deepEqual(h.app.state().resultRecord,before.resultRecord);
  assert.equal(h.app.state().dirty,true);assert.equal(server.calls.length,requests);
  const group=h.nodes.get("parameter-group").value;
  await h.nodes.get("concept-reference-back").onclick();
  assert.equal(h.app.state().analysisView,"overview");assert.equal(h.nodes.get("parameter-group").value,group);
  assert.equal(h.nodes.get("concept-reference-panel").hidden,true);assert.equal(h.nodes.get("parameter-pane").hidden,false);
  assert.deepEqual(h.app.state().draft,before.draft);assert.deepEqual(h.app.state().resultRecord,before.resultRecord);
  assert.equal(h.app.state().dirty,true);assert.equal(server.calls.length,requests);
});

test("primary steady and transient navigation preserves drafts and sources, cleans up tools and honors editor locks",async()=>{
  const record={id:"operation-source",project_id:project.id,revision:1,status:"succeeded",created:1};
  const server=navigationServer({records:[record]}),h=harness(server.fetcher);
  await h.app.loadRecord(record.id);
  h.nodes.get("project-name").value="工况研究中的未保存名称";h.nodes.get("project-name").oninput();
  await h.app.chooseView("operating_throttle");
  const before=h.app.state(),requests=server.calls.length;
  const click=async target=>{
    const button=h.navButtons.find(node=>node.dataset.view===target);
    assert.ok(button,`Missing sidebar destination ${target}`);
    await button.onclick();
  };
  await click("transient_operations");
  assert.equal(h.app.state().analysisView,"transient_operations");
  assertPrimaryNavigation(h,"transient_operations","transient");
  assert.equal(h.operatingThrottleLifecycle.leaves,1);
  assert.equal(h.nodes.get("operating-throttle-content").hidden,true);
  await h.app.chooseView("transient");
  const transientLeaves=h.transientLifecycle.leaves;
  await click("steady_operations");
  assert.equal(h.app.state().analysisView,"steady_operations");
  assertPrimaryNavigation(h,"steady_operations","steady");
  assert.equal(h.transientLifecycle.leaves,transientLeaves+1);
  assert.equal(h.nodes.get("transient-content").hidden,true);
  await h.app.withEditorLock(async()=>{
    assert.ok(h.navButtons.every(button=>button.disabled));
    await click("transient_operations");
    assert.equal(h.app.state().analysisView,"steady_operations");
  });
  assert.ok(h.navButtons.every(button=>!button.disabled));
  assert.equal(server.calls.length,requests);
  assert.deepEqual(h.app.state().draft,before.draft);
  assert.deepEqual(h.app.state().resultRecord,before.resultRecord);
  assert.equal(h.app.state().selectedJobId,before.selectedJobId);
  assert.equal(h.app.state().dirty,true);
  assert.equal(h.nodes.get("project-name").value,"工况研究中的未保存名称");
  assert.equal(h.confirmations(),0);
});

test("primary sidebar keeps its focused button when changing analysis views",async()=>{
  const h=harness(async url=>{throw Error(`Navigation unexpectedly requested ${url}`);});
  await h.app.chooseView("operating_throttle");
  const button=h.navButtons.find(node=>node.dataset.view==="transient_operations");
  button.focus();assert.equal(h.activeElement(),button);
  await button.onclick();
  assert.equal(h.app.state().analysisView,"transient_operations");
  assert.equal(h.activeElement(),button);
  assert.equal(button.focusCount,1);
  assert.equal(button.getAttribute("aria-current"),"page");
});

test("the retired simulation home alias restores the steady hub without replacing its historical source",async()=>{
  const record={id:"historical-operation-source",project_id:project.id,revision:1,status:"succeeded",created:1};
  const storage=memoryStorage(),key=`lre.workspace.navigation.v1:${user.id}`;
  storage.setItem(key,JSON.stringify({version:1,page:"project",projectId:project.id,view:"simulation_home",jobId:record.id}));
  const server=navigationServer({records:[record]}),h=harness(server.fetcher,{storage});
  h.app.reset();await h.app.initialize();
  assert.equal(h.app.state().analysisView,"steady_operations");
  assertPrimaryNavigation(h,"steady_operations","steady");
  assert.equal(h.app.state().selectedJobId,record.id);
  assert.equal(h.app.state().resultRecord.id,record.id);
  assert.equal(JSON.parse(storage.getItem(key)).view,"steady_operations");
  assert.ok(!h.navButtons.some(button=>button.dataset.view==="simulation_home"));
  await h.app.chooseView("transient");
  const before=h.app.state(),requests=server.calls.length;
  await h.app.chooseView("simulation_home");
  assert.equal(h.app.state().analysisView,"steady_operations");
  assertPrimaryNavigation(h,"steady_operations","steady");
  assert.deepEqual(h.app.state().resultRecord,before.resultRecord);
  assert.deepEqual(h.app.state().draft,before.draft);
  assert.equal(server.calls.length,requests);
});

test("result and research navigation hides unrelated design inputs without losing the selected record",async()=>{
  const record={id:"saved-source",project_id:project.id,revision:1,status:"succeeded",created:1};
  const h=harness(navigationServer({records:[record]}).fetcher);
  await h.app.loadRecord(record.id);
  for(const view of ["states","components","topology","validation","transient","feed","chilldown","ignition","shutdown","log"]){
    await h.app.chooseView(view);
    assert.equal(h.nodes.get("parameter-pane").hidden,true,view);
    assert.equal(h.nodes.get("header-run").hidden,true,view);
    assert.equal(h.nodes.get("design-run-submit").disabled,true,view);
    assert.equal(h.app.state().selectedJobId,record.id);
  }
});

test("saved schematic stays read-only and retains its source when inspecting states and components",async()=>{
  const record={id:"cycle-source",project_id:project.id,revision:1,status:"succeeded",inputs:defaults};
  const server=navigationServer({records:[record]}),h=harness(server.fetcher);
  await h.app.loadRecord(record.id);
  h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"pc"},value:"12"}});
  const before=h.app.state(),requests=server.calls.length;
  await h.app.chooseView("topology");
  assertPrimaryNavigation(h,"topology","concept");
  assert.match(h.nodes.get("analysis-breadcrumb").innerHTML,/总体方案与循环设计.*保存结果.*循环原理图.*只读/);
  for(const id of ["header-run","header-save","toggle-parameters","parameter-pane","design-run-panel"])assert.equal(h.nodes.get(id).hidden,true,id);
  assert.equal(h.nodes.get("parameter-pane").inert,true);assert.equal(h.nodes.get("design-run-submit").disabled,true);
  assert.equal(h.analysisRenders.at(-1).view,"topology");
  assert.deepEqual(copy(h.analysisRenders.at(-1).record),copy(before.resultRecord));
  assert.equal(h.analysisMounts.cycle.at(-1).recordId,record.id);

  const host=h.nodes.get("analysis-content"),stateLink={dataset:{flowState:"fuel_pump_out",flowGroup:"fuel"}};
  host.contains=node=>node===stateLink;
  let prevented=false;
  host.listeners.get("click")({target:{closest:selector=>selector==="[data-flow-state]"?stateLink:null},preventDefault(){prevented=true;}});
  assert.equal(prevented,true);assertPrimaryNavigation(h,"states","concept");
  assert.equal(host.dataset.saRecordId,record.id);assert.equal(host.dataset.saSelected,"fuel_pump_out");assert.equal(host.dataset.saGroup,"fuel");
  assert.equal(h.analysisMounts.flow.at(-1).recordId,record.id);
  await h.analysisMounts.flow.at(-1).options.onTopology("fuel_pump_out");
  assertPrimaryNavigation(h,"topology","concept");
  assert.equal(host.dataset.flowLocation,"fuel_pump_out");assert.equal(host.dataset.flowLocationRecord,record.id);
  await h.analysisMounts.cycle.at(-1).options.onComponent("fuel_pump");
  assertPrimaryNavigation(h,"components","concept");
  assert.equal(host.dataset.caRecordId,record.id);assert.equal(host.dataset.caSelected,"fuel_pump");
  assert.equal(h.analysisMounts.components.at(-1).recordId,record.id);
  assert.equal(server.calls.length,requests);
  assert.deepEqual(h.app.state().draft,before.draft);assert.deepEqual(h.app.state().resultRecord,before.resultRecord);
  assert.equal(h.app.state().selectedJobId,record.id);assert.equal(h.app.state().dirty,true);
});

test("mobile navigation closes on a view change and unknown views are ignored",async()=>{
  const h=harness(async(url)=>{throw new Error(`Unexpected request ${url}`);});
  h.nodes.get("toggle-navigation").onclick();
  assert.equal(h.nodes.get("navigation-backdrop").hidden,false);
  await h.app.chooseView("model_library");
  assert.equal(h.nodes.get("navigation-backdrop").hidden,true);
  assert.equal(h.nodes.get("toggle-navigation").dataset.open,"false");
  await h.app.chooseView("unregistered_future_mode");
  assert.equal(h.app.state().analysisView,"model_library");
});
const parameterNavigationInfo={...info,ui_schema:{...schema,groups:[
  {id:"cycle",title:"循环方式",fields:[fields[0]]},
  {id:"design",title:"设计目标与工况",fields:fields.slice(1)},
  {id:"ffsc-only",title:"预燃室",cycles:["ffsc"],fields:[fields[1]]},
]}};

const configurationInputs={...defaults,propellant_pair:"lox_methane"};
const configurationInfo={...info,ui_schema:{defaults:configurationInputs,groups:[
  {id:"cycle",title:"发动机构型与循环原理",fields:[
    {...fields[0],read_only:true},
    {key:"propellant_pair",label:"推进剂组合",default:"lox_methane",read_only:true},
  ]},
  {id:"design",title:"设计目标与工况",fields:fields.slice(1)},
]}};

const mergedDefinitionInfo={...configurationInfo,ui_schema:{...configurationInfo.ui_schema,version:20,groups:[{
  id:"design",title:"发动机定义与设计工况",engine_definition:true,
  fields:configurationInfo.ui_schema.groups.flatMap(group=>group.fields),
}]}};

test("legacy summary and cycle locations open the merged definition without mutating inputs",async()=>{
  const saved={...project,inputs:configurationInputs},storage=memoryStorage();
  storage.setItem(`lre.workspace.navigation.v1:${user.id}`,JSON.stringify({version:1,page:"project",projectId:saved.id,view:"project_overview",conceptSection:"results"}));
  storage.setItem(`lre.workspace.parameter-group.v1:${user.id}:${saved.id}`,"cycle");
  const server=navigationServer({uiInfo:mergedDefinitionInfo,projects:[saved]}),h=harness(server.fetcher,{storage});
  h.app.reset();await h.app.initialize();
  assert.equal(h.app.state().analysisView,"overview");assert.equal(h.app.state().conceptSection,"settings");
  assert.equal(h.nodes.get("parameter-group").value,"design");
  assert.equal(h.nodes.get("analysis-title").textContent,"发动机定义与设计工况");
  assert.equal(h.nodes.get("parameter-edit-actions").hidden,false);
  h.app.openReadinessField("cycle");assert.equal(h.nodes.get("parameter-group").value,"design");
  assert.deepEqual(copy(h.app.state().draft),configurationInputs);assert.equal(h.app.state().dirty,false);
  assert.equal(h.nodes.get("open-project-summary").hidden,false);
});

test("opening the optional summary preserves the last working location and selected record",async()=>{
  const records=[{id:"recent",project_id:project.id,revision:2,status:"succeeded",inputs:defaults},
    {id:"chosen",project_id:project.id,revision:1,status:"succeeded",inputs:defaults}];
  const server=navigationServer({records}),h=harness(server.fetcher);h.app.reset();await h.app.initialize();
  await h.app.chooseView("solver_diagnostics");await h.app.loadRecord("chosen");
  const requests=server.calls.length;await h.nodes.get("open-project-summary").onclick();
  assert.equal(h.app.state().analysisView,"project_overview");assert.equal(server.calls.length,requests);
  assert.equal(JSON.parse(h.storage.getItem(`lre.workspace.navigation.v1:${user.id}`)).view,"solver_diagnostics");
  const reloaded=harness(server.fetcher,{storage:h.storage});reloaded.app.reset();await reloaded.app.initialize();
  assert.equal(reloaded.app.state().analysisView,"solver_diagnostics");assert.equal(reloaded.app.state().resultRecord.id,"chosen");
});

test("global context actions navigate to review and difference fields without saving or running",async()=>{
  const server=navigationServer(),h=harness(server.fetcher);await h.app.chooseView("overview");
  const before=h.app.state(),requests=server.calls.length;
  const click=(selector,dataset)=>h.nodes.get("project-context").listeners.get("click")({preventDefault(){},target:{closest:value=>value===selector?{dataset}:null}});
  click("[data-workspace-view]",{workspaceView:"design_run"});
  assert.equal(h.app.state().analysisView,"design_run");
  click("[data-readiness-group]",{readinessGroup:"design",readinessField:"pc"});
  assert.equal(h.app.state().analysisView,"overview");assert.equal(h.nodes.get("parameter-group").value,"design");
  assert.deepEqual(h.app.state().draft,before.draft);assert.equal(server.calls.length,requests);
});

test("merging readonly identity retains an exact existing design review and invalidates changed physical inputs",async()=>{
  const inputs=configurationInputs,oldGroup=configurationInfo.ui_schema.groups.find(group=>group.id==="design");
  const signature=JSON.stringify([20,[inputs.cycle_type,inputs.propellant_pair,null],oldGroup.fields.map(field=>[field.key,inputs[field.key]])]);
  const bytes=await webcrypto.subtle.digest("SHA-256",new TextEncoder().encode(signature));
  const digest="sha256:"+Array.from(new Uint8Array(bytes),byte=>byte.toString(16).padStart(2,"0")).join("");
  for(const proof of [signature,digest]){
    const storage=reviewStorage({...project,inputs,design_reviews:{design:{kind:"confirmed",signature:proof}}});
    const merged=mergedDefinitionInfo.ui_schema.groups;
    const h=reviewPage(storage,{groups:merged,fields:merged[0].fields,extraInfo:mergedDefinitionInfo});
    await h.check();await new Promise(resolve=>setImmediate(resolve));await h.check();
    assert.equal(h.app.state().groupReviews.find(([id])=>id==="design")?.[1].kind,"confirmed");
    assert.equal(storage.requests.filter(([,method])=>method!=="GET").length,0);
    h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"pc"},value:"12"}});
    assert.ok(!h.app.state().groupReviews.some(([id])=>id==="design"));
  }
});

test("configuration entry is read-only, returns from help, and continues to design without modifying inputs",async()=>{
  const saved={...project,inputs:configurationInputs};
  const server=navigationServer({uiInfo:configurationInfo,projects:[saved]}),h=harness(server.fetcher);
  h.app.reset();await h.app.initialize();
  h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"pc"},value:"12"}});
  const before=h.app.state(),requests=server.calls.length;
  const click=group=>h.body.listeners.get("click")({preventDefault(){},target:{closest:selector=>selector==="[data-setup-group],[data-task-group]"?{dataset:{setupGroup:group}}:null}});
  click("cycle");
  assert.equal(h.nodes.get("analysis-title").textContent,"发动机构型与循环原理");
  const breadcrumb=h.nodes.get("analysis-breadcrumb").innerHTML;
  assert.match(breadcrumb,/系统工作台.*总体方案与循环设计.*发动机构型与循环原理/);
  assert.equal((breadcrumb.match(/发动机构型与循环原理/g)||[]).length,1);
  assert.doesNotMatch(breadcrumb,/模型与效率假设/);
  assert.equal(h.nodes.get("parameter-panel-title").textContent,"构型说明");
  for(const id of ["parameter-edit-actions","dirty-label","design-readiness","header-run"])assert.equal(h.nodes.get(id).hidden,true,id);
  assert.equal(h.nodes.get("parameter-edit-actions").inert,true);
  assert.equal(h.nodes.get("configuration-actions").hidden,false);
  await h.nodes.get("project-form").onsubmit({preventDefault(){}});
  h.app.openHelp();assert.equal(h.app.state().helpChapter,"parameter:cycle");
  await h.app.returnFromHelp();
  assert.equal(h.nodes.get("parameter-group").value,"cycle");
  click("design");
  assert.equal(h.nodes.get("parameter-group").value,"design");
  assert.equal(h.nodes.get("parameter-panel-title").textContent,"参数设置");
  assert.equal(h.nodes.get("parameter-edit-actions").hidden,false);
  assert.equal(h.nodes.get("parameter-edit-actions").inert,false);
  assert.equal(h.nodes.get("configuration-actions").hidden,true);
  assert.deepEqual(h.app.state().draft,before.draft);
  assert.equal(h.app.state().dirty,true);
  assert.equal(server.calls.length,requests);
});

test("viewing configuration does not block a later metadata save from history",async()=>{
  const saved={...project,inputs:configurationInputs},writes=[];
  const server=navigationServer({uiInfo:configurationInfo,projects:[saved]});
  const h=harness(async(url,options)=>{
    if(url==="/api/projects/p1"&&options.method==="PUT"){
      const payload=JSON.parse(options.body);writes.push(payload);
      return response({project:{...saved,...payload,revision:2},warnings:[]});
    }
    return server.fetcher(url,options);
  });
  h.app.reset();await h.app.initialize();h.app.openReadinessField("cycle");
  await h.app.chooseView("history");
  h.nodes.get("project-name").value="构型核对后的方案";h.nodes.get("project-name").oninput();
  await h.nodes.get("project-form").onsubmit({submitter:h.nodes.get("history-save"),preventDefault(){}});
  assert.equal(writes.length,1);assert.equal(writes[0].name,"构型核对后的方案");
  assert.deepEqual(writes[0].inputs,configurationInputs);
  assert.equal(h.app.state().current.revision,2);
  assert.equal(h.app.state().dirty,false);
});

test("saved thermal group navigates to numerical settings after relocation without changing the case",async()=>{
  const newInfo=copy(parameterNavigationInfo);
  newInfo.ui_schema.groups.push({id:"initialization",title:"初始化",fields:[fields[3]],numerical_configuration:true});
  const storage=memoryStorage();
  storage.setItem(`lre.workspace.parameter-group.v1:${user.id}:${project.id}`,"thermo");
  const server=navigationServer({uiInfo:newInfo}),h=harness(server.fetcher,{storage});
  h.app.reset();await h.app.initialize();
  assert.equal(h.nodes.get("parameter-group").value,"initialization");
  assert.equal(storage.getItem(`lre.workspace.parameter-group.v1:${user.id}:${project.id}`),"initialization");
  assert.deepEqual(copy(h.app.state().draft),defaults);
  assert.equal(h.app.state().dirty,false);
});

test("reload restores the selected parameter group without changing inputs or the analysis view",async()=>{
  const server=navigationServer({uiInfo:parameterNavigationInfo}),first=harness(server.fetcher);
  first.app.reset();await first.app.initialize();
  assert.equal(first.nodes.get("parameter-group").value,"cycle");
  first.nodes.get("parameter-group").value="design";first.nodes.get("parameter-group").onchange();
  await first.app.chooseView("topology");
  const reloaded=harness(server.fetcher,{storage:first.storage});reloaded.app.reset();await reloaded.app.initialize();
  assert.equal(reloaded.nodes.get("parameter-group").value,"design");
  assert.equal(reloaded.app.state().analysisView,"topology");
  assertPrimaryNavigation(reloaded,"topology","concept");
  assert.deepEqual(copy(reloaded.app.state().draft),defaults);
  assert.equal(reloaded.app.state().dirty,false);
});

test("parameter group memory is isolated by project and account",async()=>{
  const other={...project,id:"p2"};
  const server=navigationServer({uiInfo:parameterNavigationInfo,projects:[project,other]}),h=harness(server.fetcher);
  h.app.reset();await h.app.initialize();
  h.nodes.get("parameter-group").value="design";h.nodes.get("parameter-group").onchange();
  await h.app.openProject(other.id);assert.equal(h.nodes.get("parameter-group").value,"cycle");
  await h.app.openProject(project.id);assert.equal(h.nodes.get("parameter-group").value,"design");
  const memberServer=navigationServer({uiInfo:parameterNavigationInfo,account:{...user,id:"u2"}});
  const member=harness(memberServer.fetcher,{storage:h.storage});member.app.reset();await member.app.initialize();
  assert.equal(member.nodes.get("parameter-group").value,"cycle");
  assert.equal(h.storage.getItem(`lre.workspace.parameter-group.v1:${user.id}:${project.id}`),"design");
});

test("removed parameter groups fall back to an available group",async()=>{
  for(const saved of ["removed","{broken"]){
    const storage=memoryStorage(),server=navigationServer({uiInfo:parameterNavigationInfo});
    storage.setItem(`lre.workspace.parameter-group.v1:${user.id}:${project.id}`,saved);
    const h=harness(server.fetcher,{storage});h.app.reset();await h.app.initialize();
    assert.equal(h.nodes.get("parameter-group").value,"cycle");
    assert.equal(h.app.state().dirty,false);
  }
});

for(const view of navigationViews)test(`reload restores ${view} with the selected project and historical design record`,async()=>{
  const other={...project,id:"p2",name:"另一个方案"};
  const records=[
    {id:"latest",project_id:other.id,project_name:other.name,revision:2,status:"succeeded",created:2},
    {id:"historical",project_id:other.id,project_name:other.name,revision:1,status:"succeeded",created:1},
  ];
  const server=navigationServer({projects:[project,other],records});
  const first=harness(server.fetcher);
  await first.app.openProject(other.id);
  await first.app.loadRecord("historical");
  await first.app.chooseView(view);
  const saved=JSON.parse(first.storage.getItem(`lre.workspace.navigation.v1:${user.id}`));
  assert.deepEqual(Object.keys(saved).sort(),["conceptSection","helpChapter","jobId","libraryTab","page","projectId","version","view"]);
  assert.equal(saved.projectId,other.id);assert.equal(saved.jobId,"historical");
  const reloaded=harness(server.fetcher,{storage:first.storage});reloaded.app.reset();
  await reloaded.app.initialize();
  const state=reloaded.app.state();
  assert.equal(state.page,"project");assert.equal(state.analysisView,view==="project_overview"?"overview":view);
  assert.equal(state.current.id,other.id);assert.equal(state.selectedJobId,"historical");
  assert.equal(state.resultRecord.id,"historical");assert.equal(state.dirty,false);
  assert.equal(reloaded.nodes.get("page-name").textContent,reloaded.nodes.get("analysis-title").textContent);
  const expectedOperationGroup=Object.entries(operationNavigationGroups).find(([,routes])=>routes.includes(view))?.[0];
  assertPrimaryNavigation(reloaded,view==="project_overview"?"overview":view,view==="project_overview"?"concept":expectedOperationGroup||(view==="overview"?setupTree.setupNavigationGroup(reloaded.nodes.get("parameter-group").value):workspaceNavigation.workspaceGroup(view)?.id));
});

for(const page of ["projects","jobs","members"])test(`reload keeps the ${page} workspace page`,async()=>{
  const server=navigationServer(),first=harness(server.fetcher);
  await first.app.chooseView("validation");await first.app.navigate(page);
  const reloaded=harness(server.fetcher,{storage:first.storage});reloaded.app.reset();
  await reloaded.app.initialize();
  assert.equal(reloaded.app.state().page,page);
  assert.equal(reloaded.nodes.get(`${page}-page`).hidden,false);
  assert.equal(reloaded.nodes.get("project-page").hidden,true);
});

test("missing or inaccessible saved project falls back to the accessible project list",async()=>{
  const server=navigationServer(),storage=memoryStorage();
  storage.setItem(`lre.workspace.navigation.v1:${user.id}`,JSON.stringify({version:1,page:"project",projectId:"private-or-deleted",view:"validation",jobId:"private-job"}));
  const h=harness(server.fetcher,{storage});h.app.reset();await h.app.initialize();
  assert.equal(h.app.state().page,"projects");assert.equal(h.app.state().current,null);
  assert.ok(!server.calls.some(url=>url.includes("private")));
});

test("missing or cross-project record is never fetched and falls back within the restored project",async()=>{
  for(const records of [[],[{id:"available",project_id:project.id,project_name:project.name,revision:1,status:"succeeded",created:1}]]){
    const storage=memoryStorage(),server=navigationServer({records});
    storage.setItem(`lre.workspace.navigation.v1:${user.id}`,JSON.stringify({version:1,page:"project",projectId:project.id,view:"validation",jobId:"foreign-job"}));
    const h=harness(server.fetcher,{storage});h.app.reset();await h.app.initialize();
    assert.equal(h.app.state().analysisView,"validation");
    assert.equal(h.app.state().selectedJobId,records[0]?.id??null);
    assert.equal(h.app.state().resultRecord?.id??null,records[0]?.id??null);
    assert.ok(!server.calls.includes("/api/jobs/foreign-job"));
  }
});

test("corrupt and unsupported stored navigation safely use the default workspace",async()=>{
  for(const value of ["{broken", "null", "[]", JSON.stringify({version:2,page:"project"}),JSON.stringify({version:1,page:"unknown"})]){
    const storage=memoryStorage(),server=navigationServer();
    storage.setItem(`lre.workspace.navigation.v1:${user.id}`,value);
    const h=harness(server.fetcher,{storage});h.app.reset();await h.app.initialize();
    assert.equal(h.app.state().page,"project");assert.equal(h.app.state().analysisView,"overview");
    assert.equal(h.app.state().current.id,project.id);
  }
});

test("invalid saved analysis names cannot address inherited object properties",async()=>{
  for(const view of ["unknown","constructor","__proto__",null,{}]){
    const storage=memoryStorage(),server=navigationServer();
    storage.setItem(`lre.workspace.navigation.v1:${user.id}`,JSON.stringify({version:1,page:"project",projectId:project.id,view}));
    const h=harness(server.fetcher,{storage});h.app.reset();await h.app.initialize();
    assert.equal(h.app.state().analysisView,"overview");assert.equal(h.app.state().current.id,project.id);
  }
});

test("blocked browser storage does not prevent navigation or initialization",async()=>{
  const storage={getItem(){throw new Error("SecurityError");},setItem(){throw new Error("QuotaExceededError");}};
  const server=navigationServer(),h=harness(server.fetcher,{storage});h.app.reset();
  await h.app.initialize();await h.app.chooseView("validation");await h.app.navigate("jobs");
  assert.equal(h.app.state().page,"jobs");
});

test("saved navigation is account scoped and a non-admin cannot restore the members page",async()=>{
  const storage=memoryStorage(),member={id:"u2",display_name:"成员",role:"member"};
  storage.setItem(`lre.workspace.navigation.v1:${user.id}`,JSON.stringify({version:1,page:"project",projectId:project.id,view:"validation"}));
  let server=navigationServer({account:member}),h=harness(server.fetcher,{storage});h.app.reset();await h.app.initialize();
  assert.equal(h.app.state().analysisView,"overview");
  assert.equal(JSON.parse(storage.getItem(`lre.workspace.navigation.v1:${user.id}`)).view,"validation");
  storage.setItem(`lre.workspace.navigation.v1:${member.id}`,JSON.stringify({version:1,page:"members",projectId:project.id,view:"history"}));
  server=navigationServer({account:member});h=harness(server.fetcher,{storage});h.app.reset();await h.app.initialize();
  assert.equal(h.app.state().page,"projects");assert.equal(h.nodes.get("members-nav").hidden,true);
  assert.ok(!server.calls.includes("/api/members"));
});

test("switching authenticated accounts clears the previous workspace and restores only the new account",async()=>{
  let account=user;
  const member={id:"u2",display_name:"成员",role:"member"},other={...project,id:"p2",name:"成员方案"};
  const storage=memoryStorage(),calls=[];
  const h=harness(async(url,options)=>{
    calls.push(url);
    return navigationServer({account,projects:account===user?[project]:[other]}).fetcher(url,options);
  },{storage});
  h.app.reset();await h.app.initialize();await h.app.chooseView("validation");
  account=member;calls.length=0;await h.app.initialize();
  assert.equal(h.app.state().current.id,other.id);assert.equal(h.app.state().analysisView,"overview");
  assert.equal(h.app.state().resultRecord,null);assert.equal(h.app.state().selectedJobId,null);
  assert.ok(!calls.includes("/api/projects/p1"));
  assert.equal(JSON.parse(storage.getItem(`lre.workspace.navigation.v1:${user.id}`)).view,"validation");
});

test("explicitly opening a different project starts its overview without reusing the saved record",async()=>{
  const other={...project,id:"p2",name:"新打开方案"},server=navigationServer({projects:[project,other]});
  const h=harness(server.fetcher);await h.app.chooseView("validation");await h.app.openProject(other.id);
  assert.equal(h.app.state().analysisView,"overview");assert.equal(h.app.state().current.id,other.id);
  assert.equal(h.app.state().selectedJobId,null);assert.equal(h.app.state().resultRecord,null);
  assert.equal(JSON.parse(h.storage.getItem(`lre.workspace.navigation.v1:${user.id}`)).view,"overview");
});

test("returning to each project restores its own task location without retaining another project's record",async()=>{
  const other={...project,id:"p2",name:"另一发动机方案"};
  const records=[{id:"record-p1",project_id:project.id,revision:1,status:"succeeded",inputs:defaults},
    {id:"record-p2",project_id:other.id,revision:1,status:"succeeded",inputs:defaults}];
  const server=navigationServer({projects:[project,other],records}),h=harness(server.fetcher);
  await h.app.chooseView("validation");
  await h.app.openProject(other.id);
  assert.equal(h.app.state().analysisView,"overview");
  await h.app.chooseView("log");
  await h.app.openProject(project.id);
  assert.equal(h.app.state().analysisView,"validation");
  assert.equal(h.app.state().resultRecord.id,"record-p1");
  await h.app.openProject(other.id);
  assert.equal(h.app.state().analysisView,"log");
  assert.equal(h.app.state().resultRecord.id,"record-p2");
  for(const id of [project.id,other.id]){
    const saved=JSON.parse(h.storage.getItem(`lre.workspace.navigation.v1:${user.id}:project:${id}`));
    assert.deepEqual(Object.keys(saved).sort(),["conceptSection","helpChapter","jobId","libraryTab","view"]);
  }
});

test("workflow compares calculation inputs rather than metadata or the unsaved flag and preserves the selected record",async()=>{
  const record={id:"source",project_id:project.id,revision:1,status:"succeeded",inputs:defaults,
    result:{summary:{thrust_N:defaults.thrust,Isp_s:330},calculation:{inputs:defaults}}};
  const server=navigationServer({records:[record]}),h=harness(server.fetcher);
  await h.app.loadRecord(record.id);await h.app.chooseView("project_overview");
  assert.equal(h.app.currentWorkflow().freshness.state,"current");
  h.nodes.get("project-name").value="仅修改方案名称";h.nodes.get("project-name").oninput();
  assert.equal(h.app.state().dirty,true);
  assert.equal(h.app.currentWorkflow().freshness.state,"current");
  assert.doesNotMatch(h.nodes.get("result-context").textContent,/历史输入的结果/);
  const requests=server.calls.length;
  h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"pc"},value:"12.25"}});
  assert.equal(h.app.currentWorkflow().freshness.state,"stale");
  assert.deepEqual(copy(h.app.currentWorkflow().freshness.changedKeys),["pc"]);
  assert.deepEqual(copy(h.app.currentWorkflow().freshness.changedGroups.map(group=>group.id)),["design"]);
  assert.match(h.nodes.get("result-context").textContent,/历史输入的结果/);
  assert.match(h.nodes.get("project-context").innerHTML,/结果待更新/);
  assert.deepEqual(copy(h.app.state().resultRecord),record);
  h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"pc"},value:String(defaults.pc*1e-6)}});
  assert.equal(h.app.currentWorkflow().freshness.state,"current");
  assert.equal(h.app.state().dirty,true,"a restored input can still have unsaved metadata");
  assert.equal(server.calls.length,requests,"editing and overview rendering never run a job");
});

test("saving only project metadata creates a revision while the calculation remains current",async()=>{
  const record={id:"metadata-source",project_id:project.id,revision:1,status:"succeeded",inputs:defaults};
  const h=harness(async(url,options)=>{
    if(url===`/api/jobs/${record.id}`)return response(record);
    assert.equal(url,`/api/projects/${project.id}`);assert.equal(options.method,"PUT");
    return response({project:{...project,...JSON.parse(options.body),revision:2},warnings:[]});
  });
  await h.app.loadRecord(record.id);
  h.nodes.get("project-description").value="补充设计意图";h.nodes.get("project-description").oninput();
  await h.app.saveProject();
  assert.equal(h.app.state().current.revision,2);
  assert.equal(h.app.currentWorkflow().freshness.state,"current");
  assert.deepEqual(copy(h.app.state().resultRecord),record);
});

test("empty and partial historical input snapshots never become current by filling live defaults",async()=>{
  for(const inputs of [undefined,{}, {pc:defaults.pc}]){
    const record={id:"incomplete-source",project_id:project.id,revision:1,status:"succeeded",
      ...(inputs===undefined?{}:{inputs}),result:{calculation:{summary:{thrust_N:defaults.thrust}}}};
    const server=navigationServer({records:[record]}),h=harness(server.fetcher);
    await h.app.loadRecord(record.id);await h.app.chooseView("project_overview");
    assert.equal(h.app.currentWorkflow().freshness.state,"unknown");
    assert.match(h.nodes.get("result-context").textContent,/输入快照不完整/);
    assert.match(h.nodes.get("project-context").innerHTML,/版本一致性待确认/);
    assert.deepEqual(copy(h.app.state().resultRecord),record);
    h.nodes.get("project-description").value="保留历史记录补充说明";h.nodes.get("project-description").oninput();
    assert.equal(h.app.currentWorkflow().freshness.state,"unknown");
  }
});

test("a known difference in a partial snapshot remains stale without inventing its missing inputs",async()=>{
  const record={id:"partial-source",project_id:project.id,revision:1,status:"succeeded",inputs:{pc:9e6}};
  const h=harness(navigationServer({records:[record]}).fetcher);
  await h.app.loadRecord(record.id);
  assert.equal(h.app.currentWorkflow().freshness.state,"stale");
  assert.deepEqual(copy(h.app.currentWorkflow().freshness.changedKeys),["pc"]);
  assert.ok(h.app.currentWorkflow().freshness.missingKeys.includes("thrust"));
  assert.deepEqual(copy(h.app.state().resultRecord),record);
});

test("setup tree goes directly to a parameter group while preserving unsaved inputs and selected evidence",async()=>{
  const record={id:"tree-source",project_id:project.id,revision:1,status:"succeeded",inputs:defaults};
  const server=navigationServer({records:[record],uiInfo:parameterNavigationInfo}),h=harness(server.fetcher);
  h.app.reset();await h.app.initialize();
  await h.app.chooseView("project_overview");
  h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"pc"},value:"12"}});
  const before=h.app.state(),requests=server.calls.length;
  const branch=h.setupBranches.find(host=>host.dataset.setupBranch==="configuration");
  assert.match(branch.innerHTML,/data-setup-group="design"/);
  const button={dataset:{setupGroup:"design"}};
  h.body.listeners.get("click")({preventDefault(){},target:{closest:selector=>selector==="[data-setup-group],[data-task-group]"?button:null}});
  assert.equal(h.app.state().analysisView,"overview");assert.equal(h.app.state().conceptSection,"settings");
  assert.equal(h.nodes.get("parameter-group").value,"design");
  assert.equal(h.nodes.get("analysis-title").textContent,"设计目标与工况");
  assert.equal(h.nodes.get("page-name").textContent,"设计目标与工况");
  assert.match(h.nodes.get("analysis-breadcrumb").innerHTML,/系统工作台.*总体方案与循环设计.*设计目标与工况/);
  assertPrimaryNavigation(h,"overview","concept");
  assert.ok(h.setupSections.every(node=>!node.classList.contains("is-current")),"direct setup items do not create a duplicate wrapper");
  assert.deepEqual(h.app.state().draft,before.draft);assert.deepEqual(h.app.state().resultRecord,before.resultRecord);
  assert.equal(h.app.state().dirty,true);assert.equal(server.calls.length,requests);
});

test("opening setup from a component focuses an editable control and never the hidden selector",async()=>{
  const pumpField={key:"pump_test",default:1,label:"泵输入",type:"number",factor:1};
  const groups=[...schema.groups,{id:"pumps",title:"泵参数",fields:[pumpField]}];
  const h=harness(async()=>{throw Error("Changing parameter focus cannot request data");});
  h.app.seed(copy({current:project,draft:{...defaults,pump_test:1},dirty:false,user,
    info:{...info,ui_schema:{engineering_workflow:{components:[{id:"pumps",groups:["pumps"],views:["pump_geometry"]}]},defaults:{...defaults,pump_test:1},groups}},groups,fields:[...fields,pumpField],page:"project",projectJobs:[],connected:true}));
  const control=element(),group=element();group.dataset.group="pumps";
  group.querySelector=selector=>selector.startsWith("[data-input]")?control:null;
  h.nodes.get("parameter-fields").querySelectorAll=selector=>selector==="[data-group]"?[group]:[];
  await h.app.chooseView("pump_geometry");h.nodes.get("toggle-parameters").onclick();
  assert.equal(h.app.state().analysisView,"overview");assert.equal(h.nodes.get("parameter-group").value,"pumps");
  assert.equal(control.focusCount,1);assert.equal(h.nodes.get("parameter-group").focusCount,0);
  assert.equal(h.nodes.get("parameter-search").focusCount,0);
});

test("reconnection keeps the current analysis view and unsaved draft in memory",async()=>{
  const server=navigationServer(),h=harness(server.fetcher);
  await h.app.chooseView("validation");
  h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"pc"},value:"12.5"}});
  await h.app.initialize();
  assert.equal(h.app.state().analysisView,"validation");assert.equal(h.app.state().draft.pc,12.5e6);
  assert.equal(h.app.state().dirty,true);
  assert.ok(!server.calls.includes("/api/projects/p1"));
});

test("throttle navigation keeps the selected design and stops its polling when leaving", async () => {
  const record={id:"steady-source",project_id:project.id,revision:1,status:"succeeded",created:1,result:{calculation:{inputs:defaults}}};
  const h=harness(async(url,options)=>{
    assert.equal(options.method,"GET");
    assert.equal(url,"/api/jobs/steady-source");
    return response(record);
  });
  await h.app.loadRecord(record.id);
  const before=h.app.state();
  await h.app.chooseView("throttle");
  assert.equal(h.nodes.get("analysis-title").textContent,"设计匹配扫描历史");
  assert.equal(h.nodes.get("throttle-content").hidden,false);
  assert.equal(h.nodes.get("analysis-content").hidden,true);
  assert.equal(h.nodes.get("validation-content").hidden,true);
  assert.equal(h.throttleLifecycle.mounts,1);
  assert.equal(h.throttleLifecycle.refreshes,1);
  assert.deepEqual(h.app.state().resultRecord,before.resultRecord);
  assert.deepEqual(h.app.state().draft,before.draft);
  await h.app.chooseView("overview");
  assert.equal(h.nodes.get("throttle-content").hidden,true);
  assert.equal(h.throttleLifecycle.leaves,1);
  await h.app.chooseView("throttle");
  assert.equal(h.throttleLifecycle.mounts,1);
  assert.equal(h.throttleLifecycle.refreshes,2);
});

test("operating throttle has a separate lifecycle, preserves the design and rejects concept execution",async()=>{
  const record={id:"operating-source",project_id:project.id,revision:1,status:"succeeded",created:1,result:{calculation:{inputs:defaults}}};
  const h=harness(async(url,options)=>{assert.equal(options.method,"GET");assert.equal(url,"/api/jobs/operating-source");return response(record);});
  await h.app.loadRecord(record.id);const before=h.app.state();
  await h.app.chooseView("operating_throttle");
  assert.equal(h.nodes.get("analysis-title").textContent,"发动机节流分析");
  assert.equal(h.nodes.get("operating-throttle-content").hidden,false);assert.equal(h.nodes.get("throttle-content").hidden,true);
  assert.equal(h.nodes.get("toggle-parameters").hidden,true);assert.equal(h.nodes.get("design-run-submit").disabled,true);
  assert.equal(h.operatingThrottleLifecycle.mounts,1);assert.equal(h.operatingThrottleLifecycle.refreshes,1);assert.equal(h.throttleLifecycle.mounts,0);
  await assert.rejects(h.app.runDesign(),/不能运行概念设计/);await assert.rejects(h.app.saveProject(),/保存的设计基准/);
  assert.deepEqual(h.app.state().draft,before.draft);assert.deepEqual(h.app.state().resultRecord,before.resultRecord);
  await h.app.chooseView("overview");assert.equal(h.operatingThrottleLifecycle.leaves,1);assert.equal(h.nodes.get("operating-throttle-content").hidden,true);
});

test("fixed hardware entry hides concept controls and refuses direct concept save or run",async()=>{
  const calls=[];
  const h=harness(async(url)=>{calls.push(url);throw new Error(`Unexpected request ${url}`);});
  const before=h.app.state();
  await h.app.chooseView("fixed_steady");
  assert.equal(h.nodes.get("fixed-steady-content").hidden,false);
  assert.equal(h.nodes.get("parameter-pane").hidden,true);
  assert.equal(h.nodes.get("parameter-pane").inert,true);
  for(const id of ["header-save","header-run","result-toolbar","result-consistency","toggle-parameters"])assert.equal(h.nodes.get(id).hidden,true,id);
  assert.equal(h.nodes.get("design-run-submit").disabled,true);
  await assert.rejects(h.app.runDesign(),/不能运行概念设计/);
  await assert.rejects(h.app.saveProject(),/保存任务定义/);
  assert.equal(calls.length,0);
  assert.deepEqual(h.app.state().draft,before.draft);
  await h.app.chooseView("overview");
  assert.equal(h.nodes.get("parameter-pane").hidden,false);
  assert.equal(h.nodes.get("parameter-pane").inert,false);
  assert.equal(h.nodes.get("header-run").hidden,true);
  assert.equal(h.nodes.get("design-run-submit").disabled,true);
  assert.equal(h.nodes.get("open-design-run").disabled,false);
});

test("transient workspace has an independent entrance and retains steady source",async()=>{
  const record={id:"steady-source",project_id:project.id,revision:1,status:"succeeded",created:1,result:{calculation:{inputs:defaults}}};
  const h=harness(async()=>response(record));
  await h.app.loadRecord(record.id);const before=h.app.state();
  await h.app.chooseView("transient");
  assert.equal(h.nodes.get("analysis-title").textContent,"瞬态响应分析");
  assert.equal(h.nodes.get("transient-content").hidden,false);
  assert.equal(h.nodes.get("throttle-content").hidden,true);
  assert.equal(h.nodes.get("analysis-content").hidden,true);
  assert.deepEqual(h.app.state().resultRecord,before.resultRecord);
  await h.app.chooseView("overview");assert.equal(h.nodes.get("transient-content").hidden,true);
});

test("feed workspace has an independent entrance and retains steady source",async()=>{
  const record={id:"steady-source",project_id:project.id,revision:1,status:"succeeded",created:1,result:{calculation:{inputs:defaults}}};
  const h=harness(async()=>response(record));
  await h.app.loadRecord(record.id);const before=h.app.state();
  await h.app.chooseView("feed");
  assert.equal(h.nodes.get("analysis-title").textContent,"供给网络动态");
  assert.equal(h.nodes.get("feed-content").hidden,false);
  assert.equal(h.nodes.get("throttle-content").hidden,true);
  assert.equal(h.nodes.get("analysis-content").hidden,true);
  assert.deepEqual(h.app.state().resultRecord,before.resultRecord);
  await h.app.chooseView("overview");assert.equal(h.nodes.get("feed-content").hidden,true);
});

test("chilldown has an independent view and preserves the selected design",async()=>{
  const record={id:"steady-source",project_id:project.id,revision:1,status:"succeeded",result:{calculation:{inputs:defaults}}};
  const h=harness(async()=>response(record));await h.app.loadRecord(record.id);const before=h.app.state();
  await h.app.chooseView("chilldown");
  assert.equal(h.nodes.get("analysis-title").textContent,"预冷与充填分析");
  assert.equal(h.nodes.get("chilldown-content").hidden,false);
  assert.equal(h.nodes.get("feed-content").hidden,true);
  assert.deepEqual(h.app.state().resultRecord,before.resultRecord);
  assert.deepEqual(h.app.state().draft,before.draft);
  await h.app.chooseView("overview");assert.equal(h.nodes.get("chilldown-content").hidden,true);
});

test("ignition has an independent view and preserves the selected design",async()=>{
  const record={id:"steady-source",project_id:project.id,revision:1,status:"succeeded",result:{calculation:{inputs:defaults}}};
  const h=harness(async()=>response(record));await h.app.loadRecord(record.id);const before=h.app.state();
  await h.app.chooseView("ignition");
  assert.equal(h.nodes.get("analysis-title").textContent,"点火事件与建压分析");
  assert.equal(h.nodes.get("ignition-content").hidden,false);
  assert.equal(h.nodes.get("feed-content").hidden,true);
  assert.deepEqual(h.app.state().resultRecord,before.resultRecord);
  assert.deepEqual(h.app.state().draft,before.draft);
  await h.app.chooseView("overview");assert.equal(h.nodes.get("ignition-content").hidden,true);
});

test("network failure retains draft and disables save/run until a healthy response", async () => {
  let offline = true;
  const {app, nodes} = harness(async () => {if (offline) throw new TypeError("Network down");return response({status: "ok"});});
  const before = app.state();
  await assert.rejects(app.api("/health"), /无法连接/);
  assert.deepEqual(app.state().draft, before.draft);
  assert.equal(nodes.get("connection-banner").hidden, false);
  assert.equal(nodes.get("header-save").disabled, true);
  await assertRunGate({app,nodes}, true);
  offline = false;
  await app.api("/health");
  assert.equal(nodes.get("connection-banner").hidden, true);
  assert.equal(nodes.get("header-save").disabled, false);
});

test("display units are converted to SI before saving and running the returned revision", async () => {
  const calls = [];
  const {app, nodes} = harness(async (url, options) => {
    const payload = options.body && JSON.parse(options.body);
    calls.push({url, method: options.method, payload});
    if (options.method === "PUT") return response({project: {...project, revision: 2, inputs: payload.inputs}, warnings: []});
    if (options.method === "POST") return response({id: "j1", status: "queued"});
    if (url.includes("/revisions")) return response([]);
    if (url === "/api/jobs/j1") return response({id: "j1", project_id: "p1", revision: 2, status: "queued", created: 1, result: null});
    if (url.startsWith("/api/jobs?")) return response([{id: "j1", project_name: "测试方案", revision: 2, status: "queued", created: 1}]);
    throw new Error(`Unexpected request ${url}`);
  });
  nodes.get("parameter-fields").oninput({target: {dataset: {input: "thrust"}, value: "900"}});
  nodes.get("parameter-fields").oninput({target: {dataset: {input: "pc"}, value: "11.5"}});
  nodes.get("project-name").value="运行时保存的方案";
  nodes.get("project-description").value="包含当前参数与名称说明的同一版本";
  nodes.get("project-description").oninput();
  await app.chooseView("design_run");
  await app.runDesign();
  assert.equal(calls[0].method, "PUT");
  assert.equal(calls[0].payload.inputs.thrust, 900000);
  assert.equal(calls[0].payload.inputs.pc, 11500000);
  assert.equal(calls[0].payload.name,"运行时保存的方案");
  assert.equal(calls[0].payload.description,"包含当前参数与名称说明的同一版本");
  assert.equal(calls[1].method, "POST");
  assert.equal(calls[1].payload.revision, 2);
  assert.equal(calls[1].payload.mode, "concept_design");
  assert.equal(Object.hasOwn(calls[0].payload.inputs,"mode"),false);
  assert.equal(app.state().dirty, false);
  assert.equal(app.state().editorBusy, false);
  await assertRunGate({app,nodes}, true);
  assert.equal(app.state().analysisView,"design_monitor");
  assert.equal(app.state().selectedJobId,"j1");
  assert.equal(nodes.get("concept-settings-host").hidden,true);
  assert.equal(nodes.get("concept-settings-host").inert,true);
  assert.equal(nodes.get("design-run-panel").hidden,true);
  assert.equal(nodes.get("design-monitor-panel").hidden,false);
});

test("a conflicting save does not submit a job or discard the current edit", async () => {
  const calls = [];
  const {app, nodes} = harness(async (url, options) => {calls.push(options.method);return response({detail: "此方案已被其他成员更新。"}, 409);});
  nodes.get("parameter-fields").oninput({target: {dataset: {input: "thrust"}, value: "950"}});
  await app.chooseView("design_run");
  await assert.rejects(app.runDesign(), /其他成员更新/);
  assert.deepEqual(calls, ["PUT"]);
  assert.equal(app.state().draft.thrust, 950000);
  assert.equal(app.state().dirty, true);
  assert.equal(app.state().current.revision, 1);
  assert.equal(app.state().editorBusy, false);
});

test("malformed import leaves the edited draft and metadata usable", async () => {
  const {app, nodes} = harness(async () => {throw new Error("Import must not send a request");});
  nodes.get("parameter-fields").oninput({target:{dataset:{input:"thrust"},value:"975"}});
  nodes.get("project-name").value="未保存的方案名称";
  nodes.get("project-name").oninput();
  const before=app.state();
  nodes.get("import-inputs").files=[{text:async()=>'{"thrust": 1000000'}];
  await nodes.get("import-inputs").onchange({target:nodes.get("import-inputs")});
  assert.deepEqual(app.state().draft,before.draft);
  assert.equal(nodes.get("project-name").value,"未保存的方案名称");
  assert.equal(app.state().dirty,true);
  assert.equal(app.state().editorBusy,false);
  assert.ok(nodes.get("project-history-error").textContent.length>0);
  nodes.get("parameter-fields").oninput({target:{dataset:{input:"pc"},value:"12"}});
  assert.equal(app.state().draft.pc,12e6);
  assert.doesNotThrow(()=>app.validateDraft());
});

test("reconnection preserves dirty SI draft and metadata instead of reloading the saved project", async () => {
  const calls = [], checks = [], storage=reviewStorage();
  const {app, nodes} = harness(async (url, options) => {
    calls.push(url);
    if (url === "/api/auth/status") return response({initialized: true, user});
    if (url === "/api/info") return response({...info,design_readiness:true,design_review_persistence:true});
    if(url==="/api/projects/p1"&&options.method==="PUT")return storage.fetch(url,options);
    if (url === "/api/jobs?project_id=p1" || url === "/api/projects/p1/revisions") return response([]);
    if (url === "/api/projects/p1/design-readiness") {
      assert.equal(JSON.parse(options.body).inputs.thrust,975000);
      return response({valid:true,issues:[]});
    }
    throw new Error(`Reconnection must not overwrite the draft: ${url}`);
  },{timers:{setTimeout(callback,delay){if(delay===300)checks.push(callback);return 1;}}});
  nodes.get("parameter-fields").oninput({target: {dataset: {input: "thrust"}, value: "975"}});
  nodes.get("project-description").value = "重连期间保留的方案说明";
  nodes.get("project-description").oninput();
  await app.initialize();
  assert.equal(app.state().draft.thrust, 975000);
  assert.equal(app.state().dirty, true);
  assert.equal(nodes.get("project-description").value,"重连期间保留的方案说明");
  assert.equal(calls.includes("/api/projects/p1"), false);
  assert.equal(checks.length,1);
  await assertRunGate({app,nodes},true);
  await checks[0]();
  await assertRunGate({app,nodes},true);
  await confirmGroup(nodes,"design");
  await assertRunGate({app,nodes},false);
  assert.equal(storage.project.inputs.thrust,975000);
  assert.equal(storage.project.description,"重连期间保留的方案说明");
});

test("HTTP validation failures keep the service online", async () => {
  const {app, nodes} = harness(async () => response({detail: "输入格式不正确。"}, 422));
  await assert.rejects(app.api("/projects/p1", "PUT", {}), /输入格式不正确/);
  assert.equal(app.state().connected, true);
  assert.equal(nodes.get("connection-banner").hidden, true);
});

test("empty numeric and fractional integer inputs fail before saving even in hidden groups", async () => {
  for (const [key, value, message] of [["pc", "", /有限数值/], ["throttle_steps", "4.5", /整数/]]) {
    const {app, nodes} = harness(async () => {throw new Error("Validation should prevent requests");});
    nodes.get("parameter-fields").oninput({target: {dataset: {input: key}, value}});
    await app.chooseView("design_run");
    await assert.rejects(app.runDesign(), message);
    assert.equal(app.state().dirty, true);
    assert.equal(app.state().editorBusy, false);
  }
});

test("editor lock keeps replacement controls inert and clears the lock after failure", async () => {
  const {app, nodes} = harness(async () => response({}));
  await assert.rejects(app.withEditorLock(async () => {
    nodes.get("parameter-fields").innerHTML = '<input data-input="pc">';
    assert.equal(nodes.get("project-form").inert, true);
    assert.equal(nodes.get("project-history").inert, true);
    assert.equal(nodes.get("header-run").disabled, true);
    throw new Error("Submission failed");
  }), /Submission failed/);
  assert.equal(nodes.get("project-form").inert, false);
  assert.equal(nodes.get("project-history").inert, false);
  assert.equal(app.state().editorBusy, false);
});

test("an older refresh must not erase a newly submitted active job", async () => {
  let releaseOldJobs;
  let jobLists = 0;
  const oldJobs = new Promise(resolve => {releaseOldJobs = resolve;});
  const {app, nodes} = harness(async url => {
    if (url.includes("/revisions")) return response([]);
    if (url === "/api/jobs/j1") return response({id: "j1", project_id: "p1", revision: 1, status: "queued", created: 1, result: null});
    if (url.startsWith("/api/jobs?")) {
      if (++jobLists === 1) return oldJobs;
      return response([{id: "j1", project_name: "测试方案", revision: 1, status: "queued", created: 1}]);
    }
    throw new Error(`Unexpected request ${url}`);
  });
  const previousRefresh = app.refreshProjectSide();
  await app.refreshProjectSide();
  assert.equal(app.state().projectJobs[0].id, "j1");
  releaseOldJobs(response([]));
  await previousRefresh;
  assert.equal(app.state().projectJobs[0]?.id, "j1");
  await assertRunGate({app,nodes}, true);
});

test("system editor hides detailed fields but preserves and validates their case values", async () => {
  const systemSchema = {...schema, system_groups: [{id:"system", title:"系统参数",
    fields:fields.filter(f=>f.key!=="throttle_steps")}]};
  let saved;
  const {app,nodes} = harness(async (url,options) => {
    if(url==="/api/auth/status")return response({initialized:true,user});
    if(url==="/api/info")return response({...info,ui_schema:systemSchema});
    if(url==="/api/projects")return response([project]);
    if(url==="/api/projects/p1" && options.method==="PUT"){
      saved=JSON.parse(options.body);return response({project:{...project,...saved,revision:2},warnings:[]});
    }
    if(url==="/api/projects/p1")return response(project);
    if(url==="/api/jobs?project_id=p1"||url==="/api/projects/p1/revisions")return response([]);
    throw new Error(`Unexpected request ${url}`);
  });
  await app.initialize();
  assert.match(nodes.get("parameter-fields").innerHTML,/data-input="pc"/);
  assert.doesNotMatch(nodes.get("parameter-fields").innerHTML,/throttle_steps/);
  nodes.get("parameter-fields").oninput({target:{dataset:{input:"pc"},value:"12"}});
  await app.saveProject();
  assert.equal(saved.inputs.throttle_steps,5);
  const before=app.state().draft;
  assert.throws(()=>app.applyInputDocument({...defaults,throttle_steps:4.5}),/整数|throttle_steps|步数/);
  assert.deepEqual(app.state().draft,before);
});

test("unsupported reference stays read-only, preserves the active case and escapes sources", async () => {
  const reference={key:"merlin1d_reference",name:"Merlin 1D",propellants:"LOX / RP-1",
    cycle_label:"燃气发生器循环（GG，开式）",applicable:false,preset_key:null,
    compatibility_reason:"仅资料；不支持 RP-1 计算",model_note:"参考说明",
    reference_facts:[{label:"推进剂",value:"<img src=x onerror=alert(1)>"}],
    sources:[{title:"危险链接",url:"javascript:alert(1)"},{title:"官方资料",url:"https://www.spacex.com/example.pdf"}]};
  const {app,nodes}=harness(async url=>{
    if(url==="/api/auth/status")return response({initialized:true,user});
    if(url==="/api/info")return response({...info,reference_engines:[reference]});
    if(url==="/api/projects")return response([project]);
    if(url==="/api/projects/p1")return response(project);
    if(url==="/api/jobs?project_id=p1"||url==="/api/projects/p1/revisions")return response([]);
    throw new Error(`Reference selection must not send mutations: ${url}`);
  });
  await app.initialize();
  assert.match(nodes.get("project-preset").innerHTML,/Merlin 1D · 仅资料/);
  assert.doesNotMatch(nodes.get("new-preset").innerHTML,/Merlin/);
  nodes.get("project-preset").value=reference.key;
  nodes.get("project-preset").onchange();
  assert.equal(nodes.get("apply-preset").disabled,true);
  assert.match(nodes.get("preset-context").textContent,/当前计算方案保持不变/);
  const before=app.state().draft;
  nodes.get("apply-preset").onclick();
  assert.deepEqual(app.state().draft,before);
  assert.equal(app.state().dirty,false);
  nodes.get("open-references").onclick();
  assert.match(nodes.get("reference-content").innerHTML,/&lt;img/);
  assert.doesNotMatch(nodes.get("reference-content").innerHTML,/href="javascript:/);
  assert.match(nodes.get("reference-content").innerHTML,/href="https:\/\/www.spacex.com/);
});

test("reference dialog binds each fact to its own locator and separates public facts from sample assumptions",()=>{
  const reference={key:"ffsc_reference",name:"全流量补燃循环工程示例",propellants:"液氧／甲烷",cycle_label:"全流量补燃循环",
    applicable:true,preset_key:"test",reference_facts:[
      {label:"历史整机氧燃质量比",value:"3.60",source_title:"2019 原始报告",source_url:"https://www.faa.gov/report.pdf",source_locator:"第 1 页 §2",scope:"历史海平面基准；不能证明 Raptor 3 实机值",checked_on:"2026-11-05"},
      {label:"推力指标",value:"公开指标",source_title:"厂商说明",source_url:"https://www.spacex.com/updates",source_locator:"推力段落",scope:"仅支持所述指标"},
      {label:"旧条目",value:"兼容旧资料"}],
    sources:[{title:"2019 原始报告",url:"https://www.faa.gov/report.pdf",publisher:"FAA",published_on:"2019",locator:"第 1 页 §2",scope:"历史海平面参考",
      verification_note:"检索文本核验，原文直连超时"}],
    parameter_evidence:[{name:"pc",value_si:33e6,classification:"engineering_assumption",reviewed_on:"2026-11-05",source_path:"src/engine_presets.py:42"}]};
  const catalog={...info,reference_engines:[reference]},before=copy(catalog);
  const {app,nodes}=harness(async()=>{throw Error("Inspecting reference evidence must not change or calculate a case");});
  app.seed(copy({current:project,draft:defaults,dirty:false,user,info:catalog,groups:schema.groups,fields,page:"project",projectJobs:[],connected:true}));
  nodes.get("project-preset").value="test";nodes.get("open-references").onclick();
  const content=nodes.get("reference-content").innerHTML;
  assert.match(content,/参考对象公开信息/);assert.match(content,/逐条来源与支持范围/);
  assert.match(content,/<td>历史整机氧燃质量比<\/td><td>3\.60<\/td><td>[\s\S]*?2019 原始报告[\s\S]*?第 1 页 §2[\s\S]*?不能证明 Raptor 3 实机值[\s\S]*?<\/tr>/);
  assert.match(content,/<td>推力指标<\/td><td>公开指标<\/td><td>[\s\S]*?厂商说明[\s\S]*?推力段落[\s\S]*?<\/tr>/);
  assert.match(content,/旧条目<\/td><td>兼容旧资料<\/td><td>逐条来源未登记/);
  for(const text of ["发布机构：FAA","发布日期：2019","检索文本核验，原文直连超时","示例参数依据与项目设定","项目设定 · 未登记外部数值依据","说明更新 2026-11-05"])
    assert(content.includes(text),text);
  assert.deepEqual(copy(app.state().draft),defaults);assert.equal(app.state().dirty,false);assert.deepEqual(catalog,before);
});

test("reference fact and source metadata are escaped and unsafe links stay plain text",()=>{
  const attack='<img src=x onerror="bad()">';
  const reference={key:"unsafe_metadata",name:"资料转义",reference_facts:[{label:attack,value:attack,source_title:attack,
    source_url:"https://user:secret@example.com/report",source_locator:attack,scope:attack,checked_on:attack,reviewed_on:attack,verification_note:attack}],
    sources:[{title:attack,url:"javascript:alert(1)",publisher:attack,published_on:attack,locator:attack,scope:attack,verification_note:attack}]};
  const {app,nodes}=harness(async()=>{throw Error("Read-only evidence");});
  app.seed(copy({current:project,draft:defaults,dirty:false,user,info:{...info,reference_engines:[reference]},groups:schema.groups,fields,page:"project",projectJobs:[],connected:true}));
  nodes.get("open-references").onclick();
  const content=nodes.get("reference-content").innerHTML;
  assert.match(content,/&lt;img/);assert.doesNotMatch(content,/<img|<script| onerror="bad|href=|javascript:|secret/);
});

test("budget reference explains historical compatibility without offering a routine load",()=>{
  const reference={key:"merlin1d_reference",name:"Merlin 1D",propellants:"LOX / RP-1",
    cycle_label:"GG",applicable:true,preset_key:"merlin1d_gg",
    compatibility_reason:"可加载液氧/煤油 GG 工程示例；冷却采用指定吸热量。"};
  const catalog={...info,presets:[...info.presets,{key:"merlin1d_gg",routine_design:false,
    inputs:{...defaults,propellant_pair:"lox_kerosene",cooling_bartz_mode:0}}],reference_engines:[reference]};
  const {app,nodes}=harness(async()=>{throw Error("Reading a reference must not save or load a design");});
  app.seed(copy({current:project,draft:defaults,dirty:false,user,info:catalog,
    groups:schema.groups,fields,page:"project",projectJobs:[],connected:true}));
  nodes.get("project-preset").value="merlin1d_gg";
  nodes.get("open-references").onclick();
  const content=nodes.get("reference-content").innerHTML;
  assert.match(content,/仅公开资料/);
  assert.match(content,/历史预算冷却模型，不作为常规新设计入口/);
  assert.match(content,/已有方案可从“方案与计算记录”载入复现/);
  assert.doesNotMatch(content,/可加载/);
  assert.deepEqual(copy(app.state().draft),defaults);
  assert.equal(app.state().dirty,false);
  assert.match(reference.compatibility_reason,/可加载/);
});


test("an unsupported cycle cannot replace an FFSC design even without server scope metadata",()=>{
  const h=harness(async()=>{throw Error("Import must not submit or save");});
  h.app.seed(copy({current:project,draft:defaults,dirty:false,user,info,
    groups:schema.groups,fields,page:"project",projectJobs:[],connected:true}));
  const before=h.app.state().draft;
  assert.throws(()=>h.app.applyInputDocument({inputs:{...defaults,cycle_type:"gg"}}),/仅支持液氧甲烷全流量补燃循环/);
  assert.deepEqual(h.app.state().draft,before);
});

// Model switching tests use distinct inputs for every fuel/cooling decision.
// These are UI fixtures; the real numerical cases are covered by Python tests.
const modelDefaults={...defaults,propellant_pair:"lox_methane",
  fuel_inlet_temp:100,ox_inlet_temp:70,cooling_bartz_mode:1,cooling_dp_auto:0,
  cooling_heat_load:63e6,cooling_dp_pa:2e6};
const modelPresets=[
  {key:"ffsc_low",label:"FFSC 低推力样例",inputs:{...modelDefaults,
    thrust:845000,pc:9.7e6,fuel_inlet_temp:105,ox_inlet_temp:90,cooling_heat_load:20e6}},
  {key:"ffsc_base",label:"FFSC 基准样例",inputs:{...modelDefaults}},
  {key:"raptor3_ffsc",label:"Raptor · 工程示例",inputs:{...modelDefaults,
    thrust:2450000,pc:33e6,fuel_inlet_temp:110,ox_inlet_temp:85,cooling_dp_auto:1,cooling_heat_load:100e6}},
];
const modelGroups=[
  {id:"architecture",title:"循环与推进剂",fields:[
    {key:"propellant_pair",default:"lox_methane",label:"推进剂",choices:[{value:"lox_methane",label:"液氧/甲烷"}]},
    {key:"cycle_type",default:"ffsc",label:"循环",choices:[{value:"ffsc",label:"FFSC"}]},
  ]},
  {id:"design",title:"目标与供给",fields:[...fields.filter(f=>f.key!=="cycle_type"),
    ...["fuel_inlet_temp","ox_inlet_temp","cooling_bartz_mode","cooling_dp_auto","cooling_heat_load","cooling_dp_pa"]
      .map(key=>({key,default:modelDefaults[key],label:key,type:"number",
        ...(key==="cooling_bartz_mode"?{choices:[{value:0,label:"指定吸热量"},{value:1,label:"Bartz"}]}:{})}))]},
];
const modelFields=modelGroups.flatMap(group=>group.fields);
const modelInfo={...info,presets:modelPresets,ui_schema:{defaults:modelDefaults,groups:modelGroups,
  propellant_models:[{id:"lox_methane",label:"液氧/甲烷",supported_cycles:["ffsc"],limitations:[]}]}};
const modelProject={...project,inputs:modelPresets[1].inputs};
function modelHarness(fetcher=async()=>{throw Error("Switching examples must not send requests");}){
  const h=harness(fetcher);
  h.app.seed(copy({current:modelProject,draft:modelProject.inputs,dirty:false,user,info:modelInfo,
    groups:modelGroups,fields:modelFields,page:"project",projectJobs:[],connected:true}));
  h.app.fillProject(copy(modelProject));
  return h;
}
function browseModel(h,key){h.nodes.get("project-preset").value=key;h.nodes.get("project-preset").onchange();}
function selectModel(h,key){browseModel(h,key);h.nodes.get("apply-preset").onclick();}

test("browsing examples preserves both saved and edited inputs, results and review status",()=>{
  for(const edited of [false,true]){
    const h=modelHarness(()=>{throw Error("Browsing must not write or run");});
    if(edited)editField(h,"thrust","910");
    const before=h.app.state();
    for(const item of [...modelPresets,{key:""}]){
      browseModel(h,item.key);
      assert.deepEqual(h.app.state(),before);
      assert.equal(h.nodes.get("project-preset").value,item.key);
      assert.equal(h.nodes.get("new-from-example").disabled,!item.key);
    }
  }
});

test("creating from a browsed example opens the creation dialog without replacing this scheme",()=>{
  const h=modelHarness(()=>{throw Error("Opening creation must not send a write");});
  const before=h.app.state();
  browseModel(h,"ffsc_low");h.nodes.get("new-from-example").onclick();
  assert.equal(h.nodes.get("new-dialog").open,true);
  assert.equal(h.nodes.get("new-preset").value,"ffsc_low");
  assert.ok(h.nodes.get("new-preset-description").textContent);
  assert.deepEqual(h.app.state(),before);
});

test("property references are an auxiliary view and preserve the active draft",async()=>{
  const h=modelHarness(()=>{throw Error("Reference navigation must not write or run");});
  editField(h,"thrust","910");const before=h.app.state();
  await h.app.chooseView("property_library");
  assert.equal(h.app.state().analysisView,"property_library");
  assert.deepEqual(h.app.state().draft,before.draft);
  assert.equal(h.app.state().dirty,true);
  assert.equal(h.nodes.get("parameter-pane").hidden,true);
  assert.equal(h.nodes.get("analysis-content").hidden,false);
  h.nodes.get("parameter-help").onclick();
  assert.equal(h.app.state().helpChapter,"properties");
  assert.deepEqual(h.app.state().draft,before.draft);
});
function editField(h,key,value){h.nodes.get("parameter-fields").oninput({target:{dataset:{input:key},value}});}

const ffscInputs={...defaults,cycle_type:"ffsc",use_target_preburner_temp:0,use_target_preburner_mr:0,
  preburner_temp_target:750,preburner_mr:61,split_guess:.07,cooling_bartz_mode:1,hidden_fixed_q:91e6};
const ffscCanonicalFields=[...fields,...Object.keys(ffscInputs).filter(key=>!(key in defaults)).map(key=>
  ({key,label:key,default:ffscInputs[key],type:"number"}))];
const ffscSystemGroups=[{id:"design",title:"预燃室设计",fields:[
  {key:"preburner_design_mode",label:"预燃室目标",virtual:true,choices:[
    {value:"temperature",label:"指定预燃室温度"},{value:"mixture_ratio",label:"指定预燃室混合比"}]},
  {...ffscCanonicalFields.find(f=>f.key==="preburner_temp_target"),visibility:{preburner_modes:["temperature"]}},
  {...ffscCanonicalFields.find(f=>f.key==="preburner_mr"),role:"design_input",roles_by_mode:{temperature:"initial_guess"},visibility:{preburner_modes:["temperature","mixture_ratio"]}},
  {...ffscCanonicalFields.find(f=>f.key==="split_guess"),role:"initial_guess",visibility:{preburner_modes:["legacy_free_split"]}},
  {...ffscCanonicalFields.find(f=>f.key==="cooling_bartz_mode"),choices:[{value:0,label:"给定热量"},{value:1,label:"Bartz"}]},
  {...ffscCanonicalFields.find(f=>f.key==="hidden_fixed_q"),visibility:{all:[{key:"cooling_bartz_mode",op:"lte",value:.5}]}},
]}];
const ffscInfo={...info,ui_schema:{defaults:ffscInputs,groups:[{id:"canonical",fields:ffscCanonicalFields}],system_groups:ffscSystemGroups},
  presets:[{key:"modern",label:"温度目标示例",inputs:{...ffscInputs,use_target_preburner_temp:1,use_target_preburner_mr:1}}]};
function ffscHarness(fetcher=async()=>{throw Error("Mode changes must remain a local draft");}){
  const h=harness(fetcher),ffscProject={...project,inputs:ffscInputs};
  h.app.seed(copy({current:ffscProject,draft:ffscInputs,dirty:false,user,info:ffscInfo,
    groups:ffscSystemGroups,fields:ffscCanonicalFields,page:"project",projectJobs:[],connected:true}));
  h.app.fillProject(copy(ffscProject));
  return h;
}

const ffscOnlyScope={id:"ffsc_lox_methane",cycle_type:"ffsc",propellant_pair:"lox_methane",label:"液氧甲烷全流量补燃循环",history_read_only:true};
function scopedHarness(inputs={...ffscInputs,propellant_pair:"lox_methane"},fetcher=async()=>{throw Error("Scope edits must not submit requests");}){
  const h=harness(fetcher),selected={...project,inputs};
  const canonical=[...ffscCanonicalFields,{key:"propellant_pair",label:"推进剂",type:"string",default:"lox_methane"}];
  const scopedGroups=[...ffscSystemGroups,{id:"architecture",fields:canonical.filter(f=>["cycle_type","propellant_pair"].includes(f.key)).map(f=>({...f,read_only:true}))}];
  const catalog={...ffscInfo,design_scope:ffscOnlyScope,ui_schema:{...ffscInfo.ui_schema,defaults:{...ffscInputs,propellant_pair:"lox_methane"}},presets:[...ffscInfo.presets,{key:"old_gg",label:"旧GG",inputs:{...ffscInputs,cycle_type:"gg"}}]};
  h.app.seed(copy({current:selected,draft:inputs,dirty:false,user,info:catalog,groups:scopedGroups,fields:canonical,page:"project",projectJobs:[],connected:true}));
  h.app.fillProject(copy(selected));
  return h;
}

test("FFSC platform rejects incompatible imports and stale GG presets without changing the draft",()=>{
  const h=scopedHarness(),before=h.app.state();
  for(const inputs of [{...before.draft,cycle_type:"gg"},{...before.draft,propellant_pair:"lox_kerosene"}]){
    assert.throws(()=>h.app.applyInputDocument(inputs),/仅支持液氧甲烷全流量补燃循环/);
    assert.deepEqual(h.app.state().draft,before.draft);
  }
  selectModel(h,"old_gg");
  assert.deepEqual(h.app.state().draft,before.draft);
  editField(h,"cycle_type","gg");
  assert.equal(h.app.state().draft.cycle_type,"ffsc");
  assert.equal(h.app.state().dirty,false);
});

test("out-of-scope saved projects remain read-only and export their original inputs",async()=>{
  const original={cycle_type:"gg",propellant_pair:"lox_kerosene",thrust:912};
  const h=scopedHarness(original),before=h.app.state();
  assert.equal(before.analysisView,"history");
  assert.equal(h.nodes.get("project-scope-note").hidden,false);
  assert.match(h.nodes.get("project-scope-note").textContent,/历史记录仅供原始数据导出/);
  for(const id of ["header-run","design-run-submit","header-save","history-save","choose-import","project-preset","project-name","project-description"])
    assert.equal(h.nodes.get(id).disabled,true,id);
  assert.equal(h.nodes.get("export-inputs").disabled,false);
  assert.deepEqual(copy(h.app.buildInputBackup().inputs),original);
  await assert.rejects(h.app.saveProject(),/仅支持液氧甲烷/);
  await assert.rejects(h.app.runDesign(),/仅支持液氧甲烷/);
  assert.throws(()=>h.app.applyInputDocument(ffscInputs),/仅支持液氧甲烷/);
  editField(h,"thrust","999");selectModel(h,"modern");
  h.nodes.get("project-preset").value="";h.nodes.get("project-preset").onchange();
  h.app.upgradeThermochemistryData();
  assert.deepEqual(h.app.state().draft,before.draft);
  assert.equal(h.app.state().dirty,false);
  assert.deepEqual(copy(h.app.state().current.inputs),original);
  await h.app.chooseView("throttle");
  assert.equal(h.app.state().analysisView,"history");
  assert.equal(h.throttleLifecycle.mounts,0);
  await h.app.chooseConceptSection("settings");
  assert.equal(h.app.state().conceptSection,before.conceptSection);
  assert.equal(h.app.state().analysisView,"history");
  assert.equal(h.nodes.get("parameter-pane").hidden,true);
});

test("a conflicting archived result identity cannot open a new operating study on an FFSC project",async()=>{
  for(const source of ["inputs","submitted_inputs","calculation"]){
    const inputs={...ffscInputs,propellant_pair:"lox_methane"};
    const record={id:"legacy-source",project_id:"p1",status:"succeeded",inputs:{...inputs},result:{submitted_inputs:{...inputs},calculation:{inputs:{...inputs}}}};
    if(source==="inputs")record.inputs.cycle_type="gg";
    else if(source==="submitted_inputs")record.result.submitted_inputs.cycle_type="gg";
    else record.result.calculation.inputs.cycle_type="gg";
    const h=scopedHarness(inputs,async()=>response(record));
    await h.app.loadRecord(record.id);
    await h.app.chooseView("throttle");
    assert.equal(h.app.state().analysisView,"history",source);
    assert.equal(h.throttleLifecycle.mounts,0,source);
    assert.deepEqual(copy(h.app.state().resultRecord),record);
  }
});

test("an unsupported record arriving on the run page hides execution controls and cannot submit",async()=>{
  const inputs={...ffscInputs,propellant_pair:"lox_methane"};
  const record={id:"legacy-run-source",project_id:"p1",status:"succeeded",
    inputs:{...inputs,cycle_type:"gg"},result:{submitted_inputs:{...inputs}}};
  const calls=[];
  let resolveRecord;
  const h=scopedHarness(inputs,async(url,options)=>{
    calls.push([url,options?.method||"GET"]);
    assert.equal(url,"/api/jobs/legacy-run-source");
    assert.equal(options?.method||"GET","GET");
    return new Promise(resolve=>{resolveRecord=resolve;});
  });
  await h.app.chooseView("design_run");
  assert.equal(h.app.state().analysisView,"design_run");
  assert.equal(h.nodes.get("header-run").hidden,false);
  const before=h.app.state(),pending=h.app.loadRecord(record.id);
  assert.equal(h.app.state().analysisView,"design_run");
  resolveRecord(response(record));
  await pending;
  assert.equal(h.app.state().analysisView,"history");
  assert.equal(h.nodes.get("header-run").hidden,true);
  assert.equal(h.nodes.get("header-run").disabled,true);
  assert.equal(h.nodes.get("design-run-panel").hidden,true);
  assert.equal(h.nodes.get("design-run-submit").disabled,true);
  // A stale callback must not bypass the route change or create a calculation.
  await h.nodes.get("header-run").onclick();
  assert.equal(h.app.state().analysisView,"history");
  assert.deepEqual(calls,[["/api/jobs/legacy-run-source","GET"]]);
  assert.deepEqual(h.app.state().current,before.current);
  assert.deepEqual(h.app.state().draft,before.draft);
  assert.deepEqual(copy(h.app.state().resultRecord),record);
});

test("FFSC scoped projects still save their canonical values",async()=>{
  let saved;
  const h=scopedHarness(undefined,async(url,options)=>{
    saved=JSON.parse(options.body);return response({project:{...project,inputs:saved.inputs,revision:2},warnings:[]});
  });
  editField(h,"thrust","915");
  await h.app.saveProject();
  assert.equal(saved.inputs.cycle_type,"ffsc");
  assert.equal(saved.inputs.propellant_pair,"lox_methane");
  assert.equal(saved.inputs.thrust,915000);
  assert.equal(h.nodes.get("project-scope-note").hidden,true);
});

test("historical FFSC drafts and backups discard only established retired fields",()=>{
  const retired={gg_exhaust_expansion_ratio:10,gg_fraction_guess:.03,gg_injector_dp_ratio:.2,
    gg_layout:"dual_shaft",gg_mech_efficiency:.9,gg_mr_target:.3,gg_temp_limit:1200,
    gg_temp_target:850,gg_turbine_backpressure_pa:2e5,use_target_gg_temp:1,
    injector_dp_ratio_ox:.2,injector_dp_ratio_fuel:.2};
  const original={...ffscInputs,propellant_pair:"lox_methane",...retired},h=scopedHarness(original);
  assert.deepEqual(copy(h.app.state().current.inputs),original,"stored historical inputs stay untouched");
  for(const key of Object.keys(retired))assert.ok(!Object.hasOwn(h.app.state().draft,key),key);
  const backup=h.app.buildInputBackup();
  assert.equal(backup.inputs.pc,original.pc);
  assert.equal(backup.inputs.thrust,original.thrust);
  const fresh=scopedHarness();
  fresh.app.applyInputDocument({format:"lre.workbench.inputs",schema_version:1,inputs:original});
  assert.deepEqual(copy(fresh.app.state().draft),copy(backup.inputs));
  fresh.app.applyInputDocument(backup);
  assert.deepEqual(copy(fresh.app.state().draft),copy(backup.inputs));
  const before=fresh.app.state().draft;
  assert.throws(()=>fresh.app.applyInputDocument({inputs:{...original,gg_unrecognized:1}}),/无法识别的参数/);
  assert.throws(()=>fresh.app.applyInputDocument({inputs:{...original,cycle_type:"gg"}}),/仅支持液氧甲烷/);
  assert.deepEqual(fresh.app.state().draft,before);
  const archived=scopedHarness({...original,cycle_type:"gg"});
  assert.deepEqual(copy(archived.app.buildInputBackup().inputs),{...original,cycle_type:"gg"});
});

test("retired saved equipment cannot append valve analysis in project or history dialogs",async()=>{
  for(const identity of ["record","submitted","calculation","equipment"]){
    const inputs={...ffscInputs,propellant_pair:"lox_methane"};
    const record={id:"retired-result",project_id:"p1",revision:1,status:"succeeded",inputs:{...inputs},
      result:{submitted_inputs:{...inputs},calculation:{inputs:{...inputs},components:{}}},
      valve_requirements:{scope:"concept_valve_requirements",source:"saved_calculation_only",rows:[{id:"retired-valve",label:"旧循环阀门"}]}};
    if(identity==="record")record.inputs.cycle_type="gg";
    else if(identity==="submitted")record.result.submitted_inputs.cycle_type="gg";
    else if(identity==="calculation")record.result.calculation.inputs.cycle_type="gg";
    else record.result.calculation.components.gas_generator={};
    const h=scopedHarness(inputs,async()=>response(record));
    await h.app.showResult(record.id);
    assert.equal(h.app.state().analysisView,"history",identity);
    assert.doesNotMatch(h.nodes.get("analysis-content").innerHTML,/旧循环阀门|阀门需求交接/);
    h.app.switchPage("jobs");
    await h.app.showResult(record.id);
    assert.equal(h.nodes.get("result-dialog").open,true);
    assert.doesNotMatch(h.nodes.get("result-content").innerHTML,/旧循环阀门|阀门需求交接/);
    assert.equal(h.nodes.get("download-result").hidden,false,"raw result export remains available");
  }
});

function pumpModeHarness(fetcher){
  const key="lox_pump_speed_rpm_override",nq="lox_pump_specific_speed_target";
  const pumpDefaults={...ffscInputs,[key]:26500,[nq]:30,fuel_pump_speed_rpm_override:38000};
  const inputs={...pumpDefaults,[key]:27123,[nq]:32};
  const canonical=[...ffscCanonicalFields,...[key,nq,"fuel_pump_speed_rpm_override"].map(key=>({key,label:key,default:pumpDefaults[key],type:"number"}))];
  const pumpFields=[{key:"lox_pump_speed_mode",label:"转速方式",virtual:true,pump_speed_key:key,choices:[{value:"specified",label:"给定初选转速"},{value:"estimated",label:"按目标比转速估算"}]},
    {...canonical.find(f=>f.key===key),visibility:{all:[{key,op:"neq",value:0}]}},
    {...canonical.find(f=>f.key===nq),visibility:{all:[{key,op:"eq",value:0}]}}];
  const system=[...ffscSystemGroups,{id:"pumps",title:"泵方案与尺寸初估",fields:pumpFields}];
  const catalog={...ffscInfo,ui_schema:{defaults:pumpDefaults,groups:[{id:"canonical",fields:canonical}],system_groups:system}};
  const selected={...project,inputs};
  const h=harness(fetcher);
  h.app.seed(copy({current:selected,draft:inputs,dirty:false,user,info:catalog,groups:system,fields:canonical,page:"project",projectJobs:[],connected:true}));
  h.app.fillProject(copy(selected));
  return h;
}

test("pump mode edits preserve the candidate across toggles and save only canonical inputs",async()=>{
  let saved;
  const h=pumpModeHarness(async(url,options)=>{
    saved=JSON.parse(options.body);return response({project:{...project,inputs:saved.inputs,revision:2},warnings:[]});
  });
  const original=h.app.state().draft;
  editField(h,"lox_pump_speed_mode","estimated");
  assert.equal(h.app.state().draft.lox_pump_speed_rpm_override,0);
  assert.match(h.nodes.get("parameter-fields").innerHTML,/data-input="lox_pump_specific_speed_target"/);
  assert.doesNotMatch(h.nodes.get("parameter-fields").innerHTML,/data-input="lox_pump_speed_rpm_override"/);
  editField(h,"preburner_design_mode","temperature");
  editField(h,"lox_pump_speed_mode","specified");
  assert.equal(h.app.state().draft.lox_pump_speed_rpm_override,original.lox_pump_speed_rpm_override);
  editField(h,"lox_pump_speed_mode","estimated");
  await h.app.saveProject();
  assert.equal(saved.inputs.lox_pump_speed_rpm_override,0);
  assert.equal(saved.inputs.lox_pump_specific_speed_target,32);
  assert.equal(saved.inputs.fuel_pump_speed_rpm_override,38000);
  assert.ok(!Object.hasOwn(saved.inputs,"lox_pump_speed_mode"));
  assert.ok(!Object.hasOwn(h.app.buildInputBackup().inputs,"lox_pump_speed_mode"));
  editField(h,"lox_pump_speed_mode","specified");
  assert.equal(h.app.state().draft.lox_pump_speed_rpm_override,27123);
});

test("import and project load reset candidate memory while preset undo restores it",()=>{
  const h=pumpModeHarness();
  editField(h,"lox_pump_speed_mode","estimated");
  const estimated=h.app.state().draft;
  selectModel(h,"modern");
  h.nodes.get("undo-preset").onclick();
  assert.deepEqual(h.app.state().draft,estimated);
  editField(h,"lox_pump_speed_mode","specified");
  assert.equal(h.app.state().draft.lox_pump_speed_rpm_override,27123);
  h.app.applyInputDocument(estimated);
  assert.equal(h.app.state().draft.lox_pump_speed_rpm_override,0);
  editField(h,"lox_pump_speed_mode","specified");
  assert.equal(h.app.state().draft.lox_pump_speed_rpm_override,26500);
  editField(h,"lox_pump_speed_rpm_override","29000");
  editField(h,"lox_pump_speed_mode","estimated");
  h.app.fillProject({...project,id:"p2",inputs:estimated});
  editField(h,"lox_pump_speed_mode","specified");
  assert.equal(h.app.state().draft.lox_pump_speed_rpm_override,26500);
});

function turbineModeHarness(fetcher){
  const key="turb_ox_n_stages_user";
  const turbineDefaults={...ffscInputs,[key]:0,turb_fuel_n_stages_user:0,turb_ox_reaction_degree:.45,turb_ox_meanline_flow_coefficient:.6,turbine_meanline_mode:"disabled"};
  const canonical=[...ffscCanonicalFields,...[key,"turb_fuel_n_stages_user","turb_ox_reaction_degree","turb_ox_meanline_flow_coefficient","turbine_meanline_mode"].map(key=>({key,label:key,default:turbineDefaults[key],type:key.endsWith("stages_user")?"int":typeof turbineDefaults[key]==="number"?"number":"string"}))];
  const oxVisibility={key:"cycle_type",op:"eq",value:"ffsc"};
  const turbineFields=[{key:"turb_ox_stage_mode",label:"级数方式",virtual:true,turbine_stages_key:key,visibility:oxVisibility,choices:[{value:"estimated",label:"按现有规则估算"},{value:"specified",label:"给定初选级数"}]},
    {...canonical.find(f=>f.key===key),visibility:{...oxVisibility,all:[{key,op:"neq",value:0}]}},
    {...canonical.find(f=>f.key==="turbine_meanline_mode"),choices:[{value:"disabled",label:"关闭"},{value:"equal_work",label:"等比功均径校核"}]},
    ...["turb_ox_reaction_degree","turb_ox_meanline_flow_coefficient"].map(key=>({...canonical.find(f=>f.key===key),visibility:{...oxVisibility,all:[{key:"turbine_meanline_mode",op:"neq",value:"disabled"}]}}))];
  const system=[...ffscSystemGroups,{id:"turbines",title:"涡轮方案与尺寸初估",fields:turbineFields}];
  const catalog={...ffscInfo,ui_schema:{defaults:turbineDefaults,groups:[{id:"canonical",fields:canonical}],system_groups:system}};
  const selected={...project,inputs:turbineDefaults};
  const h=harness(fetcher);
  h.app.seed(copy({current:selected,draft:turbineDefaults,dirty:false,user,info:catalog,groups:system,fields:canonical,page:"project",projectJobs:[],connected:true}));
  h.app.fillProject(copy(selected));
  return h;
}

test("manual turbine stages stay empty and block save until the designer supplies a count",async()=>{
  let requests=0;
  const h=turbineModeHarness(async()=>{requests++;throw Error("Incomplete draft must stay local");});
  editField(h,"turb_ox_stage_mode","specified");
  assert.equal(h.app.state().draft.turb_ox_n_stages_user,null);
  assert.match(h.nodes.get("parameter-fields").innerHTML,/data-input="turb_ox_n_stages_user"/);
  await assert.rejects(h.app.saveProject(),/turb_ox_n_stages_user必须是整数/);
  assert.equal(requests,0);
  editField(h,"turb_ox_n_stages_user","2");
  assert.doesNotThrow(()=>h.app.validateDraft());
  assert.equal(h.app.state().draft.turb_ox_reaction_degree,.45);
});

test("turbine stage selection saves canonical values and restores the candidate after save",async()=>{
  let saved;
  const h=turbineModeHarness(async(url,options)=>{
    saved=JSON.parse(options.body);return response({project:{...project,inputs:saved.inputs,revision:2},warnings:[]});
  });
  editField(h,"turb_ox_stage_mode","specified");
  editField(h,"turb_ox_n_stages_user","3");
  editField(h,"turb_ox_stage_mode","estimated");
  assert.doesNotMatch(h.nodes.get("parameter-fields").innerHTML,/data-input="turb_ox_n_stages_user"/);
  await h.app.saveProject();
  assert.equal(saved.inputs.turb_ox_n_stages_user,0);
  assert.ok(!Object.hasOwn(saved.inputs,"turb_ox_stage_mode"));
  assert.ok(!Object.hasOwn(h.app.buildInputBackup().inputs,"turb_ox_stage_mode"));
  editField(h,"turb_ox_stage_mode","specified");
  assert.equal(h.app.state().draft.turb_ox_n_stages_user,3);
});

test("an unsupported cycle edit preserves unfinished FFSC turbine-stage input",()=>{
  const h=turbineModeHarness();
  editField(h,"turb_ox_stage_mode","specified");
  editField(h,"cycle_type","gg");
  assert.equal(h.app.state().draft.cycle_type,"ffsc");
  assert.match(h.nodes.get("parameter-fields").innerHTML,/data-input="turb_ox_n_stages_user"/);
  assert.throws(()=>h.app.validateDraft());
  editField(h,"turb_ox_stage_mode","estimated");
  assert.doesNotThrow(()=>h.app.validateDraft());
});

test("unfinished meanline assumptions remain recoverable after disabling the optional check",()=>{
  for(const [key,value] of [["turb_ox_reaction_degree","0.45"],["turb_ox_meanline_flow_coefficient","0.6"]]){
    const h=turbineModeHarness();
    editField(h,"turbine_meanline_mode","equal_work");
    editField(h,key,"");
    editField(h,"turbine_meanline_mode","disabled");
    assert.ok(!h.nodes.get("parameter-fields").innerHTML.includes(`data-input="${key}"`));
    assert.throws(()=>h.app.validateDraft(),/重新启用均径校核并补填.*当前草稿已保留/);
    editField(h,"turbine_meanline_mode","equal_work");
    editField(h,key,value);
    assert.doesNotThrow(()=>h.app.validateDraft());
  }
});

test("turbine stage candidate follows preset undo and never leaks into an imported or another project",()=>{
  const h=turbineModeHarness();
  editField(h,"turb_ox_stage_mode","specified");
  editField(h,"turb_ox_n_stages_user","2");
  editField(h,"turb_ox_stage_mode","estimated");
  const estimated=h.app.state().draft;
  selectModel(h,"modern");
  h.nodes.get("undo-preset").onclick();
  assert.deepEqual(h.app.state().draft,estimated);
  editField(h,"turb_ox_stage_mode","specified");
  assert.equal(h.app.state().draft.turb_ox_n_stages_user,2);
  h.app.applyInputDocument(estimated);
  editField(h,"turb_ox_stage_mode","specified");
  assert.equal(h.app.state().draft.turb_ox_n_stages_user,null);
  editField(h,"turb_ox_n_stages_user","3");
  editField(h,"turb_ox_stage_mode","estimated");
  h.app.fillProject({...project,id:"p2",inputs:estimated});
  editField(h,"turb_ox_stage_mode","specified");
  assert.equal(h.app.state().draft.turb_ox_n_stages_user,null);
});

test("the help design contract follows draft mode changes without altering saved inputs",async()=>{
  const h=ffscHarness();
  assert.equal(h.nodes.has("concept-design-scope"),false);
  h.app.openHelp("theory");
  assert.match(h.nodes.get("help-content").innerHTML,/data-design-mode="legacy_free_split"/);
  await h.app.returnFromHelp();
  editField(h,"preburner_design_mode","temperature");
  h.app.openHelp("theory");
  assert.match(h.nodes.get("help-content").innerHTML,/data-design-mode="temperature"/);
  await h.app.returnFromHelp();
  editField(h,"preburner_design_mode","mixture_ratio");
  h.app.openHelp("theory");
  assert.match(h.nodes.get("help-content").innerHTML,/data-design-mode="mixture_ratio"/);
  assert.equal(h.app.state().current.inputs.use_target_preburner_temp,0);
});

test("legacy preburner drafts survive load, import, preset undo and save without migration",async()=>{
  let saved;
  const h=ffscHarness(async(url,options)=>{
    assert.equal(options.method,"PUT");saved=JSON.parse(options.body);
    return response({project:{...project,inputs:saved.inputs,revision:2},warnings:[]});
  });
  assert.deepEqual(copy(h.app.state().draft),ffscInputs);
  assert.match(h.nodes.get("parameter-fields").innerHTML,/value="legacy_free_split" selected disabled/);
  assert.equal(h.app.state().dirty,false);
  h.app.applyInputDocument({...ffscInputs,preburner_mr:63});
  const before=h.app.state().draft;
  selectModel(h,"modern");
  assert.equal(h.app.state().draft.use_target_preburner_temp,1);
  h.nodes.get("undo-preset").onclick();
  assert.deepEqual(h.app.state().draft,before);
  await h.app.saveProject();
  assert.deepEqual(saved.inputs,copy(before));
  assert.ok(!("preburner_design_mode" in saved.inputs));
  assert.equal(saved.inputs.hidden_fixed_q,91e6);
});

test("routine new and load lists exclude budget presets while the API catalog remains intact",async()=>{
  const old={key:"budget",label:"旧预算示例",routine_design:false,inputs:{...defaults,cooling_bartz_mode:0}};
  const catalog={...info,presets:[...info.presets,old]};
  const {app,nodes}=harness(async url=>{
    if(url==="/api/auth/status")return response({initialized:true,user});
    if(url==="/api/info")return response(catalog);
    if(url==="/api/projects"||url.startsWith("/api/jobs"))return response([]);
    throw Error(url);
  });
  app.reset();await app.initialize();
  assert.doesNotMatch(nodes.get("new-preset").innerHTML,/旧预算/);
  assert.match(nodes.get("project-preset").innerHTML,/<option value="budget" disabled>/);
  assert.equal(catalog.presets[1].inputs.cooling_bartz_mode,0);
});

test("explicit preburner mode edits persist both flags and keep inactive values intact",async()=>{
  let saved;
  const h=ffscHarness(async(url,options)=>{
    saved=JSON.parse(options.body);return response({project:{...project,inputs:saved.inputs,revision:2},warnings:[]});
  });
  for(const [selected,temp] of [["temperature",1],["mixture_ratio",0]]){
    editField(h,"preburner_design_mode",selected);
    assert.deepEqual(copy(h.app.state().draft),{...ffscInputs,use_target_preburner_temp:temp,use_target_preburner_mr:1});
    assert.equal(h.app.state().dirty,true);
    assert.ok(!("preburner_design_mode" in h.app.state().draft));
    const rendered=h.nodes.get("parameter-fields").innerHTML;
    assert.doesNotMatch(rendered,/data-input="split_guess"|data-input="hidden_fixed_q"/);
    if(selected==="temperature")assert.match(rendered,/data-input="preburner_temp_target"/);
    else assert.doesNotMatch(rendered,/data-input="preburner_temp_target"/);
  }
  await h.app.saveProject();
  assert.equal(saved.inputs.use_target_preburner_mr,1);
  assert.equal(saved.inputs.use_target_preburner_temp,0);
  assert.equal(saved.inputs.split_guess,.07);
});

test("mode dependent controls update the same draft without dropping dormant inputs",()=>{
  const h=ffscHarness();
  editField(h,"preburner_design_mode","temperature");
  assert.equal(h.app.state().draft.use_target_preburner_temp,1);
  assert.equal(h.app.state().draft.use_target_preburner_mr,1);
  editField(h,"cooling_bartz_mode","0");
  assert.match(h.nodes.get("parameter-fields").innerHTML,/data-input="hidden_fixed_q"/);
  editField(h,"cooling_bartz_mode","1");
  assert.doesNotMatch(h.nodes.get("parameter-fields").innerHTML,/data-input="hidden_fixed_q"/);
  assert.equal(h.app.state().draft.hidden_fixed_q,91e6);
});

test("invalid inactive canonical fields identify their key without referring to a removed editor",()=>{
  const h=ffscHarness();
  const invalid={...ffscInputs,hidden_fixed_q:null};
  h.app.seed(copy({current:{...project,inputs:ffscInputs},draft:invalid,dirty:true,user,info:ffscInfo,
    groups:ffscSystemGroups,fields:ffscCanonicalFields,page:"project",projectJobs:[],connected:true}));
  assert.throws(()=>h.app.validateDraft(),/hidden_fixed_q/);
  assert.equal(h.nodes.get("parameter-search").value,"");
});

test("explicit sample replacement loads the complete case without saving or running",async()=>{
  const requests=[];
  const h=modelHarness(async(url,options)=>{requests.push([url,options.method]);return response({});});
  for(const preset of modelPresets){
    selectModel(h,preset.key);
    const state=h.app.state(), rendered=h.nodes.get("parameter-fields").innerHTML;
    assert.deepEqual(copy(state.draft),preset.inputs);
    assert.equal(state.referenceKey,preset.key);
    assert.equal(h.nodes.get("project-preset").value,preset.key);
    assert.equal(state.dirty,true);
    assert.match(rendered,new RegExp(`value="${preset.inputs.propellant_pair}" selected`));
    assert.match(rendered,new RegExp(`value="${preset.inputs.cycle_type}" selected`));
    assert.doesNotMatch(rendered,/data-input="gg_layout"|data-input="gg_temp_target"/);
    assert.doesNotMatch(h.nodes.get("parameter-group").innerHTML,/value="gg"/);
    await assertRunGate(h,false);
    assert.equal(h.nodes.get("undo-preset").hidden,false);
  }
  assert.deepEqual(requests,[]);
  assert.equal(h.confirmations(),0);
});

test("undoing a preset switch restores saved and edited drafts with their parameter navigation",()=>{
  for(const edited of [false,true]){
    const h=modelHarness();
    if(edited){
      editField(h,"thrust","963.25");
    }
    h.nodes.get("parameter-search").value="入口";
    h.nodes.get("parameter-group").value="design";
    const before=h.app.state();
    selectModel(h,"ffsc_low");
    assert.equal(h.nodes.get("parameter-search").value,"");
    assert.equal(h.nodes.get("parameter-group").value,"architecture");
    h.nodes.get("undo-preset").onclick();
    const after=h.app.state();
    assert.deepEqual(after.draft,before.draft);
    assert.equal(after.dirty,before.dirty);
    assert.equal(after.referenceKey,before.referenceKey);
    assert.equal(h.nodes.get("parameter-search").value,"");
    assert.equal(h.nodes.get("parameter-group").value,"design");
    assert.equal(h.nodes.get("undo-preset").hidden,true);
    assert.equal(after.presetUndo,null);
    assert.equal(h.confirmations(),0);
  }
});

test("architecture remains FFSC while thrust edits retain the selected reference",()=>{
  for(const [field,value] of [["propellant_pair","lox_kerosene"],["cycle_type","gg"]]){
    const h=modelHarness();
    selectModel(h,"ffsc_base");
    editField(h,field,value);
    assert.equal(h.app.state().draft[field],modelDefaults[field]);
    assert.equal(h.app.state().referenceKey,"ffsc_base");
  }
  const h=modelHarness();
  selectModel(h,"ffsc_low");
  editField(h,"thrust","901.5");
  assert.equal(h.app.state().draft.thrust,901500);
  assert.equal(h.app.state().referenceKey,"ffsc_low");
  assert.equal(h.nodes.get("project-preset").value,"ffsc_low");
  assert.equal(h.nodes.get("undo-preset").hidden,true);
  h.nodes.get("apply-preset").onclick();
  assert.deepEqual(copy(h.app.state().draft),modelPresets[0].inputs);
  h.nodes.get("undo-preset").onclick();
  assert.equal(h.app.state().draft.thrust,901500);
});

test("project metadata edits expire preset undo instead of overwriting later edits",()=>{
  for(const nodeId of ["project-name","project-description"]){
    const h=modelHarness();
    selectModel(h,"ffsc_low");
    h.nodes.get(nodeId).value="new manual edit";
    h.nodes.get(nodeId).oninput();
    const before=h.app.state();
    h.nodes.get("undo-preset").onclick();
    assert.deepEqual(h.app.state().draft,before.draft);
    assert.equal(h.nodes.get(nodeId).value,"new manual edit");
    assert.equal(h.app.state().presetUndo,null);
    assert.equal(h.nodes.get("undo-preset").hidden,true);
  }
});

test("reconnection preserves the selected example, modified draft and previous result",async()=>{
  const calls=[];
  const oldResult={id:"j-old",project_id:"p1",revision:1,status:"succeeded",created:1,
    result:{inputs:modelPresets[1].inputs,summary:{thrust_N:829640}}};
  const h=modelHarness(async(url,options)=>{
    calls.push([url,options.method]);
    if(url==="/api/auth/status")return response({initialized:true,user});
    if(url==="/api/info")return response(modelInfo);
    if(url==="/api/jobs/j-old")return response(oldResult);
    if(url==="/api/jobs?project_id=p1")return response([oldResult]);
    if(url==="/api/projects/p1/revisions")return response([]);
    throw Error(`Reconnection must preserve the draft: ${url}`);
  });
  await h.app.loadRecord("j-old");
  selectModel(h,"ffsc_low");
  editField(h,"thrust","910");
  const before=h.app.state();
  await h.app.initialize();
  const after=h.app.state();
  assert.deepEqual(after.draft,before.draft);
  assert.equal(after.referenceKey,"ffsc_low");
  assert.equal(h.nodes.get("project-preset").value,"ffsc_low");
  assert.equal(after.dirty,true);
  assert.deepEqual(after.resultRecord,before.resultRecord);
  assert.match(h.nodes.get("result-context").textContent,/历史输入的结果/);
  assert.ok(calls.every(([url,method])=>method==="GET"&&!/^\/api\/projects(?:\/p1)?$/.test(url)));
});

test("opening saved projects and importing parameters infer only exact examples and remove stale model labels",()=>{
  const h=modelHarness();
  selectModel(h,"ffsc_low");
  h.app.fillProject(copy({...modelProject,inputs:modelPresets[2].inputs}));
  assert.equal(h.nodes.get("project-preset").value,"raptor3_ffsc");
  assert.equal(h.app.state().dirty,false);
  assert.equal(h.app.state().presetUndo,null);
  h.app.fillProject(copy({...modelProject,inputs:{...modelPresets[1].inputs,thrust:1000000}}));
  assert.equal(h.nodes.get("project-preset").value,"");
  assert.match(h.nodes.get("preset-context").textContent,/请选择条目浏览/);
  const oldMethane=copy(modelPresets[1].inputs);
  delete oldMethane.propellant_pair;
  h.app.fillProject(copy({...modelProject,inputs:oldMethane}));
  assert.equal(h.app.state().draft.propellant_pair,"lox_methane");
  assert.equal(h.nodes.get("project-preset").value,"ffsc_base");
  for(const [inputs,key] of [[modelPresets[0].inputs,"ffsc_low"],[{...modelPresets[0].inputs,pc:11e6},""]]){
    h.app.applyInputDocument({inputs});
    assert.deepEqual(copy(h.app.state().draft),inputs);
    assert.equal(h.nodes.get("project-preset").value,key);
    assert.equal(h.app.state().referenceKey,key);
    assert.equal(h.app.state().presetUndo,null);
  }
});

test("loading an older revision replaces stale reference labels while preserving the recorded result",async()=>{
  const record={id:"j-old",project_id:"p1",revision:1,status:"succeeded",created:1,
    result:{inputs:modelPresets[0].inputs,summary:{thrust_N:845000}}};
  const older={...modelProject,name:"历史方案名称",description:"旧版本设计说明",revision:1,inputs:{...modelPresets[1].inputs,pc:35e6}};
  const requests=[];
  const h=modelHarness(async(url,options)=>{
    requests.push([url,options.method]);
    if(url==="/api/jobs/j-old")return response(record);
    if(url==="/api/projects/p1/revisions/1")return response(older);
    throw Error(`Unexpected request ${url}`);
  });
  await h.app.loadRecord("j-old");
  selectModel(h,"ffsc_low");
  const button={dataset:{revision:"1"},hasAttribute:()=>false};
  h.body.listeners.get("click")({target:{closest:selector=>selector==="button"?button:null}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(copy(h.app.state().draft),older.inputs);
  assert.equal(h.nodes.get("project-preset").value,"");
  assert.equal(h.app.state().referenceKey,"");
  assert.equal(h.app.state().presetUndo,null);
  assert.deepEqual(copy(h.app.state().resultRecord),record);
  assert.match(h.nodes.get("result-context").textContent,/历史输入的结果/);
  assert.ok(requests.every(([,method])=>method==="GET"));
  assert.equal(h.nodes.get("project-name").value,older.name);
  assert.equal(h.nodes.get("project-description").value,older.description);
  assert.equal(h.app.state().dirty,true);
});

test("reload locks the editor throughout its GET and prevents a preset switch from replacing the awaited project",async()=>{
  let finishRead;
  const pendingRead=new Promise(resolve=>{finishRead=resolve;});
  const reloaded={...modelProject,revision:2,inputs:modelPresets[2].inputs};
  const h=modelHarness(async url=>{
    if(url==="/api/projects/p1")return pendingRead;
    if(url==="/api/jobs?project_id=p1"||url==="/api/projects/p1/revisions")return response([]);
    throw Error(`Unexpected request ${url}`);
  });
  const before=h.app.state();
  const pendingReload=h.nodes.get("reload-project").onclick();
  assert.equal(h.app.state().editorBusy,true);
  assert.equal(h.nodes.get("project-form").inert,true);
  selectModel(h,"ffsc_low");
  editField(h,"thrust","999");
  assert.deepEqual(h.app.state().draft,before.draft);
  assert.equal(h.nodes.get("project-preset").value,before.referenceKey);
  assert.equal(h.app.state().presetUndo,null);
  finishRead(response(reloaded));
  await pendingReload;
  assert.deepEqual(copy(h.app.state().draft),reloaded.inputs);
  assert.equal(h.app.state().current.revision,2);
  assert.equal(h.nodes.get("project-preset").value,"raptor3_ffsc");
  assert.equal(h.app.state().editorBusy,false);
  assert.equal(h.nodes.get("project-form").inert,false);
});

test("invalid text and numeric choices reject imports atomically and prevent invalid drafts from saving",async()=>{
  for(const [key,value] of [["propellant_pair","lox_hydrogen"],
    ["cycle_type","unknown_cycle"],["cooling_bartz_mode",7]]){
    const requests=[];
    const h=modelHarness(async(url,options)=>{requests.push([url,options.method]);return response({});});
    const before=h.app.state();
    const invalid={...copy(before.draft),[key]:value};
    assert.throws(()=>h.app.applyInputDocument({inputs:invalid}),undefined,`${key} must fail before replacing the draft`);
    assert.deepEqual(h.app.state().draft,before.draft);
    assert.equal(h.app.state().referenceKey,before.referenceKey);
    assert.equal(h.app.state().dirty,before.dirty);
    h.nodes.get("import-inputs").files=[{text:async()=>JSON.stringify({inputs:invalid})}];
    await h.nodes.get("import-inputs").onchange({target:h.nodes.get("import-inputs")});
    assert.ok(h.nodes.get("project-history-error").textContent.length>0,`${key} should explain the rejected choice`);
    assert.deepEqual(h.app.state().draft,before.draft);
    h.app.seed(copy({current:modelProject,draft:invalid,dirty:true,user,info:modelInfo,
      groups:modelGroups,fields:modelFields,page:"project",projectJobs:[],connected:true,referenceKey:"ffsc_base"}));
    assert.throws(()=>h.app.validateDraft(),undefined,`${key} must also fail before save/run`);
    assert.deepEqual(requests,[]);
  }
});

test("reconnecting a clean current project retains its identity, reference and selected result instead of opening the newest project",async()=>{
  const currentProject={...modelProject,id:"p-older",inputs:modelPresets[0].inputs};
  const result={id:"j-kept",project_id:"p-older",revision:1,status:"succeeded",created:1,
    result:{inputs:currentProject.inputs,summary:{thrust_N:845000}}};
  const requests=[];
  const h=modelHarness(async(url,options)=>{
    requests.push([url,options.method]);
    if(url==="/api/auth/status")return response({initialized:true,user});
    if(url==="/api/info")return response(modelInfo);
    if(url==="/api/jobs/j-kept")return response(result);
    if(url==="/api/jobs?project_id=p-older")return response([result]);
    if(url==="/api/projects/p-older/revisions")return response([]);
    throw Error(`A clean open project must not be replaced on reconnection: ${url}`);
  });
  h.app.fillProject(copy(currentProject));
  await h.app.loadRecord("j-kept");
  assert.equal(h.app.state().dirty,false);
  const before=h.app.state();
  await h.app.initialize();
  const after=h.app.state();
  assert.equal(after.current.id,"p-older");
  assert.equal(after.referenceKey,"ffsc_low");
  assert.equal(h.nodes.get("project-preset").value,"ffsc_low");
  assert.deepEqual(after.draft,before.draft);
  assert.deepEqual(after.resultRecord,before.resultRecord);
  assert.equal(after.selectedJobId,"j-kept");
  assert.equal(after.dirty,false);
  assert.ok(requests.every(([url,method])=>method==="GET"&&url!=="/api/projects"&&url!=="/api/projects/p-older"));
});

test("saving a non-architecture edit retains its chosen reference identity",async()=>{
  let saved;
  const h=modelHarness(async(url,options)=>{
    assert.equal(url,"/api/projects/p1");
    assert.equal(options.method,"PUT");
    saved=JSON.parse(options.body);
    return response({project:{...modelProject,...saved,revision:2},warnings:[]});
  });
  selectModel(h,"ffsc_low");
  editField(h,"thrust","902.5");
  await h.app.saveProject();
  assert.equal(saved.inputs.thrust,902500);
  assert.equal(h.app.state().draft.thrust,902500);
  assert.equal(h.app.state().referenceKey,"ffsc_low");
  assert.equal(h.nodes.get("project-preset").value,"ffsc_low");
  assert.equal(h.app.state().dirty,false);
  assert.equal(h.app.state().current.revision,2);
});

test("history and steady views share one unsaved draft while history hides performance controls",async()=>{
  const record={id:"selected-design",project_id:"p1",revision:1,status:"succeeded",created:1,result:{calculation:{inputs:defaults}}};
  const h=harness(async()=>response(record));
  await h.app.loadRecord(record.id);
  h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"pc"},value:"12.25"}});
  await h.app.chooseView("history");
  assert.equal(h.nodes.get("project-history").hidden,false);
  assert.equal(h.nodes.get("analysis-content").hidden,true);
  assert.equal(h.nodes.get("result-toolbar").hidden,true);
  assert.equal(h.nodes.get("result-context").hidden,true);
  assert.equal(h.nodes.get("toggle-parameters").hidden,true);
  h.nodes.get("project-name").value="概念方案 A";
  h.nodes.get("project-description").value="记录同一份未保存参数";
  h.nodes.get("project-description").oninput();
  const before=h.app.state();
  await h.app.chooseView("overview");
  assert.equal(h.nodes.get("project-history").hidden,true);
  assert.equal(h.nodes.get("concept-settings-host").hidden,false);
  assert.equal(h.nodes.get("result-toolbar").hidden,true);
  h.app.chooseConceptSection("results");
  assert.equal(h.nodes.get("result-toolbar").hidden,false);
  assert.deepEqual(h.app.state().draft,before.draft);
  assert.equal(h.nodes.get("project-name").value,"概念方案 A");
  assert.equal(h.nodes.get("project-description").value,"记录同一份未保存参数");
  assert.deepEqual(h.app.state().resultRecord,before.resultRecord);
  assert.equal(h.app.state().dirty,true);
  assert.equal(h.confirmations(),0);
});

test("history metadata saves with all visible and inactive parameters as one new revision",async()=>{
  const requests=[];
  const h=ffscHarness(async(url,options)=>{
    const payload=JSON.parse(options.body);requests.push({url,payload});
    return response({project:{...project,...payload,revision:2},warnings:[]});
  });
  await h.app.chooseView("history");
  h.nodes.get("project-name").value="试验前方案";
  h.nodes.get("project-description").value="保存设计意图和边界";
  h.nodes.get("project-description").oninput();
  await h.app.saveProject();
  assert.equal(requests.length,1);
  assert.equal(requests[0].url,"/api/projects/p1");
  assert.equal(requests[0].payload.name,"试验前方案");
  assert.equal(requests[0].payload.description,"保存设计意图和边界");
  assert.equal(requests[0].payload.revision,1);
  assert.deepEqual(requests[0].payload.inputs,ffscInputs);
  assert.equal(h.app.state().current.revision,2);
  assert.equal(h.app.state().dirty,false);
  assert.equal(h.nodes.get("project-dirty-label").textContent,"已保存");
});

test("parameter backups round trip hidden inputs without replacing project metadata",async()=>{
  const h=ffscHarness();
  h.nodes.get("project-name").value="备份来源";
  h.nodes.get("project-description").value="设计说明";
  editField(h,"preburner_design_mode","temperature");
  const backup=copy(h.app.buildInputBackup()), expected=copy(h.app.state().draft);
  assert.equal(backup.project_name,"备份来源");
  assert.equal(backup.project_description,"设计说明");
  assert.equal(backup.base_revision,1);
  assert.equal(backup.unsaved,true);
  assert.deepEqual(backup.inputs,expected);
  h.nodes.get("project-name").value="导入目标";
  h.nodes.get("project-description").value="目标说明";
  h.app.applyInputDocument({...ffscInputs,hidden_fixed_q:5e6});
  h.nodes.get("import-inputs").files=[{text:async()=>JSON.stringify(backup)}];
  await h.nodes.get("import-inputs").onchange({target:h.nodes.get("import-inputs")});
  assert.deepEqual(copy(h.app.state().draft),expected);
  assert.equal(h.nodes.get("project-name").value,"导入目标");
  assert.equal(h.nodes.get("project-description").value,"目标说明");
  assert.equal(h.app.state().dirty,true);
  const exported=h.app.buildInputBackup();
  exported.inputs.hidden_fixed_q=0;
  assert.equal(h.app.state().draft.hidden_fixed_q,91e6);
});

test("file import locks both editable areas until reading and validation complete",async()=>{
  const h=modelHarness();
  let finishRead;
  const pendingRead=new Promise(resolve=>{finishRead=resolve;});
  const input=h.nodes.get("import-inputs");
  input.files=[{text:()=>pendingRead}];input.value="selected-file.json";
  const before=h.app.state();
  const importing=input.onchange({target:input});
  assert.equal(h.app.state().editorBusy,true);
  assert.equal(h.nodes.get("project-form").inert,true);
  assert.equal(h.nodes.get("project-history").inert,true);
  assert.equal(h.nodes.get("history-save").disabled,true);
  editField(h,"thrust","999");
  selectModel(h,"ffsc_low");
  assert.deepEqual(h.app.state().draft,before.draft);
  finishRead(JSON.stringify({inputs:{...modelProject.inputs,thrust:1234567}}));
  await importing;
  assert.equal(h.app.state().draft.thrust,1234567);
  assert.equal(h.app.state().editorBusy,false);
  assert.equal(h.nodes.get("project-form").inert,false);
  assert.equal(h.nodes.get("project-history").inert,false);
  assert.equal(input.value,"");
});

test("invalid imported values cannot partially replace a complete draft",()=>{
  const h=modelHarness(),before=h.app.state();
  for(const value of [null,[],{inputs:null},{inputs:[]},{unknown_parameter:1},
    {...modelDefaults,thrust:"123"},{...modelDefaults,thrust:NaN},
    {...modelDefaults,pc:Infinity},{...modelDefaults,throttle_steps:4.5}]){
    assert.throws(()=>h.app.applyInputDocument(value));
    assert.deepEqual(h.app.state().draft,before.draft);
    assert.equal(h.app.state().dirty,before.dirty);
    assert.equal(h.nodes.get("project-name").value,project.name);
  }
});

test("saved legacy imports preserve thermochemistry and material identity while new inputs use current defaults",()=>{
  const h=harness(async()=>{throw Error("Local imports must not save");});
  const materialDefaults={...defaults,methane_thermo_dataset:"gri30_highT",
    chamber_inner_wall_material:"grcop84",wall_thermal_dataset_id:"grcop84_ellis2000_extruded_high_fe_v1",
    wall_manufacturing_state:"unspecified"};
  const materialFields=[...fields,...Object.keys(materialDefaults).filter(key=>!(key in defaults)).map(key=>({key,label:key,default:materialDefaults[key]}))];
  h.app.seed(copy({current:project,draft:materialDefaults,dirty:false,user,
    info:{...info,ui_schema:{defaults:materialDefaults,groups:[{id:"materials",fields:materialFields}]}},
    groups:[{id:"materials",fields:materialFields}],fields:materialFields,page:"project",projectJobs:[],connected:true}));
  h.app.applyInputDocument({format:"lre.workbench.inputs",schema_version:1,inputs:{pc:25e6,chamber_inner_wall_material:"cuzr"}});
  assert.equal(h.app.state().draft.methane_thermo_dataset,"gri30");
  assert.equal(h.app.state().draft.wall_thermal_dataset_id,"cuzr_qi2024_heat_treated_v1");
  assert.equal(h.app.state().draft.wall_manufacturing_state,"unspecified");
  h.app.applyInputDocument({pc:25e6});
  assert.equal(h.app.state().draft.methane_thermo_dataset,"gri30_highT");
  h.app.applyInputDocument({format:"lre.workbench.inputs",schema_version:1,inputs:{chamber_inner_wall_material:"grcop42"}});
  assert.equal(h.app.state().draft.wall_thermal_dataset_id,"grcop42_chen2023_lpbf_v1");
  const explicit={...materialDefaults,methane_thermo_dataset:"gri30_highT",wall_manufacturing_state:"specific_qualified_state"};
  h.app.applyInputDocument({format:"lre.workbench.inputs",schema_version:1,inputs:explicit});
  assert.deepEqual(copy(h.app.state().draft),explicit);
});

test("explicit high-T upgrade saves a new revision while retaining the old result and other draft edits",async()=>{
  const oldInputs={...defaults,propellant_pair:"lox_methane",methane_thermo_dataset:"gri30",
    combustion_loss_model:"legacy_temperature_rise",cooling_bartz_mode:0};
  const oldProject={...project,inputs:oldInputs};
  const currentDefaults={...oldInputs,methane_thermo_dataset:"gri30_highT"};
  const allFields=[...fields,...Object.keys(currentDefaults).filter(key=>!(key in defaults))
    .map(key=>({key,label:key,default:currentDefaults[key]}))];
  const visibleFields=allFields.filter(f=>f.key!=="methane_thermo_dataset");
  const currentInfo={...info,ui_schema:{defaults:currentDefaults,
    groups:[{id:"design",fields:allFields}],system_groups:[{id:"design",fields:visibleFields}]}};
  const record={id:"old-thermo",project_id:project.id,revision:1,status:"succeeded",created:1,
    result:{calculation:{inputs:oldInputs}}};
  const saved=[];
  const h=harness(async(url,options)=>{
    if(url==="/api/jobs/old-thermo")return response(record);
    if(url==="/api/projects/p1"&&options.method==="PUT"){
      const body=JSON.parse(options.body);saved.push(body);
      return response({project:{...oldProject,...body,revision:2},warnings:[]});
    }
    throw new Error(`Unexpected request ${options.method} ${url}`);
  });
  h.app.seed(copy({current:oldProject,draft:oldInputs,dirty:false,user,info:currentInfo,
    groups:currentInfo.ui_schema.system_groups,fields:allFields,page:"project",projectJobs:[],connected:true}));
  h.app.fillProject(oldProject);await h.app.loadRecord(record.id);
  assert.equal(h.app.state().draft.methane_thermo_dataset,"gri30");
  assert.doesNotMatch(h.nodes.get("parameter-fields").innerHTML,/data-input="methane_thermo_dataset"/);
  h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"pc"},value:"22"}});
  h.app.upgradeThermochemistryData();
  let state=h.app.state();
  assert.equal(saved.length,0);assert.equal(state.current.inputs.methane_thermo_dataset,"gri30");
  assert.equal(state.draft.methane_thermo_dataset,"gri30_highT");assert.equal(state.draft.pc,22e6);
  assert.equal(state.draft.combustion_loss_model,"legacy_temperature_rise");
  assert.equal(state.draft.cooling_bartz_mode,0);assert.equal(state.dirty,true);
  assert.deepEqual(copy(state.resultRecord),record);assert.equal(h.app.state().draft.methane_thermo_dataset,"gri30_highT");
  await h.app.saveProject();state=h.app.state();
  assert.equal(saved.length,1);assert.equal(saved[0].revision,1);
  assert.equal(saved[0].inputs.methane_thermo_dataset,"gri30_highT");
  assert.equal(state.current.revision,2);assert.equal(state.dirty,false);
  assert.deepEqual(copy(state.resultRecord),record);
  assert.equal(oldProject.inputs.methane_thermo_dataset,"gri30");
});

test("a missing scenario name opens its editable history field before save or run",async()=>{
  const h=harness(async()=>{throw Error("A missing name must not send a request");});
  h.nodes.get("project-name").value="   ";
  h.nodes.get("project-name").oninput();
  await h.app.chooseView("design_run");
  await assert.rejects(h.app.runDesign(),/方案与计算记录/);
  assert.equal(h.nodes.get("project-history").hidden,false);
  assert.equal(h.nodes.get("analysis-content").hidden,true);
  assert.equal(h.app.state().dirty,true);
});

test("cooling upgrade creates a draft and revision without changing the selected budget record",async()=>{
  const oldInputs={...defaults,propellant_pair:"lox_methane",cooling_bartz_mode:0,cooling_heat_load:17e6,cooling_dp_pa:2e6};
  const oldProject={...project,inputs:oldInputs};
  const allFields=[...fields,...Object.keys(oldInputs).filter(key=>!(key in defaults)).map(key=>({key,label:key,default:oldInputs[key]}))];
  const currentInfo={...info,ui_schema:{defaults:{...oldInputs,cooling_bartz_mode:1},groups:[{id:"cooling",fields:allFields}],
    system_groups:[{id:"cooling",fields:allFields.map(f=>f.key==="cooling_bartz_mode"?{...f,read_only:true}:f)}]}};
  const record={id:"old-budget",project_id:project.id,revision:1,status:"succeeded",created:1,result:{calculation:{inputs:oldInputs}}};
  const saved=[];
  const h=harness(async(url,options)=>{
    if(url==="/api/jobs/old-budget")return response(record);
    if(url==="/api/projects/p1"&&options.method==="PUT"){
      const body=JSON.parse(options.body);saved.push(body);
      return response({project:{...oldProject,...body,revision:2},warnings:[]});
    }
    throw Error(url);
  });
  h.app.seed(copy({current:oldProject,draft:oldInputs,dirty:false,user,info:currentInfo,
    groups:currentInfo.ui_schema.system_groups,fields:allFields,page:"project",projectJobs:[],connected:true}));
  h.app.fillProject(oldProject);await h.app.loadRecord(record.id);
  assert.doesNotMatch(h.nodes.get("parameter-fields").innerHTML,/data-input="cooling_bartz_mode"/);
  h.nodes.get("parameter-fields").oninput({target:{dataset:{input:"pc"},value:"22"}});
  h.nodes.get("parameter-fields").listeners.get("click")({target:{closest:selector=>selector==="[data-upgrade-cooling]"?{}:null}});
  let state=h.app.state();
  assert.equal(saved.length,0);assert.equal(state.current.inputs.cooling_bartz_mode,0);
  assert.equal(state.draft.cooling_bartz_mode,1);assert.equal(state.draft.pc,22e6);
  assert.equal(state.draft.cooling_heat_load,17e6);assert.equal(state.draft.cooling_dp_pa,2e6);
  assert.equal(state.dirty,true);assert.deepEqual(copy(state.resultRecord),record);
  await h.app.saveProject();state=h.app.state();
  assert.equal(saved.length,1);assert.equal(saved[0].revision,1);
  assert.equal(state.current.revision,2);assert.equal(state.current.inputs.cooling_bartz_mode,1);
  assert.deepEqual(copy(state.resultRecord),record);assert.equal(oldProject.inputs.cooling_bartz_mode,0);
});

test("cooling candidate adoption changes only a draft and keeps unrelated edits and the saved record",async()=>{
  const patch={cooling_n_channels:280,cooling_channel_height_m:.007,cooling_channel_auto_size:0,cooling_wall_temp_limit_status:"unset"};
  const calls=[];
  const h=harness(async(url,options)=>{calls.push(url);assert.equal(options.method,"GET");return response({project_id:project.id,input_patch:patch});});
  const inputs={...defaults,propellant_pair:"lox_methane",cooling_bartz_mode:1,cooling_channel_table_json:"",pc:42e6};
  h.app.seed(copy({current:project,draft:inputs,dirty:true,user,info,groups:schema.groups,fields,page:"project",projectJobs:[],connected:true}));
  await h.app.adoptCoolingCandidate({studyId:"study1",candidateId:2});
  const state=h.app.state();assert.equal(state.draft.pc,42e6);assert.equal(state.draft.cooling_channel_height_m,.007);
  assert.equal(state.dirty,true);assert.deepEqual(copy(state.current),project);assert.equal(state.resultRecord,null);
  assert.deepEqual(calls,["/api/cooling-designs/study1/candidates/2/draft"]);
});

test("cooling candidate adoption rejects a different project and an active axial channel table",async()=>{
  for(const scenario of [{returnedProject:"different",table:""},{returnedProject:project.id,table:'[{"x_m":0}]'}]){
    const h=harness(async()=>response({project_id:scenario.returnedProject,input_patch:{cooling_n_channels:280}}));
    const inputs={...defaults,propellant_pair:"lox_methane",cooling_bartz_mode:1,cooling_channel_table_json:scenario.table};
    h.app.seed(copy({current:project,draft:inputs,dirty:true,user,info,groups:schema.groups,fields,page:"project",projectJobs:[],connected:true}));
    await assert.rejects(h.app.adoptCoolingCandidate({studyId:"s",candidateId:1}));
    assert.deepEqual(copy(h.app.state().draft),inputs);
  }
});

test("component feedback saves only owned edits and retains unrelated drafts, metadata and source",async()=>{
 for(const [savedSpeed,draftSpeed,revision] of [[26500,27000,1],[27000,26500,2]]){
  const pumpField={key:"lox_pump_speed_rpm_override",label:"泵转速",type:"number",factor:1,default:26500};
  const allGroups=[...schema.groups,{id:"pumps",title:"泵初选",workflow_stage:"components",design_dependency:"sizing",fields:[pumpField]}];
  const inputs={...defaults,lox_pump_speed_rpm_override:26500};
  const saved={...project,revision,inputs:{...inputs,lox_pump_speed_rpm_override:savedSpeed}},record={id:"feedback-source",project_id:project.id,revision:1,status:"succeeded",kind:"design",inputs,result:{calculation:{inputs}}};
  const catalog={components:[{id:"pumps",label:"泵与轴系",groups:["pumps"],views:["pump_geometry"],impact:"component"}]};
  const testInfo={...info,ui_schema:{...schema,defaults:inputs,groups:allGroups,engineering_workflow:catalog}};
  const posts=[];
  const h=harness(async(url,options)=>{
    if(url==="/api/jobs/feedback-source")return response(record);
    if(url==="/api/projects/p1/engineering/iterations"){
      const payload=JSON.parse(options.body);posts.push(payload);
      return response({project:{...saved,revision:revision+1,inputs:{...saved.inputs,...payload.patch}}});
    }
    if(url==="/api/jobs?project_id=p1")return response([record]);
    if(url.endsWith("/revisions"))return response([]);
    if(url.endsWith("/engineering"))return response({iterations:[],baselines:[]});
    throw Error(`Unexpected request: ${url}`);
  });
  h.app.seed(copy({current:saved,draft:saved.inputs,dirty:false,user,info:testInfo,groups:allGroups,fields:[...fields,pumpField],page:"project",projectJobs:[record],connected:true}));
  await h.app.loadRecord(record.id);
  h.app.openReadinessField("pumps");
  editField(h,pumpField.key,String(draftSpeed));editField(h,"thrust","975");
  h.nodes.get("project-name").value="另存名称草稿";
  h.nodes.get("project-description").value="未保存说明";
  await h.app.submitComponentFeedback("pumps");
  assert.deepEqual(posts,[{revision,source_job_id:record.id,component:"pumps",patch:{lox_pump_speed_rpm_override:draftSpeed}}]);
  const state=h.app.state();
  assert.equal(state.current.inputs.thrust,inputs.thrust);assert.equal(state.draft.thrust,975000);
  assert.equal(state.current.inputs.lox_pump_speed_rpm_override,draftSpeed);
  assert.equal(state.dirty,true);assert.equal(state.editorBusy,false);assert.equal(state.analysisView,"integration");
  assert.equal(h.nodes.get("project-name").value,"另存名称草稿");
  assert.equal(h.nodes.get("project-description").value,"未保存说明");
  assert.deepEqual(copy(state.resultRecord),record);
 }
});

test("a rejected component feedback leaves the draft and source intact and enables retry",async()=>{
  const pumpField={key:"lox_pump_speed_rpm_override",label:"泵转速",type:"number",factor:1,default:26500};
  const allGroups=[...schema.groups,{id:"pumps",title:"泵初选",fields:[pumpField]}];
  const inputs={...defaults,lox_pump_speed_rpm_override:26500};
  const record={id:"conflict-source",project_id:project.id,revision:1,status:"succeeded",inputs,result:{calculation:{inputs}}};
  const catalog={components:[{id:"pumps",label:"泵与轴系",groups:["pumps"],views:["pump_geometry"]}]};
  const h=harness(async url=>url==="/api/jobs/conflict-source"?response(record):response({detail:"方案版本已更新"},409));
  h.app.seed(copy({current:{...project,inputs},draft:inputs,dirty:false,user,info:{...info,ui_schema:{...schema,groups:allGroups,engineering_workflow:catalog}},groups:allGroups,fields:[...fields,pumpField],page:"project",projectJobs:[record],connected:true}));
  await h.app.loadRecord(record.id);h.app.openReadinessField("pumps");editField(h,pumpField.key,"27000");
  const before=h.app.state();
  await assert.rejects(h.app.submitComponentFeedback("pumps"),/方案版本已更新/);
  const after=h.app.state();
  assert.deepEqual(after.draft,before.draft);assert.deepEqual(after.current,before.current);assert.deepEqual(copy(after.resultRecord),record);
  assert.equal(after.editorBusy,false);
  assert.doesNotMatch(h.nodes.get("component-workflow-context").innerHTML,/data-component-feedback="pumps" disabled/);
});

test("new project explicitly selects budget-first or example geometry without rewriting the example",async()=>{
  for(const start of ["cycle_budget","preset_geometry"]){
    const input={...defaults,cooling_bartz_mode:1,cooling_heat_load:22e6,cooling_channel_height_m:.008};
    const coolingField={key:'cooling_bartz_mode',type:'number',label:'冷却模型',factor:1};
    const startingGroups=[...schema.groups,{id:'cooling',title:'冷却与循环耦合模型',requires_review:true,fields:[coolingField]}];
    const testInfo={...info,ui_schema:{...schema,defaults:input,groups:startingGroups},presets:[{key:"test",label:"工程示例",inputs:input}]};
    let created;
    const h=harness(async(url,options)=>{
      if(url==="/api/projects"&&options.method==="POST"){
        created=JSON.parse(options.body);return response({project:{...project,...created}});
      }
      if(url==="/api/jobs?project_id=p1"||url==="/api/projects/p1/revisions")return response([]);
      throw Error(`Unexpected request: ${url}`);
    });
    h.app.seed(copy({current:project,draft:defaults,dirty:false,user,info:testInfo,groups:startingGroups,fields:[...fields,coolingField],page:"project",projectJobs:[],connected:true}));
    const form=h.nodes.get("new-form");
    form.formData=Object.entries({name:"新方案",description:"起点验证",preset:"test",design_start:start});
    await form.onsubmit({target:form,preventDefault(){}});
    assert.equal(created.inputs.cooling_bartz_mode,start==="cycle_budget"?0:1);
    assert.equal(created.inputs.cooling_heat_load,22e6);assert.equal(created.inputs.cooling_channel_height_m,.008);
    assert.equal(input.cooling_bartz_mode,1);assert.equal(h.app.state().dirty,false);
    assert.equal(h.app.state().groupReviews.some(([id])=>id==='cooling'),start==='preset_geometry');
  }
});
