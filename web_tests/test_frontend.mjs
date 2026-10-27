/** State/API regression tests with a small DOM double; no browser automation. */
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {fileURLToPath} from "node:url";
import vm from "node:vm";
import test from "node:test";
import * as parameterEditor from "../webapp/static/parameter-editor.js";
import {mountSteadyWorkspace} from "../webapp/static/steady-workspace.js";

const root = new URL("../", import.meta.url);
const html = readFileSync(new URL("webapp/static/index.html", root), "utf8");
const source = readFileSync(new URL("webapp/static/app.js", root), "utf8")
  .replace(/^import .*;\r?\n/gm, "")
  .replace(/^try\{await initialize\(\);\}catch\(error\)\{.*\}\r?$/m, "");
const defaults = {cycle_type: "gg", thrust: 829640, pc: 10100000, throttle_steps: 5};
const fields = [
  {key: "cycle_type", default: "gg", label: "循环类型", factor: 1},
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
  return {value: "", textContent: "", innerHTML: "", hidden: false, disabled: false,
    dataset: {}, className: "", inert: false, querySelectorAll: () => [], querySelector: () => null,
    listeners,
    classList: {toggle(name, force) {const on = force ?? !classes.has(name); on ? classes.add(name) : classes.delete(name); return on;}},
    reportValidity: () => true, checkValidity: () => true, addEventListener(name, callback) {listeners.set(name, callback);},
    setAttribute() {}, showModal() {}, close() {}, reset() {}, append() {}, click() {}, remove() {}, focus() {}};
}

function harness(fetcher) {
  const nodes = new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(match => [match[1], element()]));
  const body = element();
  let confirmations = 0;
  const throttleLifecycle = {mounts:0,refreshes:0,leaves:0};
  const context = vm.createContext({
    ...parameterEditor,
    document: {hidden: false, getElementById: id => {
      assert.ok(nodes.has(id), `Unexpected DOM id ${id}`);return nodes.get(id);
    }, querySelectorAll: () => [], querySelector: () => element(), body, createElement: () => element()},
    window: {addEventListener() {}},
    renderAnalysis: () => "analysis", configureResultSchema() {}, fetch: fetcher, AbortController, Blob, URL,
    mountIgnition:()=>({refresh(){},sync(){},onLeave(){}}),mountChilldown:()=>({refresh(){},sync(){},onLeave(){}}),mountFeedNetwork:()=>({refresh(){},sync(){},onLeave(){}}),mountTransient: () => ({refresh(){},onLeave(){},sync(){}}),
    mountValidation: () => ({refresh(){},onLeave(){},sync(){}}),
    mountThrottle: () => {throttleLifecycle.mounts++;return {refresh(){throttleLifecycle.refreshes++;},onLeave(){throttleLifecycle.leaves++;},sync(){}};},
    mountComponentWorkspace() {},
    mountFlowWorkspace() {},
    mountCycleWorkspace() {},
    mountSteadyWorkspace,
    setTimeout: () => 1, clearTimeout() {}, setInterval() {}, confirm: () => {confirmations++;return true;},
    FormData: class {}, console,
  });
  vm.runInContext(source + `\n;globalThis.app={api,runDesign,saveProject,initialize,poll,refreshProjectSide,withEditorLock,fillProject,applyInputDocument,buildInputBackup,validateDraft,loadRecord,chooseView,
    seed(value){({current,draft,dirty,user,info,groups,fields,page,projectJobs,connected}=value);referenceKey=value.referenceKey||"";presetUndo=value.presetUndo||null;},
    state(){return JSON.parse(JSON.stringify({current,draft,dirty,user,page,projectJobs,connected,editorBusy,resultRecord,selectedJobId,referenceKey,presetUndo}));}};`, context,
    {filename: fileURLToPath(new URL("webapp/static/app.js", root))});
  nodes.get("project-name").value = project.name;
  nodes.get("project-description").value = "";
  context.app.seed(copy({current: project, draft: defaults, dirty: false, user, info,
    groups: schema.groups, fields, page: "project", projectJobs: [], connected: true}));
  return {app: context.app, nodes, body, throttleLifecycle, confirmations:()=>confirmations};
}

const response = (data, status = 200) => ({ok: status < 400, status, json: async () => copy(data)});

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
  assert.equal(h.nodes.get("analysis-title").textContent,"节流稳态性能分析");
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
  assert.equal(nodes.get("header-run").disabled, true);
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
  await app.runDesign();
  assert.equal(calls[0].method, "PUT");
  assert.equal(calls[0].payload.inputs.thrust, 900000);
  assert.equal(calls[0].payload.inputs.pc, 11500000);
  assert.equal(calls[0].payload.name,"运行时保存的方案");
  assert.equal(calls[0].payload.description,"包含当前参数与名称说明的同一版本");
  assert.equal(calls[1].method, "POST");
  assert.equal(calls[1].payload.revision, 2);
  assert.equal(app.state().dirty, false);
  assert.equal(app.state().editorBusy, false);
  assert.equal(nodes.get("header-run").disabled, true);
});

test("a conflicting save does not submit a job or discard the current edit", async () => {
  const calls = [];
  const {app, nodes} = harness(async (url, options) => {calls.push(options.method);return response({detail: "此方案已被其他成员更新。"}, 409);});
  nodes.get("parameter-fields").oninput({target: {dataset: {input: "thrust"}, value: "950"}});
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
  const calls = [];
  const {app, nodes} = harness(async url => {
    calls.push(url);
    if (url === "/api/auth/status") return response({initialized: true, user});
    if (url === "/api/info") return response(info);
    if (url === "/api/jobs?project_id=p1" || url === "/api/projects/p1/revisions") return response([]);
    throw new Error(`Reconnection must not overwrite the draft: ${url}`);
  });
  nodes.get("parameter-fields").oninput({target: {dataset: {input: "thrust"}, value: "975"}});
  nodes.get("project-description").value = "重连期间保留的方案说明";
  nodes.get("project-description").oninput();
  await app.initialize();
  assert.equal(app.state().draft.thrust, 975000);
  assert.equal(app.state().dirty, true);
  assert.equal(nodes.get("project-description").value,"重连期间保留的方案说明");
  assert.equal(calls.includes("/api/projects/p1"), false);
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
  assert.equal(nodes.get("header-run").disabled, true);
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


test("switching propellants preserves numbers, blocks incompatible runs, and loads an explicit matching example", async () => {
  const base={...defaults,propellant_pair:"lox_methane",fuel_inlet_temp:100,cooling_bartz_mode:1,cooling_dp_auto:0};
  const fluidFields=[...fields,{key:"propellant_pair",default:"lox_methane",label:"推进剂组合"},
    {key:"fuel_inlet_temp",default:100,label:"燃料入口温度",type:"number"},
    {key:"cooling_bartz_mode",default:1,label:"吸热量模型",type:"number"},
    {key:"cooling_dp_auto",default:0,label:"压降模型",type:"number"}];
  const fluidSchema={defaults:base,groups:[{id:"cycle",title:"循环",fields:fluidFields}],
    propellant_models:[{id:"lox_methane",label:"液氧/甲烷",supported_cycles:["gg","ffsc"],limitations:[]},
      {id:"lox_kerosene",label:"液氧/煤油（正十二烷替代模型）",supported_cycles:["gg"],
        inlet_temperature_min_K:263.6,inlet_temperature_max_K:700,limitations:["未标定真实 RP-1"]}]};
  const kerosene={...base,propellant_pair:"lox_kerosene",fuel_inlet_temp:290,cooling_bartz_mode:0};
  const localInfo={...info,ui_schema:fluidSchema,presets:[{key:"kerosene",inputs:kerosene}]};
  const {app,nodes}=harness(async()=>{throw Error("Selection must not submit or save");});
  app.seed(copy({current:project,draft:base,dirty:false,user,info:localInfo,
    groups:fluidSchema.groups,fields:fluidFields,page:"project",projectJobs:[],connected:true}));
  nodes.get("parameter-fields").oninput({target:{dataset:{input:"propellant_pair"},value:"lox_kerosene"}});
  assert.equal(app.state().draft.fuel_inlet_temp,100);
  assert.equal(app.state().draft.cooling_bartz_mode,1);
  assert.equal(nodes.get("header-run").disabled,true);
  assert.match(nodes.get("propellant-model-error").textContent,/指定吸热量/);
  await assert.rejects(app.runDesign(),/指定吸热量/);
  nodes.get("load-propellant-example").onclick();
  assert.deepEqual(copy(app.state().draft),kerosene);
  assert.equal(app.state().dirty,true);
  assert.equal(nodes.get("header-run").disabled,false);
  assert.match(nodes.get("propellant-model-label").textContent,/正十二烷/);
  nodes.get("parameter-fields").oninput({target:{dataset:{input:"cycle_type"},value:"ffsc"}});
  assert.equal(nodes.get("header-run").disabled,true);
  assert.match(nodes.get("propellant-model-error").textContent,/支持 GG/);
  await assert.rejects(app.runDesign(),/支持 GG/);
});

// Model switching tests use distinct inputs for every fuel/cooling decision.
// These are UI fixtures; the real numerical cases are covered by Python tests.
const modelDefaults={...defaults,propellant_pair:"lox_methane",gg_layout:"single_shaft",
  fuel_inlet_temp:100,ox_inlet_temp:70,cooling_bartz_mode:1,cooling_dp_auto:0,
  cooling_heat_load:63e6,cooling_dp_pa:2e6,gg_temp_target:850};
const modelPresets=[
  {key:"merlin1d_gg",label:"Merlin 1D · 工程示例",inputs:{...modelDefaults,propellant_pair:"lox_kerosene",
    thrust:845000,pc:9.7e6,fuel_inlet_temp:290,ox_inlet_temp:90,cooling_bartz_mode:0,cooling_heat_load:20e6}},
  {key:"tq12a_gg",label:"TQ-12A · 工程示例",inputs:{...modelDefaults}},
  {key:"raptor3_ffsc",label:"Raptor · 工程示例",inputs:{...modelDefaults,cycle_type:"ffsc",
    thrust:2450000,pc:33e6,fuel_inlet_temp:110,ox_inlet_temp:85,cooling_dp_auto:1,cooling_heat_load:100e6}},
];
const modelGroups=[
  {id:"architecture",title:"循环与推进剂",fields:[
    {key:"propellant_pair",default:"lox_methane",label:"推进剂",choices:[
      {value:"lox_methane",label:"液氧/甲烷"},{value:"lox_kerosene",label:"液氧/煤油"}]},
    {key:"cycle_type",default:"gg",label:"循环",choices:[{value:"gg",label:"GG"},{value:"ffsc",label:"FFSC"}]},
  ]},
  {id:"gg",title:"燃气发生器",cycles:["gg"],fields:[
    {key:"gg_layout",default:"single_shaft",label:"GG 轴系",cycles:["gg"],choices:[
      {value:"single_shaft",label:"单轴"},{value:"dual_shaft",label:"双轴"}]},
    {key:"gg_temp_target",default:850,label:"发生器温度",type:"number"},
  ]},
  {id:"design",title:"性能与供给",fields:[...fields.filter(f=>f.key!=="cycle_type"),
    ...["fuel_inlet_temp","ox_inlet_temp","cooling_bartz_mode","cooling_dp_auto","cooling_heat_load","cooling_dp_pa"]
      .map(key=>({key,default:modelDefaults[key],label:key,type:"number",
        ...(key==="cooling_bartz_mode"?{choices:[{value:0,label:"给定吸热量"},{value:1,label:"Bartz"}]}:{})}))]},
];
const modelFields=modelGroups.flatMap(group=>group.fields);
const modelInfo={...info,presets:modelPresets,ui_schema:{defaults:modelDefaults,groups:modelGroups,
  propellant_models:[
    {id:"lox_methane",label:"液氧/甲烷",supported_cycles:["gg","ffsc"],limitations:[]},
    {id:"lox_kerosene",label:"液氧/煤油（正十二烷替代模型）",supported_cycles:["gg"],
      inlet_temperature_min_K:263.6,inlet_temperature_max_K:700,limitations:["未标定真实 RP-1"]},
  ]}};
const modelProject={...project,inputs:modelPresets[1].inputs};
function modelHarness(fetcher=async()=>{throw Error("Switching examples must not send requests");}){
  const h=harness(fetcher);
  h.app.seed(copy({current:modelProject,draft:modelProject.inputs,dirty:false,user,info:modelInfo,
    groups:modelGroups,fields:modelFields,page:"project",projectJobs:[],connected:true}));
  h.app.fillProject(copy(modelProject));
  return h;
}
function selectModel(h,key){h.nodes.get("project-preset").value=key;h.nodes.get("project-preset").onchange();}
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

test("Merlin, TQ and Raptor selections immediately load the complete matching case without save or run",()=>{
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
    if(preset.inputs.cycle_type==="gg"){
      assert.match(rendered,new RegExp(`value="${preset.inputs.gg_layout}" selected`));
      assert.match(h.nodes.get("parameter-group").innerHTML,/value="gg"/);
    }else{
      assert.doesNotMatch(rendered,/data-input="gg_layout"|data-input="gg_temp_target"/);
      assert.doesNotMatch(h.nodes.get("parameter-group").innerHTML,/value="gg"/);
    }
    assert.equal(h.nodes.get("header-run").disabled,false);
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
    selectModel(h,"merlin1d_gg");
    assert.equal(h.nodes.get("parameter-search").value,"");
    assert.equal(h.nodes.get("parameter-group").value,"architecture");
    h.nodes.get("undo-preset").onclick();
    const after=h.app.state();
    assert.deepEqual(after.draft,before.draft);
    assert.equal(after.dirty,before.dirty);
    assert.equal(after.referenceKey,before.referenceKey);
    assert.equal(h.nodes.get("parameter-search").value,"入口");
    assert.equal(h.nodes.get("parameter-group").value,"design");
    assert.equal(h.nodes.get("undo-preset").hidden,true);
    assert.equal(after.presetUndo,null);
    assert.equal(h.confirmations(),0);
  }
});

test("manual architecture changes make the case custom while thrust edits retain the reference",()=>{
  for(const [field,value] of [["gg_layout","dual_shaft"],["propellant_pair","lox_kerosene"],["cycle_type","ffsc"]]){
    const h=modelHarness();
    selectModel(h,"tq12a_gg");
    editField(h,field,value);
    assert.equal(h.app.state().draft[field],value);
    assert.equal(h.app.state().referenceKey,"");
    assert.equal(h.nodes.get("project-preset").value,"");
    assert.match(h.nodes.get("preset-context").textContent,/自定义/);
    assert.equal(h.nodes.get("undo-preset").hidden,true);
    assert.equal(h.app.state().presetUndo,null);
  }
  const h=modelHarness();
  selectModel(h,"merlin1d_gg");
  editField(h,"thrust","901.5");
  assert.equal(h.app.state().draft.thrust,901500);
  assert.equal(h.app.state().referenceKey,"merlin1d_gg");
  assert.equal(h.nodes.get("project-preset").value,"merlin1d_gg");
  assert.equal(h.nodes.get("undo-preset").hidden,true);
  h.nodes.get("apply-preset").onclick();
  assert.deepEqual(copy(h.app.state().draft),modelPresets[0].inputs);
  h.nodes.get("undo-preset").onclick();
  assert.equal(h.app.state().draft.thrust,901500);
});

test("project metadata edits expire preset undo instead of overwriting later edits",()=>{
  for(const nodeId of ["project-name","project-description"]){
    const h=modelHarness();
    selectModel(h,"merlin1d_gg");
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
  selectModel(h,"merlin1d_gg");
  editField(h,"thrust","910");
  const before=h.app.state();
  await h.app.initialize();
  const after=h.app.state();
  assert.deepEqual(after.draft,before.draft);
  assert.equal(after.referenceKey,"merlin1d_gg");
  assert.equal(h.nodes.get("project-preset").value,"merlin1d_gg");
  assert.equal(after.dirty,true);
  assert.deepEqual(after.resultRecord,before.resultRecord);
  assert.match(h.nodes.get("result-context").textContent,/历史输入的结果/);
  assert.ok(calls.every(([url,method])=>method==="GET"&&!/^\/api\/projects(?:\/p1)?$/.test(url)));
});

test("opening saved projects and importing parameters infer only exact examples and remove stale model labels",()=>{
  const h=modelHarness();
  selectModel(h,"merlin1d_gg");
  h.app.fillProject(copy({...modelProject,inputs:modelPresets[2].inputs}));
  assert.equal(h.nodes.get("project-preset").value,"raptor3_ffsc");
  assert.equal(h.app.state().dirty,false);
  assert.equal(h.app.state().presetUndo,null);
  h.app.fillProject(copy({...modelProject,inputs:{...modelPresets[1].inputs,thrust:1000000}}));
  assert.equal(h.nodes.get("project-preset").value,"");
  assert.match(h.nodes.get("preset-context").textContent,/自定义/);
  const oldMethane=copy(modelPresets[1].inputs);
  delete oldMethane.propellant_pair;
  h.app.fillProject(copy({...modelProject,inputs:oldMethane}));
  assert.equal(h.app.state().draft.propellant_pair,"lox_methane");
  assert.equal(h.nodes.get("project-preset").value,"tq12a_gg");
  for(const [inputs,key] of [[modelPresets[0].inputs,"merlin1d_gg"],[{...modelPresets[0].inputs,pc:11e6},""]]){
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
  const older={...modelProject,name:"历史方案名称",description:"旧版本设计说明",revision:1,inputs:{...modelPresets[1].inputs,gg_layout:"dual_shaft"}};
  const requests=[];
  const h=modelHarness(async(url,options)=>{
    requests.push([url,options.method]);
    if(url==="/api/jobs/j-old")return response(record);
    if(url==="/api/projects/p1/revisions/1")return response(older);
    throw Error(`Unexpected request ${url}`);
  });
  await h.app.loadRecord("j-old");
  selectModel(h,"merlin1d_gg");
  const button={dataset:{revision:"1"},hasAttribute:()=>false};
  h.body.listeners.get("click")({target:{closest:()=>button}});
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
  selectModel(h,"merlin1d_gg");
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
  for(const [key,value] of [["gg_layout","three_shafts"],["propellant_pair","lox_hydrogen"],
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
      groups:modelGroups,fields:modelFields,page:"project",projectJobs:[],connected:true,referenceKey:"tq12a_gg"}));
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
  assert.equal(after.referenceKey,"merlin1d_gg");
  assert.equal(h.nodes.get("project-preset").value,"merlin1d_gg");
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
  selectModel(h,"merlin1d_gg");
  editField(h,"thrust","902.5");
  await h.app.saveProject();
  assert.equal(saved.inputs.thrust,902500);
  assert.equal(h.app.state().draft.thrust,902500);
  assert.equal(h.app.state().referenceKey,"merlin1d_gg");
  assert.equal(h.nodes.get("project-preset").value,"merlin1d_gg");
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
  selectModel(h,"merlin1d_gg");
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
  assert.equal(h.app.state().draft.wall_thermal_dataset_id,"");
  const explicit={...materialDefaults,methane_thermo_dataset:"gri30_highT",wall_manufacturing_state:"specific_qualified_state"};
  h.app.applyInputDocument({format:"lre.workbench.inputs",schema_version:1,inputs:explicit});
  assert.deepEqual(copy(h.app.state().draft),explicit);
});

test("a missing scenario name opens its editable history field before save or run",async()=>{
  const h=harness(async()=>{throw Error("A missing name must not send a request");});
  h.nodes.get("project-name").value="   ";
  h.nodes.get("project-name").oninput();
  await assert.rejects(h.app.runDesign(),/方案与计算记录/);
  assert.equal(h.nodes.get("project-history").hidden,false);
  assert.equal(h.nodes.get("analysis-content").hidden,true);
  assert.equal(h.app.state().dirty,true);
});
