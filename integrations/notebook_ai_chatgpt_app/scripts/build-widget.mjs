import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { build } from "esbuild";

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const outputDirectory = resolve(packageRoot, "web", "dist");

const result = await build({
  entryPoints: [resolve(packageRoot, "web", "src", "main.tsx")],
  bundle: true,
  write: false,
  outdir: outputDirectory,
  entryNames: "widget",
  platform: "browser",
  format: "iife",
  target: ["es2022"],
  jsx: "automatic",
  sourcemap: false,
  legalComments: "none",
});

const javascript = result.outputFiles.find((file) => file.path.endsWith(".js"));
const stylesheet = result.outputFiles.find((file) => file.path.endsWith(".css"));

if (!javascript) {
  throw new Error("The widget build did not produce JavaScript.");
}

const html = `<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>NOTEBOOK_AI Research Search</title>
    <style>${stylesheet?.text ?? ""}</style>
  </head>
  <body>
    <div id="root"></div>
    <script>${javascript.text}</script>
  </body>
</html>`;

await mkdir(outputDirectory, { recursive: true });
await writeFile(resolve(outputDirectory, "widget.html"), html, "utf8");
console.log("Built web/dist/widget.html");
