(() => {
  const bridge = window.searchDesktop;
  const isSettings = window.location.pathname === "/settings";
  const elements = Object.fromEntries(
    [...document.querySelectorAll("[id]")].map((element) => [element.id, element]),
  );
  document.querySelector(`[data-route="${isSettings ? "settings" : "system-status"}"]`)?.setAttribute("aria-current", "page");
  elements.pageTitle.textContent = isSettings ? "设置" : "系统状态";
  elements.statusSection.hidden = isSettings;
  elements.settingsSection.hidden = !isSettings;
  elements.technical.hidden = isSettings;
  elements.refresh.textContent = isSettings ? "重新读取" : "重新检查";

  if (!bridge) {
    elements.summary.textContent = "当前不是 Search Desktop 环境。";
    elements.refresh.disabled = true;
    return;
  }

  const unsubscribe = bridge.onRuntimeStatus?.((runtime) => renderRuntime(runtime));
  window.addEventListener("beforeunload", () => unsubscribe?.(), { once: true });
  elements.refresh.addEventListener("click", () => refresh());
  elements.autostart.addEventListener("change", async () => {
    await updateSetting(() => bridge.setAutostartEnabled(elements.autostart.checked));
  });
  elements.minimizeToTray.addEventListener("change", async () => {
    await updateSetting(() => bridge.updateSettings({ minimizeToTray: elements.minimizeToTray.checked }));
  });
  void refresh();

  async function refresh() {
    setBusy(true);
    try {
      const [runtime, settings, autostart] = await Promise.all([
        bridge.getRuntimeStatus(),
        bridge.getSettings(),
        bridge.getAutostartStatus(),
      ]);
      renderRuntime(runtime);
      elements.minimizeToTray.checked = settings?.minimizeToTray !== false;
      elements.autostart.checked = autostart?.installed === true || autostart?.enabled === true || autostart?.status === "installed";
      elements.autostart.disabled = autostart?.available !== true;
      elements.message.textContent = "";
    } catch (error) {
      elements.message.textContent = error?.message || "状态检查失败";
    } finally {
      setBusy(false);
    }
  }

  async function updateSetting(operation) {
    setBusy(true);
    try {
      await operation();
      elements.message.textContent = "设置已更新。";
    } catch (error) {
      elements.message.textContent = error?.message || "设置更新失败";
    } finally {
      setBusy(false);
    }
  }

  function renderRuntime(runtime = {}) {
    const fastapi = service(runtime.components?.fastapi);
    const mcp = service(runtime.components?.mcp);
    const tunnel = tunnelService(runtime);
    setState(elements.fastapiState, fastapi);
    setState(elements.mcpState, mcp);
    setState(elements.codexState, clientService(mcp));
    setState(elements.zoteroState, clientService(fastapi));
    setState(elements.tunnelState, tunnel);
    elements.tunnelNote.textContent = tunnel.note;
    elements.summary.textContent = fastapi.tone === "ready" && mcp.tone === "ready"
      ? tunnel.tone === "ready"
        ? "本地后端已就绪；ChatGPT Tunnel 当前在线。"
        : "本地后端已就绪；ChatGPT 仍需要持久 Tunnel。"
      : "Search 可继续显示，但部分后端当前不可用。";
    elements.checkedAt.textContent = runtime.updated_at || "unknown";
    elements.fastapiDetail.textContent = componentDetail(runtime.components?.fastapi);
    elements.mcpDetail.textContent = componentDetail(runtime.components?.mcp);
    elements.tunnelDetail.textContent = [runtime.tunnel_type, runtime.components?.tunnel?.pid ? `PID ${runtime.components.tunnel.pid}` : "", runtime.tunnel_url].filter(Boolean).join(" · ") || "未配置";
    elements.desktopDetail.textContent = `${bridge.productVersion || "unknown"} · ${bridge.buildId || "unknown"}`;
  }

  function service(component = {}) {
    if (["ready", "external"].includes(component.state)) return { label: "已就绪", tone: "ready" };
    if (component.state === "starting") return { label: "启动中", tone: "warning" };
    return { label: "不可用", tone: "error" };
  }

  function clientService(value) {
    if (value.tone === "ready") return { label: "后端已就绪", tone: "ready" };
    return value.label === "启动中" ? value : { label: "不可用", tone: "error" };
  }

  function tunnelService(runtime = {}) {
    if (runtime.tunnel_state === "quick_tunnel_online") return { label: "临时在线", tone: "ready", note: "Quick Tunnel 地址会变化，不能视为永久连接" };
    if (runtime.tunnel_state === "persistent_tunnel_online") return { label: "持久在线", tone: "ready", note: "已只读验证 named tunnel 在线" };
    if (runtime.tunnel_state === "persistent_tunnel_configured" || runtime.tunnel_type === "named") return { label: "不可用", tone: "error", note: "已发现持久配置，但公网健康检查未通过" };
    if (runtime.tunnel_state === "tunnel_not_configured" || !runtime.tunnel_state) return { label: "需要持久配置", tone: "warning", note: "Phase B 配置 named tunnel 后可获得固定地址" };
    return { label: "不可用", tone: "error", note: "Tunnel 当前未通过健康检查" };
  }

  function setState(element, value) {
    element.textContent = value.label;
    element.className = `tone-${value.tone}`;
  }

  function componentDetail(component = {}) {
    return [component.port ? `端口 ${component.port}` : "", component.pid ? `PID ${component.pid}` : "", component.owner ? `owner ${component.owner}` : "", component.error_code ? `错误 ${component.error_code}` : ""].filter(Boolean).join(" · ") || "无可用详情";
  }

  function setBusy(value) {
    elements.refresh.disabled = value;
  }
})();
