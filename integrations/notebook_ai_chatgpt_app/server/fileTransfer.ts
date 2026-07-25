import { createHash, randomBytes } from "node:crypto";
import { createWriteStream } from "node:fs";
import { mkdir, open, rename, stat, unlink } from "node:fs/promises";
import { isAbsolute, join } from "node:path";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";

import { NotebookBackendError } from "./notebookClient.js";

const MAX_PDF_BYTES = 200 * 1024 * 1024;
const STAGED_IMPORT_TTL_MS = 10 * 60 * 1000;
const stagedImports = new Map<string, { path: string; expiresAt: number }>();

export interface OpenAIFileInput {
  download_url: string;
  file_id: string;
  mime_type?: string;
  file_name?: string;
}

export async function stageChatPdf(
  file: OpenAIFileInput,
  options: {
    env?: NodeJS.ProcessEnv;
    fetchImpl?: typeof fetch;
  } = {},
): Promise<{ filename: string; path: string; sha256: string; size: number }> {
  const environment = options.env ?? process.env;
  const stagingDirectory = String(environment.SEARCH_IMPORT_INBOX ?? "").trim();
  if (!stagingDirectory || !isAbsolute(stagingDirectory)) {
    throw new NotebookBackendError(
      "Search attachment staging is not configured.",
      503,
      "IMPORT_STAGING_NOT_CONFIGURED",
    );
  }
  const downloadUrl = validateDownloadUrl(file.download_url);
  const declaredMime = String(file.mime_type ?? "").trim().toLowerCase();
  if (declaredMime && declaredMime !== "application/pdf") {
    throw new NotebookBackendError("Attachment must be a PDF.", 422, "IMPORT_FILE_TYPE_INVALID");
  }
  await mkdir(stagingDirectory, { recursive: true });
  await purgeExpiredStagedImports();
  const randomId = randomBytes(12).toString("hex");
  const temporaryPath = join(stagingDirectory, `chat-upload-${randomId}.part`);
  const fetchImpl = options.fetchImpl ?? fetch;
  let response: Response;
  try {
    response = await fetchImpl(downloadUrl, {
      method: "GET",
      redirect: "follow",
      signal: AbortSignal.timeout(60_000),
      headers: { Accept: "application/pdf,application/octet-stream;q=0.5" },
    });
  } catch (error) {
    throw new NotebookBackendError(
      "ChatGPT attachment download failed.",
      502,
      "IMPORT_FILE_DOWNLOAD_FAILED",
    );
  }
  if (!response.ok || !response.body) {
    throw new NotebookBackendError(
      "ChatGPT attachment download failed.",
      502,
      "IMPORT_FILE_DOWNLOAD_FAILED",
    );
  }
  if (response.url && new URL(response.url).protocol !== "https:") {
    throw new NotebookBackendError(
      "Attachment redirect was not secure.",
      422,
      "IMPORT_FILE_URL_INVALID",
    );
  }
  const contentLength = Number(response.headers.get("content-length") ?? "0");
  if (Number.isFinite(contentLength) && contentLength > MAX_PDF_BYTES) {
    throw new NotebookBackendError("PDF exceeds 200 MB.", 413, "IMPORT_FILE_TOO_LARGE");
  }
  const responseMime = String(response.headers.get("content-type") ?? "")
    .split(";", 1)[0]
    .trim()
    .toLowerCase();
  if (!declaredMime && responseMime !== "application/pdf") {
    throw new NotebookBackendError("Attachment must be a PDF.", 422, "IMPORT_FILE_TYPE_INVALID");
  }
  const digest = createHash("sha256");
  let size = 0;
  const source = Readable.fromWeb(response.body as never);
  source.on("data", (chunk: Buffer) => {
    size += chunk.length;
    if (size > MAX_PDF_BYTES) {
      source.destroy(new NotebookBackendError("PDF exceeds 200 MB.", 413, "IMPORT_FILE_TOO_LARGE"));
      return;
    }
    digest.update(chunk);
  });
  try {
    await pipeline(source, createWriteStream(temporaryPath, { flags: "wx" }));
    const handle = await open(temporaryPath, "r");
    try {
      const magic = Buffer.alloc(5);
      await handle.read(magic, 0, magic.length, 0);
      if (!magic.equals(Buffer.from("%PDF-"))) {
        throw new NotebookBackendError("Attachment is not a valid PDF.", 422, "IMPORT_FILE_INVALID");
      }
    } finally {
      await handle.close();
    }
    const sha256 = digest.digest("hex");
    const filename = `chat-upload-${sha256.slice(0, 16)}-${randomId}.pdf`;
    const finalPath = join(stagingDirectory, filename);
    await rename(temporaryPath, finalPath);
    return { filename, path: finalPath, sha256, size };
  } catch (error) {
    await unlink(temporaryPath).catch(() => undefined);
    if (error instanceof NotebookBackendError) throw error;
    throw new NotebookBackendError(
      "ChatGPT attachment could not be staged.",
      500,
      "IMPORT_FILE_STAGING_FAILED",
    );
  }
}

export function rememberStagedImport(confirmationToken: string, path: string): void {
  stagedImports.set(confirmationToken, {
    path,
    expiresAt: Date.now() + STAGED_IMPORT_TTL_MS,
  });
}

export async function releaseStagedImport(confirmationToken: string): Promise<void> {
  const staged = stagedImports.get(confirmationToken);
  stagedImports.delete(confirmationToken);
  if (staged) {
    await unlink(staged.path).catch(() => undefined);
  }
}

export async function discardStagedPath(path: string): Promise<void> {
  await unlink(path).catch(() => undefined);
}

async function purgeExpiredStagedImports(): Promise<void> {
  const now = Date.now();
  const expired = [...stagedImports.entries()].filter(([, value]) => value.expiresAt <= now);
  for (const [token, value] of expired) {
    stagedImports.delete(token);
    const info = await stat(value.path).catch(() => null);
    if (info?.isFile()) {
      await unlink(value.path).catch(() => undefined);
    }
  }
}

function validateDownloadUrl(value: string): string {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new NotebookBackendError("Attachment URL is invalid.", 422, "IMPORT_FILE_URL_INVALID");
  }
  if (parsed.protocol !== "https:" || parsed.username || parsed.password) {
    throw new NotebookBackendError("Attachment URL is invalid.", 422, "IMPORT_FILE_URL_INVALID");
  }
  const hostname = parsed.hostname.toLowerCase();
  if (
    hostname === "localhost"
    || hostname === "::1"
    || /^127\./.test(hostname)
    || /^10\./.test(hostname)
    || /^192\.168\./.test(hostname)
    || /^169\.254\./.test(hostname)
    || /^172\.(1[6-9]|2\d|3[01])\./.test(hostname)
  ) {
    throw new NotebookBackendError("Attachment URL is invalid.", 422, "IMPORT_FILE_URL_INVALID");
  }
  return parsed.toString();
}
