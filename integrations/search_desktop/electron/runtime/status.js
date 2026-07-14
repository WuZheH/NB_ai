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
  if (fastapiReady && mcpReady && status.tunnel_state === "tunnel_ready") {
    return { code: "ready", label: "本地搜索与 MCP 已就绪", localReady: true, mcpReady: true };
  }
  if (fastapiReady && mcpReady) {
    return {
      code: "local_ready_tunnel_missing",
      label: "本地搜索已就绪 · 安全通道未配置",
      localReady: true,
      mcpReady: true,
    };
  }
  if (fastapiReady || mcpReady) {
    return { code: "degraded", label: "部分服务异常", localReady: fastapiReady, mcpReady };
  }
  return { code: "failed", label: "启动失败", localReady: false, mcpReady: false };
}
