import StatusPill from "./StatusPill.jsx";

export default function SafetyStatus({ safety }) {
  return (
    <div className="safetyList">
      <StatusPill label="生产写入已启用" value={safety.production_write_enabled} />
      <StatusPill label="已调用外部大模型" value={safety.external_llm_called} />
      <StatusPill label="已执行数据库写入" value={safety.db_write_performed} />
      <StatusPill label="已生成 mechanism" value={safety.mechanism_generated} />
    </div>
  );
}
