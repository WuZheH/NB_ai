import { NotebookBackendError } from "../notebookClient.js";

export const WIDGET_RESOURCE_URI = "ui://notebook-ai/research-search-v1.html";

export const READ_ONLY_ANNOTATIONS = Object.freeze({
  readOnlyHint: true,
  destructiveHint: false,
  idempotentHint: true,
  openWorldHint: false,
});

export function toolMetadata(invoking: string, invoked: string): Record<string, unknown> {
  return {
    ui: { resourceUri: WIDGET_RESOURCE_URI },
    "openai/outputTemplate": WIDGET_RESOURCE_URI,
    "openai/toolInvocation/invoking": invoking,
    "openai/toolInvocation/invoked": invoked,
    "openai/widgetAccessible": true,
  };
}

export function jsonContent(value: unknown): Array<{ type: "text"; text: string }> {
  return [{ type: "text", text: JSON.stringify(value) }];
}

export function errorToolResult(error: unknown): {
  isError: true;
  content: Array<{ type: "text"; text: string }>;
  structuredContent: { status: "error"; error_code: string; message: string };
} {
  const code = error instanceof NotebookBackendError ? error.code : "MCP_ADAPTER_ERROR";
  const message = error instanceof Error ? error.message : "Unexpected NOTEBOOK_AI adapter error.";
  const structuredContent = { status: "error" as const, error_code: code, message };
  return { isError: true, content: jsonContent(structuredContent), structuredContent };
}

export function errorCode(error: unknown): string {
  return error instanceof NotebookBackendError ? error.code : "MCP_ADAPTER_ERROR";
}

export function elapsedMilliseconds(startedAt: number): number {
  return Math.max(0, Math.round(performance.now() - startedAt));
}

export function truncate(value: unknown, maximum = 1200): string | null {
  if (typeof value !== "string" || !value) {
    return null;
  }
  return value.length <= maximum ? value : `${value.slice(0, maximum - 1)}…`;
}
