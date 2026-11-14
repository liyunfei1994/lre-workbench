import assert from "node:assert/strict";
import {test} from "node:test";
import {monitorSegments,monitorDisplayPoints,renderMonitorChart,createSolverMonitorFeed,mountSolverMonitor,monitorLayerState} from "../webapp/static/solver-monitor.js";

const point=(seq,y,run_id="inner-1",extra={})=>({seq,kind:"point",layer:"inner",run_id,iteration:seq-1,elapsed_seconds:seq,values:{acceptance_norm:y,mass_flow_kg_s:700+seq},...extra});
const job=(id="j1",project_id="p1",status="running")=>({id,project_id,status,revision:2,created:1});
const response=(events=[],extra={})=>({schema_version:1,job:job(),events,next_seq:events.at(-1)?.seq||0,has_more:false,...extra});
const flush=async()=>{await Promise.resolve();await Promise.resolve();await Promise.resolve();};
function scheduler(){let count=0;const pending=new Map();return {pending,setTimer(fn,delay){pending.set(++count,{fn,delay});return count;},clearTimer(id){pending.delete(id);},async tick(){const [id,item]=pending.entries().next().value||[];if(item){pending.delete(id);await item.fn();await flush();}return item;}};}
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
function feedHarness(api){const clock=scheduler(),changes=[],feed=createSolverMonitorFeed({api,onChange:data=>changes.push(data),...clock,now:()=>1234});return {feed,clock,changes};}

test("separate solver runs and missing physical observations never form one curve",()=>{
  const events=[point(1,5),point(2,4),point(3,null),point(4,1),point(5,10,"inner-2"),point(6,3,"inner-2")];
  const segments=monitorSegments(events);
  assert.deepEqual(segments.map(segment=>segment.points.map(point=>point.x)),[[1,2],[4],[5,6]]);
  assert.deepEqual(monitorSegments(events,{run:"inner-2"}).map(segment=>segment.points.map(point=>point.y)),[[10,3]]);
  assert.deepEqual(monitorSegments([point(1,NaN),point(2,Infinity),point(3,"1")]),[]);
});
test("log residual chart preserves exact zeros with a separate marker and disconnection",()=>{
  const html=renderMonitorChart([point(1,5),point(2,0),point(3,.2)],{log:true});
  assert(html.includes("精确零值（1 点）"));assert(html.includes("准则 1"));
  assert(!html.includes("NaN")&&!html.includes("Infinity"));
  const paths=[...html.matchAll(/<path d="([^"]*)" fill="none"/g)].map(match=>match[1]);
  assert.equal(paths.length,1);assert.equal((paths[0].match(/M/g)||[]).length,2);
});
test("physical quantity charts do not borrow the normalized residual tolerance",()=>{
  const html=renderMonitorChart([point(1,5,"r",{tolerance:1,values:{mass_flow_kg_s:300}})],{key:"mass_flow_kg_s"});
  assert(html.includes("kg/s"));assert(!html.includes("准则"));
});
test("thermal model-evaluation success cannot stand in for outer feedback convergence",()=>{
  const events=[{kind:"start",layer:"thermal",run_id:"outer",context:{scope:"outer_feedback"}},{kind:"start",layer:"thermal",run_id:"trial",context:{scope:"model_evaluation"}},{kind:"end",layer:"thermal",run_id:"trial",status:"pass",context:{scope:"model_evaluation"}}];
  assert.equal(monitorLayerState(events,"thermal"),"最近模型求值：满足该层准则；外层热反馈：已开始");
  events.push({kind:"end",layer:"thermal",run_id:"outer",status:"fail",context:{scope:"outer_feedback"}});
  assert.equal(monitorLayerState(events,"thermal"),"最近模型求值：满足该层准则；外层热反馈：未满足该层准则");
  assert.equal(monitorLayerState([{kind:"end",layer:"inner",run_id:"r",status:"pass"}],"inner"),"最近轮次：满足该层准则");
});
test("signed physical residuals retain signs; normalized charts can explicitly display magnitude",()=>{
  const events=[point(1,3,"r",{values:{thrust_residual_N:-500,thrust_normalized_residual:-3}})];
  assert(renderMonitorChart(events,{key:"thrust_residual_N"}).includes("-500 N"));
  const html=renderMonitorChart(events,{key:"thrust_normalized_residual",absolute:true});
  assert(html.includes(" 3 1"));assert.equal(events[0].values.thrust_normalized_residual,-3);
});
test("temperature residual chart uses temperature-specific tolerance, not six-criterion norm",()=>{
  const html=renderMonitorChart([point(1,4,"t",{layer:"thermal",values:{residual_K:.008},tolerance:1,residual_tolerance_K:.001})],{layer:"thermal",key:"residual_K"});
  assert(html.includes("准则 0.001"));assert(!html.includes("准则 1"));
});
test("long chart sampling preserves extrema and endpoints; full input remains available",()=>{
  const points=Array.from({length:3000},(_,x)=>({x,y:x===1377?9999:x===1388?-444:x%7}));
  const display=monitorDisplayPoints(points,500);
  assert(display.length<=500);assert.equal(display[0],points[0]);assert.equal(display.at(-1),points.at(-1));
  assert(display.includes(points[1377])&&display.includes(points[1388]));assert.equal(points.length,3000);
});
test("chart escapes arbitrary labels and run identifiers",()=>{
  const html=renderMonitorChart([point(1,1,'<img src=x onerror=alert(1)>')],{title:"<script>unsafe</script>"});
  assert(!html.includes("<img")&&!html.includes("<script>"));assert(html.includes("&lt;script&gt;"));
});

test("incremental monitor drains paginated history and retains failed terminal samples",async()=>{
  const calls=[],pages=[response([point(1,3)],{has_more:true}),response([point(2,2)],{job:job("j1","p1","failed")})];
  const h=feedHarness(async path=>{calls.push(path);return pages.shift();});
  h.feed.update({projectId:"p1",jobId:"j1",job:job(),visible:true});await flush();
  assert(calls[0].includes("/jobs/j1/monitor?after=0&limit=500"));assert.equal(h.clock.pending.size,1);
  assert.equal((await h.clock.tick()).delay,0);assert(calls[1].includes("after=1"));
  const data=h.feed.snapshot();assert.equal(data.complete,true);assert.equal(data.job.status,"failed");assert.equal(data.events.length,2);assert.equal(h.clock.pending.size,0);
});
test("network interruption keeps accepted data and retries from the last cursor",async()=>{
  let count=0;const calls=[];
  const h=feedHarness(async path=>{calls.push(path);if(++count===2)throw Error("offline");return response([point(count===1?1:2,5)],{next_seq:count===1?1:2});});
  h.feed.update({projectId:"p1",jobId:"j1",visible:true});await flush();await h.clock.tick();
  assert.equal(h.feed.snapshot().events.length,1);assert.equal(h.feed.snapshot().error,"offline");
  await h.clock.tick();assert(calls[2].includes("after=1"));assert.equal(h.feed.snapshot().error,"");assert.equal(h.feed.snapshot().events.length,2);h.feed.dispose();
});
test("switching projects rejects late responses and old-request errors",async()=>{
  const first=deferred(),second=deferred();let count=0;
  const h=feedHarness(()=>++count===1?first.promise:second.promise);
  h.feed.update({projectId:"p1",jobId:"j1",visible:true});
  h.feed.update({projectId:"p2",jobId:"j2",visible:true});
  second.resolve(response([point(9,2)],{job:job("j2","p2","cancelled"),next_seq:9}));await flush();
  first.reject(Error("old error"));await flush();
  assert.equal(h.feed.snapshot().job.id,"j2");assert.equal(h.feed.snapshot().error,"");assert.deepEqual(h.feed.snapshot().events.map(event=>event.seq),[9]);
});
test("foreign project identity is rejected without appending observations",async()=>{
  const h=feedHarness(async()=>response([point(1,4)],{job:job("j1","other")}));
  h.feed.update({projectId:"p1",jobId:"j1",visible:true});await flush();
  assert.equal(h.feed.snapshot().events.length,0);assert.match(h.feed.snapshot().error,/不一致/);h.feed.dispose();
});
test("hidden pages suspend polling; resuming loads missed samples; disposal ignores in-flight data",async()=>{
  let count=0;const last=deferred();
  const h=feedHarness(async()=>++count<3?response([point(count,4)],{next_seq:count}):last.promise);
  h.feed.update({projectId:"p1",jobId:"j1",visible:true});await flush();
  h.feed.update({projectId:"p1",jobId:"j1",visible:false});assert.equal(h.clock.pending.size,0);
  h.feed.update({projectId:"p1",jobId:"j1",visible:true});await flush();assert.equal(count,2);
  await h.clock.tick();const before=h.changes.length;h.feed.dispose();last.resolve(response([point(3,2)]));await flush();
  assert.equal(h.changes.length,before);assert.equal(h.clock.pending.size,0);
});
test("legacy terminal record reports empty history and does not synthesize final-result points",async()=>{
  const h=feedHarness(async()=>response([],{job:job("j1","p1","succeeded")}));
  h.feed.update({projectId:"p1",jobId:"j1",visible:true});await flush();
  assert.equal(h.feed.snapshot().complete,true);assert.equal(h.feed.snapshot().events.length,0);
});
test("invalid cursors cannot produce endless zero-delay backlog loops",async()=>{
  const h=feedHarness(async()=>response([],{has_more:true,next_seq:0}));
  h.feed.update({projectId:"p1",jobId:"j1",visible:true});await flush();
  assert.match(h.feed.snapshot().error,/未取得进展/);assert.equal([...h.clock.pending.values()][0].delay,1500);h.feed.dispose();
});

function element(){
  let html="";const children=new Map(),listeners=new Map();return {listeners,textContent:"",value:"",hidden:false,disabled:false,
    get innerHTML(){return html;},set innerHTML(value){html=value;children.clear();},
    querySelector(selector){const name=selector.slice(1,-1);if(!html.includes(name))return null;if(!children.has(selector))children.set(selector,element());return children.get(selector);},
    addEventListener(name,fn){listeners.set(name,fn);},removeEventListener(name){listeners.delete(name);},
  };
}
test("mounted monitor shows failure provenance and model-evaluation thermal boundaries",async()=>{
  const clock=scheduler(),host=element(),events=[point(1,4),{seq:2,kind:"point",layer:"thermal",run_id:"thermal-2",iteration:1,context:{scope:"model_evaluation",parent_run_id:"inner-1"},values:{acceptance_norm:3,residual_K:.003},tolerance:1},{seq:3,kind:"end",layer:"inner",run_id:"inner-1",status:"fail",termination_reason:"iteration_limit"}];
  const controller=mountSolverMonitor(host,{api:async()=>response(events,{job:{...job("j1","p1","failed"),error:"iteration_limit"}}),...clock});
  controller.update({project:{id:"p1",revision:8},jobs:[job("j1","p1","failed")],selectedJobId:"j1",visible:true,draftDirty:true});await flush();
  assert.match(host.querySelector("[data-sm-version]").innerHTML,/方案 v2/);assert.match(host.querySelector("[data-sm-version]").innerHTML,/当前方案 v8/);
  assert.match(host.querySelector("[data-sm-layer-state]").innerHTML,/未满足该层准则/);
  assert.match(host.querySelector("[data-sm-thermal-context]").textContent,/不代表循环内层已接受/);
  assert.match(host.querySelector("[data-sm-thermal-context]").textContent,/所属内层 inner-1/);
  assert.equal(host.querySelector("[data-sm-export]").textContent,"导出完整监控记录");
  assert.match(host.querySelector("[data-sm-events]").innerHTML,/iteration_limit/);controller.dispose();assert.equal(host.listeners.size,0);
});
test("active history export is clearly identified as partial",async()=>{
  const clock=scheduler(),host=element(),controller=mountSolverMonitor(host,{api:async()=>response([point(1,3)]),...clock});
  controller.update({project:{id:"p1"},jobs:[job()],selectedJobId:"j1",visible:true});await flush();
  assert.equal(host.querySelector("[data-sm-export]").textContent,"导出已接收记录");controller.dispose();
});
