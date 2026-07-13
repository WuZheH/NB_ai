import { createServer, type Server } from "node:http";

import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";

import { createNotebookMcpServer } from "./app.js";
import { logDevelopmentWarning } from "./logging.js";
import { requireUnauthenticatedDevelopment } from "./security.js";

function sendJson(response: import("node:http").ServerResponse, status: number, value: unknown): void {
  response.writeHead(status, { "Content-Type": "application/json; charset=utf-8" });
  response.end(JSON.stringify(value));
}

export async function startNotebookMcpHttpServer(): Promise<Server> {
  const security = requireUnauthenticatedDevelopment();
  const activeConnections = new Set<Promise<void>>();

  const httpServer = createServer(async (request, response) => {
    const url = new URL(request.url ?? "/", `http://${request.headers.host ?? "127.0.0.1"}`);

    if (request.method === "GET" && url.pathname === "/healthz") {
      sendJson(response, 200, { status: "ok", service: "notebook-ai-mcp" });
      return;
    }
    if (url.pathname !== "/mcp") {
      sendJson(response, 404, { error: "not_found" });
      return;
    }
    if (request.method !== "POST") {
      response.setHeader("Allow", "POST");
      sendJson(response, 405, { error: "method_not_allowed" });
      return;
    }

    const mcpServer = createNotebookMcpServer();
    const transport = new StreamableHTTPServerTransport({
      sessionIdGenerator: undefined,
      enableJsonResponse: true,
    });
    const closeConnection = new Promise<void>((resolve) => {
      response.once("close", () => {
        Promise.allSettled([transport.close(), mcpServer.close()]).then(() => resolve());
      });
    });
    activeConnections.add(closeConnection);
    void closeConnection.finally(() => activeConnections.delete(closeConnection));
    try {
      await mcpServer.connect(transport);
      await transport.handleRequest(request, response);
    } catch {
      await Promise.allSettled([transport.close(), mcpServer.close()]);
      if (!response.headersSent) {
        sendJson(response, 500, {
          jsonrpc: "2.0",
          error: { code: -32603, message: "Internal MCP transport error" },
          id: null,
        });
      } else {
        response.end();
      }
    }
  });

  httpServer.on("close", () => {
    void Promise.allSettled(activeConnections);
  });

  await new Promise<void>((resolve, reject) => {
    httpServer.once("error", reject);
    httpServer.listen(security.port, security.host, () => {
      httpServer.off("error", reject);
      resolve();
    });
  });

  logDevelopmentWarning(security.port);
  console.info(JSON.stringify({ event: "mcp_started", host: security.host, port: security.port, endpoint: "/mcp" }));
  return httpServer;
}
