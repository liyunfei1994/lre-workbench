// Read-only views of the state and composition snapshots saved by the two solvers.
// No property calls, inferred missing states, or composition renormalization occur here.
const escape = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const finite = value => typeof value === "number" && Number.isFinite(value);
const number = value => finite(value) ? value.toLocaleString("zh-CN", {maximumSignificantDigits:7}) : "—";
const signed = value => finite(value) ? `${value>0?"+":""}${number(value)}` : "—";
const object = value => value && typeof value === "object" && !Array.isArray(value) ? value : {};
const sections = {chamber:"燃烧室",throat:"喉部",exit:"喷管出口"};
const properties = [
  ["temperature_K","静温","K",1],
  ["pressure_Pa","静压","MPa",1e-6],
  ["density_kg_m3","密度","kg/m³",1],
  ["sound_speed_m_s","等熵声速","m/s",1],
  ["MW_kg_kmol","平均分子量","kg/kmol",1],
  ["gamma_isentropic","等熵指数 a²ρ/p","1",1],
  ["enthalpy_Jkg","静焓","kJ/kg",1e-3],
];
const table = (headers, rows) => `<div class="validation-table-wrap"><table><thead><tr>${headers.map(item=>`<th>${escape(item)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
const row = (cells, attribute="") => `<tr${attribute}>${cells.map(item=>`<td>${item}</td>`).join("")}</tr>`;
const message = text => `<p class="validation-help">${escape(text)}</p>`;

function savedStation(point, backend, station){
  return point?.[backend]?.status === "ok" ? object(point[backend]?.stations?.[station]) : {};
}

function commonEnthalpyReference(result, point){
  const reactants=point?.reactants;
  const reference=reactants?.reference;
  // Older comparisons used a different inlet reference. Merely having two h values
  // or two successful solvers is not sufficient evidence of their common datum.
  const v2=typeof result?.provenance?.inlet_enthalpy_basis === "string" &&
    result.provenance.inlet_enthalpy_basis.includes("finite-pressure residual removed (v2)");
  const referenceV2=typeof reference?.id === "string" && reference.id.endsWith("_ideal_reference_v2");
  return point?.comparable?.conditions_aligned === true && (v2 || referenceV2) &&
    [reactants?.fuel_enthalpy_Jkg,reactants?.oxidizer_enthalpy_Jkg,reactants?.mixture_enthalpy_Jkg].every(finite);
}

export function verifiedCeaComposition(point){
  const evidence=point?.cea?.diagnostics?.composition_verification;
  return point?.cea?.status === "ok" && evidence?.status === "verified" && evidence?.reader_version === 2 &&
    evidence?.name_value_readback_verified === true && evidence?.element_conservation_verified === true;
}

function compositionRows(cantera, cea, ceaWithheld=false){
  const groups=new Map();
  for(const [backend, source] of [["cantera",cantera],["cea",cea]]){
    for(const [name,value] of Object.entries(object(source))){
      // CEA's leading * is a name marker. Do not case-fold, remove phase labels,
      // strip isomer qualifiers, or infer correspondence from chemical formulae.
      const key=backend==="cea" && name.startsWith("*") ? name.slice(1) : name;
      if(!groups.has(key))groups.set(key,{cantera:[],cea:[]});
      groups.get(key)[backend].push({name,value});
    }
  }
  const rows=[];
  for(const [key,group] of [...groups].sort(([a],[b])=>a.localeCompare(b,"en"))){
    const a=group.cantera,b=group.cea;
    if(a.length===1 && b.length===1){
      rows.push(row([
        escape(a[0].name===b[0].name ? a[0].name : `${a[0].name} / ${b[0].name}`),
        number(a[0].value),number(b[0].value),
        finite(a[0].value)&&finite(b[0].value) ? signed(b[0].value-a[0].value) : "—",
        a[0].name===b[0].name ? "保存名称一致" : "仅去除 CEA 名称前的 * 标记后配对",
      ],` data-thermo-species="${escape(key)}"`));
    }else{
      const ambiguous=a.length>1 || b.length>1;
      for(const backend of ["cantera","cea"]){
        for(const entry of group[backend])rows.push(row([
          escape(entry.name),backend==="cantera"?number(entry.value):"—",backend==="cea"?number(entry.value):"—","—",
          ambiguous ? "名称对应不唯一，未合并或计算差值" : ceaWithheld ? "Cantera 单端数据；CEA 组分待重新校核" : `仅 ${backend==="cantera"?"Cantera":"CEA"} 保存该物种；另一端未记录`,
        ],` data-thermo-species="${escape(key)}"`));
      }
    }
  }
  return rows;
}

export function renderThermoStations(result, selection={}){
  const points=Array.isArray(result?.points) ? result.points : [];
  const index=Number.isInteger(selection.pointIndex) && selection.pointIndex>=0 && selection.pointIndex<points.length ? selection.pointIndex : 0;
  const section=Object.hasOwn(sections,selection.station) ? selection.station : "chamber";
  const point=points[index],cantera=savedStation(point,"cantera",section),cea=savedStation(point,"cea",section);
  const open=selection.open===true ? " open" : "";
  let body;
  if(!points.length){
    body=message("本记录没有工况点，无法展示截面状态与组成。");
  }else{
    const controls=`<div class="validation-form-grid"><label>工况点<select data-thermo-point>${points.map((item,i)=>`<option value="${i}"${i===index?" selected":""}>${i+1} · O/F ${number(item?.of)} · Ae/At ${number(item?.eps)}</option>`).join("")}</select></label><label>截面<select data-thermo-station>${Object.entries(sections).map(([key,label])=>`<option value="${key}"${key===section?" selected":""}>${label}</option>`).join("")}</select></label></div>`;
    if(!Object.keys(cantera).length && !Object.keys(cea).length){
      body=controls+message("本工况未保存可用的该截面状态。旧记录如仅有性能指标，需重新运行校核才能查看；失败后端不会用推算值补齐。");
    }else{
      const sharedH=commonEnthalpyReference(result,point);
      const stateRows=properties.map(([key,label,unit,scale])=>{
        const a=finite(cantera[key])?cantera[key]*scale:null,b=finite(cea[key])?cea[key]*scale:null;
        const diff=finite(a)&&finite(b)&&(key!=="enthalpy_Jkg"||sharedH)?b-a:null;
        return row([escape(label),escape(unit),number(a),number(b),signed(diff)],` data-thermo-state="${key}"`);
      });
      const ceaVerified=verifiedCeaComposition(point);
      const ceaWithheld=point?.cea?.status === "ok" && !ceaVerified;
      const composition=compositionRows(cantera.mass_fractions,ceaVerified?cea.mass_fractions:{},ceaWithheld);
      body=controls+message("读取本次校核保存的截面值；差值为这些保存量的 CEA − Cantera，不重新求解物性。缺项显示 —，不以零或推算值替代。")+
        message("若两端允许的产物范围不同，差值同时包含模型差异，不能直接解释为求解器误差。")+
        table(["状态量","单位","Cantera","CEA","CEA − Cantera"],stateRows)+
        message("等熵指数采用保存的 a²ρ/p；其声速对应记录所选的平衡或冻结假设，不能一律解释为定组成 cp/cv。")+
        message(sharedH ? "本工况保存了两端共同的 v2 入口焓参考，可查看静焓差。焓的数值包含参考零点，负值不表示温度或能量异常。" : "本记录缺少已核实的共同焓参考信息：静焓分别列出，暂不计算焓差。比较绝对焓必须先统一参考态。")+
        (ceaWithheld?`<div class="validation-message validation-message-warning" data-thermo-composition-warning>本记录的 CEA 组分缺少新版名称与数值对应检查、元素守恒校核证据，暂不展示 CEA 质量分数或组分差值。请重新运行本项校核；质量分数之和为 1 不能单独证明物种与数值对应正确。上方保存的截面状态量仍可查阅，历史记录保持原样。</div>`:"")+
        `<details class="validation-data-details"><summary>产物质量分数 · ${composition.length} 行${ceaWithheld?" · CEA 组分待重新校核":""}</summary>`+
        message("质量分数表示每种产物占混合物总质量的比例（0–1）。组成会影响热容、平均分子量和解离程度，从而影响平衡温度、声速及喷管性能；这张表帮助定位差异来源。")+
        message(`${ceaWithheld?"本表仅展示 Cantera 已保存的物种，CEA 组分暂不展示。":"保留两端通过读取检查的已保存物种。"}未记录不等于零，也不代表该物种一定被禁用。仅对唯一的同名物种配对，CEA 名称前的 * 标记不改变物种身份；不擅自合并异构物或不同相态。质量分数不重新归一化。`)+
        (composition.length?table(["物种（保存名称）","Cantera 质量分数","CEA 质量分数","CEA − Cantera","配对说明"],composition):message("该截面未保存产物质量分数，需重新运行校核。"))+`</details>`;
    }
  }
  return `<details class="validation-data-details" data-thermo-stations${open}><summary>截面状态与产物组成对照</summary>${body}</details>`;
}
