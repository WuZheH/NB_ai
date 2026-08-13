import ReasonList from "./ReasonList.jsx";

export function ObjectSearchSummary({ object = {} }) {
  const topDocuments = object.top_documents || [];
  const summaries = object.match_summary || [];
  return (
    <div className="objectSearchSummary">
      {!!summaries.length && <ReasonList reasons={summaries.slice(0, 3)} />}
      {!!topDocuments.length && (
        <div className="topDocumentList">
          {topDocuments.map((document) => (
            <span key={document.document_id}>
              {document.title || `doc ${document.document_id}`} · {document.evidence_count} 条
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export default function ObjectCandidateCard({ object, onOpenObject, compact = false }) {
  const evidenceCount = object.evidence_count ?? object.evidence_refs?.length ?? 0;
  const openObject = () => onOpenObject?.(object.object_key);
  return (
    <article
      className={`objectCandidateCard clickableCard ${compact ? "compact" : ""}`}
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
      <div className="objectCardKicker">
        <span>{object.object_type_label || object.object_type || "对象"}</span>
        <span>{evidenceCount} 条证据</span>
      </div>
      <h3>{object.object_name}</h3>
      <ObjectCandidatePreviewContract object={object} />
      <FourLayerTagsInline object={object} />
      <ObjectSearchSummary object={object} />
    </article>
  );
}

function ObjectCandidatePreviewContract({ object = {} }) {
  const previewText = object.evidence_preview || object.selected_text || object.representative_evidence?.[0]?.snippet || "";
  const sourceNoteCount = sourceNoteIds(object).length;
  const badges = [
    object.source_origin && `source_origin: ${object.source_origin}`,
    object.necessity_judgment && `necessity_judgment: ${object.necessity_judgment}`,
    object.importance_score && `importance_score: ${object.importance_score}`,
    sourceNoteCount > 0 && `source note marker: ${sourceNoteCount}`,
  ].filter(Boolean);
  if (!previewText && !badges.length) return null;
  return (
    <div className="objectCandidateSmallPreview objectCandidateDetailPreview">
      {!!badges.length && (
        <div className="objectProcessingBadges">
          {badges.map((badge) => <span key={badge}>{badge}</span>)}
        </div>
      )}
      {previewText && <p>{previewText}</p>}
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

// Inline FourLayerTags to avoid circular dependency
function FourLayerTagsInline({ object = {} }) {
  return (
    <div className="fourLayerTags">
      <TagBucketInline label="Topic" tags={object.topic_tags} />
      <TagBucketInline label="Problem" tags={object.problem_tags} />
      <TagBucketInline label="Mechanism" tags={object.mechanism_tags} />
      <TagBucketInline label="Inspiration" tags={object.inspiration_tags} />
    </div>
  );
}

function TagBucketInline({ label, tags = [] }) {
  if (!tags?.length) return null;
  return (
    <div className="tagBucket">
      <span>{label}</span>
      <div className="tagList">
        {tags.slice(0, 3).map((tag) => (
          <span key={tag}>{tag}</span>
        ))}
      </div>
    </div>
  );
}
