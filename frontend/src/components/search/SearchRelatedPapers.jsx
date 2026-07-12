import { useState } from "react";
import { scorePercent } from "../../utils/formatters.js";
import { cleanSearchSnippet, highlightQueryTerms } from "../../utils/snippet.js";

const MAX_PASSAGES_PER_DOCUMENT = 5;
const MAX_PASSAGE_LENGTH = 320;

export default function SearchRelatedPapers({ groups, query, selectedEvidenceId, onOpenDocument, onOpenEvidence }) {
  const totalPassages = groups.reduce((count, group) => count + group.matched_passages.length, 0);

  return (
    <section className="searchSection">
      <div className="sectionHeader">
        <h3>相关资料</h3>
        <span>{groups.length ? `${groups.length} 个文档 / ${totalPassages} 条片段` : "暂无资料"}</span>
      </div>
      {groups.length ? (
        <div className="paperResultList">
          {groups.map((group) => (
            <article key={group.document_id || group.title} className="paperResultGroup">
              <header className="paperResultHeader">
                <div>
                  <button
                    type="button"
                    className="paperTitleButton"
                    onClick={() => onOpenDocument?.(group.document_id)}
                    disabled={!group.document_id}
                  >
                    {group.title || "Untitled document"}
                  </button>
                  <div className="paperResultMeta">
                    <span>{documentTypeLabel(group.document_type)}</span>
                    {group.object_import_mode && <span>{group.object_import_mode}</span>}
                    <span>{group.matched_count} 条命中片段</span>
                    {group.tiers ? (
                      <span>核心 {group.primary_count || 0} 条 · 补充 {group.secondary_count || 0} 条 · 参考 {group.reference_count || 0} 条</span>
                    ) : (
                      <span>{scorePercent(group.doc_score)}</span>
                    )}
                  </div>
                </div>
              </header>
              {group.tiers ? (
                <PaperTierStack
                  group={group}
                  query={query}
                  selectedEvidenceId={selectedEvidenceId}
                  onOpenEvidence={onOpenEvidence}
                />
              ) : (
                <div className="matchedPassageList">
                  {group.matched_passages.map((passage) => (
                    <MatchedPassageCard
                      key={`${group.document_id}-${passage.chunk_id}`}
                      passage={passage}
                      query={query}
                      selected={selectedEvidenceId === passage.chunk_id}
                      onOpenEvidence={onOpenEvidence}
                    />
                  ))}
                </div>
              )}
            </article>
          ))}
        </div>
      ) : (
        <p className="emptyInline">当前查询没有匹配到资料片段。</p>
      )}
    </section>
  );
}

function PaperTierStack({ group, query, selectedEvidenceId, onOpenEvidence }) {
  const [expandedPrimary, setExpandedPrimary] = useState(false);
  const [expandedSecondary, setExpandedSecondary] = useState(false);
  const [expandedReference, setExpandedReference] = useState(false);
  const primaryAll = group.tiers.primary || [];
  const secondaryAll = group.tiers.secondary || [];
  const referenceAll = group.tiers.reference || [];
  return (
    <div className="paperTierStack">
      <PaperTierSection
        label="核心命中"
        count={group.primary_count}
        passages={primaryAll}
        query={query}
        defaultOpen={true}
        maxVisible={5}
        expanded={expandedPrimary}
        onToggleExpand={() => setExpandedPrimary((v) => !v)}
        selectedEvidenceId={selectedEvidenceId}
        onOpenEvidence={onOpenEvidence}
      />
      <PaperTierSection
        label="补充片段"
        count={group.secondary_count}
        passages={secondaryAll}
        query={query}
        defaultOpen={false}
        expanded={expandedSecondary}
        onToggleExpand={() => setExpandedSecondary((v) => !v)}
        explanation="这些片段可能包含表格、图注、背景或低分正文，可作为补充材料。"
        selectedEvidenceId={selectedEvidenceId}
        onOpenEvidence={onOpenEvidence}
      />
      <PaperTierSection
        label="参考文献脉络"
        count={group.reference_count}
        passages={referenceAll}
        query={query}
        defaultOpen={false}
        expanded={expandedReference}
        onToggleExpand={() => setExpandedReference((v) => !v)}
        explanation="这些片段多来自参考文献或引用背景，可用于扩展参照资料，不代表正文直接论点。"
        selectedEvidenceId={selectedEvidenceId}
        onOpenEvidence={onOpenEvidence}
      />
    </div>
  );
}

function PaperTierSection({ label, count = 0, passages = [], query, defaultOpen, explanation, maxVisible, expanded, onToggleExpand, selectedEvidenceId, onOpenEvidence }) {
  if (!passages.length && !count) return null;
  const hasMore = maxVisible && passages.length > maxVisible;
  const visiblePassages = hasMore && !expanded ? passages.slice(0, maxVisible) : passages;
  return (
    <details className="paperTierSection" open={defaultOpen || expanded}>
      <summary className="paperTierSummary">
        <span>{label} {count} 条</span>
      </summary>
      {explanation && <p className="paperTierExplanation">{explanation}</p>}
      {visiblePassages.length ? (
        <div className="matchedPassageList">
          {visiblePassages.map((passage) => (
            <MatchedPassageCard
              key={`${passage.document_id}-${passage.chunk_id}-${passage.tier || label}`}
              passage={passage}
              query={query}
              selected={selectedEvidenceId === passage.chunk_id}
              onOpenEvidence={onOpenEvidence}
            />
          ))}
          {hasMore && (
            <button type="button" className="paperTierExpandBtn" onClick={onToggleExpand}>
              {expanded ? `收起${label}` : `展开全部${label}（还有 ${passages.length - maxVisible} 条）`}
            </button>
          )}
        </div>
      ) : (
        <p className="emptyInline">暂无{label}。</p>
      )}
    </details>
  );
}

function MatchedPassageCard({ passage, query, selected, onOpenEvidence }) {
  return (
    <article
      className={`matchedPassageCard ${selected ? "selected" : ""}`}
      role="button"
      tabIndex={0}
      onClick={() => onOpenEvidence?.(passage.chunk_id, passage.source_trace, "search")}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpenEvidence?.(passage.chunk_id, passage.source_trace, "search");
        }
      }}
    >
      <p className="matchedPassageText">
        <HighlightedPassage text={passage.passage_text} query={query} />
      </p>
      <div className="passageMetaLine">
        <span className="locationPath">{passage.location_label}</span>
        <button
          type="button"
          className="textButton passageOpenButton"
          onClick={(event) => {
            event.stopPropagation();
            onOpenEvidence?.(passage.chunk_id, passage.source_trace, "search");
          }}
        >
          查看详情
        </button>
      </div>
    </article>
  );
}

function HighlightedPassage({ text, query }) {
  const segments = highlightQueryTerms(text, query);
  if (!segments.length) return cleanSearchSnippet(text);
  return segments.map((segment, index) =>
    segment.highlighted
      ? <mark key={`${segment.text}-${index}`}>{segment.text}</mark>
      : segment.text
  );
}

export function buildPaperGroups(results = [], grouped, query) {
  const groups = grouped ? normalizeGroupedResults(results, query) : normalizeFlatResults(results, query);
  return groups
    .map((group) => {
      const passages = dedupePassages(group.matched_passages)
        .sort((left, right) => scoreValue(right.score) - scoreValue(left.score))
        .slice(0, MAX_PASSAGES_PER_DOCUMENT);
      return {
        ...group,
        matched_passages: passages,
        matched_count: group.matched_count || passages.length,
        doc_score: scoreValue(group.doc_score) || scoreValue(passages[0]?.score)
      };
    })
    .filter((group) => group.matched_passages.length)
    .sort((left, right) => scoreValue(right.doc_score) - scoreValue(left.doc_score));
}

export function buildHighQualityPaperGroups(papers = [], query = "") {
  return (papers || []).map((paper) => {
    const tiers = {
      primary: normalizeTierPassages(paper.tiers?.primary || [], paper, query),
      secondary: normalizeTierPassages(paper.tiers?.secondary || [], paper, query),
      reference: normalizeTierPassages(paper.tiers?.reference || [], paper, query)
    };
    const matchedPassages = [...tiers.primary, ...tiers.secondary, ...tiers.reference];
    return {
      ...paper,
      title: paper.title,
      document_id: paper.document_id,
      document_type: paper.document_type || paper.source_kind || "other",
      object_import_mode: paper.object_import_mode || "",
      source_kind: paper.source_kind || paper.document_type || "other",
      doc_score: paper.best_score || paper.max_rerank_score,
      matched_count: paper.total_passage_count || matchedPassages.length,
      matched_passages: matchedPassages,
      tiers,
    };
  });
}

function normalizeTierPassages(passages = [], paper = {}, query = "") {
  return passages.map((passage) => ({
    ...passage,
    document_id: passage.document_id || paper.document_id,
    title: paper.title,
    passage_text: bestEffortPassage(passage.passage_text || "", ""),
    score: passage.embedding_score,
    location_label: structureLocationLabel(passage),
    source_trace: {
      ...(passage.source_trace || {}),
      selection_type: "evidence",
      document_id: passage.document_id || paper.document_id,
      chunk_id: passage.chunk_id,
      search_query: query,
      fallback_terms: fallbackTermsForQuery(query, passage.passage_text || ""),
      snippet: passage.passage_text || ""
    }
  }));
}

function normalizeGroupedResults(results, query) {
  return (results || []).map((group) => ({
    document_id: group.document_id,
    title: group.document_title,
    document_type: group.document_type || group.source_kind || "other",
    object_import_mode: group.object_import_mode || "",
    source_kind: group.source_kind || group.document_type || "other",
    doc_score: group.document_relevance_score,
    matched_count: (group.top_chunks || []).length,
    matched_passages: (group.top_chunks || []).map((chunk) =>
      normalizePassage(chunk, {
        query,
        title: group.document_title,
        document_id: group.document_id
      })
    )
  }));
}

function normalizeFlatResults(results, query) {
  const groups = new Map();
  (results || []).forEach((result) => {
    if (!result.chunk_id) return;
    const documentId = result.document_id || result.source_trace?.document_id || "unknown";
    if (!groups.has(documentId)) {
      groups.set(documentId, {
        document_id: result.document_id,
        title: result.title,
        document_type: result.document_type || result.source_kind || "other",
        object_import_mode: result.object_import_mode || "",
        source_kind: result.source_kind || result.document_type || "other",
        doc_score: result.relevance_score,
        matched_count: 0,
        matched_passages: []
      });
    }
    const group = groups.get(documentId);
    group.matched_count += 1;
    group.doc_score = Math.max(scoreValue(group.doc_score), scoreValue(result.relevance_score));
    group.matched_passages.push(normalizePassage(result, { query, title: result.title, document_id: result.document_id }));
  });
  return Array.from(groups.values());
}

function normalizePassage(chunk, { query, title, document_id }) {
  const passageText = bestEffortPassage(chunk.passage_text || chunk.sentence_text || chunk.full_text || chunk.chunk_text || chunk.snippet || "", query);
  return {
    chunk_id: chunk.chunk_id,
    document_id: chunk.document_id || document_id,
    title,
    passage_text: passageText,
    score: chunk.passage_score ?? chunk.relevance_score ?? chunk.score,
    location_label: structureLocationLabel(chunk),
    source_trace: {
      ...(chunk.source_trace || {}),
      selection_type: "evidence",
      document_id: chunk.document_id || document_id,
      chunk_id: chunk.chunk_id,
      search_query: query,
      fallback_terms: fallbackTermsForQuery(query, passageText),
      snippet: passageText
    }
  };
}

function fallbackTermsForQuery(query, text) {
  const terms = [];
  const add = (value) => {
    const term = compactText(value);
    if (term.length >= 2 && !terms.some((existing) => existing.toLowerCase() === term.toLowerCase())) {
      terms.push(term);
    }
  };
  add(query);
  String(query || "").split(/[\s,;，；、/]+/).forEach(add);
  const combined = `${query || ""} ${text || ""}`;
  (combined.match(/\b[A-Z][A-Za-z]+(?:-[A-Z][A-Za-z]+)*\b|\b[A-Z]{2,}\b/g) || []).forEach(add);
  (combined.match(/[\u4e00-\u9fff]{2,8}/g) || []).forEach(add);
  return terms.slice(0, 10);
}

function dedupePassages(passages) {
  const seen = new Set();
  return passages.filter((passage) => {
    if (!passage.chunk_id || seen.has(passage.chunk_id)) return false;
    seen.add(passage.chunk_id);
    return Boolean(passage.passage_text);
  });
}

function bestEffortPassage(value, query) {
  const text = cleanSearchSnippet(value);
  if (!text) return "暂无可显示命中片段。";
  if (text.length <= MAX_PASSAGE_LENGTH) return text;

  const terms = directHighlightTerms(query, text);
  const firstHit = terms.reduce((best, term) => {
    const index = text.toLowerCase().indexOf(term.toLowerCase());
    if (index < 0) return best;
    return best < 0 ? index : Math.min(best, index);
  }, -1);
  const center = firstHit >= 0 ? firstHit : 0;
  const start = Math.max(0, center - 90);
  const end = Math.min(text.length, start + MAX_PASSAGE_LENGTH);
  const prefix = start > 0 ? "..." : "";
  const suffix = end < text.length ? "..." : "";
  return `${prefix}${text.slice(start, end).trim()}${suffix}`;
}

function structureLocationLabel(chunk) {
  const path = chunk.heading_path || chunk.section_path || chunk.section_title || chunk.section_label;
  if (Array.isArray(path)) return path.filter(Boolean).join(" / ") || "章节暂不可用";
  if (typeof path === "string" && path.trim()) return path.trim();
  return "章节暂不可用";
}

function documentTypeLabel(value) {
  const type = String(value || "other").toLowerCase();
  if (type === "book") return "book";
  if (type === "paper" || type === "article") return "paper";
  return "other";
}

function directHighlightTerms(query, text) {
  const source = compactText(query);
  if (!source || !text) return [];
  const terms = new Set();
  source
    .split(/[\s,;，；、]+/)
    .map((term) => term.trim())
    .filter((term) => term.length >= 2 && /[A-Za-z0-9]/.test(term))
    .forEach((term) => terms.add(term));
  if (source.length >= 2 && source.length <= 40 && text.toLowerCase().includes(source.toLowerCase())) {
    terms.add(source);
  }
  return Array.from(terms)
    .filter((term) => shouldHighlightTerm(term, text))
    .sort((left, right) => right.length - left.length);
}

function shouldHighlightTerm(term, text) {
  if (!/^[A-Za-z0-9]+$/.test(term)) return true;
  if (term.length >= 3) return true;
  return new RegExp(`(^|[^A-Za-z0-9])${escapeRegExp(term)}(?=$|[^A-Za-z0-9])`, "i").test(text);
}

function highlightRanges(text, terms) {
  const occupied = [];
  terms.forEach((term) => {
    const pattern = new RegExp(escapeRegExp(term), "gi");
    let match = pattern.exec(text);
    while (match) {
      const start = match.index;
      const end = start + match[0].length;
      const shortAscii = /^[A-Za-z0-9]{1,2}$/.test(term);
      const boundaryOk = !shortAscii || (isTokenBoundary(text[start - 1]) && isTokenBoundary(text[end]));
      const overlaps = occupied.some((range) => start < range.end && end > range.start);
      if (boundaryOk && !overlaps) occupied.push({ start, end });
      match = pattern.exec(text);
    }
  });
  return occupied.sort((left, right) => left.start - right.start);
}

function isTokenBoundary(char) {
  return !char || !/[A-Za-z0-9]/.test(char);
}

function scoreValue(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : 0;
}

function compactText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
