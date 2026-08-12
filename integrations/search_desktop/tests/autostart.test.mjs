import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { readFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { AutostartService } from "../electron/ipc/autostartService.js";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const EXECUTABLE = "D:\\Program Files\\Search\\Search.exe";

function mockSpawn(calls) {
  return (executable, args, options) => {
    calls.push({ executable, args, options });
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    child.kill = () => {};
    queueMicrotask(() => {
      child.stdout.emit("data", Buffer.from('{"status":"installed","available":true,"enabled":true}\n'));
      child.emit("close", 0);
    });
    return child;
  };
}

function packagedService(calls) {
  return new AutostartService(
    {
      desktopRoot: ROOT,
      executablePath: EXECUTABLE,
      isPackaged: true,
    },
    {
      spawnProcess: mockSpawn(calls),
      existsPath: (value) => value === EXECUTABLE,
    },
  );
}

test("development and missing executable modes are explicitly unavailable without Task Scheduler calls", async () => {
  const calls = [];
  const development = new AutostartService(
    { desktopRoot: ROOT, executablePath: "D:\\tools\\electron.exe", isPackaged: false },
    { spawnProcess: mockSpawn(calls), existsPath: () => true },
  );
  assert.deepEqual(await development.status(), {
    status: "unavailable",
    available: false,
    enabled: false,
    error_code: "search_desktop_executable_unavailable",
  });
  assert.throws(() => development.setEnabled(true), /search_desktop_autostart_unavailable/);

  const missing = new AutostartService(
    { desktopRoot: ROOT, executablePath: EXECUTABLE, isPackaged: true },
    { spawnProcess: mockSpawn(calls), existsPath: () => false },
  );
  assert.equal((await missing.status()).error_code, "search_desktop_executable_unavailable");
  assert.equal(calls.length, 0);
});

test("packaged Search uses only Desktop-specific scripts and the packaged executable", async () => {
  const calls = [];
  const service = packagedService(calls);
  await service.status();
  await service.setEnabled(true);
  await service.setEnabled(false);

  assert.equal(calls.length, 3);
  assert.deepEqual(
    calls.map((call) => call.args[call.args.indexOf("-File") + 1].split(/[\\/]/).at(-1)),
    ["status-autostart.ps1", "install-autostart.ps1", "uninstall-autostart.ps1"],
  );
  for (const call of calls) {
    assert.equal(call.executable, "powershell.exe");
    assert.equal(call.args[call.args.indexOf("-ExecutablePath") + 1], EXECUTABLE);
    assert.equal(call.args[call.args.indexOf("-WorkingDirectory") + 1], "D:\\Program Files\\Search");
    assert.equal(call.options.cwd, ROOT);
    assert.equal(call.options.windowsHide, true);
    assert.equal(call.options.shell, false);
    assert.doesNotMatch(call.args.join(" "), /scripts[\\/]runtime|notebook_ai_launcher/i);
  }
});

test("Desktop Task Scheduler scripts target Search.exe and never the legacy runtime task", async () => {
  const names = [
    "autostart-common.ps1",
    "status-autostart.ps1",
    "install-autostart.ps1",
    "uninstall-autostart.ps1",
  ];
  const sources = await Promise.all(names.map((name) => readFile(join(ROOT, "scripts", name), "utf8")));
  const combined = sources.join("\n");
  assert.match(combined, /Search Desktop/);
  assert.match(combined, /New-ScheduledTaskAction -Execute \$ExecutablePath/);
  assert.match(combined, /MultipleInstances IgnoreNew/);
  assert.match(combined, /StartWhenAvailable/);
  assert.match(combined, /PT20S/);
  assert.doesNotMatch(combined, /NOTEBOOK_AI Runtime Launcher|notebook_ai_launcher\.py|PythonExe/);
});
