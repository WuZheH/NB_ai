import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const PROJECT_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const TREE_HASH_SCRIPT = resolve(PROJECT_ROOT, "scripts", "compute_search_tree_hash.ps1");
const TEST_ROOT = resolve(
  process.env.SEARCH_TEST_TMP_ROOT || resolve(PROJECT_ROOT, ".codex_tmp", "candidate2-fix", "tree-hash-tests"),
  `run-${process.pid}-${randomUUID()}`,
);

test("Search tree hash is ordinal, locale-independent, binary framed, and ignores empty directories", async () => {
  const first = resolve(TEST_ROOT, "first");
  const second = resolve(TEST_ROOT, "second");
  const entries = [
    ["中文/文件.txt", "中文内容"],
    ["Case.txt", "case-sensitive-path"],
    ["file2.txt", "two"],
    ["file10.txt", "ten"],
    ["empty.bin", ""],
  ];
  await writeFixture(first, entries, ["empty-directory"]);
  await writeFixture(second, [...entries].reverse(), ["empty-directory"]);

  const firstRuns = [];
  for (let index = 0; index < 3; index += 1) firstRuns.push(await treeHash(first, { includeFiles: true }));
  const secondRuns = [];
  for (let index = 0; index < 3; index += 1) secondRuns.push(await treeHash(second, { includeFiles: true }));
  assert.equal(new Set(firstRuns.map((value) => value.sha256)).size, 1);
  assert.equal(new Set(secondRuns.map((value) => value.sha256)).size, 1);
  assert.equal(firstRuns[0].sha256, secondRuns[0].sha256);
  assert.equal(firstRuns[0].empty_directories, "excluded");
  assert.equal(firstRuns[0].path_sort, "OrdinalIgnoreCase;OrdinalTieBreak");
  assert.equal(firstRuns[0].files.findIndex((value) => value.relative_path === "file10.txt")
    < firstRuns[0].files.findIndex((value) => value.relative_path === "file2.txt"), true);
  assert.equal(firstRuns[0].files.some((value) => value.relative_path === "中文/文件.txt"), true);
  assert.equal(firstRuns[0].files.some((value) => value.relative_path === "empty.bin" && value.length === 0), true);
  assert.equal(firstRuns[0].sha256, referenceHash(firstRuns[0].files));

  const localeHashes = await Promise.all(["en-US", "tr-TR", "zh-CN"].map(async (culture) => (await treeHash(first, { culture })).sha256));
  assert.equal(new Set(localeHashes).size, 1);

  const noDirectories = resolve(TEST_ROOT, "no-directories");
  const emptyDirectoryOnly = resolve(TEST_ROOT, "empty-directory-only");
  await writeFixture(noDirectories, []);
  await writeFixture(emptyDirectoryOnly, [], ["中文空目录"]);
  assert.equal((await treeHash(noDirectories)).sha256, (await treeHash(emptyDirectoryOnly)).sha256);

  const differentCase = resolve(TEST_ROOT, "different-case");
  await writeFixture(differentCase, entries.map(([path, content]) => [path === "Case.txt" ? "case.txt" : path, content]));
  assert.notEqual((await treeHash(first)).sha256, (await treeHash(differentCase)).sha256);

  const differentPath = resolve(TEST_ROOT, "different-path");
  await writeFixture(differentPath, entries.map(([path, content]) => [path === "file2.txt" ? "renamed.txt" : path, content]));
  assert.notEqual((await treeHash(first)).sha256, (await treeHash(differentPath)).sha256);

  const differentContent = resolve(TEST_ROOT, "different-content");
  await writeFixture(differentContent, entries.map(([path, content]) => [path, path === "file2.txt" ? "changed" : content]));
  assert.notEqual((await treeHash(first)).sha256, (await treeHash(differentContent)).sha256);
});

async function writeFixture(root, entries, emptyDirectories = []) {
  await mkdir(root, { recursive: true });
  for (const directory of emptyDirectories) await mkdir(resolve(root, directory), { recursive: true });
  for (const [relativePath, content] of entries) {
    const target = resolve(root, relativePath);
    await mkdir(dirname(target), { recursive: true });
    await writeFile(target, content);
  }
}

function treeHash(root, { culture = "", includeFiles = false } = {}) {
  return new Promise((resolvePromise, reject) => {
    const args = ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", TREE_HASH_SCRIPT, "-Root", root];
    if (culture) args.push("-Culture", culture);
    if (includeFiles) args.push("-IncludeFiles");
    const child = spawn("powershell.exe", args, { cwd: PROJECT_ROOT, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.once("error", reject);
    child.once("close", (code) => {
      if (code !== 0) return reject(new Error(`tree_hash_failed:${code}:${stderr}`));
      try { resolvePromise(JSON.parse(stdout)); } catch (error) { reject(new Error(`tree_hash_json_invalid:${stdout}:${error}`)); }
    });
  });
}

function referenceHash(files) {
  const chunks = [Buffer.from("SearchTreeHashV1\0", "utf8"), uint64(files.length)];
  for (const file of files) {
    const path = Buffer.from(file.relative_path, "utf8");
    chunks.push(uint64(path.length), path, uint64(file.length), Buffer.from(file.sha256, "hex"));
  }
  return createHash("sha256").update(Buffer.concat(chunks)).digest("hex").toUpperCase();
}

function uint64(value) {
  const result = Buffer.alloc(8);
  result.writeBigUInt64LE(BigInt(value));
  return result;
}
