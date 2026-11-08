// Keep startup recovery independent of the application's entire module graph.
// A missing imported module otherwise prevents even app.js's error UI running.
async function diagnoseWorkbenchStartup(error) {
  const detail = document.getElementById("auth-startup-error");
  if (!detail || typeof fetch !== "function") return;
  const modulePath = /^\/static\/[a-z0-9-]+\.js$/;
  const origin = window.location.origin;
  const pending = ["/static/app.js"], seen = new Set(), files = [];
  const imports = /(?:\bfrom\s*|\bimport\s*(?:\(\s*)?)["']([^"']+\.js)["']/g;
  const started = Date.now();
  async function inspect(path) {
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(),6000);
    try {
      const response = await fetch(path,{credentials:"same-origin",cache:"no-store",redirect:"error",signal:controller.signal});
      const mime = response.headers.get("content-type") || "";
      const kind = !response.ok ? "http" : !/^(text|application)\/javascript\b/i.test(mime) ? "mime" : "ok";
      files.push({path,status:response.status,kind});
      if (kind === "ok") {
        const source = await response.text();
        for (const match of source.matchAll(imports)) {
          const target = new URL(match[1],new URL(path,origin));
          if(target.origin === origin && modulePath.test(target.pathname) && !seen.has(target.pathname)) pending.push(target.pathname);
        }
      }
    } catch { files.push({path,status:0,kind:"network"}); }
    finally { clearTimeout(timer); }
  }
  try {
    while(pending.length && seen.size < 40 && Date.now()-started < 30000) {
      const batch = [];
      while(pending.length && batch.length < 3 && seen.size < 40) {
        const path = pending.shift();
        if(seen.has(path)) continue;
        seen.add(path); batch.push(inspect(path));
      }
      await Promise.all(batch);
    }
    const failed = files.filter(file=>file.kind!=="ok");
    detail.textContent += `\n资源检查：${files.length} 个脚本，${failed.length} 个请求失败。`;
    for(const file of failed) detail.textContent += `\n${file.path}：${file.status ? "HTTP "+file.status+" / "+file.kind : "网络请求未完成或被登录跳转拦截"}`;
    // Only asset paths and numeric results leave the browser. No exception
    // text, page URL, cookies, account details or calculation data are sent.
    if(window.location.protocol === "https:") {
      const controller = new AbortController(), timer = setTimeout(()=>controller.abort(),6000);
      try {
        const response = await fetch("/api/startup-report",{method:"POST",credentials:"same-origin",redirect:"error",
          headers:{"Content-Type":"application/json"},signal:controller.signal,
          body:JSON.stringify({kind:error?.name === "SyntaxError" ? "syntax" : "module_load",files})});
        if(response.ok) {
          const report = await response.json();
          if(/^[a-f0-9]{12}$/.test(report.id || "")) detail.textContent += `\n诊断编号：${report.id}（已发送给平台管理员）`;
        }
      } finally { clearTimeout(timer); }
    }
  } catch { /* Keep the original error readable if diagnostics are unavailable. */ }
}

async function startWorkbench(load = () => import("./app.js")) {
  const node = id => document.getElementById(id);
  const remote = window.location.protocol === "https:";
  function showFailure(description, error) {
    if (node("auth")?.hidden) return;
    node("auth-title").textContent = "工作台页面加载失败";
    node("auth-description").textContent = description;
    node("auth-form").hidden = true;
    const retry = node("auth-retry");
    retry.textContent = "重新加载页面";
    retry.hidden = false;
    retry.onclick = () => window.location.reload();
    if (error && node("auth-startup-details") && node("auth-startup-error")) {
      // Text only: make the actual module failure available without asking a
      // visiting user to install tools or send an entire browser console dump.
      node("auth-startup-error").textContent = `${error.name || "Error"}: ${error.message || String(error)}`.slice(0,1000);
      node("auth-startup-details").hidden = false;
      node("auth-startup-details").open = true;
    }
  }
  const timeout = setTimeout(() => {
    if (node("auth-title").textContent === "正在连接工作台…") {
      showFailure(remote
        ? "页面加载超时。请重新加载；如仍无法进入，请联系平台管理员。"
        : "页面加载超时。请重新加载；如仍无法进入，请重新启动工作台服务。");
    }
  }, 35000);
  try {
    await load();
  } catch (error) {
    showFailure(remote
      ? "页面脚本未能加载或执行。请重新加载；如仍失败，请将下方加载错误反馈给平台管理员。"
      : "页面文件加载失败。本机服务可能仍正常，请按 Ctrl+F5 强制刷新；如仍失败，请重新启动工作台服务。", error);
    console.error("Workbench startup failed:", error);
    await diagnoseWorkbenchStartup(error);
  } finally {
    clearTimeout(timeout);
  }
}

startWorkbench();
