import { useState } from "react";
import { API_BASE_URL } from "../api/client.js";
import PdfLocationPreview, { buildDocumentPdfPath } from "../PdfLocationPreview.jsx";
import StateMessage from "../components/StateMessage.jsx";
import TagList from "../components/TagList.jsx";
import PdfPageHint from "../components/PdfPageHint.jsx";
import RelationSection from "../components/RelationSection.jsx";
import PdfActionGroup from "../components/PdfActionGroup.jsx";
import LocatorSummary from "../components/LocatorSummary.jsx";
import { enhanceSourceWithZoteroCandidate } from "../utils/formatters.js";
import { buildDocumentNoteFirstGate as buildNoteFirstGate } from "../utils/noteFirstWorkflow.js";
import {
  NOTE_FILTERS,
  NOTE_TYPE_LABELS,
  hasUserNoteText,
  isEvidenceOnlyNote,
  jsonListValue,
  noteKey,
  noteMatchedChunkIds,
  noteProcessingSummary,
  noteRoleLabel,
  noteSort,
  noteSourceLabel,
  noteTypeCounts,
  noteTypeTags,
  notesSourceSummary,
  primaryNoteType,
} from "../features/library/utils/documentDetail.js";
import BookDetailPage from "./BookDetailPage.jsx";

export default function DocumentDetailPage({
  state,
  locatorState,
  zoteroCandidateState,
  onBack,
  onOpenWorkspace,
  advancedWorkflowRoute,
  onOpenEvidence,
  onOpenObject,
  onLocateEvidence,
  onSelectNote
}) {
  const [activePreviewEvidence, setActivePreviewEvidence] = useState(null);

  if (state.status === "loading") return <StateMessage title="正在加载文档" />;
  if (state.status === "error") return <StateMessage title="文档暂不可用" body={state.error} />;
  if (!state.data?.document) {
    return <StateMessage title="未选择文档" body="从已读书架选择文档查看详情。" />;
  }

  if (isBookLikeChapteredDocument(state.data.document, state.data.book_detail)) {
    return (
      <BookDetailPage
        state={state}
        onBack={onBack}
        onOpenWorkspace={onOpenWorkspace}
        initialChapterId={advancedWorkflowRoute?.chapterId}
        initialWorkflow={advancedWorkflowRoute?.workflow}
      />
    );
  }

  const {
    document,
    object_groups: objectGroups = [],
    evidence_preview: evidencePreview = [],
    notes_preview: notesPreview = [],
    inspiration_notes_preview: inspirationNotesPreview = [],
    inspiration_notes_count: inspirationNotesCount = inspirationNotesPreview.length,
    inspiration_notes_error: inspirationNotesError = "",
    linked_relations: linkedRelations = []
  } = state.data;
  const totalNotesPreviewCount = inspirationNotesPreview.length + notesPreview.length;
  const documentSource = enhanceSourceWithZoteroCandidate(
    document,
    zoteroCandidateState.byDocumentId[document.document_id]
  );
  const objectCount = objectGroups.reduce((count, group) => count + (group.objects || []).length, 0);
  const acceptedCount = objectGroups.reduce(
    (count, group) => count + (group.objects || []).filter((object) => object.review_status === "accepted").length,
    0
  );
  const candidateCount = objectGroups.reduce(
    (count, group) => count + (group.objects || []).filter((object) => object.status === "candidate").length,
    0
  );
  const activeLocator = activePreviewEvidence?.chunk_id
    ? locatorState?.byChunkId?.[activePreviewEvidence.chunk_id]
    : null;

  return (
    <section className="detailStack documentDetailPage">
      <button className="detailBack" type="button" onClick={onBack}>
        返回已读书架
      </button>

      <article className="documentHero">
        <div className="documentHeroMain">
          <div className="cardMeta">
            <span>{documentTypeLabel(document.document_type)}</span>
            <span className={document.read_status === "mastered" ? "statusMastered" : "statusRead"}>
              {readStatusLabel(document.read_status)}
            </span>
            <span>{objectCount} 个对象</span>
            <span>{document.evidence_count || 0} 条证据</span>
            {acceptedCount > 0 && <span>{acceptedCount} 个已审核</span>}
            {candidateCount > 0 && <span>{candidateCount} 个候选</span>}
          </div>
          <h3>{document.title}</h3>
          <p>{document.summary || "暂无个人总结"}</p>
          <TagList tags={document.tags} />
        </div>
        <div className="documentHeroActions">
          <PdfActionGroup source={documentSource} />
          <PdfPageHint source={documentSource} />
        </div>
      </article>

      <UnitProcessingPanel
        document={document}
        evidencePreview={evidencePreview}
        inspirationNotes={inspirationNotesPreview}
        objectGroups={objectGroups}
      />

      <section className="documentObjectOverview">
        <div className="sectionHeader">
          <h3>对象总览</h3>
          <span>{objectCount ? `${objectGroups.length} 类 / ${objectCount} 个对象` : "暂无结构化对象"}</span>
        </div>
        {objectCount ? (
          <div className="documentObjectGroupList">
            {objectGroups.map((group) => (
              <DocumentObjectGroup
                key={group.object_type}
                group={group}
                document={document}
                zoteroCandidateState={zoteroCandidateState}
                locatorState={locatorState}
                onOpenObject={onOpenObject}
                onOpenEvidence={onOpenEvidence}
                onLocateEvidence={(evidence) => {
                  setActivePreviewEvidence(evidence);
                  onLocateEvidence?.(evidence.chunk_id, evidence);
                }}
              />
            ))}
          </div>
        ) : (
          <div className="documentEmptyObjects">
            <StateMessage title="当前文档暂无结构化对象" body="可先查看全部证据；完成对象审核或映射后会在这里按类型展示。" />
          </div>
        )}
      </section>

      {activePreviewEvidence?.is_locatable && (
        <section className="documentPreviewSlot">
          <div className="sectionHeader">
            <h3>证据定位预览</h3>
            <span>{activePreviewEvidence.heading_path || activePreviewEvidence.section_title || "证据片段"}</span>
          </div>
          <LocatorSummary state={activeLocator} />
          <PdfLocationPreview
            apiBase={API_BASE_URL}
            documentId={activePreviewEvidence.document_id || document.document_id}
            location={activeLocator?.payload?.location}
            pdfUrl={buildDocumentPdfPath(activePreviewEvidence.document_id || document.document_id)}
            page={activeLocator?.payload?.location?.pdf_page ?? activePreviewEvidence.pdf_page_start ?? activePreviewEvidence.pdf_page}
            pdf_page_start={activePreviewEvidence.pdf_page_start ?? activePreviewEvidence.pdf_page}
            chunkId={activePreviewEvidence.chunk_id}
            highlightText={activePreviewEvidence.chunk_text || activePreviewEvidence.snippet}
          />
        </section>
      )}

      <details className="documentRawEvidence">
        <summary>全部证据 / 原始片段 ({evidencePreview.length})</summary>
        <div className="resultList rawEvidencePreviewList">
          {evidencePreview.length === 0 && (
            <StateMessage title="暂无证据预览" body="该文档暂无短证据预览。" />
          )}
          {evidencePreview.map((item) => (
            <article key={item.chunk_id} className="rawEvidencePreviewItem">
              <div className="chunkSectionPath">
                <span>位置</span>
                <strong>{item.section_title || item.heading_path || "章节暂不可用"}</strong>
              </div>
              <p className="summaryClamp">{item.snippet || item.locator_reason || "暂无片段文本。"}</p>
              <LocatorReason item={item} />
              <button type="button" className="quietButton" onClick={() => onOpenEvidence(item.chunk_id, item.source_trace)}>
                查看详情
              </button>
            </article>
          ))}
        </div>
      </details>

      <NotesPreviewModule
        inspirationNotes={inspirationNotesPreview}
        personalNotes={notesPreview}
        inspirationCount={inspirationNotesCount}
        error={inspirationNotesError}
        totalCount={totalNotesPreviewCount}
        onOpenEvidence={onOpenEvidence}
        onSelectNote={onSelectNote}
      />
      <RelationSection relations={linkedRelations} emptyBody="该文档暂无关联关系。" />
    </section>
  );
}

function NotesPreviewModule({
  inspirationNotes,
  personalNotes,
  inspirationCount,
  error,
  totalCount,
  onOpenEvidence,
  onSelectNote
}) {
  const [activeFilter, setActiveFilter] = useState("all");
  const [expandedNoteKey, setExpandedNoteKey] = useState("");
  const [selectedNoteKey, setSelectedNoteKey] = useState("");
  const sortedNotes = [...(inspirationNotes || [])].sort(noteSort);
  const counts = noteTypeCounts(sortedNotes);
  const visibleNotes = activeFilter === "all"
    ? sortedNotes
    : sortedNotes.filter((note) => noteTypeTags(note).includes(activeFilter));
  const total = totalCount || sortedNotes.length + (personalNotes || []).length;
  const sourceSummary = notesSourceSummary(sortedNotes, personalNotes || []);
  const summary = noteProcessingSummary(sortedNotes);

  function selectNote(note) {
    const key = noteKey(note);
    setSelectedNoteKey(key);
    const chunkIds = noteMatchedChunkIds(note);
    onSelectNote?.(note, {
      primaryChunkId: note.matched_chunk_id || chunkIds[0],
      chunkIds,
      noteType: primaryNoteType(note),
    });
  }

  return (
    <section className="notesPreviewModule" aria-label="笔记预览">
      <div className="sectionHeader">
        <h3>笔记预览</h3>
        <span>Zotero annotations: {summary.annotationCount || inspirationCount || 0} · 用户笔记: {summary.userNoteCount} · 仅高亮证据: {summary.evidenceOnlyCount} · 来源：{sourceSummary}</span>
      </div>
      {summary.evidenceOnlyCount > 0 && (
        <p className="unitEvidenceNotice">有 {summary.evidenceOnlyCount} 条 Zotero 高亮没有笔记内容，只会作为证据上下文，不进入笔记纠错/分类审核。</p>
      )}
      {error && (
        <StateMessage title="Zotero 笔记暂不可用" body={error} />
      )}
      {total === 0 ? (
        <StateMessage title="暂无关联笔记" body="有关联笔记时会显示在这里。" />
      ) : (
        <>
          <div className="noteTypeFilterChips" role="tablist" aria-label="按笔记类型筛选">
            {NOTE_FILTERS.map((filter) => (
              <button
                key={filter.value}
                type="button"
                className={activeFilter === filter.value ? "active" : ""}
                onClick={() => setActiveFilter(filter.value)}
              >
                <span>{filter.label}</span>
                <strong>{filter.value === "all" ? sortedNotes.length : counts[filter.value] || 0}</strong>
              </button>
            ))}
          </div>
          <div className="compactNoteList">
            {visibleNotes.length === 0 && (
              <StateMessage title="该类型暂无笔记" body="切换到全部可查看其他笔记。" />
            )}
            {visibleNotes.map((note) => {
              const key = noteKey(note);
              return (
                <CompactInspirationNoteItem
                  key={key}
                  note={note}
                  selected={selectedNoteKey === key}
                  expanded={expandedNoteKey === key}
                  onSelect={() => selectNote(note)}
                  onToggleExpand={(event) => {
                    event.stopPropagation();
                    setExpandedNoteKey(expandedNoteKey === key ? "" : key);
                  }}
                  onOpenEvidence={onOpenEvidence}
                />
              );
            })}
            {(personalNotes || []).length > 0 && (
              <div className="personalNotesCompact">
                <div className="cardMeta">
                  <span>PersonalNote</span>
                  <span>{personalNotes.length} 条</span>
                </div>
                {personalNotes.map((note) => (
                  <article key={note.note_id} className="compactPersonalNote">
                    <strong>{note.title}</strong>
                    <p>{note.summary || note.snippet}</p>
                  </article>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </section>
  );
}

function CompactInspirationNoteItem({
  note,
  selected,
  expanded,
  onSelect,
  onToggleExpand,
  onOpenEvidence
}) {
  const tags = Array.isArray(note.user_tags) ? note.user_tags.filter(Boolean) : [];
  const typeTags = noteTypeTags(note);
  const typeTag = typeTags[0] || "zotero_inspiration_note";
  const selectedText = note.selected_text || "";
  const chunkIds = noteMatchedChunkIds(note);
  const primaryChunkId = note.matched_chunk_id || chunkIds[0];
  const sourceLabel = noteSourceLabel(note);
  const roleLabel = noteRoleLabel(note);
  const locationLabel = note.page_label || (note.pdf_page ? `p. ${note.pdf_page}` : "页码未知");
  const evidenceStatus = compactSourceLine([
    note.evidence_alignment_status,
    note.alignment_confidence
  ]);
  return (
    <article
      className={`compactInspirationNote ${selected ? "selected" : ""} ${expanded ? "expanded" : ""} noteType-${typeTag}`}
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect?.();
        }
      }}
    >
      <div className="compactNoteTopline">
        <span>{locationLabel}</span>
        <strong>{NOTE_TYPE_LABELS[typeTag] || typeTag}</strong>
        <span>{roleLabel}</span>
        <span>{sourceLabel}</span>
        {chunkIds.length > 0 && <code>chunks: {chunkIds.join(",")}</code>}
      </div>
      <div className="compactNoteBody">
        <div className="compactNoteTextBlock">
          <p className="compactNoteText">{note.note_text || (isEvidenceOnlyNote(note) ? "仅高亮证据，无笔记内容。" : "暂无笔记正文。")}</p>
          {selectedText && <p className="compactSelectedText">{selectedText}</p>}
        </div>
        <div className="noteStatusChips">
          {evidenceStatus && <span>{evidenceStatus}</span>}
          {note.mechanism_status && <span>{note.mechanism_status}</span>}
          <span>{note.zotero_attachment_key ? `Zotero anchor: ${note.zotero_attachment_key}` : "无 Zotero anchor，可通过 chunk 预览"}</span>
        </div>
      </div>
      <div className="compactNoteActions" onClick={(event) => event.stopPropagation()}>
        {primaryChunkId && (
          <button
            type="button"
            className="quietButton"
            onClick={() => onOpenEvidence?.(primaryChunkId, {
              selection_type: "zotero_inspiration_note",
              source: note.source,
              server_note_id: note.server_note_id,
              matched_document_id: note.matched_document_id,
              matched_chunk_ids: chunkIds,
              chunk_id: primaryChunkId,
              pdf_page: note.pdf_page,
            })}
          >
            查看 chunk
          </button>
        )}
        <button type="button" className="quietButton" onClick={onToggleExpand}>
          {expanded ? "收起" : "展开"}
        </button>
      </div>
      {expanded && (
        <div className="noteExpandedDetail" onClick={(event) => event.stopPropagation()}>
          <DetailRow label="note_text" value={note.note_text} />
          <DetailRow label="selected_text" value={selectedText} long />
          <DetailRow label="user_tags" value={tags.join(", ")} />
          <DetailRow label="matched_chunk_ids_json" value={JSON.stringify(chunkIds)} />
          <DetailRow label="matched_object_ids_json" value={JSON.stringify(jsonListValue(note.matched_object_ids_json || note.matched_object_ids))} />
          <DetailRow label="evidence_alignment_status" value={note.evidence_alignment_status} />
          <DetailRow label="alignment_confidence" value={note.alignment_confidence} />
          <DetailRow label="alignment_method" value={note.alignment_method} />
          <DetailRow label="alignment_warnings_json" value={JSON.stringify(jsonListValue(note.alignment_warnings_json))} />
          <DetailRow label="note_processing_role" value={note.note_processing_role} />
          <DetailRow label="is_evidence_only" value={String(Boolean(note.is_evidence_only))} />
          <DetailRow label="mechanism_status" value={note.mechanism_status} />
          <DetailRow label="source" value={note.source} />
          <DetailRow label="zotero_attachment_key" value={note.zotero_attachment_key || "空：无 Zotero anchor，可通过 chunk 预览"} />
          {tags.length > typeTags.length && (
            <TagList tags={tags.filter((tag) => !NOTE_TYPE_TAGS.has(tag)).slice(0, 12)} />
          )}
        </div>
      )}
    </article>
  );
}

function DetailRow({ label, value, long = false }) {
  return (
    <div className={`noteDetailRow ${long ? "long" : ""}`}>
      <span>{label}</span>
      <code>{value || "—"}</code>
    </div>
  );
}

function DocumentObjectGroup({
  group,
  document,
  zoteroCandidateState,
  locatorState,
  onOpenObject,
  onOpenEvidence,
  onLocateEvidence
}) {
  const objects = group.objects || [];
  return (
    <section className="documentObjectGroup">
      <div className="sectionHeader">
        <h3>{group.label || group.object_type || "其他对象"} · {objects.length} 个</h3>
      </div>
      <div className="documentObjectGrid">
        {objects.map((object) => (
          <DocumentObjectCard
            key={object.object_key}
            object={object}
            document={document}
            zoteroCandidateState={zoteroCandidateState}
            locatorState={locatorState}
            onOpenObject={onOpenObject}
            onOpenEvidence={onOpenEvidence}
            onLocateEvidence={onLocateEvidence}
          />
        ))}
      </div>
    </section>
  );
}

function DocumentObjectCard({
  object,
  document,
  zoteroCandidateState,
  locatorState,
  onOpenObject,
  onOpenEvidence,
  onLocateEvidence
}) {
  const evidence = object.representative_evidence || [];
  const tags = objectTags(object);
  const objectName = object.object_name || "未命名对象";
  const sourceLine = compactSourceLine([
    document.title,
    object.heading_path || object.section_title || evidence[0]?.heading_path || evidence[0]?.section_title
  ]);
  const openObject = () => onOpenObject?.(object.object_key);
  return (
    <article
      className="documentObjectCard clickableCard"
      role="button"
      tabIndex={0}
      onClick={openObject}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          openObject();
        }
      }}
    >
      <div className="documentObjectCardHeader">
        <div>
          <div className="objectCardKicker">
            <span>{objectTypeLabel(object.object_type_label || object.object_type)}</span>
            <span>{object.evidence_count || 0} 条证据</span>
          </div>
          <h4 className="objectCardTitle">{objectName}</h4>
        </div>
      </div>
      <ObjectProcessingBadges object={object} />
      {sourceLine && <p className="objectCardSource">{sourceLine}</p>}
      {object.description && <p className="documentObjectDescription">{object.description}</p>}
      {!!tags.length && (
        <div className="searchObjectTags">
          {tags.map((tag) => (
            <span key={`${object.object_key}-${tag}`}>{tag}</span>
          ))}
        </div>
      )}
      <div className="representativeEvidenceList" onClick={(event) => event.stopPropagation()}>
        {evidence.length ? (
          evidence.slice(0, 1).map((rawEvidence) => {
            const item = enhanceSourceWithZoteroCandidate(
              { ...rawEvidence, document_id: rawEvidence.document_id || document.document_id },
              zoteroCandidateState?.byDocumentId?.[rawEvidence.document_id || document.document_id]
            );
            const locatorStateForItem = locatorState?.byChunkId?.[item.chunk_id];
            return (
              <article key={item.chunk_id} className="representativeEvidenceCard">
                <p>{item.snippet || item.chunk_text || item.locator_reason || "暂无代表性支撑片段。"}</p>
                <div className="passageMetaLine">
                  <span className="locationPath">{item.heading_path || item.section_title || "章节暂不可用"}</span>
                </div>
                <LocatorReason item={item} />
                <div className="mappedChunkActions">
                  <button type="button" className="quietButton" onClick={() => onOpenEvidence?.(item.chunk_id, item)}>
                    查看详情
                  </button>
                  {item.is_locatable ? (
                    <PdfActionGroup
                      source={{ ...item, locator_result: locatorStateForItem?.payload?.location }}
                      showPreview
                      compact
                      onPreview={() => onLocateEvidence?.(item)}
                    />
                  ) : (
                    <PdfActionGroup source={item} compact showUnavailable={false} />
                  )}
                </div>
              </article>
            );
          })
        ) : (
          null
        )}
      </div>
    </article>
  );
}

function UnitProcessingPanel({ document, evidencePreview, inspirationNotes, objectGroups }) {
  const units = inferPaperUnits(evidencePreview);
  const objects = objectGroups.flatMap((group) => group.objects || []);
  const unitKindLabel = document.document_type === "paper" && document.object_import_mode === "chaptered"
    ? "paper_section_from_chaptered_import"
    : document.document_type === "paper" ? "paper_section" : "document_unit";
  return (
    <section className="unitProcessingPanel" aria-label="按章/节处理笔记与对象">
      <div className="sectionHeader">
        <h3>按章/节处理笔记与对象</h3>
        <span>{unitKindLabel}</span>
      </div>
      {units.warning && (
        <p className="unitProcessingWarning">无法检测一级 section，当前回退为 whole_paper_unit。</p>
      )}
      <div className="unitProcessingList">
        {units.items.map((unit) => {
          const unitNotes = inspirationNotes.filter((note) => noteBelongsToUnit(note, unit));
          const unitNoteSummary = noteProcessingSummary(unitNotes);
          const unitObjects = objects.filter((object) => objectBelongsToUnit(object, unit));
          const gate = buildNoteFirstGate(unitNoteSummary, unitObjects, "本节");
          return (
            <article key={unit.unit_id} className="unitProcessingCard">
              <div className="unitProcessingMain">
                <span className="unitTypeBadge">{unit.unit_type}</span>
                <h4>{unit.title}</h4>
                <p>{unit.pageLabel} · {unit.chunkIds.length} chunks · 双源流程：Zotero 笔记与原文片段进入对象审核，最后机制审核</p>
              </div>
              <div className="unitProcessingMetrics">
                <MetricMini label="Zotero annotations" value={gate.annotationCount} />
                <MetricMini label="用户笔记" value={gate.userNoteCount} />
                <MetricMini label="仅高亮证据" value={gate.evidenceOnlyCount} />
                <MetricMini label="已同步到 Search" value={gate.syncedNoteCount} />
                <MetricMini label="note correction gate" value={gate.canCorrectNotes ? "ready" : "blocked"} />
                <MetricMini label="object candidate gate" value={gate.canGenerateObjects ? "ready" : "blocked"} />
                <MetricMini label="object candidates count" value={unitObjects.length} />
                <MetricMini label="reviewed object count" value={gate.reviewedObjectCount} />
                <MetricMini label="mechanism readiness" value={gate.reviewedObjectCount ? "ready gate" : "blocked"} />
              </div>
              {gate.evidenceOnlyCount > 0 && (
                <p className="unitEvidenceNotice">有 {gate.evidenceOnlyCount} 条 Zotero 高亮没有笔记内容；不进入笔记纠错/分类审核，但可作为 source-led 原文片段来源。</p>
              )}
              <div className="unitProcessingActions">
                <DisabledAction label="1 同步本节 Zotero 笔记" reason={gate.syncReason} />
                <DisabledAction label="2 生成本节笔记纠错包" reason={gate.noteCorrectionReason} />
                <DisabledAction label="3 笔记纠错审核" reason={gate.noteCorrectionReviewReason} />
                <DisabledAction label="4 生成本节笔记分类包" reason={gate.noteClassificationReason} />
                <DisabledAction label="5 笔记分类审核" reason={gate.noteClassificationReviewReason} />
                <DisabledAction label="6 生成对象候选：笔记 / 高光 / 全文章节" reason={gate.objectCandidateReason} />
                <DisabledAction label="7 对象审核" reason={gate.objectReviewReason} />
                <DisabledAction label="8 生成双源机制候选包" reason={gate.mechanismCandidateReason} />
                <DisabledAction label="9 机制审核" reason={gate.mechanismReviewReason} />
              </div>
              <ReviewGateSummary />
              <p className="mechanismGateNotice">mechanism_blocked_until_objects_reviewed：对象审核完成后可生成双源机制候选包。</p>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function ReviewGateSummary() {
  return (
    <div className="reviewGateSummary" aria-label="双源到机制流程">
      <span>1 同步本节 Zotero 笔记</span>
      <span>2 生成本节笔记纠错包</span>
      <span>3 note_correction_review：笔记纠错审核</span>
      <span>4 生成本节笔记分类包</span>
      <span>5 note_classification_review：笔记分类审核</span>
      <span>6 生成三路对象候选包：笔记 / 高光 / 全文章节</span>
      <span>7 object_review：对象审核</span>
      <span>8 生成双源机制候选包</span>
      <span>9 mechanism_review：机制审核</span>
      <small>mechanism_review layers：evidence_review / abstraction_review / classification_review / relationship_review / search_entry_review</small>
    </div>
  );
}

function DisabledAction({ label, reason }) {
  return (
    <span className="disabledActionWithReason">
      <button type="button" disabled title={reason}>{label}</button>
      <small>{reason}</small>
    </span>
  );
}

function MetricMini({ label, value }) {
  return (
    <span className="unitMetricMini">
      <em>{label}</em>
      <strong>{value}</strong>
    </span>
  );
}

function ObjectProcessingBadges({ object }) {
  const sourceNoteCount = sourceNoteIds(object).length;
  const badges = [
    object.source_origin && `source_origin: ${object.source_origin}`,
    object.necessity_judgment && `necessity_judgment: ${object.necessity_judgment}`,
    object.importance_score && `importance_score: ${object.importance_score}`,
    sourceNoteCount > 0 && `source note marker: ${sourceNoteCount}`,
  ].filter(Boolean);
  if (!badges.length) return null;
  return (
    <div className="objectProcessingBadges objectCardSmallEvidencePreview">
      {badges.map((badge) => <span key={badge}>{badge}</span>)}
    </div>
  );
}

function sourceNoteIds(object = {}) {
  if (Array.isArray(object.source_note_ids)) return object.source_note_ids.filter(Boolean);
  if (Array.isArray(object.source_note_ids_json)) return object.source_note_ids_json.filter(Boolean);
  if (typeof object.source_note_ids_json === "string") {
    try {
      const parsed = JSON.parse(object.source_note_ids_json);
      return Array.isArray(parsed) ? parsed.filter(Boolean) : [];
    } catch {
      return [];
    }
  }
  return [];
}

function inferPaperUnits(evidencePreview) {
  const sections = new Map();
  (evidencePreview || []).forEach((item) => {
    const title = firstLevelSection(item.section_title || item.heading_path);
    if (!title) return;
    const existing = sections.get(title) || { title, chunkIds: [], pages: [] };
    if (item.chunk_id) existing.chunkIds.push(item.chunk_id);
    [item.pdf_page_start, item.pdf_page_end, item.pdf_page].forEach((page) => {
      if (page != null) existing.pages.push(Number(page));
    });
    sections.set(title, existing);
  });
  if (!sections.size) {
    return {
      warning: true,
      items: [{
        unit_type: "whole_paper_unit",
        unit_id: "whole_paper",
        title: "Whole paper",
        chunkIds: (evidencePreview || []).map((item) => item.chunk_id).filter(Boolean),
        pageLabel: "whole_paper_unit warning",
      }],
    };
  }
  return {
    warning: false,
    items: Array.from(sections.values()).map((section) => ({
      unit_type: "paper_section",
      unit_id: section.title,
      title: section.title,
      chunkIds: section.chunkIds,
      pageLabel: section.pages.length ? `p.${Math.min(...section.pages)}-${Math.max(...section.pages)}` : "页码暂不可用",
    })),
  };
}

function firstLevelSection(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  const separators = [" / ", ">", "|"];
  const separator = separators.find((item) => text.includes(item));
  return separator ? text.split(separator)[0].trim() : text;
}

function isBookLikeChapteredDocument(document = {}, bookDetail = null) {
  const isChaptered = document?.object_import_mode === "chaptered" || bookDetail?.object_import_mode === "chaptered";
  return isChaptered && document?.document_type !== "paper";
}

function noteBelongsToUnit(note, unit) {
  const chunkIds = noteMatchedChunkIds(note);
  if (chunkIds.some((chunkId) => unit.chunkIds.includes(chunkId))) return true;
  return firstLevelSection(note.section_title || note.heading_path) === unit.title;
}

function objectBelongsToUnit(object, unit) {
  if (firstLevelSection(object.section_title || object.heading_path) === unit.title) return true;
  const evidence = object.representative_evidence || [];
  return evidence.some((item) => firstLevelSection(item.section_title || item.heading_path) === unit.title || unit.chunkIds.includes(item.chunk_id));
}

function LocatorReason({ item }) {
  if (item.is_locatable) return null;
  const reason = item.locator_reason || "该片段暂不支持 PDF 定位。";
  return <p className="locatorReasonInline">{reason}</p>;
}

function objectTags(object) {
  return [
    ...(object.topic_tags || []),
    ...(object.problem_tags || []),
    ...(object.mechanism_tags || []),
    ...(object.inspiration_tags || [])
  ].filter(Boolean).slice(0, 4);
}

function compactSourceLine(parts) {
  const cleaned = parts.filter(Boolean).map((part) => String(part).trim()).filter(Boolean);
  const title = cleaned[0];
  return cleaned
    .map((part, index) => (index > 0 && title && part.startsWith(`${title} / `) ? part.slice(title.length + 3) : part))
    .filter((part, index, values) => part !== values[index - 1])
    .join(" / ");
}

function objectTypeLabel(value) {
  const labels = {
    method: "方法",
    dataset: "数据集",
    metric: "指标",
    problem: "问题",
    mechanism: "机制",
    task: "任务",
    concept: "概念",
    contribution: "贡献",
    limitation: "限制",
    inspiration: "灵感",
    experiment: "实验设置",
    experiment_setting: "实验设置",
    loss: "损失函数",
    other: "其他"
  };
  return labels[String(value || "").toLowerCase()] || value || "对象";
}

function documentTypeLabel(value) {
  if (value === "paper") return "论文";
  if (value === "book") return "书籍";
  if (value === "thesis") return "学位论文";
  if (value === "report") return "报告";
  if (value === "other") return "其他";
  return value || "文档";
}

function readStatusLabel(value) {
  if (value === "read") return "已读";
  if (value === "mastered") return "已掌握";
  return value || "已读资料";
}
