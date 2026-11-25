// Offline bridge. When the Python server is unreachable (static hosting, or
// `npm run dev` without `sift ui`) the page answers from the same
// sift/ui/preview.json fixture the server falls back to. It is loaded on
// demand, so a normal session never downloads it.
import type { Benchmark, Mode, SearchResult, Status } from "./types";

interface PreviewFile {
  examples: string[];
  searches: Record<string, Record<Mode, { timings: Record<string, number>; hits: SearchResult["hits"] }>>;
  stats: { index: Status["corpus"]; last_index_run: Status["index_run"] };
  benchmark: Omit<Benchmark, "source">;
}

let cached: Promise<PreviewFile> | undefined;

function load(): Promise<PreviewFile> {
  cached ??= import("../../sift/ui/preview.json").then((m) => m.default as unknown as PreviewFile);
  return cached;
}

const normalise = (q: string) => q.toLowerCase().split(/\s+/).filter(Boolean).join(" ");

export async function fallbackStatus(): Promise<Status> {
  const p = await load();
  return {
    search_source: "preview",
    benchmark_source: "preview",
    corpus: p.stats.index,
    index_run: p.stats.last_index_run,
    examples: p.examples,
  };
}

export async function fallbackSearch(query: string, mode: Mode, k: number): Promise<SearchResult> {
  const p = await load();
  const key = Object.keys(p.searches).find((q) => normalise(q) === normalise(query));
  const base = { query, mode, source: "preview" as const };
  if (!key) return { ...base, hits: [], timings_ms: {}, preview_miss: true };
  const recorded = p.searches[key]![mode];
  return { ...base, hits: recorded.hits.slice(0, k), timings_ms: recorded.timings };
}

export async function fallbackBenchmark(): Promise<Benchmark> {
  const p = await load();
  return { source: "preview", ...p.benchmark };
}
