export interface ToolLogRecord {
  tool:
    | "search"
    | "fetch"
    | "export_evidence"
    | "list_library"
    | "integrity_report"
    | "import_preview"
    | "import_document"
    | "import_status"
    | "delete_preview"
    | "delete_document";
  duration_ms: number;
  result_count?: number;
  error_code?: string;
}

export function logToolInvocation(record: ToolLogRecord): void {
  // Deliberately excludes queries, excerpts, notes, fragment ids, and provenance.
  const safeErrorCode =
    record.error_code && /^[A-Za-z0-9_.-]{1,96}$/.test(record.error_code)
      ? record.error_code
      : record.error_code
        ? "MCP_ADAPTER_ERROR"
        : undefined;
  console.info(JSON.stringify({ event: "mcp_tool", ...record, error_code: safeErrorCode }));
}

export function logDevelopmentWarning(port: number): void {
  console.warn(
    `[SECURITY WARNING] Unauthenticated MCP Developer Mode is enabled on 127.0.0.1:${port}. ` +
      "Use only for a short-lived HTTPS tunnel test and stop the tunnel afterward.",
  );
}
