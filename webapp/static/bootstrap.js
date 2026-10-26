// Keep startup recovery independent of the application's entire module graph.
// A missing imported module otherwise prevents even app.js's error UI running.
async function startWorkbench(load = () => import("./app.js")) {
  const node = id => document.getElementById(id);
  function showFailure(description) {
    if (node("auth")?.hidden) return;
    node("auth-title").textContent = "页面程序未能启动";
    node("auth-description").textContent = description;
    node("auth-form").hidden = true;
    const retry = node("auth-retry");
    retry.textContent = "重新加载页面";
    retry.hidden = false;
    retry.onclick = () => window.location.reload();
  }
  const timeout = setTimeout(() => {
    if (node("auth-title").textContent === "正在连接工作台…") {
      showFailure("页面加载超时。请重新加载；如仍无法进入，请重新启动工作台服务。");
    }
  }, 35000);
  try {
    await load();
  } catch (error) {
    showFailure("页面文件加载失败。本机服务可能仍正常，请按 Ctrl+F5 强制刷新；如仍失败，请重新启动工作台服务。");
    console.error("Workbench startup failed:", error);
  } finally {
    clearTimeout(timeout);
  }
}

startWorkbench();
