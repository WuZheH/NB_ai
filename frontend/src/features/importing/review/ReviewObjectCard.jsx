export default function ReviewObjectCard({
  item = {},
  index,
  sourceTraceSections,
  onToggleStatus,
  onEditTag,
  onRemoveTag,
  onAddTag,
  onSetComment,
  onEditEvidenceField,
  onRemoveEvidenceRef,
  onAddEvidenceRef,
  onSelectSection,
}) {
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
          {["suggested", "accepted", "edited", "rejected"].map(status => (
            <button
              key={status}
              type="button"
              className={`statusBtn ${reviewStatus === status ? "active" : ""}`}
              style={reviewStatus === status ? { borderColor: statusColors[status], color: statusColors[status] } : {}}
              onClick={() => onToggleStatus(index, status)}
            >
              {status}
            </button>
          ))}
        </div>
      </div>

      {item.description && <p className="reviewDescription">{item.description}</p>}
      {(item.aliases || []).length > 0 && (
        <div className="reviewAliasList">
          {item.aliases.map((alias, aliasIndex) => <span key={aliasIndex}>{alias}</span>)}
        </div>
      )}

      <section className="reviewTagsSection" aria-label="标签">
        {["topic_tags", "problem_tags", "mechanism_tags", "inspiration_tags"].map(layer => {
          const tags = item.editedTags?.[layer] || [];
          const labels = { topic_tags: "Topic", problem_tags: "Problem", mechanism_tags: "Mechanism", inspiration_tags: "Inspiration" };
          if (layer === "inspiration_tags" && !tags.length) return null;
          return (
            <div key={layer} className="reviewTagBucket">
              <strong>{labels[layer]}</strong>
              <div className="editableTagList">
                {tags.map((tag, tagIndex) => (
                  <span key={tagIndex} className="editableTag">
                    <input
                      value={tag}
                      onChange={event => onEditTag(index, layer, tagIndex, event.target.value)}
                      aria-label={`${labels[layer]} tag ${tagIndex + 1}`}
                    />
                    <button type="button" className="tagRemoveBtn" onClick={() => onRemoveTag(index, layer, tagIndex)} title="删除标签">×</button>
                  </span>
                ))}
                <button type="button" className="tagAddBtn" onClick={() => onAddTag(index, layer)}>+</button>
              </div>
            </div>
          );
        })}
      </section>

      <details className="reviewEvidenceDetails" open>
        <summary>证据引用（{(item.editedEvidenceRefs || item.evidence_refs || []).length} 条）— 编辑原文引用</summary>
        <div className="evidenceEditBanner">
          证据引用连接对象与原文段落。修改后保存仍只写入 staging，正式入库时系统会重新映射到 knowledge_chunks。
        </div>
        <div className="evidenceEditGrid">
          {(item.editedEvidenceRefs || item.evidence_refs || []).map((ref, refIndex) => (
            <div key={refIndex} className="evidenceEditRow">
              <div className="evidenceEditFields evidenceEditFieldsCompact">
                <label>页 <input type="number" min="1" value={ref.pdf_page || ""} onChange={event => onEditEvidenceField(index, refIndex, "pdf_page", event.target.value)} placeholder="页码" className="evidenceFieldSmall" /></label>
                <label>ID <input value={ref.section_id || ""} onChange={event => onEditEvidenceField(index, refIndex, "section_id", event.target.value)} placeholder="sec_id" className="evidenceFieldSmall" /></label>
              </div>
              <label>
                Section
                <select value={ref.section_id || ""} onChange={event => onSelectSection(index, refIndex, event.target.value)} className="evidenceFieldSelect">
                  <option value="">— 选 section —</option>
                  {(sourceTraceSections || []).map(section => (
                    <option key={section.section_id} value={section.section_id}>{section.section_id} · {section.title} (p.{section.pdf_page})</option>
                  ))}
                </select>
              </label>
              <label>标题 <input value={ref.section_title || ""} onChange={event => onEditEvidenceField(index, refIndex, "section_title", event.target.value)} placeholder="section_title" /></label>
              <label>引用原文 <input value={ref.quote_text_short || ""} onChange={event => onEditEvidenceField(index, refIndex, "quote_text_short", event.target.value)} placeholder="quote_text_short" /></label>
              <button type="button" className="tagRemoveBtn evidenceRemoveBtn" onClick={() => onRemoveEvidenceRef(index, refIndex)} title="删除此证据">×</button>
            </div>
          ))}
        </div>
        <button type="button" className="tagAddBtn" onClick={() => onAddEvidenceRef(index)}>+ 新增证据引用</button>
      </details>

      <div className="reviewCommentRow">
        <label>备注：</label>
        <input
          value={item.userComment || ""}
          onChange={event => onSetComment(index, event.target.value)}
          placeholder="审核备注（可选）"
        />
      </div>

      {(item.warnings || []).length > 0 && (
        <div className="reviewWarnings">
          ⚠ {item.warnings.map((warning, warningIndex) => (
            <span key={warningIndex}>{typeof warning === "string" ? warning : warning.warning || warning.message || JSON.stringify(warning)}</span>
          ))}
        </div>
      )}
    </article>
  );
}
