import assert from "node:assert/strict";
import test from "node:test";
import {turbineStageMode,selectTurbineStageMode,parameterValue,parameterGroups,renderParameterGroups} from "../webapp/static/parameter-editor.js";
import {collectLocalReadiness} from "../webapp/static/design-readiness.js";

const key="turb_ox_n_stages_user";
const mode={key:"turb_ox_stage_mode",virtual:true,turbine_stages_key:key,label:"级数初选方式",
  choices:[{value:"estimated",label:"按现有规则估算"},{value:"specified",label:"给定初选级数"}]};
const groups=[{id:"turbines",fields:[mode,
  {key,label:"初选级数",default:0,type:"int",visibility:{all:[{key,op:"neq",value:0}]}}]}];
const render=inputs=>renderParameterGroups(parameterGroups(groups,inputs),inputs);

test("saved stage counts select the mode without rewriting inputs",()=>{
  for(const stages of [0,1,2,3]){
    const inputs={[key]:stages},before={...inputs};
    assert.equal(parameterValue(mode,inputs),stages===0?"estimated":"specified");
    assert.equal(render(inputs).includes(`data-input="${key}"`),stages!==0);
    assert.deepEqual(inputs,before);
  }
});

test("a fresh manual choice requires an explicit integer before submission",()=>{
  const inputs={[key]:0,turb_fuel_n_stages_user:2};
  const pending=selectTurbineStageMode(inputs,mode,"specified");
  assert.deepEqual(pending,{...inputs,[key]:null});
  assert.equal(turbineStageMode(mode,pending),"specified");
  assert.match(render(pending),/data-input="turb_ox_n_stages_user"/);
  assert.equal(collectLocalReadiness(groups,pending).issues.length,1);
  assert.equal(collectLocalReadiness(groups,{...pending,[key]:2}).issues.length,0);
  assert.deepEqual(selectTurbineStageMode(pending,mode,"estimated"),inputs);
  assert.ok(!Object.hasOwn(pending,mode.key));
});

test("mode switches preserve a remembered candidate without changing other assumptions",()=>{
  const inputs={[key]:3,turb_fuel_n_stages_user:2,turb_ox_reaction_degree:.45};
  const estimated=selectTurbineStageMode(inputs,mode,"estimated");
  assert.deepEqual(estimated,{...inputs,[key]:0});
  assert.deepEqual(selectTurbineStageMode(estimated,mode,"specified",3),inputs);
  assert.deepEqual(selectTurbineStageMode(inputs,mode,"specified",2),inputs);
  assert.equal(selectTurbineStageMode(estimated,mode,"specified",1.5)[key],null);
  assert.throws(()=>selectTurbineStageMode(inputs,mode,"invalid"));
  assert.throws(()=>selectTurbineStageMode(inputs,{...mode,turbine_stages_key:"other"},"estimated"));
});

test("invalid saved counts remain visible for correction",()=>{
  for(const value of [-1,1.5,undefined,"0",NaN]){
    const inputs={[key]:value};
    assert.equal(turbineStageMode(mode,inputs),"invalid");
    assert.match(render(inputs),/无效值：invalid/);
    assert.match(render(inputs),/data-input="turb_ox_n_stages_user"/);
  }
});
