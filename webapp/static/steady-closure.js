// Render persisted model evidence only. This module never solves or reconstructs a network.
const finite = value => typeof value === "number" && Number.isFinite(value);
const nonnegative = value => finite(value) && value >= 0;
const object = value => value && typeof value === "object" && !Array.isArray(value) ? value : {};
const firstDrop = (...values) => values.find(nonnegative) ?? null;
const list = value => Array.isArray(value) ? value : [];
const objects = value => list(value).map(item=>item && typeof item === "object" && !Array.isArray(item) ? item : {});
const text = value => typeof value === "string" && value.trim().length > 0;
const escape = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
const fmt = value => !finite(value) ? "—" : value !== 0 && (Math.abs(value) < .001 || Math.abs(value) >= 1e6) ? value.toExponential(3) : value.toLocaleString("zh-CN", {maximumSignificantDigits:6});
const scaled = (value, factor) => finite(value) ? value * factor : null;
const signed = value => !finite(value) ? "—" : `${value > 0 ? "+" : ""}${fmt(value)}`;
const statusLabel = {pass:"闭合", fail:"未闭合", unavailable:"证据不足"};
const badge = (status, label) => `<span class="sc-badge sc-${["pass","fail"].includes(status) ? status : "unavailable"}">${escape(label || statusLabel[status] || statusLabel.unavailable)}</span>`;
const requiredBranchValues = ["mass_flow_kg_s","temperature_K","source_pressure_Pa","destination_pressure_Pa","line_drop_Pa","injector_drop_Pa","valve_drop_Pa","pressure_residual_Pa","mass_residual_kg_s"];
const locations = {fuel_after_cooling:"再生冷却出口",lox_after_pump:"液氧主泵出口",ox_preburner:"富氧预燃室",fuel_preburner:"富燃预燃室"};
const location = value => locations[value] || value || "—";
const lossSource = value => ({frozen_design_resistance:"冻结设计点阻力系数与当前密度",specified_design_pressure_budget:"设计输入的喷注压降比与管路压降"}[value] || value || "未记录来源");
const temperatureLocations = {
  source_tap:{heading:"取流温度",description:"温度对应取流点"},
  after_adiabatic_supply_loss:{heading:"供给损失后温度",description:"温度对应等焓供给损失后的预燃室入口"},
};

function branchState(branch, network = {}) {
  if (branch?.status === "fail") return "fail";
  if (!branch || requiredBranchValues.some(key => !finite(branch[key])) || !text(branch.source) || !text(branch.target)) return "unavailable";
  if (["mass_flow_kg_s","temperature_K","source_pressure_Pa","destination_pressure_Pa","line_drop_Pa","injector_drop_Pa"].some(key => branch[key] < 0)) return "fail";
  if (branch.valve_drop_Pa < -(nonnegative(network.pressure_tolerance_Pa) ? network.pressure_tolerance_Pa : 0)) return "fail";
  if ((nonnegative(network.pressure_tolerance_Pa) && Math.abs(branch.pressure_residual_Pa) > network.pressure_tolerance_Pa)
      || (nonnegative(network.mass_tolerance_kg_s) && Math.abs(branch.mass_residual_kg_s) > network.mass_tolerance_kg_s)) return "fail";
  return branch.status === "pass" ? "pass" : "unavailable";
}

function checkState(check) {
  if (check?.status === "fail") return "fail";
  if (!finite(check?.residual_W) || !nonnegative(check?.tolerance_W)) return "unavailable";
  if (Math.abs(check.residual_W) > check.tolerance_W) return "fail";
  return check.status === "pass" ? "pass" : "unavailable";
}

/** Conservative display guards: missing evidence must never become a numeric zero or pass. */
export function closureDisplayState(result = {}) {
  const closure = result.calculation?.steady_closure ?? result.steady_closure;
  if (closure?.schema_version !== 1) return {available:false, feed:"unavailable", energy:"unavailable", closure:null};
  const network=closure.feed_network || {}, branches=list(network.branches), energy=closure.energy_balance || {}, checks=list(energy.checks), terms=list(energy.terms);
  const branchStates=branches.map(branch=>branchState(branch,network)), checkStates=checks.map(checkState);
  const feedComplete=branches.length === 4 && branches.every(branch=>text(branch?.id)) && new Set(branches.map(branch=>branch.id)).size === 4
    && branchStates.every(status=>status === "pass") && nonnegative(network.max_pressure_residual_Pa) && nonnegative(network.max_mass_residual_kg_s);
  const failedNetworkResidual=(nonnegative(network.max_pressure_residual_Pa) && nonnegative(network.pressure_tolerance_Pa) && network.max_pressure_residual_Pa > network.pressure_tolerance_Pa)
    || (nonnegative(network.max_mass_residual_kg_s) && nonnegative(network.mass_tolerance_kg_s) && network.max_mass_residual_kg_s > network.mass_tolerance_kg_s);
  const feed=network.status === "fail" || branchStates.includes("fail") || failedNetworkResidual ? "fail" : network.status === "pass" && feedComplete ? "pass" : "unavailable";
  const energyComplete=text(energy.reference) && text(energy.boundary) && terms.length > 0 && terms.every(term=>finite(term?.value_W) && ["in","out","internal"].includes(term.role))
    && checks.length > 0 && checkStates.every(status=>status === "pass") && finite(energy.residual_W) && nonnegative(energy.tolerance_W);
  const failedResidual=finite(energy.residual_W) && nonnegative(energy.tolerance_W) && Math.abs(energy.residual_W) > energy.tolerance_W;
  const energyState=energy.status === "fail" || checkStates.includes("fail") || failedResidual ? "fail" : energy.status === "pass" && energyComplete ? "pass" : "unavailable";
  return {available:true, feed, energy:energyState, closure};
}

function table(headings, rows, className="") {
  return `<div class="sc-table-wrap ${className}"><table><thead><tr>${headings.map(heading=>`<th scope="col">${escape(heading)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
}
const cell = value => `<td>${escape(value)}</td>`;

function pressureLinkState(record, statusKey="status") {
  if (record[statusKey] === "fail") return "fail";
  if (!finite(record.pressure_residual_Pa) || !nonnegative(record.pressure_tolerance_Pa)) return "unavailable";
  if (finite(record.model_outlet_pressure_Pa) && finite(record.required_outlet_pressure_Pa)
      && Math.abs(record.model_outlet_pressure_Pa-record.required_outlet_pressure_Pa)>record.pressure_tolerance_Pa) return "fail";
  if (Math.abs(record.pressure_residual_Pa) > record.pressure_tolerance_Pa) return "fail";
  return record[statusKey] === "pass" ? "pass" : "unavailable";
}

function branchLinkState(record) {
  if (record.status === "fail") return "fail";
  const residuals=[record.source_state_residual_Pa,record.preburner_state_residual_Pa,record.branch_outlet_state_residual_Pa];
  if (residuals.some(value=>!finite(value)) || !nonnegative(record.max_pressure_residual_Pa) || !nonnegative(record.pressure_tolerance_Pa)) return "unavailable";
  if ([...residuals,record.max_pressure_residual_Pa].some(value=>Math.abs(value) > record.pressure_tolerance_Pa)) return "fail";
  return record.status === "pass" ? "pass" : "unavailable";
}

export function pressureContractDisplayState(contract) {
  if (contract?.schema_version !== 1) return "unavailable";
  const gasPaths=objects(contract.gas_paths), pumps=objects(contract.pump_capacity?.pumps), branchLinks=objects(contract.branch_state_links);
  const linkStates=[...gasPaths.map(path=>pressureLinkState(path)),...pumps.map(pump=>pressureLinkState(pump,"pressure_link_status")),...branchLinks.map(branchLinkState)];
  const uniqueCount=rows=>rows.every(row=>text(row.id)) ? new Set(rows.map(row=>row.id)).size : 0;
  const complete=gasPaths.length === 2 && uniqueCount(gasPaths) === 2 && pumps.length === 2 && uniqueCount(pumps) === 2 && branchLinks.length === 4 && uniqueCount(branchLinks) === 4;
  return contract.pressure_link_status === "fail" || linkStates.includes("fail") ? "fail" : contract.pressure_link_status === "pass" && complete && linkStates.every(status=>status === "pass") ? "pass" : "unavailable";
}

function pressureContract(network, result) {
  const contract=network.pressure_contract;
  if (contract?.schema_version !== 1) return '<p class="sc-notice sc-pressure-contract-missing">本记录未保存压力定义与部件能力证据；不从当前输入补算历史涡轮压比或泵能力。</p>';
  const convention=contract.pressure_convention || {}, capability=contract.pump_capacity || {};
  const pumps=objects(capability.pumps), gasPaths=objects(contract.gas_paths), roles=objects(contract.parameter_roles), branchLinks=objects(contract.branch_state_links);
  const pumpRows=pumps.map(pump=>`<tr>${cell(pump.label || "未命名泵")}${cell(fmt(scaled(pump.model_outlet_pressure_Pa,1e-6)))}${cell(fmt(scaled(pump.required_outlet_pressure_Pa,1e-6)))}${cell(fmt(scaled(pump.required_rise_Pa,1e-6)))}</tr>`);
  const gasRows=gasPaths.map(path=>`<tr>${cell(path.label || "未命名热气支路")}${cell(fmt(scaled(path.preburner_pressure_Pa,1e-6)))}${cell(fmt(scaled(path.turbine_inlet_pressure_Pa,1e-6)))}${cell(fmt(scaled(path.turbine_outlet_pressure_Pa,1e-6)))}${cell(fmt(scaled(path.chamber_pressure_Pa,1e-6)))}${cell(fmt(path.turbine_pressure_ratio))}${cell(fmt(path.preburner_chamber_pressure_ratio))}${cell(fmt(scaled(path.hot_gas_drop_Pa,1e-6)))}</tr>`);
  const roleLabels={design_budget:"设计压降预算",specified_design_budget:"给定压降预算",initial_guess:"数值初值",solved_result:"求解结果",fixed_resistance:"冻结阻力系数",model_assumption:"模型假设",inactive_with_frozen_resistance:"当前不生效（采用冻结阻力）",inactive_with_hydraulic_model:"当前不生效（采用水力模型）"};
  const evidenceLabels={unverified:"未核实实机数据",not_verified:"未核实实机数据",unverified_engineering_assumption:"工程假设；未核实实机数据",engineering_assumption:"工程假设",not_recorded:"未记录",model_result:"模型结果"};
  const roleRows=roles.map(role=>`<tr>${cell(role.label || role.key || "未命名参数")}${cell(role.unit === "Pa" ? `${fmt(scaled(role.value,1e-6))} MPa` : `${fmt(role.value)}${role.unit && role.unit !== "1" ? ` ${role.unit}` : ""}`)}${cell(roleLabels[role.role] || "身份未记录或未识别")}${cell(evidenceLabels[role.source_status] || "依据未记录或未识别")}${cell(role.detail || "未记录说明")}</tr>`);
  const linkStatus=pressureContractDisplayState(contract);
  const linkRows=[...gasPaths.map(path=>({record:path,statusKey:"status"})),...pumps.map(pump=>({record:pump,statusKey:"pressure_link_status"}))].map(({record,statusKey})=>`<tr>${cell(record.label || "未命名压力链")}${cell(signed(record.pressure_residual_Pa))}${cell(fmt(record.pressure_tolerance_Pa))}<td>${badge(pressureLinkState(record,statusKey))}</td>${cell(record.detail || "未记录")}</tr>`);
  const branchLinkRows=branchLinks.map(record=>`<tr>${cell(record.label || "未命名支路")}${cell(signed(record.source_state_residual_Pa))}${cell(signed(record.preburner_state_residual_Pa))}${cell(signed(record.branch_outlet_state_residual_Pa))}${cell(fmt(record.pressure_tolerance_Pa))}<td>${badge(branchLinkState(record))}</td></tr>`);
  const branchAudit=branchLinkRows.length ? `<p class="sc-description">四支路网络压力与保存流体状态的交叉核对；各差值均保留符号。</p>${table(["支路","取流点差 / Pa","预燃室差 / Pa","供给出口状态差 / Pa","容差 / Pa","状态"],branchLinkRows)}` : '<p class="sc-empty">四支路与流体状态的压力交叉核对未记录。</p>';
  const capacityLabel=capability.status === "not_verified" ? "固定泵能力未验证" : "固定泵能力结论未记录";
  const failedLinks=[...gasPaths.filter(path=>pressureLinkState(path)==="fail"),...pumps.filter(pump=>pressureLinkState(pump,"pressure_link_status")==="fail"),...branchLinks.filter(branch=>branchLinkState(branch)==="fail")];
  const staleCoolingRole=coolingEvidence(result).unified && roles.some(role=>role.key==="cooling_dp_pa" && role.role==="specified_design_budget");
  const conflict=failedLinks.length || staleCoolingRole ? `<p class="sc-notice sc-pressure-conflict"><strong>保存的压力证据需复核</strong>：${failedLinks.length ? `${failedLinks.map(record=>escape(record.label || "未命名压力链")).join("、")}存在压力关系差异。` : ""}${staleCoolingRole ? "本记录的冷却压降身份报告与统一沿程模型不一致。" : ""}模型出口压力与需求报告分别保留；历史记录未改写，请重新计算设计点更新报告。</p>` : "";
  return `<section class="sc-pressure-contract" aria-label="压力定义与部件能力"><div class="sc-section-heading"><div><h3>压力定义与部件能力</h3><p>${escape(convention.label || "压力口径未记录")}</p></div><div class="sc-pressure-status">${badge(linkStatus,linkStatus === "pass" ? "压力关系闭合" : linkStatus === "fail" ? "压力关系未闭合" : "压力关系证据不足")}${badge("unavailable",capacityLabel)}</div></div>${conflict}<h4>泵出口压力与需求记录</h4>${pumpRows.length ? table(["部件","模型出口压力 / MPa","保存所需出口压力 / MPa","保存所需压升 / MPa"],pumpRows) : '<p class="sc-empty">所需泵压未记录。</p>'}<p class="sc-notice">${escape(capability.detail || "求出所需供给压力，不等于证明固定泵能够提供该压力；本记录未保存部件能力验证依据。")}</p><details class="sc-diagnostics"><summary>查看热气压力链与压力定义</summary><p class="sc-description">${escape(convention.detail || "本记录未保存总压、静压及速度项的处理说明。")}</p><p class="sc-description">涡轮进出口压比 πT = pT,in / pT,out；预燃室 / 主室压力比 = pPB / pc。涡轮排气进入主室的热气压降使这两个比值不同；这里不表示液氧、液甲烷直接喷入主室。</p>${gasRows.length ? table(["热气支路","预燃室 / MPa","涡轮入口 / MPa","涡轮出口 / MPa","主室 / MPa","πT = pT,in/pT,out","pPB/pc","热气压降 / MPa"],gasRows) : '<p class="sc-empty">热气压力链未记录。</p>'}</details><details class="sc-diagnostics"><summary>查看压力关系残差与模型边界</summary><p class="sc-description">检查保存状态与指定压降预算是否一致；闭合不证明压降预算或固定部件能力已验证。</p>${linkRows.length ? table(["压力链","有符号残差 / Pa","容差 / Pa","状态","模型边界"],linkRows,"sc-source-table") : '<p class="sc-empty">泵与热气压力关系残差未记录。</p>'}${branchAudit}</details>${roleRows.length ? `<details class="sc-diagnostics"><summary>查看本次压降参数的身份与依据</summary>${table(["参数","保存值","数值身份","来源状态","含义与生效方式"],roleRows,"sc-source-table")}</details>` : '<p class="sc-empty">本次压降参数的身份与依据未记录。</p>'}</section>`;
}

function branchPressureNodes(branches) {
  const recorded=branches.filter(branch=>Array.isArray(branch.pressure_nodes) && branch.pressure_nodes.length);
  if (!recorded.length) return '<p class="sc-description">逐支路中间压力节点未记录；本页不补算历史节点。</p>';
  const rows=recorded.flatMap(branch=>objects(branch.pressure_nodes).map(node=>`<tr>${cell(branch.label || "未命名支路")}${cell(node.label || node.id || "未命名节点")}${cell(fmt(scaled(node.pressure_Pa,1e-6)))}</tr>`));
  return `<p class="sc-description">中间压力节点来自本次记录中的集总损失分配，不代表实物测点。</p>${table(["支路","压力节点","压力 / MPa"],rows)}`;
}

function feedNetwork(network = {}, state, result) {
  const branches=objects(network.branches), mode=network.mode === "design" ? "设计点支路匹配" : network.mode === "fixed_resistance" ? "固定支路阻力再匹配" : "未记录网络模式";
  const positions=new Set(branches.map(branch=>branch.temperature_location));
  const temperature=positions.size === 1 ? temperatureLocations[[...positions][0]] : null;
  const temperatureCell=branch=>temperature ? cell(fmt(branch.temperature_K)) : `<td>${fmt(branch.temperature_K)}<small class="sc-temperature-location">${escape(temperatureLocations[branch.temperature_location]?.heading || "温度位置未记录")}</small></td>`;
  const rows=branches.map(branch=>`<tr>${cell(branch.label || "未命名支路")}${cell(location(branch.source))}${cell(location(branch.target))}${cell(fmt(branch.mass_flow_kg_s))}${temperatureCell(branch)}${cell(fmt(scaled(branch.source_pressure_Pa,1e-6)))}${cell(fmt(scaled(branch.destination_pressure_Pa,1e-6)))}<td>${badge(branchState(branch,network))}</td></tr>`);
  const detailsRows=branches.map(branch=>`<tr>${cell(branch.label || "未命名支路")}${cell(fmt(scaled(branch.source_pressure_Pa,1e-6)))}${cell(fmt(scaled(branch.line_drop_Pa,1e-6)))}${cell(fmt(scaled(branch.injector_drop_Pa,1e-6)))}${cell(fmt(scaled(branch.valve_drop_Pa,1e-6)))}${cell(fmt(scaled(branch.destination_pressure_Pa,1e-6)))}${cell(signed(branch.pressure_residual_Pa))}${cell(signed(branch.mass_residual_kg_s))}${cell(lossSource(branch.loss_source))}</tr>`);
  return `${pressureContract(network,result)}<section class="sc-network" aria-label="四条供给支路"><div class="sc-section-heading"><div><h3>四条供给支路</h3><p>${escape(mode)} · ${escape(temperature?.description || "温度位置逐条注明，未记录时不推断")}</p></div>${badge(state)}</div>${rows.length ? table(["支路","取流点","目的地","流量 / kg·s⁻¹",`${temperature?.heading || "记录温度"} / K`,"取流压力 / MPa","目的压力 / MPa","支路状态"],rows) : '<p class="sc-empty">本记录未保存支路状态。</p>'}<div class="sc-metrics"><div><span>最大压力方程残差</span><strong>${fmt(network.max_pressure_residual_Pa)} <small>Pa</small></strong><span>容差 ${fmt(network.pressure_tolerance_Pa)} Pa</span></div><div><span>最大流量方程残差</span><strong>${fmt(network.max_mass_residual_kg_s)} <small>kg/s</small></strong><span>容差 ${fmt(network.mass_tolerance_kg_s)} kg/s</span></div></div>${network.pressure_reserve_status === "not_demonstrated" ? '<p class="sc-notice">当前按最低所需供给压头匹配。方程闭合未证明工程压力储备；附加调节阀压降是匹配结果，也不等同于泵的可用压头裕度。</p>' : ""}${network.mode === "fixed_resistance" ? '<p class="sc-description">冻结四条支路的阻力系数后重新匹配工况；缺少泵、涡轮特性图及阀门行程限制，尚不能据此确认完整固定硬件节流包线。</p>' : ""}${rows.length ? `<details class="sc-diagnostics"><summary>查看逐支路压力预算与守恒残差</summary><p class="sc-description">压力残差 = 取流压力 − 基础管路/全开阀损失 − 喷注损失 − 附加调节阀损失 − 目的压力。基础损失与附加调节损失分开记账，避免重复扣除。保留有符号残差，负值表示该压力预算存在缺口。附加阀压降必须非负；取流点上游的共用管路与冷却损失以取流压力体现，不在支路中重复扣除。</p>${table(["支路","取流 / MPa","基础管路/全开阀 / MPa","喷注 / MPa","附加调节阀 / MPa","目的 / MPa","压力残差 / Pa","流量残差 / kg·s⁻¹","损失来源"],detailsRows)}${branchPressureNodes(branches)}</details>` : ""}</section>`;
}

function energyLedger(energy = {}, state) {
  const terms=objects(energy.terms), checks=objects(energy.checks), roles={in:"输入边界",out:"输出边界",internal:"内部传递"};
  const termRows=terms.map(term=>`<tr>${cell(term.label || "未命名能量项")}${cell(roles[term.role] || "未记录归属")}${cell(signed(scaled(term.value_W,1e-6)))}</tr>`);
  const checkRows=checks.map(check=>`<tr>${cell(check.label || "未命名校核")}${cell(signed(check.residual_W))}${cell(fmt(check.tolerance_W))}<td>${badge(checkState(check))}</td></tr>`);
  const sourceRows=terms.map(term=>`<tr>${cell(term.label || "未命名能量项")}${cell(term.source || "未记录来源")}</tr>`);
  const failures=checks.filter(check=>checkState(check)==="fail");
  const failureSummary=failures.length ? `<p class="sc-notice sc-energy-failures"><strong>未闭合能量项</strong>：${failures.map(check=>`${escape(check.label || "未命名校核")}（残差 ${signed(check.residual_W)} W）`).join("、")}。分项判据见下方明细。</p>` : "";
  return `<section class="sc-energy" aria-label="能量收支与边界"><div class="sc-section-heading"><div><h3>能量收支</h3><p>按记录中的模型边界校核</p></div>${badge(state)}</div>${failureSummary}<dl class="sc-basis"><div><dt>校核边界</dt><dd>${escape(energy.boundary || "未记录")}</dd></div><div><dt>统一焓基准</dt><dd>${escape(energy.reference || "未记录")}</dd></div></dl><div class="sc-metrics"><div><span>边界能量残差</span><strong>${signed(energy.residual_W)} <small>W</small></strong></div><div><span>允许绝对残差</span><strong>${fmt(energy.tolerance_W)} <small>W</small></strong></div></div>${termRows.length ? `<details class="sc-diagnostics"><summary>查看能量收支明细（${termRows.length} 项）</summary>${table(["能量项","账目归属","功率 / MW"],termRows)}</details>` : '<p class="sc-empty">本记录未保存能量项，不能仅凭轴功率平衡判定能量闭合。</p>'}<p class="sc-description">内部传递项用于追踪热量和功率流向，不重复计为边界净输入。焓值可因统一参考基准而为负。</p>${checkRows.length || sourceRows.length ? `<details class="sc-diagnostics"><summary>查看分项残差与数据来源</summary>${checkRows.length ? table(["校核项","有符号残差 / W","容差 / W","状态"],checkRows) : '<p class="sc-empty">未记录分项残差。</p>'}${sourceRows.length ? table(["能量项","数据来源"],sourceRows,"sc-source-table") : ""}</details>` : ""}${list(energy.limitations).length ? `<div class="sc-notice"><strong>适用范围</strong><ul>${energy.limitations.map(item=>`<li>${escape(item)}</li>`).join("")}</ul></div>` : ""}</section>`;
}

function coolingEvidence(result = {}) {
  const calculation=object(result.calculation ?? result), inputs=object(calculation.inputs), summary=object(calculation.summary);
  const component=object(object(calculation.components).cooling_channel), outputs=object(component.outputs), profile=object(component.profile);
  const hasProfile=Object.keys(profile).length>0, unified=profile.version===1 && profile.model_id==="regen_quasi1d_ph_v1";
  const profileDrop=scaled(object(profile.summary).dp_total_Pa,1e-6);
  const componentDrop=firstDrop(scaled(outputs.dp_total_Pa,1e-6),outputs.delta_P_MPa);
  const value=unified ? firstDrop(profileDrop,componentDrop) : firstDrop(componentDrop,summary.cooling_dp_MPa);
  const recordedSource=outputs.delta_P_source, legacyFlag=inputs.cooling_dp_auto;
  const automatic=!hasProfile && (recordedSource===1 || (recordedSource===undefined && (legacyFlag===true || (finite(legacyFlag) && legacyFlag>0))));
  const prescribed=!hasProfile && (recordedSource===0 || (recordedSource===undefined && (legacyFlag===false || legacyFlag===0)));
  const dropMismatch=unified && nonnegative(profileDrop) && nonnegative(componentDrop) && Math.abs(profileDrop-componentDrop)>1e-6;
  return {unified,hasProfile,automatic,prescribed,value,dropMismatch,outputs};
}

function coolingHydraulics(result) {
  const {unified,hasProfile,automatic,prescribed,value,dropMismatch,outputs}=coolingEvidence(result);
  const source=unified ? "统一准一维沿程解" : automatic ? "水力迭代计算" : prescribed ? "给定压降，未证明与通道几何一致" : "未知";
  const detail=unified ? "吸热量、压降与出口状态来自同一冷却剖面；给定压降预算与旧兼容开关不控制该模式。模型计算不等于部件试验验证。"
    : automatic ? "根据本次组件记录或历史水力标记识别；是否完成迭代与守恒闭合，仍以本次记录的对应判据为准。"
    : prescribed ? "供给网络采用给定压降预算；本记录不提供由通道几何求解的沿程水力证据。"
    : hasProfile ? "本记录包含未识别版本的沿程数据，不根据旧输入开关推断当前模型身份。"
    : "本记录未保存可识别的冷却压降计算方式，不推断其水力来源。";
  const estimate=!hasProfile && nonnegative(outputs.delta_P_hydraulic_MPa) ? outputs.delta_P_hydraulic_MPa : null;
  const comparison=estimate !== null ? ` 同工况通道几何压降估算：${fmt(estimate)} MPa${value !== null ? `（估算 − 采用值：${signed(estimate-value)} MPa）` : ""}。该估算用于一致性复核，不代表已验证的正确压降。` : "";
  const conflict=dropMismatch ? '<strong>保存的剖面与组件压降不一致，请重新计算核查；历史数据未改写。</strong>' : "";
  return `<p class="sc-notice sc-cooling-hydraulics"><strong>冷却水力来源：${source}</strong><br>本次记录冷却压降：${fmt(value)} MPa。${detail}${comparison}${conflict}</p>`;
}

export function renderSteadyClosure(result = {}) {
  const state=closureDisplayState(result), closure=state.closure;
  const hydraulics=coolingHydraulics(result);
  const header='<div class="sc-heading"><div><span class="sc-kicker">FEED NETWORK &amp; ENERGY BALANCE</span><h2>供给网络与能量闭合</h2><p>流路、压力预算与能量收支来自本次计算记录；分别保留数值收敛和物理闭合结论。</p></div></div>';
  if (!state.available) return `<section class="panel steady-closure-workspace" aria-label="供给网络与能量闭合">${header}${hydraulics}<p class="sc-notice">本记录未保存可识别的供给网络与能量闭合证据。缺失数据不作为零值或通过；历史结果保持原样。</p></section>`;
  const assumptions=list(closure.assumptions);
  return `<section class="panel steady-closure-workspace" aria-label="供给网络与能量闭合">${header}${hydraulics}<p class="sc-scope">以下为集总循环模型的方程闭合检查；不能据此确认实际发动机流路或整机试验性能。</p>${assumptions.length ? `<details class="sc-assumptions"><summary>本次流路与热量模型假设（${assumptions.length} 项）</summary><ul>${assumptions.map(item=>`<li>${escape(item)}</li>`).join("")}</ul></details>` : '<p class="sc-notice">本记录未注明流路与热量模型假设。</p>'}${feedNetwork(closure.feed_network || {},state.feed,result)}${energyLedger(closure.energy_balance || {},state.energy)}</section>`;
}
