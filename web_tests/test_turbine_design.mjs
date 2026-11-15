import assert from "node:assert/strict";
import {test} from "node:test";
import {renderTurbineDesign,mountTurbineDesign,buildTurbineDesignJson,buildTurbineDesignCsv,buildTurbineDesignMarkdown,turbineDesignExportFilename} from "../webapp/static/turbine-design.js";
import {renderPreliminaryForm,readPreliminaryScenario,renderVelocityTriangles,renderPreliminaryResult,buildPreliminaryCsv,mountPreliminaryWorkspace} from "../webapp/static/turbine-preliminary.js";

const field=(key,label,value,unit="—",status="saved",note="")=>({key,label,value,unit,status,source:`保存结果 · ${key}`,note});
function machine(side="ox") {
  return {available:true,label:side==="ox"?"富氧涡轮":"富燃涡轮",source:{record_id:"record-a",component_key:side+"_preburner_turbine",assumption_prefix:side,inlet_state_key:side+"_pb_out",outlet_state_key:side+"_turb_out"},status:"incomplete",ready_for_global_setup:false,
    global_fields:[field("mass_flow_kg_s","质量流量",side==="ox"?80:20,"kg/s"),field("shaft_speed_rpm","转速",20000,"rpm"),field("node_pressure_in_pa","系统入口节点压力",4e6,"Pa","saved","节点值；未声明总态 / 静态。"),field("total_pressure_in_pa","CFturbo 入口总压",null,"Pa","missing","需确认状态口径。"),field("state_basis","状态定义",null,"—","missing")],
    design_point:{mass_flow_kg_s:side==="ox"?80:20,pressure_in_pa:4e6,pressure_out_pa:1e6,temperature_in_k:1000,temperature_out_k:700,pressure_ratio:4,state_basis:null,state_assumption:null},
    power_matching:{fields:[field("turbine_power_w","涡轮功率（传动损失前）",3e6,"W"),field("actual_drop_j_kg","实际比功",150000,"J/kg"),field("eta_turbine","等熵效率",.75),field("eta_additional","附加效率",null,"—","missing","与等熵效率分开定义。")],checks:[{id:"power",label:"功率与比功",status:"pass",note:"按保存的流量与比功核对。"}]},
    gas:{status:"saved",composition_basis:"mass_fraction",composition:[{species:"O2",mass_fraction:.6},{species:"H2O",mass_fraction:.3},{species:"CO2",mass_fraction:.1},{species:"H2",mass_fraction:0}],fields:[field("molar_mass","摩尔质量",20,"kg/kmol"),field("cp_in","入口定压比热",1700,"J/(kg·K)")],model:{mechanism:"gri30_highT.yaml",mechanism_sha256:"b".repeat(64),phase:"gri30",gas_eos:"ideal-gas",source:"Cantera saved capture",cantera_version:"3.1.0"},property_table:{status:"saved",temperature_range_k:[700,1000],pressure_pa:4e6,composition_policy:"frozen_saved_inlet",columns:[{key:"temperature_k",label:"温度",unit:"K"},{key:"cp_j_kg_k",label:"定压比热",unit:"J/(kg·K)"},{key:"gamma",label:"比热比",unit:"—"},{key:"viscosity_pa_s",label:"动力黏度",unit:"Pa·s"}],rows:[{temperature_k:700,cp_j_kg_k:1610,gamma:1.3,viscosity_pa_s:0.000024123456789},{temperature_k:1000,cp_j_kg_k:1700,gamma:1.28,viscosity_pa_s:0.000031}],note:"保存的采样结果。"}},
    stage_handoff:{fields:[field("stages","级数",2,"级"),field("reaction","反动度目标",.5,"—","assumed")],missing_requirements:["确定逐级载荷分配与叶片数。"],note:"目标值属于设计约束。"},
    meanline:{status:"disabled",reason:"本记录未启用均径初选。",rows:[],notes:[]},geometry:{status:"available",note:"概念环形流道可在附录查看。",geometry_sha256:"c".repeat(64)},checks:[{id:"state",label:"热力学状态定义",status:"missing",note:"未声明总态或静态。"}],missing_requirements:["确认系统节点压力和温度的总态 / 静态定义。"],notes:["仅整理保存记录中的设计条件。"],references:[{title:"CFturbo Global setup",url:"https://manual.cfturbo.com/en/global_setup.html"}],design_sha256:"a".repeat(64)};
}
const model=()=>({schema_version:1,source:{record_id:"record-a",kind:"saved_record"},turbines:{ox:machine(),fuel:machine("fuel")}});

test("global setup preserves missing state definitions and node values without inventing total conditions",()=>{
  const html=renderTurbineDesign(model());
  assert.match(html,/涡轮设计条件/);assert.match(html,/循环需求与部件初选/);assert.match(html,/CFturbo 设计任务书/);assert.match(html,/Global setup 待补充/);assert.doesNotMatch(html,/Global setup 数据齐备/);
  const totalRow=html.match(/<tr data-td-field="total_pressure_in_pa">.*?<\/tr>/)[0];assert.match(totalRow,/>—<\/td>/);assert.match(totalRow,/td-status-missing/);assert.doesNotMatch(totalRow,/4,000,000/);
  const nodeRow=html.match(/<tr data-td-field="node_pressure_in_pa">.*?<\/tr>/)[0];assert.match(nodeRow,/4,000,000/);assert.match(nodeRow,/未声明总态/);
  assert.match(html,/保存的系统节点值与状态约定/);assert.match(html,/系统出口节点压力/);assert.match(html,/系统出口节点温度/);assert.match(html,/不把 p、T 自动标为总压或总温/);
  assert.match(html,/涡轮功率（传动损失前）/);assert.match(html,/等熵效率/);assert.match(html,/附加效率/);assert.match(html,/与等熵效率分开定义/);assert.doesNotMatch(html,/<canvas|<svg/);assert.match(html,/data-td-scenario-form/);
});

test("saved gas composition and temperature table retain actual fractions, zero values and provenance",()=>{
  const html=renderTurbineDesign(model());for(const content of ["质量分数 Y",">O2<",">0.6<",">H2O<",">0.3<",">H2<",">0<","700–1,000 K","2 个保存温度点","4,000,000 Pa","按已保存入口质量分数冻结组分","动力黏度","gri30_highT.yaml","Cantera saved capture"])assert.ok(html.includes(content),content);
  assert.match(html,/data-td-field="cp_in"/);assert.match(html,/保存结果 · cp_in/);assert.match(html,new RegExp("b".repeat(64)));
});

test("extreme values use compact scientific notation while sorted trace UI preserves every export value",()=>{
  const t=machine(),tiny=1.234567890123456e-120;
  t.gas.composition=[{species:"tiny",mass_fraction:tiny},{species:"O2",mass_fraction:.2},{species:"zero",mass_fraction:0},{species:"H2O",mass_fraction:.8},{species:"at_threshold",mass_fraction:1e-6},{species:"trace",mass_fraction:1e-7},{species:"missing",mass_fraction:null},{species:"negative",mass_fraction:-1e-20}];
  t.global_fields.push(field("huge","极大数",1e20,"Pa"),field("small_signed","有符号微小量",-9.87654321012345e-9,"—"));
  const m={source:{record_id:"trace-record"},turbines:{ox:t}},before=JSON.stringify(m),html=renderTurbineDesign(m);
  const trace=html.match(/<details class="td-details td-trace">.*?<\/details>/s)[0],major=html.slice(html.indexOf('<div class="td-composition">'),html.indexOf('<details class="td-details td-trace">'));
  assert.match(html,/1\.2345679e-120/);assert.match(html,/1e\+20/);assert.match(html,/-9\.8765432e-9/);assert.doesNotMatch(html,/0\.0{20}/);
  assert.ok(major.indexOf(">H2O<")<major.indexOf(">O2<"));assert.ok(major.indexOf(">O2<")<major.indexOf(">at_threshold<"));assert.doesNotMatch(major,/>tiny<|>trace<|>zero</);
  assert.match(major,/Y ≥ 1e-6/);assert.match(trace,/微量组分 · 3 项/);assert.match(trace,/0 ≤ Y &lt; 1e-6/);assert.doesNotMatch(trace,/td-trace" open/);assert.ok(trace.indexOf(">trace<")<trace.indexOf(">tiny<"));assert.ok(trace.indexOf(">tiny<")<trace.indexOf(">zero<"));
  assert.match(html,/组分缺项 \/ 异常值/);assert.match(html,/>missing</);assert.match(html,/>negative</);
  assert.deepEqual(JSON.parse(buildTurbineDesignJson(m,"ox")).turbine.gas.composition,t.gas.composition);
  for(const output of [buildTurbineDesignCsv(m,"ox"),buildTurbineDesignMarkdown(m,"ox")]){assert.ok(output.includes(String(tiny)));assert.ok(output.includes("zero"));assert.ok(output.includes("negative"));assert.ok(output.includes("missing"));}
  assert.equal(JSON.stringify(m),before);
});

test("old records and invalid scalar values remain visibly missing without placeholder gas or sampled properties",()=>{
  const m={source:{record_id:"old"},turbines:{fuel:{available:false,label:"富燃涡轮",status:"incomplete",global_fields:[field("rpm","转速",NaN,"rpm","saved"),field("pin","入口压力",null,"Pa","saved")],gas:{status:"missing"}}}};
  const html=renderTurbineDesign(m);assert.match(html,/data-td-side="fuel" aria-pressed="true"/);assert.doesNotMatch(html,/data-td-machine="fuel" hidden/);assert.match(html,/未保存入口组分/);assert.match(html,/没有保存可用的温区物性采样/);assert.doesNotMatch(html,/NaN|undefined|gri30|1700|<canvas/);
  for(const key of ["rpm","pin"])assert.match(html.match(new RegExp(`<tr data-td-field="${key}">.*?</tr>`))[0],/td-status-missing/);assert.match(renderTurbineDesign(null),/没有保存可用的涡轮部件/);
  const json=JSON.parse(buildTurbineDesignJson(m,"fuel"));assert.equal(json.turbine.global_fields[1].value,null);assert.equal(json.turbine.available,false);
});

test("saved extrapolated samples are distinctly marked and their range flags survive text exports",()=>{
  const m=model(),gas=m.turbines.ox.gas;gas.property_table.status="saved_with_extrapolation";gas.property_table.rows[0].within_mechanism_common_temperature_range=false;gas.property_table.rows[1].within_mechanism_common_temperature_range=true;gas.model.common_min_temperature_k=800;gas.model.common_max_temperature_k=2500;gas.endpoint_properties={inlet:{temperature_k:1000,enthalpy_j_kg:123456.789}};
  const html=renderTurbineDesign(m);assert.match(html,/部分超出机制温区/);assert.match(html,/带标记的采样超出机制共同声明温区/);assert.match(html,/td-range-outside/);assert.match(html,/>700 †<\/td>/);assert.match(html,/机制共同温区下限/);
  const csv=buildTurbineDesignCsv(m,"ox"),markdown=buildTurbineDesignMarkdown(m,"ox");assert.match(csv,/within_mechanism_common_temperature_range/);assert.match(csv,/"false"/);assert.match(csv,/"gas.endpoint.inlet"/);assert.match(csv,/123456.789/);assert.match(markdown,/部分超出机制温区/);assert.match(markdown,/位于机制共同声明温区/);
});

test("conflicts override a stale readiness flag and open the audit without masking assumptions",()=>{
  const m=model(),t=m.turbines.ox;t.ready_for_global_setup=true;t.status="conflict";t.checks.push({id:"shaft",label:"轴转速来源不一致",status:"conflict",note:"记录转速与同轴泵不一致。"});t.global_fields[1].status="conflict";
  const html=renderTurbineDesign(m);assert.match(html,/存在待解决冲突/);assert.match(html,/td-readiness is-conflict/);assert.match(html,/td-audit" open/);assert.match(html,/轴转速来源不一致/);assert.match(html,/采用假设/);assert.doesNotMatch(html,/Global setup 数据齐备/);
});

test("machine selection offers only FFSC branches and excludes legacy shared turbines",()=>{
  const html=renderTurbineDesign(model());
  assert.match(html,/data-td-side="ox" aria-pressed="true"/);
  assert.match(html,/data-td-machine="fuel" hidden/);
  assert.doesNotMatch(html,/data-td-side="shared"/);
  const legacy=renderTurbineDesign({turbines:{shared:machine()}});
  assert.doesNotMatch(legacy,/data-td-side=/);
  assert.match(legacy,/没有保存可用的涡轮部件/);
});

test("optional meanline results show saved Euler kinematics separately from the main design taskbook",()=>{
  const m=model();m.turbines.ox.meanline={status:"kinematic_only",total_work_j_kg:150000,allocated_work_j_kg:150000,rows:[{stage:1,work_fraction:1,work_j_kg:150000,loading_coefficient:1.5,u_m_s:300,ca_m_s:180,cu1_m_s:400,cu2_m_s:-100,wu1_m_s:100,wu2_m_s:-400,c1_m_s:438,c2_m_s:206,w1_m_s:206,w2_m_s:438,alpha1_deg:65.7,alpha2_deg:-29.1,beta1_deg:29.1,beta2_deg:-65.7,rotor_static_drop_j_kg:75000,reaction_target:.5,reaction_check:.5,euler_residual_j_kg:0}],checks:[{id:"euler",label:"Euler 功闭合",status:"pass",note:"仅代数关系。"}],notes:["尚无逐级静态与压比解。"]};
  const html=renderTurbineDesign(m);assert.match(html,/均径速度三角形与逐级比功初选/);assert.match(html,/运动学初选/);assert.match(html,/td-meanline"><summary>/);assert.match(html,/Euler 残差/);assert.match(html,/动叶静焓降代数量/);assert.match(html,/不提供静态、压比、叶型或性能预测/);assert.match(html,/>-400<\/td>/);assert.doesNotMatch(html,/td-meanline" open/);
  const json=JSON.parse(buildTurbineDesignJson(m,"ox"));assert.deepEqual(json.turbine.meanline,m.turbines.ox.meanline);assert.match(buildTurbineDesignCsv(m,"ox"),/"meanline.rows"/);assert.match(buildTurbineDesignMarkdown(m,"ox"),/Euler 残差/);
});

test("JSON and text exports retain selected record data, raw precision, missing fields and all handoff sections",()=>{
  const m=model(),t=m.turbines.fuel,json=JSON.parse(buildTurbineDesignJson(m,"fuel"));assert.equal(json.turbine_key,"fuel");assert.deepEqual(json.source,m.source);assert.deepEqual(json.turbine,t);assert.equal(json.turbine.global_fields[3].value,null);
  const csv=buildTurbineDesignCsv(m,"fuel"),markdown=buildTurbineDesignMarkdown(m,"fuel");assert.match(csv,/section,key,label,value,unit,status,source,note,turbine_key,record_id/);assert.match(csv,/0.000024123456789/);assert.match(csv,/"total_pressure_in_pa","CFturbo 入口总压","","Pa","missing"/);assert.match(csv,/"fuel","record-a"/);
  for(const content of ["global_setup","power_matching","gas.composition","gas.model","gas.property_table","stage_handoff","missing_requirements","reference","design_sha256"])assert.ok(csv.includes(content),content);
  for(const content of ["# 富燃涡轮","Global setup","入口组分质量分数","温区物性采样","功率匹配与效率口径","一维设计约束","CFturbo 入口总压","0.000024123456789","record-a"])assert.ok(markdown.includes(content),content);
  assert.equal(buildTurbineDesignJson(m,"unknown"),"");assert.equal(buildTurbineDesignCsv(m,"unknown"),"");assert.equal(buildTurbineDesignMarkdown(null,"fuel"),"");
});

test("HTML and Markdown escape saved text, reject active links, and CSV neutralizes formulas without changing numeric signs",()=>{
  const m=model(),t=m.turbines.ox;m.source.record_id='<script>alert(1)</script>';t.label='<img src=x onerror="bad()">';t.global_fields[0].label='<iframe>';t.global_fields[0].note='[click](javascript:bad()) | <script>';t.global_fields.push(field("formula","Formula","=HYPERLINK(\"bad\")"),field("negative","Signed velocity",-100,"m/s"));t.notes=['<svg onload=bad()>'];t.gas.composition[0].species='<img>';t.references.push({title:"bad",url:"javascript:bad()"});t.references.push({title:'quoted" link',url:"https://example.com/?q=%22"});
  const before=JSON.stringify(m),html=renderTurbineDesign(m),markdown=buildTurbineDesignMarkdown(m,"ox"),csv=buildTurbineDesignCsv(m,"ox");assert.doesNotMatch(html,/<script|<img|<iframe|<svg|href="javascript:/);assert.match(html,/&lt;script/);assert.match(html,/rel="noopener noreferrer"/);assert.doesNotMatch(markdown,/<script|<img|\[click\]\(javascript:/);assert.match(markdown,/\\\|/);assert.match(csv,/'=HYPERLINK/);assert.match(csv,/"-100","m\/s"/);assert.doesNotMatch(csv,/"'-100"/);assert.equal(JSON.stringify(m),before);
});

test("filenames isolate turbine and saved record and remove traversal characters",()=>{
  const m=model();assert.equal(turbineDesignExportFilename(m,"fuel","md"),"turbine-design-fuel-record-a.md");assert.equal(turbineDesignExportFilename(m,"fuel","csv"),"turbine-design-fuel-record-a.csv");m.source.record_id='../../<bad>"\\';assert.doesNotMatch(turbineDesignExportFilename(m,"ox","json"),/[<>"/\\]/);
});

function fakeDom(sides=["ox","fuel"]) {
  class Element {constructor(dataset={},owner=null){this.dataset=dataset;this.owner=owner;this.events=new Map();this.attrs={};this.hidden=false;}addEventListener(type,fn){if(!this.events.has(type))this.events.set(type,new Set());this.events.get(type).add(fn);}removeEventListener(type,fn){this.events.get(type)?.delete(fn);}setAttribute(k,v){this.attrs[k]=v;}hasAttribute(k){return Object.hasOwn(this.attrs,k);}closest(selector){return selector==="[data-td-machine]"?this.owner:this;}contains(){return true;}matches(){return true;}fire(type,event){for(const fn of this.events.get(type)||[])fn(event);}querySelectorAll(selector){return this.groups?.[selector]||[];}}
  const root=new Element(),panels=sides.map((side,i)=>Object.assign(new Element({tdMachine:side}),{hidden:i!==0})),tabs=sides.map(side=>new Element({tdSide:side})),inspect=panels.map(panel=>{const button=new Element({},panel);button.attrs["data-td-inspect"]="";return button;}),downloads=[],urls=new Map(),revoked=[],timers=[];
  const win={Blob,URL:{createObjectURL(blob){const url=`blob:turbine-design-${urls.size}`;urls.set(url,blob);return url;},revokeObjectURL(url){revoked.push(url);}},setTimeout(fn,delay){timers.push({fn,delay});}};
  const doc={defaultView:win,body:{append(anchor){anchor.attached=true;}},createElement(tag){assert.equal(tag,"a");return {click(){assert.equal(this.attached,true);downloads.push({url:this.href,name:this.download,blob:urls.get(this.href),anchor:this});},remove(){this.removed=true;this.attached=false;}};}};
  root.ownerDocument=doc;root.groups={"[data-td-side]":tabs,"[data-td-machine]":panels,"[data-td-inspect]":inspect};
  return {root,tabs,panels,inspect,downloads,revoked,timers,button(ext,sideIndex){return new Element({tdExport:ext},panels[sideIndex]);}};
}

test("switching changes selected machine and the component callback, while hidden buttons cannot export another machine",()=>{
  const dom=fakeDom(),calls=[],cleanup=mountTurbineDesign(dom.root,model(),{onInspectComponent:(...args)=>calls.push(args)});dom.root.fire("click",{target:dom.tabs[1]});assert.equal(dom.panels[0].hidden,true);assert.equal(dom.panels[1].hidden,false);assert.equal(dom.tabs[1].attrs["aria-pressed"],"true");dom.root.fire("click",{target:dom.inspect[1]});assert.deepEqual(calls,[["fuel_preburner_turbine","fuel"]]);
  dom.root.fire("click",{target:dom.button("json",0)});assert.equal(dom.downloads.length,0);dom.root.fire("click",{target:dom.inspect[0]});assert.equal(calls.length,1);
  cleanup();assert.equal(dom.root.events.get("click").size,0);assert.equal(dom.root._turbineDesignCleanup,undefined);cleanup();
});

test("JSON CSV Markdown buttons download the selected machine with correct MIME, payload and delayed URL cleanup",async()=>{
  const dom=fakeDom(),m=model(),cleanup=mountTurbineDesign(dom.root,m);assert.ok(dom.inspect.every(button=>button.hidden));dom.root.fire("click",{target:dom.tabs[1]});
  for(const [ext,build,type] of [["json",buildTurbineDesignJson,"application/json"],["csv",buildTurbineDesignCsv,"text/csv;charset=utf-8"],["md",buildTurbineDesignMarkdown,"text/markdown;charset=utf-8"]]) {
    dom.root.fire("click",{target:dom.button(ext,1)});const download=dom.downloads.at(-1);assert.equal(download.name,turbineDesignExportFilename(m,"fuel",ext));assert.equal(download.blob.type,type);assert.equal(await download.blob.text(),build(m,"fuel"));assert.notEqual(await download.blob.text(),build(m,"ox"));assert.ok(download.anchor.removed&&!download.anchor.attached);
  }
  assert.equal(dom.downloads.length,3);assert.deepEqual(dom.revoked,[]);cleanup();assert.ok(dom.timers.every(timer=>timer.delay===1000));dom.timers.forEach(timer=>timer.fn());assert.deepEqual(dom.revoked,dom.downloads.map(download=>download.url));
});

test("remount replaces old event state, supports a single FFSC turbine and tolerates missing containers",()=>{
  const dom=fakeDom(["fuel"]),m={source:{record_id:"ffsc-record"},turbines:{fuel:machine()}},calls=[];mountTurbineDesign(dom.root,m,{onInspectComponent:()=>calls.push("old")});const cleanup=mountTurbineDesign(dom.root,m,{onInspectComponent:()=>calls.push("new")});assert.equal(dom.root.events.get("click").size,1);dom.root.fire("click",{target:dom.inspect[0]});assert.deepEqual(calls,["new"]);dom.root.fire("click",{target:dom.button("json",0)});assert.equal(dom.downloads[0].name,"turbine-design-fuel-ffsc-record.json");cleanup();assert.equal(typeof mountTurbineDesign(null,m),"function");
});

test("component-page navigation selects the exact saved machine before inspecting or downloading",()=>{
  const dom=fakeDom(),m=model(),calls=[],cleanup=mountTurbineDesign(dom.root,m,{initialComponent:"fuel_preburner_turbine",onInspectComponent:(...args)=>calls.push(args)});
  assert.equal(dom.panels[0].hidden,true);assert.equal(dom.panels[1].hidden,false);assert.equal(dom.tabs[0].attrs["aria-pressed"],"false");assert.equal(dom.tabs[1].attrs["aria-pressed"],"true");
  dom.root.fire("click",{target:dom.inspect[1]});assert.deepEqual(calls,[["fuel_preburner_turbine","fuel"]]);dom.root.fire("click",{target:dom.button("json",1)});assert.equal(dom.downloads[0].name,"turbine-design-fuel-record-a.json");cleanup();
  const fallback=mountTurbineDesign(dom.root,m,{initialComponent:"removed_component"});assert.equal(dom.panels[0].hidden,false);assert.equal(dom.panels[1].hidden,true);fallback();
});

const scenarioValues=()=>({stages:"2",mean_diameter_m:"150",flow_coefficient:"0.6",reaction:"0",inlet_swirl_m_s:"0",work_split:"equal",work_fractions:"",stator_axial_chord_m:"15",rotor_axial_chord_m:"",stator_zweifel:"0.9",rotor_zweifel:""});
function preliminaryResult(record="record-a") {
  return {status:"preliminary",source:{record_id:record,turbine_key:"ox"},scenario:{mean_diameter_m:.15},summary:{stages:2,total_work_j_kg:150000,total_power_w:3e6,total_torque_nm:1432,u_m_s:300},stages:[{stage:1,work_fraction:.5,work_j_kg:75000,power_w:1.5e6,torque_nm:716,loading_coefficient:.8333333333333333,u_m_s:300,ca_m_s:180,cu0_m_s:0,cu1_m_s:275,cu2_m_s:25,wu1_m_s:-25,wu2_m_s:-275,alpha0_deg:0,alpha1_deg:56.8,alpha2_deg:7.9,beta1_deg:-7.9,beta2_deg:-56.8,reaction_target:.5,reaction_stage:null,stator:{status:"candidate",blade_count_candidate:31,pitch_m:.015201254775434483,axial_solidity:.98676,zweifel_candidate:.903,note:"整数候选，需校核"},rotor:{status:"missing",blade_count_candidate:null,note:"缺少轴向弦长"}}],checks:[{id:"euler",label:"Euler 闭合",status:"pass",note:"仅代数"}],limitations:["没有静态密度与叶型"],calculation_sha256:"d".repeat(64)};
}

test("scenario form uses explicit saved seeds and does not invent absent dimensions",()=>{
  const workspace={seeds:{mean_diameter_m:{value:.150075011,source:"保存中径",status:"saved",note:"初选值"},inlet_swirl_m_s:{value:0,status:"assumed",source:"零旋流起始方案",note:"起始假设"}}},before=JSON.stringify(workspace);
  const html=renderPreliminaryForm(machine(),workspace,"ox");assert.match(html,/value="150.075011"/);assert.match(html,/name="inlet_swirl_m_s"[^>]*value="0"/);assert.match(html,/name="rotor_axial_chord_m"[^>]*value=""/);assert.match(html,/起始假设/);assert.match(html,/不回写历史记录/);assert.equal(JSON.stringify(workspace),before);
  const m=model();m.workspace={turbines:{ox:workspace},parameter_map:[{parameter:"流量",cfturbo:"Mass flow",source:"保存流量",note:"燃气流量"}]};const rendered=renderTurbineDesign(m);assert.match(rendered,/role="tablist"/);assert.match(rendered,/data-td-panel="gas" hidden/);assert.match(rendered,/CFturbo 参数对应/);assert.match(rendered,/Mass flow/);assert.match(rendered,/td-field-source/);
});

test("scenario parser converts millimetres once and preserves zero reaction and swirl",()=>{
  const input=scenarioValues(),s=readPreliminaryScenario(input);assert.equal(s.mean_diameter_m,.15);assert.equal(s.stator_axial_chord_m,.015);assert.equal(s.rotor_axial_chord_m,null);assert.equal(s.inlet_swirl_m_s,0);assert.equal(s.reaction,0);assert.deepEqual(s.work_fractions,[]);assert.equal(input.mean_diameter_m,"150");
  assert.throws(()=>readPreliminaryScenario({...input,inlet_swirl_m_s:""}),/请填写/);assert.throws(()=>readPreliminaryScenario({...input,mean_diameter_m:"Infinity"}),/有限/);assert.throws(()=>readPreliminaryScenario({...input,work_split:"custom",work_fractions:"[.5,.5]"}),/JSON/);assert.deepEqual(readPreliminaryScenario({...input,work_split:"custom",work_fractions:"[0.4,0.6]"}).work_fractions,[.4,.6]);
});

test("velocity triangles use finite vectors and explicit SVG presentation attributes",()=>{
  const r=preliminaryResult(),svg=renderVelocityTriangles(r.stages[0]);assert.match(svg,/c = U \+ w/);assert.match(svg,/动叶入口/);assert.match(svg,/动叶出口/);assert.match(svg,/stroke-width=/);assert.doesNotMatch(svg,/<style|style=|NaN|Infinity|<script/);assert.match(renderVelocityTriangles({...r.stages[0],ca_m_s:null}),/没有可绘制/);
  const html=renderPreliminaryResult(r);assert.match(html,/整数候选/);assert.match(html,/缺少轴向弦长/);assert.match(html,/级反动度/);assert.match(html,/>—<\/td>/);assert.doesNotMatch(html,/几何已验证|性能通过/);const bad=renderPreliminaryResult({status:"unavailable",errors:['<img onerror="x">']});assert.doesNotMatch(bad,/<img/);assert.match(bad,/&lt;img/);
});

test("scenario CSV retains missing blades, numerical precision and the source separately from saved exports",()=>{
  const result=preliminaryResult(),before=JSON.stringify(result);result.scenario.label="=WEBSERVICE(1)";const csv=buildPreliminaryCsv(result);assert.match(csv,/0.015201254775434483/);assert.match(csv,/"rotor","1","blade_count_candidate","整数叶片数候选","","片","missing"/);assert.match(csv,/'=WEBSERVICE/);assert.match(csv,/"record-a"/);assert.match(csv,/没有静态密度与叶型/);delete result.scenario.label;assert.equal(JSON.stringify(result),before);assert.equal(buildPreliminaryCsv({status:"unavailable"}),"");
});

test("scenario exchange includes the method, source references and static enthalpy accounting",()=>{
  const r=preliminaryResult();r.method="euler_zweifel_v1";r.scenario.inlet_swirl_m_s=0;r.stages[0].rotor_static_drop_j_kg=37500;r.stages[0].stator_static_drop_j_kg=25000;r.stages[0].stage_static_drop_j_kg=62500;r.references=[{title:"NASA 叶栅方法",url:"https://ntrs.nasa.gov/citations/19740024806"}];
  const csv=buildPreliminaryCsv(r),html=renderPreliminaryResult(r);for(const key of ["rotor_static_drop_j_kg","stator_static_drop_j_kg","stage_static_drop_j_kg","euler_zweifel_v1","https://ntrs.nasa.gov/citations/19740024806"])assert.ok(csv.includes(key),key);assert.match(csv,/"mean_diameter_m","mean_diameter_m","0.15","m"/);assert.match(csv,/"inlet_swirl_m_s","inlet_swirl_m_s","0","m\/s"/);assert.match(html,/href="https:\/\/ntrs.nasa.gov/);assert.match(html,/气动力矩/);assert.doesNotMatch(html,/轴扭矩/);
});

test("all rotor-stator harmonic pairs and scenario identity survive export with Chinese result states",()=>{
  const r=preliminaryResult(),pairs=[];r.scenario_sha256="e".repeat(64);
  for(let s=1;s<=3;s++)for(let t=1;t<=3;t++){const m=Math.abs(s*22-t*23);pairs.push({stator_order:s,rotor_order:t,stator_periodicity:s*22,rotor_periodicity:t*23,m,status:m<2?"warning":m===2?"review":"unverified"});}
  r.stages[0].interaction={method:"harmonic_1_3",status:"warning",stator_blade_count:22,rotor_blade_count:23,pairs,note:"m=1；调整叶数、弦长或系数后进行模态校核。"};r.checks=[...r.checks,...["assumed","missing","review","unverified"].map(status=>({label:"状态检查",status}))];
  const html=renderPreliminaryResult(r),csv=buildPreliminaryCsv(r);assert.match(html,/9 项/);assert.match(html,/m=1/);assert.match(html,/初选假设/);assert.match(html,/待补充/);assert.match(html,/尚未验证/);assert.doesNotMatch(html,/>assumed<|>missing<|>review<|>unverified</);assert.equal((csv.match(/"interaction.pair"/g)||[]).length,54);assert.match(csv,/"9.m","组合 9 · m","3"/);assert.match(csv,new RegExp("e".repeat(64)));assert.match(csv,/harmonic_1_3/);
});

function preliminaryDom() {
  class E {constructor(){this.listeners={};this.innerHTML="";this.textContent="";this.disabled=false;this.hidden=false;}addEventListener(k,f){(this.listeners[k]??=[]).push(f);}removeEventListener(k,f){this.listeners[k]=(this.listeners[k]||[]).filter(x=>x!==f);}async fire(k,event={}){return Promise.all((this.listeners[k]||[]).map(f=>f(event)));}}
  const root=new E(),form=new E(),output=new E(),state=new E(),button=new E(),fractions=new E(),inputs=Object.entries(scenarioValues()).map(([name,value])=>({name,value})),downloads=[];
  const map={"[data-td-scenario-form]":form,"[data-td-preliminary-result]":output,"[data-td-calculation-state]":state,"[data-td-calculate]":button,"[data-td-work-fractions]":fractions};root.querySelector=s=>map[s];root.contains=()=>true;form.querySelector=s=>map[s];form.querySelectorAll=()=>inputs;
  let blob;root.ownerDocument={defaultView:{Blob,URL:{createObjectURL(b){blob=b;return "blob:test";},revokeObjectURL(){}},setTimeout(){}},body:{append(){}},createElement(){return {click(){downloads.push({name:this.download,blob});},remove(){}};}};
  return {root,form,output,state,button,inputs,downloads,submit:()=>form.fire("submit",{preventDefault(){}}),edit:async(name,value)=>{inputs.find(x=>x.name===name).value=value;await form.fire("input");},export:ext=>root.fire("click",{target:{closest:()=>({dataset:{tdScenarioExport:ext}})}})};
}

test("editing while a request is pending invalidates the late result and blocks stale exports",async()=>{
  const dom=preliminaryDom();let resolve,request;const stop=mountPreliminaryWorkspace(dom.root,{turbineKey:"ox",recordId:"record-a",onCalculate:payload=>{request=payload;return new Promise(r=>resolve=r);}});
  const pending=dom.submit();assert.equal(dom.button.disabled,true);assert.equal(request.turbineKey,"ox");assert.equal(request.scenario.mean_diameter_m,.15);await dom.edit("stages","3");resolve(preliminaryResult());await pending;assert.match(dom.output.innerHTML,/旧方案已失效/);assert.doesNotMatch(dom.output.innerHTML,/td-triangle/);await dom.export("json");assert.equal(dom.downloads.length,0);stop();
});

test("a valid scenario downloads independently and changing any input invalidates it",async()=>{
  const dom=preliminaryDom(),stop=mountPreliminaryWorkspace(dom.root,{turbineKey:"ox",recordId:"record-a",onCalculate:async()=>preliminaryResult()});await dom.submit();assert.match(dom.output.innerHTML,/td-triangle/);await dom.export("json");assert.equal(dom.downloads[0].name,"turbine-preliminary-ox-record-a.json");assert.equal(JSON.parse(await dom.downloads[0].blob.text()).source.record_id,"record-a");await dom.export("csv");assert.match(await dom.downloads[1].blob.text(),/0.015201254775434483/);await dom.edit("reaction","0.3");await dom.export("csv");assert.equal(dom.downloads.length,2);stop();
});

test("wrong-record, wrong-turbine and disposed responses cannot populate the workspace",async()=>{
  for(const result of [preliminaryResult("other"),{...preliminaryResult(),source:{record_id:"record-a",turbine_key:"fuel"}}]){const dom=preliminaryDom(),stop=mountPreliminaryWorkspace(dom.root,{turbineKey:"ox",recordId:"record-a",onCalculate:async()=>result});await dom.submit();assert.match(dom.output.innerHTML,/不属于当前/);assert.doesNotMatch(dom.output.innerHTML,/td-triangle/);stop();}
  const dom=preliminaryDom();let resolve;const stop=mountPreliminaryWorkspace(dom.root,{turbineKey:"ox",recordId:"record-a",onCalculate:()=>new Promise(r=>resolve=r)}),pending=dom.submit();stop();resolve(preliminaryResult());await pending;assert.equal(dom.output.innerHTML,"");assert.equal(dom.form.listeners.submit.length,0);
});
