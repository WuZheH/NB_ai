export default function StateMessage({ title, body }) {
  return (
    <div className="stateMessage">
      <h3>{title}</h3>
      {body && <p>{body}</p>}
    </div>
  );
}

export function LoadingState({ title = "正在加载...", body }) {
  return <StateMessage title={title} body={body} />;
}

export function ErrorState({ title = "暂不可用", error, body }) {
  return <StateMessage title={title} body={body || error?.message || String(error || "")} />;
}

export function EmptyState({ title = "暂无内容", body }) {
  return <StateMessage title={title} body={body} />;
}
