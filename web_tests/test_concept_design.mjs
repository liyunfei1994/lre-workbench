import assert from "node:assert/strict";
import test from "node:test";
import {renderConceptDesignScope} from "../webapp/static/concept-design.js";

const input = (temperature, mixture) => ({cycle_type:"ffsc", use_target_preburner_temp:temperature, use_target_preburner_mr:mixture});

test("temperature design shows inner pressure-ratio matching and the additional mixture-ratio loop", () => {
  const inputs = {...input(1,1), lox_pump_speed_rpm_override:26500};
  const before = structuredClone(inputs), html = renderConceptDesignScope(inputs);
  assert.match(html, /data-design-mode="temperature"/);
  assert.match(html, /目标推力和两侧涡轮—泵功率平衡/);
  assert.match(html, /总质量流量、富氧侧和富燃侧涡轮压比/);
  assert.match(html, /反求并更新两侧预燃室氧燃质量比/);
  assert.match(html, /级数与几何系数作为本轮初选假设/);
  assert.match(html, /需人工将部件校核后的效率和损失更新/);
  assert.match(html, /未另设轴承、密封等机械损失预算/);
  assert.match(html, /不是独立的性能预测验证/);
  for (const role of ["given","inner","outer","sizing"]) assert.ok(html.includes(`data-concept-role="${role}"`));
  assert.deepEqual(inputs, before);
  assert.doesNotMatch(html, /data-input=|<input|<select|已通过部件验证/);
});

test("mixture-ratio mode does not present its targets as adjustable temperature-loop guesses", () => {
  const html = renderConceptDesignScope(input(0,1));
  assert.match(html, /data-design-mode="mixture_ratio"/);
  assert.match(html, /交叉分流由给定混合比与质量守恒确定/);
  assert.match(html, /不反求预燃室氧燃质量比，出口温度由热化学计算得到/);
  assert.doesNotMatch(html, /反求并更新两侧|两侧预燃室温度目标/);
});

test("historical free splits retain their pressure assumptions and are not described as modern pressure-ratio unknowns", () => {
  const inputs = input(0,0), before = structuredClone(inputs), html = renderConceptDesignScope(inputs);
  assert.match(html, /历史自由分流模式/);
  assert.match(html, /两侧预燃室压力设定/);
  assert.match(html, /进入富氧预燃室的燃料分流/);
  assert.match(html, /不施加预燃室温度或混合比目标/);
  assert.doesNotMatch(html, /总质量流量、富氧侧和富燃侧涡轮压比/);
  assert.deepEqual(inputs, before);
});

test("current missing-flag defaults and temperature precedence match the parameter editor without migrating inputs", () => {
  for (const inputs of [{cycle_type:"ffsc"}, input(1,0)]) {
    const before = structuredClone(inputs);
    assert.match(renderConceptDesignScope(inputs), /data-design-mode="temperature"/);
    assert.deepEqual(inputs, before);
  }
});

test("scope is limited to explicit FFSC drafts and invalid mode text cannot become markup or a valid legacy mode", () => {
  for (const inputs of [null,undefined,{}, {cycle_type:"gg"}, {cycle_type:'<img src=x>'}]) assert.equal(renderConceptDesignScope(inputs), "");
  for (const bad of [NaN,Infinity,'<img src=x onerror="bad()">']) {
    const html = renderConceptDesignScope(input(bad,1));
    assert.match(html, /设计方式标识无效/);
    assert.doesNotMatch(html, /<img|onerror|data-design-mode="legacy|反求并更新/);
  }
});
