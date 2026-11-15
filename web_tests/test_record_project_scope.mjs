/** Exercise production record loaders with deliberately reordered API replies. */
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";
import test from "node:test";

const source=readFileSync(new URL("../webapp/static/app.js",import.meta.url),"utf8");
function production(start,end){
  const first=source.indexOf(start),last=source.indexOf(end,first+start.length);
  assert.ok(first>=0&&last>first,`Missing production functions ${start} / ${end}`);
  return source.slice(first,last);
}
const functions=[
  production("function fillProject(","async function openProject("),
  production("async function refreshProjectSide(","function renderJobs("),
  production("async function showResult(","function newProject("),
  production("async function runDesign(","async function refreshMembers("),
].join("\n");
const record=(id,projectId="A")=>({id,project_id:projectId,revision:1,status:"succeeded",result:{pump_geometry:{source:{record_id:id}}}});
const project=id=>({id,revision:1,inputs:{},name:id,description:""});
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
function harness(api){
  const nodes=new Map(),renders=[];
  const context=vm.createContext({
    api,structuredClone,console,Map,
    $:id=>{if(!nodes.has(id))nodes.set(id,{value:"",textContent:"",innerHTML:""});return nodes.get(id);},
    renderView(){renders.push({projectId:context.current?.id,recordId:context.resultRecord?.id});},
    renderJobs(){},paintProjectWorkflow(){},rememberNavigation(){},updateActionState(){},escape:String,date:String,
    active:j=>["queued","running"].includes(j.status),archivedProject:()=>false,
    mergeInputDefaults:(a,b)=>b,clone:structuredClone,restoreDesignGroupReviews(){},
    setEditorError(){},clearPresetUndo(){},syncPresetSelection(){},resetParameterFinder(){},readParameterGroup:()=>"",
    renderFields(){},updatePresetContext(){},setDirty(){},updateDocument(){},
    switchPage(page){context.page=page;},designReadinessEnabled:()=>false,validateDraft(){},
    withEditorLock:fn=>fn(),toast(){},requireEditableDesign(){},
  });
  vm.runInContext(`
    var current={id:"A",revision:1},draft={},resultRecord=null,selectedJobId=null,projectJobs=[];
    var recordLoadSequence=0,refreshSequence=0,analysisView="pump_geometry",conceptSection="results",page="project",dirty=false;
    var info={ui_schema:{defaults:{}}},specifiedPumpSpeeds=new Map(),specifiedTurbineStages=new Map(),coolingChannelEditor=null;
    var statusNames={succeeded:"完成",queued:"排队"},editorBusy=false;
    ${functions}
    globalThis.app={fillProject,refreshProjectSide,loadRecord,showResult,runDesign};
  `,context);
  return {context,app:context.app,nodes,renders};
}

test("a record from another project or with the wrong id never reaches geometry rendering",async()=>{
  for(const returned of [record("job","B"),record("other"),{id:"job"}]){
    const h=harness(async()=>returned);
    await assert.rejects(h.app.loadRecord("job"),/当前方案不一致/);
    assert.equal(h.context.resultRecord,null);
    assert.equal(h.context.selectedJobId,null);
    assert.ok(h.renders.every(view=>!view.recordId));
  }
});

test("returning A to B to A invalidates an old A record response even for the same selected id",async()=>{
  const old=deferred(),fresh=deferred();let calls=0;
  const h=harness(()=>++calls===1?old.promise:fresh.promise);
  const pending=h.app.loadRecord("job");
  h.app.fillProject(project("B"));h.app.fillProject(project("A"));
  const newest=h.app.loadRecord("job");
  fresh.resolve({...record("job"),revision:2});await newest;
  old.resolve(record("job"));assert.equal(await pending,false);
  assert.equal(h.context.resultRecord.revision,2);
});

test("a project switch invalidates an in-flight jobs list, including A to B to A",async()=>{
  const jobs=deferred();
  const h=harness(path=>path.includes("/revisions")?Promise.resolve([]):jobs.promise);
  const pending=h.app.refreshProjectSide();
  h.app.fillProject(project("B"));h.app.fillProject(project("A"));
  jobs.resolve([record("old")]);await pending;
  assert.equal(h.context.projectJobs.length,0);
  assert.equal(h.context.resultRecord,null);
});

test("empty job lists clear geometry and invalidate a pending record request",async()=>{
  const job=deferred();
  const h=harness(path=>path==="/jobs/old"?job.promise:Promise.resolve([]));
  const pending=h.app.loadRecord("old");
  await h.app.refreshProjectSide();
  job.resolve(record("old"));await pending;
  assert.equal(h.context.resultRecord,null);
  assert.equal(h.context.selectedJobId,null);
  h.context.resultRecord=record("saved");h.context.selectedJobId="saved";
  await h.app.refreshProjectSide();assert.equal(h.context.resultRecord,null);
});

test("foreign project summaries cannot populate the current scheme selector",async()=>{
  const h=harness(async path=>path.includes("/revisions")?[]:[record("job","B")]);
  await assert.rejects(h.app.refreshProjectSide(),/其他方案/);
  assert.equal(h.context.projectJobs.length,0);
  assert.equal(h.context.resultRecord,null);
});

test("obsolete record success or failure cannot switch the newly chosen scheme view",async()=>{
  for(const fail of [false,true]){
    const job=deferred(),h=harness(()=>job.promise);
    const pending=h.app.showResult("job");
    h.app.fillProject(project("B"));h.context.analysisView="pump_geometry";
    if(fail)job.reject(new Error("A offline"));else job.resolve(record("job"));
    await pending;
    assert.equal(h.context.analysisView,"pump_geometry");
    assert.equal(h.context.resultRecord,null);
  }
});

test("submitting a new design removes the previous geometry before waiting for the new job list",async()=>{
  const list=deferred(),h=harness(async path=>{
    if(path==="/projects/A/jobs")return {id:"new",project_id:"A"};
    if(path.includes("/revisions"))return [];
    if(path.startsWith("/jobs?"))return list.promise;
    return record("new");
  });
  h.context.resultRecord=record("old");h.context.selectedJobId="old";
  h.context.analysisView="design_run";
  const pending=h.app.runDesign();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(h.context.resultRecord,null);
  assert.equal(h.context.selectedJobId,"new");
  list.resolve([record("new")]);await pending;
  assert.equal(h.context.resultRecord.id,"new");
});

test("empty selection does not issue an unscoped record request",async()=>{
  const h=harness(async()=>{throw new Error("unexpected API call");});
  assert.equal(await h.app.loadRecord(""),false);
  h.context.current=null;assert.equal(await h.app.loadRecord("job"),false);
});
