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
  /** Collection names from the registry (§10.2); absent until the workspace store exists. */
  collections?: string[];
}

/** One query term's share of the BM25 score (§13.1). */
export interface TermContribution {
  term: string;
  tf: number;
  df: number;
  idf: number;
  contribution: number;
}

/** Why a hit ranked where it did: the §13.1 payload, sent with `explain=1`. */
export interface Provenance {
  lexical: { rank: number | null; score: number | null; terms: TermContribution[]; length_ratio: number };
  semantic: { rank: number | null; cosine: number | null };
  fusion: { k: number; lexical: number; semantic: number; score: number; rank: number };
  /** List rank minus hybrid rank: positive means fusion moved the hit up; null when absent from that list. */
  lift: { lexical: number | null; semantic: number | null };
  pool_size: number;
}

/** A word form the analyzer matched (E.2): "page" for the query "paging". Lines are 1-based and inclusive. */
export interface MatchSpan {
  start_line: number;
  end_line: number;
  text: string;
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
  collection?: string;
  provenance?: Provenance;
  match_spans?: MatchSpan[];
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
