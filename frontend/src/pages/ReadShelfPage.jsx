import { API_BASE_URL } from "../api/client.js";
import PdfCoverThumbnail from "../PdfCoverThumbnail.jsx";
import StateMessage from "../components/StateMessage.jsx";

export default function ReadShelfPage({
  state,
  apiStatus,
  selectedDocumentId,
  onOpenDocument,
  onOpenWorkspace,
  onOpenImport,
  onRefresh,
}) {
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
  if (!state.items.length) {
    return (
      <StateMessage
        title="暂无已读文档"
        body={state.error || "仅显示本地已读或已掌握资料。"}
      />
    );
  }
  return (
    <section className="pageStack">
      <div className="pageHeader">
        <div>
          <h3>已读 / 已掌握资料</h3>
          <p>仅显示本地已有的简短元数据与摘要。</p>
        </div>
        <div className="pageHeaderActions">
          <button className="primaryButton" type="button" onClick={onOpenImport}>
            导入书籍
          </button>
          <button className="quietButton" type="button" onClick={() => onOpenWorkspace?.()}>
            打开 Research Workspace
          </button>
          <button className="quietButton" type="button" onClick={onRefresh}>
            刷新书架
          </button>
        </div>
      </div>
      <div className="cardGrid">
        {state.items.map((item) => (
          <article
            key={item.document_id}
            className={`documentCard ${selectedDocumentId === item.document_id ? "selected" : ""} ${item.duplicate_count > 1 ? "duplicateDocument" : ""}`}
            role="button"
            tabIndex={0}
            onClick={() => onOpenDocument(item.duplicate_primary_document_id || item.document_id)}
            onKeyDown={(event) => event.key === "Enter" && onOpenDocument(item.duplicate_primary_document_id || item.document_id)}
          >
            <div className="documentCardCoverSlot">
              <PdfCoverThumbnail
                apiBase={API_BASE_URL}
                documentId={item.document_id}
                title={item.title}
                documentType={item.document_type}
              />
            </div>
            <div className="documentCardTitleBlock">
              <h3 className="cardTitle">{item.title}</h3>
            </div>
            <div className="documentCardMeta" aria-label="文档元数据">
              <span>{documentTypeLabel(item.document_type)}</span>
              {item.object_import_mode === "chaptered" && <span>chaptered</span>}
              {Number(item.chapter_count || 0) > 0 && <span>{item.chapter_count} 章</span>}
              <span>{item.chunk_count ?? item.evidence_count ?? 0} 证据</span>
              {item.duplicate_count > 1 && <span className="duplicateBadge">可能重复 / {item.duplicate_count}</span>}
            </div>
            <p className="duplicateDocumentNotice" title={item.duplicate_warning || ""}>
              {item.duplicate_warning || "\u00a0"}
            </p>
            {item.object_import_mode === "chaptered" && (
              <button
                className="quietButton readShelfWorkspaceButton"
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  onOpenWorkspace?.(item.duplicate_primary_document_id || item.document_id);
                }}
              >
                打开 Research Workspace
              </button>
            )}
          </article>
        ))}
      </div>
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
