import { API_BASE_URL } from "../api/client.js";
import PdfLocationPreview, { buildDocumentPdfPath, extractPage } from "../PdfLocationPreview.jsx";
import StateMessage from "../components/StateMessage.jsx";
import SourceTraceLine from "../components/SourceTraceLine.jsx";
import PdfPageHint from "../components/PdfPageHint.jsx";
import SourceLocationConfidenceNote from "../components/SourceLocationConfidenceNote.jsx";
import LocatorSummary from "../components/LocatorSummary.jsx";
import PdfActionGroup from "../components/PdfActionGroup.jsx";
import ObjectCandidateCard from "../components/ObjectCandidateCard.jsx";
import RelationSection from "../components/RelationSection.jsx";
import { enhanceSourceWithZoteroCandidate, withSourceLocationConfidence } from "../utils/formatters.js";
import { cleanSearchSnippet } from "../utils/snippet.js";

export default function EvidenceDetailPage({ state, locatorState, evidenceObjectState, zoteroCandidateState, onLocateEvidence, onOpenObject, onBack }) {
  if (state.status === "loading") return <StateMessage title="正在加载证据" />;
  if (state.status === "error") return <StateMessage title="证据暂不可用" body={state.error} />;
  if (!state.data?.evidence) {
    return <StateMessage title="未选择证据" body="从搜索结果或证据预览中选择证据。" />;
  }

  const { evidence, linked_notes: linkedNotes = [], linked_relations: linkedRelations = [] } = state.data;
  const objectState = evidenceObjectState.byChunkId[evidence.chunk_id] || { status: "idle", objects: [] };
  const locatorResult = locatorState.byChunkId[evidence.chunk_id];
  const location = locatorResult?.payload?.location;
  const evidencePage = evidence.pdf_page_start ?? evidence.pdf_page;
  const evidenceHeading = evidence.heading_path || evidence.section_label || "未识别章节";
  const evidenceText = evidence.chunk_text || evidence.full_text || evidence.snippet || "";
  const cleanEvidenceText = cleanSearchSnippet(evidenceText);
  const canLocate = evidence.is_locatable === true;
  const evidenceSource = withSourceLocationConfidence(
    enhanceSourceWithZoteroCandidate(
      { ...evidence, pdf_page: evidencePage },
      zoteroCandidateState.byDocumentId[evidence.document_id]
    ),
    location
  );
  const sourcePdfUrl = evidenceSource.preferred_source_open_url || evidenceSource.pdf_fallback_url || "";
  const previewPage = location?.pdf_page ?? extractPage(sourcePdfUrl) ?? evidencePage;
  const previewPdfUrl = buildDocumentPdfPath(evidence.document_id);
  return (
    <section className="detailStack">
      <button className="detailBack" type="button" onClick={onBack}>
        返回
      </button>
      <article className="detailHeader">
        <h3>{evidence.title}</h3>
        <div className="chunkSectionPath">
          <span>位置</span>
          <strong>{evidenceHeading}</strong>
        </div>
        <p>{cleanEvidenceText}</p>
        <SourceTraceLine trace={evidence.source_trace} fallback={evidenceSource} />
        <PdfActionGroup
          source={{ ...evidenceSource, locator_result: location }}
          showPreview
          onPreview={() => onLocateEvidence(evidence.chunk_id)}
        />
        <PdfPageHint source={evidenceSource} />
        <SourceLocationConfidenceNote source={evidenceSource} location={location} />
        <LocatorSummary state={locatorResult} />
        {canLocate && (locatorResult?.status === "ready" || locatorResult?.status === "error") && (
          <PdfLocationPreview
            apiBase={API_BASE_URL}
            documentId={evidence.document_id}
            location={location}
            pdfUrl={previewPdfUrl}
            page={previewPage}
            pdf_page_start={evidence.pdf_page_start}
            chunkId={evidence.chunk_id}
            highlightText={evidenceText}
          />
        )}
      </article>
      <ObjectCandidateSection
        state={objectState}
        onOpenObject={onOpenObject}
      />
      <section>
        <div className="sectionHeader">
          <h3>我的笔记</h3>
          <span>{linkedNotes.length} 条笔记</span>
        </div>
        {linkedNotes.length === 0 ? (
          <StateMessage title="暂无与该证据直接关联的个人笔记" body="有 note_evidence_links 时会显示在这里。" />
        ) : (
          linkedNotes.map((note) => (
            <article key={note.note_id} className="resultItem">
              <h3>{note.title}</h3>
              <p>{cleanSearchSnippet(note.summary || note.snippet)}</p>
            </article>
          ))
        )}
      </section>
      <RelationSection relations={linkedRelations} emptyBody="当前只读视图暂无关系记录。" />
    </section>
  );
}

function ObjectCandidateSection({ state, onOpenObject }) {
  const objects = state.objects || [];
  return (
    <section>
      <div className="sectionHeader">
        <h3>相关对象</h3>
        <span>{objects.length} 个候选</span>
      </div>
      {state.status === "loading" && <StateMessage title="正在派生相关对象" />}
      {state.status !== "loading" && !objects.length && (
        <StateMessage title="暂无相关对象候选" body="当前只读规则未从该证据片段派生对象。" />
      )}
      {!!objects.length && (
        <div className="objectCandidateList">
          {objects.map((object) => (
            <ObjectCandidateCard key={object.object_key} object={object} onOpenObject={onOpenObject} compact />
          ))}
        </div>
      )}
    </section>
  );
}
