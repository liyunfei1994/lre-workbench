import assert from "node:assert/strict";
import test from "node:test";
import {configuredPropertyUse, renderPropertyConfiguration, propertyRecordState, recordDatasetMatch,
  renderPropertyRecord, curveSegments, renderPropertyCurve} from "../webapp/static/property-library.js";

const project={id:"p1"};
const oldRecord={id:"r1",project_id:"p1",revision:1,result:{calculation:{thermodynamic_applicability:{
  thermochemical_data:{id:"gri30",sha256:"old-hash",cantera_version:"old-runtime"},
  states:[{id:"chamber",label:"主室",T_K:3400}],solid_wall_thermal:{status:"not_evaluated"},
},states:{chamber:{composition:{H2O:.7,CO2:.3}}}}}};

test("legacy evidence is shown without adding today's defaults or hiding saved composition",()=>{
  const before=structuredClone(oldRecord),html=renderPropertyRecord(oldRecord,project);
  assert.match(html,/old-runtime/);assert.match(html,/old-hash/);
  assert.match(html,/未保存详细数据快照/);assert.match(html,/文件名未记录/);
  assert.match(html,/H2O/);assert.match(html,/0.7/);assert.match(html,/部件草案/);
  assert.doesNotMatch(html,/gri30_highT|GRCop|Setzmann/);
  assert.equal(propertyRecordState(oldRecord,project).manifest,null);
  assert.deepEqual(oldRecord,before);
});

test("foreign records cannot supply evidence or node overlays for this project",()=>{
  const record={...oldRecord,project_id:"other"};
  assert.equal(propertyRecordState(record,project).available,false);
  assert.equal(recordDatasetMatch(record,project,{sha256:"old-hash"}),false);
  assert.doesNotMatch(renderPropertyRecord(record,project),/old-runtime|old-hash/);
  assert.equal(recordDatasetMatch(oldRecord,project,{sha256:"different"}),false);
  assert.equal(recordDatasetMatch(oldRecord,project,{sha256:"old-hash"}),true);
});

test("record snapshot keeps source species distinct from actual candidates and escapes metadata",()=>{
  const record=structuredClone(oldRecord),evidence=record.result.calculation.thermodynamic_applicability;
  evidence.data_manifest={schema_version:1,candidate_species:["H2O"],gas_dataset:{file:"saved.yaml",species_count:2,
    file_description:'<img src=x onerror="bad()">',source_url:"javascript:bad()",species:[
      {name:"H2O",thermo:{model:"NASA7","temperature-ranges":[200,6000]}},
      {name:"OH",thermo:{model:"NASA7","temperature-ranges":[200,6000]}}]},
    pure_fluids:[],wall_thermal:{use:"not_evaluated"}};
  const html=renderPropertyRecord(record,project);
  assert.match(html,/saved.yaml/);assert.match(html,/H2O<\/td><td>是/);assert.match(html,/OH<\/td><td>否/);
  assert.match(html,/&lt;img/);assert.doesNotMatch(html,/<img|href="javascript:/);
});

test("wall dataset is a dormant draft in conceptual or externally specified heat modes",()=>{
  for(const mode of [0,2])assert.match(configuredPropertyUse({cooling_bartz_mode:mode}).wallUse,/不求解壁面导热/);
  assert.match(configuredPropertyUse({cooling_bartz_mode:1}).wallUse,/详细冷却模型使用/);
  assert.match(configuredPropertyUse({}).wallUse,/不能推定/);
  const html=renderPropertyConfiguration({cooling_bartz_mode:2,methane_thermo_dataset:"gri30_highT",wall_thermal_dataset_id:"material-a"});
  assert.match(html,/Cantera/);assert.match(html,/HEOS/);assert.match(html,/material-a.*部件草案/);
});

test("plots preserve gaps and phase boundaries instead of fabricating connecting data",()=>{
  const rows=[{T_K:100,v:1,phase:"liquid",status:"available"},{T_K:110,v:2,phase:"liquid",status:"available"},
    {T_K:120,status:"unavailable"},{T_K:130,v:3,phase:"liquid",status:"available"},
    {T_K:140,v:4,phase:"gas",status:"available"},{T_K:150,v:5,phase:"gas",status:"available"}];
  assert.deepEqual(curveSegments(rows,"v").map(s=>s.map(p=>p.T_K)),[[100,110],[130],[140,150]]);
  const html=renderPropertyCurve({rows,units:{v:"J/kg"},definition:"test"},"v");
  assert.equal((html.match(/<polyline/g)||[]).length,2);assert.equal((html.match(/<circle/g)||[]).length,1);
  assert.match(html,/温度 \/ K/);assert.match(html,/J\/kg/);assert.doesNotMatch(html,/NaN|Infinity/);
});

test("zero and single-point curves remain visible and missing curves are labelled",()=>{
  const curve={rows:[{T_K:300,v:0,status:"available"}],units:{v:"J/mol"}};
  const html=renderPropertyCurve(curve,"v",[{label:"<script>",T_K:300}]);
  assert.match(html,/<circle/);assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/NaN|Infinity|<script>/);
  assert.match(renderPropertyCurve({rows:[{T_K:300,status:"unavailable"}]}),/没有可绘制/);
});
