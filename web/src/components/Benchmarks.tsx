import { useEffect, useMemo, useState } from "react";
import { fmtInt, fmtMetric, fmtMs, MODE_ENGINE, MODE_LABEL } from "../lib/format";
import type { Benchmark, Mode, ModeBench } from "../types";

interface Props {
  bench: Benchmark | null;
  error: string | null;
  load: () => void;
}

// The same names as the Search view's Ranking options, in the same order of complexity.
const ROWS: Mode[] = ["lexical", "semantic", "hybrid"];

type Key = "recall" | "mrr" | "ndcg" | "p50" | "p95";

interface Column {
  key: Key;
  label: string;
  /** Plain-language meaning, shown on hover and focus. */
  tip: string;
  /** Lower is better (latency); otherwise higher is better. */
  lowerBetter: boolean;
  value: (m: ModeBench) => number | null | undefined;
  format: (n: number | null | undefined) => string;
}

const COLUMNS: Column[] = [
  {
    key: "recall",
    label: "Recall@3",
    tip: "Share of the relevant notes that appear in the top 3 results.",
    lowerBetter: false,
    value: (m) => m.quality["recall@3"],
    format: fmtMetric,
  },
  {
    key: "mrr",
    label: "MRR",
    tip: "Mean reciprocal rank: how near the top the first relevant note lands, averaged over queries.",
    lowerBetter: false,
    value: (m) => m.quality.mrr,
    format: fmtMetric,
  },
  {
    key: "ndcg",
    label: "NDCG@5",
    tip: "Quality of the top 5 results, where relevant notes count for more the higher they rank.",
    lowerBetter: false,
    value: (m) => m.quality["ndcg@5"],
    format: fmtMetric,
  },
  {
    key: "p50",
    label: "p50",
    tip: "Median query time: half of all queries finish faster than this.",
    lowerBetter: true,
    value: (m) => m.latency_ms.total_ms?.p50,
    format: fmtMs,
  },
  {
    key: "p95",
    label: "p95",
    tip: "Slow-query time: 95 in 100 queries finish faster than this.",
    lowerBetter: true,
    value: (m) => m.latency_ms.total_ms?.p95,
    format: fmtMs,
  },
];

type Sort = { key: Key; dir: "best" | "worst" } | null;

export function Benchmarks({ bench, error, load }: Props) {
  const [sort, setSort] = useState<Sort>(null);
  // Esc hides the open tooltip until the pointer or focus leaves its heading (WCAG 1.4.13).
  const [hush, setHush] = useState<Key | null>(null);

  useEffect(() => {
    if (!bench && !error) load();
  }, [bench, error, load]);

  const rows = useMemo(() => {
    const base = ROWS.map((mode) => ({ mode, m: bench?.modes[mode] }));
    if (!sort) return base;
    const col = COLUMNS.find((c) => c.key === sort.key)!;
    // "Best" means high for quality and low for latency; absent values sink to the bottom.
    const sign = (col.lowerBetter ? 1 : -1) * (sort.dir === "best" ? 1 : -1);
    return [...base].sort((a, b) => {
      const x = a.m ? col.value(a.m) : null;
      const y = b.m ? col.value(b.m) : null;
      if (x == null && y == null) return 0;
      if (x == null) return 1;
      if (y == null) return -1;
      return (x - y) * sign;
    });
  }, [bench, sort]);

  // best first, then worst first, then back to the written order
  const cycle = (key: Key) =>
    setSort((s) => (s?.key !== key ? { key, dir: "best" } : s.dir === "best" ? { key, dir: "worst" } : null));

  if (error) {
    return (
      <div className="bench">
        <p className="note">
          Couldn’t load benchmark results. <span className="muted">{error}</span>
        </p>
        <button type="button" className="link" onClick={load}>
          Try again
        </button>
      </div>
    );
  }
  if (!bench) {
    return (
      <div className="bench" aria-busy="true">
        <p className="note muted">Loading benchmark results…</p>
      </div>
    );
  }

  const { queries } = bench.environment;

  // Quality at the ceiling for every mode means the set is too easy to tell rankings apart: say so.
  const saturated = COLUMNS.filter((c) => !c.lowerBetter).every((c) =>
    ROWS.every((mode) => {
      const m = bench.modes[mode];
      const v = m ? c.value(m) : null;
      return v == null || v >= 0.95;
    }),
  );

  // Bold the best value in each column, and only where it beats the rest as shown: a three-way tie is no winner.
  const best = new Map<Key, string>();
  for (const c of COLUMNS) {
    const shown = ROWS.map((mode) => bench.modes[mode]).flatMap((m) => {
      const v = m ? c.value(m) : null;
      return v == null ? [] : [v];
    });
    if (shown.length < 2) continue;
    const top = c.format(c.lowerBetter ? Math.min(...shown) : Math.max(...shown));
    if (shown.some((v) => c.format(v) !== top)) best.set(c.key, top);
  }

  return (
    <div className="bench">
      <header className="bench-head">
        <h1>Benchmarks</h1>
        {queries != null && bench.corpus && (
          <p className="lede">
            <span className="num">{fmtInt(queries)}</span> evaluation queries across{" "}
            <span className="num">{fmtInt(bench.corpus.files)}</span> Markdown documents <span aria-hidden="true">·</span>{" "}
            <span className="num">{fmtInt(bench.corpus.chunks)}</span> passages
            {bench.source !== "benchmark" && <>, recorded on the bundled sample data</>}
          </p>
        )}
      </header>

      <div className="table-scroll" tabIndex={0} role="region" aria-label="Retrieval evaluation results">
        <table>
          <caption className="visually-hidden">Retrieval quality and latency by ranking method. Column headers sort the rows.</caption>
          <thead>
            <tr>
              <th scope="col">Ranking</th>
              {COLUMNS.map((c) => {
                const active = sort?.key === c.key ? sort.dir : null;
                return (
                  <th
                    key={c.key}
                    scope="col"
                    className="r"
                    aria-sort={active ? (active === "best" === !c.lowerBetter ? "descending" : "ascending") : "none"}
                  >
                    <button
                      type="button"
                      className="colhead"
                      onClick={() => cycle(c.key)}
                      aria-describedby={`tip-${c.key}`}
                      data-sorted={active ?? undefined}
                      data-hush={hush === c.key || undefined}
                      onKeyDown={(e) => e.key === "Escape" && setHush(c.key)}
                      onBlur={() => setHush(null)}
                      onMouseLeave={() => setHush(null)}
                    >
                      <span>{c.label}</span>
                      <span className="sort num" aria-hidden="true">
                        {active ? (active === "best" === !c.lowerBetter ? "↓" : "↑") : ""}
                      </span>
                      {/* aria-hidden keeps it out of the button's name; aria-describedby still reads it as the description. */}
                      <span id={`tip-${c.key}`} role="tooltip" className="tip" aria-hidden="true" onClick={(e) => e.stopPropagation()}>
                        {c.tip}
                      </span>
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {rows.map(({ mode, m }) => (
              <tr key={mode}>
                <th scope="row">
                  {MODE_LABEL[mode]}
                  <span className="engine">{MODE_ENGINE[mode]}</span>
                </th>
                {COLUMNS.map((c) => (
                  <td key={c.key} className="r num" data-label={c.label} data-best={best.get(c.key) === c.format(m ? c.value(m) : null) || undefined}>
                    {c.format(m ? c.value(m) : null)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {saturated && (
        <p className="foot">
          Every ranking finds nearly every relevant note here, so the quality columns sit close to 1.000 and the table mostly shows
          speed. A larger or harder set of notes would separate them.
        </p>
      )}
      <p className="foot">
        Latency is the whole search. Words runs BM25 only; Meaning embeds your query and searches the vectors; Words + meaning
        does both, then fuses the two rankings. Select a column heading to sort.
      </p>

      {/* The same definitions as the hover tooltips, for touch and keyboard readers. */}
      <details className="glossary">
        <summary>What these columns measure</summary>
        <dl>
          {COLUMNS.map((c) => (
            <div key={c.key}>
              <dt className="num">{c.label}</dt>
              <dd>{c.tip}</dd>
            </div>
          ))}
        </dl>
      </details>
    </div>
  );
}
