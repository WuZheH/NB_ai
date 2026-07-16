(() => {
  const desktopRoutes = new Set(["/system-status", "/settings"]);
  const originalPushState = window.history.pushState.bind(window.history);
  window.history.pushState = (state, title, url) => {
    const target = new URL(String(url || window.location.href), window.location.href);
    if (target.origin === window.location.origin && desktopRoutes.has(target.pathname)) {
      window.location.assign(target.href);
      return;
    }
    originalPushState(state, title, url);
  };
})();
