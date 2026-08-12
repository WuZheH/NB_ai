import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { rcedit } from "rcedit";

const DESKTOP_ROOT = resolve(fileURLToPath(new URL("..", import.meta.url)));
const packagedRoot = resolve(commandArgument("--packaged-root") || join(DESKTOP_ROOT, "dist", "win-unpacked"));
const executable = join(packagedRoot, "Search.exe");
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

function commandArgument(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : null;
}
