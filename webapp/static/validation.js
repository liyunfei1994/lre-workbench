// Independent, source-bound validation workspace. No engine result is mutated here.
import {renderThermodynamicApplicability} from "./thermo-applicability.js";
import {renderThermoStations,verifiedCeaComposition} from "./thermo-stations.js";
import {renderThermoCharts, buildThermoChartSvg} from "./validation-charts.js";
import {renderPropertyComparison, buildPropertyComparisonSvg, buildPropertyComparisonCsv} from "./property-comparison.js";
const escape = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const copy = value => JSON.parse(JSON.stringify(value));
const finite = value => typeof value === "number" && Number.isFinite(value);
const number = value => finite(value) ? value.toLocaleString("zh-CN", {maximumSignificantDigits:7}) : "—";
const date = value => value ? new Date(value * 1000).toLocaleString("zh-CN", {hour12:false}) : "—";
const running = record => ["queued","running"].includes(record?.status);
const tabs = {thermochemistry:"CEA–Cantera",conservation:"系统守恒",properties:"真实流体 / REFPROP",experiment:"试验验证"};
const statusNames = {queued:"排队中",running:"运行中",succeeded:"任务已完成",failed:"执行失败",cancelled:"已取消",timed_out:"超时",interrupted:"服务中断",pass:"通过",fail:"未通过",ok:"有结果",error:"计算错误",unavailable:"不可用",not_provided:"未提供",partial:"部分结果",skipped:"未执行",warning:"需复核",calibration:"标定数据",holdout:"独立验证数据",not_comparable:"产物范围不同"};
const metricLabels = {Tc_K:["燃烧温度","K"],Cstar_m_s:["特征速度 c*","m/s"],Isp_vac_s:["真空比冲","s"],Cf_vac:["真空推力系数","1"],MW_chamber_kg_kmol:["燃气分子量","kg/kmol"],gamma_chamber:["燃烧室等熵指数 a²ρ/p","1"]};
const badge = status => `<span class="validation-badge validation-${["pass","fail","error","unavailable","running","queued"].includes(status)?status:"neutral"}">${escape(statusNames[status]||status||"待校核")}</span>`;
const message = (text, kind="note") => `<div class="validation-message validation-message-${kind}">${escape(text)}</div>`;
const table = (headers,rows) => `<div class="validation-table-wrap"><table><thead><tr>${headers.map(h=>`<th>${escape(h)}</th>`).join("")}</tr></thead><tbody>${rows.length?rows.map(row=>`<tr>${row.map(cell=>`<td>${cell}</td>`).join("")}</tr>`).join(""):`<tr><td colspan="${headers.length}" class="validation-empty">暂无数据</td></tr>`}</tbody></table></div>`;
const diagnostic = data => data ? `<details class="validation-diagnostics"><summary>该项计算明细（排查用）</summary><p class="fine">保留求解器输出，用于复现或排查问题。字段名称是内部记录，不是额外的验证结论。</p><pre>${escape(JSON.stringify(data,null,2))}</pre></details>` : "";
const notes = items => Array.isArray(items)&&items.length?`<ul class="validation-notes">${items.map(item=>`<li>${escape(typeof item==="string"?item:JSON.stringify(item))}</li>`).join("")}</ul>`:"";
const input = (name,label,value,extra="") => `<label>${escape(label)}<input data-validation-field="${name}" value="${escape(value)}" ${extra}></label>`;
const textarea = (name,label,value,extra="") => `<label>${escape(label)}<textarea data-validation-field="${name}" ${extra}>${escape(value)}</textarea></label>`;

function sourceRecord(context){
  const record=context?.record;
  return record?.status==="succeeded"&&record.result&&record.id&&record.project_id===context.project?.id ? record : null;
}
function sourceInputs(context){const record=sourceRecord(context);return record?.inputs||record?.result?.submitted_inputs||record?.result?.calculation?.inputs||record?.result?.inputs||{};}
function positive(value,label){const n=Number(value);if(String(value).trim()===""||!Number.isFinite(n)||n<=0)throw Error(`${label}必须为正的有限数值。`);return n;}
function nonnegative(value,label){const n=Number(value);if(String(value).trim()===""||!Number.isFinite(n)||n<0)throw Error(`${label}必须为非负有限数值。`);return n;}
function values(text,label){const output=String(text).split(/[,，;；\s]+/).filter(Boolean).map(value=>positive(value,label));if(!output.length||output.length>20)throw Error(`${label}须提供 1–20 个点。`);return output;}

export function scanValues(form){
  if(form.scan_mode==="range"){
    const start=positive(form.of_start,"O/F 起始值"),end=positive(form.of_end,"O/F 终止值"),count=positive(form.of_count,"O/F 点数");
    if(!Number.isInteger(count)||count<2||count>20)throw Error("O/F 扫描点数须为 2–20 的整数。");
    if(end<=start)throw Error("O/F 终止值必须大于起始值。");
    return Array.from({length:count},(_,i)=>i===count-1?end:Number((start+(end-start)*i/(count-1)).toPrecision(12)));
  }
  if(form.scan_mode==="point")return [positive(form.of_values,"O/F")];
  if(form.scan_mode!=="list"&&form.scan_mode!==undefined)throw Error("请选择有效的扫描方式。");
  return values(form.of_values,"O/F");
}

function scanSummary(form){
  try{
    const count=scanValues(form).length,eps=values(form.eps_values,"膨胀比").length,total=count*eps;
    return `<b>${count} 个 O/F 点 × ${eps} 个膨胀比 = ${total} 个工况</b><span>${total>60?"超出上限：每次最多 60 个工况。":count===1?"单个 O/F 显示数值对照；扫描多个 O/F 才能绘制趋势。":"运行后同时绘制 Isp、c*、Cf 和燃烧室温度四张图。"}</span>`;
  }catch(problem){return `<span>${escape(problem.message)}</span>`;}
}

export function propertyPoints(form){
  const bounded=(value,label,max)=>{const n=positive(value,label);if(n>max)throw Error(`${label}超出请求上限 ${max}；实际物性范围还需逐点检查。`);return n;};
  if(!form.property_mode||form.property_mode==="points"){
    const lines=String(form.property_points).split(/\r?\n/).map(line=>line.trim()).filter(Boolean);
    if(!lines.length||lines.length>20)throw Error("物性校核须提供 1–20 行状态点。");
    return lines.map((line,index)=>{
      const parts=line.split(/[,，]/).map(part=>part.trim());
      if(parts.length!==3||!["Oxygen","Methane","n-Dodecane"].includes(parts[0]))throw Error(`第 ${index+1} 行格式应为 Oxygen、Methane 或 n-Dodecane, 温度 K, 压力 MPa。`);
      return {fluid:parts[0],temperature_K:bounded(parts[1],`第 ${index+1} 行温度 / K`,10000),pressure_Pa:bounded(parts[2],`第 ${index+1} 行压力 / MPa`,10000)*1e6};
    });
  }
  if(!["temperature","pressure"].includes(form.property_mode))throw Error("请选择有效的物性扫描方式。");
  if(!["Oxygen","Methane","n-Dodecane"].includes(form.property_fluid))throw Error("请选择支持的纯流体。");
  const temperature=form.property_mode==="temperature",label=temperature?"温度 / K":"压力 / MPa";
  const start=bounded(form.property_start,`${label}起始值`,10000),end=bounded(form.property_end,`${label}终止值`,10000),count=positive(form.property_count,"扫描点数");
  const fixed=bounded(form.property_fixed,temperature?"固定压力 / MPa":"固定温度 / K",10000);
  if(end<=start)throw Error("扫描终止值必须大于起始值。");
  if(!Number.isInteger(count)||count<2||count>20)throw Error("物性扫描点数须为 2–20 的整数。");
  return Array.from({length:count},(_,i)=>{
    const value=i===count-1?end:Number((start+(end-start)*i/(count-1)).toPrecision(12));
    return {fluid:form.property_fluid,temperature_K:temperature?value:fixed,pressure_Pa:(temperature?fixed:value)*1e6};
  });
}

function propertySummary(form){
  try{
    const points=propertyPoints(form);
    return `<b>${points.length} 个状态点 · ${form.compare_refprop?"HEOS / REFPROP 同条件对照":"仅 HEOS 物性计算"}</b><span>${form.property_mode==="temperature"?"固定压力，沿温度绘制物性与偏差曲线。":form.property_mode==="pressure"?"固定温度，沿压力绘制物性与偏差曲线。":"逐点比较各项物性；不同工质、不同相态不连成一条曲线。"}</span>`;
  }catch(problem){return `<span>${escape(problem.message)}</span>`;}
}

function resetPropertySweep(form){
  const source=String(form.property_points).split(/\r?\n/).map(line=>line.split(/[,，]/).map(s=>s.trim())).find(parts=>parts[0]===form.property_fluid);
  const temperature=Number(source?.[1]),pressure=Number(source?.[2]),isTemperature=form.property_mode==="temperature";
  const center=isTemperature?temperature:pressure,fixed=isTemperature?pressure:temperature;
  form.property_start=center>0?String(Number((center*.9).toPrecision(8))):"";
  form.property_end=center>0?String(Number((center*1.1).toPrecision(8))):"";
  form.property_fixed=fixed>0?String(fixed):"";
}

export function createValidationForm(context){
  const p=sourceInputs(context),fluid=(p.propellant_pair||"lox_methane")==="lox_kerosene"?"n-Dodecane":"Methane";
  const record=sourceRecord(context),states=record?.result?.calculation?.states||record?.result?.states||{};
  const nozzleMode=record?.result?.calculation?.thermodynamic_applicability?.nozzle?.id;
  const inlet=(side,stateKey,tankKey,temperature)=>{
    const state=[states[stateKey],states[tankKey]].find(value=>finite(value?.pressure)&&value.pressure>0&&finite(value?.temperature));
    const pressure=state?.pressure??p[`${side}_tank_pressure`]??p.tank_pressure_pa??p.inlet_pressure;
    return `${state?.temperature??temperature??""}, ${finite(pressure)?pressure/1e6:""}`;
  };
  return {propellant_pair:p.propellant_pair||"lox_methane",product_policy:(p.propellant_pair||"lox_methane")==="lox_methane"?"matched_cho_gas":"native_databases",cantera_dataset:"production",
    pc_MPa:p.pc?String(p.pc/1e6):"",of_values:p.of_ratio?String(p.of_ratio):"",eps_values:p.expansion_ratio?String(p.expansion_ratio):"",
    scan_mode:"range",of_start:p.of_ratio?String(Number(Math.max(.05,p.of_ratio*.75).toPrecision(6))):"",of_end:p.of_ratio?String(Number(Math.min(20,p.of_ratio*1.25).toPrecision(6))):"",of_count:"8",
    T_fuel_K:p.fuel_inlet_temp==null?"":String(p.fuel_inlet_temp),T_ox_K:p.ox_inlet_temp==null?"":String(p.ox_inlet_temp),mode:["equilibrium","frozen_chamber","frozen_throat"].includes(nozzleMode)?nozzleMode:"equilibrium",
    property_points:`Oxygen, ${inlet("ox","lox_pump_inlet","lox_tank",p.ox_inlet_temp)}\n${fluid}, ${inlet("fuel","fuel_pump_inlet","fuel_tank",p.fuel_inlet_temp)}`,
    property_mode:"points",property_fluid:"Oxygen",property_start:"",property_end:"",property_fixed:"",property_count:"10",
    compare_refprop:true,experiment_role:"holdout",conditions_confirmed:false,condition_pc_MPa:p.pc?String(p.pc/1e6):"",
    condition_of:p.of_ratio?String(p.of_ratio):"",condition_fuel_K:p.fuel_inlet_temp==null?"":String(p.fuel_inlet_temp),
    condition_ox_K:p.ox_inlet_temp==null?"":String(p.ox_inlet_temp),condition_ambient_MPa:p.ambient_pressure==null?"":String(p.ambient_pressure/1e6),
    condition_pc_tolerance:"0",condition_of_tolerance:"0",condition_temp_tolerance:"0",condition_ambient_tolerance:"0",
    architecture_label:`${(p.propellant_pair||"lox_methane")==="lox_kerosene"?"液氧 / 煤油（正十二烷替代）":"液氧 / 甲烷"} · ${p.cycle_type==="gg"?`GG · ${p.gg_layout==="dual_shaft"?"双轴":"单轴"}`:"FFSC"}`,
    exp_thrust:"",exp_thrust_tolerance:"",exp_thrust_uncertainty:"",exp_isp:"",exp_isp_tolerance:"",exp_isp_uncertainty:"",
    exp_mdot:"",exp_mdot_tolerance:"",exp_mdot_uncertainty:""};
}

export function buildValidationRequest(context,form,tab){
  const record=sourceRecord(context);
  if(!record)throw Error("请先在计算记录中选择本项目已完成的设计点，再运行校核。");
  const p=sourceInputs(context),request={source_job_id:record.id};
  if(tab==="thermochemistry"){
    const of_values=scanValues(form),eps_values=values(form.eps_values,"膨胀比");
    if(of_values.some(value=>value<.05||value>20))throw Error("O/F 必须在 0.05–20 范围内。");
    if(eps_values.some(value=>value<1||value>1000))throw Error("膨胀比必须在 1–1000 范围内。");
    if(of_values.length*eps_values.length>60)throw Error("O/F × 膨胀比的组合不得超过 60 个点。");
    if(!["equilibrium","frozen_chamber","frozen_throat"].includes(form.mode))throw Error("请选择有效的喷管膨胀假设。");
    const productPolicy=form.product_policy||"native_databases",dataset=form.cantera_dataset||"production";
    if(!["native_databases","matched_cho_gas"].includes(productPolicy)||!["production","nasa_gas_candidate"].includes(dataset))throw Error("请选择有效的对照物种集合和热力学数据集。");
    if(productPolicy==="matched_cho_gas"&&(p.propellant_pair||"lox_methane")!=="lox_methane")throw Error("共同 CHO16 气相物种对照仅适用于液氧 / 甲烷。");
    if(dataset==="nasa_gas_candidate"&&productPolicy!=="matched_cho_gas")throw Error("候选 NASA 数据只在共同 CHO16 气相集合下对照。");
    request.thermochemistry={propellant_pair:p.propellant_pair||"lox_methane",pc_pa:positive(form.pc_MPa,"燃烧室压力")*1e6,
      of_values,eps_values,T_fuel_K:positive(form.T_fuel_K,"燃料入口温度"),T_ox_K:positive(form.T_ox_K,"氧化剂入口温度"),mode:form.mode,
      product_policy:productPolicy,cantera_dataset:dataset,methane_thermo_dataset:p.methane_thermo_dataset||"gri30"};
  }else if(tab==="properties"){
    request.properties={points:propertyPoints(form),compare_refprop:!!form.compare_refprop};
  }else if(tab==="experiment"){
    if(!form.conditions_confirmed)throw Error("请核对并确认试验工况；从方案复制的条件不能自动视为试验条件。");
    if(!["holdout","calibration"].includes(form.experiment_role))throw Error("请选择试验数据用途。");
    const conditions={propellant_pair:p.propellant_pair||"lox_methane",cycle_type:p.cycle_type};
    if(p.cycle_type==="gg")conditions.gg_layout=p.gg_layout||"single_shaft";
    for(const [key,field,unit,tolerance] of [["pc","condition_pc_MPa","MPa","condition_pc_tolerance"],["of_ratio","condition_of","1","condition_of_tolerance"],
      ["fuel_inlet_temp","condition_fuel_K","K","condition_temp_tolerance"],["ox_inlet_temp","condition_ox_K","K","condition_temp_tolerance"]]){
      conditions[key]={value:positive(form[field],"试验工况"),unit};
      conditions[key].tolerance=nonnegative(form[tolerance],"工况容差");
    }
    if(String(form.exp_thrust).trim()!==""||String(form.exp_isp).trim()!=="")conditions.ambient_pressure={value:nonnegative(form.condition_ambient_MPa,"试验环境压力"),unit:"MPa",tolerance:nonnegative(form.condition_ambient_tolerance,"环境压力容差")};
    const measurements=[];
    for(const [name,metric,unit] of [["exp_thrust","thrust_N","kN"],["exp_isp","Isp_s","s"],["exp_mdot","mdot_total_kg_s","kg/s"]]){
      if(String(form[name]).trim()==="")continue;
      const measurement={metric,value:nonnegative(form[name],"实测值"),unit};
      for(const key of ["tolerance","uncertainty"])if(String(form[`${name}_${key}`]).trim()!=="")measurement[key]=nonnegative(form[`${name}_${key}`],key==="tolerance"?"判定容差":"测量不确定度");
      if(measurement.tolerance===undefined&&measurement.uncertainty===undefined)throw Error("每个实测指标须填写判定容差或测量不确定度，不能自动假定误差界限。");
      measurements.push(measurement);
    }
    if(!measurements.length)throw Error("请至少填写一个真实试验测量值；空白不会被当作零或通过。");
    request.experiment={role:form.experiment_role,conditions,measurements};
  }else if(tab!=="conservation")throw Error("未知校核类型。");
  return request;
}

function capabilityCards(capabilities){
  if(!capabilities)return message("正在读取计算后端与依赖状态。", "note");
  return `<div class="validation-capabilities">${[["cantera","Cantera"],["cea","NASA CEA"],["coolprop","CoolProp"],["refprop","REFPROP"]].map(([key,label])=>{
    const c=capabilities[key]||{},available=c.available===true;
    const purpose={cantera:"燃烧平衡与喷管膨胀",cea:"独立热化学对照 · RocketCEA 接口",coolprop:"当前循环物性：HEOS 后端",refprop:"纯流体物性对照 · 经 CoolProp 调用"}[key];
    return `<div class="validation-capability ${available?"is-available":"is-unavailable"}"><span>${label}</span><b>${available?escape((key==="cea"?"RocketCEA ":"")+(c.version||"可用")):"不可用"}</b><small>${escape(purpose)}</small><small>${escape(c.reason||(!available?"未检测到可用后端":"本机当前可用"))}</small></div>`;
  }).join("")}</div>`;
}
function provenancePanel(provenance){
  if(!provenance)return message("该校核记录未保存所用软件版本和数据文件；不能用本机当前版本补写。", "warning");
  const rows=[["Cantera 版本",provenance.cantera_version,"计算化学平衡及喷管膨胀"],
    ["RocketCEA 版本",provenance.rocketcea_version,"调用 NASA CEA 的 Python 接口版本，不是 CEA 数据库版本"],
    ["CoolProp 版本",provenance.coolprop_version,"通过 HEOS 计算纯推进剂入口焓；不是燃烧产物数据库"],
    ["Cantera 热化学数据文件",provenance.mechanism,"物种热力学多项式的来源文件"],
    ["对照产物范围",provenance.product_policy==="matched_cho_gas"?"共同 16 种中性气体":provenance.product_policy==="native_databases"?"各程序原有产物范围":"未记录","规定允许生成哪些化学物种及物态"],
    ["对照数据选择",provenance.cantera_dataset==="nasa_gas_candidate"?"候选 NASA 数据":provenance.cantera_dataset==="production"?"来源设计记录的数据":"未记录","独立校核的选择，不修改已保存的设计结果"],
    ["Cantera 计算相标识",provenance.phase,"受限基准由共同物种构建；名称不代表数据文件版本。旧记录可能保存来源文件的相名"],
    ["REFPROP 在本项中的用途","未参与此热化学对照","请在“真实流体 / REFPROP”中比较纯流体物性"]];
  const hashes=[[provenance.mechanism||"Cantera 数据文件",provenance.mechanism_sha256],
    ...(provenance.cea_database_files||[]).map(file=>[`CEA 数据库 ${file.name}`,file.sha256])];
  return `<details class="validation-data-details"><summary>本次校核使用的软件与数据</summary><p class="fine">以下来自所选校核记录，说明当时具体用了什么。页面顶部显示的是本机当前可用的软件，两者可能不同。</p>${table(["项目","本次记录","用途与说明"],rows.map(row=>row.map(value=>escape(value??"未记录"))))}<details class="validation-diagnostics"><summary>数据文件校验码（复现与排查用）</summary><p class="fine">SHA-256 是文件内容的校验码：用于判断两次计算是否用了完全相同的文件。它不是精度评分，也不是软件版本。trans.lib 是随 CEA 安装的输运数据文件，本项未据此进行输运性质对照。</p>${table(["数据文件","SHA-256 校验码"],hashes.map(row=>row.map(value=>escape(value??"未记录"))))}</details></details>`;
}

function comparisonExplanation(result){
  const policy=result.request?.product_policy||result.provenance?.product_policy||result.scope?.product_policy;
  const common=policy==="matched_cho_gas",native=policy==="native_databases";
  const explanation=common
    ? "本次把两边允许生成的产物都限定为相同的 16 种中性气体，并使用相同入口焓及膨胀假设，便于检查热力学数据与数值方法的差异。物种相同不代表热力学系数完全相同，也不验证积碳或电离。"
    : native ? "Cantera 只在所选文件定义的气体物种中求平衡；CEA 按自己的产品数据库选择产物，还可能允许固体或液体（如固体碳）。允许的产物清单不同，会改变平衡组成及性能。可以比较两套模型的总体差异，但不能把差值全部解释成某个软件的计算误差；也不表示本次一定生成了固体碳。" : "本条历史记录未完整保存产物范围设置，请结合记录中的对齐说明判断；不能推断两边采用了相同产物。重新运行可保存完整条件。";
  const original="Cantera 为指定机制的单一理想气相；CEA 使用默认产品数据库，物种与凝聚相范围未统一。";
  const extra=[...new Set((result.points||[]).flatMap(point=>point.comparable?.notes||[]))].filter(note=>note!==original&&note!==result.scope?.phase_policy);
  return `<div class="validation-message validation-message-note"><b>${common?"相同物种范围的受限基准":native?"产物范围未统一，差值包含模型差异":"产物范围未完整记录"}</b><p>${explanation}</p><p>“物种”指 H₂O、CO₂、CO、H₂ 等不同化学成分；“凝聚相”指固体或液体。产物是否实际出现，需看通过读取校核的组分记录。</p>${notes(extra)}</div>`;
}

function sourceProvenancePanel(source){
  if(!source)return "";
  const environment=source.environment||{};
  return `<details class="validation-data-details"><summary>本次校核关联的设计记录</summary><p class="fine">以下信息用于追溯输入来自哪个已保存的设计点；不是另一组校核结果。完整机器可读记录可用上方“导出 JSON”保存。</p>${table(["项目","记录值"],[["设计记录编号",source.job_id],["方案版本",source.revision==null?"未记录":`v${source.revision}`],["设计记录时间",date(source.created)],["平台程序版本",source.application_version]].map(row=>row.map(value=>escape(value??"未记录"))))}<details class="validation-diagnostics"><summary>输入与结果校验码（复现用）</summary>${table(["内容","SHA-256 校验码"],[["来源设计输入",source.inputs_sha256],["来源设计结果",source.result_sha256],["来源计算环境",environment.fingerprint]].map(row=>row.map(value=>escape(value??"未记录"))))}</details></details>`;
}
function comparisonRangeEvidence(points){
  const labels={chamber:"燃烧室",throat:"喉部",exit:"出口"};
  const rows=points.flatMap((point,index)=>["cantera","cea"].flatMap(backend=>{
    const ranges=point[backend]?.diagnostics?.station_temperature_ranges;
    if(backend==="cea"&&ranges&&!verifiedCeaComposition(point))return [[escape(index+1),"NASA CEA","—","—","组分读取证据不足，需重新校核","—","—"]];
    return Object.entries(ranges||{}).map(([station,range])=>{
      const outside=Object.entries(range.outside_species||{}),missing=Array.isArray(range.unverified_species)?range.unverified_species:[];
      return [escape(index+1),escape(backend==="cea"?"NASA CEA":"Cantera"),escape(labels[station]||station),escape(number(range.temperature_K)),
        escape(outside.length?"发生温区外推":missing.length?"存在未核实物种":"所查组成未发现外推"),
        escape(outside.map(([name,limits])=>`${name}：${number(limits.min_K)}–${number(limits.max_K)} K，Y=${number(limits.mass_fraction)}`).join("；")||"—"),escape(missing.join("、")||"—")];
    });
  }));
  return `<details class="validation-data-details"><summary>各截面热化学数据温区检查</summary>${rows.length?table(["点","后端","截面","温度 / K","温区检查","外推物种与范围","未核实物种"],rows):message("本记录未保存逐截面温区信息，不推断数据有效性。")}${message("历史 CEA 温区诊断依赖组分读取，缺少新版核验证据时暂停展示。温区覆盖只检查热力学多项式声明范围；两后端均为理想气体模型，高压真实气体效应须另行核实。")}</details>`;
}

function thermochemistryResult(result,metric,selection){
  if(!result)return message("尚未运行 CEA–Cantera 校核。结果只评价指定的独立热化学基准。", "note");
  const points=Array.isArray(result.points)?result.points:[],counts=result.counts||{};
  const availability=Object.entries(result.availability||{}).filter(([,value])=>value.available===false).map(([name,value])=>message(`${name.toUpperCase()} 不可用：${value.reason||"缺少依赖"}`,"error")).join("");
  const rows=points.map((point,index)=>{
    const a=point.cantera||{},b=point.cea||{},difference=point.differences?.[metric];
    const comparable=point.comparable?.conditions_aligned===true&&point.comparable?.products_aligned===true;
    return [escape(index+1),`${escape(number(point.of))} / ${escape(number(point.eps))}`,
      a.status==="ok"?escape(number(a.metrics?.[metric])):`${badge(a.status)}<small>${escape(a.reason||"")}</small>`,
      b.status==="ok"?escape(number(b.metrics?.[metric])):`${badge(b.status)}<small>${escape(b.reason||"")}</small>`,
      escape(number(difference?.absolute)),escape(number(difference?.relative_percent)),
      `<span class="validation-badge">${a.status!=="ok"||b.status!=="ok"?"缺少双端结果":comparable?"条件与物种已对齐":point.comparable?.conditions_aligned===true?"产物范围不同":"对齐信息未记录"}</span>`];
  });
  return availability+`<div class="validation-result-summary"><b>${escape(counts.paired_success??0)} / ${escape(counts.requested??points.length)} 点具有双后端结果</b><span>已保存的校核结果 · 刷新页面不会重算</span></div>`+
    `<div class="validation-chart-toolbar"><div><h3>性能对比曲线</h3></div><div><button data-validation-action="export-png">导出 PNG</button><button data-validation-action="export-svg">导出 SVG</button></div></div>`+
    comparisonExplanation(result)+renderThermoCharts(result)+
    `<details class="validation-data-details" open><summary>逐点数据与差值 · ${points.length} 个工况</summary><label class="validation-metric">表格指标<select data-validation-metric>${Object.entries(metricLabels).map(([key,[label,unit]])=>`<option value="${key}" ${metric===key?"selected":""}>${label} / ${unit}</option>`).join("")}</select></label>`+
    table(["点","O/F / ε",`Cantera / ${metricLabels[metric]?.[1]||""}`,`CEA / ${metricLabels[metric]?.[1]||""}`,"差值 CEA−Cantera","相对差 / %","比较条件"],rows)+`</details>`+
    message("相对差 = (CEA − Cantera) / |Cantera| × 100%。曲线和差值用于定位模型差异；小差值不等于真实发动机的预测误差。")+
    renderThermoStations(result,selection)+comparisonRangeEvidence(points)+provenancePanel(result.provenance);
}

function comparisonControls(form){
  const matched=form.product_policy==="matched_cho_gas";
  return `<div class="validation-scope-help" data-comparison-advanced><label>两边允许生成的产物<select data-validation-field="product_policy"><option value="matched_cho_gas"${matched?" selected":""}${form.propellant_pair!=="lox_methane"?" disabled":""}>相同 16 种气体 · 受限基准（甲烷推荐）</option><option value="native_databases"${matched?"":" selected"}>各自数据库的产物范围 · 模型差异对照</option></select></label><p>${matched?"相同物种清单便于检查热力学数据差异；仍须采用相同入口焓和膨胀假设。":"Cantera 只考虑所选文件中的气体；CEA 的产物清单及允许的相可能不同。结果差异包含这部分建模差别。"}</p>${matched?`<details><summary>高级：替换本次独立对照的数据</summary><label>Cantera 对照数据<select data-validation-field="cantera_dataset"><option value="production"${form.cantera_dataset==="production"?" selected":""}>来源设计的数据（限定共同物种）</option><option value="nasa_gas_candidate"${form.cantera_dataset==="nasa_gas_candidate"?" selected":""}>候选 NASA 数据 · nasa_gas.yaml</option></select></label><p>共同集合只用于液氧 / 甲烷的受限独立基准：16 种中性气体，不含凝聚相和离子；不能据此验证富燃预燃室，也不会替换循环求解的数据。</p>${form.cantera_dataset==="nasa_gas_candidate"?"<p>候选数据采用 NASA TM-4513（1993）的 NASA7 多项式，所选物种声明 200–6000 K。它与当前 CEA 数据库不同，不是 CEA 的 NASA9 数据，也不因温区更宽就自动更准确。</p>":""}</details>`:""}</div>`;
}
function checkResults(section,kind){
  if(!section)return message(kind==="experiment"?"尚无试验测量记录。请填写独立测量与工况，再进行试验验证。":"尚未运行本项校核。", "note");
  const checks=Array.isArray(section.checks)?section.checks:[];
  const labels={condition_ambient_pressure:"可比工况 · 环境压力",condition_cycle_type:"可比工况 · 循环方式",condition_fuel_inlet_temp:"可比工况 · 燃料入口温度",condition_gg_layout:"可比工况 · GG 轴系布局",condition_of_ratio:"可比工况 · 总体氧燃比 O/F",condition_ox_inlet_temp:"可比工况 · 氧化剂入口温度",condition_pc:"可比工况 · 燃烧室压力",condition_propellant_pair:"可比工况 · 推进剂组合",condition_fuel_tank_pressure:"可比工况 · 燃料贮箱压力",condition_ox_tank_pressure:"可比工况 · 氧化剂贮箱压力"};
  const names={gg:"燃气发生器循环（GG）",ffsc:"全流量分级燃烧（FFSC）",single_shaft:"单轴",dual_shaft:"双轴",lox_methane:"液氧 / 甲烷",lox_kerosene:"液氧 / 正十二烷（煤油替代）",liquid:"液相",gas:"气相",supercritical:"超临界",supercritical_gas:"超临界气相",supercritical_liquid:"超临界液相",critical_point:"临界点"};
  const named=value=>Object.hasOwn(names,value)?names[value]:value;
  const format=(value,unit)=>`${typeof value==="string"?escape(named(value)):escape(number(value))}${finite(value)&&unit?` <small>${escape(unit)}</small>`:""}`;
  let headers,rows;
    headers=["校核项","状态",kind==="experiment"?"预测值":"左侧 / 当前值",kind==="experiment"?"实测值":"右侧 / 参考值","差值 / 容差","说明"];
    rows=checks.map(check=>[escape(Object.hasOwn(labels,check.id)?labels[check.id]:check.label||check.metric||check.id||"检查项"),badge(check.status),
      format(check.predicted_si??check.left_si??check.value??check.predicted,check.unit),format(check.measured_si??check.right_si??check.measured,check.unit),
      `${format(check.difference_si,check.unit)}<small>容差 ${escape(number(check.tolerance_si??check.tolerance))}${check.unit?` ${escape(check.unit)}`:""}</small>`,
      `${escape(check.reason||check.message||check.note||"")}${diagnostic(check)}`]);
  const title={conservation:"系统守恒",properties:"真实流体物性",experiment:"试验验证"}[kind]||"校核结果";
  return `<div class="validation-result-summary">${badge(section.status)}<span>${title}</span></div>`+
    notes(section.notes)+table(headers,rows);
}
export function renderValidationResult(result,tab,metric="Isp_vac_s",propertySelection={},thermoSelection={}){
  if(!Object.hasOwn(tabs,tab))return message("未知校核类型。","error");
  if(tab==="properties")return renderPropertyComparison(result?.properties,propertySelection);
  return tab==="thermochemistry"?thermochemistryResult(result?.thermochemistry,metric,thermoSelection):checkResults(result?.[tab],tab);
}

function controls(form,tab){
  if(tab==="thermochemistry")return `<div class="validation-scan-fields"><label>计算方式<select data-validation-field="scan_mode">${[["range","O/F 等间隔扫描"],["point","单点校核"],["list","自定义 O/F 点"]].map(([key,label])=>`<option value="${key}" ${form.scan_mode===key?"selected":""}>${label}</option>`).join("")}</select></label>${input("pc_MPa","燃烧室压力 / MPa",form.pc_MPa,'type="number" min="0" step="any"')}${form.scan_mode==="range"?`${input("of_start","O/F 起始值",form.of_start,'type="number" min="0.05" max="20" step="any"')}${input("of_end","O/F 终止值",form.of_end,'type="number" min="0.05" max="20" step="any"')}${input("of_count","O/F 点数",form.of_count,'type="number" min="2" max="20" step="1"')}`:input("of_values",form.scan_mode==="point"?"O/F 混合比":"O/F 点（逗号分隔）",form.of_values)}${input("eps_values","膨胀比 ε（可填多个）",form.eps_values,'placeholder="如 40,80,120"')}${input("T_fuel_K","燃料入口温度 / K",form.T_fuel_K,'type="number" min="0" step="any"')}${input("T_ox_K","氧化剂入口温度 / K",form.T_ox_K,'type="number" min="0" step="any"')}<label>喷管膨胀假设<select data-validation-field="mode">${[["equilibrium","平衡流"],["frozen_chamber","燃烧室冻结"],["frozen_throat","喉部冻结"]].map(([key,label])=>`<option value="${key}" ${form.mode===key?"selected":""}>${label}</option>`).join("")}</select></label></div>${comparisonControls(form)}<div class="validation-scan-summary" data-validation-scan-summary>${scanSummary(form)}</div><details class="validation-scope-help"><summary>扫描条件说明</summary><p>扫描初值以来源设计点的 O/F 为中心展开，可自行修改；膨胀比、压力和入口温度复制自来源记录。若要复现原桌面的 24 点扫描，可填 O/F 2.5–5.0、8 点，膨胀比 40,80,120。</p><p>用于独立的推进剂燃烧与喷管基准；不等同于 GG 冷却后的主室入口或 FFSC 预燃室排气。每次最多 60 个 O/F × ε 组合。</p></details>`;
  if(tab==="conservation")return message("按所选已完成设计记录检查质量流量与轴功率闭合。仅检查系统数值一致性，不替代热化学模型检查或试验验证；当前草稿不参与本次校核。");
  if(tab==="properties")return `<div class="validation-property-inputs"><label>分析方式<select data-validation-field="property_mode">${[["points","系统状态点对比"],["temperature","温度扫描 · 定压"],["pressure","压力扫描 · 定温"]].map(([key,label])=>`<option value="${key}" ${form.property_mode===key?"selected":""}>${label}</option>`).join("")}</select></label>${form.property_mode==="points"?textarea("property_points","状态点：每行 工质, 温度 K, 压力 MPa",form.property_points,'rows="3" spellcheck="false"'): `<label>扫描工质<select data-validation-field="property_fluid">${[["Oxygen","氧气 / 液氧"],["Methane","甲烷"],["n-Dodecane","正十二烷（煤油替代）"]].map(([key,label])=>`<option value="${key}" ${form.property_fluid===key?"selected":""}>${label}</option>`).join("")}</select></label>${input("property_start",form.property_mode==="temperature"?"起始温度 / K":"起始压力 / MPa",form.property_start,'type="number" min="0" step="any"')}${input("property_end",form.property_mode==="temperature"?"终止温度 / K":"终止压力 / MPa",form.property_end,'type="number" min="0" step="any"')}${input("property_count","扫描点数",form.property_count,'type="number" min="2" max="20" step="1"')}${input("property_fixed",form.property_mode==="temperature"?"固定压力 / MPa":"固定温度 / K",form.property_fixed,'type="number" min="0" step="any"')}`}</div><label class="validation-checkbox"><input data-validation-field="compare_refprop" type="checkbox" ${form.compare_refprop?"checked":""}>同时对比本机 REFPROP（通过 CoolProp 接口调用）</label><p class="fine">当前循环使用 CoolProp 的 HEOS 模型计算纯推进剂物性；这里用 REFPROP 作独立数据对照。两者都不替代 Cantera / CEA 的燃烧产物化学平衡。</p><div class="validation-scan-summary" data-property-scan-summary>${propertySummary(form)}</div><details class="validation-scope-help"><summary>状态点来源与物性对比说明</summary><p>支持 Oxygen、Methane、n-Dodecane，每次最多 20 点。状态点初值复制自所选设计记录的主泵入口，缺失时使用已记录的贮箱或供给输入；缺项留空。切换扫描方式或工质时，扫描初值设为上述状态的 ±10%，可自行修改；未包含该工质时请填写条件。</p><p>逐点检查两端的实际适用范围与相态，失败点保留原因。焓、熵采用共同参考态后的增量对比；原始绝对值仅供查阅。差异不设自动精度验收阈值。REFPROP 不可用时不会生成替代数据。</p></details>`;
  return `<label>数据用途<select data-validation-field="experiment_role"><option value="holdout" ${form.experiment_role==="holdout"?"selected":""}>独立验证数据（未用于标定）</option><option value="calibration" ${form.experiment_role==="calibration"?"selected":""}>标定数据（不能作为独立验证）</option></select></label>`+
    message(`来源架构：${form.architecture_label}`)+`<div class="validation-form-grid">`+
    input("condition_pc_MPa","试验燃烧室压力 / MPa",form.condition_pc_MPa)+input("condition_of","试验 O/F",form.condition_of)+
    input("condition_fuel_K","试验燃料入口温度 / K",form.condition_fuel_K)+input("condition_ox_K","试验氧化剂入口温度 / K",form.condition_ox_K)+
    input("condition_ambient_MPa","试验环境压力 / MPa",form.condition_ambient_MPa)+input("condition_pc_tolerance","压力匹配容差 / MPa",form.condition_pc_tolerance)+
    input("condition_of_tolerance","O/F 匹配容差",form.condition_of_tolerance)+input("condition_temp_tolerance","入口温度匹配容差 / K",form.condition_temp_tolerance)+
    input("condition_ambient_tolerance","环境压力匹配容差 / MPa",form.condition_ambient_tolerance)+
    `</div><label class="validation-checkbox"><input data-validation-field="conditions_confirmed" type="checkbox" ${form.conditions_confirmed?"checked":""}>已核对试验工况与所示推进剂、循环、轴系；以上条件确为该次试验条件。</label>`+
    table(["测量指标","实测值","判定容差（绝对）","测量不确定度（绝对）"],[["exp_thrust","环境压力下推力","kN"],["exp_isp","环境压力下比冲","s"],["exp_mdot","总流量","kg/s"]].map(([name,label,unit])=>[
      escape(`${label} / ${unit}`),
      input(name,"",form[name],`type="number" min="0" step="any" aria-label="${escape(`${label}实测值 / ${unit}`)}"`),
      input(`${name}_tolerance`,"",form[`${name}_tolerance`],`type="number" min="0" step="any" aria-label="${escape(`${label}判定容差 / ${unit}`)}"`),
      input(`${name}_uncertainty`,"",form[`${name}_uncertainty`],`type="number" min="0" step="any" aria-label="${escape(`${label}测量不确定度 / ${unit}`)}"`)]))+
    message("未填实测值的行不参与判断。每个指标必须给出容差或不确定度；同时给出时按显式容差判定。工况容差 0 表示要求精确匹配，请按试验精度修改。复制条件不代表已匹配。");
}

export function mountValidation(container,{api,getContext,notify=()=>{}}){
  let capabilities=null,records=[],selected=null,form={},tab="thermochemistry",metric="Isp_vac_s",projectId="",sourceKey="",error="",busy=false,live=true,timer=null,sequence=0,propertySelection={},thermoSelection={};
  function stop(){if(timer!==null)clearTimeout(timer);timer=null;}
  function syncContext(){
    const context=getContext()||{},id=context.project?.id||"",source=sourceRecord(context),key=source?.id||"";
    if(id!==projectId){projectId=id;records=[];selected=null;sourceKey="";error="";}
    if(key!==sourceKey){propertySelection={};thermoSelection={};sourceKey=key;form=createValidationForm(context);}
    if(!Object.keys(form).length)form=createValidationForm(context);
    return context;
  }
  function render(){
    const context=syncContext(),source=sourceRecord(context),hasSource=!!source,activeCount=records.filter(running).length;
    const sourceName=source?`方案 v${source.revision} · 设计记录 ${source.id} · ${date(source.created)}`:"未选择已完成的设计记录";
    const stale=source&&(context.dirty||source.revision!==context.project.revision);
    const selectedSource=selected?.source_job_id||selected?.request?.source_job_id||selected?.result?.source?.job_id;
    container.innerHTML=`<section class="validation-workspace" aria-label="热力学与系统校核"><div class="validation-heading"><div><span class="validation-kicker">MODEL VERIFICATION</span><h2>热力学与系统校核</h2><p>独立基准 · 守恒审计 · 物性边界 · 试验证据</p></div><button data-validation-action="refresh" ${busy?"disabled":""}>重新读取记录</button></div>${capabilityCards(capabilities)}<div class="validation-source"><b>输入来源</b><span>${escape(sourceName)}</span></div>${!hasSource?message("先选择本项目已完成的设计点。校核不会保存草稿，也不会覆盖系统计算结果。","warning"):stale?message("当前草稿或方案版本与该设计记录不同。本次校核使用上述已保存的计算快照；不会将新草稿混入旧结果。","warning"):""}${hasSource ? `<details class="validation-diagnostics"><summary>设计点的热力学适用性与假设</summary>${renderThermodynamicApplicability(source.result)}</details>` : ""}<div class="validation-tabs" role="tablist" aria-label="校核类别">${Object.entries(tabs).map(([key,label])=>`<button role="tab" aria-selected="${tab===key}" data-validation-tab="${key}" class="${tab===key?"is-active":""}">${label}</button>`).join("")}</div><div class="validation-layout ${["thermochemistry","properties"].includes(tab)?"validation-thermo-layout":""}"><section class="validation-settings"><h3>${["thermochemistry","properties"].includes(tab)?"分析条件":"校核条件"}</h3><fieldset ${busy?"disabled":""}>${controls(form,tab)}</fieldset><div class="validation-actions"><button class="primary" data-validation-action="run" ${!hasSource||busy||activeCount>=2?"disabled":""}>${busy?"正在提交…":(tab==="thermochemistry"&&form.scan_mode!=="point")||(tab==="properties"&&form.property_mode!=="points")?"运行扫描对比":"运行本项校核"}</button>${activeCount>=2?'<span class="validation-help">已有两个校核任务等待或运行，请稍后提交。</span>':""}</div></section><section class="validation-output"><div class="validation-record-toolbar"><label>校核记录<select data-validation-record ${busy?"disabled":""}><option value="">选择记录</option>${records.map(record=>`<option value="${escape(record.id)}" ${selected?.id===record.id?"selected":""}>v${escape(record.revision)} · ${escape(date(record.created))} · ${escape(statusNames[record.status]||record.status)}</option>`).join("")}</select></label><button data-validation-action="export" ${selected?"":"disabled"}>导出 JSON</button><button data-validation-action="cancel" ${running(selected)&&!busy?"":"disabled"}>取消任务</button></div>${error?message(error,"error"):""}${selected?`<div class="validation-run-status">${badge(selected.status)}<span>校核记录 ${escape(selected.id)} · 方案 v${escape(selected.revision)}</span></div><p class="validation-help">刷新页面或“重新读取记录”只更新显示。更新校核结果请点击上方“运行扫描对比／运行本项校核”；“运行设计点”只生成新的系统设计记录。</p>`:""}${selected?.error?message(typeof selected.error==="string"?selected.error:JSON.stringify(selected.error),"error"):""}${selectedSource&&selectedSource!==source?.id?message("当前展示的是另一设计记录的校核结果；更改校核条件不会改写该历史记录。","warning"):""}${renderValidationResult(selected?.result,tab,metric,propertySelection,thermoSelection)}${selected?.result?.source?sourceProvenancePanel(selected.result.source):""}</section></div></section>`;
  }
  function schedule(){stop();if(live&&records.some(running))timer=setTimeout(()=>{refresh().catch(()=>{});},1800);}
  async function refresh(){
    live=true;stop();const context=syncContext(),id=context.project?.id,token=++sequence;
    render();if(!id)return;
    const results=await Promise.allSettled([capabilities?Promise.resolve(capabilities):api("/validation/capabilities"),api(`/projects/${encodeURIComponent(id)}/validations`)]);
    if(!live||token!==sequence||getContext()?.project?.id!==id)return;
    error="";
    if(results[0].status==="fulfilled")capabilities=results[0].value;else error=results[0].reason?.message||"无法读取校核依赖状态。";
    if(results[1].status==="fulfilled")records=Array.isArray(results[1].value)?results[1].value:[];else error=results[1].reason?.message||"无法读取校核记录。";
    const next=records.find(record=>record.id===selected?.id)||records[0];
    if(next){
      try{const detail=await api(`/validations/${encodeURIComponent(next.id)}`);if(!live||token!==sequence||getContext()?.project?.id!==id)return;selected=detail;}
      catch(problem){if(!live||token!==sequence)return;error=problem.message;}
    }
    render();schedule();
  }
  async function chooseRecord(id){
    thermoSelection={};
    const token=++sequence,project=projectId;stop();
    if(!id){selected=null;render();return;}
    try{const detail=await api(`/validations/${encodeURIComponent(id)}`);if(live&&token===sequence&&projectId===project&&getContext()?.project?.id===project){selected=detail;error="";render();schedule();}}
    catch(problem){if(live&&token===sequence){error=problem.message;render();}}
  }
  async function run(){
    if(busy)return;
    const context=syncContext(),source=sourceRecord(context);
    let request;try{request=buildValidationRequest(context,form,tab);}catch(problem){error=problem.message;render();return;}
    ++sequence;stop();busy=true;error="";render();const id=projectId;
    try{const created=await api(`/projects/${encodeURIComponent(id)}/validations`,"POST",request);if(!live||getContext()?.project?.id!==id)return;selected={project_id:id,revision:source.revision,request,status:"queued",...created};records=[selected,...records.filter(record=>record.id!==created.id)];notify("校核任务已提交；系统设计结果保持原记录。");}
    catch(problem){if(getContext()?.project?.id===id)error=problem.message;}finally{busy=false;if(live){render();schedule();}}
  }
  async function cancel(){
    if(busy||!running(selected))return;const id=selected.id,project=projectId;busy=true;render();
    const stillCurrent=()=>live&&projectId===project&&getContext()?.project?.id===project;
    try{await api(`/validations/${encodeURIComponent(id)}/cancel`,"POST",{});if(!stillCurrent())return;notify("已请求取消校核任务。");await refresh();}
    catch(problem){if(stillCurrent())error=problem.message;}finally{busy=false;if(stillCurrent())render();}
  }
  function exportRecord(){if(!selected)return;const url=URL.createObjectURL(new Blob([JSON.stringify(selected,null,2)],{type:"application/json"}));const link=document.createElement("a");link.href=url;link.download=`validation-${String(selected.id).replace(/[^a-zA-Z0-9_-]/g,"_")}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  function download(blob,name){const url=URL.createObjectURL(blob),link=document.createElement("a");link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  async function exportCharts(format,kind="thermochemistry"){
    const section=selected?.result?.[kind];if(!(kind==="properties"?section?.checks?.length:section?.points?.length))return;
    const name=`${kind}-${String(selected.id).replace(/[^a-zA-Z0-9_-]/g,"_")}`;
    try{
      if(format==="csv"){download(new Blob([buildPropertyComparisonCsv(section)],{type:"text/csv;charset=utf-8"}),`${name}.csv`);return;}
      const svg=kind==="properties"?buildPropertyComparisonSvg(section,propertySelection):buildThermoChartSvg(section);
      if(format==="svg"){download(new Blob([svg],{type:"image/svg+xml;charset=utf-8"}),`${name}.svg`);return;}
      const image=new Image();
      await new Promise((resolve,reject)=>{image.onload=resolve;image.onerror=()=>reject(Error("图表图像生成失败，请使用 SVG 导出。"));image.src=`data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;});
      const canvas=document.createElement("canvas");canvas.width=image.naturalWidth*2;canvas.height=image.naturalHeight*2;
      const ctx=canvas.getContext("2d");if(!ctx)throw Error("浏览器无法生成 PNG，请使用 SVG 导出。");
      ctx.fillStyle="#fff";ctx.fillRect(0,0,canvas.width,canvas.height);ctx.drawImage(image,0,0,canvas.width,canvas.height);
      const blob=await new Promise(resolve=>canvas.toBlob(resolve,"image/png"));if(!blob)throw Error("PNG 导出失败，请使用 SVG 导出。");
      download(blob,`${name}.png`);
    }catch(problem){if(live){error=problem.message;render();}}
  }
  container.addEventListener("input",event=>{const target=event.target;if(target.dataset?.validationField){form[target.dataset.validationField]=target.type==="checkbox"?target.checked:target.value;const summary=container.querySelector?.("[data-validation-scan-summary]");if(summary)summary.innerHTML=scanSummary(form);const propertyScan=container.querySelector?.("[data-property-scan-summary]");if(propertyScan)propertyScan.innerHTML=propertySummary(form);}});
  container.addEventListener("change",event=>{const target=event.target;if(target.hasAttribute?.("data-validation-record"))chooseRecord(target.value);if(target.hasAttribute?.("data-validation-metric")&&Object.hasOwn(metricLabels,target.value)){metric=target.value;render();}if(["product_policy","cantera_dataset"].includes(target.dataset?.validationField)){form[target.dataset.validationField]=target.value;if(form.product_policy!=="matched_cho_gas")form.cantera_dataset="production";render();}if(target.dataset?.validationField==="scan_mode"){form.scan_mode=target.value;render();}if(["property_mode","property_fluid"].includes(target.dataset?.validationField)){form[target.dataset.validationField]=target.value;if(form.property_mode!=="points")resetPropertySweep(form);render();}if(target.hasAttribute?.("data-thermo-point")){thermoSelection={...thermoSelection,pointIndex:Number(target.value),open:true};render();}if(target.hasAttribute?.("data-thermo-station")){thermoSelection={...thermoSelection,station:target.value,open:true};render();}if(target.hasAttribute?.("data-property-group")){propertySelection={group:target.value};render();}});
  container.addEventListener("click",event=>{const button=event.target.closest?.("button");if(!button||button.disabled)return;const next=button.dataset.validationTab;if(Object.hasOwn(tabs,next)){tab=next;error="";render();return;}const action=button.dataset.validationAction;if(action==="run")run();if(action==="refresh")refresh();if(action==="cancel")cancel();if(action==="export")exportRecord();if(action==="export-png")exportCharts("png");if(action==="export-svg")exportCharts("svg");if(action==="export-property-png")exportCharts("png","properties");if(action==="export-property-svg")exportCharts("svg","properties");if(action==="export-property-csv")exportCharts("csv","properties");});
  render();
  return {refresh,sync(){if(live)render();},onLeave(){live=false;++sequence;stop();}};
}
