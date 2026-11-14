import assert from "node:assert/strict";
import test from "node:test";
import {savedModelValidity,recordModelValidity,renderModelValidity} from "../webapp/static/model-validity.js";
import {renderAnalysis} from "../webapp/static/results.js";
import {buildValidationRequest,createValidationForm} from "../webapp/static/validation.js";

const model=version=>({eos:"Peng-Robinson-Boston-Mathias",eos_model:{version}});
const result=()=>({environment:{gas_model:model("pr_boston_mathias_v1")},calculation:{inputs:{},summary:{Isp_vac_s:374},states:{}}});
test("saved withdrawn model wins over a stale advisory without mutating numerical history",()=>{
  const record={id:"old",status:"succeeded",result:result(),model_validity:{reuse_blocked:false}},before=structuredClone(record);
  assert.equal(recordModelValidity(record).reuse_blocked,true);
  const html=renderAnalysis("log",record);
  assert.match(html,/物理模型已撤回 · 结果无效/);
  assert.match(html,/压力项被重复计入/);
  assert.match(html,/原始历史输出/);
  assert.equal(html.indexOf("物理模型已撤回")<html.indexOf("计算警告"),true);
  assert.deepEqual(record,before);
});
test("station-only and saved validation source evidence also flags withdrawn BM",()=>{
  assert.equal(savedModelValidity({calculation:{states:{port:{metadata:{model:model("pr_boston_mathias_v1")}}}}}).reuse_blocked,true);
  assert.equal(savedModelValidity({source:{environment:{gas_model:model("pr_boston_mathias_v1")}}}).reuse_blocked,true);
  assert.equal(savedModelValidity({environment:{gas_model:model("pr_boston_mathias_v2")}}).status,"not_listed_invalid");
  assert.equal(renderModelValidity({result:{}}),"");
});
test("new comparison and handoff requests reject invalid source regardless of completed status",()=>{
  const ctx={project:{id:"p"},record:{id:"old",project_id:"p",status:"succeeded",inputs:{},result:result()}};
  const form=createValidationForm(ctx),before=structuredClone(ctx);
  for(const tab of ["gas_eos_comparison","gas_handoff","conservation"])assert.throws(()=>buildValidationRequest(ctx,form,tab),/已撤回/);
  assert.deepEqual(ctx,before);
});
test("server-provided withdrawal reasons are escaped",()=>{
  const html=renderModelValidity({model_validity:{reuse_blocked:true,reason:"<img src=x onerror=attack()>"}});
  assert.doesNotMatch(html,/<img/);assert.match(html,/&lt;img/);
});
