import TagList from "./TagList.jsx";

export function TagBucket({ label, tags = [], emptyLabel = "无" }) {
  if (!tags?.length) return null;
  return (
    <div className="tagBucket">
      <span>{label}</span>
      <TagList tags={tags} />
    </div>
  );
}

export default function FourLayerTags({ object = {} }) {
  return (
    <div className="fourLayerTags">
      <TagBucket label="Topic" tags={object.topic_tags} />
      <TagBucket label="Problem" tags={object.problem_tags} />
      <TagBucket label="Mechanism" tags={object.mechanism_tags} />
      <TagBucket label="Inspiration" tags={object.inspiration_tags} emptyLabel="空" />
    </div>
  );
}
