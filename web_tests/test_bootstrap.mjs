import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";
import test from "node:test";

const code=readFileSync(new URL("../webapp/static/bootstrap.js",import.meta.url),"utf8")
  .replace(/^startWorkbench\(\);\s*$/m,"");
function harness(protocol="http:",fetchImpl) {
  const nodes=Object.fromEntries(["auth","auth-title","auth-description","auth-form","auth-retry","auth-startup-details","auth-startup-error"]
    .map(id=>[id,{hidden:false,textContent:""}]));
  nodes["auth-title"].textContent="正在连接工作台…";
  nodes["auth-retry"].hidden=true;
  nodes["auth-startup-details"].hidden=true;
  let timeout,cleared=0,reloaded=0,errors=0;
  const context=vm.createContext({document:{getElementById:id=>nodes[id]},
    window:{location:{protocol,origin:protocol+"//pilot.example.org",reload(){reloaded++;}}},console:{error(){errors++;}},
    fetch:fetchImpl,URL,AbortController,
    setTimeout(callback){timeout=callback;return 1;},clearTimeout(){cleared++;}});
  vm.runInContext(code,context);
  return {nodes,start:context.startWorkbench,expire:()=>timeout(),counts:()=>({cleared,reloaded,errors})};
}

test("missing module and top-level failure produce a recoverable message",async()=>{
  for (const error of [new TypeError("Failed to fetch dynamically imported module"),new SyntaxError("Invalid token")]) {
    const h=harness();
    await h.start(async()=>{throw error;});
    assert.equal(h.nodes["auth-title"].textContent,"工作台页面加载失败");
    assert.equal(h.nodes["auth-form"].hidden,true);
    assert.equal(h.nodes["auth-retry"].hidden,false);
    h.nodes["auth-retry"].onclick();
    assert.deepEqual(h.counts(),{cleared:1,reloaded:1,errors:1});
  }
});

test("startup diagnostics find dependency failures and report only asset metadata",async()=>{
  const calls=[];let report;
  const h=harness("https:",async(path,options)=>{
    calls.push(path);
    assert.equal(options.credentials,"same-origin");
    assert.equal(options.redirect,"error");
    if(path==="/api/startup-report") {
      report=JSON.parse(options.body);
      return {ok:true,json:async()=>({id:"aabbcc112233"})};
    }
    if(path==="/static/app.js") return {ok:true,status:200,headers:{get:()=>"text/javascript"},
      text:async()=>`import {render} from './results.js'; import './missing.js'; import './wrong-mime.js'; import './offline.js';`};
    if(path==="/static/results.js") return {ok:true,status:200,headers:{get:()=>"text/javascript"},text:async()=>`import './app.js';`};
    if(path==="/static/missing.js") return {ok:false,status:404,headers:{get:()=>"application/json"}};
    if(path==="/static/wrong-mime.js") return {ok:true,status:200,headers:{get:()=>"text/html"}};
    throw new TypeError("network failure");
  });
  await h.start(async()=>{throw new TypeError("a detail that must not be uploaded");});
  assert.equal(report.files.length,5);
  assert.deepEqual(report.files.filter(f=>f.kind!=="ok").map(f=>f.kind).sort(),["http","mime","network"]);
  assert.equal(calls.filter(path=>path==="/static/app.js").length,1);
  assert.doesNotMatch(JSON.stringify(report),/detail|password|cookie|https:/);
  assert.match(h.nodes["auth-startup-error"].textContent,/3 个请求失败/);
  assert.match(h.nodes["auth-startup-error"].textContent,/aabbcc112233/);
  assert.equal(h.nodes["auth-startup-details"].open,true);
});

test("successful app startup and app-owned connection errors are preserved",async()=>{
  for (const title of ["欢迎回到工作台","连接未完成"]) {
    const h=harness();
    await h.start(async()=>{h.nodes["auth-title"].textContent=title;});
    assert.equal(h.nodes["auth-title"].textContent,title);
    assert.deepEqual(h.counts(),{cleared:1,reloaded:0,errors:0});
  }
});

test("HTTPS visitors receive a precise error and administrator guidance",async()=>{
  const h=harness("https:");
  const message="Failed to fetch dynamically imported module: https://pilot.example.org/static/app.js";
  await h.start(async()=>{throw new TypeError(message);});
  assert.match(h.nodes["auth-description"].textContent,/平台管理员/);
  assert.doesNotMatch(h.nodes["auth-description"].textContent,/本机|重新启动/);
  assert.equal(h.nodes["auth-startup-details"].hidden,false);
  assert.equal(h.nodes["auth-startup-error"].textContent,"TypeError: "+message);
  const pending=harness("https:");let resolve;
  const running=pending.start(()=>new Promise(done=>{resolve=done;}));
  pending.expire();
  assert.match(pending.nodes["auth-description"].textContent,/平台管理员/);
  assert.doesNotMatch(pending.nodes["auth-description"].textContent,/重新启动/);
  resolve();await running;
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
