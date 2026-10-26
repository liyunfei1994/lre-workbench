// Synthetic chart fixtures only; these numbers are not engine validation data.
import assert from "node:assert/strict";
import test from "node:test";
import {buildThrottleRequest,prepareThrottleData,renderThrottleCharts,renderThrottleResponseGains,renderThrottleResult,buildThrottleCsv,buildThrottleSvg,mountThrottle} from "../webapp/static/throttle.js";

const clone=value=>JSON.parse(JSON.stringify(value));
const source=()=>({project:{id:"project-1",revision:3},dirty:true,draft:{pc:99e6,thrust:9e6},record:{id:"design-1",project_id:"project-1",revision:2,status:"succeeded",created:1,inputs:{pc:8e6,thrust:999999},result:{calculation:{inputs:{pc:9e6,thrust:1000000,ambient_pressure:101325},summary:{thrust_N:1000001}}}}});
function point(ratio,options={}){
  return {index:1,throttle_ratio:ratio,thrust_target_N:1e6*ratio,pc_Pa:9e6*ratio,converged:true,summary:{thrust_N:999000*ratio,Isp_s:300+20*ratio,Isp_vac_s:330+20*ratio,mdot_total_kg_s:300*ratio,mdot_ox_kg_s:240*ratio,mdot_fuel_kg_s:60*ratio,chamber_temperature_K:3400+40*ratio,ox_preburner_T_combustion_K:750,fuel_preburner_T_combustion_K:850,solver_residual_norm:1e-5},assessment:{status:"pass",numerical:{status:"pass",residual:1e-5},checks:[{key:"pressure",label:"供给压差",value:1,limit:0,criterion:"minimum",unit:"MPa",status:"pass"}]},geometry:{status:"pass",throat_area_m2:.02,reference_area_m2:.02,relative_error:0,tolerance:.0005},warnings:[],status:"pass",...options};
}
function result(){return {source:{job_id:"design-1",revision:2},request:{source_job_id:"design-1",throttle_min:.4,throttle_steps:5},baseline:{thrust_N:1e6,pc_Pa:9e6,ambient_pressure_Pa:101325,of_ratio:4,expansion_ratio:15},points:[point(1),point(.85,{status:"review",assessment:{status:"fail",checks:[{label:"供给压力",value:-1,limit:0,criterion:"minimum",unit:"MPa",status:"fail"}]}}),point(.7,{converged:false,summary:{thrust_N:123456789,mdot_total_kg_s:999999},assessment:{numerical:{status:"fail"}},status:"failed",error:"未收敛"}),point(.55),point(.4)],complete:true,limitations:["只固定主喷管喉部，不计算节流过渡。"]};}

test("selected FFSC points expose saved physical closure and legacy missing evidence",()=>{
  const data=result(), selected=data.points.find(p=>p.throttle_ratio === .4);
  selected.inputs={cycle_type:"ffsc"};
  selected.steady_closure={schema_version:1,assumptions:["测试保存的取流点"],feed_network:{status:"fail",mode:"fixed_resistance",branches:[]},energy_balance:{status:"unavailable",reference:"测试共同焓基准",boundary:"测试边界",terms:[],checks:[]}};
  let html=renderThrottleResult(data,.4);
  assert.match(html,/供给网络与能量闭合/);
  assert.match(html,/固定支路阻力再匹配/);
  assert.match(html,/测试保存的取流点/);
  assert.match(html,/测试共同焓基准/);
  delete selected.steady_closure;
  html=renderThrottleResult(data,.4);
  assert.match(html,/缺失数据不作为零值或通过/);
  selected.inputs.cycle_type="gg";
  assert.doesNotMatch(renderThrottleResult(data,.4),/供给网络与能量闭合/);
});

test("request binds the completed source and validates the actual backend bounds without mutating inputs",()=>{
  const context=source(),before=clone(context);
  assert.deepEqual(buildThrottleRequest(context,{minimum_percent:"40",point_count:"5"}),{source_job_id:"design-1",throttle_min:.4,throttle_steps:5});
  assert.deepEqual(buildThrottleRequest(context,{minimum_percent:5,point_count:15}),{source_job_id:"design-1",throttle_min:.05,throttle_steps:15});
  assert.equal(buildThrottleRequest(context,{minimum_percent:99,point_count:2}).throttle_min,.99);
  for(const form of [{minimum_percent:"",point_count:5},{minimum_percent:4,point_count:5},{minimum_percent:100,point_count:5},{minimum_percent:NaN,point_count:5},{minimum_percent:40,point_count:1},{minimum_percent:40,point_count:16},{minimum_percent:40,point_count:2.5}])assert.throws(()=>buildThrottleRequest(context,form));
  assert.deepEqual(context,before);
});

test("absent, unfinished and foreign sources cannot submit scans",()=>{
  for(const mutate of [c=>c.record=null,c=>c.record.status="running",c=>c.record.result=null,c=>c.record.project_id="foreign"]){const context=source();mutate(context);assert.throws(()=>buildThrottleRequest(context,{minimum_percent:40,point_count:5}),/已完成/);}
});

test("chart grid sorts numeric throttle values and preserves failed and pending positions",()=>{
  const data=prepareThrottleData(result());
  assert.deepEqual(data.points.map(p=>p.throttle_ratio),[.4,.55,.7,.85,1]);
  assert.equal(data.panels.length,6);
  const thrust=data.panels.find(p=>p.id==="thrust");
  assert.equal(thrust.series[0].value(data.points[2]),null,"Failed numeric payload must not be plotted as valid performance");
  assert.equal(thrust.series[1].value(data.points[2]),700,"Target is a saved input, not the failed output");
  const partial=result();partial.points=[point(1),point(.85)];partial.complete=false;
  const pending=prepareThrottleData(partial);
  assert.equal(pending.points.length,5);assert.equal(pending.points.filter(p=>p.pending).length,3);
  assert.equal(pending.points[0].summary,undefined,"No interpolation or zero fill");
});

test("the actual thrust curve breaks around failed points and uses review marker shapes",()=>{
  const html=renderThrottleCharts(result());
  const first=html.match(/data-throttle-chart="thrust"[\s\S]*?<\/article>/)[0];
  const lines=[...first.matchAll(/<polyline[^>]*data-series="thrust_N"[^>]*points="([^"]+)"/g)];
  assert.equal(lines.length,2);assert.ok(lines.every(line=>line[1].split(" ").length===2));
  assert.match(first,/data-failed-ratio="0.7"/);assert.match(first,/th-review-marker/);
  assert.doesNotMatch(first,/123456789/);
  assert.match(first,/data-ratio="0.4"/);assert.match(first,/data-ratio="1"/);
  assert.doesNotMatch(html,/NaN|Infinity/);
});

test("missing GG preburners are absent instead of showing invented zero temperatures",()=>{
  const gg=result();
  for(const p of gg.points){delete p.summary.ox_preburner_T_combustion_K;delete p.summary.fuel_preburner_T_combustion_K;p.summary.gg_T_combustion_K=810;}
  const panel=prepareThrottleData(gg).panels.find(p=>p.id==="temperature");
  assert.deepEqual(panel.series.map(s=>s.key),["chamber_temperature_K","gg_T_combustion_K"]);
  const html=renderThrottleCharts(gg).match(/data-throttle-chart="temperature"[\s\S]*?<\/article>/)[0];
  assert.match(html,/燃气发生器/);assert.doesNotMatch(html,/富氧预燃室|富燃预燃室/);
});

test("numeric ratio rounding does not add phantom points and duplicated saved ratios are not silently chosen",()=>{
  const r=result();r.points[1].throttle_ratio=.8500000000000001;
  assert.equal(prepareThrottleData(r).points.length,5);
  r.points.push(point(.85));const data=prepareThrottleData(r);
  assert.equal(data.points.length,5);assert.equal(data.points[3].duplicate,true);
  assert.equal(data.panels[0].series[0].value(data.points[3]),null);
});

test("numerical success, engineering rejection and throat closure remain separate",()=>{
  const r=result(),html=renderThrottleResult(r,.85);
  assert.match(html,/85% 节流点诊断/);assert.match(html,/已收敛/);assert.match(html,/系统约束 · 未通过/);
  assert.match(html,/主喷管喉部闭合/);assert.match(html,/data-check-status="fail"/);
  assert.match(html,/此曲线不是时间响应/);assert.match(html,/失败点与未计算点保留缺口/);
  const failed=html.match(/data-point-status="failed"[\s\S]*?<\/tr>/)[0];
  assert.match(failed,/未收敛/);assert.doesNotMatch(failed,/123,456|999,999/);
  assert.match(failed,/>—<\/td>/);
});

test("main throat closure failure does not relabel an independently converged solver as nonconverged",()=>{
  const r=result();r.points[1].converged=false;r.points[1].status="failed";r.points[1].assessment.numerical={status:"pass"};r.points[1].geometry.status="fail";
  const html=renderThrottleResult(r,.85),detail=html.slice(html.indexOf('aria-label="所选节流点诊断"'));
  assert.match(detail,/已收敛/);assert.match(detail,/主喷管喉部闭合<\/span><span class="th-badge th-status-fail">未通过/);
  const data=prepareThrottleData(r);assert.equal(data.panels[0].series[0].value(data.points[3]),null);
});

test("record fields and diagnostic strings are escaped throughout HTML and SVG",()=>{
  const r=result();r.points[1].error='<img src=x onerror="alert(1)">';r.points[1].warnings=['<script>oops</script>'];r.points[1].assessment.checks[0].label='<b>pressure</b>';r.limitations=['</li><script>x</script>'];r.source.job_id='</text><script>x</script>';
  const html=renderThrottleResult(r,.85),svg=buildThrottleSvg(r);
  assert.doesNotMatch(html,/<script>|<img src=x/);assert.match(html,/&lt;img/);assert.match(html,/&lt;b&gt;pressure/);
  assert.doesNotMatch(svg,/<script>/);assert.match(svg,/&lt;\/text&gt;/);
});

test("CSV preserves failed and uncomputed rows, export values and formula-like error text safely",()=>{
  const r=result();r.points[2].error='=HYPERLINK("bad")';r.points[1].summary.thrust_N=r.points[1].thrust_target_N-1;
  const csv=buildThrottleCsv(r);
  assert.equal(csv.charCodeAt(0),0xfeff);assert.equal(csv.split("\r\n").length,6);
  assert.match(csv,/'=HYPERLINK/);assert.doesNotMatch(csv,/"123456789"|"999999"/);
  const row=csv.split("\r\n").find(line=>line.startsWith('"70"'));
  assert.match(row,/"70","700","","",""/);assert.match(row,/未收敛/);
  assert.doesNotMatch(csv,/"'-0\./,"negative numeric errors must remain numeric");
  const partial=result();partial.points=[point(1)];assert.equal(buildThrottleCsv(partial).split("\r\n").length,6);
});

test("CSV rounds only throttle percentage representation, retaining raw result precision",()=>{
  const r=result();r.points[3].throttle_ratio=.5500000000000001;r.points[3].summary.Isp_s=311.1234567890123;
  const csv=buildThrottleCsv(r),row=csv.split("\r\n").find(line=>line.startsWith('"55",'));
  assert.ok(row);assert.doesNotMatch(row,/55\.00000000000001/);assert.match(row,/"311\.1234567890123"/);
});

test("zero-point job states distinguish initial, queued, running and cancelled records",()=>{
  assert.match(renderThrottleResult(null),/选择已完成的设计记录/);
  const cases={queued:"扫描已提交，正在排队等待",running:"正在求解首个工况",cancelled:"扫描已取消，尚未返回工况",timed_out:"扫描已超时，尚未返回工况",failed:"扫描执行失败，尚未返回工况"};
  for(const [status,label] of Object.entries(cases)){
    const html=renderThrottleResult({points:[]},null,status);
    assert.ok(html.includes(label));assert.doesNotMatch(html,/选择已完成的设计记录/);assert.doesNotMatch(html,/<svg/);
  }
});

test("standalone SVG contains six labeled plots, units, source and no external assets",()=>{
  const svg=buildThrottleSvg(result());
  assert.match(svg,/width="1220" height="1240"/);assert.equal((svg.match(/th-export-panel-title"/g)||[]).length,6);
  assert.match(svg,/主燃烧室/);assert.match(svg,/当前环境/);assert.match(svg,/design-1/);assert.match(svg,/不表示时间响应/);
  assert.doesNotMatch(svg,/NaN|Infinity|<image|<foreignObject|<script/);
});

const tick=()=>new Promise(resolve=>setImmediate(resolve));

test("response gain history and incomplete scans explain absent normalization without inventing results",()=>{
  assert.match(renderThrottleResponseGains(result()),/历史记录尚未保存响应增益/);
  const html=renderThrottleResponseGains({response_gains:{status:"unavailable",reason:"缺少有效100%参考工况",metrics:[]}});
  assert.match(html,/缺少有效100%参考工况/);assert.doesNotMatch(html,/<table|>0<\/td>/);
});

test("response gains preserve review intervals, absent finite differences and units",()=>{
  const interval={from_ratio:.8,to_ratio:1,step_ratio:.2,status:"review",reason:"供给压力不足"};
  const r=result();r.response_gains={status:"partial",reference:{status:"review"},intervals:[interval],metrics:[
    {key:"pc_Pa",label:"室压",unit:"Pa",reference_value:1e7,near_nominal_gain:1,gain_min:1,gain_max:1,segments:[{...interval,gain:1,slope_per_ratio:1e7}]},
    {key:"Isp_s",label:"比冲",unit:"s",reference_value:320,near_nominal_gain:null,gain_min:null,gain_max:null,segments:[{...interval,status:"unavailable",gain:null,slope_per_ratio:null}]}
  ]};
  const html=renderThrottleResponseGains(r);
  assert.match(html,/100%参考工况的系统约束未通过/);assert.match(html,/全部增益仅用于诊断/);
  assert.match(html,/80%–100%/);assert.match(html,/20 个百分点/);assert.match(html,/归一化增益 G \/ —/);
  assert.match(html,/Pa \/ 指令比例/);assert.match(html,/缺少有效区间/);assert.match(html,/不跨失败点/);
  assert.match(html,/未给出步长收敛检查、重复性试验或统计置信区间/);
  assert.doesNotMatch(html,/NaN|Infinity|>null<|>0<\/td>/);
  const full=renderThrottleResult(r);
  assert.ok(full.indexOf('aria-label="有限区间节流响应增益"')>full.indexOf('class="th-chart-grid"'));
  assert.ok(full.indexOf('aria-label="有限区间节流响应增益"')<full.indexOf('class="th-data"'));
  r.response_gains.metrics[0].label='<img src=x onerror="bad()">';r.response_gains.intervals[0].reason='<script>x</script>';
  const escaped=renderThrottleResponseGains(r);assert.doesNotMatch(escaped,/<img|<script>/);assert.match(escaped,/&lt;img/);
});

function container(){const listeners={};return {innerHTML:"",listeners,addEventListener(type,handler){listeners[type]=handler;},action(action){listeners.click({target:{closest:()=>({disabled:false,dataset:{throttleAction:action}})}});},field(name,value){listeners.input({target:{dataset:{throttleField:name},value}});},choose(id){listeners.change({target:{hasAttribute:attribute=>attribute==="data-throttle-record",value:id}});}};}
const deferred=()=>{let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};};

test("mount displays actual frozen calculation inputs and does not present draft or recomputed summary as target",()=>{
  const view=container(),ctx=source(),ui=mountThrottle(view,{api:async()=>[],getContext:()=>ctx});
  assert.match(view.innerHTML,/基准目标推力 \/ kN<\/span><b>1,000<\/b>/);
  assert.match(view.innerHTML,/基准室压 \/ MPa<\/span><b>9<\/b>/);
  assert.match(view.innerHTML,/当前参数或方案版本/);assert.match(view.innerHTML,/固定主喷管喉部/);
  assert.doesNotMatch(view.innerHTML,/从 100% 向最低|固定基准几何/);
  assert.match(view.innerHTML,/readonly aria-label="最高节流比例固定为100%"/);ui.onLeave();
});

test("mount submits only source ID and scan controls, then retains the created record",async()=>{
  const view=container(),ctx=source(),calls=[];
  const ui=mountThrottle(view,{getContext:()=>ctx,api:async(path,method,body)=>{calls.push({path,method,body});return {id:"sweep-new",status:"succeeded",result:result()};}});
  view.field("minimum_percent","55");view.field("point_count","4");view.action("run");await tick();
  assert.deepEqual(calls,[{path:"/projects/project-1/throttles",method:"POST",body:{source_job_id:"design-1",throttle_min:.55,throttle_steps:4}}]);
  assert.match(view.innerHTML,/sweep-ne/);assert.match(view.innerHTML,/扫描完成/);ui.onLeave();
});

test("an in-flight history response cannot overwrite a newly selected project",async()=>{
  const view=container(),pending=deferred();let ctx=source();
  const ui=mountThrottle(view,{getContext:()=>ctx,api:async()=>pending.promise});
  const refresh=ui.refresh();ctx={...source(),project:{id:"project-2",revision:1},record:null};ui.sync();
  pending.resolve([{id:"old-sweep",project_id:"project-1",status:"succeeded"}]);await refresh;
  assert.doesNotMatch(view.innerHTML,/old-sweep/);assert.match(view.innerHTML,/未选择已完成的设计记录/);ui.onLeave();
});

test("leaving the page prevents a pending run from updating or notifying the old view",async()=>{
  const view=container(),pending=deferred(),notices=[];
  const ui=mountThrottle(view,{getContext:()=>source(),api:async()=>pending.promise,notify:text=>notices.push(text)});
  view.action("run");ui.onLeave();const before=view.innerHTML;
  pending.resolve({id:"late-sweep",status:"queued"});await tick();
  assert.equal(view.innerHTML,before);assert.deepEqual(notices,[]);
});

test("failed polling is visible and cancelled scans keep their partial points",async()=>{
  const view=container(),record={id:"partial",project_id:"project-1",revision:2,status:"cancelled",request:result().request,progress:{completed:2},result:{...result(),complete:false,points:[point(.4),point(.55)]}};
  const ui=mountThrottle(view,{getContext:()=>source(),api:async path=>path.includes("/projects/")?[record]:record});
  await ui.refresh();assert.match(view.innerHTML,/已取消/);assert.match(view.innerHTML,/2 \/ 5 个工况已返回/);assert.match(view.innerHTML,/未计算/);assert.match(view.innerHTML,/throttle-chart="thrust"/);ui.onLeave();
  const errorView=container(),errorUi=mountThrottle(errorView,{getContext:()=>source(),api:async()=>{throw Error("connection unavailable");}});
  await errorUi.refresh();assert.match(errorView.innerHTML,/connection unavailable/);errorUi.onLeave();
});

test("mount forwards zero-point cancelled state instead of initial run instructions",async()=>{
  const view=container(),record={id:"cancelled-empty",project_id:"project-1",revision:2,status:"cancelled",request:result().request,progress:{completed:0},result:{points:[]}};
  const ui=mountThrottle(view,{getContext:()=>source(),api:async path=>path.includes("/projects/")?[record]:record});
  await ui.refresh();assert.match(view.innerHTML,/扫描已取消，尚未返回工况/);assert.doesNotMatch(view.innerHTML,/选择已完成的设计记录，设置最低/);ui.onLeave();
});
