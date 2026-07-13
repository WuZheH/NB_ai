import ReviewObjectCard from "./ReviewObjectCard.jsx";

export default function ReviewObjectList({
  reviewItems,
  reviewSource,
  sourceTraceSections,
  handlers,
}) {
  if (!reviewItems.length) return null;
  return (
    <>
      <div className="importReviewMeta">
        <span>{reviewSource === "reviewed_objects" ? "来源：已审核结果（user_reviewed）" : "来源：ChatGPT AI 建议（ai_suggested）"}</span>
        <span>对象数：{reviewItems.length}</span>
        {reviewSource === "reviewed_objects" && (
          <span className="warningPill">⚠ 正在查看已审核结果，非 AI 原始建议</span>
        )}
      </div>

      <div className="reviewObjectList">
        <div className="sectionHeader">
          <h3>审核对象</h3>
          <span>{reviewItems.length} 个候选</span>
        </div>
        {reviewItems.map((item, index) => (
          <ReviewObjectCard
            key={item.object_key || index}
            item={item}
            index={index}
            sourceTraceSections={sourceTraceSections}
            onToggleStatus={handlers.toggleReviewStatus}
            onEditTag={handlers.editTag}
            onRemoveTag={handlers.removeTag}
            onAddTag={handlers.addTag}
            onSetComment={handlers.setUserComment}
            onEditEvidenceField={handlers.editEvidenceField}
            onRemoveEvidenceRef={handlers.removeEvidenceRef}
            onAddEvidenceRef={handlers.addEvidenceRef}
            onSelectSection={handlers.selectSectionForRef}
          />
        ))}
      </div>
    </>
  );
}
