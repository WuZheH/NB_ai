import { NOTEBOOK_SOURCES } from "../constants";
import { SOURCE_LABELS } from "./SourceBadge";
import type { SourceType } from "../types";

interface SourceFiltersProps {
  active: Set<SourceType>;
  onToggle: (sourceType: SourceType) => void;
}

export function SourceFilters({ active, onToggle }: SourceFiltersProps) {
  return (
    <fieldset className="source-filters">
      <legend>来源筛选</legend>
      {NOTEBOOK_SOURCES.map((sourceType) => (
        <label key={sourceType}>
          <input type="checkbox" checked={active.has(sourceType)} onChange={() => onToggle(sourceType)} />
          <span>{SOURCE_LABELS[sourceType]}</span>
        </label>
      ))}
    </fieldset>
  );
}
