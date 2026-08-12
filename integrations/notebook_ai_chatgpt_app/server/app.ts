import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";

import { NotebookClient, type NotebookClientOptions } from "./notebookClient.js";
import { READ_PRODUCT_NAME, READ_SERVER_INSTRUCTIONS } from "./productIdentity.js";
import { registerNotebookTools } from "./tools/index.js";
import { registerWidgetResource, type WidgetResourceOptions } from "./widgetResource.js";

export interface NotebookMcpServerOptions {
  client?: NotebookClient;
  clientOptions?: NotebookClientOptions;
  widget?: WidgetResourceOptions;
}

export function createNotebookMcpServer(options: NotebookMcpServerOptions = {}): McpServer {
  const server = new McpServer({
    name: READ_PRODUCT_NAME,
    version: "0.1.0",
  }, {
    instructions: READ_SERVER_INSTRUCTIONS,
  });
  const client = options.client ?? new NotebookClient(options.clientOptions);

  registerNotebookTools(server, client);
  registerWidgetResource(server, options.widget);
  return server;
}
