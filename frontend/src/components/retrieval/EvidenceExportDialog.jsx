const OPTION_FIELDS = [
  ["include_context_before", "前文"],
  ["include_context_after", "后文"],
  ["include_note_comment", "笔记或批注"],
  ["include_match_reasons", "匹配原因"],
  ["include_provenance", "Provenance"],
  ["include_raw_warnings", "原始 warnings"],
  ["group_by_document", "显示文档分组"],
];

export default function EvidenceExportDialog({ options, busy, disabled, onChange, onExport }) {
  function update(key, checked) {
    onChange({ ...options, [key]: checked });
  }

  return (
    <div className="localEvidenceExport">
      <div className="localEvidenceExportOptions">
        {OPTION_FIELDS.map(([key, label]) => (
          <label key={key}>
            <input
              type="checkbox"
              checked={Boolean(options[key])}
              onChange={(event) => update(key, event.target.checked)}
            />
            <span>{label}</span>
          </label>
        ))}
      </div>
      <div className="localEvidenceExportActions">
        <button type="button" disabled={disabled || busy} onClick={() => onExport("markdown", "copy")} title="复制 Markdown">
          ⧉ 复制 Markdown
        </button>
        <button type="button" disabled={disabled || busy} onClick={() => onExport("markdown", "download")} title="下载 Markdown">
          ↓ Markdown
        </button>
        <button type="button" disabled={disabled || busy} onClick={() => onExport("jsonl", "download")} title="下载 JSONL">
          ↓ JSONL
        </button>
        <button type="button" disabled={disabled || busy} onClick={() => onExport("json", "download")} title="下载 JSON">
          ↓ JSON
        </button>
      </div>
    </div>
  );
}
