export const LOCAL_READY_STATES = new Set(["ready", "local_ready_tunnel_missing"]);
export const REUSABLE_STATES = new Set([
  "ready",
  "local_ready_tunnel_missing",
  "starting",
]);

export function componentReady(status, name) {
  const state = status?.components?.[name]?.state;
  return state === "ready" || state === "external";
}

export function localRuntimeReady(status) {
  return (
    LOCAL_READY_STATES.has(status?.state) &&
    componentReady(status, "fastapi") &&
    componentReady(status, "mcp")
  );
}

export function desktopStatus(status) {
  if (!status || status.status === "error") {
    return { code: "failed", label: "启动失败", localReady: false, mcpReady: false };
  }
  const fastapiReady = componentReady(status, "fastapi");
  const mcpReady = componentReady(status, "mcp");
  if (status.state === "starting") {
    return { code: "starting", label: "正在启动", localReady: false, mcpReady };
  }
  if (fastapiReady && mcpReady && status.tunnel_state === "persistent_tunnel_online") {
    return { code: "ready", label: "本地搜索与 MCP 已就绪", localReady: true, mcpReady: true };
  }
  if (fastapiReady && mcpReady && status.tunnel_state === "quick_tunnel_online") {
    return {
      code: "local_ready_tunnel_missing",
      label: "本地后端已就绪 · ChatGPT 临时 Tunnel 在线",
      localReady: true,
      mcpReady: true,
    };
  }
  if (fastapiReady && mcpReady) {
    return {
      code: "local_ready_tunnel_missing",
      label: "本地后端已就绪 · ChatGPT 需要持久 Tunnel",
      localReady: true,
      mcpReady: true,
    };
  }
  if (fastapiReady || mcpReady) {
    return { code: "degraded", label: "部分服务异常", localReady: fastapiReady, mcpReady };
  }
  return { code: "failed", label: "启动失败", localReady: false, mcpReady: false };
}
