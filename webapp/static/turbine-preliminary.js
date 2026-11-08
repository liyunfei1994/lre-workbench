// Independent, transient design scenarios. Saved cycle records are never mutated.
const finite=v=>typeof v==="number"&&Number.isFinite(v);
const esc=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const arr=v=>Array.isArray(v)?v:[];
const fmt=v=>finite(v)?v.toLocaleString("zh-CN",{maximumSignificantDigits:7}):"—";
const text=v=>v==null?"":typeof v==="object"?JSON.stringify(v):String(v);
const safeUrl=v=>{try{const u=new URL(v);return u.protocol==="https:"&&["manual.cfturbo.com","cfturbo.com","ntrs.nasa.gov"].includes(u.hostname)?u.href:"";}catch{return "";}};
const STATUS_LABELS={pass:"代数检查通过",fail:"未通过",warning:"需调整与复核",review:"需复核",assumed:"初选假设",missing:"待补充",unverified:"尚未验证",candidate:"叶数候选",unavailable:"无法初选",preliminary:"初选结果"};
const statusBadge=status=>`<span class="td-status td-status-${Object.hasOwn(STATUS_LABELS,status)?status:"missing"}">${STATUS_LABELS[status]||"待确认"}</span>`;
const NUMBERS=[
  ["stages","候选级数","级",1,6,1],
  ["mean_diameter_m","平均直径 Dm","mm",0.001,null,0.1,1000],
  ["flow_coefficient","轴向流量系数 ca/U","—",0.001,null,0.01],
  ["reaction","动叶静焓降 / 级欧拉比功（目标）","—",0,1,0.01],
  ["inlet_swirl_m_s","首级静叶入口切向速度 cu₀","m/s",null,null,1],
  ["stator_axial_chord_m","静叶轴向弦长","mm",0.001,null,0.1,1000,true],
  ["rotor_axial_chord_m","动叶轴向弦长","mm",0.001,null,0.1,1000,true],
  ["stator_zweifel","静叶 Zweifel 目标","—",0.001,null,0.01,1,true],
  ["rotor_zweifel","动叶 Zweifel 目标","—",0.001,null,0.01,1,true],
];

export function renderPreliminaryForm(machine,workspace,key) {
  const seeds=workspace?.seeds||{};
  const field=([id,label,unit,min,max,step,scale=1,optional=false])=>{
    const seed=seeds[id],value=finite(seed?.value)?Number((seed.value*scale).toPrecision(15)):null;
    return `<label class="td-scenario-field"><span>${label} <small>${unit}</small></span><input type="number" name="${id}" data-td-scenario-input="${id}" value="${value==null?"":esc(String(value))}" step="${id==="stages"?step:"any"}"${min==null?"":` min="${min}"`}${max==null?"":` max="${max}"`}${optional?"":" required"}><small>${esc(seed?.note|| (value==null?(optional?"可留空；该叶排仅计算运动学":"请明确初选值"):"来自本记录的初选值"))}</small>${seed?.source?`<details class="td-field-source"><summary>${seed.status==="assumed"?"起始假设依据":"来源"}</summary>${esc(seed.source)}</details>`:""}</label>`;
  };
  return `<section class="td-preliminary" data-td-preliminary="${key}"><div class="td-section-heading"><div><span class="td-kicker">INDEPENDENT PRELIMINARY SCENARIO</span><h3>逐级均径与叶排初选</h3></div><span class="td-status td-status-assumed">独立方案</span></div><p class="td-caption">沿用本记录燃气流量、轴转速与实际比功。可修改下列初选约束，计算速度三角形及静/动叶叶片数候选；结果不回写历史记录。静态压力、马赫数、通流尺寸及详细叶型仍需后续设计。</p><div class="td-scenario-layout"><form data-td-scenario-form><div class="td-scenario-fields">${NUMBERS.slice(0,5).map(field).join("")}<label class="td-scenario-field"><span>逐级比功分配</span><select name="work_split" data-td-scenario-input="work_split"><option value="equal">等比功初选</option><option value="custom">自定义比例</option></select><small>按实际比功分配；不等同于等压比。</small></label><label class="td-scenario-field td-wide" data-td-work-fractions hidden><span>各级比功比例（JSON 数组）</span><input type="text" name="work_fractions" data-td-scenario-input="work_fractions" value="" placeholder="例如 [0.6, 0.4]"><small>数量等于级数，总和为 1；不自动归一化。</small></label></div><fieldset class="td-blade-assumptions"><legend>叶片数初选约束</legend><p class="td-caption">弦长与 Zweifel 系数是方案假设。留空时不生成对应叶排的叶片数候选。</p><div class="td-scenario-fields">${NUMBERS.slice(5).map(field).join("")}</div></fieldset><div class="td-scenario-actions"><button type="submit" data-td-calculate>计算当前初选方案</button><span data-td-calculation-state role="status" aria-live="polite">尚未计算</span></div></form><aside class="td-scenario-guide"><h4>本次求解范围</h4><ol><li>从保存记录提取质量流量、转速和实际焓降。</li><li>按中径、轴速系数及比功分配闭合欧拉功。</li><li>计算静叶/动叶流角和载荷；按显式弦长初选叶片数。</li><li>核对整数化后的节距、稠度和 Zweifel 值。</li></ol><p>角度从轴向量起，正切向与转动同向。流角与叶片金属角分别处理。</p><p>首级静叶入口旋流属于额外边界假设；多级后续静叶承接前一级动叶出口旋流。</p><p>几何可行性、叶排损失、热力状态、强度及性能图不由运动学闭合证明。</p></aside></div><div class="td-preliminary-result" data-td-preliminary-result aria-live="polite"><p class="td-empty">确认初选约束后计算。已保存的级数、中径与反动度仍可在下方查看。</p></div></section>`;
}

export function readPreliminaryScenario(values) {
  const result={};
  for(const [key,label,,,,,scale=1,optional=false] of NUMBERS){
    const raw=values[key];
    if(raw==null||String(raw).trim()===""){
      if(optional){result[key]=null;continue;}
      throw new Error(`请填写${label}。`);
    }
    const value=Number(raw)/scale;
    if(!finite(value))throw new Error(`${label}须为有限数值。`);
    result[key]=value;
  }
  result.work_split=values.work_split;
  if(!["equal","custom"].includes(result.work_split))throw new Error("请选择逐级比功分配方式。");
  if(result.work_split==="custom"){
    try{result.work_fractions=JSON.parse(values.work_fractions);}catch{throw new Error("比功比例须为 JSON 数组，例如 [0.6, 0.4]。");}
    if(!Array.isArray(result.work_fractions)||!result.work_fractions.every(finite))throw new Error("比功比例须为有限数值数组。");
  }else result.work_fractions=[];
  return result;
}

function vector(x1,y1,x2,y2,color,label,dx=0,dy=0) {
  const angle=Math.atan2(y2-y1,x2-x1),size=7;
  return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${color}" stroke-width="2.2"/><path d="M ${x2-size*Math.cos(angle-.42)} ${y2-size*Math.sin(angle-.42)} L ${x2} ${y2} L ${x2-size*Math.cos(angle+.42)} ${y2-size*Math.sin(angle+.42)}" fill="none" stroke="${color}" stroke-width="2.2"/><text x="${(x1+x2)/2+dx}" y="${(y1+y2)/2+dy}" fill="${color}" font-size="12" paint-order="stroke" stroke="#f7f9fc" stroke-width="4" stroke-linejoin="round">${label}</text>`;
}

export function renderVelocityTriangles(stage) {
  const values=[stage?.ca_m_s,stage?.u_m_s,stage?.cu1_m_s,stage?.cu2_m_s];
  if(!values.every(finite)||stage.ca_m_s<=0)return '<p class="td-empty">本级没有可绘制的速度三角形。</p>';
  const maxY=Math.max(0,...values.slice(1)),minY=Math.min(0,...values.slice(1)),scale=Math.min(180/stage.ca_m_s,180/Math.max(maxY-minY,1));
  const originY=65+maxY*scale;
  const triangle=(cu,x,title)=>{
    const ox=x+48,oy=originY,ex=ox+stage.ca_m_s*scale,ey=oy-cu*scale,uy=oy-stage.u_m_s*scale;
    return `<text x="${x+145}" y="27" fill="#425c77" font-size="13" text-anchor="middle">${title}</text><line x1="${ox-12}" y1="${oy}" x2="${x+275}" y2="${oy}" stroke="#bdcbd8" stroke-dasharray="4 4"/><line x1="${ox}" y1="${oy+15}" x2="${ox}" y2="45" stroke="#bdcbd8" stroke-dasharray="4 4"/>${vector(ox,oy,ex,ey,"#3578a8","c",9,15)}${vector(ox,uy,ex,ey,"#b08042","w",9,-9)}${vector(ox,oy,ox,uy,"#66866c","U",-21,0)}<text x="${x+270}" y="${oy+18}" fill="#8192a5" font-size="11" text-anchor="end">轴向 →</text>`;
  };
  return `<svg class="td-triangle" viewBox="0 0 640 300" role="img" aria-label="第 ${esc(stage.stage)} 级动叶入口与出口速度三角形"><rect x="0" y="0" width="640" height="300" fill="#f7f9fc"/>${triangle(stage.cu1_m_s,10,"动叶入口")}${triangle(stage.cu2_m_s,330,"动叶出口")}<text x="320" y="286" fill="#7a8ea2" text-anchor="middle" font-size="11">c = U + w · 同级共用比例 · 箭头表示速度，不是叶片轮廓</text></svg>`;
}

const STAGE_COLUMNS=[["stage","级次","—"],["work_fraction","比功比例","—"],["work_j_kg","实际比功","J/kg"],["power_w","气体功率","W"],["torque_nm","气动力矩","N·m"],["loading_coefficient","负荷 Δh/U²","—"],["u_m_s","U","m/s"],["ca_m_s","ca","m/s"],["cu0_m_s","cu₀","m/s"],["cu1_m_s","cu₁","m/s"],["cu2_m_s","cu₂","m/s"],["wu1_m_s","wu₁","m/s"],["wu2_m_s","wu₂","m/s"],["c0_m_s","c₀","m/s"],["c1_m_s","c₁","m/s"],["c2_m_s","c₂","m/s"],["w1_m_s","w₁","m/s"],["w2_m_s","w₂","m/s"],["alpha0_deg","α₀","°"],["alpha1_deg","α₁","°"],["alpha2_deg","α₂","°"],["beta1_deg","β₁","°"],["beta2_deg","β₂","°"],["reaction_target","动叶静焓降/欧拉功目标","—"],["reaction_check","动叶静焓降/欧拉功校核","—"],["reaction_stage","级反动度（静焓降比）","—"],["euler_residual_j_kg","Euler 残差","J/kg"]];
const BLADE_COLUMNS=[["axial_chord_m","轴向弦长","m"],["zweifel_target","Zweifel 目标","—"],["blade_count_continuous","连续叶片数","—"],["blade_count_candidate","整数叶片数候选","片"],["pitch_m","节距","m"],["axial_solidity","轴向稠度","—"],["zweifel_candidate","整数化后 Zweifel","—"],["turning_deg","转折角","°"]];
STAGE_COLUMNS.push(["rotor_static_drop_j_kg","动叶静焓降（代数）","J/kg"],["stator_static_drop_j_kg","静叶静焓降（代数）","J/kg"],["stage_static_drop_j_kg","整级静焓降（代数）","J/kg"]);

function renderEngineering(screening) {
  if(!screening)return "";
  return `<section class="td-section"><h3>当前方案工程筛查</h3><div class="td-table-wrap"><table class="td-fields"><thead><tr><th>检查量</th><th>值</th><th>单位</th><th>定义</th></tr></thead><tbody>${arr(screening.fields).map(f=>`<tr><th>${esc(f.label||f.key)}</th><td class="td-value">${esc(finite(f.value)?fmt(f.value):f.value??"—")}</td><td>${esc(f.unit)}</td><td class="td-provenance">${esc(f.note)}</td></tr>`).join("")}</tbody></table></div><ul class="td-notes">${arr(screening.checks).map(c=>`<li><strong>${esc(c.label)}</strong>：${esc(c.note)}</li>`).join("")}</ul><p class="td-caption">这是简化初选筛查；不能替代真实叶盘几何、材料温度及载荷下的结构分析。</p></section>`;
}

function renderInteraction(interaction) {
  if(!interaction)return "";
  const columns=[["stator_order","静叶谐波阶次"],["rotor_order","动叶谐波阶次"],["stator_periodicity","静叶周期数"],["rotor_periodicity","动叶周期数"],["m","周期差 m"]];
  return `<div class="td-pending"><h4>动静叶数谐波筛查 ${statusBadge(interaction.status)}</h4><p class="td-caption">${esc(interaction.note)}</p>${arr(interaction.pairs).length?`<details class="td-details"><summary>查看 1–3 阶谐波组合 · ${arr(interaction.pairs).length} 项</summary><p class="td-caption">m = |νs × Zs − νr × Zr|。周期差筛查不等于模态或共振验证。</p><div class="td-table-wrap"><table class="td-properties-table"><thead><tr>${columns.map(([,label])=>`<th>${label}</th>`).join("")}<th>状态</th></tr></thead><tbody>${interaction.pairs.map(pair=>`<tr>${columns.map(([key])=>`<td>${esc(fmt(pair[key]))}</td>`).join("")}<td>${statusBadge(pair.status)}</td></tr>`).join("")}</tbody></table></div></details>`:""}</div>`;
}

export function renderPreliminaryResult(result) {
  if(result?.status!=="preliminary")return `<div class="td-pending"><h4>初选尚未完成</h4><ul class="td-requirements">${arr(result?.errors).map(e=>`<li>${esc(e)}</li>`).join("")||"<li>请核对输入与当前记录。</li>"}</ul></div>`;
  const s=result.summary||{},table=(rows,columns)=>`<div class="td-table-wrap"><table class="td-properties-table"><thead><tr>${columns.map(([,label,unit])=>`<th>${label}<small>${unit}</small></th>`).join("")}</tr></thead><tbody>${rows.map(row=>`<tr>${columns.map(([key])=>`<td>${esc(fmt(row?.[key]))}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  const checks=arr(result.checks).map(c=>`<li><div><strong>${esc(c.label||c.id)}</strong>${statusBadge(c.status)}</div>${c.note?`<p>${esc(c.note)}</p>`:""}</li>`).join("");
  return `<div class="td-result-heading"><div><span class="td-kicker">PRELIMINARY RESULT</span><h3>当前方案 · ${esc(fmt(s.stages))} 级</h3><p class="td-caption">欧拉运动学与叶片数初选；尚未完成气动损失、结构或性能验证。</p></div><div><button type="button" data-td-scenario-export="json">方案 JSON</button><button type="button" data-td-scenario-export="csv">逐级参数 CSV</button></div></div><div class="td-metrics"><div><span>总实际比功</span><strong>${fmt(s.total_work_j_kg==null?null:s.total_work_j_kg/1000)}<small>kJ/kg</small></strong></div><div><span>总气体功率</span><strong>${fmt(s.total_power_w==null?null:s.total_power_w/1e6)}<small>MW</small></strong></div><div><span>气动力矩</span><strong>${fmt(s.total_torque_nm)}<small>N·m</small></strong></div><div><span>圆周速度 U</span><strong>${fmt(s.u_m_s)}<small>m/s</small></strong></div></div>${arr(result.stages).map(row=>`<section class="td-stage-result"><h4>第 ${esc(row.stage)} 级 <span>比功 ${fmt(row.work_j_kg/1000)} kJ/kg · 负荷 ${fmt(row.loading_coefficient)}</span></h4>${renderVelocityTriangles(row)}${table([row],STAGE_COLUMNS.filter(([key])=>["alpha0_deg","alpha1_deg","alpha2_deg","beta1_deg","beta2_deg","reaction_target","reaction_stage"].includes(key)))}<div class="td-blade-results">${["stator","rotor"].map(type=>{const blade=row[type]||{};return `<div><h4>${type==="stator"?"静叶":"动叶"}叶片数初选</h4>${table([blade],BLADE_COLUMNS.filter(([key])=>["blade_count_candidate","pitch_m","axial_solidity","zweifel_candidate"].includes(key)))}<p class="td-caption">${esc(blade.note||"叶片数是待校核候选，须继续设计金属角、厚度及喉部。")}</p></div>`;}).join("")}</div>${renderInteraction(row.interaction)}<details class="td-details"><summary>全部速度与逐级参数</summary>${table([row],STAGE_COLUMNS)}${["stator","rotor"].map(type=>`<h4>${type==="stator"?"静叶":"动叶"}</h4>${table([row[type]],BLADE_COLUMNS)}`).join("")}</details></section>`).join("")}<ul class="td-checks">${checks}</ul>${renderEngineering(result.engineering_screening)}<ul class="td-notes">${arr(result.limitations).map(note=>`<li>${esc(note)}</li>`).join("")}</ul><div class="td-references">${arr(result.references).filter(r=>safeUrl(r.url)).map(r=>`<a href="${esc(safeUrl(r.url))}" target="_blank" rel="noopener noreferrer">${esc(r.title)}</a>`).join("")}</div><p class="td-fingerprint">方案标识：${esc(result.calculation_sha256||"未提供")}</p>`;
}

export function buildPreliminaryCsv(result) {
  if(result?.status!=="preliminary")return "";
  const columns=["section","stage","key","label","value","unit","status","source"],rows=[];
  const add=(section,stage,key,label,value,unit,status)=>rows.push([section,stage,key,label,value,unit,status,result.source?.record_id||""]);
  const scenarioUnits={stages:"级",mean_diameter_m:"m",flow_coefficient:"—",reaction:"—",work_split:"—",work_fractions:"—",inlet_swirl_m_s:"m/s",stator_axial_chord_m:"m",rotor_axial_chord_m:"m",stator_zweifel:"—",rotor_zweifel:"—"};
  for(const [k,v] of Object.entries(result.scenario||{}))add("scenario","",k,k,v,scenarioUnits[k]||"—","assumed");
  for(const [k,v] of Object.entries(result.source||{}))add("source","",k,k,v,"—","saved");
  const summaryUnits={mass_flow_kg_s:"kg/s",shaft_speed_rpm:"rpm",mean_diameter_m:"m",u_m_s:"m/s",ca_m_s:"m/s",total_work_j_kg:"J/kg",total_power_w:"W",total_torque_nm:"N·m",stages:"级"};
  for(const [k,v] of Object.entries(result.summary||{}))add("summary","",k,k,v,summaryUnits[k]||"—","preliminary");
  for(const stage of arr(result.stages)){
    for(const [key,label,unit] of STAGE_COLUMNS)add("stage",stage.stage,key,label,stage[key],unit,"preliminary");
    for(const type of ["stator","rotor"]){const b=stage[type]||{};for(const [key,label,unit] of BLADE_COLUMNS)add(type,stage.stage,key,label,b[key],unit,b.status||"missing");add(type,stage.stage,"note","说明",b.note,"",b.status||"missing");}
    const interaction=stage.interaction;
    if(interaction){
      for(const key of ["method","status","stator_blade_count","rotor_blade_count","note"])add("interaction",stage.stage,key,key,interaction[key],key.endsWith("blade_count")?"片":"—",interaction.status);
      arr(interaction.pairs).forEach((pair,i)=>{for(const key of ["stator_order","rotor_order","stator_periodicity","rotor_periodicity","m","status"])add("interaction.pair",stage.stage,`${i+1}.${key}`,`组合 ${i+1} · ${key}`,pair[key],"—",pair.status);});
    }
  }
  for(const c of arr(result.checks))add("check","",c.id,c.label,c.note,"",c.status);
  for(const f of arr(result.engineering_screening?.fields))add("engineering_screening","",f.key,f.label,f.value,f.unit,f.status);
  for(const c of arr(result.engineering_screening?.checks))add("engineering_check","",c.id,c.label,c.note,"",c.status);
  arr(result.limitations).forEach((v,i)=>add("limitation","",i,"范围",v,"","preliminary"));
  add("method","","method","计算方法",result.method,"—","preliminary");
  arr(result.references).forEach((r,i)=>add("reference","",i,r.title,r.url,"—","reference"));
  add("identity","","calculation_sha256","方案标识",result.calculation_sha256,"","preliminary");
  add("identity","","scenario_sha256","方案来源与输入标识",result.scenario_sha256,"—","preliminary");
  const cell=v=>{let s=text(v);if(typeof v==="string"&&/^\s*[=+@-]/.test(s))s="'"+s;return `"${s.replaceAll('"','""')}"`;};
  return [columns.join(","),...rows.map(row=>row.map(cell).join(","))].join("\r\n")+"\r\n";
}

export function mountPreliminaryWorkspace(root,{turbineKey,recordId,onCalculate}={}) {
  const form=root?.querySelector?.("[data-td-scenario-form]");if(!form)return ()=>{};
  const output=root.querySelector("[data-td-preliminary-result]"),state=root.querySelector("[data-td-calculation-state]"),calculate=root.querySelector("[data-td-calculate]");
  const fields=()=>Object.fromEntries([...form.querySelectorAll("[data-td-scenario-input]")].map(el=>[el.name,el.value]));
  let version=0,disposed=false,result=null;
  const invalidate=()=>{version++;result=null;calculate.disabled=false;state.textContent="约束已变更，请重新计算";output.innerHTML='<p class="td-empty">当前约束尚未计算；旧方案已失效。</p>';};
  const change=()=>{invalidate();const custom=form.querySelector("[data-td-work-fractions]");if(custom)custom.hidden=fields().work_split!=="custom";};
  const submit=async event=>{
    event.preventDefault();if(disposed)return;const ticket=++version;result=null;output.innerHTML="";
    try{
      if(typeof onCalculate!=="function")throw new Error("当前计算接口不可用，请刷新页面后重试。");
      const scenario=readPreliminaryScenario(fields());calculate.disabled=true;state.textContent="正在计算当前方案…";
      const next=await onCalculate({turbineKey,scenario});
      if(disposed||ticket!==version)return;
      if(next?.source?.record_id&&recordId&&next.source.record_id!==recordId)throw new Error("返回结果不属于当前计算记录，请重新计算。");
      if(next?.source?.turbine_key&&next.source.turbine_key!==turbineKey)throw new Error("返回结果不属于当前涡轮，请重新计算。");
      result=next?.status==="preliminary"?next:null;output.innerHTML=renderPreliminaryResult(next);state.textContent=result?"初选完成 · 待气动与结构校核":"初选未完成，请核对输入";
    }catch(error){if(!disposed&&ticket===version){state.textContent="未能完成初选";output.innerHTML=`<p class="td-empty">${esc(error?.message||"计算失败，请重试。")}</p>`;}}
    finally{if(!disposed&&ticket===version)calculate.disabled=false;}
  };
  const download=event=>{
    const button=event.target?.closest?.("[data-td-scenario-export]");if(disposed||!result||!button||!root.contains(button))return;
    const ext=button.dataset.tdScenarioExport;if(!["json","csv"].includes(ext))return;
    const doc=root.ownerDocument,win=doc.defaultView,body=ext==="json"?JSON.stringify(result,null,2)+"\n":"\ufeff"+buildPreliminaryCsv(result);
    const url=win.URL.createObjectURL(new win.Blob([body],{type:ext==="json"?"application/json":"text/csv;charset=utf-8"})),anchor=doc.createElement("a");
    anchor.href=url;anchor.download=`turbine-preliminary-${String(turbineKey).replace(/[^a-z0-9_-]/gi,"_")}-${String(recordId||"unrecorded").replace(/[^a-z0-9_-]/gi,"_")}.${ext}`;doc.body.append(anchor);try{anchor.click();}finally{anchor.remove();win.setTimeout(()=>win.URL.revokeObjectURL(url),1000);}
  };
  form.addEventListener("input",change);form.addEventListener("change",change);form.addEventListener("submit",submit);root.addEventListener("click",download);
  return ()=>{disposed=true;version++;result=null;form.removeEventListener("input",change);form.removeEventListener("change",change);form.removeEventListener("submit",submit);root.removeEventListener("click",download);};
}
