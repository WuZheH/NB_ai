export function formatConfidence(value) {
  if (typeof value !== "number" || Number.isNaN(value)) return String(value ?? "unknown");
  return value.toFixed(2);
}

export function normalizeIdentityText(value = "") {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

export function sourceModeLabel(mode) {
  if (mode === "note_led") return "Note-led";
  if (mode === "source_led") return "Source-led";
  if (mode === "joint_led") return "Joint-led";
  return "Source mode unknown";
}

export function apiErrorMessage(error, fallback = "本地 API 暂不可用。") {
  return error?.payload?.detail || error?.message || fallback;
}
