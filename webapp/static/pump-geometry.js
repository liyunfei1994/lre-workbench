// Saved preliminary hydraulic geometry. CAD exports and both views share these coordinates.
const finite = value => typeof value === "number" && Number.isFinite(value);
const escape = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const number = value => finite(value) ? value.toLocaleString("zh-CN", {maximumSignificantDigits:6}) : "未记录";
const sides = ["ox", "fuel"];
const DIMENSIONS = [
  ["impeller_diameter_m", "叶轮外径 D₂", "mm", 1000], ["eye_shroud_diameter_m", "入口轮盖直径 D₁s", "mm", 1000],
  ["eye_hub_diameter_m", "入口轮毂直径 D₁h", "mm", 1000], ["outlet_width_m", "出口流道宽 b₂", "mm", 1000],
  ["axial_length_m", "代表级子午流道轴向长度 Lz", "mm", 1000], ["inlet_area_m2", "入口环形流通面积", "mm²", 1e6],
  ["outlet_area_m2", "出口流通面积", "mm²", 1e6], ["inlet_meridional_velocity_m_s", "入口子午速度", "m/s"],
  ["outlet_meridional_velocity_m_s", "出口子午速度", "m/s"],
];
const DESIGN = [
  ["mass_flow_kg_s", "质量流量", "kg/s"], ["volume_flow_m3_s", "入口体积流量", "L/s", 1000], ["density_in_kg_m3", "入口密度", "kg/m³"],
  ["pressure_in_pa", "泵入口压力", "MPa", 1e-6], ["pressure_out_pa", "泵出口压力", "MPa", 1e-6], ["pressure_rise_pa", "泵压升 Δp", "MPa", 1e-6],
  ["shaft_speed_rpm", "轴转速", "rpm"], ["stages", "设定级数", "级"], ["total_head_m", "入口密度参考总扬程", "m"], ["stage_head_m", "每级参考扬程", "m"],
  ["specific_speed_nq", "比转速 nq（rpm、m³/s、m）", "—"], ["tip_speed_m_s", "叶轮外缘速度", "m/s"],
];
const ASSUMPTIONS = [
  ["head_coefficient_psi", "扬程系数 ψ = gH级 / u₂²", "—"], ["inlet_flow_coefficient", "入口流量系数 cm₁ / u₂", "—"],
  ["outlet_flow_coefficient", "出口流量系数 cm₂ / u₂", "—"], ["eye_hub_ratio", "入口轮毂比 D₁h / D₁s", "—"],
  ["axial_length_ratio", "子午流道轴向长度比 Lz / D₂", "—"], ["blockage_factor", "流通面积系数（未扣叶片厚度时为 1）", "—"],
  ["leakage_flow_m3_s", "附加泄漏流量", "L/s", 1000],
];

function rows(values, fields) {
  return fields.map(([key,label,unit,factor=1]) => `<div><dt>${label}</dt><dd>${finite(values?.[key]) ? `${number(values[key]*factor)} <small>${unit}</small>` : "未记录"}</dd></div>`).join("");
}

function references(pump) {
  const links=(Array.isArray(pump?.references)?pump.references:[]).filter(item=>officialUrl(item?.url));
  return links.length?`<ul class="pg-notes">${links.map(item=>`<li><a href="${escape(item.url)}" target="_blank" rel="noopener noreferrer">${escape(item.title || item.url)}</a></li>`).join("")}</ul>`:"";
}

function officialUrl(value) {
  try {const url=new URL(value);return url.protocol==="https:" && ["manual.cfturbo.com","cfturbo.com"].includes(url.hostname) && !url.username && !url.password;} catch {return false;}
}
const list=value=>Array.isArray(value)?value:[];
const FIELD_STATES={recorded:"记录值",derived:"派生值",assumed:"设计假设",missing:"待设计",conflict:"数据冲突"};
const fieldState=field=>Object.hasOwn(FIELD_STATES,field?.state)?field.state:"missing";
const scalar=value=>finite(value)||typeof value==="string"?value:null;
function handoffValue(field) {
  const value=scalar(field?.value);
  return value===null?"待补充":typeof value==="number"?number(value):escape(value);
}
function sourceLabel(field) {
  const source=String(field?.source || "来源未记录");
  if(source.includes("calculation.components."))return "本记录 · 泵组件输出";
  if(source.startsWith("由本记录"))return "本记录 · 派生计算";
  if(source.includes("已校验的泵系统点"))return "本记录 · 已校验系统点";
  return source;
}
function fieldTable(fields) {
  return `<div class="pg-table-scroll"><table class="pg-field-table"><thead><tr><th scope="col">参数</th><th scope="col">数值 / 单位</th><th scope="col">来源与状态</th><th scope="col">CFturbo 对应项 / 使用说明</th></tr></thead><tbody>${list(fields).map(field=>`<tr><th scope="row">${escape(field?.label || field?.id)}</th><td class="pg-value">${handoffValue(field)} <small>${escape(field?.unit || "")}</small></td><td><span class="pg-state pg-state-${fieldState(field)}">${FIELD_STATES[fieldState(field)]}</span><small class="pg-field-source" title="${escape(field?.source || "来源未记录")}">${escape(sourceLabel(field))}</small></td><td><strong>${escape(field?.target || "—")}</strong>${field?.note?`<p>${escape(field.note)}</p>`:""}</td></tr>`).join("")}</tbody></table></div>`;
}
function handoffGroups(handoff) {
  return list(handoff?.groups).map((group,i)=>`<details class="pg-parameter-group"${i===0?" open":""}><summary>${escape(group?.title || group?.id)}<span>${list(group?.fields).length} 项</span></summary>${fieldTable(group?.fields)}</details>`).join("");
}
function componentMarkup(handoff) {
  if(!handoff)return `<p class="pg-muted">本记录尚无部件交接清单。诱导轮、叶片和机壳需在专业设计中定义。</p>`;
  return `<p class="pg-muted">下列顺序用于组织建模任务；最终部件组合、级间连接及尺寸需按实际方案确认。</p>${list(handoff.components).map(component=>`<details class="pg-parameter-group"><summary>${escape(component?.label || component?.id)}<span>${FIELD_STATES[fieldState(component)]}</span></summary><p class="pg-component-note">${escape(component?.note || "")}</p>${fieldTable(component?.fields)}</details>`).join("")}${list(handoff.stage_allocation).length?`<h4>代表级扬程分配</h4><p class="pg-muted">等参考扬程分配用于总体初估；诱导轮增能、级间损失和各级入口状态仍需单独闭合。</p><div class="pg-table-scroll"><table class="pg-field-table"><thead><tr><th>级</th><th>参考扬程 / m</th><th>入口状态与用途</th></tr></thead><tbody>${handoff.stage_allocation.map(stage=>`<tr><th>${escape(stage.stage)}</th><td class="pg-value">${number(stage.reference_head_m)}</td><td>${escape(stage.inlet_state_note || "未记录")}</td></tr>`).join("")}</tbody></table></div>`:""}`;
}
function workflowMarkup(design) {
  return `<div class="pg-workflow">${list(design?.workflow).map((step,i)=>`<article><span>${String(i+1).padStart(2,"0")}</span><div><h4>${escape(step?.title || "建模步骤")}</h4><p>${escape(step?.note || "")}</p>${officialUrl(step?.url)?`<a href="${escape(step.url)}" target="_blank" rel="noopener noreferrer">官方方法说明 ↗</a>`:""}</div></article>`).join("")}</div>${references(design)}`;
}

/** A reviewable parameter sheet, not an automatically importable CFturbo project. */
export function buildPumpHandoffCsv(handoff,source={}) {
  if(!handoff || !["partial"].includes(handoff.status))return "";
  const cell=value=>{
    if(finite(value))return String(value);
    let text=typeof value==="string"?value:"";
    if(/^[\s\uFEFF]*[=+\-@]|^[\t\r\n]/.test(text))text="'"+text;
    return `"${text.replaceAll('"','""')}"`;
  };
  const rows=[["record_id","group","parameter_id","parameter","value","unit","status","source","cfturbo_target","note"]];
  const groups=[...list(handoff.groups),...list(handoff.components).map(c=>({id:c.id,title:c.label,fields:c.fields}))];
  for(const group of groups)for(const field of list(group?.fields))rows.push([String(source?.record_id || ""),group.title || group.id,field?.id,field?.label,scalar(field?.value),field?.unit,fieldState(field),field?.source,field?.target,field?.note]);
  return rows.map(row=>row.map(cell).join(",")).join("\r\n")+"\r\n";
}

export function inspectPumpGeometry(pump) {
  if (!pump?.available) return {valid:false, reason:typeof pump?.reason === "string" ? pump.reason : "本条记录缺少完整的泵几何设计假设，未生成流道。请保存假设并重新计算设计点。"};
  const hub=pump.meridional?.hub, shroud=pump.meridional?.shroud, d=pump.dimensions;
  if (!d || ![hub,shroud].every(curve=>Array.isArray(curve) && curve.length>=3 && curve.length<=2048)) return {valid:false,reason:"保存的子午面坐标不完整，无法绘图或导出。"};
  if (pump.meridional.unit!=="m" || pump.meridional.rotation_axis!=="z" || pump.meridional.coordinate_origin!=="impeller_eye_inlet") return {valid:false,reason:"保存的坐标单位、旋转轴或原点未定义，无法绘图或导出。"};
  if (!DIMENSIONS.slice(0,5).every(([key])=>finite(d[key]) && d[key]>0)) return {valid:false,reason:"保存的主尺寸缺失或无效，无法绘图或导出。"};
  const close=(a,b)=>Math.abs(a-b)<=1e-9*Math.max(1,Math.abs(a),Math.abs(b));
  if (d.eye_hub_diameter_m>=d.eye_shroud_diameter_m || d.eye_shroud_diameter_m>=d.impeller_diameter_m || d.outlet_width_m>=d.axial_length_m) return {valid:false,reason:"入口环形流道或出口宽度与主尺寸不一致。"};
  if ([hub,shroud].some(curve=>curve.some((p,i)=>!Array.isArray(p) || p.length!==2 || !p.every(finite) || p[0]<0 || p[1]<=0 || (i>0 && (p[0]<curve[i-1][0] || p[1]<curve[i-1][1]))))) return {valid:false,reason:"保存的子午面含无效坐标或次序异常，无法绘图或导出。"};
  if (!close(hub[0][0],0) || !close(shroud[0][0],0) || !close(hub[0][1]*2,d.eye_hub_diameter_m) || !close(shroud[0][1]*2,d.eye_shroud_diameter_m) || !close(hub.at(-1)[0],d.axial_length_m) || !close(shroud.at(-1)[0],d.axial_length_m-d.outlet_width_m) || !close(hub.at(-1)[1]*2,d.impeller_diameter_m) || !close(shroud.at(-1)[1]*2,d.impeller_diameter_m)) return {valid:false,reason:"保存的尺寸与子午面端点不一致，已停止绘图与导出。"};
  return {valid:true,hub,shroud,length:d.axial_length_m,radius:d.impeller_diameter_m/2};
}

function dimension(x1,y1,x2,y2,label,vertical=false,side=1,labelOffset=0) {
  return `<g class="pg-dimension" stroke="#697b90" stroke-width="1" fill="none"><path d="M${x1},${y1}L${x2},${y2}"/>${vertical?`<path d="M${x1-5},${y1}h10 M${x2-5},${y2}h10 M${x1-3},${y1+5}l3,-5 3,5 M${x2-3},${y2-5}l3,5 3,-5"/>`:`<path d="M${x1},${y1-5}v10 M${x2},${y2-5}v10 M${x1+5},${y1-3}l-5,3 5,3 M${x2-5},${y2-3}l5,3 -5,3"/>`}</g><text x="${(x1+x2)/2+(vertical?side*9:0)}" y="${(y1+y2)/2+(vertical?4+labelOffset:-8)}" text-anchor="${vertical?(side>0?"start":"end"):"middle"}" fill="#40566e" font-size="13" paint-order="stroke" stroke="#fff" stroke-width="4" stroke-linejoin="round">${escape(label)}</text>`;
}

export function buildPumpSvg(pump) {
  const data=inspectPumpGeometry(pump); if (!data.valid) return "";
  const d=pump.dimensions,width=1000,height=570,scale=Math.min(540/data.length,390/(2*data.radius)),left=220+(540-data.length*scale)/2,center=285;
  const X=z=>left+z*scale,Y=r=>center-r*scale;
  const path=(curve,sign=1)=>curve.map(([z,r],i)=>`${i?"L":"M"}${X(z)},${Y(sign*r)}`).join(" ");
  const region=sign=>path(data.hub,sign)+data.shroud.toReversed().map(([z,r])=>` L${X(z)},${Y(sign*r)}`).join("")+" Z";
  const pointData=escape(JSON.stringify({hub:data.hub,shroud:data.shroud}));
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escape(pump.label || "泵")}代表级子午面等比例剖面" data-pg-scale="${scale}" data-pg-coordinates="${pointData}" font-family="system-ui,Microsoft YaHei,sans-serif"><title>代表级叶轮流道子午面</title><desc>z 轴为旋转轴，入口平面 z=0，尺寸以 mm 标注，坐标保留 m。蓝色为流道，未定义叶片与实体壁厚。</desc><rect width="1000" height="570" fill="#fff"/><path d="M${left-20},${center}H${X(data.length)+20}" fill="none" stroke="#93a3b6" stroke-dasharray="6 4"/><path d="${region(1)} ${region(-1)}" fill="#edf4fb"/><path d="${path(data.hub)} ${path(data.hub,-1)}" fill="none" stroke="#b1884f" stroke-width="2.4"/><path d="${path(data.shroud)} ${path(data.shroud,-1)}" fill="none" stroke="#477aa9" stroke-width="2.4"/>${dimension(X(0),52,X(data.length),52,`Lz ${number(d.axial_length_m*1000)} mm`)}${dimension(X(data.length)+30,Y(data.radius),X(data.length)+30,Y(-data.radius),`D₂ ${number(d.impeller_diameter_m*1000)} mm`,true)}${dimension(X(0)-30,Y(d.eye_shroud_diameter_m/2),X(0)-30,Y(-d.eye_shroud_diameter_m/2),`D₁s ${number(d.eye_shroud_diameter_m*1000)} mm`,true,-1,-25)}${dimension(X(0)-130,Y(d.eye_hub_diameter_m/2),X(0)-130,Y(-d.eye_hub_diameter_m/2),`D₁h ${number(d.eye_hub_diameter_m*1000)} mm`,true,-1,25)}${dimension(X(data.length-d.outlet_width_m),height-57,X(data.length),height-57,`b₂ ${number(d.outlet_width_m*1000)} mm`)}<text x="${X(0)+8}" y="${center+19}" fill="#6f8094" font-size="12">入口 z = 0</text><text x="500" y="552" text-anchor="middle" fill="#6a7c91" font-size="12">轴向 z 与径向 r 等比例 · 代表级流道，不是泵总装尺寸</text><g font-size="12"><path d="M28,25h24" stroke="#b1884f" stroke-width="3"/><text x="60" y="29" fill="#6a7c91">轮毂边界</text><path d="M180,25h24" stroke="#477aa9" stroke-width="3"/><text x="212" y="29" fill="#6a7c91">轮盖边界</text></g></svg>`;
}

export function buildPumpCsv(pump) {
  const data=inspectPumpGeometry(pump); if (!data.valid) return "";
  return "boundary,point_index,z_m,r_m\r\n"+["hub","shroud"].flatMap(key=>data[key].map(([z,r],i)=>`${key},${i},${z},${r}`)).join("\r\n")+"\r\n";
}

export function buildPumpDxf(pump) {
  const data=inspectPumpGeometry(pump); if (!data.valid) return "";
  const lines=[0,"SECTION",2,"HEADER",9,"$ACADVER",1,"AC1015",9,"$INSUNITS",70,6,9,"$MEASUREMENT",70,1,0,"ENDSEC",0,"SECTION",2,"ENTITIES"];
  for (const key of ["hub","shroud"]) {lines.push(0,"LWPOLYLINE",100,"AcDbEntity",8,key.toUpperCase(),100,"AcDbPolyline",90,data[key].length,70,0); for (const [z,r] of data[key]) lines.push(10,z,20,r);}
  lines.push(0,"ENDSEC",0,"EOF"); return lines.join("\r\n")+"\r\n";
}

export function buildPumpPointTxt(pump,boundary) {
  const data=inspectPumpGeometry(pump); if (!data.valid || !["hub","shroud"].includes(boundary)) return "";
  return data[boundary].map(([z,r])=>`${z}\t${r}`).join("\r\n")+"\r\n";
}

export function buildPumpMesh(pump, segments=40, cutaway=false) {
  const data=inspectPumpGeometry(pump); if (!data.valid) return {vertices:[],faces:[]};
  const count=Number.isInteger(segments) && segments>=8 && segments<=96 ? segments : 40;
  const angle=2*Math.PI*(cutaway ? .75 : 1),ringCount=cutaway?count+1:count,vertices=[],faces=[];
  for (const boundary of ["hub","shroud"]) {
    const offset=vertices.length;
    for (const [z,r] of data[boundary]) for (let j=0;j<ringCount;j++) {const a=j/count*angle;vertices.push([r*Math.cos(a),r*Math.sin(a),z]);}
    for (let i=0;i<data[boundary].length-1;i++) for (let j=0;j<count;j++) {
      const next=cutaway?j+1:(j+1)%count,a=offset+i*ringCount+j,b=offset+(i+1)*ringCount+j,c=offset+(i+1)*ringCount+next,e=offset+i*ringCount+next;
      faces.push({indices:boundary==="hub"?[a,e,c,b]:[a,b,c,e],boundary});
    }
  }
  return {vertices,faces};
}

export function buildPumpObj(pump) {
  const mesh=buildPumpMesh(pump); if (!mesh.vertices.length) return "";
  const lines=["# Preliminary representative-stage hydraulic flow boundary; units: m", "# Cartesian coordinates: x=r*cos(theta), y=r*sin(theta), z=axial", "# Open surfaces only. No blades, wall thickness, inducer, volute or solid body."];
  mesh.vertices.forEach(p=>lines.push(`v ${p.join(" ")}`));let boundary=null;
  for (const face of mesh.faces) {if(face.boundary!==boundary){boundary=face.boundary;lines.push(`g ${boundary}`);}lines.push(`f ${face.indices.map(i=>i+1).join(" ")}`);}
  return lines.join("\n")+"\n";
}

export function buildPumpJson(pump, source={}, handoff=null) {
  if(handoff && handoff.status!=="partial")return "";
  return JSON.stringify({schema_version:1,kind:"preliminary_pump_geometry_exchange",source,pump,...(handoff?{design_handoff:handoff}:{}),exchange:{length_unit:"m",drawing_dimension_unit:"mm",coordinate_origin:"impeller_eye_inlet",rotation_axis:"z",csv:"boundary,point_index,z_m,r_m",txt:"separate hub/shroud files; no header; two columns z_m r_m",dxf:"X=z, Y=r, Z=0; INSUNITS=6 (metres); two open meridional polylines",obj:"X=r*cos(theta), Y=r*sin(theta), Z=z; open hub/shroud surfaces",usage:"CFTurbo: manually enter design point and dimensions. NX/UG: import reference curves or surface mesh after checking units. Not a proprietary project file, blade design or manufacturing solid."}},null,2)+"\n";
}

export function pumpExportFilename(model,side,extension) {
  const key=sides.includes(side)?side:"unknown",record=String(model?.source?.record_id || model?.pumps?.[key]?.source?.record_id || "unrecorded").replace(/[^a-zA-Z0-9_-]/g,"_").slice(0,80);
  if (extension==="handoff-csv") return `pump-${key}-${record}-cfturbo-parameters.csv`;
  if (["hub-txt","shroud-txt"].includes(extension)) return `pump-${key}-${record}-${extension.slice(0,-4)}.txt`;
  return `pump-${key}-${record}.${["csv","dxf","obj","json"].includes(extension)?extension:"txt"}`;
}

function renderPump(pump,side,handoff,design) {
  const data=inspectPumpGeometry(pump),notes=list(pump?.notes),checks=list(pump?.checks),blocked=handoff?.status==="conflict";
  const checkLabel=status=>({pass:"一致性检查通过",not_evaluated:"尚未评估",warning:"需要复核",fail:"未通过"}[status] || "状态未记录");
  const visual=data.valid?`<div class="pg-views" role="group" aria-label="几何视图"><button type="button" data-pg-view="2d" aria-pressed="true">二维子午面</button><button type="button" data-pg-view="3d" aria-pressed="false">三维流道边界</button><span>等比例 · 代表级</span></div><div class="pg-viewport" data-pg-panel="2d">${buildPumpSvg(pump)}</div><div class="pg-viewport pg-three" data-pg-panel="3d" hidden><canvas data-pg-canvas tabindex="0" aria-label="泵轮毂与轮盖三维流道边界，拖动或方向键旋转，滚轮或加减键缩放，Home 键复位"></canvas><div class="pg-three-controls"><span>拖动旋转 · 滚轮缩放</span><button type="button" data-pg-cutaway aria-pressed="true">剖开 ¼</button><button type="button" data-pg-wireframe aria-pressed="false">线框</button><button type="button" data-pg-zoom="in" aria-label="放大三维视图">＋</button><button type="button" data-pg-zoom="out" aria-label="缩小三维视图">−</button><button type="button" data-pg-reset>复位视角</button></div><p class="pg-error" data-pg-canvas-error hidden>三维画布不可用，仍可查看二维剖面和导出坐标。</p></div>`:`<p class="pg-unavailable">${escape(data.reason)}</p><p class="pg-muted">保留已知系统设计点；缺少的假设和坐标不会由前端默认值补齐。</p>`;
  const exports=data.valid?`<div class="pg-exports">${["csv","dxf","obj","json","hub-txt","shroud-txt"].map(ext=>`<button type="button" data-pg-export="${ext}"${ext==="json"&&handoff&&handoff.status!=="partial"?" disabled":""}>${({csv:"坐标 CSV",dxf:"二维 DXF",obj:"三维 OBJ",json:"尺寸与设计点 JSON","hub-txt":"轮毂 TXT","shroud-txt":"轮盖 TXT"})[ext]}</button>`).join("")}</div>`:"";
  const point=pump?.design_point || {},metrics=[["入口体积流量 Q",finite(point.volume_flow_m3_s)?point.volume_flow_m3_s*1000:null,"L/s"],["参考总扬程 H",point.total_head_m,"m"],["轴转速 n",point.shaft_speed_rpm,"rpm"],["设定主泵级数",point.stages,"级"]];
  const componentChain=list(handoff?.components).length?`<div class="pg-component-chain" aria-label="部件建模任务顺序">${handoff.components.map(component=>`<div><span>${escape(component?.label || component?.id)}</span><small>${FIELD_STATES[fieldState(component)]}</small></div>`).join("")}</div>`:"";
  return `<article class="pg-pump" data-pg-pump="${side}"${side==="fuel"?" hidden":""}>
    <div class="pg-heading"><div><h3>${escape(pump?.label || (side==="ox"?"液氧泵":"甲烷泵"))}</h3><p>${escape(pump?.source?.label || "保存记录中的概念几何")}</p></div><span class="pg-badge${blocked?" pg-badge-conflict":""}">${blocked?"数据冲突 · 暂停交接":"系统设计点 / 部件设计待补全"}</span></div>
    <div class="pg-metrics">${metrics.map(([label,value,unit])=>`<div><span>${label}</span><strong>${number(value)} <small>${unit}</small></strong></div>`).join("")}</div>
    ${handoff?.summary?`<p class="pg-handoff-summary">${escape(handoff.summary)}</p>`:""}
    ${componentChain}
    <div class="pg-workspace"><div class="pg-model">${visual}<p class="pg-scope">本图表示叶轮流道的轮毂与轮盖边界，未设计叶片、诱导轮、扩压器、蜗壳及实体壁厚。多级泵显示等扬程假设下的一个代表级，轴向长度不等于整泵总长。</p></div><aside class="pg-inspector"><h4>代表级主尺寸 <span>mm</span></h4><dl class="pg-data">${rows(pump?.dimensions,DIMENSIONS)}</dl><h4>坐标与设计记录交接</h4>${exports}<p class="pg-muted">坐标采用 m；子午面为 z / r，旋转轴为 z。所有格式使用本条记录的同一组坐标。</p>${handoff?`<button type="button" class="pg-handoff-export" data-pg-export="handoff-csv"${handoff.status!=="partial"?" disabled":""}>CFturbo 参数 CSV</button><p class="pg-muted">含设计点、单位、来源和待设计项；供逐项录入与核对。</p>`:""}</aside></div>
    <div class="pg-section-tabs" role="group" aria-label="泵设计资料"><button type="button" data-pg-tab="parameters" aria-pressed="true">设计点与 CFturbo 参数</button><button type="button" data-pg-tab="components" aria-pressed="false">部件与叶片设计</button><button type="button" data-pg-tab="method" aria-pressed="false">方法与依据</button></div>
    <section data-pg-section="parameters"><h4>本记录的系统设计点</h4>${handoff?handoffGroups(handoff):`<dl class="pg-data">${rows(point,DESIGN)}</dl><p class="pg-muted">本记录未生成 CFturbo 参数交接清单。这里只展示已保存的系统量。</p>`}<p class="pg-muted">${escape(point.head_scope || "参考扬程 H = Δp / (ρ入口g)，未计进出口速度头修正；用于代表级概念定尺寸。")}</p></section>
    <section data-pg-section="components" hidden>${componentMarkup(handoff)}${list(handoff?.missing).length?`<details class="pg-parameter-group"><summary>待补充的设计条件<span>${handoff.missing.length} 项</span></summary><ul class="pg-notes">${handoff.missing.map(item=>`<li>${escape(item)}</li>`).join("")}</ul></details>`:""}</section>
    <section data-pg-section="method" hidden>${workflowMarkup(design)}<details class="pg-details"><summary>本平台的几何假设与检查</summary><dl class="pg-data">${rows(pump?.assumptions,ASSUMPTIONS)}</dl><p class="pg-muted">按入口密度与等扬程代表级进行概念定尺寸；子午面采用平滑构造，不是优化叶片型线。本页 ψ = gH级 / u₂²；按相同参考压头，CFturbo 的扬程系数 Ψ = 2ψ。这里的压头由 Δp / ρ入口g 得到，未包含进出口速度头修正；专业设计时应核对总压头定义。</p>${checks.length?`<ul class="pg-checks">${checks.map(check=>`<li><strong>${escape(check?.label || check?.id || "检查项")}</strong><span>${checkLabel(check?.status)}</span>${finite(check?.value)?`<span>${number(check.value)}</span>`:""}${check?.note?`<p>${escape(check.note)}</p>`:""}</li>`).join("")}</ul>`:"<p class=\"pg-muted\">本记录未保存检查结果。</p>"}${notes.length?`<ul class="pg-notes">${notes.map(note=>`<li>${escape(note)}</li>`).join("")}</ul>`:""}${references(pump)}<p class="pg-fingerprint">几何记录标识：${escape(pump?.geometry_sha256 || "未保存")}</p></details><details class="pg-details"><summary>在 CFTurbo、NX / UG 中继续建模</summary><p>在 CFTurbo 中录入设计点和主尺寸，继续设计叶片与部件。轮毂 / 轮盖 TXT 为各自无表头的两列 z、r 坐标，单位 m，可用于其二维点数据导入。在 NX / UG 中将 CSV / DXF 子午曲线或 OBJ 边界网格作为参考。导出不是这些软件的原生工程文件，也不是可制造实体。</p><p>所有导出坐标单位为 m；页面主尺寸用 mm。DXF 的 X 为轴向 z，Y 为径向 r；OBJ 的 Z 为旋转轴。CSV、TXT、DXF、OBJ 与本页使用同一组保存坐标，OBJ 始终导出完整周向边界，页面剖开只影响显示。</p></details></section>
  </article>`;
}

export function renderPumpGeometry(model) {
  const source=model?.source || {},design=model?.design_handoff;
  return `<section class="pump-geometry" data-pump-geometry><div class="pg-title"><div><span class="pg-eyebrow">PUMP DESIGN / CFTURBO HANDOFF</span><h2>泵设计与建模交接</h2><p>循环设计点 · 部件设计条件 · 代表级子午面与坐标</p></div><code>${escape(source.record_id || "未指定记录")}</code></div><div class="pg-select" role="group" aria-label="泵选择">${sides.map(side=>`<button type="button" data-pg-side="${side}" aria-pressed="${side==="ox"}">${escape(model?.pumps?.[side]?.label || (side==="ox"?"液氧泵":"甲烷泵"))}</button>`).join("")}</div>${sides.map(side=>renderPump(model?.pumps?.[side],side,design?.pumps?.[side],design)).join("")}</section>`;
}

function saveFile(text,type,name,doc) {
  const win=doc.defaultView || window,url=win.URL.createObjectURL(new win.Blob([text],{type})),a=doc.createElement("a");a.href=url;a.download=name;doc.body.append(a);a.click();a.remove();win.setTimeout(()=>win.URL.revokeObjectURL(url),1000);
}

export function mountPumpGeometry(container,model) {
  const root=container?.matches?.("[data-pump-geometry]")?container:container?.querySelector?.("[data-pump-geometry]"); if (!root) return ()=>{};
  root._pumpCleanup?.(); const win=root.ownerDocument.defaultView || window,listeners=[],controllers=[];let disposed=false;
  const listen=(element,type,fn,options)=>{element?.addEventListener(type,fn,options);listeners.push(()=>element?.removeEventListener(type,fn,options));};
  for (const side of sides) {
    const pump=model?.pumps?.[side],data=inspectPumpGeometry(pump),panel=root.querySelector(`[data-pg-pump="${side}"]`);if(!panel) continue;
    const handoff=model?.design_handoff?.pumps?.[side];
    listen(panel,"click",event=>{const button=event.target?.closest?.("button");if(!button||!panel.contains(button))return;
      if(["parameters","components","method"].includes(button.dataset.pgTab)){for(const tab of panel.querySelectorAll("[data-pg-tab]"))tab.setAttribute("aria-pressed",String(tab.dataset.pgTab===button.dataset.pgTab));for(const section of panel.querySelectorAll("[data-pg-section]"))section.hidden=section.dataset.pgSection!==button.dataset.pgTab;}
      if(button.dataset.pgExport==="handoff-csv"){const text=buildPumpHandoffCsv(handoff,model?.source);if(text)saveFile("\uFEFF"+text,"text/csv;charset=utf-8",pumpExportFilename(model,side,"handoff-csv"),root.ownerDocument);}
    });
    if(!data.valid) continue;
    const canvas=panel.querySelector("[data-pg-canvas]");let ctx=null;try{ctx=canvas?.getContext?.("2d");}catch{}
    let yaw=.65,pitch=-.25,zoom=1,drag=null,frame=null,cutaway=true,wireframe=false,mesh=buildPumpMesh(pump,36,true);
    const draw=()=>{frame=null;if(disposed || !ctx || panel.hidden || panel.querySelector('[data-pg-panel="3d"]')?.hidden)return;
      const box=canvas.getBoundingClientRect(),width=Math.max(240,box.width),height=Math.max(260,box.height),ratio=Math.min(win.devicePixelRatio || 1,2);canvas.width=Math.round(width*ratio);canvas.height=Math.round(height*ratio);ctx.setTransform(ratio,0,0,ratio,0,0);ctx.clearRect(0,0,width,height);ctx.fillStyle="#f8fafd";ctx.fillRect(0,0,width,height);
      const scale=Math.min(width*.73/Math.hypot(data.length,2*data.radius),height*.70/(2*data.radius))*zoom,cy=Math.cos(yaw),sy=Math.sin(yaw),cp=Math.cos(pitch),sp=Math.sin(pitch);
      const points=mesh.vertices.map(([rX,rY,z])=>{const x=(z-data.length/2)*cy+rX*sy,depth=-(z-data.length/2)*sy+rX*cy;return [width/2+(x*cp-rY*sp)*scale,height/2-(x*sp+rY*cp)*scale,depth];});
      const faces=mesh.faces.map(face=>({...face,points:face.indices.map(i=>points[i]),depth:face.indices.reduce((sum,i)=>sum+points[i][2],0)/4})).sort((a,b)=>a.depth-b.depth);
      for(const face of faces){ctx.beginPath();face.points.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));ctx.closePath();if(!wireframe){ctx.fillStyle=face.boundary==="hub"?"#c9b18b":"#b1c8de";ctx.fill();}ctx.strokeStyle=face.boundary==="hub"?"rgba(140,107,60,.45)":"rgba(57,94,129,.4)";ctx.lineWidth=wireframe ? .65 : .35;ctx.stroke();}
      ctx.fillStyle="#657a92";ctx.font="12px system-ui, sans-serif";ctx.fillText("轮毂（金色）与轮盖（蓝色）流道边界 · 无叶片 / 无壁厚",17,25);
    };
    const schedule=()=>{if(frame===null&&!disposed)frame=win.requestAnimationFrame(draw);};
    const setZoom=factor=>{zoom=Math.max(.4,Math.min(3,zoom*factor));schedule();};
    listen(panel,"click",event=>{const button=event.target?.closest?.("button");if(!button||!panel.contains(button))return;
      if(["2d","3d"].includes(button.dataset.pgView)){for(const tab of panel.querySelectorAll("[data-pg-view]"))tab.setAttribute("aria-pressed",String(tab.dataset.pgView===button.dataset.pgView));for(const p of panel.querySelectorAll("[data-pg-panel]"))p.hidden=p.dataset.pgPanel!==button.dataset.pgView;schedule();}
      const ext=button.dataset.pgExport;if(["csv","dxf","obj","json","hub-txt","shroud-txt"].includes(ext)){const text={csv:buildPumpCsv,dxf:buildPumpDxf,obj:buildPumpObj,json:p=>buildPumpJson(p,model?.source,handoff),"hub-txt":p=>buildPumpPointTxt(p,"hub"),"shroud-txt":p=>buildPumpPointTxt(p,"shroud")}[ext](pump);if(text)saveFile(text,ext==="json"?"application/json":"text/plain;charset=utf-8",pumpExportFilename(model,side,ext),root.ownerDocument);}
      if(button.hasAttribute("data-pg-cutaway")){cutaway=!cutaway;button.setAttribute("aria-pressed",String(cutaway));mesh=buildPumpMesh(pump,36,cutaway);schedule();}
      if(button.hasAttribute("data-pg-wireframe")){wireframe=!wireframe;button.setAttribute("aria-pressed",String(wireframe));schedule();}
      if(button.dataset.pgZoom)setZoom(button.dataset.pgZoom==="in"?1.15:1/1.15);if(button.hasAttribute("data-pg-reset")){yaw=.65;pitch=-.25;zoom=1;schedule();}
    });
    listen(canvas,"pointerdown",event=>{if(event.button!==0)return;drag={x:event.clientX,y:event.clientY,id:event.pointerId};canvas.setPointerCapture?.(event.pointerId);canvas.focus();});
    listen(canvas,"pointermove",event=>{if(!drag||drag.id!==event.pointerId)return;yaw+=(event.clientX-drag.x)*.008;pitch+=(event.clientY-drag.y)*.008;drag={x:event.clientX,y:event.clientY,id:event.pointerId};schedule();});
    for(const type of ["pointerup","pointercancel","lostpointercapture"])listen(canvas,type,()=>{drag=null;});
    listen(canvas,"wheel",event=>{event.preventDefault();setZoom(event.deltaY<0?1.08:1/1.08);},{passive:false});
    listen(canvas,"keydown",event=>{if(!["ArrowLeft","ArrowRight","ArrowUp","ArrowDown","+","=","-","Home"].includes(event.key))return;event.preventDefault();if(event.key==="ArrowLeft")yaw-=.12;if(event.key==="ArrowRight")yaw+=.12;if(event.key==="ArrowUp")pitch-=.12;if(event.key==="ArrowDown")pitch+=.12;if(["+","="].includes(event.key))setZoom(1.15);if(event.key==="-")setZoom(1/1.15);if(event.key==="Home"){yaw=.65;pitch=-.25;zoom=1;}schedule();});
    if(!ctx){const error=panel.querySelector("[data-pg-canvas-error]");if(error)error.hidden=false;}
    let observer=null;if(win.ResizeObserver&&canvas){observer=new win.ResizeObserver(schedule);observer.observe(canvas);}else listen(win,"resize",schedule);
    controllers.push({schedule,cleanup:()=>{observer?.disconnect();if(frame!==null)win.cancelAnimationFrame(frame);if(drag)try{canvas.releasePointerCapture?.(drag.id);}catch{}}});
  }
  listen(root,"click",event=>{const button=event.target?.closest?.("[data-pg-side]");if(!button||!root.contains(button)||!sides.includes(button.dataset.pgSide))return;for(const tab of root.querySelectorAll("[data-pg-side]"))tab.setAttribute("aria-pressed",String(tab.dataset.pgSide===button.dataset.pgSide));for(const panel of root.querySelectorAll("[data-pg-pump]"))panel.hidden=panel.dataset.pgPump!==button.dataset.pgSide;controllers.forEach(controller=>controller.schedule());});
  const cleanup=()=>{disposed=true;listeners.forEach(remove=>remove());controllers.forEach(controller=>controller.cleanup());if(root._pumpCleanup===cleanup)delete root._pumpCleanup;};root._pumpCleanup=cleanup;return cleanup;
}
