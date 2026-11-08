import assert from "node:assert/strict";
import test from "node:test";
import {mergeInputDefaults,renderParameterField,parameterLayoutDependency} from "../webapp/static/parameter-editor.js";

const modern = {chamber_inner_wall_material:"grcop42",wall_thermal_dataset_id:"grcop42_chen2023_lpbf_v1",wall_manufacturing_state:"unspecified"};
const oldDataset = "grcop84_ellis2000_extruded_high_fe_v1";

test("new custom drafts use 42 while persisted missing material identity keeps 84",()=>{
  assert.deepEqual(mergeInputDefaults(modern,{}),modern);
  const old=Object.freeze({pc:33e6});
  const loaded=mergeInputDefaults(modern,old,true);
  assert.equal(loaded.chamber_inner_wall_material,"grcop84");
  assert.equal(loaded.wall_thermal_dataset_id,oldDataset);
  assert.equal(loaded.wall_manufacturing_state,"unspecified");
  assert.deepEqual(old,{pc:33e6});
});

test("explicit saved materials, dataset and zones survive the new default",()=>{
  const zones='[{"x_start_m":0.1,"x_end_m":0.2,"dataset_id":"grcop84_ellis2000_extruded_high_fe_v1"}]';
  const old={chamber_inner_wall_material:"grcop84",wall_thermal_dataset_id:oldDataset,wall_material_zones_json:zones,wall_manufacturing_state:"powder_metallurgy_extruded_high_fe"};
  assert.deepEqual(mergeInputDefaults(modern,old,true),old);
  assert.deepEqual(mergeInputDefaults(modern,modern,true),modern);
  assert.equal(mergeInputDefaults(modern,{chamber_inner_wall_material:"grcop42"},true).wall_thermal_dataset_id,modern.wall_thermal_dataset_id);
  assert.equal(mergeInputDefaults(modern,{chamber_inner_wall_material:"cuzr"},true).wall_thermal_dataset_id,"cuzr_qi2024_heat_treated_v1");
});

test("a saved alloy/dataset conflict stays visible and is never silently repaired",()=>{
  const field={key:"wall_thermal_dataset_id",label:"内衬热物性",default:modern.wall_thermal_dataset_id,type:"select",
    choices:[{value:modern.wall_thermal_dataset_id,label:"GRCop-42"}],
    dataset_catalog:[{dataset_id:modern.wall_thermal_dataset_id,material_id:"grcop42",conductivity_property:{range_K:[298.15,973.15]}}]};
  const old=Object.freeze({...modern,chamber_inner_wall_material:"grcop84"});
  const markup=renderParameterField(field,old);
  assert.match(markup,/role="alert"/);
  assert.match(markup,/内衬材料与默认热物性数据集不一致/);
  assert.match(markup,/grcop84/);assert.match(markup,/grcop42/);
  assert.equal(old.chamber_inner_wall_material,"grcop84");
  assert.doesNotMatch(renderParameterField(field,modern),/role="alert"/);
  assert.equal(parameterLayoutDependency("chamber_inner_wall_material",[]),true);
});
