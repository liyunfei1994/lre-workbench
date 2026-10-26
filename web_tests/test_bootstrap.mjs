import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";
import test from "node:test";

const code=readFileSync(new URL("../webapp/static/bootstrap.js",import.meta.url),"utf8")
  .replace(/^startWorkbench\(\);\s*$/m,"");
function harness() {
  const nodes=Object.fromEntries(["auth","auth-title","auth-description","auth-form","auth-retry"]
    .map(id=>[id,{hidden:false,textContent:""}]));
  nodes["auth-title"].textContent="正在连接工作台…";
  nodes["auth-retry"].hidden=true;
  let timeout,cleared=0,reloaded=0,errors=0;
  const context=vm.createContext({document:{getElementById:id=>nodes[id]},
    window:{location:{reload(){reloaded++;}}},console:{error(){errors++;}},
    setTimeout(callback){timeout=callback;return 1;},clearTimeout(){cleared++;}});
  vm.runInContext(code,context);
  return {nodes,start:context.startWorkbench,expire:()=>timeout(),counts:()=>({cleared,reloaded,errors})};
}

test("missing module and top-level failure produce a recoverable message",async()=>{
  for (const error of [new TypeError("Failed to fetch dynamically imported module"),new SyntaxError("Invalid token")]) {
    const h=harness();
    await h.start(async()=>{throw error;});
    assert.equal(h.nodes["auth-title"].textContent,"页面程序未能启动");
    assert.equal(h.nodes["auth-form"].hidden,true);
    assert.equal(h.nodes["auth-retry"].hidden,false);
    h.nodes["auth-retry"].onclick();
    assert.deepEqual(h.counts(),{cleared:1,reloaded:1,errors:1});
  }
});

test("successful app startup and app-owned connection errors are preserved",async()=>{
  for (const title of ["欢迎回到工作台","连接未完成"]) {
    const h=harness();
    await h.start(async()=>{h.nodes["auth-title"].textContent=title;});
    assert.equal(h.nodes["auth-title"].textContent,title);
    assert.deepEqual(h.counts(),{cleared:1,reloaded:0,errors:0});
  }
});

test("a stuck module request times out without changing an active workspace",async()=>{
  const h=harness();let resolve;
  const running=h.start(()=>new Promise(done=>{resolve=done;}));
  h.expire();
  assert.match(h.nodes["auth-description"].textContent,/加载超时/);
  resolve();await running;
  const active=harness();active.nodes.auth.hidden=true;
  await active.start(async()=>{throw new Error("late failure");});
  assert.equal(active.nodes["auth-retry"].hidden,true);
});
