import StateMessage from "../../../shared/components/StateMessage.jsx";

export default function ReviewInputPanel({
  jobId,
  jsonPaste,
  uploadLoading,
  uploadError,
  uploadResult,
  suggestionsLoading,
  suggestionsError,
  onJobIdChange,
  onJsonPasteChange,
  onUpload,
  onLoadSuggestions,
  onLoadReviewedObjects,
}) {
  return (
    <>
      <div className="importReviewJobInput">
        <input
          value={jobId}
          onChange={event => onJobIdChange(event.target.value)}
          placeholder="输入 import_job_id..."
          aria-label="Import Job ID"
        />
      </div>

      <div className="importReviewPasteSection">
        <h3>导入 ChatGPT 对象标签建议</h3>
        <textarea
          value={jsonPaste}
          onChange={event => onJsonPasteChange(event.target.value)}
          placeholder="粘贴 ChatGPT 输出的 object_tag_suggestions_v1 JSON..."
          rows={10}
          aria-label="ChatGPT JSON input"
        />
        <div className="importReviewPasteActions">
          <button type="button" onClick={onUpload} disabled={uploadLoading || !jobId.trim() || !jsonPaste.trim()}>
            {uploadLoading ? "上传中..." : "上传建议"}
          </button>
          <button type="button" onClick={onLoadSuggestions} disabled={suggestionsLoading || !jobId.trim()}>
            {suggestionsLoading ? "加载中..." : "加载 ChatGPT 建议"}
          </button>
          <button type="button" className="quietButton" onClick={onLoadReviewedObjects} disabled={suggestionsLoading || !jobId.trim()}>
            {suggestionsLoading ? "加载中..." : "加载已审核结果"}
          </button>
        </div>
        {uploadError && <StateMessage title="上传错误" body={uploadError} />}
        {uploadResult && !uploadError && (
          <div className="uploadResultSummary">
            <span>✅ 已上传</span>
            <span>对象数：{uploadResult.object_count}</span>
            {(uploadResult.warnings || []).length > 0 && <span className="warningPill">⚠ {uploadResult.warnings.length} warnings</span>}
          </div>
        )}
      </div>

      {suggestionsError && <StateMessage title="加载失败" body={suggestionsError} />}
    </>
  );
}
