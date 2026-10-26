// Read-only presentation of the cooling profile used by the saved cycle result.
// This module does not integrate heat flux, calculate losses, or rerun a model.
const finite = value => typeof value === "number" && Number.isFinite(value);
const obj = value => value && typeof value === "object" && !Array.isArray(value) ? value : {};
const list = value => Array.isArray(value) ? value : [];
const esc = value => String(value ?? "").replace(/[&<>"']/g, char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
const fmt = value => !finite(value) ? "—" : value !== 0 && (Math.abs(value)<1e-4 || Math.abs(value)>=1e8)
  ? value.toExponential(3) : value.toLocaleString("zh-CN",{maximumSignificantDigits:6});
const scaled = (value,factor) => finite(value) ? value * factor : null;
const first = (...values) => values.find(finite) ?? null;
const quantity = (value,unit) => `${fmt(value)}${finite(value) ? ` ${unit}` : ""}`;
const badge = (kind,label) => `<span class="cool-badge cool-${kind}">${esc(label)}</span>`;
const cell = value => `<td>${esc(value)}</td>`;
const table = (headers,rows) => `<div class="cool-table-wrap"><table><thead><tr>${headers.map(v=>`<th scope="col">${esc(v)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;

export function coolingDisplayState(result={}) {
  const calculation=obj(result.calculation ?? result), component=obj(obj(calculation.components).cooling_channel);
  const profile=obj(component.profile), outputs=obj(component.outputs), summary=obj(profile.summary);
  const available=profile.version===1 && profile.model_id==="regen_quasi1d_ph_v1" && list(profile.nodes).length>=2 && list(profile.cells).length>0;
  const hasProfile=Object.keys(profile).length>0;
  const validity=obj(profile.validity), warnings=list(validity.warnings).filter(v=>typeof v==="string");
  const complete=available && list(profile.nodes).every(raw=>["s_m","p_Pa","T_K","h_Jkg"].every(key=>finite(obj(raw)[key])))
    && list(profile.cells).every(raw=>obj(raw).kind!=="channel" || ["s_m","q_W_m2","T_wall_hot_K","T_wall_cold_K"].every(key=>finite(obj(raw)[key])));
  if(available && !complete)warnings.push("部分沿程字段缺失或非有限；对应曲线保留断点，不能把不完整显示解释为完整检查通过。");
  let kind="unknown", label=hasProfile ? "沿程记录不完整或版本不受支持" : "未记录沿程解";
  if(available){
    kind=validity.status==="pass" && complete && !warnings.length ? "pass" : "review";
    label=kind==="pass" ? "内核检查通过" : "适用性需核查";
  }
  return {component,profile,outputs,available,hasProfile,kind,label,warnings,
    summary:{
      Q_total_W:first(summary.Q_total_W,outputs.Q_total_W,outputs.heat_load_W),
      dp_total_Pa:first(summary.dp_total_Pa,outputs.dp_total_Pa,outputs.delta_p_Pa,scaled(outputs.delta_P_MPa,1e6)),
      T_out_K:first(summary.T_out_K,outputs.T_out_K),
      p_out_Pa:first(summary.p_out_Pa,outputs.p_out_Pa,outputs.P_out_Pa,scaled(outputs.p_out_MPa,1e6)),
      h_out_Jkg:first(summary.h_out_Jkg,outputs.h_out_Jkg),
      h0_out_Jkg:first(summary.h0_out_Jkg,outputs.h0_out_Jkg),
      T_wall_hot_max_K:first(summary.T_wall_hot_max_K,outputs.T_wall_hot_max_K),
    }};
}

/** Gaps and non-increasing distances split a curve; no interpolation repairs saved data. */
export function coolingCurveSegments(points,field,scale=1) {
  const segments=[]; let current=[], previous=null;
  const flush=()=>{if(current.length)segments.push(current);current=[];previous=null;};
  for(const raw of list(points)){
    const point=obj(raw), x=point.s_m, y=point[field];
    if(!finite(x)||!finite(y)){flush();continue;}
    if(previous!==null && x<=previous)flush();
    current.push({x,y:y*scale});previous=x;
  }
  flush();return segments;
}

function chart(title,unit,points,definitions) {
  const series=definitions.map(([field,label,scale=1])=>({field,label,segments:coolingCurveSegments(points,field,scale)}));
  const all=series.flatMap(s=>s.segments.flat());
  const head=`<figcaption>${esc(title)}<span>${esc(unit)}</span></figcaption>`;
  if(!all.length)return `<figure class="cool-chart">${head}<p class="cool-empty">本记录缺少该曲线的有限数值。</p></figure>`;
  let xmin=Math.min(...all.map(p=>p.x)), xmax=Math.max(...all.map(p=>p.x));
  let ymin=Math.min(...all.map(p=>p.y)), ymax=Math.max(...all.map(p=>p.y));
  if(xmax===xmin){xmin-=.5;xmax+=.5;}
  const padding=ymax===ymin ? Math.max(Math.abs(ymax)*.02,1) : (ymax-ymin)*.08;
  ymin-=padding;ymax+=padding;
  const X=x=>58+(x-xmin)/(xmax-xmin)*534, Y=y=>198-(y-ymin)/(ymax-ymin)*166;
  const ticks=Array.from({length:5},(_,i)=>{
    const fraction=i/4, y=ymin+(ymax-ymin)*fraction, x=xmin+(xmax-xmin)*fraction;
    return `<g class="cool-grid"><line x1="58" x2="592" y1="${Y(y)}" y2="${Y(y)}"/><text x="49" y="${Y(y)+4}" text-anchor="end">${esc(fmt(y))}</text><text x="${X(x)}" y="219" text-anchor="middle">${esc(fmt(x))}</text></g>`;
  }).join("");
  const paths=series.map((s,i)=>s.segments.map(segment=>{
    const line=segment.length>1 ? `<polyline class="cool-line cool-series-${i}" points="${segment.map(p=>`${X(p.x)},${Y(p.y)}`).join(" ")}"/>` : "";
    const marks=segment.map(p=>`<circle class="cool-point cool-series-${i}" cx="${X(p.x)}" cy="${Y(p.y)}" r="2.6"><title>${esc(`${s.label}；s=${fmt(p.x)} m；${fmt(p.y)} ${unit}`)}</title></circle>`).join("");
    return line+marks;
  }).join("")).join("");
  const legend=series.map((s,i)=>`<span><i class="cool-key cool-series-${i}"></i>${esc(s.label)}</span>`).join("");
  return `<figure class="cool-chart">${head}<svg viewBox="0 0 620 244" role="img" aria-label="${esc(`${title}，横轴为沿冷却剂流向的弧长 s，单位 m`)}"><title>${esc(title)} · 已保存离散值</title>${ticks}${paths}<text class="cool-axis-title" x="325" y="240" text-anchor="middle">沿冷却剂流向的弧长 s / m</text></svg><div class="cool-legend">${legend}</div></figure>`;
}

function summaryCards(summary) {
  const values=[
    ["冷却剂总吸热",scaled(summary.Q_total_W,1e-6),"MW"],
    ["冷却回路总压降",scaled(summary.dp_total_Pa,1e-6),"MPa"],
    ["冷却出口温度",summary.T_out_K,"K"],
    ["冷却出口压力",scaled(summary.p_out_Pa,1e-6),"MPa"],
  ];
  return `<div class="cool-kpis">${values.map(([label,value,unit])=>`<div><span>${esc(label)}</span><strong>${fmt(value)} <small>${esc(unit)}</small></strong></div>`).join("")}</div>`;
}

function diagnostics(profile) {
  const balance=obj(profile.balance), coupling=obj(profile.hot_side_coupling);
  const rows=[
    ["冷却剂能量总残差",balance.energy_residual_W,"W"],
    ["单元最大能量残差",balance.max_cell_energy_residual_W,"W"],
    ["单元最大动量残差",balance.max_cell_momentum_residual_Pa,"Pa"],
  ].map(([label,value,unit])=>`<tr>${cell(label)}${cell(quantity(value,unit))}</tr>`);
  const measures=[
    ["供给/冷却压力残差","pressure_residual_Pa","pressure_tolerance_Pa","Pa"],
    ["热气侧恢复温度残差","hot_side_temperature_residual_K","temperature_tolerance_K","K"],
    ["热气状态向量相对残差","hot_side_relative_residual","hot_side_relative_tolerance",""],
    ["逐单元热量相对残差","cell_heat_relative_residual","cell_heat_relative_tolerance",""],
    ["喉部面积相对残差","geometry_relative_residual","geometry_relative_tolerance",""],
    ["总吸热量相对残差","heat_relative_residual","heat_relative_tolerance",""],
  ];
  // Old records can carry only a heat residual in watts. Keep it as recorded,
  // without treating it as the newer pressure/temperature/vector closure proof.
  if(Object.hasOwn(coupling,"residual_W") || Object.hasOwn(coupling,"tolerance_W"))
    measures.push(["热量耦合残差（历史字段）","residual_W","tolerance_W","W"]);
  const history=list(coupling.history);
  const fixedArea=finite(coupling.fixed_hardware_area_relative_residual) ? `<p class="cool-note">固定硬件喉部面积相对残差：${esc(fmt(coupling.fixed_hardware_area_relative_residual))}；${coupling.fixed_hardware_area_owner==="outer_throttle_solve" ? "由固定喉部节流外层验收。上表面积残差为内层耦合判据，不能据其为零判断固定硬件喉部约束已通过。" : "验收层级未确认。"}</p>` : "";
  const rejected=list(coupling.rejected_pressure_trials);
  const rejectedTable=rejected.length ? `<details class="cool-details"><summary>查看未接受的压力试探（${rejected.length} 次）</summary>${table(["试算序号","本次压降初猜 / MPa","下次压降初猜 / MPa","失败原因"],rejected.map(raw=>{const row=obj(raw);return `<tr>${cell(fmt(row.iteration))}${cell(fmt(scaled(row.trial_pressure_drop_Pa,1e-6)))}${cell(fmt(scaled(row.next_pressure_drop_Pa,1e-6)))}${cell(row.reason || "未记录")}</tr>`;}))}<p class="cool-note">这些试探没有形成被接受的物理解，不能作为沿程曲线或最终热量/压力结果。最终解仍需满足上表耦合判据。</p></details>` : "";
  const historyTable=history.length ? `<details class="cool-details cool-coupling-history"><summary>查看保存的耦合迭代历程（${history.length} 轮）</summary>${table(["轮次","压力残差 / Pa","温度残差 / K","热气向量相对残差","单元热量相对残差","面积相对残差","总吸热相对残差"],history.map(raw=>{
    const record=obj(raw);
    return `<tr>${cell(fmt(record.iteration))}${measures.slice(0,6).map(([,key])=>cell(fmt(record[key]))).join("")}</tr>`;
  }))}<p class="cool-note">相对残差均为无量纲；对应容差列在上表。逐轮原样显示保存值，不补齐缺失轮次。</p></details>` : '<p class="cool-note">本记录未保存耦合迭代历程。</p>';
  const couplingRows=Object.keys(coupling).length ? `<h3>热气侧与冷却回路耦合</h3>${table(["项目","本次记录"],[
    `<tr>${cell("耦合模式")}${cell(coupling.mode || "未记录")}</tr>`,
    `<tr>${cell("耦合状态")}${cell(({pass:"达到本次耦合判据",review:"需核查",fail:"未达到判据",unavailable:"未记录"})[coupling.status] || "未确认")}</tr>`,
    `<tr>${cell("耦合迭代次数")}${cell(fmt(coupling.iterations))}</tr>`,
  ])}${table(["耦合检查量","本次残差","本次容差"],measures.map(([label,key,tol,unit])=>`<tr>${cell(label)}${cell(quantity(coupling[key],unit))}${cell(quantity(coupling[tol],unit))}</tr>`))}${fixedArea}${historyTable}${rejectedTable}` : '<p class="cool-note">本记录未保存热气侧耦合诊断；不能由冷却剂自身收敛推断整机热量耦合已闭合。</p>';
  return `<details class="cool-details"><summary>守恒残差与热气侧耦合</summary>${table(["检查量","有符号值 / 最大绝对值"],rows)}<p class="cool-note">残差按本次内核记录展示；界面不重新计算热量或压力损失，也不另设通过阈值。</p>${couplingRows}</details>`;
}

function gasHeatTransferDetails(profile) {
  const gas=obj(profile.gas_heat_transfer);
  const heading='<summary>Bartz 型热侧关联：几何来源与适用范围</summary>';
  if(!Object.keys(gas).length)
    return `<details class="cool-details cool-bartz-geometry">${heading}<p class="cool-note">历史记录未保存 Bartz 曲率来源，需重新计算。不会从当前参数或其他几何字段补写本次关联实际使用的值。</p></details>`;
  const known=gas.geometry_model_id==="bartz_upstream_contour_curvature_v2" && gas.curvature_source==="geometry.throat_upstream_radius_m";
  const reference=({supplied_hot_side_state:"本次传入的热气侧状态",chamber_reference_estimate:"主室参考状态估算"})[gas.transport_reference] || gas.transport_reference || "未记录";
  const rows=[
    ["上游喉部曲率半径 Rᵤ",quantity(scaled(gas.curvature_radius_m,1e3),"mm")],
    ["喉部直径 Dₜ",quantity(scaled(gas.throat_diameter_m,1e3),"mm")],
    ["曲率因子（本次保存值）",fmt(gas.curvature_factor)],
    ["曲率来源",known ? "规范内轮廓的上游喉部圆弧" : gas.curvature_source || "未记录"],
    ["输运物性取值参考",reference],
  ].map(([label,value])=>`<tr>${cell(label)}${cell(value)}</tr>`);
  const missing=!gas.curvature_source ? "历史记录未保存 Bartz 曲率来源，需重新计算。" : "本记录的几何来源版本未识别；下表仅展示保存值，不能据此确认与规范轮廓一致。";
  return `<details class="cool-details cool-bartz-geometry">${heading}${known ? "" : `<p class="cool-note">${missing}</p>`}${table(["项目","本次记录"],rows)}<p class="cool-note">几何来源一致不代表关联式已验证；输运物性取值参考态与 σ 修正的适用性仍待校核。此处只读取本次记录，不重新计算曲率因子或补齐缺失值。</p></details>`;
}

function profileTables(profile) {
  const nodes=list(profile.nodes).map((raw,i)=>{
    const p=obj(raw);
    return `<tr>${cell(i)}${cell(fmt(p.s_m))}${cell(fmt(p.x_m))}${cell(fmt(scaled(p.p_Pa,1e-6)))}${cell(fmt(p.T_K))}${cell(fmt(scaled(p.h_Jkg,1e-3)))}${cell(fmt(scaled(p.h0_Jkg,1e-3)))}${cell(fmt(p.rho_kgm3))}${cell(fmt(p.velocity_ms))}</tr>`;
  });
  const cells=list(profile.cells).map((raw,i)=>{
    const p=obj(raw), kind=({channel:"通道",inlet_manifold:"入口集流腔",outlet_manifold:"出口集流腔"})[p.kind] || "未记录";
    return `<tr>${cell(i)}${cell(kind)}${cell(fmt(p.s_m))}${cell(fmt(p.x_m))}${cell(fmt(scaled(p.q_W_m2,1e-6)))}${cell(fmt(p.T_wall_hot_K))}${cell(fmt(p.T_wall_cold_K))}${cell(fmt(scaled(p.Q_W,1e-6)))}${cell(fmt(scaled(p.dp_friction_Pa,1e-6)))}${cell(fmt(scaled(p.dp_momentum_Pa,1e-6)))}${cell(fmt(scaled(p.dp_local_Pa,1e-6)))}</tr>`;
  });
  return `<details class="cool-details"><summary>查看保存的沿程节点与单元（${nodes.length} 个节点 / ${cells.length} 个单元）</summary><p class="cool-note">s 随冷却剂流向递增；x 为内核保存的发动机轴向位置。集流腔端口与通道节点按记录顺序保留。静焓 h 与总焓 h₀ 分列，缺失值显示 —。</p>${table(["节点","s / m","x / m","压力 / MPa","温度 / K","静焓 / kJ·kg⁻¹","总焓 / kJ·kg⁻¹","密度 / kg·m⁻³","流速 / m·s⁻¹"],nodes)}${table(["单元","类型","s / m","x / m","热流密度 / MW·m⁻²","热面壁温 / K","冷面壁温 / K","吸热 / MW","摩擦压降 / MPa","动量项 / MPa","局部压降 / MPa"],cells)}</details>`;
}

export function renderCooling(result={}) {
  const state=coolingDisplayState(result), {profile,summary}=state;
  const header=`<div class="cool-heading"><div><span class="cool-kicker">REGENERATIVE COOLING</span><h2>再生冷却 · 沿程热流与状态</h2><p>展示本次循环计算采用的同一份冷却结果；沿程曲线只读取保存记录。</p></div>${badge(state.kind,state.label)}</div>`;
  const start=`<section class="panel cooling-workspace" aria-label="再生冷却沿程分析">${header}${summaryCards(summary)}`;
  if(!state.available){
    const text=state.hasProfile ? "本记录的沿程字段不完整或版本尚不受支持，无法可靠绘制曲线。" : "本记录没有保存沿程冷却解。给定吸热量和压降的预算记录、以及旧版结果，不能据此生成沿程壁温、热流或压力曲线。";
    return `${start}<p class="cool-notice">${text} 上方仅显示记录中已有的总量；不会使用当前参数补算历史曲线。</p></section>`;
  }
  const direction=({counterflow:"逆流：冷却剂与热气流向相反",coflow:"顺流：冷却剂与热气流向相同"})[profile.flow_direction] || "流动方向未记录";
  const channelCells=list(profile.cells).map(raw=>obj(raw).kind==="channel" ? raw : {s_m:obj(raw).s_m});
  const curves=[
    chart("冷却剂压力","MPa",profile.nodes,[["p_Pa","压力",1e-6]]),
    chart("冷却剂温度","K",profile.nodes,[["T_K","温度"]]),
    chart("冷却剂比焓","kJ/kg",profile.nodes,[["h_Jkg","静焓 h",1e-3],["h0_Jkg","总焓 h₀",1e-3]]),
    chart("通道壁温","K",channelCells,[["T_wall_hot_K","热面壁温"],["T_wall_cold_K","冷面壁温"]]),
    chart("壁面热流密度","MW/m²",channelCells,[["q_W_m2","热流密度",1e-6]]),
  ].join("");
  const warnings=state.warnings.length ? `<ul class="cool-warnings">${state.warnings.map(v=>`<li>${esc(v)}</li>`).join("")}</ul>` : "";
  return `${start}<p class="cool-direction">${esc(direction)}。横轴统一采用沿冷却剂流向的弧长 s；缺失数据断线，不平滑、不补点。</p><p class="cool-notice">内核检查描述当前模型与离散方程的状态，不能替代换热关联式、通道几何和材料温限的独立验证。</p>${warnings}<div class="cool-chart-grid">${curves}</div><div class="cool-outlet-detail"><span>出口静焓 <b>${esc(quantity(scaled(summary.h_out_Jkg,1e-3),"kJ/kg"))}</b></span><span>出口总焓 <b>${esc(quantity(scaled(summary.h0_out_Jkg,1e-3),"kJ/kg"))}</b></span><span>记录最大热面壁温 <b>${esc(quantity(summary.T_wall_hot_max_K,"K"))}</b></span></div>${gasHeatTransferDetails(profile)}${diagnostics(profile)}${profileTables(profile)}</section>`;
}
