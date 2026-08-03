import { useState } from "react";

export default function FragmentIdBlock({ fragmentId, onCopied, className = "" }) {
  const [copyState, setCopyState] = useState("idle");
  const value = String(fragmentId || "").trim();

  async function copyFragmentId() {
    if (!value || !navigator.clipboard?.writeText) {
      setCopyState("error");
      return;
    }
    try {
      await navigator.clipboard.writeText(value);
      setCopyState("copied");
      onCopied?.(value);
      window.setTimeout(() => setCopyState("idle"), 1600);
    } catch {
      setCopyState("error");
    }
  }

  return (
    <div className={`search-fragment-id ${className}`.trim()} title={value || "没有 fragment ID"}>
      <code tabIndex={value ? 0 : undefined}>{value || "fragment_id unavailable"}</code>
      <button
        type="button"
        className="search-button search-button-transparent search-button-compact"
        disabled={!value}
        onClick={copyFragmentId}
        aria-label="复制完整 fragment ID"
      >
        {copyState === "copied" ? "已复制" : copyState === "error" ? "复制失败" : "复制 ID"}
      </button>
    </div>
  );
}
