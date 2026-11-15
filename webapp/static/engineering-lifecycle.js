// Task views project one parameter document and immutable saved results.
import {numericalDisplayState} from "./convergence.js";
import {renderPreburnerRequirements} from "./preburner-design.js";
const escape=value=>String(value??"").replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const canonical=value=>Array.isArray(value)?value.map(canonical):value&&typeof value==="object"?Object.fromEntries(Object.keys(value).sort().map(k=>[k,canonical(value[k])])):value;
const same=(a,b)=>JSON.stringify(canonical(a))===JSON.stringify(canonical(b));
const viewLink=(view,label)=>`<button type="button" data-workspace-view="${escape(view)}">${escape(label)}</button>`;
const groupLink=(id,label)=>`<button type="button" data-setup-group="${escape(id)}">${escape(label)}</button>`;
const numericalLabel=evidence=>evidence?.status==="pass"?"完整数值收敛":evidence?.status==="fail"?"数值未收敛":"收敛证据待确认";
function sourceNumericalNotice(record){
  if(!record?.result)return "";
  const evidence=numericalDisplayState(record.result),passed=evidence.status==="pass";
  return `<p class="lifecycle-evidence ${passed?"":"notice warning"}">来源计算：${escape(evidence.label)}。${passed?"工程约束和部件能力仍需分别校核。":"当前状态与负荷仅供诊断和草案迭代，不能作为已确认的部件设计依据。"} ${viewLink("solver_diagnostics","查看收敛诊断")}</p>`;
}

export function designStartingInputs(defaults,preset,start="concept_heat"){
  if(!["concept_heat","cycle_budget","preset_geometry"].includes(start))throw new Error("请选择概略热负荷估算或沿用示例模型作为设计起点。");
  const inputs={...defaults,...preset};
  return start==="concept_heat"?{...inputs,cooling_bartz_mode:2}:start==="cycle_budget"?{...inputs,cooling_bartz_mode:0}:inputs;
}
export function componentForLocation(catalog,view,groupId){
  return catalog?.components?.find(c=>view==="overview"?c.groups.includes(groupId):c.views.includes(view))||null;
}
export function componentPendingPatch(component,groups,before={},after={},saved=before){
  const keys=new Set(groups.filter(g=>component?.groups.includes(g.id)).flatMap(g=>g.fields.filter(f=>!f.virtual&&!f.read_only).map(f=>f.key)));
  // Include both saved differences from the source and unsaved edits. An edit
  // that restores the source value must still replace the current saved value.
  return Object.fromEntries([...keys].filter(key=>Object.hasOwn(after,key)&&(!same(before[key],after[key])||!same(saved[key],after[key]))).map(key=>[key,after[key]]));
}
export function renderComponentContext({component,groups,views,project,draft,record,busy,view}){
  if(!component)return "";
  const patch=componentPendingPatch(component,groups,record?.inputs||project?.inputs,draft,project?.inputs),count=Object.keys(patch).length;
  const source=record?.status==="succeeded"&&(record.kind||"design")==="design";
  const sourceText=source?`来源设计点 v${record.revision} · ${record.id}`:"尚无来源设计点；可以先编辑部件草案。";
  const sourceNotice=sourceNumericalNotice(record);
  const tabs=component.groups.map(id=>{const group=groups.find(g=>g.id===id);return group?groupLink(id,group.title):"";}).join("")
    +component.views.map(id=>viewLink(id,views[id]?.[0]||id)).join("");
  const mode=component.impact==="component"?"这些初选参数用于部件尺寸与交接；当前常效率循环不会据此预测新的部件效率。":
    component.id==="cooling"||component.id==="chamber"?(draft?.cooling_bartz_mode===2?"当前采用概略热负荷：共享型面与参考壁温影响循环；通道及材料保留为部件草案，尚未用于冷却能力校核。":draft?.cooling_bartz_mode===1?"当前采用部件耦合：型面、通道和材料变化需要重新闭合循环。":"当前采用循环预算：保留部件草案，热工与压降由总体预算给定。"):
    "实体流路或阀门方案变化需要重新闭合循环。";
  return `<section class="component-workflow" aria-label="${escape(component.label)}工作区"><div class="lifecycle-heading"><div><b>${escape(component.label)}</b><p>${escape(component.description)}</p></div><span class="lifecycle-tag">${view==="overview"?"当前部件草案":"保存记录与局部研究"}</span></div><p class="fine lifecycle-source">${escape(sourceText)}</p>${sourceNotice}<nav class="component-workflow-tabs" aria-label="部件输入与结果">${tabs}</nav><p class="fine">${escape(mode)}</p><div class="lifecycle-actions"><button type="button" data-component-feedback="${escape(component.id)}" ${!source||!count||busy?"disabled":""}>保存本部件修改并进入整机复核${count?`（${count} 项）`:""}</button>${viewLink("design_requirements","查看部件设计要求")}${viewLink("integration","查看反馈与基准")}</div><p class="fine">反馈保存本部件的参数修改和来源工况；其他未保存编辑仍保留。计算完成后核对结果，确认输入不等于部件校核通过。</p></section>`;
}

const metric=(outputs,keys,scale=1)=>{
  for(const key of keys){const value=outputs?.[key];if(typeof value==="number"&&Number.isFinite(value))return (value/scale).toLocaleString("zh-CN",{maximumSignificantDigits:6});}
  return "未记录";
};
export function renderDesignRequirements(record){
  const calc=record?.result?.calculation;
  if(!calc)return `<section class="lifecycle-card"><h2>部件设计要求</h2><p>先完成总体设计点计算，再将保存的状态和负荷作为部件设计来源。</p>${viewLink("design_run","进入总体设计点计算")}</section>`;
  const components=calc.components||{},specs=[
    ["ox_pump","液氧泵","pump_geometry",[["体积流量 / m³·s⁻¹",["volume_flow_m3s"]],["压升需求 / MPa",["delta_p_Pa"],1e6],["轴功需求 / MW",["power_W"],1e6]]],
    ["fuel_pump","燃料泵","pump_geometry",[["体积流量 / m³·s⁻¹",["volume_flow_m3s"]],["压升需求 / MPa",["delta_p_Pa"],1e6],["轴功需求 / MW",["power_W"],1e6]]],
    ["ox_preburner_turbine","富氧侧涡轮","turbine_geometry",[["轴功 / MW",["turbine_power_W"],1e6]]],
    ["fuel_preburner_turbine","富燃侧涡轮","turbine_geometry",[["轴功 / MW",["turbine_power_W"],1e6]]],
    ["cooling_channel","再生冷却","cooling_workspace",[["吸热量 / MW",["heat_load_W"],1e6]]],
  ];
  const cards=specs.map(([id,label,view,metrics])=>`<article class="lifecycle-card"><h3>${label}</h3><dl>${metrics.map(([title,keys,scale])=>`<div><dt>${title}</dt><dd>${metric(components[id]?.outputs,keys,scale)}</dd></div>`).join("")}</dl>${viewLink(view,"进入部件工作区")}</article>`).join("");
  return `<section class="engineering-lifecycle"><h2>总体设计点 → 部件设计要求</h2><p>来源：方案 v${escape(record.revision)} · ${escape(record.id)}。以下需求来自该记录的状态与负荷，不表示所选硬件已经具备对应性能。</p>${sourceNumericalNotice(record)}<div class="lifecycle-grid">${cards}<article class="lifecycle-card"><h3>推力室、喷管与喷注器</h3><p>使用同一设计点的入口状态、流量、热化学及膨胀条件开展设计。</p>${viewLink("chamber_design","推力室交接")}${viewLink("nozzle_design","喷管交接")}${viewLink("injector_design","喷注器交接")}</article></div>${renderPreburnerRequirements(record)}<p class="fine">缺少保存值时显示“未记录”，不以当前草稿或默认值补齐。</p>${viewLink("states","查看完整流路状态")}${viewLink("integration","进入整机集成与基准确认")}</section>`;
}

function feedbackChange(change,snapshot){
  const previous=snapshot.previous_saved_values||{},persisted=Object.hasOwn(previous,change.key);
  const from=persisted?previous[change.key]:change.before;
  const sourceNote=persisted&&!same(from,change.before)?`<p class="fine">来源设计点：${escape(JSON.stringify(change.before))}</p>`:"";
  return `<div><dt>${escape(change.key)}</dt><dd>${escape(JSON.stringify(from))} → ${escape(JSON.stringify(change.after))}${sourceNote}</dd></div>`;
}
export function renderIntegration(model,{project,record,dirty,busy=false,jobs=[]}={}){
  const source=record?.status==="succeeded"&&(record.kind||"design")==="design";
  const baseline=model.baselines?.find(b=>b.source_job_id===record?.id);
  const stale=source&&!same(project?.inputs,record.inputs);
  const sources=jobs.filter(job=>job.status==="succeeded"&&(job.kind||"design")==="design");
  const sourceChoice=`<label class="lifecycle-source-select">来源设计点<select data-engineering-source ${busy?"disabled":""}><option value="">选择已完成记录</option>${sources.map(job=>`<option value="${escape(job.id)}" ${job.id===record?.id?"selected":""}>v${escape(job.revision)} · ${escape(job.id)}</option>`).join("")}</select></label>`;
  const iterations=(model.iterations||[]).map(row=>`<tr><td>${escape(row.snapshot.component_label)}</td><td>v${escape(row.snapshot.source_revision)} → v${escape(row.target_revision)}</td><td>${row.snapshot.changes.length} 项</td><td>${row.status==="recomputed"?"已完成复算 · 待工程校核":row.status==="recomputed_with_changes"?"已纳入后续整机复算 · 待工程校核":"待整机复算"}${row.review_job_id?`<p class="fine">${escape(numericalLabel(row.numerical_review))}</p>`:""}</td><td>${row.review_job_id?`<button type="button" data-engineering-record="${escape(row.review_job_id)}">查看复算记录</button>`:""}<details><summary>查看修改</summary><dl>${row.snapshot.changes.map(c=>feedbackChange(c,row.snapshot)).join("")}</dl></details></td></tr>`).join("");
  const baselines=(model.baselines||[]).map(row=>`<article class="lifecycle-card"><div class="lifecycle-heading"><h3>候选基准 · v${escape(row.snapshot.payload.source_revision)}</h3><span class="lifecycle-tag">${row.integrity?"快照已保存":"快照校验异常"}</span></div><p class="fine lifecycle-source">来源 ${escape(row.source_job_id)}</p><p>来源计算：${escape(numericalLabel(row.snapshot.payload.numerical_evidence))}。</p><p>固定硬件名义工况复核：尚未开放。候选快照不代表可运行的整机基准。</p><details><summary>模型接入与缺项（${row.snapshot.blockers.length}）</summary><ul>${row.snapshot.blockers.map(b=>`<li><b>${escape(b.label)}</b>：${escape(b.detail)}</li>`).join("")}</ul></details><div class="lifecycle-actions"><a href="/api/projects/${encodeURIComponent(project.id)}/engineering/baselines/${encodeURIComponent(row.id)}/download">导出候选快照</a><button type="button" data-baseline-record="${escape(row.source_job_id)}" data-baseline-view="fixed_steady" ${row.integrity?"":"disabled"}>以此来源定义稳态任务</button><button type="button" data-baseline-record="${escape(row.source_job_id)}" data-baseline-view="transient_operations" ${row.integrity?"":"disabled"}>以此来源查看瞬态研究</button></div></article>`).join("");
  return `<section class="engineering-lifecycle"><div class="lifecycle-flow" aria-label="设计迭代过程"><span>总体设计点</span><span>部件设计</span><span>整机反馈复算</span><span>候选硬件与模型</span><span>固定硬件复核</span></div><section class="lifecycle-card"><h2>整机集成与基准确认</h2>${sourceChoice}${sourceNumericalNotice(record)}<p>当前方案 v${escape(project?.revision)}；${source?`所选设计点 v${escape(record.revision)}`:"尚未选择已完成设计点"}。${dirty?"有未保存编辑。":""}</p>${stale?'<p class="notice warning">所选记录与当前输入不同。反馈复算使用当前方案；保存候选基准仅引用所选记录，不混入当前草稿。</p>':""}<div class="lifecycle-actions">${viewLink("design_run","复算当前总体设计点")}${viewLink("results_overview","核对总体性能与约束")}<button type="button" data-capture-baseline ${!source||busy||baseline?"disabled":""}>${baseline?"所选记录已保存候选基准":"将所选记录保存为候选基准"}</button></div><p class="fine">当前复算采用总体设计模型。固定硬件名义工况复核需要完整部件特性及整机运行方程，两者分别记录。</p></section><section class="lifecycle-card"><h3>部件反馈记录</h3>${iterations?`<div class="table-wrap"><table><thead><tr><th>部件</th><th>来源与反馈版本</th><th>修改</th><th>状态</th><th>记录</th></tr></thead><tbody>${iterations}</tbody></table></div>`:"<p>在部件工作区修改参数后，使用“保存本部件修改并进入整机复核”建立反馈记录。</p>"}</section><h3>候选硬件与模型快照</h3>${baselines||'<p class="fine">尚无候选快照。先选择已完成的总体设计记录，再保存其几何、模型证据与来源。</p>'}<section class="lifecycle-card"><h3>运行分析准备</h3><p>稳态任务需要部件通流、压升或压比、效率及控制关系。瞬态还需要容积、惯量、热容量、执行机构和物理初始状态。</p><p>现有局部节流与热态瞬态研究保留各自的模型范围；当前不能把候选快照确认为完整整机运行基准。</p>${viewLink("steady_operations","稳态任务")}${viewLink("transient_operations","瞬态任务")}</section></section>`;
}

export function mountEngineeringLifecycle(host,{api,getContext,notify,onSelectRecord,onOpenView}){
  let sequence=0,visible=false,busy=false,model=null,modelProject=null;
  const valid=(token,projectId)=>visible&&token===sequence&&getContext().project?.id===projectId;
  function paint(){if(model&&visible&&modelProject===getContext().project?.id)host.innerHTML=renderIntegration(model,{...getContext(),busy});}
  async function refresh(){
    visible=true;const token=++sequence,projectId=getContext().project?.id;if(!projectId)return;
    host.innerHTML='<p role="status">正在读取部件反馈与候选基准…</p>';
    try{const next=await api(`/projects/${encodeURIComponent(projectId)}/engineering`);if(valid(token,projectId)){model=next;modelProject=projectId;paint();}}
    catch(error){if(valid(token,projectId))host.innerHTML=`<p class="notice error" role="alert">${escape(error.message)}</p>`;}
  }
  const click=async event=>{
    const button=event.target.closest?.('[data-capture-baseline],[data-engineering-record],[data-baseline-record]');
    if(!button||!host.contains(button)||busy||button.disabled)return;
    const {project,record}=getContext(),token=sequence;busy=true;paint();
    try{
      if(button.hasAttribute('data-capture-baseline')){
        if(!record?.id)throw new Error('请选择已完成的来源记录。');
        await api(`/projects/${encodeURIComponent(project.id)}/engineering/baselines`,'POST',{source_job_id:record.id});
        if(valid(token,project.id)){notify('已保存候选快照；固定硬件复核状态保持待接入。');await refresh();}
      }else{
        const selected=button.dataset.baselineRecord||button.dataset.engineeringRecord;
        const loaded=await onSelectRecord(selected);
        // Selecting a record refreshes this host, so its fetch sequence changes.
        if(loaded&&visible&&getContext().project?.id===project.id&&getContext().record?.id===selected)await onOpenView(button.dataset.baselineView||'results_overview');
      }
    }catch(error){if(valid(token,project.id))notify(error.message);}
    finally{busy=false;paint();}
  };
  host.addEventListener('click',click);
  const change=async event=>{
    if(!event.target.matches?.('[data-engineering-source]')||!event.target.value||busy)return;
    const projectId=getContext().project?.id;
    try{await onSelectRecord(event.target.value);}catch(error){if(visible&&getContext().project?.id===projectId)notify(error.message);}
  };
  host.addEventListener('change',change);
  return {refresh,sync:paint,onLeave(){visible=false;sequence++;},dispose(){visible=false;sequence++;host.removeEventListener('click',click);host.removeEventListener('change',change);}};
}
