// Channel candidates are solved on the server using one saved design's boundaries.
// This view never resizes hardware, invents a constraint, or closes the engine cycle.
import {coolingCurveSegments} from "./cooling.js";

const object=value=>value&&typeof value==="object"&&!Array.isArray(value)?value:{};
const array=value=>Array.isArray(value)?value:[];
const finite=value=>typeof value==="number"&&Number.isFinite(value);
const escape=value=>String(value??"").replace(/[&<>"']/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
const format=value=>finite(value)?value.toLocaleString("zh-CN",{maximumSignificantDigits:5}):"—";
const quantity=(value,unit)=>`${format(value)}${finite(value)?` ${unit}`:""}`;
const active=record=>["queued","running","cancelling","cancel_requested"].includes(record?.status);
const statusNames={queued:"排队中",running:"计算中",cancelling:"正在取消",cancel_requested:"正在取消",cancelled:"已取消",succeeded:"已完成",failed:"失败",timed_out:"已超时"};
const assessmentNames={rejected:"不满足已设约束",incomplete:"约束未完整评估",provisional:"满足暂定约束 · 待核实",meets_declared:"满足已声明条件"};
const checkNames={wall_temperature:["最大热面壁温","K","≤"],pressure_drop:["冷却回路压降","MPa","≤"],rib_width:["最小肋宽","mm","≥"],aspect_ratio:["最大通道高宽比","","≤"],velocity:["最大通道速度","m/s","≤"]};
const dimensions=[
  ["channel_counts","通道数 N",10,2000,true,"条"],
  ["widths_mm","喉部通道宽度 w",.3,12,false,"mm"],
  ["heights_mm","通道深度 H",.5,20,false,"mm"],
  ["wall_thicknesses_mm","内衬热壁厚度 t",.2,3,false,"mm"],
];
const constraints=[
  ["wall_temp_limit_K","最大热面壁温",1,5000,"K"],
  ["max_pressure_drop_MPa","最大冷却回路压降",.001,500,"MPa"],
  ["min_rib_mm","最小肋宽",.001,20,"mm"],
  ["max_aspect_ratio","最大通道高宽比",.1,200,"—"],
  ["max_velocity_ms","最大沿程通道速度",.01,2000,"m/s"],
];
const message=(text,kind="note")=>`<p class="chd-message chd-${kind}">${escape(text)}</p>`;
const badge=candidate=>`<span class="chd-badge chd-${escape(candidate?.status==="failed"?"rejected":candidate?.assessment?.status||"incomplete")}">${escape(candidate?.status==="failed"?"计算失败":assessmentNames[candidate?.assessment?.status]||"未记录评估")}</span>`;

export function coolingDesignSource(context={}) {
  const record=context.record;
  return record?.status==="succeeded"&&record.project_id===context.project?.id&&record.result
    &&(!record.kind||record.kind==="design")?record:null;
}

export function createCoolingDesignForm(setup={}) {
  const form={};
  for(const [key] of dimensions)form[key]=array(setup[key]).join(", ");
  const limits=object(setup.constraints);
  for(const [key] of constraints)form[key]=finite(limits[key])?String(limits[key]):"";
  form.wall_limit_status=["unset","provisional","documented"].includes(limits.wall_limit_status)?limits.wall_limit_status:"unset";
  form.wall_limit_basis=typeof limits.wall_limit_basis==="string"?limits.wall_limit_basis:"";
  form.basis=typeof limits.basis==="string"?limits.basis:"";
  return form;
}

function values(raw,[,label,low,high,integer]) {
  const tokens=String(raw??"").trim().split(/[,，;；\s]+/).filter(Boolean);
  if(!tokens.length||tokens.length>16)throw Error(`${label}需填写 1–16 个候选值。`);
  const parsed=tokens.map(token=>Number(token));
  if(parsed.some(value=>!Number.isFinite(value)||value<low||value>high||(integer&&!Number.isInteger(value))))
    throw Error(`${label}需为 ${low}–${high} ${integer?"范围内的整数":"范围内的数值"}。`);
  return [...new Set(parsed)].sort((a,b)=>a-b);
}

export function coolingDesignCandidateCount(form={},maximum=64) {
  try{
    const count=dimensions.reduce((total,spec)=>total*values(form[spec[0]],spec).length,1);
    return {count,valid:count<=maximum,message:count<=maximum?`${count} 个候选组合 · 上限 ${maximum}`:`${count} 个组合超过上限 ${maximum}，请缩小范围。`};
  }catch(error){return {count:null,valid:false,message:error.message};}
}

export function buildCoolingDesignRequest(context,form,maximum=64) {
  const source=coolingDesignSource(context);
  if(!source)throw Error("先选择本项目已完成的稳态设计记录。");
  const count=coolingDesignCandidateCount(form,maximum);
  if(!count.valid)throw Error(count.message);
  const request={source_job_id:source.id,constraints:{}};
  for(const spec of dimensions)request[spec[0]]=values(form[spec[0]],spec);
  const status=form.wall_limit_status;
  if(!["unset","provisional","documented"].includes(status))throw Error("请选择有效的壁温约束状态。");
  for(const [key,label,low,high] of constraints){
    const raw=String(form[key]??"").trim();
    const value=raw===""||key==="wall_temp_limit_K"&&status==="unset"?null:Number(raw);
    if(value!==null&&(!Number.isFinite(value)||value<low||value>high))throw Error(`${label}需为 ${low}–${high} 范围内的数值，或留空。`);
    request.constraints[key]=value;
  }
  request.constraints.wall_limit_status=status;
  for(const key of ["wall_limit_basis","basis"]){
    const text=String(form[key]??"").trim();
    if(text.length>2000)throw Error("约束依据不能超过 2000 字。");
    request.constraints[key]=text;
  }
  if(status!=="unset"&&request.constraints.wall_temp_limit_K===null)throw Error("启用壁温约束时需填写限值。");
  if(status==="documented"&&!request.constraints.wall_limit_basis)throw Error("已有依据的壁温约束需填写依据与适用条件。");
  return request;
}

function input(key,label,value,unit="",attributes="") {
  return `<label class="chd-field"><span>${escape(label)}${unit?` / ${escape(unit)}`:""}</span><input data-cd-field="${key}" value="${escape(value)}" ${attributes}></label>`;
}

function formMarkup(form,setup) {
  const maximum=setup.maximum_candidates||64,count=coolingDesignCandidateCount(form,maximum);
  return `<div class="chd-input-grid">${dimensions.map(([key,label,low,high,integer,unit])=>`${input(key,label,form[key],unit,`type="text" inputmode="decimal" autocomplete="off" aria-label="${label}候选列表"`)}<span class="chd-sr-only">${low} 至 ${high}${integer?"，整数":""}</span>`).join("")}</div>
    <p class="chd-help">用逗号分隔候选值。采用恒定通道数、等高通道，宽度随半径变化；型面、覆盖范围、流向和材料沿用来源记录。各列表组合后逐一计算，不预设最优通道数。</p>
    <p class="chd-count ${count.valid?"":"chd-count-invalid"}" data-cd-count aria-live="polite">${escape(count.message)}</p>
    <details class="chd-constraints" open><summary>筛选约束与依据</summary><p class="chd-help">材料热物性数据温区、方案允许壁温与计算壁温分别检查。未设定的约束显示“未评估”；暂定值即使满足，也不能视为完成工程验证。</p>
    <div class="chd-input-grid"><label class="chd-field"><span>壁温约束状态</span><select data-cd-field="wall_limit_status">${[["unset","未设定 · 尚未评估"],["provisional","示例暂定 · 待依据"],["documented","已有依据 · 按条件检查"]].map(([key,label])=>`<option value="${key}" ${form.wall_limit_status===key?"selected":""}>${label}</option>`).join("")}</select></label>${constraints.map(([key,label,low,high,unit])=>input(key,label,form[key],unit,`type="number" min="${low}" max="${high}" step="any" placeholder="未设定" ${key==="wall_temp_limit_K"&&form.wall_limit_status==="unset"?"disabled":""}`)).join("")}</div>
    <div class="chd-basis-grid"><label class="chd-field"><span>壁温约束依据与适用条件</span><textarea data-cd-field="wall_limit_basis" maxlength="2000" rows="2" placeholder="例如材料状态、任务寿命、验证报告及适用条件">${escape(form.wall_limit_basis)}</textarea></label><label class="chd-field"><span>压降、肋宽、高宽比及速度约束依据</span><textarea data-cd-field="basis" maxlength="2000" rows="2" placeholder="记录制造能力、供压预算等来源；填写依据不等于已验证">${escape(form.basis)}</textarea></label></div></details>
    <div class="chd-actions"><button class="primary" data-cd-action="run">计算候选方案</button><span class="chd-help">仅生成筛选记录，不修改系统方案。</span></div>`;
}

function boundaryMarkup(setup) {
  const b=object(setup.boundary);
  return `<dl class="chd-boundary">${[["冷却入口温度",b.T_in_K,"K"],["冷却入口压力",b.p_in_MPa,"MPa"],["冷却剂流量",b.mdot_coolant_kg_s,"kg/s"],["来源最大热面壁温",b.source_wall_max_K,"K"],["来源冷却压降",b.source_dp_MPa,"MPa"]].map(([label,value,unit])=>`<div><dt>${label}</dt><dd>${quantity(value,unit)}</dd></div>`).join("")}</dl>`;
}

function savedRequestMarkup(record) {
  const request=object(record?.request||record?.result?.request),limits=object(request.constraints);
  if(!Object.keys(request).length)return "";
  const rows=dimensions.map(([key,label,,,,unit])=>`<tr><th scope="row">${escape(label)} / ${escape(unit)}</th><td>${array(request[key]).map(format).join("，")||"未记录"}</td></tr>`);
  const wallStatus=({unset:"未设定 · 尚未评估",provisional:"示例暂定 · 待依据",documented:"已有依据 · 按条件检查"})[limits.wall_limit_status]||"未记录";
  rows.push(`<tr><th scope="row">壁温约束状态</th><td>${wallStatus}</td></tr>`);
  for(const [key,label,,,unit] of constraints)rows.push(`<tr><th scope="row">${escape(label)}</th><td>${key==="wall_temp_limit_K"&&limits.wall_limit_status==="unset"?"未启用":finite(limits[key])?quantity(limits[key],unit==="—"?"":unit):"未设定"}</td></tr>`);
  return `<details class="chd-saved-request"><summary>查看该筛选记录的条件与依据</summary><p class="chd-help">上方表单用于下一次筛选；编辑或刷新不会改写下方历史结果。</p><div class="chd-table-wrap"><table><tbody>${rows.join("")}</tbody></table></div><p class="chd-help">壁温依据：${escape(limits.wall_limit_basis||"未记录")}。其他约束依据：${escape(limits.basis||"未记录")}。</p></details>`;
}

export function renderCoolingCandidateScatter(candidates=[],selectedId=null) {
  const points=array(candidates).filter(row=>row.status==="computed"&&finite(row.metrics?.dp_MPa)&&finite(row.metrics?.wall_max_K));
  if(!points.length)return message("还没有可绘制的候选结果。计算失败的候选及原因保留在下方表格中。");
  const xs=points.map(p=>p.metrics.dp_MPa),ys=points.map(p=>p.metrics.wall_max_K);
  const extent=vals=>{const lo=Math.min(...vals),hi=Math.max(...vals),pad=hi===lo?Math.max(Math.abs(lo)*.05,1):(hi-lo)*.1;return [lo-pad,hi+pad];};
  const [xmin,xmax]=extent(xs),[ymin,ymax]=extent(ys);
  const X=x=>68+(x-xmin)/(xmax-xmin)*510,Y=y=>258-(y-ymin)/(ymax-ymin)*214;
  const grid=Array.from({length:5},(_,i)=>{const x=xmin+(xmax-xmin)*i/4,y=ymin+(ymax-ymin)*i/4;return `<line x1="68" x2="578" y1="${Y(y)}" y2="${Y(y)}"/><text x="59" y="${Y(y)+4}" text-anchor="end">${format(y)}</text><text x="${X(x)}" y="278" text-anchor="middle">${format(x)}</text>`;}).join("");
  const marks=points.map(row=>{
    const x=X(row.metrics.dp_MPa),y=Y(row.metrics.wall_max_K),selected=String(row.id)===String(selectedId),g=object(row.geometry);
    return `<g class="chd-point chd-${escape(row.assessment?.status||"incomplete")} ${selected?"is-selected":""}" role="button" tabindex="0" data-cd-candidate="${escape(row.id)}" aria-label="候选 ${escape(row.id)}，通道 ${escape(g.count)} 条，壁温 ${format(row.metrics.wall_max_K)} K，压降 ${format(row.metrics.dp_MPa)} MPa" aria-pressed="${selected}"><title>${escape(`候选 ${row.id} · ${g.count} 条 · ${g.width_mm} × ${g.height_mm} mm；${assessmentNames[row.assessment?.status]||"未评估"}`)}</title>${row.pareto?`<circle class="chd-pareto-ring" cx="${x}" cy="${y}" r="9"/>`:""}<circle cx="${x}" cy="${y}" r="${selected?6:4.5}"/><text x="${x+10}" y="${y-7}">${escape(row.id)}</text></g>`;
  }).join("");
  return `<figure class="chd-scatter"><figcaption>壁温与压降权衡 <span>点击候选点查看几何与沿程结果</span></figcaption><svg viewBox="0 0 620 310" role="img" aria-label="候选最大热面壁温与冷却回路压降散点图"><text x="68" y="19">最大热面壁温 / K</text><g class="chd-grid">${grid}</g>${marks}<text x="323" y="307" text-anchor="middle">冷却回路压降 / MPa</text></svg><div class="chd-legend"><span class="chd-legend-meets">● 满足已声明条件</span><span class="chd-legend-pending">● 暂定或未完整评估</span><span class="chd-legend-rejected">● 不满足约束</span><span>◎ 本次非支配候选</span></div><p class="chd-help">非支配仅比较本次未被已设约束否决的候选：没有另一候选同时降低壁温和压降。未设约束仍未评估；这不是全局最优或制造可行性证明。</p></figure>`;
}

function sectionMarkup(candidate) {
  const g=object(candidate.geometry),m=object(candidate.metrics);
  const w=g.width_mm,h=g.height_mm,t=g.wall_mm,rib=m.throat_rib_mm,pitch=m.throat_pitch_mm;
  if(![w,h,t,rib,pitch].every(finite)||w<=0||h<=0||t<=0||rib<=0||pitch<=w)return message("本候选缺少完整喉部通道截面尺寸，不能绘制截面。");
  const scale=Math.min(300/pitch,150/(h+t)),width=w*scale,height=h*scale,wall=t*scale,period=pitch*scale;
  const x=225-width/2,y=37;
  return `<figure class="chd-section"><figcaption>喉部代表通道截面 <span>尺寸 / mm</span></figcaption><svg viewBox="0 0 450 255" role="img" aria-label="喉部通道宽 ${format(w)}、深 ${format(h)}、热壁厚 ${format(t)}、肋宽 ${format(rib)} 毫米"><rect class="chd-metal" x="${225-period/2}" y="${y}" width="${period}" height="${height+wall}"/><rect class="chd-fluid" x="${x}" y="${y}" width="${width}" height="${height}"/><line class="chd-hot-face" x1="${225-period/2}" x2="${225+period/2}" y1="${y+height+wall}" y2="${y+height+wall}"/><text x="225" y="23" text-anchor="middle">通道宽 w = ${format(w)}</text><text x="${225+period/2+9}" y="${y+height/2}" class="chd-section-height">H = ${format(h)}</text><text x="225" y="${y+height+wall+20}" text-anchor="middle">热壁 t = ${format(t)} · 燃气侧</text><text x="225" y="232" text-anchor="middle">节距 ${format(pitch)} · 肋宽 ${format(rib)} · N = ${format(g.count)}</text></svg><p class="chd-help">一个周期内两侧各画半个肋。外套及材料接合层未在此截面中建模；宽度随轴向半径变化，通道深度和热壁厚度按本候选给定。</p></figure>`;
}

function curve(title,unit,points,field,factor=1) {
  const segments=coolingCurveSegments(points,field,factor),all=segments.flat();
  if(!all.length)return `<figure class="chd-curve"><figcaption>${title}</figcaption>${message("该候选未保存可用沿程数值。")}</figure>`;
  const xs=all.map(p=>p.x),ys=all.map(p=>p.y),xmin=Math.min(...xs),xmax=Math.max(...xs);
  const lo=Math.min(...ys),hi=Math.max(...ys),pad=lo===hi?Math.max(Math.abs(lo)*.05,1):(hi-lo)*.08,ymin=lo-pad,ymax=hi+pad;
  const X=x=>55+(x-xmin)/(xmax-xmin||1)*345,Y=y=>150-(y-ymin)/(ymax-ymin)*119;
  return `<figure class="chd-curve"><figcaption>${title}<span>${unit}</span></figcaption><svg viewBox="0 0 430 193" role="img" aria-label="${title}，沿冷却剂流向弧长 s"><g class="chd-grid">${[0,.5,1].map(f=>{const y=ymin+(ymax-ymin)*f,x=xmin+(xmax-xmin)*f;return `<line x1="55" x2="400" y1="${Y(y)}" y2="${Y(y)}"/><text x="48" y="${Y(y)+4}" text-anchor="end">${format(y)}</text><text x="${X(x)}" y="168" text-anchor="middle">${format(x)}</text>`;}).join("")}</g>${segments.map(segment=>`${segment.length>1?`<polyline points="${segment.map(p=>`${X(p.x)},${Y(p.y)}`).join(" ")}"/>`:""}${segment.map(p=>`<circle cx="${X(p.x)}" cy="${Y(p.y)}" r="1.8"><title>${format(p.x)} m；${format(p.y)} ${unit}</title></circle>`).join("")}`).join("")}<text x="227" y="190" text-anchor="middle">沿冷却剂流向 s / m</text></svg></figure>`;
}

function candidateDetail(candidate,canAdopt,canReuseConstraints=false) {
  if(!candidate)return message("选择表格中的候选或图中数据点，查看详细尺寸与沿程结果。");
  if(candidate.status!=="computed")return `<div class="chd-detail"><h4>候选 ${escape(candidate.id)}</h4>${message(candidate.error||"本候选未完成计算。","error")}</div>`;
  const m=object(candidate.metrics),assessment=object(candidate.assessment),profile=object(candidate.profile);
  const fluid=array(profile.nodes).filter(node=>node?.kind==="channel");
  const thermal=array(profile.cells).map(cell=>cell?.kind==="channel"?cell:{s_m:cell?.s_m});
  const material=({within_range:"已检查的数据温区内",out_of_range:"材料数据温区越界",missing_evidence:"缺少材料检查证据"})[assessment.material_status]||assessment.material_status||"未记录";
  const match=({matched:"声明制造状态匹配",mismatch:"制造状态不匹配",unknown:"制造状态未确认",unspecified:"制造状态未指定"})[assessment.manufacturing_match]||"制造状态未确认";
  const checks=array(assessment.checks).map(check=>{const [label,unit,relation]=checkNames[check.id]||[check.id,"","≤"];return `<tr><th scope="row">${escape(label)}</th><td>${quantity(check.actual,unit)}</td><td>${finite(check.limit)?`${relation} ${quantity(check.limit,unit)}`:"未设定"}</td><td>${escape(({pass:"满足数值约束",fail:"超出约束",unset:"尚未评估"})[check.status]||"未记录")}</td></tr>`;}).join("");
  return `<section class="chd-detail"><div class="chd-detail-heading"><h4>候选 ${escape(candidate.id)} · ${format(candidate.geometry?.count)} 条通道</h4>${badge(candidate)}<div class="chd-detail-actions"><button data-cd-action="reuse-constraints" ${canReuseConstraints?"":"disabled"}>沿用这次筛选约束</button><button data-cd-action="adopt" ${canAdopt?"":"disabled"}>将该候选采纳到草稿</button></div></div><p class="chd-help">采纳草稿仅带入通道几何与壁温约束；压降、肋宽、高宽比及速度仍是本次筛选条件。保存并重算整机后，请选择新设计记录，沿用约束重新筛选；旧筛选结果不能作为新工况通过的依据。</p>${assessment.status==="rejected"?message("本候选不满足已设约束；如用于继续修改，只能作为待改进草稿。","warning"):""}<div class="chd-detail-grid">${sectionMarkup(candidate)}<div><dl class="chd-candidate-facts">${[["实际通道入口速度",m.velocity_inlet_ms,"m/s"],["实际喉部节点速度",m.velocity_throat_ms,"m/s"],["沿程最大速度",m.velocity_max_ms,"m/s"],["沿程最小肋宽",m.min_rib_mm,"mm"],["总吸热",m.Q_MW,"MW"],["冷却出口温度",m.T_out_K,"K"],["冷却出口压力",m.p_out_MPa,"MPa"],["冷却剂能量残差",m.energy_residual_W,"W"]].map(([label,value,unit])=>`<div><dt>${label}</dt><dd>${quantity(value,unit)}</dd></div>`).join("")}</dl><p class="chd-help">${escape(material)}；${escape(match)}。${finite(assessment.material_out_of_range_cells)?`${format(assessment.material_out_of_range_cells)} 个热单元超出材料数据温区。`:"越界单元数未记录。"} 这项检查不代表材料寿命或制造资格通过。</p><div class="chd-table-wrap"><table><thead><tr><th>检查项目</th><th>计算值</th><th>方案约束</th><th>结果</th></tr></thead><tbody>${checks}</tbody></table></div></div></div><div class="chd-curves">${curve("热面壁温","K",thermal,"T_wall_hot_K")}${curve("通道实际速度","m/s",fluid,"velocity_ms")}${curve("冷却剂通道压力","MPa",fluid,"p_Pa",1e-6)}</div><p class="chd-help">沿程图仅绘制保存的通道节点与热单元；入口、出口集流腔的零速度端口不当作通道速度。缺失数据保留断点。</p></section>`;
}

export function renderCoolingDesignResult(result={},selectedId=null,{canAdopt=false,canReuseConstraints=false}={}) {
  const rows=array(result.candidates),selected=rows.find(row=>String(row.id)===String(selectedId));
  if(!rows.length)return message("运行后将保存每个候选的计算结果、约束检查及失败原因。选择合适候选后，再回到系统设计点校核。");
  return `<div class="chd-result">${message(result.scope_note||"使用保存的热气状态及冷却入口边界筛选；候选吸热尚未反馈整机。采纳几何后须重新运行设计点。")}${renderCoolingCandidateScatter(rows,selectedId)}<div class="chd-table-wrap"><table class="chd-candidate-table"><thead><tr><th>候选</th><th>N / 条</th><th>w × H / mm</th><th>热壁 / mm</th><th>最小肋宽 / mm</th><th>最大速度 / m·s⁻¹</th><th>压降 / MPa</th><th>最大壁温 / K</th><th>检查结果</th></tr></thead><tbody>${rows.map(row=>{
    const g=object(row.geometry),m=object(row.metrics),selected=String(row.id)===String(selectedId);
    return `<tr class="${selected?"is-selected":""}"><td><button data-cd-candidate="${escape(row.id)}" aria-pressed="${selected}">${escape(row.id)}${row.pareto?" ◎":""}</button></td><td>${format(g.count)}</td><td>${format(g.width_mm)} × ${format(g.height_mm)}</td><td>${format(g.wall_mm)}</td><td>${format(m.min_rib_mm)}</td><td>${format(m.velocity_max_ms)}</td><td>${format(m.dp_MPa)}</td><td>${format(m.wall_max_K)}</td><td>${badge(row)}${row.error?`<small class="chd-row-error">${escape(row.error)}</small>`:""}</td></tr>`;
  }).join("")}</tbody></table></div>${candidateDetail(selected,canAdopt,canReuseConstraints)}</div>`;
}

const mounted=new WeakMap();

export function mountCoolingDesign(host,{api,getContext,notify=()=>{},onAdopt}={}) {
  if(!host||typeof host.addEventListener!=="function")return {refresh:async()=>{},sync(){},onLeave(){}};
  mounted.get(host)?.dispose();
  let live=true,timer=null,sequence=0,projectId="",sourceId="",setup=null,form={},records=[],selected=null,candidateId=null,error="",busy=false;
  const context=()=>getContext?.()||{};
  const stop=()=>{if(timer!==null)clearTimeout(timer);timer=null;};
  const current=(token,id,key)=>live&&token===sequence&&context().project?.id===id&&(coolingDesignSource(context())?.id||"")===key;
  const part=selector=>host.querySelector?.(selector);
  function syncContext(){
    const ctx=context(),id=ctx.project?.id||"",key=coolingDesignSource(ctx)?.id||"";
    if(id!==projectId||key!==sourceId){
      stop();++sequence;setup=null;form={};error="";candidateId=null;
      if(id!==projectId){records=[];selected=null;}
      projectId=id;sourceId=key;shell();
    }
    return ctx;
  }
  function shell(){
    const ctx=context(),source=coolingDesignSource(ctx);
    host.innerHTML=`<section class="cooling-design" aria-label="冷却通道方案筛选"><div class="chd-heading"><div><span class="chd-kicker">COOLING CHANNEL DESIGN</span><h3>通道方案筛选</h3><p>以壁温、压降和制造约束比较有限候选，再把所选几何带回整机闭合。</p></div><button data-cd-action="refresh">重新读取记录</button></div><p class="chd-source">输入来源：${source?`方案 v${escape(source.revision)} · 设计记录 ${escape(source.id)}`:"未选择本项目已完成的设计记录"}</p><div data-cd-context-note>${source&&(ctx.dirty||source.revision!==ctx.project?.revision)?message("当前草稿或方案版本与来源记录不同；本次筛选仍使用上述已保存边界。","warning"):""}</div>${message("本阶段固定来源记录的热气沿程状态与冷却入口温度、压力、流量。它能筛选通道尺寸；计算结果仍需重新反馈主循环后验算。")}<div data-cd-error role="status"></div><div data-cd-settings>${setup?boundaryMarkup(setup)+`<fieldset ${busy?"disabled":""}>${formMarkup(form,setup)}</fieldset>`:message(source?"正在读取该记录的通道几何与热边界…":"先运行或选择一个已完成的甲烷准一维设计点。历史预算或缺少沿程热侧状态的记录不能直接筛选。")}</div><div data-cd-output></div></section>`;
    output();showError();
  }
  function showError(){const node=part("[data-cd-error]");if(node)node.innerHTML=error?message(error,"error"):"";}
  function output(){
    const node=part("[data-cd-output]");if(!node)return;
    const progress=object(selected?.progress),iteration=progress.iteration,maximum=progress.maximum;
    const mismatch=(selected?.request?.source_job_id||selected?.result?.source_job_id)&&((selected?.request?.source_job_id||selected?.result?.source_job_id)!==sourceId);
    node.innerHTML=`<div class="chd-record-toolbar"><label>筛选记录<select data-cd-record ${busy?"disabled":""}><option value="">选择记录</option>${records.map(record=>`<option value="${escape(record.id)}" ${selected?.id===record.id?"selected":""}>v${escape(record.revision??record.source_revision??"—")} · ${escape(statusNames[record.status]||record.status)} · ${escape(record.id)}</option>`).join("")}</select></label><button data-cd-action="cancel" ${active(selected)&&!busy?"":"disabled"}>取消筛选</button></div>${selected?`<div class="chd-run-status" aria-live="polite">${escape(statusNames[selected.status]||selected.status)}${finite(iteration)&&finite(maximum)?` · ${format(iteration)} / ${format(maximum)} 个候选`:""}</div>`:""}${mismatch?message("当前展示另一设计记录的筛选结果；上方新筛选条件仍绑定当前选中的系统设计记录。","warning"):""}${savedRequestMarkup(selected)}${selected?.error?message(typeof selected.error==="string"?selected.error:JSON.stringify(selected.error),"error"):""}${renderCoolingDesignResult(selected?.result||{},candidateId,{canAdopt:typeof onAdopt==="function"&&!busy&&!active(selected),canReuseConstraints:!!setup&&!busy&&!!selected?.request?.constraints})}`;
  }
  function schedule(){stop();if(live&&records.some(active))timer=setTimeout(()=>refresh().catch(()=>{}),1800);}
  async function refresh(){
    live=true;stop();const ctx=syncContext(),id=ctx.project?.id,key=sourceId,token=++sequence;
    if(!id){shell();return;}
    const needSetup=!!key&&!setup;
    const results=await Promise.allSettled([needSetup?api(`/projects/${encodeURIComponent(id)}/cooling-designs/setup?source_job_id=${encodeURIComponent(key)}`):Promise.resolve(setup),api(`/projects/${encodeURIComponent(id)}/cooling-designs`)]);
    if(!current(token,id,key))return;
    error="";
    if(results[0].status==="fulfilled"&&needSetup){setup=results[0].value;form=createCoolingDesignForm(setup);shell();}
    else if(results[0].status==="rejected"){error=results[0].reason?.message||"无法读取通道筛选边界。";const node=part("[data-cd-settings]");if(node&&!setup)node.innerHTML=message("该来源记录暂不能用于通道方案筛选，请根据提示处理后重新读取。","warning");}
    if(results[1].status==="fulfilled")records=array(results[1].value);
    else error=[error,results[1].reason?.message||"无法读取筛选历史。"].filter(Boolean).join(" ");
    const next=records.find(record=>record.id===selected?.id)||records[0];
    if(next){
      try{const detail=await api(`/cooling-designs/${encodeURIComponent(next.id)}`);if(!current(token,id,key))return;selected=detail;if(!array(selected.result?.candidates).some(row=>String(row.id)===String(candidateId)))candidateId=selected.result?.candidates?.[0]?.id??null;}
      catch(problem){if(!current(token,id,key))return;error=[error,problem.message].filter(Boolean).join(" ");}
    }else{selected=null;candidateId=null;}
    // Polling replaces only results: input values, focus and partially typed lists stay intact.
    output();showError();schedule();
  }
  async function chooseRecord(id){
    stop();const token=++sequence,pid=projectId,key=sourceId;
    if(!id){selected=null;candidateId=null;output();schedule();return;}
    try{const detail=await api(`/cooling-designs/${encodeURIComponent(id)}`);if(!current(token,pid,key))return;selected=detail;candidateId=detail.result?.candidates?.[0]?.id??null;error="";output();showError();schedule();}
    catch(problem){if(current(token,pid,key)){error=problem.message;showError();schedule();}}
  }
  function updateBusy(){const fieldset=part("[data-cd-settings] fieldset");if(fieldset)fieldset.disabled=busy;output();}
  async function run(){
    if(busy||!setup)return;
    let request;try{request=buildCoolingDesignRequest(context(),form,setup.maximum_candidates||64);}catch(problem){error=problem.message;showError();return;}
    stop();busy=true;error="";const token=++sequence,id=projectId,key=sourceId;updateBusy();showError();
    try{const created=await api(`/projects/${encodeURIComponent(id)}/cooling-designs`,"POST",request);if(!current(token,id,key))return;selected={project_id:id,revision:setup.source_revision,request,status:"queued",...created};candidateId=null;records=[selected,...records.filter(record=>record.id!==created.id)];notify("已提交通道候选筛选；当前系统方案与设计记录保持原样。");}
    catch(problem){if(current(token,id,key))error=problem.message;}
    finally{busy=false;if(current(token,id,key)){updateBusy();showError();schedule();}}
  }
  async function cancel(){
    if(busy||!active(selected))return;
    const id=selected.id,pid=projectId,key=sourceId,token=++sequence;busy=true;stop();updateBusy();
    try{await api(`/cooling-designs/${encodeURIComponent(id)}/cancel`,"POST",{});if(current(token,pid,key)){notify("已请求取消筛选；已保存的记录继续保留。");await refresh();}}
    catch(problem){if(live&&projectId===pid){error=problem.message;showError();}}
    finally{busy=false;if(live&&projectId===pid){updateBusy();schedule();}}
  }
  async function adopt(){
    if(busy||active(selected)||typeof onAdopt!=="function")return;
    const row=array(selected?.result?.candidates).find(candidate=>String(candidate.id)===String(candidateId));
    if(row?.status!=="computed")return;
    const pid=projectId;busy=true;updateBusy();
    try{await onAdopt({studyId:selected.id,candidateId:row.id});}
    catch(problem){if(live&&projectId===pid){error=problem.message;showError();}}
    finally{busy=false;if(live&&projectId===pid)updateBusy();}
  }
  function reuseConstraints(){
    if(busy||!setup||!selected?.request?.constraints)return;
    const saved=createCoolingDesignForm({constraints:selected.request.constraints});
    for(const key of [...constraints.map(([key])=>key),"wall_limit_status","wall_limit_basis","basis"]){
      form[key]=saved[key];
      const field=part(`[data-cd-field="${key}"]`);if(field)field.value=saved[key];
    }
    const wall=part('[data-cd-field="wall_temp_limit_K"]');if(wall)wall.disabled=form.wall_limit_status==="unset";
    error="";showError();
    notify("已将这次筛选约束填入当前来源的待提交表单；候选尺寸未改，请重新计算。旧筛选记录保持原样。");
  }
  function inputEvent(event){
    const key=event.target?.dataset?.cdField;if(!key||!Object.hasOwn(form,key))return;
    form[key]=event.target.value;
    const count=part("[data-cd-count]");if(count){const state=coolingDesignCandidateCount(form,setup?.maximum_candidates||64);count.textContent=state.message;count.classList?.toggle("chd-count-invalid",!state.valid);}
    if(key==="wall_limit_status"){const field=part('[data-cd-field="wall_temp_limit_K"]');if(field)field.disabled=form.wall_limit_status==="unset";}
  }
  function changeEvent(event){inputEvent(event);if(event.target?.hasAttribute?.("data-cd-record"))chooseRecord(event.target.value);}
  function clickEvent(event){
    const candidate=event.target?.closest?.("[data-cd-candidate]");
    if(candidate){const row=array(selected?.result?.candidates).find(row=>String(row.id)===candidate.dataset.cdCandidate);if(row){candidateId=row.id;output();}return;}
    const action=event.target?.closest?.("[data-cd-action]")?.dataset.cdAction;
    if(action==="run")run();else if(action==="refresh")refresh().catch(problem=>{error=problem.message;showError();});else if(action==="cancel")cancel();else if(action==="adopt")adopt();else if(action==="reuse-constraints")reuseConstraints();
  }
  function keyEvent(event){if(["Enter"," "].includes(event.key)&&event.target?.closest?.("g[data-cd-candidate]")){event.preventDefault();clickEvent(event);}}
  host.addEventListener("input",inputEvent);host.addEventListener("change",changeEvent);host.addEventListener("click",clickEvent);host.addEventListener("keydown",keyEvent);
  function onLeave(){live=false;stop();++sequence;}
  function dispose(){onLeave();for(const [name,listener] of [["input",inputEvent],["change",changeEvent],["click",clickEvent],["keydown",keyEvent]])host.removeEventListener?.(name,listener);}
  const controller={refresh,sync(){syncContext();},onLeave,dispose};mounted.set(host,controller);syncContext();if(!host.innerHTML)shell();return controller;
}
