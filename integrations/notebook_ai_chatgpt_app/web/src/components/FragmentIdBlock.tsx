interface FragmentIdBlockProps {
  fragmentId: string;
  onCopy: () => void;
}

export function FragmentIdBlock({ fragmentId, onCopy }: FragmentIdBlockProps) {
  return (
    <div className="search-fragment-id" title={fragmentId}>
      <code tabIndex={0}>{fragmentId}</code>
      <button
        type="button"
        className="search-button search-button-transparent search-button-compact"
        onClick={onCopy}
        aria-label="复制完整 fragment ID"
      >
        复制 ID
      </button>
    </div>
  );
}
