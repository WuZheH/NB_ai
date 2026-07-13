import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";

import { NotebookClient, type NotebookClientOptions } from "./notebookClient.js";
import { registerNotebookTools } from "./tools/index.js";
import { registerWidgetResource, type WidgetResourceOptions } from "./widgetResource.js";

export interface NotebookMcpServerOptions {
  client?: NotebookClient;
  clientOptions?: NotebookClientOptions;
  widget?: WidgetResourceOptions;
}

export function createNotebookMcpServer(options: NotebookMcpServerOptions = {}): McpServer {
  const server = new McpServer({
    name: "notebook-ai-research-search",
    version: "0.1.0",
  });
  const client = options.client ?? new NotebookClient(options.clientOptions);

  registerNotebookTools(server, client);
  registerWidgetResource(server, options.widget);
  return server;
}
