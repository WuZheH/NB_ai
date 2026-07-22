import { useCallback, useEffect, useRef, useState } from "react";
import { createApiContractError, getJson } from "../api/client.js";

export const STARTUP_MAX_HEALTH_ATTEMPTS = 6;
export const STARTUP_RETRY_BASE_MS = 250;
export const STARTUP_RETRY_MAX_MS = 4_000;

const INITIAL_STATE = {
  phase: "starting",
  errorCode: null,
  runtime: null,
};

export function useLocalApiStatus() {
  const [state, setState] = useState(INITIAL_STATE);
  const recheckRef = useRef(() => {});
  const runtimeRef = useRef(null);

  useEffect(() => {
    let active = true;
    let generation = 0;
    let retryTimer = null;
    let unsubscribe = () => {};
    const bridge = typeof window !== "undefined" ? window.searchDesktop : null;

    const emit = (next) => {
      if (active) setState(next);
    };
    const cancelRetry = () => {
      if (retryTimer !== null) clearTimeout(retryTimer);
      retryTimer = null;
    };
    const startHealthSequence = (phase, { allowRetry = false, runtime = null } = {}) => {
      cancelRetry();
      const currentGeneration = ++generation;
      emit({ phase, errorCode: null, runtime });

      const attemptHealth = async (attempt) => {
        try {
          const payload = await getJson("/health", { timeoutMs: 2_500 });
          if (payload?.status !== "ok") {
            throw createApiContractError("health_response_invalid", "Health response status is not ok.");
          }
          if (active && generation === currentGeneration) {
            emit({ phase: "connected", errorCode: null, runtime });
          }
        } catch (error) {
          if (!active || generation !== currentGeneration) return;
          const retryable = isRetryableStartupError(error);
          if (allowRetry && retryable && attempt + 1 < STARTUP_MAX_HEALTH_ATTEMPTS) {
            retryTimer = setTimeout(
              () => void attemptHealth(attempt + 1),
              startupRetryDelayMs(attempt),
            );
            return;
          }
          emit({
            phase: "unavailable",
            errorCode: String(error?.code || "api_request_failed"),
            runtime,
          });
        }
      };

      void attemptHealth(0);
    };

    const handleRuntimeStatus = (runtime) => {
      if (!active) return;
      runtimeRef.current = runtime;
      if (runtimeFastApiReady(runtime)) {
        startHealthSequence("starting", { runtime });
        return;
      }
      if (runtimeTerminallyUnavailable(runtime)) {
        cancelRetry();
        generation += 1;
        emit({
          phase: "unavailable",
          errorCode: runtimeErrorCode(runtime),
          runtime,
        });
        return;
      }
      emit({ phase: "starting", errorCode: null, runtime });
    };

    recheckRef.current = () => startHealthSequence("checking", { runtime: runtimeRef.current });

    if (bridge?.getRuntimeStatus) {
      unsubscribe = bridge.onRuntimeStatus?.(handleRuntimeStatus) || (() => {});
      void bridge.getRuntimeStatus()
        .then(handleRuntimeStatus)
        .catch(() => startHealthSequence("starting", { allowRetry: true }));
    } else {
      startHealthSequence("starting", { allowRetry: true });
    }

    return () => {
      active = false;
      generation += 1;
      cancelRetry();
      unsubscribe();
      runtimeRef.current = null;
      recheckRef.current = () => {};
    };
  }, []);

  const recheck = useCallback(() => recheckRef.current(), []);
  return { ...state, recheck };
}

export function runtimeFastApiReady(runtime) {
  return ["ready", "external"].includes(String(runtime?.components?.fastapi?.state || ""));
}

export function runtimeTerminallyUnavailable(runtime) {
  const runtimeState = String(runtime?.state || "");
  const fastApiState = String(runtime?.components?.fastapi?.state || "");
  return ["failed", "unavailable"].includes(runtimeState)
    || fastApiState === "failed"
    || fastApiState === "unavailable";
}

export function runtimeErrorCode(runtime) {
  return String(
    runtime?.components?.fastapi?.error_code
      || runtime?.error_code
      || "api_unavailable",
  );
}

export function isRetryableStartupError(error) {
  return new Set(["api_connection_failed", "api_request_timeout"]).has(String(error?.code || ""));
}

export function startupRetryDelayMs(attempt) {
  return Math.min(STARTUP_RETRY_BASE_MS * (2 ** Math.max(0, attempt)), STARTUP_RETRY_MAX_MS);
}
