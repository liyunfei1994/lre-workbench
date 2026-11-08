import test from "node:test";
import assert from "node:assert/strict";
import {renderNozzleDesign,renderNozzleAmbient,nozzleChart,nozzleDesignCsv,mountNozzleDesign} from "../webapp/static/nozzle-design.js";

const field=(key,value,unit="—")=>({key,label:key,value,unit,status:value==null?"missing":"saved",basis:"保存计算记录",note:"定义说明"});
const model=()=>({available:true,schema_version:1,status:"review",source:{record_id:"record-1",label:"测试保存记录",scope:"总体模型；待后续验证"},
  metrics:[field("pc_Pa",10e6,"Pa"),field("thrust_N",123456.789,"N"),field("Isp_s",310,"s"),field("Cf",1.7),field("c_star_ms",1700,"m/s"),field("expansion_ratio",24),field("p_exit_Pa",null,"Pa")],
  geometry:{available:true,derived:[field("At_m2",.003,"m²")],method:"保存参数化型面",source:"saved",contour_sha256:"abcdef"},
  checks:[{key:"thrust",label:"推力一致性",status:"pass",actual:123456.789,expected:123456.789,relative_error:0,note:"同一来源"}],
  flow_profile:{available:true,model_id:"saved-flow",nodes:[{x_m:0,M:0.1,p_Pa:10e6,T_K:3400,velocity_ms:100},{x_m:1,M:3,p_Pa:.2e6,T_K:1300,velocity_ms:2400}],throat_x_m:.3,sonic_x_m:.3},
  ambient:{available:true,scope:"固定几何和出口状态",design_p_amb_Pa:100000,match_p_amb_Pa:20000,screening_note:"经验阈值只作复核提示",points:[
    {p_amb_Pa:0,thrust_N:130000,Isp_s:330,Cf:1.8,pressure_thrust_N:0,pe_pa_ratio:null,regime:"underexpanded",screening:"vacuum"},
    {p_amb_Pa:50000,thrust_N:125000,Isp_s:315,Cf:1.7,pressure_thrust_N:-5000,pe_pa_ratio:.4,regime:"overexpanded",screening:"not_flagged"},
    {p_amb_Pa:100000,thrust_N:120000,Isp_s:300,Cf:1.6,pressure_thrust_N:-10000,pe_pa_ratio:.2,regime:"overexpanded",screening:"flag"}]},
  thermal:{metrics:[field("q_max",3e6,"W/m²")],scope:"热预算",nodes:[{x_m:0,q_W_m2:3e6,T_wall_hot_K:600,T_wall_cold_K:500}]},
  losses:[{key:"viscous",label:"黏性损失",status:"not_modelled",note:"未计算"}],
  references:[{title:"NASA report",url:"https://ntrs.nasa.gov/citations/123",year:2020,use:"适用性复核"}],
  handoff:[{topic:"CFD",available:"几何",required:"网格与边界条件"}]});

test("nozzle page preserves saved values, distinguishes c-star from Cf and exposes missing fields",()=>{
  const value=model(),before=JSON.stringify(value),html=renderNozzleDesign(value);
  assert.match(html,/特征速度描述燃烧室热化学与燃烧性能/);
  assert.match(html,/压力推力分别列出/);assert.match(html,/123\.4568/);
  assert.match(html,/data-status="missing"/);assert.match(html,/待补充/);
  assert.match(html,/未建模项不等于损失为零/);assert.match(html,/内型面页面与本页引用同一计算记录/);
  assert.equal((html.match(/role="tab"/g)||[]).length,5);
  assert.equal((html.match(/role="tabpanel"/g)||[]).length,5);
  assert.equal(JSON.stringify(value),before);
});
test("missing records and sections do not manufacture plotted distributions",()=>{
  assert.match(renderNozzleDesign(null),/请选择已完成/);
  const value=model();value.flow_profile={available:false,reason:"缺少流动分布"};value.thermal={metrics:[],nodes:[]};
  const html=renderNozzleDesign(value);assert.match(html,/缺少流动分布/);assert.match(html,/热流和壁温保持缺项/);
  assert.doesNotMatch(html,/NaN|Infinity|undefined/);
});
test("backend geometry objects and exact design point remain legible outside the sampled pressure range",()=>{
  const value=model();value.metrics.push(field("p_amb_Pa",101325,"Pa"),field("momentum_thrust_N",130000,"N"),field("pressure_thrust_N",-6543.211,"N"),field("pe_pa_ratio",.3));
  value.metrics.find(item=>item.key==="p_exit_Pa").value=30397.5;
  value.geometry.method={label:"指定角度钟形",angle_source:"方案指定角度",notes:["不是优化结果"]};value.geometry.source={label:"保存的完整内轮廓"};value.geometry.dimensions={throat_diameter_m:.123456789};
  value.ambient.maximum_allowed_pressure_Pa=500000;value.ambient.range_note="当前设计背压在所示范围之外";
  value.ambient.points=[];value.ambient.design_point={p_amb_Pa:101325,screening:"flag"};
  const html=renderNozzleDesign(value);assert.match(html,/指定角度钟形/);assert.match(html,/不是优化结果/);assert.match(html,/保存的完整内轮廓/);assert.match(html,/123\.4568/);
  assert.match(html,/过膨胀/);assert.match(html,/触发分离经验筛查，需进一步分析/);assert.match(html,/-6\.543211/);
  assert.match(html,/当前设计背压在所示范围之外/);assert.match(html,/这是分析工具的输入限制/);assert.match(html,/max="500"/);
  assert.doesNotMatch(html,/\[object Object\]/);assert.match(nozzleDesignCsv(value),/"0\.123456789","m"/);
});
test("saved strings, reference protocols and unknown status cannot inject markup",()=>{
  const value=model();value.source.label='<img src=x onerror="bad()">';value.metrics[0].label='<script>bad()</script>';
  value.references=[{title:'<svg onload="bad()">',url:"javascript:bad()",use:'<iframe src="bad">'}];value.status='bad" onclick="bad()';
  const html=renderNozzleDesign(value);assert.doesNotMatch(html,/<script|<img|<iframe|javascript:|onclick=/);assert.match(html,/&lt;img/);
});
test("ambient curves mark flagged extrapolation, retain negative pressure thrust and have no safe claim",()=>{
  const ambient=model().ambient,html=renderNozzleAmbient(ambient);
  assert.equal((html.match(/class="nd-chart-line nd-chart-extrapolated"/g)||[]).length,1);
  assert.match(html,/未触发筛查也不代表已经验证无分离/);assert.match(html,/理论外推/);
  assert.match(html,/-10,000/);assert.doesNotMatch(html,/已验证安全|data-status="pass"/);
  assert.match(renderNozzleAmbient(ambient,"Isp_s"),/比冲 \/ s/);
  assert.match(renderNozzleAmbient(ambient,"Cf"),/推力系数 Cꜰ/);
});
test("chart never bridges missing samples or inserts zero for absent data",()=>{
  const svg=nozzleChart([{x:0,y:1},{x:1,y:null},{x:2,y:3},{x:3,y:4}],{xKey:"x",yKey:"y",xLabel:"x",yLabel:"y"});
  assert.equal((svg.match(/class="nd-chart-line"/g)||[]).length,1);
  assert.equal((svg.match(/<circle/g)||[]).length,3);
  assert.doesNotMatch(svg,/NaN|Infinity/);
  assert.match(nozzleChart([],{xKey:"x",yKey:"y"}),/没有可绘制/);
});
test("physical profile axes have compact nonnegative ticks while signed quantities keep their negative range",()=>{
  const axisTicks=svg=>[...svg.matchAll(/<text x="67"[^>]*>([^<]+)<\/text>/g)].map(match=>match[1]);
  const svg=nozzleChart([{x:0,M:0.001},{x:1,M:4.23356789}],{xKey:"x",yKey:"M",xLabel:"x",yLabel:"M"});
  assert.equal(axisTicks(svg)[0],"0");assert.ok(axisTicks(svg).every(tick=>Number(tick)>=0));
  assert.ok(axisTicks(svg).every(tick=>tick.replace(/[^0-9]/g,"").length<=4));
  const negative=nozzleChart([{x:0,pressure_thrust_N:-35359.994241},{x:1,pressure_thrust_N:1000}],{xKey:"x",yKey:"pressure_thrust_N",xLabel:"x",yLabel:"压力推力"});
  assert.ok(Number(axisTicks(negative)[0])<-35359.994241);
  const tiny=nozzleChart([{x:0,y:1e-8},{x:1,y:2e-8}],{xKey:"x",yKey:"y",xLabel:"x",yLabel:"y"});
  assert.ok(axisTicks(tiny).every(tick=>/e-/.test(tick)));
  const value=model();value.checks[0].relative_error=2.3456789e-16;
  assert.match(renderNozzleDesign(value),/相对差 2\.35e-14%/);
  assert.match(nozzleDesignCsv(value),/123456\.789/);
});
test("CSV keeps raw SI precision, record source and escaped spreadsheet text",()=>{
  const value=model();value.metrics.push({...field("formula",0),label:'=HYPERLINK("bad")',note:"line one\nline two"});
  const before=JSON.stringify(value),csv=nozzleDesignCsv(value);
  assert.match(csv,/"123456\.789","N"/);assert.match(csv,/"0"/);assert.match(csv,/"record-1"/);
  assert.match(csv,/"'=HYPERLINK\(""bad""\)"/);assert.match(csv,/"line one\nline two"/);
  const ambient=nozzleDesignCsv(value,"ambient");assert.match(ambient,/"-10000"/);assert.doesNotMatch(ambient,/"'-10000"/);
  assert.match(ambient,/"screening"/);assert.match(ambient,/"固定几何和出口状态"/);assert.equal(JSON.stringify(value),before);
});

function node(dataset={}){return {dataset,hidden:false,innerHTML:"",textContent:"",disabled:false,attributes:{},setAttribute(key,value){this.attributes[key]=value;},focus(){this.focused=true;},closest(selector){return selector===this.selector?this:null;},matches(selector){return selector===this.selector;}};}
function workspace(value=model(),options={}){
  const tabs=["overview","flow","ambient","thermal","methods"].map(id=>Object.assign(node({ndTab:id}),{selector:"[data-nd-tab]"}));
  const panels=tabs.map(tab=>node({ndPanel:tab.dataset.ndTab})),chart=node(),error=node(),submit=node(),listeners=new Map(),downloads=[];
  const host={dataset:{},querySelectorAll:selector=>selector==="[data-nd-tab]"?tabs:selector==="[data-nd-panel]"?panels:[],querySelector:selector=>selector==="[data-nd-ambient-chart]"?chart:selector==="[data-nd-error]"?error:null,
    addEventListener(type,fn){listeners.set(type,fn);},removeEventListener(type,fn){assert.equal(listeners.get(type),fn);listeners.delete(type);}};
  const form=Object.assign(node(),{selector:"[data-nd-ambient-form]",elements:{maximum_pressure_kPa:{value:"200"},point_count:{value:"21"}},querySelector:()=>submit});
  const dispose=mountNozzleDesign(host,value,{onDownload:(...args)=>downloads.push(args),...options});
  return {host,tabs,panels,chart,error,submit,form,listeners,downloads,dispose,event:(type,target,extra={})=>listeners.get(type)?.({target,preventDefault(){},...extra})};
}
test("workspace tabs support keyboard focus, geometry handoff and listener cleanup",()=>{
  let opened=0;const ui=workspace(model(),{onGeometry:()=>opened++});
  ui.event("click",ui.tabs[2]);assert.equal(ui.host.dataset.nozzleDesignTab,"ambient");assert.equal(ui.panels[2].hidden,false);
  ui.event("keydown",ui.tabs[2],{key:"End"});assert.equal(ui.tabs[4].focused,true);assert.equal(ui.panels[4].hidden,false);
  ui.event("click",Object.assign(node({ndAction:"geometry"}),{selector:"[data-nd-action]"}));assert.equal(opened,1);
  ui.dispose();assert.equal(ui.listeners.size,0);
});
test("ambient update is read-only, validates inputs, blocks duplicate requests and exports new result",async()=>{
  const value=model(),before=JSON.stringify(value);let resolve,requests=[];
  const ui=workspace(value,{onAmbient:request=>{requests.push(request);return new Promise(done=>resolve=done);}});
  ui.form.elements.point_count.value="4.5";await ui.event("submit",ui.form);assert.equal(requests.length,0);assert.match(ui.error.textContent,/整数/);
  ui.form.elements.point_count.value="21";ui.form.elements.maximum_pressure_kPa.value="2001";await ui.event("submit",ui.form);assert.equal(requests.length,0);assert.match(ui.error.textContent,/不超过/);
  ui.form.elements.maximum_pressure_kPa.value="200";const pending=ui.event("submit",ui.form);await ui.event("submit",ui.form);assert.equal(requests.length,1);assert.equal(ui.submit.disabled,true);
  assert.deepEqual(requests[0],{maximum_pressure_Pa:200000,point_count:21});
  const fresh={...value.ambient,scope:"新采样范围",points:[{p_amb_Pa:200000,thrust_N:110000,Isp_s:280,Cf:1.4,screening:"flag"}]};resolve(fresh);await pending;
  assert.equal(ui.submit.disabled,false);assert.match(ui.chart.innerHTML,/新采样范围/);
  ui.event("click",Object.assign(node({ndExport:"ambient"}),{selector:"[data-nd-export]"}));assert.match(ui.downloads[0][0],/200000/);
  ui.event("click",Object.assign(node({ndExport:"json"}),{selector:"[data-nd-export]"}));assert.deepEqual(JSON.parse(ui.downloads[1][0]).ambient,fresh);
  assert.equal(JSON.stringify(value),before);ui.dispose();
});
test("leaving a nozzle record suppresses late ambient response and escaped service error",async()=>{
  let resolve;const ui=workspace(model(),{onAmbient:()=>new Promise(done=>resolve=done)}),pending=ui.event("submit",ui.form);
  ui.dispose();resolve({...model().ambient,scope:"late answer"});await pending;assert.equal(ui.chart.innerHTML,"");
  const rejected=workspace(model(),{onAmbient:async()=>{throw new Error("<script>network failure</script>");}});
  await rejected.event("submit",rejected.form);assert.equal(rejected.error.textContent,"<script>network failure</script>");assert.equal(rejected.error.innerHTML,"");assert.equal(rejected.error.hidden,false);rejected.dispose();
});
