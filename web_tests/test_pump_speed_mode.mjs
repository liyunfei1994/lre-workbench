import assert from "node:assert/strict";
import test from "node:test";
import {pumpSpeedMode,selectPumpSpeedMode,parameterValue,parameterGroups,renderParameterGroups} from "../webapp/static/parameter-editor.js";

const key="lox_pump_speed_rpm_override";
const mode={key:"lox_pump_speed_mode",virtual:true,pump_speed_key:key,label:"转速初选方式",
  choices:[{value:"specified",label:"给定初选转速"},{value:"estimated",label:"按目标比转速估算"}]};
const groups=[{id:"pumps",fields:[mode,
  {key,label:"初选转速",default:26500,visibility:{all:[{key,op:"neq",value:0}]}},
  {key:"lox_pump_specific_speed_target",label:"目标比转速",default:30,visibility:{all:[{key,op:"eq",value:0}]}}]}];
const render=inputs=>renderParameterGroups(parameterGroups(groups,inputs),inputs);

test("saved speed values determine the displayed mode without migrating the inputs",()=>{
  for(const rpm of [27123,0]){
    const inputs={[key]:rpm,lox_pump_specific_speed_target:32},before={...inputs};
    assert.equal(parameterValue(mode,inputs),rpm===0?"estimated":"specified");
    const html=render(inputs);
    assert.equal(html.includes(`data-input="${key}"`),rpm!==0);
    assert.equal(html.includes('data-input="lox_pump_specific_speed_target"'),rpm===0);
    assert.deepEqual(inputs,before);
  }
});

test("mode changes only update the canonical speed and preserve the inactive nq",()=>{
  const inputs={[key]:27123,lox_pump_specific_speed_target:32,fuel_pump_speed_rpm_override:39000};
  const estimated=selectPumpSpeedMode(inputs,mode,"estimated");
  assert.deepEqual(estimated,{...inputs,[key]:0});
  assert.deepEqual(selectPumpSpeedMode(estimated,mode,"specified",inputs[key]),inputs);
  assert.equal(selectPumpSpeedMode(inputs,mode,"specified",26500)[key],27123);
  assert.equal(inputs[key],27123);
  assert.ok(!Object.hasOwn(estimated,mode.key));
  assert.throws(()=>selectPumpSpeedMode(estimated,mode,"specified",0));
  assert.throws(()=>selectPumpSpeedMode(inputs,mode,"invalid",26500));
});

test("invalid saved speeds remain visible for correction and never masquerade as estimation",()=>{
  for(const rpm of [-1,null,undefined,"0",NaN]){
    const inputs={[key]:rpm};
    assert.equal(pumpSpeedMode(mode,inputs),"invalid");
    assert.match(render(inputs),/无效值：invalid/);
    assert.match(render(inputs),/data-input="lox_pump_speed_rpm_override"/);
    assert.doesNotMatch(render(inputs),/data-input="lox_pump_specific_speed_target"/);
  }
});
