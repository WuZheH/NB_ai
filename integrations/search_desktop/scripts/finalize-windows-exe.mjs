import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { rcedit } from "rcedit";

const DESKTOP_ROOT = resolve(fileURLToPath(new URL("..", import.meta.url)));
const executable = join(DESKTOP_ROOT, "dist", "win-unpacked", "Search.exe");
const icon = join(DESKTOP_ROOT, "assets", "search.ico");
const packageMetadata = JSON.parse(readFileSync(join(DESKTOP_ROOT, "package.json"), "utf8"));
const fileVersion = `${packageMetadata.version}.0`;

if (process.platform !== "win32") throw new Error("search_windows_resource_edit_requires_windows");
if (!existsSync(executable)) throw new Error("search_desktop_executable_missing");
if (!existsSync(icon)) throw new Error("search_desktop_icon_missing");

await rcedit(executable, {
  "file-version": fileVersion,
  "product-version": fileVersion,
  "version-string": {
    CompanyName: "Search",
    FileDescription: "Search",
    InternalName: "Search",
    OriginalFilename: "Search.exe",
    ProductName: "Search",
  },
  icon,
});

process.stdout.write(`${JSON.stringify({
  status: "ready",
  executable,
  productName: "Search",
  productVersion: packageMetadata.version,
})}\n`);
