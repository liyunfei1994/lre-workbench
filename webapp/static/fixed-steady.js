// Task definitions and independent numerical checks. Never submits a physical solver job.
import {renderAnalysisParameterRoles} from "./parameter-editor.js";
import {renderModelData,renderFrameworkChecks,renderDataDeclarationCheck} from "./fixed-iteration.js";
import {renderValveRequirements} from "./valve-requirements.js";
import {mountControlTrim,renderControlTrimPlaceholder} from "./control-trim.js";
const escape = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const clone = value => JSON.parse(JSON.stringify(value));
const date = value => value ? new Date(value * 1000).toLocaleString("zh-CN", {hour12:false}) : "—";
export const FIXED_BOUNDARIES = Object.freeze([
  {key:"ambient_pressure",label:"环境压力",unit:"MPa",factor:1e-6,zero:true},
  {key:"tank_pressure_pa",label:"贮箱压力",unit:"MPa",factor:1e-6},
  {key:"fuel_inlet_temp",label:"燃料入口温度",unit:"K",factor:1},
  {key:"ox_inlet_temp",label:"氧化剂入口温度",unit:"K",factor:1},
]);
const LEGACY_BUDGET_FIELDS = [
  {key:"inlet_pressure",label:"历史增压出口压力要求（未采纳为边界）",unit:"MPa",factor:1e-6},
  {key:"feed_line_dp_pa",label:"历史管路压降预算（未采纳为边界）",unit:"MPa",factor:1e-6,zero:true},
];
const displayedBoundaries=task=>task?.schema_version===1 ? [...FIXED_BOUNDARIES,...LEGACY_BUDGET_FIELDS] : FIXED_BOUNDARIES;

export function fixedSteadyForm(task) {
  const boundaries=task?.operating_conditions?.boundaries || {};
  return Object.fromEntries(displayedBoundaries(task).map(field=>[field.key,
    Number.isFinite(boundaries[field.key]) ? String(Number((boundaries[field.key]*field.factor).toPrecision(12))) : ""]));
}

export function buildFixedSteadyRequest(sourceJobId, form) {
  if(!sourceJobId)throw new Error("请先选择已完成的概念设计记录。");
  const boundaries={};
  for(const field of FIXED_BOUNDARIES){
    const raw=form?.[field.key], value=String(raw??"").trim()==="" ? NaN : Number(raw);
    if(!Number.isFinite(value) || (field.zero ? value<0 : value<=0))
      throw new Error(`${field.label}必须是${field.zero?"非负":"正"}有限数值。`);
    boundaries[field.key]=value*(1/field.factor);
  }
  // Explicit allowlist: concept targets, hardware dimensions and solver initials
  // cannot be copied from an engine-input draft into operating conditions.
  return {source_job_id:sourceJobId,operating_conditions:{boundaries,controls:{valve_openings:{}},control_targets:{}}};
}

function snapshot(task) {
  if(!task)return "";
  const source=task.source || {}, hardware=task.hardware || {}, nozzle=hardware.payload?.nozzle || {};
  return `<section class="fs-card"><h3>冻结硬件与来源</h3><dl class="fs-metadata"><dt>来源设计记录</dt><dd>${escape(source.job_id)} · v${escape(source.revision)}</dd><dt>来源结果 SHA-256</dt><dd><code>${escape(source.result_sha256)}</code></dd><dt>硬件 SHA-256</dt><dd><code>${escape(hardware.id)}</code></dd><dt>喷管喉部面积 / m²</dt><dd>${escape(nozzle.throat_area_m2??"—")}</dd><dt>面积比</dt><dd>${escape(nozzle.expansion_ratio??"—")}</dd></dl><p class="fine">硬件取自已完成设计记录；本页保存工况时不会重新定尺寸。概念设计参数草稿不会写入此快照。</p><details><summary>查看冻结硬件定义</summary><pre>${escape(JSON.stringify(hardware,null,2))}</pre></details></section>`;
}

export function renderFixedSteadyTask(task, form, {readOnly=false,busy=false}={}) {
  if(!task)return '<div class="fs-empty">选择已完成的概念设计记录，读取硬件快照与独立工况。</div>';
  const blockers=task.readiness?.blockers || [];
  const legacy=task.schema_version===1;
  const boundaryFields=readOnly ? displayedBoundaries(task) : FIXED_BOUNDARIES;
  const historical=legacy?'<p class="fs-notice">此为版本 1 历史定义。原有增压出口压力要求和管路压降预算按原文展示，均未采纳为新固定硬件任务的外部边界；查看不会迁移或改写此记录。</p>':"";
  const assumptions=task.legacy_design_assumptions ? `<section class="fs-card"><h3>来源设计假设 · 未采纳为运行边界</h3><p class="fine">前置增压出口压力要求与管路压降预算仅保留用于追溯。后续需要由固定部件模型匹配，或另行明确模型截断处的外部接口。</p><details><summary>查看来源假设</summary><pre>${escape(JSON.stringify(task.legacy_design_assumptions,null,2))}</pre></details></section>` : "";
  return `${snapshot(task)}${historical}<section class="fs-card"><h3>${readOnly?"已保存工况 · 只读":"独立运行工况"}</h3><p class="fine">此处只定义外部边界。预燃室温度、氧燃质量比和主室压力通常由工作点计算得到；允许上限与数值初值分别保存。</p><div class="fs-fields">${boundaryFields.map(field=>`<label>${field.label} / ${field.unit}<input type="number" step="any" min="0" data-fixed-boundary="${field.key}" value="${escape(form?.[field.key]??"")}" ${readOnly?"readonly":""} ${busy?"disabled":""}></label>`).join("")}</div><div class="fs-control-note"><b>阀门硬件与运行设定</b><p>固定的是阀门的尺寸与特性，开度属于可调运行设定。当前尚无已定义阀门、可调范围及饱和处理，因此暂不开放开度输入；这不表示固定硬件要求固定开度。</p><p>推力与整机 氧燃质量比 通常为输出。将其设为控制目标时，必须明确操纵量、调节机制及限位处理；指定转速也必须明确为外部边界，不能直接继承概念选型值。</p></div></section>${assumptions}<section class="fs-card fs-readiness" role="status"><h3>任务定义可保存 · 物理求解尚未开放</h3><p>任务、工况与通用数值接口已分开。发动机工作点计算仍需补齐以下能力：</p><ul>${blockers.map(item=>`<li><b>${escape(item.label || item.code)}</b>${item.detail?`：${escape(item.detail)}`:""}</li>`).join("") || "<li>固定硬件部件特性和独立稳态求解器。</li>"}</ul><button type="button" disabled title="需完成固定硬件稳态求解器后开放">固定硬件稳态求解 · 尚未实现</button><p class="fine">保存定义不会运行概念设计、反求预燃室氧燃质量比或生成性能结果。</p></section><div class="fs-details"><details><summary>数值初值 · 与运行边界分离</summary><pre>${escape(JSON.stringify(task.initial_guess||{},null,2))}</pre></details><details><summary>约束定义 · 与设计目标分离</summary><pre>${escape(JSON.stringify(task.constraints||{},null,2))}</pre></details></div>`;
}

export function mountFixedSteady(container,{api,getContext,notify=()=>{}}) {
  let projectId="",sourceId="",sources=[],records=[],selected=null,error="",busy=false,live=true,generation=0,loaded=false,pending=null;
  let cache=new Map(),checks=[],checkId="",checkError="",dataReport=null,dataError="";
  let trimView=null;
  const prefix=()=>`/projects/${encodeURIComponent(projectId)}/fixed-steady`;
  const entry=()=>cache.get(sourceId);
  const current=(token,id)=>live&&token===generation&&id===projectId&&getContext().project?.id===id;
  function syncContext(){
    const context=getContext(),id=context.project?.id || "";
    if(id!==projectId){++generation;projectId=id;sourceId="";sources=[];records=[];selected=null;error="";busy=false;loaded=false;pending=null;cache=new Map();checks=[];checkId="";checkError="";dataReport=null;dataError="";}
    const jobs=[...(context.jobs || [])];
    if(context.record && !jobs.some(job=>job.id===context.record.id))jobs.push(context.record);
    sources=jobs.filter(job=>job.status==="succeeded" && job.project_id===id && (!job.mode || job.mode==="concept_design"));
    if(!sourceId)sourceId=sources.find(job=>job.id===context.record?.id)?.id || sources[0]?.id || "";
  }
  function render(){
    if(!live)return;
    const task=selected?.task || entry()?.task,form=selected ? fixedSteadyForm(task) : entry()?.form;
    const roleTable=renderAnalysisParameterRoles(task?.parameter_roles || getContext().roleCatalog,"fixed_hardware_steady",{currentReference:!!selected&&!task?.parameter_roles});
    const shownSource=selected?.task?.source?.job_id || sourceId;
    const shownSources=sources.some(job=>job.id===shownSource)||!selected ? sources
      : [...sources,{id:shownSource,revision:task?.source?.revision,created:null}];
    const taskUrl=selected?`/api${prefix()}/tasks/${encodeURIComponent(selected.id)}`:"";
    const interfaces=task?`<div data-control-trim-host>${renderControlTrimPlaceholder()}</div>`+renderModelData(task.model_data,{templateUrl:task.model_data&&taskUrl?`${taskUrl}/data-template`:""})+(selected&&task.model_data?renderDataDeclarationCheck(dataReport,{busy,error:dataError}):"")+renderFrameworkChecks(checks,{taskId:selected?.id||"",baseUrl:taskUrl,selectedId:checkId,busy,error:checkError})+renderValveRequirements(task.valve_requirements):"";
    container.innerHTML=`<section class="fixed-steady-workspace" aria-label="固定硬件稳态任务"><div class="fs-intro"><div><span class="fs-kicker">FIXED HARDWARE / STEADY STATE</span><h2>固定硬件稳态任务</h2><p>从设计记录冻结硬件，单独定义工况，保存可追溯的分析任务。</p></div><span class="badge">数据接口与数值框架</span></div>${roleTable}<section class="fs-card fs-source"><label>硬件来源 · 已完成的概念设计<select data-fixed-source ${busy||selected?"disabled":""}><option value="">选择设计记录</option>${shownSources.map(job=>`<option value="${escape(job.id)}" ${shownSource===job.id?"selected":""}>v${escape(job.revision)} · ${escape(date(job.created))} · ${escape(job.id)}</option>`).join("")}</select></label><button type="button" data-fixed-action="refresh" ${busy?"disabled":""}>刷新定义记录</button></section>${!sources.length?'<p class="fs-notice">尚无已完成的概念设计记录。请先进入“概念设计”完成设计点计算。</p>':""}${error?`<p class="fs-error" role="alert">${escape(error)}</p>`:""}${busy&&!task?'<p role="status">正在读取任务定义…</p>':""}<section class="fs-history"><label>已保存任务<select data-fixed-record ${busy?"disabled":""}><option value="">新任务草稿</option>${records.map(record=>`<option value="${escape(record.id)}" ${selected?.id===record.id?"selected":""}>${escape(date(record.created))} · ${escape(record.id)}</option>`).join("")}</select></label>${selected?`<button type="button" data-fixed-action="draft" ${busy?"disabled":""}>返回新任务草稿</button><a class="fs-download" href="/api${prefix()}/tasks/${encodeURIComponent(selected.id)}/download" download>下载任务 JSON</a>`:""}</section>${selected?`<p class="fs-notice">正在查看已保存任务 ${escape(selected.id)}，所有字段只读。下方硬件与工况均来自该历史定义，修改草稿不会覆盖它。</p>`:""}${renderFixedSteadyTask(task,form,{readOnly:!!selected,busy})}${interfaces}${!selected&&task?`<div class="fs-save"><p>每次保存生成一条独立定义；概念设计方案和旧任务保持原样。</p><button type="button" class="primary" data-fixed-action="save" ${busy?"disabled":""}>${busy?"正在保存…":"保存固定硬件稳态任务"}</button></div>`:""}</section>`;
    const trimHost=container.querySelector?.("[data-control-trim-host]");
    if(trimHost){
      trimView ||= mountControlTrim(null,{api,notify,getContext:()=>({projectId:getContext().project?.id||"",taskId:selected?.id||"",live})});
      trimView.setContext({projectId,taskId:selected?.id||"",baseUrl:taskUrl},trimHost);
    } else trimView?.onLeave();
  }
  async function refresh(force=false){
    live=true;syncContext();
    if(!projectId){render();return;}
    if(pending)return pending;
    if(!force&&loaded&&(!sourceId||entry())){render();return;}
    const token=++generation,id=projectId,source=sourceId,path=prefix(),taskId=selected?.id;busy=true;error="";render();
    const work=(async()=>{
      try{
        const [list,setup,history]=await Promise.allSettled([
          api(`${path}/tasks`),source&&!entry()?api(`${path}/setup?source_job_id=${encodeURIComponent(source)}`):Promise.resolve(null),
          taskId?api(`${path}/tasks/${encodeURIComponent(taskId)}/framework-checks`):Promise.resolve(null),
        ]);
        if(!current(token,id)||source!==sourceId)return;
        if(list.status==="fulfilled")records=Array.isArray(list.value)?list.value:[];
        if(setup.status==="fulfilled"&&setup.value)cache.set(source,{task:clone(setup.value),form:fixedSteadyForm(setup.value)});
        if(taskId&&selected?.id===taskId){
          if(history.status==="fulfilled"){checks=Array.isArray(history.value)?history.value.filter(row=>row?.task_id===taskId):[];checkId=checks.some(row=>row.id===checkId)?checkId:checks[0]?.id||"";checkError="";}
          else checkError=`数值自检历史读取失败：${history.reason.message}`;
        }
        error=[list,setup].filter(result=>result.status==="rejected").map(result=>result.reason.message).join("；");
        loaded=list.status==="fulfilled";
      }catch(failure){if(current(token,id))error=failure.message;}
      finally{if(current(token,id)){busy=false;pending=null;render();}}
    })();
    pending=work;return work;
  }
  async function chooseSource(id){
    if(busy)return;
    ++generation;sourceId=id;selected=null;error="";loaded=false;pending=null;checks=[];checkId="";checkError="";dataReport=null;dataError="";
    await refresh();
  }
  async function chooseRecord(recordId){
    if(busy)return;
    if(!recordId){++generation;selected=null;error="";checks=[];checkId="";checkError="";dataReport=null;dataError="";render();return;}
    const token=++generation,id=projectId;busy=true;error="";render();
    try{
      const record=await api(`${prefix()}/tasks/${encodeURIComponent(recordId)}`);
      if(!current(token,id))return;
      selected=record;checks=[];checkId="";checkError="";dataReport=null;dataError="";render();
      await loadChecks(token,id,recordId);
    }catch(failure){if(current(token,id))error=failure.message;}
    finally{if(current(token,id)){busy=false;render();}}
  }
  async function save(){
    if(busy||selected||!entry())return;
    if(entry().task.schema_version!==2){error="当前模板仍为历史版本，请重启网页服务并刷新后建立新任务。";render();return;}
    let request;
    try{request=buildFixedSteadyRequest(sourceId,entry().form);}catch(failure){error=failure.message;render();return;}
    const token=++generation,id=projectId;busy=true;error="";render();
    try{
      const record=await api(`${prefix()}/tasks`,"POST",request);
      if(!current(token,id))return;
      records=[record,...records.filter(item=>item.id!==record.id)];selected=record;checks=[];checkId="";checkError="";dataReport=null;dataError="";
      notify("固定硬件稳态任务定义已保存，尚未执行物理求解。");
    }catch(failure){if(current(token,id))error=failure.message;}
    finally{if(current(token,id)){busy=false;render();}}
  }
  async function loadChecks(token,id,taskId){
    try{
      const rows=await api(`${prefix()}/tasks/${encodeURIComponent(taskId)}/framework-checks`);
      if(current(token,id)&&selected?.id===taskId){checks=Array.isArray(rows)?rows.filter(row=>row?.task_id===taskId):[];checkId=checks[0]?.id||"";checkError="";}
    }catch(failure){if(current(token,id)&&selected?.id===taskId)checkError=`数值自检历史读取失败：${failure.message}`;}
  }
  async function frameworkCheck(){
    if(busy||!selected)return;
    const token=++generation,id=projectId,taskId=selected.id;busy=true;checkError="";render();
    try{
      const record=await api(`${prefix()}/tasks/${encodeURIComponent(taskId)}/framework-checks`,"POST",{});
      if(!current(token,id)||selected?.id!==taskId)return;
      if(record?.task_id!==taskId)throw new Error("返回的自检记录与当前任务不匹配。");
      checks=[record,...checks.filter(row=>row.id!==record.id)];checkId=record.id;
      notify("数值框架自检记录已保存；发动机工作点仍需补齐部件数据与模型。");
    }catch(failure){if(current(token,id)&&selected?.id===taskId)checkError=failure.message;}
    finally{if(current(token,id)){busy=false;render();}}
  }
  async function checkDataFile(file){
    if(busy||!selected||!selected.task?.model_data||!file)return;
    const token=++generation,id=projectId,taskId=selected.id;busy=true;dataError="";dataReport=null;render();
    try{
      if(!Number.isFinite(file.size)||file.size<0||file.size>200000)throw new Error("请选择不超过 200000 字节的 JSON 登记文件。");
      const content=await file.text();
      if(!current(token,id)||selected?.id!==taskId)return;
      let body;try{body=JSON.parse(content);}catch{throw new Error("登记文件不是有效 JSON，请检查格式后重试。");}
      if(new TextEncoder().encode(JSON.stringify(body)).byteLength>200000)throw new Error("登记内容编码为 UTF-8 后超过 200000 字节，请缩减后重试。");
      const report=await api(`${prefix()}/tasks/${encodeURIComponent(taskId)}/data-check`,"POST",body);
      if(!current(token,id)||selected?.id!==taskId)return;
      if(report?.scope!=="data_declaration_only"||report.can_run!==false||report.adopted!==false)throw new Error("登记校核记录类型不匹配。");
      dataReport=report;
    }catch(failure){if(current(token,id)&&selected?.id===taskId)dataError=failure.message;}
    finally{if(current(token,id)){busy=false;render();}}
  }
  container.oninput=event=>{
    const key=event.target?.dataset?.fixedBoundary;
    if(!busy&&!selected&&entry()&&FIXED_BOUNDARIES.some(field=>field.key===key))entry().form[key]=event.target.value;
  };
  container.onchange=event=>{
    if(event.target?.dataset?.fixedSource!==undefined)return chooseSource(event.target.value);
    if(event.target?.dataset?.fixedRecord!==undefined)return chooseRecord(event.target.value);
    if(!busy&&event.target?.dataset?.fixedCheck!==undefined){checkId=event.target.value;render();}
    if(event.target?.dataset?.fixedDataFile!==undefined)return checkDataFile(event.target.files?.[0]);
  };
  container.onclick=event=>{
    const action=event.target.closest?.("[data-fixed-action]")?.dataset.fixedAction;
    if(busy)return;
    if(action==="save")return save();
    if(action==="framework-check")return frameworkCheck();
    if(action==="refresh")return refresh(true);
    if(action==="draft"){++generation;selected=null;error="";checks=[];checkId="";checkError="";dataReport=null;dataError="";render();}
  };
  return {refresh,sync(){if(live){syncContext();render();}},onLeave(){live=false;++generation;pending=null;busy=false;loaded=false;trimView?.onLeave();}};
}
