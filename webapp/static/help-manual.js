// Read-only documentation. Inputs select applicable explanations; this module
// neither edits a draft nor submits a workspace action.
import {renderConceptDesignScope} from "./concept-design.js";
import {renderAnalysisParameterRoles, renderParameterReference} from "./parameter-editor.js";

const escape = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const CHAPTERS = [
  {id:"workflow", title:"用户手册", description:"工作流程、输入确认、计算与结果判读"},
  {id:"theory", title:"理论与模型说明", description:"设计点循环匹配、求解初值与模型假设"},
  {id:"parameters", title:"参数参考", description:"按设置组查阅定义、单位与取值依据"},
  {id:"modes", title:"分析模式与参数角色", description:"概念设计和固定硬件分析中的输入、输出与约束"},
  {id:"properties", title:"物性数据与参考态", description:"计算工具、热数据、状态方程、输运、材料及实际使用依据"},
  {id:"examples", title:"示例教程", description:"建立方案、载入示例与往返修订"},
  {id:"troubleshooting", title:"版本说明与故障排查", description:"输入版本、连接、检查与计算问题"},
];

function referenceGroups(groups) {
  const ids = new Set();
  return (Array.isArray(groups)?groups:[]).filter(group=>{
    if(!group || typeof group.id!=="string" || !group.id || ids.has(group.id))return false;
    ids.add(group.id);return true;
  });
}
export function helpChapterForGroup(groupId) {
  return typeof groupId==="string"&&groupId ? `parameter:${groupId}` : "parameters";
}
export function normalizeHelpChapter(chapter, groups=[]) {
  if(chapter==="parameter:cycle"&&groups.some(group=>group.engine_definition))chapter="parameter:design";
  if(CHAPTERS.some(item=>item.id===chapter))return chapter;
  return referenceGroups(groups).some(group=>helpChapterForGroup(group.id)===chapter)?chapter:"workflow";
}
const chapterButton=(id,title,className="")=>`<button type="button"${className?` class="${className}"`:""} data-help-chapter="${escape(id)}">${escape(title)}</button>`;
const section=(title,body)=>`<section class="help-section"><h3>${title}</h3>${body}</section>`;
const hasDraft=inputs=>inputs?.cycle_type==="ffsc";

function workflow() {
  return `<p>围绕当前方案完成输入定义、设计点计算和结果复核。任务树用于切换工作内容；帮助文档用于查阅定义与方法。</p>`+
    section("打开与继续工作",`<p>新方案默认采用概略热负荷估算，也可沿用示例模型，从“发动机定义与设计工况”开始。概略起点需核对参考壁温、压降估计、概略型面与冷却范围；已有方案恢复当前标签页内的上次工作位置。页顶显示当前输入、所选计算记录及一致性，可展开查看差异。方案名称旁的“方案摘要”按需打开，不占任务树步骤，也不覆盖上次工作位置。计算模型、数据文件与版本在“校核与记录 → 方案与计算记录 → 模型与数据来源”查阅。</p>`)+
    section("总体与部件设计流程",`<ol class="help-steps"><li><b>定义发动机与设计工况。</b>在“发动机定义与设计工况”核对给定的推进剂组合和循环形式，并填写推力、室压、氧燃比与环境压力。循环原理与建模范围可按需展开。</li><li><b>定义供给边界。</b>在“推进剂供给条件”设置两路入口条件。</li><li><b>设置总体模型与匹配条件。</b>核对物性、效率、压力预算和膨胀条件。概略冷却模型根据工况、参考壁温与受热面估算热负荷，无需先完成通道设计；选择详细耦合时需确认参与闭合的部件几何与材料。</li><li><b>检查并确认参数。</b>设置页显示本组状态。确认并保存本组参数会保存当前方案；确认与保存不启动计算。</li><li><b>配置设计点求解。</b>在“求解设置”核对算法、容差及离散，在“初始化”检查迭代起点。数值配置随方案保存，通过检查即可使用。</li><li><b>提交设计点计算。</b>在“输入检查与运行”核对整机输入、待确认项与计算输入版本，检查通过后运行设计点。页面导航不自动提交任务。</li><li><b>开展部件设计并反馈。</b>从保存记录提取部件设计要求，在部件工作区编辑尺寸与材料、查看结果和局部研究，再将修改连同来源工况送入整机复核。</li></ol>`)+
    section("部件反馈与候选基准",`<p>泵、涡轮、冷却通道与材料在部件工作区编辑；概略型面与冷却范围由总体页统一管理，部件页引用同一份几何。系统图解释对象归属，总体面积比保留在总体循环条件中。</p><p>部件反馈只保存本部件变化，保留其他未保存编辑。普通保存后仍可补记反馈；整机集成页关联包含这些修改的复算记录，分别显示数值收敛和待工程校核状态。未收敛结果只供诊断与草案迭代。</p><p>候选基准仅冻结所选记录实际保存的几何与模型证据，不混入草稿。当前尚不能完成固定硬件名义工况复核，因此不能把候选快照标为已确认运行基准。稳态和瞬态研究仍受各自模型范围限制。</p>`)+
    section("工程视图区",`<p>总体与部件参数页右侧提供常驻工程视图。“系统简图”表示当前草稿的循环关系，选择参数组时高亮相关对象；点击图中对象可定位相应设置。简图不提供计算状态或部件尺寸。相关设置列出对象关联的模型、边界、几何和数值配置，可直接定位；每个参数只有一个当前编辑入口。</p><p>求解设置与初始化页默认显示“任务摘要”，用于核对当前任务的给定量、待求量、方程和收敛判据；仍可切换到系统简图查看对象关系。</p><p>“保存结果”读取所选计算记录的循环图和已有几何。草稿修改不会改变这些结果；输入与记录不同、历史输入快照缺失或模型已撤回时，视图区分别提示对应状态。完整图形、数据与导出功能可从视图区进入原结果页面。</p><p>拖动两区之间的分隔条调整宽度；分隔条获得焦点后可使用左右方向键，按 Enter 或双击恢复默认宽度。窗口较窄时改为上下排列，宽度偏好在恢复分栏后继续使用。</p>`)+
    section("设计点求解",`<p>任务树按总体方案与循环设计、部件设计与特性建模、整机集成与基准确认、固定硬件稳态分析、瞬态运行分析组织。各阶段保留输入、运行与结果；总体阶段的算法、容差与数值离散在“总体设计点计算 → 求解设置”中管理。</p><p>“初始化”统一管理总流量和燃气状态等迭代初值。指定预燃室温度时，混合比为反求初值；指定混合比时，它返回“预燃室热力匹配条件”作为设计条件。参数查找与错误定位跟随当前角色。</p><p>数值配置通过检查后显示“采用默认”或“已自定义”，不要求额外工程确认。设计条件仍需核对并确认。右侧“任务摘要”展示当前方案的给定量、待求量和完整收敛准则；历史结果仍读取所选计算记录。</p>`)+
    section("预燃室条件与限值",`<p>“预燃室热力匹配条件”只规定总体循环工作条件。给定工作温度时反求氧燃比，给定氧燃比时计算温度；支路流量和压力由系统联立匹配。迭代初值在“初始化”管理，温度上限不是完成循环匹配的必填输入。</p><p>有工程限值时，在“校核与记录 → 工程限值与校核”中按需登记。限值随方案保存，无需额外确认；未设置时可求解，该项限值校核为未评估。启用后，输入检查会拒绝目标温度高于上限的矛盾设置，计算结果也会检查是否超限；上限不参与循环残差，不会自动降低目标温度或调整求解结果。设计暂定值用于筛选，依据登记不代表材料与寿命已验证。</p><p>新建自定义方案及新载入工程示例均默认不启用温度上限；已保存方案明确设置的限值继续保留。右侧工程视图提供关联参数来源和返回入口。“保存结果”对照同一记录中的目标、计算值、偏差与已启用限值，当前编辑不会改变该记录。完整流量、压力、轴功率及入口状态在“部件设计要求”的预燃室交接段查看。</p>`)+
    section("实时收敛监控",`<p>在“总体设计点计算 → 输入检查与运行”提交计算后，进入“收敛监视”，连续读取该任务已记录的迭代数据。可选择当前或历史计算记录；曲线始终对应提交时的输入版本，后续草稿修改不改变该记录。</p><p>内层方程显示求解器接受的迭代点，归一化验收残差以 1 为通过阈值。关键物理量与残差分开显示。预燃室外环、模型求值内的热闭合及延续阶段分别记录；模型试算过程中的热闭合不代表该试算已被内层求解器接受。不同求解调用不连接成同一条轨迹，缺失值不补零。</p><p>横轴表示求解过程，不表示发动机物理运行时间。失败、取消、超时或服务中断后，已写入的过程记录继续保留，可重新打开或导出。旧任务没有逐步记录时显示数据缺失，不从最终结果反推曲线。任务执行完成仍需单独检查各层收敛、工程约束和模型适用性。</p>`)+
    section("结果判读",`<p>分别检查输入完整性、数值收敛、工程约束和模型验证证据。输入检查通过表示可以提交；计算任务完成状态不能替代数值收敛与工程适用性检查。</p><p>部件初步设计可用于修订概念设计假设。固定硬件分析应有明确的硬件基准和特性；各分析入口的可用能力以当前页面说明为准。</p>`)+
    section("查阅帮助与继续编辑",`<p>“本页帮助”定位当前设置组的参数参考。帮助文档中的目录与搜索只切换阅读内容；使用顶部返回按钮可回到原工作位置。当前草稿不会因查阅文档而保存、替换或提交。</p><p>参数字段旁保留单位、必要定义和操作提示。完整的参数说明与依据集中在${chapterButton("parameters","参数参考","help-inline-link")}中。</p>`);
}
function theory(inputs) {
  const scope=hasDraft(inputs)?renderConceptDesignScope(inputs):"";
  return `<p>本章说明当前程序提供的设计点计算方法。参数定义、计算方法和验证范围应与所用软件版本保持一致。</p>`+
    (scope?`<p class="help-context-note">以下求解方式由当前概念设计草稿生成，说明本次设置的参数角色；不代表所选历史计算记录。</p>${scope.replace('<details class="concept-design-explanation">','<details class="concept-design-explanation" open>')}`:
      `<p class="help-context-note">当前没有适用的概念设计草稿。选择方案后，可在此查看与草稿对应的求解方式。</p>`)+
    section("初值、假设与约束",`<dl class="help-definitions"><dt>数值初值</dt><dd>用于启动迭代。最终值应从计算结果读取，不能把输入初值作为已达到的性能指标。</dd><dt>模型与部件初选假设</dt><dd>按当前配置参与计算，应结合部件设计和校核结果修订，并保留取值依据。</dd><dt>工程约束</dt><dd>用于评估计算结果是否满足设计要求。约束的取值依据、适用范围和满足情况需要单独记录。</dd></dl><p>${chapterButton("modes","查阅分析模式与参数角色","help-inline-link")}</p>`)+
    section("工质与热物性模型",`<p>总体设计采用化学平衡、燃气性质和部件能量关系计算稳态状态。热化学数据、物种集合、状态方程及膨胀组分假设分别登记；模型名称不能代替适用性判定。</p><p>${chapterButton("parameter:chamber_chemistry","查阅热化学模型与温度判读","help-inline-link")}</p>`);
}
function parameters(groups) {
  return `<p>参数参考与设置页使用同一套参数定义。以下章节提供完整说明、单位和取值依据；输入值与确认状态在工作台中维护。</p>`+
    (groups.length?`<ul class="help-reference-list">${groups.map(group=>`<li>${chapterButton(helpChapterForGroup(group.id),group.title||group.id)}<span>${group.id==="cycle"?"只读构型说明":`${Array.isArray(group.fields)?group.fields.length:0} 项参数定义`}</span></li>`).join("")}</ul>`:
      '<p class="help-empty">当前没有可用的参数定义。连接工作区并载入方案后重试。</p>');
}
function modes(catalog,inputs) {
  const table=renderAnalysisParameterRoles(catalog,hasDraft(inputs)?"concept_design":null);
  return `<p>同一物理量在不同分析模式下可能分别作为输入、求解量或约束。设置和判读结果时，应先确认分析任务及参数角色。</p>`+
    `<p class="help-context-note">本表采用当前安装版本的参数定义，${hasDraft(inputs)?"“当前模式”指当前草稿的概念设计模式":"当前未选定适用的概念设计草稿"}。历史计算记录仍以其保存的输入和模型信息为准。</p>`+
    (table?table.replace('<details class="analysis-parameter-roles">','<details class="analysis-parameter-roles" open>'):
      '<p class="help-empty">当前版本未提供分析模式参数对照数据。</p>');
}
function examples() {
  return `<p>示例用于建立初始方案和演示操作流程。示例数值、模型假设与公开资料的对应关系，应查阅该示例的参数来源记录。</p>`+
    section("从示例建立方案",`<ol class="help-steps"><li>新建项目时选择适用示例，填写方案名称。</li><li>首先进入“发动机定义与设计工况”，核对构型并定义任务。依次检查供给条件、模型、几何及求解设置；按实际任务修改。</li><li>核对各输入组参数后确认保存，在“输入检查与运行”查看完整检查结果。构型身份为只读，设计工况按本组参数确认。</li><li>提交计算后，在“总体结果与部件要求”核对计算输入版本与收敛状态，再开展部件复核。</li></ol>`)+
    section("工程示例与已有方案",`<p>工程示例位于“模型与资料库 → 工程示例与来源”。选择条目只浏览说明，可从此示例新建独立方案。需要替换已有方案时，展开“替换当前草稿”并明确点击替换按钮；原计算记录保留，替换后的草稿尚未保存，可撤销。</p><p>新建项目也可直接查阅所选示例的参数来源。物性资料独立置于“热物性与材料数据”，不再与载入示例合并。本章只提供操作说明。</p>`)+
    section("查看参数来源",`<p>在示例操作区使用“参数来源与建模假设”查阅资料。应区分公开值、历史型号参考、工程假设和数值初始化设置；引用示例不构成对具体发动机性能的验证。</p>`);
}
function propertyGuide(){
  return `<p>“采用 Cantera 物性”只说明使用了一个计算工具。可复核的模型还须说明数据文件、物种集合、热力学表达式、状态方程、组成处理、输运与参考态。</p>`+
    section("三个入口各自负责什么",`<p>“总体模型与假设 → 工质与热物性模型”定义方案采用的模型；“模型与资料库 → 热物性与材料数据”查阅具体数据文件、温区、系数和曲线；“所选计算记录”展示当次实际身份与节点证据。资料浏览不会替换方案数据或重新计算。帮助文档解释方法与使用方式。</p>`)+
    section("计算工具、数据文件与状态方程",`<p>Cantera 执行物种热力学、混合物性质及化学平衡等运算。当前甲烷热化学文件 gri30.yaml 与 gri30_highT.yaml 提供候选物种及其标准态热数据。high-T 文件说明中记载 Joseph Shepherd 根据 NASA TM-4513 更新高温多项式；它不是只把温度上限改大。具体文件指纹和逐物种温区以资料页读取值为准。</p><p>NASA7 表达 cₚ°、h°、s°，不直接描述高压非理想性；理想气体、PR 或 PR–BM 的状态方程是另一层模型。源 YAML 中的 ideal-gas 声明与运行时构造的 PR 候选相分别记录。文件生成时的 Cantera 版本也不同于运行时版本。</p>`)+
    section("候选物种、实际组成与反应速率",`<p>源文件中的 53 种物种是候选范围；实际组分由入口元素库存、状态与所用组成假设确定。PR 参数目前覆盖其中 16 种，清单见方案配置和记录；源文件物种数不能当作实际启用数。高温解离物种缺失需要单独评估。</p><p>预燃室和主室按压力与焓约束求化学平衡；涡轮冻结入口组成，生产喷管冻结主室组成。文件虽然包含反应速率，当前总体设计点不积分有限速率反应，因此不能用这次平衡计算说明点火延迟或反应时间。独立瞬态任务需查阅自己的模型范围。</p>`)+
    section("怎样读温区和曲线",`<p>数据浏览可逐物种查看分段温区、NASA7 系数、参考压力以及标准态摩尔比热、焓、熵曲线。共同温区是所列物种声明温区的交集；交集外须结合实际含量逐物种判断。范围内不等于给定压力下预测精度已经验证。</p><p>物种曲线为纯物种标准态性质，不等于平衡燃气混合物的性质。冻结组成的 cₚ 与随温度重新达到平衡的有效热响应也不同。曲线上的记录温度虚线仅定位温度，不是该混合物的物性值。只有数据指纹相同时才叠加这些位置。</p><p>纯流体曲线必须给定绝对压力。缺失或无效点不插补，跨相态不连线；资料曲线禁止外推。发动机计算记录仍保留实际温度及其温区审查，不能为满足温区而截温。</p>`)+
    section("输运、纯流体与壁面材料",`<p>黏度和导热率由分子输运参数及输运模型计算，不由 NASA7 热数据直接给出。当前热侧使用源数据对应的稀薄气体输运模型；PR 状态方程不自动提供经过验证的高压输运修正。</p><p>CoolProp HEOS 为氧、甲烷提供纯流体状态方程及各自的输运关联。资料页分别列出 EOS、黏度、导热率文献键和声明范围。HEOS 端口查询与 REFPROP 独立对照分别组织；二者可能使用同源模型，对照相符不等于独立试验验证。</p><p>壁面材料按试样成分、制造状态、导热率和比热数据分别登记。详细稳态冷却使用导热率；概略热负荷采用参考壁温，不求解壁面导热，此时材料仅为部件草案。材料比热曲线可供热惯性建模参考，不能自动解释为当前稳态计算已使用。材料热物性不替代强度、疲劳或寿命数据。</p>`)+
    section("焓参考与历史复现",`<p>焓与熵的零点取决于参考约定。将 HEOS 纯流体送入化学能量平衡前，程序对齐理想物种化学生成焓，并移除有限压力参考点的残余焓。实际对齐公式、温压参考与版本随结果保存。不同参考零点的绝对焓不能直接相减或作为误差比较。</p><p>新计算保存热化学文件及指纹、实际候选物种、纯流体数据版本和壁面使用身份。历史记录缺少的来源、系数或版本显示为未记录，不使用当前默认模型补写。浏览当前安装曲线不等于复算历史结果。</p>`)+
    section("方法参考",`<p><a href="https://cantera.org/3.2/reference/thermo/species-thermo.html" target="_blank" rel="noopener noreferrer">Cantera：物种标准态热力学</a>；<a href="https://github.com/Cantera/cantera/blob/v3.2.0/data/gri30_highT.yaml" target="_blank" rel="noopener noreferrer">GRI30 high-T 数据文件说明</a>；<a href="https://coolprop.org/coolprop/HighLevelAPI.html#reference-states" target="_blank" rel="noopener noreferrer">CoolProp：焓熵参考态</a>。</p>`);
}
function troubleshooting() {
  return section("方案、草稿与计算记录",`<dl class="help-definitions"><dt>当前草稿</dt><dd>正在编辑的输入，可能含未保存修改。</dd><dt>方案版本</dt><dd>保存时形成的版本。运行设计点前，程序会先保存当前修改。</dd><dt>计算记录</dt><dd>保留提交时的输入版本与结果。旧计算记录继续保留其输入快照与版本，不会随后续草稿修改而更新。</dd></dl>`)+
    section("常见问题",`<dl class="help-definitions"><dt>输入检查未完成</dt><dd>先确认服务连接，再重试输入检查；校验服务不可用时不能按已检查处理。</dd><dt>存在待确认项</dt><dd>从输入检查页定位对应参数组，核对取值后确认。修改相关参数后需要重新确认。</dd><dt>计算结果与当前设置不一致</dt><dd>核对所选计算记录和当前方案版本。输入或模型改变后，应重新计算并复核相关结果；缺少历史输入快照时不能假定一致。</dd><dt>无法运行或未收敛</dt><dd>先处理输入检查和任务状态提示，再查看“收敛诊断”与“日志与警告”。数值检查通过不等于模型已完成工程验证。</dd><dt>页面更新后显示异常</dt><dd>先保存当前编辑，再刷新页面；服务版本不一致时，停止旧服务并使用当前安装目录的启动脚本重新启动。</dd></dl>`)+
    section("文档与维护",`<p>本地帮助随程序提供，内容与当前安装版本的参数定义配套。查阅历史结果时仍需核对当时保存的模型和输入信息。</p><p>开发环境、代码结构、接口与测试流程由项目开发文档维护，日常参数设置无需阅读这些内容。</p>`);
}

function chapters(groups) {
  return [...CHAPTERS,...groups.map(group=>({id:helpChapterForGroup(group.id),title:group.title||group.id,description:group.id==="cycle"?"推进剂组合、循环连接、轴系关系与设计范围":"参数定义与取值依据",group}))];
}
function configurationGuide() {
  return section("页面定位", `<p>“发动机定义与设计工况”是总体方案与循环设计的首个入口。推进剂组合与循环形式按方案只读显示，设计目标与环境边界在同页编辑。浏览页面不保存方案，也不启动计算。</p><p>核对构型并定义设计工况后，再设置供给条件、模型、初选假设及求解参数。总体采用系统级集中参数热力匹配，冷却与喷管使用各自模型，无需全局二维或三维设置。</p>`)+
    section("循环与供给构型", `<p>当前平台支持液氧 / 液甲烷全流量补燃循环（FFSC）。富氧侧涡轮与液氧主泵、富燃侧涡轮与甲烷主泵分别构成两套轴系；两侧涡轮排气进入主燃烧室。工质流路和机械传动分别表示，系统简图不表示部件安装位置或几何尺寸。</p><p>前置低压泵属于当前供给模型的假设，相关压力、效率及管路压降在“推进剂供给条件”中设置。它不是全流量补燃循环的定义条件。</p>`)+
    section("草稿构型与计算记录", `<p>页面构型说明和工程视图中的“系统简图”依据当前草稿。尚未计算时也可查看连接关系；构型标识缺失或不受支持时显示对应提示，不使用历史记录替代当前草稿。</p><p>工程视图中的“保存结果”及“详细循环图”依据所选计算记录，显示该记录的版本与已有流路状态。修改草稿不会更新这些历史结果；详细循环图不作为当前草稿的计算结果。</p>`)+
    section("当前能力范围", `<p>本页不提供推进剂组合切换、循环方式选择或自由拓扑装配。只读说明不构成硬件配置确认、数值收敛判定或工程验证结论。</p>`);
}
function supplyConditionsGuide() {
  return section("供给模型与接口位置", `<p>本页定义单个概念设计工况的供给条件。液氧与液甲烷分别经贮箱出口、前置低压泵及供给管路到达主泵入口。前置泵是当前采用的构型假设，不是全流量补燃循环的必备部件；其功率按外部输入计入整机能量账。</p><p>当前未单独求解气枕增压、液柱、加速度或贮箱至前置泵的吸入管损。贮箱出口压力表示液体接口条件，不能直接作为已计算的气枕压力。</p>`)+
    section("压力、温度与给定值", `<dl class="help-definitions"><dt>绝对压力</dt><dd>以真空为零点，与表压的关系为：绝对压力 = 表压 + 当地大气压力。本页压力统一显示为 MPa；1 MPa = 10 bar。压降表示两端压力之差。</dd><dt>接口温度</dt><dd>贮箱出口液体温度是给定边界条件；后续温度由泵焓升和绝热等焓管路模型计算。示例甲烷 100 K、液氧 70 K 需结合接口压力核对相态、过冷度及实际供给条件，不是通用推荐值。</dd><dt>设计给定值与模型假设</dt><dd>前置泵出口设计压力、等熵效率和管路压降在本次设计点计算中固定采用。它们影响最终结果，不是求解器会自动修正的迭代初值。主泵入口压力由前置泵出口压力减去供给管路压降得到。</dd></dl>`)+
    section("两路独立设置与历史方案", `<p>两路压力、效率和管路压降可独立填写。字段标记“沿用共用设定”时，实际使用原方案的共用值；编辑该字段后仅改变本路，清空则恢复共用设定。页面浏览或保存其他参数不会自动改写这些继承关系。共用设定表示数值相同，不表示共用硬件。</p>`)+
    section("结果与适用范围", `<p>右侧供给链显示当前草稿的给定值及压力关系，尚未求解的温度不补算。保存结果显示提交时输入对应的计算状态。相态成立、数值收敛与泵吸入性能满足要求分别判断；当前没有具体泵的 NPSHr 数据，不能仅凭可用吸入条件判定汽蚀余量合格。</p><p>固定硬件变工况分析需要泵特性及管路阻力模型；本页给定的设计点压降不代表同一管路在所有流量下的实际压降。</p>`);
}

function thermochemistryGuide(inputs) {
  const eos=inputs?.gas_eos;
  const backend=eos==="Peng-Robinson-Boston-Mathias"
    ? "Peng–Robinson / Boston–Mathias 修正模型（PR–BM）采用本项目独立的残余 Helmholtz 性质与逸度约束平衡求解。Cantera 提供物种标准态热化学数据等支持。"
    : eos==="Peng-Robinson"
      ? "预燃室采用 Cantera 原生 PR 平衡计算，涡轮冻结组成；主燃烧室及后续燃气流程采用理想气体。该分段模型可用于设计点研究与独立热力学校核。"
      : ["ideal-gas","ideal"].includes(eos)
        ? "当前理想气体混合物由 Cantera 计算燃烧平衡及热力性质。循环计算中的涡轮和喷管模型采用冻结组成。"
        : "当前设置未登记可识别的燃气物性模型。请在参数页核对模型；历史记录以其保存的模型身份为准。";
  return section("页面操作与状态",`<p>本页选择本次设计采用的燃气物性模型。这是设计模型决策；物种范围和旧版本温度修正以只读信息显示，不另设切换控件。默认选项为理想气体混合物，PR 与 PR–BM 可作为研究模型用于设计点计算。所有选项均按焓平衡求温度，不附加经验温升折减。</p><p>显式选择理想气体模型时，采用所选热化学文件的完整候选物种；选择 PR 或 PR–BM 时，采用当前 PR 参数支持的 16 种物种。每次选择均取消旧版本温升折减，并保留所选热化学数据。该操作不会自动保存；确认并保存后生成新方案版本，再运行设计点。原计算记录及其模型身份保留。“完整”仅指当前数据文件登记的候选物种，不代表所有实际化学物种。</p><p><b>参数已确认</b>表示当前输入已经核对并登记；<b>结果需重算</b>表示当前设置与所选计算记录不一致。两项状态可同时存在，参数确认不会重新计算结果。“热力学校核 → 理想气体 / PR”用于已保存设计记录的独立模型对比，不替代本页的模型选择。研究模型的计算结果不能作为已验证性能。</p>`)+
    section("化学平衡与燃气物性",`<p>本模块用于发动机总体设计的稳态热化学与物性计算。化学平衡确定允许物种中的平衡组成；状态方程和标准态热数据共同确定密度、焓、熵及比热；部件模型规定膨胀、换热和效率。化学反应速率不参与当前平衡求解。</p><p>理想气体混合物忽略分子间相互作用对状态关系的影响。Peng–Robinson（PR）状态方程描述真实气体的非理想性质；PR–BM 是采用 Boston–Mathias 温度修正的 Peng–Robinson 模型。模型名称说明所用方法，不表示精度等级。</p><p>当前总体模型不以点火延迟、空间混合长度或燃烧不稳定性为计算目标。这些局部非平衡问题不作为本页设置项。</p><p class="help-context-note">方案登记的计算实现：${backend}</p>`)+
    section("压力与焓约束",`<p>预燃室和主燃烧室均采用零维化学平衡。入口物质给出元素库存，入口焓流与已建模的能量交换给出目标焓；在指定压力下求解温度和平衡组成。温度是计算结果，不能同时作为任意给定的平衡温度使用。</p><p>全流量补燃主燃烧室接收两股涡轮排气的实际质量流量、组分和比焓。入口混合比焓按质量流量加权，包含化学生成焓与热状态贡献；不同物性实现之间须保持化学焓参考一致。</p><p>采用给定冷却热负荷时，从入口焓流中扣除规定热量。准一维冷却模式先求未扣除再生换热的平衡参考状态，再由燃气侧沿程模型计入换热；同一换热量不得在主室与沿程重复扣除。</p>`)+
    section("参与计算的化学物种",`<p>化学物种是参与计算的化学种类，例如 H₂O 分子、H 原子及 OH 自由基，与发动机部件或几何模型无关。当前 gri30_highT 数据登记了 53 种候选物种，包含 H、O、OH 等解离物种；计算仍受入口元素组成约束，并非每种物种在每个工况下都有非零含量。</p><p>PR 与 PR–BM 在本项目的参数目前仅覆盖其中 16 种物种，未纳入 H、O、OH、HO₂、CH₃ 等物种。16 种是当前参数覆盖的限制，不是 PR 状态方程的理论物种数上限，也不是通用的精度档位。遗漏高温解离物种可能导致平衡温度偏高；删减物种对组成、温度与物性的影响须单独评估。</p><p>物种范围随本页显式选择的物性模型确定：理想气体采用完整候选物种，PR 系列采用当前参数支持的 16 种物种。页面显示该范围，不提供独立选择。历史 auto 策略按原状态方程解释；查看页面或保存其他参数不会改写原物种范围。</p><p>各物种热数据的温区分别检查。采用高温多项式不等于反应机理或高压混合物性质已经通过验证。</p>`)+
    section("真实气体模型的部件范围",`<p>PR 与 PR–BM 均为研究模型，可在本页选作设计点计算模型，也可通过热力学校核开展独立对照。当前实现仅支持上述 16 种物种，不接受 PR 与完整 53 物种的组合；未覆盖的物种参数不能由程序任意补填。</p><p>PR–BM 覆盖预燃室、涡轮、主燃烧室、冷却燃气侧和喷管；其当前实现采用未标定的默认二元混合规则和单一均匀气相，高压输运与全局多相稳定性不在已完成的校核范围内。</p><p>PR 分段模型仅用于预燃室和涡轮，主燃烧室及后续流程采用理想气体。旧记录保持原模型，不自动改为 PR–BM。模型范围应与保存的实现版本一并核对。</p>`)+
    section("涡轮与喷管的组成假设",`<p>循环计算中的涡轮模型冻结其入口组成，按同一物性模型求等熵出口焓，再由效率确定实际焓降和轴功。喷管模型冻结主燃烧室组成，按所选热侧模型处理膨胀与换热。</p><p>热力学校核中的平衡膨胀、主室冻结和喉部冻结为独立对照条件，循环计算仍采用其登记的喷管模型。燃烧化学平衡与膨胀中的组分处理应分别确认。</p>`)+
    section("主燃烧室温度与求解耗时",`<p>3600 K 或 4000 K 均不能作为跨工况通用的正常值或错误阈值。应同时核对室压、实际入口焓与组分、氧燃比、物种集合、冷却处理以及温度对应的位置。主室平衡参考温度、喷管局部静温和壁面温度不是同一物理量。</p><p>出现温度偏高时，先核对是否遗漏解离物种、使用了不相容的焓参考或超出热数据温区，再进行相同入口条件的模型对照。不得通过裁剪温度、调整温度初值或任意温升折减使结果落入预期范围。</p><p>PR–BM 的非理想平衡和焓熵状态反算嵌套在循环与冷却迭代中，计算量高于理想气体模型。应查看各层收敛记录与失败原因。默认采用化学平衡与理想气体混合物；如选择真实气体研究模型，应结合独立模型对照判读结果。计算较快不表示理想气体模型的物理精度在所有工况下更高。</p>`)+
    section("模型对照与验证证据",`<ol><li><b>区分物种集合影响。</b>使用同一状态方程、热数据与入口条件，比较完整源物种与受限物种。当前尚无本页自动执行该比较的功能。</li><li><b>区分状态方程影响。</b>现有“理想气体 / PR”整机对照固定共同 16 物种；仍须核对两端输入、实际部件模型及热侧算法的一致性。</li><li><b>区分数值校核与工程验证。</b>守恒、热力学恒等式和参考实现对照用于检查计算实现。模型在实际工况下的预测精度需由独立试验或适用的参考数据评估。</li></ol><p>PR–BM 已开展热力学一致性与数值实现校核。该证据不构成高压混合物精度、多相稳定性或高压输运验证；比较结果的差值也不能直接称为误差。</p>`)+
    section("旧版本温度修正",`<p>常规热化学计算按焓平衡求温度，无需另设温度折减系数。旧版本曾在平衡计算后冻结组成，并按经验系数降低温升，再将对应焓差计入经验热汇。该方法已退出常规设计执行；原参数仅以只读信息保留，用于解释历史结果。</p><p>旧版本温升折减系数不等同于特征速度效率、反应完成度或试验标定的燃烧效率，也不能用于将主室温度调整到预期值。含有该修正的历史方案，须明确选择本次采用的燃气物性模型，取消旧修正，确认保存后再提交设计点计算。直接打开页面或保存其他参数不执行此转换。</p><p>转换不会改写原计算记录。保存后形成新方案版本，新计算采用转换后的模型，原输入、原结果及其模型身份仍可追溯。</p>`)+
    section("方法参考",`<p><a href="https://www.cantera.org/stable/python/thermo.html#cantera.ThermoPhase.equilibrate" target="_blank" rel="noopener noreferrer">Cantera：化学平衡约束</a>；<a href="https://www.cantera.org/stable/reference/thermo/species-thermo.html" target="_blank" rel="noopener noreferrer">Cantera：物种标准态热力学模型</a>；<a href="https://cearun.grc.nasa.gov/intro.html" target="_blank" rel="noopener noreferrer">NASA CEA：焓压力约束与喷管膨胀假设</a>。</p><p>参考程序的方法说明不能替代当前实现的验证记录。CEA 用于独立热化学对照，本平台的循环计算采用方案中登记的模型与计算实现。</p>`);
}
function article(item,{groups,inputs,catalog}) {
  if(item.group?.id==="cycle")return configurationGuide();
  if(item.group)return `<p class="help-context-note">${item.group.id==="chamber_chemistry"?"依据当前版本的参数定义及当前计算设置显示。":"依据当前版本的参数定义及概念设计草稿所选模式显示。"}此处为只读参考，实际取值、来源记录和确认状态请在参数设置中核对。</p>${item.group.engine_definition?configurationGuide():item.group.id==="inlet"?supplyConditionsGuide():item.group.id==="chamber_chemistry"?thermochemistryGuide(inputs):""}${renderParameterReference(item.group,inputs)}`;
  return {workflow:()=>workflow(),theory:()=>theory(inputs),parameters:()=>parameters(groups),
    modes:()=>modes(catalog,inputs),properties:()=>propertyGuide(),examples:()=>examples(),troubleshooting:()=>troubleshooting()}[item.id]();
}
function textContent(html) {
  return html.replace(/<[^>]*>/g," ").replace(/&(amp|lt|gt|quot|#39);/g,(_,key)=>({amp:"&",lt:"<",gt:">",quot:'"',"#39":"'"}[key])).replace(/\s+/g," ").trim();
}
export function searchHelpManual(query,{groups=[],inputs={},catalog=null}={}) {
  const words=String(query??"").trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  if(!words.length)return [];
  groups=referenceGroups(groups);
  return chapters(groups).flatMap(item=>{
    const body=textContent(article(item,{groups,inputs,catalog}));
    const full=`${item.title} ${item.description} ${body}`, lower=full.toLocaleLowerCase();
    if(!words.every(word=>lower.includes(word)))return [];
    const position=lower.indexOf(words[0]), start=Math.max(0,position-25);
    return [{id:item.id,title:item.title,snippet:`${start?"…":""}${full.slice(start,start+140)}${full.length>start+140?"…":""}`}];
  });
}

export function renderHelpManual({chapter="workflow",query="",groups=[],inputs={},catalog=null,version="",returnLabel="返回工作台"}={}) {
  groups=referenceGroups(groups);inputs=inputs||{};query=String(query??"");
  chapter=normalizeHelpChapter(chapter,groups);
  const items=chapters(groups), selected=items.find(item=>item.id===chapter);
  const searching=Boolean(query.trim()), results=searching?searchHelpManual(query,{groups,inputs,catalog}):[];
  const navButton=item=>`<button type="button" data-help-chapter="${escape(item.id)}"${item.id===chapter?' aria-current="page"':""}>${escape(item.title)}</button>`;
  const searchResults=searching?`<section class="help-search-results" aria-labelledby="help-search-title"><h3 id="help-search-title">搜索结果 <span role="status">${results.length} 个章节</span></h3>${results.length?`<ul>${results.map(item=>`<li>${chapterButton(item.id,item.title)}<p>${escape(item.snippet)}</p></li>`).join("")}</ul>`:'<p class="help-empty">未找到匹配章节。可按参数名称、单位或说明中的关键词查找。</p>'}</section>`:"";
  return `<section class="help-manual" aria-label="帮助文档">
    <header class="help-toolbar"><div><span class="help-eyebrow">本地帮助文档</span><p class="help-version">文档依据：${escape(version||"版本未登记")} · 随当前安装版本提供</p></div><button type="button" data-help-return>${escape(returnLabel)}</button></header>
    <div class="help-layout"><aside class="help-directory" aria-label="文档目录"><label for="help-search">查找帮助文档</label><div class="help-search-control"><input id="help-search" type="search" autocomplete="off" value="${escape(query)}" placeholder="章节、参数名称、单位或关键词"><button type="button" data-help-clear${searching?"":" hidden"}>清除</button></div><nav aria-label="帮助章节">${CHAPTERS.map(navButton).join("")}${groups.length?`<details class="help-group-directory"${chapter.startsWith("parameter:")?" open":""}><summary>构型与参数参考</summary>${items.filter(item=>item.group).map(navButton).join("")}</details>`:""}</nav></aside>
    <div class="help-reading">${searchResults}<article class="help-article" aria-labelledby="help-article-title" data-help-current="${escape(chapter)}"><header><p class="help-eyebrow">${selected.group?.id==="cycle"?"构型与循环说明":selected.group?"参数参考":"使用与参考"}</p><h2 id="help-article-title" tabindex="-1">${escape(selected.title)}</h2><p class="help-description">${escape(selected.description)}</p></header>${article(selected,{groups,inputs,catalog})}</article></div></div>
  </section>`;
}
