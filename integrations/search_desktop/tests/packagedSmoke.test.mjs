import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const smoke = await readFile(new URL("../scripts/smoke-packaged-search.ps1", import.meta.url), "utf8");
const configureWrapper = await readFile(new URL("../../../scripts/configure_search_desktop_runtime.ps1", import.meta.url), "utf8");
const configureTool = await readFile(new URL("../../../scripts/configure_search_desktop_runtime.mjs", import.meta.url), "utf8");

test("packaged smoke creates isolated userData provisioning with the official tool", () => {
  assert.match(smoke, /configure_search_desktop_runtime\.ps1/);
  assert.match(smoke, /-Action set -ConfigPath \$DesktopRuntimePath/);
  assert.match(smoke, /--user-data-dir=\$UserData/);
  assert.match(smoke, /desktop-runtime\.json/);
  assert.match(smoke, /ToolResult\.desktop_runtime\.ready/);
});

test("packaged smoke source is ASCII so Windows PowerShell 5.1 parses it without a BOM", () => {
  assert.equal(Buffer.from(smoke, "utf8").every((byte) => byte < 0x80), true);
});

test("packaged smoke covers missing, invalid, valid, and migrated legacy configurations", () => {
  assert.match(smoke, /ValidateSet\("valid", "missing", "invalid", "legacy-migration"\)/);
  assert.match(smoke, /desktop_runtime_config_missing/);
  assert.match(smoke, /desktop_runtime_config_invalid_json/);
  assert.match(smoke, /-Action migrate-legacy/);
  assert.match(smoke, /desktop-runtime\.legacy-sidecar\.bak\.json/);
  assert.match(smoke, /search_packaged_smoke_unavailable_spawned_runtime/);
  assert.match(smoke, /search_packaged_smoke_created_fallback_data/);
  assert.match(smoke, /stage_completed" -and \$_.stage -eq "ready/);
});

test("packaged smoke clears ambient prerequisites and never prestarts Runtime", () => {
  for (const name of [
    "SEARCH_DATA_DIR",
    "SEARCH_LOG_DIR",
    "SEARCH_PYTHON",
    "SEARCH_NODE",
    "NOTEBOOK_AI_DATA_PROJECT_ROOT",
    "NOTEBOOK_AI_PROJECT_ROOT",
  ]) {
    assert.match(smoke, new RegExp(`Set-Item "Env:\\$Name" \\$null`));
    assert.doesNotMatch(smoke, new RegExp(`\\$env:${name}\\s*=\\s*\\$`));
  }
  assert.doesNotMatch(smoke, /\$env:SEARCH_[A-Z0-9_]+\s*=/);
  assert.match(smoke, /"SEARCH_ELECTRON_TEST_MODE", "SEARCH_RENDERER_PORT", "SEARCH_RUNTIME_ROOT"/);
  assert.doesNotMatch(smoke, /Start-IsolatedRuntimeFixture|Invoke-IsolatedRuntimeCommand[^\n]+start/);
});

test("packaged smoke confines every writable root to its explicit isolated D-drive scope", () => {
  assert.match(smoke, /search_packaged_smoke_writable_root_not_isolated/);
  assert.match(smoke, /GetPathRoot\(\$ProjectRoot\)/);
  assert.match(smoke, /\$DataDir\.StartsWith\(\$TestPrefix/);
  assert.match(smoke, /\$env:LOCALAPPDATA = \$LocalAppData/);
  assert.match(smoke, /\$env:APPDATA = \$RoamingAppData/);
  assert.match(smoke, /\$env:TEMP = \$TempDirectory/);
  assert.match(smoke, /\$env:TMP = \$TempDirectory/);
  assert.match(smoke, /--user-data-dir=\$UserData/);
});

test("packaged smoke proves Electron spawned and owns Runtime", () => {
  assert.match(smoke, /stage -eq "runtime_checked"/);
  assert.match(smoke, /RuntimeEntry\.runtime_owner -ne "managed-by-search"/);
  assert.match(smoke, /ExpectedConfigSource = "user_data"/);
  assert.match(smoke, /ExpectedRuntimeStatus = "desktop_runtime_ready"/);
  assert.match(smoke, /ExpectedLauncherSpawned = \$true/);
  assert.match(smoke, /RuntimeEntry\.desktop_started_runtime -ne \$true/);
  assert.match(smoke, /127\.0\.0\.1:8000\/health/);
  assert.match(smoke, /127\.0\.0\.1:8787\/healthz/);
  assert.match(smoke, /127\.0\.0\.1:\$RendererPort\/retrieval/);
  assert.match(smoke, /runtime_console_visible/);
  assert.match(smoke, /cloudflared_process_changed/);
  assert.match(smoke, /cloudflared_started = \$false/);
});

test("packaged smoke requests controlled test-mode shutdown and has no forced cleanup", () => {
  assert.match(smoke, /Invoke-SearchTestModeFullyQuit/);
  assert.match(smoke, /--search-test-quit/);
  assert.match(smoke, /search_packaged_smoke_test_mode_quit_timeout/);
  assert.doesNotMatch(smoke, /UIAutomation|SendKeys|mouse_event|SetCursorPos/);
  assert.match(smoke, /runtime_residual_count = 0/);
  assert.doesNotMatch(smoke, /taskkill|Stop-Process|\.Kill\(/i);
});

test("packaged smoke checks single instance, path-redacted logs, and restores its environment", () => {
  assert.match(smoke, /second_instance_changed_process_tree/);
  assert.match(smoke, /startup_log_path_leak/);
  assert.match(smoke, /SetEnvironmentVariable\(\$Name, \$OriginalEnvironment\[\$Name\], "Process"\)/);
  assert.match(smoke, /Assert-PortOwnershipUnchanged/);
});

test("official provisioning tool is machine-local, atomic, and has no system mutation or download path", () => {
  assert.match(configureWrapper, /ApplicationData/);
  assert.match(configureWrapper, /Search\\desktop-runtime\.json/);
  assert.match(configureTool, /writeDesktopRuntimeConfig/);
  assert.match(configureTool, /migrateLegacyDesktopRuntimeConfig/);
  assert.doesNotMatch(`${configureWrapper}\n${configureTool}`, /SetEnvironmentVariable|Set-ItemProperty|New-ItemProperty|reg\.exe|Invoke-WebRequest|Start-BitsTransfer|npm install|pip install/i);
});
