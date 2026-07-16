import { EventEmitter } from "node:events";
import { REUSABLE_STATES, desktopStatus, localRuntimeReady } from "./status.js";

export class RuntimeCoordinator extends EventEmitter {
  constructor(client, { pollIntervalMs = 1_000, readyTimeoutMs = 75_000, sleep = delay } = {}) {
    super();
    this.client = client;
    this.pollIntervalMs = pollIntervalMs;
    this.readyTimeoutMs = readyTimeoutMs;
    this.sleep = sleep;
    this.startedByDesktop = false;
    this.lastStatus = null;
  }

  async ensureReady() {
    let before;
    try {
      before = await this.client.status();
    } catch {
      before = null;
    }
    if (!before || !REUSABLE_STATES.has(before.state)) {
      await this.client.start();
      this.startedByDesktop = true;
    }
    return this.waitForLocalReady();
  }

  async waitForLocalReady() {
    const deadline = Date.now() + this.readyTimeoutMs;
    while (Date.now() <= deadline) {
      const status = await this.client.status();
      this.update(status);
      if (localRuntimeReady(this.lastStatus)) return this.lastStatus;
      if (status?.state === "failed") throw new Error(status.error_code || "runtime_start_failed");
      await this.sleep(this.pollIntervalMs);
    }
    throw new Error("runtime_ready_timeout");
  }

  async refresh() {
    const status = await this.client.status();
    this.update(status);
    return this.lastStatus;
  }

  async restart() {
    if (!this.startedByDesktop) throw new Error("external_runtime_restart_not_allowed");
    await this.client.restart();
    this.startedByDesktop = true;
    return this.waitForLocalReady();
  }

  async stopIfOwned() {
    if (!this.startedByDesktop) return { status: "reused_runtime_left_running" };
    const status = await this.client.stop();
    this.startedByDesktop = false;
    this.update(status);
    return status;
  }

  update(status) {
    this.lastStatus = this.decorate(status);
    this.emit("status", this.lastStatus);
  }

  presentation() {
    return desktopStatus(this.lastStatus);
  }

  decorate(status) {
    if (!status || typeof status !== "object") return status;
    const components = Object.fromEntries(
      Object.entries(status.components || {}).map(([name, component]) => [
        name,
        {
          ...component,
          owner: this.startedByDesktop && component?.owned
            ? "managed-by-search"
            : "external",
        },
      ]),
    );
    return {
      ...status,
      runtime_owner: this.startedByDesktop ? "managed-by-search" : "external",
      components,
    };
  }
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}
