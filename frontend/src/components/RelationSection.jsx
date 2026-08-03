import { relationEvidenceLabel, relationEntityFallback } from "../utils/formatters.js";

export function RelationCard({ relation = {} }) {
  const sourceLabel = relation.source_label || relationEntityFallback(relation.source_type, relation.source_id);
  const targetLabel = relation.target_label || relationEntityFallback(relation.target_type, relation.target_id);
  const relationLabel = relation.relation_label_zh || relation.relation_type || "未知关系";
  const evidenceText = relationEvidenceLabel(relation);
  const rawRelation =
    relation.raw_relation ||
    `${relationEntityFallback(relation.source_type, relation.source_id)} ${relation.relation_type || "unknown"} ${relationEntityFallback(
      relation.target_type,
      relation.target_id
    )}`;

  return (
    <article className="relationCard">
      <div className="relationFlow">
        <div>
          <span className="relationFieldLabel">主体</span>
          <strong>{sourceLabel}</strong>
        </div>
        <span className="relationPill">{relationLabel}</span>
        <div>
          <span className="relationFieldLabel">对象</span>
          <strong>{targetLabel}</strong>
        </div>
      </div>
      <div className="relationMeta">
        <span>证据：{evidenceText}</span>
        {relation.confidence !== null && relation.confidence !== undefined && (
          <span>置信度：{relation.confidence}</span>
        )}
      </div>
      {relation.description && <p className="relationDescription">{relation.description}</p>}
      <details className="relationRaw">
        <summary>原始关系</summary>
        <small>{rawRelation}</small>
      </details>
    </article>
  );
}

export default function RelationSection({ relations = [], emptyBody }) {
  return (
    <section>
      <div className="sectionHeader">
        <h3>关联关系</h3>
        <span>{relations.length} 条关系</span>
      </div>
      {relations.length === 0 ? (
        <div className="stateMessage">
          <h3>暂无关联关系</h3>
          {emptyBody && <p>{emptyBody}</p>}
        </div>
      ) : (
        <div className="relationList">
          {relations.map((relation, index) => (
            <RelationCard relation={relation} key={relation.relation_id || relation.raw_relation || index} />
          ))}
        </div>
      )}
    </section>
  );
}
