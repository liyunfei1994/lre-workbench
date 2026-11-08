// Numerical diagnostics only. This renderer cannot turn a framework check into
// an engine result, even when a caller supplies a forged success flag.
import {residualPlot,renderSolverDiagnostics} from "./convergence.js";
const escape=value=>String(value??"").replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const list=value=>Array.isArray(value)?value:[];
const finite=value=>typeof value==="number"&&Number.isFinite(value);
const fmt=value=>finite(value)?Number(value.toPrecision(6)).toString():"—";

export function renderModelData(manifest,{templateUrl=""}={}) {
  if(!manifest)return '<section class="fs-card"><h3>部件数据接口</h3><p class="fs-notice">此历史任务未包含部件数据清单；没有记录不表示数据齐全。可从来源设计新建任务以查看当前接口。</p></section>';
  const items=list(manifest.items),fields=items.reduce((sum,item)=>sum+list(item.fields).length,0);
  return `<section class="fs-card fs-model-data" aria-label="固定硬件待补数据"><h3>部件数据接口 · 待补齐</h3><p class="fine">${items.length} 类接口、${fields} 个登记字段。缺失值保持为空；已有设计点证据不能直接替代部件特性和验证数据。</p>${templateUrl?`<a class="fs-download" href="${escape(templateUrl)}" download>下载待补数据模板 JSON</a>`:'<p class="fine">保存任务后可下载与该硬件快照关联的待补数据模板。</p>'}<div class="fs-data-items">${items.map(item=>`<details><summary><b>${escape(item.label||item.id)}</b><span class="fs-data-status">${item.status==="design_evidence_only"?"仅有设计点证据":"待补数据"}</span></summary><p class="fine">${escape(item.detail)}</p>${list(item.evidence).length?`<ul>${list(item.evidence).map(evidence=>`<li>${escape(evidence.description)}<code>${escape(evidence.source_path)}</code></li>`).join("")}</ul>`:""}<div class="fs-table-wrap"><table class="fs-table"><thead><tr><th>待登记字段</th><th>标识</th><th>单位 / 类型</th><th>当前状态</th></tr></thead><tbody>${list(item.fields).map(field=>`<tr><th scope="row">${escape(field.label||field.key)}</th><td><code>${escape(field.key)}</code></td><td>${escape(field.unit||"—")}</td><td><span class="fs-missing">待补</span></td></tr>`).join("")}</tbody></table></div></details>`).join("")}</div><details><summary>接口约定</summary><pre>${escape(JSON.stringify(manifest.conventions||{},null,2))}</pre></details></section>`;
}

export function renderDataDeclarationCheck(report,{busy=false,error=""}={}) {
  let result="";
  if(report){
    if(report.scope!=="data_declaration_only"||report.can_run!==false||report.adopted!==false)
      result='<p class="fs-error" role="alert">登记校核记录类型不匹配，未采用此结果。</p>';
    else{
      const missing=list(report.missing);
      result=`<p class="fs-notice">${report.complete?"登记字段齐全，数据与模型尚待验证。":`登记仍有 ${missing.length} 个缺项。`} 本次只校核登记结构、单位和缺项；没有读取引用的部件文件，也没有采用或替换硬件。</p>${missing.length?`<details><summary>查看待补字段（${missing.length}）</summary><ul class="fs-warnings">${missing.slice(0,100).map(path=>`<li><code>${escape(path)}</code></li>`).join("")}</ul>${missing.length>100?'<p class="fine">此处列出前 100 项。</p>':""}</details>`:""}${list(report.limitations).length?`<ul class="fs-warnings">${list(report.limitations).map(item=>`<li>${escape(item)}</li>`).join("")}</ul>`:""}`;
    }
  }
  return `<section class="fs-card fs-data-check"><h3>登记文件校核</h3><p class="fine">填写上方 JSON 模板后，可选择文件检查登记格式。本页只显示校核结果；任务快照保持原样，登记齐全也不会开放发动机求解。</p><label>选择数据登记 JSON（最大 200000 字节）<input type="file" accept=".json,application/json" data-fixed-data-file ${busy?"disabled":""}></label>${error?`<p class="fs-error" role="alert">${escape(error)}</p>`:""}${result}</section>`;
}

// Linear axes preserve sign and magnitude. Missing states break the path and
// are never filled with the final value or with zero.
export function variablePlot(points,specs) {
  const series=list(specs).map((spec,index)=>({name:spec.name,label:spec.label||spec.name,unit:spec.unit,color:index%4,points:list(points).map(point=>({x:point?.iteration,y:point?.variables?.[spec.name]}))}));
  const valid=series.flatMap(series=>series.points).filter(point=>finite(point.x)&&point.x>=0&&finite(point.y));
  if(!valid.length)return '<p class="cv-empty">未记录可用变量轨迹。缺失值不补零。</p>';
  const xmin=Math.min(...valid.map(point=>point.x)),xmax=Math.max(...valid.map(point=>point.x));
  let ymin=Math.min(...valid.map(point=>point.y)),ymax=Math.max(...valid.map(point=>point.y));
  const padding=(ymax-ymin)*.08||Math.max(1,Math.abs(ymax)*.08);ymin-=padding;ymax+=padding;
  const left=70,right=550,top=34,bottom=228;
  const x=value=>xmin===xmax?(left+right)/2:left+(value-xmin)/(xmax-xmin)*(right-left),y=value=>bottom-(value-ymin)/(ymax-ymin)*(bottom-top);
  const yticks=Array.from({length:5},(_,index)=>ymin+(ymax-ymin)*index/4);
  const xticks=[...new Set([xmin,...[.25,.5,.75].map(t=>Math.round(xmin+(xmax-xmin)*t)),xmax])];
  let missing=0;
  const paths=series.map(series=>{
    let d="",prior=null;
    const marks=series.points.map(point=>{
      if(!finite(point.x)||point.x<0||!finite(point.y)){prior=null;missing++;return "";}
      d+=`${prior!==null&&point.x>prior?"L":"M"}${x(point.x).toFixed(3)},${y(point.y).toFixed(3)} `;prior=point.x;
      return `<circle class="cv-mark cv-series-${series.color}" cx="${x(point.x)}" cy="${y(point.y)}" r="3"><title>${escape(series.label)} · 接受步 ${fmt(point.x)} · ${fmt(point.y)} ${escape(series.unit)}</title></circle>`;
    }).join("");
    return `<path class="cv-line cv-series-${series.color}" d="${d.trim()}"/>${marks}`;
  }).join("");
  return `<figure class="cv-chart"><figcaption>数值自检变量历程 · 无量纲</figcaption><svg class="cv-plot" viewBox="0 0 580 290" role="img" aria-label="数值自检变量随接受步变化；无量纲，不代表发动机状态"><title>数值自检变量历程</title><text class="cv-tick" x="70" y="17">变量值 / 1 · 线性轴</text>${yticks.map(t=>`<line class="cv-grid" x1="${left}" x2="${right}" y1="${y(t)}" y2="${y(t)}"/><text class="cv-tick" x="60" y="${y(t)+4}" text-anchor="end">${fmt(t)}</text>`).join("")}${paths}${xticks.map(t=>`<text class="cv-tick" x="${x(t)}" y="255" text-anchor="middle">${fmt(t)}</text>`).join("")}<text class="cv-tick" x="310" y="283" text-anchor="middle">接受迭代步</text></svg><div class="cv-legend">${series.map(series=>`<span class="cv-legend-${series.color}">${escape(series.label)} / ${escape(series.unit)}</span>`).join("")}</div><p class="cv-chart-note">缺失 / 无效值 ${missing} 个（保留断点）。只展示服务端保存的接受步。</p></figure>`;
}

export function renderFrameworkReport(report,{downloadUrl=""}={}) {
  if(!report)return '<p class="fs-empty">尚无数值框架自检记录。运行后查看实际迭代轨迹。</p>';
  if(report.analysis_type!=="numerical_framework_check"||report.scope!=="numerical_framework_only"||report.is_engine_result!==false)
    return '<p class="fs-error" role="alert">记录类型不匹配，未作为数值框架自检展示。</p>';
  const points=list(report.points),specs=list(report.residual_specs),variables=list(report.variables);
  const passed=report.status==="pass",last=points.at(-1);
  const series=[{label:"最大归一化残差",points:points.map(point=>({x:point?.iteration,y:point?.residual_norm}))},...specs.map(spec=>({label:spec.label||spec.name,points:points.map(point=>({x:point?.iteration,y:finite(point?.residuals?.[spec.name])?Math.abs(point.residuals[spec.name]):null}))}))];
  return `<div class="fs-check-report" data-report-scope="numerical_framework_only"><div class="fs-check-summary"><strong>${passed?"数值自检通过":"数值自检未通过"}</strong><span>终止原因：${escape(report.reason||"未记录")}</span>${downloadUrl?`<a class="fs-download" href="${escape(downloadUrl)}" download>下载自检记录 JSON</a>`:""}</div><p class="fine">模型 ${escape(report.model_id||"—")} · 已接受步 ${points.length?points.length-1:0} · 求值 ${fmt(report.nfev)} 次 · 雅可比 ${fmt(report.njev)} 次 · 拒绝步 ${fmt(report.rejected_steps)} 次 · 最终最大归一化残差 ${fmt(last?.residual_norm)}</p><p class="fs-notice">仅验证无量纲代数问题 x² + y = 5、y² + x = 3 的数值接口。这里的 x、y 不代表发动机流量、压力或温度；自检通过不改变发动机任务的待补数据状态。</p>${report.error?`<p class="fs-error">${escape(report.error)}</p>`:""}${report.diagnostics ? renderSolverDiagnostics(report) : ""}${points.length?`<div class="fs-check-charts">${residualPlot({title:"数值自检残差 · 按各方程验收阈值归一化",series,tolerance:1,unit:"1",xLabel:"接受迭代步"})}${variablePlot(points,variables)}</div><details><summary>查看逐步数值与方程尺度</summary><p class="fine">原始残差 ÷ 尺度 ÷ 容差 = 验收归一化残差；每个分量的绝对值均不超过 1 才满足数值阈值。雅可比探针和拒绝步不绘为接受步。</p><div class="fs-table-wrap"><table class="fs-table"><thead><tr><th>接受步</th>${variables.map(spec=>`<th>${escape(spec.label||spec.name)} / ${escape(spec.unit)}</th>`).join("")}<th>最大归一化残差</th>${specs.map(spec=>`<th>${escape(spec.label||spec.name)} · 原始值</th><th>归一化值</th>`).join("")}<th>累计求值</th></tr></thead><tbody>${points.map(point=>`<tr><td>${fmt(point?.iteration)}</td>${variables.map(spec=>`<td>${fmt(point?.variables?.[spec.name])}</td>`).join("")}<td>${fmt(point?.residual_norm)}</td>${specs.map(spec=>`<td>${fmt(point?.raw_residuals?.[spec.name])}</td><td>${fmt(point?.residuals?.[spec.name])}</td>`).join("")}<td>${fmt(point?.nfev)}</td></tr>`).join("")}</tbody></table></div><dl class="fs-metadata">${specs.map(spec=>`<dt>${escape(spec.label||spec.name)}</dt><dd>尺度 ${fmt(spec.scale)} ${escape(spec.unit)} · 容差 ${fmt(spec.tolerance)}</dd>`).join("")}</dl></details>`:'<p class="fs-empty">本次自检没有可用接受步，未生成曲线。</p>'}${list(report.limitations).length?`<ul class="fs-warnings">${list(report.limitations).map(value=>`<li>${escape(value)}</li>`).join("")}</ul>`:""}</div>`;
}

export function renderFrameworkChecks(records,{taskId="",baseUrl="",selectedId="",busy=false,error=""}={}) {
  const eligible=list(records).filter(record=>record?.task_id===taskId);
  const selected=eligible.find(record=>record.id===selectedId)||eligible[0];
  return `<section class="fs-card fs-framework" aria-label="独立数值框架自检"><div class="fs-check-heading"><div><span class="fs-kicker">NUMERICAL FRAMEWORK CHECK</span><h3>独立数值框架自检</h3></div><button type="button" data-fixed-action="framework-check" ${!taskId||busy?"disabled":""}>运行数值框架自检</button></div><p class="fine">用独立代数问题检查变量、残差、迭代记录与曲线显示。${taskId?"自检记录单独保存，可追溯到当前任务。":"请先保存任务定义。"}</p>${error?`<p class="fs-error" role="alert">${escape(error)}</p>`:""}${eligible.length?`<label class="fs-check-select">数值自检记录<select data-fixed-check ${busy?"disabled":""}>${eligible.map(record=>`<option value="${escape(record.id)}" ${selected?.id===record.id?"selected":""}>${escape(record.created?new Date(record.created*1000).toLocaleString("zh-CN",{hour12:false}):"时间未记录")} · ${escape(record.id)}</option>`).join("")}</select></label>`:""}${renderFrameworkReport(selected?.report,{downloadUrl:selected?`${baseUrl}/framework-checks/download?check_id=${encodeURIComponent(selected.id)}`:""})}</section>`;
}
