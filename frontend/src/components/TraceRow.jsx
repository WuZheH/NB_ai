export default function TraceRow({ label, value }) {
  return (
    <div className="traceRow">
      <span>{label}</span>
      <strong>{value || "暂不可用"}</strong>
    </div>
  );
}
