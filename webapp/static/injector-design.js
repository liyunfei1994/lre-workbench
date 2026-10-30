const finite=v=>typeof v==="number"&&Number.isFinite(v);
const escape=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const number=v=>finite(v)?(v!==0&&(Math.abs(v)<1e-5||Math.abs(v)>=1e9)?v.toExponential(3):v.toLocaleString("zh-CN",{maximumSignificantDigits:7})):"未记录";
const statusText={consistent:"几何连续性通过 · 性能未验证",inconsistent:"保存尺寸／设计点不一致",legacy:"旧记录 · 未完成新版回算",incomplete:"缺少回算依据",unavailable:"未记录几何",boundary_only:"设计边界 · 结构未选型"};
const fields=[
  ["face_diameter_m","喷注面直径","mm",1000],["num_elements","概念单元数","个"],
  ["element_spacing_mm","面积等效间距 √(A面/N)","mm"],
  ["d_ox_mm","中心富氧流道直径 dₒ","mm"],["d_post_mm","中心管外径 dₚ","mm"],
  ["d_outer_mm","外环流道外径 D（外筒内径）","mm"],["delta_fuel_mm","富燃环缝径向宽度 δ","mm"],
  ["post_wall_thickness_mm","中心管壁厚（假设）","mm"],
];
const quantity=(v,f=1,u="")=>finite(v)?`${number(v*f)}${u?` <small>${escape(u)}</small>`:""}`:"未记录";
function rows(values,definitions){return definitions.map(([key,label,unit,f=1])=>`<div><dt>${escape(label)}</dt><dd>${quantity(values?.[key],f,unit)}</dd></div>`).join("");}
function checksList(checks){return `<ul class="id-checks">${(checks||[]).map(c=>`<li><span>${escape(c.label)}</span><b class="${c.status==="fail"?"id-error":""}">${({pass:"一致",fail:"不一致",unavailable:"依据缺失"})[c.status]||"未评估"}</b></li>`).join("")}</ul>`;}

export function injectorSectionSvg(station){
  const d=station?.dimensions;
  if(!station?.shape_valid||!d||!["d_ox_mm","d_post_mm","d_outer_mm"].every(k=>finite(d[k])&&d[k]>0)||!(d.d_ox_mm<d.d_post_mm&&d.d_post_mm<d.d_outer_mm))return `<p class="id-note">缺少有效的同轴尺寸，未绘制截面。</p>`;
  const scale=280/d.d_outer_mm,ri=d.d_ox_mm/2*scale,rp=d.d_post_mm/2*scale;
  return `<svg class="id-section" viewBox="0 0 720 400" role="img" aria-label="主喷注单元横截面：中心富氧流道、中心管实体壁与外环富燃流道；流道直径按保存尺寸等比例绘制">
    <circle cx="240" cy="192" r="140" fill="#f3b49c" stroke="#ad5637" stroke-width="2"/>
    <circle cx="240" cy="192" r="${rp}" fill="#8391a4" stroke="#59697b"/>
    <circle cx="240" cy="192" r="${ri}" fill="#a4c5eb" stroke="#356498" stroke-width="2"/>
    <path d="M240 192 L450 95 M${240+(ri+rp)/2} 192 L450 180 M${240+(rp+140)/2} 200 L450 265" stroke="#68788e" fill="none"/>
    <g fill="#26394f" font-size="15"><text x="458" y="89">富氧燃气流道</text><text x="458" y="113">dₒ = ${number(d.d_ox_mm)} mm</text>
    <text x="458" y="174">中心管实体壁</text><text x="458" y="198">dₚ = ${number(d.d_post_mm)} mm</text>
    <text x="458" y="259">富燃燃气环缝</text><text x="458" y="283">δ = ${number(d.delta_fuel_mm)} mm</text>
    <text x="100" y="360">外环流道外径 D = ${number(d.d_outer_mm)} mm</text></g>
    <text x="100" y="385" fill="#64748b" font-size="13">横截面等比例；外筒壁厚、轴向长度和旋流结构未定义</text></svg>`;
}

function portTable(station){
  const main=station.id==="main";
  return `<div class="id-ports">${(station.ports||[]).map(p=>`<section><h4>${escape(p.label)}</h4><dl class="id-values">${rows(p,main?[
    ["mass_flow_kg_s","涡轮排气流量","kg/s"],["pressure_pa","排气系统压力","MPa",1e-6],
    ["temperature_k","排气系统温度","K"],["pressure_drop_pa","喷注压降预算","MPa",1e-6],
    ["density_kg_m3","本次定尺寸采用密度","kg/m³"],["cd","假设流量系数 Cd","—"],
    ["effective_velocity_m_s","模型有效速度（含 Cd）","m/s"],["required_area_m2","单元所需通流面积","mm²",1e6],
    ["geometry_area_m2","保存尺寸回算面积","mm²",1e6],["geometry_mass_flow_kg_s","N 个单元回算总流量","kg/s"],
    ["relative_flow_error","几何回算流量相对误差","%",100],
  ]:[["mass_flow_kg_s","支路质量流量","kg/s"],["chamber_pressure_pa","目标预燃室压力","MPa",1e-6],
      ["pressure_drop_pa","该支路喷注压降","MPa",1e-6],["branch_temperature_k","集总损失后支路温度","K"]])}</dl>${!main?`<p class="id-note">${escape(p.temperature_basis)}</p>`:""}</section>`).join("")}</div>`;
}

function stationPanel(station,index){
  const main=station.id==="main",status=station.status||"unavailable";
  let content="";
  if(main){
    const warning=status==="consistent"?"本页只验证已保存几何与当前孔口模型的一致性。":status==="legacy"?"该历史记录尚未保存新版面积与密度依据，不能确认流量闭合。旧尺寸保持原值；请重新运行设计点取得修正结果。":status==="inconsistent"?"回算发现保存几何或设计点不一致。请重新计算并核对；以下为原记录，未自动改写尺寸。":"当前记录缺少完整的几何或回算依据。";
    content=`<p class="id-alert ${status==="inconsistent"?"id-fail":""}">${warning}</p>
      <div class="id-geometry"><div>${injectorSectionSvg(station)}</div><div><h4>单元与面板初步尺寸</h4><dl class="id-values">${rows(station.dimensions,fields)}</dl><p class="id-note">面板孔位尚未排布；等效间距不等于真实节距。单元数与壁厚均应在后续设计中复核。</p></div></div>
      ${portTable(station)}
      <details class="id-details" open><summary>公式与独立回算</summary><p>Aₒ = πdₒ²/4；A环 = π(D²−dₚ²)/4 = πδ(dₚ+δ)；δ = [√(dₚ²+4A环/π)−dₚ]/2。</p><p>ṁ = Nρv有效A；v有效 = Cd√(2Δp/ρ)。Cd 在流量关系中只计入一次。尺寸越出经验范围时保留计算值并提示，不能夹断尺寸后声称流量闭合。</p>
      ${checksList(station.checks)}</details>
      ${(station.warnings||[]).length?`<div class="id-alert">${station.warnings.map(w=>`<p>${escape(w)}</p>`).join("")}</div>`:""}
      <ul class="id-notes">${(station.limitations||[]).map(t=>`<li>${escape(t)}</li>`).join("")}</ul>`;
  }else{
    const boundary=station.boundary_status;
    const boundaryText=boundary==="consistent"?"保存支路与预燃室设计边界一致。":boundary==="inconsistent"?"保存支路与预燃室状态存在不一致，请重新计算并核对。":"当前记录缺少完整的支路设计边界。";
    content=`<p class="id-alert ${boundary==="inconsistent"?"id-fail":""}">${boundaryText}</p><p class="id-note">${escape(station.note)}</p>${portTable(station)}<details class="id-details"><summary>设计边界一致性检查</summary>${checksList(station.checks)}</details><section class="id-pending"><h4>详细孔径尚无设计依据</h4><p>当前提供供给与喷注压降预算。以下输入确定后，才能对这处喷注器进行结构选型和尺寸设计：</p><ul>${(station.missing_geometry||[]).map(t=>`<li>${escape(t)}</li>`).join("")}</ul><p>主燃烧室的单元数量、壁厚和气—气流动假设不会自动套用到预燃室。</p></section>`;
  }
  return `<section class="id-panel" data-id-panel="${escape(station.id)}"${index?" hidden":""}><header><div><h3>${escape(station.label)}</h3><p>${escape(station.location)}</p></div><span class="id-status">${escape(statusText[status]||status)}</span></header><p class="id-type">${escape(station.type)}</p><button type="button" class="id-component-link" data-id-component="${escape(station.component_id)}">查看该部件系统结果 →</button>${content}</section>`;
}

export function renderInjectorDesign(model){
  if(!model?.available)return `<section class="id-workspace"><h2>喷注器初步设计</h2><p>${escape(model?.reason||"请选择一条 FFSC 设计点计算记录。")}</p></section>`;
  return `<section class="id-workspace"><header class="id-heading"><div><span class="id-eyebrow">INJECTOR INTERFACES</span><h2>喷注器初步设计</h2><p>${escape(model.source)}${model.record_id?` · 记录 ${escape(model.record_id)}`:""}</p></div><button type="button" data-id-export>导出本页 JSON</button></header>
    <div class="id-topology" aria-label="FFSC 三处喷注位置"><div>氧化剂／燃料供给支路 → <b>富氧预燃室喷注器</b> → 富氧预燃室 → 富氧涡轮 ↘</div><div>氧化剂／燃料供给支路 → <b>富燃预燃室喷注器</b> → 富燃预燃室 → 富燃涡轮 ↗</div><div class="id-main-path"><b>主燃烧室喷注器</b> → 主燃烧室 → 喷管</div></div>
    <nav class="id-tabs" aria-label="喷注器位置">${(model.stations||[]).map((s,i)=>`<button type="button" data-id-tab="${escape(s.id)}" aria-pressed="${!i}">${escape(s.label)}</button>`).join("")}</nav>
    ${(model.stations||[]).map(stationPanel).join("")}
    <details class="id-details"><summary>喷注器类型与当前模型</summary><p>常见构型包括直射／淋浴式、撞击式、同轴剪切式、同轴旋流式和针栓式。相态（液—液、气—液、气—气）是另一项分类，不能仅凭构型名称确定。</p><p>本程序已有尺寸对应同轴中心孔与外环缝，不是针栓。原有旋流数为经验假设；尚无切向入口或旋流槽几何，不能据此宣称完成了同轴旋流设计，也不代表 Raptor 实机结构。</p><p>参考：<a href="https://ntrs.nasa.gov/citations/19760023196" target="_blank" rel="noopener noreferrer">NASA SP-8089 · Liquid Rocket Engine Injectors</a>（重点为液—液／液—气，对气—气应用有限）；<a href="https://doi.org/10.1016/j.cja.2013.07.017" target="_blank" rel="noopener noreferrer">富氢／富氧气—气喷注实验</a>（不是甲烷工况）。</p></details></section>`;
}

export function injectorDesignJson(model){return JSON.stringify(model,null,2);}

export function mountInjectorDesign(host,model,{initialStation,onInspectComponent}={}){
  if(!host?.querySelectorAll||!model?.available)return ()=>{};
  const select=id=>{
    if(!(model.stations||[]).some(s=>s.id===id))return;
    host.querySelectorAll("[data-id-tab]").forEach(b=>b.setAttribute("aria-pressed",String(b.dataset.idTab===id)));
    host.querySelectorAll("[data-id-panel]").forEach(p=>p.hidden=p.dataset.idPanel!==id);
  };
  if(initialStation)select(initialStation);
  let url=null;
  const handler=event=>{
    const target=event.target?.closest?.("[data-id-tab],[data-id-component],[data-id-export]");
    if(!target||!host.contains(target))return;
    if(target.dataset.idTab)select(target.dataset.idTab);
    else if(target.dataset.idComponent)onInspectComponent?.(target.dataset.idComponent);
    else{
      if(url)URL.revokeObjectURL(url);
      url=URL.createObjectURL(new Blob([injectorDesignJson(model)],{type:"application/json;charset=utf-8"}));
      const a=document.createElement("a");a.href=url;a.download="injector-design.json";a.click();
    }
  };
  host.addEventListener("click",handler);
  return ()=>{host.removeEventListener("click",handler);if(url)URL.revokeObjectURL(url);};
}
