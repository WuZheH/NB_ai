let currentSearchSession = null;
let captureSearchSession = null;

export function readSearchSession() {
  return currentSearchSession;
}

export function writeSearchSession(session) {
  currentSearchSession = session || null;
}

export function registerSearchSessionCapture(capture) {
  captureSearchSession = typeof capture === "function" ? capture : null;
  return () => {
    if (captureSearchSession === capture) captureSearchSession = null;
  };
}

export function captureSearchSessionBeforeNavigation() {
  captureSearchSession?.();
}

export function clearSearchSessionForTests() {
  currentSearchSession = null;
  captureSearchSession = null;
}
