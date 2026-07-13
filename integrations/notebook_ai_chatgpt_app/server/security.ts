const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "::1"]);

export interface McpRuntimeSecurity {
  host: "127.0.0.1";
  port: number;
  unauthenticatedDevelopment: true;
}

export function requireUnauthenticatedDevelopment(env: NodeJS.ProcessEnv = process.env): McpRuntimeSecurity {
  if (env.NOTEBOOK_AI_ALLOW_UNAUTHENTICATED_MCP_DEV !== "1") {
    throw new Error(
      "Refusing to start an unauthenticated MCP server. Set NOTEBOOK_AI_ALLOW_UNAUTHENTICATED_MCP_DEV=1 only for short-lived Developer Mode testing.",
    );
  }

  const port = Number(env.NOTEBOOK_AI_MCP_PORT ?? "8787");
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error("NOTEBOOK_AI_MCP_PORT must be an integer from 1 to 65535.");
  }

  return { host: "127.0.0.1", port, unauthenticatedDevelopment: true };
}

export function isLoopbackHostname(hostname: string): boolean {
  return LOOPBACK_HOSTS.has(hostname.toLowerCase());
}

export const PRODUCTION_AUTHENTICATION_NOTICE =
  "A hosted deployment must replace the development gate with MCP OAuth 2.1 resource-server validation.";
