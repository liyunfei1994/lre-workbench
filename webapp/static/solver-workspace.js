// A read-only description of the current task, never a reconstructed result.
const esc=value=>String(value??"").replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':'&quot;'}[ch]));
const finite=value=>typeof value==="number"&&Number.isFinite(value);
const fmt=value=>finite(value)?new Intl.NumberFormat("zh-CN",{maximumSignificantDigits:6,notation:value!==0&&Math.abs(value)<.001?"scientific":"standard"}).format(value):"未设置";
const link=(group,label)=>`<button type="button" data-task-group="${esc(group)}">${esc(label)} →</button>`;
export const isSolverGroup=id=>["solver","initialization","thermo"].includes(id);

export function renderSolverTask({draft={},groups=[],solverControls={},compact=false}={}){
  const temperature=Number(draft.use_target_preburner_temp??1)>=.5;
  const mixture=temperature||Number(draft.use_target_preburner_mr??1)>=.5;
  const spatialCooling=Boolean(draft.cooling_bartz_mode);
  const conceptCooling=Number(draft.cooling_bartz_mode)===2;
  let valves=false;
  try{valves=Boolean(JSON.parse(draft.valve_configuration_json||"null")?.enabled);}catch{}
  const physical=[
    ["目标推力",`${fmt(draft.thrust)} N`], ["指定主室压力",`${fmt(finite(draft.pc)?draft.pc/1e6:null)} MPa`],
    ["整机氧燃质量比",fmt(draft.of_ratio)], ["环境绝对压力",`${fmt(finite(draft.ambient_pressure)?draft.ambient_pressure/1e6:null)} MPa`],
    ["预燃室设计方式",temperature?"指定温度，反求混合比":mixture?"指定混合比，计算温度":"历史自由分流模式"],
    ["冷却模型",conceptCooling?"概略热负荷估算":spatialCooling?"详细通道耦合":"外部指定吸热与压降"],
    ...(conceptCooling?[["热面参考壁温 · 估算假设",`${fmt(draft.cooling_reference_wall_temp_K)} K`],
      ["冷却压降估计",`${fmt(finite(draft.cooling_estimated_dp_pa)?draft.cooling_estimated_dp_pa/1e6:null)} MPa`]]:[]),
    ...(temperature?[["富氧／富燃目标温度",`${fmt(draft.ox_preburner_temp_target)} / ${fmt(draft.fuel_preburner_temp_target)} K`]]:
      mixture?[["富氧／富燃指定氧燃比",`${fmt(draft.ox_preburner_mr_target)} / ${fmt(draft.fuel_preburner_mr_target)}`]]:[]),
  ];
  const method=groups.flatMap(group=>group.fields||[]).find(field=>field.key==="nonlinear_solver");
  const methodLabel=method?.choices?.find(choice=>choice.value===draft.nonlinear_solver)?.label || draft.nonlinear_solver || "未设置";
  const given=physical.map(([label,value])=>`<div><dt>${label}</dt><dd>${esc(value)}</dd></div>`).join("");
  const relation=mixture?"内层求总质量流量与两侧涡轮膨胀压力比，满足推力及两侧轴功率平衡。":"内层求总质量流量及交叉供给流量，满足推力及两侧轴功率平衡。";
  const policy=solverControls;
  const criteria=[
    ["推力平衡",finite(policy.thrust_relative_tolerance)&&finite(draft.thrust)?`${fmt(policy.thrust_relative_tolerance*Math.max(Math.abs(draft.thrust),100))} N` : "未提供", `内置相对容差 ${fmt(policy.thrust_relative_tolerance)}`],
    ["富氧侧轴功率平衡",`${fmt(draft.steady_ox_power_tolerance_W)} W`,"求解设置"],
    ["富燃侧轴功率平衡",`${fmt(draft.steady_fuel_power_tolerance_W)} W`,"求解设置"],
    ...(temperature?[["预燃室温度反求",`${fmt(policy.preburner_temperature_tolerance_K)} K`,"内置准则"]]:[]),
    ...(policy.coupling_criteria||[]).filter(row=>spatialCooling || valves && row.key==="pressure_residual_Pa").map(row=>[
      spatialCooling?row.label:"供给水力反馈",`${fmt(row.tolerance)} ${row.unit==="1"?"（相对）":row.unit}`,spatialCooling?"冷却耦合 · 内置":"阀路耦合 · 内置"]),
  ];
  const table=`<div class="table-wrap"><table class="solver-criteria"><thead><tr><th>残差检查</th><th>容差</th><th>来源</th></tr></thead><tbody>${criteria.map(row=>`<tr>${row.map(value=>`<td>${esc(value)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  const configuration=groups.filter(group=>group.numerical_configuration).map(group=>`<span>${esc(group.title)}：${esc(group.configuration_label||"待检查")}</span>`).join("");
  return `<section class="solver-task-summary" aria-label="当前设计点任务摘要"><div class="solver-task-heading"><h3>本次设计点任务</h3><p>稳态设计匹配 · 当前方案设置</p></div>
    <div class="solver-config-tags">${configuration}</div><dl class="solver-task-facts">${given}</dl>
    <p class="solver-matching-relation">${relation}${temperature?"外层更新两侧混合比以匹配指定温度。":""}冷却与燃气热状态按当前模型参与耦合。</p>
    <div class="solver-task-actions" role="group" aria-label="关联设计条件">${link("design","设计目标")}${link("preburner_design","匹配条件")}${link("pressure_ffsc","压力预算")}</div>
    <dl class="solver-task-facts"><div><dt>非线性方法</dt><dd>${esc(methodLabel)}</dd></div><div><dt>初始化来源</dt><dd>当前方案的迭代初值</dd></div>${spatialCooling?`<div><dt>冷却轴向网格节点数</dt><dd>${fmt(draft.cooling_grid_points)}</dd></div>`:""}</dl>
    ${compact?`<details><summary>完整数值收敛准则</summary>${table}</details>`:`<h4>完整数值收敛准则</h4>${table}`}
    <p class="fine">以上为当前程序用于新任务的配置。已提交任务采用各自保存的输入；历史判据从该记录的收敛诊断读取。</p>
    <details><summary>内置迭代控制与任务范围</summary><p>循环内层默认最多 ${fmt(policy.inner_max_iterations)} 次迭代；${temperature?`预燃室温度外层最多 ${fmt(policy.preburner_max_iterations)} 次，松弛系数 ${fmt(policy.preburner_relaxation)}；`:""}${spatialCooling||valves?`${spatialCooling?"冷却／热气／供给":"供给水力"}耦合每次最多 ${fmt(policy.coupling_max_iterations)} 次尝试。`:""}延续求解可多次调用内层，单层上限不等于整项任务迭代总数。</p>${spatialCooling?"":'<p>预算冷却采用给定吸热量与压降，不启用热状态外层反馈。</p>'}<p>数值收敛、工程约束和模型验证分别判断。固定硬件稳态与瞬态分析使用各自任务设置；瞬态物理初始条件不从此页迭代初值推定。</p></details>
    <div class="solver-task-actions" role="group" aria-label="数值配置入口">${link("solver","求解设置")}${link("initialization","初始化")}</div>
  </section>`;
}
