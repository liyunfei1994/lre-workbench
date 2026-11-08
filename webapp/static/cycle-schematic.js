// Interactive system schematic. Geometry is illustrative; all quantities and
// connections are supplied by the saved-result adapter in cycle-model.js.
import {cycleEquipmentSymbol} from './cycle-symbols.js';
const esc = value => String(value ?? "").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const finite = value => typeof value === "number" && Number.isFinite(value);
const fmt = value => !finite(value) ? "—" : Math.abs(value)>0 && (Math.abs(value)<.001 || Math.abs(value)>=1e7) ? value.toExponential(3) : value.toLocaleString("en-US",{maximumFractionDigits:3});
const tones = new Set(["ox","fuel","oxgas","fuelgas","hot","neutral"]);
const tone = value => tones.has(value) ? value : "neutral";
const names = {ffsc:"全流量分级燃烧循环",generic:"记录中的循环连接"};
const tag = (source,target) => `${source}>${target}`;

/** Symbol coordinates only: this profile does not encode calculated dimensions. */
export function thrustChamberProfile() {
  return {
    chamber:{halfWidth:50,top:-40,bottom:45},
    convergent:[[50,-80],[50,-62],[20,-52],[20,-28]],
    divergent:[[20,-28],[20,-8],[90,43],[100,80]],
  };
}

function nozzleSymbol() {
  const {convergent:c,divergent:d}=thrustChamberProfile();
  const point=([x,y])=>`${x} ${y}`,mirror=([x,y])=>[-x,y];
  const curve=points=>`C${points.map(point).join(' ')}`;
  const right=`M${point(c[0])}${curve(c.slice(1))}${curve(d.slice(1))}`;
  const left=`M${point(mirror(c[0]))}${curve(c.slice(1).map(mirror))}${curve(d.slice(1).map(mirror))}`;
  const fill=`${right}L${point(mirror(d[3]))}${curve([d[2],d[1],d[0]].map(mirror))}${curve([c[2],c[1],c[0]].map(mirror))}Z`;
  return `<path class="cy-flow-volume" d="${fill}"/><path class="cy-flow-wall cy-nozzle-wall" d="${right}${left}"/><path class="cy-exit-plane" d="M${point(mirror(d[3]))}H${d[3][0]}"/><path class="cy-centerline" d="M0 ${c[0][1]+8}V${d[3][1]}"/><path class="cy-throat-guide" d="M${-c[3][0]-5} ${c[3][1]}H130"/><text class="cy-profile-label" x="142" y="${c[3][1]+4}">喉部</text><path class="cy-exhaust" d="M-55 90V105M0 90V112M55 90V105"/>`;
}

/** Fixed engineering layouts are display coordinates, never engine dimensions. */
export function cycleLayout(model) {
  if(model.kind==='ffsc') {
    // A compact process drawing. Shaft lines express power transfer, not a bent physical shaft.
    const positions={fuel_tank:[340,100],lox_tank:[940,100],fuel_pump:[340,210],ox_pump:[940,210],
      cooling_channel:[340,320],fuel_preburner:[340,450],ox_preburner:[940,450],
      fuel_preburner_turbine:[340,565],ox_preburner_turbine:[940,565],main_chamber:[640,660],nozzle:[640,785]};
    const paths={
      'fuel_tank>fuel_pump':'M340 128V175','lox_tank>ox_pump':'M940 128V175',
      'fuel_pump>cooling_channel':'M340 245V284','cooling_channel>fuel_preburner':'M340 356V414',
      'cooling_channel>ox_preburner':'M340 386H380Q390 374 400 386H910V414',
      'ox_pump>ox_preburner':'M940 245V414','ox_pump>fuel_preburner':'M940 266H450V366H390V414',
      'fuel_preburner>fuel_preburner_turbine':'M340 486V530','ox_preburner>ox_preburner_turbine':'M940 486V530',
      'fuel_preburner_turbine>main_chamber':'M340 600V620H590',
      'ox_preburner_turbine>main_chamber':'M940 600V620H690','main_chamber>nozzle':'M640 700V710',
    };
    const shafts=[{source:'fuel_preburner_turbine',target:'fuel_pump',d:'M305 565H64V210H305',x:64,y:398,label:'燃料轴 · 功率传递'},
      {source:'ox_preburner_turbine',target:'ox_pump',d:'M975 565H1216V210H975',x:1216,y:398,label:'液氧轴 · 功率传递'}];
    // Dedicated label bays keep readouts clear of fluid and mechanical connections.
    const labels=Object.fromEntries(Object.entries(positions).map(([id,[x,y]])=>[id,
      id==='main_chamber'?[830,658]:id==='nozzle'?[850,810]:[x===340?180:1100,y-9]]));
    return {width:1280,height:930,positions,paths,shafts,junctions:[[340,386,'fuel'],[940,266,'ox']],labels};
  }
  if(model.kind==="generic") {
    const nodes=model.nodes || [];
    const positions=Object.fromEntries(nodes.map((node,i)=>[node.id,[250,140+i*210]]));
    const paths={};let lane=500;
    // Keep arbitrary forward, backward and self edges outside every object.
    for(const edge of model.connections || []) {
      const a=positions[edge.source],b=positions[edge.target];
      if(!a || !b)continue;
      const self=edge.source===edge.target, start=a[1]-(self?12:0),end=b[1]+(self?12:0);
      paths[tag(edge.source,edge.target)]=`M${a[0]+75} ${start}H${lane}V${end}H${b[0]+75}`;
      lane+=28;
    }
    return {width:Math.max(1120,lane+100),height:Math.max(460,nodes.length*210+100),positions,paths,shafts:[],junctions:[]};
  }
  return {width:1280,height:930,positions:{},paths:{},shafts:[],junctions:[]};
}

function symbol(node) {
  const equipment=cycleEquipmentSymbol(node);
  if(equipment)return equipment;
  if(node.type==="chamber") {
    const {halfWidth:r,top,bottom}=thrustChamberProfile().chamber;
    return `<path class="cy-flow-volume" d="M${-r} ${top}H${r}V${bottom}H${-r}Z"/><path class="cy-flow-wall cy-chamber-wall" d="M${-r} ${bottom}V${top}H${r}V${bottom}"/><path class="cy-injector" d="M-57-43H57V-33H-57Z"/><path class="cy-detail" d="M-30-26V-16M-10-26V-16M10-26V-16M30-26V-16"/><path class="cy-flow-direction" d="M0-8V27M-7 19L0 27L7 19"/>`;
  }
  if(node.type==="nozzle")return nozzleSymbol();
  return '<rect class="cy-symbol" x="-75" y="-30" width="150" height="60" rx="5"/>';
}

function annotations(node,x,y,labelPosition) {
  const metrics=node.metrics || [];
  const pressure=metrics.find(m=>m.unit==="MPa" && /出口|室压/.test(m.label)) || metrics.find(m=>m.unit==="MPa" && !/入口/.test(m.label)),temperature=metrics.find(m=>m.unit==="K"),flow=metrics.find(m=>m.unit==="kg/s");
  const first=(node.states || []).at(-1);
  // Generic objects expose associated interfaces in the inspector; their last
  // available state does not establish which interface is the actual outlet.
  const known=node.type!=="generic";
  const p=!known ? null : pressure ? pressure.value : metrics.length ? null : first?.pressure,
    t=!known ? null : temperature ? temperature.value : metrics.length ? null : first?.temperature,
    q=!known ? null : flow ? flow.value : metrics.length ? null : first?.massFlow;
  const offset=node.type==="nozzle" ? 72 : node.type==="preburner" || node.type==="cooling" ? 57 : 62;
  if(node.type==='chamber'){x+=180;y-=offset-20;}
  if(node.id==='nozzle' && node.type==='nozzle'){x+=195;y-=offset-50;}
  let yy=y+offset;
  if(labelPosition){x=labelPosition[0];yy=labelPosition[1]+24;}
  return `<g class="cy-annotation" data-cy-annotation="pt"><text x="${x}" y="${yy}" text-anchor="middle">${esc(fmt(p))} <tspan>MPa</tspan> · ${esc(fmt(t))} <tspan>K</tspan></text></g><g class="cy-annotation" data-cy-annotation="flow" hidden><text x="${x}" y="${yy}" text-anchor="middle">ṁ ${esc(fmt(q))} <tspan>kg/s</tspan></text></g>`;
}

function diagram(model) {
  const layout=cycleLayout(model), present=new Set(model.nodes.map(n=>n.id));
  const nodes=model.nodes.filter(n=>layout.positions[n.id]);
  const defs=[...tones].map(t=>`<marker id="cy-arrow-${t}" viewBox="0 0 10 10" refX="9" refY="5" markerUnits="userSpaceOnUse" markerWidth="10" markerHeight="10" orient="auto"><path d="M1 1L9 5L1 9Z" class="cy-arrow" data-tone="${t}"/></marker>`).join("");
  const edges=(model.connections || []).filter(e=>present.has(e.source)&&present.has(e.target)).map(edge=>{
    const a=layout.positions[edge.source],b=layout.positions[edge.target]; if(!a||!b)return "";
    const d=layout.paths[tag(edge.source,edge.target)] || `M${a[0]+75} ${a[1]}H${(a[0]+b[0])/2}V${b[1]}H${b[0]-75}`;
    return `<g class="cy-pipe" data-tone="${tone(edge.tone)}" data-cy-edge="${esc(tag(edge.source,edge.target))}" data-cy-from="${esc(edge.source)}" data-cy-to="${esc(edge.target)}"><title>${esc(edge.label || `${edge.source} → ${edge.target}`)}</title><path class="cy-pipe-halo" d="${d}"/><path class="cy-pipe-line" d="${d}" marker-end="url(#cy-arrow-${tone(edge.tone)})"/></g>`;
  }).join("");
  const shafts=layout.shafts.filter(s=>(model.shafts || []).some(x=>x.source===s.source&&x.target===s.target || x.target===s.source&&x.source===s.target)).map(s=>`<g class="cy-shaft"><title>${esc(s.label)} · 机械功率传递</title><path d="${s.d}"/><text x="${s.x}" y="${s.y}"${model.kind==='ffsc'?` transform="rotate(-90 ${s.x} ${s.y})"`:''} text-anchor="middle">${esc(s.label)}</text></g>`).join("");
  const defaultId=nodes.some(n=>n.id==='main_chamber')?'main_chamber':nodes[0]?.id;
  const shapes=nodes.map((node,i)=>{
    const [x,y]=layout.positions[node.id],mainNozzle=node.id==='nozzle' && node.type==='nozzle';
    const labelPosition=layout.labels?.[node.id];
    const labelX=labelPosition?.[0] ?? (mainNozzle?x+195:x),labelY=labelPosition?.[1] ?? (mainNozzle?y+25:node.type==="nozzle" ? y+5 : node.type==="tank" ? y-43 : y-50);
    const hit=mainNozzle?[x-115,y-83,230,178]:node.type==='chamber'?[x-72,y-53,144,103]:[x-82,y-57,164,node.type==='nozzle'?146:130];
    return `<g class="cy-object${node.id===defaultId?' is-selected':''}" data-cy-object="${esc(node.id)}" data-cy-states="${(node.states || []).map(s=>esc(s.id)).join(' ')}" data-tone="${tone(node.tone)}" role="button" tabindex="0" aria-label="查看${esc(node.title)}" aria-pressed="${node.id===defaultId}"><title>${esc(node.title)} · 选择查看记录参数</title><rect class="cy-hit" x="${hit[0]}" y="${hit[1]}" width="${hit[2]}" height="${hit[3]}" rx="6"/><g transform="translate(${x} ${y})">${symbol(node)}</g>${labelPosition?`<path class="cy-label-leader" d="M${x<640?labelX+90:labelX-90} ${labelY+5}H${x<640?x-76:x+76}"/>`:''}<text class="cy-object-title" x="${labelX}" y="${labelY}" text-anchor="middle">${esc(node.title)}</text>${annotations(node,x,y,labelPosition)}</g>`;
  }).join("");
  const labelPositions=model.kind==='ffsc' ? [['ox_pump','fuel_preburner',650,252],['cooling_channel','ox_preburner',650,372]]
    : [];
  const branchLabels=labelPositions.map(([source,target,x,y])=>{
    const edge=model.connections.find(e=>e.source===source&&e.target===target);if(!edge)return '';
    const short=String(edge.label).split(' · ')[0];
    return ['pt','none','flow'].map(mode=>`<text class="cy-branch-label" data-cy-annotation="${mode}"${mode==='pt'?'':' hidden'} x="${x}" y="${y}" text-anchor="middle">${esc(mode==='flow'?edge.label:short)}</text>`).join('');
  }).join('');
  const sheet=model.kind==='ffsc'?`<path class="cy-sheet-rule" d="M96 42H1184M96 904H1184"/><text class="cy-circuit-label" x="340" y="30" text-anchor="middle">FUEL / ${esc(model.fuel?.name || '燃料')}支路</text><text class="cy-circuit-label" x="940" y="30" text-anchor="middle">OXIDIZER / 液氧支路</text><text class="cy-sheet-label" x="640" y="550" text-anchor="middle">THRUST CHAMBER</text><text class="cy-sheet-label" x="96" y="923">FFSC / 系统流路与功率传递</text><text class="cy-sheet-label" x="1184" y="923" text-anchor="end">示意剖面 · 非比例绘制</text>`:`<text class="cy-sheet-label" x="24" y="30">ENGINE CYCLE / 稳态系统</text><text class="cy-sheet-label" x="1096" y="30" text-anchor="end">${model.kind==='generic'?'RECORDED CONNECTIONS':'FUNCTIONAL SCHEMATIC'}</text>`;
  return `<svg class="cy-svg" viewBox="0 0 ${layout.width} ${layout.height}" role="group" aria-label="${esc(names[model.kind] || names.generic)}原理图"><defs>${defs}</defs><rect class="cy-paper" width="100%" height="100%"/>${sheet}${shafts}${edges}${branchLabels}${layout.junctions.map(([x,y,t])=>`<circle class="cy-junction" data-tone="${t}" cx="${x}" cy="${y}" r="3.5"/>`).join('')}${shapes}</svg>`;
}

function inspector(node,index) {
  return `<section class="cy-inspector" data-cy-panel="${esc(node.id)}"${index?' hidden':''}><div class="cy-inspector-heading" data-tone="${tone(node.tone)}"><span>SELECTED OBJECT</span><h3>${esc(node.title)}</h3><code>${esc(node.id)}</code></div><dl class="cy-metrics">${(node.metrics || []).map(metric=>`<div><dt>${esc(metric.label)}</dt><dd title="${finite(metric.value)?metric.value:'未提供'} ${esc(metric.unit)}">${fmt(metric.value)} <small>${esc(metric.unit)}</small></dd></div>`).join('') || '<p class="cy-note">本记录未提供该对象的独立结果。</p>'}</dl><div class="cy-state-links"><h4>关联流路状态</h4>${(node.states || []).map(s=>`<a href="#states" data-flow-state="${esc(s.id)}" data-flow-group="${esc(s.group)}"><b>${esc(s.code)}</b><span>${esc(s.title)}</span><i>↗</i></a>`).join('') || '<p class="cy-note">没有独立的流路状态记录。</p>'}</div>${node.componentId?`<button type="button" class="cy-component-link" data-cy-component="${esc(node.componentId)}">查看部件性能 <span>→</span></button>`:''}</section>`;
}

export function renderCycleWorkspace(model) {
  if(model.kind==='unsupported' || !['ffsc','generic'].includes(model.kind))return '<section class="panel"><h2>循环原理图不可用</h2><p>当前平台仅支持全流量补燃循环（FFSC）。此历史记录不提供循环分析，请导出原始记录查看。</p></section>';
  if(!model.nodes?.length)return '<section class="panel"><h2>发动机循环原理图</h2><p class="muted">本记录没有流程节点数据。请重新运行设计点。</p></section>';
  return `<section class="cy-workspace" aria-label="发动机循环原理图"><header class="cy-header"><div><span class="cy-eyebrow">ENGINE CYCLE SCHEMATIC</span><h2>${esc(names[model.kind] || names.generic)}</h2><p>${esc(model.fuel?.pair || '')} · 供给、能量转换与推力输出</p></div><div class="cy-record-mark"><span class="cy-record-dot"></span>计算记录驱动<small>示意布局 · 非实际安装位置</small></div></header><div class="cy-toolbar"><div class="cy-annotation-controls" role="group" aria-label="图上显示"><span>图上显示</span><button type="button" data-cy-mode="pt" aria-pressed="true" class="is-selected">压力 / 温度</button><button type="button" data-cy-mode="flow" aria-pressed="false">质量流量</button><button type="button" data-cy-mode="none" aria-pressed="false">仅原理</button></div><div class="cy-display-controls"><button type="button" data-cy-fit aria-pressed="false">适应窗口</button><button type="button" data-cy-export>导出 SVG</button></div></div><div class="cy-body"><div class="cy-drawing"><div class="cy-legend" aria-label="图例">${[['fuel',model.fuel?.name || '燃料'],['ox','液氧'],['fuelgas','富燃燃气'],...(model.kind==='ffsc'?[['oxgas','富氧燃气']]:[]),['hot','主室燃气'],['shaft','机械传动']].map(([t,label])=>`<span data-tone="${t}"><i></i>${esc(label)}</span>`).join('')}</div><div class="cy-viewport">${diagram(model)}</div><div class="cy-drawing-foot"><span>● 分流 / 汇流节点 · 无圆点交叉不连接</span><span>部件标注出口 p / T；贮箱标注本体状态</span></div></div><aside class="cy-sidebar" aria-label="循环对象详情">${model.nodes.map((node,index)=>inspector(node,model.nodes.some(n=>n.id==='main_chamber')?(node.id==='main_chamber'?0:1):index)).join('')}<section class="cy-summary"><h3>设计点摘要</h3><dl class="cy-metrics">${(model.summary || []).map(m=>`<div><dt>${esc(m.label)}</dt><dd>${fmt(m.value)} <small>${esc(m.unit)}</small></dd></div>`).join('')}</dl></section></aside></div><div class="cy-model-notes"><b>模型说明</b><p>此图用于说明系统连接与能量传递，不表示实际布置或几何尺寸。图上数值来自当前计算记录；“—”表示未提供，不补算物性。</p>${(model.notes || []).map(note=>`<p>${esc(note)}</p>`).join('')}</div><details class="cy-connections"><summary>流路连接明细 <span>${model.connections.length} 条</span></summary><div class="table-wrap"><table><thead><tr><th>起点</th><th>终点</th><th>流路说明</th></tr></thead><tbody>${model.connections.map(e=>`<tr><td>${esc(model.nodes.find(n=>n.id===e.source)?.title || e.source)}</td><td>${esc(model.nodes.find(n=>n.id===e.target)?.title || e.target)}</td><td>${esc(e.label || '—')}</td></tr>`).join('')}</tbody></table></div></details></section>`;
}

/** Make a portable SVG with its external presentation rules embedded. */
export function portableCycleSvg(svg,css) {
  const clone=svg.cloneNode(true);
  clone.setAttribute('xmlns','http://www.w3.org/2000/svg');clone.setAttribute('width',clone.viewBox.baseVal.width);clone.setAttribute('height',clone.viewBox.baseVal.height);
  clone.querySelectorAll('[hidden]').forEach(node=>node.remove());
  clone.querySelectorAll('.is-selected').forEach(node=>node.classList.remove('is-selected'));
  clone.querySelectorAll('[tabindex]').forEach(node=>node.removeAttribute('tabindex'));
  const style=clone.ownerDocument.createElementNS('http://www.w3.org/2000/svg','style');style.textContent=css;clone.prepend(style);
  return '<?xml version="1.0" encoding="UTF-8"?>\n'+new XMLSerializer().serializeToString(clone);
}
const mounted=new WeakMap();
export function mountCycleWorkspace(host,recordId,{onComponent,onError}={}) {
  if(!host?.querySelector || !host.dataset)return;
  const old=mounted.get(host);if(old){host.removeEventListener('click',old.click);host.removeEventListener('keydown',old.keydown);}mounted.delete(host);
  const workspace=host.querySelector('.cy-workspace');if(!workspace)return;
  const nodes=[...workspace.querySelectorAll('[data-cy-object]')],panels=[...workspace.querySelectorAll('[data-cy-panel]')],buttons=[...workspace.querySelectorAll('[data-cy-mode]')];
  const changed=host.dataset.cyRecord!==String(recordId ?? '');host.dataset.cyRecord=String(recordId ?? '');
  const initial=nodes.find(n=>n.dataset.cyObject==='main_chamber')?.dataset.cyObject || nodes[0]?.dataset.cyObject;
  let selected=changed?initial:host.dataset.cySelected,mode=host.dataset.cyMode || 'pt';
  if(host.dataset.flowLocationRecord===String(recordId ?? '') && host.dataset.flowLocation) {
    selected=nodes.find(n=>(n.dataset.cyStates || '').split(' ').includes(host.dataset.flowLocation))?.dataset.cyObject || selected;
    delete host.dataset.flowLocation;
  }
  function update(){
    if(!nodes.some(n=>n.dataset.cyObject===selected))selected=initial;
    if(!['pt','flow','none'].includes(mode))mode='pt';
    host.dataset.cySelected=selected || '';host.dataset.cyMode=mode;
    nodes.forEach(n=>{const active=n.dataset.cyObject===selected;n.classList.toggle('is-selected',active);n.setAttribute('aria-pressed',String(active));});
    panels.forEach(p=>{p.hidden=p.dataset.cyPanel!==selected;});
    buttons.forEach(b=>{const active=b.dataset.cyMode===mode;b.classList.toggle('is-selected',active);b.setAttribute('aria-pressed',String(active));});
    workspace.querySelectorAll('[data-cy-annotation]').forEach(n=>{n.toggleAttribute('hidden',n.dataset.cyAnnotation!==mode);});
    workspace.querySelectorAll('[data-cy-edge]').forEach(n=>n.classList.toggle('is-active',n.dataset.cyFrom===selected || n.dataset.cyTo===selected));
    workspace.classList.toggle('cy-fit',host.dataset.cyFit==='true');workspace.querySelector('[data-cy-fit]')?.setAttribute('aria-pressed',String(host.dataset.cyFit==='true'));
  }
  update();
  async function activate(event){
    const target=event.target?.closest?.('[data-cy-object],[data-cy-mode],[data-cy-component],[data-cy-fit],[data-cy-export]');
    if(!target || !workspace.contains(target))return;
    if(target.dataset.cyComponent!=null){onComponent?.(target.dataset.cyComponent);return;}
    if(target.dataset.cyExport!=null){
      target.disabled=true;
      try{const response=await fetch('/static/cycle-schematic.css');if(!response.ok)throw Error('无法读取原理图导出样式。');const css=await response.text();
        const text=portableCycleSvg(workspace.querySelector('.cy-svg'),css);const url=URL.createObjectURL(new Blob([text],{type:'image/svg+xml;charset=utf-8'}));
        const a=document.createElement('a');a.href=url;a.download=`engine-cycle-${String(recordId || 'result').replace(/[^a-zA-Z0-9_-]/g,'_')}.svg`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
      }catch(error){onError?.(error.message);}finally{target.disabled=false;}return;
    }
    if(target.dataset.cyFit!=null)host.dataset.cyFit=host.dataset.cyFit==='true'?'false':'true';
    else if(target.dataset.cyMode!=null)mode=target.dataset.cyMode;
    else selected=target.dataset.cyObject;
    update();
  }
  const keydown=event=>{if(['Enter',' '].includes(event.key)&&event.target?.closest?.('[role="button"][data-cy-object]')){event.preventDefault();activate(event);}};
  host.addEventListener('click',activate);host.addEventListener('keydown',keydown);mounted.set(host,{click:activate,keydown});
}
