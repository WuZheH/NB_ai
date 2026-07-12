import { useState } from "react";
import { cleanSearchSnippet } from "../../utils/snippet.js";

const DEFAULT_VISIBLE_OBJECTS = 6;

export default function SearchRelatedObjects({ objects = [], semanticObjects, onOpenObject }) {
  const [expanded, setExpanded] = useState(false);
  const semStatus = semanticObjects?.status || "idle";
  const semResults = semanticObjects?.results || [];
  const useSemantic = semStatus === "ready" && semResults.length > 0;
  const displayObjects = useSemantic ? semResults : objects;
  const semDegraded = semStatus === "error" && objects.length > 0;
  const visibleObjects = expanded ? displayObjects : displayObjects.slice(0, DEFAULT_VISIBLE_OBJECTS);
  const hiddenCount = Math.max(0, displayObjects.length - DEFAULT_VISIBLE_OBJECTS);
  const relevanceStats = objectRelevanceStats(displayObjects);
  const hasRelevantObjects = relevanceStats.high_count + relevanceStats.medium_count > 0;
  const sectionLabel = displayObjects.length > 0 && !hasRelevantObjects ? "语义邻近对象" : "相关对象";
  const countLabel = displayObjects.length
    ? `显示 ${Math.min(visibleObjects.length, displayObjects.length)} / ${displayObjects.length} 个${sectionLabel}`
    : "暂无对象";
  return (
    <section className="searchSection objectCandidateBand">
      <div className="sectionHeader">
        <h3>{sectionLabel}</h3>
        <span>{countLabel}</span>
      </div>
      {displayObjects.length > 0 && !hasRelevantObjects && (
        <p className="semanticNeighborNote">以下对象按语义距离排序，相关性较低，仅供参考。</p>
      )}
      {semDegraded && objects.length > 0 && (
        <p className="emptyInline">语义对象检索暂不可用，已使用基础对象匹配。</p>
      )}
      {displayObjects.length ? (
        <>
          <div className="searchObjectList">
            {visibleObjects.map((object) => (
              <SearchObjectCard
                key={object.object_key || object.canonical_name}
                object={object}
                isSemantic={useSemantic}
                onOpenObject={onOpenObject}
              />
            ))}
          </div>
          {hiddenCount > 0 && (
            <button type="button" className="objectExpandButton" onClick={() => setExpanded((value) => !value)}>
              {expanded ? `收起${sectionLabel}` : `展开更多对象（还有 ${hiddenCount} 个）`}
            </button>
          )}
        </>
      ) : (
        <p className="emptyInline">当前查询没有对象结果。</p>
      )}
    </section>
  );
}

function SearchObjectCard({ object, isSemantic, onOpenObject }) {
  const objectKey = object.object_key || object.canonical_name;
  const evidenceCount = object.evidence_count ?? object.evidence_refs?.length ?? 0;
  const tags = searchObjectTags(object);
  const docTitle = object.document_title || "";
  const objectName = object.object_name || object.canonical_name || "未命名对象";
  const relevanceLabel = object.display_relevance_label || object.relevance_label;
  const openObject = () => onOpenObject?.(objectKey);
  return (
    <article
      className="searchObjectCard clickableCard"
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
      <div className="searchObjectMain">
        <h4>{objectName}</h4>
        <div className="searchObjectMeta">
          <span>{object.object_type_label || object.object_type || "object"}</span>
          <span className="semanticScore">{objectScoreLabel(object)}</span>
          {relevanceLabel && <span>{relevanceLabel}相关</span>}
          {object.boosted_exact_match && <span className="exactMatchBadge">名称命中</span>}
        </div>
      </div>
      {!!tags.length && (
        <div className="searchObjectTags" aria-label="核心标签">
          {tags.map((tag) => (
            <span key={`${tag.layer}-${tag.value}`} title={tag.layer}>
              {tag.value}
            </span>
          ))}
        </div>
      )}
      {docTitle && (
        <p className="searchObjectDocTitle">{docTitle}</p>
      )}
      {isSemantic && (object.representative_evidence || []).length > 0 && (
        <div className="searchObjectEvidence">
          {(object.representative_evidence || []).slice(0, 1).map((evidence) => (
            <p key={evidence.chunk_id}>{cleanSearchSnippet(evidence.snippet) || "暂无代表性支撑片段。"}</p>
          ))}
        </div>
      )}
      <div className="searchObjectFooter">
        <span>{evidenceCount} 条证据</span>
      </div>
    </article>
  );
}

function objectScoreLabel(object = {}) {
  const distance = Number(object.raw_distance);
  if (Number.isFinite(distance)) return `distance ${distance.toFixed(2)}`;
  const score = Number(object.normalized_score ?? object.embedding_score ?? object.final_score);
  if (Number.isFinite(score)) return `score ${score.toFixed(2)}`;
  return "score --";
}


function objectRelevanceStats(objects = []) {
  return objects.reduce(
    (stats, object) => {
      const label = String(object.display_relevance_label || object.relevance_label || "").trim();
      if (label === "高") stats.high_count += 1;
      else if (label === "中") stats.medium_count += 1;
      else stats.low_count += 1;
      stats.total_count += 1;
      return stats;
    },
    { high_count: 0, medium_count: 0, low_count: 0, total_count: 0 }
  );
}


function searchObjectTags(object = {}) {
  const layers = [
    ["topic", object.topic_tags],
    ["problem", object.problem_tags],
    ["mechanism", object.mechanism_tags],
    ["inspiration", object.inspiration_tags]
  ];
  const tags = [];
  layers.forEach(([layer, values]) => {
    (values || []).slice(0, 3).forEach((value) => {
      if (tags.length < 4 && value) tags.push({ layer, value });
    });
  });
  return tags;
}

