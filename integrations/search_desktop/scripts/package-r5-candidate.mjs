import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { readdir, readFile, stat, writeFile } from "node:fs/promises";
import { dirname, extname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  verifyPackagedResources,
  verifySourceResources,
} from "./verify-packaged-resources.mjs";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const DESKTOP_ROOT = resolve(SCRIPT_DIR, "..");
const PROJECT_ROOT = resolve(DESKTOP_ROOT, "../..");
const MCP_ROOT = resolve(DESKTOP_ROOT, "../notebook_ai_chatgpt_app");
const DATA_PROJECT_ROOT = resolve(
  process.env.NOTEBOOK_AI_DATA_PROJECT_ROOT || "D:\\LEARNING\\Tools\\notebook_ai",
);
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
const packagedRuntime = join(packagedRoot, "resources", "app", "runtime-project");

if (await exists(candidateBase)) {
  throw new Error(`search_r5_candidate_already_exists:${candidateBase}`);
}
if (isWorktreePath(DATA_PROJECT_ROOT)) {
  throw new Error("search_r5_data_project_root_must_be_stable");
}

await runNode(join(MCP_ROOT, "scripts", "build-widget.mjs"));
await runNode(join(MCP_ROOT, "scripts", "build-server.mjs"));
await verifySourceResources();
await runNode(join(DESKTOP_ROOT, "node_modules", "electron-builder", "cli.js"), [
  "--win",
  "--x64",
  "--dir",
  `--config.directories.output=${candidateBase}`,
]);
await verifyPackagedResources(packagedRoot);
await runNode(join(SCRIPT_DIR, "finalize-windows-exe.mjs"), ["--packaged-root", packagedRoot]);
await runNode(join(SCRIPT_DIR, "write-local-config.mjs"), [
  "--packaged-root",
  packagedRoot,
  "--data-project-root",
  DATA_PROJECT_ROOT,
]);
await verifyPackagedResources(packagedRoot);
await verifyNoWorktreeReferences(packagedRoot);

const frontendAssets = await compareTrees(FRONTEND_DIST, packagedFrontend);
const runtimeAggregate = await aggregateTree(packagedRuntime);
const executable = join(packagedRoot, "Search.exe");
const manifest = {
  status: "ready",
  candidate: "r5",
  version: packageJson.version,
  buildId: metadata.buildId,
  rendererAssetVersion: metadata.rendererAssetVersion,
  sourceCommit: await gitHead(),
  packagedRoot,
  executable,
  executableSha256: await sha256(executable),
  frontendAssetCount: frontendAssets.length,
  frontendAssets,
  frontendSource: FRONTEND_DIST,
  frontendSourceReusedFromOlderCandidate: false,
  runtimeRoot: packagedRuntime,
  runtimeFileCount: runtimeAggregate.fileCount,
  runtimeAggregateSha256: runtimeAggregate.sha256,
  dataProjectRoot: DATA_PROJECT_ROOT,
  worktreeReferences: 0,
  productionDataBundled: false,
  mcpDependenciesBundled: true,
  headlessBackendBootstrap: true,
};
await writeFile(
  join(packagedRoot, "r5-build-manifest.json"),
  `${JSON.stringify(manifest, null, 2)}\n`,
  "utf8",
);
process.stdout.write(`${JSON.stringify(manifest)}\n`);

async function verifyNoWorktreeReferences(root) {
  const extensions = new Set([".json", ".js", ".cjs", ".mjs", ".py", ".txt", ".html", ".css"]);
  const files = await listFiles(root);
  for (const path of files) {
    if (!extensions.has(extname(path).toLowerCase())) continue;
    const source = (await readFile(join(root, path), "utf8")).toLowerCase();
    if (
      source.includes("d:\\learning\\tools\\notebook_ai_worktrees")
      || source.includes("d:\\\\learning\\\\tools\\\\notebook_ai_worktrees")
    ) {
      throw new Error(`search_r5_worktree_reference_detected:${path}`);
    }
  }
}

async function compareTrees(sourceRoot, packagedAssetRoot) {
  const sourceFiles = await listFiles(sourceRoot);
  const packagedFiles = await listFiles(packagedAssetRoot);
  if (sourceFiles.join("\n") !== packagedFiles.join("\n")) {
    throw new Error("search_r5_frontend_asset_set_mismatch");
  }
  const rows = [];
  for (const path of sourceFiles) {
    const sourceHash = await sha256(join(sourceRoot, path));
    const packagedHash = await sha256(join(packagedAssetRoot, path));
    if (sourceHash !== packagedHash) {
      throw new Error(`search_r5_frontend_asset_hash_mismatch:${path}`);
    }
    rows.push({ path: path.replaceAll("\\", "/"), sha256: sourceHash });
  }
  return rows;
}

async function aggregateTree(root) {
  const rows = [];
  for (const path of await listFiles(root)) {
    const absolute = join(root, path);
    const value = await stat(absolute);
    rows.push(`${path.replaceAll("\\", "/")}\t${await sha256(absolute)}\t${value.size}`);
  }
  return {
    fileCount: rows.length,
    sha256: createHash("sha256").update(`${rows.join("\n")}\n`).digest("hex").toUpperCase(),
  };
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
  return createHash("sha256").update(await readFile(path)).digest("hex").toUpperCase();
}

async function gitHead() {
  return new Promise((resolvePromise, reject) => {
    const child = spawn("git", ["rev-parse", "HEAD"], {
      cwd: PROJECT_ROOT,
      shell: false,
      windowsHide: true,
      stdio: ["ignore", "pipe", "inherit"],
    });
    let output = "";
    child.stdout.on("data", (chunk) => { output += String(chunk); });
    child.once("error", reject);
    child.once("exit", (code) => {
      if (code === 0) resolvePromise(output.trim());
      else reject(new Error("search_r5_git_head_failed"));
    });
  });
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

function isWorktreePath(path) {
  return resolve(path).toLowerCase().split(/[\\/]+/).includes("notebook_ai_worktrees");
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
      else reject(new Error(`search_r5_packaging_step_failed:${code ?? signal ?? "unknown"}`));
    });
  });
}
