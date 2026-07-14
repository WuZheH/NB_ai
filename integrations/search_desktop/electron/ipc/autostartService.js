import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, extname, isAbsolute, join } from "node:path";

const SCRIPTS = Object.freeze({
  status: "status-autostart.ps1",
  install: "install-autostart.ps1",
  uninstall: "uninstall-autostart.ps1",
});

export class AutostartService {
  constructor(
    { desktopRoot, executablePath, isPackaged },
    { spawnProcess = spawn, existsPath = existsSync, timeoutMs = 20_000 } = {},
  ) {
    this.desktopRoot = desktopRoot;
    this.executablePath = executablePath;
    this.isPackaged = isPackaged === true;
    this.spawnProcess = spawnProcess;
    this.existsPath = existsPath;
    this.timeoutMs = timeoutMs;
  }

  available() {
    return this.isPackaged &&
      typeof this.executablePath === "string" &&
      isAbsolute(this.executablePath) &&
      extname(this.executablePath).toLowerCase() === ".exe" &&
      this.existsPath(this.executablePath);
  }

  status() {
    if (!this.available()) return Promise.resolve(unavailableStatus());
    return this.run("status");
  }

  setEnabled(enabled) {
    if (typeof enabled !== "boolean") throw new Error("invalid_autostart_value");
    if (!this.available()) throw new Error("search_desktop_autostart_unavailable");
    return this.run(enabled ? "install" : "uninstall");
  }

  run(action) {
    const script = SCRIPTS[action];
    if (!script) throw new Error("autostart_action_not_allowed");
    const path = join(this.desktopRoot, "scripts", script);
    const workingDirectory = dirname(this.executablePath);
    return new Promise((resolve, reject) => {
      const child = this.spawnProcess(
        "powershell.exe",
        [
          "-NoLogo",
          "-NoProfile",
          "-NonInteractive",
          "-File",
          path,
          "-ExecutablePath",
          this.executablePath,
          "-WorkingDirectory",
          workingDirectory,
        ],
        {
          cwd: this.desktopRoot,
          windowsHide: true,
          shell: false,
          stdio: ["ignore", "pipe", "pipe"],
        },
      );
      let stdout = "";
      let settled = false;
      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        child.kill();
        reject(new Error("autostart_command_timeout"));
      }, this.timeoutMs);
      child.stdout?.on("data", (chunk) => {
        stdout = `${stdout}${String(chunk)}`.slice(-32_768);
      });
      child.once("error", () => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        reject(new Error("autostart_process_start_failed"));
      });
      child.once("close", (code) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        if (code !== 0) {
          reject(new Error("autostart_command_failed"));
          return;
        }
        try {
          resolve(JSON.parse(stdout.trim().split(/\r?\n/).filter(Boolean).at(-1)));
        } catch {
          reject(new Error("autostart_response_invalid"));
        }
      });
    });
  }
}

function unavailableStatus() {
  return {
    status: "unavailable",
    available: false,
    enabled: false,
    error_code: "search_desktop_executable_unavailable",
  };
}
