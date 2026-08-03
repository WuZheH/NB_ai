import { mkdir, readFile, rename, unlink, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { randomUUID } from "node:crypto";

export const DEFAULT_SETTINGS = Object.freeze({
  minimizeToTray: true,
});

export class SettingsStore {
  constructor(path) {
    if (!path) throw new Error("settings_path_required");
    this.path = path;
  }

  async read() {
    try {
      const value = JSON.parse(await readFile(this.path, "utf8"));
      return normalizeSettings(value);
    } catch (error) {
      if (error?.code === "ENOENT" || error instanceof SyntaxError) return { ...DEFAULT_SETTINGS };
      throw error;
    }
  }

  async update(patch) {
    const current = await this.read();
    const next = normalizeSettings({ ...current, ...normalizeSettingsPatch(patch) });
    await mkdir(dirname(this.path), { recursive: true });
    const temporary = `${this.path}.${randomUUID()}.tmp`;
    let temporaryCreated = false;
    try {
      await writeFile(temporary, `${JSON.stringify(next)}\n`, { encoding: "utf8", flag: "wx" });
      temporaryCreated = true;
      await rename(temporary, this.path);
    } finally {
      if (temporaryCreated) {
        await unlink(temporary).catch((error) => {
          if (error?.code !== "ENOENT") throw error;
        });
      }
    }
    return next;
  }
}

function normalizeSettings(value) {
  return {
    minimizeToTray: value?.minimizeToTray !== false,
  };
}

export function normalizeSettingsPatch(value) {
  const keys = Object.keys(value || {});
  if (keys.some((key) => key !== "minimizeToTray")) {
    throw new Error("desktop_setting_not_allowed");
  }
  const result = {};
  if (Object.hasOwn(value || {}, "minimizeToTray")) {
    if (typeof value.minimizeToTray !== "boolean") throw new Error("invalid_minimize_to_tray");
    result.minimizeToTray = value.minimizeToTray;
  }
  return result;
}
