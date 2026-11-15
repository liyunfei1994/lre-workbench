import assert from "node:assert/strict";
import {renderValveDesign, renderValveSelectionEnvelope, valveSelectionJobs,
  renderValveSelectionPicker, mountValveSelection} from "../webapp/static/valve-design.js";

const jobs = [
  {id:"a",project_id:"project-a",status:"succeeded",mode:"concept_design",revision:1,created:1794182400},
  {id:"b",project_id:"project-a",status:"succeeded",mode:"concept_design",revision:2},
  {id:"foreign",project_id:"project-b",status:"succeeded",mode:"concept_design"},
  {id:"transient",project_id:"project-a",status:"succeeded",mode:"transient"},
  {id:"running",project_id:"project-a",status:"running",mode:"concept_design"}
];
assert.deepEqual(valveSelectionJobs(jobs,"project-a").map(job=>job.id),["a","b"]);
const picker = renderValveSelectionPicker(valveSelectionJobs(jobs,"project-a"));
assert.doesNotMatch(picker, /checked/);
assert.match(picker, /data-valve-build disabled/);
assert.match(picker, /<details[^>]*data-valve-selection-details><summary>多工况选型包<\/summary>/);
assert.doesNotMatch(picker, /data-valve-selection-details open/);
assert.match(picker, /class="fs-valve-job-list"/);
assert.ok(picker.includes(new Date(jobs[0].created*1000).toLocaleString("zh-CN",{hour12:false})));
assert.match(picker, /时间未保存/);
assert.match(renderValveDesign({status:"missing"}), /未保存实体阀/);
const html = renderValveDesign({scope:"saved_valve_design",status:"recorded",calculation_status:"infeasible",
  configuration:{mode:"candidate_check"},source:{project_id:"<img src=x>",revision:1,source_design_job_id:"a"},
  rows:[{id:"MFV",configuration:{fluid:"CH4",upstream_node:"<script>"},result:{status:"infeasible",reason:"above_maximum_flow",saturated:true}}],
  issues:[],limitations:[]});
assert.doesNotMatch(html, /<img src=x>|<script>/);
assert.match(html, /&lt;img/);
assert.match(html, /所需流量超过/);
assert.match(renderValveSelectionEnvelope({scope:"saved_valve_selection_envelope",sources:[],valves:{},all_points_feasible:false}), /不能作为能力通过证明/);

class Container {
  constructor(){this.innerHTML="";this.listeners=new Map();this.detailsOpen=false;}
  querySelector(){return {open:this.detailsOpen};}
  addEventListener(kind,handler){this.listeners.set(kind,handler);}
  removeEventListener(kind,handler){if(this.listeners.get(kind)===handler)this.listeners.delete(kind);}
  contains(){return true;}
  choose(value, checked=true){const checkbox={value,checked};return this.listeners.get("change")?.({target:{closest:()=>checkbox}});}
  build(){const button={disabled:false,hasAttribute:key=>key==="data-valve-build"};return this.listeners.get("click")?.({target:{closest:()=>button}});}
}
let requests=[], resolve;
const container = new Container();
const cleanup = mountValveSelection(container,{project:{id:"project-a"},jobs,
  api:(...args)=>{requests.push(args);return new Promise(done=>{resolve=done;});}});
assert.equal(requests.length,0,"mount must not automatically select or aggregate jobs");
assert.doesNotMatch(container.innerHTML,/data-valve-selection-details open/);
container.choose("foreign");
await container.build();
assert.equal(requests.length,0,"foreign ids must not become selected");
container.detailsOpen=true;container.choose("b");
assert.match(container.innerHTML,/data-valve-selection-details open/);
const pending = container.build();
assert.deepEqual(requests[0],["/projects/project-a/valve-selection-envelope","POST",{job_ids:["b"]}]);
const answer={scope:"saved_valve_selection_envelope",project_id:"project-a",sources:[{source_design_job_id:"b"}],valves:{},all_points_feasible:true};
resolve(answer);await pending;
assert.match(container.innerHTML,/三阀 · 多工况选型需求包/);
container.choose("a");
assert.doesNotMatch(container.innerHTML,/三阀 · 多工况选型需求包/);
assert.match(container.innerHTML,/data-valve-download disabled/);
const late=container.build();
cleanup();const before=container.innerHTML;resolve(answer);await late;
assert.equal(container.innerHTML,before,"late response cannot repaint a disposed scheme view");
assert.equal(container.listeners.size,0);

const wrong = new Container();let message="";
mountValveSelection(wrong,{project:{id:"project-a"},jobs,notify:value=>{message=value;},
  api:async()=>({...answer,project_id:"project-b"})});
wrong.choose("a");await wrong.build();
assert.match(message,/当前方案/);
assert.doesNotMatch(wrong.innerHTML,/三阀 · 多工况选型需求包/);
console.log("valve design saved-result UI checks passed");
