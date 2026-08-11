import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { RESOURCE_MIME_TYPE } from "@modelcontextprotocol/ext-apps/server";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";

import { WIDGET_RESOURCE_URI } from "./tools/shared.js";

export { RESOURCE_MIME_TYPE };

export const WIDGET_DOMAIN = "https://read-library-widget.openaiusercontent.com";

export interface WidgetResourceOptions {
  html?: string;
  htmlPath?: string;
}

export function registerWidgetResource(server: McpServer, options: WidgetResourceOptions = {}): void {
  const htmlPath = options.htmlPath ?? resolve(process.cwd(), "web", "dist", "widget.html");
  server.registerResource(
    "read-research-evidence-widget",
    WIDGET_RESOURCE_URI,
    {
      title: "READ",
      description:
        "Interactive PDF and Zotero reading-note evidence from the user's READ library.",
      mimeType: RESOURCE_MIME_TYPE,
    },
    async () => {
      const html = options.html ?? (await readFile(htmlPath, "utf8"));
      const ui = {
        prefersBorder: true,
        csp: { connectDomains: [], resourceDomains: [] },
        permissions: { clipboardWrite: {} },
        domain: WIDGET_DOMAIN,
      };
      return {
        contents: [
          {
            uri: WIDGET_RESOURCE_URI,
            mimeType: RESOURCE_MIME_TYPE,
            text: html,
            _meta: {
              ui,
              "notebookAi/widgetDomainMode": "configured",
              "openai/widgetDescription":
                "Shows READ PDF passages and the user's Zotero reading notes with preview, fragment ID copy, evidence selection, and export controls.",
              "openai/widgetPrefersBorder": true,
              "openai/widgetCSP": { connect_domains: [], resource_domains: [] },
              "openai/widgetDomain": WIDGET_DOMAIN,
            },
          },
        ],
      };
    },
  );
}
