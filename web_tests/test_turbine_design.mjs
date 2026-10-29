import assert from "node:assert/strict";
import {test} from "node:test";
import {renderTurbineDesign,mountTurbineDesign,buildTurbineDesignJson,buildTurbineDesignCsv,buildTurbineDesignMarkdown,turbineDesignExportFilename} from "../webapp/static/turbine-design.js";

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
  assert.match(html,/涡轮总体设计/);assert.match(html,/CFturbo 设计任务书/);assert.match(html,/Global setup 待补充/);assert.doesNotMatch(html,/Global setup 数据齐备/);
  const totalRow=html.match(/<tr data-td-field="total_pressure_in_pa">.*?<\/tr>/)[0];assert.match(totalRow,/>—<\/td>/);assert.match(totalRow,/td-status-missing/);assert.doesNotMatch(totalRow,/4,000,000/);
  const nodeRow=html.match(/<tr data-td-field="node_pressure_in_pa">.*?<\/tr>/)[0];assert.match(nodeRow,/4,000,000/);assert.match(nodeRow,/未声明总态/);
  assert.match(html,/保存的系统节点值与状态约定/);assert.match(html,/系统出口节点压力/);assert.match(html,/系统出口节点温度/);assert.match(html,/不把 p、T 自动标为总压或总温/);
  assert.match(html,/涡轮功率（传动损失前）/);assert.match(html,/等熵效率/);assert.match(html,/附加效率/);assert.match(html,/与等熵效率分开定义/);assert.doesNotMatch(html,/<input|<select|<canvas|<svg/);
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
  const m={source:{record_id:"old"},turbines:{shared:{available:false,label:"GG 共轴涡轮",status:"incomplete",global_fields:[field("rpm","转速",NaN,"rpm","saved"),field("pin","入口压力",null,"Pa","saved")],gas:{status:"missing"}}}};
  const html=renderTurbineDesign(m);assert.match(html,/data-td-side="shared" aria-pressed="true"/);assert.doesNotMatch(html,/data-td-machine="shared" hidden/);assert.match(html,/未保存入口组分/);assert.match(html,/没有保存可用的温区物性采样/);assert.doesNotMatch(html,/NaN|undefined|gri30|1700|<canvas/);
  for(const key of ["rpm","pin"])assert.match(html.match(new RegExp(`<tr data-td-field="${key}">.*?</tr>`))[0],/td-status-missing/);assert.match(renderTurbineDesign(null),/没有保存可用的涡轮部件/);
  const json=JSON.parse(buildTurbineDesignJson(m,"shared"));assert.equal(json.turbine.global_fields[1].value,null);assert.equal(json.turbine.available,false);
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

test("machine selection contains only actual branches and correct initially selected panel",()=>{
  const html=renderTurbineDesign(model());assert.match(html,/data-td-side="ox" aria-pressed="true"/);assert.match(html,/data-td-machine="fuel" hidden/);assert.doesNotMatch(html,/data-td-side="shared"/);
  const shared=renderTurbineDesign({turbines:{shared:machine()}});assert.match(shared,/data-td-side="shared" aria-pressed="true"/);assert.doesNotMatch(shared,/data-td-side="ox"|data-td-machine="shared" hidden/);
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
  assert.equal(buildTurbineDesignJson(m,"unknown"),"");assert.equal(buildTurbineDesignCsv(m,"unknown"),"");assert.equal(buildTurbineDesignMarkdown(null,"shared"),"");
});

test("HTML and Markdown escape saved text, reject active links, and CSV neutralizes formulas without changing numeric signs",()=>{
  const m=model(),t=m.turbines.ox;m.source.record_id='<script>alert(1)</script>';t.label='<img src=x onerror="bad()">';t.global_fields[0].label='<iframe>';t.global_fields[0].note='[click](javascript:bad()) | <script>';t.global_fields.push(field("formula","Formula","=HYPERLINK(\"bad\")"),field("negative","Signed velocity",-100,"m/s"));t.notes=['<svg onload=bad()>'];t.gas.composition[0].species='<img>';t.references.push({title:"bad",url:"javascript:bad()"});t.references.push({title:'quoted" link',url:"https://example.com/?q=%22"});
  const before=JSON.stringify(m),html=renderTurbineDesign(m),markdown=buildTurbineDesignMarkdown(m,"ox"),csv=buildTurbineDesignCsv(m,"ox");assert.doesNotMatch(html,/<script|<img|<iframe|<svg|href="javascript:/);assert.match(html,/&lt;script/);assert.match(html,/rel="noopener noreferrer"/);assert.doesNotMatch(markdown,/<script|<img|\[click\]\(javascript:/);assert.match(markdown,/\\\|/);assert.match(csv,/'=HYPERLINK/);assert.match(csv,/"-100","m\/s"/);assert.doesNotMatch(csv,/"'-100"/);assert.equal(JSON.stringify(m),before);
});

test("filenames isolate turbine and saved record and remove traversal characters",()=>{
  const m=model();assert.equal(turbineDesignExportFilename(m,"shared","md"),"turbine-design-shared-record-a.md");assert.equal(turbineDesignExportFilename(m,"fuel","csv"),"turbine-design-fuel-record-a.csv");m.source.record_id='../../<bad>"\\';assert.doesNotMatch(turbineDesignExportFilename(m,"ox","json"),/[<>"/\\]/);
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

test("remount replaces old event state, supports a single GG turbine and tolerates missing containers",()=>{
  const dom=fakeDom(["shared"]),m={source:{record_id:"gg-record"},turbines:{shared:machine()}},calls=[];mountTurbineDesign(dom.root,m,{onInspectComponent:()=>calls.push("old")});const cleanup=mountTurbineDesign(dom.root,m,{onInspectComponent:()=>calls.push("new")});assert.equal(dom.root.events.get("click").size,1);dom.root.fire("click",{target:dom.inspect[0]});assert.deepEqual(calls,["new"]);dom.root.fire("click",{target:dom.button("json",0)});assert.equal(dom.downloads[0].name,"turbine-design-shared-gg-record.json");cleanup();assert.equal(typeof mountTurbineDesign(null,m),"function");
});

test("component-page navigation selects the exact saved machine before inspecting or downloading",()=>{
  const dom=fakeDom(),m=model(),calls=[],cleanup=mountTurbineDesign(dom.root,m,{initialComponent:"fuel_preburner_turbine",onInspectComponent:(...args)=>calls.push(args)});
  assert.equal(dom.panels[0].hidden,true);assert.equal(dom.panels[1].hidden,false);assert.equal(dom.tabs[0].attrs["aria-pressed"],"false");assert.equal(dom.tabs[1].attrs["aria-pressed"],"true");
  dom.root.fire("click",{target:dom.inspect[1]});assert.deepEqual(calls,[["fuel_preburner_turbine","fuel"]]);dom.root.fire("click",{target:dom.button("json",1)});assert.equal(dom.downloads[0].name,"turbine-design-fuel-record-a.json");cleanup();
  const fallback=mountTurbineDesign(dom.root,m,{initialComponent:"removed_component"});assert.equal(dom.panels[0].hidden,false);assert.equal(dom.panels[1].hidden,true);fallback();
});
