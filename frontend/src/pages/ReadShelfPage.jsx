import { useState } from "react";
import { API_BASE_URL } from "../api/client.js";
import PdfCoverThumbnail from "../PdfCoverThumbnail.jsx";
import StateMessage from "../components/StateMessage.jsx";
import BookDeletionDialog from "../features/library/components/BookDeletionDialog.jsx";
import {
  archiveShelfDocuments,
  loadDeletionPreview,
  managementError,
  restoreShelfDocuments,
} from "../features/library/api/libraryManagement.js";

export default function ReadShelfPage({
  state,
  apiStatus,
  selectedDocumentId,
  onOpenDocument,
  onOpenImport,
  onRefresh,
  onShelfViewChange,
  onBooksChanged,
}) {
  const [managementMode, setManagementMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState([]);
  const [deleteManagedPdf, setDeleteManagedPdf] = useState(false);
  const [previewState, setPreviewState] = useState({ status: "idle", previews: [], error: null });
  const [actionState, setActionState] = useState({ status: "idle", message: "", code: "" });
  const shelfView = state.view || "active";

  if (apiStatus?.phase === "starting") {
    return <StateMessage title="服务正在启动" body="Search Runtime 就绪后会自动加载已读书架。" />;
  }
  if (apiStatus?.phase === "checking") {
    return <StateMessage title="正在重新检查本地 API" body="检查完成后会自动刷新已读书架。" />;
  }
  if (apiStatus?.phase === "unavailable" && state.status === "idle") {
    return (
      <StateMessage
        title="本地 API 不可用"
        body={`无法连接本地 API。（诊断码：${apiStatus.errorCode || "api_unavailable"}）`}
      />
    );
  }
  if (state.status === "loading") return <StateMessage title="正在加载已读书架" />;
  if (state.status === "error") {
    return (
      <StateMessage
        title={state.errorTitle || "已读书架暂不可用"}
        body={`${state.error}（诊断码：${state.errorCode || "api_request_failed"}）`}
      />
    );
  }

  function enterManagement() {
    setManagementMode(true);
    setSelectedIds([]);
    setDeleteManagedPdf(false);
    setActionState({ status: "idle", message: "", code: "" });
  }

  function cancelManagement() {
    setManagementMode(false);
    setSelectedIds([]);
    setDeleteManagedPdf(false);
    setPreviewState({ status: "idle", previews: [], error: null });
    if (shelfView !== "active") onShelfViewChange?.("active");
  }

  function toggleDocument(documentId) {
    const id = Number(documentId);
    setSelectedIds((current) => {
      if (current.includes(id)) return current.filter((value) => value !== id);
      if (current.length >= 5) {
        setActionState({ status: "error", message: "一次最多选择 5 本书。", code: "library_batch_limit_exceeded" });
        return current;
      }
      return [...current, id];
    });
  }

  async function openDeletionPreview() {
    if (!selectedIds.length) return;
    setPreviewState({ status: "loading", previews: [], error: null });
    try {
      const previews = [];
      for (const documentId of selectedIds) {
        previews.push(await loadDeletionPreview(documentId, { deleteManagedPdf }));
      }
      setPreviewState({ status: "ready", previews, error: null });
    } catch (error) {
      setPreviewState({ status: "error", previews: [], error: managementError(error, "无法读取删除影响。") });
    }
  }

  async function archiveSelected() {
    if (!selectedIds.length) return;
    setActionState({ status: "loading", message: "正在移出书架…", code: "" });
    try {
      await archiveShelfDocuments(selectedIds);
      setSelectedIds([]);
      setActionState({ status: "success", message: "已移出书架，可在“已归档”视图恢复。", code: "archive_completed" });
      await onBooksChanged?.("active");
    } catch (error) {
      const failure = managementError(error, "移出书架失败。");
      setActionState({ status: "error", message: failure.message, code: failure.code });
    }
  }

  async function restoreSelected() {
    if (!selectedIds.length) return;
    setActionState({ status: "loading", message: "正在恢复书架…", code: "" });
    try {
      await restoreShelfDocuments(selectedIds);
      setSelectedIds([]);
      setActionState({ status: "success", message: "已恢复到活动书架。", code: "archive_restore_completed" });
      await onBooksChanged?.("archived");
    } catch (error) {
      const failure = managementError(error, "恢复书架失败。");
      setActionState({ status: "error", message: failure.message, code: failure.code });
    }
  }

  async function handleDeletionCompleted(results) {
    setPreviewState({ status: "idle", previews: [], error: null });
    setSelectedIds([]);
    setActionState({
      status: "success",
      message: `已永久删除 ${results.length} 本书的 Search 数据；恢复包已创建。`,
      code: "deletion_completed",
    });
    await onBooksChanged?.(shelfView);
  }

  function refreshShelf() {
    setSelectedIds([]);
    setPreviewState({ status: "idle", previews: [], error: null });
    onRefresh?.(shelfView);
  }

  return (
    <section className="pageStack readShelfPage">
      <div className="pageHeader">
        <div>
          <h3>{shelfView === "archived" ? "已归档资料" : "已读 / 已掌握资料"}</h3>
          <p>{shelfView === "archived" ? "归档资料默认不参与搜索，可随时恢复。" : "仅显示本地已有的简短元数据与摘要。"}</p>
        </div>
        <div className="pageHeaderActions">
          {!managementMode ? (
            <>
              <button className="primaryButton" type="button" onClick={onOpenImport}>导入书籍</button>
              <button className="quietButton" type="button" onClick={enterManagement}>管理书架</button>
              <button className="quietButton" type="button" onClick={refreshShelf}>刷新书架</button>
            </>
          ) : (
            <>
              <span className="shelfSelectionCount">已选择 {selectedIds.length} 本</span>
              <button className="quietButton" type="button" onClick={() => {
                const next = shelfView === "active" ? "archived" : "active";
                setSelectedIds([]);
                onShelfViewChange?.(next);
              }}>{shelfView === "active" ? "查看已归档" : "返回活动书架"}</button>
              <button className="quietButton" type="button" disabled={!selectedIds.length || previewState.status === "loading"} onClick={openDeletionPreview}>
                {previewState.status === "loading" ? "正在检查影响…" : "查看删除影响"}
              </button>
              {shelfView === "active" ? (
                <button className="quietButton" type="button" disabled={!selectedIds.length || actionState.status === "loading"} onClick={archiveSelected}>移出书架</button>
              ) : (
                <button className="quietButton" type="button" disabled={!selectedIds.length || actionState.status === "loading"} onClick={restoreSelected}>恢复到书架</button>
              )}
              <label className="managedPdfDeleteOption" title="仅影响 Search 数据根内的 PDF 副本；外部 PDF 始终保留。">
                <input
                  type="checkbox"
                  checked={deleteManagedPdf}
                  onChange={(event) => {
                    setDeleteManagedPdf(event.target.checked);
                    setPreviewState({ status: "idle", previews: [], error: null });
                  }}
                />
                <span>同时删除 Search 管理的 PDF 副本</span>
              </label>
              <button className="dangerButton" type="button" disabled={!selectedIds.length || previewState.status === "loading"} onClick={openDeletionPreview}>删除所选</button>
              <button className="quietButton" type="button" onClick={cancelManagement}>取消管理</button>
            </>
          )}
        </div>
      </div>

      {actionState.status !== "idle" && (
        <div className={`shelfActionMessage ${actionState.status}`} role="status">
          <span>{actionState.message}</span>
          {actionState.code && <code>诊断码：{actionState.code}</code>}
        </div>
      )}
      {previewState.status === "error" && (
        <div className="shelfActionMessage error" role="alert">
          <span>{previewState.error?.message}</span>
          <code>诊断码：{previewState.error?.code}</code>
          <button className="quietButton" type="button" onClick={openDeletionPreview}>重新检查</button>
        </div>
      )}

      {!state.items.length ? (
        <StateMessage
          title={shelfView === "archived" ? "暂无归档资料" : "暂无已读文档"}
          body={state.error || (shelfView === "archived" ? "移出书架的资料会显示在这里。" : "可通过“导入书籍”添加本地 PDF。")}
        />
      ) : (
        <div className="cardGrid">
          {state.items.map((item) => {
            const documentId = Number(item.duplicate_primary_document_id || item.document_id);
            const checked = selectedIds.includes(documentId);
            return (
              <article
                key={item.document_id}
                className={`documentCard ${selectedDocumentId === item.document_id ? "selected" : ""} ${item.duplicate_count > 1 ? "duplicateDocument" : ""} ${managementMode ? "managementCard" : ""} ${checked ? "managementSelected" : ""}`}
                role="button"
                tabIndex={0}
                onClick={() => managementMode ? toggleDocument(documentId) : onOpenDocument(documentId)}
                onKeyDown={(event) => {
                  if (event.key !== "Enter" && event.key !== " ") return;
                  event.preventDefault();
                  managementMode ? toggleDocument(documentId) : onOpenDocument(documentId);
                }}
              >
                {managementMode && (
                  <label className="shelfCardCheckbox" onClick={(event) => event.stopPropagation()}>
                    <input type="checkbox" checked={checked} onChange={() => toggleDocument(documentId)} />
                    <span>选择</span>
                  </label>
                )}
                <div className="documentCardCoverSlot">
                  <PdfCoverThumbnail apiBase={API_BASE_URL} documentId={item.document_id} title={item.title} documentType={item.document_type} />
                </div>
                <div className="documentCardTitleBlock"><h3 className="cardTitle">{item.title}</h3></div>
                <div className="documentCardMeta" aria-label="文档元数据">
                  <span>{documentTypeLabel(item.document_type)}</span>
                  {item.object_import_mode === "chaptered" && <span>chaptered</span>}
                  {Number(item.chapter_count || 0) > 0 && <span>{item.chapter_count} 章</span>}
                  <span>{item.chunk_count ?? item.evidence_count ?? 0} 证据</span>
                  {item.duplicate_count > 1 && <span className="duplicateBadge">可能重复 / {item.duplicate_count}</span>}
                </div>
                <p className="duplicateDocumentNotice" title={item.duplicate_warning || ""}>{item.duplicate_warning || "\u00a0"}</p>
                {!managementMode && <button className="quietButton readShelfOpenBookButton" type="button" onClick={(event) => { event.stopPropagation(); onOpenDocument(documentId); }}>打开书籍</button>}
              </article>
            );
          })}
        </div>
      )}

      {previewState.status === "ready" && previewState.previews.length > 0 && (
        <BookDeletionDialog
          previews={previewState.previews}
          onClose={() => setPreviewState({ status: "idle", previews: [], error: null })}
          onCompleted={handleDeletionCompleted}
          onRecheck={() => {
            setPreviewState({ status: "idle", previews: [], error: null });
            void openDeletionPreview();
          }}
        />
      )}
    </section>
  );
}

function documentTypeLabel(value) {
  return {
    book: "书籍",
    paper: "论文",
    thesis: "学位论文",
    report: "报告",
    pdf: "PDF",
    other: "PDF",
    unknown: "PDF",
  }[value] || value || "未知类型";
}
