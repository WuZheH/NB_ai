import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { basename, isAbsolute, resolve } from "node:path";
import {
  DESKTOP_RUNTIME_SCHEMA_VERSION,
  backupDesktopRuntimeConfig,
  loadDesktopRuntimeConfig,
  migrateLegacyDesktopRuntimeConfig,
  publicDesktopRuntimeConfig,
  writeDesktopRuntimeConfig,
} from "../integrations/search_desktop/electron/main/desktopRuntimeConfig.js";

try {
  const args = parseArguments(process.argv.slice(2));
  const action = args._[0];
  if (!isAbsolute(String(args["config-path"] || ""))) throw new Error("desktop_runtime_path_not_absolute");
  const configPath = resolve(args["config-path"]);
  if (action === "inspect" || action === "validate") {
    const config = loadDesktopRuntimeConfig(configPath);
    emit({
      status: config.ready ? "ready" : "unavailable",
      desktop_runtime: publicDesktopRuntimeConfig(config),
      config_file: fileIdentity(configPath),
    }, action === "validate" && !config.ready ? 1 : 0);
  } else if (action === "set") {
    const config = writeDesktopRuntimeConfig(configPath, {
      schemaVersion: DESKTOP_RUNTIME_SCHEMA_VERSION,
      dataDir: required(args, "data-dir"),
      pythonExe: required(args, "python-exe"),
      nodeExe: required(args, "node-exe"),
    });
    emit({
      status: "written",
      desktop_runtime: publicDesktopRuntimeConfig(config),
      config_file: fileIdentity(configPath),
    });
  } else if (action === "backup") {
    const backupPath = backupDesktopRuntimeConfig(configPath);
    emit({ status: "backed_up", backup_file: fileIdentity(backupPath) });
  } else if (action === "migrate-legacy") {
    const legacyPath = required(args, "legacy-config-path");
    if (!isAbsolute(legacyPath)) throw new Error("desktop_runtime_path_not_absolute");
    const result = migrateLegacyDesktopRuntimeConfig({
      legacyPath: resolve(legacyPath),
      destinationPath: configPath,
    });
    emit({
      status: "migrated",
      desktop_runtime: publicDesktopRuntimeConfig(result.config),
      config_file: fileIdentity(configPath),
      legacy_backup_file: fileIdentity(result.legacyBackup),
    });
  } else {
    throw new Error("desktop_runtime_action_invalid");
  }
} catch (error) {
  emit({ status: "error", error_code: safeErrorCode(error) }, 1);
}

function parseArguments(values) {
  const result = { _: [] };
  for (let index = 0; index < values.length; index += 1) {
    const value = values[index];
    if (!value.startsWith("--")) {
      result._.push(value);
      continue;
    }
    const key = value.slice(2);
    const next = values[index + 1];
    if (next === undefined || next.startsWith("--")) throw new Error("desktop_runtime_argument_missing");
    result[key] = next;
    index += 1;
  }
  return result;
}

function required(value, key) {
  const candidate = String(value[key] || "").trim();
  if (!candidate) throw new Error("desktop_runtime_required_field_missing");
  return candidate;
}

function fileIdentity(path) {
  try {
    const content = readFileSync(path);
    return {
      present: true,
      basename: basename(path),
      sha256: createHash("sha256").update(content).digest("hex"),
    };
  } catch {
    return { present: false, basename: basename(path) };
  }
}

function safeErrorCode(error) {
  const candidate = String(error?.message || "desktop_runtime_configuration_failed");
  return /^[A-Za-z0-9_.-]{1,128}$/.test(candidate)
    ? candidate
    : "desktop_runtime_configuration_failed";
}

function emit(value, exitCode = 0) {
  process.stdout.write(`${JSON.stringify(value)}\n`);
  process.exitCode = exitCode;
}
