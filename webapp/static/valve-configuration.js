// A valve definition belongs to the current input snapshot. Rendering never
// upgrades an old case or derives hardware from a previously selected result.
const escape = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const clone = value => JSON.parse(JSON.stringify(value));
const finite = value => typeof value === "number" && Number.isFinite(value);
const object = value => value && typeof value === "object" && !Array.isArray(value);
export const VALVE_CONFIGURATION_KEY = "valve_configuration_json";
const IDS = ["MFV", "OPFV", "FPOV"];
const LABELS = {MFV:"主燃料阀",OPFV:"富氧预燃室燃料阀",FPOV:"富燃预燃室氧化剂阀"};
const RECOMMENDED_TOPOLOGY = "paper_split_before_cooling_v1";
const NODES = {fuel_after_pump:"燃料泵出口",cooling_inlet:"再生冷却入口",fuel_after_cooling:"再生冷却出口",opfv_outlet:"富氧预燃室燃料入口",lox_after_pump:"氧化剂泵出口",fpov_outlet:"富燃预燃室氧化剂入口"};
const FUNCTIONS = {
  MFV:"调节主燃料支路流量与压降，并影响再生冷却供给；在本模型中以参考开度进行尺寸初选或候选配平。",
  OPFV:"计量送入富氧预燃室的少量燃料，配合氧流量调节其氧燃比、出口温度与涡轮驱动功率。",
  FPOV:"计量送入富燃预燃室的少量氧化剂，配合燃料流量调节其氧燃比、出口温度与涡轮驱动功率。",
};
const NUMERIC_KEYS = new Set(["rated_cda_m2","reference_opening","opening_min","opening_max","design_pressure_drop_Pa","leak_fraction","max_pressure_drop_ratio","actuator_tau_s","actuator_rate_per_s"]);
const ROW_KEYS=["id","fluid","upstream_node","downstream_node","characteristic_version","area_curve",...NUMERIC_KEYS].sort();
const sameKeys=(value,keys)=>object(value)&&Object.keys(value).sort().join() === [...keys].sort().join();
const factors = {rated_cda_m2:1e6,design_pressure_drop_Pa:1e-6,reference_opening:100,opening_min:100,opening_max:100,leak_fraction:100,max_pressure_drop_ratio:100,actuator_rate_per_s:100};
const rawOf = inputs => inputs?.[VALVE_CONFIGURATION_KEY] ?? "";
function pressureRecommendation(inputs,catalog) {
  const rule=catalog.pressure_drop_recommendation;
  if(!rule||!finite(rule.chamber_pressure_ratio)||rule.chamber_pressure_ratio<=0)return null;
  const pc=finite(inputs?.pc)&&inputs.pc>0?inputs.pc:rule.reference_pressure_Pa;
  return finite(pc)&&pc>0?{pressure:pc*rule.chamber_pressure_ratio,pc,ratio:rule.chamber_pressure_ratio}:null;
}
function applyPressureRecommendation(config,inputs,catalog,missingOnly=false) {
  const recommendation=pressureRecommendation(inputs,catalog);
  if(!recommendation)return;
  for(const id of IDS)if(config.valves?.[id]&&(!missingOnly||config.valves[id].design_pressure_drop_Pa===null))
    config.valves[id].design_pressure_drop_Pa=recommendation.pressure;
}

function read(inputs) {
  const raw=rawOf(inputs);
  if(typeof raw!=="string")throw new Error("阀门配置必须为 JSON 文本。");
  if(raw==="")return null;
  let config;
  try {config=JSON.parse(raw);} catch {throw new Error("阀门配置不是有效 JSON；原内容已保留，请修复后保存。");}
  if(!object(config)||config.schema_version!==1||typeof config.enabled!=="boolean")throw new Error("阀门配置版本或启用状态无效；原内容已保留。");
  return config;
}

export function valveConfigurationIssues(inputs, catalog = {}) {
  let config;
  try {config=read(inputs);} catch(error) {return [{key:VALVE_CONFIGURATION_KEY,message:error.message}];}
  if(!config)return [];
  const issues=[];
  const issue=(key,message)=>issues.push({key:VALVE_CONFIGURATION_KEY,path:key,message});
  if(!config.enabled){
    if(!sameKeys(config,["schema_version","enabled"]))issue("enabled","禁用阀门配置不能夹带其他拓扑或特性字段。");
    return issues;
  }
  if(!sameKeys(config,["schema_version","enabled","topology_id","mode","valves"]))issue("configuration","阀门配置字段不完整或包含未知字段，请修复原始配置。");
  if(!["requirement_sizing","candidate_check"].includes(config.mode))issue("mode","请选择阀门需求定尺寸或候选特性校核模式。");
  const template=catalog.templates?.[config.topology_id];
  if(typeof config.topology_id!=="string"||(catalog.templates&&!template))issue("topology_id","阀门拓扑不在当前受支持模板中，不能自动改为其他拓扑。");
  if(!object(config.valves)||Object.keys(config.valves).sort().join()!==[...IDS].sort().join()){
    issue("valves","阀门配置必须且只能包含 MFV、OPFV、FPOV 三只阀。");return issues;
  }
  for(const id of IDS){
    const row=config.valves[id],add=(key,message)=>issue(`${id}.${key}`,`${id}：${message}`);
    if(!object(row)){add("valves","阀定义必须为对象。");continue;}
    if(!sameKeys(row,ROW_KEYS))add("fields","阀定义字段不完整或包含未知字段。");
    if(row.id!==id)add("id","阀 ID 与登记位置不一致。");
    for(const key of ["fluid","upstream_node","downstream_node"])
      if(typeof row[key]!=="string"||!row[key]||(template&&row[key]!==template.valves[id][key]))add(key,"介质或安装端口与所选拓扑不一致。");
    if(typeof row.characteristic_version!=="string"||!row.characteristic_version.trim()||row.characteristic_version.length>128||/[\u0000-\u001f]/.test(row.characteristic_version))add("characteristic_version","特性版本须为不超过 128 字符的非空单行标识。");
    if(row.rated_cda_m2!==null&&(!finite(row.rated_cda_m2)||row.rated_cda_m2<=0))add("rated_cda_m2","额定 CdA 须留空或为正数。");
    if(config.mode==="candidate_check"&&(!finite(row.rated_cda_m2)||row.rated_cda_m2<=0))add("rated_cda_m2","候选校核需要正的额定有效流通面积 CdA。");
    if((config.mode==="requirement_sizing"||row.design_pressure_drop_Pa!==null)&&(!finite(row.design_pressure_drop_Pa)||row.design_pressure_drop_Pa<=0))add("design_pressure_drop_Pa","请填写正的最小设计压降。");
    for(const key of ["opening_min","opening_max","reference_opening"])
      if(!finite(row[key])||row[key]<0||row[key]>1)add(key,"行程上下限和参考开度须在 0～100% 内。");
    if(finite(row.opening_min)&&finite(row.opening_max)&&row.opening_min>=row.opening_max)add("opening_min","行程下限必须小于上限。");
    if(finite(row.reference_opening)&&(row.reference_opening<row.opening_min||row.reference_opening>row.opening_max))add("reference_opening","参考开度须在允许行程内。");
    if(!finite(row.leak_fraction)||row.leak_fraction<0||row.leak_fraction>=1)add("leak_fraction","泄漏面积比例须在 0%（含）至 100%（不含）之间。");
    if(!finite(row.max_pressure_drop_ratio)||row.max_pressure_drop_ratio<=0||row.max_pressure_drop_ratio>0.3)add("max_pressure_drop_ratio","单相小压降模型的压降比上限须大于 0 且不超过 30%。");
    for(const key of ["actuator_tau_s","actuator_rate_per_s"])
      if(!finite(row[key])||row[key]<=0)add(key,"执行器时间常数和最大行程速率须为正数。");
    const curve=row.area_curve;
    if(!Array.isArray(curve)||curve.length<2||curve.length>101||curve.some(point=>!Array.isArray(point)||point.length!==2||point.some(value=>!finite(value)||value<0||value>1)))add("area_curve","特性曲线须有 2～101 个有效断点，开度和面积比例均为 0～100%。");
    else if(curve[0][0]!==0||curve.at(-1)[0]!==1||curve[0][1]!==0||curve.at(-1)[1]!==1||curve.some((point,index)=>index&&(point[0]<=curve[index-1][0]||point[1]<curve[index-1][1])))add("area_curve","曲线须从 (0%, 0%) 到 (100%, 100%)，开度严格递增、面积比例不减；泄漏面积另行定义。");
    else if(row.leak_fraction===0&&finite(row.reference_opening)){
      const upper=curve.findIndex(point=>point[0]>=row.reference_opening);
      const area=upper===0?curve[0][1]:upper>0?curve[upper-1][1]+(curve[upper][1]-curve[upper-1][1])*(row.reference_opening-curve[upper-1][0])/(curve[upper][0]-curve[upper-1][0]):null;
      if(area===0)add("reference_opening","参考开度的有效流通面积必须为正。");
    }
  }
  return issues;
}

export function applyValveConfigurationEdit(inputs, edit, catalog = {}) {
  const action=edit.action;
  if(action==="raw")return {...inputs,[VALVE_CONFIGURATION_KEY]:String(edit.value??"")};
  if(action==="refresh")return inputs;
  if(action==="topology"){
    if(!edit.value)return {...inputs,[VALVE_CONFIGURATION_KEY]:""};
    const template=catalog.templates?.[edit.value];
    if(!template)throw new Error("该阀门拓扑尚无可用定义。");
    let current;try{current=read(inputs);}catch{}
    if(current?.enabled&&current.topology_id===edit.value)return inputs;
    // Changing installation topology explicitly starts a fresh definition, so
    // an old candidate characteristic is never silently attached to new ports.
    const next=clone(template);
    applyPressureRecommendation(next,inputs,catalog);
    return {...inputs,[VALVE_CONFIGURATION_KEY]:JSON.stringify(next)};
  }
  const config=read(inputs);
  if(!config?.enabled)throw new Error("请先选择受支持的阀门拓扑。");
  if(action==="pressure_defaults"||action==="pressure_fill"){
    if(config.mode!=="requirement_sizing")throw new Error("候选特性校核不使用最小设计压降。");
    applyPressureRecommendation(config,inputs,catalog,action==="pressure_fill");
  }else if(action==="mode"){
    if(!["requirement_sizing","candidate_check"].includes(edit.value))throw new Error("阀门分析模式无效。");
    config.mode=edit.value;
    if(config.mode==="requirement_sizing"){
      for(const id of IDS)config.valves[id].rated_cda_m2=null;
      applyPressureRecommendation(config,inputs,catalog,true);
    }
  }else{
    const row=config.valves?.[edit.valveId];
    if(!IDS.includes(edit.valveId)||!object(row))throw new Error("未找到当前方案中的阀定义。");
    if(action==="field"){
      if(edit.key==="characteristic_version")row[edit.key]=String(edit.value??"");
      else if(NUMERIC_KEYS.has(edit.key))row[edit.key]=edit.value===""?null:Number(edit.value)/(factors[edit.key]||1);
      else throw new Error("该安装字段由拓扑定义，不能单独编辑。");
    }else if(action==="curve"){
      const index=Number(edit.index),column=Number(edit.column);
      if(!Number.isInteger(index)||index<0||!Array.isArray(row.area_curve?.[index])||![0,1].includes(column))throw new Error("特性断点索引无效。");
      row.area_curve[index][column]=edit.value===""?null:Number(edit.value)/100;
    }else if(action==="curve_add"){
      const curve=row.area_curve;
      if(!Array.isArray(curve)||curve.length<2||curve.length>=101||curve.some(point=>!Array.isArray(point)||point.length!==2||point.some(value=>!finite(value))))throw new Error("请先修复曲线；最多支持 101 个断点。");
      let index=1;
      for(let n=2;n<curve.length;n++)if(curve[n][0]-curve[n-1][0]>curve[index][0]-curve[index-1][0])index=n;
      curve.splice(index,0,[(curve[index-1][0]+curve[index][0])/2,(curve[index-1][1]+curve[index][1])/2]);
    }else if(action==="curve_remove"){
      const index=Number(edit.index);
      if(!Number.isInteger(index)||index<=0||index>=row.area_curve?.length-1)throw new Error("曲线首尾断点必须保留。");
      row.area_curve.splice(index,1);
    }else throw new Error("阀门编辑操作无效。");
  }
  return {...inputs,[VALVE_CONFIGURATION_KEY]:JSON.stringify(config)};
}

function input(id,key,label,value,{text=false,help=""}={}) {
  const shown=finite(value)?Number((value*(factors[key]||1)).toPrecision(12)):value??"";
  return `<label>${escape(label)}<input type="${text?"text":"number"}"${text?"":' step="any"'} data-valve-action="field" data-valve-id="${id}" data-valve-key="${key}" value="${escape(shown)}" aria-label="${id} ${escape(label)}">${help?`<span class="field-help">${escape(help)}</span>`:""}</label>`;
}
const issuesHtml=issues=>issues.length?`<p class="notice warning" role="status">${issues.map(item=>escape(item.message)).join("<br>")}</p>`:'<p class="fine" role="status">输入格式已检查；可行开度、物性适用域及功率平衡须在重新运行设计点后校核。</p>';
export function refreshValveConfigurationStatus(root,inputs,catalog={}) {
  root?.querySelectorAll("[data-valve-status]").forEach(node=>{node.innerHTML=issuesHtml(valveConfigurationIssues(inputs,catalog));});
}

export function renderValveConfiguration(inputs,field={}, {expanded=new Set()}={}) {
  const catalog=field.valve_catalog||{},raw=rawOf(inputs);
  let config,error;
  try{config=read(inputs);}catch(exc){error=exc.message;}
  const editable=config?.enabled&&sameKeys(config,["schema_version","enabled","topology_id","mode","valves"])&&sameKeys(config.valves,IDS)&&IDS.every(id=>sameKeys(config.valves[id],ROW_KEYS));
  const options=Object.entries(catalog.templates||{}).map(([id])=>`<option value="${escape(id)}"${config?.enabled&&config.topology_id===id?" selected":""}>${escape(catalog.topology_labels?.[id]||id)}</option>`).join("");
  const missing=error||(config?.enabled&&!catalog.templates?.[config.topology_id])||(config&&!config.enabled&&!sameKeys(config,["schema_version","enabled"]));
  const topologyLabel=catalog.topology_labels?.[config?.topology_id]||config?.topology_id;
  const select=config?.enabled||missing?`<div class="valve-topology-summary"><b>冷却与分流方案</b><p>${escape(missing?"当前配置需修复":topologyLabel)}</p>${!missing?`<span class="field-help">${config.topology_id===RECOMMENDED_TOPOLOGY?"默认初选方案：只有 MFV 主支路经过再生冷却，OPFV 从泵后取燃料。":"对比方案：MFV 与再生冷却承受全部燃料流量，OPFV 从冷却后取燃料。"}</span>`:""}</div><details class="valve-topology-options" data-parameter-fold="valve_configuration:topology"${expanded.has("valve_configuration:topology")||missing?" open":""}><summary>高级：更换流路或停用实体阀模型</summary><p class="fine">两种流路会改变冷却流量、预燃室入口焓和阀入口状态。仅在研究不同系统方案时切换；切换会新建三阀定义，原候选面积、曲线与版本不会自动移植到新位置。</p><label>冷却与分流拓扑<select data-valve-action="topology" aria-label="冷却与分流拓扑">${missing?'<option value="invalid" selected disabled>当前配置需修复，不自动替换</option>':""}${options}</select></label><button type="button" data-valve-action="topology" value="">停用实体阀，使用原压力预算模型</button></details>`:`<div class="valve-topology-summary"><b>未启用实体阀</b><p>当前方案沿用原压力预算。启用后可初选三阀有效面积，再校核候选特性。</p><button type="button" data-valve-action="topology" value="${escape(catalog.recommended_topology_id||RECOMMENDED_TOPOLOGY)}">启用阀门初步设计</button><p class="fine">默认：燃料泵后先分流，MFV 后冷却，OPFV 绕过冷却。另一种流路保留在高级设置中。</p></div>`;
  const intro='<p class="fine">阀门通过改变有效流通面积调节流量并产生压降，配合供给网络实现流量分配。这里建模的是三个调节自由度，未包含发动机完整的隔离、止回、泄放和排放阀系；稳态模型不评估关断密封或起停时序。</p>';
  let body="";
  if(editable&&!missing){
    const modes=catalog.mode_labels||{requirement_sizing:"阀门需求定尺寸",candidate_check:"候选特性校核"};
    body=`<label>计算方式<select data-valve-action="mode" aria-label="阀门计算方式">${Object.entries(modes).map(([id,label])=>`<option value="${escape(id)}"${config.mode===id?" selected":""}>${escape(label)}</option>`).join("")}</select></label><p class="fine">${config.mode==="requirement_sizing"?"按最小设计压降和参考开度提出额定 CdA 需求。实际压差由供给网络决定，可能高于最小值；若支路无法满足最小压降，报告不可行。结果是选型需求，应填入候选特性后重新校核。":"给定 MFV 参考开度，按候选 CdA 和曲线重新匹配网络，并求解 OPFV、FPOV 所需开度与行程余量。初选的最小压降存值不参与本模式的候选配平。"} 这是概念设计匹配，泵/涡轮采用当前设计假设；固定硬件离设计求解仍需完整部件特性。</p>`;
    const recommendation=pressureRecommendation(inputs,catalog);
    if(config.mode==="requirement_sizing"&&recommendation){
      const mpa=value=>Number((value/1e6).toPrecision(6));
      body+=`<div class="valve-pressure-basis"><b>最小压降初选：${mpa(recommendation.pressure)} MPa / 阀</b><p class="fine">按当前主室压 ${mpa(recommendation.pc)} MPa × ${Number((100*recommendation.ratio).toPrecision(6))}% 给出初选下限。这是项目概念设计假设，不是通用阀门标准，也不是计算得到的实际压降。压降过大增加泵功，过小则需要更大流通面积，并可能削弱调节余量。最终应结合系统压力预算、控制范围与候选阀特性复核。</p><p class="fine">已有数值保持保存值；修改室压后可主动更新。三阀实际压降可不同，当前下限可按设计依据调整。</p>${IDS.some(id=>config.valves[id].design_pressure_drop_Pa===null)?'<button type="button" data-valve-action="pressure_fill">补齐空白压降</button> ':""}<button type="button" data-valve-action="pressure_defaults">将三阀压降更新为当前初选值</button></div>`;
    }
    body+=IDS.map(id=>{
      const row=config.valves[id];
      const curve=Array.isArray(row.area_curve)?row.area_curve:[];
      const primary=input(id,"characteristic_version","特性版本 / 来源标识",row.characteristic_version,{text:true})+
        (config.mode==="candidate_check"?input(id,"rated_cda_m2","候选额定 CdA / mm²",row.rated_cda_m2):'<p class="fine">额定 CdA：由本次设计需求计算输出。</p>')+
        input(id,"reference_opening",config.mode==="candidate_check"?(id==="MFV"?"MFV 给定开度 / %":"选型参考开度 / %"):"参考开度 / %",row.reference_opening,{help:config.mode==="candidate_check"&&id!=="MFV"?"用于额定面积需求对照；该阀实际开度由配平求出。":""})+
        (config.mode==="requirement_sizing"?input(id,"design_pressure_drop_Pa","最小设计压降 / MPa",row.design_pressure_drop_Pa,{help:"实际压差由网络确定；这是最低需求，不强制各阀恰好等于该值。"}):"");
      const extra=input(id,"opening_min","允许行程下限 / %",row.opening_min)+input(id,"opening_max","允许行程上限 / %",row.opening_max)+
        input(id,"leak_fraction","关闭泄漏面积比例 / %",row.leak_fraction)+input(id,"max_pressure_drop_ratio","允许压降 / 入口压力上限 / %",row.max_pressure_drop_ratio)+
        input(id,"actuator_tau_s","执行器时间常数 / s",row.actuator_tau_s)+input(id,"actuator_rate_per_s","最大行程速率 / %/s",row.actuator_rate_per_s);
      const points=curve.map((point,index)=>`<tr><td><input type="number" step="any" data-valve-action="curve" data-valve-id="${id}" data-valve-index="${index}" data-valve-column="0" aria-label="${id} 第${index+1}点开度百分比" value="${finite(point?.[0])?Number((100*point[0]).toPrecision(12)):""}"></td><td><input type="number" step="any" data-valve-action="curve" data-valve-id="${id}" data-valve-index="${index}" data-valve-column="1" aria-label="${id} 第${index+1}点面积百分比" value="${finite(point?.[1])?Number((100*point[1]).toPrecision(12)):""}"></td><td>${index>0&&index<curve.length-1?`<button type="button" data-valve-action="curve_remove" data-valve-id="${id}" data-valve-index="${index}" aria-label="删除 ${id} 第${index+1}个断点">删除</button>`:"端点"}</td></tr>`).join("");
      return `<section class="parameter-subsection"><h3>${id} · ${LABELS[id]}</h3><p class="valve-function">${FUNCTIONS[id]}</p><p class="fine">介质：${row.fluid==="CH4"?"甲烷":"氧"} · ${escape(NODES[row.upstream_node]||row.upstream_node)} → ${escape(NODES[row.downstream_node]||row.downstream_node)}</p><div class="form-grid">${primary}</div><details data-parameter-fold="valve_configuration:${id}"${expanded.has(`valve_configuration:${id}`)?" open":""}><summary>行程、适用域、执行器与特性曲线</summary><p class="fine">模型端口：${escape(row.upstream_node)} → ${escape(row.downstream_node)}</p><div class="form-grid">${extra}</div><p class="fine">执行器采用一阶滞后与行程速率限制，参数随方案保存；稳态开度不等于瞬态响应已验证。曲线按开度分段线性插值，面积比例单调；线性模板是明确的概念假设，应替换为有依据的候选数据。</p><div class="table-wrap"><table><thead><tr><th>开度 / %</th><th>归一化面积 / %</th><th>断点</th></tr></thead><tbody>${points}</tbody></table></div><button type="button" data-valve-action="curve_add" data-valve-id="${id}">添加特性断点</button></details></section>`;
    }).join("");
  }else if(!config?.enabled&&!error)body='';
  if(missing||config?.enabled&&!editable)body+=`<p class="notice warning">${escape(error||"配置不完整或拓扑不受支持；修复 JSON 或显式选择模板。")}</p><label>原始阀门配置<textarea rows="8" data-valve-action="raw" aria-label="原始阀门配置">${escape(raw)}</textarea></label><button type="button" data-valve-action="refresh">按当前 JSON 刷新表单</button>`;
  return `<div class="valve-configuration" data-field="${VALVE_CONFIGURATION_KEY}" data-role="design_input">${select}${intro}${body}<div data-valve-status>${issuesHtml(valveConfigurationIssues(inputs,catalog))}</div></div>`;
}
