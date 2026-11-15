// Every view and export consumes the same frozen, ordered inner-wall coordinates.
import {buildNozzleCoordinateCsv} from "./nozzle-export.js";

const finite=value=>typeof value==="number" && Number.isFinite(value);
const escape=value=>String(value??"").replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const number=value=>finite(value)?value.toLocaleString("zh-CN",{maximumSignificantDigits:7}):"未记录";
const coordinateLabel=origin=>origin==="injector_face"?"喷注面 x = 0":origin==="throat"?"喉部 x = 0":"坐标原点未记录";
const DIMENSIONS=[
  ["chamber_diameter_m","燃烧室内径 Dc","m"],["throat_diameter_m","喉部内径 Dt","m"],["exit_diameter_m","出口内径 De","m"],
  ["total_length_m","内轮廓总长 L","m"],["chamber_length_m","直筒段长度","m"],["convergent_length_m","收缩段长度","m"],
  ["nozzle_length_m","膨胀段长度","m"],["throat_x_m","喉部位置 xt","m"],["expansion_ratio","出口面积比 Ae/At","—"],
  ["bell_fraction","钟形长度比","—"],["initial_angle_deg","抛物线起始角","°"],["exit_angle_deg","出口角","°"],
  ["throat_upstream_radius_m","喉部上游曲率半径","m"],["throat_downstream_radius_m","喉部下游曲率半径","m"],
];

export function inspectNozzleGeometry(geometry){
  if(!geometry?.available)return {valid:false,reason:geometry?.reason||"本条记录未保存喷管内轮廓。请重新计算设计点以生成新的几何记录。"};
  const x=geometry.contour?.x_m,r=geometry.contour?.r_m;
  if(!Array.isArray(x)||!Array.isArray(r)||x.length!==r.length||x.length<3)return {valid:false,reason:"保存的轮廓坐标不完整。"};
  if(x.some((value,i)=>!finite(value)||!finite(r[i])||r[i]<=0||(i>0&&value<=x[i-1])))return {valid:false,reason:"保存的轮廓含无效坐标或轴向次序异常。"};
  return {valid:true,x,r,minX:x[0],maxX:x.at(-1),maxR:Math.max(...r),length:x.at(-1)-x[0]};
}

function drawingLayout(data){
  const width=1100,height=620,left=140,right=145,top=120,bottom=125;
  const scale=Math.min((width-left-right)/data.length,(height-top-bottom)/(2*data.maxR));
  const start=left+(width-left-right-data.length*scale)/2,center=(top+height-bottom)/2;
  return {width,height,scale,start,center,X:x=>start+(x-data.minX)*scale,Y:r=>center-r*scale};
}

// Presentation attributes work under the platform's style-src 'self' policy
// and keep exported SVGs self-contained. Open dimension paths must never fill.
const DIMENSION_PATH='fill="none" stroke="#76879b" stroke-width="0.9"';
const DIMENSION_TEXT='fill="#41566c" font-size="11" paint-order="stroke" stroke="#f8fafc" stroke-width="4" stroke-linejoin="round"';

function horizontalDimension(x1,x2,y,label){
  return `<g class="ng-svg-dimension"><path ${DIMENSION_PATH} d="M${x1},${y-7}v14 M${x2},${y-7}v14 M${x1},${y}H${x2}"/><path ${DIMENSION_PATH} d="M${x1+5},${y-3}l-5,3 5,3 M${x2-5},${y-3}l5,3 -5,3"/><text ${DIMENSION_TEXT} x="${(x1+x2)/2}" y="${y-9}" text-anchor="middle">${escape(label)}</text></g>`;
}

function verticalDimension(x,y1,y2,label,side=1){
  return `<g class="ng-svg-dimension"><path ${DIMENSION_PATH} d="M${x-6},${y1}h12 M${x-6},${y2}h12 M${x},${y1}V${y2}"/><path ${DIMENSION_PATH} d="M${x-3},${y1+5}l3,-5 3,5 M${x-3},${y2-5}l3,5 3,-5"/><text ${DIMENSION_TEXT} x="${x+side*10}" y="${(y1+y2)/2+4}" text-anchor="${side>0?"start":"end"}">${escape(label)}</text></g>`;
}

export function buildNozzleSvg(geometry,options={}){
  const data=inspectNozzleGeometry(geometry);if(!data.valid)return "";
  const d=geometry.dimensions||{},unit=options.unit==="mm"?"mm":"m",factor=unit==="mm"?1000:1;
  const {width,height,scale,center,X,Y}=drawingLayout(data),fmt=v=>number(v*factor);
  const upper=data.x.map((x,i)=>`${i?"L":"M"}${X(x)},${Y(data.r[i])}`).join(" ");
  const lower=data.x.map((x,i)=>`${i?"L":"M"}${X(x)},${Y(-data.r[i])}`).join(" ");
  const fill=upper+data.x.toReversed().map((x,i)=>` L${X(x)},${Y(-data.r[data.r.length-1-i])}`).join("")+" Z";
  const throat=finite(d.throat_x_m)&&d.throat_x_m>=data.minX&&d.throat_x_m<=data.maxX;
  let dimensions="",datums="",cooling="";
  if(options.dimensions!==false){
    if(finite(d.total_length_m))dimensions+=horizontalDimension(X(data.minX),X(data.maxX),58,`总长 ${fmt(d.total_length_m)} ${unit}`);
    if(finite(d.chamber_diameter_m))dimensions+=verticalDimension(X(data.minX)-25,Y(d.chamber_diameter_m/2),Y(-d.chamber_diameter_m/2),`Dc ${fmt(d.chamber_diameter_m)} ${unit}`,-1);
    if(finite(d.exit_diameter_m))dimensions+=verticalDimension(X(data.maxX)+25,Y(d.exit_diameter_m/2),Y(-d.exit_diameter_m/2),`De ${fmt(d.exit_diameter_m)} ${unit}`);
    if(throat){
      datums+=`<path d="M${X(d.throat_x_m)},115V505" fill="none" stroke="#b67d35" stroke-dasharray="5 4"/><text x="${X(d.throat_x_m)}" y="109" text-anchor="middle" fill="#886024">喉部 x = ${fmt(d.throat_x_m)} ${unit}</text>`;
      if(finite(d.throat_diameter_m))dimensions+=`<g class="ng-svg-dimension"><path ${DIMENSION_PATH} d="M${X(d.throat_x_m)},${Y(-d.throat_diameter_m/2)}l25,25h72"/><text ${DIMENSION_TEXT} x="${X(d.throat_x_m)+31}" y="${Y(-d.throat_diameter_m/2)+20}">Dt ${fmt(d.throat_diameter_m)} ${unit}</text></g>`;
      if(finite(d.nozzle_length_m))dimensions+=horizontalDimension(X(d.throat_x_m),X(data.maxX),557,`膨胀段 ${fmt(d.nozzle_length_m)} ${unit}`);
      if(finite(d.convergent_length_m)&&d.throat_x_m-d.convergent_length_m>=data.minX)dimensions+=horizontalDimension(X(d.throat_x_m-d.convergent_length_m),X(d.throat_x_m),527,`收缩段 ${fmt(d.convergent_length_m)} ${unit}`);
      if(finite(d.chamber_length_m))dimensions+=horizontalDimension(X(data.minX),X(data.minX+d.chamber_length_m),557,`直筒段 ${fmt(d.chamber_length_m)} ${unit}`);
    }
    const c=geometry.cooling;
    if(finite(c?.start_x_m)&&finite(c?.end_x_m)&&c.start_x_m>=data.minX&&c.end_x_m<=data.maxX){
      cooling=`<path d="M${X(c.start_x_m)},79H${X(c.end_x_m)}" fill="none" stroke="#328778" stroke-width="2.5"/><text x="${(X(c.start_x_m)+X(c.end_x_m))/2}" y="94" text-anchor="middle" fill="#328778" font-size="10">再生冷却覆盖</text>`;
    }
  }
  const grid=options.grid===false?"":`<path d="${Array.from({length:26},(_,i)=>`M${30+i*40},30V590`).join(" ")} ${Array.from({length:15},(_,i)=>`M30,${30+i*40}H1070`).join(" ")}" fill="none" stroke="#e5eaf0" stroke-width=".55"/>`;
  const nodes=options.points?data.x.map((x,i)=>`<circle cx="${X(x)}" cy="${Y(data.r[i])}" r="2.4" fill="#fff" stroke="#376f9a" data-nozzle-node="${i}"/>`).join(""):"";
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" role="img" tabindex="0" aria-label="推力室燃气内型面等比例剖面" data-geometry-scale="${scale}" font-family="system-ui,Microsoft YaHei,sans-serif" font-size="11"><title>推力室燃气内型面</title><desc>轴向与径向使用相同比例。${escape(coordinateLabel(geometry.contour.coordinate_origin))}。${escape(geometry.method?.label||"构造方法未记录")}。</desc><rect width="1100" height="620" fill="#f8fafc"/>${grid}<path d="${fill}" fill="#e8eef5" fill-opacity=".65"/><path d="M${X(data.minX)-20},${center}H${X(data.maxX)+20}" fill="none" stroke="#9aa8b6" stroke-dasharray="12 4 2 4"/><text x="${X(data.minX)-25}" y="${center-10}" fill="#78899b" text-anchor="end">X</text><path d="${upper} ${lower}" fill="none" stroke="#356e9d" stroke-width="2" stroke-linejoin="round"/>${datums}${cooling}${dimensions}${nodes}<text x="550" y="602" text-anchor="middle" fill="#778799">轴向 x / ${unit} · ${escape(coordinateLabel(geometry.contour.coordinate_origin))} · 轴向与径向等比例</text></svg>`;
}

// Retain the original x/r format for callers already using this public helper.
export function buildNozzleCsv(geometry){
  const data=inspectNozzleGeometry(geometry);if(!data.valid||geometry.export_allowed===false||geometry.consistency?.status==="fail")return "";
  const origin=["injector_face","throat"].includes(geometry.contour.coordinate_origin)?geometry.contour.coordinate_origin:"unspecified";
  return "x_m,r_m,coordinate_origin\r\n"+data.x.map((x,i)=>`${x},${data.r[i]},${origin}`).join("\r\n")+"\r\n";
}

export function buildNozzleSurface(geometry,segments=36){
  const data=inspectNozzleGeometry(geometry);if(!data.valid)return [];
  const count=Number.isInteger(segments)&&segments>=8&&segments<=96?segments:36;
  return data.x.map((x,i)=>Array.from({length:count},(_,j)=>{const theta=j/count*Math.PI*2;return [x-(data.minX+data.maxX)/2,data.r[i]*Math.cos(theta),data.r[i]*Math.sin(theta)];}));
}

function dimensionRows(geometry,unit){
  const factor=unit==="mm"?1000:1,d=geometry.dimensions||{};
  return DIMENSIONS.map(([key,label,u])=>`<div><dt>${label}</dt><dd>${number(u==="m"&&finite(d[key])?d[key]*factor:d[key])}${finite(d[key])?` <small>${u==="m"?unit:u}</small>`:""}</dd></div>`).join("");
}
function segmentAt(geometry,x){const d=geometry.dimensions||{};return !finite(d.chamber_length_m)||!finite(d.throat_x_m)?"未记录":x<=d.chamber_length_m?"直筒段":x<=d.throat_x_m?"收缩段":"膨胀段";}
function coordinateRows(geometry,unit){const f=unit==="mm"?1000:1;return geometry.contour.x_m.map((x,i)=>`<tr data-nozzle-row="${i}"><td>${i+1}</td><td>${escape(segmentAt(geometry,x))}</td><td>${number(x*f)}</td><td>${number(geometry.contour.r_m[i]*f)}</td><td>0</td></tr>`).join("");}
function consistencyMarkup(geometry){
  const status=["pass","partial","fail"].includes(geometry.consistency?.status)?geometry.consistency.status:"partial";
  const checks=Array.isArray(geometry.consistency?.checks)?geometry.consistency.checks:[];
  const label=status==="pass"?"与本次循环结果一致":status==="fail"?"数据存在冲突":status==="partial"?"部分来源待核对":"已保存型面 · 一致性未记录";
  return `<div class="ng-consistency ng-${status}"><span class="ng-status-dot"></span><strong>${label}</strong></div>${checks.length?`<details class="ng-check-details"${status==="fail"?" open":""}><summary>${checks.length} 项来源核对</summary><ul class="ng-checks">${checks.map(c=>`<li><span>${escape(c.label||c.name||c.id)}</span><b>${c.status==="pass"?"一致":c.status==="fail"?"冲突":"未记录"}</b></li>`).join("")}</ul></details>`:""}`;
}

export function renderNozzleGeometry(geometry){
  const data=inspectNozzleGeometry(geometry);
  if(!data.valid)return `<section class="nozzle-geometry ng-unavailable"><span class="ng-eyebrow">GEOMETRY</span><h3>推力室内型面</h3><p>${escape(data.reason)}</p><p class="ng-muted">历史结果保留原样；缺失或冲突的尺寸与坐标不会用当前参数补画。</p></section>`;
  const method=geometry.method||{},source=geometry.source||{},blocked=geometry.export_allowed===false||geometry.consistency?.status==="fail";
  const notes=Array.isArray(method.notes)?method.notes:method.notes?[method.notes]:[];
  return `<section class="nozzle-geometry" data-nozzle-geometry>
    <header class="ng-heading"><div><span class="ng-eyebrow">GEOMETRY / INNER WALL</span><h3>内型面工作区</h3><p>${escape(method.label||"构造方法未记录")}</p></div><div class="ng-record"><span>保存的设计记录</span><code>${escape(String(source.record_id||"未记录编号").slice(0,12))}</code></div></header>
    <div class="ng-source${source.kind==="reconstructed_from_record"?" ng-source-reconstructed":""}"><span>几何来源</span><strong>${escape(source.label||"来源未记录")}</strong><span>${data.x.length} 个原始轮廓点</span>${source.kind==="reconstructed_from_record"?"<em>按本记录的尺寸与输入构造，不补造冷却结果。</em>":""}</div>
    <div class="ng-toolbar"><div class="ng-views" role="group" aria-label="几何视图"><button type="button" data-nozzle-view="2d" aria-pressed="true">二维剖面</button><button type="button" data-nozzle-view="3d" aria-pressed="false">三维旋转面</button></div><div class="ng-display"><label><input type="checkbox" data-nozzle-grid checked>网格</label><label><input type="checkbox" data-nozzle-dimensions checked>尺寸</label><label><input type="checkbox" data-nozzle-points>节点 / 线框</label><label>显示单位 <select data-nozzle-unit aria-label="几何显示单位"><option value="mm">mm</option><option value="m">m</option></select></label></div></div>
    <div class="ng-workspace"><div class="ng-model">
      <div class="ng-viewport ng-two" data-nozzle-panel="2d">${buildNozzleSvg(geometry,{unit:"mm"})}<div class="ng-view-label">正视 · 轴对称剖面</div></div>
      <div class="ng-viewport ng-three" data-nozzle-panel="3d" hidden><canvas data-nozzle-canvas tabindex="0" aria-label="喷管燃气内边界三维旋转面，拖动旋转，Shift 拖动平移，滚轮缩放">当前浏览器不支持三维显示。</canvas><div class="ng-view-label">正交投影 · 燃气侧内表面</div><div class="ng-presets"><button type="button" data-nozzle-preset="iso">轴测视图</button><button type="button" data-nozzle-preset="side">正视</button><button type="button" data-nozzle-preset="end">出口视图</button></div><p class="ng-canvas-error" data-nozzle-canvas-error hidden>三维画布不可用，请查看二维剖面或导出坐标。</p></div>
      <div class="ng-view-controls"><span data-nozzle-hint>拖动平移 · 滚轮缩放 · 移动鼠标检查节点</span><button type="button" data-nozzle-zoom="in" aria-label="放大视图">＋</button><button type="button" data-nozzle-zoom="out" aria-label="缩小视图">−</button><button type="button" data-nozzle-reset>适应窗口</button></div>
      <div class="ng-probe" aria-live="polite"><span class="ng-probe-tag">节点检查</span><span data-nozzle-probe>上半型面为原始母线；下半型面为镜像显示。</span></div>
      <div class="ng-scope">轴对称燃气内轮廓及其旋转面；未表示壁厚、冷却槽道或承力外壳。</div>
    </div><aside class="ng-inspector"><h4>几何与循环结果</h4>${consistencyMarkup(geometry)}<p class="ng-inspector-note">尺寸取自当前选中的计算记录。修改设计输入后，需重新计算才能更新本图。</p><h4>主尺寸 <span data-nozzle-dimension-unit>mm</span></h4><dl class="ng-dimensions" data-nozzle-dimensions-list>${dimensionRows(geometry,"mm")}</dl></aside></div>
    <div class="ng-export-panel"><div><h4>坐标交接</h4><p>完整内壁母线 · 原始点序 · 不重采样</p></div><div class="ng-export-options"><label>单位<select data-nozzle-export-unit><option value="mm">mm</option><option value="m">m</option></select></label><label>原点<select data-nozzle-origin><option value="injector_face">喷注面</option><option value="throat"${finite(geometry.dimensions?.throat_x_m)?"":" disabled"}>喉部</option></select></label><label>文件格式<select data-nozzle-format><option value="xyz">CAD 坐标 X / Y / Z</option><option value="annotated">坐标与分段信息</option></select></label></div><div class="ng-exports"><button type="button" class="ng-primary" data-nozzle-export="csv"${blocked?" disabled":""}>导出坐标 CSV</button><button type="button" data-nozzle-export="svg">导出剖面 SVG</button></div><p class="ng-export-help">X 沿燃气流向，Y 为内壁半径，Z = 0；在建模软件中将母线绕 X 轴旋转。CSV 使用完整数值，屏幕数值仅作格式化显示。<span data-nozzle-export-status role="status">${blocked?"存在数据冲突，坐标导出已停用。":""}</span></p></div>
    <details class="ng-coordinate-table"><summary>查看原始坐标 <span>${data.x.length} 点 · <b data-nozzle-table-unit>mm</b> · 喷注面原点</span></summary><div><table><thead><tr><th>序号</th><th>区段</th><th>X</th><th>R / Y</th><th>Z</th></tr></thead><tbody data-nozzle-coordinates>${coordinateRows(geometry,"mm")}</tbody></table></div></details>
    <details class="ng-method"><summary>构造方法与数据来源</summary><dl><dt>构造方法</dt><dd>${escape(method.label||"未记录")}</dd><dt>角度来源</dt><dd>${escape(method.angle_source||"未记录")}</dd><dt>坐标原点</dt><dd>${escape(coordinateLabel(geometry.contour.coordinate_origin))}</dd><dt>型面指纹</dt><dd><code>${escape(geometry.contour_sha256||"未记录")}</code></dd></dl><p>喉部尺寸由本次循环计算结果确定；收缩比、特征长度、钟形长度比与端点角度属于本次设计输入或构造假设，型面并非独立优化求解结果。</p>${notes.length?`<ul>${notes.map(note=>`<li>${escape(note)}</li>`).join("")}</ul>`:""}</details>
  </section>`;
}

function saveFile(text,type,name,doc){const win=doc.defaultView||window,url=win.URL.createObjectURL(new win.Blob([text],{type})),a=doc.createElement("a");a.href=url;a.download=name;doc.body.append(a);a.click();a.remove();win.setTimeout(()=>win.URL.revokeObjectURL(url),1000);}

export function mountNozzleGeometry(container,geometry){
  const root=container?.matches?.("[data-nozzle-geometry]")?container:container?.querySelector?.("[data-nozzle-geometry]");
  if(!root||!inspectNozzleGeometry(geometry).valid)return ()=>{};
  root._nozzleCleanup?.();
  const win=root.ownerDocument.defaultView||window,data=inspectNozzleGeometry(geometry),layout=drawingLayout(data),mesh=buildNozzleSurface(geometry,48);
  const canvas=root.querySelector("[data-nozzle-canvas]"),ctx=canvas?.getContext?.("2d"),panel2=root.querySelector('[data-nozzle-panel="2d"]');
  const state={mode:"2d",unit:"mm",grid:true,dimensions:true,points:false,yaw:-.55,pitch:-.18,zoom:1,panX:0,panY:0,viewBox:[0,0,1100,620]};
  let drag=null,frame=null,disposed=false;const listeners=[];
  const listen=(element,type,fn,options)=>{element?.addEventListener(type,fn,options);listeners.push(()=>element?.removeEventListener(type,fn,options));};
  const svg=()=>panel2.querySelector("svg");
  const applyBox=()=>svg()?.setAttribute("viewBox",state.viewBox.join(" "));
  const redraw2=()=>{const old=svg();if(old)old.outerHTML=buildNozzleSvg(geometry,state);applyBox();};
  const draw=()=>{
    frame=null;if(disposed||!ctx||state.mode!=="3d")return;
    const bounds=canvas.getBoundingClientRect(),width=Math.max(240,bounds.width),height=Math.max(260,bounds.height),ratio=Math.min(win.devicePixelRatio||1,2);
    canvas.width=Math.round(width*ratio);canvas.height=Math.round(height*ratio);ctx.setTransform(ratio,0,0,ratio,0,0);ctx.clearRect(0,0,width,height);ctx.fillStyle="#f4f7fa";ctx.fillRect(0,0,width,height);
    if(state.grid){ctx.strokeStyle="#e5ebf1";ctx.lineWidth=.6;ctx.beginPath();for(let x=20;x<width;x+=40){ctx.moveTo(x,0);ctx.lineTo(x,height);}for(let y=20;y<height;y+=40){ctx.moveTo(0,y);ctx.lineTo(width,y);}ctx.stroke();}
    const cy=Math.cos(state.yaw),sy=Math.sin(state.yaw),cp=Math.cos(state.pitch),sp=Math.sin(state.pitch);
    const rotate=p=>{const x=p[0]*cy+p[2]*sy,z=-p[0]*sy+p[2]*cy;return [x,p[1]*cp-z*sp,p[1]*sp+z*cp];};
    const rotated=mesh.map(ring=>ring.map(rotate)),all=rotated.flat(),xs=all.map(p=>p[0]),ys=all.map(p=>p[1]);
    const spanX=Math.max(...xs)-Math.min(...xs),spanY=Math.max(...ys)-Math.min(...ys);
    const scale=Math.min((width-120)/Math.max(spanX,1e-9),(height-100)/Math.max(spanY,1e-9))*state.zoom;
    const midX=(Math.max(...xs)+Math.min(...xs))/2,midY=(Math.max(...ys)+Math.min(...ys))/2;
    const project=p=>[width/2+(p[0]-midX)*scale+state.panX,height/2-(p[1]-midY)*scale+state.panY,p[2]];
    const rings=rotated.map(r=>r.map(project)),faces=[];
    for(let i=0;i<rings.length-1;i++)for(let j=0;j<rings[i].length;j++){const next=(j+1)%rings[i].length,points=[rings[i][j],rings[i+1][j],rings[i+1][next],rings[i][next]],light=.5+.5*Math.cos((j+.5)/rings[i].length*Math.PI*2-state.yaw);faces.push({points,depth:points.reduce((sum,p)=>sum+p[2],0)/4,light});}
    faces.sort((a,b)=>a.depth-b.depth);
    for(const face of faces){ctx.beginPath();face.points.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));ctx.closePath();ctx.fillStyle=`hsl(210,17%,${47+face.light*35}%)`;ctx.fill();ctx.strokeStyle=state.points?"rgba(39,65,89,.3)":ctx.fillStyle;ctx.lineWidth=state.points?.55:.6;ctx.stroke();}
    const throatIndex=data.x.findIndex(x=>Math.abs(x-(geometry.dimensions?.throat_x_m??NaN))<1e-9);
    for(const index of [0,throatIndex,rings.length-1]){if(index<0)continue;ctx.beginPath();rings[index].forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));ctx.closePath();ctx.strokeStyle=index===throatIndex?"#bf914f":"#415d77";ctx.lineWidth=1.3;ctx.stroke();}
    for(const [v,label,color] of [[[1,0,0],"X","#b86560"],[[0,1,0],"Y","#59947b"],[[0,0,1],"Z","#5484b1"]]){const p=rotate(v);ctx.beginPath();ctx.moveTo(44,height-44);ctx.lineTo(44+p[0]*27,height-44-p[1]*27);ctx.strokeStyle=color;ctx.lineWidth=2;ctx.stroke();ctx.fillStyle=color;ctx.font="11px system-ui";ctx.fillText(label,42+p[0]*36,height-41-p[1]*36);}
  };
  const schedule=()=>{if(frame===null&&!disposed)frame=win.requestAnimationFrame(draw);};
  const fit=()=>{state.viewBox=[0,0,1100,620];state.zoom=1;state.panX=state.panY=0;applyBox();schedule();};
  const zoomBy=factor=>{if(state.mode==="3d"){state.zoom=Math.max(.3,Math.min(5,state.zoom*factor));schedule();}else{const [x,y,w,h]=state.viewBox,newW=Math.max(110,Math.min(3300,w/factor)),newH=newW*620/1100;state.viewBox=[x+(w-newW)/2,y+(h-newH)/2,newW,newH];applyBox();}};
  const showPoint=index=>{
    if(index<0||index>=data.x.length)return;const f=state.unit==="mm"?1000:1;
    root.querySelector("[data-nozzle-probe]").textContent=`P${String(index+1).padStart(3,"0")} · ${segmentAt(geometry,data.x[index])} · X ${number(data.x[index]*f)} ${state.unit} · R ${number(data.r[index]*f)} ${state.unit}`;
    const drawing=svg();if(!drawing)return;
    let marker=drawing.querySelector("[data-nozzle-probe-marker]");
    if(!marker){marker=root.ownerDocument.createElementNS("http://www.w3.org/2000/svg","circle");marker.setAttribute("data-nozzle-probe-marker","");marker.setAttribute("fill","#b8813d");marker.setAttribute("stroke","#fff");marker.setAttribute("stroke-width","1.5");drawing.append(marker);}
    marker.setAttribute("cx",layout.X(data.x[index]));marker.setAttribute("cy",layout.Y(data.r[index]));marker.setAttribute("r",4*state.viewBox[2]/1100);
  };
  listen(root,"click",event=>{
    const row=event.target?.closest?.("[data-nozzle-row]");if(row)showPoint(Number(row.dataset.nozzleRow));
    const button=event.target?.closest?.("button");if(!button||!root.contains(button))return;
    if(button.dataset.nozzleView){state.mode=button.dataset.nozzleView;root.querySelectorAll("[data-nozzle-view]").forEach(tab=>tab.setAttribute("aria-pressed",String(tab.dataset.nozzleView===state.mode)));root.querySelectorAll("[data-nozzle-panel]").forEach(panel=>panel.hidden=panel.dataset.nozzlePanel!==state.mode);root.querySelector("[data-nozzle-dimensions]").disabled=state.mode==="3d";root.querySelector("[data-nozzle-hint]").textContent=state.mode==="2d"?"拖动平移 · 滚轮缩放 · 移动鼠标检查节点":"拖动旋转 · Shift 拖动平移 · 滚轮缩放";schedule();}
    if(button.dataset.nozzleExport==="csv"){
      const options={unit:root.querySelector("[data-nozzle-export-unit]").value,origin:root.querySelector("[data-nozzle-origin]").value,format:root.querySelector("[data-nozzle-format]").value},status=root.querySelector("[data-nozzle-export-status]");
      try{const csv=buildNozzleCoordinateCsv(geometry,options);if(!csv)throw new Error("轮廓未通过导出检查。");const id=String(geometry.source?.record_id||geometry.contour_sha256?.slice(0,12)||"saved").replace(/[^a-zA-Z0-9_-]/g,"_");saveFile(csv,"text/csv;charset=utf-8",`chamber-nozzle-${id}-${options.unit}-${options.origin}-${options.format}.csv`,root.ownerDocument);status.textContent=`已导出 ${data.x.length} 个点 · ${options.unit} · ${options.origin==="throat"?"喉部":"喷注面"}原点`;}
      catch(error){status.textContent=error.message;}
    }
    if(button.dataset.nozzleExport==="svg")saveFile(buildNozzleSvg(geometry,state),"image/svg+xml;charset=utf-8","chamber-nozzle-profile.svg",root.ownerDocument);
    if(button.dataset.nozzleZoom)zoomBy(button.dataset.nozzleZoom==="in"?1.2:1/1.2);
    if(button.hasAttribute("data-nozzle-reset"))fit();
    if(button.dataset.nozzlePreset){const p=button.dataset.nozzlePreset;state.yaw=p==="side"?0:p==="end"?-Math.PI/2:-.55;state.pitch=p==="iso"?-.18:0;fit();}
  });
  listen(root,"change",event=>{
    const el=event.target;
    if(el.matches("[data-nozzle-unit]")){state.unit=el.value;root.querySelector("[data-nozzle-dimensions-list]").innerHTML=dimensionRows(geometry,state.unit);root.querySelector("[data-nozzle-dimension-unit]").textContent=state.unit;root.querySelector("[data-nozzle-table-unit]").textContent=state.unit;root.querySelector("[data-nozzle-coordinates]").innerHTML=coordinateRows(geometry,state.unit);root.querySelector("[data-nozzle-probe]").textContent="移动鼠标检查原始坐标节点。";}
    if(el.matches("[data-nozzle-grid]"))state.grid=el.checked;if(el.matches("[data-nozzle-dimensions]"))state.dimensions=el.checked;if(el.matches("[data-nozzle-points]"))state.points=el.checked;
    if(el.matches("[data-nozzle-unit],[data-nozzle-grid],[data-nozzle-dimensions],[data-nozzle-points]")){redraw2();schedule();}
  });
  listen(panel2,"pointerdown",event=>{if(event.button!==0)return;drag={mode:"2d",x:event.clientX,y:event.clientY,id:event.pointerId,box:[...state.viewBox]};panel2.setPointerCapture?.(event.pointerId);svg()?.focus();});
  listen(panel2,"pointermove",event=>{
    const current=svg();if(!current)return;
    if(drag?.mode==="2d"&&drag.id===event.pointerId){const bounds=current.getBoundingClientRect(),px=Math.min(bounds.width/drag.box[2],bounds.height/drag.box[3]);state.viewBox=[drag.box[0]-(event.clientX-drag.x)/px,drag.box[1]-(event.clientY-drag.y)/px,drag.box[2],drag.box[3]];applyBox();return;}
    const matrix=current.getScreenCTM?.();if(!matrix)return;const pt=current.createSVGPoint();pt.x=event.clientX;pt.y=event.clientY;const local=pt.matrixTransform(matrix.inverse());let nearest=0,best=Infinity;
    data.x.forEach((x,i)=>{const distance=Math.hypot(local.x-layout.X(x),local.y-layout.Y(data.r[i]));if(distance<best){nearest=i;best=distance;}});if(best<60*state.viewBox[2]/1100)showPoint(nearest);
  });
  listen(canvas,"pointerdown",event=>{if(event.button!==0)return;drag={mode:"3d",x:event.clientX,y:event.clientY,id:event.pointerId,pan:event.shiftKey};canvas.setPointerCapture?.(event.pointerId);canvas.focus();});
  listen(canvas,"pointermove",event=>{if(drag?.mode!=="3d"||drag.id!==event.pointerId)return;const dx=event.clientX-drag.x,dy=event.clientY-drag.y;if(drag.pan){state.panX+=dx;state.panY+=dy;}else{state.yaw+=dx*.008;state.pitch+=dy*.008;}drag.x=event.clientX;drag.y=event.clientY;schedule();});
  for(const element of [panel2,canvas]){
    for(const type of ["pointerup","pointercancel","lostpointercapture"])listen(element,type,()=>{drag=null;});
    listen(element,"wheel",event=>{event.preventDefault();zoomBy(event.deltaY<0?1.1:1/1.1);},{passive:false});
    listen(element,"keydown",event=>{if(!["ArrowLeft","ArrowRight","ArrowUp","ArrowDown","+","=","-","Home"].includes(event.key))return;event.preventDefault();if(["+","="].includes(event.key))zoomBy(1.2);else if(event.key==="-")zoomBy(1/1.2);else if(event.key==="Home")fit();else if(state.mode==="3d"){state.yaw+=(event.key==="ArrowLeft"?-.1:event.key==="ArrowRight"?.1:0);state.pitch+=(event.key==="ArrowUp"?-.1:event.key==="ArrowDown"?.1:0);schedule();}else{state.viewBox[0]+=(event.key==="ArrowLeft"?-1:event.key==="ArrowRight"?1:0)*state.viewBox[2]*.05;state.viewBox[1]+=(event.key==="ArrowUp"?-1:event.key==="ArrowDown"?1:0)*state.viewBox[3]*.05;applyBox();}});
  }
  if(!ctx){const error=root.querySelector("[data-nozzle-canvas-error]");if(error)error.hidden=false;}
  let observer=null;if(win.ResizeObserver){observer=new win.ResizeObserver(schedule);observer.observe(canvas);}else listen(win,"resize",schedule);
  const cleanup=()=>{disposed=true;listeners.forEach(remove=>remove());observer?.disconnect();if(frame!==null)win.cancelAnimationFrame(frame);if(root._nozzleCleanup===cleanup)delete root._nozzleCleanup;};root._nozzleCleanup=cleanup;return cleanup;
}
