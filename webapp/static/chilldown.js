// Pure-fluid thermal inventory bench. The end state is not an engine ignition clearance.
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const finite = value => typeof value === 'number' && Number.isFinite(value);
const fmt = value => finite(value) ? (value !== 0 && Math.abs(value) < .001 ? value.toExponential(3) : value.toLocaleString('zh-CN', {maximumSignificantDigits:6})) : '—';
const clone = value => JSON.parse(JSON.stringify(value));
const active = record => ['queued','running'].includes(record?.status);
const names = {queued:'排队中',running:'积分中',succeeded:'计算完成',cancelled:'已取消',failed:'执行失败',timed_out:'超时',interrupted:'服务中断',ready:'初态可建立',pass:'通过',fail:'未通过',review:'需复核',pending:'待完成',unknown:'未判定',met:'达到用户阈值',not_met:'未达到用户阈值',incomplete:'积分未完成'};
const phases = {liquid:'液相',gas:'气相',twophase:'气液两相',supercritical_liquid:'液体侧高压单相',supercritical_gas:'气体侧高温单相',supercritical:'超临界单相',unknown:'未判定'};
const stamp = value => value ? new Date(value * 1000).toLocaleString('zh-CN', {hour12:false}) : '—';
const badge = status => `<span class="cd-badge cd-${['pass','met','fail','failed','review','not_met','running'].includes(status) ? status : 'neutral'}">${esc(names[status] || status || '未判定')}</span>`;
const notice = (message, error = false) => `<div class="cd-notice ${error ? 'cd-error' : ''}">${esc(message)}</div>`;
const scaled = (value, factor = 1) => finite(value) ? value * factor : null;

export function chilldownDisplayDefaults(setup) {
  const result = clone(setup.defaults || {});
  for (const p of setup.parameters || []) if (finite(result[p.key])) result[p.key] = Number((result[p.key] * (p.factor || 1)).toPrecision(12));
  result.circuits = (result.circuits || []).map(c => {
    for (const p of setup.circuit_parameters || []) if (finite(c[p.key])) c[p.key] = Number((c[p.key] * (p.factor || 1)).toPrecision(12));
    return c;
  });
  result.selected_circuit_ids = result.circuits.map(c => c.id);
  return result;
}

export function buildChilldownRequest(source, setup, values) {
  if (!source?.id || source.status !== 'succeeded') throw Error('请选择已完成的稳态设计记录，以确定工质与参考边界。');
  if (!setup?.ready) throw Error('预冷研究台初始化检查未通过，请查看准备度说明。');
  const result = clone(setup.defaults);
  result.source_job_id = source.id;
  const read = (p, raw, fallback) => {
    if (raw === undefined) return fallback;
    if (raw === null || String(raw).trim() === '' || !Number.isFinite(Number(raw))) throw Error(`${p.label || p.key} 必须是有限数值。`);
    const value = Number((Number(raw) / (p.factor || 1)).toPrecision(14));
    if ((finite(p.min) && value < p.min) || (finite(p.max) && value > p.max)) throw Error(`${p.label || p.key} 超出允许范围。`);
    return value;
  };
  for (const p of setup.parameters || []) if (!p.readonly) result[p.key] = read(p, values[p.key], result[p.key]);
  if (!Number.isInteger(result.sample_count)) throw Error('输出采样点数必须为整数。');
  if (finite(result.command_time_s) && finite(result.command_ramp_s) && result.command_time_s + result.command_ramp_s >= result.duration_s) throw Error('阀门命令变化须在积分结束之前完成。');
  const selected = values.selected_circuit_ids ?? result.circuits.map(c => c.id);
  if (!Array.isArray(selected) || !selected.length || new Set(selected).size !== selected.length || selected.some(id => !result.circuits.some(c => c.id === id))) throw Error('请至少选择一个有效工质单元参与计算。');
  result.circuits = result.circuits.filter(c => selected.includes(c.id)).map(c => {
    const v = (values.circuits || []).find(row => row.id === c.id) || {};
    c.initial_mode = v.initial_mode ?? c.initial_mode;
    if (!['PT','PQ'].includes(c.initial_mode)) throw Error('初态定义必须选择压力–温度或压力–质量含气率。');
    for (const p of setup.circuit_parameters || []) {
      if (p.readonly || (p.key === 'initial_temperature_K' && c.initial_mode !== 'PT') || (p.key === 'initial_quality' && c.initial_mode !== 'PQ')) continue;
      c[p.key] = read(p, v[p.key], c[p.key]);
    }
    // Preserve the other mode's default only; edited inactive values never define the state.
    return c;
  });
  return result;
}

const series = (key, label, color, factor = 1, dashed = false) => ({key,label,color,factor,dashed});
export function chilldownPanels(result, circuitId) {
  const rows = (result?.samples || []).map(row => ({time_s:row.time_s, ...((row.circuits || []).find(c => c.id === circuitId) || {})}));
  const panels = [
    {name:'壁面与流体温度',unit:'K',hint:'壁温独立积分；两相均衡温度由库存状态确定。',series:[series('wall_temperature_K','壁面','#b97942'),series('temperature_K','流体','#267a9c')]},
    {name:'容腔绝对压力',unit:'MPa',hint:'有限体积中的质量与内能共同确定压力。',series:[series('pressure_Pa','容腔压力','#267a9c',1e-6)]},
    {name:'气液质量库存',unit:'kg',hint:'液相和气相质量相加得到容腔总质量。',series:[series('mass_kg','总质量','#5b7182',1,true),series('liquid_mass_kg','液相库存','#267a9c'),series('vapor_mass_kg','气相库存','#b97942')]},
    {name:'质量含气率与空隙率',unit:'%',hint:'质量含气率与气相体积分数不同；超临界无两相定义时不绘值。',series:[series('quality','质量含气率 x','#b97942',100),series('void_fraction','气相体积分数 α','#796a98',100,true)]},
    {name:'进出口质量流量',unit:'kg/s',hint:'按研究台边界定义；流向与阻塞判定见时间截面。',series:[series('inlet_mass_flow_kg_s','入口','#267a9c'),series('outlet_mass_flow_kg_s','出口','#b97942',1,true)]},
    {name:'壁面热交换',unit:'kW',hint:'壁面对流体、环境对壁面为正方向；负值表示热量反向。',series:[series('heat_to_fluid_W','壁面 → 流体','#b97942',1e-3),series('heat_from_ambient_W','环境 → 壁面','#796a98',1e-3,true)]},
    {name:'阀门指令与实际开度',unit:'%',hint:'执行机构开度与输入指令分别显示。',series:[series('inlet_command','入口指令','#8cafbf',100,true),series('inlet_opening','入口实际','#267a9c',100),series('outlet_command','出口指令','#d5ae86',100,true),series('outlet_opening','出口实际','#b97942',100)]},
    {name:'质量守恒账目',unit:'kg',hint:'库存变化与净输入质量积分逐工质对照。',series:[series('mass_balance_error_kg','质量闭合误差','#368270')]},
    {name:'流体与壁面总能量账目',unit:'J',hint:'流动焓、环境热输入与流体及壁面储能逐工质对照。',series:[series('energy_balance_error_J','能量闭合误差','#368270')]},
  ];
  return panels.map(panel => ({...panel,rows,series:panel.series.filter(s => rows.some(row => finite(row[s.key])))}));
}

function plot(panel, index = 0) {
  const w = 570, h = 270, left = 85, right = 25, top = 28, bottom = 45;
  const rows = panel.rows.filter(row => finite(row.time_s));
  const xs = rows.map(row => row.time_s), x0 = xs.length ? Math.min(...xs) : 0, x1 = xs.length ? Math.max(...xs) : 1;
  const values = panel.series.flatMap(s => rows.filter(row => finite(row[s.key])).map(row => row[s.key] * s.factor));
  const lo = values.length ? Math.min(...values) : 0, hi = values.length ? Math.max(...values) : 1;
  const padding = (hi - lo || Math.abs(hi) * .02 || 1) * .12, y0 = lo - padding, y1 = hi + padding;
  const x = value => left + (value - x0) / (x1 - x0 || 1) * (w - left - right);
  const y = value => top + (y1 - value) / (y1 - y0) * (h - top - bottom);
  const axes = Array.from({length:5}, (_, i) => {
    const q = i / 4, vx = x0 + q * (x1 - x0), vy = y0 + q * (y1 - y0);
    return `<line class="cd-grid" x1="${left}" x2="${w-right}" y1="${y(vy)}" y2="${y(vy)}"/><text class="cd-tick" x="${left-9}" y="${y(vy)+4}" text-anchor="end">${esc(fmt(vy))}</text><text class="cd-tick" x="${x(vx)}" y="${h-bottom+20}" text-anchor="middle">${esc(fmt(vx))}</text>`;
  }).join('');
  const paths = panel.series.map(s => {
    let path = '', pen = false;
    for (const row of rows) {
      if (!finite(row[s.key])) {pen = false; continue;}
      path += `${pen ? 'L' : 'M'}${x(row.time_s)},${y(row[s.key] * s.factor)} `; pen = true;
    }
    const points = rows.length === 1 && finite(rows[0]?.[s.key]) ? `<circle cx="${x(rows[0].time_s)}" cy="${y(rows[0][s.key]*s.factor)}" r="3" fill="${s.color}"/>` : '';
    return `<path data-series="${esc(s.key)}" class="cd-line" stroke="${s.color}" ${s.dashed ? 'stroke-dasharray="6 4"' : ''} d="${path}"><title>${esc(s.label)}</title></path>${points}`;
  }).join('');
  return `<article class="cd-chart"><div class="cd-chart-heading"><span>${String(index+1).padStart(2,'0')}</span><div><h3>${esc(panel.name)}</h3><p>${esc(panel.hint)}</p></div></div><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(panel.name)}，时间 / s，${esc(panel.unit)}"><title>${esc(panel.name)}</title>${axes}${y0 < 0 && y1 > 0 ? `<line class="cd-zero" x1="${left}" x2="${w-right}" y1="${y(0)}" y2="${y(0)}"/>` : ''}<path class="cd-axis" d="M${left},${top}V${h-bottom}H${w-right}"/><text class="cd-unit" x="${left}" y="15">${esc(panel.unit)}</text><text class="cd-unit" x="${w/2}" y="${h-4}" text-anchor="middle">时间 / s</text>${paths}${!values.length ? `<text class="cd-unit" x="${w/2}" y="${h/2}" text-anchor="middle">该量未定义或暂无有效数据</text>` : ''}</svg><div class="cd-legend">${panel.series.map(s => `<span><i style="border-top:${s.dashed?'dashed':'solid'} 2px ${s.color}"></i>${esc(s.label)}</span>`).join('')}</div></article>`;
}

function schematic(c = {}, transient = false) {
  const liquid = finite(c.liquid_volume_fraction) && c.liquid_volume_fraction >= 0 && c.liquid_volume_fraction <= 1 ? c.liquid_volume_fraction : null;
  return `<div class="cd-schematic" aria-label="纯工质预冷与充填研究边界"><div class="cd-boundary"><span>01 / 供给边界</span><b>入口状态与阀门</b><small>${transient ? `${fmt(c.inlet_mass_flow_kg_s)} kg/s` : '压力 · 温度 · 有效面积'}</small></div><span class="cd-flow">→</span><div class="cd-volume"><span>02 / 壁面–流体耦合</span><b>${transient ? esc(phases[c.phase] || c.phase || '相态未判定') : '有限容腔 + 壁面热惯性'}</b><small>${transient ? `流体 ${fmt(c.temperature_K)} K / 壁面 ${fmt(c.wall_temperature_K)} K` : '质量 · 内能 · 两相平衡'}</small><div class="cd-fraction" role="img" aria-label="${liquid === null ? '液相体积分数未定义' : `液相体积分数 ${fmt(liquid*100)}%`}">${liquid === null ? '<span class="cd-undefined">液相占比待计算 / 未定义</span>' : `<i style="width:${liquid*100}%"></i><span>液相 ${fmt(liquid*100)}% · 气相 ${fmt((1-liquid)*100)}%</span>`}</div></div><span class="cd-flow">→</span><div class="cd-boundary"><span>03 / 排放边界</span><b>出口状态与阀门</b><small>${transient ? `${fmt(c.outlet_mass_flow_kg_s)} kg/s` : '背压 · 排放 · 执行响应'}</small></div></div><p class="cd-reading">色条表示均相平衡体积分数，非空间液位；本模型不追踪液体前锋、分层或不凝气置换。</p>`;
}

const labels = {status:'检查状态',experiment_validated:'独立试验验证',mass_balance_error_kg:'质量误差 / kg',max_abs_mass_balance_error_kg:'最大质量误差 / kg',energy_balance_error_J:'能量误差 / J',max_abs_energy_balance_error_J:'最大总能量误差 / J',max_abs_fluid_energy_balance_error_J:'流体能量平衡最大绝对残差 / J',max_abs_wall_energy_balance_error_J:'壁面能量平衡最大绝对残差 / J',mass_relative_error:'相对质量误差',energy_relative_error:'相对能量误差',max_eos_density_relative_error:'状态方程回算 · 最大相对密度误差',max_eos_specific_energy_error_J_kg:'状态方程回算 · 最大比内能误差 / J·kg⁻¹',max_phase_volume_closure_relative_error:'两相体积 · 最大相对闭合误差',basis:'审计口径',ignition_ready:'整机点火就绪',wall_temperature_met:'壁温阈值',liquid_volume_met:'液相占比阈值',wall_temperature_K:'末态壁温 / K',liquid_volume_fraction:'末态液相体积分数',phase:'相态'};
function detailsTable(object) {
  if (!object || typeof object !== 'object') return notice('暂无检查数据。');
  return `<div class="cd-table"><table><thead><tr><th>检查项</th><th>结果 / 说明</th></tr></thead><tbody>${Object.entries(object).map(([key,value]) => `<tr><td>${esc(labels[key] || key)}</td><td>${key === 'status' ? badge(value) : value && typeof value === 'object' ? `<pre>${esc(JSON.stringify(value,null,2))}</pre>` : typeof value === 'boolean' ? (key === 'experiment_validated' ? (value ? '已完成' : '尚未完成') : key === 'ignition_ready' ? (value ? '需独立确认' : '不作此判定') : value ? '满足' : '未满足') : finite(value) ? fmt(value) : esc(value ?? '—')}</td></tr>`).join('')}</tbody></table></div>`;
}

function readinessTable(readiness) {
  const checks=readiness?.checks || [];
  if(!checks.length)return notice('尚无末态阈值检查结果。');
  const status=value=>typeof value==='boolean'?badge(value?'pass':'fail'):badge('unknown');
  return `<div class="cd-table"><table><thead><tr><th>预冷单元</th><th>检查量</th><th>末态值</th><th>用户判据</th><th>判定</th></tr></thead><tbody>${checks.map(item=>`<tr><td rowspan="2">${esc(item.label||item.id)}</td><td>金属壁温</td><td>${fmt(item.wall_temperature_K)} K</td><td>≤ ${fmt(item.target_wall_temperature_K)} K</td><td>${status(item.wall_temperature_met)}</td></tr><tr><td>液相体积分数</td><td>${fmt(scaled(item.liquid_volume_fraction,100))}%</td><td>≥ ${fmt(scaled(item.target_liquid_volume_fraction,100))}%</td><td>${status(item.liquid_volume_met)}</td></tr>`).join('')}</tbody></table></div>${readiness.basis?`<p class="cd-reading">${esc(readiness.basis)}</p>`:''}`;
}

export function renderChilldownResult(record, circuitId = 'ox', index = 0) {
  const result = record?.result, rows = result?.samples || [];
  if (!rows.length) return `<div class="cd-empty"><span>THERMAL INVENTORY / INITIAL STATE</span><h3>${active(record) ? '正在积分壁温与气液库存' : record ? '本记录暂无有效时间采样' : '先建立可检查的冷态初始条件'}</h3><p>从用户定义的初始气液状态出发，观察壁面冷却、库存变化及排放过程。计算结果将保留物性、输入和守恒审计。</p></div>`;
  const frozen = result.request || record.request || {}, ids = rows[0].circuits || [];
  const id = ids.some(c => c.id === circuitId) ? circuitId : ids[0]?.id;
  const row = rows[Math.min(Math.max(0,index),rows.length-1)], c = (row.circuits || []).find(item => item.id === id) || {};
  const descriptor = (frozen.circuits || []).find(item => item.id === id) || c;
  const fields = [['pressure_Pa','容腔绝压 / MPa',1e-6],['mass_kg','总库存 / kg',1],['liquid_mass_kg','液相库存 / kg',1],['vapor_mass_kg','气相库存 / kg',1],['quality','质量含气率 / %',100],['void_fraction','气相体积分数 / %',100],['density_kg_m3','平均密度 / kg·m⁻³',1],['heat_to_fluid_W','壁面对流体换热 / kW',1e-3]];
  const endpoint = result.endpoint_snapshot;
  return `<div class="cd-result-heading"><div><span>RESULT EXPLORER</span><h2>预冷与充填时间响应</h2><p>${rows.length} 个输出采样 · 来源与参数随记录冻结</p></div><div><button data-chill-action="csv">导出 CSV</button><button data-chill-action="svg">导出 SVG</button><button data-chill-action="snapshot" ${endpoint ? '' : 'disabled'}>导出末态快照</button></div></div><div class="cd-tabs" role="group" aria-label="查看结果工质">${ids.map(item => `<button data-chill-result-circuit="${esc(item.id)}" aria-pressed="${item.id===id}">${esc((frozen.circuits || []).find(d => d.id === item.id)?.label || item.label || item.id)}</button>`).join('')}</div>${result.complete === false ? notice('本记录只包含有效积分时间段；请先检查终止原因，再解释末态和曲线。') : ''}${result.termination_reason && result.termination_reason !== 'completed' ? notice(result.termination_reason) : ''}<section class="cd-sample"><div class="cd-section-heading"><div><h3>时间截面 · ${esc(descriptor.label || id)}</h3><p>移动时间轴查看物性、库存与边界流量</p></div><b class="cd-time">${fmt(row.time_s)} <small>s</small></b></div><label class="cd-time-slider">采样时刻：${fmt(row.time_s)} s<input data-chill-sample type="range" min="0" max="${rows.length-1}" value="${rows.indexOf(row)}" step="1" aria-label="预冷与充填时间截面"></label>${schematic(c,true)}<div class="cd-sample-grid">${fields.map(([key,label,factor]) => `<div><span>${label}</span><b>${fmt(scaled(c[key],factor))}</b></div>`).join('')}</div><p class="cd-reading">${esc(c.quality_basis || '两相区 x 表示气相质量分数；单相端点 0 / 1 仅用于气液库存记账。超临界状态下不以干度描述状态。')} ${c.inlet_choked === true ? '入口已达所用流动模型的阻塞条件。' : ''}${c.outlet_choked === true ? '出口已达所用流动模型的阻塞条件。' : ''}</p></section><div class="cd-chart-grid">${chilldownPanels(result,id).map(plot).join('')}</div><section class="cd-readiness"><div class="cd-section-heading"><div><h3>用户预冷与液相占比阈值</h3><p>用于本容腔末态筛选，不等于整机点火条件</p></div>${badge(result.readiness?.status || 'unknown')}</div>${readinessTable(result.readiness)}<p class="cd-reading">末态快照仅包含本研究容腔状态。连接供给网络、预燃室及点火模型前，仍需建立接口映射与全系统一致性检查。</p></section><details class="cd-group"><summary>质量与能量守恒审计</summary>${detailsTable(result.audits)}</details><details class="cd-group"><summary>冻结输入、模型方程与适用范围</summary><div class="cd-model"><p>${esc(result.model_description?.title || '纯工质均相平衡预冷与充填模型')}</p>${Array.isArray(result.model_description?.equations) ? `<div class="cd-equations">${result.model_description.equations.map(eq => `<code>${esc(eq)}</code>`).join('')}</div>` : ''}<ul>${(result.limitations || []).map(item => `<li>${esc(item)}</li>`).join('')}</ul><details><summary>完整模型与参数</summary><pre>${esc(JSON.stringify({model:result.model_description,request:frozen},null,2))}</pre></details></div></details>`;
}

export function buildChilldownCsv(result) {
  const rows = (result?.samples || []).map(row => {
    const flat = {time_s:row.time_s};
    for (const c of row.circuits || []) for (const [key,value] of Object.entries(c)) if (key !== 'id' && (finite(value) || typeof value === 'string' || typeof value === 'boolean' || value === null)) flat[`${c.id}.${key}`] = value;
    return flat;
  });
  const keys = [...new Set(rows.flatMap(row => Object.keys(row)))];
  const cell = value => `"${String(value ?? '').replace(/"/g,'""')}"`;
  return '\uFEFF' + [keys,...rows.map(row => keys.map(key => typeof row[key] === 'string' ? "'" + row[key] : row[key] ?? ''))].map(row => row.map(cell).join(',')).join('\r\n');
}

export function buildChilldownSvg(result, circuitId = 'ox') {
  const panels = chilldownPanels(result,circuitId), height = 120 + Math.ceil(panels.length/2)*350;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1220" height="${height}" viewBox="0 0 1220 ${height}"><style>text{font:11px Arial,'Microsoft YaHei',sans-serif;fill:#526c7c}.cd-grid{stroke:#e0e7ec}.cd-axis{stroke:#96a7b3;fill:none}.cd-line{fill:none;stroke-width:2}.cd-zero{stroke:#9a8aaa;stroke-dasharray:4 4}</style><rect width="1220" height="${height}" fill="white"/><text x="30" y="35" style="font-size:22px">预冷与充填分析 · ${esc(circuitId)}</text><text x="30" y="60">纯工质均相平衡研究 · 用户阈值不等于整机点火条件 · 来源 ${esc(result.source?.job_id || '—')}</text>${panels.map((panel,i) => {
    const svg = plot(panel,i).match(/<svg[\s\S]*?<\/svg>/)?.[0] || '';
    return `<g transform="translate(${25+i%2*600} ${85+Math.floor(i/2)*350})"><text x="10" y="20" style="font-size:15px">${esc(panel.name)}</text><svg x="0" y="30" width="570" height="270" viewBox="0 0 570 270">${svg.replace(/^<svg[^>]*>/,'').replace(/<\/svg>$/,'')}</svg>${panel.series.map((s,j) => `<text x="${15+j*138}" y="320" style="fill:${s.color}">${esc(s.label)}</text>`).join('')}</g>`;
  }).join('')}</svg>`;
}

export function buildChilldownSnapshot(record) {
  if (!record?.result?.endpoint_snapshot) throw Error('本记录尚未生成有效末态快照。');
  return {schema:'chilldown-subvolume-snapshot-v1',study_id:record.id,source:record.result.source,complete:record.result.complete,termination_reason:record.result.termination_reason,scope:'仅为预冷研究容腔末态，需映射与全系统一致性检查后才能用于后续模型初始化。',ignition_ready:false,endpoint_snapshot:clone(record.result.endpoint_snapshot),readiness:clone(record.result.readiness || {}),audits:clone(record.result.audits || {})};
}

export function chilldownRunBlockReason(source,setup,busy,count=0,error='') {
  if (!source) return '请选择已完成的设计记录，以确定工质与参考边界。';
  if (!setup) return error ? '初始化未完成，请检查连接后刷新。' : '正在读取预冷研究台初始化设置。';
  if (!setup.ready) return '研究台输入尚不完整，请处理上方初始化问题。';
  if (busy) return '正在提交任务，请稍候。';
  if (count >= 2) return '本项目已有 2 个等待或运行中的预冷研究，请等待完成或取消。';
  return '';
}

const groupTitles = {initial:'容腔与初始状态',inventory:'容腔与初始状态',wall:'壁面与换热',thermal:'壁面与换热',boundaries:'供给与排放边界',boundary:'供给与排放边界',control:'阀门执行机构',valves:'阀门执行机构',acceptance:'预冷完成阈值',readiness:'预冷完成阈值','容腔与初态':'容腔与初始状态','金属壁面':'壁面与换热','流动边界':'供给与排放边界','阀门执行器':'阀门执行机构','边界与判据':'适用边界与预冷阈值'};
function groupTitle(parameter) {
  if (parameter.group) return groupTitles[parameter.group] || parameter.group;
  if (/wall|ambient|heat_transfer|heat_capacity/.test(parameter.key)) return '壁面与换热';
  if (/target|criterion|threshold/.test(parameter.key)) return '预冷完成阈值';
  if (/opening|valve|command/.test(parameter.key)) return '阀门执行机构';
  if (/inlet|outlet|supply|back/.test(parameter.key)) return '供给与排放边界';
  return '容腔与初始状态';
}

export function mountChilldown(container,{api,getContext,notify=()=>{}}) {
  let projectId='',sourceId='',setup=null,values={},records=[],selected=null,circuitId='ox',resultCircuitId='ox',selectedIndex=0,error='',setupError='',busy=false,live=true,timer=null,seq=0;
  const openGroups = new Set(['容腔与初始状态']);
  const context = () => getContext() || {};
  const source = () => {const c=context();return c.record?.status==='succeeded' && c.record.project_id===c.project?.id ? c.record : null;};
  const stop = () => {if(timer!==null) clearTimeout(timer);timer=null;};
  function syncContext() {
    const p=context().project?.id || '', s=source()?.id || '';
    if (p!==projectId || s!==sourceId) {++seq;stop();if(p!==projectId){records=[];selected=null;selectedIndex=0;}projectId=p;sourceId=s;setup=null;values={};error='';setupError='';busy=false;}
  }
  const current = (token,p,s) => live && seq===token && context().project?.id===p && (source()?.id || '')===s;
  function field(p,v,cid='') {
    const factor=p.factor || 1;
    return `<label>${esc(p.label || p.key)}${p.unit && !String(p.label).includes(p.unit) ? ` / ${esc(p.unit)}` : ''}<input type="number" data-chill-field="${esc(p.key)}" ${cid?`data-chill-circuit="${esc(cid)}"`:''} value="${esc(v[p.key] ?? '')}" ${finite(p.min)?`min="${p.min*factor}"`:''} ${finite(p.max)?`max="${p.max*factor}"`:''} step="${finite(p.step)?p.step:'any'}" ${p.readonly?'readonly':''}><small>${esc(p.description || '')}</small></label>`;
  }
  function render() {
    const s=source(),ctx=context(),circuits=values.circuits || [],c=circuits.find(item=>item.id===circuitId) || circuits[0];
    // Editable validation/API errors never become an initialization prerequisite.
    const block=chilldownRunBlockReason(s,setup,busy,records.filter(active).length,setupError) || (setup?.ready && !values.selected_circuit_ids?.length ? '请至少勾选一个工质单元参与本次计算。' : '');
    const checks=Array.isArray(setup?.source_constraints)?setup.source_constraints:(setup?.source_constraints?.checks || []);
    const progress=selected?.progress || {},rows=selected?.result?.samples || [],maximum=selected?.request?.sample_count || 0;
    const params=(setup?.circuit_parameters || []).filter(p=>!(p.key==='initial_temperature_K' && c?.initial_mode==='PQ') && !(p.key==='initial_quality' && c?.initial_mode!=='PQ'));
    const groups=[...new Set(['容腔与初始状态',...params.map(groupTitle)])];
    container.innerHTML=`<section class="chilldown-workspace" aria-label="预冷与充填分析工作区"><div class="cd-heading"><div><span>CHILLDOWN / THERMAL INVENTORY</span><h2>预冷与充填分析</h2><p>壁面热惯性 · 真实流体状态 · 气液库存 · 末态快照</p></div><button data-chill-action="refresh">刷新记录</button></div><div class="cd-source"><b>参考记录</b><span>${s?`方案 v${s.revision} · ${stamp(s.created)} · ${esc(s.id)}`:'请选择已完成的稳态记录'}</span><span>工质与边界参考 / 冻结来源</span></div>${s&&(ctx.dirty||s.revision!==ctx.project?.revision)?notice('计算使用所选设计记录的冻结输入；左侧当前编辑参数不参与本研究台计算。'):''}${setupError?notice(setupError,true):''}${error?notice(error,true):''}<section class="cd-setup"><div class="cd-section-heading"><div><h3>纯工质热流体研究台</h3><p>有限容腔 + 集总壁面 · 使用用户给定的边界与换热参数</p></div>${badge(setup?.ready?'ready':setup?'review':'unknown')}</div>${schematic()}${notice('先验证容腔预冷与气液库存。均相平衡假设不分辨膜态沸腾、再润湿前沿和不凝气置换；换热参数应按具体结构与试验校核。')}${setup&&!setup.ready?`<ul class="cd-issues">${(setup.issues || []).map(item=>`<li>${esc(typeof item==='string'?item:JSON.stringify(item))}</li>`).join('')}</ul>`:''}${checks.length?`<details class="cd-source-checks"><summary>来源整机约束 · 仅作参考诊断</summary><div class="cd-table"><table><thead><tr><th>约束</th><th>原记录值</th><th>状态</th></tr></thead><tbody>${checks.map(item=>`<tr><td>${esc(item.label||item.key)}</td><td>${fmt(item.value)} ${esc(item.unit||'')}</td><td>${badge(item.status)}</td></tr>`).join('')}</tbody></table></div></details>`:''}<div class="cd-field-grid cd-runtime">${(setup?.parameters || []).map(p=>field(p,values)).join('')}</div><fieldset class="cd-run-circuits"><legend>运行工质</legend>${circuits.map(item=>`<label><input type="checkbox" data-chill-include="${esc(item.id)}" ${values.selected_circuit_ids?.includes(item.id)?'checked':''} ${busy?'disabled':''}>${esc(item.label||item.id)}</label>`).join('')}<small>勾选的单元参与本次计算；下方标签仅切换参数编辑。</small></fieldset><div class="cd-tabs" role="group" aria-label="编辑工质支路">${circuits.map(item=>`<button data-chill-edit-circuit="${esc(item.id)}" aria-pressed="${item.id===c?.id}">${esc(item.label||item.id)}</button>`).join('')}</div>${c?`<div class="cd-fluid"><span>工质 <b>${esc(c.fluid||'—')}</b></span><span>状态方程与输入边界随记录保存</span></div>${groups.map((group,i)=>`<details class="cd-group" data-chill-group="${esc(group)}" ${openGroups.has(group)?'open':''}><summary><span>${String(i+1).padStart(2,'0')}</span>${esc(group)}</summary><div class="cd-field-grid">${i===0?`<label>初始状态定义<select data-chill-mode="${esc(c.id)}"><option value="PT" ${c.initial_mode==='PT'?'selected':''}>P–T：压力与温度</option><option value="PQ" ${c.initial_mode==='PQ'?'selected':''}>P–x：压力与质量含气率</option></select><small>${c.initial_mode==='PQ'?'两相饱和初态由压力与气相质量分数确定，温度由状态方程给出。':'单相初态由绝对压力与温度确定；饱和线上请改用 P–x。'}</small></label>`:''}${params.filter(p=>groupTitle(p)===group).map(p=>field(p,c,c.id)).join('')}</div></details>`).join('')}`:''}<details class="cd-scope"><summary>模型边界与参数来源</summary><ul>${(setup?.limitations || []).map(item=>`<li>${esc(item)}</li>`).join('')}</ul></details><div class="cd-run-row"><p id="chilldown-run-reason" role="status">${esc(block||'运行独立工质容腔，保存冻结输入、有效时间段、守恒审计和末态快照。')}</p><button class="primary" data-chill-action="run" ${block?'disabled aria-describedby="chilldown-run-reason"':''}>${busy?'提交中…':'运行预冷与充填分析'}</button></div></section><section class="cd-output"><div class="cd-history"><label>预冷与充填记录<select data-chill-record><option value="">选择已保存的分析</option>${records.map(record=>`<option value="${esc(record.id)}" ${record.id===selected?.id?'selected':''}>v${record.revision} · ${stamp(record.created)} · ${esc(names[record.status]||record.status)}</option>`).join('')}</select></label><button data-chill-action="json" ${selected?'':'disabled'}>导出 JSON</button><button data-chill-action="cancel" ${active(selected)&&!busy?'':'disabled'}>取消分析</button></div>${selected?`<div class="cd-job-state">${badge(selected.status==='succeeded'&&selected.result?.complete===false?'review':selected.status)}<span>${esc(progress.phase||'')}</span><span>${rows.length} 个时间采样</span><small>记录 ${esc(selected.id.slice(0,8))}</small></div>`:''}${maximum?`<progress max="${maximum}" value="${Math.min(maximum,finite(progress.completed)?progress.completed:rows.length)}" aria-label="预冷积分进度"></progress>`:''}${selected?.error?notice(selected.error,true):''}${selected?.request?.source_job_id&&selected.request.source_job_id!==sourceId?notice('正在显示历史研究记录；结果与导出使用该记录的冻结参数，与上方新任务设置独立。'):''}${renderChilldownResult(selected,resultCircuitId,selectedIndex)}</section></section>`;
  }
  function schedule() {stop();if(live&&records.some(active))timer=setTimeout(()=>refresh(),1500);}
  async function refresh() {
    live=true;stop();syncContext();const p=projectId,s=sourceId,token=++seq;render();if(!p)return;
    try {
      const [list,config]=await Promise.all([api(`/projects/${encodeURIComponent(p)}/chilldown-studies`),s&&!setup?api(`/projects/${encodeURIComponent(p)}/chilldown-studies/setup?source_job_id=${encodeURIComponent(s)}`):Promise.resolve(setup)]);
      if(!current(token,p,s))return;
      if(config&&!setup){setup=config;values=chilldownDisplayDefaults(config);circuitId=values.circuits?.[0]?.id||'ox';}
      records=Array.isArray(list)?list:[];const next=records.find(record=>record.id===selected?.id)||records[0];
      if(next){const detail=await api(`/chilldown-studies/${encodeURIComponent(next.id)}`);if(!current(token,p,s))return;selected=detail;}else selected=null;
      error='';setupError='';
    } catch(e) {if(current(token,p,s)){if(!setup)setupError=e.message;else error=e.message;}}
    if(current(token,p,s)){render();schedule();}
  }
  async function run() {
    if(busy)return;syncContext();let request;
    try {const block=chilldownRunBlockReason(source(),setup,busy,records.filter(active).length,setupError);if(block)throw Error(block);request=buildChilldownRequest(source(),setup,values);}catch(e){error=e.message;render();return;}
    const p=projectId,s=sourceId,token=++seq;stop();busy=true;error='';render();
    try {const created=await api(`/projects/${encodeURIComponent(p)}/chilldown-studies`,'POST',request);if(!current(token,p,s))return;selected={...created,request,revision:source().revision,created:Date.now()/1000};records=[selected,...records];selectedIndex=0;notify('预冷与充填任务已提交。');}
    catch(e){if(current(token,p,s))error=e.message;}
    finally{if(current(token,p,s)){busy=false;render();schedule();}}
  }
  async function choose(id) {
    stop();const p=projectId,s=sourceId,token=++seq;
    if(!id){selected=null;render();schedule();return;}
    try{const detail=await api(`/chilldown-studies/${encodeURIComponent(id)}`);if(current(token,p,s)){selected=detail;selectedIndex=0;error='';render();schedule();}}
    catch(e){if(current(token,p,s)){error=e.message;render();schedule();}}
  }
  async function cancel() {
    if(!active(selected)||busy)return;const p=projectId,s=sourceId,token=++seq;stop();busy=true;render();
    try{await api(`/chilldown-studies/${encodeURIComponent(selected.id)}/cancel`,'POST',{});if(!current(token,p,s))return;busy=false;await refresh();}
    catch(e){if(current(token,p,s)){busy=false;error=e.message;render();schedule();}}
  }
  function download(format) {
    if(!selected||(format!=='json'&&!selected.result?.samples?.length))return;
    try{
      const data=format==='json'?JSON.stringify(selected,null,2):format==='snapshot'?JSON.stringify(buildChilldownSnapshot(selected),null,2):format==='csv'?buildChilldownCsv(selected.result):buildChilldownSvg(selected.result,resultCircuitId);
      const ext=format==='snapshot'?'json':format,type=ext==='json'?'application/json':ext==='csv'?'text/csv;charset=utf-8':'image/svg+xml;charset=utf-8';
      const url=URL.createObjectURL(new Blob([data],{type})),a=document.createElement('a');a.href=url;a.download=`chilldown-${format==='snapshot'?'endpoint-':'study-'}${selected.id}.${ext}`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }catch(e){error=e.message;render();}
  }
  container.addEventListener('input',e=>{const d=e.target.dataset||{};if(d.chillField){const target=d.chillCircuit?(values.circuits||[]).find(c=>c.id===d.chillCircuit):values;if(target)target[d.chillField]=e.target.value;}});
  container.addEventListener('change',e=>{
    const d=e.target.dataset||{};
    if(e.target.hasAttribute?.('data-chill-record'))choose(e.target.value);
    if(e.target.hasAttribute?.('data-chill-sample')){selectedIndex=Number(e.target.value);render();}
    if(d.chillMode){const c=(values.circuits||[]).find(item=>item.id===d.chillMode);if(c){c.initial_mode=e.target.value;openGroups.add('容腔与初始状态');render();}}
    if(d.chillInclude){const ids=new Set(values.selected_circuit_ids||[]);if(e.target.checked)ids.add(d.chillInclude);else ids.delete(d.chillInclude);values.selected_circuit_ids=[...ids];error='';render();}
  });
  container.addEventListener('toggle',e=>{const group=e.target.dataset?.chillGroup;if(group){if(e.target.open)openGroups.add(group);else openGroups.delete(group);}},true);
  container.addEventListener('click',e=>{
    const button=e.target.closest?.('button');if(!button||button.disabled)return;const action=button.dataset.chillAction;
    if(button.dataset.chillEditCircuit){circuitId=button.dataset.chillEditCircuit;render();}
    else if(button.dataset.chillResultCircuit){resultCircuitId=button.dataset.chillResultCircuit;render();}
    else if(action==='run')run();else if(action==='refresh')refresh();else if(action==='cancel')cancel();else if(['json','csv','svg','snapshot'].includes(action))download(action);
  });
  syncContext();render();return {refresh,sync(){if(live){syncContext();render();}},onLeave(){live=false;++seq;busy=false;stop();}};
}
