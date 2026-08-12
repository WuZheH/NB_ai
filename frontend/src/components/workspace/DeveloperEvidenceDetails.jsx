import { sourceTargetSummary } from "./sourceTargets.js";

export default function DeveloperEvidenceDetails({ sourceTarget, locatorState }) {
  if (!sourceTarget) return null;
  const details = {
    source_target: sourceTargetSummary(sourceTarget),
    locator_status: locatorState?.status || "idle",
    locator_location: locatorState?.payload?.location || null,
    raw_markdown_policy: "hidden_from_main_source_view",
  };
  return (
    <details className="developerEvidenceDetails">
      <summary>开发者细节</summary>
      <p>raw markdown 折叠在这里，不作为主 Source UI。</p>
      <pre>{JSON.stringify(details, null, 2)}</pre>
    </details>
  );
}
