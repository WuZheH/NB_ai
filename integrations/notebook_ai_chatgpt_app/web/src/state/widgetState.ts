import { NOTEBOOK_SOURCES } from "../constants";
import type { SourceType } from "../types";

const MAX_SELECTED_IDS = 50;
const MAX_EXPANDED_IDS = 100;

export interface NotebookWidgetState {
  version: 1;
  selectedIds: string[];
  activeSources: SourceType[];
  expandedIds: string[];
}

interface WidgetStateHost {
  widgetState?: unknown;
  setWidgetState?: (state: Record<string, unknown>) => Promise<void> | void;
}

const pendingWrites = new WeakMap<object, Promise<void>>();

function hostOpenAI(): WidgetStateHost | undefined {
  return typeof window === "undefined" ? undefined : window.openai;
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function compactIds(value: unknown, maximum: number): string[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  for (const entry of value) {
    if (typeof entry !== "string") continue;
    const id = entry.trim();
    if (!id || seen.has(id)) continue;
    seen.add(id);
    if (seen.size >= maximum) break;
  }
  return [...seen];
}

function compactSources(value: unknown): SourceType[] {
  if (!Array.isArray(value)) return [...NOTEBOOK_SOURCES];
  const requested = new Set(value.filter((entry): entry is SourceType => NOTEBOOK_SOURCES.includes(entry as SourceType)));
  return NOTEBOOK_SOURCES.filter((sourceType) => requested.has(sourceType));
}

export function defaultWidgetState(): NotebookWidgetState {
  return {
    version: 1,
    selectedIds: [],
    activeSources: [...NOTEBOOK_SOURCES],
    expandedIds: [],
  };
}

export function normalizeWidgetState(value: unknown): NotebookWidgetState {
  const raw = objectValue(value);
  return {
    version: 1,
    // selected_fragment_ids was written by the first widget version. Reading
    // it here preserves existing selections while all new snapshots use the
    // compact camelCase contract below.
    selectedIds: compactIds(raw.selectedIds ?? raw.selected_fragment_ids, MAX_SELECTED_IDS),
    activeSources: compactSources(raw.activeSources),
    expandedIds: compactIds(raw.expandedIds, MAX_EXPANDED_IDS),
  };
}

export function readHostWidgetState(host: WidgetStateHost | undefined = hostOpenAI()): NotebookWidgetState {
  return normalizeWidgetState(host?.widgetState);
}

export function createWidgetState(
  selectedIds: Iterable<string>,
  activeSources: Iterable<SourceType>,
  expandedIds: Iterable<string>,
): NotebookWidgetState {
  return normalizeWidgetState({
    selectedIds: [...selectedIds],
    activeSources: [...activeSources],
    expandedIds: [...expandedIds],
  });
}

export function retainAvailableIds(ids: string[], availableIds: ReadonlySet<string>): string[] {
  const retained = ids.filter((id) => availableIds.has(id));
  return retained.length === ids.length && retained.every((id, index) => id === ids[index]) ? ids : retained;
}

export function reconcileWidgetState(
  state: NotebookWidgetState,
  availableIds: ReadonlySet<string>,
): NotebookWidgetState {
  return {
    ...state,
    selectedIds: retainAvailableIds(state.selectedIds, availableIds),
    expandedIds: retainAvailableIds(state.expandedIds, availableIds),
  };
}

export async function persistHostWidgetState(
  state: NotebookWidgetState,
  host: WidgetStateHost | undefined = hostOpenAI(),
): Promise<boolean> {
  if (!host?.setWidgetState) return false;
  const snapshot = {
    version: 1,
    selectedIds: state.selectedIds,
    activeSources: state.activeSources,
    expandedIds: state.expandedIds,
  };
  const key = host as object;
  const writeSnapshot = () =>
    Promise.resolve(
      host.setWidgetState({
        version: snapshot.version,
        selectedIds: snapshot.selectedIds,
        activeSources: snapshot.activeSources,
        expandedIds: snapshot.expandedIds,
      }),
    ).then(() => undefined);
  const previous = pendingWrites.get(key);
  let next: Promise<void>;
  try {
    // The documented ChatGPT method is synchronous. Invoke the first write
    // immediately; only non-standard async hosts need the per-host queue.
    next = previous ? previous.catch(() => undefined).then(writeSnapshot) : writeSnapshot();
  } catch {
    return false;
  }
  pendingWrites.set(key, next);
  try {
    await next;
    return true;
  } catch {
    // Older and non-ChatGPT MCP Apps hosts may not implement persisted widget
    // state. Local React state remains fully functional in that case.
    return false;
  } finally {
    if (pendingWrites.get(key) === next) pendingWrites.delete(key);
  }
}
