import { compactReasons } from "../utils/formatters.js";

export default function ReasonList({ reasons = [] }) {
  const visibleReasons = compactReasons(reasons).slice(0, 3);
  if (!visibleReasons.length) return null;
  return (
    <div className="reasonList" aria-label="匹配原因">
      {visibleReasons.map((reason) => (
        <span key={reason}>{reason}</span>
      ))}
    </div>
  );
}
