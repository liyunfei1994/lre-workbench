import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";
import {test} from "node:test";
import {thermoModelDecisionState} from "../webapp/static/thermochemistry-model.js";
import {isSolverGroup,renderSolverTask} from "../webapp/static/solver-workspace.js";
import {renderPreburnerInterfaces,renderPreburnerSummary} from "../webapp/static/preburner-design.js";
import {draftThermochemistryKey,renderEngineeringThermochemistry} from "../webapp/static/engineering-view.js";
import {engineeringObjectsForGroup,engineeringGroupForObject,engineeringRecord,draftEngineeringKey,renderEngineeringSystem,renderEngineeringSource,scopeEngineeringCycle,engineeringSourceTag,supplyDraftModel,draftSupplyKey,renderEngineeringSupply} from "../webapp/static/engineering-view.js";

test("highlight targets resolve editable groups without inventing missing settings",()=>{
  assert.deepEqual(engineeringObjectsForGroup("inlet"),["lox_tank","fuel_tank","ox_boost_pump","fuel_boost_pump","ox_feed_line","fuel_feed_line","lox_pump_inlet","fuel_pump_inlet"]);
  for(const id of engineeringObjectsForGroup("inlet"))assert.equal(engineeringGroupForObject(id,[{id:"inlet"}]),"inlet");
  assert.deepEqual(engineeringObjectsForGroup("pumps"),["ox_pump","fuel_pump"]);
  assert.equal(engineeringGroupForObject("fuel_preburner",[{id:"preburners"}]),"preburners");
  assert.equal(engineeringGroupForObject("fuel_preburner",[{id:"preburner_design"}]),"preburner_design");
  assert.equal(engineeringGroupForObject("cooling_channel",[{id:"cooling"}]),"cooling");
  assert.equal(engineeringGroupForObject("nozzle",[]),null);
});
test("saved record requires matching project and selected identity",()=>{
  const record={id:"r1",project_id:"p1"},project={id:"p1"};
  assert.equal(engineeringRecord({record,project,selectedJobId:"r1"}),record);
  assert.equal(engineeringRecord({record,project:{id:"p2"}}),null);
  assert.equal(engineeringRecord({record:{id:"r1"},project}),null);
  assert.equal(engineeringRecord({record,project,selectedJobId:"r2"}),null);
});
test("embedded cycle markers cannot resolve to a second hidden full-page SVG",()=>{
  const scoped=scopeEngineeringCycle('<marker id="cy-arrow-ox"><path class="cy-arrow"/></marker><path marker-end="url(#cy-arrow-ox)"/>');
  assert(scoped.includes('id="ev-saved-cy-arrow-ox"'));assert(scoped.includes("url(#ev-saved-cy-arrow-ox)"));
  assert(scoped.includes('class="cy-arrow"'));
  assert.equal(scopeEngineeringCycle(scoped),scoped);
});
test("draft sketch does not respond to numeric edits or populate calculation values",()=>{
  const a={cycle_type:"ffsc",propellant_pair:"lox_methane",thrust:100000,pc:30000000};
  const b={...a,thrust:22222,pc:12345};
  assert.equal(draftEngineeringKey(a),draftEngineeringKey(b));
  assert.equal(renderEngineeringSystem(a),renderEngineeringSystem(b));
  assert(!renderEngineeringSystem(a).includes("30000000"));
  assert(!renderEngineeringSystem({cycle_type:"gg"}).includes("data-ev-object"));
  assert(!renderEngineeringSystem({valve_configuration_json:"broken"}).includes("data-ev-object"));
});
test("thermochemistry diagram exposes actual model coverage without presenting calculated temperatures",()=>{
  const draft={cycle_type:"ffsc",propellant_pair:"lox_methane",gas_eos:"Peng-Robinson",methane_thermo_dataset:"gri30_highT",gas_species_policy:"auto",combustion_loss_model:"adiabatic"};
  const before=JSON.stringify(draft),html=renderEngineeringThermochemistry(draft);
  assert.equal((html.match(/data-ev-object=/g)||[]).length,6);
  assert(html.includes('data-ev-object="ox_preburner_turbine"'));
  assert(html.includes("16 种")&&html.includes("真实气体（PR）")&&html.includes("理想气体混合物"));
  assert(html.includes("冻结入口组成")&&html.includes("主室组成冻结"));
  assert(!html.includes("3600")&&!html.includes("4000"));
  assert.equal(JSON.stringify(draft),before);
  const changed={...draft,gas_eos:"ideal-gas",gas_species_policy:"full"};
  assert(renderEngineeringThermochemistry(changed).includes("53 种"));
  assert.notEqual(draftThermochemistryKey(draft),draftThermochemistryKey(changed));
  assert.equal(draftThermochemistryKey(draft),draftThermochemistryKey({...draft,pc:1,thrust:2}));
  assert(!renderEngineeringThermochemistry({...draft,gas_eos:'<script>alert(1)</script>'}).includes("<script>"));
  assert(!renderEngineeringThermochemistry({cycle_type:"gg"}).includes("data-ev-object"));
});
test("draft schematic respects the configured fuel split without altering inputs",()=>{
  for(const before of [true,false]){
    const ports={MFV:["CH4","fuel_after_pump","cooling_inlet"],OPFV:["CH4",before?"fuel_after_pump":"fuel_after_cooling","opfv_outlet"],FPOV:["O2","lox_after_pump","fpov_outlet"]};
    const input={valve_configuration_json:JSON.stringify({schema_version:1,enabled:true,topology_id:before?"paper_split_before_cooling_v1":"all_fuel_cooled_then_split_valves_v1",valves:Object.fromEntries(Object.entries(ports).map(([id,[fluid,upstream_node,downstream_node]])=>[id,{id,fluid,upstream_node,downstream_node}]))})};
    const original=JSON.stringify(input),markup=renderEngineeringSystem(input);
    assert(markup.includes(`data-ev-from="${before?"fuel_pump":"cooling_channel"}" data-ev-to="ox_preburner"`));
    assert.equal(JSON.stringify(input),original);
  }
});
test("source labels separate current revision, saved revision and stale evidence",()=>{
  const state={project:{id:"p1",revision:9},dirty:true,record:{id:"<r1>",project_id:"p1",revision:4,created:1,status:"succeeded"},workflow:{freshness:{state:"stale",label:"结果待更新",reason:"2 项输入改变"}}};
  const draft=renderEngineeringSource(state,"system"),saved=renderEngineeringSource(state,"saved");
  assert(draft.includes("基于方案 v9")&&draft.includes("未保存")&&!draft.includes("方案 v4"));
  assert(!draft.includes("草稿"));
  assert(saved.includes("方案 v4")&&saved.includes("结果待更新")&&!saved.includes("方案 v9"));
  assert(saved.includes("&lt;r1&gt;")&&!saved.includes("<r1>"));
  assert(renderEngineeringSource({...state,record:{...state.record,project_id:"p2"}},"saved").includes("不属于当前方案"));
});
test("persistent header identifies the actual data version and freshness at a glance",()=>{
  const state={project:{id:"p1",revision:9},dirty:true,record:{id:"r1",project_id:"p1",revision:4}};
  assert.equal(engineeringSourceTag(state),"当前设置 · 基于 v9 · 未保存");
  assert.equal(engineeringSourceTag({...state,dirty:false}),"当前设置 v9 · 已保存");
  for(const [status,label] of [["current","结果 v4"],["stale","结果 v4 · 待更新"],["invalid","结果 v4 · 已撤回"],["unknown","结果 v4 · 待核对"]])
    assert.equal(engineeringSourceTag({...state,workflow:{freshness:{state:status}}},"saved"),label);
  assert.equal(engineeringSourceTag({project:state.project},"saved"),"尚无结果");
  assert.equal(engineeringSourceTag({project:state.project,selectedJobId:"r1"},"saved"),"读取中");
  assert.equal(engineeringSourceTag({project:state.project,selectedJobId:"r1",recordError:"网络中断"},"saved"),"读取失败");
});

function element(dataset={}){
  let html="";const children=new Map(),listeners=new Map(),attributes=new Map();
  return {dataset,listeners,attributes,writes:0,value:"",textContent:"",hidden:false,disabled:false,
    classList:{add(){},toggle(){}},
    get innerHTML(){return html;},set innerHTML(value){html=value;this.writes++;children.clear();},
    querySelector(selector){
      const attr=/^\[([^\]=]+)(?:="([^\"]*)")?\]$/.exec(selector);if(!attr||!html.includes(attr[1]))return null;
      if(!children.has(selector))children.set(selector,element());return children.get(selector);
    },querySelectorAll(){return [];},contains(){return true;},
    setAttribute(key,value){attributes.set(key,String(value));},hasAttribute(key){return attributes.has(key);},
    addEventListener(type,listener){listeners.set(type,listener);},removeEventListener(type){listeners.delete(type);},
    closest(){return this;},
  };
}
function harness(){
  const calls={render:[],mount:[],dispose:0,groups:[],records:[],states:[],components:[],views:[],cycleOptions:null};
  const source=readFileSync(new URL("../webapp/static/engineering-view.js",import.meta.url),"utf8").replace(/^import .*;\r?\n/gm,"").replace(/^export /gm,"");
  const context=vm.createContext({Intl,Date,JSON,Number,Object,String,Promise,thermoModelDecisionState,isSolverGroup,renderSolverTask,renderPreburnerInterfaces,renderPreburnerSummary,
    savedValveTopology:()=>null,supportsCycleResult:()=>true,
    buildCycleModel:result=>result,flowAnalysisModel:()=>({}),componentAnalysisItems:()=>[],
    renderCycleWorkspace:()=>'<section class="cy-workspace"></section>',mountCycleWorkspace(host,id,options){calls.cycleOptions=options;},
    ...Object.fromEntries(["Nozzle","Pump","Turbine"].flatMap(kind=>[
      [`render${kind}Geometry`,model=>{calls.render.push([kind,model]);return `<div>${kind}</div>`;}],
      [`mount${kind}Geometry`,(host,model)=>{calls.mount.push([kind,model]);return ()=>calls.dispose++;}],
    ])),
  });vm.runInContext(source,context);
  const host=element(),controller=context.mountEngineeringView(host,{onGroup:id=>calls.groups.push(id),onRecord:id=>calls.records.push(id),onState:(id,group)=>calls.states.push([id,group]),onComponent:id=>calls.components.push(id),onView:(view,section)=>calls.views.push([view,section])});
  return {host,controller,calls,body:()=>host.querySelector("[data-ev-body]"),
    click(dataset){let prevented=false;const target=element(dataset);for(const key of Object.keys(dataset))target.setAttribute("data-"+key.replace(/[A-Z]/g,c=>"-"+c.toLowerCase()),dataset[key]);host.listeners.get("click")({target,preventDefault(){prevented=true;}});return prevented;},
    select(view){const select=host.querySelector("[data-ev-result-view]");select.value=view;host.listeners.get("change")({target:select});},
  };
}
const fixture=()=>({project:{id:"p1",revision:3},draft:{pc:30000000},dirty:false,groupId:"design",groups:[{id:"design",title:"设计目标"},{id:"pumps",title:"泵设计"}],jobs:[],selectedJobId:"r1",record:{id:"r1",project_id:"p1",revision:2,status:"succeeded",result:{calculation:{}},nozzle_geometry:{source:{record_id:"r1"},marker:"saved"}},workflow:{freshness:{state:"current",label:"输入一致",reason:"输入快照一致"}}});

test("preburner source navigation offers a return and reflects current cooling mode",()=>{
  const h=harness(),state={...fixture(),groupId:"preburner_design",draft:{cooling_bartz_mode:2},groups:[{id:"preburner_design",title:"预燃室热力匹配条件"},{id:"cooling",title:"冷却模型"}]};
  h.controller.update(state);assert.match(h.body().innerHTML,/关联模型与参数来源/);assert.match(h.body().innerHTML,/概略受热面/);
  h.click({evRelated:"cooling",evOrigin:"preburner_design"});assert.deepEqual(h.calls.groups,["cooling"]);
  h.controller.update({...state,groupId:"cooling"});assert.match(h.host.querySelector("[data-ev-context]").innerHTML,/data-ev-return="preburner_design"/);
  h.click({evReturn:"preburner_design"});assert.deepEqual(h.calls.groups,["cooling","preburner_design"]);
  h.controller.update({...state,draft:{cooling_bartz_mode:1}});assert.match(h.body().innerHTML,/沿程模型/);
  assert.doesNotMatch(h.host.querySelector("[data-ev-context]").innerHTML,/data-ev-return/);
  h.controller.dispose();
});

test("preburner saved tab uses record targets and keeps the snapshot through edits",()=>{
  const h=harness(),state={...fixture(),groupId:"preburner_design"};
  state.record.result.calculation={inputs:{cycle_type:"ffsc",use_target_preburner_temp:1,ox_preburner_temp_target:750,ox_preburner_temp_limit_status:"unset"},summary:{ox_preburner_T_combustion_K:749}};
  h.controller.update(state);h.click({evTab:"saved"});
  const saved=h.body().querySelector("[data-ev-saved-body]"),html=saved.innerHTML,writes=h.body().writes;
  assert.match(html,/预燃室匹配摘要/);assert.match(html,/>750</);assert.match(html,/未评估/);
  h.controller.update({...state,draft:{ox_preburner_temp_target:999},workflow:{freshness:{state:"stale",label:"结果待更新"}}});
  assert.equal(h.body().writes,writes);assert.equal(saved.innerHTML,html);assert.doesNotMatch(saved.innerHTML,/999/);
  h.click({evOpen:""});assert.deepEqual(h.calls.views,[["design_requirements","preburner-requirements"]]);
  h.controller.update({...state,record:{...state.record,project_id:"foreign"}});
  assert.match(h.body().innerHTML,/结果来源不匹配/);assert.doesNotMatch(h.body().innerHTML,/>750</);
  h.controller.dispose();
});

test("engineering limits link to working conditions and inspect saved evidence without rewriting it",()=>{
  const h=harness(),state={...fixture(),groupId:"engineering_constraints",groups:[{id:"engineering_constraints",title:"工程限值与校核"},{id:"preburner_design",title:"预燃室热力匹配条件"}]};
  state.record.result.calculation={inputs:{cycle_type:"ffsc",use_target_preburner_temp:1,ox_preburner_temp_target:750,ox_preburner_temp_limit_status:"provisional",ox_preburner_temp_limit:800},summary:{ox_preburner_T_combustion_K:749}};
  const before=JSON.stringify(state);
  h.controller.update(state);
  assert.match(h.body().innerHTML,/data-ev-related="preburner_design" data-ev-origin="engineering_constraints"/);
  h.click({evRelated:"preburner_design",evOrigin:"engineering_constraints"});
  h.controller.update({...state,groupId:"preburner_design"});
  assert.match(h.host.querySelector("[data-ev-context]").innerHTML,/返回工程限值与校核/);
  h.click({evReturn:"engineering_constraints"});assert.deepEqual(h.calls.groups,["preburner_design","engineering_constraints"]);
  h.controller.update(state);h.click({evTab:"saved"});
  const saved=h.body().querySelector("[data-ev-saved-body]");
  assert.match(saved.innerHTML,/预燃室匹配摘要/);assert.match(saved.innerHTML,/>800</);
  h.controller.update({...state,draft:{ox_preburner_temp_limit_status:"unset"},workflow:{freshness:{state:"stale",label:"结果待更新"}}});
  assert.match(saved.innerHTML,/>800</);assert.equal(JSON.stringify(state),before);
  h.controller.dispose();
});

test("preburner summary can open during input validation without changing the draft",()=>{
  const h=harness(),state={...fixture(),groupId:"preburner_design",busy:true};
  const before=JSON.stringify(state);
  h.controller.update(state);h.click({evPreburnerSummary:""});
  assert.match(h.body().querySelector("[data-ev-saved-body]").innerHTML,/预燃室匹配摘要/);
  assert.equal(h.host.querySelector("[data-ev-controls]").hidden,false);
  assert.equal(JSON.stringify(state),before);
  assert.deepEqual(h.calls.groups,[]);
  h.controller.dispose();
});
test("numerical pages show the current task; returning to physical inputs restores object context",()=>{
  const h=harness(),state=fixture(),before=JSON.stringify(state);
  h.controller.update({...state,groupId:"solver"});
  assert(h.body().innerHTML.includes("本次设计点任务"));
  assert(h.host.querySelector("[data-ev-context]").innerHTML.includes("当前任务配置"));
  h.click({taskGroup:"preburner_design"});assert.deepEqual(h.calls.groups,["preburner_design"]);
  h.controller.update({...state,groupId:"pumps"});
  assert(h.body().innerHTML.includes("data-ev-object"));
  assert(h.body().innerHTML.includes("参数归属与相互影响"));
  h.click({evRelated:"solver"});assert.deepEqual(h.calls.groups,["preburner_design","solver"]);
  h.controller.update({...state,groupId:"initialization",busy:true});
  h.click({taskGroup:"design"});assert.equal(h.calls.groups.length,2);
  assert.equal(JSON.stringify(state),before);h.controller.dispose();
});

test("an explicitly selected saved result stays immutable while entering solver and initialization",()=>{
  const h=harness(),state=fixture();h.controller.update(state);h.click({evTab:"saved"});h.select("nozzle");
  const writes=h.body().writes;
  for(const groupId of ["solver","initialization"]){
    h.controller.update({...state,groupId,draft:{...state.draft,chamber_temp_guess:4000},dirty:true});
    assert.equal(h.body().writes,writes);assert.equal(h.calls.dispose,0);
    assert.equal(h.calls.render[0][1],state.record.nozzle_geometry);
  }
  h.controller.dispose();
});

test("editing or moving between groups preserves the mounted saved viewer and immutable geometry",()=>{
  const h=harness(),state=fixture(),before=JSON.stringify(state.record);
  h.controller.update(state);h.click({evTab:"saved"});h.select("nozzle");
  const writes=h.body().writes;
  assert.equal(h.calls.mount.length,1);
  h.controller.update({...state,draft:{pc:45000000},dirty:true,groupId:"pumps",workflow:{freshness:{state:"stale",label:"结果待更新",reason:"室压改变"}}});
  assert.equal(h.body().writes,writes);assert.equal(h.calls.mount.length,1);assert.equal(h.calls.dispose,0);
  assert.equal(h.calls.render[0][1],state.record.nozzle_geometry);
  assert.equal(JSON.stringify(state.record),before);
  assert(h.host.querySelector("[data-ev-context]").innerHTML.includes("结果待更新"));
  h.controller.dispose();assert.equal(h.calls.dispose,1);assert.equal(h.host.listeners.size,0);
});
test("changing result or project disposes old geometry and prevents a cross-project display",()=>{
  const h=harness(),state=fixture();h.controller.update(state);h.click({evTab:"saved"});h.select("nozzle");
  h.controller.update({...state,project:{id:"p2",revision:1}});
  assert.equal(h.calls.dispose,1);assert.equal(h.calls.mount.length,1);assert(h.body().innerHTML.includes("结果来源不匹配"));
  h.controller.update({...state,record:{...state.record,id:"r2",nozzle_geometry:{source:{record_id:"r2"}}},selectedJobId:"r2"});
  assert.equal(h.calls.mount.length,2);h.controller.dispose();assert.equal(h.calls.dispose,2);
});
test("missing geometry and mismatched geometry source do not fall back to draft geometry",()=>{
  const h=harness(),state=fixture();delete state.record.nozzle_geometry;
  state.draft.nozzle_geometry={marker:"must not render"};h.controller.update(state);h.click({evTab:"saved"});h.select("nozzle");
  assert.equal(h.calls.mount.length,0);assert(h.body().querySelector("[data-ev-saved-body]").innerHTML.includes("未保存"));
  h.controller.update({...state,record:{...state.record,nozzle_geometry:{source:{record_id:"other"}}}});
  assert.equal(h.calls.mount.length,0);assert(h.body().querySelector("[data-ev-saved-body]").innerHTML.includes("不一致"));
  h.controller.dispose();
});
test("object navigation and saved-record selection are actions with no draft mutation",()=>{
  const h=harness(),state=fixture(),before=JSON.stringify(state);h.controller.update(state);
  h.click({evObject:"ox_pump"});assert.deepEqual(h.calls.groups,["pumps"]);
  const select=h.host.querySelector("[data-ev-record]");select.value="r2";h.host.listeners.get("change")({target:select});
  assert.deepEqual(h.calls.records,["r2"]);assert.equal(JSON.stringify(state),before);
  h.controller.update({...state,busy:true});h.click({evObject:"ox_pump"});assert.equal(h.calls.groups.length,1);h.controller.dispose();
});
test("keyboard selection in saved cycle uses the same parameter navigation as mouse",()=>{
  const h=harness(),state=fixture();h.controller.update(state);h.click({evTab:"saved"});
  let prevented=false;h.host.listeners.get("keydown")({key:"Enter",target:element({cyObject:"fuel_pump"}),preventDefault(){prevented=true;}});
  assert(prevented);assert.deepEqual(h.calls.groups,["pumps"]);h.controller.dispose();
});
test("record loading failure replaces the loading view and retries the selected record",()=>{
  const h=harness(),state={...fixture(),record:null};h.controller.update(state);h.click({evTab:"saved"});
  assert(h.body().innerHTML.includes("正在读取"));
  h.controller.update({...state,recordError:"网络读取失败 <detail>"});
  assert(h.body().innerHTML.includes("网络读取失败 &lt;detail&gt;"));assert(h.body().innerHTML.includes("data-ev-retry"));
  h.click({evRetry:""});assert.deepEqual(h.calls.records,["r1"]);
  h.controller.update({...state,recordError:""});assert(h.body().innerHTML.includes("正在读取"));
  h.controller.update(fixture());assert(!h.body().innerHTML.includes("data-ev-retry"));h.controller.dispose();
});
test("saved cycle inspector links forward state and component identities without hash navigation",()=>{
  const h=harness(),state=fixture();h.controller.update(state);h.click({evTab:"saved"});
  const writes=h.body().writes;
  assert(h.click({flowState:"lox_after_pump",flowGroup:"oxidizer"}));
  assert.deepEqual(h.calls.states,[["lox_after_pump","oxidizer"]]);
  h.calls.cycleOptions.onComponent("ox_pump");assert.deepEqual(h.calls.components,["ox_pump"]);
  assert.equal(h.calls.groups.length,0);assert.equal(h.body().writes,writes);
  h.controller.update({...state,busy:true});
  assert(h.click({flowState:"fuel_after_pump",flowGroup:"fuel"}));
  h.calls.cycleOptions.onComponent("fuel_pump");assert.equal(h.calls.states.length,1);assert.equal(h.calls.components.length,1);
  h.controller.dispose();
});

const supplyInputs=()=>({tank_pressure_pa:3e5,inlet_pressure:1e6,feed_line_dp_pa:5e4,boost_pump_efficiency:.7,ox_inlet_temp:70,fuel_inlet_temp:100});
test("supply draft inherits legacy boundaries while explicit side conditions remain independent",()=>{
  const draft={...supplyInputs(),fuel_tank_pressure_pa:4e5,fuel_boost_outlet_pressure_pa:1.2e6,fuel_feed_line_dp_pa:0,fuel_boost_pump_efficiency:.8,ox_tank_pressure_pa:null};
  const original=JSON.stringify(draft),[ox,fuel]=supplyDraftModel(draft);
  assert.equal(ox.tank.value,3e5);assert(ox.tank.inherited);assert.equal(ox.inletPressure,9.5e5);
  assert.equal(fuel.tank.value,4e5);assert(!fuel.tank.inherited);assert.equal(fuel.inletPressure,1.2e6);
  assert.equal(fuel.loss.value,0);assert(!fuel.loss.inherited);assert.equal(fuel.eta.value,.8);
  assert.equal(JSON.stringify(draft),original);
});
test("invalid explicit boundaries do not silently fall back or yield plausible inlet pressures",()=>{
  for(const value of ["",NaN,Infinity,-1,0]){
    const [ox]=supplyDraftModel({...supplyInputs(),ox_boost_outlet_pressure_pa:value});
    assert.equal(ox.boost.state,"invalid");assert.equal(ox.boost.inherited,false);
    assert.equal(ox.inletPressure,null);assert(ox.issues.length);
  }
  for(const extra of [{ox_feed_line_dp_pa:-1},{ox_feed_line_dp_pa:1e6},{ox_boost_outlet_pressure_pa:2e5}]){
    const [ox]=supplyDraftModel({...supplyInputs(),...extra});assert.equal(ox.inletPressure,null);assert(ox.issues.length);
  }
  const [missing]=supplyDraftModel({});assert.equal(missing.inletPressure,null);assert.equal(missing.tank.state,"missing");
});
test("supply view labels draft pressure arithmetic and never invents solved temperature or phase",()=>{
  const html=renderEngineeringSupply(supplyInputs());
  assert(html.includes("0.95"));assert(html.includes("压力关系计算"));
  assert.equal((html.match(/求解后查看/g)||[]).length,2);
  assert(html.includes("主泵入口压力 = 前置泵出口压力 − 管路压降"));
  assert(!html.includes("相态：液体"));assert(!renderEngineeringSupply({propellant_pair:"lox_hydrogen"}).includes("ev-supply-chain"));
  assert(draftSupplyKey({...supplyInputs(),ox_feed_line_dp_pa:0})!==draftSupplyKey(supplyInputs()));
});
test("supply page changes remount the draft detail without affecting other schematic pages",()=>{
  const h=harness(),state={...fixture(),draft:supplyInputs(),groupId:"inlet",groups:[{id:"inlet",title:"推进剂供给条件"},{id:"design",title:"设计目标"}]};
  h.controller.update(state);assert(h.body().innerHTML.includes("ev-supply-chain"));assert(h.body().innerHTML.includes("0.95"));
  const writes=h.body().writes;
  h.controller.update({...state,draft:{...state.draft,ox_boost_outlet_pressure_pa:1.1e6},dirty:true});
  assert.equal(h.body().writes,writes+1);assert(h.body().innerHTML.includes("1.05"));
  h.click({evObject:"ox_feed_line"});assert.deepEqual(h.calls.groups,["inlet"]);
  h.controller.update({...state,groupId:"design"});assert(!h.body().innerHTML.includes("ev-supply-chain"));
  const systemWrites=h.body().writes;
  h.controller.update({...state,groupId:"design",draft:{...state.draft,ox_boost_outlet_pressure_pa:1.1e6}});
  assert.equal(h.body().writes,systemWrites);h.controller.dispose();
});
test("supply draft edits never remount or relabel a selected saved result",()=>{
  const h=harness(),state={...fixture(),draft:supplyInputs(),groupId:"inlet",groups:[{id:"inlet",title:"推进剂供给条件"}]};
  h.controller.update(state);h.click({evTab:"saved"});h.select("nozzle");
  const writes=h.body().writes;
  h.controller.update({...state,draft:{...state.draft,ox_boost_outlet_pressure_pa:2e6},dirty:true});
  assert.equal(h.body().writes,writes);assert.equal(h.calls.mount.length,1);
  assert.equal(h.calls.render[0][1],state.record.nozzle_geometry);
  assert(h.host.querySelector("[data-ev-context]").innerHTML.includes("方案 v2"));
  h.controller.dispose();
});
test("thermochemistry configuration refreshes the draft diagram but preserves a selected saved viewer",()=>{
  const h=harness(),state={...fixture(),groupId:"chamber_chemistry",draft:{cycle_type:"ffsc",gas_eos:"Peng-Robinson-Boston-Mathias",gas_species_policy:"auto",methane_thermo_dataset:"gri30_highT",combustion_loss_model:"adiabatic"}};
  h.controller.update(state);assert(h.body().innerHTML.includes("16 种"));
  const changed={...state,draft:{...state.draft,gas_eos:"ideal-gas",gas_species_policy:"full"},dirty:true};
  h.controller.update(changed);assert(h.body().innerHTML.includes("53 种"));
  h.click({evTab:"saved"});h.select("nozzle");const writes=h.body().writes;
  h.controller.update(state);assert.equal(h.body().writes,writes);
  assert.equal(h.calls.mount.length,1);assert.equal(h.calls.render[0][1],state.record.nozzle_geometry);
  h.controller.dispose();
});
