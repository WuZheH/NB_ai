import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const smoke = await readFile(
  new URL("../scripts/smoke-packaged-search.ps1", import.meta.url),
  "utf8",
);

test("packaged smoke exercises the real Search executable for at least ten seconds", () => {
  assert.match(smoke, /dist\\win-unpacked\\Search\.exe/);
  assert.match(smoke, /\[ValidateRange\(10, 120\)\]/);
  assert.match(smoke, /Start-Process[\s\S]*-FilePath \$ExecutablePath/);
  assert.match(smoke, /Start-Sleep -Seconds \$HoldSeconds/);
  assert.match(smoke, /\.HasExited/);
});

test("packaged smoke validates window identity and final ready startup stage", () => {
  assert.match(smoke, /MainWindowHandle/);
  assert.match(smoke, /MainWindowTitle/);
  assert.match(smoke, /WindowTitle -notmatch "Search"/);
  assert.match(smoke, /event -eq "stage_completed"/);
  assert.match(smoke, /stage -eq "ready"/);
  assert.match(smoke, /lastSuccessfulStage -eq "ready"/);
});

test("packaged smoke keeps all mutable paths inside the task temp root", () => {
  assert.match(smoke, /\.codex_tmp\\search-desktop-startup-0\.1\.2\\packaged-smoke/);
  assert.match(smoke, /--user-data-dir=\$UserData/);
  for (const name of ["LOCALAPPDATA", "APPDATA", "TEMP", "TMP"]) {
    assert.match(smoke, new RegExp(`\\$env:${name} =`));
  }
});

test("packaged smoke cleanup is identity-scoped and uses the isolated launcher", () => {
  assert.match(smoke, /ExpectedExecutable/);
  assert.match(smoke, /StringComparison\]::OrdinalIgnoreCase/);
  assert.match(smoke, /Get-DescendantProcessIds/);
  assert.match(smoke, /Stop-Process -Id \$ProcessId/);
  assert.match(smoke, /Start-IsolatedRuntimeFixture/);
  assert.match(smoke, /Invoke-IsolatedRuntimeCommand -Command "stop"/);
  assert.match(smoke, /search_packaged_smoke_port_owner_changed/);
  assert.doesNotMatch(smoke, /taskkill|Stop-Process\s+-Name|Get-Process\s+Search/);
});

test("packaged smoke reuses healthy loopback services before requiring a local MCP build", () => {
  assert.match(smoke, /Get-HealthyExternalRuntimeFixture/);
  assert.match(smoke, /127\.0\.0\.1:8000\/api\/v1\/retrieval\/index\/status/);
  assert.match(smoke, /127\.0\.0\.1:8787\/healthz/);
  assert.match(smoke, /fastapi = \[pscustomobject\]@\{ state = "external" \}/);
  assert.match(smoke, /mcp = \[pscustomobject\]@\{ state = "external" \}/);
  assert.match(
    smoke,
    /if \(\$ExternalRuntime\) \{ return \$ExternalRuntime \}[\s\S]*Invoke-IsolatedRuntimeCommand -Command "start"/,
  );
});

test("packaged smoke treats incomplete launcher failure payloads as not ready", () => {
  assert.match(smoke, /Properties\["components"\]/);
  assert.match(smoke, /Properties\["fastapi"\]/);
  assert.match(smoke, /Properties\["mcp"\]/);
});
