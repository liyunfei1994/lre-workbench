/** Read-only result navigation: visible evidence, history isolation and keyboard use. */
import assert from "node:assert/strict";
import {test} from "node:test";
import {renderSteadyWorkspace,mountSteadyWorkspace} from "../webapp/static/steady-workspace.js";
import {renderAnalysis,configureResultSchema} from "../webapp/static/results.js";

const IDS=["overview","checks","cooling","convergence","models"];
const TITLES=["性能总览","系统校核","再生冷却","收敛诊断","模型与物性"];
const pages=()=>IDS.map((id,index)=>({id,title:TITLES[index],content:`<p>content:${id}</p>`}));

// Panels contain nested sections, so a first </section> does not delimit them.
// The next panel opening is the reliable boundary in this renderer's output.
function panelsOf(html) {
  const openings=[...html.matchAll(/<([a-z][a-z\d-]*)\b[^>]*\bdata-steady-panel="([^"]+)"[^>]*>/gi)];
  return openings.map((match,index)=>({id:match[2],opening:match[0],
    hidden:/\shidden(?:\s|=|>)/.test(match[0]),
    content:html.slice(match.index+match[0].length,openings[index+1]?.index ?? html.length)}));
}

// Project the visible text without treating hidden panels or closed details as
// user-visible evidence. This is only a deterministic HTML test helper.
function visibleText(html) {
  const stack=[], output=[];
  for (const match of html.matchAll(/<!--[\s\S]*?-->|<[^>]+>|[^<]+/g)) {
    const token=match[0];
    if(token.startsWith("<!--"))continue;
    if(!token.startsWith("<")) {
      if(!stack.some(item=>item.hidden || (item.closedDetails && !stack.some(child=>child.summary && child.depth>item.depth))))output.push(token);
      continue;
    }
    const close=token.match(/^<\/([\w-]+)/);
    if(close){const index=stack.findLastIndex(item=>item.tag===close[1]);if(index>=0)stack.splice(index);continue;}
    const open=token.match(/^<([\w-]+)/);if(!open)continue;
    if(["br","hr","img","input","meta","link","source","wbr"].includes(open[1])||/\/>$/.test(token))continue;
    stack.push({tag:open[1],depth:stack.length,hidden:/\shidden(?:\s|=|>)/.test(token),
      closedDetails:open[1]==="details"&&!/\sopen(?:\s|=|>)/.test(token),summary:open[1]==="summary"});
  }
  return output.join(" ");
}

class Element {
  constructor(dataset={},parent=null){
    this.dataset=dataset;this.parentElement=parent;this.attrs={};this.hidden=false;this.disabled=false;
    this.tabIndex=-1;this.focusCount=0;this.classes=new Set();
    this.classList={toggle:(key,value)=>value?this.classes.add(key):this.classes.delete(key),contains:key=>this.classes.has(key)};
  }
  setAttribute(key,value){this.attrs[key]=String(value);if(key==="tabindex")this.tabIndex=Number(value);}
  getAttribute(key){return this.attrs[key] ?? null;}
  hasAttribute(key){return Object.hasOwn(this.attrs,key);}
  removeAttribute(key){delete this.attrs[key];}
  focus(){this.focusCount++;}
  scrollIntoView(){}
  matches(selector){
    return selector.split(",").some(item=>{
      const trimmed=item.trim();
      if(trimmed===".steady-workspace")return this.classes.has("steady-workspace");
      const key=trimmed.match(/\[data-([\w-]+)(?:="([^"]*)")?\]/);
      if(!key)return false;
      const property=key[1].replace(/-([a-z])/g,(_,c)=>c.toUpperCase());
      return Object.hasOwn(this.dataset,property)&&(key[2]===undefined||this.dataset[property]===key[2]);
    });
  }
  closest(selector){return this.matches(selector)?this:this.parentElement?.closest?.(selector) ?? null;}
}

function workspace(ids=IDS){
  const root=new Element();root.classes.add("steady-workspace");
  root.tabs=ids.map(id=>new Element({steadyTab:id},root));
  root.panels=ids.map(id=>new Element({steadyPanel:id},root));
  root.links=ids.map(id=>new Element({steadyOpen:id},root));
  root.elements=[...root.tabs,...root.panels,...root.links];
  root.querySelectorAll=selector=>root.elements.filter(item=>item.matches(selector));
  root.querySelector=selector=>root.querySelectorAll(selector)[0] ?? null;
  root.contains=element=>element===root||root.elements.includes(element)||root.elements.includes(element?.parentElement);
  return root;
}

function host(){
  return {dataset:{},workspace:workspace(),listeners:{click:new Set(),keydown:new Set()},
    querySelector(selector){return selector===".steady-workspace"?this.workspace:null;},
    contains(element){return this.workspace?.contains(element) ?? false;},
    addEventListener(type,listener){this.listeners[type].add(listener);},
    removeEventListener(type,listener){this.listeners[type].delete(listener);},
    fire(type,target,key){let prevented=false;for(const fn of [...this.listeners[type]])fn({target,key,preventDefault(){prevented=true;}});return prevented;},
    click(target){return this.fire("click",target);},key(target,key){return this.fire("keydown",target,key);},
  };
}

const tab=(node,id)=>node.workspace.tabs.find(item=>item.dataset.steadyTab===id);
const link=(node,id)=>node.workspace.links.find(item=>item.dataset.steadyOpen===id);
function assertSelected(node,id){
  assert.equal(node.dataset.steadySelected,id);
  assert.deepEqual(node.workspace.panels.filter(item=>!item.hidden).map(item=>item.dataset.steadyPanel),[id]);
  assert.deepEqual(node.workspace.tabs.filter(item=>item.getAttribute("aria-selected")==="true").map(item=>item.dataset.steadyTab),[id]);
  assert.equal(tab(node,id).tabIndex,0);
  assert(node.workspace.tabs.filter(item=>item.dataset.steadyTab!==id).every(item=>item.tabIndex===-1));
}

test("initial markup exposes only overview with semantic tab and panel relationships",()=>{
  const html=renderSteadyWorkspace(pages()), panels=panelsOf(html);
  assert.match(html,/class="[^"]*steady-workspace/);
  assert.match(html,/role="tablist"/);
  assert.deepEqual(panels.map(item=>item.id),IDS);
  assert.deepEqual(panels.filter(item=>!item.hidden).map(item=>item.id),["overview"]);
  for(const [index,id] of IDS.entries()){
    const button=html.match(new RegExp(`<button\\b[^>]*data-steady-tab="${id}"[^>]*>`))?.[0];
    assert(button,`missing tab ${id}`);
    assert.match(button,/role="tab"/);
    assert.match(button,new RegExp(`aria-selected="${index===0}"`));
    const controls=button.match(/aria-controls="([^"]+)"/)?.[1];
    const buttonId=button.match(/\bid="([^"]+)"/)?.[1];
    assert(controls);assert(buttonId);
    assert.match(panels[index].opening,/role="tabpanel"/);
    assert(panels[index].opening.includes(`id="${controls}"`));
    assert(panels[index].opening.includes(`aria-labelledby="${buttonId}"`));
  }
});

test("tab labels and optional descriptions are escaped while page HTML remains renderable",()=>{
  const items=pages(),attack='<img src=x onerror="alert(1)">';
  items[1].title=attack;items[1].meta=attack;items[1].content="<strong>saved content</strong>";
  const before=structuredClone(items),html=renderSteadyWorkspace(items);
  assert.doesNotMatch(html,/<img/);
  assert.match(html,/&lt;img/);
  assert.match(html,/<strong>saved content<\/strong>/);
  assert.deepEqual(items,before);
});

test("simultaneous main and dialog renderings never reuse aria target identifiers",()=>{
  const main=renderSteadyWorkspace(pages()),dialog=renderSteadyWorkspace(pages());
  const identifiers=html=>[...html.matchAll(/\bid="([^"]+)"/g)].map(match=>match[1]);
  const all=[...identifiers(main),...identifiers(dialog)];
  assert.equal(all.length,20);assert.equal(new Set(all).size,all.length);
});

test("clicks and overview links activate exactly one panel without changing record identity",()=>{
  const node=host();mountSteadyWorkspace(node,"record-a");
  assert.equal(node.dataset.steadyRecordId,"record-a");assertSelected(node,"overview");
  node.click(new Element({},tab(node,"cooling")));assertSelected(node,"cooling");
  node.click(link(node,"checks"));assertSelected(node,"checks");
  assert.equal(node.dataset.steadyRecordId,"record-a");
});

test("arrow keys wrap and Home or End activate and focus the selected tab",()=>{
  const node=host();mountSteadyWorkspace(node,"r1");
  for(const [from,key,to] of [["overview","ArrowRight","checks"],["checks","ArrowLeft","overview"],
    ["overview","ArrowLeft","models"],["models","ArrowRight","overview"],["overview","End","models"],["models","Home","overview"]]){
    const count=tab(node,to).focusCount;
    assert.equal(node.key(tab(node,from),key),true);
    assertSelected(node,to);assert.equal(tab(node,to).focusCount,count+1);
  }
  assert.equal(node.key(tab(node,"overview"),"Tab"),false);
  assert.equal(node.key(link(node,"checks"),"ArrowRight"),false);
  assertSelected(node,"overview");
});

test("same-record remount restores selection, new records reset and listeners do not accumulate",()=>{
  const node=host();mountSteadyWorkspace(node,"r1");node.click(tab(node,"convergence"));
  const stale=tab(node,"models");node.workspace=workspace();mountSteadyWorkspace(node,"r1");
  assertSelected(node,"convergence");
  assert.equal(node.listeners.click.size,1);assert.equal(node.listeners.keydown.size,1);
  node.click(stale);assertSelected(node,"convergence");
  mountSteadyWorkspace(node,"r2");assertSelected(node,"overview");
  const selected=node.dataset.steadySelected;
  node.workspace=null;mountSteadyWorkspace(node,"r2");
  node.click(stale);node.key(stale,"End");assert.equal(node.dataset.steadySelected,selected);
  node.workspace=workspace();mountSteadyWorkspace(node,"r2");assertSelected(node,"overview");
  assert.equal(node.listeners.click.size,1);assert.equal(node.listeners.keydown.size,1);
  assert.doesNotThrow(()=>mountSteadyWorkspace(null,"missing"));
});

test("main result and history dialog retain independent selection and reject foreign events",()=>{
  const main=host(),dialog=host();mountSteadyWorkspace(main,"main-record");mountSteadyWorkspace(dialog,"history-record");
  main.click(tab(main,"cooling"));dialog.click(link(dialog,"models"));
  assertSelected(main,"cooling");assertSelected(dialog,"models");
  main.click(tab(dialog,"checks"));main.key(tab(dialog,"models"),"Home");
  assertSelected(main,"cooling");assertSelected(dialog,"models");
  mountSteadyWorkspace(main,"next-record");assertSelected(main,"overview");assertSelected(dialog,"models");
});

test("unrecognized saved tabs fall back to overview instead of hiding every page",()=>{
  const node=host();node.dataset={steadyRecordId:"r1",steadySelected:"removed-tab"};
  mountSteadyWorkspace(node,"r1");assertSelected(node,"overview");
  const invalid=new Element({steadyOpen:"missing"},node.workspace);node.workspace.elements.push(invalid);
  node.click(invalid);assertSelected(node,"overview");
});

function resultRecord(){
  const layer={enabled:true,status:"pass",residual:0,tolerance:1e-6,iterations:1,termination_reason:"converged"};
  return {id:"ui-contract-only",status:"succeeded",result:{
    calculation_scope:{id:"engine_system",version:3},warnings:[],
    assessment:{scope:"system",status:"fail",label:"系统约束未通过",numerical:{status:"pass",residual:0},
      checks:[{key:"wall_temperature",label:"沿程热面壁温峰值",status:"fail",value:1252.873,limit:800,unit:"K",criterion:"maximum"}],limitations:[]},
    calculation:{inputs:{cycle_type:"ffsc",cooling_bartz_mode:0,use_target_preburner_temp:0,pc:33e6,of_ratio:3.6,expansion_ratio:34.34,ambient_pressure:101325},
      summary:{thrust_vac_N:2596624,thrust_N:2450000,Isp_vac_s:344.863,Isp_s:325.39,mdot_total_kg_s:767.788,
        chamber_temperature_K:3773.6,ox_preburner_T_combustion_K:750,fuel_preburner_T_combustion_K:850,solver_residual_norm:0},
      components:{},states:{},warnings:[],
      convergence:{schema_version:1,layers:{inner:{...layer},continuation:{...layer,reached_ratio:1,termination_reason:"target_reached"},
        thermal:{enabled:false,status:"disabled"},preburner:{enabled:false,status:"disabled"}}},
      thermodynamic_applicability:{schema_version:2,states:[{id:"main_chamber",label:"主燃烧室",T_K:3773.6,p_Pa:33e6,
        thermo_range:{status:"extrapolated",valid_temperature_min_K:300,valid_temperature_max_K:3500,outside_mass_fraction:1,
          species_outside:[{species:"H2O",mass_fraction:.6,min_temperature_K:300,max_temperature_K:3500}]}}],
        pure_fluid_states:[],notes:[]},
    },
  }};
}

test("real overview rendering keeps wall-limit failure and extrapolation visible while four details pages are hidden",()=>{
  configureResultSchema({});const record=resultRecord(),before=structuredClone(record);
  const html=renderAnalysis("overview",record),panels=panelsOf(html);
  assert.deepEqual(panels.map(item=>item.id),IDS);
  assert.deepEqual(panels.filter(item=>!item.hidden).map(item=>item.id),["overview"]);
  const overview=panels.find(item=>item.id==="overview"),visible=visibleText(overview.content);
  assert.match(visible,/沿程热面壁温峰值/);
  assert.match(visible,/1,?252\.873/);assert.match(visible,/800/);
  assert.match(visible,/温区外推/);
  assert.match(html,/完整数值收敛/);
  assert.match(html,/系统约束未通过/);
  assert.doesNotMatch(overview.content,/class="panel (?:convergence-workspace|thermo-applicability|cooling-workspace|steady-closure-workspace)"/);
  assert(panels.find(item=>item.id==="convergence").content.includes("完整收敛与迭代历程"));
  assert(panels.find(item=>item.id==="models").content.includes("热力学适用性与模型假设"));
  assert(panels.find(item=>item.id==="cooling").content.includes("再生冷却沿程分析"));
  assert.deepEqual(record,before);
});

test("overview uses rechecked pressure-link failure instead of a contradictory saved pass flag",()=>{
  configureResultSchema({});const record=resultRecord();
  record.result.assessment.status="pass";record.result.assessment.label="已检查的系统约束通过";
  record.result.assessment.checks=[];
  const branch={status:"pass",source:"lox_after_pump",target:"ox_preburner",mass_flow_kg_s:1,
    temperature_K:80,source_pressure_Pa:3e6,destination_pressure_Pa:2e6,line_drop_Pa:0,
    injector_drop_Pa:1e6,valve_drop_Pa:0,pressure_residual_Pa:0,mass_residual_kg_s:0};
  const link={pressure_residual_Pa:0,pressure_tolerance_Pa:1,status:"pass"};
  // UI evidence fixture: every stored pass flag is retained, while one saved
  // residual contradicts it. It is not a calculated physical engine result.
  const contract={schema_version:1,pressure_link_status:"pass",
    gas_paths:[{...link,id:"ox",label:"富氧燃气",pressure_residual_Pa:2},{...link,id:"fuel",label:"富燃燃气"}],
    pump_capacity:{status:"not_verified",pumps:["ox","fuel"].map(id=>({...link,id,pressure_link_status:"pass"}))},
    branch_state_links:["a","b","c","d"].map(id=>({...link,id,source_state_residual_Pa:0,
      preburner_state_residual_Pa:0,branch_outlet_state_residual_Pa:0,max_pressure_residual_Pa:0}))};
  record.result.calculation.steady_closure={schema_version:1,
    feed_network:{status:"pass",branches:["a","b","c","d"].map(id=>({...branch,id})),
      max_pressure_residual_Pa:0,max_mass_residual_kg_s:0,pressure_contract:contract},
    energy_balance:{status:"pass",reference:"UI contract fixture",boundary:"UI contract fixture",
      terms:[{value_W:0,role:"in"}],checks:[{status:"pass",residual_W:0,tolerance_W:1}],residual_W:0,tolerance_W:1}};
  const before=structuredClone(record),html=renderAnalysis("overview",record),panels=panelsOf(html);
  const visible=visibleText(panels.find(item=>item.id==="overview").content);
  assert.match(visible,/压力关系.*(?:未通过|未闭合)/);
  assert.doesNotMatch(visible,/本记录已检查的系统约束通过/);
  assert.match(panels.find(item=>item.id==="checks").content,/压力关系未闭合/);
  assert.deepEqual(record,before);
});

test("wall-temperature failure remains visible when it follows three other failed checks",()=>{
  configureResultSchema({});const record=resultRecord();
  const wall=record.result.assessment.checks[0];
  record.result.assessment.checks=[
    {key:"fuel_feed_margin",label:"甲烷供给压力裕度",status:"fail",value:-2,limit:0,unit:"MPa",criterion:"minimum"},
    {key:"ox_power_error",label:"氧侧轴功率平衡",status:"fail",value:3,limit:1,unit:"%",criterion:"maximum"},
    {key:"fuel_power_error",label:"燃料侧轴功率平衡",status:"fail",value:4,limit:1,unit:"%",criterion:"maximum"},
    wall,
  ];
  const before=structuredClone(record),html=renderAnalysis("overview",record);
  const visible=visibleText(panelsOf(html).find(item=>item.id==="overview").content);
  for(const check of record.result.assessment.checks)assert(visible.includes(check.label),`${check.label} is hidden`);
  assert.match(visible,/1,?252\.873/);assert.match(visible,/800/);
  assert.doesNotMatch(visible,/另有 1 项未通过/);
  assert.deepEqual(record,before);
});

test("historical pass and missing evidence remain visible as unresolved on the default page",()=>{
  configureResultSchema({});const record=resultRecord();
  record.result.calculation_scope.version=1;
  record.result.assessment.status="pass";record.result.assessment.label="已检查的系统约束通过";record.result.assessment.checks=[];
  delete record.result.calculation.convergence;delete record.result.calculation.thermodynamic_applicability;
  const before=structuredClone(record),html=renderAnalysis("overview",record);
  const overview=panelsOf(html).find(item=>item.id==="overview");assert(overview);
  assert.match(visibleText(html),/历史|缺少|未记录|待确认/);
  assert.doesNotMatch(visibleText(html),/完整数值收敛|已检查的系统约束通过/);
  assert.match(visibleText(overview.content),/未记录|缺少|待确认|需重算|证据不足/);
  assert.deepEqual(record,before);
});
