// Saved conceptual axial turbine passage. All views and CAD exports use record coordinates.
const finite = value => typeof value === "number" && Number.isFinite(value);
const escape = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const number = value => finite(value) ? value.toLocaleString("zh-CN", {maximumSignificantDigits:6}) : "未记录";
const turbineKeys = model => ["ox", "fuel", "shared"].filter(key => Object.hasOwn(model?.turbines || {},key));
const sideLabel = side => ({ox:"氧侧涡轮",fuel:"燃料侧涡轮",shared:"共轴涡轮"})[side];
const DIMENSIONS = [
  ["mean_diameter_m", "平均直径 Dm", "mm", 1000], ["axial_length_m", "叶栅区轴向长度 Lz", "mm", 1000],
  ["inlet_hub_diameter_m", "入口轮毂直径 Dh₁", "mm", 1000], ["inlet_shroud_diameter_m", "入口外缘直径 Ds₁", "mm", 1000],
  ["outlet_hub_diameter_m", "出口轮毂直径 Dh₂", "mm", 1000], ["outlet_shroud_diameter_m", "出口外缘直径 Ds₂", "mm", 1000],
  ["inlet_blade_height_m", "入口环形流道高度 h₁", "mm", 1000], ["outlet_blade_height_m", "出口环形流道高度 h₂", "mm", 1000],
  ["inlet_area_m2", "入口环形面积", "mm²", 1e6], ["outlet_area_m2", "出口环形面积", "mm²", 1e6],
  ["inlet_axial_velocity_m_s", "入口轴向速度", "m/s"], ["outlet_axial_velocity_m_s", "出口轴向速度", "m/s"],
  ["stator_axial_chord_m", "静叶排轴向弦长", "mm", 1000], ["rotor_axial_chord_m", "动叶排轴向弦长", "mm", 1000], ["row_gap_m", "相邻叶排轴向间隙", "mm", 1000],
  ["maximum_annulus_tip_speed_m_s", "环道外缘圆周速度上界", "m/s"],
];
const DESIGN = [
  ["mass_flow_kg_s", "质量流量", "kg/s"], ["shaft_speed_rpm", "轴转速", "rpm"], ["stages", "保存级数", "级"],
  ["pressure_in_pa", "系统入口压力", "MPa", 1e-6], ["pressure_out_pa", "系统出口压力", "MPa", 1e-6], ["pressure_ratio", "膨胀压比", "—"],
  ["temperature_in_k", "系统入口温度", "K"], ["temperature_out_k", "系统出口温度", "K"], ["power_w", "涡轮功率（传动损失前）", "MW", 1e-6],
  ["density_in_kg_m3", "定尺寸入口密度", "kg/m³"], ["density_out_kg_m3", "定尺寸出口密度", "kg/m³"], ["gas_constant_j_kg_k", "燃气比气体常数", "J/(kg·K)"],
  ["volume_flow_in_m3_s", "入口体积流量", "m³/s"], ["volume_flow_out_m3_s", "出口体积流量", "m³/s"], ["mean_speed_m_s", "平均圆周速度 Um", "m/s"],
  ["isentropic_drop_j_kg", "总等熵焓降", "kJ/kg", .001], ["actual_drop_j_kg", "总实际焓降", "kJ/kg", .001], ["efficiency", "等熵效率", "—"],
  ["u_over_c_target", "目标速比 U/C₀", "—"], ["u_over_c_actual", "实际速比 U/C₀", "—"], ["target_mean_diameter_m", "目标平均直径", "mm", 1000],
];
const ASSUMPTIONS = [
  ["inlet_flow_coefficient", "入口流量系数 cm₁ / Um", "—"], ["outlet_flow_coefficient", "出口流量系数 cm₂ / Um", "—"],
  ["stator_axial_chord_ratio", "静叶排轴向弦长 / Dm", "—"], ["rotor_axial_chord_ratio", "动叶排轴向弦长 / Dm", "—"],
  ["row_gap_ratio", "叶排间隙 / Dm", "—"], ["blockage_factor", "流通面积系数", "—"], ["leakage_mass_flow_kg_s", "附加泄漏质量流量", "kg/s"],
];

function rows(values, fields) {
  return fields.map(([key,label,unit,factor=1]) => `<div><dt>${label}</dt><dd>${finite(values?.[key]) ? `${number(values[key]*factor)} <small>${unit}</small>` : "未记录"}</dd></div>`).join("");
}

function references(turbine) {
  const links=(Array.isArray(turbine?.references)?turbine.references:[]).filter(item=>typeof item?.url==="string" && /^https?:\/\//i.test(item.url));
  return links.length?`<ul class="tg-notes">${links.map(item=>`<li><a href="${escape(item.url)}" target="_blank" rel="noopener noreferrer">${escape(item.title || item.url)}</a></li>`).join("")}</ul>`:"";
}

export function inspectTurbineGeometry(turbine) {
  if (!turbine?.available) return {valid:false, reason:typeof turbine?.reason === "string" ? turbine.reason : "本条记录缺少完整的涡轮几何假设，未生成流道。请保存假设并重新计算设计点。"};
  const hub=turbine.meridional?.hub, shroud=turbine.meridional?.shroud, d=turbine.dimensions;
  const invalid=reason=>({valid:false,reason});
  if (!d || ![hub,shroud].every(curve=>Array.isArray(curve) && curve.length>=2 && curve.length<=2048) || hub.length!==shroud.length) return invalid("保存的子午面坐标不完整，无法绘图或导出。");
  if (turbine.meridional.unit!=="m" || turbine.meridional.rotation_axis!=="z" || turbine.meridional.coordinate_origin!=="first_stator_inlet") return invalid("保存的坐标单位、旋转轴或原点未定义，无法绘图或导出。");
  if (!DIMENSIONS.filter(([key])=>key!=="maximum_annulus_tip_speed_m_s").every(([key])=>finite(d[key]) && d[key]>0)) return invalid("保存的主尺寸缺失或无效，无法绘图或导出。");
  const close=(a,b)=>finite(a)&&finite(b)&&Math.abs(a-b)<=1e-9*Math.max(1,Math.abs(a),Math.abs(b));
  if ([hub,shroud].some(curve=>curve.some((p,i)=>!Array.isArray(p) || p.length!==2 || !p.every(finite) || p[0]<0 || p[1]<=0 || (i>0 && p[0]<=curve[i-1][0])))) return invalid("保存的子午面含无效坐标或轴向次序异常，无法绘图或导出。");
  if (hub.some(([z,r],i)=>!close(z,shroud[i][0]) || r>=shroud[i][1] || !close(r+shroud[i][1],d.mean_diameter_m))) return invalid("轮毂与外缘边界交叉，或平均直径与环形流道不一致。");
  if (!close(hub[0][0],0) || !close(hub.at(-1)[0],d.axial_length_m) || !close(2*hub[0][1],d.inlet_hub_diameter_m) || !close(2*shroud[0][1],d.inlet_shroud_diameter_m) || !close(2*hub.at(-1)[1],d.outlet_hub_diameter_m) || !close(2*shroud.at(-1)[1],d.outlet_shroud_diameter_m)) return invalid("保存的主尺寸与子午面端点不一致，已停止绘图与导出。");
  for (const [index,prefix] of [[0,"inlet"],[hub.length-1,"outlet"]]) {
    if (!close(shroud[index][1]-hub[index][1],d[`${prefix}_blade_height_m`]) || !close(Math.PI*(shroud[index][1]**2-hub[index][1]**2),d[`${prefix}_area_m2`])) return invalid("保存的环形面积或流道高度与边界坐标不一致。");
  }
  const stages=turbine.design_point?.stages, bladeRows=turbine.rows;
  if (!Number.isInteger(stages) || stages<1 || stages>6 || !Array.isArray(bladeRows) || bladeRows.length!==2*stages) return invalid("保存的级数与叶排位置不完整，无法绘图或导出。");
  const radiusAt=(curve,z)=>{const i=curve.findIndex(p=>p[0]>=z);if(i<=0)return curve[0][1];const [za,ra]=curve[i-1],[zb,rb]=curve[i];return ra+(rb-ra)*(z-za)/(zb-za);};
  for (let i=0;i<bladeRows.length;i++) {
    const row=bladeRows[i], kind=i%2?"rotor":"stator";
    if (!row || row.kind!==kind || row.stage!==Math.floor(i/2)+1 || !finite(row.z_start_m) || !finite(row.z_end_m) || row.z_start_m<0 || row.z_end_m>d.axial_length_m+1e-9 || row.z_start_m>=row.z_end_m || !close(row.z_end_m-row.z_start_m,d[`${kind}_axial_chord_m`]) || (i>0&&!close(row.z_start_m-bladeRows[i-1].z_end_m,d.row_gap_m))) return invalid("保存的叶排类型、弦长或间隙与轴向布置不一致。");
    for (const [key,curve] of [["hub",hub],["shroud",shroud]]) if (!Array.isArray(row[key]) || row[key].length!==2 || row[key].some((p,j)=>!Array.isArray(p)||p.length!==2||!p.every(finite)||!close(p[0],j?row.z_end_m:row.z_start_m)||!close(p[1],radiusAt(curve,Math.min(d.axial_length_m,p[0]))))) return invalid("保存的叶排包络与子午流道边界不一致。");
  }
  if (!close(bladeRows[0].z_start_m,0) || !close(bladeRows.at(-1).z_end_m,d.axial_length_m)) return invalid("叶栅区首尾位置与保存总长不一致。");
  return {valid:true,hub,shroud,rows:bladeRows,length:d.axial_length_m,radius:Math.max(...shroud.map(p=>p[1]))};
}

function dimension(x1,y1,x2,y2,label,vertical=false,side=1,labelOffset=0) {
  return `<g class="tg-dimension" stroke="#697b90" stroke-width="1" fill="none"><path d="M${x1},${y1}L${x2},${y2}"/>${vertical?`<path d="M${x1-5},${y1}h10 M${x2-5},${y2}h10 M${x1-3},${y1+5}l3,-5 3,5 M${x2-3},${y2-5}l3,5 3,-5"/>`:`<path d="M${x1},${y1-5}v10 M${x2},${y2-5}v10 M${x1+5},${y1-3}l-5,3 5,3 M${x2-5},${y2-3}l5,3 -5,3"/>`}</g><text x="${(x1+x2)/2+(vertical?side*9:0)}" y="${(y1+y2)/2+(vertical?4+labelOffset:-8)}" text-anchor="${vertical?(side>0?"start":"end"):"middle"}" fill="#40566e" font-size="13" paint-order="stroke" stroke="#fff" stroke-width="4" stroke-linejoin="round">${escape(label)}</text>`;
}

export function buildTurbineSvg(turbine) {
  const data=inspectTurbineGeometry(turbine); if (!data.valid) return "";
  const d=turbine.dimensions,width=1080,height=590,scale=Math.min(600/data.length,360/(2*data.radius)),left=230+(600-data.length*scale)/2,center=290;
  const X=z=>left+z*scale,Y=r=>center-r*scale;
  const path=(curve,sign=1)=>curve.map(([z,r],i)=>`${i?"L":"M"}${X(z)},${Y(sign*r)}`).join(" ");
  const region=(hub,shroud,sign=1)=>path(hub,sign)+shroud.toReversed().map(([z,r])=>` L${X(z)},${Y(sign*r)}`).join("")+" Z";
  const envelopes=data.rows.map(row=>{
    const color=row.kind==="stator"?"#789eb6":"#c49e68",mid=(row.z_start_m+row.z_end_m)/2;
    return `<g data-tg-row="${row.stage}-${row.kind}"><path d="${region(row.hub,row.shroud)} ${region(row.hub,row.shroud,-1)}" fill="${color}" fill-opacity=".25" stroke="${color}" stroke-width="1" stroke-dasharray="4 3"/><text x="${X(mid)}" y="${height-67-(row.kind==="stator"?0:17)}" text-anchor="middle" fill="${color}" font-size="11">${row.stage}${row.kind==="stator"?"静":"动"}</text></g>`;
  }).join("");
  const pointData=escape(JSON.stringify({hub:data.hub,shroud:data.shroud}));
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escape(turbine.label || "涡轮")}轴流流道等比例子午面" data-tg-scale="${scale}" data-tg-coordinates="${pointData}" style="font-family:system-ui,Microsoft YaHei,sans-serif;background:#fff"><title>轴流涡轮环形流道与叶排位置</title><desc>z 轴为旋转轴，首级静叶排入口为 z=0。阴影框仅表示静叶和动叶排包络，不代表叶型。坐标单位 m，标注 mm。</desc><rect width="1080" height="590" fill="#fff"/><path d="M${left-25},${center}H${X(data.length)+25}" stroke="#93a3b6" stroke-dasharray="6 4"/><path d="${region(data.hub,data.shroud)} ${region(data.hub,data.shroud,-1)}" fill="#f0f5fa"/>${envelopes}<path d="${path(data.hub)} ${path(data.hub,-1)}" fill="none" stroke="#b1884f" stroke-width="2.4"/><path d="${path(data.shroud)} ${path(data.shroud,-1)}" fill="none" stroke="#477aa9" stroke-width="2.4"/><path d="M${X(0)},${Y(d.mean_diameter_m/2)}H${X(data.length)} M${X(0)},${Y(-d.mean_diameter_m/2)}H${X(data.length)}" stroke="#8b9aab" stroke-dasharray="3 5" fill="none"/>${dimension(X(0),65,X(data.length),65,`Lz ${number(d.axial_length_m*1000)} mm`)}${dimension(X(0)-25,Y(d.inlet_shroud_diameter_m/2),X(0)-25,Y(-d.inlet_shroud_diameter_m/2),`Ds₁ ${number(d.inlet_shroud_diameter_m*1000)} mm`,true,-1,-25)}${dimension(X(0)-125,Y(d.inlet_hub_diameter_m/2),X(0)-125,Y(-d.inlet_hub_diameter_m/2),`Dh₁ ${number(d.inlet_hub_diameter_m*1000)} mm`,true,-1,25)}${dimension(X(data.length)+25,Y(d.outlet_shroud_diameter_m/2),X(data.length)+25,Y(-d.outlet_shroud_diameter_m/2),`Ds₂ ${number(d.outlet_shroud_diameter_m*1000)} mm`,true,1,-25)}${dimension(X(data.length)+125,Y(d.outlet_hub_diameter_m/2),X(data.length)+125,Y(-d.outlet_hub_diameter_m/2),`Dh₂ ${number(d.outlet_hub_diameter_m*1000)} mm`,true,1,25)}<text x="${X(0)+8}" y="${center+19}" fill="#6f8094" font-size="12">首级静叶入口 z = 0</text><text x="540" y="554" text-anchor="middle" fill="#40566e" font-size="12">Dm ${number(d.mean_diameter_m*1000)} mm · h₁ ${number(d.inlet_blade_height_m*1000)} mm · h₂ ${number(d.outlet_blade_height_m*1000)} mm</text><text x="540" y="575" text-anchor="middle" fill="#6a7c91" font-size="12">z、r 等比例 · 阴影为叶排包络，不代表叶型 · 长度仅覆盖叶栅区</text><g font-size="12" fill="#6a7c91"><path d="M28,25h24" stroke="#b1884f" stroke-width="3"/><text x="60" y="29">轮毂边界</text><path d="M180,25h24" stroke="#477aa9" stroke-width="3"/><text x="212" y="29">外缘边界</text><rect x="350" y="17" width="24" height="12" fill="#789eb6" fill-opacity=".4"/><text x="382" y="29">静叶排包络</text><rect x="500" y="17" width="24" height="12" fill="#c49e68" fill-opacity=".4"/><text x="532" y="29">动叶排包络</text></g></svg>`;
}
export function buildTurbineCsv(turbine) {
  const data=inspectTurbineGeometry(turbine); if (!data.valid) return "";
  const cell=value=>`"${String(value ?? "").replaceAll('"','""')}"`,source=cell(turbine.source?.record_id),digest=cell(turbine.geometry_sha256);
  return "boundary,point_index,z_m,r_m,record_id,geometry_sha256\r\n"+["hub","shroud"].flatMap(key=>data[key].map(([z,r],i)=>`${key},${i},${z},${r},${source},${digest}`)).join("\r\n")+"\r\n";
}

export function buildTurbineDxf(turbine) {
  const data=inspectTurbineGeometry(turbine); if (!data.valid) return "";
  const clean=value=>String(value ?? "unrecorded").replace(/[\r\n]/g," ");
  const lines=[999,`Record: ${clean(turbine.source?.record_id)}; Geometry SHA256: ${clean(turbine.geometry_sha256)}`,999,"Concept axial passage; X=z, Y=r, units m; open hub and shroud reference curves",0,"SECTION",2,"HEADER",9,"$ACADVER",1,"AC1015",9,"$INSUNITS",70,6,9,"$MEASUREMENT",70,1,0,"ENDSEC",0,"SECTION",2,"ENTITIES"];
  for (const key of ["hub","shroud"]) {lines.push(0,"LWPOLYLINE",100,"AcDbEntity",8,key.toUpperCase(),100,"AcDbPolyline",90,data[key].length,70,0); for (const [z,r] of data[key]) lines.push(10,z,20,r);}
  lines.push(0,"ENDSEC",0,"EOF"); return lines.join("\r\n")+"\r\n";
}

export function buildTurbinePointTxt(turbine,boundary) {
  const data=inspectTurbineGeometry(turbine); if (!data.valid || !["hub","shroud"].includes(boundary)) return "";
  return data[boundary].map(([z,r])=>`${z}\t${r}`).join("\r\n")+"\r\n";
}

export function buildTurbineMesh(turbine, segments=40, cutaway=false) {
  const data=inspectTurbineGeometry(turbine); if (!data.valid) return {vertices:[],faces:[]};
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

export function buildTurbineObj(turbine) {
  const mesh=buildTurbineMesh(turbine); if (!mesh.vertices.length) return "";
  const clean=value=>String(value ?? "unrecorded").replace(/[\r\n]/g," ");
  const lines=["# Concept axial turbine passage; units: m", `# Record: ${clean(turbine.source?.record_id)}; Geometry SHA256: ${clean(turbine.geometry_sha256)}`, "# Cartesian coordinates: x=r*cos(theta), y=r*sin(theta), z=axial; origin=first_stator_inlet", "# Open surfaces only. No blade profiles, disks, tip clearances, wall thickness or manufacturing solid."];
  mesh.vertices.forEach(p=>lines.push(`v ${p.join(" ")}`));let boundary=null;
  for (const face of mesh.faces) {if(face.boundary!==boundary){boundary=face.boundary;lines.push(`g ${boundary}`);}lines.push(`f ${face.indices.map(i=>i+1).join(" ")}`);}
  return lines.join("\n")+"\n";
}

export function buildTurbineJson(turbine, source={}) {
  if (!inspectTurbineGeometry(turbine).valid) return "";
  return JSON.stringify({schema_version:1,kind:"preliminary_turbine_geometry_exchange",source,turbine,exchange:{length_unit:"m",drawing_dimension_unit:"mm",coordinate_origin:"first_stator_inlet",rotation_axis:"z",csv:"boundary,point_index,z_m,r_m,record_id,geometry_sha256",txt:"separate hub/shroud files; no header; two columns z_m r_m; record ID in filename; use companion JSON for provenance",dxf:"X=z, Y=r, Z=0; INSUNITS=6 (metres); two open meridional polylines",obj:"X=r*cos(theta), Y=r*sin(theta), Z=z; open hub/shroud surfaces",usage:"CFTurbo: enter design point and dimensions, verify static/total gas state convention before aerodynamic design. NX/UG: import reference curves or surface mesh after checking units. Not a proprietary project file, blade design, CFD-ready mesh or manufacturing solid."}},null,2)+"\n";
}

export function turbineExportFilename(model,side,extension) {
  const key=["ox","fuel","shared"].includes(side)?side:"unknown",record=String(model?.source?.record_id || model?.turbines?.[key]?.source?.record_id || "unrecorded").replace(/[^a-zA-Z0-9_-]/g,"_").slice(0,80);
  if (["hub-txt","shroud-txt"].includes(extension)) return `turbine-${key}-${record}-${extension.slice(0,-4)}.txt`;
  return `turbine-${key}-${record}.${["csv","dxf","obj","json"].includes(extension)?extension:"txt"}`;
}

function renderTurbine(turbine,side,selected) {
  const data=inspectTurbineGeometry(turbine),notes=Array.isArray(turbine?.notes)?turbine.notes:[],checks=Array.isArray(turbine?.checks)?turbine.checks:[];
  const checkLabel=status=>({pass:"一致性检查通过",not_evaluated:"尚未评估",assumed:"采用显式假设",info:"参考信息",warning:"需要复核",fail:"未通过"}[status] || "状态未记录");
  const visual=data.valid?`<div class="tg-toolbar"><div class="tg-views" role="group" aria-label="几何视图"><button type="button" data-tg-view="2d" aria-pressed="true">二维子午面</button><button type="button" data-tg-view="3d" aria-pressed="false">三维流道边界</button></div><div class="tg-exports">${["csv","dxf","obj","json","hub-txt","shroud-txt"].map(ext=>`<button type="button" data-tg-export="${ext}">${({csv:"坐标 CSV",dxf:"二维 DXF",obj:"三维 OBJ",json:"尺寸与设计点 JSON","hub-txt":"轮毂 TXT","shroud-txt":"外缘 TXT"})[ext]}</button>`).join("")}</div></div><div class="tg-viewport" data-tg-panel="2d">${buildTurbineSvg(turbine)}</div><div class="tg-viewport tg-three" data-tg-panel="3d" hidden><canvas data-tg-canvas tabindex="0" aria-label="涡轮轮毂与外缘三维流道边界，拖动或方向键旋转，滚轮或加减键缩放，Home 键复位"></canvas><div class="tg-three-controls"><span>拖动旋转 · 滚轮缩放</span><button type="button" data-tg-cutaway aria-pressed="true">剖开 ¼</button><button type="button" data-tg-wireframe aria-pressed="false">线框</button><button type="button" data-tg-zoom="in" aria-label="放大三维视图">＋</button><button type="button" data-tg-zoom="out" aria-label="缩小三维视图">−</button><button type="button" data-tg-reset>复位视角</button></div><p class="tg-error" data-tg-canvas-error hidden>三维画布不可用，仍可查看二维剖面和导出坐标。</p></div>`:`<p class="tg-unavailable">${escape(data.reason)}</p><p class="tg-muted">保留已知系统设计点；缺少的假设和坐标不会由前端默认值补齐。</p>`;
  const rowTable=data.valid?`<details class="tg-details"><summary>各级静叶 / 动叶排轴向布置</summary><div class="tg-table-wrap"><table class="tg-row-table"><thead><tr><th>叶排包络</th><th>起点 z / mm</th><th>终点 z / mm</th><th>轴向弦长 / mm</th></tr></thead><tbody>${data.rows.map(row=>`<tr><th>${escape(row.label || `第 ${row.stage} 级${row.kind==="stator"?"静叶":"动叶"}排`)}</th><td>${number(row.z_start_m*1000)}</td><td>${number(row.z_end_m*1000)}</td><td>${number((row.z_end_m-row.z_start_m)*1000)}</td></tr>`).join("")}</tbody></table></div><p class="tg-muted">包络只定义叶排所占轴向区间；叶型、安装角、叶片数、厚度与端隙尚未定义。</p></details>`:"";
  return `<article class="tg-turbine" data-tg-turbine="${side}"${selected?"":" hidden"}><div class="tg-heading"><div><h3>${escape(turbine?.label || sideLabel(side))}</h3><p>${escape(turbine?.source?.label || "本次计算记录的涡轮设计点")}</p></div><span class="tg-badge">轴流流道 · 概念尺寸</span></div>${visual}<p class="tg-scope">本图给出全部保存级数对应的环形流道与叶排包络；叶栅区总长不含轴承、密封、轮盘和壳体。导出用于后续几何建模，尚不包含实际叶型和可直接求解的 CFD 网格。</p>${turbine?.design_point?.diameter_limited?'<p class="tg-unavailable">系统平均直径受到范围限制，实际速比可能偏离目标；请结合保存的检查结果复核。</p>':""}<h4>几何尺寸</h4><dl class="tg-data">${rows(turbine?.dimensions,DIMENSIONS)}</dl>${rowTable}<details class="tg-details" open><summary>本记录的系统设计点</summary><dl class="tg-data">${rows(turbine?.design_point,DESIGN)}</dl><p class="tg-muted">系统压力、温度用于本记录的概念定尺寸；后续气动设计需明确总态 / 静态与速度三角形，并复核进出口流动状态。</p></details><details class="tg-details"><summary>几何假设与检查</summary><dl class="tg-data">${rows(turbine?.assumptions,ASSUMPTIONS)}</dl><p class="tg-muted">${turbine?.assumptions?.geometry_state_basis==="system_pt_as_static"?"本记录显式采用“系统 p、T 作为静态近似”。":"本记录未声明可用的系统 p、T 状态近似。"}入口组分的比气体常数用于 ρ = p / (RT)，cm = φUm，A = ṁ / (ρcm)，h = A / (πDm)。按恒定平均半径连接进出口；内部截面为几何插值，不是逐级气动求解。未扣除叶片厚度和端隙，也未求解反动度与载荷分配。</p>${checks.length?`<ul class="tg-checks">${checks.map(check=>`<li><strong>${escape(check?.label || check?.id || "检查项")}</strong><span>${checkLabel(check?.status)}</span>${finite(check?.value)?`<span>${number(check.value)}</span>`:""}${check?.note?`<p>${escape(check.note)}</p>`:""}</li>`).join("")}</ul>`:'<p class="tg-muted">本记录未保存检查结果。</p>'}${notes.length?`<ul class="tg-notes">${notes.map(note=>`<li>${escape(note)}</li>`).join("")}</ul>`:""}${references(turbine)}<p class="tg-fingerprint">几何记录标识：${escape(turbine?.geometry_sha256 || "未保存")}</p></details><details class="tg-details"><summary>在 CFTurbo、NX / UG 中继续建模</summary><p>在专业涡轮设计软件中核对燃气物性与总态 / 静态定义，再录入转速、流量、功率和主尺寸，继续叶型、级间匹配与三维叶片设计。在 NX / UG 中可导入子午曲线或边界网格作为建模参考，随后补全轮盘、端隙与壳体。</p><p>CSV、TXT、DXF、OBJ 与本页使用同一组保存坐标，单位为 m；页面标注为 mm。DXF 的 X=z、Y=r；OBJ 的 Z 为旋转轴。轮毂 / 外缘 TXT 为无表头两列 z、r；请同时保留 JSON 中的设计点、假设和记录标识。OBJ 始终导出完整周向边界，页面剖开只影响显示。导出不是软件原生工程、叶片实体或 CFD 网格。</p></details></article>`;
}

export function renderTurbineGeometry(model) {
  const source=model?.source || {},sides=turbineKeys(model);
  return `<section class="turbine-geometry" data-turbine-geometry><div class="tg-title"><div><span class="tg-eyebrow">TURBINE GEOMETRY</span><h2>涡轮概念几何</h2><p>保存设计点 → 环形流道主尺寸 → 多级叶排布局 → CAD 参考数据</p></div></div><p class="tg-source">计算记录：${escape(source.record_id || "未指定")}。几何与本条记录绑定，用于总体布局与后续专业设计；一致性检查不代表气动效率、强度或热性能已验证。</p>${sides.length?`<div class="tg-select" role="group" aria-label="涡轮选择">${sides.map((side,i)=>`<button type="button" data-tg-side="${side}" aria-pressed="${i===0}">${escape(model.turbines[side]?.label || sideLabel(side))}</button>`).join("")}</div>${sides.map((side,i)=>renderTurbine(model.turbines[side],side,i===0)).join("")}`:'<p class="tg-unavailable">本条记录未保存可用的涡轮系统点。保存几何假设并重新计算设计点后可生成概念流道。</p>'}</section>`;
}
function saveFile(text,type,name,doc) {
  const win=doc.defaultView || window,url=win.URL.createObjectURL(new win.Blob([text],{type})),a=doc.createElement("a");a.href=url;a.download=name;doc.body.append(a);a.click();a.remove();win.setTimeout(()=>win.URL.revokeObjectURL(url),1000);
}

export function mountTurbineGeometry(container,model) {
  const root=container?.matches?.("[data-turbine-geometry]")?container:container?.querySelector?.("[data-turbine-geometry]"); if (!root) return ()=>{};
  root._turbineCleanup?.(); const win=root.ownerDocument.defaultView || window,listeners=[],controllers=[],sides=turbineKeys(model);let disposed=false;
  const listen=(element,type,fn,options)=>{element?.addEventListener(type,fn,options);listeners.push(()=>element?.removeEventListener(type,fn,options));};
  for (const side of sides) {
    const turbine=model?.turbines?.[side],data=inspectTurbineGeometry(turbine),panel=root.querySelector(`[data-tg-turbine="${side}"]`);if(!data.valid || !panel) continue;
    const canvas=panel.querySelector("[data-tg-canvas]");let ctx=null;try{ctx=canvas?.getContext?.("2d");}catch{}
    let yaw=.65,pitch=-.25,zoom=1,drag=null,frame=null,cutaway=true,wireframe=false,mesh=buildTurbineMesh(turbine,36,true);
    const draw=()=>{frame=null;if(disposed || !ctx || panel.hidden || panel.querySelector('[data-tg-panel="3d"]')?.hidden)return;
      const box=canvas.getBoundingClientRect(),width=Math.max(240,box.width),height=Math.max(260,box.height),ratio=Math.min(win.devicePixelRatio || 1,2);canvas.width=Math.round(width*ratio);canvas.height=Math.round(height*ratio);ctx.setTransform(ratio,0,0,ratio,0,0);ctx.clearRect(0,0,width,height);ctx.fillStyle="#f8fafd";ctx.fillRect(0,0,width,height);
      const scale=Math.min(width*.73/Math.hypot(data.length,2*data.radius),height*.70/(2*data.radius))*zoom,cy=Math.cos(yaw),sy=Math.sin(yaw),cp=Math.cos(pitch),sp=Math.sin(pitch);
      const points=mesh.vertices.map(([rX,rY,z])=>{const x=(z-data.length/2)*cy+rX*sy,depth=-(z-data.length/2)*sy+rX*cy;return [width/2+(x*cp-rY*sp)*scale,height/2-(x*sp+rY*cp)*scale,depth];});
      const faces=mesh.faces.map(face=>({...face,points:face.indices.map(i=>points[i]),depth:face.indices.reduce((sum,i)=>sum+points[i][2],0)/4})).sort((a,b)=>a.depth-b.depth);
      for(const face of faces){ctx.beginPath();face.points.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));ctx.closePath();if(!wireframe){ctx.fillStyle=face.boundary==="hub"?"#c9b18b":"#b1c8de";ctx.fill();}ctx.strokeStyle=face.boundary==="hub"?"rgba(140,107,60,.45)":"rgba(57,94,129,.4)";ctx.lineWidth=wireframe ? .65 : .35;ctx.stroke();}
      ctx.fillStyle="#657a92";ctx.font="12px system-ui, sans-serif";ctx.fillText("轮毂（金色）与外缘（蓝色）流道边界 · 无叶型 / 无壁厚",17,25);
    };
    const schedule=()=>{if(frame===null&&!disposed)frame=win.requestAnimationFrame(draw);};
    const setZoom=factor=>{zoom=Math.max(.4,Math.min(3,zoom*factor));schedule();};
    listen(panel,"click",event=>{const button=event.target?.closest?.("button");if(!button||!panel.contains(button))return;
      if(["2d","3d"].includes(button.dataset.tgView)){for(const tab of panel.querySelectorAll("[data-tg-view]"))tab.setAttribute("aria-pressed",String(tab.dataset.tgView===button.dataset.tgView));for(const p of panel.querySelectorAll("[data-tg-panel]"))p.hidden=p.dataset.tgPanel!==button.dataset.tgView;schedule();}
      const ext=button.dataset.tgExport;if(["csv","dxf","obj","json","hub-txt","shroud-txt"].includes(ext)){const text={csv:buildTurbineCsv,dxf:buildTurbineDxf,obj:buildTurbineObj,json:p=>buildTurbineJson(p,model?.source),"hub-txt":p=>buildTurbinePointTxt(p,"hub"),"shroud-txt":p=>buildTurbinePointTxt(p,"shroud")}[ext](turbine);if(text)saveFile(text,ext==="json"?"application/json":"text/plain;charset=utf-8",turbineExportFilename(model,side,ext),root.ownerDocument);}
      if(button.hasAttribute("data-tg-cutaway")){cutaway=!cutaway;button.setAttribute("aria-pressed",String(cutaway));mesh=buildTurbineMesh(turbine,36,cutaway);schedule();}
      if(button.hasAttribute("data-tg-wireframe")){wireframe=!wireframe;button.setAttribute("aria-pressed",String(wireframe));schedule();}
      if(button.dataset.tgZoom)setZoom(button.dataset.tgZoom==="in"?1.15:1/1.15);if(button.hasAttribute("data-tg-reset")){yaw=.65;pitch=-.25;zoom=1;schedule();}
    });
    listen(canvas,"pointerdown",event=>{if(event.button!==0)return;drag={x:event.clientX,y:event.clientY,id:event.pointerId};canvas.setPointerCapture?.(event.pointerId);canvas.focus();});
    listen(canvas,"pointermove",event=>{if(!drag||drag.id!==event.pointerId)return;yaw+=(event.clientX-drag.x)*.008;pitch+=(event.clientY-drag.y)*.008;drag={x:event.clientX,y:event.clientY,id:event.pointerId};schedule();});
    for(const type of ["pointerup","pointercancel","lostpointercapture"])listen(canvas,type,()=>{drag=null;});
    listen(canvas,"wheel",event=>{event.preventDefault();setZoom(event.deltaY<0?1.08:1/1.08);},{passive:false});
    listen(canvas,"keydown",event=>{if(!["ArrowLeft","ArrowRight","ArrowUp","ArrowDown","+","=","-","Home"].includes(event.key))return;event.preventDefault();if(event.key==="ArrowLeft")yaw-=.12;if(event.key==="ArrowRight")yaw+=.12;if(event.key==="ArrowUp")pitch-=.12;if(event.key==="ArrowDown")pitch+=.12;if(["+","="].includes(event.key))setZoom(1.15);if(event.key==="-")setZoom(1/1.15);if(event.key==="Home"){yaw=.65;pitch=-.25;zoom=1;}schedule();});
    if(!ctx){const error=panel.querySelector("[data-tg-canvas-error]");if(error)error.hidden=false;}
    let observer=null;if(win.ResizeObserver&&canvas){observer=new win.ResizeObserver(schedule);observer.observe(canvas);}else listen(win,"resize",schedule);
    controllers.push({schedule,cleanup:()=>{observer?.disconnect();if(frame!==null)win.cancelAnimationFrame(frame);if(drag)try{canvas.releasePointerCapture?.(drag.id);}catch{}}});
  }
  listen(root,"click",event=>{const button=event.target?.closest?.("[data-tg-side]");if(!button||!root.contains(button)||!sides.includes(button.dataset.tgSide))return;for(const tab of root.querySelectorAll("[data-tg-side]"))tab.setAttribute("aria-pressed",String(tab.dataset.tgSide===button.dataset.tgSide));for(const panel of root.querySelectorAll("[data-tg-turbine]"))panel.hidden=panel.dataset.tgTurbine!==button.dataset.tgSide;controllers.forEach(controller=>controller.schedule());});
  const cleanup=()=>{disposed=true;listeners.forEach(remove=>remove());controllers.forEach(controller=>controller.cleanup());if(root._turbineCleanup===cleanup)delete root._turbineCleanup;};root._turbineCleanup=cleanup;return cleanup;
}
