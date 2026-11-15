// Read-only handoff of saved concept requirements; never infer valve sizing.
const escape=value=>String(value??"").replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const list=value=>Array.isArray(value)?value:[];
const fmt=value=>typeof value==="number"&&Number.isFinite(value)?Number(value.toPrecision(6)).toString():"—";
const quantity=(row,key,{displayPressure=false}={})=>{
  const q=row?.quantities?.[key];
  if(q?.status!=="recorded"||typeof q.value!=="number"||!Number.isFinite(q.value))return '<span class="fs-missing">待补</span>';
  const convert=displayPressure&&q.unit==="Pa",value=convert?q.value*1e-6:q.value,unit=convert?"MPa":q.unit;
  return `<span title="${escape(q.source_path)} · ${fmt(q.value)} ${escape(q.unit)}">${fmt(value)} <small>${escape(unit)}</small></span>`;
};
const TITLES={mass_flow:"支路流量",source_pressure:"取流点压力",destination_pressure:"目标端压力",line_drop:"管路压降预算",injector_drop:"喷注器压降预算",extra_valve_drop:"额外节流需求",source_tap_temperature:"取流点温度",density:"取流点密度",source_tap_enthalpy:"取流点焓",supply_end_temperature:"供给末端温度"};
const HARDWARE_TITLES={valve_id:"阀门编号",position_and_port_ids:"安装位置与端口编号",geometry_and_hardware_revision:"几何定义与硬件版本",opening_range:"允许开度范围",opening_flow_characteristic:"开度与流量特性",characteristic_domain_and_evidence:"特性适用范围与验证依据",leakage_characteristic:"泄漏特性",fail_position:"故障时的阀位",material_compatibility:"材料与介质兼容性",actuator_and_control_assignment:"执行机构与控制分工",saturation_policy:"达到限位后的处理规则"};
const missingLabel=key=>`<span title="${escape(key)}">${escape(HARDWARE_TITLES[key]||TITLES[key]||key)}</span>`;

export function renderValveRequirements(report) {
  if(!report)return '<section class="fs-card fs-valve-requirements"><h3>概念设计 · 阀门需求交接</h3><p class="fine">此记录未包含阀门需求交接表。没有记录的部件能力保留为待补项。</p></section>';
  if(report.scope!=="concept_valve_requirements"||report.source!=="saved_calculation_only")return '<p class="fs-error">阀门需求报告类型不匹配，未作为硬件特性展示。</p>';
  const rows=list(report.rows);
  return `<section class="fs-card fs-valve-requirements" aria-label="概念设计阀门需求交接"><h3>概念设计 · 阀门需求交接</h3><p class="fine">从已保存设计记录 ${escape(report.record_id||"—")} 整理支路流量与压力预算，供后续阀门选型使用。尚未得到阀门尺寸、流量特性或实际开度。</p><p class="fs-notice">“额外节流需求”是设计点压力预算；取流点与供给末端也不等于已确定的阀门入口和出口。压降为零不能证明无需阀门。</p>${rows.length?`<div class="fs-table-wrap"><table class="fs-table"><thead><tr><th>供给支路</th><th>流量</th><th>取流点压力</th><th>目标端压力</th><th>管路压降预算</th><th>喷注器压降预算</th><th>额外节流需求</th></tr></thead><tbody>${rows.map(row=>`<tr><th scope="row">${escape(row.label||row.id)}<small>${escape(row.source)} → ${escape(row.target)}</small></th>${["mass_flow","source_pressure","destination_pressure","line_drop","injector_drop","extra_valve_drop"].map(key=>`<td>${quantity(row,key,{displayPressure:true})}</td>`).join("")}</tr>`).join("")}</tbody></table></div><details><summary>状态位置、来源字段与缺项</summary>${rows.map(row=>`<div class="fs-valve-detail"><h4>${escape(row.label||row.id)}</h4><p class="fine">取流点 ${escape(row.source||"待定义")}；供给末端 ${escape(row.temperature_location||"未记录")}。不同位置的温度与密度分开保留。</p><dl class="fs-metadata">${Object.entries(TITLES).map(([key,label])=>`<dt>${label}</dt><dd>${quantity(row,key)}<code>${escape(row.quantities?.[key]?.source_path||"来源未记录")}</code></dd>`).join("")}</dl>${list(row.missing_fields).length?`<p class="fine">待补：${list(row.missing_fields).map(missingLabel).join("；")}</p>`:""}</div>`).join("")}</details>`:'<p class="fs-empty">本记录没有可用的支路需求数据。</p>'}${list(report.missing_fields).length?`<details><summary>阀门硬件待补数据（${list(report.missing_fields).length} 项）</summary><ul>${list(report.missing_fields).map(item=>`<li>${missingLabel(item)}</li>`).join("")}</ul></details>`:""}${list(report.warnings).length?`<ul class="fs-warnings">${list(report.warnings).map(item=>`<li>${escape(item)}</li>`).join("")}</ul>`:""}${list(report.limitations).length?`<details><summary>适用范围</summary><ul>${list(report.limitations).map(item=>`<li>${escape(item)}</li>`).join("")}</ul></details>`:""}</section>`;
}
