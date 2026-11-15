// Control task definitions and dimensionless nested checks stay separate from
// engine results and from the legacy design-matching throttle scan.
import {residualPlot} from "./convergence.js";
const escape=value=>String(value??"").replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const list=value=>Array.isArray(value)?value:[];
const finite=value=>typeof value==="number"&&Number.isFinite(value);
const positive=value=>String(value??"").trim()!==""&&Number.isFinite(Number(value))&&Number(value)>0;
const fmt=value=>finite(value)?Number(value.toPrecision(6)).toString():"—";
const date=value=>value?new Date(value*1000).toLocaleString("zh-CN",{hour12:false}):"时间未记录";
const clone=value=>JSON.parse(JSON.stringify(value));
export const TRIM_SCENARIOS=Object.freeze([
  {id:"reachable",label:"可达目标"},
  {id:"bound_limited",label:"受控制边界限制"},
  {id:"inner_failure",label:"内层数值失败"},
  {id:"rank_deficient",label:"控制方向不独立"},
]);
export const TRIM_OPTIONAL_TARGETS=Object.freeze([
  {name:"overall_of",label:"整机 氧燃质量比",unit:"1",tolerance:"0.01"},
  {name:"ox_preburner_temperature",label:"富氧预燃室出口温度",unit:"K",tolerance:"1"},
  {name:"fuel_preburner_temperature",label:"富燃预燃室出口温度",unit:"K",tolerance:"1"},
]);

export function controlTrimForm(contract) {
  return {thrust_percent:String(contract?.defaults?.thrust_percent??100),thrust_relative_tolerance:String(contract?.defaults?.thrust_relative_tolerance??.001),
    additional_targets:Object.fromEntries(TRIM_OPTIONAL_TARGETS.map(spec=>[spec.name,{enabled:false,value:"",tolerance:spec.tolerance}])),
    controls:Object.fromEntries(list(contract?.available_controls).map(spec=>[spec.valve_id,{enabled:false,lower:String(spec.lower??""),upper:String(spec.upper??""),initial:String(spec.initial??"")}]))};
}
const number=(value,label)=>{
  const result=typeof value==="string"&&value.trim()===""?NaN:Number(value);
  if(!Number.isFinite(result))throw new Error(`${label}必须是有限数值。`);
  return result;
};
export function buildControlTrimRequest(form,contract) {
  const thrust_percent=number(form?.thrust_percent,"目标推力百分比"),thrust_relative_tolerance=number(form?.thrust_relative_tolerance,"推力相对容差");
  if(thrust_percent<=0)throw new Error("目标推力百分比必须大于零。");
  if(thrust_relative_tolerance<=0||thrust_relative_tolerance>=1)throw new Error("推力相对容差必须大于 0 且小于 1。");
  const additional_targets={};
  for(const spec of TRIM_OPTIONAL_TARGETS){
    const target=form?.additional_targets?.[spec.name];if(!target?.enabled)continue;
    const value=number(target.value,spec.label),tolerance=number(target.tolerance,`${spec.label}容差`);
    if(value<=0||tolerance<=0)throw new Error(`${spec.label}及其绝对容差必须大于零。`);
    additional_targets[spec.name]={value,tolerance};
  }
  const controls=[];
  for(const [valve_id,control] of Object.entries(form?.controls||{})){
    if(!control.enabled)continue;
    const registered=list(contract?.available_controls).find(spec=>spec.valve_id===valve_id);
    if(!registered)throw new Error(`阀门 ${valve_id} 未在当前硬件中登记。`);
    const lower=number(control.lower,"开度下限"),upper=number(control.upper,"开度上限"),initial=number(control.initial,"开度初值");
    if(!finite(registered.lower)||!finite(registered.upper)||lower<registered.lower||upper>registered.upper||lower>=upper||initial<lower||initial>upper)
      throw new Error(`阀门 ${valve_id} 的范围和初值必须在已登记边界内，且下限小于上限。`);
    controls.push({valve_id,lower,upper,initial});
  }
  return {thrust_percent,thrust_relative_tolerance,additional_targets,controls};
}

function definitionForm(definition,contract) {
  const form=controlTrimForm(contract);
  form.thrust_percent=String(definition.thrust_percent??"");form.thrust_relative_tolerance=String(definition.thrust_relative_tolerance??"");
  for(const spec of TRIM_OPTIONAL_TARGETS){
    const target=definition.additional_targets?.[spec.name];
    if(target)form.additional_targets[spec.name]={enabled:true,value:String(target.value??""),tolerance:String(target.tolerance??"")};
  }
  form.controls=Object.fromEntries(list(definition.controls).map(control=>[control.valve_id,{enabled:true,lower:String(control.lower??""),upper:String(control.upper??""),initial:String(control.initial??"")}]));
  return form;
}

export function renderControlTrimDefinition(contract,form,{definition=null,busy=false}={}) {
  const readOnly=!!definition,reference=definition?.reference_thrust||contract?.reference_thrust||{};
  if(definition&&(definition.scope!=="task_definition_only"||definition.mode!=="fixed_hardware_steady_trim"))
    return '<p class="fs-error">配平定义类型不匹配，未将此记录作为配平任务展示。</p>';
  const disabled=readOnly||busy?"disabled":"",targetCount=1+TRIM_OPTIONAL_TARGETS.filter(spec=>form.additional_targets?.[spec.name]?.enabled).length;
  const controls=readOnly?list(definition.controls):list(contract?.available_controls),controlCount=Object.values(form.controls||{}).filter(control=>control.enabled).length;
  const blockers=list(definition?.readiness?.blockers||contract?.readiness?.blockers);
  return `<div class="ct-definition"><p class="fs-notice">百分比基准取自来源设计点已保存的计算推力输出，尚未验证为固定硬件额定点。此处保存控制意图，发动机工作点求解仍未开放。</p><dl class="fs-metadata ct-reference"><dt>${escape(reference.label||"来源设计点计算推力")}</dt><dd>${fmt(finite(reference.value_N)?reference.value_N/1000:null)} kN</dd><dt>基准环境压力</dt><dd>${fmt(finite(reference.ambient_pressure_Pa)?reference.ambient_pressure_Pa/1e6:null)} MPa</dd><dt>来源字段</dt><dd><code>${escape(reference.source_path||"未记录")}</code></dd></dl><div class="fs-fields"><label>目标推力 / 基准的 %<input type="number" step="any" min="0" data-trim-field="thrust_percent" value="${escape(form.thrust_percent)}" ${disabled}></label><label>推力相对容差 / 1<input type="number" step="any" min="0" max="1" data-trim-field="thrust_relative_tolerance" value="${escape(form.thrust_relative_tolerance)}" ${disabled}></label><div class="ct-target-value"><span>目标推力（定义值）</span><b>${fmt(finite(reference.value_N)&&positive(form.thrust_percent)?reference.value_N*Number(form.thrust_percent)/100000:null)} kN</b></div></div><h4>额外等式控制目标 · 显式选择</h4><p class="fine">预燃室温度默认作为工作点输出。勾选后才建立温度控制目标；允许上限继续保留在固定任务约束中。</p><div class="ct-targets">${TRIM_OPTIONAL_TARGETS.map(spec=>{
    const target=form.additional_targets?.[spec.name]||{};
    return `<div class="ct-target-row"><label class="ct-checkbox"><input type="checkbox" data-trim-target="${spec.name}" ${target.enabled?"checked":""} ${disabled}>${escape(spec.label)}</label><label>目标值 / ${escape(spec.unit)}<input type="number" step="any" min="0" data-trim-target-value="${spec.name}" value="${escape(target.value??"")}" ${disabled||!target.enabled?"disabled":""}></label><label>绝对容差 / ${escape(spec.unit)}<input type="number" step="any" min="0" data-trim-target-tolerance="${spec.name}" value="${escape(target.tolerance??"")}" ${disabled||!target.enabled?"disabled":""}></label></div>`;
  }).join("")}</div><h4>允许操纵的阀门</h4><p class="fine">阀门尺寸与特性保持冻结，只允许在登记范围内调整开度。不会根据分流支路自动创建阀门。</p>${controls.length?`<div class="fs-table-wrap"><table class="fs-table"><thead><tr><th>允许操纵</th><th>阀门</th><th>开度下限 / 1</th><th>开度上限 / 1</th><th>开度初值 / 1</th></tr></thead><tbody>${controls.map(spec=>{
    const control=form.controls?.[spec.valve_id]||{},fields=["lower","upper","initial"];
    return `<tr><td><input type="checkbox" aria-label="允许操纵 ${escape(spec.label||spec.valve_id)}" data-trim-control="${escape(spec.valve_id)}" ${control.enabled?"checked":""} ${disabled}></td><th>${escape(spec.label||spec.valve_id)}<small>${escape(spec.valve_id)}</small></th>${fields.map(field=>`<td><input type="number" step="any" aria-label="${escape(spec.valve_id)} ${field}" data-trim-valve="${escape(spec.valve_id)}" data-trim-bound="${field}" value="${escape(control[field]??"")}" ${disabled||!control.enabled?"disabled":""}></td>`).join("")}</tr>`;
  }).join("")}</tbody></table></div>`:'<p class="fs-empty">尚未登记可操纵阀门、特性和开度范围。阀门表为空，待补数据。</p>'}<div class="ct-dof" role="status"><span>等式目标 <b>${targetCount}</b></span><span>可调控制量 <b>${controlCount}</b></span><span>数量检查 <b>${controlCount>=targetCount?"数量足够，尚不能证明独立":"控制量不足"}</b></span><span>独立性 <b>尚未验证</b></span></div><p class="fine">数量检查仅为必要条件；控制响应的独立性与可达性需要已验证的内层模型。不会默认推力、氧燃质量比 与两个温度目标均可满足。</p>${blockers.length?`<details><summary>查看真实配平阻塞项</summary><ul class="fs-warnings">${blockers.map(item=>`<li><b>${escape(item.label||item.code)}</b>${item.detail?`：${escape(item.detail)}`:""}</li>`).join("")}</ul></details>`:""}<div class="ct-actions">${!readOnly?`<button type="button" class="primary" data-trim-action="save" ${busy?"disabled":""}>保存独立配平定义</button>`:""}<button type="button" disabled title="需先接入并验证固定硬件内层模型及部件数据">发动机稳态配平 · 尚未开放</button></div><p class="ct-no-results">尚无发动机配平结果；没有生成推力、温度或真实阀位曲线。</p></div>`;
}

// Linear axes retain signs. Missing values break lines; bounds are shown only
// when recorded by the backend, not inferred from the plotted values.
export function trimControlPlot(points,specs) {
  const series=list(specs).map((spec,index)=>({...spec,color:index%4,points:list(points).map(point=>({x:point?.iteration,y:point?.controls?.[spec.name]}))}));
  const valid=series.flatMap(item=>item.points).filter(point=>finite(point.x)&&point.x>=0&&finite(point.y));
  if(!valid.length)return '<p class="cv-empty">没有可用操纵量轨迹；缺失值不补零。</p>';
  const xmin=Math.min(...valid.map(point=>point.x)),xmax=Math.max(...valid.map(point=>point.x));
  const values=[...valid.map(point=>point.y),...series.flatMap(spec=>[spec.lower,spec.upper]).filter(finite)];
  let ymin=Math.min(...values),ymax=Math.max(...values);
  const pad=(ymax-ymin)*.08||1;ymin-=pad;ymax+=pad;
  const left=70,right=550,top=32,bottom=228;
  const x=value=>xmin===xmax?(left+right)/2:left+(value-xmin)/(xmax-xmin)*(right-left),y=value=>bottom-(value-ymin)/(ymax-ymin)*(bottom-top);
  let missing=0;
  const paths=series.map(spec=>{
    let d="",previous=null;
    const marks=spec.points.map(point=>{
      if(!finite(point.x)||point.x<0||!finite(point.y)){previous=null;missing++;return "";}
      d+=`${previous!==null&&point.x>previous?"L":"M"}${x(point.x).toFixed(3)},${y(point.y).toFixed(3)} `;previous=point.x;
      return `<circle class="cv-mark cv-series-${spec.color}" cx="${x(point.x)}" cy="${y(point.y)}" r="3"><title>${escape(spec.label||spec.name)} · 接受步 ${fmt(point.x)} · ${fmt(point.y)}</title></circle>`;
    }).join("");
    const bounds=[["下限",spec.lower],["上限",spec.upper]].filter(([,value])=>finite(value)).map(([label,value])=>`<line class="ct-bound cv-line cv-series-${spec.color}" x1="${left}" x2="${right}" y1="${y(value)}" y2="${y(value)}"><title>${escape(spec.label||spec.name)} ${label} ${fmt(value)}</title></line>`).join("");
    return `${bounds}<path class="cv-line cv-series-${spec.color}" d="${d.trim()}"/>${marks}`;
  }).join("");
  const yticks=Array.from({length:5},(_,i)=>ymin+(ymax-ymin)*i/4),xticks=[...new Set([xmin,Math.round((xmin+xmax)/2),xmax])];
  return `<figure class="cv-chart"><figcaption>无量纲操纵量与已登记边界</figcaption><svg class="cv-plot" viewBox="0 0 580 290" role="img" aria-label="无量纲操纵量轨迹，不代表发动机阀位"><title>无量纲操纵量与边界</title><text class="cv-tick" x="70" y="17">操纵量 / 1 · 线性轴</text>${yticks.map(t=>`<line class="cv-grid" x1="${left}" x2="${right}" y1="${y(t)}" y2="${y(t)}"/><text class="cv-tick" x="60" y="${y(t)+4}" text-anchor="end">${fmt(t)}</text>`).join("")}${paths}${xticks.map(t=>`<text class="cv-tick" x="${x(t)}" y="255" text-anchor="middle">${fmt(t)}</text>`).join("")}<text class="cv-tick" x="310" y="283" text-anchor="middle">外层接受迭代步</text></svg><div class="cv-legend">${series.map(spec=>`<span class="cv-legend-${spec.color}">${escape(spec.label||spec.name)} · [${fmt(spec.lower)}, ${fmt(spec.upper)}]</span>`).join("")}</div><p class="cv-chart-note">虚线为保存的上下限；缺失 / 无效值 ${missing} 个，保留断点。</p></figure>`;
}

export function renderControlTrimCheck(report,{downloadUrl=""}={}) {
  if(!report)return '<p class="fs-empty">尚无双层自检记录。运行下方无量纲示例后，显示实际保存的迭代曲线；不生成发动机结果。</p>';
  if(report.analysis_type!=="control_trim_framework_check"||report.scope!=="numerical_framework_only"||report.is_engine_result!==false)
    return '<p class="fs-error" role="alert">双层自检记录类型不匹配，未展示曲线。</p>';
  const points=list(report.points),controls=list(report.controls),targets=list(report.targets),evaluations=list(report.evaluations);
  const status={converged:"无量纲目标已满足",blocked:"自检被阻塞",numerical_failure:"数值失败（不能据此判定目标不可达）",target_not_met:"尚未找到满足目标的点（可达性未定）",target_unreachable:report.evidence?"无量纲目标在所设控制范围内不可达（有边界证据）":"不可达结论缺少证据，需复核"}[report.status]||"状态未识别";
  const acceptedIds=new Set(points.map(point=>point?.inner_evaluation_id));
  const dof=report.dof||{},independence={not_checked:"尚未检查",full_row_rank:"当前采样点控制响应独立",rank_deficient:"当前采样点控制方向不独立"}[dof.independence]||"未记录";
  const outerSeries=[{label:"最大归一化误差",points:points.map(point=>({x:point?.iteration,y:point?.residual_norm}))},...targets.map(target=>({label:target.label||target.name,points:points.map(point=>({x:point?.iteration,y:finite(point?.residuals?.[target.name])?Math.abs(point.residuals[target.name]):null}))}))];
  const phases={initial:"初始点",sensitivity:"灵敏度探针",trial:"试探点"};
  const innerSeries=evaluations.map(evaluation=>({label:`求值 ${evaluation.id} · ${phases[evaluation.phase]||evaluation.phase}`,points:list(evaluation.inner_points).map(point=>({x:point?.iteration,y:point?.residual_norm}))}));
  return `<div class="ct-check-report" data-report-scope="numerical_framework_only"><div class="fs-check-summary"><strong>${escape(status)}</strong>${downloadUrl?`<a class="fs-download" href="${escape(downloadUrl)}" download>下载双层自检 JSON</a>`:""}</div><p class="fs-notice">仅为无量纲代数模型的外层调整与内层迭代。这里的操纵量、输出和控制边界均不代表发动机；不使用上方推力百分比，也不证明真实内层模型已经稳定。</p><p class="fine">${escape(report.message||"")} 终止原因：${escape(report.reason||"未记录")} · 模型 ${escape(report.model_id||"—")} · 内层调用 ${fmt(report.nfev)} 次</p><div class="ct-dof"><span>目标 <b>${fmt(dof.target_count)}</b></span><span>控制量 <b>${fmt(dof.control_count)}</b></span><span>局部响应秩 <b>${fmt(dof.rank)}</b></span><span><b>${escape(independence)}</b></span></div><p class="fine">秩检查位置：外层接受步 ${fmt(dof.checked_at_iteration)}，内层调用 ${fmt(dof.inner_evaluation_id)}。局部响应秩与数量检查不等同于整个控制范围的可达性证明。</p>${points.length?`<div class="fs-check-charts">${residualPlot({title:"外层接受步 · 目标误差 / 验收容差",series:outerSeries,tolerance:1,unit:"1",xLabel:"外层接受步"})}${trimControlPlot(points,controls)}</div>`:'<p class="fs-empty">没有外层接受步，未生成外层误差和操纵量曲线。</p>'}${evaluations.some(evaluation=>list(evaluation.inner_points).length)?residualPlot({title:"各次内层调用 · 归一化残差",series:innerSeries,tolerance:1,unit:"1",xLabel:"该次内层接受步"}):'<p class="fs-empty">没有内层迭代轨迹，未生成内层曲线。</p>'}<details class="ct-ledger" open><summary>内层调用记录 · 探针、接受与失败分开</summary><div class="fs-table-wrap"><table class="fs-table"><thead><tr><th>调用</th><th>用途</th><th>操纵量 / 1</th><th>是否外层接受点</th><th>内层状态</th><th>内层迭代</th><th>最终归一化残差</th><th>错误</th></tr></thead><tbody>${evaluations.map(evaluation=>`<tr><td>${fmt(evaluation.id)}</td><td>${escape(phases[evaluation.phase]||evaluation.phase)}</td><td>${controls.map(spec=>`${escape(spec.label||spec.name)}: ${fmt(evaluation.controls?.[spec.name])}`).join("<br>")}</td><td>${acceptedIds.has(evaluation.id)?"已接受":evaluation.phase==="sensitivity"?"探针，不计入接受步":"未接受"}</td><td>${escape(evaluation.inner_status||evaluation.status||"未记录")}</td><td>${fmt(evaluation.inner_iterations)}</td><td>${fmt(evaluation.inner_residual_norm)}</td><td>${escape(evaluation.error||"—")}</td></tr>`).join("")||'<tr><td colspan="8">未记录内层调用。</td></tr>'}</tbody></table></div></details>${points.length?`<details><summary>外层接受步与原始目标值</summary><div class="fs-table-wrap"><table class="fs-table"><thead><tr><th>接受步</th>${targets.map(target=>`<th>${escape(target.label||target.name)} 输出 / 1</th><th>目标 / 1</th><th>有符号误差 / 容差</th>`).join("")}<th>命中的控制边界</th></tr></thead><tbody>${points.map(point=>`<tr><td>${fmt(point.iteration)}</td>${targets.map(target=>`<td>${fmt(point.outputs?.[target.name])}</td><td>${fmt(target.value)}</td><td>${fmt(point.residuals?.[target.name])}</td>`).join("")}<td>${list(point.active_bounds).map(bound=>`${escape(bound.name)} · ${bound.bound==="lower"?"下限":bound.bound==="upper"?"上限":escape(bound.bound)}`).join("；")||"无记录"}</td></tr>`).join("")}</tbody></table></div></details>`:""}${report.evidence?`<details><summary>边界结论证据（仅当前代数模型）</summary><pre>${escape(JSON.stringify(report.evidence,null,2))}</pre></details>`:""}${list(report.limitations).length?`<ul class="fs-warnings">${list(report.limitations).map(text=>`<li>${escape(text)}</li>`).join("")}</ul>`:""}<details><summary>查看完整自检记录</summary><pre>${escape(JSON.stringify(report,null,2))}</pre></details></div>`;
}

export function renderControlTrimPlaceholder() {
  return '<section class="fs-card ct-workspace"><h3>按推力百分比的稳态控制配平</h3><p class="fs-notice">先保存并选择固定硬件稳态任务，再单独定义配平目标和允许操纵的阀门。</p></section>';
}

export function mountControlTrim(initialContainer,{api,notify=()=>{},getContext=null}={}) {
  let host=initialContainer,context={projectId:"",taskId:"",baseUrl:""},generation=0,live=true;
  let contract=null,form=null,definitions=[],selectedId="",checks=[],checkId="",scenario="reachable",error="",busy=false,loaded=false,pending=null;
  const active=token=>{
    const external=getContext?.();
    return live&&token===generation&&(!external||(external.live!==false&&external.projectId===context.projectId&&external.taskId===context.taskId));
  };
  const path=suffix=>`${context.baseUrl.replace(/^\/api(?=\/)/,"")}/trim/${suffix}`;
  const eligible=rows=>list(rows).filter(row=>row?.task_id===context.taskId);
  function render(){
    if(!host||!live)return;
    if(!context.taskId){host.innerHTML=renderControlTrimPlaceholder();return;}
    const selected=definitions.find(row=>row.id===selectedId),definition=selected?.definition;
    const shownForm=definition?definitionForm(definition,contract):form;
    const check=checks.find(row=>row.id===checkId);
    host.innerHTML=`<section class="fs-card ct-workspace" aria-label="稳态控制配平定义"><div class="fs-check-heading"><div><span class="fs-kicker">STEADY CONTROL TRIM</span><h3>按推力百分比的稳态控制配平</h3></div><button type="button" data-trim-action="refresh" ${busy?"disabled":""}>刷新配平记录</button></div><p class="fine">当前固定任务：${escape(context.taskId)}。定义目标 → 调整明确的阀位 → 固定硬件内层求解 → 检查误差和边界。</p>${error?`<p class="fs-error" role="alert">${escape(error)}</p>`:""}<div class="fs-history ct-history"><label>配平定义历史<select data-trim-definition ${busy?"disabled":""}><option value="">新配平草稿</option>${definitions.map(row=>`<option value="${escape(row.id)}" ${selectedId===row.id?"selected":""}>${escape(date(row.created))} · ${escape(row.id)}</option>`).join("")}</select></label>${selected?`<a class="fs-download" href="${escape(context.baseUrl)}/trim/definitions/download?trim_id=${encodeURIComponent(selected.id)}" download>下载配平定义 JSON</a>`:""}</div>${selected?'<p class="fs-notice">正在查看已保存配平定义，目标和阀位范围只读。返回新配平草稿不会覆盖历史记录。</p>':""}${(definition||contract)&&shownForm?renderControlTrimDefinition(contract,shownForm,{definition,busy}):'<p class="fs-empty">正在读取配平定义接口；尚无发动机求解结果。</p>'}</section><section class="fs-card ct-self-check" aria-label="无量纲双层数值自检"><div class="fs-check-heading"><div><span class="fs-kicker">DIMENSIONLESS NESTED CHECK</span><h3>双层迭代数值自检 · 非发动机结果</h3></div></div><p class="fine">独立检查外层调整、控制边界、内层失败和控制独立性。示例不读取发动机目标、硬件或工况，自检成功也不会开放发动机配平。</p><div class="ct-demo-controls"><label>固定示例<select data-trim-scenario ${busy?"disabled":""}>${TRIM_SCENARIOS.map(item=>`<option value="${item.id}" ${scenario===item.id?"selected":""}>${escape(item.label)}</option>`).join("")}</select></label><button type="button" data-trim-action="check" ${busy?"disabled":""}>运行双层数值自检</button></div><label class="fs-check-select">已保存双层自检<select data-trim-check ${busy?"disabled":""}><option value="">选择记录</option>${checks.map(row=>`<option value="${escape(row.id)}" ${checkId===row.id?"selected":""}>${escape(date(row.created))} · ${escape(row.id)}</option>`).join("")}</select></label>${renderControlTrimCheck(check?.report,{downloadUrl:check?`${context.baseUrl}/trim/checks/download?check_id=${encodeURIComponent(check.id)}`:""})}</section>`;
  }
  async function refresh(){
    if(!live||!context.taskId)return;
    if(pending)return pending;
    const token=++generation;busy=true;error="";render();
    const work=(async()=>{
      const responses=await Promise.allSettled([api(path("setup")),api(path("definitions")),api(path("checks"))]);
      if(!active(token))return;
      const [setup,history,checkHistory]=responses;
      if(setup.status==="fulfilled"){
        if(setup.value?.binding?.task_id!==context.taskId)error="配平接口与当前固定任务不匹配。";
        else {contract=clone(setup.value);form ||= controlTrimForm(contract);}
      }
      if(history.status==="fulfilled"){definitions=eligible(history.value);if(!definitions.some(row=>row.id===selectedId))selectedId="";}
      if(checkHistory.status==="fulfilled"){checks=eligible(checkHistory.value);checkId=checks.some(row=>row.id===checkId)?checkId:checks[0]?.id||"";}
      error=[error,...responses.filter(item=>item.status==="rejected").map(item=>item.reason?.message||String(item.reason))].filter(Boolean).join("；");
      loaded=setup.status==="fulfilled"&&!!contract;busy=false;pending=null;render();
    })().catch(failure=>{if(active(token)){error=failure.message;busy=false;pending=null;render();}});
    pending=work;return work;
  }
  async function save(){
    if(!active(generation)||busy||!contract||!form||selectedId)return;
    let body;try{body=buildControlTrimRequest(form,contract);}catch(failure){error=failure.message;render();return;}
    const token=++generation;busy=true;error="";render();
    try{
      const record=await api(path("definitions"),"POST",body);if(!active(token))return;
      if(record?.task_id!==context.taskId||record?.definition?.binding?.task_id!==context.taskId)throw new Error("配平定义与当前固定任务不匹配。");
      definitions=[record,...definitions.filter(row=>row.id!==record.id)];selectedId=record.id;notify("独立配平定义已保存；尚未执行发动机工作点求解。");
    }catch(failure){if(active(token))error=failure.message;}
    finally{if(active(token)){busy=false;render();}}
  }
  async function runCheck(){
    if(!active(generation)||busy||!context.taskId||!TRIM_SCENARIOS.some(item=>item.id===scenario))return;
    const token=++generation;busy=true;error="";render();
    try{
      const record=await api(path("checks"),"POST",{scenario});if(!active(token))return;
      if(record?.task_id!==context.taskId)throw new Error("双层自检记录与当前固定任务不匹配。");
      checks=[record,...checks.filter(row=>row.id!==record.id)];checkId=record.id;notify("双层数值自检已保存；该结果不代表发动机配平。");
    }catch(failure){if(active(token))error=failure.message;}
    finally{if(active(token)){busy=false;render();}}
  }
  function bind(){
    if(!host)return;
    host.oninput=event=>{
      if(busy||selectedId||!form)return;
      const data=event.target?.dataset||{},value=event.target?.value;
      if(["thrust_percent","thrust_relative_tolerance"].includes(data.trimField)){
        form[data.trimField]=value;
        if(data.trimField==="thrust_percent"){
          const summary=host.querySelector?.(".ct-target-value b"),reference=contract?.reference_thrust?.value_N;
          if(summary)summary.textContent=`${fmt(finite(reference)&&positive(value)?reference*Number(value)/100000:null)} kN`;
        }
      }
      const target=data.trimTargetValue||data.trimTargetTolerance;
      if(form.additional_targets[target])form.additional_targets[target][data.trimTargetValue?"value":"tolerance"]=value;
      if(form.controls[data.trimValve]&&["lower","upper","initial"].includes(data.trimBound))form.controls[data.trimValve][data.trimBound]=value;
    };
    host.onchange=event=>{
      if(busy)return;
      const data=event.target?.dataset||{};
      if(data.trimDefinition!==undefined){selectedId=definitions.some(row=>row.id===event.target.value)?event.target.value:"";error="";render();return;}
      if(data.trimCheck!==undefined){checkId=checks.some(row=>row.id===event.target.value)?event.target.value:"";render();return;}
      if(data.trimScenario!==undefined){if(TRIM_SCENARIOS.some(item=>item.id===event.target.value))scenario=event.target.value;return;}
      if(selectedId||!form)return;
      if(form.additional_targets[data.trimTarget]){form.additional_targets[data.trimTarget].enabled=!!event.target.checked;render();}
      if(form.controls[data.trimControl]){form.controls[data.trimControl].enabled=!!event.target.checked;render();}
    };
    host.onclick=event=>{
      const action=event.target.closest?.("[data-trim-action]")?.dataset.trimAction;
      if(busy)return;
      if(action==="refresh")return refresh();if(action==="save")return save();if(action==="check")return runCheck();
    };
  }
  return {
    setContext(next,nextHost=host){
      const changed=next.projectId!==context.projectId||next.taskId!==context.taskId||next.baseUrl!==context.baseUrl;
      host=nextHost;context={...next};live=true;
      if(changed){++generation;contract=null;form=null;definitions=[];selectedId="";checks=[];checkId="";scenario="reachable";error="";busy=false;loaded=false;pending=null;}
      bind();render();if(context.taskId&&!loaded&&!pending)return refresh();return pending||Promise.resolve();
    },refresh,
    onLeave(){live=false;++generation;pending=null;busy=false;loaded=false;},
  };
}
