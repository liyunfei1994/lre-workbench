import assert from 'node:assert/strict';
import test from 'node:test';
import {cycleLayout,renderCycleWorkspace,mountCycleWorkspace,thrustChamberProfile} from '../webapp/static/cycle-schematic.js';
import {buildCycleModel} from '../webapp/static/cycle-model.js';
import {buildFlowModel} from '../webapp/static/flow-model.js';

const sample=(kind='ffsc')=>{
  const layout=cycleLayout({kind,nodes:[]});
  const nodes=Object.keys(layout.positions).map(id=>({id,title:id,type:id.includes('turbine')?'turbine':'pump',tone:'ox',states:[],componentId:id,metrics:[]}));
  return {kind,nodes,connections:Object.keys(layout.paths).map(key=>{const [source,target]=key.split('>');return {source,target,tone:'ox',label:key};}),shafts:layout.shafts,summary:[],notes:[],fuel:{name:'甲烷',pair:'液氧 / 甲烷'}};
};
test('every declared branch has visible endpoint objects in all three known layouts',()=>{
  for(const kind of ['ffsc','gg-single','gg-dual']){
    const model=sample(kind),layout=cycleLayout(model),html=renderCycleWorkspace(model);
    for(const {source,target} of model.connections){assert(layout.positions[source]);assert(layout.positions[target]);assert(html.includes(`data-cy-from="${source}" data-cy-to="${target}"`));}
    assert(!/NaN|Infinity|undefined/.test(html));assert(!/\sstyle=/.test(html));
  }
});

const bezier=(points,t)=>[0,1].map(axis=>(1-t)**3*points[0][axis]+3*(1-t)**2*t*points[1][axis]+3*(1-t)*t*t*points[2][axis]+t**3*points[3][axis]);
const tangent=(points,t)=>[0,1].map(axis=>3*((1-t)**2*(points[1][axis]-points[0][axis])+2*(1-t)*t*(points[2][axis]-points[1][axis])+t*t*(points[3][axis]-points[2][axis])));

test('Laval nozzle really converges to a smooth minimum throat then follows a bell expansion',()=>{
  const {convergent,divergent}=thrustChamberProfile();
  assert.deepEqual(convergent[3],divergent[0]);
  const upstream=Array.from({length:81},(_,i)=>bezier(convergent,i/80));
  const downstream=Array.from({length:81},(_,i)=>bezier(divergent,i/80));
  for(let i=1;i<upstream.length;i++){
    assert(upstream[i][0]<upstream[i-1][0],'Convergent radius must decrease throughout');
    assert(upstream[i][1]>upstream[i-1][1],'Contour must move downstream without folding');
    assert(downstream[i][0]>downstream[i-1][0],'Divergent radius must increase throughout');
    assert(downstream[i][1]>downstream[i-1][1],'Bell must move downstream without folding');
  }
  const throat=convergent[3][0];
  assert(throat>0 && throat<convergent[0][0] && throat<divergent[3][0]);
  const approach=tangent(convergent,1),departure=tangent(divergent,0);
  assert.equal(approach[0],0);assert.equal(departure[0],0);
  assert(approach[1]>0 && departure[1]>0,'Both sides share a downstream axial tangent at the throat');
  const middle=tangent(divergent,.5),exit=tangent(divergent,1);
  assert(exit[0]/exit[1]<middle[0]/middle[1],'Bell wall turns back toward the axis near the exit, unlike a cone');
});

test('main chamber and nozzle share one smooth interface in FFSC and both GG layouts',()=>{
  const profile=thrustChamberProfile();
  for(const kind of ['ffsc','gg-single','gg-dual']){
    const {positions}=cycleLayout(sample(kind)),chamber=positions.main_chamber,nozzle=positions.nozzle;
    assert.equal(chamber[0],nozzle[0]);
    assert.equal(chamber[1]+profile.chamber.bottom,nozzle[1]+profile.convergent[0][1]);
    assert.equal(profile.chamber.halfWidth,profile.convergent[0][0]);
    assert.equal(tangent(profile.convergent,0)[0],0,'The inlet tangent continues the straight chamber sidewall');
  }
});

test('rendered nozzle owns the complete contour while chamber and nozzle remain independent linked objects',()=>{
  const profile=thrustChamberProfile(),expected=[...profile.convergent,...profile.divergent.slice(1)];
  for(const kind of ['ffsc','gg-single','gg-dual']){
    const model=sample(kind),chamber=model.nodes.find(n=>n.id==='main_chamber'),nozzle=model.nodes.find(n=>n.id==='nozzle');
    chamber.type='chamber';nozzle.type='nozzle';
    chamber.states=[{id:'main_chamber',code:'C01',title:'主燃烧室',group:'chamber'}];
    const html=renderCycleWorkspace(model),path=/<path class="cy-flow-wall cy-nozzle-wall" d="([^"]+)"/.exec(html)?.[1];
    assert(path,'The rendered main nozzle includes its own continuous contour');
    const coordinates=path.match(/-?\d+(?:\.\d+)?/g).map(Number).slice(0,14);
    const rendered=Array.from({length:7},(_,i)=>coordinates.slice(i*2,i*2+2));
    assert.deepEqual(rendered,expected,'The rendered right wall uses both convergent and divergent curves');
    const chamberObject=html.split('data-cy-object="main_chamber"')[1].split('data-cy-object=')[0];
    const nozzleObject=html.split('data-cy-object="nozzle"')[1].split('</svg>')[0];
    assert(chamberObject.includes('cy-chamber-wall') && !chamberObject.includes('cy-nozzle-wall'));
    assert(nozzleObject.includes('cy-nozzle-wall') && nozzleObject.includes('喉部'));
    assert(!chamberObject.includes('cy-flame'));
    assert.match(chamberObject,/role="button" tabindex="0"/);
    assert.match(nozzleObject,/role="button" tabindex="0"/);
    assert.match(html,/data-flow-state="main_chamber"/);
    assert.match(html,/data-cy-component="main_chamber"/);
    assert.match(html,/data-cy-component="nozzle"/);
  }
});
test('FFSC diagram separates crossed feeds, two turbines and mechanical shafts',()=>{
  const model=sample(),layout=cycleLayout(model),html=renderCycleWorkspace(model);
  assert(layout.paths['cooling_channel>ox_preburner']);assert(!layout.paths['fuel_pump>ox_preburner']);
  assert(layout.paths['ox_preburner>ox_preburner_turbine']);assert(layout.paths['fuel_preburner>fuel_preburner_turbine']);
  assert.equal(layout.shafts.length,2);assert(layout.shafts.every(s=>s.source.endsWith('turbine') && s.target.endsWith('pump')));
  assert.match(html,/无圆点交叉不连接/);
});
test('GG layouts keep open exhaust separate from main chamber and dual branches parallel',()=>{
  for(const kind of ['gg-single','gg-dual']){
    const layout=cycleLayout(sample(kind));
    assert(layout.paths['fuel_pump>gas_generator']);assert(!layout.paths['cooling_channel>gas_generator']);
    assert(!Object.keys(layout.paths).some(k=>k.includes('turbine')&&k.endsWith('>main_chamber')));
    if(kind==='gg-dual'){
      assert(layout.paths['gas_generator>gg_turbine_ox']);assert(layout.paths['gas_generator>gg_turbine_fuel']);
      assert(!layout.paths['gg_turbine_ox>gg_turbine_fuel']);
    }
  }
});

test('GG annotations clear the pump readouts, parallel split and single-shaft return pipe',()=>{
  for(const kind of ['gg-single','gg-dual']){
    const model=sample(kind),gas=model.nodes.find(n=>n.id==='gas_generator');
    gas.type='preburner';gas.metrics=[{label:'出口压力',unit:'MPa',value:10.539},{label:'出口温度',unit:'K',value:852.697}];
    const html=renderCycleWorkspace(model);
    const object=html.split('data-cy-object="gas_generator"')[1].split('data-cy-object=')[0];
    assert.match(object,/data-cy-annotation="pt"><text x="780" y="440"/);
    assert.match(html,/class="cy-branch-label" data-cy-annotation="pt" x="405" y="310"/);
    assert.match(html,/class="cy-branch-label" data-cy-annotation="pt" x="775" y="345"/);
    assert.doesNotMatch(object,/data-cy-annotation="pt"><text x="560" y="477"/);
  }
});
test('turbine diagram pairs outlet pressure with outlet temperature, preserves missing outlet',()=>{
  const model=sample(); const n=model.nodes.find(n=>n.id==='ox_preburner_turbine');
  n.metrics=[{label:'入口压力',value:61,unit:'MPa'},{label:'出口压力',value:41,unit:'MPa'},{label:'出口温度',value:690,unit:'K'}];
  n.states=[{id:'inlet',code:'G01',title:'入口',group:'gas',pressure:61,temperature:750}];
  let html=renderCycleWorkspace(model);
  const annotation=()=>html.split('data-cy-object="ox_preburner_turbine"')[1].split('data-cy-object=')[0];
  assert.match(annotation(),/>41 <tspan>MPa/);assert.match(annotation(),/>K/);
  n.metrics[1].value=null;n.metrics[2].value=null;html=renderCycleWorkspace(model);
  assert.match(annotation(),/>— <tspan>MPa/);assert.doesNotMatch(annotation(),/>61 <tspan>MPa/);
});
test('record labels and component IDs are escaped; output never mutates the model',()=>{
  const model=sample('gg-single'),attack='<img src=x onerror="bad()">';
  model.nodes[0].title=attack;model.nodes[0].componentId=attack;model.connections[0].label=attack;
  const before=JSON.stringify(model),html=renderCycleWorkspace(model);
  assert(!html.includes(attack));assert(html.includes('&lt;img'));assert.equal(JSON.stringify(model),before);
});
test('old generic topology is usable without inventing shaft layouts',()=>{
  const model={kind:'generic',nodes:[{id:'one',title:'记录对象',type:'generic',metrics:[],states:[]}],connections:[],shafts:[],summary:[],notes:[]};
  assert.match(renderCycleWorkspace(model),/记录中的循环连接/);assert.equal(cycleLayout(model).shafts.length,0);
  assert.match(renderCycleWorkspace({...model,nodes:[]}),/没有流程节点数据/);
});

test('generic forward, backward and self edges route outside unrelated objects in separate lanes',()=>{
  const nodes=['A','B','C','D'].map(id=>({id,title:id,type:'generic',metrics:[],states:[]}));
  const connections=[['A','C'],['C','A'],['A','A'],['B','D']].map(([source,target])=>({source,target}));
  const model={kind:'generic',nodes,connections,shafts:[],summary:[],notes:[]};
  const layout=cycleLayout(model),lanes=[];
  for(const {source,target} of connections){
    const match=/^M([\d.]+) ([\d.]+)H([\d.]+)V([\d.]+)H([\d.]+)$/.exec(layout.paths[`${source}>${target}`]);
    assert(match,'Every generic edge has an explicit routed path');
    const [,startX,startY,lane,endY,endX]=match.map(Number);lanes.push(lane);
    assert.equal(startX,layout.positions[source][0]+75);
    assert.equal(endX,layout.positions[target][0]+75);
    for(const other of nodes.filter(n=>n.id!==source && n.id!==target)){
      const [x,y]=layout.positions[other.id];
      assert(lane>x+75,'Vertical lane stays outside unrelated symbols');
      assert(Math.abs(startY-y)>30 && Math.abs(endY-y)>30,'Horizontal segments stay outside unrelated symbols');
    }
    if(source===target)assert.notEqual(startY,endY,'Self edge has distinct entry and exit ports');
  }
  assert.equal(new Set(lanes).size,connections.length);
  assert.equal(new Set(Object.values(layout.positions).map(([x])=>x)).size,1);
  assert.match(renderCycleWorkspace(model),/data-cy-from="A" data-cy-to="C"/);
});

test('partial pump topology keeps its inlet link but never annotates it as outlet p or T',()=>{
  const result={calculation:{inputs:{cycle_type:'ffsc'},topology_nodes:[{key:'ox_pump',label:'液氧泵',pos:[100,100]}],
    topology_edges:[],states:{lox_pump_inlet:{pressure:950000,temperature:70,mass_flow:600}},components:{ox_pump:{outputs:{power_MW:37}}}}};
  const flow=buildFlowModel(result.calculation.states,{inputs:result.calculation.inputs,componentIds:['ox_pump']});
  const model=buildCycleModel(result,flow,[{id:'ox_pump'}]);
  assert.equal(model.kind,'generic');
  const html=renderCycleWorkspace(model);
  const annotations=[...html.matchAll(/<g class="cy-annotation"[^>]*>([\s\S]*?)<\/g>/g)].map(m=>m[1]).join('');
  assert.match(annotations,/>— <tspan>MPa/);
  assert.match(annotations,/· — <tspan>K/);
  assert.match(annotations,/ṁ — <tspan>kg\/s/);
  assert.doesNotMatch(annotations,/>0\.95 |· 70 |ṁ 600 /);
  assert.match(html,/data-flow-state="lox_pump_inlet"/);
  assert.match(html,/data-cy-component="ox_pump"/);
});

function element(dataset={}) {
  const classes=new Set();return {dataset,hidden:false,attributes:{},classList:{toggle(k,v){v?classes.add(k):classes.delete(k);},contains:k=>classes.has(k)},setAttribute(k,v){this.attributes[k]=v;},toggleAttribute(k,v){this.attributes[k]=v;}};
}
function hostFixture(){
  const nodes=[element({cyObject:'pump',cyStates:'in out'}),element({cyObject:'turbine',cyStates:'exhaust'})],panels=nodes.map(n=>element({cyPanel:n.dataset.cyObject}));
  const modes=['pt','flow','none'].map(mode=>element({cyMode:mode})),annotations=['pt','flow'].map(mode=>element({cyAnnotation:mode})),fit=element();
  const workspace=element();workspace.contains=()=>true;workspace.querySelector=()=>fit;
  const lists={'[data-cy-object]':nodes,'[data-cy-panel]':panels,'[data-cy-mode]':modes,'[data-cy-annotation]':annotations,'[data-cy-edge]':[]};
  workspace.querySelectorAll=sel=>lists[sel]||[];
  const host=element(),handlers=new Map();host.querySelector=()=>workspace;host.addEventListener=(k,f)=>handlers.set(k,f);host.removeEventListener=(k,f)=>{if(handlers.get(k)===f)handlers.delete(k);};
  const fire=(data,type='click')=>{const target=element(data);target.closest=()=>target;return handlers.get(type)?.({target,key:'Enter',preventDefault(){}});};
  return {host,nodes,panels,modes,annotations,workspace,handlers,fire};
}
test('selection, display mode, history reset, flow navigation and keyboard stay synchronized',async()=>{
  const f=hostFixture();let component=null;mountCycleWorkspace(f.host,'r1',{onComponent:id=>component=id});
  assert.equal(f.host.dataset.cySelected,'pump');await f.fire({cyObject:'turbine'},'keydown');assert.equal(f.host.dataset.cySelected,'turbine');assert.equal(f.panels[1].hidden,false);
  await f.fire({cyMode:'flow'});assert.equal(f.annotations[0].attributes.hidden,true);assert.equal(f.annotations[1].attributes.hidden,false);
  await f.fire({cyComponent:'turbine'});assert.equal(component,'turbine');
  mountCycleWorkspace(f.host,'r1');assert.equal(f.host.dataset.cySelected,'turbine');assert.equal(f.handlers.size,2);
  mountCycleWorkspace(f.host,'r2');assert.equal(f.host.dataset.cySelected,'pump');
  f.host.dataset.flowLocation='exhaust';f.host.dataset.flowLocationRecord='r2';mountCycleWorkspace(f.host,'r2');assert.equal(f.host.dataset.cySelected,'turbine');assert.equal(f.host.dataset.flowLocation,undefined);
  f.host.dataset.flowLocation='out';f.host.dataset.flowLocationRecord='old';mountCycleWorkspace(f.host,'r2');assert.equal(f.host.dataset.cySelected,'turbine');
});
