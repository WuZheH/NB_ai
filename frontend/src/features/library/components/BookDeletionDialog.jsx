import { useMemo, useState } from "react";
import { managementError, permanentlyDeleteDocuments } from "../api/libraryManagement.js";

export default function BookDeletionDialog({ previews, onClose, onCompleted, onRecheck }) {
  const [confirmationText, setConfirmationText] = useState("");
  const [idsConfirmed, setIdsConfirmed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState(null);
  const blockers = previews.flatMap((preview) => preview.deletion_blockers || []);
  const selectedIds = previews.map((preview) => Number(preview.document_id));
  const isBatch = previews.length > 1;
  const validText = isBatch
    ? confirmationText === "删除"
    : confirmationText === "删除" || confirmationText === previews[0]?.title;
  const totals = useMemo(() => previews.reduce((value, preview) => ({
    chunks: value.chunks + Number(preview.chunk_count || 0),
    notes: value.notes + Number(preview.personal_note_count || 0) + Number(preview.zotero_note_count || 0),
    objects: value.objects + Number(preview.object_link_count || 0),
    vectors: value.vectors + Number(preview.passage_vector_count || 0) + Number(preview.object_vector_impact_count || 0),
    rows: value.rows + Number(preview.estimated_deleted_rows || 0),
  }), { chunks: 0, notes: 0, objects: 0, vectors: 0, rows: 0 }), [previews]);

  async function confirmPermanentDeletion() {
    if (blockers.length || !validText || !idsConfirmed || submitting) return;
    setSubmitting(true);
    setFailure(null);
    try {
      const batch = await permanentlyDeleteDocuments(previews, confirmationText);
      if (batch.status !== "completed") {
        const error = new Error("删除后的索引或文件清理未完成。");
        error.code = batch.error_code || "deletion_cleanup_incomplete";
        throw error;
      }
      onCompleted?.(batch.results || []);
    } catch (error) {
      setFailure(managementError(error, "永久删除未完成。卡片将保留或重新检查实际状态。"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="bookDeletionBackdrop" role="presentation">
      <section className="bookDeletionDialog" role="dialog" aria-modal="true" aria-labelledby="bookDeletionTitle">
        <div className="bookDeletionHeader">
          <div>
            <p className="eyebrow">删除影响预览</p>
            <h3 id="bookDeletionTitle">永久删除 Search 书籍数据</h3>
          </div>
          <button className="quietButton" type="button" onClick={onClose} disabled={submitting}>关闭</button>
        </div>
        <p className="bookDeletionWarning">
          删除书籍数据会移除 Search 中的文档、片段、索引和关联数据。原始外部 PDF 默认保留。
        </p>
        <div className="bookDeletionTotals" aria-label="总影响">
          <span>{previews.length} 本</span>
          <span>{totals.chunks} 个片段</span>
          <span>{totals.notes} 条保留笔记</span>
          <span>{totals.objects} 个对象关系</span>
          <span>{totals.vectors} 个向量影响</span>
          <span>预计 {totals.rows} 行</span>
        </div>
        <div className="bookDeletionPreviewList">
          {previews.map((preview) => (
            <article key={preview.document_id} className="bookDeletionPreviewCard">
              <div><strong>{preview.title}</strong><code>document ID: {preview.document_id}</code></div>
              <dl>
                <div><dt>片段</dt><dd>{preview.chunk_count}</dd></div>
                <div><dt>笔记</dt><dd>{Number(preview.personal_note_count || 0) + Number(preview.zotero_note_count || 0)}（保留并解除关联）</dd></div>
                <div><dt>对象</dt><dd>{preview.object_link_count} / 共享 {preview.shared_object_count}</dd></div>
                <div><dt>向量</dt><dd>{Number(preview.passage_vector_count || 0) + Number(preview.object_vector_impact_count || 0)}</dd></div>
                <div><dt>PDF</dt><dd>{preview.retention?.managed_pdf === "delete" ? "删除 Search 管理副本" : "保留"}</dd></div>
                <div><dt>恢复包</dt><dd>{preview.recovery_package?.location?.basename || "SearchBookDeletion"}</dd></div>
              </dl>
              {(preview.warnings || []).length > 0 && (
                <div className="bookDeletionNotices"><strong>警告</strong>{preview.warnings.map((item) => <code key={item}>{item}</code>)}</div>
              )}
              {(preview.deletion_blockers || []).length > 0 && (
                <div className="bookDeletionBlockers"><strong>阻塞项</strong>{preview.deletion_blockers.map((item) => <code key={item.code}>{item.code}</code>)}</div>
              )}
            </article>
          ))}
        </div>
        <label className="bookDeletionIdConfirmation">
          <input type="checkbox" checked={idsConfirmed} onChange={(event) => setIdsConfirmed(event.target.checked)} />
          <span>我确认仅处理 document ID：{selectedIds.join(", ")}</span>
        </label>
        <label className="bookDeletionTextConfirmation">
          <span>{isBatch ? "输入“删除”继续" : `输入准确书名“${previews[0]?.title}”或“删除”继续`}</span>
          <input value={confirmationText} onChange={(event) => setConfirmationText(event.target.value)} autoComplete="off" />
        </label>
        {failure && (
          <div className="bookDeletionFailure" role="alert">
            <strong>删除未完成</strong>
            <span>{failure.message}</span>
            <code>诊断码：{failure.code}</code>
            <button className="quietButton" type="button" onClick={onRecheck}>重新检查删除影响</button>
          </div>
        )}
        <div className="bookDeletionActions">
          <button className="quietButton" type="button" onClick={onClose} disabled={submitting}>取消</button>
          <button
            className="dangerButton"
            type="button"
            disabled={submitting || blockers.length > 0 || !validText || !idsConfirmed}
            onClick={confirmPermanentDeletion}
          >
            {submitting ? "正在执行事务删除…" : isBatch ? "永久删除所选书籍的 Search 数据" : "永久删除此书的 Search 数据"}
          </button>
        </div>
      </section>
    </div>
  );
}
