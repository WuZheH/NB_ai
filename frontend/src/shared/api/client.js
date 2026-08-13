const DEFAULT_API_BASE_URL = "http://127.0.0.1:8000";
const VITE_API_BASE_URL = typeof import.meta.env === "object"
  ? import.meta.env.VITE_API_BASE_URL
  : "";

export const API_BASE_URL =
  VITE_API_BASE_URL || DEFAULT_API_BASE_URL;

export class ApiRequestError extends Error {
  constructor(message, {
    code = "api_request_failed",
    status = null,
    payload = null,
    backendCode = null,
    cause,
  } = {}) {
    super(message, cause === undefined ? undefined : { cause });
    this.name = "ApiRequestError";
    this.code = code;
    this.status = status;
    this.payload = payload;
    this.backendCode = backendCode;
  }
}

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
    let response;
    try {
      response = await fetch(`${API_BASE_URL}${path}`, requestOptions);
    } catch (error) {
      const timedOut = signalState.didTimeout();
      throw new ApiRequestError(
        timedOut ? "Local API request timed out." : "Local API connection failed.",
        {
          code: timedOut ? "api_request_timeout" : "api_connection_failed",
          cause: error,
        },
      );
    }

    const text = await response.text().catch(() => "");
    const payload = parseJsonPayload(text);
    if (!response.ok) {
      const backendCode = extractBackendErrorCode(payload);
      throw new ApiRequestError(
        requestErrorMessage(normalizedMethod, path, response.status),
        {
          code: backendCode || httpErrorCode(response.status),
          status: response.status,
          payload,
          backendCode,
        },
      );
    }

    const contentType = String(response.headers.get("content-type") || "").toLowerCase();
    if (!isJsonContentType(contentType)) {
      throw new ApiRequestError("Local API returned a non-JSON response.", {
        code: "api_response_content_type_invalid",
        status: response.status,
      });
    }
    if (payload === null && text.trim() !== "null") {
      throw new ApiRequestError("Local API returned invalid JSON.", {
        code: "api_response_json_invalid",
        status: response.status,
      });
    }
    return payload;
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

export function createApiContractError(code, message) {
  return new ApiRequestError(message, { code });
}

function parseJsonPayload(text) {
  try {
    return text ? JSON.parse(text) : null;
  } catch {
    return null;
  }
}

function requestErrorMessage(method, path, status) {
  return `${method} ${path} failed with HTTP ${status}.`;
}

function extractBackendErrorCode(payload) {
  const candidates = [
    payload?.error_code,
    payload?.detail?.error_code,
  ];
  return candidates.find((value) => /^[A-Za-z0-9_.-]{1,96}$/.test(String(value || ""))) || null;
}

function httpErrorCode(status) {
  if (status === 404) return "api_endpoint_not_found";
  if (status === 405) return "api_method_not_allowed";
  if (status === 422) return "api_request_validation_failed";
  if (status >= 500) return "api_internal_error";
  return "api_http_error";
}

function isJsonContentType(value) {
  return /(^|\s|;)application\/([a-z0-9.+-]*\+)?json(?:\s*;|$)/i.test(value);
}

function createRequestSignal({ signal, timeoutMs }) {
  const timeout = Number(timeoutMs);
  if (!Number.isFinite(timeout) || timeout <= 0) {
    return { signal, didTimeout: () => false, cleanup() {} };
  }

  const controller = new AbortController();
  let timedOut = false;
  const abortFromExternalSignal = () => controller.abort(signal?.reason);
  if (signal?.aborted) {
    abortFromExternalSignal();
  } else if (signal) {
    signal.addEventListener("abort", abortFromExternalSignal, { once: true });
  }
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort(new Error(`Request timed out after ${timeout}ms`));
  }, timeout);

  return {
    signal: controller.signal,
    didTimeout: () => timedOut,
    cleanup() {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abortFromExternalSignal);
    },
  };
}
