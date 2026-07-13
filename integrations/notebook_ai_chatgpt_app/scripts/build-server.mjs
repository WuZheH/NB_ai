import { mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { build } from "esbuild";

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const outputDirectory = resolve(packageRoot, "dist", "server");

await mkdir(outputDirectory, { recursive: true });
await build({
  entryPoints: [resolve(packageRoot, "server", "index.ts")],
  outfile: resolve(outputDirectory, "index.js"),
  bundle: true,
  packages: "external",
  platform: "node",
  format: "esm",
  target: "node20",
  sourcemap: false,
  legalComments: "none",
});

console.log("Built dist/server/index.js");
