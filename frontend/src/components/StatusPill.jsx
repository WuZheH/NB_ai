export default function StatusPill({ label, value }) {
  return (
    <div className="statusPill">
      <span>{label}</span>
      <strong>{value ? "是" : "否"}</strong>
    </div>
  );
}
