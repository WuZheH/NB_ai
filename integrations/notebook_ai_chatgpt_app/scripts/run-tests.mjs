import { readdir, mkdir, rm } from "node:fs/promises";
import { dirname, extname, join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import { build } from "esbuild";

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const buildDirectory = resolve(packageRoot, ".test-build");

async function collectTests(directory) {
  const found = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) {
      found.push(...(await collectTests(path)));
    } else if (/\.test\.(?:ts|tsx)$/.test(entry.name)) {
      found.push(path);
    }
  }
  return found;
}

const tests = [
  ...(await collectTests(resolve(packageRoot, "server"))),
  ...(await collectTests(resolve(packageRoot, "web", "src"))),
];

await rm(buildDirectory, { recursive: true, force: true });
await mkdir(buildDirectory, { recursive: true });

try {
  for (const [index, test] of tests.entries()) {
    await build({
      entryPoints: [test],
      outfile: resolve(buildDirectory, `${index}-${test.split(/[\\/]/).at(-1).replace(extname(test), "")}.mjs`),
      bundle: true,
      packages: "external",
      platform: "node",
      format: "esm",
      target: "node20",
      jsx: "automatic",
      sourcemap: false,
    });
  }

  const run = spawnSync(
    process.execPath,
    ["--test", ...tests.map((_, index) => resolve(buildDirectory, `${index}-${tests[index].split(/[\\/]/).at(-1).replace(extname(tests[index]), "")}.mjs`))],
    { cwd: packageRoot, stdio: "inherit" },
  );
  process.exitCode = run.status ?? 1;
} finally {
  await rm(buildDirectory, { recursive: true, force: true });
}
