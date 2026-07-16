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
  assert.match(smoke, /\.codex_tmp\\search-desktop-startup-0\.1\.3\\packaged-smoke/);
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
