// Saved-record gas handoff and independent screening. This view never computes physics.
const escape=value=>String(value??"").replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const object=value=>value&&typeof value==="object"&&!Array.isArray(value)?value:{};
const list=value=>Array.isArray(value)?value:[];
const finite=value=>typeof value==="number"&&Number.isFinite(value);
const number=value=>finite(value)?value.toLocaleString("zh-CN",{maximumSignificantDigits:7}):"未记录";
const scaled=(value,scale=1)=>number(finite(value)?value*scale:null);
const text=value=>typeof value==="string"?value:typeof value==="boolean"?(value?"是":"否"):finite(value)?number(value):"未记录";
const entries=value=>Array.isArray(value)?value.map((item,index)=>[item?.id??String(index),object(item)]):Object.entries(object(value));
const states={pass:"守恒通过",fail:"未通过",ok:"已计算",succeeded:"已计算",complete:"交接字段完整",completed:"已计算",consistent:"一致",inconsistent:"不一致",partial:"部分结果",warning:"需复核",unavailable:"不可用",missing:"缺少数据",incomplete:"数据不完整",not_run:"尚未运行",skipped:"未执行",error:"计算失败",not_validated:"尚未验证",unverified:"尚未验证",blocked:"条件不足",screened:"已筛查"};
Object.assign(states,{pass:"检查通过",computed:"已计算",screened_with_limitations:"已筛查 · 存在模型限制",not_applicable:"不适用",computed_outside_declared_range:"已计算 · 超出声明范围"});
const basisLabels={total:"总态",stagnation:"总态",static:"静态",reservoir:"储腔参考态",reservoir_reference:"储腔参考态",reservoir_approximation:"储腔近似",unspecified:"未定义",unknown:"未定义",missing:"未定义",total_to_total:"总—总",total_to_static:"总—静"};
Object.assign(basisLabels,{chamber_reservoir:"储腔参考态",thermodynamic:"热力学比焓",absolute:"绝对参考系",relative:"旋转参考系"});
const label=value=>basisLabels[value]||text(value);
const badge=status=>`<span class="gh-badge gh-${["fail","error","inconsistent"].includes(status)?"error":["unavailable","incomplete","missing","blocked","computed_outside_declared_range"].includes(status)?"warning":"neutral"}">${escape(states[status]||status||"未记录")}</span>`;
const note=(value,kind="note")=>`<p class="gh-message gh-${kind}">${escape(value)}</p>`;
const notes=values=>list(values).length?`<ul class="gh-notes">${values.map(value=>`<li>${escape(typeof value==="string"?value:JSON.stringify(value))}</li>`).join("")}</ul>`:"";
const table=(headers,rows)=>`<div class="gh-table-wrap"><table><thead><tr>${headers.map(value=>`<th>${escape(value)}</th>`).join("")}</tr></thead><tbody>${rows.length?rows.map(row=>`<tr>${row.map(value=>`<td>${value}</td>`).join("")}</tr>`).join(""):`<tr><td colspan="${headers.length}">未记录</td></tr>`}</tbody></table></div>`;
const detail=(title,data)=>Object.keys(object(data)).length?`<details class="gh-details"><summary>${escape(title)}</summary><pre>${escape(JSON.stringify(data,null,2))}</pre></details>`:"";
const heading=(title,status)=>`<div class="gh-heading"><h3>${escape(title)}</h3>${status?badge(status):""}</div>`;

function temperatureEvidence(value,title){
  const data=object(value),evidence=object(data.temperature_range_evidence),status=data.thermochemical_range_status;
  if(status!=="extrapolated"&&status!=="within_declared_ranges")return "";
  const warning=status==="extrapolated"?note(`${title}：发生热化学数据温区外推。计算收敛和守恒闭合不表示热力学数据在该温度下已经验证。`,"warning"):"";
  return warning+`<details class="gh-details"><summary>${escape(title)} · ${status==="extrapolated"?"外推物种与声明温区":"所查物种在声明温区内"}</summary>${note("温区检查只核对数据声明范围，不验证高压 EOS 或真实发动机精度。")}${table(["本次温度 / K"],[[escape(number(evidence.temperature_K))]])}${detail("逐物种温区证据",evidence)}</details>`;
}
function eosRangeWarning(data){
  return (data.within_all_component_declared_ranges===false?note("至少一个混合物组分超出其声明的温度或压力范围。已计算数值属于范围外结果，不能据此认定物性已验证。","warning"):"")+(data.reference_within_all_component_declared_ranges===false?note("焓熵对齐参考态超出至少一个组分的声明范围，参考平移也需要复核。","warning"):"");
}

function basis(value){
  if(typeof value==="string")return escape(label(value));
  const b=object(value),names=[["pressure","压力"],["temperature","温度"],["enthalpy","焓"],["frame","参考系"]];
  const rows=names.filter(([key])=>b[key]!=null).map(([key,name])=>`${name}：${label(b[key])}`);
  return escape(rows.join("；")||label(b.id??b.kind??b.type??b.basis??"unknown"));
}
function modelName(value){
  if(typeof value==="string")return value;
  const model=object(value);
  return [model.backend,model.eos??model.thermo_model??model.model,model.phase].filter(value=>typeof value==="string"&&value).join(" · ")||"模型身份未记录";
}
function checks(value,title="本模型内的守恒与数据检查"){
  if(!entries(value).length)return "";
  const rows=entries(value).map(([id,check])=>[
    escape(check.label??check.name??id),badge(check.status),
    escape(number(check.left_si)),escape(number(check.right_si)),
    escape(number(check.residual??check.residual_si??check.difference_si??check.difference??check.value)),
    escape(number(check.tolerance??check.tolerance_si)),escape(check.unit??"—"),escape(check.reason??check.message??check.note??"")]);
  return `<details class="gh-details"><summary>${escape(title)} · ${rows.length} 项</summary>${table(["检查项","计算状态","左侧 / 当前值","右侧 / 参考值","残差 / 记录值","保存的容差","单位","说明"],rows)}</details>`;
}
function fractions(value,title,unit="质量分数"){return `<details class="gh-details"><summary>${escape(title)} · ${Object.keys(object(value)).length} 项</summary>${table(["成分",unit],Object.entries(object(value)).sort((a,b)=>finite(b[1])&&finite(a[1])?b[1]-a[1]:0).map(([key,value])=>[escape(key),escape(number(value))]))}</details>`;}
function station(station,id){
  const data=object(station),model=data.model??data.property_model;
  const rows=[["压力",scaled(data.p_Pa??data.pressure_Pa,1e-6),"MPa"],["温度",number(data.T_K??data.temperature_K),"K"],["质量流量",number(data.mass_flow_kg_s),"kg/s"],["比焓",scaled(data.h_Jkg??data.enthalpy_J_kg,1e-3),"kJ/kg"],["比熵",number(data.entropy_JkgK),"J/(kg·K)"],["密度",number(data.density_kg_m3),"kg/m³"]];
  return `<details class="gh-station"><summary><b>${escape(data.label??id)}</b><span>${basis(data.state_basis)}</span><span>${escape(modelName(model))}</span>${data.status?badge(data.status):""}</summary><div class="gh-station-body">${table(["状态量","记录值","单位"],rows.map(row=>row.map(escape)))}${note("这里保留本记录的状态定义与化学参考焓；未记录的总／静态不自动转换。")}${object(data.state_basis).reason?note(data.state_basis.reason):""}<p class="gh-message">高压物性验证：${badge(object(model).high_pressure_validation??"unverified")}</p>${data.reason?note(data.reason,"warning"):""}${temperatureEvidence(data,"本接口气相热化学数据")}${fractions(data.composition_mass_fractions,"完整保存组成")}${fractions(data.element_mass_fractions,"元素质量分数")}${fractions(data.element_fluxes_kg_s,"元素质量流率","kg/s")}${checks(data.checks)}${detail("物性模型、焓基准与状态定义",{model,state_basis:data.state_basis,enthalpy_reference:data.enthalpy_reference,provenance:data.provenance})}${notes(data.notes)}</div></details>`;
}

const metricLabels={Z:["压缩因子 Z","1"],compressibility_factor:["压缩因子 Z","1"],density_kg_m3:["密度","kg/m³"],rho_kgm3:["密度","kg/m³"],enthalpy_J_kg:["比焓","J/kg"],h_Jkg:["比焓","J/kg"],entropy_J_kg_K:["比熵","J/(kg·K)"],s_JkgK:["比熵","J/(kg·K)"],cp_J_kg_K:["冻结组成定压比热","J/(kg·K)"],cp_JkgK:["冻结组成定压比热","J/(kg·K)"],cv_J_kg_K:["冻结组成定容比热","J/(kg·K)"],cv_JkgK:["冻结组成定容比热","J/(kg·K)"],sound_speed_m_s:["冻结声速","m/s"],a_ms:["冻结声速","m/s"],isentropic_enthalpy_drop_J_kg:["等熵焓降","J/kg"],specific_work_J_kg:["比功","J/kg"],temperature_K:["温度","K"],T_K:["温度","K"]};
Object.assign(metricLabels,{enthalpy_j_kg:["比焓","J/kg"],entropy_j_kg_k:["比熵","J/(kg·K)"],cp_j_kg_k:["冻结组成定压比热","J/(kg·K)"],cv_j_kg_k:["冻结组成定容比热","J/(kg·K)"],speed_sound_m_s:["冻结声速","m/s"],temperature_k:["温度","K"],pressure_pa:["压力","Pa"],molar_mass_kg_kmol:["摩尔质量","kg/kmol"]});
function properties(data){
  const values=object(data?.properties??data?.properties_si??data?.values??data);
  return Object.fromEntries(Object.entries(values).filter(([key])=>Object.hasOwn(metricLabels,key)));
}
function eosComparison(candidate,baseline,id){
  const data=object(candidate),base=properties(data.paired_ideal_baseline??baseline),other=properties(data.candidate??data),diff=object(data.differences??data.relative_differences_percent??data.differences_percent),reduction=object(data.reduction);
  const keys=[...new Set([...Object.keys(base),...Object.keys(other),...Object.keys(diff)])];
  const rows=keys.map(key=>{
    const [name,unit]=metricLabels[key]||[key,"—"],delta=diff[key];
    const referenceDependent=/^(h_Jkg|s_JkgK|enthalpy_|entropy_)/.test(key);
    const absolute=typeof delta==="object"?delta?.difference:finite(other[key])&&finite(base[key])?other[key]-base[key]:null;
    const relative=referenceDependent?null:typeof delta==="object"?(finite(delta?.relative_difference)?delta.relative_difference*100:delta?.relative_percent??delta?.percent):delta;
    return [escape(name),escape(number(base[key])),escape(number(other[key])),escape(number(absolute)),referenceDependent?"基准相关，不作百分比":escape(number(relative)),escape(unit)];
  });
  const reduced=data.scope==="reduced_composition_screening",expansion=object(data.frozen_expansion),idealExpansion=object(data.paired_ideal_expansion);
  const expansionRows=[["等熵焓降 / kJ/kg",scaled(idealExpansion.isentropic_drop_j_kg,1e-3),scaled(expansion.isentropic_drop_j_kg,1e-3)],["实际焓降 / kJ/kg",scaled(idealExpansion.actual_drop_j_kg,1e-3),scaled(expansion.actual_drop_j_kg,1e-3)],["实际出口温度 / K",number(idealExpansion.actual_outlet_temperature_k),number(expansion.actual_outlet_temperature_k)]];
  return `<details class="gh-details" open><summary>${escape(data.label??data.backend??data.name??data.eos??(typeof data.model==="string"?data.model:id))} ${badge(data.status)}</summary>${eosRangeWarning(data)}${reduced?note(`约简组成筛查：移除物种总质量分数 ${number(reduction.removed_mass_fraction)}，再归一化。对照采用同一约简组成的理想气体基线，不是完整燃气混合物的精度结论。`,"warning"):note(data.scope==="full_frozen_composition_screening"?"完整冻结组成物性筛查；本项不重新求化学平衡。":"完整组成适用性和对照范围须结合本条记录核实。")}${data.reason?note(data.reason,"warning"):""}${data.full_composition_status?`<p class="gh-message">完整组成可用性：${badge(data.full_composition_status)}</p>`:""}${table(["物性量","同组成理想基线","候选模型","绝对差（同右列单位）","相对差 / %","单位"],rows)}${note("热容与声速均按冻结组成定义。焓、熵只比较已对齐参考基准下的绝对差；不以参考零点相关的百分比判断误差。")}${reduced?detail("被移除的组分、焓与元素变化",reduction):""}${Object.keys(expansion).length?`<details class="gh-details" open><summary>冻结组成膨胀对照 ${badge(expansion.status)}</summary>${expansion.reason?note(expansion.reason,"warning"):""}${table(["量","同组成理想基线","候选模型"],expansionRows.map(row=>row.map(escape)))}${expansion.domain_check?note("适用性检查覆盖入口、出口与中间采样；实际路径采用固定累计效率包络。离散采样和局部单相检查不证明连续路径或全局相稳定性。"):note("历史筛查未保存膨胀路径适用性证据，请重新运行筛查。","warning")}${detail("膨胀路径声明范围与局部相态检查",expansion.domain_check)}${note("采用保存的入口、出口压力与效率口径；这是局部冻结膨胀对照，尚未重新匹配整个循环。")}</details>`:""}${notes(data.notes)}${detail("候选模型身份、适用范围及计算明细",data)}</details>`;
}
function carbon(value){
  if(!value)return note("凝聚相筛查尚未保存；缺少结果不表示无固碳风险。","warning");
  const data=object(value),result=object(data.result),fraction=result.solid_carbon_mass_fraction_total_gas_plus_solid??data.graphite_mass_fraction??data.carbon_mass_fraction??data.solid_carbon_mass_fraction??data.solid_mass_fraction;
  const rows=[["平衡石墨质量／气固总质量",finite(fraction)?`${number(fraction*100)} %`:"未记录"],["气固平衡温度",`${number(result.temperature_k??data.equilibrium_temperature_K??data.temperature_K??data.T_K)} K`],["质量残差",`${number(result.mass_residual_kg)} kg`],["总焓残差",`${number(result.enthalpy_residual_j)} J`],["本次平衡守恒检查",typeof result.conservation_passed==="boolean"?(result.conservation_passed?"通过":"未通过"):"未记录"]];
  return `<section class="gh-carbon">${heading("允许石墨相的平衡筛查",data.status)}${table(["项目","计算结果"],rows.map(row=>row.map(escape)))}${note("平衡石墨比例不是实际积碳率，也不能换算为沉积量；实际沉积还取决于动力学、停留时间和表面条件。")}${data.reason?note(data.reason,"warning"):""}${checks(data.checks,"本次筛查的质量、元素与焓闭合")}${notes(data.notes)}${detail("凝聚相筛查的固定条件、相集合与结果",data)}</section>`;
}
function screeningSection(screening){
  if(!screening)return `<section class="gh-section">${heading("设计点定量筛查")}${note("尚未保存本设计点的 EOS／凝聚相筛查。点击“运行燃气交接与筛查”计算独立对照；不会替换生产循环的物性模型。")}</section>`;
  const data=object(screening),stations=entries(data.stations);
  return `<section class="gh-section">${heading("设计点定量筛查",data.status)}${note("EOS 对照与凝聚相结果用于识别模型敏感性。筛查完成、守恒闭合、物性得到试验验证是不同结论。")}${data.reason?note(data.reason,"warning"):""}${notes(data.notes)}${stations.map(([id,item])=>`<details class="gh-station"><summary><b>${escape(item.label??id)}</b>${badge(item.status)}</summary><div class="gh-station-body">${item.reason?note(item.reason,"warning"):""}${entries(item.eos_comparisons).map(([key,value])=>eosComparison(value,item.baseline,key)).join("")||note("本记录未保存混合气体 EOS 对照；不以纯氧或纯甲烷结果代替。","warning")}${carbon(item.carbon_screen)}${notes(item.notes)}${detail("基线状态与组成",item.baseline)}</div></details>`).join("")}${!stations.length?note("本次筛查未包含可展示的站点。","warning"):""}${detail("筛查范围与模型来源",{model_scope:data.model_scope,provenance:data.provenance})}</section>`;
}
function chamberComparison(comparison){
  if(!comparison)return `<section class="gh-section">${heading("本记录主燃烧室入口复核")}${note("尚未保存实际涡轮排气的主室复核；纯推进剂理论基线不代表这项复核已经完成。")}</section>`;
  const data=object(comparison),reproduction=object(data.production_reproduction),matched=object(data.matched_cho_comparison),a=object(matched.cantera),b=object(matched.cea),target=object(data.target);
  const inletRows=entries(data.source_ports).map(([id,port])=>[escape(port.label??id),escape(number(port.mass_flow_kg_s)),escape(scaled(port.h_Jkg,1e-3)),escape(Object.keys(object(port.composition_mass_fractions)).length)]);
  const replicationRows=[["生产模型复算温度",number(reproduction.temperature_K),"K"],["原保存温度",number(reproduction.saved_temperature_K),"K"],["复算温度 − 保存温度",number(reproduction.temperature_difference_K),"K"],["复算比焓残差",number(reproduction.enthalpy_residual_Jkg),"J/kg"],["元素质量分数最大残差",number(reproduction.element_max_residual),"1"]];
  const comparisonRows=[["温度 / K",number(a.temperature_K),number(b.temperature_K)],["比焓 / J/kg",number(a.enthalpy_Jkg),number(b.enthalpy_Jkg)],["元素质量分数最大残差",number(a.element_max_residual),number(b.element_max_residual)]];
  return `<section class="gh-section">${heading("本记录主燃烧室入口复核",data.status)}${note("以本记录两股涡轮排气的完整组成、元素库存和化学参考焓进行复核；此处没有常温纯推进剂替代入口。")}${data.reason?note(data.reason,"warning"):""}${table(["实际入口","质量流量 / kg/s","化学参考比焓 / kJ/kg","已保存物种数"],inletRows)}${table(["混合后目标压力 / MPa","目标比焓 / kJ/kg","总流量 / kg/s","热损失 / W"],[[scaled(target.p_Pa,1e-6),scaled(target.h_Jkg,1e-3),number(target.mass_flow_kg_s),number(target.heat_loss_W)].map(escape)])}<details class="gh-details" open><summary>生产模型独立复算 ${badge(reproduction.status)}</summary>${temperatureEvidence(reproduction,"生产模型主室复算")}${reproduction.reason?note(reproduction.reason,"warning"):""}${table(["量","值","单位"],replicationRows.map(row=>row.map(escape)))}</details><details class="gh-details" open><summary>共同 CHO 物种集合下的 Cantera–CEA 对照 ${badge(matched.status)}</summary>${note("两端使用同一元素与焓目标，但限制了允许物种集合。该对照用于识别热化学数据／数值方法差异，不等同于生产模型精度验证。")}${matched.reason?note(matched.reason,"warning"):""}${temperatureEvidence(a,"共同物种 Cantera")}${temperatureEvidence(b,"共同物种 NASA CEA")}${table(["量","Cantera","CEA"],comparisonRows.map(row=>row.map(escape)))}${table(["CEA − Cantera 温度差 / K","相对温度差 / %"],[[number(matched.temperature_difference_K),number(matched.relative_temperature_difference_percent)].map(escape)])}${a.reason?note(`Cantera：${a.reason}`,"warning"):""}${b.reason?note(`NASA CEA：${b.reason}`,"warning"):""}${detail("共同物种与入口对齐条件",matched)}</details>${checks(data.checks)}${notes(data.notes)}${detail("实际入口、对齐条件与复核明细",data)}</section>`;
}

export function renderGasHandoff(report,screening,comparison){
  const data=object(report),stations=entries(data.stations);
  if(data.status==="not_applicable")return `<div class="gas-handoff">${heading("燃气交接与筛查","not_applicable")}${note("此项针对全流量分级燃烧（FFSC）的燃气交接与主室入口。所选循环不适用，不生成缺失端口或筛查通过结论。")}${notes(data.notes)}</div>`;
  return `<div class="gas-handoff">${heading("燃气交接与筛查")}${note("交接数据取自所选保存记录。守恒检查评价模型内的一致性；EOS 筛查评价候选模型差异，均不等同于真实发动机性能验证。")}${report?`<section class="gh-section">${heading("记录中的燃气状态与组成",data.status)}${data.reason?note(data.reason,"warning"):""}${notes(data.notes)}${stations.map(([id,item])=>station(item,id)).join("")||note("此记录没有完整站点数据。请重新运行设计点；旧结果不会用当前参数补写。","warning")}${checks(data.element_checks,"跨站元素守恒检查")}${entries(data.component_balances).map(([id,item])=>checks(item.checks,item.label??id)).join("")}${detail("交接报告来源",data.provenance??data.source)}</section>`:note("历史记录未保存燃气交接报告，当前不能认定总／静态、完整组成和焓基准已经核实。运行本项校核可检查已有记录，重新运行设计点可保存新版交接合同。","warning")}${chamberComparison(comparison)}${screeningSection(screening)}</div>`;
}
