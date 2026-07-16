import { useEffect, useMemo, useState } from "react";

export default function DesktopSettingsPage({ section = "status" }) {
  const bridge = typeof window !== "undefined" ? window.searchDesktop : null;
  const [runtime, setRuntime] = useState(null);
  const [settings, setSettings] = useState({ minimizeToTray: true });
  const [autostart, setAutostart] = useState(null);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const presentation = useMemo(() => presentRuntime(runtime), [runtime]);

  useEffect(() => {
    if (!bridge) return undefined;
    void refresh();
    return bridge.onRuntimeStatus?.((value) => setRuntime(value));
  }, []);

  async function refresh() {
    if (!bridge || busy) return;
    setBusy("refresh");
    try {
      const [runtimeValue, settingsValue, autostartValue] = await Promise.all([
        bridge.getRuntimeStatus(),
        bridge.getSettings(),
        bridge.getAutostartStatus(),
      ]);
      setRuntime(runtimeValue);
      setSettings(settingsValue);
      setAutostart(autostartValue);
      setMessage("");
    } catch (error) {
      setMessage(desktopError(error));
    } finally {
      setBusy("");
    }
  }

  async function updateSetting(action, operation) {
    if (!bridge || busy) return;
    setBusy(action);
    setMessage("");
    try {
      await operation();
      setMessage("设置已更新。");
      const [settingsValue, autostartValue] = await Promise.all([
        bridge.getSettings(),
        bridge.getAutostartStatus(),
      ]);
      setSettings(settingsValue);
      setAutostart(autostartValue);
    } catch (error) {
      setMessage(desktopError(error));
    } finally {
      setBusy("");
    }
  }

  if (!bridge) {
    return (
      <main className="desktopSettingsPage">
        <header><span>SEARCH DESKTOP</span><h1>{section === "settings" ? "设置" : "系统状态"}</h1></header>
        <section className="search-state-card desktopHostNotice">
          <strong>当前是浏览器模式</strong>
          <p>本地检索仍可使用；统一后端状态仅在 Search Desktop 中显示。</p>
        </section>
      </main>
    );
  }

  return (
    <main className="desktopSettingsPage">
      <header>
        <span>SEARCH DESKTOP</span>
        <h1>{section === "settings" ? "设置" : "系统状态"}</h1>
        <p>{presentation.summary}</p>
      </header>

      <section className="search-card desktopServiceList" aria-label="统一后端状态">
        {presentation.rows.map((row) => (
          <div className="desktopServiceRow" key={row.name}>
            <span>{row.name}</span>
            <strong className={`desktopServiceValue status-${row.tone}`}>{row.label}</strong>
            <small>{row.note}</small>
          </div>
        ))}
      </section>

      <div className="desktopSettingsFooter">
        <button type="button" className="search-button search-button-primary" disabled={Boolean(busy)} onClick={() => void refresh()}>
          {busy === "refresh" ? "检查中" : "重新检查"}
        </button>
        {message && <span role="status">{message}</span>}
      </div>

      <details className="search-card desktopTechnicalDetails">
        <summary>技术详情</summary>
        <dl>
          <Detail label="最后检查" value={runtime?.updated_at} />
          <Detail label="FastAPI" value={componentDetail(runtime?.components?.fastapi)} />
          <Detail label="MCP" value={componentDetail(runtime?.components?.mcp)} />
          <Detail label="ChatGPT Tunnel" value={tunnelDetail(runtime)} />
          <Detail label="Desktop" value={`${bridge.productVersion || "unknown"} · ${bridge.buildId || "unknown"}`} />
          <Detail label="Renderer" value={bridge.rendererAssetVersion || "unknown"} />
        </dl>
      </details>

      {section === "settings" && (
        <section className="search-card desktopControlPanel">
          <h2>桌面行为</h2>
          <label className="desktopSettingRow">
            <span>
              <strong>登录 Windows 后自动启动 Search</strong>
              <small>{autostart?.available === false
                ? "仅打包后的 Search Desktop 可配置。"
                : "使用当前用户 Task Scheduler 任务，不修改注册表。"}</small>
            </span>
            <input
              type="checkbox"
              checked={autostartEnabled(autostart)}
              disabled={Boolean(busy) || autostart?.available !== true}
              onChange={(event) => updateSetting("autostart", () => bridge.setAutostartEnabled(event.target.checked))}
            />
          </label>
          <label className="desktopSettingRow">
            <span>
              <strong>关闭窗口时最小化到托盘</strong>
              <small>完全退出仅处理本次 Search 明确启动的后端 PID。</small>
            </span>
            <input
              type="checkbox"
              checked={settings.minimizeToTray !== false}
              disabled={Boolean(busy)}
              onChange={(event) => updateSetting("settings", () => bridge.updateSettings({ minimizeToTray: event.target.checked }))}
            />
          </label>
          {message && <p role="status">{message}</p>}
        </section>
      )}
    </main>
  );
}

export function presentRuntime(runtime) {
  const fastapi = serviceState(runtime?.components?.fastapi);
  const mcp = serviceState(runtime?.components?.mcp);
  const tunnel = tunnelState(runtime);
  return {
    summary: fastapi.tone === "ready" && mcp.tone === "ready"
      ? tunnel.tone === "ready"
        ? "本地后端已就绪；ChatGPT Tunnel 当前在线。"
        : "本地后端已就绪；ChatGPT 仍需要持久 Tunnel。"
      : "Search 可继续显示，但部分后端当前不可用。",
    rows: [
      { name: "检索后端", ...fastapi, note: "127.0.0.1:8000" },
      { name: "MCP 后端", ...mcp, note: "127.0.0.1:8787/mcp" },
      { name: "Codex MCP", ...clientBackendState(mcp), note: "仅表示本地后端，不代表 Codex 已调用" },
      { name: "Zotero 后端", ...clientBackendState(fastapi), note: "仅表示本地后端，不代表 Zotero 已打开" },
      { name: "ChatGPT Tunnel", ...tunnel, note: tunnel.note },
    ],
  };
}

function serviceState(component = {}) {
  const state = String(component?.state || "unknown");
  if (state === "ready" || state === "external") return { label: "已就绪", tone: "ready" };
  if (state === "starting") return { label: "启动中", tone: "warning" };
  return { label: "不可用", tone: "error" };
}

function clientBackendState(service) {
  if (service.tone === "ready") return { label: "后端已就绪", tone: "ready" };
  if (service.label === "启动中") return { label: "启动中", tone: "warning" };
  return { label: "不可用", tone: "error" };
}

function tunnelState(runtime = {}) {
  const state = String(runtime?.tunnel_state || runtime?.components?.tunnel?.state || "");
  if (state === "quick_tunnel_online") {
    return { label: "临时在线", tone: "ready", note: "Quick Tunnel 地址会变化，不能视为永久连接" };
  }
  if (state === "persistent_tunnel_online") {
    return { label: "持久在线", tone: "ready", note: "已只读验证 named tunnel 在线" };
  }
  if (state === "persistent_tunnel_configured" || runtime?.tunnel_type === "named") {
    return { label: "不可用", tone: "error", note: "已发现持久配置，但公网健康检查未通过" };
  }
  if (state === "tunnel_not_configured" || !state) {
    return { label: "需要持久配置", tone: "warning", note: "Phase B 配置 named tunnel 后可获得固定地址" };
  }
  return { label: "不可用", tone: "error", note: "Tunnel 当前未通过健康检查" };
}

function componentDetail(component = {}) {
  const parts = [
    component?.port ? `端口 ${component.port}` : null,
    component?.pid ? `PID ${component.pid}` : null,
    component?.owner ? `owner ${component.owner}` : null,
    component?.error_code ? `错误 ${component.error_code}` : null,
  ];
  return parts.filter(Boolean).join(" · ") || "无可用详情";
}

function tunnelDetail(runtime = {}) {
  const parts = [
    runtime?.tunnel_type ? `类型 ${runtime.tunnel_type}` : null,
    runtime?.components?.tunnel?.pid ? `PID ${runtime.components.tunnel.pid}` : null,
    runtime?.tunnel_url || null,
  ];
  return parts.filter(Boolean).join(" · ") || "未配置";
}

function Detail({ label, value }) {
  return <div><dt>{label}</dt><dd>{value || "unknown"}</dd></div>;
}

function autostartEnabled(value) {
  return value?.installed === true || value?.enabled === true || value?.status === "installed";
}

function desktopError(error) {
  return error?.message || "Search Desktop 操作失败。";
}
