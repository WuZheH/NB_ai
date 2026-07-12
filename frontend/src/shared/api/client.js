const DEFAULT_API_BASE_URL = "http://127.0.0.1:8000";

export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || DEFAULT_API_BASE_URL;

export async function requestJson(path, options = {}) {
  const {
    method = "GET",
    body,
    headers = {},
    signal,
    timeoutMs,
  } = options;
  const normalizedMethod = String(method || "GET").toUpperCase();
  const requestHeaders = {
    Accept: "application/json",
    ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    ...headers,
  };
  const signalState = createRequestSignal({ signal, timeoutMs });
  const requestOptions = {
    method: normalizedMethod,
    headers: requestHeaders,
  };
  if (body !== undefined) requestOptions.body = JSON.stringify(body);
  if (signalState.signal) requestOptions.signal = signalState.signal;

  try {
    const response = await fetch(`${API_BASE_URL}${path}`, requestOptions);
    if (!response.ok) {
      const text = await response.text().catch(() => "");
      const error = new Error(requestErrorMessage(normalizedMethod, path, response.status, text));
      error.status = response.status;
      error.payload = parseErrorPayload(text);
      throw error;
    }
    return response.json();
  } finally {
    signalState.cleanup();
  }
}

export function getJson(path, options = {}) {
  return requestJson(path, { ...options, method: "GET" });
}

export function postJson(path, body, options = {}) {
  return requestJson(path, { ...options, method: "POST", body });
}

function parseErrorPayload(text) {
  try {
    return text ? JSON.parse(text) : null;
  } catch {
    return null;
  }
}

function requestErrorMessage(method, path, status, text) {
  if (method === "GET") return `API request failed with status ${status}: ${text}`;
  if (method === "POST") return `POST ${path} failed (${status}): ${text}`;
  return `${method} ${path} failed (${status}): ${text}`;
}

function createRequestSignal({ signal, timeoutMs }) {
  const timeout = Number(timeoutMs);
  if (!Number.isFinite(timeout) || timeout <= 0) {
    return { signal, cleanup() {} };
  }

  const controller = new AbortController();
  const abortFromExternalSignal = () => controller.abort(signal?.reason);
  if (signal?.aborted) {
    abortFromExternalSignal();
  } else if (signal) {
    signal.addEventListener("abort", abortFromExternalSignal, { once: true });
  }
  const timer = setTimeout(() => controller.abort(new Error(`Request timed out after ${timeout}ms`)), timeout);

  return {
    signal: controller.signal,
    cleanup() {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abortFromExternalSignal);
    },
  };
}
