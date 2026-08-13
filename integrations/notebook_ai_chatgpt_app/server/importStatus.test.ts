import assert from "node:assert/strict";
import { mkdir, mkdtemp, readdir, rm, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import test from "node:test";

import type {
  ImportDocumentInput,
  ImportDocumentResponse,
  ImportPreviewInput,
  ImportPreviewResponse,
  ImportStatusInput,
  ImportStatusResponse,
} from "./contracts";
import {
  purgeExpiredStagedImports,
  releaseStagedImport,
  rememberStagedImport,
} from "./fileTransfer";
import { NotebookBackendError, NotebookClient } from "./notebookClient";
import { runImportDocumentTool } from "./tools/importDocument";
import { runImportPreviewTool } from "./tools/importPreview";
import { runImportStatusTool } from "./tools/importStatus";

const OPERATION_ID = "a".repeat(32);

class LifecycleClient extends NotebookClient {
  previewResult: (ImportPreviewResponse & { operation_id: string }) | null = null;
  importResult: ImportDocumentResponse | null = null;
  importError: unknown = null;
  statusResult: ImportStatusResponse | null = null;

  constructor() {
    super({
      baseUrl: "http://127.0.0.1:8000",
      fetchImpl: async () => new Response("{}"),
    });
  }

  override async importPreview(
    _input: ImportPreviewInput,
  ): Promise<ImportPreviewResponse & { operation_id: string }> {
    assert.ok(this.previewResult);
    return this.previewResult;
  }

  override async importDocument(_input: ImportDocumentInput): Promise<ImportDocumentResponse> {
    if (this.importError) throw this.importError;
    assert.ok(this.importResult);
    return this.importResult;
  }

  override async importStatus(_input: ImportStatusInput): Promise<ImportStatusResponse> {
    assert.ok(this.statusResult);
    return this.statusResult;
  }
}

function previewResponse(): ImportPreviewResponse & { operation_id: string } {
  return {
    status: "ok",
    operation_id: OPERATION_ID,
    source_type: "local_pdf",
    filename: "fixture.pdf",
    title: "Fixture",
    item_type: "book",
    pdf_sha256: "b".repeat(64),
    duplicate_status: "not_detected",
    existing_document_id: null,
    estimated_pages: 1,
    estimated_chunks: 6,
    document_type: "book",
    warnings: [],
    confirmation_token: "p".repeat(40),
    confirmation_expires_in_seconds: 600,
    attachment_choices: [],
    annotation_count: 0,
    annotation_comment_count: 0,
    child_note_count: 0,
  };
}

function committedResponse(): ImportDocumentResponse {
  return {
    status: "committed",
    operation_id: OPERATION_ID,
    terminal: true,
    document_id: 4,
    title: "Fixture",
    document_type: "book",
    chunk_count: 6,
    operation_in_progress: false,
    token_consumed: true,
    writes_performed: true,
    safe_to_retry: false,
  };
}

function statusResponse(
  status: ImportStatusResponse["status"],
  terminal: boolean,
): ImportStatusResponse {
  return {
    status,
    operation_id: OPERATION_ID,
    document_id: status === "committed" ? 4 : null,
    title: "Fixture",
    document_type: "book",
    chunk_count: status === "committed" ? 6 : null,
    terminal,
    operation_in_progress: status === "accepted" || status === "running",
    writes_performed: status === "accepted" ? false : true,
    token_consumed: true,
    safe_to_retry: false,
    replayed_receipt: status === "committed" || status === "failed",
    error_code: status === "failed" ? "fixture_failed" : null,
    error_stage: status === "failed" ? "candidate_validate" : null,
    rollback_attempted: status === "failed" ? true : false,
    rollback_completed: status === "failed" ? true : false,
  };
}

async function makeStagedImport(label: string): Promise<{
  directory: string;
  path: string;
  token: string;
}> {
  const root = resolve(
    process.env.SEARCH_TEST_TEMP_ROOT
      ?? resolve(process.cwd(), "..", "..", ".codex_tmp"),
  );
  await mkdir(root, { recursive: true });
  const directory = await mkdtemp(resolve(root, `mcp-import-status-${label}-`));
  const path = resolve(directory, "staged.pdf");
  await writeFile(path, "%PDF-1.4\nfixture", { flag: "wx" });
  const token = `${label.padEnd(8, "x")}-${"t".repeat(40)}`;
  rememberStagedImport(token, path);
  return { directory, path, token };
}

async function cleanupStagedImport(staged: Awaited<ReturnType<typeof makeStagedImport>>): Promise<void> {
  await releaseStagedImport(staged.token);
  await rm(staged.directory, { recursive: true, force: true });
}

test("terminal committed import releases its staged PDF", async () => {
  const staged = await makeStagedImport("commit");
  const client = new LifecycleClient();
  client.importResult = committedResponse();
  try {
    const result = await runImportDocumentTool(client, {
      confirmation_token: staged.token,
      confirmed: true,
    });
    assert.equal("structuredContent" in result, true);
    assert.deepEqual(await readdir(staged.directory), []);
  } finally {
    await cleanupStagedImport(staged);
  }
});

test("known durable failed import releases its staged PDF", async () => {
  const staged = await makeStagedImport("failed");
  const client = new LifecycleClient();
  client.importError = new NotebookBackendError(
    "private backend detail",
    500,
    "fixture_import_failed",
    {
      status: "failed",
      operation_id: OPERATION_ID,
      terminal: true,
      operation_in_progress: false,
      writes_performed: true,
      token_consumed: true,
      safe_to_retry: false,
      rollback_attempted: true,
      rollback_completed: true,
    },
  );
  try {
    const result = await runImportDocumentTool(client, {
      confirmation_token: staged.token,
      confirmed: true,
    });
    assert.equal(result.isError, true);
    assert.deepEqual(await readdir(staged.directory), []);
  } finally {
    await cleanupStagedImport(staged);
  }
});

for (const code of ["BACKEND_TIMEOUT", "BACKEND_UNAVAILABLE"] as const) {
  test(`${code} retains the staged PDF for the still-unknown backend operation`, async () => {
    const staged = await makeStagedImport(code.toLowerCase());
    const client = new LifecycleClient();
    client.importError = new NotebookBackendError("private transport detail", 503, code);
    try {
      const result = await runImportDocumentTool(client, {
        confirmation_token: staged.token,
        confirmed: true,
      });
      assert.equal(result.isError, true);
      assert.deepEqual(await readdir(staged.directory), ["staged.pdf"]);
    } finally {
      await cleanupStagedImport(staged);
    }
  });
}

test("running import and a secondary in-progress caller retain the primary staged PDF", async () => {
  const staged = await makeStagedImport("running");
  const client = new LifecycleClient();
  client.importResult = {
    ...committedResponse(),
    status: "in_progress",
    terminal: false,
    document_id: null,
    chunk_count: 0,
    operation_in_progress: true,
    writes_performed: null,
  };
  try {
    for (let invocation = 0; invocation < 2; invocation += 1) {
      await runImportDocumentTool(client, {
        confirmation_token: staged.token,
        confirmed: true,
      });
      assert.deepEqual(await readdir(staged.directory), ["staged.pdf"]);
    }
  } finally {
    await cleanupStagedImport(staged);
  }
});

test("import_status retains an active staged PDF and releases it only after a durable terminal status", async () => {
  const staged = await makeStagedImport("status");
  const client = new LifecycleClient();
  client.importResult = {
    ...committedResponse(),
    status: "in_progress",
    terminal: false,
    document_id: null,
    chunk_count: 0,
    operation_in_progress: true,
    writes_performed: null,
  };
  try {
    await runImportDocumentTool(client, {
      confirmation_token: staged.token,
      confirmed: true,
    });
    client.statusResult = statusResponse("running", false);
    await runImportStatusTool(client, { operation_id: OPERATION_ID });
    assert.deepEqual(await readdir(staged.directory), ["staged.pdf"]);

    client.statusResult = statusResponse("committed", true);
    const terminal = await runImportStatusTool(client, { operation_id: OPERATION_ID });
    assert.equal("structuredContent" in terminal, true);
    assert.deepEqual(await readdir(staged.directory), []);
  } finally {
    await cleanupStagedImport(staged);
  }
});

test("import_status exposes every durable state without making any import retryable", async () => {
  const client = new LifecycleClient();
  for (const [status, terminal] of [
    ["accepted", false],
    ["running", false],
    ["committed", true],
    ["failed", true],
    ["orphaned", true],
  ] as const) {
    client.statusResult = statusResponse(status, terminal);
    const result = await runImportStatusTool(client, { operation_id: OPERATION_ID });
    assert.equal("structuredContent" in result, true);
    if (!("structuredContent" in result)) continue;
    assert.equal(result.structuredContent.status, status);
    assert.equal(result.structuredContent.terminal, terminal);
    assert.equal(result.structuredContent.safe_to_retry, false);
    assert.equal(
      result.structuredContent.operation_in_progress,
      status === "accepted" || status === "running",
    );
  }
});

test("a timed-out import can be checked as running without issuing a second import", async () => {
  const staged = await makeStagedImport("recovery");
  const client = new LifecycleClient();
  client.importError = new NotebookBackendError(
    "private transport detail",
    504,
    "BACKEND_TIMEOUT",
  );
  client.statusResult = statusResponse("running", false);
  try {
    const timedOut = await runImportDocumentTool(client, {
      confirmation_token: staged.token,
      confirmed: true,
    });
    assert.equal(timedOut.isError, true);
    const status = await runImportStatusTool(client, { operation_id: OPERATION_ID });
    assert.equal("structuredContent" in status, true);
    if (!("structuredContent" in status)) return;
    assert.equal(status.structuredContent.status, "running");
    assert.equal(status.structuredContent.safe_to_retry, false);
    assert.deepEqual(await readdir(staged.directory), ["staged.pdf"]);
  } finally {
    await cleanupStagedImport(staged);
  }
});

test("preview operation identity releases a staged PDF after a timed-out import later becomes terminal", async () => {
  const root = resolve(
    process.env.SEARCH_TEST_TEMP_ROOT
      ?? resolve(process.cwd(), "..", "..", ".codex_tmp"),
  );
  await mkdir(root, { recursive: true });
  const directory = await mkdtemp(resolve(root, "mcp-preview-operation-"));
  const client = new LifecycleClient();
  client.previewResult = previewResponse();
  client.importError = new NotebookBackendError(
    "private transport detail",
    504,
    "BACKEND_TIMEOUT",
  );
  client.statusResult = statusResponse("committed", true);
  try {
    const preview = await runImportPreviewTool(
      client,
      {
        source_type: "local_pdf",
        file: {
          download_url: "https://files.openaiusercontent.com/fixture.pdf",
          file_id: "file_fixture",
          mime_type: "application/pdf",
          file_name: "fixture.pdf",
        },
      },
      {
        env: { SEARCH_IMPORT_INBOX: directory },
        fetchImpl: async () => new Response(
          Buffer.from("%PDF-1.4\nfixture"),
          { status: 200, headers: { "content-type": "application/pdf" } },
        ),
      },
    );
    assert.equal("structuredContent" in preview, true);
    if (!("structuredContent" in preview)) return;
    assert.equal(preview.structuredContent.operation_id, OPERATION_ID);
    assert.equal((await readdir(directory)).length, 1);

    const timedOut = await runImportDocumentTool(client, {
      confirmation_token: previewResponse().confirmation_token,
      confirmed: true,
    });
    assert.equal(timedOut.isError, true);
    assert.equal((await readdir(directory)).length, 1);

    await runImportStatusTool(client, { operation_id: OPERATION_ID });
    assert.deepEqual(await readdir(directory), []);
  } finally {
    await releaseStagedImport(previewResponse().confirmation_token);
    await rm(directory, { recursive: true, force: true });
  }
});

test("import_status rejects non-canonical operation identities before the backend call", async () => {
  class UnexpectedStatusClient extends LifecycleClient {
    calls = 0;
    override async importStatus(input: ImportStatusInput): Promise<ImportStatusResponse> {
      this.calls += 1;
      return super.importStatus(input);
    }
  }
  const client = new UnexpectedStatusClient();
  const result = await runImportStatusTool(client, { operation_id: "../journal" });
  assert.equal(result.isError, true);
  assert.equal(client.calls, 0);
  assert.doesNotMatch(JSON.stringify(result), /journal.*path|confirmation|bearer/i);
});

test("staged PDF TTL purge remains the final cleanup path for unknown operations", async () => {
  const staged = await makeStagedImport("ttl");
  try {
    await purgeExpiredStagedImports(Number.MAX_SAFE_INTEGER);
    assert.deepEqual(await readdir(staged.directory), []);
  } finally {
    await cleanupStagedImport(staged);
  }
});
