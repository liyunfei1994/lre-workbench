import assert from "node:assert/strict";
import {test} from "node:test";
import {buildNozzleCoordinateCsv,buildNozzleExportMetadata,inspectNozzleExport} from "../webapp/static/nozzle-export.js";

const geometry=()=>({available:true,export_allowed:true,consistency:{status:"pass"},
  source:{record_id:"job-14",kind:"saved_full_contour",label:"计算记录保存的完整内轮廓"},contour_sha256:"abc123",
  contour:{coordinate_origin:"injector_face",x_m:[0,.2,.4,.5,.7,1,1.5,2],r_m:[.2,.2,.14,.1,.15,.3,.4,.5]},
  dimensions:{throat_x_m:.5,chamber_length_m:.2}});
const numericRows=csv=>csv.trimEnd().split("\r\n").slice(1).map(line=>line.split(",").map(Number));

test("XYZ keeps every saved meridian point in SI with explicit headers and zero Z",()=>{
  const g=geometry(),csv=buildNozzleCoordinateCsv(g);
  assert.equal(csv.split("\r\n")[0],"x_m,y_m,z_m");
  assert.deepEqual(numericRows(csv),g.contour.x_m.map((x,i)=>[x,g.contour.r_m[i],0]));
  assert.ok(csv.endsWith("\r\n"));
});
test("millimetre and throat-origin conversion is reversible without resampling",()=>{
  const g=geometry();g.contour.x_m[1]=.200000000000007;
  const csv=buildNozzleCoordinateCsv(g,{unit:"mm",origin:"throat"}),rows=numericRows(csv);
  assert.equal(csv.split("\r\n")[0],"x_mm,y_mm,z_mm");assert.equal(rows.length,g.contour.x_m.length);
  assert.deepEqual(rows[3],[0,100,0]);assert.deepEqual(rows[0],[-500,200,0]);
  rows.forEach(([x,y,z],i)=>{assert.ok(Math.abs(x/1000+.5-g.contour.x_m[i])<1e-15);assert.ok(Math.abs(y/1000-g.contour.r_m[i])<1e-15);assert.equal(z,0);});
});
test("SI coordinates retain precision beyond display rounding",()=>{
  const g=geometry();g.contour.x_m[1]=.200000000000007;g.contour.r_m[1]=.200000000000003;
  assert.ok(buildNozzleCoordinateCsv(g).includes("0.200000000000007,0.200000000000003,0"));
});
test("annotated CSV identifies segments and exact boundary stations without extra points",()=>{
  const rows=buildNozzleCoordinateCsv(geometry(),{format:"annotated"}).trimEnd().split("\r\n");
  assert.equal(rows[0],"point_index,segment,landmark,x_m,y_m,z_m,coordinate_origin,source_record_id,source_kind,contour_sha256");
  assert.equal(rows.length,9);
  assert.equal(rows[1],"1,chamber,injector_face,0,0.2,0,injector_face,job-14,saved_full_contour,abc123");
  assert.match(rows[2],/^2,chamber,chamber_end,/);assert.match(rows[4],/^4,convergent,throat,/);
  assert.match(rows.at(-1),/^8,divergent,exit,/);
});
test("text provenance is CSV-quoted and cannot become a spreadsheet formula",()=>{
  const g=geometry();g.source.record_id=' =HYPERLINK("https://invalid")\r\nnext';g.source.kind='source,"saved"';
  const csv=buildNozzleCoordinateCsv(g,{format:"annotated"});
  assert.ok(csv.includes('"\' =HYPERLINK(""https://invalid"")\r\nnext"'));
  assert.ok(csv.includes('"source,""saved"""'));
  assert.deepEqual(numericRows(buildNozzleCoordinateCsv(g,{origin:"throat"}))[0],[-.5,.2,0]);
});
test("missing geometry or failed consistency never exports CAD coordinates",()=>{
  for(const g of [null,{}, {...geometry(),available:false}, {...geometry(),export_allowed:false}, {...geometry(),consistency:{status:"fail"}}]){
    assert.equal(inspectNozzleExport(g).valid,false);assert.throws(()=>buildNozzleCoordinateCsv(g));assert.throws(()=>buildNozzleExportMetadata(g));
  }
  const g=geometry();g.consistency.status="partial";assert.equal(inspectNozzleExport(g).valid,true);
  assert.equal(buildNozzleExportMetadata(g).consistency_status,"partial");
});
test("invalid points cannot silently be filtered, bridged or sorted",()=>{
  for(const [x,r] of [[[0,.2,.2,1],[.2,.2,.1,.3]],[[0,.4,.2,1],[.2,.2,.1,.3]],
    [[0,.2,.5,1],[.2,NaN,.1,.3]],[[0,.2,.5,1],[.2,0,.1,.3]],[[0,.2,.5,Infinity],[.2,.2,.1,.3]],
    [[0,.2,.5],[.2,.2,.1]],[[0,.2,.5,1],[.2,.2,.1]]]){
    const g=geometry();g.contour.x_m=x;g.contour.r_m=r;assert.equal(inspectNozzleExport(g).valid,false);
  }
});
test("unknown origins, unsupported options and absent throat location fail explicitly",()=>{
  const g=geometry();g.contour.coordinate_origin="unspecified";assert.throws(()=>buildNozzleCoordinateCsv(g),/原点/);
  for(const options of [{unit:"inch"},{origin:"exit"},{format:"stl"},null])assert.equal(inspectNozzleExport(geometry(),options).valid,false);
  const missing=geometry();delete missing.dimensions.throat_x_m;
  assert.equal(inspectNozzleExport(missing).valid,true);assert.throws(()=>buildNozzleCoordinateCsv(missing,{origin:"throat"}),/喉部位置/);
});
test("saved throat-origin meridians transform back to the injector frame",()=>{
  const g=geometry();g.contour.x_m=g.contour.x_m.map(x=>x-.5);g.contour.coordinate_origin="throat";g.dimensions.throat_x_m=0;
  const rows=numericRows(buildNozzleCoordinateCsv(g));
  assert.deepEqual(rows[0],[0,.2,0]);assert.deepEqual(rows[3],[.5,.1,0]);
  const info=buildNozzleExportMetadata(g);assert.equal(info.source_origin_offset_m,-.5);assert.equal(info.source_coordinate_origin,"throat");
});
test("conflicting origin or throat dimensions fail instead of shifting the wrong feature",()=>{
  const g=geometry();g.dimensions.throat_x_m=.7;assert.throws(()=>buildNozzleCoordinateCsv(g),/喉部位置/);
  g.dimensions.throat_x_m=.51;assert.throws(()=>buildNozzleCoordinateCsv(g),/喉部位置/);
  const shifted=geometry();shifted.contour.x_m=shifted.contour.x_m.map(x=>x+.1);assert.throws(()=>buildNozzleCoordinateCsv(shifted),/喷注面/);
});
test("companion metadata states units, transform, provenance and CAD interpretation",()=>{
  const metadata=buildNozzleExportMetadata(geometry(),{unit:"mm",origin:"throat"});
  assert.equal(metadata.rotation_axis,"x");assert.equal(metadata.radius_axis,"positive y");
  assert.equal(metadata.point_count,8);assert.equal(metadata.resampling,"none");assert.equal(metadata.unit_factor,1000);
  assert.equal(metadata.point_index_base,1);
  assert.equal(metadata.source.record_id,"job-14");assert.equal(metadata.contour_sha256,"abc123");
  assert.deepEqual(metadata.columns,["x_mm","y_mm","z_mm"]);assert.match(metadata.notes.join(" "),/半径，不是直径/);
  assert.equal(buildNozzleExportMetadata(geometry(),{format:"annotated"}).columns.join(","),
    buildNozzleCoordinateCsv(geometry(),{format:"annotated"}).split("\r\n")[0]);
  assert.equal(metadata.hash_scope,"original_saved_contour_in_metres");
  assert.doesNotThrow(()=>JSON.stringify(metadata));
});
test("export and metadata never mutate a frozen snapshot",()=>{
  const g=geometry(),before=structuredClone(g);Object.freeze(g.contour.x_m);Object.freeze(g.contour.r_m);Object.freeze(g.dimensions);Object.freeze(g.source);Object.freeze(g);
  buildNozzleCoordinateCsv(g,{format:"annotated",unit:"mm",origin:"throat"});buildNozzleExportMetadata(g);inspectNozzleExport(g);
  assert.deepEqual(g,before);
});
