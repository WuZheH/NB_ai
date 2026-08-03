import { getJson, postJson } from "../../../api/client.js";

let mutationSession = { token: "", expiresAt: 0 };

export async function loadDeletionPreview(documentId, { deleteManagedPdf = false } = {}) {
  return getJson(
    `/api/v1/library/documents/${Number(documentId)}/deletion-preview?delete_managed_pdf=${deleteManagedPdf ? "true" : "false"}`,
    { timeoutMs: 30000 },
  );
}

export async function archiveShelfDocuments(documentIds) {
  return postLibraryMutation("/api/v1/library/management/archive", {
    document_ids: normalizedIds(documentIds),
  });
}

export async function restoreShelfDocuments(documentIds) {
  return postLibraryMutation("/api/v1/library/management/restore", {
    document_ids: normalizedIds(documentIds),
  });
}

export async function permanentlyDeleteDocument(preview, confirmationText) {
  return postLibraryMutation(
    `/api/v1/library/documents/${Number(preview.document_id)}/delete`,
    {
      document_id: Number(preview.document_id),
      preview_token: preview.preview_token,
      expected_document_revision: preview.document_revision,
      confirmation_text: confirmationText,
      deletion_options: preview.deletion_options,
    },
    { timeoutMs: 120000 },
  );
}

export async function permanentlyDeleteDocuments(previews, confirmationText) {
  if (previews.length === 1) {
    const result = await permanentlyDeleteDocument(previews[0], confirmationText);
    return { status: result.status, results: [result], error_code: result.error_code };
  }
  const documentIds = previews.map((preview) => Number(preview.document_id));
  return postLibraryMutation(
    "/api/v1/library/documents/delete-batch",
    {
      document_ids: documentIds,
      confirmation_text: confirmationText,
      requests: previews.map((preview) => ({
        document_id: Number(preview.document_id),
        preview_token: preview.preview_token,
        expected_document_revision: preview.document_revision,
        confirmation_text: "删除",
        deletion_options: preview.deletion_options,
      })),
    },
    { timeoutMs: 300000 },
  );
}

export function managementError(error, fallback = "书架管理操作失败。") {
  return {
    code: String(error?.backendCode || error?.code || "library_management_failed"),
    message: String(error?.payload?.detail?.message || error?.message || fallback),
  };
}

async function postLibraryMutation(path, body, options = {}) {
  const token = await mutationToken();
  return postJson(path, body, {
    ...options,
    headers: {
      ...(options.headers || {}),
      "X-Search-Mutation-Token": token,
    },
  });
}

async function mutationToken() {
  const now = Date.now();
  if (mutationSession.token && mutationSession.expiresAt > now + 30000) {
    return mutationSession.token;
  }
  const payload = await postJson(
    "/api/v1/library/management/mutation-session",
    {},
    { timeoutMs: 10000 },
  );
  mutationSession = {
    token: String(payload.mutation_token || ""),
    expiresAt: now + Number(payload.expires_in_seconds || 0) * 1000,
  };
  return mutationSession.token;
}

function normalizedIds(values) {
  const ids = [...new Set((values || []).map(Number).filter((value) => Number.isInteger(value) && value > 0))];
  if (!ids.length || ids.length > 5) throw new Error("一次只能处理 1 到 5 本书。");
  return ids;
}
