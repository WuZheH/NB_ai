import { spawn } from "node:child_process";

const MAX_OUTPUT_BYTES = 64 * 1024;
const ALLOWED_COMMANDS = new Set([
  "start",
  "ensure-running",
  "stop",
  "restart",
  "status",
  "doctor",
  "logs",
  "tunnel-status",
  "signal",
]);

export class LauncherClient {
  constructor(config, { spawnProcess = spawn, timeoutMs = 75_000 } = {}) {
    this.config = config;
    this.spawnProcess = spawnProcess;
    this.timeoutMs = timeoutMs;
  }

  run(command, extraArguments = []) {
    if (!ALLOWED_COMMANDS.has(command)) throw new Error("runtime_command_not_allowed");
    if (!extraArguments.every(isSafeArgument)) throw new Error("runtime_argument_not_allowed");
    const args = ["-B", this.config.runtimeScript, command, ...extraArguments];
    return new Promise((resolve, reject) => {
      const child = this.spawnProcess(this.config.pythonExe, args, {
        cwd: this.config.projectRoot,
        windowsHide: true,
        shell: false,
        stdio: ["ignore", "pipe", "pipe"],
        env: {
          ...process.env,
          NOTEBOOK_AI_PYTHON_EXE: this.config.pythonExe,
          NOTEBOOK_AI_NODE_EXE: this.config.nodeExe,
        },
      });
      let stdout = "";
      let stderr = "";
      let settled = false;
      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        child.kill();
        reject(new Error("runtime_command_timeout"));
      }, this.timeoutMs);
      child.stdout?.on("data", (chunk) => {
        stdout = appendCapped(stdout, chunk);
      });
      child.stderr?.on("data", (chunk) => {
        stderr = appendCapped(stderr, chunk);
      });
      child.once("error", (error) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        reject(new Error(safeErrorCode(error, "runtime_process_start_failed")));
      });
      child.once("close", (code) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        try {
          const payload = parseJsonLine(stdout);
          if (code !== 0 || payload?.status === "error") {
            reject(new Error(safeErrorCode(payload?.error_code, "runtime_command_failed")));
            return;
          }
          resolve(payload);
        } catch {
          void stderr;
          reject(new Error("runtime_response_invalid"));
        }
      });
    });
  }

  status() {
    return this.run("status");
  }

  start() {
    return this.run("start");
  }

  restart() {
    return this.run("restart");
  }

  stop() {
    return this.run("stop");
  }

  logs() {
    return this.run("logs");
  }

  tunnelStatus() {
    return this.run("tunnel-status");
  }

  pauseTunnel() {
    return this.run("signal", ["pause_tunnel"]);
  }

  resumeTunnel() {
    return this.run("signal", ["resume_tunnel"]);
  }
}

function appendCapped(current, chunk) {
  const next = `${current}${String(chunk)}`;
  return Buffer.byteLength(next) <= MAX_OUTPUT_BYTES ? next : next.slice(-MAX_OUTPUT_BYTES);
}

function parseJsonLine(output) {
  const lines = String(output).split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  if (!lines.length) throw new Error("runtime_response_empty");
  const value = JSON.parse(lines.at(-1));
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("runtime_response_invalid");
  }
  return value;
}

function isSafeArgument(value) {
  return typeof value === "string" && value.length <= 256 && !/[\r\n\0]/.test(value);
}

function safeErrorCode(value, fallback) {
  const candidate = value instanceof Error ? value.message : String(value || "");
  return /^[A-Za-z0-9_.-]{1,96}$/.test(candidate) ? candidate : fallback;
}
