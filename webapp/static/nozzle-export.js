// CAD interchange of the saved gas-wall meridian. No interpolation or sizing.
const finite = value => typeof value === "number" && Number.isFinite(value);
const text = value => typeof value === "string" ? value : "";
const near = (a,b) => finite(a) && finite(b) && Math.abs(a-b)<=1e-10*Math.max(1,Math.abs(a),Math.abs(b));
const ORIGINS = new Set(["injector_face", "throat"]);
const xyzColumns = unit => [`x_${unit}`,`y_${unit}`,`z_${unit}`];
const csvColumns = (unit,format) => format==="xyz" ? xyzColumns(unit)
  : ["point_index","segment","landmark",...xyzColumns(unit),"coordinate_origin","source_record_id","source_kind","contour_sha256"];

function csvText(value) {
  let cell = String(value ?? "");
  // Quoting alone does not stop spreadsheet formula interpretation. This
  // applies only to descriptive strings; signed coordinates stay numeric.
  if (/^[\s\u0000-\u001f]*[=+\-@]/u.test(cell)) cell = "'" + cell;
  return /[",\r\n]/u.test(cell) ? `"${cell.replaceAll('"', '""')}"` : cell;
}

function exportData(geometry, options) {
  if (!geometry || geometry.available !== true)
    throw new Error(geometry?.reason || "本记录没有可导出的完整内轮廓。");
  if (geometry.export_allowed === false || geometry.consistency?.status === "fail")
    throw new Error("几何一致性检查未通过，不能导出用于建模的坐标。");
  if (!options || typeof options !== "object" || Array.isArray(options))
    throw new Error("坐标导出选项无效。");
  const unit = options.unit ?? "m", origin = options.origin ?? "injector_face", format = options.format ?? "xyz";
  if (!["m", "mm"].includes(unit)) throw new Error("坐标单位只能选择 m 或 mm。");
  if (!ORIGINS.has(origin)) throw new Error("坐标原点只能选择喷注面或喉部。");
  if (!["xyz", "annotated"].includes(format)) throw new Error("坐标格式只能选择 XYZ 或带分段信息的 CSV。");
  const contour = geometry.contour, x = contour?.x_m, r = contour?.r_m;
  if (!Array.isArray(x) || !Array.isArray(r) || x.length!==r.length || x.length<4)
    throw new Error("保存的完整内轮廓坐标缺失或点数不足。");
  if (x.some((v,i)=>!finite(v) || !finite(r[i]) || r[i]<=0 || (i>0 && v<=x[i-1])))
    throw new Error("轮廓坐标必须有限、半径为正，且沿轴向严格递增。");
  const sourceOrigin = contour.coordinate_origin;
  if (!ORIGINS.has(sourceOrigin)) throw new Error("记录未声明可识别的坐标原点，不能猜测坐标变换。");
  const d = geometry.dimensions || {};
  if (sourceOrigin==="injector_face" && !near(x[0],0))
    throw new Error("喷注面坐标与完整内轮廓起点不一致。");
  let throat = d.throat_x_m;
  if (sourceOrigin==="throat") {
    if (finite(throat) && !near(throat,0)) throw new Error("喉部原点与保存的喉部位置不一致。");
    throat = 0;
  }
  if (finite(throat)) {
    const i = x.findIndex(value=>near(value,throat));
    if (i<0 || !near(r[i],Math.min(...r)))
      throw new Error("保存的喉部位置与轮廓节点不一致。");
  } else if (origin==="throat") {
    throw new Error("本记录未保存有效喉部位置，不能导出喉部原点坐标。");
  }
  // Only complete meridians are accepted above. In a throat-origin record the
  // first saved station is still the injector face; never infer a new contour.
  const injector = x[0], offset = origin==="throat" ? throat : injector;
  const factor = unit==="mm" ? 1000 : 1;
  const cylinderEnd = finite(d.chamber_length_m) && d.chamber_length_m>0 ? injector+d.chamber_length_m : null;
  const points = x.map((axial,i)=>{
    const X=(axial-offset)*factor, Y=r[i]*factor;
    if (!finite(X) || !finite(Y)) throw new Error("单位转换后坐标超出数值范围。");
    let segment = "unclassified";
    if (finite(throat) && axial>throat && !near(axial,throat)) segment="divergent";
    else if (finite(cylinderEnd) && (axial<cylinderEnd || near(axial,cylinderEnd))) segment="chamber";
    else if (finite(cylinderEnd) && finite(throat) && axial<=throat) segment="convergent";
    const landmarks = [];
    if (i===0) landmarks.push("injector_face");
    if (finite(cylinderEnd) && near(axial,cylinderEnd)) landmarks.push("chamber_end");
    if (finite(throat) && near(axial,throat)) landmarks.push("throat");
    if (i===x.length-1) landmarks.push("exit");
    return {index:i+1,x:X,y:Y,z:0,segment,landmark:landmarks.join("|")};
  });
  return {unit,origin,format,sourceOrigin,offset_m:offset,factor,points,
    source:{record_id:text(geometry.source?.record_id),kind:text(geometry.source?.kind),
      label:text(geometry.source?.label)}, contour_sha256:text(geometry.contour_sha256),
    consistency_status:text(geometry.consistency?.status) || "unrecorded"};
}

/** Inspect export readiness without changing the immutable geometry snapshot. */
export function inspectNozzleExport(geometry, options={}) {
  try { return {valid:true,...exportData(geometry,options)}; }
  catch (error) { return {valid:false,reason:error.message}; }
}

/** XYZ is numeric-only after the header; annotated CSV carries provenance. */
export function buildNozzleCoordinateCsv(geometry, options={}) {
  const data=exportData(geometry,options),header=csvColumns(data.unit,data.format);
  if (data.format==="xyz")
    return [header.join(","),...data.points.map(p=>[p.x,p.y,p.z].join(","))].join("\r\n")+"\r\n";
  return [header.join(","),...data.points.map(p=>[
    p.index,csvText(p.segment),csvText(p.landmark),p.x,p.y,p.z,csvText(data.origin),
    csvText(data.source.record_id),csvText(data.source.kind),csvText(data.contour_sha256),
  ].join(","))].join("\r\n")+"\r\n";
}

/** Companion metadata for users importing numeric XYZ into a CAD package. */
export function buildNozzleExportMetadata(geometry, options={}) {
  const data=exportData(geometry,options);
  return {schema_version:1,geometry:"axisymmetric_gas_wall_meridian",format:data.format,
    columns:csvColumns(data.unit,data.format),unit:data.unit,
    coordinate_origin:data.origin,source_coordinate_origin:data.sourceOrigin,
    axis_direction:"positive x follows gas flow",radius_axis:"positive y",rotation_axis:"x",
    z_plane:0,source_origin_offset_m:data.offset_m,point_count:data.points.length,
    point_order:"injector face to nozzle exit",point_index_base:1,
    coordinate_transform:"x_export=(x_saved-source_origin_offset_m)*unit_factor; y_export=r_saved*unit_factor; z_export=0",
    unit_factor:data.factor,resampling:"none",source:data.source,contour_sha256:data.contour_sha256,
    consistency_status:data.consistency_status,hash_scope:"original_saved_contour_in_metres",
    notes:["坐标为保存的燃气侧内轮廓点；未插值、重采样或平滑。",
      "在 XY 平面按行顺序建立曲线，绕 X 轴旋转；Y 为半径，不是直径。",
      "此曲线不包含壁厚、冷却槽、外壳、入口/出口封口或计算域。",
      "CAD 拟合和网格划分后应复核喉径、出口径及原始点偏差。"],
    segment_definitions:{chamber:"燃烧室直筒段，含其下游端点",convergent:"收缩段，含喉部端点",
      divergent:"喉部下游膨胀段",unclassified:"缺少用于分段的保存尺寸"}};
}
