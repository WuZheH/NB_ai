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
    if (!this.config.runtimeAvailable) {
      return Promise.reject(new Error("runtime_prerequisites_missing"));
    }
    const args = ["-B", this.config.runtimeScript, command, ...extraArguments];
    const environment = {
      ...process.env,
      SEARCH_RUNTIME_ROOT: this.config.runtimeRoot,
      SEARCH_DATA_DIR: this.config.dataDir,
      SEARCH_PYTHON: this.config.pythonExe,
      SEARCH_NODE: this.config.nodeExe,
      SEARCH_BUILD_MODE: this.config.buildMode,
      SEARCH_BUILD_IDENTITY_PATH: this.config.buildIdentityPath,
      NOTEBOOK_AI_RUNTIME_ROOT: this.config.runtimeRoot,
      NOTEBOOK_AI_DATA_PROJECT_ROOT: this.config.dataProjectRoot,
      NOTEBOOK_AI_PYTHON_EXE: this.config.pythonExe,
      NOTEBOOK_AI_NODE_EXE: this.config.nodeExe,
    };
    // The formal runtime must never import from a developer worktree through
    // an ambient shell setting. The packaged runtime root is inserted by the
    // launcher itself and all executables are explicit.
    delete environment.PYTHONPATH;
    delete environment.NODE_PATH;
    delete environment.NOTEBOOK_AI_PROJECT_ROOT;
    return new Promise((resolve, reject) => {
      const child = this.spawnProcess(this.config.pythonExe, args, {
        cwd: this.config.runtimeRoot,
        windowsHide: true,
        shell: false,
        stdio: ["ignore", "pipe", "pipe"],
        env: environment,
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
