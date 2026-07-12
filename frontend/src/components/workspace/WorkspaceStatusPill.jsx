const PILL_LABELS = {
  available: "可用",
  locked: "只读安全模式",
  planned: "规划中",
  raw_unreviewed: "原始未审核",
  unavailable: "不可用",
  blocked: "只读安全模式",
  not_saved: "未保存",
  saved: "已保存",
  reviewed: "已审核",
  partial_saved: "部分保存",
  info: "只读模式",
  warning: "需要注意",
  danger: "错误",
  read_only: "只读模式",
  disabled: "未启用",
};

export default function WorkspaceStatusPill({ status, children, tone = "" }) {
  const value = status || tone || "planned";
  const semanticTone = tone || semanticToneForStatus(value);
  return (
    <span className={`workspaceStatusPill ${value} ${semanticTone}`}>
      {children || PILL_LABELS[value] || value}
    </span>
  );
}

function semanticToneForStatus(value) {
  if (["available", "saved", "reviewed", "ready"].includes(value)) return "success";
  if (["warning", "raw_unreviewed", "partial_saved"].includes(value)) return "warning";
  if (["danger", "error", "unavailable"].includes(value)) return "danger";
  return "info";
}
