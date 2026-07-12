import StateMessage from "../components/StateMessage.jsx";

export default function ReviewObjectCard({ item = {}, index, sourceTraceSections, onToggleStatus, onEditTag, onRemoveTag, onAddTag, onSetComment, onEditEvidenceField, onRemoveEvidenceRef, onAddEvidenceRef, onSelectSection }) {
  const reviewStatus = item.reviewStatus || "suggested";
  const statusColors = { accepted: "#2e7d32", edited: "#1565c0", rejected: "#c62828", suggested: "#6d6d6d" };
  return (
    <article className="reviewObjectCard">
      <div className="reviewCardTop">
        <div className="reviewCardHeader">
          <div className="cardMeta">
            <span>{item.object_type || "object"}</span>
            <span>priority {item.priority ?? item.confidence ?? "n/a"}</span>
            <span className="reviewStatusBadge" style={{ color: statusColors[reviewStatus] || "#6d6d6d" }}>
              {reviewStatus}
            </span>
            {(item.warnings || []).length > 0 && <span className="warningPill">⚠ {(item.warnings || []).length}</span>}
          </div>
          <h3>{item.object_name}</h3>
          {item.object_key && <code className="objectKeyLabel">{item.object_key}</code>}
        </div>
        <div className="reviewStatusRow" aria-label="审核决定">
          {["suggested", "accepted", "edited", "rejected"].map(st => (
            <button
              key={st}
              type="button"
              className={`statusBtn ${reviewStatus === st ? "active" : ""}`}
              style={reviewStatus === st ? { borderColor: statusColors[st], color: statusColors[st] } : {}}
              onClick={() => onToggleStatus(index, st)}
            >
              {st}
            </button>
          ))}
        </div>
      </div>

      {item.description && <p className="reviewDescription">{item.description}</p>}
      {(item.aliases || []).length > 0 && (
        <div className="reviewAliasList">
          {item.aliases.map((a, i) => <span key={i}>{a}</span>)}
        </div>
      )}

      <section className="reviewTagsSection" aria-label="标签">
        {["topic_tags", "problem_tags", "mechanism_tags", "inspiration_tags"].map(layer => {
          const tags = item.editedTags?.[layer] || [];
          const layerLabels = { topic_tags: "Topic", problem_tags: "Problem", mechanism_tags: "Mechanism", inspiration_tags: "Inspiration" };
          if (layer === "inspiration_tags" && !tags.length) return null;
          return (
            <div key={layer} className="reviewTagBucket">
              <strong>{layerLabels[layer]}</strong>
              <div className="editableTagList">
                {tags.map((tag, ti) => (
                  <span key={ti} className="editableTag">
                    <input
                      value={tag}
                      onChange={e => onEditTag(index, layer, ti, e.target.value)}
                      aria-label={`${layerLabels[layer]} tag ${ti + 1}`}
                    />
                    <button type="button" className="tagRemoveBtn" onClick={() => onRemoveTag(index, layer, ti)} title="删除标签">×</button>
                  </span>
                ))}
                <button type="button" className="tagAddBtn" onClick={() => onAddTag(index, layer)}>+</button>
              </div>
            </div>
          );
        })}
      </section>

      {/* Editable Evidence Refs */}
      <details className="reviewEvidenceDetails" open>
        <summary>证据引用（{(item.editedEvidenceRefs || item.evidence_refs || []).length} 条）— 编辑原文引用</summary>
        <div className="evidenceEditBanner">
          证据引用连接对象与原文段落。修改后保存仍只写入 staging，正式入库时系统会重新映射到 knowledge_chunks。
        </div>
        <div className="evidenceEditGrid">
          {(item.editedEvidenceRefs || item.evidence_refs || []).map((ref, ri) => (
            <div key={ri} className="evidenceEditRow">
              <div className="evidenceEditFields evidenceEditFieldsCompact">
                <label>页 <input type="number" min="1" value={ref.pdf_page || ""} onChange={e => onEditEvidenceField(index, ri, "pdf_page", e.target.value)} placeholder="页码" className="evidenceFieldSmall" /></label>
                <label>ID <input value={ref.section_id || ""} onChange={e => onEditEvidenceField(index, ri, "section_id", e.target.value)} placeholder="sec_id" className="evidenceFieldSmall" /></label>
              </div>
              <label>
                Section
                <select value={ref.section_id || ""} onChange={e => onSelectSection(index, ri, e.target.value)} className="evidenceFieldSelect">
                  <option value="">— 选 section —</option>
                  {(sourceTraceSections || []).map(sec => (
                    <option key={sec.section_id} value={sec.section_id}>{sec.section_id} · {sec.title} (p.{sec.pdf_page})</option>
                  ))}
                </select>
              </label>
              <label>标题 <input value={ref.section_title || ""} onChange={e => onEditEvidenceField(index, ri, "section_title", e.target.value)} placeholder="section_title" /></label>
              <label>引用原文 <input value={ref.quote_text_short || ""} onChange={e => onEditEvidenceField(index, ri, "quote_text_short", e.target.value)} placeholder="quote_text_short" /></label>
              <button type="button" className="tagRemoveBtn evidenceRemoveBtn" onClick={() => onRemoveEvidenceRef(index, ri)} title="删除此证据">×</button>
            </div>
          ))}
        </div>
        <button type="button" className="tagAddBtn" onClick={() => onAddEvidenceRef(index)}>+ 新增证据引用</button>
      </details>

      <div className="reviewCommentRow">
        <label>备注：</label>
        <input
          value={item.userComment || ""}
          onChange={e => onSetComment(index, e.target.value)}
          placeholder="审核备注（可选）"
        />
      </div>

      {(item.warnings || []).length > 0 && (
        <div className="reviewWarnings">
          ⚠ {item.warnings.map((w, wi) => <span key={wi}>{typeof w === "string" ? w : w.warning || w.message || JSON.stringify(w)}</span>)}
        </div>
      )}
    </article>
  );
}
