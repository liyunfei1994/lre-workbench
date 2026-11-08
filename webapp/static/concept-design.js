// Explains the current draft's model contract; never changes saved inputs or
// claims that a design-point closure validates a particular piece of hardware.
const escape = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));

function designMode(inputs) {
  const temperature = Number(inputs.use_target_preburner_temp ?? 1);
  const mixture = Number(inputs.use_target_preburner_mr ?? 1);
  if (!Number.isFinite(temperature) || !Number.isFinite(mixture)) return null;
  return temperature >= .5 ? "temperature" : mixture >= .5 ? "mixture_ratio" : "legacy_free_split";
}

const MODES = {
  temperature: {
    label: "指定预燃室温度",
    given: "目标推力、室压、整机氧燃质量比、两侧预燃室温度目标。",
    inner: "总质量流量、富氧侧和富燃侧涡轮压比。交叉分流由当前预燃室氧燃质量比与质量守恒确定。",
    outer: "反求并更新两侧预燃室氧燃质量比，使出口温度达到目标；热状态与冷却按所选模型迭代闭合。",
  },
  mixture_ratio: {
    label: "指定预燃室氧燃质量比",
    given: "目标推力、室压、整机氧燃质量比、两侧预燃室氧燃质量比。",
    inner: "总质量流量、富氧侧和富燃侧涡轮压比。交叉分流由给定混合比与质量守恒确定。",
    outer: "不反求预燃室氧燃质量比，出口温度由热化学计算得到；热状态与冷却按所选模型迭代闭合。",
  },
  legacy_free_split: {
    label: "历史自由分流模式",
    given: "目标推力、室压、整机氧燃质量比，以及两侧预燃室压力设定。",
    inner: "总质量流量、进入富氧预燃室的燃料分流、进入富燃预燃室的氧化剂分流。",
    outer: "不施加预燃室温度或混合比目标；温度与混合比由结果读取。热状态与冷却按所选模型闭合。",
  },
};

export function renderConceptDesignScope(inputs) {
  if (inputs?.cycle_type !== "ffsc") return "";
  const mode = designMode(inputs), contract = MODES[mode];
  if (!contract) return '<section class="concept-design-scope" aria-label="当前设计点求解方式"><h3>设计点循环匹配</h3><p>预燃室设计方式标识无效，请核对设计输入。</p></section>';
  const roles = [
    ["given", "已给定", contract.given],
    ["inner", "内层求解", contract.inner],
    ["outer", "外层求解", contract.outer],
    ["sizing", "部件初选", "泵转速采用给定值或按目标比转速估算；级数与几何系数作为本轮初选假设，不由循环自动优化。"],
  ];
  return `<section class="concept-design-scope" aria-label="当前设计点求解方式" data-design-mode="${mode}">
    <div class="concept-design-heading"><h3>设计点循环匹配</h3><span class="parameter-role">${escape(contract.label)}</span></div>
    <p class="fine">以目标推力和两侧涡轮—泵功率平衡为核心，求给定假设下的设计点。效率与压损按本方案选择的输入或模型采用。</p>
    <dl class="concept-solve-roles">${roles.map(([id,label,description]) => `<div data-concept-role="${id}"><dt>${label}</dt><dd>${escape(description)}</dd></div>`).join("")}</dl>
    <details class="concept-design-explanation"><summary>初选假设、求解初值与后续部件设计</summary>
      <p>初选假设在本轮计算中按给定方式采用，设计人员可根据校核结果修改；求解初值只是迭代起点，求解器会调整对应未知量，终值不必等于初值。</p>
      <ol class="concept-design-flow"><li>循环提出部件需求</li><li>部件设计与校核</li><li>更新效率与损失假设</li><li>重新计算设计点</li></ol>
      <p>当前需人工将部件校核后的效率和损失更新到设计输入，再保存并重新计算。尚未通过泵、涡轮特性图自动反馈性能或匹配运行转速。</p>
      <p>FFSC 当前未另设轴承、密封等机械损失预算。推力达到目标表示约束闭合，不是独立的性能预测验证；循环收敛后仍需核实部件能力与工程约束。</p>
    </details>
  </section>`;
}
