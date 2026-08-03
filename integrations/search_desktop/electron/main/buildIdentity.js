import { execFile } from "node:child_process";
import { readFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { promisify } from "node:util";

import { DESKTOP_ROOT, SOURCE_PROJECT_ROOT } from "./config.js";

export const BUILD_IDENTITY_SCHEMA_VERSION = "search.build-identity.v1";
export const BUILD_IDENTITY_PROPERTY = "searchBuildIdentity";
export const DEVELOPMENT_BUILD_ID = "development";

const runFile = promisify(execFile);

export async function loadBuildIdentityForApp(app, {
  read = readFile,
  runGit = defaultRunGit,
  now = () => new Date(),
} = {}) {
  const packagePath = app.isPackaged
    ? join(resolve(app.getAppPath()), "package.json")
    : join(DESKTOP_ROOT, "package.json");
  const packageValue = await readPackage(packagePath, read);
  if (app.isPackaged) {
    if (!packageValue[BUILD_IDENTITY_PROPERTY]) {
      throw new Error("search_packaged_build_identity_missing");
    }
    return validateBuildIdentity(packageValue[BUILD_IDENTITY_PROPERTY], {
      expectedVersion: packageValue.version,
      expectedMode: "packaged",
    });
  }
  const git = await readDevelopmentGitIdentity(runGit);
  return Object.freeze({
    schema_version: BUILD_IDENTITY_SCHEMA_VERSION,
    build_mode: "development",
    product: "Search",
    version: packageValue.version,
    build_id: DEVELOPMENT_BUILD_ID,
    source_commit: git.sourceCommit,
    source_branch: git.sourceBranch,
    build_timestamp_utc: now().toISOString(),
  });
}

export function validateBuildIdentity(value, { expectedVersion, expectedMode } = {}) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("search_build_identity_invalid");
  }
  const identity = {
    schema_version: clean(value.schema_version),
    build_mode: clean(value.build_mode),
    product: clean(value.product),
    version: clean(value.version),
    build_id: clean(value.build_id),
    source_commit: clean(value.source_commit).toLowerCase(),
    source_branch: clean(value.source_branch),
    build_timestamp_utc: clean(value.build_timestamp_utc),
  };
  if (
    identity.schema_version !== BUILD_IDENTITY_SCHEMA_VERSION
    || identity.product !== "Search"
    || !/^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/.test(identity.version)
    || !["development", "packaged"].includes(identity.build_mode)
    || !identity.source_branch
    || !validTimestamp(identity.build_timestamp_utc)
  ) {
    throw new Error("search_build_identity_invalid");
  }
  if (expectedVersion && identity.version !== expectedVersion) {
    throw new Error("search_build_identity_version_mismatch");
  }
  if (expectedMode && identity.build_mode !== expectedMode) {
    throw new Error("search_build_identity_mode_mismatch");
  }
  if (identity.build_mode === "packaged") {
    if (
      identity.build_id === DEVELOPMENT_BUILD_ID
      || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(identity.build_id)
      || !/^[0-9a-f]{40}$/.test(identity.source_commit)
    ) {
      throw new Error("search_packaged_build_identity_invalid");
    }
  } else if (
    identity.build_id !== DEVELOPMENT_BUILD_ID
    || !/^(?:[0-9a-f]{40}|unavailable)$/.test(identity.source_commit)
  ) {
    throw new Error("search_development_build_identity_invalid");
  }
  return Object.freeze(identity);
}

export function encodeBuildIdentityArgument(identity) {
  const validated = validateBuildIdentity(identity, { expectedMode: identity?.build_mode });
  return `--search-build-identity=${encodeURIComponent(JSON.stringify(validated))}`;
}

async function readPackage(path, read) {
  let value;
  try {
    value = JSON.parse(await read(path, "utf8"));
  } catch {
    throw new Error("search_package_metadata_invalid");
  }
  if (
    value?.productName !== "Search"
    || typeof value?.version !== "string"
    || !/^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/.test(value.version)
  ) {
    throw new Error("search_package_metadata_invalid");
  }
  return value;
}

async function readDevelopmentGitIdentity(runGit) {
  let sourceCommit = "unavailable";
  let sourceBranch = "unavailable";
  try {
    sourceCommit = clean(await runGit(["rev-parse", "HEAD"])).toLowerCase();
    if (!/^[0-9a-f]{40}$/.test(sourceCommit)) sourceCommit = "unavailable";
    const branch = clean(await runGit(["symbolic-ref", "--short", "-q", "HEAD"]));
    sourceBranch = branch || (sourceCommit === "unavailable" ? "unavailable" : "(detached)");
  } catch {
    sourceCommit = "unavailable";
    sourceBranch = "unavailable";
  }
  return { sourceCommit, sourceBranch };
}

async function defaultRunGit(argumentsList) {
  const { stdout } = await runFile("git", argumentsList, {
    cwd: SOURCE_PROJECT_ROOT,
    windowsHide: true,
    shell: false,
    encoding: "utf8",
  });
  return stdout;
}

function validTimestamp(value) {
  return value.endsWith("Z") && Number.isFinite(Date.parse(value));
}

function clean(value) {
  return String(value ?? "").trim();
}
