export default function TagList({ tags = [] }) {
  if (!tags.length) return <div className="tagList muted">暂无标签</div>;
  return (
    <div className="tagList">
      {tags.map((tag) => (
        <span key={tag}>{tag}</span>
      ))}
    </div>
  );
}
