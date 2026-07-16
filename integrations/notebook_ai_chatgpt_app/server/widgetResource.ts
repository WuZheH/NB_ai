import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { RESOURCE_MIME_TYPE } from "@modelcontextprotocol/ext-apps/server";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";

import { WIDGET_RESOURCE_URI } from "./tools/shared.js";

export { RESOURCE_MIME_TYPE };

export interface WidgetResourceOptions {
  html?: string;
  htmlPath?: string;
  widgetDomain?: string;
}

function validatedWidgetDomain(value: string | undefined): string | undefined {
  if (!value) {
    return undefined;
  }
  const url = new URL(value);
  if (url.protocol !== "https:") {
    throw new Error("NOTEBOOK_AI_WIDGET_DOMAIN must use HTTPS.");
  }
  return url.origin;
}

export function registerWidgetResource(server: McpServer, options: WidgetResourceOptions = {}): void {
  const htmlPath = options.htmlPath ?? resolve(process.cwd(), "web", "dist", "widget.html");
  const widgetDomain = validatedWidgetDomain(options.widgetDomain ?? process.env.NOTEBOOK_AI_WIDGET_DOMAIN);

  server.registerResource(
    "notebook-ai-research-search-widget",
    WIDGET_RESOURCE_URI,
    {
      title: "Search",
      description:
        "Interactive PDF and Zotero reading-note evidence results from the user's Search collection." +
        (widgetDomain ? "" : " Widget domain mode is development-only until a real HTTPS origin is configured."),
      mimeType: RESOURCE_MIME_TYPE,
    },
    async () => {
      const html = options.html ?? (await readFile(htmlPath, "utf8"));
      const ui = {
        prefersBorder: true,
        csp: { connectDomains: [], resourceDomains: [] },
        permissions: { clipboardWrite: {} },
        ...(widgetDomain ? { domain: widgetDomain } : {}),
      };
      return {
        contents: [
          {
            uri: WIDGET_RESOURCE_URI,
            mimeType: RESOURCE_MIME_TYPE,
            text: html,
            _meta: {
              ui,
              "notebookAi/widgetDomainMode": widgetDomain ? "configured" : "development-only",
              "openai/widgetDescription":
                "Shows Search PDF passages and the user's Zotero reading notes with preview, fragment ID copy, evidence selection, and export controls.",
              "openai/widgetPrefersBorder": true,
              "openai/widgetCSP": { connect_domains: [], resource_domains: [] },
              ...(widgetDomain ? { "openai/widgetDomain": widgetDomain } : {}),
            },
          },
        ],
      };
    },
  );
}
