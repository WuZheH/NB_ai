export function StatePanel({ kind, children }: { kind: "loading" | "error" | "empty"; children: React.ReactNode }) {
  return (
    <div className={`state-panel state-${kind}`} role={kind === "error" ? "alert" : "status"}>
      {children}
    </div>
  );
}
