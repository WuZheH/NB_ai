import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { readdir, readFile, stat, writeFile } from "node:fs/promises";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  verifyPackagedResources,
  verifySourceResources,
} from "./verify-packaged-resources.mjs";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const DESKTOP_ROOT = resolve(SCRIPT_DIR, "..");
const PROJECT_ROOT = resolve(DESKTOP_ROOT, "../..");
const FRONTEND_DIST = join(PROJECT_ROOT, "frontend", "dist");
const packageJson = JSON.parse(await readFile(join(DESKTOP_ROOT, "package.json"), "utf8"));
const metadata = JSON.parse(await readFile(join(DESKTOP_ROOT, "electron", "product-metadata.json"), "utf8"));
const candidateBase = join(
  DESKTOP_ROOT,
  "dist-candidates",
  `Search-${packageJson.version}-${metadata.buildId}`,
);
const packagedRoot = join(candidateBase, "win-unpacked");
const packagedFrontend = join(packagedRoot, "resources", "search-assets", "frontend");

if (await exists(candidateBase)) {
  throw new Error(`search_r4_candidate_already_exists:${candidateBase}`);
}

await verifySourceResources();
await runNode(join(DESKTOP_ROOT, "node_modules", "electron-builder", "cli.js"), [
  "--win",
  "--x64",
  "--dir",
  `--config.directories.output=${candidateBase}`,
]);
await verifyPackagedResources(packagedRoot);
await runNode(join(SCRIPT_DIR, "finalize-windows-exe.mjs"), ["--packaged-root", packagedRoot]);
await runNode(join(SCRIPT_DIR, "write-local-config.mjs"), ["--packaged-root", packagedRoot]);
await verifyPackagedResources(packagedRoot);

const frontendAssets = await compareTrees(FRONTEND_DIST, packagedFrontend);
const executable = join(packagedRoot, "Search.exe");
const manifest = {
  status: "ready",
  candidate: "r4",
  version: packageJson.version,
  buildId: metadata.buildId,
  rendererAssetVersion: metadata.rendererAssetVersion,
  packagedRoot,
  executable,
  executableSha256: await sha256(executable),
  frontendAssetCount: frontendAssets.length,
  frontendAssets,
  frontendSource: FRONTEND_DIST,
  frontendSourceReusedFromOlderCandidate: false,
  headlessBackendBootstrap: true,
};
await writeFile(
  join(packagedRoot, "r4-build-manifest.json"),
  `${JSON.stringify(manifest, null, 2)}\n`,
  "utf8",
);
process.stdout.write(`${JSON.stringify(manifest)}\n`);

async function compareTrees(sourceRoot, packagedAssetRoot) {
  const sourceFiles = await listFiles(sourceRoot);
  const packagedFiles = await listFiles(packagedAssetRoot);
  if (sourceFiles.join("\n") !== packagedFiles.join("\n")) {
    throw new Error("search_r4_frontend_asset_set_mismatch");
  }
  const rows = [];
  for (const path of sourceFiles) {
    const sourcePath = join(sourceRoot, path);
    const packagedPath = join(packagedAssetRoot, path);
    const sourceHash = await sha256(sourcePath);
    const packagedHash = await sha256(packagedPath);
    if (sourceHash !== packagedHash) {
      throw new Error(`search_r4_frontend_asset_hash_mismatch:${path}`);
    }
    rows.push({ path: path.replaceAll("\\", "/"), sha256: sourceHash });
  }
  return rows;
}

async function listFiles(root) {
  const files = [];
  async function visit(directory) {
    const entries = await readdir(directory, { withFileTypes: true });
    for (const entry of entries.sort((left, right) => left.name.localeCompare(right.name))) {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) await visit(path);
      else if (entry.isFile()) files.push(relative(root, path));
    }
  }
  await visit(root);
  return files.sort();
}

async function sha256(path) {
  const value = await readFile(path);
  return createHash("sha256").update(value).digest("hex").toUpperCase();
}

async function exists(path) {
  try {
    await stat(path);
    return true;
  } catch (error) {
    if (error?.code === "ENOENT") return false;
    throw error;
  }
}

function runNode(script, args = []) {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(process.execPath, [script, ...args], {
      cwd: DESKTOP_ROOT,
      shell: false,
      stdio: "inherit",
      windowsHide: true,
      env: process.env,
    });
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      if (code === 0) resolvePromise();
      else reject(new Error(`search_r4_packaging_step_failed:${code ?? signal ?? "unknown"}`));
    });
  });
}
