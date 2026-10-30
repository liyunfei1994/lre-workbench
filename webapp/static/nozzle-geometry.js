// Immutable geometry snapshots. The drawing never reconstructs a missing contour.
const finite = value => typeof value === "number" && Number.isFinite(value);
const escape = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const number = value => finite(value) ? value.toLocaleString("zh-CN", {maximumSignificantDigits:6}) : "未记录";
const coordinateLabel = origin => origin === "injector_face" ? "喷注面 x = 0" : origin === "throat" ? "喉部 x = 0" : "坐标原点未记录";
const DIMENSIONS = [
  ["chamber_diameter_m","燃烧室内径","m"], ["throat_diameter_m","喉部内径","m"], ["exit_diameter_m","出口内径","m"],
  ["chamber_length_m","直筒段长度","m"], ["convergent_length_m","收缩段长度","m"], ["nozzle_length_m","膨胀段长度","m"],
  ["total_length_m","内轮廓总长","m"], ["throat_x_m","喉部轴向位置","m"], ["expansion_ratio","喷管面积比","—"],
  ["bell_fraction","钟形长度比","—"], ["initial_angle_deg","抛物线起始角","°"], ["exit_angle_deg","出口角","°"],
  ["throat_upstream_radius_m","喉部上游曲率半径","m"], ["throat_downstream_radius_m","喉部下游曲率半径","m"],
];

export function inspectNozzleGeometry(geometry) {
  if (!geometry?.available) return {valid:false, reason:geometry?.reason || "本条记录未保存喷管内轮廓，无法绘图。请使用当前模型重新计算设计点以生成新的几何记录。"};
  const x=geometry.contour?.x_m, r=geometry.contour?.r_m;
  if (!Array.isArray(x) || !Array.isArray(r) || x.length !== r.length || x.length < 3)
    return {valid:false, reason:"保存的轮廓坐标不完整，无法绘图。"};
  if (x.some((value,i)=>!finite(value) || !finite(r[i]) || r[i]<=0 || (i>0 && value<x[i-1])))
    return {valid:false, reason:"保存的轮廓含无效坐标或轴向次序异常，无法绘图。"};
  if (x.at(-1)<=x[0]) return {valid:false, reason:"保存的轮廓没有有效轴向长度，无法绘图。"};
  return {valid:true,x,r,minX:x[0],maxX:x.at(-1),maxR:Math.max(...r),length:x.at(-1)-x[0]};
}

function horizontalDimension(x1,x2,y,label) {
  return `<path d="M${x1},${y-5}v10 M${x2},${y-5}v10 M${x1},${y}H${x2}" fill="none" stroke="#6b7c91" stroke-width="1"/><path d="M${x1+5},${y-3}l-5,3 5,3 M${x2-5},${y-3}l5,3 -5,3" fill="none" stroke="#6b7c91"/><text x="${(x1+x2)/2}" y="${y-8}" text-anchor="middle" fill="#42556d" font-size="12">${escape(label)}</text>`;
}

function verticalDimension(x,y1,y2,label,side=1) {
  return `<path d="M${x-5},${y1}h10 M${x-5},${y2}h10 M${x},${y1}V${y2}" fill="none" stroke="#6b7c91" stroke-width="1"/><path d="M${x-3},${y1+5}l3,-5 3,5 M${x-3},${y2-5}l3,5 3,-5" fill="none" stroke="#6b7c91"/><text x="${x+side*10}" y="${(y1+y2)/2}" text-anchor="${side>0?"start":"end"}" fill="#42556d" font-size="12">${escape(label)}</text>`;
}

export function buildNozzleSvg(geometry) {
  const data=inspectNozzleGeometry(geometry);
  if (!data.valid) return "";
  const d=geometry.dimensions || {},width=1040,height=530,top=97,bottom=102,left=120,right=130;
  // One metres-to-pixels scale for both axes, including exported SVG.
  const scale=Math.min((width-left-right)/data.length,(height-top-bottom)/(2*data.maxR));
  const drawingWidth=data.length*scale, start=left+(width-left-right-drawingWidth)/2, center=(top+height-bottom)/2;
  const X=x=>start+(x-data.minX)*scale, Y=r=>center-r*scale;
  const upper=data.x.map((x,i)=>`${i?"L":"M"}${X(x)},${Y(data.r[i])}`).join(" ");
  const lower=data.x.map((x,i)=>`${i?"L":"M"}${X(x)},${Y(-data.r[i])}`).join(" ");
  const fill=upper+data.x.toReversed().map((x,i)=>` L${X(x)},${Y(-data.r[data.r.length-1-i])}`).join("")+" Z";
  const throatInside=finite(d.throat_x_m) && d.throat_x_m>=data.minX && d.throat_x_m<=data.maxX;
  let dimensions="";
  if (finite(d.total_length_m)) dimensions+=horizontalDimension(X(data.minX),X(data.maxX),44,`总长 ${number(d.total_length_m)} m`);
  if (finite(d.chamber_diameter_m)) dimensions+=verticalDimension(X(data.minX)-22,Y(d.chamber_diameter_m/2),Y(-d.chamber_diameter_m/2),`Dc ${number(d.chamber_diameter_m)} m`,-1);
  if (finite(d.exit_diameter_m)) dimensions+=verticalDimension(X(data.maxX)+22,Y(d.exit_diameter_m/2),Y(-d.exit_diameter_m/2),`De ${number(d.exit_diameter_m)} m`);
  if (throatInside) {
    dimensions+=`<path d="M${X(d.throat_x_m)},${top+28}V${height-bottom+23}" fill="none" stroke="#ae6c50" stroke-dasharray="4 4"/><text x="${X(d.throat_x_m)}" y="${top+22}" text-anchor="middle" fill="#9b6049" font-size="12">喉部</text>`;
    if (finite(d.throat_diameter_m)) dimensions+=`<text x="${X(d.throat_x_m)+12}" y="${Y(-d.throat_diameter_m/2)+23}" fill="#42556d" font-size="12">Dt ${number(d.throat_diameter_m)} m</text>`;
    if (finite(d.nozzle_length_m)) dimensions+=horizontalDimension(X(d.throat_x_m),X(data.maxX),height-53,`膨胀段 ${number(d.nozzle_length_m)} m`);
    if (finite(d.convergent_length_m)) {
      const convergentStart=d.throat_x_m-d.convergent_length_m;
      if (convergentStart>=data.minX) dimensions+=horizontalDimension(X(convergentStart),X(d.throat_x_m),height-78,`收缩段 ${number(d.convergent_length_m)} m`);
    }
    if (finite(d.chamber_length_m)) dimensions+=horizontalDimension(X(data.minX),X(data.minX+d.chamber_length_m),height-53,`直筒段 ${number(d.chamber_length_m)} m`);
  }
  let cooling="";
  const c=geometry.cooling;
  if (finite(c?.start_x_m) && finite(c?.end_x_m) && Math.min(c.start_x_m,c.end_x_m)>=data.minX && Math.max(c.start_x_m,c.end_x_m)<=data.maxX) {
    cooling=`<path d="M${X(Math.min(c.start_x_m,c.end_x_m))},70H${X(Math.max(c.start_x_m,c.end_x_m))}" stroke="#39877c" stroke-width="5"/><text x="${(X(c.start_x_m)+X(c.end_x_m))/2}" y="88" fill="#286c63" text-anchor="middle" font-size="12">再生冷却覆盖</text>`;
  }
  const paintedDimensions=dimensions.replaceAll("<text ", '<text paint-order="stroke" stroke="#fff" stroke-width="4" stroke-linejoin="round" ');
  const ticks=Array.from({length:5},(_,i)=>data.minX+data.length*i/4).map(value=>`<path d="M${X(value)},${center-3}v6" stroke="#8b99aa"/><text x="${X(value)}" y="${center+17}" fill="#79889b" text-anchor="middle" font-size="11">${number(value)}</text>`).join("");
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="推力室与喷管燃气内轮廓等比例剖面" data-geometry-scale="${scale}" style="font-family:system-ui,Microsoft YaHei,sans-serif;background:#fff"><title>推力室与喷管燃气内轮廓</title><desc>轴向与径向使用相同比例。${escape(coordinateLabel(geometry.contour.coordinate_origin))}。${escape(geometry.method?.label || "构造方法未记录")}。</desc><rect width="${width}" height="${height}" fill="#fff"/><path d="${fill}" fill="#edf3fa"/><path d="M${X(data.minX)-10},${center}H${X(data.maxX)+10}" stroke="#97a5b7" stroke-dasharray="6 4"/>${ticks}<path d="${upper} ${lower}" fill="none" stroke="#376a9c" stroke-width="2.5" stroke-linejoin="round"/>${cooling}${paintedDimensions}<text x="${width/2}" y="${height-13}" fill="#65778e" text-anchor="middle" font-size="12">轴向 x / m · ${escape(coordinateLabel(geometry.contour.coordinate_origin))} · 轴向与径向等比例</text></svg>`;
}

export function buildNozzleCsv(geometry) {
  const data=inspectNozzleGeometry(geometry);
  if (!data.valid) return "";
  const origin=["injector_face","throat"].includes(geometry.contour.coordinate_origin)?geometry.contour.coordinate_origin:"unspecified";
  return "x_m,r_m,coordinate_origin\r\n"+data.x.map((x,i)=>`${x},${data.r[i]},${origin}`).join("\r\n")+"\r\n";
}

export function buildNozzleSurface(geometry, segments=36) {
  const data=inspectNozzleGeometry(geometry);
  if (!data.valid) return [];
  const count=Number.isInteger(segments) && segments>=8 && segments<=96?segments:36;
  return data.x.map((x,i)=>Array.from({length:count},(_,j)=>{
    const theta=j/count*Math.PI*2;
    return [x-(data.minX+data.maxX)/2,data.r[i]*Math.cos(theta),data.r[i]*Math.sin(theta)];
  }));
}

export function renderNozzleGeometry(geometry) {
  const data=inspectNozzleGeometry(geometry);
  if (!data.valid) return `<section class="nozzle-geometry ng-unavailable"><h3>推力室与喷管几何</h3><p>${escape(data.reason)}</p><p class="ng-muted">历史结果保留原样；缺失的尺寸与坐标不会用当前参数补画。</p></section>`;
  const d=geometry.dimensions || {},method=geometry.method || {},source=geometry.source || {};
  const notes=Array.isArray(method.notes)?method.notes:method.notes?[method.notes]:[];
  const details=DIMENSIONS.map(([key,label,unit])=>`<div><dt>${label}</dt><dd>${number(d[key])}${finite(d[key])?` <small>${unit}</small>`:""}</dd></div>`).join("");
  const check=geometry.validation || {};
  const messages=Array.isArray(check.messages)?check.messages:[];
  return `<section class="nozzle-geometry" data-nozzle-geometry><div class="ng-heading"><div><span class="ng-eyebrow">GEOMETRY</span><h3>推力室与喷管几何</h3><p>${escape(method.label || "构造方法未记录")}</p></div><span class="ng-point-count">${data.x.length} 个轮廓点 · m</span></div><p class="ng-source${source.kind === "reconstructed_from_record" ? " ng-source-reconstructed" : ""}"><strong>几何来源：</strong>${escape(source.label || "来源未记录")}${source.kind === "reconstructed_from_record" ? "；按本记录的尺寸与输入构造，不补造冷却结果。" : ""}</p><div class="ng-toolbar"><div class="ng-views" role="group" aria-label="几何视图"><button type="button" data-nozzle-view="2d" aria-pressed="true">二维剖面</button><button type="button" data-nozzle-view="3d" aria-pressed="false">三维旋转面</button></div><div class="ng-exports"><button type="button" data-nozzle-export="csv">导出坐标 CSV</button><button type="button" data-nozzle-export="svg">导出剖面 SVG</button></div></div><div class="ng-viewport" data-nozzle-panel="2d">${buildNozzleSvg(geometry)}</div><div class="ng-viewport ng-three" data-nozzle-panel="3d" hidden><canvas data-nozzle-canvas tabindex="0" aria-label="喷管燃气内边界三维旋转面，拖动或方向键旋转，滚轮或加减键缩放，Home 键复位">当前浏览器不支持三维示意显示，二维剖面与坐标仍可查看。</canvas><div class="ng-three-controls"><span>拖动旋转 · 滚轮缩放 · 方向键亦可操作</span><button type="button" data-nozzle-zoom="in" aria-label="放大三维视图">＋</button><button type="button" data-nozzle-zoom="out" aria-label="缩小三维视图">−</button><button type="button" data-nozzle-reset>复位视角</button></div><p class="ng-canvas-error" data-nozzle-canvas-error hidden>三维画布不可用，请查看二维剖面或导出坐标。</p></div><p class="ng-scope">图示为轴对称燃气内轮廓及其旋转面，用于总体尺寸与覆盖范围检查；未表示壁厚、冷却槽道或承力外壳。</p><dl class="ng-dimensions">${details}</dl><details class="ng-method"><summary>构造方法与数据来源</summary><dl><dt>构造方法</dt><dd>${escape(method.label || "未记录")}</dd><dt>角度来源</dt><dd>${escape(method.angle_source || "未记录")}</dd><dt>坐标原点</dt><dd>${escape(coordinateLabel(geometry.contour.coordinate_origin))}</dd><dt>几何来源</dt><dd>${escape(source.label || "来源未记录")}</dd></dl>${notes.length?`<ul>${notes.map(note=>`<li>${escape(note)}</li>`).join("")}</ul>`:""}${messages.length?`<ul>${messages.map(message=>`<li>${escape(message)}</li>`).join("")}</ul>`:""}</details></section>`;
}

function saveFile(text,type,name,doc) {
  const win=doc.defaultView || window,url=win.URL.createObjectURL(new Blob([text],{type}));
  const a=doc.createElement("a");a.href=url;a.download=name;doc.body.append(a);a.click();a.remove();
  win.setTimeout(()=>win.URL.revokeObjectURL(url),1000);
}

export function mountNozzleGeometry(container,geometry) {
  const root=container?.matches?.("[data-nozzle-geometry]")?container:container?.querySelector?.("[data-nozzle-geometry]");
  if (!root || !inspectNozzleGeometry(geometry).valid) return ()=>{};
  root._nozzleCleanup?.();
  const canvas=root.querySelector("[data-nozzle-canvas]"),ctx=canvas?.getContext?.("2d");
  const data=inspectNozzleGeometry(geometry),mesh=buildNozzleSurface(geometry),win=root.ownerDocument.defaultView || window;
  let yaw=.45,pitch=-.12,zoom=1,drag=null,frame=null,disposed=false;
  const listeners=[];
  const listen=(element,type,fn,options)=>{element?.addEventListener(type,fn,options);listeners.push(()=>element?.removeEventListener(type,fn,options));};
  const draw=()=>{
    frame=null;
    if (disposed || !ctx || root.querySelector('[data-nozzle-panel="3d"]')?.hidden) return;
    const bounds=canvas.getBoundingClientRect(),width=Math.max(240,bounds.width),height=Math.max(260,bounds.height),ratio=Math.min(win.devicePixelRatio || 1,2);
    canvas.width=Math.round(width*ratio);canvas.height=Math.round(height*ratio);ctx.setTransform(ratio,0,0,ratio,0,0);ctx.clearRect(0,0,width,height);
    ctx.fillStyle="#f8fafd";ctx.fillRect(0,0,width,height);
    const scale=Math.min(width*.72/Math.hypot(data.length,2*data.maxR),height*.68/(2*data.maxR))*zoom;
    const cy=Math.cos(yaw),sy=Math.sin(yaw),cp=Math.cos(pitch),sp=Math.sin(pitch);
    const project=p=>{const x=p[0]*cy+p[2]*sy,z=-p[0]*sy+p[2]*cy;return [width/2+(x*cp-p[1]*sp)*scale,height/2-(x*sp+p[1]*cp)*scale,z];};
    const projected=mesh.map(ring=>ring.map(project)),faces=[];
    for(let i=0;i<projected.length-1;i++)for(let j=0;j<projected[i].length;j++){
      const next=(j+1)%projected[i].length,points=[projected[i][j],projected[i+1][j],projected[i+1][next],projected[i][next]];
      faces.push({points,depth:points.reduce((sum,p)=>sum+p[2],0)/4,light:.5+.5*Math.cos((j+.5)/projected[i].length*Math.PI*2-yaw)});
    }
    faces.sort((a,b)=>a.depth-b.depth);
    for(const face of faces){ctx.beginPath();face.points.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));ctx.closePath();ctx.fillStyle=`hsl(209,33%,${50+face.light*25}%)`;ctx.fill();ctx.strokeStyle="rgba(47,78,109,.17)";ctx.lineWidth=.45;ctx.stroke();}
    // Open inlet/outlet rings identify an inner flow boundary, not a solid end cap.
    for(const ring of [projected[0],projected.at(-1)]){ctx.beginPath();ring.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));ctx.closePath();ctx.strokeStyle="#345d83";ctx.lineWidth=1.4;ctx.stroke();}
    ctx.font="12px system-ui, sans-serif";ctx.fillStyle="#64778e";ctx.fillText("轴对称燃气内边界 · 无壁厚",18,27);
  };
  const schedule=()=>{if(frame===null&&!disposed)frame=win.requestAnimationFrame(draw);};
  const setZoom=factor=>{zoom=Math.min(2.8,Math.max(.4,zoom*factor));schedule();};
  listen(root,"click",event=>{
    const button=event.target?.closest?.("button");if(!button||!root.contains(button))return;
    if(button.dataset.nozzleView){const mode=button.dataset.nozzleView;for(const tab of root.querySelectorAll("[data-nozzle-view]"))tab.setAttribute("aria-pressed",String(tab.dataset.nozzleView===mode));for(const panel of root.querySelectorAll("[data-nozzle-panel]"))panel.hidden=panel.dataset.nozzlePanel!==mode;schedule();}
    if(button.dataset.nozzleExport==="csv")saveFile(buildNozzleCsv(geometry),"text/csv;charset=utf-8","nozzle-contour.csv",root.ownerDocument);
    if(button.dataset.nozzleExport==="svg")saveFile(buildNozzleSvg(geometry),"image/svg+xml;charset=utf-8","nozzle-profile.svg",root.ownerDocument);
    if(button.dataset.nozzleZoom)setZoom(button.dataset.nozzleZoom==="in"?1.15:1/1.15);
    if(button.hasAttribute("data-nozzle-reset")){yaw=.45;pitch=-.12;zoom=1;schedule();}
  });
  listen(canvas,"pointerdown",event=>{if(event.button!==0)return;drag={x:event.clientX,y:event.clientY,id:event.pointerId};canvas.setPointerCapture?.(event.pointerId);canvas.focus();});
  listen(canvas,"pointermove",event=>{if(!drag||drag.id!==event.pointerId)return;yaw+=(event.clientX-drag.x)*.008;pitch+=(event.clientY-drag.y)*.008;drag={x:event.clientX,y:event.clientY,id:event.pointerId};schedule();});
  const stopDrag=event=>{if(drag?.id===event.pointerId)drag=null;};
  listen(canvas,"pointerup",stopDrag);listen(canvas,"pointercancel",stopDrag);listen(canvas,"lostpointercapture",()=>{drag=null;});
  listen(canvas,"wheel",event=>{event.preventDefault();setZoom(event.deltaY<0?1.08:1/1.08);},{passive:false});
  listen(canvas,"keydown",event=>{if(!["ArrowLeft","ArrowRight","ArrowUp","ArrowDown","+","=","-","Home"].includes(event.key))return;event.preventDefault();if(event.key==="ArrowLeft")yaw-=.12;if(event.key==="ArrowRight")yaw+=.12;if(event.key==="ArrowUp")pitch-=.12;if(event.key==="ArrowDown")pitch+=.12;if(["+","="].includes(event.key))setZoom(1.15);if(event.key==="-")setZoom(1/1.15);if(event.key==="Home"){yaw=.45;pitch=-.12;zoom=1;}schedule();});
  if(!ctx){const error=root.querySelector("[data-nozzle-canvas-error]");if(error)error.hidden=false;}
  let observer=null;
  if(win.ResizeObserver){observer=new win.ResizeObserver(schedule);observer.observe(canvas);}else listen(win,"resize",schedule);
  const cleanup=()=>{disposed=true;listeners.forEach(remove=>remove());observer?.disconnect();if(frame!==null)win.cancelAnimationFrame(frame);if(root._nozzleCleanup===cleanup)delete root._nozzleCleanup;};
  root._nozzleCleanup=cleanup;
  return cleanup;
}
