const STATUS_LABELS = Object.freeze({
  starting: "正在启动",
  ready: "本地搜索与 MCP 已就绪",
  local_ready_tunnel_missing: "安全通道未配置",
  degraded: "部分服务异常",
  failed: "启动失败",
});

export function createTrayController({ Tray, Menu, nativeImage, coordinator, windowController, onFullyQuit, iconPath, designTokens }) {
  const icon = createTrayIcon(nativeImage, iconPath, designTokens);
  const tray = new Tray(icon);
  tray.setToolTip("Search");
  function rebuild(status = coordinator.presentation()) {
    tray.setContextMenu(Menu.buildFromTemplate([
      { label: "打开 Search", click: () => windowController.show() },
      { label: `服务状态：${STATUS_LABELS[status.code] || status.label}`, enabled: false },
      { label: "重新检查", click: () => void coordinator.refresh() },
      { label: "打开日志目录", click: () => void coordinator.client.logs().then((value) => windowController.openLogsDirectory(value.logs_dir)) },
      { type: "separator" },
      { label: "完全退出", click: () => void onFullyQuit() },
    ]));
  }

  tray.on("click", () => windowController.show());
  const onStatus = () => rebuild();
  coordinator.on("status", onStatus);
  rebuild();
  return {
    destroy() {
      coordinator.off("status", onStatus);
      tray.destroy();
    },
    rebuild,
  };
}

export function createTrayIcon(nativeImage, iconPath, designTokens) {
  if (typeof iconPath === "string" && iconPath.trim()) {
    try {
      const packagedIcon = nativeImage.createFromPath(iconPath);
      if (packagedIcon && (typeof packagedIcon.isEmpty !== "function" || !packagedIcon.isEmpty())) {
        return packagedIcon;
      }
    } catch {
      // A missing or unreadable packaged icon must not prevent Search from starting.
    }
  }

  return nativeImage.createFromDataURL(
    `data:image/svg+xml;charset=utf-8,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><rect width="24" height="24" rx="5" fill="${designTokens.primary}"/><path d="M6 8h12M6 12h9M6 16h7" stroke="white" stroke-width="2" stroke-linecap="round"/></svg>`)}`,
  );
}
