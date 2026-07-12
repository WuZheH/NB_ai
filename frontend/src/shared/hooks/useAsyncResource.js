import { useCallback, useRef, useState } from "react";

export function useAsyncResource(loader, initialData = null) {
  const requestId = useRef(0);
  const [state, setState] = useState({
    status: "idle",
    data: initialData,
    error: null,
  });

  const load = useCallback(async (...args) => {
    const activeRequestId = ++requestId.current;
    setState((current) => ({ ...current, status: "loading", error: null }));
    try {
      const data = await loader(...args);
      if (requestId.current === activeRequestId) {
        setState({ status: "ready", data, error: null });
      }
      return data;
    } catch (error) {
      if (requestId.current === activeRequestId) {
        setState((current) => ({ ...current, status: "error", error }));
      }
      throw error;
    }
  }, [loader]);

  const reset = useCallback((data = initialData) => {
    requestId.current += 1;
    setState({ status: "idle", data, error: null });
  }, [initialData]);

  return { ...state, load, reset };
}
