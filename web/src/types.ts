// Shapes of the JSON served by sift/ui/server.py (and by the bundled fixture).

export type Mode = "hybrid" | "semantic" | "lexical";
export type TopK = 3 | 5 | 10;
export type View = "search" | "bench";

export interface Corpus {
  files: number;
  chunks: number;
}

export interface IndexRun {
  chunk_ms?: number | null;
  embed_ms?: number | null;
  total_ms?: number | null;
}

export interface Status {
  search_source: "live" | "preview";
  benchmark_source: "benchmark" | "preview";
  corpus: Corpus;
  index_run: IndexRun;
  examples: string[];
}

export interface Hit {
  rank: number;
  chunk_id: number;
  file_path: string;
  start_line: number;
  end_line: number;
  heading_context: string;
  text: string;
  score: number;
  lexical_score: number | null;
  semantic_score: number | null;
  /** Rank in that ranker's own list; null when it did not return the chunk or did not run. */
  lexical_rank: number | null;
  semantic_rank: number | null;
}

export interface SearchResult {
  query: string;
  mode: Mode;
  source: "live" | "preview";
  preview_miss?: boolean;
  hits: Hit[];
  timings_ms: Record<string, number>;
}

export interface Summary {
  p50: number;
  p95: number;
}

export interface ModeBench {
  quality: { "recall@3": number; "ndcg@5": number; mrr: number };
  latency_ms: Record<string, Summary | null>;
}

export interface Benchmark {
  source: "benchmark" | "preview";
  environment: { queries?: number; repeats?: number };
  corpus?: Corpus;
  modes: Partial<Record<Mode, ModeBench>>;
}

export interface DocumentText {
  path: string;
  text: string;
}
