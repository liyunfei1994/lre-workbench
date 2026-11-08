/* Read-only main-chamber analysis. Views and exports use one saved record. */
import {nozzleChart} from "./nozzle-design.js";
import {buildNozzleSurface} from "./nozzle-geometry.js";

const finite = value => typeof value === "number" && Number.isFinite(value);
const array = value => Array.isArray(value) ? value : [];
const text = value => value == null ? "" : typeof value === "object" ? JSON.stringify(value) : String(value);
const escape = value => text(value).replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const number = value => finite(value) ? value.toLocaleString("zh-CN", {maximumSignificantDigits:7}) : "—";
const labels = {ready:"资料可用",saved:"已保存",derived:"由保存值计算",pass:"核对一致",review:"需复核",warning:"需复核",inconsistent:"存在冲突",fail:"不一致",invalid:"数据异常",missing:"待补充",unavailable:"不可用",not_modelled:"未建模",model_assumption:"模型假设",assumed:"假设值",included:"已计入",incomplete:"资料不全",partial:"资料不全",screening_only:"仅作筛查",not_validated:"尚未验证",consistent:"内部一致",within_range:"声明温区内",out_of_range:"超出声明温区",matched:"工艺类别匹配",mismatch:"工艺声明不匹配",unknown:"尚未确认",within_declared_ranges:"保存检查：声明温区内",extrapolated:"保存检查：存在温区外推"};
const status = value => `<span class="cd-status" data-status="${Object.hasOwn(labels,value) ? value : "missing"}">${labels[value] || "待确认"}</span>`;
const empty = reason => `<p class="cd-empty">${escape(reason || "本记录未保存此项数据。")}</p>`;
const section = (title, body, caption="") => `<section class="cd-section"><h3>${escape(title)}</h3>${caption ? `<p class="cd-caption">${escape(caption)}</p>` : ""}${body}</section>`;
const scales = {Pa:[1e-6,"MPa"],W:[1e-6,"MW"],"J/kg":[1e-6,"MJ/kg"],m:[1e3,"mm"],m2:[1e6,"mm²"],"m²":[1e6,"mm²"],m3:[1e3,"L"],"m³":[1e3,"L"],"W/m2":[1e-6,"MW/m²"],"W/m²":[1e-6,"MW/m²"]};
function display(field) {
  const [factor,unit] = scales[field?.unit] || [1,field?.unit || "—"];
  return {value: finite(field?.value) ? number(field.value * factor) : typeof field?.value === "string" && field.value ? escape(field.value) : "—", unit};
}
function fields(items) {
  if (!array(items).length) return empty();
  return `<div class="cd-scroll"><table class="cd-fields"><thead><tr><th>参数</th><th>保存值 / 派生值</th><th>状态</th><th>说明</th></tr></thead><tbody>${items.map(field => {
    const view = display(field);
    return `<tr><th scope="row">${escape(field.label || field.key)}</th><td class="cd-value">${view.value} <small>${escape(view.unit)}</small></td><td>${status(field.status === "invalid" ? "invalid" : field.value == null || typeof field.value === "number" && !finite(field.value) ? "missing" : field.status)}</td><td>${escape(field.note || "—")}</td></tr>`;
  }).join("")}</tbody></table></div>`;
}
function checks(items) {
  return array(items).length ? `<ul class="cd-checks">${items.map(item => {
    const actual=item.actual ?? item.geometry_value,expected=item.expected ?? item.record_value;
    const material="expected_material_id" in item ? `<p>X = ${number(item.x_m)} m · 期望材料 ${escape(item.expected_material_id || "未记录")} / 数据集 ${escape(item.expected_dataset_id || "未记录")}<br>实际材料 ${escape(item.actual_material_id || "未记录")} / 数据集 ${escape(item.actual_dataset_id || "未记录")}</p>` : "";
    return `<li><div><strong>${escape(item.label || item.topic || item.key || item.id)}</strong>${status(item.status)}</div><p>${escape(item.note || item.message || "")}</p>${finite(actual) || finite(expected) ? `<small>保存值 ${number(actual)} · 对照 ${number(expected)} ${escape(item.unit || "")}</small>` : ""}${material}</li>`;
  }).join("")}</ul>` : empty("未保存可用核对项。");
}
const noteList = items => array(items).length ? `<ul class="cd-notes">${items.map(item => `<li>${escape(typeof item === "object" ? item.note || item.message || item.label || item.detail : item)}</li>`).join("")}</ul>` : "";
const allFields = model => [...array(model?.metrics),...array(model?.performance?.metrics),...array(model?.model?.metrics),...array(model?.geometry?.metrics)];
const metric = (model,keys) => allFields(model).find(item => keys.includes(item.key));
function hero(model) {
  const groups = [["燃烧室参考压力",["pressure_Pa","pc_Pa","chamber_pressure_Pa"]],["主室参考温度",["temperature_K","T_K","T_chamber_K","chamber_temperature_K"]],["主室质量流量",["mass_flow_kg_s","mass_flow_kgs","mdot_kg_s"]],["系统有效 c*",["c_star_effective_ms","c_star_ms","cstar_ms"]]];
  return `<div class="cd-metrics">${groups.map(([label,keys]) => { const field=metric(model,keys),view=display(field); return `<div><span>${label}</span><strong>${view.value}<small>${escape(view.unit)}</small></strong>${status(field?.status)}</div>`; }).join("")}</div>`;
}
function compositionRows(composition) {
  if (Array.isArray(composition?.rows)) return composition.rows;
  return [];
}
function compositionTable(composition,{bars=false}={}) {
  const rows = compositionRows(composition);
  if (!rows.length) return empty(composition?.reason || composition?.note || "未保存组分数据。");
  const rawBasis=composition?.basis !== "mass_fraction" && rows.some(row=>Object.hasOwn(row,"raw_fraction"));
  const valid = rows.filter(row => finite(row.mass_fraction) && row.mass_fraction >= 0 && row.mass_fraction <= 1);
  const sorted = [...valid].sort((a,b) => b.mass_fraction - a.mass_fraction);
  const chart = bars && sorted.length ? `<svg class="cd-composition-chart" viewBox="0 0 650 ${Math.min(10,sorted.length)*28+32}" role="img" aria-label="主要组分质量分数"><title>质量分数最大的十项组分；全部组分见下表，未重新归一化</title>${sorted.slice(0,10).map((row,i) => `<text x="12" y="${26+i*28}" fill="#506c82" font-size="12">${escape(row.species)}</text><rect x="102" y="${12+i*28}" width="440" height="17" fill="#eef3f7"/><rect x="102" y="${12+i*28}" width="${row.mass_fraction*440}" height="17" fill="#487995"/><text x="555" y="${26+i*28}" fill="#506c82" font-size="11">${number(row.mass_fraction*100)}%</text>`).join("")}</svg>` : "";
  return `${chart}<div class="cd-composition-summary"><span>${rawBasis ? `保存基准：${escape(composition?.basis || "未记录")}` : `质量分数总和：${number(composition?.mass_fraction_sum)}`}</span>${status(composition?.status)}<span>${escape(composition?.note)}</span></div><div class="cd-scroll cd-composition-table"><table><thead><tr><th>组分</th><th>${rawBasis ? "原保存分数（未转换）" : "质量分数 Y"}</th><th>摩尔分数 X</th><th>摩尔质量 / (kg/kmol)</th></tr></thead><tbody>${rows.map(row => `<tr><th scope="row">${escape(row.species)}</th><td${finite(row.mass_fraction) && (row.mass_fraction < 0 || row.mass_fraction > 1) ? ' class="cd-invalid"' : ""}>${number(rawBasis ? row.raw_fraction : row.mass_fraction)}</td><td>${number(row.mole_fraction)}</td><td>${number(row.molar_mass_kg_kmol)}</td></tr>`).join("")}</tbody></table></div>`;
}
function stateBasis(basis) {
  if (typeof basis === "string") return basis;
  if (basis?.reason || basis?.label) return basis.reason || basis.label;
  return ({reservoir_approximation:"零维储腔近似，未解析局部速度",total_to_total:"入口与出口总态",unspecified:"未明确总态 / 静态"})[basis?.kind] || "本记录未明确总态 / 静态与速度口径";
}
function overview(model) {
  return `${section("主室设计点",fields(model.metrics),"取自当前计算记录；主室与喷管、冷却和循环使用同一设计点。")}<div class="cd-columns">${array(model.inlets).map(inlet => section(inlet.label || "入口燃气",`${fields(inlet.metrics)}<p class="cd-caption">状态口径：${escape(stateBasis(inlet.state_basis))}</p><details><summary>查看入口燃气组分</summary>${compositionTable(inlet.composition)}</details>`,"富氧 / 富燃涡轮后燃气；采用保存的组分与焓。" )).join("") || empty("未保存两股入口燃气的完整界面数据。")}</div>${section("质量、元素与能量收支",`${fields(model.balances?.metrics)}${checks(model.balances?.checks)}`)}${section("跨部件一致性",checks(model.checks))}`;
}
function temperatureEvidence(evidence) {
  if(!evidence || typeof evidence !== "object" || !Object.keys(evidence).length)return empty("本记录未保存燃气热化学温区检查；不按当前机制补算。");
  const outside=array(evidence.species_outside);
  return `<div class="${evidence.status === "extrapolated" ? "cd-warning" : "cd-note"}"><strong>保存的燃气热化学温区检查</strong> ${status(evidence.status)}<dl class="cd-facts"><div><dt>保存温度</dt><dd>${number(evidence.temperature_K)} K</dd></div><div><dt>存在物种的共同声明温区</dt><dd>${number(evidence.valid_temperature_min_K)}–${number(evidence.valid_temperature_max_K)} K</dd></div><div><dt>整个机制的共同声明温区</dt><dd>${number(evidence.common_temperature_min_K)}–${number(evidence.common_temperature_max_K)} K</dd></div><div><dt>保存的温区外物种质量分数合计</dt><dd>${number(evidence.outside_mass_fraction)}</dd></div></dl><p>${escape(evidence.interpretation || "只展示保存检查；温区内不代表高压物性或机理已验证，外推占比不是误差百分比。")}</p><details><summary>保存温区证据 · ${outside.length} 个温区外物种</summary><pre class="cd-scroll">${escape(JSON.stringify(evidence,null,2))}</pre></details></div>`;
}
function modelEvidence(model) {
  const metadata=model?.metadata || {};
  return `<dl class="cd-facts"><div><dt>方法</dt><dd>${escape(model?.label || "未记录")}</dd></div><div><dt>热化学数据</dt><dd>${escape(metadata.mechanism || "未记录")}</dd></div><div><dt>状态方程</dt><dd>${escape(metadata.eos || "未记录")}</dd></div><div><dt>状态约定</dt><dd>${escape(stateBasis(model?.state_basis))}</dd></div><div><dt>高压适用性</dt><dd>${metadata.high_pressure_validation === "not_validated" ? "尚未验证" : escape(metadata.high_pressure_validation || "未记录验证证据")}</dd></div></dl>${temperatureEvidence(model?.temperature_range_evidence)}`;
}
function chemistry(model) {
  return `${section("热化学模型",`${modelEvidence(model.model)}${fields(model.model?.metrics)}${noteList(model.model?.notes)}<button type="button" data-cd-action="validation">实际入口的 NASA CEA / Cantera 复核 →</button><p class="cd-caption">独立对照应使用相同元素库存、入口焓、压力与允许物种；一致性对照不等于试验准确性验证。</p>`)}<div class="cd-note"><strong>平衡状态是热化学参考</strong><p>平衡温度与组分不描述有限时间内的点火、混合或反应过程。富燃产物中的 CO、H₂ 和自由基不能直接解释为未燃尽；特征速度效率也不等于燃料转化率。</p></div>${section("主室燃气组分",`${compositionTable(model.composition,{bars:true})}<p class="cd-caption">表格保留全部保存组分。条图只展示已明确质量基准的主要十项；基准未确认或缺少摩尔质量时不补算摩尔分数。</p><button type="button" data-cd-export="composition"${compositionRows(model.composition).length ? "" : " disabled"}>组分 CSV</button>`)}${section("燃烧性能与参考值",fields(model.performance?.metrics),"比较值必须采用一致的入口焓、元素组成、压力与喉面积口径。")}`;
}

export function inspectChamberGeometry(geometry) {
  if (!geometry?.available) return {valid:false,reason:geometry?.reason || "本记录未保存可用主室内型面。"};
  const x=geometry.contour?.x_m,r=geometry.contour?.r_m;
  if (!Array.isArray(x) || !Array.isArray(r) || x.length !== r.length || x.length < 3) return {valid:false,reason:"主室原始母线坐标不完整。"};
  if (x.some((value,i) => !finite(value) || !finite(r[i]) || r[i] <= 0 || i > 0 && value <= x[i-1])) return {valid:false,reason:"主室母线存在无效值或轴向次序异常；暂停绘图与坐标导出。"};
  const throat=geometry.dimensions?.throat_x_m;
  if (!finite(throat) || Math.abs(x[0])>1e-10 || Math.abs(x.at(-1)-throat)>1e-9) return {valid:false,reason:"主室母线未完整覆盖喷注面至喉部，或包含喉后喷管点。"};
  return {valid:true,x,r,minX:x[0],maxX:x.at(-1),maxR:Math.max(...r),length:x.at(-1)-x[0]};
}
const dimensionPath='fill="none" stroke="#7b8d9f" stroke-width="1"';
function dimension(x1,y1,x2,y2,label,vertical=false) {
  return `<g><path ${dimensionPath} d="M${x1},${y1}L${x2},${y2}${vertical ? ` M${x1-5},${y1}h10 M${x2-5},${y2}h10` : ` M${x1},${y1-5}v10 M${x2},${y2-5}v10`}"/><text x="${vertical ? x1-10 : (x1+x2)/2}" y="${vertical ? (y1+y2)/2 : y1-10}" text-anchor="${vertical ? "end" : "middle"}" fill="#546b7f" font-size="12">${escape(label)}</text></g>`;
}
export function buildChamberSvg(geometry,{unit="mm"}={}) {
  const data=inspectChamberGeometry(geometry); if (!data.valid) return "";
  const factor=unit === "m" ? 1 : 1000,displayUnit=unit === "m" ? "m" : "mm",scale=Math.min(680/data.length,290/(data.maxR*2));
  const left=160+(680-data.length*scale)/2,center=245,X=x=>left+(x-data.minX)*scale,Y=r=>center-r*scale,d=geometry.dimensions || {};
  const top=data.x.map((x,i)=>`${i ? "L" : "M"}${X(x)},${Y(data.r[i])}`).join(" "),bottom=data.x.map((x,i)=>`${i ? "L" : "M"}${X(x)},${Y(-data.r[i])}`).join(" ");
  const reverseBottom=data.x.map((x,i)=>`L${X(x)},${Y(-data.r[i])}`).reverse().join(" ");
  const grid=Array.from({length:19},(_,i)=>`<path d="M${80+i*45},80V420" fill="none" stroke="#e9eff4" stroke-width="1"/>`).join("")+Array.from({length:9},(_,i)=>`<path d="M80,${80+i*42}H920" fill="none" stroke="#e9eff4" stroke-width="1"/>`).join("");
  const annotations=[];
  if (finite(d.chamber_diameter_m)) annotations.push(dimension(left-35,Y(d.chamber_diameter_m/2),left-35,Y(-d.chamber_diameter_m/2),`Dc ${number(d.chamber_diameter_m*factor)} ${displayUnit}`,true));
  if (finite(d.throat_diameter_m) && Math.abs(data.maxX-d.throat_x_m)<1e-8) annotations.push(dimension(X(data.maxX)+35,Y(d.throat_diameter_m/2),X(data.maxX)+35,Y(-d.throat_diameter_m/2),`Dt ${number(d.throat_diameter_m*factor)} ${displayUnit}`,true));
  annotations.push(dimension(X(data.minX),65,X(data.maxX),65,`保存母线范围 ${number(data.length*factor)} ${displayUnit}`));
  if (finite(d.chamber_length_m) && d.chamber_length_m>data.minX && d.chamber_length_m<data.maxX) annotations.push(dimension(X(data.minX),455,X(d.chamber_length_m),455,`直筒段 ${number(d.chamber_length_m*factor)} ${displayUnit}`),dimension(X(d.chamber_length_m),490,X(data.maxX),490,`收缩段 ${number((data.maxX-d.chamber_length_m)*factor)} ${displayUnit}`));
  return `<svg class="cd-contour" viewBox="0 0 1000 560" role="img" aria-label="保存的主燃烧室内边界二维剖面" data-chamber-scale="${scale}" xmlns="http://www.w3.org/2000/svg"><title>主室原始内型面，轴向与径向等比例；无喷管扩张段、壁厚或冷却槽</title>${grid}<path d="${top} ${reverseBottom} Z" fill="#eaf1f6" stroke="none"/><path d="${top}" fill="none" stroke="#376f97" stroke-width="2.3"/><path d="${bottom}" fill="none" stroke="#376f97" stroke-width="2.3"/><path d="M${left-18},${center}H${X(data.maxX)+20}" fill="none" stroke="#9aafbf" stroke-dasharray="12 4 2 4" stroke-width="1"/>${annotations.join("")}<text x="500" y="540" text-anchor="middle" fill="#7d91a3" font-size="12">轴向 X / ${displayUnit} · ${geometry.contour.coordinate_origin === "injector_face" ? "喷注面原点" : "保留保存的坐标原点"} · 等比例剖面</text></svg>`;
}
export function buildChamberSurfaceSvg(geometry,{angle="iso"}={}) {
  if (!inspectChamberGeometry(geometry).valid) return "";
  const mesh=buildNozzleSurface(geometry,32),yaw=angle === "side" ? 0 : angle === "end" ? -Math.PI/2 : -.65,pitch=angle === "iso" ? -.25 : 0;
  const rotated=mesh.map(ring=>ring.map(([x,y,z])=>{const u=x*Math.cos(yaw)+z*Math.sin(yaw),w=-x*Math.sin(yaw)+z*Math.cos(yaw);return [u,y*Math.cos(pitch)-w*Math.sin(pitch),y*Math.sin(pitch)+w*Math.cos(pitch)];}));
  const points=rotated.flat(),xs=points.map(p=>p[0]),ys=points.map(p=>p[1]),xmin=Math.min(...xs),xmax=Math.max(...xs),ymin=Math.min(...ys),ymax=Math.max(...ys),scale=Math.min(760/Math.max(xmax-xmin,1e-8),330/Math.max(ymax-ymin,1e-8)),project=p=>[500+(p[0]-(xmin+xmax)/2)*scale,240-(p[1]-(ymin+ymax)/2)*scale,p[2]];
  const rings=rotated.map(ring=>ring.map(project)),faces=[];
  for(let i=0;i<rings.length-1;i++)for(let j=0;j<32;j++){const next=(j+1)%32,vertices=[rings[i][j],rings[i+1][j],rings[i+1][next],rings[i][next]];faces.push({vertices,depth:vertices.reduce((sum,p)=>sum+p[2],0)/4,light:65+12*Math.cos(j/32*Math.PI*2)});}
  faces.sort((a,b)=>a.depth-b.depth);
  return `<svg class="cd-surface" viewBox="0 0 1000 500" role="img" aria-label="主室内边界三维旋转参考面"><title>原始母线绕 X 轴旋转，无实体壁厚、喷注器和冷却槽</title>${faces.map(face=>`<polygon points="${face.vertices.map(p=>`${p[0]},${p[1]}`).join(" ")}" fill="hsl(207,23%,${face.light}%)" stroke="#7e9aaf" stroke-width="0.35"/>`).join("")}${[rings[0],rings.at(-1)].map(ring=>`<polygon points="${ring.map(p=>`${p[0]},${p[1]}`).join(" ")}" fill="none" stroke="#476b86" stroke-width="1.3"/>`).join("")}<text x="30" y="465" fill="#6b8194" font-size="12">X：燃气流向 · 旋转参考面 · 两端开放</text></svg>`;
}
function geometry(model) {
  const g=model.geometry || {},inspection=inspectChamberGeometry(g),allowed=inspection.valid && g.export_allowed === true && g.consistency?.status === "pass";
  const visual=inspection.valid ? `<div class="cd-geometry-toolbar"><div role="group" aria-label="主室几何视图"><button type="button" data-cd-view="2d" aria-pressed="true">二维剖面</button><button type="button" data-cd-view="3d" aria-pressed="false">三维参考面</button></div><label>显示单位 <select data-cd-unit><option value="mm">mm</option><option value="m">m</option></select></label></div><div data-cd-geometry-panel="2d">${buildChamberSvg(g)}</div><div data-cd-geometry-panel="3d" hidden><div data-cd-surface>${buildChamberSurfaceSvg(g)}</div><div class="cd-geometry-toolbar" role="group" aria-label="三维参考面视角"><button type="button" data-cd-angle="iso" aria-pressed="true">轴测</button><button type="button" data-cd-angle="side" aria-pressed="false">正视</button><button type="button" data-cd-angle="end" aria-pressed="false">喉部方向</button></div></div><div class="cd-geometry-footer"><span>${inspection.x.length} 个保存点 · ${escape(g.source?.label || "保存内型面")}</span><button type="button" data-cd-export="coordinates"${allowed ? "" : " disabled"}>主室坐标 CSV</button><button type="button" data-cd-action="geometry">完整推力室内型面 →</button></div>` : empty(inspection.reason);
  return `${section("主室内型面工作区",visual,"范围为喷注面至喉部。二维与三维共用原始母线；三维仅为内边界参考面，不包含壁厚、喷注器、冷却槽或承力壳。")}${allowed ? "" : `<div class="cd-warning">主室坐标尚未通过导出条件，请先核对几何数据与记录一致性。</div>`}${section("容积与主尺寸",fields(g.metrics))}${section("几何一致性",checks(g.consistency?.checks))}<div class="cd-note"><strong>长度与停留时间的含义</strong><p>L* 是容积与喉面积的比值；平均停留时间是流动时间尺度估计。两者都不能单独证明混合完成、燃尽或燃烧稳定。</p></div>`;
}
const thermalColumns=[["x_m","X / m"],["q_W_m2","热流密度 / (W/m²)"],["T_wall_hot_K","热侧壁温 / K"],["T_wall_cold_K","冷侧壁温 / K"],["T_coolant_K","冷却剂温度 / K"],["p_coolant_Pa","冷却剂压力 / Pa"],["T_static_K","燃气静温 / K"],["p_gas_Pa","燃气静压 / Pa"],["M","燃气 Mach"],["T_aw_K","绝热壁温 / K"],["Q_W","单元吸热 / W"]];
function materialEvidence(materials) {
  const rows=array(materials.evidence),range=value=>array(value).length===2 ? value.map(number).join("–")+" K" : "未记录";
  const identity=`<dl class="cd-facts"><div><dt>方案内衬材料</dt><dd>${escape(materials.requested_material_id || "未记录")}</dd></div><div><dt>实际采用材料</dt><dd>${escape(array(materials.actual_material_ids).join("、") || "未记录")}</dd></div></dl>`;
  if(!rows.length)return identity;
  return `${identity}<details><summary>材料依据与保存信息 · ${rows.length} 个单元</summary><p>数据集试样工艺与方案声明分开记录；类别匹配不代表实际硬件工艺或强度已验证。</p><div class="cd-scroll cd-material-evidence"><table><thead><tr><th>单元 / X (m)</th><th>材料 / 工艺依据</th><th>物性数据</th><th>实际温区</th><th>声明温区</th><th>温区检查</th></tr></thead><tbody>${rows.map(row=>`<tr><td>${escape(row.cell_index)} / ${number(row.x_m)}</td><td>${escape(row.material_id || "未记录")}<small>数据集试样工艺：${escape(row.manufacturing_state_id || "未记录")}</small><small>方案声明：${escape(row.declared_manufacturing_state === "unspecified" ? "未指定" : row.declared_manufacturing_state || "未记录")}</small>${status(row.manufacturing_match || "unknown")}</td><td>${escape(row.dataset_id || "未记录")}<small>${escape(row.property || "")}</small></td><td>${range(row.actual_temperature_range_K)}</td><td>${range(row.data_temperature_range_K)}</td><td>${status(row.status || (row.within_range === true ? "within_range" : row.within_range === false ? "out_of_range" : "missing"))}</td></tr>`).join("")}</tbody></table></div></details>`;
}
function thermal(model) {
  const data=model.thermal || {},nodes=thermalNodes(model),materials=model.materials || {};
  const plots=nodes.length ? `<div class="cd-columns">${[["q_W_m2",1e-6,"热流密度 / (MW/m²)"],["T_wall_hot_K",1,"热侧壁温 / K"]].map(([yKey,yFactor,yLabel])=>`<div class="cd-plot">${nozzleChart(nodes,{xKey:"x_m",yKey,xFactor:1e3,yFactor,xLabel:"主室轴向位置 / mm",yLabel})}</div>`).join("")}</div><details><summary>查看保存的主室单元 · ${nodes.length} 项</summary><div class="cd-scroll"><table><thead><tr>${thermalColumns.map(([,label])=>`<th>${label}</th>`).join("")}</tr></thead><tbody>${nodes.map(node=>`<tr>${thermalColumns.map(([key])=>`<td>${number(node?.[key])}</td>`).join("")}</tr>`).join("")}</tbody></table></div></details><button type="button" data-cd-export="thermal">主室冷却数据 CSV</button>` : empty("本记录没有保存可绘制的主室冷却单元；不从整机峰值生成轴向分布。");
  return `${section("主室冷却与热环境",`${fields(data.metrics)}${data.note ? `<div class="${["invalid","partial"].includes(data.status) ? "cd-warning" : "cd-note"}">${status(data.status)} ${escape(data.note)}</div>` : ""}${plots}${array(data.checks).length ? checks(data.checks) : ""}`,data.scope || "只显示已保存且属于主室范围的冷却单元。")}${section("材料与壁面条件",`${materials.status === "inconsistent" || materials.status === "fail" ? '<div class="cd-warning">材料记录存在冲突，热计算与结构交接需先复核。</div>' : ""}${materialEvidence(materials)}${fields(materials.metrics)}${noteList(materials.issues)}${array(materials.checks).length ? `<details><summary>逐单元材料与分区核对</summary>${checks(materials.checks)}</details>` : ""}`)}<div class="cd-note"><strong>热分析与结构寿命</strong><p>已有热流和壁温不等于完成强度、疲劳或重复使用寿命验证。热边界、材料数据与几何需一并交接后续分析。</p></div>`;
}
function thermalNodes(model) {
  const data=model.thermal || {},nodes=array(data.nodes),throat=data.throat_x_m ?? model.geometry?.dimensions?.throat_x_m;
  if(data.status === "invalid" || !finite(throat) || nodes.some((node,i)=>!finite(node?.x_m) || node.x_m<0 || node.x_m>=throat || i>0 && node.x_m<=nodes[i-1].x_m))return [];
  return nodes;
}
const safeUrl = value => {try {const url=new URL(value);return ["https:","http:"].includes(url.protocol) ? url.href : "";}catch{return "";}};
function methods(model) {
  return `${section("模型覆盖范围",checks(model.scope))}${section("后续分析交接",`<ul class="cd-checks">${array(model.handoff).map(item=>`<li><strong>${escape(item.topic || item.label)}</strong><p><b>当前提供：</b>${escape(item.available || item.note)}</p><p><b>后续需要：</b>${escape(item.required)}</p></li>`).join("")}</ul>${noteList(model.notes)}<p class="cd-caption">导出的坐标与设计条件是建模参考资料。未生成计算域、网格、完整边界条件或可直接提交的 CFD / 有限元模型。</p>`)}${section("方法与文献依据",`<ol class="cd-references">${array(model.references).map(item=>{const url=safeUrl(item.url);return `<li>${url ? `<a href="${escape(url)}" target="_blank" rel="noopener noreferrer">${escape(item.title)}</a>` : escape(item.title)}${item.year ? ` (${escape(item.year)})` : ""}<p>${escape(item.use || item.note)}</p></li>`;}).join("")}</ol>`)}<details class="cd-section"><summary>记录追溯与字段来源</summary><p>计算记录：<code>${escape(model.source?.record_id || "未记录")}</code></p><div class="cd-scroll"><table><thead><tr><th>参数</th><th>来源</th></tr></thead><tbody>${allFields(model).map(field=>`<tr><th>${escape(field.label || field.key)}</th><td><code>${escape(field.source)}</code></td></tr>`).join("")}</tbody></table></div></details>`;
}
const tabs=[["overview","入口与设计点"],["chemistry","平衡热化学与组成"],["geometry","主室几何"],["thermal","冷却与材料"],["methods","方法与交接"]];
export function renderChamberDesign(model) {
  if (!model?.available) return `<section class="chamber-design"><span class="cd-kicker">MAIN CHAMBER / DESIGN & ANALYSIS</span><h2>主燃烧室设计与分析</h2>${empty(model?.reason || "请选择已完成的设计计算记录。")}</section>`;
  const alert=model.status === "inconsistent" || array(model.checks).some(check=>check.status === "fail") || ["inconsistent","fail"].includes(model.materials?.status);
  return `<section class="chamber-design"><header class="cd-heading"><div><span class="cd-kicker">MAIN CHAMBER / DESIGN & ANALYSIS</span><h2>主燃烧室设计与分析</h2><p>${escape(model.source?.label || "所选计算记录")} · 富氧与富燃气体补燃 · 保存设计点</p></div><div class="cd-actions">${status(model.status)}<button type="button" data-cd-export="metrics">设计参数 CSV</button><button type="button" data-cd-export="json">交接数据 JSON</button></div></header>${hero(model)}${alert ? '<div class="cd-warning" role="alert"><strong>本记录存在需要复核的冲突</strong><p>请查看收支、几何或材料核对项；保留原始保存值供检查。</p></div>' : ""}<div class="cd-note"><strong>总体设计分析范围</strong><p>本页核对入口燃气、热化学参考、主室几何和冷却结果。平衡解、平均停留时间与声学估计不构成燃尽或燃烧稳定性验证。</p></div><div class="cd-tabs" role="tablist" aria-label="主燃烧室分析">${tabs.map(([id,label],i)=>`<button type="button" role="tab" id="cd-tab-${id}" data-cd-tab="${id}" aria-controls="cd-panel-${id}" aria-selected="${i===0}" tabindex="${i===0 ? 0 : -1}">${label}</button>`).join("")}</div>${tabs.map(([id],i)=>`<div role="tabpanel" id="cd-panel-${id}" data-cd-panel="${id}" aria-labelledby="cd-tab-${id}"${i ? " hidden" : ""}>${({overview,chemistry,geometry,thermal,methods})[id](model)}</div>`).join("")}<p class="cd-export-status" data-cd-export-status role="status" aria-live="polite"></p></section>`;
}

const csvCell = value => {let s=finite(value) ? String(value) : typeof value === "number" ? "" : text(value);if(typeof value !== "number" && /^[=+@\-\t\r]/.test(s))s="'"+s;return `"${s.replace(/"/g,'""')}"`;};
const csv = rows => "\ufeff"+rows.map(row=>row.map(csvCell).join(",")).join("\r\n")+"\r\n";
export function chamberDesignCsv(model,kind="metrics",{unit="mm"}={}) {
  if (!model?.available) return "";
  const record=model.source?.record_id || "",g=model.geometry || {};
  if(kind === "coordinates") {
    const data=inspectChamberGeometry(g);
    if(!data.valid || g.export_allowed !== true || g.consistency?.status !== "pass" || !["mm","m"].includes(unit))return "";
    const factor=unit === "mm" ? 1000 : 1;
    return csv([["record_id",`x_${unit}`,`r_${unit}`,"coordinate_origin","contour_sha256","scope"],...data.x.map((x,i)=>[record,x*factor,data.r[i]*factor,g.contour.coordinate_origin || "unspecified",g.contour_sha256 || "","saved_main_chamber_inner_wall_no_thickness"])]);
  }
  if(kind === "composition")return csv([["record_id","species","mass_fraction","mole_fraction","molar_mass_kg_kmol","scope","declared_basis","raw_fraction"],...compositionRows(model.composition).map(row=>[record,row.species,row.mass_fraction,row.mole_fraction,row.molar_mass_kg_kmol,"saved_main_chamber_composition",model.composition?.basis || "",row.raw_fraction])]);
  if(kind === "thermal"){const nodes=thermalNodes(model);return nodes.length ? csv([["record_id",...thermalColumns.map(([key])=>key),"scope"],...nodes.map(node=>[record,...thermalColumns.map(([key])=>node[key]),model.thermal?.scope || "saved_main_chamber_cells"])]) : "";}
  if(kind !== "metrics")return "";
  const groups=[["design_point",model.metrics],["thermochemistry",model.model?.metrics],["balance",model.balances?.metrics],["performance",model.performance?.metrics],["geometry",g.metrics],["thermal",model.thermal?.metrics],["materials",model.materials?.metrics],...array(model.inlets).map(inlet=>[`inlet_${inlet.id}`,inlet.metrics])];
  return csv([["record_id","section","key","label","value","unit","status","source","note"],...groups.flatMap(([group,items])=>array(items).map(field=>[record,group,field.key,field.label,field.value,field.unit,field.status,field.source,field.note]))]);
}
export function chamberDesignJson(model) {return model?.available ? JSON.stringify(model,(_key,value)=>typeof value === "number" && !finite(value) ? null : value,2) : "";}
function download(content,name,type) {const url=URL.createObjectURL(new Blob([content],{type})),link=document.createElement("a");link.href=url;link.download=name;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),0);}
export function mountChamberDesign(host,model,{onGeometry,onValidation,onDownload=download}={}) {
  if(!host)return ()=>{};
  host._chamberDesignCleanup?.();if(!model?.available)return ()=>{};
  let disposed=false,unit="mm";
  const get=selector=>host.querySelector(selector),tabNodes=[...host.querySelectorAll("[data-cd-tab]")],panels=[...host.querySelectorAll("[data-cd-panel]")];
  const activate=(id,focus=false)=>{const selected=tabNodes.find(tab=>tab.dataset.cdTab === id);if(!selected)return;host.dataset.chamberDesignTab=id;tabNodes.forEach(tab=>{tab.setAttribute("aria-selected",String(tab===selected));tab.tabIndex=tab===selected ? 0 : -1;});panels.forEach(panel=>{panel.hidden=panel.dataset.cdPanel!==id;});if(focus)selected.focus();};
  activate(host.dataset.chamberDesignTab || "overview");
  const click=event=>{
    if(disposed)return;const button=event.target?.closest?.("button");if(!button || !host.contains(button) || button.disabled)return;
    if(button.dataset.cdTab){activate(button.dataset.cdTab);return;}
    if(button.dataset.cdAction === "geometry"){onGeometry?.();return;}
    if(button.dataset.cdAction === "validation"){onValidation?.();return;}
    if(["2d","3d"].includes(button.dataset.cdView)){host.querySelectorAll("[data-cd-view]").forEach(node=>node.setAttribute("aria-pressed",String(node===button)));host.querySelectorAll("[data-cd-geometry-panel]").forEach(panel=>{panel.hidden=panel.dataset.cdGeometryPanel!==button.dataset.cdView;});return;}
    if(["iso","side","end"].includes(button.dataset.cdAngle)){const surface=get("[data-cd-surface]");if(surface)surface.innerHTML=buildChamberSurfaceSvg(model.geometry,{angle:button.dataset.cdAngle});host.querySelectorAll("[data-cd-angle]").forEach(node=>node.setAttribute("aria-pressed",String(node===button)));return;}
    const kind=button.dataset.cdExport;if(!["metrics","json","composition","thermal","coordinates"].includes(kind))return;
    const feedback=get("[data-cd-export-status]");
    try{const content=kind === "json" ? chamberDesignJson(model) : chamberDesignCsv(model,kind,{unit});if(!content)throw new Error("数据未满足导出条件，请核对记录与坐标。");const id=String(model.source?.record_id || "saved").replace(/[^a-zA-Z0-9_-]/g,"_");onDownload(content,`main-chamber-${id}-${kind}${kind === "coordinates" ? `-${unit}` : ""}.${kind === "json" ? "json" : "csv"}`,kind === "json" ? "application/json" : "text/csv;charset=utf-8");if(feedback)feedback.textContent="已导出当前计算记录的保存数据与分析口径。";}catch(error){if(feedback)feedback.textContent=error.message || String(error);}
  };
  const change=event=>{if(!event.target?.matches?.("[data-cd-unit]"))return;unit=event.target.value === "m" ? "m" : "mm";const panel=get('[data-cd-geometry-panel="2d"]');if(panel)panel.innerHTML=buildChamberSvg(model.geometry,{unit});};
  const keyboard=event=>{const index=tabNodes.indexOf(event.target?.closest?.("[data-cd-tab]"));if(index<0)return;const next=event.key === "ArrowRight" ? (index+1)%tabNodes.length : event.key === "ArrowLeft" ? (index+tabNodes.length-1)%tabNodes.length : event.key === "Home" ? 0 : event.key === "End" ? tabNodes.length-1 : null;if(next===null)return;event.preventDefault();activate(tabNodes[next].dataset.cdTab,true);};
  host.addEventListener("click",click);host.addEventListener("change",change);host.addEventListener("keydown",keyboard);
  const cleanup=()=>{disposed=true;host.removeEventListener("click",click);host.removeEventListener("change",change);host.removeEventListener("keydown",keyboard);if(host._chamberDesignCleanup===cleanup)delete host._chamberDesignCleanup;};host._chamberDesignCleanup=cleanup;return cleanup;
}
