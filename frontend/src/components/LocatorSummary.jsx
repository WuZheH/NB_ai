export default function LocatorSummary({ state, compact = false }) {
  if (!state || state.status === "idle") return null;
  if (state.status === "loading") return <span className="locatorStatus">正在定位 PDF 文本...</span>;
  if (state.status === "error") return <span className="locatorStatus warning">{state.error}</span>;
  const location = state.payload?.location;
  if (!location) return null;
  const aligned = location.locator_status === "chunk_aligned" || location.locator_status === "partial_chunk_aligned";
  const found = location.locator_status === "exact_text_location" || aligned || location.status === "located";
  const pageLevel = location.locator_status === "page_level_only";
  return (
    <div className={`locatorStatus ${found ? "found" : "warning"} ${compact ? "compact" : ""}`}>
      <strong>{locatorTitle(location, found, pageLevel)}</strong>
      <span>
        第 {location.pdf_page || "n/a"} 页 · {location.match_method} · {location.confidence} · {location.highlight_count ?? location.rects?.length ?? 0} 个高亮框
      </span>
      {location.page_metadata_mismatch && (
        <span>已自动定位到相邻页：原页 p.{location.original_pdf_page || "n/a"} → p.{location.corrected_pdf_page || location.pdf_page || "n/a"}</span>
      )}
      {!found && location.locator_reason && <span>{location.locator_reason}</span>}
    </div>
  );
}

function locatorTitle(location, found, pageLevel) {
  if (location.locator_status === "chunk_aligned") return "已定位到证据片段";
  if (location.locator_status === "partial_chunk_aligned") return "已定位到证据片段附近";
  if (found) return "已找到文本位置";
  if (pageLevel) return "已打开 PDF 页码";
  return location.locator_reason || "未能在该页定位精确文本";
}
