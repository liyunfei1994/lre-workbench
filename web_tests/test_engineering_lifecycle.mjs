import test from 'node:test';
import assert from 'node:assert/strict';
import {componentForLocation,componentPendingPatch,renderComponentContext,renderDesignRequirements,renderIntegration,mountEngineeringLifecycle} from '../webapp/static/engineering-lifecycle.js';
import {parameterGroups} from '../webapp/static/parameter-editor.js';
import {buildDesignReadiness} from '../webapp/static/design-readiness.js';
import {setupNodes,setupProgress} from '../webapp/static/setup-tree.js';
import {renderCoolingChannelInputs} from '../webapp/static/cooling-channel-inputs.js';

const pump={id:'pumps',label:'泵与轴系',description:'部件设计',groups:['pumps'],views:['pump_geometry'],impact:'component'};
const groups=[{id:'pumps',title:'泵初选',workflow_stage:'components',design_dependency:'sizing',fields:[{key:'speed',label:'转速',type:'number'},{key:'mode',virtual:true},{key:'identity',read_only:true}]},
 {id:'design',title:'总体目标',fields:[{key:'thrust'}]}];
const record={id:'source',revision:1,status:'succeeded',inputs:{speed:100,thrust:10}};
const model={iterations:[],baselines:[]};

test('component views and their parameter editors resolve one owner',()=>{
 const catalog={components:[pump]};
 assert.equal(componentForLocation(catalog,'overview','pumps'),pump);
 assert.equal(componentForLocation(catalog,'pump_geometry'),pump);
 assert.equal(componentForLocation(catalog,'results_overview','pumps'),null);
});
test('feedback contains only editable component keys and preserves other draft edits',()=>{
 const before={speed:100,thrust:10},after={speed:120,thrust:12,mode:'auto',identity:'x'};
 const copy=structuredClone(after);
 assert.deepEqual(componentPendingPatch(pump,groups,before,after),{speed:120});
 assert.deepEqual(after,copy);
 assert.deepEqual(componentPendingPatch(pump,groups,{speed:{a:1,b:2}},{speed:{b:2,a:1}}),{});
});
test('already saved component edits still offer source-based feedback',()=>{
 const html=renderComponentContext({component:pump,groups,views:{},project:{inputs:{speed:120}},draft:{speed:120},record,view:'overview'});
 assert.match(html,/保存本部件修改并进入整机复核（1 项）/);
 assert.doesNotMatch(html,/data-component-feedback="pumps" disabled/);
 assert.match(html,/常效率循环不会据此预测新的部件效率/);
});
test('restoring a source value is still an unsaved component edit',()=>{
 const saved={speed:120,thrust:10},draft={speed:100,thrust:12};
 assert.deepEqual(componentPendingPatch(pump,groups,record.inputs,draft,saved),{speed:100});
 const html=renderComponentContext({component:pump,groups,views:{},project:{inputs:saved},draft,record,view:'overview'});
 assert.doesNotMatch(html,/data-component-feedback="pumps" disabled/);
 const feedback={component_label:'泵与轴系',source_revision:1,changes:[{key:'speed',before:100,after:100}],previous_saved_values:{speed:120}};
 const ledger=renderIntegration({iterations:[{snapshot:feedback,target_revision:3,status:'awaiting_recompute'}],baselines:[]},{project:{revision:3}});
 assert.match(ledger,/120 → 100/);
 assert.match(ledger,/来源设计点：100/);
});
test('no source means editable drafts but no feedback submission',()=>{
 const html=renderComponentContext({component:pump,groups,views:{},project:{inputs:{speed:100}},draft:{speed:120},view:'overview'});
 assert.match(html,/data-component-feedback="pumps" disabled/);
 assert.match(html,/可以先编辑部件草案/);
});
test('detailed cooling dependencies return when switching from budgets without losing values',()=>{
 const all=[...groups,{id:'chamber_contour',workflow_stage:'components',design_dependency:'cooling',fields:[{key:'length',type:'number'}]}];
 const input={cooling_bartz_mode:0,speed:100,length:2,thrust:10};
 let active=parameterGroups(all,input);
 assert.ok(active.find(g=>g.id==='pumps').deferred_design);
 assert.ok(active.find(g=>g.id==='chamber_contour').deferred_design);
 const ready=buildDesignReadiness(active,[],{phase:'ready'});
 assert.equal(ready.groups.find(g=>g.id==='pumps').configurationLabel,'部件草案');
 assert.equal(setupProgress(setupNodes(active,ready),'pumps').label,'部件草案');
 const coupled=parameterGroups(all,{...input,cooling_bartz_mode:1});
 assert.equal(coupled.find(g=>g.id==='chamber_contour').deferred_design,false);
 assert.equal(input.length,2);
});
test('cooling component workspace permits geometry drafting while overall model uses budgets',()=>{
 const inputs={cooling_bartz_mode:0,cooling_channel_table_json:'',cooling_n_channels:300};
 const html=renderCoolingChannelInputs(inputs,{componentDraft:true});
 assert.match(html,/通道几何定义方式/);assert.match(html,/新建沿程表草稿/);
 assert.equal(inputs.cooling_bartz_mode,0);
});
test('requirements consume saved component identities and never substitute absent values',()=>{
 const saved={...record,result:{calculation:{components:{ox_pump:{outputs:{delta_p_Pa:2e6,power_W:1e6}},ox_preburner_turbine:{outputs:{turbine_power_W:2.5e6}}}}}};
 const html=renderDesignRequirements(saved);
 assert.match(html,/2\.5/);assert.match(html,/未记录/);assert.match(html,/来源：方案 v1/);
 assert.doesNotMatch(renderDesignRequirements(null),/2\.5/);
});
test('candidate display never promotes numerical execution to hardware verification',()=>{
 const baseline={id:'b',source_job_id:'source',integrity:true,snapshot:{payload:{source_revision:1},blockers:[{label:'泵特性',detail:'缺少性能图'}]}};
 const html=renderIntegration({iterations:[],baselines:[baseline]},{project:{id:'p',revision:2,inputs:{speed:120}},record,jobs:[record],dirty:true});
 assert.match(html,/候选基准/);assert.match(html,/尚未开放/);assert.match(html,/不混入当前草稿/);
 assert.match(html,/data-engineering-source/);assert.match(html,/缺少性能图/);
 assert.doesNotMatch(html,/data-run-baseline/);
});
test('record and parameter text is escaped',()=>{
 const html=renderComponentContext({component:{...pump,label:'<script>x</script>'},groups,views:{},project:{},draft:{},record:{...record,id:'<img src=x>'},view:'overview'});
 assert.doesNotMatch(html,/<script|<img/);assert.match(html,/&lt;script/);
});
test('feedback reviewed in a later combined run retains that distinction',()=>{
 const html=renderIntegration({iterations:[{status:'recomputed_with_changes',target_revision:2,review_job_id:'combined',snapshot:{component_label:'泵与轴系',source_revision:1,changes:[{key:'speed',before:100,after:120}]}}],baselines:[]},{project:{id:'p',revision:3,inputs:{}},record,jobs:[record]});
 assert.match(html,/已纳入后续整机复算 · 待工程校核/);
 assert.match(html,/data-engineering-record="combined"/);
});
test('completed but unconverged records remain diagnostic in component requirements',()=>{
 const failed={...record,result:{calculation:{summary:{solver_warning:1},components:{}}}};
 assert.match(renderDesignRequirements(failed),/仅供诊断和草案迭代/);
 assert.match(renderComponentContext({component:pump,groups,views:{},project:{},draft:{},record:failed,view:'overview'}),/不能作为已确认的部件设计依据/);
 const html=renderIntegration({iterations:[{status:'recomputed',target_revision:2,review_job_id:'failed',numerical_review:{status:'fail'},snapshot:{component_label:'泵',source_revision:1,changes:[]}}],baselines:[]},{project:{id:'p'},record:failed});
 assert.match(html,/数值未收敛/);
});
function host(){const listeners=new Map();return {innerHTML:'',listeners,contains:()=>true,addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:name=>listeners.delete(name)};}
test('late project responses never paint another project',async()=>{
 const h=host();let context={project:{id:'a'}};let resolve;
 const tool=mountEngineeringLifecycle(h,{api:()=>new Promise(r=>resolve=r),getContext:()=>context,notify(){}});
 const waiting=tool.refresh();context={project:{id:'b'}};tool.onLeave();resolve(model);await waiting;
 assert.doesNotMatch(h.innerHTML,/整机集成与基准确认/);tool.dispose();assert.equal(h.listeners.size,0);
});
test('opening a baseline source tolerates its own refresh but not a failed record selection',async()=>{
 const h=host(),opened=[];let context={project:{id:'p',inputs:{}},record:null},tool;
 tool=mountEngineeringLifecycle(h,{api:async()=>model,getContext:()=>context,notify(){},
   onSelectRecord:async id=>{context.record={...record,id};await tool.refresh();return true;},onOpenView:async view=>opened.push(view)});
 await tool.refresh();const button={disabled:false,dataset:{baselineRecord:'source',baselineView:'fixed_steady'},hasAttribute:()=>false};
 await h.listeners.get('click')({target:{closest:()=>button}});
 assert.deepEqual(opened,['fixed_steady']);tool.dispose();
});
