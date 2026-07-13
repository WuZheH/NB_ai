import { App, PostMessageTransport } from "@modelcontextprotocol/ext-apps";

import type { ToolEnvelope } from "../types";

type ToolResultListener = (result: ToolEnvelope) => void;

function asEnvelope(value: unknown): ToolEnvelope {
  return value as ToolEnvelope;
}

class McpAppsBridge {
  private readonly listeners = new Set<ToolResultListener>();
  private readonly app: App | null;
  private readonly ready: Promise<boolean> | null;
  private latestEnvelope: ToolEnvelope | null = null;
  private connectionError: Error | null = null;

  constructor() {
    if (window.parent === window) {
      this.app = null;
      this.ready = null;
      return;
    }

    const app = new App(
      { name: "NOTEBOOK_AI Research Search", version: "0.1.0" },
      {},
      { autoResize: true, strict: true },
    );

    // One-shot lifecycle notifications can arrive during connect(), so the
    // handler must be registered before the initialize handshake starts.
    app.addEventListener("toolresult", (result) => this.publish(asEnvelope(result)));

    this.app = app;
    this.ready = app
      .connect(new PostMessageTransport(window.parent, window.parent))
      .then(() => true)
      .catch((error: unknown) => {
        this.connectionError = error instanceof Error ? error : new Error("MCP Apps bridge initialization failed.");
        return false;
      });
  }

  initialEnvelope(): ToolEnvelope | null {
    if (this.latestEnvelope) {
      return this.latestEnvelope;
    }
    const openai = window.openai;
    if (!openai?.toolOutput && !openai?.toolResponseMetadata) {
      return null;
    }
    return {
      structuredContent: openai.toolOutput,
      _meta: openai.toolResponseMetadata,
    };
  }

  subscribe(listener: ToolResultListener): () => void {
    this.listeners.add(listener);
    if (this.latestEnvelope) {
      listener(this.latestEnvelope);
    }
    return () => this.listeners.delete(listener);
  }

  async callTool(name: string, args: Record<string, unknown>): Promise<ToolEnvelope> {
    const app = await this.connectedApp();
    if (app) {
      return asEnvelope(await app.callServerTool({ name, arguments: args }));
    }
    if (window.openai?.callTool) {
      return window.openai.callTool(name, args);
    }
    throw this.connectionError ?? new Error("No MCP Apps host bridge is available.");
  }

  async updateModelContext(summary: string, fragmentIds: string[]): Promise<void> {
    const params = {
      content: [{ type: "text" as const, text: summary }],
      structuredContent: { selected_fragment_ids: fragmentIds },
    };
    const app = await this.connectedApp();
    if (app) {
      await app.updateModelContext(params).catch(() => undefined);
    }
    await window.openai?.setWidgetState?.({ selected_fragment_ids: fragmentIds });
  }

  async openLink(href: string): Promise<void> {
    const app = await this.connectedApp();
    if (app) {
      const result = await app.openLink({ url: href });
      if (result.isError) {
        throw new Error("The MCP Apps host declined to open this link.");
      }
      return;
    }
    if (window.openai?.openExternal) {
      await window.openai.openExternal({ href });
      return;
    }
    window.open(href, "_blank", "noopener,noreferrer");
  }

  private async connectedApp(): Promise<App | null> {
    if (!this.app || !this.ready) {
      return null;
    }
    return (await this.ready) ? this.app : null;
  }

  private publish(envelope: ToolEnvelope): void {
    this.latestEnvelope = envelope;
    for (const listener of this.listeners) {
      listener(envelope);
    }
  }
}

export const mcpBridge = new McpAppsBridge();
