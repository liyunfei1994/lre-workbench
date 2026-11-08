import assert from "node:assert/strict";
import test from "node:test";
import {renderAnalysisParameterRoles,renderParameterField,renderParameterGroups} from "../webapp/static/parameter-editor.js";

test("the role comparison uses its supplied catalog, escapes text and highlights the selected mode",()=>{
  const catalog={version:1,rows:[
    {id:'valve_hardware',label:'阀门尺寸与特性',concept_design:{description:'计算所需节流能力'},fixed_hardware_steady:{description:'硬件特性固定'}},
    {id:'valve_opening',label:'阀门开度',concept_design:{description:'设计参考'},fixed_hardware_steady:{description:'运行设定，可以调节'}},
    {id:'<img>',label:'<script>unsafe</script>',concept_design:{description:'<img src=x>'},fixed_hardware_steady:{description:'<b>unsafe</b>'}},
  ],principles:['固定尺寸与特性，不固定开度','<svg onload=x>']};
  const html=renderAnalysisParameterRoles(catalog,'fixed_hardware_steady');
  assert.match(html,/<summary>分析模式参数对照<\/summary>/);
  assert.match(html,/<th scope="col" class="is-current">固定硬件稳态 · 当前模式/);
  assert.equal((html.match(/data-quantity=/g)||[]).length,catalog.rows.length);
  assert.match(html,/硬件特性固定/);assert.match(html,/运行设定，可以调节/);
  assert.doesNotMatch(html,/<img|<script|<svg|<b>unsafe/);
  assert.match(html,/&lt;script&gt;/);assert.match(html,/&lt;svg/);
  assert.equal(renderAnalysisParameterRoles(null,'concept_design'),'');
  assert.equal(renderAnalysisParameterRoles({rows:[]},'concept_design'),'');
});

test("field help and compact parameter notes preserve the fixed-mode identity without creating controls",()=>{
  const field={key:'pc',label:'主室压力',role:'design_input',role_label:'设计输入',default:1,factor:1,help:'当前为概念设计输入',
    analysis_modes:{fixed_hardware_steady:{roles:['result'],description:'通常为工作点结果 <script>x</script>'}}};
  for(const html of [renderParameterField(field,{pc:3}),renderParameterGroups([{id:'design',title:'设计',fields:[field]}],{pc:3})]){
    assert.match(html,/固定硬件稳态：通常为工作点结果 &lt;script&gt;x&lt;\/script&gt;/);
    assert.equal((html.match(/data-input=/g)||[]).length,1);
    assert.doesNotMatch(html,/data-input="control|<script>/);
  }
});
