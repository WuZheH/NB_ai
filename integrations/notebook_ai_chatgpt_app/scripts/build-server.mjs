import { createHash } from "node:crypto";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { builtinModules } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { build } from "esbuild";

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const outputDirectory = resolve(packageRoot, "dist", "server");

await mkdir(outputDirectory, { recursive: true });
const outputPath = resolve(outputDirectory, "index.js");
const result = await build({
  entryPoints: [resolve(packageRoot, "server", "index.ts")],
  outfile: outputPath,
  bundle: true,
  platform: "node",
  format: "esm",
  target: "node20",
  sourcemap: false,
  legalComments: "none",
  metafile: true,
});

const externalPackages = [...new Set(
  Object.values(result.metafile.outputs)
    .flatMap((output) => output.imports)
    .filter((entry) => entry.external && !isNodeBuiltin(entry.path))
    .map((entry) => entry.path),
)].sort();
if (externalPackages.length) {
  throw new Error(`mcp_server_external_packages:${externalPackages.join(",")}`);
}
const serverBytes = await readFile(outputPath);
await writeFile(
  resolve(outputDirectory, "build-manifest.json"),
  `${JSON.stringify({
    schemaVersion: 1,
    serverSha256: createHash("sha256").update(serverBytes).digest("hex").toUpperCase(),
    externalPackages,
  }, null, 2)}\n`,
  "utf8",
);

console.log("Built dist/server/index.js");

function isNodeBuiltin(specifier) {
  const normalized = specifier.replace(/^node:/, "");
  return builtinModules.includes(normalized) || builtinModules.includes(`node:${normalized}`);
}
